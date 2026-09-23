#!/usr/bin/env python3

"""Normalize accepted Atlas static primitives into a fail-closed M4 graph."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Protocol, Sequence

from workbench_api.profile_extensions import (
    ProfileExtensionError,
    require_profile_extension,
)
if __package__:
    from .atlas_causal_provenance_contract import (
        EVIDENCE_PREFIX,
        NODE_PREFIX,
        POLICY_SHA256_V1,
        RELATION_PREFIX,
        ProvenanceContractError,
        _validate_evidence,
        _validate_node_identity,
        canonical_json,
        canonical_sha256,
        content_id,
        load_policy,
    )
    from .knowledge_catalog import (
        _validate_schema_definition,
        _validate_schema_value,
        read_json,
    )
    from .layout import DATA_ROOT, EXAMPLE_ROOT, SCHEMA_ROOT, atlas_knowledge_root
else:  # Direct module loading.
    from workbench_atlas.atlas_causal_provenance_contract import (
        EVIDENCE_PREFIX,
        NODE_PREFIX,
        POLICY_SHA256_V1,
        RELATION_PREFIX,
        ProvenanceContractError,
        _validate_evidence,
        _validate_node_identity,
        canonical_json,
        canonical_sha256,
        content_id,
        load_policy,
    )
    from workbench_atlas.knowledge_catalog import (
        _validate_schema_definition,
        _validate_schema_value,
        read_json,
    )
    from workbench_atlas.layout import DATA_ROOT, EXAMPLE_ROOT, SCHEMA_ROOT, atlas_knowledge_root


SCHEMA_PATH = SCHEMA_ROOT / "atlas-provenance-normalization-v1.schema.json"
CORPUS_ROOT = DATA_ROOT
EXAMPLE_PATH = EXAMPLE_ROOT / "atlas-provenance-normalization-example-v1.json"
def default_static_input_path(name: str) -> Path:
    return (
        atlas_knowledge_root()
        / "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
        / name
    )

# Existing V1 primitive identities are part of the retained record contract.
# Reading them does not require installing the profile that produced them.
OPERATION_PREFIX = "atlas-pack-operation:sha256:"
CONFIG_PREFIX = "atlas-pack-configuration:sha256:"
BOUNDARY_PREFIX = "atlas-pack-unresolved-boundary:sha256:"

FORMAT = "susy-atlas-provenance-normalization-v1"
RUNTIME_BUNDLE_FORMAT = "susy-atlas-provenance-runtime-evidence-bundle-v1"
NORMALIZATION_PREFIX = "atlas-provenance-normalization:sha256:"
RUNTIME_BUNDLE_PREFIX = "atlas-provenance-runtime-bundle:sha256:"
CANDIDATE_PREFIX = "atlas-provenance-operation-candidate:sha256:"
FRONTIER_PREFIX = "atlas-provenance-frontier:sha256:"
SOURCE_SPAN_PREFIX = "atlas-source-span:sha256:"

RUNTIME_NODE_CLASSES = {
    "script-operation",
    "lifecycle-event",
    "runtime-state",
    "final-runtime-record",
}
TRANSITION_PREDICATES = {
    "registers",
    "copies",
    "transforms",
    "mutates",
    "removes",
    "replaces",
}
OPERATION_PREDICATES = {
    "addition": {"registers", "transforms"},
    "copy": {"copies"},
    "property-change": {"mutates", "transforms"},
    "removal": {"removes"},
    "replacement": {"replaces"},
}
FRONTIER_REASONS = {
    "dynamic-script-unresolved",
    "runtime-transition-unobserved",
    "identity-reconciliation-unresolved",
}


class AtlasProvenanceNormalizationError(ValueError):
    """Raised when N01 cannot preserve an exact evidence boundary."""


class ProvenancePrimitiveAdapter(Protocol):
    """Owner validation of original source and mutation primitives."""

    PROVENANCE_PRIMITIVES_API_VERSION: int

    def validate_static_primitives(
        self, source_index: dict[str, Any], extraction: dict[str, Any],
        *, resolver: Any = None,
    ) -> None: ...


def _primitive_adapter(pack_profile: str | None) -> ProvenancePrimitiveAdapter:
    if not pack_profile:
        raise AtlasProvenanceNormalizationError(
            "static provenance normalization requires an explicit pack profile "
            "(--pack-profile) or a primitive-validation adapter"
        )
    try:
        adapter = require_profile_extension("workbench.provenance_primitives", pack_profile)
    except ProfileExtensionError as exc:
        raise AtlasProvenanceNormalizationError(
            f"static provenance adapter unavailable for {pack_profile!r}: {exc}; "
            "install and enable the selected profile's provenance adapter"
        ) from exc
    return _validate_primitive_adapter(adapter)


def _validate_primitive_adapter(adapter: Any) -> ProvenancePrimitiveAdapter:
    if (
        type(getattr(adapter, "PROVENANCE_PRIMITIVES_API_VERSION", None)) is not int
        or adapter.PROVENANCE_PRIMITIVES_API_VERSION != 1
        or not callable(getattr(adapter, "validate_static_primitives", None))
    ):
        raise AtlasProvenanceNormalizationError(
            "static provenance adapter must support primitive-validation API 1"
        )
    return adapter


def _selected_source_resolver(
    adapter: ProvenancePrimitiveAdapter,
    *,
    source_root: Path | None,
    pack_root: Path | None,
) -> Any:
    """Ask the selected profile to bind explicit source paths, if supplied."""

    if source_root is None and pack_root is None:
        return None
    factory = getattr(adapter, "source_resolver", None)
    if not callable(factory):
        raise AtlasProvenanceNormalizationError(
            "selected profile cannot resolve explicit source paths"
        )
    try:
        return factory(source_root=source_root, pack_root=pack_root)
    except (TypeError, ValueError) as exc:
        raise AtlasProvenanceNormalizationError(
            f"selected profile source paths are invalid: {exc}"
        ) from exc


def _exact_fields(value: Any, expected: Iterable[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AtlasProvenanceNormalizationError(f"{label} must be an object")
    expected_set = set(expected)
    if set(value) != expected_set:
        raise AtlasProvenanceNormalizationError(
            f"{label} fields differ: expected {sorted(expected_set)}, "
            f"got {sorted(value)}"
        )
    return value


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise AtlasProvenanceNormalizationError(
            f"{label} must be an array of objects"
        )
    return value


def _with_id(
    prefix: str,
    field: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    row = copy.deepcopy(value)
    row[field] = content_id(prefix, row, field)
    return row


def _require_content_id(
    row: dict[str, Any],
    field: str,
    prefix: str,
    label: str,
) -> None:
    if row.get(field) != content_id(prefix, row, field):
        raise AtlasProvenanceNormalizationError(f"{label} content identity differs")


def _scope_key(scope: dict[str, Any]) -> tuple[str, str, str]:
    return (
        scope["snapshot_id"],
        scope["profile"],
        scope["physical_side"],
    )


def _validate_scope(
    scope: Any,
    snapshot_id: str,
    label: str,
) -> dict[str, Any]:
    row = _exact_fields(
        scope,
        {"snapshot_id", "profile", "physical_side"},
        label,
    )
    valid_pairs = {
        ("COMMON_FINAL_STATE", "CLIENT"),
        ("COMMON_FINAL_STATE", "DEDICATED_SERVER"),
        ("CLIENT_JEI_FINAL_STATE", "CLIENT"),
        ("OFFLINE_ARTIFACT_STATE", "OFFLINE"),
    }
    if (
        row["snapshot_id"] != snapshot_id
        or (row["profile"], row["physical_side"]) not in valid_pairs
    ):
        raise AtlasProvenanceNormalizationError(f"{label} is invalid")
    return row


def _node(
    node_class: str,
    scope: dict[str, Any],
    identity: dict[str, Any],
    evidence_ids: Iterable[str],
) -> dict[str, Any]:
    row = {
        "node_id": "",
        "node_class": node_class,
        "scope": copy.deepcopy(scope),
        "identity": copy.deepcopy(identity),
        "evidence_ids": sorted(set(evidence_ids)),
    }
    row["node_id"] = content_id(NODE_PREFIX, row, "node_id")
    return row


def _evidence(
    authority: str,
    record_kind: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "evidence_id": "",
        "authority": authority,
        "record_kind": record_kind,
        "record": copy.deepcopy(record),
        "record_sha256": canonical_sha256(record),
    }
    row["evidence_id"] = content_id(EVIDENCE_PREFIX, row, "evidence_id")
    return row


def _relation(
    predicate: str,
    subject_node_id: str,
    object_node_id: str,
    causal_strength: str,
    lifecycle_stage_id: str | None,
    evidence_ids: Iterable[str],
) -> dict[str, Any]:
    row = {
        "relation_id": "",
        "predicate": predicate,
        "subject_node_id": subject_node_id,
        "object_node_id": object_node_id,
        "causal_strength": causal_strength,
        "lifecycle_stage_id": lifecycle_stage_id,
        "evidence_ids": sorted(set(evidence_ids)),
    }
    row["relation_id"] = content_id(RELATION_PREFIX, row, "relation_id")
    return row


def _source_evidence(
    source_index_id: str,
    source_record: dict[str, Any],
) -> dict[str, Any]:
    return _evidence(
        "pinned-source",
        "source-span",
        {
            "source_index_id": source_index_id,
            "primitive_source_span_id": source_record["source_span_id"],
            "source_span": source_record["identity"],
        },
    )


def _validate_primitive_ids(
    source_record: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    operation: dict[str, Any] | None = None,
    boundary: dict[str, Any] | None = None,
) -> None:
    if source_record is not None:
        expected = SOURCE_SPAN_PREFIX + canonical_sha256(source_record["identity"])
        if source_record.get("source_span_id") != expected:
            raise AtlasProvenanceNormalizationError(
                "source-span primitive content identity differs"
            )
    if selection is not None:
        _require_content_id(
            selection,
            "configuration_selection_id",
            CONFIG_PREFIX,
            "configuration selection",
        )
        if canonical_sha256(selection["selected_value"]) != selection[
            "selected_value_sha256"
        ]:
            raise AtlasProvenanceNormalizationError(
                "configuration selected-value digest differs"
            )
    if operation is not None:
        operation_payload = {
            key: value
            for key, value in operation.items()
            if key not in {"operation_id", "operation_sha256"}
        }
        if operation.get("operation_sha256") != canonical_sha256(
            operation_payload
        ):
            raise AtlasProvenanceNormalizationError(
                "pack operation digest differs"
            )
        _require_content_id(
            operation,
            "operation_id",
            OPERATION_PREFIX,
            "pack operation",
        )
    if boundary is not None:
        _require_content_id(
            boundary,
            "boundary_id",
            BOUNDARY_PREFIX,
            "pack unresolved boundary",
        )


def validate_runtime_bundle(
    bundle: dict[str, Any],
    *,
    snapshot_id: str,
    source_lock_id: str,
) -> dict[str, Any]:
    """Validate the content-addressed accepted runtime-evidence handoff."""

    row = _exact_fields(
        bundle,
        {
            "schema_version",
            "format",
            "bundle_id",
            "snapshot_id",
            "source_lock_id",
            "scopes",
            "nodes",
            "relations",
            "evidence",
            "operation_bindings",
            "configuration_bindings",
        },
        "runtime evidence bundle",
    )
    if (
        row["schema_version"] != 1
        or row["format"] != RUNTIME_BUNDLE_FORMAT
        or row["snapshot_id"] != snapshot_id
        or row["source_lock_id"] != source_lock_id
    ):
        raise AtlasProvenanceNormalizationError(
            "runtime evidence bundle binding differs"
        )
    _require_content_id(
        row,
        "bundle_id",
        RUNTIME_BUNDLE_PREFIX,
        "runtime evidence bundle",
    )
    scopes = [
        _validate_scope(item, snapshot_id, "runtime bundle scope")
        for item in _objects(row["scopes"], "runtime bundle scopes")
    ]
    if not scopes or scopes != sorted(scopes, key=_scope_key):
        raise AtlasProvenanceNormalizationError(
            "runtime bundle scopes must be nonempty and canonical"
        )
    if len({_scope_key(item) for item in scopes}) != len(scopes):
        raise AtlasProvenanceNormalizationError(
            "runtime bundle scopes are not unique"
        )
    for field, id_field in (
        ("nodes", "node_id"),
        ("relations", "relation_id"),
        ("evidence", "evidence_id"),
    ):
        values = _objects(row[field], f"runtime bundle {field}")
        identifiers = [item.get(id_field) for item in values]
        if identifiers != sorted(identifiers) or len(set(identifiers)) != len(values):
            raise AtlasProvenanceNormalizationError(
                f"runtime bundle {field} are not unique and ID-sorted"
            )
    operation_bindings = _objects(
        row["operation_bindings"], "runtime operation bindings"
    )
    for item in operation_bindings:
        _exact_fields(
            item,
            {"operation_id", "node_id"},
            "runtime operation binding",
        )
    if operation_bindings != sorted(
        operation_bindings, key=lambda item: (item["operation_id"], item["node_id"])
    ):
        raise AtlasProvenanceNormalizationError(
            "runtime operation bindings are not canonical"
        )
    configuration_bindings = _objects(
        row["configuration_bindings"], "runtime configuration bindings"
    )
    for item in configuration_bindings:
        _exact_fields(
            item,
            {"configuration_selection_id", "relation_id"},
            "runtime configuration binding",
        )
    if configuration_bindings != sorted(
        configuration_bindings,
        key=lambda item: (
            item["configuration_selection_id"],
            item["relation_id"],
        ),
    ):
        raise AtlasProvenanceNormalizationError(
            "runtime configuration bindings are not canonical"
        )
    return row


def empty_runtime_bundle(
    *,
    snapshot_id: str,
    source_lock_id: str,
    scopes: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Create a canonical static-only bundle for honest frontier production."""

    row = {
        "schema_version": 1,
        "format": RUNTIME_BUNDLE_FORMAT,
        "bundle_id": "",
        "snapshot_id": snapshot_id,
        "source_lock_id": source_lock_id,
        "scopes": sorted((copy.deepcopy(item) for item in scopes), key=_scope_key),
        "nodes": [],
        "relations": [],
        "evidence": [],
        "operation_bindings": [],
        "configuration_bindings": [],
    }
    row["bundle_id"] = content_id(
        RUNTIME_BUNDLE_PREFIX, row, "bundle_id"
    )
    return row


def _validate_node(
    row: dict[str, Any],
    *,
    evidence: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    snapshot_id: str,
) -> None:
    _exact_fields(
        row,
        {"node_id", "node_class", "scope", "identity", "evidence_ids"},
        "normalized provenance node",
    )
    _validate_scope(row["scope"], snapshot_id, "normalized node scope")
    if not isinstance(row["evidence_ids"], list) or not row["evidence_ids"]:
        raise AtlasProvenanceNormalizationError(
            "normalized node evidence must be nonempty"
        )
    if row["evidence_ids"] != sorted(set(row["evidence_ids"])):
        raise AtlasProvenanceNormalizationError(
            "normalized node evidence IDs are not canonical"
        )
    if not set(row["evidence_ids"]).issubset(evidence):
        raise AtlasProvenanceNormalizationError(
            "normalized node evidence is unresolved"
        )
    try:
        _validate_node_identity(row["node_class"], row["identity"], policy)
    except ProvenanceContractError as exc:
        raise AtlasProvenanceNormalizationError(str(exc)) from exc
    _require_content_id(row, "node_id", NODE_PREFIX, "normalized node")
    node_evidence = [evidence[item] for item in row["evidence_ids"]]
    identity_digest = canonical_sha256(row["identity"])
    scope = row["scope"]

    def evidence_scope_matches(item: dict[str, Any]) -> bool:
        record = item["record"]
        return (
            record.get("snapshot_id") == scope["snapshot_id"]
            and record.get("profile") == scope["profile"]
            and record.get("physical_side") == scope["physical_side"]
        )

    if row["node_class"] == "source-span":
        if not any(
            item["authority"] == "pinned-source"
            and item["record_kind"] == "source-span"
            and item["record"].get("source_span") == row["identity"]
            for item in node_evidence
        ):
            raise AtlasProvenanceNormalizationError(
                "source-span node lacks exact pinned-source evidence"
            )
    elif row["node_class"] == "script-operation":
        if not any(
            item["authority"] == "runtime-mechanics"
            and item["record_kind"] == "stage-execution"
            and item["record"].get("executed_node_class") == "script-operation"
            and item["record"].get("executed_identity_sha256") == identity_digest
            and item["record"].get("stage_execution_id")
            == row["identity"]["stage_execution_id"]
            and evidence_scope_matches(item)
            for item in node_evidence
        ):
            raise AtlasProvenanceNormalizationError(
                "script-operation node lacks exact execution evidence"
            )
    elif row["node_class"] == "lifecycle-event":
        if not any(
            item["authority"] == "runtime-mechanics"
            and item["record_kind"] == "stage-execution"
            and item["record"].get("executed_node_class") == "lifecycle-event"
            and item["record"].get("executed_identity_sha256") == identity_digest
            and item["record"].get("stage_id") == row["identity"]["stage_id"]
            and item["record"].get("occurrence_id")
            == row["identity"]["occurrence_id"]
            and evidence_scope_matches(item)
            for item in node_evidence
        ):
            raise AtlasProvenanceNormalizationError(
                "lifecycle-event node lacks exact occurrence evidence"
            )
    elif row["node_class"] in {"runtime-state", "final-runtime-record"}:
        capture_id = row["identity"]["capture_evidence_id"]
        capture = evidence.get(capture_id)
        digest_key = (
            "state_sha256"
            if row["node_class"] == "runtime-state"
            else "runtime_record_sha256"
        )
        if (
            capture_id not in row["evidence_ids"]
            or capture is None
            or capture["authority"] != "runtime-mechanics"
            or capture["record_kind"] != "final-runtime-record"
            or capture["record"].get("runtime_node_id")
            != row["identity"]["runtime_node_id"]
            or capture["record"].get(digest_key) != row["identity"][digest_key]
            or not evidence_scope_matches(capture)
        ):
            raise AtlasProvenanceNormalizationError(
                "runtime node lacks its exact capture evidence"
            )


def _validate_relation(
    row: dict[str, Any],
    *,
    nodes: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> None:
    _exact_fields(
        row,
        {
            "relation_id",
            "predicate",
            "subject_node_id",
            "object_node_id",
            "causal_strength",
            "lifecycle_stage_id",
            "evidence_ids",
        },
        "normalized provenance relation",
    )
    subject = nodes.get(row["subject_node_id"])
    target = nodes.get(row["object_node_id"])
    if subject is None or target is None:
        raise AtlasProvenanceNormalizationError(
            "normalized relation endpoint is unresolved"
        )
    relation_policy = {
        item["predicate"]: item for item in policy["relation_policies"]
    }.get(row["predicate"])
    if relation_policy is None:
        raise AtlasProvenanceNormalizationError(
            "normalized relation predicate is unknown"
        )
    if (
        subject["node_class"] not in relation_policy["subject_classes"]
        or target["node_class"] not in relation_policy["object_classes"]
        or row["causal_strength"] not in relation_policy["permitted_strengths"]
    ):
        raise AtlasProvenanceNormalizationError(
            "normalized relation ontology is invalid"
        )
    lifecycle_requirement = relation_policy["lifecycle_requirement"]
    stage_id = row["lifecycle_stage_id"]
    if (
        (lifecycle_requirement == "required" and stage_id is None)
        or (lifecycle_requirement == "forbidden" and stage_id is not None)
    ):
        raise AtlasProvenanceNormalizationError(
            "normalized relation lifecycle binding is invalid"
        )
    stage = next(
        (
            item
            for item in policy["lifecycle_model"]["stages"]
            if item["stage_id"] == stage_id
        ),
        None,
    )
    if stage_id is not None and (
        stage is None
        or subject["scope"]["profile"] not in stage["applicable_profiles"]
        or target["scope"]["profile"] not in stage["applicable_profiles"]
    ):
        raise AtlasProvenanceNormalizationError(
            "normalized relation stage does not apply to its endpoints"
        )
    subject_scope = _scope_key(subject["scope"])
    target_scope = _scope_key(target["scope"])
    if row["predicate"] != "reconciles_to" and subject_scope != target_scope:
        raise AtlasProvenanceNormalizationError(
            "normalized relation crosses scope without reconciliation"
        )
    evidence_ids = row["evidence_ids"]
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or evidence_ids != sorted(set(evidence_ids))
        or not set(evidence_ids).issubset(evidence)
    ):
        raise AtlasProvenanceNormalizationError(
            "normalized relation evidence is invalid"
        )
    relation_evidence = [evidence[item] for item in evidence_ids]
    required = set(
        relation_policy["evidence_requirements"][row["causal_strength"]]
    )
    if not required.issubset(
        {item["record_kind"] for item in relation_evidence}
    ):
        raise AtlasProvenanceNormalizationError(
            "normalized relation lacks strength-specific evidence"
        )
    endpoint_digests = {
        canonical_sha256(subject["identity"]),
        canonical_sha256(target["identity"]),
    }
    relation_scope = subject["scope"]

    def evidence_scope_matches(item: dict[str, Any]) -> bool:
        record = item["record"]
        return (
            record.get("snapshot_id") == relation_scope["snapshot_id"]
            and record.get("profile") == relation_scope["profile"]
            and record.get("physical_side")
            == relation_scope["physical_side"]
        )

    if "stage-execution" in required and not any(
        item["record_kind"] == "stage-execution"
        and item["record"].get("stage_id") == stage_id
        and isinstance(item["record"].get("stage_execution_id"), str)
        and bool(item["record"]["stage_execution_id"])
        and isinstance(
            item["record"].get("related_identity_sha256s"), list
        )
        and item["record"].get("related_identity_sha256s")
        == sorted(set(item["record"].get("related_identity_sha256s", [])))
        and endpoint_digests.issubset(
            set(item["record"].get("related_identity_sha256s", []))
        )
        and evidence_scope_matches(item)
        and (
            (
                subject["node_class"] in {"script-operation", "lifecycle-event"}
                and item["record"].get("executed_node_class")
                == subject["node_class"]
                and item["record"].get("executed_identity_sha256")
                == canonical_sha256(subject["identity"])
            )
            or (
                target["node_class"] in {"script-operation", "lifecycle-event"}
                and item["record"].get("executed_node_class")
                == target["node_class"]
                and item["record"].get("executed_identity_sha256")
                == canonical_sha256(target["identity"])
            )
            or (
                subject["node_class"]
                not in {"script-operation", "lifecycle-event"}
                and target["node_class"]
                not in {"script-operation", "lifecycle-event"}
            )
        )
        for item in relation_evidence
    ):
        raise AtlasProvenanceNormalizationError(
            "stage evidence does not bind relation scope, stage, and endpoints"
        )
    if "state-transition" in required and not any(
        item["record_kind"] == "state-transition"
        and item["record"].get("predicate") == row["predicate"]
        and item["record"].get("subject_node_id") == row["subject_node_id"]
        and item["record"].get("object_node_id") == row["object_node_id"]
        and item["record"].get("stage_id") == stage_id
        and evidence_scope_matches(item)
        for item in relation_evidence
    ):
        raise AtlasProvenanceNormalizationError(
            "transition evidence does not bind relation endpoints"
        )
    if "configuration-selection" in required and not any(
        item["record_kind"] == "configuration-selection"
        and item["authority"] == "runtime-mechanics"
        and item["record"].get("configuration_node_id")
        == row["subject_node_id"]
        and item["record"].get("stage_id") == stage_id
        and evidence_scope_matches(item)
        for item in relation_evidence
    ):
        raise AtlasProvenanceNormalizationError(
            "configuration relation lacks occurrence-specific selection evidence"
        )
    if "identity-reconciliation" in required and not any(
        item["record_kind"] == "identity-reconciliation"
        and item["record"].get("subject_node_id") == row["subject_node_id"]
        and item["record"].get("object_node_id") == row["object_node_id"]
        for item in relation_evidence
    ):
        raise AtlasProvenanceNormalizationError(
            "reconciliation evidence does not bind endpoints"
        )
    if "final-runtime-record" in required and not any(
        item["record_kind"] == "final-runtime-record"
        and item["record"].get("runtime_node_id")
        == target["identity"].get("runtime_node_id")
        and item["record"].get("runtime_record_sha256")
        == target["identity"].get("runtime_record_sha256")
        and (
            item["record"].get("snapshot_id")
            == target["scope"]["snapshot_id"]
            and item["record"].get("profile") == target["scope"]["profile"]
            and item["record"].get("physical_side")
            == target["scope"]["physical_side"]
        )
        for item in relation_evidence
    ):
        raise AtlasProvenanceNormalizationError(
            "final observation evidence does not bind its runtime record"
        )
    _require_content_id(
        row, "relation_id", RELATION_PREFIX, "normalized relation"
    )


def _operation_frontier_reason(operation: dict[str, Any]) -> str:
    target_state = operation["target"]["state"]
    if target_state == "unresolved":
        return "dynamic-script-unresolved"
    if target_state == "symbolic":
        return "identity-reconciliation-unresolved"
    return "runtime-transition-unobserved"


def _configuration_stage(selection: dict[str, Any]) -> str | None:
    key_path = selection["key_path"]
    if key_path == "/loaders/preInit" or key_path.startswith(
        "/loaders/preInit/"
    ):
        return "groovy-pre-init"
    if key_path == "/loaders/postInit" or key_path.startswith(
        "/loaders/postInit/"
    ):
        return "groovy-post-init"
    return None


def _frontier(
    *,
    scope: dict[str, Any],
    primitive_kind: str,
    primitive_id: str,
    anchor_node_id: str,
    operation_candidate_id: str | None,
    reason_code: str,
    upstream_boundary_id: str | None,
) -> dict[str, Any]:
    row = {
        "frontier_id": "",
        "scope": copy.deepcopy(scope),
        "primitive_kind": primitive_kind,
        "primitive_id": primitive_id,
        "anchor_node_id": anchor_node_id,
        "operation_candidate_id": operation_candidate_id,
        "reason_code": reason_code,
        "upstream_boundary_id": upstream_boundary_id,
    }
    row["frontier_id"] = content_id(FRONTIER_PREFIX, row, "frontier_id")
    return row


def _deduplicate(
    rows: Iterable[dict[str, Any]],
    field: str,
    label: str,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row[field]
        prior = by_id.get(identifier)
        if prior is not None and prior != row:
            raise AtlasProvenanceNormalizationError(
                f"{label} content ID collision: {identifier}"
            )
        by_id[identifier] = row
    return [by_id[item] for item in sorted(by_id)]


def _normalize_validated(
    source_index: dict[str, Any],
    extraction: dict[str, Any],
    runtime_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Build N01 output after the two upstream primitive documents validate."""

    policy = load_policy()
    validate_runtime_bundle(
        runtime_bundle,
        snapshot_id=source_index["snapshot_id"],
        source_lock_id=source_index["source_lock_id"],
    )
    scopes = runtime_bundle["scopes"]
    source_by_id = {
        row["source_span_id"]: row for row in source_index["records"]
    }
    operation_by_id = {
        row["operation_id"]: row for row in extraction["operations"]
    }
    selection_by_id = {
        row["configuration_selection_id"]: row
        for row in extraction["configuration_selections"]
    }
    boundary_by_operation = {
        row["operation_id"]: row for row in extraction["unresolved_boundaries"]
    }
    referenced_source_ids = {
        row["source_span_id"] for row in extraction["operations"]
    } | {
        row["source_span_id"] for row in extraction["configuration_selections"]
    }
    if not referenced_source_ids.issubset(source_by_id):
        raise AtlasProvenanceNormalizationError(
            "pack extraction references a missing source span"
        )

    evidence_rows: list[dict[str, Any]] = [
        copy.deepcopy(row) for row in runtime_bundle["evidence"]
    ]
    source_evidence_by_primitive: dict[str, dict[str, Any]] = {}
    for source_id in sorted(referenced_source_ids):
        source_record = source_by_id[source_id]
        _validate_primitive_ids(source_record=source_record)
        source_evidence = _source_evidence(
            source_index["index_id"], source_record
        )
        source_evidence_by_primitive[source_id] = source_evidence
        evidence_rows.append(source_evidence)
    evidence_rows = _deduplicate(
        evidence_rows, "evidence_id", "normalization evidence"
    )
    evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
    for row in evidence_rows:
        try:
            _validate_evidence(row)
        except ProvenanceContractError as exc:
            raise AtlasProvenanceNormalizationError(str(exc)) from exc

    node_rows: list[dict[str, Any]] = [
        copy.deepcopy(row) for row in runtime_bundle["nodes"]
    ]
    source_mappings: list[dict[str, Any]] = []
    source_node_by_scope: dict[tuple[str, tuple[str, str, str]], dict[str, Any]] = {}
    for scope in scopes:
        for source_id in sorted(referenced_source_ids):
            source_record = source_by_id[source_id]
            evidence_id = source_evidence_by_primitive[source_id]["evidence_id"]
            node = _node(
                "source-span",
                scope,
                source_record["identity"],
                [evidence_id],
            )
            node_rows.append(node)
            source_node_by_scope[(source_id, _scope_key(scope))] = node
            source_mappings.append(
                {
                    "source_span_id": source_id,
                    "scope": copy.deepcopy(scope),
                    "node_id": node["node_id"],
                    "source_span": copy.deepcopy(source_record),
                }
            )

    configuration_mappings: list[dict[str, Any]] = []
    config_node_by_scope: dict[tuple[str, tuple[str, str, str]], dict[str, Any]] = {}
    for scope in scopes:
        for selection_id in sorted(selection_by_id):
            selection = selection_by_id[selection_id]
            _validate_primitive_ids(selection=selection)
            source_node = source_node_by_scope[
                (selection["source_span_id"], _scope_key(scope))
            ]
            node = _node(
                "configuration-entry",
                scope,
                {
                    "source_span_id": source_node["node_id"],
                    "key_path": selection["key_path"],
                    "selected_value_sha256": selection["selected_value_sha256"],
                },
                source_node["evidence_ids"],
            )
            node_rows.append(node)
            config_node_by_scope[(selection_id, _scope_key(scope))] = node
            configuration_mappings.append(
                {
                    "configuration_selection_id": selection_id,
                    "scope": copy.deepcopy(scope),
                    "node_id": node["node_id"],
                    "selection": copy.deepcopy(selection),
                }
            )

    node_rows = _deduplicate(node_rows, "node_id", "normalization nodes")
    nodes = {row["node_id"]: row for row in node_rows}
    for row in node_rows:
        _validate_node(
            row,
            evidence=evidence_by_id,
            policy=policy,
            snapshot_id=source_index["snapshot_id"],
        )
    for row in node_rows:
        try:
            _validate_node_identity(
                row["node_class"], row["identity"], policy, nodes
            )
        except ProvenanceContractError as exc:
            raise AtlasProvenanceNormalizationError(str(exc)) from exc

    operation_nodes: dict[
        tuple[str, tuple[str, str, str]], dict[str, Any]
    ] = {}
    generated_relations: list[dict[str, Any]] = []
    for binding in runtime_bundle["operation_bindings"]:
        operation = operation_by_id.get(binding["operation_id"])
        node = nodes.get(binding["node_id"])
        if operation is None or node is None or node["node_class"] != "script-operation":
            raise AtlasProvenanceNormalizationError(
                "operation binding does not resolve an exact primitive and node"
            )
        operation_scope_key = _scope_key(node["scope"])
        operation_key = (binding["operation_id"], operation_scope_key)
        if operation_key in operation_nodes:
            raise AtlasProvenanceNormalizationError(
                "operation primitive is bound more than once in one scope"
            )
        source_node = source_node_by_scope.get(
            (operation["source_span_id"], operation_scope_key)
        )
        identity = node["identity"]
        if (
            source_node is None
            or identity["source_span_id"] != source_node["node_id"]
            or identity["operation_index"] != operation["operation_index"]
            or identity["operation_sha256"] != operation["operation_sha256"]
        ):
            raise AtlasProvenanceNormalizationError(
                "operation binding differs from its exact P02 identity"
            )
        stage_evidence = [
            evidence_by_id[item]
            for item in node["evidence_ids"]
            if evidence_by_id[item]["record_kind"] == "stage-execution"
            and evidence_by_id[item]["record"].get("executed_identity_sha256")
            == canonical_sha256(identity)
        ]
        if not any(
            item["record"].get("operation_id") == operation["operation_id"]
            and item["record"].get("stage_id") == operation["lifecycle_stage_id"]
            and item["record"].get("snapshot_id") == node["scope"]["snapshot_id"]
            and item["record"].get("profile") == node["scope"]["profile"]
            and item["record"].get("physical_side")
            == node["scope"]["physical_side"]
            for item in stage_evidence
        ):
            raise AtlasProvenanceNormalizationError(
                "operation execution evidence does not bind primitive, stage, and scope"
            )
        usable_stage_evidence = next(
            item
            for item in stage_evidence
            if item["record"].get("operation_id") == operation["operation_id"]
            and item["record"].get("stage_id")
            == operation["lifecycle_stage_id"]
            and item["record"].get("snapshot_id")
            == node["scope"]["snapshot_id"]
            and item["record"].get("profile") == node["scope"]["profile"]
            and item["record"].get("physical_side")
            == node["scope"]["physical_side"]
        )
        generated_relations.append(
            _relation(
                "invokes",
                source_node["node_id"],
                node["node_id"],
                "causation",
                operation["lifecycle_stage_id"],
                [
                    source_evidence_by_primitive[
                        operation["source_span_id"]
                    ]["evidence_id"],
                    usable_stage_evidence["evidence_id"],
                ],
            )
        )
        operation_nodes[operation_key] = node

    relation_rows = [
        *generated_relations,
        *(copy.deepcopy(row) for row in runtime_bundle["relations"]),
    ]
    relation_rows = _deduplicate(
        relation_rows, "relation_id", "normalization relations"
    )
    relations = {row["relation_id"]: row for row in relation_rows}
    for row in relation_rows:
        if row["predicate"] == "derives_explanation":
            raise AtlasProvenanceNormalizationError(
                "N01 cannot normalize presentation relations"
            )
        _validate_relation(
            row,
            nodes=nodes,
            evidence=evidence_by_id,
            policy=policy,
        )

    configuration_relation_ids: dict[str, set[str]] = {}
    for binding in runtime_bundle["configuration_bindings"]:
        selection = selection_by_id.get(binding["configuration_selection_id"])
        relation = relations.get(binding["relation_id"])
        if selection is None or relation is None:
            raise AtlasProvenanceNormalizationError(
                "configuration binding does not resolve its primitive and relation"
            )
        subject = nodes[relation["subject_node_id"]]
        expected = config_node_by_scope.get(
            (binding["configuration_selection_id"], _scope_key(subject["scope"]))
        )
        if (
            relation["predicate"] != "loads_configuration"
            or expected is None
            or relation["subject_node_id"] != expected["node_id"]
            or _configuration_stage(selection) != relation["lifecycle_stage_id"]
            or not any(
                evidence_by_id[item]["record_kind"] == "configuration-selection"
                and evidence_by_id[item]["authority"] == "runtime-mechanics"
                and evidence_by_id[item]["record"].get(
                    "configuration_selection_id"
                )
                == binding["configuration_selection_id"]
                and evidence_by_id[item]["record"].get("selected_value_sha256")
                == selection["selected_value_sha256"]
                for item in relation["evidence_ids"]
            )
        ):
            raise AtlasProvenanceNormalizationError(
                "configuration binding lacks exact selected value and occurrence"
            )
        configuration_relation_ids.setdefault(
            binding["configuration_selection_id"], set()
        ).add(binding["relation_id"])

    transition_ids_by_operation: dict[
        tuple[str, tuple[str, str, str]], set[str]
    ] = {}
    for relation in relation_rows:
        if relation["predicate"] not in TRANSITION_PREDICATES:
            continue
        for evidence_id in relation["evidence_ids"]:
            item = evidence_by_id[evidence_id]
            if item["record_kind"] != "state-transition":
                continue
            operation_id = item["record"].get("operation_id")
            operation_node_id = item["record"].get("operation_node_id")
            if not isinstance(operation_id, str):
                continue
            operation = operation_by_id.get(operation_id)
            relation_scope_key = _scope_key(
                nodes[relation["subject_node_id"]]["scope"]
            )
            operation_key = (operation_id, relation_scope_key)
            operation_node = operation_nodes.get(operation_key)
            if (
                operation is None
                or operation_node is None
                or operation_node_id != operation_node["node_id"]
                or relation["predicate"]
                not in OPERATION_PREDICATES[operation["operation_kind"]]
                or relation["lifecycle_stage_id"]
                != operation["lifecycle_stage_id"]
                or not any(
                    evidence_by_id[stage_id]["record_kind"] == "stage-execution"
                    and evidence_by_id[stage_id]["record"].get("operation_id")
                    == operation_id
                    and evidence_by_id[stage_id]["record"].get(
                        "stage_execution_id"
                    )
                    == item["record"].get("stage_execution_id")
                    for stage_id in relation["evidence_ids"]
                )
            ):
                raise AtlasProvenanceNormalizationError(
                    "operation transition does not bind exact execution and endpoints"
                )
            transition_ids_by_operation.setdefault(operation_key, set()).add(
                relation["relation_id"]
            )

    frontiers: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for scope in scopes:
        scope_key = _scope_key(scope)
        for selection_id in sorted(selection_by_id):
            node = config_node_by_scope[(selection_id, scope_key)]
            bound_ids = sorted(configuration_relation_ids.get(selection_id, set()))
            scoped_ids = [
                item
                for item in bound_ids
                if _scope_key(nodes[relations[item]["subject_node_id"]]["scope"])
                == scope_key
            ]
            if not scoped_ids:
                frontiers.append(
                    _frontier(
                        scope=scope,
                        primitive_kind="configuration-selection",
                        primitive_id=selection_id,
                        anchor_node_id=node["node_id"],
                        operation_candidate_id=None,
                        reason_code="runtime-transition-unobserved",
                        upstream_boundary_id=None,
                    )
                )
        for operation_id in sorted(operation_by_id):
            operation = operation_by_id[operation_id]
            _validate_primitive_ids(operation=operation)
            source_node = source_node_by_scope[
                (operation["source_span_id"], scope_key)
            ]
            operation_key = (operation_id, scope_key)
            promoted = operation_nodes.get(operation_key)
            scoped_transition_ids = sorted(
                transition_ids_by_operation.get(operation_key, set())
            )
            candidate_payload = {
                "candidate_id": "",
                "scope": copy.deepcopy(scope),
                "source_span_node_id": source_node["node_id"],
                "operation": copy.deepcopy(operation),
                "execution_state": (
                    "executed-exact" if promoted is not None else "static-only"
                ),
                "promoted_node_id": (
                    promoted["node_id"] if promoted is not None else None
                ),
                "transition_relation_ids": scoped_transition_ids,
                "frontier_ids": [],
            }
            candidate_payload["candidate_id"] = content_id(
                CANDIDATE_PREFIX, candidate_payload, "candidate_id"
            )
            if not scoped_transition_ids:
                boundary = boundary_by_operation.get(operation_id)
                if boundary is not None:
                    _validate_primitive_ids(boundary=boundary)
                frontier = _frontier(
                    scope=scope,
                    primitive_kind="operation",
                    primitive_id=operation_id,
                    anchor_node_id=(
                        promoted["node_id"]
                        if promoted is not None
                        else source_node["node_id"]
                    ),
                    operation_candidate_id=candidate_payload["candidate_id"],
                    reason_code=_operation_frontier_reason(operation),
                    upstream_boundary_id=(
                        boundary["boundary_id"] if boundary is not None else None
                    ),
                )
                frontiers.append(frontier)
            candidates.append(candidate_payload)

    frontiers = _deduplicate(frontiers, "frontier_id", "normalization frontiers")
    candidates = _deduplicate(
        candidates, "candidate_id", "operation candidates"
    )

    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = {}
    for relation in relation_rows:
        forward.setdefault(relation["subject_node_id"], []).append(
            relation["relation_id"]
        )
        reverse.setdefault(relation["object_node_id"], []).append(
            relation["relation_id"]
        )
    indexes = {
        "forward": [
            {"node_id": node_id, "relation_ids": sorted(relation_ids)}
            for node_id, relation_ids in sorted(forward.items())
        ],
        "reverse": [
            {"node_id": node_id, "relation_ids": sorted(relation_ids)}
            for node_id, relation_ids in sorted(reverse.items())
        ],
    }
    used_evidence = {
        evidence_id
        for node in node_rows
        for evidence_id in node["evidence_ids"]
    } | {
        evidence_id
        for relation in relation_rows
        for evidence_id in relation["evidence_ids"]
    }
    if used_evidence != set(evidence_by_id):
        raise AtlasProvenanceNormalizationError(
            "runtime bundle contains unused or omitted evidence"
        )
    document = {
        "schema_version": 1,
        "format": FORMAT,
        "normalization_id": "",
        "snapshot_id": source_index["snapshot_id"],
        "source_lock_id": source_index["source_lock_id"],
        "source_index_id": source_index["index_id"],
        "mutation_extraction_id": extraction["extraction_id"],
        "runtime_bundle_id": runtime_bundle["bundle_id"],
        "policy_id": policy["policy_id"],
        "policy_sha256": POLICY_SHA256_V1,
        "scopes": copy.deepcopy(scopes),
        "source_mappings": sorted(
            source_mappings,
            key=lambda item: (item["source_span_id"], _scope_key(item["scope"])),
        ),
        "configuration_mappings": sorted(
            configuration_mappings,
            key=lambda item: (
                item["configuration_selection_id"],
                _scope_key(item["scope"]),
            ),
        ),
        "operation_candidates": candidates,
        "nodes": node_rows,
        "relations": relation_rows,
        "evidence": evidence_rows,
        "frontiers": frontiers,
        "indexes": indexes,
        "summary": {
            "scope_count": len(scopes),
            "source_mapping_count": len(source_mappings),
            "configuration_mapping_count": len(configuration_mappings),
            "operation_candidate_count": len(candidates),
            "executed_operation_count": sum(
                item["execution_state"] == "executed-exact"
                for item in candidates
            ),
            "node_count": len(node_rows),
            "relation_count": len(relation_rows),
            "evidence_count": len(evidence_rows),
            "frontier_count": len(frontiers),
        },
    }
    document["normalization_id"] = content_id(
        NORMALIZATION_PREFIX, document, "normalization_id"
    )
    validate_normalization(document, policy=policy)
    return document


def normalize(
    source_index: dict[str, Any],
    extraction: dict[str, Any],
    runtime_bundle: dict[str, Any],
    resolver: Any = None,
    *,
    pack_profile: str | None = None,
    primitive_adapter: ProvenancePrimitiveAdapter | None = None,
) -> dict[str, Any]:
    """Validate P01/P02 completely, then normalize their accepted records."""

    if primitive_adapter is not None and pack_profile is not None:
        raise AtlasProvenanceNormalizationError(
            "select either a pack profile or an injected primitive-validation adapter"
        )
    adapter = (
        _primitive_adapter(pack_profile)
        if primitive_adapter is None
        else _validate_primitive_adapter(primitive_adapter)
    )
    try:
        adapter.validate_static_primitives(source_index, extraction, resolver=resolver)
    except ValueError as exc:
        raise AtlasProvenanceNormalizationError(
            f"invalid static provenance primitives: {exc}"
        ) from exc
    return _normalize_validated(source_index, extraction, runtime_bundle)


def validate_normalization(
    document: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a normalized artifact without trusting its producer."""

    if policy is None:
        policy = load_policy()
    try:
        schema = read_json(SCHEMA_PATH)
        _validate_schema_definition(schema, SCHEMA_PATH.name)
        _validate_schema_value(document, schema, FORMAT)
    except (OSError, ValueError) as exc:
        raise AtlasProvenanceNormalizationError(
            f"normalization schema failed: {exc}"
        ) from exc
    _require_content_id(
        document,
        "normalization_id",
        NORMALIZATION_PREFIX,
        "normalization",
    )
    if (
        document["policy_id"] != policy["policy_id"]
        or document["policy_sha256"] != canonical_sha256(policy)
    ):
        raise AtlasProvenanceNormalizationError(
            "normalization provenance policy binding differs"
        )
    scopes = [
        _validate_scope(item, document["snapshot_id"], "normalization scope")
        for item in document["scopes"]
    ]
    if scopes != sorted(scopes, key=_scope_key) or len(
        {_scope_key(item) for item in scopes}
    ) != len(scopes):
        raise AtlasProvenanceNormalizationError(
            "normalization scopes are not unique and canonical"
        )
    admitted_scopes = {_scope_key(item) for item in scopes}
    evidence_rows = document["evidence"]
    if [row["evidence_id"] for row in evidence_rows] != sorted(
        row["evidence_id"] for row in evidence_rows
    ):
        raise AtlasProvenanceNormalizationError(
            "normalization evidence is not ID-sorted"
        )
    evidence = {row["evidence_id"]: row for row in evidence_rows}
    if len(evidence) != len(evidence_rows):
        raise AtlasProvenanceNormalizationError(
            "normalization evidence IDs are not unique"
        )
    for row in evidence_rows:
        try:
            _validate_evidence(row)
        except ProvenanceContractError as exc:
            raise AtlasProvenanceNormalizationError(str(exc)) from exc
    node_rows = document["nodes"]
    if [row["node_id"] for row in node_rows] != sorted(
        row["node_id"] for row in node_rows
    ):
        raise AtlasProvenanceNormalizationError(
            "normalization nodes are not ID-sorted"
        )
    nodes = {row["node_id"]: row for row in node_rows}
    if len(nodes) != len(node_rows):
        raise AtlasProvenanceNormalizationError(
            "normalization node IDs are not unique"
        )
    for row in node_rows:
        _validate_node(
            row,
            evidence=evidence,
            policy=policy,
            snapshot_id=document["snapshot_id"],
        )
    for row in node_rows:
        try:
            _validate_node_identity(
                row["node_class"], row["identity"], policy, nodes
            )
        except ProvenanceContractError as exc:
            raise AtlasProvenanceNormalizationError(str(exc)) from exc
    relation_rows = document["relations"]
    if [row["relation_id"] for row in relation_rows] != sorted(
        row["relation_id"] for row in relation_rows
    ):
        raise AtlasProvenanceNormalizationError(
            "normalization relations are not ID-sorted"
        )
    relations = {row["relation_id"]: row for row in relation_rows}
    if len(relations) != len(relation_rows):
        raise AtlasProvenanceNormalizationError(
            "normalization relation IDs are not unique"
        )
    for row in relation_rows:
        _validate_relation(
            row, nodes=nodes, evidence=evidence, policy=policy
        )
        if (
            row["predicate"] == "observed_as_final"
            and row["lifecycle_stage_id"] != "final-observation"
        ):
            raise AtlasProvenanceNormalizationError(
                "final observation relation uses the wrong lifecycle stage"
            )
    expected_forward: dict[str, list[str]] = {}
    expected_reverse: dict[str, list[str]] = {}
    for row in relation_rows:
        expected_forward.setdefault(row["subject_node_id"], []).append(
            row["relation_id"]
        )
        expected_reverse.setdefault(row["object_node_id"], []).append(
            row["relation_id"]
        )
    expected_indexes = {
        "forward": [
            {"node_id": node_id, "relation_ids": sorted(ids)}
            for node_id, ids in sorted(expected_forward.items())
        ],
        "reverse": [
            {"node_id": node_id, "relation_ids": sorted(ids)}
            for node_id, ids in sorted(expected_reverse.items())
        ],
    }
    if document["indexes"] != expected_indexes:
        raise AtlasProvenanceNormalizationError(
            "normalization forward/reverse indexes differ"
        )
    source_mappings = document["source_mappings"]
    expected_source_order = sorted(
        source_mappings,
        key=lambda item: (item["source_span_id"], _scope_key(item["scope"])),
    )
    if source_mappings != expected_source_order:
        raise AtlasProvenanceNormalizationError(
            "source mappings are not canonical"
        )
    for mapping in source_mappings:
        _validate_primitive_ids(source_record=mapping["source_span"])
        node = nodes.get(mapping["node_id"])
        if (
            _scope_key(mapping["scope"]) not in admitted_scopes
            or mapping["source_span"]["identity"]["source_lock_id"]
            != document["source_lock_id"]
            or mapping["source_span_id"]
            != mapping["source_span"]["source_span_id"]
            or node is None
            or node["node_class"] != "source-span"
            or node["identity"] != mapping["source_span"]["identity"]
            or node["scope"] != mapping["scope"]
            or node["node_id"] == mapping["source_span_id"]
        ):
            raise AtlasProvenanceNormalizationError(
                "source primitive mapping is not exact and scoped"
            )
        source_evidence = [
            evidence[item] for item in node["evidence_ids"]
        ]
        if not any(
            item["record_kind"] == "source-span"
            and item["record"].get("source_index_id")
            == document["source_index_id"]
            and item["record"].get("primitive_source_span_id")
            == mapping["source_span_id"]
            for item in source_evidence
        ):
            raise AtlasProvenanceNormalizationError(
                "source mapping evidence does not bind the source index"
            )
    config_mappings = document["configuration_mappings"]
    if config_mappings != sorted(
        config_mappings,
        key=lambda item: (
            item["configuration_selection_id"],
            _scope_key(item["scope"]),
        ),
    ):
        raise AtlasProvenanceNormalizationError(
            "configuration mappings are not canonical"
        )
    source_nodes_by_primitive_scope = {
        (item["source_span_id"], _scope_key(item["scope"])): item["node_id"]
        for item in source_mappings
    }
    for mapping in config_mappings:
        _validate_primitive_ids(selection=mapping["selection"])
        node = nodes.get(mapping["node_id"])
        selection = mapping["selection"]
        expected_source_node = source_nodes_by_primitive_scope.get(
            (selection["source_span_id"], _scope_key(mapping["scope"]))
        )
        if (
            _scope_key(mapping["scope"]) not in admitted_scopes
            or mapping["configuration_selection_id"]
            != selection["configuration_selection_id"]
            or node is None
            or node["node_class"] != "configuration-entry"
            or node["scope"] != mapping["scope"]
            or node["identity"]
            != {
                "source_span_id": expected_source_node,
                "key_path": selection["key_path"],
                "selected_value_sha256": selection["selected_value_sha256"],
            }
        ):
            raise AtlasProvenanceNormalizationError(
                "configuration primitive mapping differs"
            )
    config_mapping_by_node = {
        item["node_id"]: item for item in config_mappings
    }
    candidates = document["operation_candidates"]
    if [row["candidate_id"] for row in candidates] != sorted(
        row["candidate_id"] for row in candidates
    ):
        raise AtlasProvenanceNormalizationError(
            "operation candidates are not ID-sorted"
        )
    candidate_keys = [
        (row["operation"]["operation_id"], _scope_key(row["scope"]))
        for row in candidates
    ]
    if len(set(candidate_keys)) != len(candidate_keys):
        raise AtlasProvenanceNormalizationError(
            "operation primitive is normalized more than once in one scope"
        )
    for candidate in candidates:
        _require_content_id(
            candidate,
            "candidate_id",
            CANDIDATE_PREFIX,
            "operation candidate",
        )
        _validate_primitive_ids(operation=candidate["operation"])
        source_key = (
            candidate["operation"]["source_span_id"],
            _scope_key(candidate["scope"]),
        )
        if (
            _scope_key(candidate["scope"]) not in admitted_scopes
            or candidate["source_span_node_id"]
            != source_nodes_by_primitive_scope.get(source_key)
        ):
            raise AtlasProvenanceNormalizationError(
                "operation candidate source mapping differs"
            )
        promoted = candidate["promoted_node_id"]
        if (promoted is None) != (candidate["execution_state"] == "static-only"):
            raise AtlasProvenanceNormalizationError(
                "operation candidate execution state differs"
            )
        if promoted is not None:
            node = nodes.get(promoted)
            if (
                node is None
                or node["node_class"] != "script-operation"
                or node["scope"] != candidate["scope"]
                or node["identity"]["source_span_id"]
                != candidate["source_span_node_id"]
                or node["identity"]["operation_index"]
                != candidate["operation"]["operation_index"]
                or node["identity"]["operation_sha256"]
                != candidate["operation"]["operation_sha256"]
            ):
                raise AtlasProvenanceNormalizationError(
                    "promoted operation node differs from P02"
                )
            operation_stage_evidence = [
                evidence[item]
                for item in node["evidence_ids"]
                if evidence[item]["record_kind"] == "stage-execution"
            ]
            if not any(
                item["record"].get("operation_id")
                == candidate["operation"]["operation_id"]
                and item["record"].get("stage_id")
                == candidate["operation"]["lifecycle_stage_id"]
                and item["record"].get("snapshot_id")
                == candidate["scope"]["snapshot_id"]
                and item["record"].get("profile")
                == candidate["scope"]["profile"]
                and item["record"].get("physical_side")
                == candidate["scope"]["physical_side"]
                for item in operation_stage_evidence
            ):
                raise AtlasProvenanceNormalizationError(
                    "promoted operation lacks primitive-bound execution evidence"
                )
            if not any(
                relation["predicate"] == "invokes"
                and relation["subject_node_id"]
                == candidate["source_span_node_id"]
                and relation["object_node_id"] == promoted
                and relation["causal_strength"] == "causation"
                and relation["lifecycle_stage_id"]
                == candidate["operation"]["lifecycle_stage_id"]
                for relation in relation_rows
            ):
                raise AtlasProvenanceNormalizationError(
                    "promoted operation lacks its exact source-to-execution edge"
                )
        expected_transition_ids = sorted(
            relation["relation_id"]
            for relation in relation_rows
            if relation["predicate"] in TRANSITION_PREDICATES
            and _scope_key(nodes[relation["subject_node_id"]]["scope"])
            == _scope_key(candidate["scope"])
            and any(
                evidence[item]["record_kind"] == "state-transition"
                and evidence[item]["record"].get("operation_id")
                == candidate["operation"]["operation_id"]
                for item in relation["evidence_ids"]
            )
        )
        if (
            candidate["transition_relation_ids"]
            != sorted(set(candidate["transition_relation_ids"]))
            or candidate["transition_relation_ids"] != expected_transition_ids
            or not set(candidate["transition_relation_ids"]).issubset(relations)
        ):
            raise AtlasProvenanceNormalizationError(
                "operation candidate transition is unresolved"
            )
        if promoted is None and candidate["transition_relation_ids"]:
            raise AtlasProvenanceNormalizationError(
                "static operation candidate cannot carry a runtime transition"
            )
        for relation_id in candidate["transition_relation_ids"]:
            relation = relations[relation_id]
            operation_row = candidate["operation"]
            if (
                relation["predicate"]
                not in OPERATION_PREDICATES[operation_row["operation_kind"]]
                or relation["lifecycle_stage_id"]
                != operation_row["lifecycle_stage_id"]
                or not any(
                    evidence[item]["record_kind"] == "state-transition"
                    and evidence[item]["record"].get("operation_id")
                    == operation_row["operation_id"]
                    and evidence[item]["record"].get("operation_node_id")
                    == promoted
                    for item in relation["evidence_ids"]
                )
            ):
                raise AtlasProvenanceNormalizationError(
                    "operation candidate transition lacks exact operation evidence"
                )
        if candidate["frontier_ids"]:
            raise AtlasProvenanceNormalizationError(
                "operation candidates cannot embed circular frontier identities"
            )
    frontiers = document["frontiers"]
    if [row["frontier_id"] for row in frontiers] != sorted(
        row["frontier_id"] for row in frontiers
    ):
        raise AtlasProvenanceNormalizationError(
            "normalization frontiers are not ID-sorted"
        )
    candidate_ids = {row["candidate_id"] for row in candidates}
    operation_frontiers: dict[str, list[dict[str, Any]]] = {}
    configuration_frontiers: dict[
        tuple[str, tuple[str, str, str]], list[dict[str, Any]]
    ] = {}
    for frontier in frontiers:
        _require_content_id(
            frontier, "frontier_id", FRONTIER_PREFIX, "normalization frontier"
        )
        if (
            frontier["reason_code"] not in FRONTIER_REASONS
            or frontier["anchor_node_id"] not in nodes
            or _scope_key(frontier["scope"]) not in admitted_scopes
            or nodes[frontier["anchor_node_id"]]["scope"]
            != frontier["scope"]
            or (
                frontier["primitive_kind"] == "operation"
                and frontier["operation_candidate_id"] not in candidate_ids
            )
            or (
                frontier["primitive_kind"] == "configuration-selection"
                and frontier["operation_candidate_id"] is not None
            )
        ):
            raise AtlasProvenanceNormalizationError(
                "normalization frontier binding is invalid"
            )
        if frontier["primitive_kind"] == "operation":
            operation_frontiers.setdefault(
                frontier["operation_candidate_id"], []
            ).append(frontier)
        else:
            configuration_frontiers.setdefault(
                (frontier["primitive_id"], _scope_key(frontier["scope"])),
                [],
            ).append(frontier)
    if not set(operation_frontiers).issubset(candidate_ids):
        raise AtlasProvenanceNormalizationError(
            "operation frontier has no normalized candidate"
        )
    configuration_keys = {
        (
            item["configuration_selection_id"],
            _scope_key(item["scope"]),
        )
        for item in config_mappings
    }
    if not set(configuration_frontiers).issubset(configuration_keys):
        raise AtlasProvenanceNormalizationError(
            "configuration frontier has no normalized selection"
        )
    for candidate in candidates:
        candidate_frontiers = operation_frontiers.get(
            candidate["candidate_id"], []
        )
        if candidate["transition_relation_ids"]:
            if candidate_frontiers:
                raise AtlasProvenanceNormalizationError(
                    "closed operation transition retains an open frontier"
                )
            continue
        if (
            len(candidate_frontiers) != 1
            or candidate_frontiers[0]["primitive_id"]
            != candidate["operation"]["operation_id"]
            or candidate_frontiers[0]["anchor_node_id"]
            != (
                candidate["promoted_node_id"]
                or candidate["source_span_node_id"]
            )
            or candidate_frontiers[0]["reason_code"]
            != _operation_frontier_reason(candidate["operation"])
        ):
            raise AtlasProvenanceNormalizationError(
                "open operation candidate lacks its exact frontier"
            )
        boundary_id = candidate_frontiers[0]["upstream_boundary_id"]
        if (
            candidate["operation"]["target"]["state"] == "unresolved"
            and (
                not isinstance(boundary_id, str)
                or not boundary_id.startswith(BOUNDARY_PREFIX)
            )
        ) or (
            candidate["operation"]["target"]["state"] != "unresolved"
            and boundary_id is not None
        ):
            raise AtlasProvenanceNormalizationError(
                "operation frontier upstream boundary differs"
            )
    for mapping in config_mappings:
        selection = mapping["selection"]
        key = (
            mapping["configuration_selection_id"],
            _scope_key(mapping["scope"]),
        )
        bound_relations = [
            relation
            for relation in relation_rows
            if relation["predicate"] == "loads_configuration"
            and relation["subject_node_id"] == mapping["node_id"]
            and relation["lifecycle_stage_id"]
            == _configuration_stage(selection)
            and any(
                evidence[item]["record_kind"] == "configuration-selection"
                and evidence[item]["authority"] == "runtime-mechanics"
                and evidence[item]["record"].get(
                    "configuration_selection_id"
                )
                == mapping["configuration_selection_id"]
                and evidence[item]["record"].get("selected_value_sha256")
                == selection["selected_value_sha256"]
                for item in relation["evidence_ids"]
            )
        ]
        mapping_frontiers = configuration_frontiers.get(key, [])
        if bound_relations and mapping_frontiers:
            raise AtlasProvenanceNormalizationError(
                "bound configuration retains an open frontier"
            )
        if not bound_relations and (
            len(mapping_frontiers) != 1
            or mapping_frontiers[0]["anchor_node_id"] != mapping["node_id"]
            or mapping_frontiers[0]["reason_code"]
            != "runtime-transition-unobserved"
        ):
            raise AtlasProvenanceNormalizationError(
                "unbound configuration lacks its exact frontier"
            )
    if len(config_mapping_by_node) != len(config_mappings):
        raise AtlasProvenanceNormalizationError(
            "configuration node mappings are not unique"
        )
    used_evidence = {
        item for node in node_rows for item in node["evidence_ids"]
    } | {
        item for relation in relation_rows for item in relation["evidence_ids"]
    }
    if used_evidence != set(evidence):
        raise AtlasProvenanceNormalizationError(
            "normalization evidence use is not exact"
        )
    expected_summary = {
        "scope_count": len(scopes),
        "source_mapping_count": len(source_mappings),
        "configuration_mapping_count": len(config_mappings),
        "operation_candidate_count": len(candidates),
        "executed_operation_count": sum(
            row["execution_state"] == "executed-exact" for row in candidates
        ),
        "node_count": len(node_rows),
        "relation_count": len(relation_rows),
        "evidence_count": len(evidence_rows),
        "frontier_count": len(frontiers),
    }
    if document["summary"] != expected_summary:
        raise AtlasProvenanceNormalizationError(
            "normalization summary differs"
        )
    return document


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="validate one N01 artifact")
    check.add_argument("artifact", type=Path)
    static = subparsers.add_parser(
        "static",
        help="normalize complete P01/P02 inputs with no runtime causal claims",
    )
    static.add_argument("--source-index", type=Path)
    static.add_argument("--mutations", type=Path)
    static.add_argument("--source-root", type=Path)
    static.add_argument("--pack-root", type=Path)
    static.add_argument("--output", type=Path, required=True)
    static.add_argument(
        "--pack-profile", required=True,
        help="explicit installed profile owning source and mutation validation",
    )
    static.add_argument(
        "--profile",
        required=True,
        choices=[
            "COMMON_FINAL_STATE",
            "CLIENT_JEI_FINAL_STATE",
            "OFFLINE_ARTIFACT_STATE",
        ],
    )
    static.add_argument(
        "--physical-side",
        required=True,
        choices=["CLIENT", "DEDICATED_SERVER", "OFFLINE"],
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "check":
        validate_normalization(read_json(arguments.artifact))
        print(f"valid Atlas provenance normalization: {arguments.artifact}")
        return 0
    adapter = _primitive_adapter(arguments.pack_profile)
    resolver = _selected_source_resolver(
        adapter,
        source_root=arguments.source_root,
        pack_root=arguments.pack_root,
    )
    source_index = read_json(
        arguments.source_index or default_static_input_path("atlas-source-spans.json")
    )
    extraction = read_json(
        arguments.mutations or default_static_input_path("atlas-pack-mutations.json")
    )
    scope = {
        "snapshot_id": source_index["snapshot_id"],
        "profile": arguments.profile,
        "physical_side": arguments.physical_side,
    }
    bundle = empty_runtime_bundle(
        snapshot_id=source_index["snapshot_id"],
        source_lock_id=source_index["source_lock_id"],
        scopes=[scope],
    )
    document = normalize(
        source_index, extraction, bundle,
        resolver=resolver, primitive_adapter=adapter,
    )
    _write(arguments.output, document)
    print(
        f"wrote {document['normalization_id']} with "
        f"{document['summary']['frontier_count']} honest frontiers"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AtlasProvenanceNormalizationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
