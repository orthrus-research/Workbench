"""Bind one successful external Strata capture without creating new authority."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Mapping


RECEIPT_FORMAT = "workbench-crucible-strata-observation-receipt-v1"
RECEIPT_PREFIX = "crucible-strata-observation:sha256:"
ADAPTER_ID = "workbench-strata-external-adapter-v1"
STRATA_INTERFACE_ID = "strata-dense-capture-cli-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

STRATA_INTERFACE_FILES = (
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
    "tools/render-explorer/package-lock.json",
    "tools/render-explorer/package.json",
    "tools/render-explorer/scripts/smoke-sharded-package.mjs",
    "tools/render-explorer/vite.config.ts",
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "receipt_id",
        "canonicalization_id",
        "adapter",
        "runtime",
        "capture",
        "artifacts",
        "checks",
        "boundaries",
    }
)
_BOUNDARIES = {
    "atlas_admission_proved": False,
    "causal_attribution_proved": False,
    "exact_chunk_state_is_source": True,
    "gregtech_fluid_capture_required": False,
    "recurrent_complex_integration_added": False,
    "renderer_simplification_is_display_only": True,
    "request_scope_is_bounded": True,
    "visual_quality_approved": False,
    "world_may_generate_or_populate_requested_chunks": True,
}


class StrataObservationValidationError(ValueError):
    """Raised when an observation cannot support a complete V1 receipt."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StrataObservationValidationError(message)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StrataObservationValidationError(
            f"cannot canonically encode Strata receipt material: {exc}"
        ) from exc


def _stable_file_bytes(path: Path) -> bytes:
    resolved = path.resolve(strict=True)
    before = resolved.stat()
    _require(stat.S_ISREG(before.st_mode), f"not a regular file: {resolved}")
    data = resolved.read_bytes()
    after = resolved.stat()
    _require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        f"file changed while binding it: {resolved}",
    )
    return data


def _file_binding(path: Path, base: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    base_resolved = base.resolve(strict=True)
    try:
        relative = resolved.relative_to(base_resolved).as_posix()
    except ValueError as exc:
        raise StrataObservationValidationError(
            f"bound file is outside declared root {base_resolved}: {resolved}"
        ) from exc
    data = _stable_file_bytes(resolved)
    return {
        "relative_path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _json_object(path: Path, context: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_stable_file_bytes(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StrataObservationValidationError(
            f"cannot parse {context}: {exc}"
        ) from exc
    _require(isinstance(value, Mapping), f"{context} must be a JSON object")
    return value


def _strata_binding(strata_root: Path) -> dict[str, Any]:
    root = strata_root.resolve(strict=True)
    files = [_file_binding(root / relative, root) for relative in STRATA_INTERFACE_FILES]
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


def _runtime_binding(
    workbench_root: Path,
    runtime_root: Path,
    server_jar: Path,
) -> dict[str, Any]:
    root = workbench_root.resolve(strict=True)
    runtime = runtime_root.resolve(strict=True)
    operational_root = (root / ".workbench").resolve(strict=True)
    try:
        runtime_relative = runtime.relative_to(root).as_posix()
        runtime.relative_to(operational_root)
    except ValueError as exc:
        raise StrataObservationValidationError(
            "Strata runtime must be inside Workbench .workbench storage"
        ) from exc
    mods = runtime / "mods"
    _require(mods.is_dir(), f"missing runtime mods directory: {mods}")
    installed_mods = [
        _file_binding(path, runtime) for path in sorted(mods.glob("*.jar"))
    ]
    observer = [
        row
        for row in installed_mods
        if Path(row["relative_path"]).name.startswith("strata-worldgen-observer-")
    ]
    _require(len(observer) == 1, "runtime must contain exactly one Strata observer")
    return {
        "relative_path": runtime_relative,
        "server_jar": _file_binding(server_jar, runtime),
        "observer_jar": observer[0],
        "installed_mods": installed_mods,
    }


def _integer(value: Any, context: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{context} must be an integer")
    return value


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and value != "", f"{context} must be non-empty text")
    return value


def _capture_binding(scan_path: Path) -> dict[str, Any]:
    scan = _json_object(scan_path, "dense observer scan")
    _require(scan.get("voxelMode") == "dense", "Strata observation must use dense voxel mode")
    dense_map = scan.get("denseBlockMap")
    _require(isinstance(dense_map, Mapping), "scan lacks denseBlockMap")
    _require(
        dense_map.get("mode") == "dense-section-paletted-states",
        "scan has an unsupported denseBlockMap mode",
    )
    window = scan.get("chunkWindow")
    _require(isinstance(window, Mapping), "scan lacks chunkWindow")
    bound_window = {
        key: _integer(window.get(key), f"chunkWindow.{key}")
        for key in (
            "minChunkX",
            "minChunkZ",
            "chunkSizeX",
            "chunkSizeZ",
            "haloChunks",
        )
    }
    chunk_count = bound_window["chunkSizeX"] * bound_window["chunkSizeZ"]
    _require(chunk_count > 0, "scan chunk window must be non-empty")
    populated = _integer(
        scan.get("scannedTerrainPopulatedChunks"),
        "scannedTerrainPopulatedChunks",
    )
    _require(
        populated == chunk_count,
        f"only {populated} of {chunk_count} scanned chunks are terrain-populated",
    )
    dense_cross_check = scan.get("denseWorldApiCrossCheck")
    _require(isinstance(dense_cross_check, Mapping), "scan lacks dense World API cross-check")
    dense_checks = _integer(
        dense_cross_check.get("checkedSamples"), "dense checkedSamples"
    )
    dense_mismatches = _integer(
        dense_cross_check.get("mismatches"), "dense mismatches"
    )
    _require(dense_checks > 0, "dense scan did not cross-check any block samples")
    _require(dense_mismatches == 0, "dense scan has World API mismatches")
    sparse_cross_check = scan.get("worldApiCrossCheck")
    _require(isinstance(sparse_cross_check, Mapping), "scan lacks sparse World API cross-check")
    sparse_mismatches = _integer(
        sparse_cross_check.get("mismatches"), "sparse mismatches"
    )
    _require(sparse_mismatches == 0, "sparse resource scan has World API mismatches")
    return {
        "dimension_id": _integer(scan.get("dimensionId"), "dimensionId"),
        "world_seed": _integer(scan.get("worldSeed"), "worldSeed"),
        "terrain_type": _text(scan.get("terrainType"), "terrainType"),
        "provider_class": _text(scan.get("providerClass"), "providerClass"),
        "chunk_generator_class": _text(
            scan.get("chunkGeneratorClass"), "chunkGeneratorClass"
        ),
        "chunk_window": bound_window,
        "terrain_populated_chunks": populated,
        "dense_world_api_checks": dense_checks,
        "dense_world_api_mismatches": dense_mismatches,
        "sparse_world_api_mismatches": sparse_mismatches,
    }


def _validate_report(
    report_path: Path,
    scan_path: Path,
    package_path: Path,
    manifest_path: Path,
    render_smoke: bool,
) -> Mapping[str, Any]:
    report = _json_object(report_path, "Strata capture report")
    paths = report.get("paths")
    _require(isinstance(paths, Mapping), "capture report lacks paths")
    expected = {
        "scan": scan_path.resolve(strict=True),
        "package": package_path.resolve(strict=True),
        "manifest": manifest_path.resolve(strict=True),
    }
    for key, expected_path in expected.items():
        actual = paths.get(key)
        _require(isinstance(actual, str), f"capture report lacks {key} path")
        _require(Path(actual).resolve(strict=True) == expected_path, f"capture report {key} path drift")
    _require(report.get("sharded") is True, "capture report is not sharded")
    _require(
        report.get("renderSmoke") is render_smoke,
        "capture report renderer-smoke state drift",
    )
    timings = report.get("timings")
    _require(isinstance(timings, Mapping), "capture report lacks timings")
    required_timings = {
        "extractionValidateSeconds",
        "packageSeconds",
        "validateSeconds",
        "shardSeconds",
        "shardValidateSeconds",
    }
    if render_smoke:
        required_timings.add("renderSmokeSeconds")
    for key in required_timings:
        value = timings.get(key)
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0,
            f"capture report lacks valid timing {key}",
        )
    return report


def build_strata_observation_receipt(
    *,
    workbench_root: Path,
    strata_root: Path,
    runtime_root: Path,
    server_jar: Path,
    scan_path: Path,
    package_path: Path,
    manifest_path: Path,
    report_path: Path,
    launch_log_path: Path,
    capture_driver_log_path: Path,
    renderer_build_log_path: Path,
    render_smoke: bool,
    screenshot_path: Path | None = None,
) -> dict[str, Any]:
    root = workbench_root.resolve(strict=True)
    package = _json_object(package_path, "Strataview package")
    _require(
        package.get("schema") == "strata.strataview.package.v1",
        "unexpected Strataview package schema",
    )
    manifest = _json_object(manifest_path, "Strataview sharded manifest")
    _require(
        manifest.get("schema") == "strata.strataview.region-manifest.v1",
        "unexpected Strataview manifest schema",
    )
    report = _validate_report(
        report_path, scan_path, package_path, manifest_path, render_smoke
    )
    artifacts: dict[str, Any] = {
        "scan": _file_binding(scan_path, root),
        "package": _file_binding(package_path, root),
        "manifest": _file_binding(manifest_path, root),
        "capture_report": _file_binding(report_path, root),
        "launch_log": _file_binding(launch_log_path, root),
        "capture_driver_log": _file_binding(capture_driver_log_path, root),
        "renderer_build_log": _file_binding(renderer_build_log_path, root),
        "screenshot": None,
    }
    if render_smoke:
        _require(screenshot_path is not None, "render smoke requires a screenshot")
        artifacts["screenshot"] = _file_binding(screenshot_path, root)
    receipt: dict[str, Any] = {
        "format": RECEIPT_FORMAT,
        "schema_version": 1,
        "receipt_id": "",
        "canonicalization_id": CANONICALIZATION_ID,
        "adapter": {
            "adapter_id": ADAPTER_ID,
            "strata": _strata_binding(strata_root),
        },
        "runtime": _runtime_binding(workbench_root, runtime_root, server_jar),
        "capture": _capture_binding(scan_path),
        "artifacts": artifacts,
        "checks": {
            "driver_exit_code": 0,
            "extraction_validation": "pass",
            "package_validation": "pass",
            "shard_validation": "pass",
            "renderer_build": "pass",
            "renderer_smoke": "pass" if render_smoke else "not_run",
            "timings": dict(report["timings"]),
        },
        "boundaries": dict(_BOUNDARIES),
    }
    material = deepcopy(receipt)
    material.pop("receipt_id")
    receipt["receipt_id"] = RECEIPT_PREFIX + hashlib.sha256(
        canonical_json_bytes(material)
    ).hexdigest()
    return parse_strata_observation_receipt(receipt)


def parse_strata_observation_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "Strata receipt must be an object")
    actual_keys = set(value)
    _require(
        actual_keys == _TOP_LEVEL_KEYS,
        f"Strata receipt fields mismatch: missing={sorted(_TOP_LEVEL_KEYS - actual_keys)!r}, unknown={sorted(actual_keys - _TOP_LEVEL_KEYS)!r}",
    )
    _require(value.get("format") == RECEIPT_FORMAT, "unexpected Strata receipt format")
    _require(value.get("schema_version") == 1, "unexpected Strata receipt schema version")
    _require(
        value.get("canonicalization_id") == CANONICALIZATION_ID,
        "unexpected Strata canonicalization",
    )
    _require(value.get("boundaries") == _BOUNDARIES, "Strata receipt boundaries drift")
    material = deepcopy(dict(value))
    receipt_id = material.pop("receipt_id", None)
    expected_id = RECEIPT_PREFIX + hashlib.sha256(
        canonical_json_bytes(material)
    ).hexdigest()
    _require(receipt_id == expected_id, "Strata receipt ID drift")
    return deepcopy(dict(value))


def write_strata_observation_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    validated = parse_strata_observation_receipt(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
