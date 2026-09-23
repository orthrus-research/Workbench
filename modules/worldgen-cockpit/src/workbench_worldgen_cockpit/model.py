"""Identity, profile, and report contracts for Worldgen Cockpit V1."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


PROFILE_FORMAT = "workbench-worldgen-cockpit-pack-profile-v1"
REPORT_FORMAT = "workbench-worldgen-cockpit-report-v1"
REPORT_PREFIX = "workbench-worldgen-cockpit:sha256:"
SESSION_FORMAT = "workbench-worldgen-cockpit-session-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
MAX_JSON_BYTES = 512 * 1024 * 1024
MAX_PROFILE_BYTES = 1024 * 1024
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CockpitError(ValueError):
    """An unsafe, incomparable, or malformed cockpit input."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CockpitError(message)


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
        raise CockpitError(f"cannot canonically encode cockpit data: {exc}") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileBinding:
    path: Path
    sha256: str
    size_bytes: int

    def public(self, *, kind: str, authority: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "authority": authority,
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def read_regular_file(
    path: Path | str, *, context: str, maximum_bytes: int = MAX_JSON_BYTES
) -> tuple[bytes, FileBinding]:
    requested = Path(path).expanduser()
    require(not requested.is_symlink(), f"{context} cannot be a symlink: {requested}")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise CockpitError(f"cannot resolve {context}: {requested}: {exc}") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise CockpitError(f"cannot open {context}: {resolved}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), f"{context} is not a regular file: {resolved}")
        require(0 < before.st_size <= maximum_bytes, f"{context} is empty or exceeds {maximum_bytes} bytes: {resolved}")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            require(bool(block), f"{context} ended before its declared size: {resolved}")
            chunks.append(block)
            remaining -= len(block)
        require(not os.read(descriptor, 1), f"{context} grew while being read: {resolved}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda row: (
        row.st_dev,
        row.st_ino,
        row.st_mode,
        row.st_size,
        row.st_mtime_ns,
        row.st_ctime_ns,
    )
    require(identity(before) == identity(after), f"{context} changed while being read: {resolved}")
    # Keep pathname/handle timestamp views separate on NTFS while retaining
    # the complete handle identity check when reopening the pathname.
    try:
        current_descriptor = os.open(resolved, flags | getattr(os, "O_BINARY", 0))
        try:
            current = os.fstat(current_descriptor)
        finally:
            os.close(current_descriptor)
    except OSError as exc:
        raise CockpitError(f"cannot recheck {context}: {resolved}: {exc}") from exc
    require(identity(before) == identity(current), f"{context} was replaced while being read: {resolved}")
    data = b"".join(chunks)
    return data, FileBinding(resolved, hashlib.sha256(data).hexdigest(), len(data))


def load_json_file(
    path: Path | str, *, context: str, maximum_bytes: int = MAX_JSON_BYTES
) -> tuple[dict[str, Any], FileBinding]:
    data, binding = read_regular_file(path, context=context, maximum_bytes=maximum_bytes)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, f"{context} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CockpitError(f"cannot parse {context}: {exc}") from exc
    require(isinstance(value, dict), f"{context} must contain one JSON object")
    return value, binding


def _exact_keys(value: Any, expected: set[str], context: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    require(
        actual == expected,
        f"{context} fields mismatch: missing={sorted(expected - actual)!r}, unknown={sorted(actual - expected)!r}",
    )
    return value


def _relative_file(root: Path, raw: Any, context: str) -> Path:
    require(isinstance(raw, str) and bool(raw), f"{context} must be nonempty text")
    relative = Path(raw)
    require(not relative.is_absolute() and ".." not in relative.parts, f"{context} must stay inside Workbench")
    current = root.resolve()
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), f"{context} cannot traverse a symlink: {current}")
    resolved = current.resolve(strict=True)
    require(resolved.is_file(), f"{context} is not a regular file: {resolved}")
    require(root.resolve() in resolved.parents, f"{context} escapes Workbench")
    return resolved


def load_profile(path: Path | str, *, root: Path) -> tuple[dict[str, Any], FileBinding]:
    value, binding = load_json_file(path, context="Worldgen Cockpit profile", maximum_bytes=MAX_PROFILE_BYTES)
    _exact_keys(
        value,
        {
            "format",
            "schema_version",
            "profile_id",
            "pack_profile",
            "iteration_profile",
            "subsurface_profile",
            "dimension_id",
            "modes",
            "thresholds",
            "limits",
            "boundaries",
        },
        "Worldgen Cockpit profile",
    )
    require(value["format"] == PROFILE_FORMAT and value["schema_version"] == 1, "unsupported Worldgen Cockpit profile version")
    require(isinstance(value["profile_id"], str) and bool(value["profile_id"]), "cockpit profile_id is invalid")
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(value["pack_profile"])) is not None, "cockpit pack_profile is invalid")
    require(isinstance(value["dimension_id"], int) and not isinstance(value["dimension_id"], bool), "cockpit dimension_id must be an integer")
    modes = _exact_keys(value["modes"], {"fast", "debug", "performance"}, "cockpit modes")
    for mode_id, mode in modes.items():
        _exact_keys(mode, {"region_default", "required_evidence"}, f"cockpit mode {mode_id}")
        require(mode["region_default"] in {"fast_region", "debug_region"}, f"cockpit mode {mode_id} has invalid region_default")
        required = mode["required_evidence"]
        require(isinstance(required, list) and required and len(required) == len(set(required)), f"cockpit mode {mode_id} required_evidence is invalid")
        require(all(item in {"semantic", "final_state", "statistical", "causal", "performance", "observer_overhead"} for item in required), f"cockpit mode {mode_id} names unsupported evidence")
    thresholds = _exact_keys(
        value["thresholds"],
        {
            "performance_regression_ratio",
            "performance_regression_min_us",
            "chunk_outlier_mad",
            "height_mean_abs_blocks",
            "cave_volume_relative",
            "ore_mass_relative",
        },
        "cockpit thresholds",
    )
    for key, number in thresholds.items():
        require(isinstance(number, (int, float)) and not isinstance(number, bool) and number >= 0, f"cockpit threshold {key} is invalid")
    require(thresholds["performance_regression_ratio"] >= 1, "performance regression ratio must be at least 1")
    limits = _exact_keys(
        value["limits"],
        {"max_chunks", "max_exact_block_comparisons", "max_topology_blocks", "max_samples_per_chunk", "max_outliers"},
        "cockpit limits",
    )
    for key, number in limits.items():
        require(isinstance(number, int) and not isinstance(number, bool) and number > 0, f"cockpit limit {key} is invalid")
    require(limits["max_chunks"] <= 1024, "cockpit max_chunks exceeds the V1 bound")
    boundaries = _exact_keys(
        value["boundaries"],
        {
            "atlas_remains_causal_authority",
            "strata_remains_final_state_authority",
            "crucible_remains_experiment_authority",
            "profile_is_pack_specific",
        },
        "cockpit boundaries",
    )
    require(all(item is True for item in boundaries.values()), "cockpit authority boundaries must remain enabled")
    value = deepcopy(value)
    value["_path"] = str(binding.path)
    value["_iteration_profile"] = str(_relative_file(root, value["iteration_profile"], "iteration_profile"))
    value["_subsurface_profile"] = str(_relative_file(root, value["subsurface_profile"], "subsurface_profile"))
    return value, binding


def resolve_profile_path(root: Path, name: str) -> Path:
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) is not None, f"invalid pack profile name: {name!r}")
    return root / "profiles" / "packs" / name / "worldgen" / "worldgen-cockpit-profile-v1.json"


def make_report_id(report: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(report))
    payload.pop("report_id", None)
    return REPORT_PREFIX + sha256_json(payload)


def validate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "format",
        "schema_version",
        "report_id",
        "created_at",
        "mode",
        "status",
        "coverage",
        "profile",
        "alignment",
        "sides",
        "evidence",
        "decision",
        "visual",
        "navigation",
        "limitations",
        "reproduction_command",
    }
    _exact_keys(report, required, "Worldgen Cockpit report")
    require(report["format"] == REPORT_FORMAT and report["schema_version"] == 1, "unsupported Worldgen Cockpit report version")
    require(report["mode"] in {"fast", "debug", "performance"}, "cockpit report mode is invalid")
    require(report["status"] in {"equivalent", "changed", "unstable", "incomparable"}, "cockpit report status is invalid")
    require(report["coverage"] in {"complete-for-mode", "partial", "incomparable"}, "cockpit report coverage is invalid")
    require(isinstance(report["limitations"], list) and len(report["limitations"]) == len(set(report["limitations"])), "cockpit report limitations must be unique")
    require(set(report["sides"]) == {"baseline", "candidate"}, "cockpit report must retain both sides")
    expected_evidence = {"semantic", "final_state", "statistical", "subsurface", "causal", "performance", "observer_overhead"}
    require(set(report["evidence"]) == expected_evidence, "cockpit report evidence fields drift")
    decision = report["decision"]
    require(isinstance(decision, Mapping), "cockpit report decision is invalid")
    require(
        decision.get("comparison_kind") in {"identical-input-control", "before-after", "incomparable"},
        "cockpit report comparison kind is invalid",
    )
    require(
        decision.get("reproducibility_status")
        in {"stable-in-scope", "failed-in-scope", "not-calibrated", "unavailable"},
        "cockpit report reproducibility status is invalid",
    )
    require(report["report_id"] == make_report_id(report), "cockpit report identity drift")
    return deepcopy(dict(report))


def load_report(path: Path | str) -> tuple[dict[str, Any], FileBinding]:
    value, binding = load_json_file(path, context="Worldgen Cockpit report")
    return validate_report(value), binding


def write_json_atomic(path: Path, value: Mapping[str, Any], *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CockpitError(f"output cannot be a symlink: {path}")
    if path.exists() and not replace:
        raise CockpitError(f"output already exists: {path}")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_json_bytes(value) + b"\n")
    os.replace(temporary, path)


__all__ = [
    "CANONICALIZATION_ID",
    "CockpitError",
    "FileBinding",
    "LABEL_RE",
    "PROFILE_FORMAT",
    "REPORT_FORMAT",
    "SESSION_FORMAT",
    "canonical_json_bytes",
    "load_json_file",
    "load_profile",
    "load_report",
    "make_report_id",
    "read_regular_file",
    "require",
    "resolve_profile_path",
    "sha256_file",
    "sha256_json",
    "validate_report",
    "write_json_atomic",
]
