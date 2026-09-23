#!/usr/bin/env python3

"""Validate and resolve exact target-parameterized Atlas query instances."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import workbench_atlas.knowledge_catalog as catalog
from workbench_atlas.runtime_graph_query import (
    NodeSelector,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
)

from workbench_atlas.layout import DATA_ROOT, SCHEMA_ROOT

CORPUS_ROOT = DATA_ROOT

DEFAULT_QUESTIONS = CORPUS_ROOT / "acceptance-questions-v1.json"
DEFAULT_CAPABILITY_POLICY = CORPUS_ROOT / "atlas-capabilities-v1.json"
QUERY_INSTANCE_SCHEMA = SCHEMA_ROOT / "atlas-query-instance-v1.schema.json"
CAPABILITY_POLICY_SCHEMA = (
    SCHEMA_ROOT / "atlas-capability-policy-v1.schema.json"
)
QUERY_RESULT_SCHEMA = SCHEMA_ROOT / "atlas-query-result-v1.schema.json"

QUERY_FORMAT = "susy-atlas-query-instance-v1"
QUERY_POLICY_ID = "ATLAS-QUERY-POLICY-V1"
CAPABILITY_POLICY_ID = "ATLAS-CAPABILITY-POLICY-V1"
QUERY_ID_PREFIX = "atlas-query:sha256:"
AUTHORITY_BASES = (
    "runtime-mechanics",
    "runtime-presentation",
    "pinned-source",
    "quest-data",
    "curated-interpretation",
    "placed-world-observation",
)
AUTHORITY_STATUSES = {
    "available",
    "not-applicable",
    "not-observed",
    "unavailable",
    "ambiguous",
    "unresolved",
    "not-requested",
}
PROFILE_SIDES = {
    "COMMON_FINAL_STATE": {"CLIENT", "DEDICATED_SERVER"},
    "CLIENT_JEI_FINAL_STATE": {"CLIENT"},
    "OFFLINE_ARTIFACT_STATE": {"OFFLINE"},
}
V1_PRIMARY_KEY_KINDS = {
    "material": "material-resource-location",
    "item": "item-resource-location",
    "fluid": "fluid-name",
    "ore-dictionary": "ore-dictionary-name",
    "ore-prefix": "ore-prefix-name",
    "recipe-map": "recipe-map-name",
    "machine": "machine-resource-location",
}


class AtlasQueryError(ValueError):
    """Raised when an Atlas query instance or exact resolution is invalid."""


def canonical_json_payload(value: Any) -> bytes:
    """Return the canonical bytes used for Atlas request identities."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AtlasQueryError(f"Atlas query contains non-canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def question_definition_sha256(question: dict[str, Any]) -> str:
    """Hash one complete question definition."""

    if not isinstance(question, dict):
        raise AtlasQueryError("Atlas question definition must be an object")
    return hashlib.sha256(canonical_json_payload(question)).hexdigest()


def capability_policy_sha256(policy: dict[str, Any]) -> str:
    """Hash one complete capability-policy definition."""

    if not isinstance(policy, dict):
        raise AtlasQueryError("Atlas capability policy must be an object")
    return hashlib.sha256(canonical_json_payload(policy)).hexdigest()


def query_instance_id(instance: dict[str, Any]) -> str:
    """Recompute one query ID without trusting its supplied identifier."""

    if not isinstance(instance, dict):
        raise AtlasQueryError("Atlas query instance must be an object")
    semantic = {
        key: value
        for key, value in instance.items()
        if key != "query_instance_id"
    }
    return QUERY_ID_PREFIX + hashlib.sha256(
        canonical_json_payload(semantic)
    ).hexdigest()


def _read_json(path: Path, label: str) -> Any:
    try:
        return catalog.read_json(path)
    except (OSError, catalog.CatalogError) as exc:
        raise AtlasQueryError(f"invalid {label} {path}: {exc}") from exc


def _validate_schema(value: Any, schema_path: Path, label: str) -> None:
    schema = _read_json(schema_path, "JSON schema")
    if not isinstance(schema, dict):
        raise AtlasQueryError(f"{schema_path} is not a JSON schema object")
    try:
        catalog._validate_schema_definition(schema, schema_path.name)
        catalog._validate_schema_value(value, schema, label)
    except catalog.CatalogError as exc:
        raise AtlasQueryError(f"invalid {label}: {exc}") from exc


def load_questions(path: Path = DEFAULT_QUESTIONS) -> dict[str, dict[str, Any]]:
    """Load question definitions with unique stable IDs."""

    value = _read_json(path, "Atlas question definitions")
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise AtlasQueryError("Atlas question definitions must be an array of objects")
    result: dict[str, dict[str, Any]] = {}
    for row in value:
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise AtlasQueryError("Atlas question definition has no stable id")
        if identifier in result:
            raise AtlasQueryError(
                f"Atlas question definition id is duplicated: {identifier}"
            )
        result[identifier] = row
    return result


def load_capability_policy(
    path: Path = DEFAULT_CAPABILITY_POLICY,
) -> dict[str, Any]:
    """Load and semantically validate the target capability policy."""

    value = _read_json(path, "Atlas capability policy")
    if not isinstance(value, dict):
        raise AtlasQueryError("Atlas capability policy must be an object")
    _validate_schema(value, CAPABILITY_POLICY_SCHEMA, path.name)
    if value["policy_id"] != CAPABILITY_POLICY_ID:
        raise AtlasQueryError("Atlas capability policy id is unsupported")

    admitted = value["admitted_target_kinds"]
    selector_rows = value["selector_policies"]
    selectors = {row["kind"]: row for row in selector_rows}
    if len(selectors) != len(selector_rows) or set(selectors) != set(admitted):
        raise AtlasQueryError(
            "Atlas selector policies must define every admitted target kind once"
        )
    for row in selector_rows:
        if row["key_kinds"] != sorted(row["key_kinds"]):
            raise AtlasQueryError(
                f"Atlas selector key kinds are not canonical: {row['kind']}"
            )

    section_rows = value["section_policies"]
    sections = {row["section"]: row for row in section_rows}
    if len(sections) != len(section_rows):
        raise AtlasQueryError("Atlas section capability policies are duplicated")
    for row in section_rows:
        if not set(row["target_kinds"]).issubset(admitted):
            raise AtlasQueryError(
                f"Atlas section admits an unknown target kind: {row['section']}"
            )
        if not set(row["required_authorities"]).isdisjoint(
            row["optional_authorities"]
        ):
            raise AtlasQueryError(
                f"Atlas section authority sets overlap: {row['section']}"
            )
    return value


@dataclass(frozen=True, order=True)
class QueryScope:
    """One exact normalized profile and physical-side scope."""

    profile: str
    physical_side: str

    def to_dict(self) -> dict[str, str]:
        return {
            "profile": self.profile,
            "physical_side": self.physical_side,
        }


@dataclass(frozen=True)
class ValidatedQuery:
    """A query instance bound to its question and capability policy."""

    instance: dict[str, Any]
    question: dict[str, Any]
    policy: dict[str, Any]
    selector_policy: dict[str, Any] | None
    scopes: tuple[QueryScope, ...]

    @property
    def substantive_sections(self) -> tuple[str, ...]:
        metadata = set(self.policy["metadata_sections"])
        return tuple(
            field
            for field in self.question["required_fields"]
            if field not in metadata
        )


@dataclass(frozen=True)
class ResolvedQueryTarget:
    """One candidate target retained with its complete runtime record."""

    scope: QueryScope
    node: dict[str, Any]

    def to_dict(self) -> dict[str, str]:
        return {
            "profile": self.scope.profile,
            "physical_side": self.scope.physical_side,
            "node_id": str(self.node["id"]),
            "node_kind": str(self.node["kind"]),
        }


@dataclass(frozen=True)
class QueryResolution:
    """A deterministic exact, gap, missing, ambiguity, or support result."""

    status: str
    targets: tuple[ResolvedQueryTarget, ...]
    profile_gaps: tuple[QueryScope, ...]
    detail: str

    @property
    def candidate_count(self) -> int:
        return len(self.targets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate_count": self.candidate_count,
            "targets": [target.to_dict() for target in self.targets],
            "profile_gaps": [scope.to_dict() for scope in self.profile_gaps],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AuthorityObservation:
    """One caller-supplied non-runtime authority observation."""

    basis: str
    status: str
    evidence_ids: tuple[str, ...]
    detail: str
    evidence_records: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.basis not in AUTHORITY_BASES:
            raise AtlasQueryError(
                f"Atlas authority basis is unsupported: {self.basis}"
            )
        if self.status not in AUTHORITY_STATUSES - {"not-requested"}:
            raise AtlasQueryError(
                f"Atlas authority observation status is invalid: {self.status}"
            )
        if (
            not isinstance(self.evidence_ids, tuple)
            or self.evidence_ids != tuple(sorted(set(self.evidence_ids)))
            or any(
                not isinstance(identifier, str) or not identifier
                for identifier in self.evidence_ids
            )
        ):
            raise AtlasQueryError(
                "Atlas authority observation evidence ids must be unique, "
                "nonempty strings in canonical order"
            )
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise AtlasQueryError(
                "Atlas authority observation detail must be nonempty"
            )
        if self.status in {"available", "ambiguous"} and not self.evidence_ids:
            raise AtlasQueryError(
                f"Atlas {self.status} authority observation requires evidence"
            )
        if (
            self.status in {"unavailable", "not-applicable"}
            and self.evidence_ids
        ):
            raise AtlasQueryError(
                f"Atlas {self.status} authority observation cannot cite evidence"
            )
        if (
            not isinstance(self.evidence_records, tuple)
            or any(
                not isinstance(row, dict)
                or set(row)
                != {
                    "id",
                    "basis",
                    "authority",
                    "record_kind",
                    "record",
                }
                or row["basis"] != self.basis
                or not isinstance(row["record"], dict)
                for row in self.evidence_records
            )
            or tuple(
                row["id"]
                for row in self.evidence_records
            )
            != self.evidence_ids
        ):
            raise AtlasQueryError(
                "Atlas authority evidence records must resolve every evidence "
                "id once in canonical order"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "detail": self.detail,
        }


def _query_scopes(instance: dict[str, Any]) -> tuple[QueryScope, ...]:
    scopes = tuple(
        QueryScope(row["profile"], row["physical_side"])
        for row in instance["scopes"]
    )
    if scopes != tuple(sorted(scopes)):
        raise AtlasQueryError(
            "Atlas query scopes must be sorted by profile and physical side"
        )
    return scopes


def validate_query_instance(
    instance: dict[str, Any],
    *,
    question_path: Path = DEFAULT_QUESTIONS,
    policy_path: Path = DEFAULT_CAPABILITY_POLICY,
) -> ValidatedQuery:
    """Validate schema, hashes, exact selector policy, and question meaning."""

    if not isinstance(instance, dict):
        raise AtlasQueryError("Atlas query instance must be an object")
    _validate_schema(instance, QUERY_INSTANCE_SCHEMA, "Atlas query instance")
    if instance["format"] != QUERY_FORMAT:
        raise AtlasQueryError("Atlas query instance format is unsupported")
    if instance["query_policy_id"] != QUERY_POLICY_ID:
        raise AtlasQueryError("Atlas query policy is unsupported")

    questions = load_questions(question_path)
    question = questions.get(instance["question_id"])
    if question is None or question.get("status") != "active":
        raise AtlasQueryError(
            "Atlas query question must resolve to one active definition: "
            + str(instance["question_id"])
        )
    expected_question_hash = question_definition_sha256(question)
    if instance["question_definition_sha256"] != expected_question_hash:
        raise AtlasQueryError(
            "Atlas query question definition hash differs from the active contract"
        )
    requirements = instance["authority_requirements"]
    for name in ("required", "optional"):
        if requirements[name] != sorted(requirements[name]):
            raise AtlasQueryError(
                f"Atlas query {name} authorities must be sorted"
            )
    if not set(requirements["required"]).isdisjoint(requirements["optional"]):
        raise AtlasQueryError(
            "Atlas query required and optional authorities must be disjoint"
        )
    question_bases = set(question["required_evidence_bases"])
    unsupported_bases = question_bases - set(AUTHORITY_BASES)
    if unsupported_bases:
        raise AtlasQueryError(
            "Atlas question requires unsupported authority bases: "
            + ", ".join(sorted(unsupported_bases))
        )
    if not question_bases.issubset(requirements["required"]):
        raise AtlasQueryError(
            "Atlas query required authorities omit question-required bases: "
            + ", ".join(
                sorted(question_bases - set(requirements["required"]))
            )
        )

    policy = load_capability_policy(policy_path)
    if instance["capability_policy_id"] != policy["policy_id"]:
        raise AtlasQueryError(
            "Atlas query capability policy id differs from the active contract"
        )
    expected_policy_hash = capability_policy_sha256(policy)
    if instance["capability_policy_sha256"] != expected_policy_hash:
        raise AtlasQueryError(
            "Atlas query capability policy hash differs from the active contract"
        )
    expected_query_id = query_instance_id(instance)
    if instance["query_instance_id"] != expected_query_id:
        raise AtlasQueryError(
            "Atlas query instance id differs from its canonical semantic fields"
        )
    selector_kind = instance["selector"]["kind"]
    selector_policy = next(
        (
            row
            for row in policy["selector_policies"]
            if row["kind"] == selector_kind
        ),
        None,
    )
    if selector_policy is not None:
        selected_key_kind = instance["selector"]["key_kind"]
        if selected_key_kind not in selector_policy["key_kinds"]:
            raise AtlasQueryError(
                "Atlas selector key kind is not admitted for "
                f"{selector_kind}: {selected_key_kind}"
            )

    sections = {
        row["section"]: row
        for row in policy["section_policies"]
    }
    metadata = set(policy["metadata_sections"])
    missing_section_policies = sorted(
        field
        for field in question["required_fields"]
        if field not in metadata and field not in sections
    )
    if missing_section_policies:
        raise AtlasQueryError(
            "Atlas capability policy omits question sections: "
            + ", ".join(missing_section_policies)
        )

    return ValidatedQuery(
        instance=instance,
        question=question,
        policy=policy,
        selector_policy=selector_policy,
        scopes=_query_scopes(instance),
    )


def selector_capability(validated: ValidatedQuery) -> dict[str, Any]:
    """Report exact public-kind, runtime-kind, key, and scope admission."""

    if not isinstance(validated, ValidatedQuery):
        raise AtlasQueryError("selector capability requires a validated query")
    selector = validated.instance["selector"]
    policy = validated.selector_policy
    return {
        "status": "admitted" if policy is not None else "unsupported-kind",
        "kind": selector["kind"],
        "runtime_kind": None if policy is None else policy["runtime_kind"],
        "selected_key_kind": selector["key_kind"],
        "admitted_key_kinds": (
            [] if policy is None else list(policy["key_kinds"])
        ),
        "scopes": [scope.to_dict() for scope in validated.scopes],
    }


def _node_scope(node: dict[str, Any]) -> tuple[str, str, str]:
    scope = node.get("scope")
    if not isinstance(scope, dict):
        raise AtlasQueryError(
            f"resolved runtime target has no scope object: {node.get('id')}"
        )
    profile = scope.get("profile")
    physical_side = scope.get("physical_side")
    snapshot_id = scope.get("snapshot_id")
    if not all(
        isinstance(value, str) and value
        for value in (profile, physical_side, snapshot_id)
    ):
        raise AtlasQueryError(
            f"resolved runtime target has an invalid scope: {node.get('id')}"
        )
    return profile, physical_side, snapshot_id


def _resolution_detail(status: str, capability_policy_id: str) -> str:
    if status == "unsupported-kind":
        return (
            "target kind has no resolver in "
            + capability_policy_id
        )
    if status == "ambiguous":
        return (
            "the exact key resolves to multiple nodes in at least one "
            "requested profile scope"
        )
    if status == "missing":
        return "the exact key is absent from every requested profile scope"
    if status == "resolved-with-profile-gaps":
        return (
            "one exact target resolved in each observed scope and remained "
            "absent from other named scopes"
        )
    if status == "resolved":
        return "one exact target resolved in every requested profile scope"
    raise AtlasQueryError(f"Atlas resolution status is unsupported: {status}")


def resolve_validated_query(
    reader: RuntimeGraphReader,
    validated: ValidatedQuery,
) -> QueryResolution:
    """Resolve all exact candidates without selecting an ambiguity."""

    if not isinstance(reader, RuntimeGraphReader):
        raise AtlasQueryError(
            "Atlas exact resolution requires an open RuntimeGraphReader"
        )
    reader.connection
    if not isinstance(validated, ValidatedQuery):
        raise AtlasQueryError("Atlas exact resolution requires a validated query")
    if validated.selector_policy is None:
        return QueryResolution(
            status="unsupported-kind",
            targets=(),
            profile_gaps=(),
            detail=_resolution_detail(
                "unsupported-kind",
                str(validated.policy["policy_id"]),
            ),
        )

    selector = validated.instance["selector"]
    runtime_kind = validated.selector_policy["runtime_kind"]
    snapshot_id = validated.instance["snapshot_id"]
    targets: list[ResolvedQueryTarget] = []
    gaps: list[QueryScope] = []
    ambiguous = False
    for scope in validated.scopes:
        try:
            matches = reader.resolve_exact(
                NodeSelector.by_key(
                    selector["key_kind"],
                    selector["key"],
                    kind=runtime_kind,
                    profile=scope.profile,
                    physical_side=scope.physical_side,
                )
            )
        except RuntimeGraphQueryError as exc:
            raise AtlasQueryError(f"Atlas exact target resolution failed: {exc}") from exc
        if not matches:
            gaps.append(scope)
            continue
        if len(matches) > 1:
            ambiguous = True
        for node in matches:
            profile, physical_side, observed_snapshot = _node_scope(node)
            if (
                profile != scope.profile
                or physical_side != scope.physical_side
            ):
                raise AtlasQueryError(
                    "Atlas exact target escaped its requested profile scope: "
                    + str(node.get("id"))
                )
            if observed_snapshot != snapshot_id:
                raise AtlasQueryError(
                    "Atlas exact target is bound to a different snapshot: "
                    + str(node.get("id"))
                )
            if node.get("kind") != runtime_kind:
                raise AtlasQueryError(
                    "Atlas exact target kind differs from selector policy: "
                    + str(node.get("id"))
                )
            targets.append(ResolvedQueryTarget(scope, node))

    resolved_targets = tuple(targets)
    profile_gaps = tuple(gaps)
    if ambiguous:
        status = "ambiguous"
    elif not resolved_targets:
        status = "missing"
    elif profile_gaps:
        status = "resolved-with-profile-gaps"
    else:
        status = "resolved"
    detail = _resolution_detail(status, str(validated.policy["policy_id"]))
    return QueryResolution(
        status=status,
        targets=resolved_targets,
        profile_gaps=profile_gaps,
        detail=detail,
    )


def resolve_query_instance(
    reader: RuntimeGraphReader,
    instance: dict[str, Any],
    *,
    question_path: Path = DEFAULT_QUESTIONS,
    policy_path: Path = DEFAULT_CAPABILITY_POLICY,
) -> tuple[ValidatedQuery, QueryResolution]:
    """Validate and resolve a caller-supplied Atlas query instance."""

    validated = validate_query_instance(
        instance,
        question_path=question_path,
        policy_path=policy_path,
    )
    return validated, resolve_validated_query(reader, validated)


def default_traversal_options() -> dict[str, Any]:
    """Return the explicit traversal values used by the V1 adapter."""

    return {
        "route": {
            "max_depth": 8,
            "max_routes": 50,
            "max_alternatives_per_slot": 25,
            "include_chanced_outputs": True,
            "include_procedural_rules": True,
            "max_visited_nodes": 10000,
        },
        "recycling": {
            "max_depth": 4,
            "max_operations": 250,
            "max_consumers_per_target": 25,
            "max_output_alternatives_per_slot": 25,
            "max_visited_targets": 10000,
        },
        "construction": {
            "max_depth": 8,
            "max_routes": 250,
            "max_alternatives_per_slot": 25,
            "max_visited_nodes": 10000,
            "max_machine_roots": 250,
        },
    }


def _scope_documents(scope_values: Iterable[str]) -> list[dict[str, str]]:
    scopes: list[QueryScope] = []
    for value in scope_values:
        parts = value.split(":")
        if len(parts) != 2 or parts[1] not in PROFILE_SIDES.get(parts[0], set()):
            raise AtlasQueryError(
                "Atlas V1 scope must be a supported PROFILE:PHYSICAL_SIDE pair: "
                + value
            )
        scopes.append(QueryScope(parts[0], parts[1]))
    normalized = sorted(set(scopes))
    if not normalized:
        raise AtlasQueryError("Atlas V1 adapter requires at least one scope")
    if len(normalized) != len(scopes):
        raise AtlasQueryError("Atlas V1 adapter scopes must not be duplicated")
    return [scope.to_dict() for scope in normalized]


def build_v1_query_instance(
    question: dict[str, Any],
    *,
    snapshot_id: str,
    scope_values: Iterable[str],
    traversal_options: dict[str, Any] | None = None,
    policy_path: Path = DEFAULT_CAPABILITY_POLICY,
) -> dict[str, Any]:
    """Materialize an explicit query instance from one V1 question."""

    if not isinstance(question, dict):
        raise AtlasQueryError("Atlas V1 adapter requires a question object")
    selector = question.get("selector")
    if not isinstance(selector, dict):
        raise AtlasQueryError("Atlas V1 question has no selector")
    kind = selector.get("kind")
    key_kind = V1_PRIMARY_KEY_KINDS.get(kind)
    if key_kind is None:
        raise AtlasQueryError(
            f"Atlas V1 selector kind has no declared primary key: {kind}"
        )
    required = sorted(set(question.get("required_evidence_bases", [])))
    unsupported = set(required) - set(AUTHORITY_BASES)
    if unsupported:
        raise AtlasQueryError(
            "Atlas V1 question has unsupported evidence bases: "
            + ", ".join(sorted(unsupported))
        )
    policy = load_capability_policy(policy_path)
    instance: dict[str, Any] = {
        "schema_version": 1,
        "format": QUERY_FORMAT,
        "query_instance_id": "",
        "question_id": question["id"],
        "question_definition_sha256": question_definition_sha256(question),
        "selector": {
            "kind": kind,
            "key_kind": key_kind,
            "key": selector["key"],
        },
        "snapshot_id": snapshot_id,
        "scopes": _scope_documents(scope_values),
        "traversal_options": (
            default_traversal_options()
            if traversal_options is None
            else traversal_options
        ),
        "authority_requirements": {
            "required": required,
            "optional": sorted(set(AUTHORITY_BASES) - set(required)),
        },
        "query_policy_id": QUERY_POLICY_ID,
        "capability_policy_id": policy["policy_id"],
        "capability_policy_sha256": capability_policy_sha256(policy),
    }
    instance["query_instance_id"] = query_instance_id(instance)
    return instance


def question_by_id(
    question_id: str,
    path: Path = DEFAULT_QUESTIONS,
) -> dict[str, Any]:
    """Return exactly one active question definition."""

    question = load_questions(path).get(question_id)
    if question is None or question.get("status") != "active":
        raise AtlasQueryError(
            f"Atlas question must resolve to one active definition: {question_id}"
        )
    return question


def capability_policy_sections(
    validated: ValidatedQuery,
) -> tuple[dict[str, Any], ...]:
    """Return required section policies in question-definition order."""

    if not isinstance(validated, ValidatedQuery):
        raise AtlasQueryError(
            "Atlas capability sections require a validated query"
        )
    rows = {
        row["section"]: row
        for row in validated.policy["section_policies"]
    }
    return tuple(rows[section] for section in validated.substantive_sections)


def target_records(
    resolution: QueryResolution,
) -> Sequence[dict[str, Any]]:
    """Expose immutable-order candidate records for later authority projection."""

    if not isinstance(resolution, QueryResolution):
        raise AtlasQueryError("Atlas target records require a query resolution")
    return tuple(target.node for target in resolution.targets)


def _requested_authorities(validated: ValidatedQuery) -> set[str]:
    requirements = validated.instance["authority_requirements"]
    return set(requirements["required"]) | set(requirements["optional"])


def _runtime_authority_row(
    basis: str,
    validated: ValidatedQuery,
    resolution: QueryResolution,
) -> dict[str, Any]:
    requested = basis in _requested_authorities(validated)
    if not requested:
        return {
            "basis": basis,
            "status": "not-requested",
            "evidence_ids": [],
            "detail": "the query instance did not request this authority",
        }

    presentation = basis == "runtime-presentation"
    evidence_ids = sorted(
        {
            str(target.node["id"])
            for target in resolution.targets
            if (
                target.scope.profile == "CLIENT_JEI_FINAL_STATE"
            )
            == presentation
        }
    )
    relevant_scope_requested = any(
        (scope.profile == "CLIENT_JEI_FINAL_STATE") == presentation
        for scope in validated.scopes
    )
    candidate_counts: dict[QueryScope, int] = {}
    for target in resolution.targets:
        if (target.scope.profile == "CLIENT_JEI_FINAL_STATE") == presentation:
            candidate_counts[target.scope] = (
                candidate_counts.get(target.scope, 0) + 1
            )
    basis_ambiguous = any(
        count > 1
        for count in candidate_counts.values()
    )
    if resolution.status == "unsupported-kind":
        status = "unresolved"
        detail = "the target kind has no admitted exact runtime resolver"
    elif basis_ambiguous:
        status = "ambiguous"
        detail = (
            "exact runtime candidates remain ambiguous in at least one "
            "requested scope"
        )
    elif evidence_ids:
        status = "available"
        detail = (
            "exact target evidence is available from requested "
            + ("presentation" if presentation else "mechanical")
            + " runtime scopes"
        )
    elif relevant_scope_requested:
        status = "not-observed"
        detail = (
            "the requested "
            + ("presentation" if presentation else "mechanical")
            + " runtime scopes contain no exact target observation"
        )
    else:
        status = "unavailable"
        detail = (
            "the query requests this authority but names no compatible "
            + ("presentation" if presentation else "mechanical")
            + " runtime scope"
        )
    return {
        "basis": basis,
        "status": status,
        "evidence_ids": evidence_ids,
        "detail": detail,
    }


def project_authority_availability(
    validated: ValidatedQuery,
    resolution: QueryResolution,
    observations: Mapping[str, AuthorityObservation] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Project all authority states without turning absence into negative proof."""

    if not isinstance(validated, ValidatedQuery):
        raise AtlasQueryError(
            "Atlas authority projection requires a validated query"
        )
    if not isinstance(resolution, QueryResolution):
        raise AtlasQueryError(
            "Atlas authority projection requires a query resolution"
        )
    supplied = {} if observations is None else dict(observations)
    if any(
        basis not in AUTHORITY_BASES
        or not isinstance(observation, AuthorityObservation)
        or observation.basis != basis
        for basis, observation in supplied.items()
    ):
        raise AtlasQueryError(
            "Atlas supplemental authority observations are invalid"
        )

    requested = _requested_authorities(validated)
    rows: list[dict[str, Any]] = []
    for basis in AUTHORITY_BASES:
        if basis in {"runtime-mechanics", "runtime-presentation"}:
            if basis in supplied:
                raise AtlasQueryError(
                    "Atlas runtime authority is derived from exact target "
                    f"records, not supplied separately: {basis}"
                )
            rows.append(
                _runtime_authority_row(basis, validated, resolution)
            )
            continue
        if basis not in requested:
            rows.append(
                {
                    "basis": basis,
                    "status": "not-requested",
                    "evidence_ids": [],
                    "detail": (
                        "the query instance did not request this authority"
                    ),
                }
            )
            continue
        observation = supplied.get(basis)
        if observation is None:
            rows.append(
                {
                    "basis": basis,
                    "status": "unavailable",
                    "evidence_ids": [],
                    "detail": (
                        "the requested authority input was not supplied: "
                        + basis
                    ),
                }
            )
        else:
            rows.append(observation.to_dict())
    return tuple(rows)


def _evidence_binding(row: dict[str, Any]) -> dict[str, Any]:
    binding = {
        "id": row["id"],
        "basis": row["basis"],
        "authority": row["authority"],
        "record_kind": row["record_kind"],
        "record": row["record"],
    }
    binding["record_sha256"] = hashlib.sha256(
        canonical_json_payload(binding)
    ).hexdigest()
    return binding


def project_evidence_bindings(
    resolution: QueryResolution,
    observations: Mapping[str, AuthorityObservation] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Bind every projected evidence ID to one immutable complete record."""

    rows: list[dict[str, Any]] = []
    for target in resolution.targets:
        rows.append(
            {
                "id": target.node["id"],
                "basis": (
                    "runtime-presentation"
                    if target.scope.profile == "CLIENT_JEI_FINAL_STATE"
                    else "runtime-mechanics"
                ),
                "authority": "normalized-runtime-graph",
                "record_kind": "runtime-node",
                "record": target.node,
            }
        )
    for observation in (
        {} if observations is None else observations
    ).values():
        rows.extend(observation.evidence_records)
    rows.sort(key=lambda row: row["id"])
    if len({row["id"] for row in rows}) != len(rows):
        raise AtlasQueryError(
            "Atlas evidence bindings contain duplicate evidence ids"
        )
    return tuple(_evidence_binding(row) for row in rows)


def project_capabilities(
    validated: ValidatedQuery,
    resolution: QueryResolution,
    authorities: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Project required question sections through kind and authority policy."""

    if not isinstance(validated, ValidatedQuery):
        raise AtlasQueryError(
            "Atlas capability projection requires a validated query"
        )
    if not isinstance(resolution, QueryResolution):
        raise AtlasQueryError(
            "Atlas capability projection requires a query resolution"
        )
    authority_by_basis = {
        row.get("basis"): row
        for row in authorities
        if isinstance(row, dict)
    }
    if (
        len(authority_by_basis) != len(AUTHORITY_BASES)
        or tuple(row.get("basis") for row in authorities) != AUTHORITY_BASES
    ):
        raise AtlasQueryError(
            "Atlas authority rows must cover every basis once in canonical order"
        )
    section_policies = {
        row["section"]: row
        for row in validated.policy["section_policies"]
    }
    target_kind = validated.instance["selector"]["kind"]
    identity_evidence = sorted(
        {
            identifier
            for row in authorities
            for identifier in row["evidence_ids"]
        }
    )
    rows: list[dict[str, Any]] = []
    for section in validated.substantive_sections:
        policy = section_policies[section]
        required = sorted(policy["required_authorities"])
        available = [
            basis
            for basis in required
            if authority_by_basis[basis]["status"] == "available"
        ]
        missing = sorted(set(required) - set(available))
        unresolved_authorities = [
            basis
            for basis in required
            if authority_by_basis[basis]["status"]
            in {"ambiguous", "unresolved"}
        ]
        if target_kind not in policy["target_kinds"]:
            status = "not-applicable"
            reason = "target-kind-not-applicable"
            evidence_ids: list[str] = []
        elif resolution.status not in {
            "resolved",
            "resolved-with-profile-gaps",
        }:
            status = "unresolved"
            reason = "identity-unresolved"
            evidence_ids = identity_evidence
        elif unresolved_authorities:
            status = "unresolved"
            reason = "policy-unresolved"
            evidence_ids = sorted(
                {
                    identifier
                    for basis in set(available)
                    | set(unresolved_authorities)
                    for identifier in authority_by_basis[basis][
                        "evidence_ids"
                    ]
                }
            )
        elif missing:
            status = "unavailable"
            reason = "required-authority-unavailable"
            evidence_ids = sorted(
                {
                    identifier
                    for basis in available
                    for identifier in authority_by_basis[basis][
                        "evidence_ids"
                    ]
                }
            )
        else:
            status = "supported"
            reason = "available"
            evidence_ids = sorted(
                {
                    identifier
                    for basis in required
                    for identifier in authority_by_basis[basis][
                        "evidence_ids"
                    ]
                }
            )
        rows.append(
            {
                "section": section,
                "question_id": validated.instance["question_id"],
                "target_kind": target_kind,
                "status": status,
                "required_authorities": required,
                "available_authorities": available,
                "missing_authorities": missing,
                "evidence_ids": evidence_ids,
                "reason_code": reason,
            }
        )
    return tuple(rows)


def preflight_result_status(
    resolution: QueryResolution,
    capabilities: Sequence[dict[str, Any]],
    *,
    required_authority_statuses: Sequence[str] = (),
) -> str:
    """Derive one aggregate preflight status from exact subordinate states."""

    if resolution.status == "unsupported-kind":
        return "unsupported-query"
    if resolution.status == "ambiguous":
        return "ambiguous"
    if resolution.status == "missing":
        return "unresolved-identity"
    content = [
        row
        for row in capabilities
        if row.get("section") != "target"
    ]
    if content and all(
        row.get("status") == "not-applicable"
        for row in content
    ):
        return "not-applicable"
    if any(
        status != "available"
        for status in required_authority_statuses
    ):
        return "unresolved-evidence"
    if any(
        row.get("status") in {"unavailable", "unresolved"}
        for row in capabilities
    ):
        return "unresolved-evidence"
    return "resolved"


def build_query_preflight(
    validated: ValidatedQuery,
    resolution: QueryResolution,
    observations: Mapping[str, AuthorityObservation] | None = None,
    *,
    question_path: Path = DEFAULT_QUESTIONS,
    policy_path: Path = DEFAULT_CAPABILITY_POLICY,
) -> dict[str, Any]:
    """Build one deterministic schema-valid query and capability preflight."""

    authorities = project_authority_availability(
        validated,
        resolution,
        observations,
    )
    capabilities = project_capabilities(
        validated,
        resolution,
        authorities,
    )
    result = {
        "schema_version": 1,
        "format": "susy-atlas-query-result-v1",
        "request_id": validated.instance["query_instance_id"],
        "query_instance": validated.instance,
        "resolution": resolution.to_dict(),
        "authorities": list(authorities),
        "evidence": list(
            project_evidence_bindings(resolution, observations)
        ),
        "capabilities": list(capabilities),
        "result_status": preflight_result_status(
            resolution,
            capabilities,
            required_authority_statuses=[
                row["status"]
                for row in authorities
                if row["basis"]
                in set(
                    validated.instance["authority_requirements"][
                        "required"
                    ]
                )
            ],
        ),
        "answer": None,
    }
    validate_query_result(
        result,
        question_path=question_path,
        policy_path=policy_path,
    )
    return result


def _expected_capability_status(
    *,
    target_kind: str,
    resolution_status: str,
    policy: dict[str, Any],
    missing_authorities: Sequence[str],
    unresolved_authorities: Sequence[str],
) -> tuple[str, str]:
    if target_kind not in policy["target_kinds"]:
        return "not-applicable", "target-kind-not-applicable"
    if resolution_status not in {"resolved", "resolved-with-profile-gaps"}:
        return "unresolved", "identity-unresolved"
    if unresolved_authorities:
        return "unresolved", "policy-unresolved"
    if missing_authorities:
        return "unavailable", "required-authority-unavailable"
    return "supported", "available"


def validate_query_result(
    result: dict[str, Any],
    *,
    question_path: Path = DEFAULT_QUESTIONS,
    policy_path: Path = DEFAULT_CAPABILITY_POLICY,
) -> None:
    """Validate query-result schema and recomputable status invariants."""

    if not isinstance(result, dict):
        raise AtlasQueryError("Atlas query result must be an object")
    _validate_schema(result, QUERY_RESULT_SCHEMA, "Atlas query result")
    validated = validate_query_instance(
        result["query_instance"],
        question_path=question_path,
        policy_path=policy_path,
    )
    if result["request_id"] != validated.instance["query_instance_id"]:
        raise AtlasQueryError(
            "Atlas query result request id differs from its query instance"
        )

    resolution = result["resolution"]
    targets = resolution["targets"]
    gaps = resolution["profile_gaps"]
    if resolution["candidate_count"] != len(targets):
        raise AtlasQueryError(
            "Atlas query resolution candidate count differs from its targets"
        )
    scope_keys = {
        (scope.profile, scope.physical_side)
        for scope in validated.scopes
    }
    target_scope_keys = [
        (row["profile"], row["physical_side"])
        for row in targets
    ]
    gap_scope_keys = [
        (row["profile"], row["physical_side"])
        for row in gaps
    ]
    if (
        not set(target_scope_keys).issubset(scope_keys)
        or not set(gap_scope_keys).issubset(scope_keys)
        or set(target_scope_keys).intersection(gap_scope_keys)
        or gap_scope_keys != sorted(set(gap_scope_keys))
        or targets
        != sorted(
            targets,
            key=lambda row: (
                row["profile"],
                row["physical_side"],
                row["node_id"],
            ),
        )
    ):
        raise AtlasQueryError(
            "Atlas query resolution scopes or targets escape, overlap, "
            "duplicate, or differ in order"
        )
    runtime_kind = (
        None
        if validated.selector_policy is None
        else validated.selector_policy["runtime_kind"]
    )
    if any(row["node_kind"] != runtime_kind for row in targets):
        raise AtlasQueryError(
            "Atlas query result target kind differs from selector policy"
        )
    counts: dict[tuple[str, str], int] = {}
    for key in target_scope_keys:
        counts[key] = counts.get(key, 0) + 1
    status = resolution["status"]
    semantic_resolution = QueryResolution(
        status=status,
        targets=tuple(
            ResolvedQueryTarget(
                QueryScope(row["profile"], row["physical_side"]),
                {
                    "id": row["node_id"],
                    "kind": row["node_kind"],
                },
            )
            for row in targets
        ),
        profile_gaps=tuple(
            QueryScope(row["profile"], row["physical_side"])
            for row in gaps
        ),
        detail=resolution["detail"],
    )
    if resolution["detail"] != _resolution_detail(
        status,
        str(validated.policy["policy_id"]),
    ):
        raise AtlasQueryError(
            "Atlas query resolution detail differs from its exact status"
        )
    covers_requested_scopes = (
        set(target_scope_keys) | set(gap_scope_keys)
    ) == scope_keys
    if status == "unsupported-kind":
        structurally_valid = (
            validated.selector_policy is None
            and not targets
            and not gaps
        )
    elif status == "ambiguous":
        structurally_valid = (
            covers_requested_scopes
            and any(count > 1 for count in counts.values())
        )
    elif status == "missing":
        structurally_valid = (
            not targets and set(gap_scope_keys) == scope_keys
        )
    elif status == "resolved-with-profile-gaps":
        structurally_valid = (
            bool(targets)
            and bool(gaps)
            and covers_requested_scopes
            and all(count == 1 for count in counts.values())
        )
    else:
        structurally_valid = (
            status == "resolved"
            and bool(targets)
            and not gaps
            and set(target_scope_keys) == scope_keys
            and all(count == 1 for count in counts.values())
        )
    if not structurally_valid:
        raise AtlasQueryError(
            "Atlas query resolution status differs from its exact candidates"
        )

    authorities = result["authorities"]
    if [row["basis"] for row in authorities] != list(AUTHORITY_BASES):
        raise AtlasQueryError(
            "Atlas authority rows are missing, duplicated, or unordered"
        )
    authority_by_basis = {
        row["basis"]: row
        for row in authorities
    }
    requested = _requested_authorities(validated)
    evidence = result["evidence"]
    if evidence != sorted(evidence, key=lambda row: row["id"]):
        raise AtlasQueryError(
            "Atlas evidence bindings differ from canonical id order"
        )
    evidence_by_id = {
        row["id"]: row
        for row in evidence
    }
    if len(evidence_by_id) != len(evidence):
        raise AtlasQueryError("Atlas evidence binding ids are duplicated")
    for row in evidence:
        semantic = {
            key: value
            for key, value in row.items()
            if key != "record_sha256"
        }
        expected_hash = hashlib.sha256(
            canonical_json_payload(semantic)
        ).hexdigest()
        if row["record_sha256"] != expected_hash:
            raise AtlasQueryError(
                "Atlas evidence record hash differs from its binding: "
                + row["id"]
            )
    authority_evidence_ids = {
        identifier
        for row in authorities
        for identifier in row["evidence_ids"]
    }
    if set(evidence_by_id) != authority_evidence_ids:
        raise AtlasQueryError(
            "Atlas evidence bindings do not close exactly over authority ids"
        )
    for row in authorities:
        basis = row["basis"]
        if (
            row["evidence_ids"]
            != sorted(set(row["evidence_ids"]))
            or any(
                evidence_by_id[identifier]["basis"] != basis
                for identifier in row["evidence_ids"]
            )
        ):
            raise AtlasQueryError(
                "Atlas authority evidence is noncanonical or bound to another "
                "basis: "
                + basis
            )
        if basis in requested and row["status"] == "not-requested":
            raise AtlasQueryError(
                "Atlas requested authority is marked not-requested: "
                + basis
            )
        if basis not in requested and row["status"] != "not-requested":
            raise AtlasQueryError(
                "Atlas unrequested authority is not marked not-requested: "
                + basis
            )
        if row["status"] in {"available", "ambiguous"} and not row[
            "evidence_ids"
        ]:
            raise AtlasQueryError(
                f"Atlas {row['status']} authority has no evidence: {basis}"
            )
        if (
            row["status"]
            in {"unavailable", "not-applicable", "not-requested"}
            and row["evidence_ids"]
        ):
            raise AtlasQueryError(
                f"Atlas {row['status']} authority cites evidence: {basis}"
            )
        if basis in {"runtime-mechanics", "runtime-presentation"}:
            expected_runtime_row = _runtime_authority_row(
                basis,
                validated,
                semantic_resolution,
            )
            if row != expected_runtime_row:
                raise AtlasQueryError(
                    "Atlas runtime authority differs from exact target "
                    "evidence: "
                    + basis
                )
    target_by_id = {
        row["node_id"]: row
        for row in targets
    }
    for identifier, binding in evidence_by_id.items():
        if binding["basis"] not in {
            "runtime-mechanics",
            "runtime-presentation",
        }:
            continue
        target = target_by_id.get(identifier)
        record = binding["record"]
        scope = record.get("scope", {})
        if (
            target is None
            or binding["authority"] != "normalized-runtime-graph"
            or binding["record_kind"] != "runtime-node"
            or record.get("id") != target["node_id"]
            or record.get("kind") != target["node_kind"]
            or scope.get("snapshot_id")
            != validated.instance["snapshot_id"]
            or scope.get("profile") != target["profile"]
            or scope.get("physical_side") != target["physical_side"]
        ):
            raise AtlasQueryError(
                "Atlas runtime evidence binding differs from its exact target: "
                + identifier
            )

    policies = {
        row["section"]: row
        for row in validated.policy["section_policies"]
    }
    capabilities = result["capabilities"]
    if [row["section"] for row in capabilities] != list(
        validated.substantive_sections
    ):
        raise AtlasQueryError(
            "Atlas capability rows differ from question-required sections"
        )
    all_authority_evidence = {
        identifier
        for row in authorities
        for identifier in row["evidence_ids"]
    }
    for row in capabilities:
        if (
            row["question_id"] != validated.instance["question_id"]
            or row["target_kind"]
            != validated.instance["selector"]["kind"]
        ):
            raise AtlasQueryError(
                "Atlas capability identity differs from its query instance"
            )
        policy = policies[row["section"]]
        required = sorted(policy["required_authorities"])
        available = [
            basis
            for basis in required
            if authority_by_basis[basis]["status"] == "available"
        ]
        missing = sorted(set(required) - set(available))
        unresolved_authorities = [
            basis
            for basis in required
            if authority_by_basis[basis]["status"]
            in {"ambiguous", "unresolved"}
        ]
        expected_status, expected_reason = _expected_capability_status(
            target_kind=row["target_kind"],
            resolution_status=status,
            policy=policy,
            missing_authorities=missing,
            unresolved_authorities=unresolved_authorities,
        )
        if row["target_kind"] not in policy["target_kinds"]:
            expected_evidence: list[str] = []
        elif status not in {"resolved", "resolved-with-profile-gaps"}:
            expected_evidence = sorted(all_authority_evidence)
        elif unresolved_authorities:
            expected_evidence = sorted(
                {
                    identifier
                    for basis in set(available)
                    | set(unresolved_authorities)
                    for identifier in authority_by_basis[basis][
                        "evidence_ids"
                    ]
                }
            )
        elif missing:
            expected_evidence = sorted(
                {
                    identifier
                    for basis in available
                    for identifier in authority_by_basis[basis][
                        "evidence_ids"
                    ]
                }
            )
        else:
            expected_evidence = sorted(
                {
                    identifier
                    for basis in required
                    for identifier in authority_by_basis[basis][
                        "evidence_ids"
                    ]
                }
            )
        if (
            row["required_authorities"] != required
            or row["available_authorities"] != available
            or row["missing_authorities"] != missing
            or row["status"] != expected_status
            or row["reason_code"] != expected_reason
            or row["evidence_ids"] != expected_evidence
        ):
            raise AtlasQueryError(
                "Atlas capability status or authority closure differs: "
                + row["section"]
            )
    expected_result_status = preflight_result_status(
        semantic_resolution,
        capabilities,
        required_authority_statuses=[
            authority_by_basis[basis]["status"]
            for basis in validated.instance["authority_requirements"][
                "required"
            ]
        ],
    )
    preflight_statuses = {
        "resolved",
        "ambiguous",
        "unresolved-identity",
        "unresolved-evidence",
        "not-applicable",
        "unsupported-query",
    }
    if result["result_status"] in preflight_statuses:
        if (
            result["result_status"] != expected_result_status
            or result["answer"] is not None
        ):
            raise AtlasQueryError(
                "Atlas preflight result status or answer differs from "
                "subordinate states"
            )
        return

    if result["result_status"] not in {
        "answered",
        "answered-with-profile-gap",
        "no-mechanical-route",
    }:
        raise AtlasQueryError("Atlas query result status is unsupported")
    answer = result["answer"]
    if not isinstance(answer, dict):
        raise AtlasQueryError(
            "Atlas composed query result must contain an answer object"
        )
    try:
        import workbench_atlas.corpus_bridge as corpus_bridge

        corpus_bridge.validate_answer(answer, validated.question)
    except corpus_bridge.CorpusBridgeError as exc:
        raise AtlasQueryError(
            f"Atlas composed answer is invalid: {exc}"
        ) from exc
    selector = validated.instance["selector"]
    answer_selector = answer.get("target", {}).get("selector")
    expected_scopes = [
        {
            "profile": row["profile"],
            "physical_side": row["physical_side"],
        }
        for row in answer.get("profile_scope", [])
    ]
    if (
        answer.get("question_id") != validated.instance["question_id"]
        or answer.get("snapshot_id") != validated.instance["snapshot_id"]
        or answer.get("result_status") != result["result_status"]
        or answer_selector
        != {
            "kind": selector["kind"],
            "key": selector["key"],
            "key_kind": selector["key_kind"],
        }
        or expected_scopes != validated.instance["scopes"]
        or any(row["status"] != "supported" for row in capabilities)
    ):
        raise AtlasQueryError(
            "Atlas composed answer identity, scope, status, or capability "
            "differs from its query result"
        )
    outer_target_ids = sorted(
        row["node_id"]
        for row in targets
    )
    nested_target_ids = answer.get("target", {}).get("runtime_node_ids")
    nested_runtime_ids = sorted(
        row.get("id")
        for row in answer.get("runtime", {}).get("resolved", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    )
    nested_gaps = answer.get("runtime", {}).get("profile_gaps")
    if (
        nested_target_ids != outer_target_ids
        or nested_runtime_ids != outer_target_ids
        or nested_gaps != gaps
    ):
        raise AtlasQueryError(
            "Atlas outer resolution differs from its composed runtime answer"
        )
    assertion_by_path = {
        row.get("path"): row
        for row in answer.get("assertions", [])
        if isinstance(row, dict)
    }
    for capability in capabilities:
        assertion = assertion_by_path.get("/" + capability["section"])
        if (
            assertion is None
            or assertion.get("status") != "supported"
            or not set(capability["evidence_ids"]).issubset(
                assertion.get("evidence_ids", [])
            )
        ):
            raise AtlasQueryError(
                "Atlas capability differs from its composed section "
                "assertion: "
                + capability["section"]
            )
    answer_evidence = {
        row.get("id")
        for row in answer.get("evidence", [])
        if isinstance(row, dict)
    }
    projected_evidence = {
        identifier
        for row in authorities
        for identifier in row["evidence_ids"]
    }
    if not projected_evidence.issubset(answer_evidence):
        raise AtlasQueryError(
            "Atlas authority availability cites evidence outside the answer"
        )
    nested_evidence = {
        row["id"]: row
        for row in answer["evidence"]
    }
    for identifier, binding in evidence_by_id.items():
        expected_binding = _evidence_binding(nested_evidence[identifier])
        if binding != expected_binding:
            raise AtlasQueryError(
                "Atlas evidence binding differs from its composed answer: "
                + identifier
            )
