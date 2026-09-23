"""Prelaunch identity envelope for bounded Worldgen Iteration captures.

The envelope is intentionally sealed before a JVM is started.  Producers cite
it; a later terminal seal cites the producer receipts.  This keeps the identity
graph acyclic while giving Observatory, World Studio, and Strata one exact
runtime/world/dimension boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from workbench_crucible_context import (
    load_context_ref,
    load_input_binding,
    seal_context_ref,
    seal_input_binding,
)
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id


ENVELOPE_FORMAT = "workbench-worldgen-execution-envelope-v2"
TERMINAL_FORMAT = "workbench-worldgen-execution-terminal-v2"


class WorldgenIdentityError(ValueError):
    """One worldgen identity boundary is incomplete or internally inconsistent."""


def _id(kind: str, value: Any) -> str:
    return content_id(kind, value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_text(value: object, label: str) -> str:
    if type(value) is not str or not value or any(char in value for char in "\r\n\x00"):
        raise WorldgenIdentityError(f"{label} must be non-empty single-line text")
    return value


def _exact_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise WorldgenIdentityError(f"{label} must be an exact integer")
    return value


def _artifact_rows(value: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if type(raw) is not dict or set(raw) != {"role", "sha256", "size_bytes"}:
            raise WorldgenIdentityError(f"artifact {index} has an unsupported shape")
        role = _exact_text(raw["role"], f"artifact {index} role")
        digest = _exact_text(raw["sha256"], f"artifact {index} sha256")
        size = _exact_int(raw["size_bytes"], f"artifact {index} size")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise WorldgenIdentityError(f"artifact {index} sha256 is invalid")
        if size < 0:
            raise WorldgenIdentityError(f"artifact {index} size is negative")
        rows.append({"role": role, "sha256": digest, "size_bytes": size})
    rows.sort(key=canonical_json_bytes)
    if not rows or len({row["role"] for row in rows}) != len(rows):
        raise WorldgenIdentityError("artifacts must be non-empty with unique roles")
    return tuple(rows)


def _authority(label: str) -> dict[str, str]:
    return {
        "owner_authority_id": _id("authority", {"owner": label}),
        "owner_revision_id": _id("owner-revision", {"owner": label, "version": 1}),
        "authority_adapter_id": _id(
            "authority-adapter", {"owner": label, "adapter": "worldgen-v2"}
        ),
    }


def _external(kind: str, semantic_key: str, **fields: Any) -> dict[str, Any]:
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "format": "workbench-worldgen-envelope-external-v1",
        "kind": kind,
        "semantic_key": semantic_key,
        **fields,
    }
    return {**body, "id": _id(kind, body)}


@dataclass(frozen=True, slots=True)
class WorldgenExecutionEnvelope:
    envelope_id: str
    run_plan_id: str
    context_ref_id: str
    input_binding_id: str
    runtime_epoch_id: str
    world_instance_id: str
    world_creation_receipt_id: str
    save_lineage_id: str
    world_epoch_id: str
    dimension_instance_id: str
    job_id: str
    attempt_id: str
    execution_instance_id: str
    process_identity_id: str
    value: Mapping[str, Any]
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return dict(self.value)


@dataclass(frozen=True, slots=True)
class WorldgenExecutionTerminal:
    terminal_id: str
    envelope_id: str
    value: Mapping[str, Any]
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return dict(self.value)


def seal_execution_envelope(
    *,
    workspace_key: str,
    profile_id: str,
    platform_profile_id: str,
    execution_nonce: str,
    seed: int,
    dimension: int,
    region: tuple[int, int, int, int],
    world_type: str,
    generator_id: str,
    max_tick_time_ms: int,
    scan_timeout_seconds: int,
    artifacts: Sequence[Mapping[str, Any]],
    action_policy_id: str,
    action_policy_state: str = "experimental-draft",
) -> WorldgenExecutionEnvelope:
    """Seal one exact, fresh prelaunch world/runtime identity closure."""

    workspace_key = _exact_text(workspace_key, "workspace key")
    profile_id = _exact_text(profile_id, "profile ID")
    platform_profile_id = _exact_text(platform_profile_id, "platform profile ID")
    execution_nonce = _exact_text(execution_nonce, "execution nonce")
    world_type = _exact_text(world_type, "world type")
    generator_id = _exact_text(generator_id, "generator ID")
    action_policy_id = _exact_text(action_policy_id, "action policy ID")
    action_policy_state = _exact_text(action_policy_state, "action policy state")
    seed = _exact_int(seed, "seed")
    dimension = _exact_int(dimension, "dimension")
    max_tick_time_ms = _exact_int(max_tick_time_ms, "max tick time")
    scan_timeout_seconds = _exact_int(scan_timeout_seconds, "scan timeout")
    if max_tick_time_ms != -1 and max_tick_time_ms < 1:
        raise WorldgenIdentityError("max tick time must be -1 or positive")
    if scan_timeout_seconds < 1:
        raise WorldgenIdentityError("scan timeout must be positive")
    if (
        type(region) is not tuple
        or len(region) != 4
        or any(type(item) is not int for item in region)
    ):
        raise WorldgenIdentityError("region must be four exact integers")
    min_x, min_z, width, height = region
    if width < 1 or height < 1 or width * height > 1024:
        raise WorldgenIdentityError("region is empty or exceeds 1024 chunks")
    artifact_rows = _artifact_rows(artifacts)

    platform_authority = _authority("cleanroom-platform-profile")
    pack_authority = _authority("supersymmetry-pack-profile")
    crucible_authority = _authority("crucible-worldgen-capture")
    profile_authority = _authority("supersymmetry-worldgen-policy")

    store = _external("store", "workbench-operational-store-v1")
    workspace = _external("workspace", workspace_key)
    workspace_registration = _external(
        "workspace-registration-revision",
        workspace_key,
        store_id=store["id"],
        workspace_id=workspace["id"],
    )
    platform_revision = _external(
        "platform-profile-revision", platform_profile_id,
        authority=platform_authority,
    )
    platform_adapter = _external(
        "profile-adapter", "cleanroom-worldgen-v2",
        applicable_profile_revision_id=platform_revision["id"],
        authority=platform_authority,
    )
    platform_support = _external(
        "support-decision", "cleanroom-candidate-provisional",
        profile_revision_id=platform_revision["id"],
        support_state="provisional",
        authority=platform_authority,
    )
    pack_revision = _external(
        "pack-profile-revision", profile_id,
        authority=pack_authority,
    )
    pack_adapter = _external(
        "profile-adapter", "supersymmetry-worldgen-v2",
        applicable_profile_revision_id=pack_revision["id"],
        authority=pack_authority,
    )
    pack_support = _external(
        "support-decision", "supersymmetry-experimental",
        profile_revision_id=pack_revision["id"],
        support_state="experimental",
        authority=pack_authority,
    )

    source_lock = _external(
        "source-lock", "worldgen-source-closure",
        artifact_rows=list(artifact_rows),
    )
    dependency_lock = _external(
        "dependency-lock", "cleanroom-runtime-inputs",
        platform_profile_revision_id=platform_revision["id"],
    )
    artifact_lock = _external(
        "artifact-lock", "worldgen-artifacts", artifact_rows=list(artifact_rows)
    )
    configuration_lock = _external(
        "configuration-lock", "worldgen-launch-configuration",
        seed=seed,
        dimension=dimension,
        region=list(region),
        max_tick_time_ms=max_tick_time_ms,
        scan_timeout_seconds=scan_timeout_seconds,
        world_type=world_type,
    )
    runtime_target = _external(
        "runtime-target", "cleanroom-dedicated-server",
        platform_profile_revision_id=platform_revision["id"],
    )
    installed_runtime = _external(
        "installed-runtime", "worldgen-runtime-install",
        artifact_lock_id=artifact_lock["id"],
        dependency_lock_id=dependency_lock["id"],
    )

    run_plan_body = {
        "artifacts": list(artifact_rows),
        "dimension": dimension,
        "generator_id": generator_id,
        "max_tick_time_ms": max_tick_time_ms,
        "platform_profile_revision_id": platform_revision["id"],
        "profile_revision_id": pack_revision["id"],
        "region": list(region),
        "scan_timeout_seconds": scan_timeout_seconds,
        "seed": seed,
        "world_type": world_type,
    }
    run_plan_id = _id("run-plan", run_plan_body)
    nonce_digest = _sha256_bytes(execution_nonce.encode("utf-8"))
    job_id = _id("job", {"run_plan_id": run_plan_id, "nonce_sha256": nonce_digest})
    attempt_id = _id("attempt", {"job_id": job_id, "ordinal": 1})
    execution_instance_id = _id(
        "execution-instance", {"attempt_id": attempt_id, "side": "dedicated-server"}
    )
    process_identity_id = _id(
        "process-identity", {"execution_instance_id": execution_instance_id}
    )
    runtime_epoch = _external(
        "runtime-epoch", "worldgen-fresh-runtime-epoch",
        execution_instance_id=execution_instance_id,
        installed_runtime_id=installed_runtime["id"],
        process_identity_id=process_identity_id,
    )
    world_instance = _external(
        "world-instance", "worldgen-fresh-world",
        execution_instance_id=execution_instance_id,
    )
    save_lineage = _external(
        "save-lineage", "worldgen-fresh-lineage",
        world_instance_id=world_instance["id"],
        parent_save_lineage_id=None,
    )
    world_creation = _external(
        "world-creation-receipt", "worldgen-fresh-creation",
        runtime_epoch_id=runtime_epoch["id"],
        save_lineage_id=save_lineage["id"],
        world_instance_id=world_instance["id"],
    )
    world_epoch = _external(
        "world-epoch", "worldgen-loaded-epoch",
        runtime_epoch_id=runtime_epoch["id"],
        save_lineage_id=save_lineage["id"],
        seed=seed,
        world_creation_receipt_id=world_creation["id"],
        world_instance_id=world_instance["id"],
    )
    world_type_record = _external("world-type", world_type)
    generator = _external("world-generator", generator_id)
    generator_config = _external(
        "object-descriptor", "worldgen-generator-configuration",
        configuration_lock_id=configuration_lock["id"],
    )
    dimension_registry = _external("dimension-registry", "cleanroom-dimensions")
    provider = _external("world-provider", "overworld-surface-provider")
    dimension_instance = _external(
        "dimension-instance", "worldgen-dimension-instance",
        dimension_registry_id=dimension_registry["id"],
        numeric_dimension_id=dimension,
        provider_id=provider["id"],
        world_epoch_id=world_epoch["id"],
    )
    experiment = _external("experiment", "crucible-m4-w01")
    capture_scope = _external(
        "capture-scope", "worldgen-bounded-region",
        dimension_instance_id=dimension_instance["id"],
        maximum_chunk_exclusive={"x": min_x + width, "z": min_z + height},
        minimum_chunk_inclusive={"x": min_x, "z": min_z},
    )
    privacy_class = _external("privacy-class", "project-local-evidence")
    resource_budget = _external("resource-budget-class", "worldgen-4g-bounded")

    context_candidate = {
        "artifact_lock_ids": [artifact_lock["id"]],
        "canonicalizer": CANONICALIZER_ID,
        "dependency_lock_ids": [dependency_lock["id"]],
        "dimension_scope": {
            "dimension_instance_id": dimension_instance["id"],
            "dimension_registry_id": dimension_registry["id"],
            "kind": "selected",
            "numeric_dimension_id": dimension,
            "provider_id": provider["id"],
            "world_epoch_id": world_epoch["id"],
        },
        "format": "workbench-crucible-context-ref-v2",
        "kind": "context-ref",
        "operation_scopes": [
            {"scope_id": experiment["id"], "scope_kind": "experiment"},
            {"scope_id": capture_scope["id"], "scope_kind": "capture-scope"},
        ],
        "privacy_class_id": privacy_class["id"],
        "profile_scope": {
            "pack": {
                "kind": "selected",
                "pack_profile_revision_id": pack_revision["id"],
                "profile_adapter_id": pack_adapter["id"],
                "profile_authority": pack_authority,
                "support_decision_ids": [pack_support["id"]],
                "support_state": "experimental",
            },
            "platform": {
                "platform_profile_revision_id": platform_revision["id"],
                "profile_adapter_id": platform_adapter["id"],
                "profile_authority": platform_authority,
                "support_decision_ids": [platform_support["id"]],
                "support_state": "provisional",
            },
        },
        "region_scope": {
            "dimension_instance_id": dimension_instance["id"],
            "kind": "chunk-bounds",
            "maximum_chunk_exclusive": {"x": min_x + width, "z": min_z + height},
            "minimum_chunk_inclusive": {"x": min_x, "z": min_z},
        },
        "resource_budget_class_id": resource_budget["id"],
        "runtime_scope": {
            "configuration_lock_ids": [configuration_lock["id"]],
            "installed_runtime_id": installed_runtime["id"],
            "kind": "selected",
            "runtime_epoch_id": runtime_epoch["id"],
            "runtime_target_id": runtime_target["id"],
            "side": "dedicated-server",
        },
        "schema_id": "workbench://schemas/crucible/crucible-context-ref-v2.schema.json",
        "schema_version": 2,
        "source_lock_ids": [source_lock["id"]],
        "store_id": store["id"],
        "workspace_binding": {
            "workspace_id": workspace["id"],
            "workspace_registration_revision_id": workspace_registration["id"],
        },
        "world_scope": {
            "generation": {
                "generator_configuration_object_descriptor_id": generator_config["id"],
                "generator_id": generator["id"],
                "kind": "applicable",
                "seed": seed,
                "world_type_id": world_type_record["id"],
            },
            "kind": "selected",
            "runtime_epoch_id": runtime_epoch["id"],
            "save_lineage_id": save_lineage["id"],
            "world_creation_receipt_id": world_creation["id"],
            "world_epoch_id": world_epoch["id"],
            "world_instance_id": world_instance["id"],
        },
    }
    context = seal_context_ref(context_candidate)

    raw_schema_descriptor = _external(
        "object-descriptor", "observatory-raw-v1-schema",
        artifact_role="observatory-raw-schema",
    )
    causal_schema_descriptor = _external(
        "object-descriptor", "world-studio-causal-v2-schema",
        artifact_role="world-studio-causal-schema",
    )
    observatory_adapter = _external(
        "transport-normalizer", "worldgen-observatory-v2",
        authority=crucible_authority,
    )
    strata_adapter = _external(
        "transport-normalizer", "strata-same-epoch-v2",
        authority=crucible_authority,
    )
    selector_policy = _external(
        "policy", "worldgen-bounded-selector-v2", authority=crucible_authority
    )
    action_policy = _external(
        "policy", "supersymmetry-worldgen-action-policy-draft-v1",
        declared_policy_id=action_policy_id,
        policy_state=action_policy_state,
        authority=profile_authority,
    )
    input_candidate = {
        "adapter_bindings": sorted(
            [
                {
                    "adapter_id": observatory_adapter["id"],
                    "adapter_kind": "transport-normalizer",
                    "authority_binding": crucible_authority,
                    "input_key": "worldgen.observatory",
                },
                {
                    "adapter_id": strata_adapter["id"],
                    "adapter_kind": "transport-normalizer",
                    "authority_binding": crucible_authority,
                    "input_key": "worldgen.strata",
                },
            ],
            key=canonical_json_bytes,
        ),
        "canonicalizer": CANONICALIZER_ID,
        "context_ref_id": context.id,
        "evidence_set_bindings": [],
        "format": "workbench-crucible-input-binding-v2",
        "graph_bindings": [],
        "graph_set_bindings": [],
        "index_bindings": [],
        "kind": "input-binding",
        "ontology_bindings": [],
        "policy_bindings": sorted(
            [
                {
                    "authority_binding": crucible_authority,
                    "input_key": "worldgen.selector",
                    "policy_id": selector_policy["id"],
                },
                {
                    "authority_binding": profile_authority,
                    "input_key": "worldgen.action-policy",
                    "policy_id": action_policy["id"],
                },
            ],
            key=canonical_json_bytes,
        ),
        "recipe_bindings": [],
        "schema_bindings": sorted(
            [
                {
                    "input_key": "worldgen.causal-schema",
                    "schema_id": "workbench://schemas/crucible/worldgen-causal-trace-v2.schema.json",
                    "schema_object_descriptor_id": causal_schema_descriptor["id"],
                },
                {
                    "input_key": "worldgen.observatory-raw-schema",
                    "schema_id": "workbench://schemas/cleanroom/worldgen-observatory-raw-v1.schema.json",
                    "schema_object_descriptor_id": raw_schema_descriptor["id"],
                },
            ],
            key=canonical_json_bytes,
        ),
        "schema_id": "workbench://schemas/crucible/crucible-input-binding-v2.schema.json",
        "schema_version": 2,
    }
    input_binding = seal_input_binding(input_candidate)

    externals = [
        store, workspace, workspace_registration, platform_revision,
        platform_adapter, platform_support, pack_revision, pack_adapter,
        pack_support, source_lock, dependency_lock, artifact_lock,
        configuration_lock, runtime_target, installed_runtime, runtime_epoch,
        world_instance, save_lineage, world_creation, world_epoch,
        world_type_record, generator, generator_config, dimension_registry,
        provider, dimension_instance, experiment, capture_scope, privacy_class,
        resource_budget, raw_schema_descriptor, causal_schema_descriptor,
        observatory_adapter, strata_adapter, selector_policy, action_policy,
    ]
    for authority in (
        platform_authority, pack_authority, crucible_authority, profile_authority
    ):
        for field, kind in (
            ("owner_authority_id", "authority"),
            ("owner_revision_id", "owner-revision"),
            ("authority_adapter_id", "authority-adapter"),
        ):
            externals.append(
                {
                    "canonicalizer": CANONICALIZER_ID,
                    "format": "workbench-worldgen-envelope-external-v1",
                    "id": authority[field],
                    "kind": kind,
                    "semantic_key": authority[field],
                }
            )
    externals.sort(key=lambda row: row["id"].encode("utf-8"))
    if len({row["id"] for row in externals}) != len(externals):
        raise WorldgenIdentityError("identity registry contains duplicate IDs")

    body = {
        "artifacts": list(artifact_rows),
        "attempt_id": attempt_id,
        "canonicalizer": CANONICALIZER_ID,
        "context_ref": context.to_dict(),
        "dimension_instance_id": dimension_instance["id"],
        "execution_instance_id": execution_instance_id,
        "format": ENVELOPE_FORMAT,
        "identity_registry": externals,
        "input_binding": input_binding.to_dict(),
        "job_id": job_id,
        "kind": "worldgen-execution-envelope",
        "process_identity_id": process_identity_id,
        "run_plan": {**run_plan_body, "id": run_plan_id},
        "runtime_epoch_id": runtime_epoch["id"],
        "schema_version": 2,
        "world_creation_receipt_id": world_creation["id"],
        "world_epoch_id": world_epoch["id"],
        "world_instance_id": world_instance["id"],
        "save_lineage_id": save_lineage["id"],
    }
    envelope_id = _id("worldgen-execution-envelope", body)
    value = {**body, "id": envelope_id}
    canonical = canonical_json_bytes(value)
    return WorldgenExecutionEnvelope(
        envelope_id,
        run_plan_id,
        context.id,
        input_binding.id,
        runtime_epoch["id"],
        world_instance["id"],
        world_creation["id"],
        save_lineage["id"],
        world_epoch["id"],
        dimension_instance["id"],
        job_id,
        attempt_id,
        execution_instance_id,
        process_identity_id,
        value,
        canonical,
    )


def load_execution_envelope(raw: bytes) -> WorldgenExecutionEnvelope:
    """Validate closed envelope bytes and reconstruct the immutable view."""

    from workbench_api.canonical import parse_canonical_json

    try:
        value = parse_canonical_json(raw)
    except Exception as exc:
        raise WorldgenIdentityError("execution envelope is not canonical JSON") from exc
    required = {
        "artifacts", "attempt_id", "canonicalizer", "context_ref",
        "dimension_instance_id", "execution_instance_id", "format", "id",
        "identity_registry", "input_binding", "job_id", "kind",
        "process_identity_id", "run_plan", "runtime_epoch_id", "schema_version",
        "save_lineage_id", "world_creation_receipt_id", "world_epoch_id",
        "world_instance_id",
    }
    if type(value) is not dict or set(value) != required:
        raise WorldgenIdentityError("execution envelope has an unsupported closed shape")
    if (
        value["format"] != ENVELOPE_FORMAT
        or value["kind"] != "worldgen-execution-envelope"
        or value["schema_version"] != 2
        or value["canonicalizer"] != CANONICALIZER_ID
    ):
        raise WorldgenIdentityError("execution envelope version differs")
    body = dict(value)
    supplied_id = body.pop("id")
    if supplied_id != _id("worldgen-execution-envelope", body):
        raise WorldgenIdentityError("execution envelope identity differs")
    context = load_context_ref(canonical_json_bytes(value["context_ref"]))
    binding = load_input_binding(canonical_json_bytes(value["input_binding"]))
    if binding.to_dict()["context_ref_id"] != context.id:
        raise WorldgenIdentityError("input binding names another ContextRef")
    registry = value["identity_registry"]
    if type(registry) is not list or any(type(row) is not dict for row in registry):
        raise WorldgenIdentityError("identity registry is invalid")
    registry_by_id = {row.get("id"): row for row in registry}
    if len(registry_by_id) != len(registry):
        raise WorldgenIdentityError("identity registry IDs are duplicated")
    for reference in (*context.references, *binding.references):
        row = registry_by_id.get(reference.record_id)
        if row is None or row.get("kind") != reference.expected_kind:
            raise WorldgenIdentityError(
                f"identity registry does not satisfy {reference.path}"
            )
    context_value = context.to_dict()
    exact_pairs = (
        (value["runtime_epoch_id"], context_value["runtime_scope"]["runtime_epoch_id"]),
        (value["runtime_epoch_id"], context_value["world_scope"]["runtime_epoch_id"]),
        (value["world_instance_id"], context_value["world_scope"]["world_instance_id"]),
        (value["world_creation_receipt_id"], context_value["world_scope"]["world_creation_receipt_id"]),
        (value["save_lineage_id"], context_value["world_scope"]["save_lineage_id"]),
        (value["world_epoch_id"], context_value["world_scope"]["world_epoch_id"]),
        (value["world_epoch_id"], context_value["dimension_scope"]["world_epoch_id"]),
        (value["dimension_instance_id"], context_value["dimension_scope"]["dimension_instance_id"]),
        (value["dimension_instance_id"], context_value["region_scope"]["dimension_instance_id"]),
    )
    if any(left != right for left, right in exact_pairs):
        raise WorldgenIdentityError("execution/world/dimension identities are incompatible")
    run_plan = value["run_plan"]
    if type(run_plan) is not dict or run_plan.get("id") != _id(
        "run-plan", {key: item for key, item in run_plan.items() if key != "id"}
    ):
        raise WorldgenIdentityError("run plan identity differs")
    return WorldgenExecutionEnvelope(
        supplied_id,
        run_plan["id"],
        context.id,
        binding.id,
        value["runtime_epoch_id"],
        value["world_instance_id"],
        value["world_creation_receipt_id"],
        value["save_lineage_id"],
        value["world_epoch_id"],
        value["dimension_instance_id"],
        value["job_id"],
        value["attempt_id"],
        value["execution_instance_id"],
        value["process_identity_id"],
        value,
        raw,
    )


def seal_execution_terminal(
    envelope: WorldgenExecutionEnvelope,
    *,
    process_exit_code: int,
    process_outcome: str,
    producer_receipt_ids: Sequence[str],
    launch_log_sha256: str,
    raw_capture_sha256: str,
) -> WorldgenExecutionTerminal:
    """Seal the acyclic post-run result over all exact producer receipts."""

    envelope = load_execution_envelope(envelope.canonical_bytes)
    process_exit_code = _exact_int(process_exit_code, "process exit code")
    process_outcome = _exact_text(process_outcome, "process outcome")
    launch_log_sha256 = _exact_text(launch_log_sha256, "launch log sha256")
    raw_capture_sha256 = _exact_text(raw_capture_sha256, "raw capture sha256")
    for label, digest in (
        ("launch log", launch_log_sha256), ("raw capture", raw_capture_sha256)
    ):
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise WorldgenIdentityError(f"{label} digest is invalid")
    receipts = sorted(
        (_exact_text(item, "producer receipt ID") for item in producer_receipt_ids),
        key=lambda item: item.encode("utf-8"),
    )
    if not receipts or len(set(receipts)) != len(receipts):
        raise WorldgenIdentityError("producer receipt IDs must be non-empty and unique")
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "execution_envelope_id": envelope.envelope_id,
        "format": TERMINAL_FORMAT,
        "kind": "worldgen-execution-terminal",
        "launch_log_sha256": launch_log_sha256,
        "process_exit_code": process_exit_code,
        "process_identity_id": envelope.process_identity_id,
        "process_outcome": process_outcome,
        "producer_receipt_ids": receipts,
        "raw_capture_sha256": raw_capture_sha256,
        "schema_version": 2,
    }
    terminal_id = _id("worldgen-execution-terminal", body)
    value = {**body, "id": terminal_id}
    return WorldgenExecutionTerminal(
        terminal_id, envelope.envelope_id, value, canonical_json_bytes(value)
    )


__all__ = [
    "ENVELOPE_FORMAT",
    "TERMINAL_FORMAT",
    "WorldgenExecutionEnvelope",
    "WorldgenExecutionTerminal",
    "WorldgenIdentityError",
    "load_execution_envelope",
    "seal_execution_envelope",
    "seal_execution_terminal",
]
