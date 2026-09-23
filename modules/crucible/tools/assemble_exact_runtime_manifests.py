#!/usr/bin/env python3
"""Assemble closed V1 environment manifests for an exact runtime case.

The session auditor deliberately does not discover or guess its inputs.  This
companion tool inventories one already-staged server directory, hashes every
installed mod and configuration file, and writes the two caller-pinned
manifests consumed by ``audit_exact_runtime_session.py``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    FIXED_WORLD_SEED,
    FIXED_WORLD_SEED_SHA256,
)


CONFIGURATION_FORMAT = "workbench-cleanroom-configuration-set-v1"
INSTALLED_MOD_SET_FORMAT = "workbench-cleanroom-installed-mod-set-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_CONFIG_FILE_BYTES = 256 * 1024 * 1024
_MAX_MOD_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _FileReceipt:
    size: int
    sha256: str


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _plain_directory(path: str | os.PathLike[str], context: str) -> Path:
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {candidate}: {exc}") from exc
    _require(not stat.S_ISLNK(metadata.st_mode), f"{context} must not be a symlink")
    _require(stat.S_ISDIR(metadata.st_mode), f"{context} must be a directory")
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise CaptureValidationError(f"cannot resolve {context} {candidate}: {exc}") from exc


def _hash_regular_file(path: Path, *, context: str, maximum_size: int) -> _FileReceipt:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISREG(before.st_mode), f"{context} must be a regular file: {path}")
    _require(0 < before.st_size <= maximum_size, f"{context} size is outside manifest bounds")
    expected_identity = _identity(before)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(_HASH_CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
        after = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot hash {context} {path}: {exc}") from exc
    _require(_identity(after) == expected_identity, f"{context} changed while hashing: {path}")
    return _FileReceipt(before.st_size, digest.hexdigest())


def _inventory(
    directory: Path,
    *,
    prefix: str,
    context: str,
    maximum_size: int,
) -> list[dict[str, str]]:
    root = _plain_directory(directory, context)
    try:
        entries = tuple(root.rglob("*"))
    except OSError as exc:
        raise CaptureValidationError(f"cannot enumerate {context} {root}: {exc}") from exc
    rows: list[dict[str, str]] = []
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise CaptureValidationError(f"cannot inspect {context} entry {entry}: {exc}") from exc
        _require(not stat.S_ISLNK(metadata.st_mode), f"{context} contains a symlink: {entry}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        _require(stat.S_ISREG(metadata.st_mode), f"{context} contains a non-file: {entry}")
        relative = entry.relative_to(root).as_posix()
        receipt = _hash_regular_file(
            entry,
            context=f"{context} file {relative}",
            maximum_size=maximum_size,
        )
        rows.append({"relative_path": prefix + relative, "sha256": receipt.sha256})
    rows.sort(key=lambda row: row["relative_path"])
    _require(
        len(rows) == len({row["relative_path"] for row in rows}),
        f"{context} contains duplicate canonical paths",
    )
    return rows


def build_installed_mod_set_manifest(
    server_directory: str | os.PathLike[str],
    *,
    candidate_lock_sha256: str,
    runtime_class_source_sha256: str,
) -> dict[str, Any]:
    """Inventory every regular file below ``server/mods``."""

    server = _plain_directory(server_directory, "server directory")
    artifacts = _inventory(
        server / "mods",
        prefix="mods/",
        context="server mods directory",
        maximum_size=_MAX_MOD_BYTES,
    )
    _require(bool(artifacts), "server mods directory is empty")
    return {
        "format": INSTALLED_MOD_SET_FORMAT,
        "candidate_lock_sha256": _sha256(candidate_lock_sha256, "candidate lock digest"),
        "runtime_class_source_sha256": _sha256(
            runtime_class_source_sha256, "runtime class source digest"
        ),
        "installed_artifacts": artifacts,
    }


def build_configuration_set_manifest(
    server_directory: str | os.PathLike[str],
    *,
    case_id: str,
    route_order: str,
    probe_enabled: bool,
    probe_capture_id: str | None,
) -> dict[str, Any]:
    """Inventory ``server.properties`` and every regular file below ``config``."""

    _require(
        isinstance(case_id, str) and _CASE_ID_RE.fullmatch(case_id) is not None,
        "invalid case ID",
    )
    _require(route_order in {"forward", "reverse"}, "route order must be forward or reverse")
    _require(isinstance(probe_enabled, bool), "probe enabled must be a boolean")
    if probe_enabled:
        _require(
            isinstance(probe_capture_id, str) and bool(probe_capture_id),
            "enabled probe requires a capture ID",
        )
    else:
        _require(probe_capture_id is None, "disabled probe forbids a capture ID")

    server = _plain_directory(server_directory, "server directory")
    properties = _hash_regular_file(
        server / "server.properties",
        context="server.properties",
        maximum_size=_MAX_CONFIG_FILE_BYTES,
    )
    files = _inventory(
        server / "config",
        prefix="config/",
        context="server configuration directory",
        maximum_size=_MAX_CONFIG_FILE_BYTES,
    )
    files.append({"relative_path": "server.properties", "sha256": properties.sha256})
    files.sort(key=lambda row: row["relative_path"])

    settings: dict[str, Any] = {
        "foundation_dump": True,
        "main_class": "com.cleanroommc.boot.MainServer",
        "fixture_driver_enabled": True,
        "fixture_expected_seed": str(FIXED_WORLD_SEED),
        "fixture_expected_seed_sha256": FIXED_WORLD_SEED_SHA256,
        "fixture_route_order": route_order,
        "fixture_result_relative_path": "fixture-result.json",
        "probe_enabled": probe_enabled,
        "probe_capture_id": probe_capture_id,
        "probe_mode": "lossless-fixture" if probe_enabled else None,
        "probe_output_relative_path": "raw.ndjson" if probe_enabled else None,
    }
    return {
        "format": CONFIGURATION_FORMAT,
        "case_id": case_id,
        "files": files,
        "runtime_settings": settings,
    }


def write_new_manifest(path: str | os.PathLike[str], manifest: Mapping[str, Any]) -> str:
    """Atomically create, but never replace, one canonical manifest."""

    _require(isinstance(manifest, Mapping), "manifest must be an object")
    output = Path(path).resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(manifest) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_name, output)
        except FileExistsError as exc:
            raise CaptureValidationError(f"manifest output already exists: {output}") from exc
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return canonical_json_sha256(manifest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    installed = commands.add_parser("installed-mod-set", help="inventory server/mods")
    installed.add_argument("--server", required=True, type=Path)
    installed.add_argument("--candidate-lock-sha256", required=True)
    installed.add_argument("--runtime-class-source-sha256", required=True)
    installed.add_argument("--output", required=True, type=Path)

    configuration = commands.add_parser(
        "configuration-set", help="inventory server configuration"
    )
    configuration.add_argument("--server", required=True, type=Path)
    configuration.add_argument("--case-id", required=True)
    configuration.add_argument("--route-order", choices=("forward", "reverse"), required=True)
    configuration.add_argument(
        "--observer", choices=("enabled", "disabled"), required=True
    )
    configuration.add_argument("--probe-capture-id")
    configuration.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "installed-mod-set":
            manifest = build_installed_mod_set_manifest(
                arguments.server,
                candidate_lock_sha256=arguments.candidate_lock_sha256,
                runtime_class_source_sha256=arguments.runtime_class_source_sha256,
            )
        else:
            enabled = arguments.observer == "enabled"
            manifest = build_configuration_set_manifest(
                arguments.server,
                case_id=arguments.case_id,
                route_order=arguments.route_order,
                probe_enabled=enabled,
                probe_capture_id=arguments.probe_capture_id,
            )
        digest = write_new_manifest(arguments.output, manifest)
    except CaptureValidationError as exc:
        print(f"manifest assembly failed: {exc}", file=sys.stderr)
        return 2
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
