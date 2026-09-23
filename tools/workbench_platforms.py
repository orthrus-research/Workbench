#!/usr/bin/env python3
"""Validate the current Workbench Pixi platform and bootstrap authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "packaging/pixi/workbench-platform-matrix-v1.json"
FORMAT = "workbench-platform-matrix-v1"
EXPECTED_TARGETS = (
    "linux-x86-64",
    "linux-arm64",
    "windows-x86-64",
    "windows-arm64",
    "macos-x86-64",
    "macos-arm64",
)
IMPLEMENTATION_FIELDS = (
    "compatibility_environment",
    "source_installer",
    "workspace_environment",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RELEASE_BASE = "https://github.com/prefix-dev/pixi/releases/download/"


class PlatformMatrixError(ValueError):
    """The checked platform matrix is missing, ambiguous, or inconsistent."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PlatformMatrixError(f"platform matrix repeats JSON key {key!r}")
        value[key] = item
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PlatformMatrixError(f"{label} must be an object")
    return value


def _read_matrix(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except OSError as error:
        raise PlatformMatrixError(f"platform matrix cannot be read: {path}") from error
    if raw.startswith("\ufeff"):
        raise PlatformMatrixError("platform matrix has a byte-order mark")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise PlatformMatrixError(f"platform matrix is not strict JSON: {path}") from error
    return _object(value, "platform matrix")


def load_platform_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    """Load the single current platform matrix and verify its pinned assets."""

    value = _read_matrix(path)
    if set(value) != {"format", "pixi", "platforms", "schema_version"}:
        raise PlatformMatrixError("platform matrix has unexpected root fields")
    if value.get("format") != FORMAT or value.get("schema_version") != 1:
        raise PlatformMatrixError("platform matrix has the wrong format or version")

    pixi = _object(value.get("pixi"), "platform matrix Pixi authority")
    version = pixi.get("version")
    if type(version) is not str or VERSION.fullmatch(version) is None:
        raise PlatformMatrixError("platform matrix Pixi version is not exact")
    tag = f"v{version}"
    if (
        pixi.get("requirement") != f"=={version}"
        or pixi.get("release_tag") != tag
        or pixi.get("release_url") != f"https://github.com/prefix-dev/pixi/releases/tag/{tag}"
        or pixi.get("checksum_manifest_url") != f"{RELEASE_BASE}{tag}/sha256.sum"
        or type(pixi.get("release_commit")) is not str
        or re.fullmatch(r"[0-9a-f]{40}", pixi["release_commit"]) is None
        or type(pixi.get("checksum_manifest_sha256")) is not str
        or SHA256.fullmatch(pixi["checksum_manifest_sha256"]) is None
    ):
        raise PlatformMatrixError("platform matrix Pixi release authority is inconsistent")

    rows = value.get("platforms")
    if type(rows) is not list or len(rows) != len(EXPECTED_TARGETS):
        raise PlatformMatrixError("platform matrix must contain exactly six rows")
    targets: list[str] = []
    pixi_platform_values: set[str] = set()
    host_aliases: set[tuple[str, str]] = set()
    for index, raw_row in enumerate(rows):
        row = _object(raw_row, f"platform matrix row {index}")
        target = row.get("target")
        if type(target) is not str:
            raise PlatformMatrixError(f"platform matrix row {index} has no target")
        targets.append(target)
        pixi_platform = row.get("pixi_platform")
        if type(pixi_platform) is not str or pixi_platform in pixi_platform_values:
            raise PlatformMatrixError(f"platform matrix target {target} repeats a platform")
        pixi_platform_values.add(pixi_platform)

        aliases = row.get("machine_aliases")
        if (
            type(aliases) is not list
            or len(aliases) < 2
            or any(type(alias) is not str or not alias for alias in aliases)
            or len(set(aliases)) != len(aliases)
        ):
            raise PlatformMatrixError(f"platform matrix target {target} has bad aliases")
        system = row.get("host_system")
        if type(system) is not str or not system:
            raise PlatformMatrixError(f"platform matrix target {target} has no host system")
        for alias in aliases:
            identity = (system.lower(), alias.lower())
            if identity in host_aliases:
                raise PlatformMatrixError(f"platform matrix repeats host identity {system} {alias}")
            host_aliases.add(identity)

        implementation = _object(
            row.get("implementation"), f"platform matrix target {target} implementation"
        )
        if set(implementation) != set(IMPLEMENTATION_FIELDS):
            raise PlatformMatrixError(
                f"platform matrix target {target} has the wrong implementation fields"
            )
        if any(type(implementation[field]) is not bool for field in IMPLEMENTATION_FIELDS):
            raise PlatformMatrixError(
                f"platform matrix target {target} has a non-boolean implementation field"
            )

        bootstrap = _object(row.get("bootstrap"), f"platform matrix target {target} bootstrap")
        asset = bootstrap.get("asset")
        if (
            bootstrap.get("available") is not True
            or type(asset) is not str
            or not asset
            or bootstrap.get("url") != f"{RELEASE_BASE}{tag}/{asset}"
            or "/latest/" in str(bootstrap.get("url"))
        ):
            raise PlatformMatrixError(
                f"platform matrix target {target} does not pin one bootstrap asset"
            )
        for field in ("archive_sha256", "executable_sha256"):
            digest = bootstrap.get(field)
            if type(digest) is not str or SHA256.fullmatch(digest) is None:
                raise PlatformMatrixError(
                    f"platform matrix target {target} has an invalid {field}"
                )

    if tuple(targets) != EXPECTED_TARGETS:
        raise PlatformMatrixError("platform matrix order or membership changed")
    expected_native = set(EXPECTED_TARGETS) - {"windows-arm64"}
    for capability in IMPLEMENTATION_FIELDS:
        if set(implementation_targets(value, capability)) != expected_native:
            raise PlatformMatrixError(
                f"{capability.replace('_', '-')} rows differ from the five implemented rows"
            )
    return value


def platform_rows(matrix: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(_object(row, "platform matrix row") for row in matrix["platforms"])


def pixi_platforms(matrix: Mapping[str, Any]) -> dict[str, str]:
    return {row["target"]: row["pixi_platform"] for row in platform_rows(matrix)}


def implementation_targets(
    matrix: Mapping[str, Any], capability: str
) -> tuple[str, ...]:
    if capability not in IMPLEMENTATION_FIELDS:
        raise PlatformMatrixError(f"unknown implementation capability: {capability}")
    return tuple(
        row["target"]
        for row in platform_rows(matrix)
        if _object(row["implementation"], "implementation")[capability] is True
    )


def host_identities(matrix: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        row["target"]: {
            "system": row["host_system"],
            "architecture": row["architecture"],
        }
        for row in platform_rows(matrix)
    }


def validate_repository_contract(
    root: Path = ROOT, matrix: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    current = (
        load_platform_matrix(root / MATRIX_PATH.relative_to(ROOT))
        if matrix is None
        else dict(matrix)
    )
    try:
        manifest = tomllib.loads((root / "pixi.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PlatformMatrixError("pixi.toml cannot be read") from error
    workspace = _object(manifest.get("workspace"), "pixi.toml workspace")
    pixi = _object(current["pixi"], "platform matrix Pixi authority")
    if workspace.get("requires-pixi") != pixi["requirement"]:
        raise PlatformMatrixError("pixi.toml and platform matrix pin different Pixi versions")
    declared = workspace.get("platforms")
    if type(declared) is not list:
        raise PlatformMatrixError("pixi.toml platforms are missing")
    manifest_platforms = {
        row.get("name"): row.get("platform") for row in declared if type(row) is dict
    }
    if manifest_platforms != pixi_platforms(current):
        raise PlatformMatrixError("pixi.toml platforms differ from platform matrix")

    features = _object(manifest.get("feature"), "pixi.toml feature table")
    expected_feature_rows = {
        "workspace-tools": implementation_targets(current, "workspace_environment"),
        "compatibility-runtime": implementation_targets(current, "compatibility_environment"),
    }
    for feature, expected in expected_feature_rows.items():
        row = _object(features.get(feature), f"pixi.toml feature {feature}")
        if tuple(row.get("platforms", ())) != expected:
            raise PlatformMatrixError(
                f"pixi.toml feature {feature} differs from platform matrix"
            )
    return current


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the verified matrix")
    args = parser.parse_args(argv)
    try:
        matrix = validate_repository_contract()
    except PlatformMatrixError as error:
        print(f"Workbench platform-matrix check failed: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(matrix, indent=2, sort_keys=True))
    else:
        print("Workbench Pixi platform matrix is internally consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
