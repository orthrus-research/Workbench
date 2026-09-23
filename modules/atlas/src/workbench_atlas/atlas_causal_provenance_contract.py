#!/usr/bin/env python3

"""Fail-closed contract validation for Atlas causal provenance v1."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from workbench_atlas.layout import DATA_ROOT, EXAMPLE_ROOT, SCHEMA_ROOT


CORPUS_ROOT = DATA_ROOT
POLICY_PATH = DATA_ROOT / "atlas-provenance-policy-v1.json"
EXAMPLES_PATH = EXAMPLE_ROOT / "atlas-provenance-contract-examples-v1.json"
REQUEST_SCHEMA_PATH = SCHEMA_ROOT / "atlas-provenance-request-v1.schema.json"
POLICY_SCHEMA_PATH = SCHEMA_ROOT / "atlas-provenance-policy-v1.schema.json"
RESULT_SCHEMA_PATH = SCHEMA_ROOT / "atlas-provenance-result-v1.schema.json"

REQUEST_PREFIX = "atlas-provenance-request:sha256:"
RESULT_PREFIX = "atlas-provenance-result:sha256:"
NODE_PREFIX = "atlas-provenance-node:sha256:"
RELATION_PREFIX = "atlas-provenance-relation:sha256:"
PATH_PREFIX = "atlas-provenance-path:sha256:"
EVIDENCE_PREFIX = "atlas-provenance-evidence:sha256:"
STATEMENT_PREFIX = "atlas-provenance-statement:sha256:"
POLICY_SHA256_V1 = (
    "fdeabbcfb352c333908a7befc53449e42d9ec6de976b7a53914286f56174df00"
)

PROFILES = {
    "COMMON_FINAL_STATE",
    "CLIENT_JEI_FINAL_STATE",
    "OFFLINE_ARTIFACT_STATE",
}
VALID_SCOPE_PAIRS = {
    ("COMMON_FINAL_STATE", "CLIENT"),
    ("COMMON_FINAL_STATE", "DEDICATED_SERVER"),
    ("CLIENT_JEI_FINAL_STATE", "CLIENT"),
    ("OFFLINE_ARTIFACT_STATE", "OFFLINE"),
}
NODE_CLASSES = {
    "source-span",
    "declared-object",
    "configuration-entry",
    "script-operation",
    "lifecycle-event",
    "runtime-state",
    "final-runtime-record",
    "explanation",
}
CAUSAL_STRENGTHS = {
    "ownership",
    "possibility",
    "participation",
    "transformation",
    "causation",
    "identity",
    "observation",
    "derivation",
}
PREDICATES = {
    "declares",
    "registers",
    "loads_configuration",
    "invokes",
    "copies",
    "transforms",
    "mutates",
    "removes",
    "replaces",
    "reconciles_to",
    "observed_as_final",
    "derives_explanation",
}
CLOSURE_STATES = {"closed", "partial", "bounded", "unresolved", "unavailable"}
OPEN_REASON_CODES = {
    "dynamic-script-unresolved",
    "source-span-unavailable",
    "runtime-transition-unobserved",
    "lifecycle-order-unresolved",
    "identity-reconciliation-unresolved",
    "required-evidence-unavailable",
    "path-bound-reached",
    "node-bound-reached",
    "relation-bound-reached",
    "evidence-bound-reached",
}
BOUND_REASONS = {
    "path-bound-reached",
    "node-bound-reached",
    "relation-bound-reached",
    "evidence-bound-reached",
}
NEGATIVE_STATEMENT_KINDS = {
    "not-found-in-closed-source-scope",
    "not-observed-in-final-capture",
    "removed-before-final",
    "no-closed-causal-path",
}
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_NODE_ID = re.compile(
    r"^rg:(?:common_final_state_client|"
    r"common_final_state_dedicated_server|"
    r"client_jei_final_state_client|"
    r"offline_artifact_state_offline):[a-z][a-z0-9_]*:.+$"
)


class ProvenanceContractError(ValueError):
    """Raised when an Atlas causal-provenance document fails closed."""


def canonical_json(value: Any) -> bytes:
    """Return the v1 canonical JSON bytes for an identity-bearing value."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def content_id(prefix: str, value: dict[str, Any], id_field: str) -> str:
    payload = copy.deepcopy(value)
    payload.pop(id_field, None)
    return prefix + canonical_sha256(payload)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProvenanceContractError(f"cannot read {path}: {exc}") from exc


def _catalog_module() -> Any:
    try:
        from . import knowledge_catalog as catalog
    except ImportError:
        import workbench_atlas.knowledge_catalog as catalog
    return catalog


def _validate_schema(value: Any, schema_path: Path, label: str) -> None:
    catalog = _catalog_module()
    schema = _read_json(schema_path)
    try:
        catalog._validate_schema_definition(schema, schema_path.name)
        catalog._validate_schema_value(value, schema, label)
    except catalog.CatalogError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _exact_fields(
    value: Any,
    expected: Iterable[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceContractError(f"{label} must be an object")
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        raise ProvenanceContractError(
            f"{label} fields differ: expected {sorted(expected_set)}, "
            f"observed {sorted(actual)}"
        )
    return value


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProvenanceContractError(f"{label} must be an object array")
    return value


def _strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProvenanceContractError(f"{label} must be a string array")
    if nonempty and not value:
        raise ProvenanceContractError(f"{label} must not be empty")
    return value


def _sorted_unique_strings(
    value: Any,
    label: str,
    *,
    nonempty: bool = False,
) -> list[str]:
    items = _strings(value, label, nonempty=nonempty)
    if items != sorted(set(items)):
        raise ProvenanceContractError(f"{label} must be sorted and unique")
    return items


def _unique_by(
    rows: list[dict[str, Any]],
    field: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get(field)
        if not isinstance(key, str) or not key:
            raise ProvenanceContractError(f"{label} contains invalid {field}")
        if key in result:
            raise ProvenanceContractError(f"{label} duplicates {key}")
        result[key] = row
    return result


def _require_content_id(
    value: dict[str, Any],
    id_field: str,
    prefix: str,
    label: str,
) -> None:
    expected = content_id(prefix, value, id_field)
    if value.get(id_field) != expected:
        raise ProvenanceContractError(f"{label} content identity differs")


def _validate_scope(scope: Any, snapshot_id: str, label: str) -> tuple[str, str]:
    row = _exact_fields(scope, {"snapshot_id", "profile", "physical_side"}, label)
    if row["snapshot_id"] != snapshot_id:
        raise ProvenanceContractError(f"{label} snapshot differs from request")
    pair = (row["profile"], row["physical_side"])
    if pair not in VALID_SCOPE_PAIRS:
        raise ProvenanceContractError(f"{label} profile/physical-side pair is invalid")
    return pair


def _validate_request_scope(scope: Any, label: str) -> tuple[str, str]:
    row = _exact_fields(scope, {"profile", "physical_side"}, label)
    pair = (row["profile"], row["physical_side"])
    if pair not in VALID_SCOPE_PAIRS:
        raise ProvenanceContractError(f"{label} profile/physical-side pair is invalid")
    return pair


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete, versioned M4 relation and lifecycle policy."""

    _validate_schema(policy, POLICY_SCHEMA_PATH, "causal provenance policy")
    if canonical_sha256(policy) != POLICY_SHA256_V1:
        raise ProvenanceContractError(
            "causal provenance policy differs from immutable v1"
        )
    if set(policy["node_classes"]) != NODE_CLASSES or len(policy["node_classes"]) != len(
        NODE_CLASSES
    ):
        raise ProvenanceContractError("policy node classes differ from v1")
    if set(policy["causal_strengths"]) != CAUSAL_STRENGTHS or len(
        policy["causal_strengths"]
    ) != len(CAUSAL_STRENGTHS):
        raise ProvenanceContractError("policy causal strengths differ from v1")

    relation_rows = _objects(policy["relation_policies"], "relation policies")
    relations = _unique_by(relation_rows, "predicate", "relation policies")
    if set(relations) != PREDICATES:
        raise ProvenanceContractError("relation policy predicate set differs from v1")
    for predicate, row in relations.items():
        if not set(row["subject_classes"]).issubset(NODE_CLASSES):
            raise ProvenanceContractError(
                f"{predicate} admits an unknown subject node class"
            )
        if not set(row["object_classes"]).issubset(NODE_CLASSES):
            raise ProvenanceContractError(
                f"{predicate} admits an unknown object node class"
            )
        if not set(row["permitted_strengths"]).issubset(CAUSAL_STRENGTHS):
            raise ProvenanceContractError(
                f"{predicate} admits an unknown causal strength"
            )
        evidence_requirements = row["evidence_requirements"]
        if (
            not isinstance(evidence_requirements, dict)
            or set(evidence_requirements) != set(row["permitted_strengths"])
        ):
            raise ProvenanceContractError(
                f"{predicate} evidence requirements differ from its strengths"
            )
        for strength, record_kinds in evidence_requirements.items():
            kinds = _sorted_unique_strings(
                record_kinds,
                f"{predicate} {strength} evidence record kinds",
                nonempty=True,
            )
            if any(
                re.fullmatch(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", item) is None
                for item in kinds
            ):
                raise ProvenanceContractError(
                    f"{predicate} has an invalid evidence record kind"
                )

    lifecycle = policy["lifecycle_model"]
    stage_rows = _objects(lifecycle["stages"], "lifecycle stages")
    stages = _unique_by(stage_rows, "stage_id", "lifecycle stages")
    constraints = _objects(
        lifecycle["order_constraints"], "lifecycle order constraints"
    )
    seen_constraints: set[tuple[str, str, tuple[str, ...]]] = set()
    for row in constraints:
        if row["before"] not in stages or row["after"] not in stages:
            raise ProvenanceContractError(
                "lifecycle order constraint references an unknown stage"
            )
        profiles = tuple(sorted(row["profiles"]))
        key = (row["before"], row["after"], profiles)
        if key in seen_constraints:
            raise ProvenanceContractError("lifecycle order constraint is duplicated")
        seen_constraints.add(key)
        if not set(profiles).issubset(PROFILES):
            raise ProvenanceContractError("lifecycle order profile is unknown")
        for profile in profiles:
            if profile not in stages[row["before"]]["applicable_profiles"]:
                raise ProvenanceContractError(
                    f"lifecycle stage {row['before']} does not apply to {profile}"
                )
            if profile not in stages[row["after"]]["applicable_profiles"]:
                raise ProvenanceContractError(
                    f"lifecycle stage {row['after']} does not apply to {profile}"
                )

    for profile in PROFILES:
        graph: dict[str, set[str]] = {stage: set() for stage in stages}
        for row in constraints:
            if profile in row["profiles"]:
                graph[row["before"]].add(row["after"])
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(stage: str) -> None:
            if stage in visiting:
                raise ProvenanceContractError(
                    f"lifecycle order contains a cycle for {profile}"
                )
            if stage in visited:
                return
            visiting.add(stage)
            for child in graph[stage]:
                visit(child)
            visiting.remove(stage)
            visited.add(stage)

        for stage in graph:
            visit(stage)

    closure_rows = _objects(policy["closure_policies"], "closure policies")
    closures = _unique_by(closure_rows, "state", "closure policies")
    if set(closures) != CLOSURE_STATES:
        raise ProvenanceContractError("closure policy state set differs from v1")
    negative_rows = _objects(
        policy["negative_statement_policies"], "negative statement policies"
    )
    negatives = _unique_by(negative_rows, "kind", "negative statement policies")
    if set(negatives) != NEGATIVE_STATEMENT_KINDS:
        raise ProvenanceContractError(
            "negative statement policy kind set differs from v1"
        )
    if policy["pagination_policy"] != {
        "mode": "none-v1-single-bounded-result",
        "truncation_is_completion": False,
    }:
        raise ProvenanceContractError("pagination policy differs from v1")
    return policy


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = _read_json(path)
    if not isinstance(policy, dict):
        raise ProvenanceContractError("causal provenance policy must be an object")
    return validate_policy(policy)


def validate_request(
    request: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one exact M1-bound provenance request and its identity."""

    if policy is None:
        policy = load_policy()
    else:
        validate_policy(policy)
    _validate_schema(request, REQUEST_SCHEMA_PATH, "causal provenance request")
    primary = _validate_request_scope(request["scope"], "request scope")
    supporting = _objects(request["supporting_scopes"], "supporting scopes")
    supporting_pairs = [
        _validate_request_scope(row, f"supporting scope {index}")
        for index, row in enumerate(supporting)
    ]
    if supporting_pairs != sorted(set(supporting_pairs)):
        raise ProvenanceContractError("supporting scopes must be sorted and unique")
    if primary in supporting_pairs:
        raise ProvenanceContractError(
            "primary scope must not be repeated in supporting scopes"
        )
    final = request["final_runtime_record"]
    if not RUNTIME_NODE_ID.fullmatch(final["runtime_node_id"]):
        raise ProvenanceContractError("final runtime node identity is invalid")
    if not HEX_64.fullmatch(final["runtime_record_sha256"]):
        raise ProvenanceContractError("final runtime record digest is invalid")
    if request["policy_id"] != policy["policy_id"]:
        raise ProvenanceContractError("request policy ID differs")
    if request["policy_sha256"] != canonical_sha256(policy):
        raise ProvenanceContractError("request policy digest differs")
    for key, value in request["bounds"].items():
        if type(value) is not int or value < 1:
            raise ProvenanceContractError(f"request bound {key} must be positive")
    _require_content_id(request, "request_id", REQUEST_PREFIX, "request")
    return request


IDENTITY_FIELDS = {
    "source-span": {
        "source_lock_id",
        "source_id",
        "repository",
        "revision",
        "tree",
        "path",
        "symbol",
        "line_start",
        "line_end",
        "byte_start",
        "byte_end",
        "file_sha256",
        "span_sha256",
        "extraction_policy_id",
    },
    "declared-object": {"identity_kind", "stable_key", "content_sha256"},
    "configuration-entry": {
        "source_span_id",
        "key_path",
        "selected_value_sha256",
    },
    "script-operation": {
        "source_span_id",
        "stage_execution_id",
        "operation_index",
        "operation_sha256",
    },
    "lifecycle-event": {
        "lifecycle_model_id",
        "stage_id",
        "occurrence_id",
        "ordinal",
    },
    "runtime-state": {
        "runtime_node_id",
        "state_sha256",
        "capture_evidence_id",
    },
    "final-runtime-record": {
        "runtime_node_id",
        "runtime_kind",
        "runtime_record_sha256",
        "capture_evidence_id",
    },
    "explanation": {"audience", "statement_sha256"},
}


def _validate_node_identity(
    node_class: str,
    identity: Any,
    policy: dict[str, Any],
    nodes: dict[str, dict[str, Any]] | None = None,
) -> None:
    row = _exact_fields(
        identity,
        IDENTITY_FIELDS[node_class],
        f"{node_class} identity",
    )
    if node_class == "source-span":
        for key in ("revision", "tree"):
            if not HEX_40.fullmatch(row[key]):
                raise ProvenanceContractError(
                    f"source-span {key} must be lowercase 40-hex"
                )
        for key in ("file_sha256", "span_sha256"):
            if not HEX_64.fullmatch(row[key]):
                raise ProvenanceContractError(
                    f"source-span {key} must be lowercase SHA-256"
                )
        if (
            type(row["line_start"]) is not int
            or type(row["line_end"]) is not int
            or row["line_start"] < 1
            or row["line_start"] > row["line_end"]
        ):
            raise ProvenanceContractError("source-span inclusive line range is invalid")
        if (
            type(row["byte_start"]) is not int
            or type(row["byte_end"]) is not int
            or row["byte_start"] < 0
            or row["byte_start"] > row["byte_end"]
        ):
            raise ProvenanceContractError("source-span inclusive byte range is invalid")
        for key in ("source_lock_id", "source_id", "repository", "path", "symbol"):
            if not isinstance(row[key], str) or not row[key]:
                raise ProvenanceContractError(f"source-span {key} is empty")
    elif node_class in {"declared-object", "configuration-entry"}:
        digest_key = (
            "content_sha256"
            if node_class == "declared-object"
            else "selected_value_sha256"
        )
        if not HEX_64.fullmatch(row[digest_key]):
            raise ProvenanceContractError(f"{node_class} digest is invalid")
    elif node_class == "script-operation":
        if type(row["operation_index"]) is not int or row["operation_index"] < 0:
            raise ProvenanceContractError("script operation index is invalid")
        if not HEX_64.fullmatch(row["operation_sha256"]):
            raise ProvenanceContractError("script operation digest is invalid")
    elif node_class == "lifecycle-event":
        stages = {
            item["stage_id"]: item
            for item in policy["lifecycle_model"]["stages"]
        }
        if row["lifecycle_model_id"] != policy["lifecycle_model"]["model_id"]:
            raise ProvenanceContractError("lifecycle-event model differs")
        if row["stage_id"] not in stages:
            raise ProvenanceContractError("lifecycle-event stage is unknown")
        if type(row["ordinal"]) is not int or row["ordinal"] < 0:
            raise ProvenanceContractError("lifecycle-event ordinal is invalid")
    elif node_class in {"runtime-state", "final-runtime-record"}:
        if not RUNTIME_NODE_ID.fullmatch(row["runtime_node_id"]):
            raise ProvenanceContractError(f"{node_class} runtime node ID is invalid")
        digest_key = (
            "state_sha256"
            if node_class == "runtime-state"
            else "runtime_record_sha256"
        )
        if not HEX_64.fullmatch(row[digest_key]):
            raise ProvenanceContractError(f"{node_class} digest is invalid")
        if not row["capture_evidence_id"].startswith(EVIDENCE_PREFIX):
            raise ProvenanceContractError(
                f"{node_class} capture evidence identity is invalid"
            )
    elif node_class == "explanation":
        if row["audience"] not in {"player", "developer"}:
            raise ProvenanceContractError("explanation audience is invalid")
        if not HEX_64.fullmatch(row["statement_sha256"]):
            raise ProvenanceContractError("explanation statement digest is invalid")

    if nodes is not None:
        for reference_key in ("source_span_id",):
            if reference_key not in row:
                continue
            source_span = nodes.get(row[reference_key])
            if source_span is None or source_span["node_class"] != "source-span":
                raise ProvenanceContractError(
                    f"{node_class} does not reference an exact source-span node"
                )


def _validate_evidence(row: dict[str, Any]) -> None:
    _exact_fields(
        row,
        {"evidence_id", "authority", "record_kind", "record", "record_sha256"},
        "provenance evidence",
    )
    if row["record_sha256"] != canonical_sha256(row["record"]):
        raise ProvenanceContractError("provenance evidence record digest differs")
    authority_by_record_kind = {
        "source-span": {"pinned-source"},
        "closed-source-index": {"pinned-source"},
        "stage-execution": {"runtime-mechanics"},
        "state-transition": {"runtime-mechanics"},
        "final-runtime-record": {"runtime-mechanics"},
        "final-capture-absence": {"runtime-mechanics"},
        "identity-reconciliation": {"runtime-mechanics"},
        "configuration-selection": {"pinned-source", "runtime-mechanics"},
        "causal-validation": {"derived-validation"},
    }
    permitted_authorities = authority_by_record_kind.get(row["record_kind"])
    if (
        permitted_authorities is not None
        and row["authority"] not in permitted_authorities
    ):
        raise ProvenanceContractError(
            "provenance evidence authority does not match its record kind"
        )
    _require_content_id(row, "evidence_id", EVIDENCE_PREFIX, "provenance evidence")


def _lifecycle_reachability(
    policy: dict[str, Any],
    profile: str,
) -> dict[str, set[str]]:
    stages = {
        row["stage_id"] for row in policy["lifecycle_model"]["stages"]
    }
    reachability = {stage: set() for stage in stages}
    for row in policy["lifecycle_model"]["order_constraints"]:
        if profile in row["profiles"]:
            reachability[row["before"]].add(row["after"])
    changed = True
    while changed:
        changed = False
        for stage in stages:
            expanded = set(reachability[stage])
            for child in tuple(reachability[stage]):
                expanded.update(reachability[child])
            if expanded != reachability[stage]:
                reachability[stage] = expanded
                changed = True
    return reachability


def _validate_negative_statement(
    row: dict[str, Any],
    *,
    result_state: str,
    evidence: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    paths: list[dict[str, Any]],
    policy: dict[str, Any],
) -> set[str]:
    _exact_fields(
        row,
        {
            "statement_id",
            "kind",
            "subject_node_id",
            "authority_evidence_ids",
            "bounded_wording",
        },
        "negative statement",
    )
    policies = {
        item["kind"]: item for item in policy["negative_statement_policies"]
    }
    if row["kind"] not in policies:
        raise ProvenanceContractError("negative statement kind is not permitted")
    statement_policy = policies[row["kind"]]
    if result_state not in statement_policy["allowed_result_states"]:
        raise ProvenanceContractError(
            "negative statement is not permitted for the result state"
        )
    if row["bounded_wording"] != statement_policy["bounded_wording"]:
        raise ProvenanceContractError("negative statement wording is not policy-bound")
    evidence_ids = _sorted_unique_strings(
        row["authority_evidence_ids"],
        "negative statement authority evidence",
        nonempty=True,
    )
    if not set(evidence_ids).issubset(evidence):
        raise ProvenanceContractError("negative statement evidence is unresolved")
    authorities = {evidence[item]["authority"] for item in evidence_ids}
    if not set(statement_policy["required_authorities"]).issubset(authorities):
        raise ProvenanceContractError(
            "negative statement lacks its required authorities"
        )
    subject = row["subject_node_id"]
    if subject is not None and subject not in nodes:
        raise ProvenanceContractError("negative statement subject is unresolved")
    closed_paths = [item for item in paths if item["closure_state"] == "closed"]
    if statement_policy["requires_closed_path"] and not closed_paths:
        raise ProvenanceContractError("negative statement requires a closed path")
    if row["kind"] == "not-found-in-closed-source-scope" and not any(
        evidence[item]["record_kind"] == "closed-source-index"
        for item in evidence_ids
    ):
        raise ProvenanceContractError(
            "closed-source negative statement lacks a closed source index"
        )
    if row["kind"] == "not-observed-in-final-capture" and not any(
        evidence[item]["record_kind"] == "final-capture-absence"
        for item in evidence_ids
    ):
        raise ProvenanceContractError(
            "runtime negative statement lacks final-capture absence evidence"
        )
    if row["kind"] == "no-closed-causal-path":
        if closed_paths:
            raise ProvenanceContractError(
                "no-closed-path statement conflicts with a closed path"
            )
        if not any(
            evidence[item]["record_kind"] == "causal-validation"
            for item in evidence_ids
        ):
            raise ProvenanceContractError(
                "no-closed-path statement lacks derived validation"
            )
    if row["kind"] == "removed-before-final":
        removed_relations = {
            item["relation_id"]: item
            for item in relations.values()
            if item["predicate"] == "removes"
        }
        if not any(
            set(removed_relations).intersection(path["relation_ids"])
            for path in closed_paths
        ):
            raise ProvenanceContractError(
                "removed-before-final statement lacks a closed removal path"
            )
        if subject is None or not any(
            subject == removed_relations[relation_id]["subject_node_id"]
            for path in closed_paths
            for relation_id in path["relation_ids"]
            if relation_id in removed_relations
        ):
            raise ProvenanceContractError(
                "removed-before-final statement does not bind the removed "
                "predecessor"
            )
    _require_content_id(
        row,
        "statement_id",
        STATEMENT_PREFIX,
        "negative statement",
    )
    return set(evidence_ids)


def validate_result(
    result: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate exact graph identity, evidence closure, order, and claims."""

    if policy is None:
        policy = load_policy()
    else:
        validate_policy(policy)
    _validate_schema(result, RESULT_SCHEMA_PATH, "causal provenance result")
    request = validate_request(result["request"], policy)
    if result["status"] != result["closure"]["state"]:
        raise ProvenanceContractError("result status differs from closure state")
    state = result["status"]
    reason_codes = _sorted_unique_strings(
        result["closure"]["reason_codes"], "closure reason codes"
    )
    if not set(reason_codes).issubset(OPEN_REASON_CODES):
        raise ProvenanceContractError("closure reason code is unknown")
    open_frontier = _sorted_unique_strings(
        result["closure"]["open_frontier_node_ids"],
        "open frontier node IDs",
    )

    evidence_rows = _objects(result["evidence"], "provenance evidence")
    evidence = _unique_by(evidence_rows, "evidence_id", "provenance evidence")
    if list(evidence) != sorted(evidence):
        raise ProvenanceContractError("provenance evidence must be sorted by ID")
    for row in evidence_rows:
        _validate_evidence(row)

    node_rows = _objects(result["nodes"], "provenance nodes")
    nodes = _unique_by(node_rows, "node_id", "provenance nodes")
    if list(nodes) != sorted(nodes):
        raise ProvenanceContractError("provenance nodes must be sorted by ID")
    allowed_scope_pairs = {
        _validate_request_scope(request["scope"], "request scope"),
        *(
            _validate_request_scope(row, "supporting scope")
            for row in request["supporting_scopes"]
        ),
    }
    used_evidence = {request["final_runtime_record"]["capture_evidence_id"]}
    for row in node_rows:
        if row["node_class"] not in NODE_CLASSES:
            raise ProvenanceContractError("provenance node class is unknown")
        pair = _validate_scope(
            row["scope"], request["snapshot_id"], f"node {row['node_id']} scope"
        )
        if pair not in allowed_scope_pairs:
            raise ProvenanceContractError("node scope is not admitted by request")
        evidence_ids = _sorted_unique_strings(
            row["evidence_ids"], "node evidence IDs", nonempty=True
        )
        if not set(evidence_ids).issubset(evidence):
            raise ProvenanceContractError("node evidence is unresolved")
        used_evidence.update(evidence_ids)
        _validate_node_identity(row["node_class"], row["identity"], policy)
        if (
            row["node_class"] == "source-span"
            and row["identity"]["source_lock_id"] != request["source_lock_id"]
        ):
            raise ProvenanceContractError(
                "source-span source lock differs from request"
            )
        if row["node_class"] == "source-span" and not any(
            evidence[item]["authority"] == "pinned-source"
            and evidence[item]["record_kind"] == "source-span"
            and evidence[item]["record"].get("source_span") == row["identity"]
            for item in evidence_ids
        ):
            raise ProvenanceContractError(
                "source-span evidence does not bind its source identity"
            )
        if row["node_class"] in {"script-operation", "lifecycle-event"} and not any(
            evidence[item]["authority"] == "runtime-mechanics"
            and evidence[item]["record_kind"] == "stage-execution"
            and evidence[item]["record"].get("executed_node_class")
            == row["node_class"]
            and evidence[item]["record"].get("executed_identity_sha256")
            == canonical_sha256(row["identity"])
            and (
                (
                    row["node_class"] == "script-operation"
                    and evidence[item]["record"].get("stage_execution_id")
                    == row["identity"]["stage_execution_id"]
                )
                or (
                    row["node_class"] == "lifecycle-event"
                    and evidence[item]["record"].get("stage_id")
                    == row["identity"]["stage_id"]
                    and evidence[item]["record"].get("occurrence_id")
                    == row["identity"]["occurrence_id"]
                )
            )
            for item in evidence_ids
        ):
            raise ProvenanceContractError(
                f"{row['node_class']} lacks exact execution evidence"
            )
        if row["node_class"] == "lifecycle-event":
            stage = next(
                item
                for item in policy["lifecycle_model"]["stages"]
                if item["stage_id"] == row["identity"]["stage_id"]
            )
            if row["scope"]["profile"] not in stage["applicable_profiles"]:
                raise ProvenanceContractError(
                    "lifecycle-event stage does not apply to its profile"
                )
        if row["node_class"] in {"runtime-state", "final-runtime-record"}:
            capture_evidence_id = row["identity"]["capture_evidence_id"]
            if capture_evidence_id not in evidence_ids:
                raise ProvenanceContractError(
                    "runtime node omits its capture evidence binding"
                )
            capture_evidence = evidence.get(capture_evidence_id)
            if (
                capture_evidence is None
                or capture_evidence["authority"] != "runtime-mechanics"
                or capture_evidence["record_kind"] != "final-runtime-record"
            ):
                raise ProvenanceContractError(
                    "runtime node capture evidence is not an exact final "
                    "runtime record"
                )
        _require_content_id(row, "node_id", NODE_PREFIX, "provenance node")
    for row in node_rows:
        _validate_node_identity(
            row["node_class"], row["identity"], policy, nodes
        )

    final_identity = request["final_runtime_record"]
    final_nodes = [
        row
        for row in node_rows
        if row["node_class"] == "final-runtime-record"
        and row["identity"] == final_identity
        and (
            row["scope"]["profile"],
            row["scope"]["physical_side"],
        )
        == _validate_request_scope(request["scope"], "request scope")
    ]
    if len(final_nodes) != 1:
        raise ProvenanceContractError(
            "result must contain exactly one request-bound final runtime record"
        )

    relation_rows = _objects(result["relations"], "provenance relations")
    relations = _unique_by(
        relation_rows, "relation_id", "provenance relations"
    )
    if list(relations) != sorted(relations):
        raise ProvenanceContractError("provenance relations must be sorted by ID")
    relation_policies = {
        row["predicate"]: row for row in policy["relation_policies"]
    }
    stages = {
        row["stage_id"]: row for row in policy["lifecycle_model"]["stages"]
    }
    for row in relation_rows:
        subject = nodes.get(row["subject_node_id"])
        object_node = nodes.get(row["object_node_id"])
        if subject is None or object_node is None:
            raise ProvenanceContractError("relation endpoint is unresolved")
        relation_policy = relation_policies[row["predicate"]]
        if subject["node_class"] not in relation_policy["subject_classes"]:
            raise ProvenanceContractError("relation subject class is not permitted")
        if object_node["node_class"] not in relation_policy["object_classes"]:
            raise ProvenanceContractError("relation object class is not permitted")
        if row["causal_strength"] not in relation_policy["permitted_strengths"]:
            raise ProvenanceContractError("relation causal strength is not permitted")
        lifecycle_requirement = relation_policy["lifecycle_requirement"]
        stage_id = row["lifecycle_stage_id"]
        if lifecycle_requirement == "required" and stage_id is None:
            raise ProvenanceContractError("relation requires a lifecycle stage")
        if lifecycle_requirement == "forbidden" and stage_id is not None:
            raise ProvenanceContractError("relation forbids a lifecycle stage")
        if stage_id is not None:
            if stage_id not in stages:
                raise ProvenanceContractError("relation lifecycle stage is unknown")
            profiles = {
                subject["scope"]["profile"],
                object_node["scope"]["profile"],
            }
            if not profiles.issubset(stages[stage_id]["applicable_profiles"]):
                raise ProvenanceContractError(
                    "relation lifecycle stage does not apply to its endpoint profile"
                )
        evidence_ids = _sorted_unique_strings(
            row["evidence_ids"], "relation evidence IDs", nonempty=True
        )
        if not set(evidence_ids).issubset(evidence):
            raise ProvenanceContractError("relation evidence is unresolved")
        observed_record_kinds = {
            evidence[item]["record_kind"] for item in evidence_ids
        }
        required_record_kinds = set(
            relation_policy["evidence_requirements"][row["causal_strength"]]
        )
        if not required_record_kinds.issubset(observed_record_kinds):
            raise ProvenanceContractError(
                "relation lacks strength-specific evidence record kinds"
            )
        relation_evidence = [evidence[item] for item in evidence_ids]
        if "stage-execution" in required_record_kinds and not any(
            item["record_kind"] == "stage-execution"
            and item["record"].get("stage_id") == stage_id
            and isinstance(item["record"].get("stage_execution_id"), str)
            and bool(item["record"]["stage_execution_id"])
            and isinstance(
                item["record"].get("related_identity_sha256s"), list
            )
            and item["record"]["related_identity_sha256s"]
            == sorted(set(item["record"]["related_identity_sha256s"]))
            and {
                canonical_sha256(subject["identity"]),
                canonical_sha256(object_node["identity"]),
            }.issubset(item["record"]["related_identity_sha256s"])
            and (
                (
                    subject["node_class"] in {
                        "script-operation",
                        "lifecycle-event",
                    }
                    and item["record"].get("executed_node_class")
                    == subject["node_class"]
                    and item["record"].get("executed_identity_sha256")
                    == canonical_sha256(subject["identity"])
                )
                or (
                    object_node["node_class"]
                    in {"script-operation", "lifecycle-event"}
                    and item["record"].get("executed_node_class")
                    == object_node["node_class"]
                    and item["record"].get("executed_identity_sha256")
                    == canonical_sha256(object_node["identity"])
                )
                or (
                    subject["node_class"]
                    not in {"script-operation", "lifecycle-event"}
                    and object_node["node_class"]
                    not in {"script-operation", "lifecycle-event"}
                )
            )
            for item in relation_evidence
        ):
            raise ProvenanceContractError(
                "relation stage-execution evidence does not bind its lifecycle "
                "stage and endpoint identities"
            )
        if "state-transition" in required_record_kinds and not any(
            item["record_kind"] == "state-transition"
            and item["record"].get("predicate") == row["predicate"]
            and item["record"].get("subject_node_id") == row["subject_node_id"]
            and item["record"].get("object_node_id") == row["object_node_id"]
            for item in relation_evidence
        ):
            raise ProvenanceContractError(
                "relation state-transition evidence does not bind its predicate "
                "and endpoints"
            )
        if "configuration-selection" in required_record_kinds and not any(
            item["record_kind"] == "configuration-selection"
            and item["record"].get("configuration_node_id")
            == row["subject_node_id"]
            for item in relation_evidence
        ):
            raise ProvenanceContractError(
                "configuration relation evidence does not bind its entry"
            )
        if "identity-reconciliation" in required_record_kinds and not any(
            item["record_kind"] == "identity-reconciliation"
            and item["record"].get("subject_node_id") == row["subject_node_id"]
            and item["record"].get("object_node_id") == row["object_node_id"]
            for item in relation_evidence
        ):
            raise ProvenanceContractError(
                "reconciliation evidence does not bind its endpoints"
            )
        if "final-runtime-record" in required_record_kinds and not any(
            item["record_kind"] == "final-runtime-record"
            and item["record"].get("runtime_node_id")
            == object_node["identity"].get("runtime_node_id")
            and item["record"].get("runtime_record_sha256")
            == object_node["identity"].get("runtime_record_sha256")
            for item in relation_evidence
        ):
            raise ProvenanceContractError(
                "final-observation evidence does not bind its runtime record"
            )
        if "causal-validation" in required_record_kinds and not any(
            item["record_kind"] == "causal-validation"
            for item in relation_evidence
        ):
            raise ProvenanceContractError(
                "derivation relation lacks causal validation evidence"
            )
        used_evidence.update(evidence_ids)
        _require_content_id(
            row, "relation_id", RELATION_PREFIX, "provenance relation"
        )

    path_rows = _objects(result["paths"], "provenance paths")
    paths = _unique_by(path_rows, "path_id", "provenance paths")
    if list(paths) != sorted(paths):
        raise ProvenanceContractError("provenance paths must be sorted by ID")
    used_relations: set[str] = set()
    used_nodes: set[str] = set()
    for row in path_rows:
        node_ids = _strings(row["node_ids"], "path node IDs", nonempty=True)
        relation_ids = _strings(row["relation_ids"], "path relation IDs")
        if len(node_ids) != len(set(node_ids)):
            raise ProvenanceContractError("path repeats a node")
        if len(relation_ids) != len(set(relation_ids)):
            raise ProvenanceContractError("path repeats a relation")
        if len(relation_ids) != len(node_ids) - 1:
            raise ProvenanceContractError("path edge count differs from node count")
        if not set(node_ids).issubset(nodes) or not set(relation_ids).issubset(
            relations
        ):
            raise ProvenanceContractError("path reference is unresolved")
        for index, relation_id in enumerate(relation_ids):
            relation = relations[relation_id]
            if (
                relation["subject_node_id"] != node_ids[index]
                or relation["object_node_id"] != node_ids[index + 1]
            ):
                raise ProvenanceContractError("path relation is not edge-connected")
        open_reasons = _sorted_unique_strings(
            row["open_reason_codes"], "path open reason codes"
        )
        if not set(open_reasons).issubset(OPEN_REASON_CODES):
            raise ProvenanceContractError("path open reason code is unknown")
        if row["closure_state"] == "closed":
            if open_reasons:
                raise ProvenanceContractError("closed path has open reasons")
            if nodes[node_ids[0]]["node_class"] not in {
                "source-span",
                "configuration-entry",
            }:
                raise ProvenanceContractError(
                    "closed causal path must start at exact source"
                )
            if nodes[node_ids[-1]]["node_class"] != "final-runtime-record":
                raise ProvenanceContractError(
                    "closed causal path must end at final runtime record"
                )
            if not relation_ids or relations[relation_ids[-1]][
                "predicate"
            ] != "observed_as_final":
                raise ProvenanceContractError(
                    "closed causal path lacks observed_as_final endpoint"
                )
            strengths = {
                relations[item]["causal_strength"] for item in relation_ids
            }
            if "possibility" in strengths:
                raise ProvenanceContractError(
                    "possibility-only evidence cannot close a causal path"
                )
            if not strengths.intersection({"transformation", "causation"}):
                raise ProvenanceContractError(
                    "closed path lacks a causal or transformation transition"
                )
        elif not open_reasons:
            raise ProvenanceContractError("partial path lacks an open reason")
        if any(
            relations[item]["predicate"] == "derives_explanation"
            for item in relation_ids
        ):
            raise ProvenanceContractError(
                "presentation derivation cannot enter a causal path"
            )

        profile = nodes[node_ids[-1]]["scope"]["profile"]
        reachability = _lifecycle_reachability(policy, profile)
        ordered_stages = [
            relations[item]["lifecycle_stage_id"]
            for item in relation_ids
            if relations[item]["lifecycle_stage_id"] is not None
        ]
        for earlier, later in zip(ordered_stages, ordered_stages[1:]):
            if earlier != later and later not in reachability[earlier]:
                raise ProvenanceContractError("path lifecycle order is invalid")
        _require_content_id(row, "path_id", PATH_PREFIX, "provenance path")
        used_relations.update(relation_ids)
        used_nodes.update(node_ids)

    presentation_relation_ids = {
        identifier
        for identifier, row in relations.items()
        if row["predicate"] == "derives_explanation"
    }
    if set(relations) - presentation_relation_ids != used_relations:
        raise ProvenanceContractError("result contains a relation unused by any path")
    for identifier in presentation_relation_ids:
        used_nodes.add(relations[identifier]["subject_node_id"])
        used_nodes.add(relations[identifier]["object_node_id"])
    statement_rows = _objects(
        result["negative_statements"], "negative statements"
    )
    statements = _unique_by(
        statement_rows, "statement_id", "negative statements"
    )
    if list(statements) != sorted(statements):
        raise ProvenanceContractError("negative statements must be sorted by ID")
    for row in statement_rows:
        used_evidence.update(
            _validate_negative_statement(
                row,
                result_state=state,
                evidence=evidence,
                nodes=nodes,
                relations=relations,
                paths=path_rows,
                policy=policy,
            )
        )
        if row["subject_node_id"] is not None:
            used_nodes.add(row["subject_node_id"])

    used_nodes.add(final_nodes[0]["node_id"])
    if set(nodes) != used_nodes:
        raise ProvenanceContractError("result contains a node unused by any path")
    if set(evidence) != used_evidence:
        raise ProvenanceContractError("result evidence is not exact and fully used")
    if not used_evidence.issubset(evidence):
        raise ProvenanceContractError("result references unresolved evidence")

    truncation = result["truncation"]
    expected_counts = {
        "emitted_paths": len(path_rows),
        "emitted_nodes": len(node_rows),
        "emitted_relations": len(relation_rows),
        "emitted_evidence_records": len(evidence_rows),
    }
    for key, expected in expected_counts.items():
        if truncation[key] != expected:
            raise ProvenanceContractError(f"truncation {key} differs")
    bounds = request["bounds"]
    for count_key, bound_key in (
        ("emitted_paths", "max_paths"),
        ("emitted_nodes", "max_nodes"),
        ("emitted_relations", "max_relations"),
        ("emitted_evidence_records", "max_evidence_records"),
    ):
        if truncation[count_key] > bounds[bound_key]:
            raise ProvenanceContractError(f"result exceeds {bound_key}")
    if result["pagination"] != {
        "mode": "none-v1-single-bounded-result",
        "continuation": None,
    }:
        raise ProvenanceContractError("result pagination differs from v1")

    closed_paths = [row for row in path_rows if row["closure_state"] == "closed"]
    if state == "closed":
        if not closed_paths or reason_codes or open_frontier:
            raise ProvenanceContractError("closed result closure fields differ")
        if truncation["truncated"]:
            raise ProvenanceContractError("closed result cannot be truncated")
    elif state == "partial":
        if not path_rows or not open_frontier or not reason_codes:
            raise ProvenanceContractError("partial result lacks an open frontier")
        if truncation["truncated"]:
            raise ProvenanceContractError("partial result cannot be truncated")
    elif state == "bounded":
        if not open_frontier or not set(reason_codes).intersection(BOUND_REASONS):
            raise ProvenanceContractError("bounded result lacks a bounded frontier")
        if not truncation["truncated"] or truncation["omitted_at_least"] < 1:
            raise ProvenanceContractError("bounded result lacks explicit truncation")
    elif state == "unresolved":
        if closed_paths or not reason_codes or open_frontier:
            raise ProvenanceContractError("unresolved result closure fields differ")
        if truncation["truncated"]:
            raise ProvenanceContractError("unresolved result cannot be truncated")
    elif state == "unavailable":
        if path_rows or reason_codes != ["required-evidence-unavailable"]:
            raise ProvenanceContractError("unavailable result closure fields differ")
        if open_frontier or truncation["truncated"]:
            raise ProvenanceContractError("unavailable result closure fields differ")

    if state != "bounded":
        if truncation["reason"] is not None or truncation["omitted_at_least"] != 0:
            raise ProvenanceContractError(
                "non-bounded result reports omitted causal records"
            )
    elif truncation["reason"] not in BOUND_REASONS:
        raise ProvenanceContractError("bounded truncation reason is invalid")
    if set(open_frontier) - set(nodes):
        raise ProvenanceContractError("open frontier references an unknown node")
    _require_content_id(result, "result_id", RESULT_PREFIX, "result")
    return result


def validate_examples(
    examples_path: Path = EXAMPLES_PATH,
    policy_path: Path = POLICY_PATH,
) -> dict[str, Any]:
    """Validate every canonical request/result and expected-invalid mutation."""

    examples = _read_json(examples_path)
    if not isinstance(examples, dict):
        raise ProvenanceContractError("contract examples must be an object")
    _exact_fields(
        examples,
        {
            "schema_version",
            "format",
            "policy_id",
            "policy_sha256",
            "requests",
            "results",
            "invalid_mutations",
        },
        "contract examples",
    )
    if examples["schema_version"] != 1:
        raise ProvenanceContractError("contract examples schema version differs")
    if examples["format"] != "susy-atlas-causal-provenance-examples-v1":
        raise ProvenanceContractError("contract examples format differs")
    policy = load_policy(policy_path)
    if examples["policy_id"] != policy["policy_id"]:
        raise ProvenanceContractError("contract examples policy ID differs")
    if examples["policy_sha256"] != canonical_sha256(policy):
        raise ProvenanceContractError("contract examples policy digest differs")
    request_rows = _objects(examples["requests"], "example requests")
    requests = _unique_by(request_rows, "request_id", "example requests")
    for row in request_rows:
        validate_request(row, policy)
    result_rows = _objects(examples["results"], "example results")
    results = _unique_by(result_rows, "result_id", "example results")
    observed_states: set[str] = set()
    for row in result_rows:
        validate_result(row, policy)
        if row["request"]["request_id"] not in requests:
            raise ProvenanceContractError("example result request is not registered")
        observed_states.add(row["status"])
    if not {"closed", "partial", "bounded", "unresolved", "unavailable"}.issubset(
        observed_states
    ):
        raise ProvenanceContractError(
            "examples do not cover every aggregate closure state"
        )
    invalid_rows = _objects(examples["invalid_mutations"], "invalid mutations")
    names = _unique_by(invalid_rows, "name", "invalid mutations")
    if len(names) < 8:
        raise ProvenanceContractError(
            "examples require at least eight independent invalid mutations"
        )
    for row in invalid_rows:
        _exact_fields(
            row,
            {"name", "result_id", "path", "replacement", "expected_error"},
            "invalid mutation",
        )
        if row["result_id"] not in results:
            raise ProvenanceContractError(
                "invalid mutation references an unknown result"
            )
        candidate = copy.deepcopy(results[row["result_id"]])
        target: Any = candidate
        parts = row["path"].split("/")
        if not parts or any(part == "" for part in parts):
            raise ProvenanceContractError("invalid mutation path is malformed")
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            last = parts[-1]
            if isinstance(target, list):
                target[int(last)] = row["replacement"]
            else:
                target[last] = row["replacement"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProvenanceContractError(
                f"invalid mutation path cannot be applied: {row['name']}"
            ) from exc
        try:
            validate_result(candidate, policy)
        except ProvenanceContractError as exc:
            if row["expected_error"] not in str(exc):
                raise ProvenanceContractError(
                    f"invalid mutation {row['name']} failed for the wrong reason: {exc}"
                ) from exc
        else:
            raise ProvenanceContractError(
                f"invalid mutation was accepted: {row['name']}"
            )
    return examples


def main() -> int:
    policy = load_policy()
    examples = validate_examples(policy_path=POLICY_PATH)
    print(
        json.dumps(
            {
                "policy_id": policy["policy_id"],
                "policy_sha256": canonical_sha256(policy),
                "requests": len(examples["requests"]),
                "results": len(examples["results"]),
                "invalid_mutations": len(examples["invalid_mutations"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
