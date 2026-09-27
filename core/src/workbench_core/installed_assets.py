"""Read verified assets retained beside a Linux bundle installation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any


FORMAT = "workbench-installed-axiom-engine-v1"
_BUNDLE_FORMATS = {"workbench-install-bundle-v1", "workbench-install-bundle-v2"}
_ENGINE = re.compile(r"axiom/workbench-axiom-engine-[A-Za-z0-9._-]+\.zip\Z")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class InstalledAssetError(ValueError):
    """The local installation cannot supply a verified bundled asset."""


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _record(path: Path) -> dict[str, Any]:
    if not _regular(path) or path.stat().st_size > 1024 * 1024:
        raise InstalledAssetError(f"missing or unsafe install record: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstalledAssetError(f"unreadable install record: {path.name}") from exc
    if not isinstance(value, dict):
        raise InstalledAssetError(f"invalid install record: {path.name}")
    return value


def _verified_axiom_engine(installation: Path) -> tuple[Path, str, int, str]:
    if not _directory(installation) or installation.parent.name != "installs":
        raise InstalledAssetError("this Workbench is not running from a bundled installation")
    hook = _record(installation / "workbench-hook.json")
    installed = _record(installation / "workbench-install.json")
    tag = hook.get("release_tag")
    if (hook.get("format") != "workbench-hook-install-v1"
            or hook.get("state") != "installed"
            or not isinstance(tag, str) or _TAG.fullmatch(tag) is None
            or tag != installation.name
            or not isinstance(hook.get("archive_sha256"), str)
            or _DIGEST.fullmatch(hook["archive_sha256"]) is None
            or installed.get("format") != "workbench-native-install-v1"
            or installed.get("state") != "installed"):
        raise InstalledAssetError("installed Workbench receipts do not match this release")
    bundles = installation.parent.parent / "bundles"
    bundle = bundles / tag
    if not _directory(bundles) or not _directory(bundle) or not _directory(bundle / "axiom"):
        raise InstalledAssetError("the retained release bundle or Axiom directory is missing")
    manifest = _record(bundle / "BUNDLE-MANIFEST.json")
    if (manifest.get("format") not in _BUNDLE_FORMATS
            or manifest.get("state") != "assembled"
            or manifest.get("release_tag") != tag):
        raise InstalledAssetError("the retained bundle differs from the installed release")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise InstalledAssetError("the retained bundle has no file inventory")
    wheelhouse_path = bundle / "wheelhouse/wheelhouse.json"
    if not _regular(wheelhouse_path) or wheelhouse_path.stat().st_size > 1024 * 1024:
        raise InstalledAssetError("the retained wheelhouse manifest is missing or unsafe")
    try:
        wheelhouse_raw = wheelhouse_path.read_bytes()
        wheelhouse_record = json.loads(wheelhouse_raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstalledAssetError("the retained wheelhouse manifest is unreadable") from exc
    if not isinstance(wheelhouse_record, dict):
        raise InstalledAssetError("the retained wheelhouse manifest is invalid")
    raw_digest = sha256(wheelhouse_raw).hexdigest()
    wheelhouse_entries = [row for row in files if isinstance(row, dict)
                          and row.get("path") == "wheelhouse/wheelhouse.json"]
    installed_digest = sha256(json.dumps(
        wheelhouse_record, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    if (len(wheelhouse_entries) != 1
            or wheelhouse_entries[0].get("size") != len(wheelhouse_raw)
            or wheelhouse_entries[0].get("sha256") != raw_digest
            or manifest.get("wheelhouse_manifest_sha256") != raw_digest
            or installed.get("wheelhouse_manifest_sha256") != installed_digest):
        raise InstalledAssetError("the retained bundle differs from the installed release")
    engines = [row for row in files if isinstance(row, dict)
               and isinstance(row.get("path"), str) and _ENGINE.fullmatch(row["path"])]
    if len(engines) != 1:
        raise InstalledAssetError("the retained bundle has no unique Axiom engine ZIP")
    entry = engines[0]
    relative = entry["path"]
    if (manifest.get("format") == "workbench-install-bundle-v2"
            and (manifest.get("edition") != "supersymmetry-client"
                 or manifest.get("engine_archive") != relative)):
        raise InstalledAssetError("the client bundle engine selection differs from its manifest")
    size, digest = entry.get("size"), entry.get("sha256")
    if (type(size) is not int or size <= 0 or size > 2 * 1024**3
            or not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None):
        raise InstalledAssetError("the Axiom engine manifest record is invalid")
    archive = bundle / relative
    if not _regular(archive) or archive.stat().st_size != size:
        raise InstalledAssetError("the retained Axiom engine ZIP is missing or changed")
    observed = sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            observed.update(chunk)
    if observed.hexdigest() != digest:
        raise InstalledAssetError("the retained Axiom engine ZIP differs from its manifest")
    return archive, digest, size, tag


def resolve_axiom_engine_source(*, installation: Path | None = None) -> dict[str, Any]:
    """Offer an exact bundle ZIP only after verifying installed receipts and bytes."""
    selected = Path(sys.prefix if installation is None else installation).absolute()
    try:
        archive, digest, size, tag = _verified_axiom_engine(selected)
    except (InstalledAssetError, OSError) as exc:
        return {"format": FORMAT, "state": "unavailable", "archive_path": None,
                "sha256": None, "size": None, "release_tag": None, "reason": str(exc)}
    return {"format": FORMAT, "state": "verified", "archive_path": str(archive),
            "sha256": digest, "size": size, "release_tag": tag, "reason": None}


__all__ = ["FORMAT", "InstalledAssetError", "resolve_axiom_engine_source"]
