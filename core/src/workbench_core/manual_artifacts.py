"""Profile-owned preflight for user-supplied Packwiz artifacts."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from hashlib import sha256
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
import tomllib
from typing import Any, TextIO

from workbench_core.configuration import WorkbenchConfiguration


PROFILE_FORMAT = "workbench-manual-artifact-profile-v1"
REPORT_FORMAT = "workbench-manual-artifact-preflight-v1"
SCHEMA_VERSION = 1
MAX_PROFILE_BYTES = 512 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
READ_CHUNK = 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ManualArtifactError(ValueError):
    """Manual artifact authority, workspace, or seed input is invalid."""


def _canonical_path(value: object, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        raise ManualArtifactError(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in path.parts)
        or value != "/".join(path.parts)
    ):
        raise ManualArtifactError(f"{label} must be a portable relative path")
    return value


def _regular_json(path_value: Path | str) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManualArtifactError(f"cannot inspect manual artifact profile: {exc}") from exc
    if path.is_symlink() or not path.is_file() or not 1 <= info.st_size <= MAX_PROFILE_BYTES:
        raise ManualArtifactError(
            "manual artifact profile must be a bounded regular non-symlink file"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManualArtifactError(
            "manual artifact profile must be strict UTF-8 JSON"
        ) from exc
    if type(value) is not dict:
        raise ManualArtifactError("manual artifact profile must be one JSON object")
    return value


def load_manual_artifact_profile(path_value: Path | str) -> dict[str, Any]:
    value = _regular_json(path_value)
    expected = {
        "format",
        "schema_version",
        "profile_id",
        "project_id",
        "seed_layout",
        "artifacts",
    }
    if set(value) != expected:
        raise ManualArtifactError("manual artifact profile has unsupported or missing fields")
    if value["format"] != PROFILE_FORMAT or value["schema_version"] != 1:
        raise ManualArtifactError("manual artifact profile format is unsupported")
    for field in ("profile_id", "project_id"):
        if type(value[field]) is not str or not value[field]:
            raise ManualArtifactError(f"manual artifact profile {field} must be text")
    if value["seed_layout"] != "minecraft-root":
        raise ManualArtifactError("manual artifact profile seed layout is unsupported")
    artifacts = value["artifacts"]
    if type(artifacts) is not list or not artifacts:
        raise ManualArtifactError("manual artifact profile must declare artifacts")
    fields = {
        "id",
        "display_name",
        "metadata_path",
        "filename",
        "output_path",
        "hash_format",
        "hash",
        "project_id",
        "file_id",
        "download_page_url",
    }
    seen_ids: set[str] = set()
    seen_outputs: set[str] = set()
    for artifact in artifacts:
        if type(artifact) is not dict or set(artifact) != fields:
            raise ManualArtifactError("manual artifact row has unsupported or missing fields")
        artifact_id = artifact["id"]
        if (
            type(artifact_id) is not str
            or _ID.fullmatch(artifact_id) is None
            or artifact_id in seen_ids
        ):
            raise ManualArtifactError("manual artifact ID is invalid or repeated")
        seen_ids.add(artifact_id)
        for field in ("display_name", "filename"):
            if type(artifact[field]) is not str or not artifact[field]:
                raise ManualArtifactError(f"manual artifact {field} must be text")
        filename = artifact["filename"]
        if filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ManualArtifactError("manual artifact filename is unsafe")
        metadata_path = _canonical_path(artifact["metadata_path"], "metadata_path")
        output_path = _canonical_path(artifact["output_path"], "output_path")
        if not metadata_path.endswith(".pw.toml"):
            raise ManualArtifactError("manual artifact metadata_path must be a Packwiz metafile")
        if PurePosixPath(output_path).name != filename or output_path in seen_outputs:
            raise ManualArtifactError("manual artifact output path is invalid or repeated")
        seen_outputs.add(output_path)
        algorithm = artifact["hash_format"]
        digest = artifact["hash"]
        if algorithm not in {"sha1", "sha256", "sha512"}:
            raise ManualArtifactError("manual artifact hash algorithm is unsupported")
        if (
            type(digest) is not str
            or len(digest) != hashlib.new(algorithm).digest_size * 2
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ManualArtifactError("manual artifact content hash is invalid")
        if any(type(artifact[field]) is not int or artifact[field] <= 0 for field in ("project_id", "file_id")):
            raise ManualArtifactError("manual artifact CurseForge IDs must be positive integers")
        url = artifact["download_page_url"]
        if (
            type(url) is not str
            or not url.startswith("https://www.curseforge.com/")
            or not url.endswith("/" + str(artifact["file_id"]))
        ):
            raise ManualArtifactError("manual artifact download page is invalid")
    return value


def manual_artifact_profile_for_configuration(
    configuration: WorkbenchConfiguration,
) -> Path | None:
    """Resolve an optional pack-owned manual-artifact document safely.

    Configuration/Profile V1 bytes remain immutable.  A V1 pack may therefore
    publish the additive, strictly validated document at the conventional
    ``runtime/manual-artifacts-v1.json`` sibling.  A future profile version may
    instead declare the same canonical relative path explicitly.
    """

    runtime = configuration.pack_document.values.get("runtime")
    if runtime is None:
        relative = "runtime/manual-artifacts-v1.json"
        optional_convention = True
    else:
        if not isinstance(runtime, Mapping) or set(runtime) != {"manual_artifacts"}:
            raise ManualArtifactError("pack profile runtime declaration is invalid")
        relative = _canonical_path(runtime["manual_artifacts"], "manual_artifacts")
        optional_convention = False
    root = configuration.pack_document.source.path.parent.resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    if optional_convention and not candidate.exists() and not candidate.is_symlink():
        return None
    current = root
    try:
        for part in PurePosixPath(relative).parts[:-1]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ManualArtifactError(
                    "manual artifact profile path traverses an unsafe directory"
                )
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ManualArtifactError("manual artifact profile is unavailable") from exc
    if not resolved.is_relative_to(root):
        raise ManualArtifactError("manual artifact profile escapes its pack profile")
    return resolved


def _workspace(path_value: Path | str) -> Path:
    lexical = Path(path_value).expanduser()
    try:
        info = lexical.lstat()
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ManualArtifactError(f"cannot inspect workspace: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ManualArtifactError("workspace must be a regular non-symlink directory")
    return resolved


def _seed_roots(values: Sequence[Path | str]) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        lexical = Path(value).expanduser()
        try:
            info = lexical.lstat()
            root = lexical.resolve(strict=True)
        except OSError as exc:
            raise ManualArtifactError(f"cannot inspect seed root {lexical}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ManualArtifactError(f"seed root is not a regular directory: {lexical}")
        if root not in seen:
            roots.append(root)
            seen.add(root)
    return tuple(roots)


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ManualArtifactError(f"Packwiz metafile is unavailable: {path}") from exc
    if path.is_symlink() or not path.is_file() or not 1 <= info.st_size <= MAX_METADATA_BYTES:
        raise ManualArtifactError(f"Packwiz metafile is not a bounded regular file: {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ManualArtifactError(f"Packwiz metafile is invalid: {path}") from exc


def _metadata_matches(artifact: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    download = metadata.get("download")
    update = metadata.get("update")
    curseforge = update.get("curseforge") if isinstance(update, Mapping) else None
    return bool(
        metadata.get("filename") == artifact["filename"]
        and isinstance(download, Mapping)
        and download.get("mode") == "metadata:curseforge"
        and download.get("hash-format") == artifact["hash_format"]
        and download.get("hash") == artifact["hash"]
        and isinstance(curseforge, Mapping)
        and curseforge.get("project-id") == artifact["project_id"]
        and curseforge.get("file-id") == artifact["file_id"]
    )


def _hash_file(path: Path, algorithm: str) -> tuple[str, int]:
    digest = hashlib.new(algorithm)
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(READ_CHUNK):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ManualArtifactError(f"cannot hash seed artifact: {path}") from exc
    return digest.hexdigest(), size


def inspect_manual_artifacts(
    workspace: Path | str,
    profile: Mapping[str, Any],
    *,
    seed_roots: Sequence[Path | str] = (),
) -> dict[str, Any]:
    """Inspect profile/metafile identity and exact seed availability read-only."""

    root = _workspace(workspace)
    seeds = _seed_roots(seed_roots)
    rows: list[dict[str, Any]] = []
    for declared in profile["artifacts"]:
        artifact = dict(declared)
        metadata_path = root.joinpath(*PurePosixPath(artifact["metadata_path"]).parts)
        try:
            metadata = _read_metadata(metadata_path)
        except ManualArtifactError as exc:
            rows.append(
                {
                    **artifact,
                    "state": "incompatible",
                    "source": None,
                    "size": None,
                    "detail": str(exc),
                }
            )
            continue
        if not _metadata_matches(artifact, metadata):
            rows.append(
                {
                    **artifact,
                    "state": "incompatible",
                    "source": str(metadata_path),
                    "size": None,
                    "detail": "The current Packwiz metafile no longer matches profile authority.",
                }
            )
            continue
        wrong: list[str] = []
        selected: tuple[Path, int] | None = None
        for seed in seeds:
            candidate = seed.joinpath(*PurePosixPath(artifact["output_path"]).parts)
            if not candidate.exists():
                continue
            if candidate.is_symlink() or not candidate.is_file() or not candidate.resolve().is_relative_to(seed):
                wrong.append(f"unsafe file at {candidate}")
                continue
            observed, size = _hash_file(candidate, artifact["hash_format"])
            if observed == artifact["hash"]:
                selected = candidate, size
                break
            wrong.append(f"hash mismatch at {candidate}")
        if selected is not None:
            source, size = selected
            state = "ready"
            detail = "Exact user-supplied bytes are available for verified seeding."
            source_value: str | None = str(source)
            size_value: int | None = size
        elif wrong:
            state = "incompatible"
            detail = "; ".join(wrong)
            source_value = None
            size_value = None
        else:
            state = "missing-manual"
            detail = (
                "Download this file from its publisher page and place it at "
                f"<seed-root>/{artifact['output_path']}."
            )
            source_value = None
            size_value = None
        rows.append(
            {
                **artifact,
                "state": state,
                "source": source_value,
                "size": size_value,
                "detail": detail,
            }
        )
    counts = {
        state: sum(1 for row in rows if row["state"] == state)
        for state in ("ready", "missing-manual", "incompatible")
    }
    state = (
        "incompatible"
        if counts["incompatible"]
        else "attention"
        if counts["missing-manual"]
        else "ready"
    )
    identity = {
        "profile_id": profile["profile_id"],
        "workspace": str(root),
        "seed_roots": [str(seed) for seed in seeds],
        "artifacts": rows,
    }
    return {
        "format": REPORT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        "report_id": "workbench-manual-artifact-preflight:sha256:"
        + sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "state": state,
        "profile_id": profile["profile_id"],
        "project_id": profile["project_id"],
        "workspace": str(root),
        "seed_roots": [str(seed) for seed in seeds],
        "counts": counts,
        "artifacts": rows,
        "next_command": [
            "workbench",
            "runtime",
            "preflight",
            str(root),
            "--profile",
            str(profile["project_id"]),
            *[
                item
                for seed in seeds
                for item in ("--seed", str(seed))
            ],
        ],
    }


def render_manual_artifact_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"Manual file preflight: {report['state']}",
        f"Workspace: {report['workspace']}",
        (
            f"Files: {report['counts']['ready']} ready · "
            f"{report['counts']['missing-manual']} missing · "
            f"{report['counts']['incompatible']} incompatible"
        ),
    ]
    for row in report["artifacts"]:
        lines.extend(
            [
                "",
                f"- {row['display_name']}: {row['state']}",
                f"  Expected: {row['output_path']}",
                f"  Identity: {row['hash_format']}:{row['hash']}",
                f"  Download: {row['download_page_url']}",
                f"  {row['detail']}",
            ]
        )
    if report["state"] == "ready":
        seeds = " ".join(f"--seed {seed}" for seed in report["seed_roots"])
        lines.extend(
            [
                "",
                "Ready to resume:",
                f"  workbench runtime-materialize {report['workspace']} {seeds}".rstrip(),
            ]
        )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench runtime preflight",
        description=(
            "List publisher-hosted files required before Packwiz materialization, "
            "and verify user-supplied seed bytes without downloading anything."
        ),
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--seed", action="append", default=[], type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    profiles: Mapping[str, Path | str],
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    try:
        profile_path = profiles.get(args.profile)
        if profile_path is None:
            raise ManualArtifactError(
                f"unknown profile {args.profile!r}; available: "
                + ", ".join(sorted(profiles))
            )
        profile = load_manual_artifact_profile(profile_path)
        report = inspect_manual_artifacts(
            args.workspace,
            profile,
            seed_roots=args.seed,
        )
        stdout.write(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.json
            else render_manual_artifact_report(report)
        )
        return 0 if report["state"] == "ready" else 1
    except (ManualArtifactError, OSError, ValueError) as exc:
        stderr.write(f"Workbench runtime preflight failed: {exc}\n")
        return 2


__all__ = [
    "ManualArtifactError",
    "PROFILE_FORMAT",
    "REPORT_FORMAT",
    "inspect_manual_artifacts",
    "load_manual_artifact_profile",
    "main",
    "manual_artifact_profile_for_configuration",
    "render_manual_artifact_report",
]
