#!/usr/bin/env python3
"""Assemble and verify a Linux x64 install bundle from reviewed artifacts.

This copies already built bytes; it does not build, qualify, tag, or publish them.
The descriptor binds the outer archive for the download hook. Its hash detects a
changed download, while the trusted release location establishes provenance.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import zipfile

from build_axiom import verify_archive as verify_engine_archive
from build_tree_custody import publish_build_tree
from build_release_clients import (
    ClientBuildError,
    manifest_id as client_manifest_id,
    verify_intellij,
    verify_vscode,
)
from component_versions import load_authority
from native_distribution import selected_components, source_identity
from release_track import artifact_filename
from verify_wheelhouse import verify as verify_wheelhouse


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_FORMAT = "workbench-install-bundle-v1"
DESCRIPTOR_FORMAT = "workbench-install-bundle-descriptor-v1"
CLIENT_BUNDLE_FORMAT = "workbench-install-bundle-v2"
CLIENT_DESCRIPTOR_FORMAT = "workbench-install-bundle-descriptor-v2"
FULL_EDITION = "full-suite"
CLIENT_EDITION = "supersymmetry-client"
CLIENT_ROOT_COMPONENTS = (
    "workbench-core", "workbench-profile-supersymmetry", "workbench-shell",
    "workbench-tui",
)
CLIENT_MANIFEST = "workbench-developer-clients-manifest-v1.json"
TARGET = {"platform": "linux", "machine": "x86_64", "python": "3.14"}
MAX_BUNDLE_FILES = 4096
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_MEMBERS = 16384
MAX_ZIP_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}\Z")


class BundleError(ValueError):
    """A candidate input or archive does not meet the install-bundle contract."""


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _unique_json(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BundleError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise BundleError("expected a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _direct_file(path: Path, *, limit: int = MAX_BUNDLE_BYTES) -> Path:
    path = path.absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise BundleError(f"input path must not cross a symlink: {path}")
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise BundleError(f"input must be a bounded direct file: {path}")
    return path


def _safe_relative(name: str) -> str:
    if not name or "\\" in name or "\0" in name:
        raise BundleError(f"unsafe archive path: {name!r}")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(name).is_absolute():
        raise BundleError(f"unsafe archive path: {name!r}")
    return name


def _inspect_zip(path: Path) -> None:
    """Reject archive redirection and excessive expansion before bundling."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS or sum(row.file_size for row in members) > MAX_ZIP_UNCOMPRESSED:
                raise BundleError(f"ZIP exceeds bundle admission limits: {path.name}")
            names: set[str] = set()
            folded: set[str] = set()
            for row in members:
                name = row.filename.removesuffix("/")
                _safe_relative(name)
                if name in names or name.casefold() in folded:
                    raise BundleError(f"duplicate ZIP member: {path.name}: {name}")
                names.add(name)
                folded.add(name.casefold())
                mode = (row.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise BundleError(f"unsupported ZIP member: {path.name}: {name}")
            if archive.testzip() is not None:
                raise BundleError(f"ZIP integrity check failed: {path.name}")
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleError(f"invalid ZIP input: {path}") from exc


def _git_metadata(root: Path) -> tuple[str, str]:
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"], cwd=root
    )
    if status:
        raise BundleError("release source must be a clean Git checkout")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    if not re.fullmatch(r"[a-f0-9]{40,64}", revision) or not re.fullmatch(r"[a-f0-9]{40,64}", tree):
        raise BundleError("invalid source revision or Git tree identity")
    return revision, tree


def _check_output_location(output_dir: Path, root: Path) -> None:
    """Keep generated release bytes out of the clean source identity."""
    try:
        relative = output_dir.relative_to(root.resolve())
    except ValueError:
        return
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative.as_posix()], cwd=root,
        check=False, capture_output=True,
    )
    if ignored.returncode != 0:
        raise BundleError("release output inside the checkout must be ignored by Git; use .workbench/ or an external path")


def _wheelhouse_files(wheelhouse: Path, *, root: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    wheelhouse = wheelhouse.absolute()
    if wheelhouse.is_symlink() or any(parent.is_symlink() for parent in wheelhouse.parents) or not wheelhouse.is_dir():
        raise BundleError("wheelhouse must be a direct directory")
    manifest = verify_wheelhouse(wheelhouse)
    if manifest["target"] != TARGET:
        raise BundleError("wheelhouse must target Linux x86_64 with Python 3.14")
    if not (wheelhouse / "wheels").is_dir() or (wheelhouse / "wheels").is_symlink():
        raise BundleError("wheel directory must be direct")
    expected = {"LICENSE", "NOTICE.md", "install_workbench.py", "verify_wheelhouse.py", "requirements.lock", "wheelhouse.json"}
    expected_wheels = {row["filename"] for row in manifest["wheels"]}
    if {item.name for item in wheelhouse.iterdir()} != expected | {"wheels"}:
        raise BundleError("wheelhouse has missing or extra top-level entries")
    if {item.name for item in (wheelhouse / "wheels").iterdir()} != expected_wheels:
        raise BundleError("wheelhouse has missing or extra wheels")
    files = {f"wheelhouse/{name}": _direct_file(wheelhouse / name) for name in sorted(expected)}
    for name in ("LICENSE", "NOTICE.md", "install_workbench.py", "verify_wheelhouse.py"):
        source = root / ("tools" if name.endswith(".py") else "") / name
        if _sha256(files[f"wheelhouse/{name}"]) != _sha256(_direct_file(source)):
            raise BundleError(f"wheelhouse {name} differs from reviewed source")
    files.update({f"wheelhouse/wheels/{name}": _direct_file(wheelhouse / "wheels" / name, limit=512 * 1024 * 1024)
                  for name in sorted(expected_wheels)})
    return manifest, files


def _verify_clients(manifest_path: Path, vscode: Path, intellij: Path, *, root: Path) -> dict[str, Path]:
    manifest_path = _direct_file(manifest_path, limit=1024 * 1024)
    manifest = _unique_json(manifest_path.read_bytes())
    if set(manifest) != {"format", "schema_version", "client_artifact_manifest_id", "lane", "artifacts"} or manifest["format"] != "workbench-developer-client-artifact-manifest-v1" or manifest["schema_version"] != 1 or manifest["lane"] != "public-v1":
        raise BundleError("invalid developer-client artifact manifest")
    if manifest["client_artifact_manifest_id"] != client_manifest_id(manifest):
        raise BundleError("developer-client manifest identity differs")
    paths = {"vscode": _direct_file(vscode), "intellij-community": _direct_file(intellij)}
    expected_names = {
        "vscode": artifact_filename(root, "workbench-vscode.vsix"),
        "intellij-community": artifact_filename(root, "workbench-intellij-community.plugin-zip"),
    }
    try:
        verified = {"vscode": verify_vscode(paths["vscode"]), "intellij-community": verify_intellij(paths["intellij-community"])}
    except ClientBuildError as exc:
        raise BundleError(f"developer-client package verification failed: {exc}") from exc
    rows = manifest["artifacts"]
    if not isinstance(rows, list) or len(rows) != 2 or {row.get("client_id") for row in rows if isinstance(row, dict)} != set(paths):
        raise BundleError("bundle needs exactly the VS Code and IntelliJ clients")
    for row in rows:
        client_id = row["client_id"]
        path = paths[client_id]
        if path.name != expected_names[client_id] or row.get("path") != path.name or row.get("size") != path.stat().st_size or row.get("sha256") != _sha256(path) or row.get("artifact_identity") != "artifact:sha256:" + row["sha256"]:
            raise BundleError(f"developer-client artifact differs: {client_id}")
        if any(row.get(key) != value for key, value in verified[client_id].items()):
            raise BundleError(f"developer-client package identity differs: {client_id}")
    return {
        f"clients/{CLIENT_MANIFEST}": manifest_path,
        f"clients/{paths['vscode'].name}": paths["vscode"],
        f"clients/{paths['intellij-community'].name}": paths["intellij-community"],
    }


def _verify_engine(engine: Path, *, root: Path) -> Path:
    engine = _direct_file(engine)
    if engine.name != artifact_filename(root, "workbench-axiom-engine.distribution"):
        raise BundleError("Axiom engine filename differs from its native version authority")
    _inspect_zip(engine)
    verify_engine_archive(engine)
    expected_version = load_authority(root)[1]["workbench-axiom-engine"]["version"]
    prefix = engine.stem + "/"
    with zipfile.ZipFile(engine) as archive:
        manifest_name = prefix + "engine-manifest.json"
        if manifest_name not in archive.namelist() or archive.getinfo(manifest_name).file_size > 1024 * 1024:
            raise BundleError("Axiom engine manifest is missing or excessive")
        inner = _unique_json(archive.read(manifest_name))
        if (inner.get("schema") != "axiom.installation.v1"
                or inner.get("component") != "workbench-axiom-engine"
                or inner.get("version") != expected_version):
            raise BundleError("Axiom engine identity differs from native authority")
        jars = inner.get("jars")
        if not isinstance(jars, dict) or not 0 < len(jars) <= 1024:
            raise BundleError("Axiom engine has no bounded library inventory")
        observed = {name.removeprefix(prefix + "lib/") for name in archive.namelist()
                    if name.startswith(prefix + "lib/") and name.endswith(".jar")}
        if set(jars) != observed:
            raise BundleError("Axiom engine library inventory differs")
        for name, digest in jars.items():
            if (not isinstance(name, str) or name != Path(name).name or not name.endswith(".jar")
                    or not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest)
                    or _sha256_zip_member(archive, prefix + "lib/" + name) != digest):
                raise BundleError(f"Axiom engine library differs: {name}")
    return engine


def _sha256_zip_member(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.uid = info.gid = info.mtime = 0
    info.uname = info.gname = ""
    info.mode = 0o644
    return info


def verify_bundle_archive(archive_path: Path, descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify archive paths and every inner file digest without extracting it."""
    archive_path = _direct_file(archive_path)
    if descriptor is not None:
        descriptor_format = descriptor.get("format")
        if (descriptor_format not in {DESCRIPTOR_FORMAT, CLIENT_DESCRIPTOR_FORMAT}
                or (descriptor_format == CLIENT_DESCRIPTOR_FORMAT
                    and descriptor.get("edition") != CLIENT_EDITION)
                or descriptor.get("archive_filename") != archive_path.name
                or descriptor.get("archive_sha256") != _sha256(archive_path)
                or descriptor.get("bundle_directory") != "workbench-linux-x64-py314"
                or descriptor.get("target") != TARGET
                or descriptor.get("qualified") is not False):
            raise BundleError("outer archive differs from install descriptor")
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if not 2 <= len(members) <= MAX_BUNDLE_FILES + 1:
                raise BundleError("bundle member count is invalid")
            names = [row.name for row in members]
            if len(names) != len(set(names)) or len({name.casefold() for name in names}) != len(names):
                raise BundleError("duplicate or case-colliding bundle path")
            prefixes = {name.split("/", 1)[0] for name in names}
            if len(prefixes) != 1:
                raise BundleError("bundle must have one top-level folder")
            prefix = prefixes.pop()
            if prefix != "workbench-linux-x64-py314":
                raise BundleError("bundle directory differs from the install contract")
            for member in members:
                _safe_relative(member.name)
                if not member.isfile() or member.size <= 0 or member.size > MAX_BUNDLE_BYTES:
                    raise BundleError(f"invalid bundle member: {member.name}")
            manifest_name = prefix + "/BUNDLE-MANIFEST.json"
            if names.count(manifest_name) != 1:
                raise BundleError("bundle manifest is missing")
            manifest_file = archive.extractfile(manifest_name)
            if manifest_file is None:
                raise BundleError("bundle manifest is unreadable")
            manifest_raw = manifest_file.read(1024 * 1024 + 1)
            if len(manifest_raw) > 1024 * 1024:
                raise BundleError("bundle manifest is too large")
            manifest = _unique_json(manifest_raw)
            bundle_format = manifest.get("format")
            if (bundle_format not in {BUNDLE_FORMAT, CLIENT_BUNDLE_FORMAT}
                    or manifest.get("state") != "assembled"
                    or manifest.get("target") != TARGET
                    or (bundle_format == CLIENT_BUNDLE_FORMAT
                        and (manifest.get("edition") != CLIENT_EDITION
                             or manifest.get("selected_components") != list(CLIENT_ROOT_COMPONENTS)))):
                raise BundleError("invalid install-bundle manifest")
            if descriptor is not None and (
                (descriptor["format"] == DESCRIPTOR_FORMAT and bundle_format != BUNDLE_FORMAT)
                or (descriptor["format"] == CLIENT_DESCRIPTOR_FORMAT
                    and bundle_format != CLIENT_BUNDLE_FORMAT)
            ):
                raise BundleError("bundle edition differs from install descriptor")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) != len(members) - 1 or len(files) > MAX_BUNDLE_FILES:
                raise BundleError("bundle manifest does not cover all files")
            records: dict[str, dict[str, Any]] = {}
            for row in files:
                if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"} or not isinstance(row["path"], str) or type(row["size"]) is not int or not isinstance(row["sha256"], str) or not SHA256_PATTERN.fullmatch(row["sha256"]):
                    raise BundleError("invalid bundle file record")
                name = _safe_relative(row["path"])
                if name in records or name == "BUNDLE-MANIFEST.json":
                    raise BundleError("duplicate or self-referential bundle file record")
                records[name] = row
            if bundle_format == CLIENT_BUNDLE_FORMAT and (
                not {
                    "GETTING-STARTED.md", "verify_install_bundle.py",
                    "wheelhouse/install_workbench.py", "wheelhouse/verify_wheelhouse.py",
                    "wheelhouse/wheelhouse.json", "wheelhouse/requirements.lock",
                } <= set(records)
                or any(name.startswith(("axiom/", "clients/")) for name in records)
            ):
                raise BundleError("Supersymmetry client bundle has missing or excluded files")
            if {name.removeprefix(prefix + "/") for name in names if name != manifest_name} != set(records):
                raise BundleError("bundle has missing or extra members")
            if sum(row.size for row in members) > MAX_BUNDLE_BYTES:
                raise BundleError("expanded bundle is too large")
            for member in members:
                if member.name == manifest_name:
                    continue
                relative = member.name.removeprefix(prefix + "/")
                row = records[relative]
                if member.size != row["size"]:
                    raise BundleError(f"bundle file size differs: {relative}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise BundleError(f"bundle file is unreadable: {relative}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                if digest.hexdigest() != row["sha256"]:
                    raise BundleError(f"bundle file digest differs: {relative}")
            if descriptor is not None and descriptor.get("release_tag") != manifest.get("release_tag"):
                raise BundleError("bundle release tag differs from install descriptor")
            return manifest
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise BundleError("invalid install-bundle archive") from exc


def _assemble_into(*, wheelhouse: Path, guide: Path, release_tag: str,
                   output_dir: Path, edition: str = FULL_EDITION,
                   engine_zip: Path | None = None, vscode_vsix: Path | None = None,
                   intellij_zip: Path | None = None,
                   clients_manifest: Path | None = None,
                   root: Path = ROOT) -> dict[str, Any]:
    if not TAG_PATTERN.fullmatch(release_tag) or ".." in release_tag:
        raise BundleError("release tag must be a safe single path component")
    output_dir = output_dir.absolute()
    if output_dir.exists() or output_dir.is_symlink() or any(parent.is_symlink() for parent in output_dir.parents):
        raise BundleError("output must be a new direct directory")
    _check_output_location(output_dir, root)
    revision, tree_oid = _git_metadata(root)
    native, files = _wheelhouse_files(wheelhouse, root=root)
    if native["source_sha256"] != source_identity(root):
        raise BundleError("wheelhouse was built from different source inputs")
    if edition not in {FULL_EDITION, CLIENT_EDITION}:
        raise BundleError("unsupported install-bundle edition")
    _, authority = load_authority(root)
    if edition == FULL_EDITION:
        expected_versions = {name: row["version"] for name, row in authority.items() if row["kind"] == "python"}
        tui = authority.get("workbench-tui")
        if tui is None or tui["kind"] != "python-client":
            raise BundleError("native TUI authority is missing")
        expected_versions["workbench-tui"] = tui["version"]
        expected_selection = set(expected_versions)
        if any(value is None for value in (engine_zip, vscode_vsix, intellij_zip, clients_manifest)):
            raise BundleError("full Suite bundle requires the engine and both IDE clients")
    else:
        if any(value is not None for value in (engine_zip, vscode_vsix, intellij_zip, clients_manifest)):
            raise BundleError("Supersymmetry client bundle excludes engine and IDE artifacts")
        _roots, closure = selected_components(CLIENT_ROOT_COMPONENTS, root=root)
        expected_versions = {row["id"]: row["version"] for row in closure}
        expected_selection = set(CLIENT_ROOT_COMPONENTS)
    actual_versions = native["native_versions"]
    if actual_versions != expected_versions or set(native["selected_components"]) != expected_selection:
        if edition == FULL_EDITION:
            raise BundleError("wheelhouse must select the current full native Suite and TUI")
        raise BundleError("wheelhouse must select the exact Supersymmetry client closure")
    if edition == FULL_EDITION:
        assert engine_zip is not None and vscode_vsix is not None
        assert intellij_zip is not None and clients_manifest is not None
        engine = _verify_engine(engine_zip, root=root)
        files[f"axiom/{engine.name}"] = engine
        files.update(_verify_clients(clients_manifest, vscode_vsix, intellij_zip, root=root))
    guide = _direct_file(guide, limit=1024 * 1024)
    files["GETTING-STARTED.md"] = guide
    files["verify_install_bundle.py"] = _direct_file(root / "tools/verify_install_bundle.py", limit=1024 * 1024)
    if len(files) > MAX_BUNDLE_FILES or sum(path.stat().st_size for path in files.values()) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle input count or size exceeds bounds")
    records = [{"path": name, "size": path.stat().st_size, "sha256": _sha256(path)} for name, path in sorted(files.items())]
    manifest = {
        "format": BUNDLE_FORMAT if edition == FULL_EDITION else CLIENT_BUNDLE_FORMAT,
        "state": "assembled",
        "git_commit": revision,
        "git_tree_oid": tree_oid,
        "release_tag": release_tag,
        "target": TARGET,
        "source_sha256": native["source_sha256"],
        "wheelhouse_manifest_sha256": _sha256(Path(wheelhouse) / "wheelhouse.json"),
        "native_versions": actual_versions,
        "files": records,
    }
    if edition == CLIENT_EDITION:
        manifest["edition"] = CLIENT_EDITION
        manifest["selected_components"] = list(CLIENT_ROOT_COMPONENTS)
    manifest_raw = _json_bytes(manifest)
    folder = "workbench-linux-x64-py314"
    archive_name = f"workbench-linux-x64-py314-{release_tag}.tar.gz"
    output_dir.mkdir(parents=True)
    archive_path = output_dir / archive_name
    with archive_path.open("wb") as output, gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.addfile(_tar_info(f"{folder}/BUNDLE-MANIFEST.json", len(manifest_raw)), io.BytesIO(manifest_raw))
        for row in records:
            path = files[row["path"]]
            with path.open("rb") as stream:
                archive.addfile(_tar_info(f"{folder}/{row['path']}", row["size"]), stream)
    result = verify_bundle_archive(archive_path)
    if result != manifest:
        raise BundleError("assembled archive manifest differs")
    # Exercise the same standalone verifier that the downloaded hook will run.
    with tempfile.TemporaryDirectory(prefix="workbench-bundle-verify-") as temporary:
        extraction = Path(temporary)
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                destination = extraction / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BundleError(f"cannot extract bundle member: {member.name}")
                with destination.open("xb") as output:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        output.write(chunk)
        packaged_verify = runpy.run_path(str(extraction / folder / "verify_install_bundle.py"))["verify"]
        if packaged_verify(extraction / folder, release_tag=release_tag) != manifest:
            raise BundleError("extracted bundle verification differs")
    if _git_metadata(root) != (revision, tree_oid) or source_identity(root) != native["source_sha256"]:
        raise BundleError("source changed while assembling the install bundle")
    if any(path.stat().st_size != row["size"] or _sha256(path) != row["sha256"]
           for row in records for path in (files[row["path"]],)):
        raise BundleError("candidate input changed while assembling the install bundle")
    descriptor = {
        "format": DESCRIPTOR_FORMAT if edition == FULL_EDITION else CLIENT_DESCRIPTOR_FORMAT,
        "release_tag": release_tag,
        "target": TARGET,
        "archive_filename": archive_name,
        "archive_sha256": _sha256(archive_path),
        "bundle_directory": folder,
        "qualified": False,
    }
    if edition == CLIENT_EDITION:
        descriptor["edition"] = CLIENT_EDITION
    verify_bundle_archive(archive_path, descriptor)
    (output_dir / "workbench-linux-x64-py314-install.json").write_bytes(_json_bytes(descriptor))
    return descriptor


def _verify_output(output_dir: Path, descriptor: dict[str, Any]) -> None:
    """Check the exact two-file release candidate before and after Core publication."""
    descriptor_path = output_dir / "workbench-linux-x64-py314-install.json"
    if {entry.name for entry in output_dir.iterdir()} != {
        descriptor_path.name, descriptor["archive_filename"]
    }:
        raise BundleError("install bundle output has missing or extra files")
    if _unique_json(_direct_file(descriptor_path, limit=1024 * 1024).read_bytes()) != descriptor:
        raise BundleError("install bundle descriptor changed during Core publication")
    verify_bundle_archive(output_dir / descriptor["archive_filename"], descriptor)


def assemble_managed(*, wheelhouse: Path, guide: Path, release_tag: str,
                     output_dir: Path, edition: str = FULL_EDITION,
                     engine_zip: Path | None = None, vscode_vsix: Path | None = None,
                     intellij_zip: Path | None = None,
                     clients_manifest: Path | None = None, root: Path = ROOT,
                     configuration_home: Path | None = None):
    """Assemble one candidate, then publish its exact bytes through Core."""
    output_dir = Path(output_dir).absolute()
    if output_dir.exists() or output_dir.is_symlink() or any(parent.is_symlink() for parent in output_dir.parents):
        raise BundleError("output must be a new direct directory")
    _check_output_location(output_dir, root)
    return publish_build_tree(
        output_dir,
        lambda staged: _assemble_into(
            wheelhouse=wheelhouse, engine_zip=engine_zip, vscode_vsix=vscode_vsix,
            intellij_zip=intellij_zip, clients_manifest=clients_manifest,
            guide=guide, release_tag=release_tag, output_dir=staged,
            edition=edition, root=root,
        ),
        _verify_output,
        lambda _path, result: "workbench-install-bundle-v1:sha256:"
        + result["archive_sha256"],
        owner_id="install-bundle-build",
        workspace=root,
        configuration_home=configuration_home,
    )


def assemble(*, wheelhouse: Path, guide: Path, release_tag: str,
             output_dir: Path, edition: str = FULL_EDITION,
             engine_zip: Path | None = None, vscode_vsix: Path | None = None,
             intellij_zip: Path | None = None,
             clients_manifest: Path | None = None, root: Path = ROOT,
             configuration_home: Path | None = None) -> dict[str, Any]:
    """Retain the V1 descriptor return while Core owns candidate publication."""
    descriptor, _reference = assemble_managed(
        wheelhouse=wheelhouse, engine_zip=engine_zip, vscode_vsix=vscode_vsix,
        intellij_zip=intellij_zip, clients_manifest=clients_manifest,
        guide=guide, release_tag=release_tag, output_dir=output_dir,
        edition=edition, root=root,
        configuration_home=configuration_home,
    )
    return descriptor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("assemble", help="assemble already built candidate bytes")
    build.add_argument("--wheelhouse", type=Path, required=True)
    build.add_argument("--edition", choices=(FULL_EDITION, CLIENT_EDITION), default=FULL_EDITION)
    build.add_argument("--engine-zip", type=Path)
    build.add_argument("--vscode-vsix", type=Path)
    build.add_argument("--intellij-zip", type=Path)
    build.add_argument("--clients-manifest", type=Path)
    build.add_argument("--guide", type=Path, required=True)
    build.add_argument("--release-tag", required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    check = subparsers.add_parser("verify", help="verify an assembled archive and descriptor")
    check.add_argument("--archive", type=Path, required=True)
    check.add_argument("--descriptor", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "assemble":
            descriptor, reference = assemble_managed(
                wheelhouse=arguments.wheelhouse, engine_zip=arguments.engine_zip,
                vscode_vsix=arguments.vscode_vsix, intellij_zip=arguments.intellij_zip,
                clients_manifest=arguments.clients_manifest, guide=arguments.guide,
                release_tag=arguments.release_tag, output_dir=arguments.output_dir,
                edition=arguments.edition,
            )
            print(json.dumps({**descriptor, "artifact_tree_id": reference.tree_id,
                              "artifact_path": str(reference.path)}, indent=2, sort_keys=True))
        else:
            path = _direct_file(arguments.descriptor, limit=1024 * 1024)
            descriptor = _unique_json(path.read_bytes())
            verify_bundle_archive(arguments.archive, descriptor)
            print(json.dumps(descriptor, indent=2, sort_keys=True))
        return 0
    except (BundleError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"install bundle error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
