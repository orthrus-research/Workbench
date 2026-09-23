"""Bind a visual, lossless Strata V2 micro-region observation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

from workbench_crucible_strata_observation.observation import (
    CANONICALIZATION_ID,
    StrataObservationValidationError,
    _capture_binding,
    _file_binding,
    _json_object,
    _runtime_binding,
    _stable_file_bytes,
    _validate_report,
    canonical_json_bytes,
)


MICRO_REGION_RECEIPT_FORMAT = "workbench-crucible-strata-micro-region-receipt-v1"
MICRO_REGION_RECEIPT_PREFIX = "crucible-strata-micro-region:sha256:"
ADAPTER_ID = "workbench-strata-external-adapter-v2"
STRATA_INTERFACE_ID = "strata-micro-region-capture-cli-v2"

_BASE_INTERFACE_FILES = (
    "tools/capture_dense_chunk_package.py",
    "tools/install_worldgen_observer.py",
    "tools/package_dense_chunk_scan.py",
    "tools/run_worldgen_scan.py",
    "tools/shard_strataview_package.py",
    "tools/validate_extraction_quality.py",
    "tools/validate_sharded_strataview_package.py",
    "tools/validate_strataview_package.py",
    "tools/worldgen-observer/build.gradle",
    "tools/worldgen-observer/src/main/java/strata/worldgenobserver/StrataWorldgenObserverMod.java",
    "tools/worldgen-observer/src/main/resources/mcmod.info",
    "tools/render-explorer/index.html",
    "tools/render-explorer/package-lock.json",
    "tools/render-explorer/package.json",
    "tools/render-explorer/scripts/smoke-sharded-package.mjs",
    "tools/render-explorer/vite.config.ts",
)

_BOUNDARIES = {
    "atlas_admission_proved": False,
    "causal_attribution_proved": False,
    "exact_chunk_state_is_source": True,
    "landform_preview_is_display_only": True,
    "recurrent_complex_integration_added": False,
    "request_scope_is_bounded": True,
    "visual_quality_approved": False,
    "world_may_generate_or_populate_requested_chunks": True,
}

_TOP_LEVEL_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "receipt_id",
        "canonicalization_id",
        "adapter",
        "runtime",
        "toolchains",
        "capture",
        "artifacts",
        "checks",
        "boundaries",
    }
)


class StrataMicroRegionValidationError(StrataObservationValidationError):
    """Raised when a V2 micro-region observation is incomplete."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StrataMicroRegionValidationError(message)


def _interface_files(strata_root: Path) -> tuple[str, ...]:
    root = strata_root.resolve(strict=True)
    dynamic = []
    for folder in (
        root / "tools/render-explorer/src",
        root / "tools/render-explorer/scripts",
    ):
        dynamic.extend(
            path.relative_to(root).as_posix()
            for path in folder.rglob("*")
            if path.is_file() and not path.name.endswith(":Zone.Identifier")
        )
    return tuple(sorted(set(_BASE_INTERFACE_FILES) | set(dynamic)))


def _strata_binding(strata_root: Path) -> dict[str, Any]:
    root = strata_root.resolve(strict=True)
    files = [_file_binding(root / relative, root) for relative in _interface_files(root)]
    tree_material = "".join(
        f"{row['relative_path']}\0{row['sha256']}\0{row['size_bytes']}\n"
        for row in files
    ).encode("utf-8")
    return {
        "interface_id": STRATA_INTERFACE_ID,
        "tree_sha256": hashlib.sha256(tree_material).hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def _jdk_binding(java_home: Path, role: str) -> dict[str, Any]:
    release_path = java_home.expanduser().resolve(strict=True) / "release"
    data = _stable_file_bytes(release_path)
    values: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip().strip('"')
    version = values.get("JAVA_VERSION")
    _require(bool(version), f"{role} JDK release lacks JAVA_VERSION")
    return {
        "role": role,
        "java_version": version,
        "implementor": values.get("IMPLEMENTOR", "unknown"),
        "release_sha256": hashlib.sha256(data).hexdigest(),
        "release_size_bytes": len(data),
    }


def _observer_classfile_binding(runtime_root: Path) -> dict[str, Any]:
    jars = sorted((runtime_root / "mods").glob("strata-worldgen-observer-*.jar"))
    _require(len(jars) == 1, "runtime must contain exactly one Strata observer")
    majors: set[int] = set()
    manifest: dict[str, str] = {}
    try:
        with ZipFile(jars[0]) as archive:
            for name in archive.namelist():
                if not name.endswith(".class"):
                    continue
                header = archive.read(name)[:8]
                _require(
                    len(header) == 8 and header[:4] == b"\xca\xfe\xba\xbe",
                    f"invalid observer classfile: {name}",
                )
                majors.add(int.from_bytes(header[6:8], "big"))
            raw_manifest = archive.read("META-INF/MANIFEST.MF").decode(
                "utf-8", errors="replace"
            )
            for line in raw_manifest.splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    manifest[key.strip()] = value.strip()
    except (BadZipFile, KeyError) as exc:
        raise StrataMicroRegionValidationError(
            f"cannot inspect observer jar: {exc}"
        ) from exc
    _require(majors == {52}, f"observer classfile majors must be [52], got {sorted(majors)}")
    _require(
        manifest.get("Build-Classfile-Target") == "8",
        "observer manifest lacks Java 8 target declaration",
    )
    return {
        "classfile_majors": sorted(majors),
        "compiler_java": manifest.get("Build-Compiler-Java"),
        "classfile_target": manifest.get("Build-Classfile-Target"),
    }


def _shard_tree(manifest_path: Path, workbench_root: Path) -> dict[str, Any]:
    shard_root = manifest_path.parent.resolve(strict=True)
    files = [
        _file_binding(path, workbench_root)
        for path in sorted(shard_root.rglob("*.json"))
        if path.is_file()
    ]
    material = "".join(
        f"{row['relative_path']}\0{row['sha256']}\0{row['size_bytes']}\n"
        for row in files
    ).encode("utf-8")
    return {
        "tree_sha256": hashlib.sha256(material).hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def build_strata_micro_region_receipt(
    *,
    workbench_root: Path,
    strata_root: Path,
    runtime_root: Path,
    server_jar: Path,
    runtime_java_home: Path,
    build_java_home: Path,
    compiler_java_home: Path,
    scan_path: Path,
    package_path: Path,
    manifest_path: Path,
    report_path: Path,
    launch_log_path: Path,
    capture_driver_log_path: Path,
    renderer_build_log_path: Path,
    overview_screenshot_path: Path,
    exact_tile_screenshot_path: Path,
) -> dict[str, Any]:
    root = workbench_root.resolve(strict=True)
    package = _json_object(package_path, "Strataview package")
    _require(
        package.get("schema") == "strata.strataview.package.v1",
        "unexpected Strataview package schema",
    )
    manifest = _json_object(manifest_path, "Strataview V2 manifest")
    _require(
        manifest.get("schema") == "strata.strataview.region-manifest.v2",
        "micro-region receipt requires region manifest V2",
    )
    preview = manifest.get("surfacePreview")
    _require(isinstance(preview, Mapping), "manifest lacks surfacePreview")
    _require(
        preview.get("mode") == "derived-landform-and-exact-surface-v1",
        "unexpected surface preview mode",
    )
    report = _validate_report(
        report_path, scan_path, package_path, manifest_path, True
    )
    _require(report.get("manifestVersion") == 2, "capture report lacks V2 manifest state")
    capture = _capture_binding(scan_path)
    window = capture["chunk_window"]
    _require(
        window["chunkSizeX"] == 16 and window["chunkSizeZ"] == 16,
        "the first micro-region profile is exactly 16x16 chunks",
    )
    expected_chunks = 16 * 16
    _require(
        len((preview.get("heightmaps") or {}).get("chunks", [])) == expected_chunks,
        "landform preview does not cover all micro-region chunks",
    )
    _require(
        len((preview.get("exactHeightmaps") or {}).get("chunks", []))
        == expected_chunks,
        "exact surface preview does not cover all micro-region chunks",
    )
    _require(
        len((preview.get("biomeMap") or {}).get("chunks", [])) == expected_chunks,
        "biome preview does not cover all micro-region chunks",
    )

    receipt: dict[str, Any] = {
        "format": MICRO_REGION_RECEIPT_FORMAT,
        "schema_version": 1,
        "receipt_id": "",
        "canonicalization_id": CANONICALIZATION_ID,
        "adapter": {
            "adapter_id": ADAPTER_ID,
            "strata": _strata_binding(strata_root),
        },
        "runtime": _runtime_binding(workbench_root, runtime_root, server_jar),
        "toolchains": {
            "runtime": _jdk_binding(runtime_java_home, "cleanroom-runtime"),
            "build_host": _jdk_binding(build_java_home, "gradle-build-host"),
            "observer_compiler": _jdk_binding(
                compiler_java_home, "observer-source-compiler"
            ),
            "observer_artifact": _observer_classfile_binding(runtime_root),
        },
        "capture": {
            **capture,
            "sample_profile": "micro-region-16x16-v1",
            "sample_chunks": expected_chunks,
            "sample_blocks_x": 256,
            "sample_blocks_z": 256,
        },
        "artifacts": {
            "scan": _file_binding(scan_path, root),
            "package": _file_binding(package_path, root),
            "shards": _shard_tree(manifest_path, root),
            "capture_report": _file_binding(report_path, root),
            "launch_log": _file_binding(launch_log_path, root),
            "capture_driver_log": _file_binding(capture_driver_log_path, root),
            "renderer_build_log": _file_binding(renderer_build_log_path, root),
            "overview_screenshot": _file_binding(overview_screenshot_path, root),
            "exact_tile_screenshot": _file_binding(
                exact_tile_screenshot_path, root
            ),
        },
        "checks": {
            "driver_exit_code": 0,
            "extraction_validation": "pass",
            "package_validation": "pass",
            "lossless_shard_validation": "pass",
            "derived_preview_validation": "pass",
            "renderer_build": "pass",
            "renderer_smoke": "pass",
            "observer_java8_classfiles": "pass",
            "timings": dict(report["timings"]),
        },
        "boundaries": dict(_BOUNDARIES),
    }
    identity = deepcopy(receipt)
    identity.pop("receipt_id")
    receipt["receipt_id"] = MICRO_REGION_RECEIPT_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    return parse_strata_micro_region_receipt(receipt)


def parse_strata_micro_region_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "micro-region receipt must be an object")
    actual_keys = set(value)
    _require(
        actual_keys == _TOP_LEVEL_KEYS,
        f"micro-region receipt fields mismatch: missing={sorted(_TOP_LEVEL_KEYS - actual_keys)!r}, unknown={sorted(actual_keys - _TOP_LEVEL_KEYS)!r}",
    )
    _require(
        value.get("format") == MICRO_REGION_RECEIPT_FORMAT,
        "unexpected micro-region receipt format",
    )
    _require(value.get("schema_version") == 1, "unexpected schema version")
    _require(value.get("canonicalization_id") == CANONICALIZATION_ID, "canonicalization drift")
    _require(value.get("boundaries") == _BOUNDARIES, "micro-region boundaries drift")
    material = deepcopy(dict(value))
    receipt_id = material.pop("receipt_id", None)
    expected = MICRO_REGION_RECEIPT_PREFIX + hashlib.sha256(
        canonical_json_bytes(material)
    ).hexdigest()
    _require(receipt_id == expected, "micro-region receipt ID drift")
    return deepcopy(dict(value))


def write_strata_micro_region_receipt(
    path: Path, receipt: Mapping[str, Any]
) -> None:
    validated = parse_strata_micro_region_receipt(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
