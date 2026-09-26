#!/usr/bin/env python3
"""Publish and rediscover one descriptor-verified release candidate through source Core."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
from typing import Sequence

import component_versions
from verify_component_artifacts import (
    ComponentArtifactError, MAX_ARTIFACT_BYTES, expected_filenames,
    verify_component_directory,
)


ROOT = Path(__file__).resolve().parents[1]
OWNER = "release-candidate"
FORMAT = "workbench-component-candidate-v1"


class CandidatePublicationError(ValueError):
    """The selected source bytes or retained Core candidate cannot be verified."""


def _source_file(component: str, directory: Path) -> tuple[Path, str]:
    filename, _owner, _relative = _component_artifact(component)
    selected = Path(directory).absolute()
    if selected.is_symlink() or not selected.is_dir():
        raise CandidatePublicationError("candidate source directory is missing or indirect")
    return selected / filename, filename


def _component_artifact(component: str) -> tuple[str, str, str]:
    _authority, components = component_versions.load_authority()
    row = components.get(component)
    if row is None or row["kind"] not in {"python", "python-client", "client"}:
        raise CandidatePublicationError("candidate adapter requires a Python or client component")
    names = expected_filenames(component)
    if len(names) != 1:
        raise CandidatePublicationError("candidate adapter requires one descriptor artifact")
    filename = names[0]
    if row["kind"] in {"python", "python-client"}:
        return filename, "native-build", "wheels/" + filename
    return filename, "developer-client-build", filename


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _copy_exact_source(source: Path, target: Path) -> str:
    """Copy an ordinary independent file while pinning its opened identity."""

    before = source.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 1 <= before.st_size <= MAX_ARTIFACT_BYTES):
        raise CandidatePublicationError("candidate source is not one bounded ordinary file")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = sha256()
    count = 0
    with os.fdopen(descriptor, "rb") as opened:
        if _file_identity(os.fstat(opened.fileno())) != _file_identity(before):
            raise CandidatePublicationError("candidate source changed before copying")
        with target.open("xb") as published:
            while chunk := opened.read(1024 * 1024):
                count += len(chunk)
                if count > MAX_ARTIFACT_BYTES:
                    raise CandidatePublicationError("candidate source exceeds the artifact byte limit")
                published.write(chunk)
                digest.update(chunk)
            published.flush()
            os.fsync(published.fileno())
        if (count != before.st_size
                or _file_identity(os.fstat(opened.fileno())) != _file_identity(before)
                or _file_identity(source.lstat()) != _file_identity(before)):
            raise CandidatePublicationError("candidate source changed during copying")
    target.chmod(0o644)
    return digest.hexdigest()


def _candidate_id(component: str, filename: str, digest: str) -> str:
    body = json.dumps({"component": component, "filename": filename, "sha256": digest},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "workbench-component-candidate-v1:sha256:" + sha256(body).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as selected:
        while chunk := selected.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_host(workspace: Path, configuration_home: Path | None, output_root: Path | None):
    for source in (ROOT / "api/src", ROOT / "core/src"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
    from workbench_core.managed_trees import CoreManagedTrees
    from workbench_core.user_config_home import default_user_config_home

    selected_workspace = Path(workspace).resolve(strict=True)
    home = Path(configuration_home or default_user_config_home()).absolute()
    output = Path(output_root or selected_workspace / ".workbench/build").absolute()
    return CoreManagedTrees(
        workspace=selected_workspace, configuration_home=home,
        locations={"artifacts": output}, owner_id=OWNER,
        location_sources={"artifacts": "component-candidate"},
    ), output


def _source_member(reference, *, owner: str, relative: str):
    if reference.owner_id != owner or reference.role != "artifacts":
        raise CandidatePublicationError("source build has another Core owner or role")
    member = next((row for row in reference.members if row.get("path") == relative), None)
    if (member is None or member.get("kind") != "file"
            or member.get("classification") != "authoritative"
            or not isinstance(member.get("sha256"), str)
            or type(member.get("size")) is not int):
        raise CandidatePublicationError("source build does not contain the exact descriptor artifact")
    return member


def _source_build(host, component: str, source_directory: Path):
    """Find the one committed builder tree at this exact output path."""

    filename, owner, relative = _component_artifact(component)
    directory = Path(source_directory).absolute()
    source_root = directory.parent if owner == "native-build" else directory
    if source_root / relative != directory / filename:
        raise CandidatePublicationError("source build directory differs from its Core layout")
    rows = [row for row in host.catalog.inventory(workspace=host.workspace)["trees"]
            if row["path"] == str(source_root)]
    if len(rows) != 1 or rows[0]["status"] != "committed" or rows[0]["owner_id"] != owner:
        raise CandidatePublicationError("exact committed Core source build is unavailable")
    reference = host.describe(str(rows[0]["tree_id"]))
    if reference.path != source_root:
        raise CandidatePublicationError("Core source build path changed")
    return reference, _source_member(reference, owner=owner, relative=relative)


def discover_candidate(
    component: str, tree_id: str, *, workspace: Path = ROOT,
    configuration_home: Path | None = None, output_root: Path | None = None,
) -> dict[str, object]:
    """Reopen Core custody and return only a verified upload directory."""

    filename, source_owner, source_relative = _component_artifact(component)
    host, output = _candidate_host(workspace, configuration_home, output_root)
    reference = host.describe(tree_id)
    expected_parent = output / "outputs" / OWNER
    if (reference.owner_id != OWNER or reference.role != "artifacts"
            or reference.path.parent != expected_parent
            or len(reference.members) != 1):
        raise CandidatePublicationError("Core candidate has another owner, location, or artifact set")
    artifact = verify_component_directory(component, reference.path)[0]
    member = reference.members[0]
    if (member.get("path") != filename or member.get("kind") != "file"
            or member.get("classification") != "authoritative"
            or member.get("mode") != 0o644
            or member.get("size") != artifact.stat().st_size
            or reference.domain_id != _candidate_id(component, filename, str(member.get("sha256")))):
        raise CandidatePublicationError("Core candidate differs from its descriptor or exact bytes")
    if _file_sha256(artifact) != member["sha256"]:
        raise CandidatePublicationError("Core candidate artifact bytes changed after reopening")
    if len(reference.references) != 1:
        raise CandidatePublicationError("Core candidate omits its source build reference")
    source = host.describe(reference.references[0])
    source_member = _source_member(source, owner=source_owner, relative=source_relative)
    if (source_member["sha256"] != member["sha256"]
            or source_member["size"] != member["size"]):
        raise CandidatePublicationError("Core candidate differs from its referenced source build")
    return {
        "format": FORMAT, "component": component, "tree_id": reference.tree_id,
        "source_tree_id": source.tree_id,
        "path": str(reference.path), "artifact_name": filename,
        "sha256": member["sha256"], "size": member["size"],
    }


def publish_candidate(
    component: str, source_directory: Path, *, workspace: Path = ROOT,
    configuration_home: Path | None = None, output_root: Path | None = None,
) -> dict[str, object]:
    """Select one exact descriptor artifact, then let Core stage and publish it."""

    source, filename = _source_file(component, source_directory)
    host, _output = _candidate_host(workspace, configuration_home, output_root)
    source_reference, source_member = _source_build(host, component, source.parent)

    def validate(staged: Path, expected_sha256: str) -> None:
        artifact = verify_component_directory(component, staged)[0]
        if _file_sha256(artifact) != expected_sha256:
            raise CandidatePublicationError("candidate bytes differ from the selected source")
        if stat.S_IMODE(artifact.stat().st_mode) != 0o644:
            raise CandidatePublicationError("candidate mode differs from release upload mode")

    with host.stage("artifacts", "bundle") as stage:
        stage.path.mkdir(mode=0o700)
        digest = _copy_exact_source(source, stage.path / filename)
        if (digest != source_member["sha256"]
                or (stage.path / filename).stat().st_size != source_member["size"]):
            raise CandidatePublicationError("candidate source differs from its Core build tree")
        validate(stage.path, digest)
        reference = stage.publish(
            validate=lambda path: validate(path, digest),
            domain_id=_candidate_id(component, filename, digest),
            references=(source_reference.tree_id,),
        )
    validate(reference.path, digest)
    if host.describe(reference.tree_id) != reference:
        raise CandidatePublicationError("Core candidate record changed after publication")
    return discover_candidate(
        component, reference.tree_id, workspace=workspace,
        configuration_home=configuration_home, output_root=output_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True)
    selected = parser.add_mutually_exclusive_group(required=True)
    selected.add_argument("--source-directory", type=Path)
    selected.add_argument("--tree-id")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (
            publish_candidate(args.component, args.source_directory)
            if args.source_directory is not None
            else discover_candidate(args.component, args.tree_id)
        )
        if args.github_output is not None:
            path = str(result["path"])
            if "\n" in path or "\r" in path:
                raise CandidatePublicationError("candidate path cannot be passed to GitHub output")
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(
                    f"path={path}\ntree_id={result['tree_id']}\n"
                    f"source_tree_id={result['source_tree_id']}\n"
                )
    except (CandidatePublicationError, ComponentArtifactError,
            component_versions.ComponentVersionError, OSError, ValueError) as exc:
        print(f"component candidate publication failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
