#!/usr/bin/env python3
"""Normalize one explicitly described Cleanroom Worldgen Observatory case.

This worker is deliberately narrower than a runtime harness.  It never finds a
profile, candidate, artifact, configuration, world, or output location.  Those
identities and every path must be present in one closed, content-addressed JSON
specification supplied by the caller.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
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
    admit_cleanroom_raw,
    canonical_json_bytes,
    canonical_json_sha256,
    collect_cleanroom_runtime_custody,
    new_run,
    parse_cleanroom_case_worker_receipt,
)
from workbench_crucible_observatory.bundle import (  # noqa: E402
    _validated_bundle_publication_state,
    _write_validated_bundle,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    FIXED_WORLD_SEED_SHA256,
    _write_validated_cleanroom_bundle_projection,
    load_fixture_result,
)
from workbench_crucible_observatory.cleanroom_normalize import (  # noqa: E402
    _normalize_cleanroom_raw_publication,
)


SPEC_FORMAT = "workbench-crucible-cleanroom-case-worker-spec-v1"
SPEC_PREFIX = "crucible-cleanroom-case-worker-spec:sha256:"
RECEIPT_SCHEMA = "workbench.crucible.cleanroom-case-worker-receipt.v1"
RECEIPT_PREFIX = "crucible-cleanroom-case-worker-receipt:sha256:"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_QUALIFIED_ID_RE = re.compile(r"^[a-z][a-z0-9._-]*:[A-Za-z0-9._:-]+$")
_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_TOP_LEVEL_KEYS = {
    "format",
    "spec_id",
    "inputs",
    "outputs",
    "admission",
    "custody",
    "run",
    "normalization",
}
_INPUT_KEYS = {
    "raw_ndjson",
    "raw_schema",
    "probe_plan",
    "candidate_lock",
    "fixture_artifact",
    "installed_mod_set_manifest",
    "configuration_set_manifest",
    "runtime_case_directory",
    "fixture_result",
}
_FILE_KEYS = {"path", "sha256"}
_SCHEMA_FILE_KEYS = {"path", "sha256", "schema_id"}
_PLAN_FILE_KEYS = {"path", "sha256", "plan_id"}
_DIRECTORY_KEYS = {"path"}
_OUTPUT_KEYS = {"canonical_bundle", "cleanroom_projection", "worker_receipt"}
_ADMISSION_KEYS = {
    "requested_mode",
    "required_roles",
    "allow_incomplete",
    "expected_publication_state",
}
_CUSTODY_KEYS = {
    "trusted_class_prefix_owners",
    "loaded_mod_source_sha256",
    "runtime_mod_inventory_sha256",
}
_RUN_KEYS = {
    "capture_mode",
    "capture_plan_sha256",
    "fixture_id",
    "fixture_sha256",
    "environment",
    "world",
}
_ENVIRONMENT_KEYS = {
    "minecraft_version",
    "platform_profile_id",
    "platform_profile_sha256",
    "pack_profile_id",
    "pack_profile_sha256",
    "snapshot_id",
    "physical_side",
    "runtime_java",
    "mapping_namespace",
    "transformed_runtime_sha256",
    "mod_set_sha256",
    "configuration_set_sha256",
}
_WORLD_KEYS = {
    "world_instance_id",
    "world_seed_sha256",
    "world_type",
    "generator_options_sha256",
    "dimension_ids",
}
_NORMALIZATION_KEYS = {
    "selection_id",
    "execution_id",
    "checkpoint_domains",
    "workbench_identity",
}
_RAW_ACTOR_KEYS = {
    "binding",
    "mod_id",
    "class_name",
    "method_name",
    "method_descriptor",
    "mapping_namespace",
    "code_source_sha256",
    "transformed_class_sha256",
}


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _FileReceipt:
    path: Path
    sha256: str
    stat_identity: tuple[int, int, int, int, int]


class _CanonicalClock:
    """A deterministic clock for the canonical record ordering receipts."""

    def __init__(self) -> None:
        self._next = 0

    def __call__(self) -> int:
        value = self._next
        self._next += 1
        return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _exact_keys(value: Any, expected: set[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == expected,
        f"{context} fields mismatch: missing={sorted(expected - actual)!r}, "
        f"unknown={sorted(actual - expected)!r}",
    )
    return value


def _nonempty(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token}")


def _reject_float(token: str) -> None:
    raise ValueError(f"worker specifications do not permit JSON number {token}")


def _parse_json_object(encoded: bytes, *, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse {context}: {exc}") from exc
    _require(isinstance(value, dict), f"{context} must be a JSON object")
    return value


def _absolute_path(value: Any, context: str) -> Path:
    raw = _nonempty(value, context)
    path = Path(raw)
    _require(path.is_absolute(), f"{context} must be an absolute path")
    return path


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _hash_regular_file(path: Path, *, context: str) -> _FileReceipt:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISREG(before.st_mode), f"{context} is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot hash {context} {path}: {exc}") from exc
    _require(
        _stat_identity(before) == _stat_identity(after),
        f"{context} changed while it was hashed: {path}",
    )
    return _FileReceipt(path, digest.hexdigest(), _stat_identity(after))


def _require_unchanged(receipt: _FileReceipt, *, context: str) -> None:
    try:
        current = receipt.path.lstat()
    except OSError as exc:
        raise CaptureValidationError(
            f"cannot re-inspect {context} {receipt.path}: {exc}"
        ) from exc
    _require(
        _stat_identity(current) == receipt.stat_identity,
        f"{context} changed during worker admission: {receipt.path}",
    )


def _file_input(value: Any, keys: set[str], context: str) -> tuple[Path, str, Mapping[str, Any]]:
    item = _exact_keys(value, keys, context)
    return (
        _absolute_path(item["path"], f"{context} path"),
        _sha256(item["sha256"], f"{context} digest"),
        item,
    )


def _string_map(value: Any, context: str, *, digest_values: bool) -> dict[str, str]:
    _require(isinstance(value, Mapping) and bool(value), f"{context} must be a nonempty object")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _nonempty(raw_key, f"{context} key")
        item = _sha256(raw_value, f"{context} {key}") if digest_values else _nonempty(
            raw_value, f"{context} {key}"
        )
        result[key] = item
    return result


def _validate_actor(value: Any) -> dict[str, Any]:
    actor = _exact_keys(value, _RAW_ACTOR_KEYS, "normalization Workbench identity")
    _require(actor["binding"] == "workbench", "Workbench identity binding must be workbench")
    mod_id = _nonempty(actor["mod_id"], "Workbench identity mod ID")
    _require(mod_id.startswith("workbench"), "Workbench identity mod ID must start with workbench")
    for field in (
        "class_name",
        "method_name",
        "method_descriptor",
        "mapping_namespace",
    ):
        _nonempty(actor[field], f"Workbench identity {field}")
    _sha256(actor["code_source_sha256"], "Workbench identity code source")
    transformed = actor["transformed_class_sha256"]
    _require(transformed is None or _SHA256_RE.fullmatch(str(transformed)) is not None,
             "Workbench identity transformed class must be null or lowercase SHA-256")
    return deepcopy(dict(actor))


def _validate_run_spec(value: Any, *, plan_file_sha256: str) -> dict[str, Any]:
    raw = _exact_keys(value, _RUN_KEYS, "worker run")
    capture_mode = _nonempty(raw["capture_mode"], "run capture mode")
    _require(
        capture_mode in {"summary", "trace", "forensic", "lossless-fixture"},
        "run capture mode is not admitted",
    )
    capture_plan = _sha256(raw["capture_plan_sha256"], "run capture plan")
    _require(
        capture_plan == plan_file_sha256,
        "run capture plan does not match the exact probe-plan file",
    )
    fixture_id = _nonempty(raw["fixture_id"], "run fixture ID")
    _require(_QUALIFIED_ID_RE.fullmatch(fixture_id) is not None, "run fixture ID is not qualified")
    fixture_sha256 = _sha256(raw["fixture_sha256"], "run fixture artifact")

    environment = _exact_keys(raw["environment"], _ENVIRONMENT_KEYS, "run environment")
    _require(environment["minecraft_version"] == "1.12.2", "run Minecraft version must be 1.12.2")
    for field in (
        "platform_profile_id",
        "snapshot_id",
        "runtime_java",
        "mapping_namespace",
    ):
        _nonempty(environment[field], f"run environment {field}")
    for field in (
        "platform_profile_sha256",
        "transformed_runtime_sha256",
        "mod_set_sha256",
        "configuration_set_sha256",
    ):
        _sha256(environment[field], f"run environment {field}")
    _require(
        environment["physical_side"] in {"CLIENT", "DEDICATED_SERVER"},
        "run physical side is invalid",
    )
    pack_id = environment["pack_profile_id"]
    pack_digest = environment["pack_profile_sha256"]
    _require(
        (pack_id is None) == (pack_digest is None),
        "run pack profile identity and digest must be present together",
    )
    if pack_id is not None:
        _nonempty(pack_id, "run pack profile ID")
        _sha256(pack_digest, "run pack profile digest")

    world = _exact_keys(raw["world"], _WORLD_KEYS, "run world")
    world_instance = _nonempty(world["world_instance_id"], "run world instance ID")
    _require(
        _QUALIFIED_ID_RE.fullmatch(world_instance) is not None,
        "run world instance ID is not qualified",
    )
    for field in ("world_seed_sha256", "generator_options_sha256"):
        _sha256(world[field], f"run world {field}")
    _nonempty(world["world_type"], "run world type")
    dimensions = world["dimension_ids"]
    _require(
        isinstance(dimensions, list)
        and bool(dimensions)
        and all(isinstance(item, int) and not isinstance(item, bool) for item in dimensions)
        and dimensions == sorted(set(dimensions)),
        "run dimension IDs must be a canonical nonempty integer set",
    )

    return {
        "capture_mode": capture_mode,
        "capture_plan_sha256": capture_plan,
        "fixture_id": fixture_id,
        "fixture_sha256": fixture_sha256,
        "environment": deepcopy(dict(environment)),
        "world": deepcopy(dict(world)),
    }


def _validate_spec(value: Any) -> dict[str, Any]:
    spec = _exact_keys(value, _TOP_LEVEL_KEYS, "worker specification")
    _require(spec["format"] == SPEC_FORMAT, "worker specification format mismatch")
    spec_id = _nonempty(spec["spec_id"], "worker specification ID")
    material = deepcopy(dict(spec))
    material.pop("spec_id")
    expected_id = SPEC_PREFIX + canonical_json_sha256(material)
    _require(spec_id == expected_id, "worker specification content identity mismatch")

    inputs = _exact_keys(spec["inputs"], _INPUT_KEYS, "worker inputs")
    raw_path, raw_sha, _ = _file_input(inputs["raw_ndjson"], _FILE_KEYS, "raw NDJSON")
    schema_path, schema_sha, schema_item = _file_input(
        inputs["raw_schema"], _SCHEMA_FILE_KEYS, "raw schema"
    )
    plan_path, plan_sha, plan_item = _file_input(
        inputs["probe_plan"], _PLAN_FILE_KEYS, "probe plan"
    )
    candidate_path, candidate_sha, _ = _file_input(
        inputs["candidate_lock"], _FILE_KEYS, "candidate lock"
    )
    artifact_path, artifact_sha, _ = _file_input(
        inputs["fixture_artifact"], _FILE_KEYS, "fixture artifact"
    )
    mod_manifest_path, mod_manifest_sha, _ = _file_input(
        inputs["installed_mod_set_manifest"], _FILE_KEYS, "installed mod-set manifest"
    )
    config_manifest_path, config_manifest_sha, _ = _file_input(
        inputs["configuration_set_manifest"], _FILE_KEYS, "configuration-set manifest"
    )
    case_item = _exact_keys(
        inputs["runtime_case_directory"], _DIRECTORY_KEYS, "runtime case directory"
    )
    case_path = _absolute_path(case_item["path"], "runtime case directory path")

    result_value = inputs["fixture_result"]
    if result_value is None:
        result_path = None
        result_sha = None
    else:
        result_path, result_sha, _ = _file_input(
            result_value, _FILE_KEYS, "fixture result"
        )

    outputs = _exact_keys(spec["outputs"], _OUTPUT_KEYS, "worker outputs")
    output_paths = {
        key: _absolute_path(outputs[key], f"worker output {key}")
        for key in sorted(_OUTPUT_KEYS)
    }
    normalized_outputs = {path.resolve(strict=False) for path in output_paths.values()}
    _require(len(normalized_outputs) == len(output_paths), "worker output paths must be distinct")
    protected = {
        path.resolve(strict=False)
        for path in (
            raw_path,
            schema_path,
            plan_path,
            candidate_path,
            artifact_path,
            mod_manifest_path,
            config_manifest_path,
            case_path,
        )
    }
    if result_path is not None:
        protected.add(result_path.resolve(strict=False))
    _require(
        normalized_outputs.isdisjoint(protected),
        "worker output path aliases an input path",
    )
    resolved_case = case_path.resolve(strict=False)
    for output in normalized_outputs:
        _require(
            output != resolved_case and resolved_case not in output.parents,
            "worker outputs must not be written inside the runtime case directory",
        )

    admission = _exact_keys(spec["admission"], _ADMISSION_KEYS, "worker admission")
    requested_mode = _nonempty(admission["requested_mode"], "requested capture mode")
    required_roles = admission["required_roles"]
    _require(
        isinstance(required_roles, list)
        and all(isinstance(item, str) and bool(item) for item in required_roles)
        and required_roles == sorted(set(required_roles)),
        "required roles must be a canonical string set",
    )
    allow_incomplete = admission["allow_incomplete"]
    _require(isinstance(allow_incomplete, bool), "allow_incomplete must be a boolean")
    expected_state = admission["expected_publication_state"]
    _require(expected_state in {"completed", "incomplete"}, "expected publication state is invalid")
    _require(
        allow_incomplete == (expected_state == "incomplete"),
        "allow_incomplete contradicts the expected publication state",
    )
    _require(
        (result_path is None) == (expected_state == "incomplete"),
        "completed cases require a fixture result and incomplete cases forbid one",
    )
    _require(
        requested_mode == spec["run"].get("capture_mode") if isinstance(spec["run"], Mapping) else False,
        "requested capture mode differs from the run capture mode",
    )
    if expected_state == "completed":
        _require(bool(required_roles), "completed cases require explicit probe roles")

    custody = _exact_keys(spec["custody"], _CUSTODY_KEYS, "worker custody")
    prefixes = _string_map(
        custody["trusted_class_prefix_owners"],
        "trusted class-prefix owners",
        digest_values=False,
    )
    sources = _string_map(
        custody["loaded_mod_source_sha256"],
        "loaded mod source digests",
        digest_values=True,
    )
    _require(set(prefixes.values()) <= set(sources), "trusted prefix owner lacks a loaded source digest")
    runtime_inventory_sha256 = custody["runtime_mod_inventory_sha256"]
    _require(
        runtime_inventory_sha256 is None
        or _SHA256_RE.fullmatch(str(runtime_inventory_sha256)) is not None,
        "runtime mod inventory digest must be null or lowercase SHA-256",
    )
    _require(
        (runtime_inventory_sha256 is None) == (expected_state == "incomplete"),
        "completed cases require a runtime inventory digest and incomplete cases forbid one",
    )

    run_spec = _validate_run_spec(spec["run"], plan_file_sha256=plan_sha)

    normalization = _exact_keys(
        spec["normalization"], _NORMALIZATION_KEYS, "worker normalization"
    )
    selection_id = _nonempty(normalization["selection_id"], "selection ID")
    _require(_QUALIFIED_ID_RE.fullmatch(selection_id) is not None, "selection ID is not qualified")
    execution_id = _nonempty(normalization["execution_id"], "execution ID")
    _require(_EXECUTION_ID_RE.fullmatch(execution_id) is not None, "execution ID is invalid")
    domains = normalization["checkpoint_domains"]
    _require(
        isinstance(domains, list)
        and bool(domains)
        and all(isinstance(item, str) and bool(item) for item in domains)
        and domains == sorted(set(domains)),
        "checkpoint domains must be a canonical nonempty string set",
    )
    workbench_identity = _validate_actor(normalization["workbench_identity"])

    return {
        "format": SPEC_FORMAT,
        "spec_id": spec_id,
        "inputs": {
            "raw_path": raw_path,
            "raw_sha256": raw_sha,
            "schema_path": schema_path,
            "schema_sha256": schema_sha,
            "schema_id": _nonempty(schema_item["schema_id"], "raw schema ID"),
            "plan_path": plan_path,
            "plan_sha256": plan_sha,
            "plan_id": _nonempty(plan_item["plan_id"], "probe plan ID"),
            "candidate_path": candidate_path,
            "candidate_sha256": candidate_sha,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha,
            "mod_manifest_path": mod_manifest_path,
            "mod_manifest_sha256": mod_manifest_sha,
            "config_manifest_path": config_manifest_path,
            "config_manifest_sha256": config_manifest_sha,
            "case_path": case_path,
            "result_path": result_path,
            "result_sha256": result_sha,
        },
        "outputs": output_paths,
        "admission": {
            "requested_mode": requested_mode,
            "required_roles": list(required_roles),
            "allow_incomplete": allow_incomplete,
            "expected_publication_state": expected_state,
        },
        "custody": {
            "trusted_class_prefix_owners": prefixes,
            "loaded_mod_source_sha256": sources,
            "runtime_mod_inventory_sha256": runtime_inventory_sha256,
        },
        "run": run_spec,
        "normalization": {
            "selection_id": selection_id,
            "execution_id": execution_id,
            "checkpoint_domains": list(domains),
            "workbench_identity": workbench_identity,
        },
    }


def load_worker_spec(path: str | os.PathLike[str]) -> tuple[dict[str, Any], str]:
    """Load and validate one closed, content-addressed worker specification."""

    spec_path = Path(path)
    _require(spec_path.is_absolute(), "worker specification path must be absolute")
    receipt = _hash_regular_file(spec_path, context="worker specification")
    try:
        encoded = spec_path.read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot read worker specification {spec_path}: {exc}") from exc
    _require_unchanged(receipt, context="worker specification")
    spec = _validate_spec(_parse_json_object(encoded, context="worker specification"))
    _require(
        spec_path.resolve(strict=False)
        not in {output.resolve(strict=False) for output in spec["outputs"].values()},
        "worker output path aliases the worker specification",
    )
    return spec, receipt.sha256


def _load_pinned_json(
    path: Path,
    expected_sha256: str,
    *,
    context: str,
) -> tuple[dict[str, Any], _FileReceipt]:
    receipt = _hash_regular_file(path, context=context)
    _require(receipt.sha256 == expected_sha256, f"{context} digest mismatch")
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot read {context} {path}: {exc}") from exc
    _require_unchanged(receipt, context=context)
    return _parse_json_object(encoded, context=context), receipt


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def run_worker(spec_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Run one case and return the small receipt written at the final boundary."""

    exact_spec_path = Path(spec_path)
    spec, spec_file_sha256 = load_worker_spec(exact_spec_path)
    inputs = spec["inputs"]

    raw_receipt = _hash_regular_file(inputs["raw_path"], context="raw NDJSON")
    _require(raw_receipt.sha256 == inputs["raw_sha256"], "raw NDJSON digest mismatch")
    schema, schema_receipt = _load_pinned_json(
        inputs["schema_path"], inputs["schema_sha256"], context="raw schema"
    )
    _require(schema.get("$id") == inputs["schema_id"], "raw schema ID mismatch")
    probe_plan, plan_receipt = _load_pinned_json(
        inputs["plan_path"], inputs["plan_sha256"], context="probe plan"
    )
    _require(probe_plan.get("plan_id") == inputs["plan_id"], "probe plan ID mismatch")
    _, candidate_receipt = _load_pinned_json(
        inputs["candidate_path"], inputs["candidate_sha256"], context="candidate lock"
    )
    artifact_receipt = _hash_regular_file(inputs["artifact_path"], context="fixture artifact")
    _require(
        artifact_receipt.sha256 == inputs["artifact_sha256"],
        "fixture artifact digest mismatch",
    )
    mod_manifest, mod_manifest_receipt = _load_pinned_json(
        inputs["mod_manifest_path"],
        inputs["mod_manifest_sha256"],
        context="installed mod-set manifest",
    )
    config_manifest, config_manifest_receipt = _load_pinned_json(
        inputs["config_manifest_path"],
        inputs["config_manifest_sha256"],
        context="configuration-set manifest",
    )
    _require(
        candidate_receipt.sha256 == spec["run"]["environment"]["platform_profile_sha256"],
        "candidate lock does not match the explicit platform-profile digest",
    )
    _require(
        artifact_receipt.sha256 == spec["run"]["fixture_sha256"],
        "fixture artifact does not match the explicit run artifact digest",
    )
    mod_manifest_canonical_sha256 = canonical_json_sha256(mod_manifest)
    config_manifest_canonical_sha256 = canonical_json_sha256(config_manifest)
    _require(
        mod_manifest_canonical_sha256 == spec["run"]["environment"]["mod_set_sha256"],
        "installed mod-set manifest does not match the explicit run digest",
    )
    _require(
        config_manifest_canonical_sha256
        == spec["run"]["environment"]["configuration_set_sha256"],
        "configuration-set manifest does not match the explicit run digest",
    )

    result_receipt: _FileReceipt | None = None
    fixture_result = None
    if inputs["result_path"] is not None:
        result_receipt = _hash_regular_file(inputs["result_path"], context="fixture result")
        _require(
            result_receipt.sha256 == inputs["result_sha256"],
            "fixture result digest mismatch",
        )
        fixture_result = load_fixture_result(inputs["result_path"])
        _require(
            fixture_result.artifact_sha256 == result_receipt.sha256,
            "fixture result loader did not admit the pinned bytes",
        )

    admission = admit_cleanroom_raw(
        inputs["raw_path"],
        schema_path=inputs["schema_path"],
        probe_plan_path=inputs["plan_path"],
        requested_mode=spec["admission"]["requested_mode"],
        required_roles=spec["admission"]["required_roles"],
        allow_incomplete=spec["admission"]["allow_incomplete"],
    )
    _require(admission.schema_id == inputs["schema_id"], "raw admission schema ID mismatch")
    _require(
        admission.schema_sha256 == inputs["schema_sha256"],
        "raw admission schema digest mismatch",
    )
    _require(admission.probe_plan_id == inputs["plan_id"], "raw admission plan ID mismatch")
    for receipt, context in (
        (raw_receipt, "raw NDJSON"),
        (schema_receipt, "raw schema"),
        (plan_receipt, "probe plan"),
        (candidate_receipt, "candidate lock"),
        (artifact_receipt, "fixture artifact"),
        (mod_manifest_receipt, "installed mod-set manifest"),
        (config_manifest_receipt, "configuration-set manifest"),
    ):
        _require_unchanged(receipt, context=context)
    if result_receipt is not None:
        _require_unchanged(result_receipt, context="fixture result")

    if fixture_result is not None:
        _require(
            fixture_result.inventory_sha256()
            == spec["custody"]["runtime_mod_inventory_sha256"],
            "fixture result inventory does not match the explicit runtime inventory digest",
        )
        available_sources = {
            row.mod_id: row.source_sha256
            for row in fixture_result.runtime_mod_inventory
            if row.source_sha256 != "unavailable"
        }
        _require(
            available_sources == spec["custody"]["loaded_mod_source_sha256"],
            "fixture result sources do not match the explicit loaded-mod source map",
        )
        _require(
            spec["run"]["world"]["world_seed_sha256"] == FIXED_WORLD_SEED_SHA256,
            "fixture result world seed does not match the explicit run world",
        )
        _require(0 in spec["run"]["world"]["dimension_ids"],
                 "fixture result dimension is absent from the explicit run world")

    custody = collect_cleanroom_runtime_custody(
        inputs["case_path"],
        admission,
        probe_plan,
        trusted_class_prefix_owners=spec["custody"]["trusted_class_prefix_owners"],
        loaded_mod_source_sha256=spec["custody"]["loaded_mod_source_sha256"],
    )
    _require(
        custody.class_dump_manifest.manifest_sha256
        == spec["run"]["environment"]["transformed_runtime_sha256"],
        "Foundation class-dump manifest does not match the explicit transformed runtime",
    )
    run = new_run(**spec["run"])
    publication = _normalize_cleanroom_raw_publication(
        admission,
        run=run,
        selection_id=spec["normalization"]["selection_id"],
        probe_plan=probe_plan,
        actor_inventory=custody.actor_inventory,
        class_dump_sha256=custody.class_dump_sha256,
        workbench_identity=spec["normalization"]["workbench_identity"],
        monotonic_ns=_CanonicalClock(),
        checkpoint_domains=spec["normalization"]["checkpoint_domains"],
    )
    publication_state = _validated_bundle_publication_state(publication)
    _require(
        publication_state == spec["admission"]["expected_publication_state"],
        "normalized publication state contradicts the worker specification",
    )

    bundle_path = spec["outputs"]["canonical_bundle"]
    projection_path = spec["outputs"]["cleanroom_projection"]
    receipt_path = spec["outputs"]["worker_receipt"]
    bundle_write = _write_validated_bundle(bundle_path, publication)
    _require(
        bundle_write.publication_state == publication_state
        and bundle_write.run_id == run["run_id"],
        "bundle write receipt contradicts the validated publication",
    )
    try:
        bundle_metadata = bundle_path.lstat()
    except OSError as exc:
        raise CaptureValidationError(
            f"cannot inspect canonical bundle output {bundle_path}: {exc}"
        ) from exc
    _require(
        stat.S_ISREG(bundle_metadata.st_mode)
        and not stat.S_ISLNK(bundle_metadata.st_mode)
        and bundle_metadata.st_size == bundle_write.byte_count,
        "canonical bundle output does not match its atomic write receipt",
    )
    projection = _write_validated_cleanroom_bundle_projection(
        projection_path,
        publication,
        bundle_write,
        execution_id=spec["normalization"]["execution_id"],
    )

    _require(
        projection.source_bundle_sha256 == bundle_write.canonical_json_sha256,
        "projection does not cite the canonical bundle",
    )
    projection_file = _hash_regular_file(projection_path, context="Cleanroom projection output")
    class_dump_rows = dict(custody.class_dump_sha256)
    actor_inventory_rows = [dict(row) for row in custody.actor_inventory]
    fixture_canonical_sha256 = (
        None if fixture_result is None else fixture_result.canonical_document_sha256
    )
    fixture_route_order = None if fixture_result is None else fixture_result.route_order

    receipt_material = {
        "schema": RECEIPT_SCHEMA,
        "spec_id": spec["spec_id"],
        "spec_file_sha256": spec_file_sha256,
        "raw_ndjson_sha256": raw_receipt.sha256,
        "raw_schema_id": admission.schema_id,
        "raw_schema_sha256": admission.schema_sha256,
        "probe_plan_id": admission.probe_plan_id,
        "probe_plan_file_sha256": plan_receipt.sha256,
        "candidate_lock_file_sha256": candidate_receipt.sha256,
        "fixture_artifact_file_sha256": artifact_receipt.sha256,
        "installed_mod_set_manifest_file_sha256": mod_manifest_receipt.sha256,
        "installed_mod_set_manifest_canonical_sha256": mod_manifest_canonical_sha256,
        "configuration_set_manifest_file_sha256": config_manifest_receipt.sha256,
        "configuration_set_manifest_canonical_sha256": config_manifest_canonical_sha256,
        "fixture_result_file_sha256": (
            None if result_receipt is None else result_receipt.sha256
        ),
        "fixture_result_canonical_sha256": fixture_canonical_sha256,
        "fixture_route_order": fixture_route_order,
        "runtime_mod_inventory_sha256": spec["custody"]["runtime_mod_inventory_sha256"],
        "capture_nonce": admission.capture_nonce,
        "raw_row_count": admission.coverage_summary["row_count"],
        "raw_completion_state": admission.control_summary["completion_state"],
        "class_dump_inventory_sha256": canonical_json_sha256(class_dump_rows),
        "foundation_class_dump_manifest_format": custody.class_dump_manifest.format,
        "foundation_class_dump_class_count": custody.class_dump_manifest.class_count,
        "foundation_class_dump_total_size_bytes": custody.class_dump_manifest.total_size_bytes,
        "foundation_class_dump_manifest_sha256": custody.class_dump_manifest.manifest_sha256,
        "actor_inventory_sha256": canonical_json_sha256(actor_inventory_rows),
        "run_id": bundle_write.run_id,
        "publication_state": publication_state,
        "publication_id": bundle_write.publication_id,
        "bundle_canonical_sha256": bundle_write.canonical_json_sha256,
        "bundle_file_sha256": bundle_write.file_sha256,
        "bundle_record_count": bundle_write.record_count,
        "projection_id": projection.projection_id,
        "projection_canonical_sha256": canonical_json_sha256(projection.as_dict()),
        "projection_file_sha256": projection_file.sha256,
    }
    receipt = parse_cleanroom_case_worker_receipt({
        "receipt_id": RECEIPT_PREFIX + canonical_json_sha256(receipt_material),
        **receipt_material,
    })
    _atomic_write_json(receipt_path, receipt)
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize one exact Cleanroom worldgen runtime case from a pinned JSON spec."
    )
    parser.add_argument("--spec", type=Path, required=True, help="absolute worker-spec JSON path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        receipt = run_worker(arguments.spec)
    except (CaptureValidationError, OSError) as exc:
        print(f"cleanroom case worker failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "receipt_id": receipt["receipt_id"],
                "publication_state": receipt["publication_state"],
                "run_id": receipt["run_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
