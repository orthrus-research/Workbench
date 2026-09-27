#!/usr/bin/env python3
"""Verify an extracted Workbench install bundle before running its installer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
import sys


FORMAT = "workbench-install-bundle-v1"
CLIENT_FORMAT = "workbench-install-bundle-v2"
CLIENT_EDITION = "supersymmetry-client"
CLIENT_ROOT_COMPONENTS = [
    "workbench-atlas", "workbench-axiom", "workbench-core",
    "workbench-profile-supersymmetry", "workbench-shell", "workbench-tui",
]
CLIENT_NATIVE_COMPONENTS = {
    "workbench-api", "workbench-atlas", "workbench-axiom", "workbench-core", "workbench-crucible",
    "workbench-material-semantics", "workbench-pack-program-studio",
    "workbench-profile-cleanroom", "workbench-profile-supersymmetry",
    "workbench-project-intelligence", "workbench-runtime-explorer",
    "workbench-shell", "workbench-tui",
}
TARGET = {"python": "3.14", "platform": "linux", "machine": "x86_64"}
MANIFEST = "BUNDLE-MANIFEST.json"


class BundleError(ValueError):
    """The bundle does not match its declared contents or target."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_path(text: str) -> PurePosixPath:
    if not isinstance(text, str) or not text or "\\" in text or "\x00" in text:
        raise BundleError("invalid bundle path")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in text.split("/")):
        raise BundleError(f"unsafe bundle path: {text}")
    if path.as_posix() != text or text == MANIFEST:
        raise BundleError(f"noncanonical bundle path: {text}")
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: Path, *, release_tag: str | None = None) -> dict:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise BundleError("bundle root must be an ordinary directory")
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BundleError("bundle manifest is missing or linked")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError("bundle manifest is unreadable") from exc
    required = {"format", "state", "release_tag", "target", "source_sha256", "files"}
    if not isinstance(manifest, dict) or not required <= manifest.keys():
        raise BundleError("bundle manifest has missing fields")
    bundle_format = manifest["format"]
    if bundle_format not in {FORMAT, CLIENT_FORMAT} or manifest["state"] != "assembled":
        raise BundleError("unsupported bundle manifest")
    if bundle_format == CLIENT_FORMAT and (
        manifest.get("edition") != CLIENT_EDITION
        or manifest.get("selected_components") != CLIENT_ROOT_COMPONENTS
    ):
        raise BundleError("unsupported Supersymmetry client bundle contract")
    if manifest["target"] != TARGET:
        raise BundleError("bundle target differs from the supported installer target")
    tag = manifest["release_tag"]
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tag):
        raise BundleError("invalid release tag")
    if release_tag is not None and tag != release_tag:
        raise BundleError("bundle release tag differs from the install hook")
    if not isinstance(manifest["source_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["source_sha256"]):
        raise BundleError("invalid source digest")
    records = manifest["files"]
    if not isinstance(records, list) or not records:
        raise BundleError("bundle has no file inventory")
    declared = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            raise BundleError("invalid bundle file record")
        relative = _safe_path(record["path"])
        if relative.as_posix() in declared:
            raise BundleError(f"duplicate bundle file: {relative}")
        if type(record["size"]) is not int or not 0 <= record["size"] <= 2 * 1024**3:
            raise BundleError(f"invalid bundle file size: {relative}")
        if not isinstance(record["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
            raise BundleError(f"invalid bundle file digest: {relative}")
        declared[relative.as_posix()] = record
    if bundle_format == CLIENT_FORMAT and (
        not {
            "GETTING-STARTED.md", "verify_install_bundle.py",
            "wheelhouse/install_workbench.py", "wheelhouse/verify_wheelhouse.py",
            "wheelhouse/wheelhouse.json", "wheelhouse/requirements.lock",
        } <= set(declared)
        or any(path.startswith("clients/") for path in declared)
        or len([path for path in declared if path.startswith("axiom/")]) != 1
        or not isinstance(manifest.get("engine_archive"), str)
        or manifest.get("engine_archive") not in declared
        or not str(manifest.get("engine_archive", "")).startswith("axiom/workbench-axiom-engine-")
        or not str(manifest.get("engine_archive", "")).endswith(".zip")
    ):
        raise BundleError("Supersymmetry client bundle needs one verified Axiom engine and no IDE clients")
    actual = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise BundleError(f"bundle contains a link or special file: {relative}")
        if relative != MANIFEST:
            actual.add(relative)
    if actual != set(declared):
        raise BundleError("bundle file inventory differs from the manifest")
    for relative, record in declared.items():
        path = root / relative
        if path.stat().st_size != record["size"] or _hash(path) != record["sha256"]:
            raise BundleError(f"bundle file differs: {relative}")
    wheelhouse = root / "wheelhouse"
    verifier = wheelhouse / "verify_wheelhouse.py"
    if not verifier.is_file():
        raise BundleError("wheelhouse verifier is missing")
    try:
        verify_wheelhouse = runpy.run_path(str(verifier))["verify"]
        wheel_manifest = verify_wheelhouse(wheelhouse)
    except (OSError, ValueError, KeyError) as exc:
        raise BundleError(f"wheelhouse verification failed: {exc}") from exc
    if wheel_manifest["target"] != TARGET or wheel_manifest["source_sha256"] != manifest["source_sha256"]:
        raise BundleError("wheelhouse target or source differs from the bundle")
    versions = wheel_manifest["native_versions"]
    selected = wheel_manifest["selected_components"]
    if versions != manifest.get("native_versions") or not isinstance(selected, list):
        raise BundleError("bundle native component inventory differs")
    if bundle_format == FORMAT:
        if (set(selected) != set(versions)
                or not {"workbench-core", "workbench-tui"} <= set(selected)):
            raise BundleError("bundle must select the declared Suite and terminal client")
    elif (selected != CLIENT_ROOT_COMPONENTS
          or set(versions) != CLIENT_NATIVE_COMPONENTS):
        raise BundleError("bundle must select the exact Supersymmetry client closure")
    wheelhouse_digest = manifest.get("wheelhouse_manifest_sha256")
    if (not isinstance(wheelhouse_digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", wheelhouse_digest)
            or _hash(wheelhouse / "wheelhouse.json") != wheelhouse_digest):
        raise BundleError("wheelhouse manifest differs from the bundle")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--release-tag")
    args = parser.parse_args(argv)
    try:
        manifest = verify(args.bundle, release_tag=args.release_tag)
    except (BundleError, OSError) as exc:
        print(f"install bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"release_tag": manifest["release_tag"], "target": manifest["target"], "state": "verified"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
