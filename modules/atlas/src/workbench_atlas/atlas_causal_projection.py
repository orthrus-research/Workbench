#!/usr/bin/env python3

"""Project one validated causal result for player and developer audiences."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any, Sequence

from jsonschema import Draft202012Validator, SchemaError
if __package__:
    from . import atlas_causal_provenance_contract as provenance
    from .knowledge_catalog import CatalogError, read_json
    from .layout import EXAMPLE_ROOT, SCHEMA_ROOT
else:  # Direct module loading.
    import workbench_atlas.atlas_causal_provenance_contract as provenance
    from workbench_atlas.knowledge_catalog import CatalogError, read_json
    from workbench_atlas.layout import EXAMPLE_ROOT, SCHEMA_ROOT


FORMAT = "susy-atlas-causal-audience-projection-v1"
PROJECTION_PREFIX = "atlas-causal-audience-projection:sha256:"
PATH_PREFIX = "atlas-causal-audience-path:sha256:"
STEP_PREFIX = "atlas-causal-explanation-step:sha256:"
SCHEMA_PATH = SCHEMA_ROOT / "atlas-causal-audience-projection-v1.schema.json"
EXAMPLE_PATH = EXAMPLE_ROOT / "atlas-causal-audience-projection-example-v1.json"

SUMMARY_CODES = {
    "closed": "causal-path-closed",
    "partial": "causal-path-partial",
    "bounded": "causal-path-bounded",
    "unresolved": "causal-path-unresolved",
    "unavailable": "causal-evidence-unavailable",
}

PLAYER_SUMMARIES = {
    "closed": (
        "Atlas has an evidence-closed path from exact source or configuration "
        "to this final game record."
    ),
    "partial": (
        "Atlas can show part of how this final game record was constructed, "
        "but the listed evidence frontier is still open."
    ),
    "bounded": (
        "Atlas reached the stated query limit before every causal branch could "
        "be projected."
    ),
    "unresolved": (
        "Atlas has the exact final game record but no evidence-typed causal "
        "path to it in this result."
    ),
    "unavailable": (
        "Atlas cannot project the causal path because required authority is "
        "explicitly unavailable."
    ),
}

DEVELOPER_SUMMARIES = {
    "closed": (
        "The C01 result contains at least one exact, lifecycle-valid, "
        "evidence-closed path to the requested final runtime record."
    ),
    "partial": (
        "The C01 result retains one or more connected partial paths and exact "
        "open frontier reasons."
    ),
    "bounded": (
        "The C01 result is globally truncated at the identified request bound; "
        "retained paths do not imply enumeration completeness."
    ),
    "unresolved": (
        "The exact final runtime record is present, but this C01 result "
        "contains no closed causal path."
    ),
    "unavailable": (
        "The C01 result records required causal authority as unavailable."
    ),
}

PLAYER_ACTION_WORDING = {
    "declares": "The exact source declares the recorded object.",
    "registers": "The recorded step registers the resulting game state.",
    "loads_configuration": (
        "The selected configuration loads the recorded lifecycle step or state."
    ),
    "invokes": "The exact source or lifecycle step invokes the recorded operation.",
    "copies": "The recorded state is copied into the next exact state.",
    "transforms": "The recorded state is transformed into the next exact state.",
    "mutates": "The recorded state is changed into the next exact state.",
    "removes": "The recorded state is removed before the next exact state.",
    "replaces": "The recorded state is replaced by the next exact state.",
    "reconciles_to": (
        "The recorded identity is reconciled to the next exact identity."
    ),
    "observed_as_final": (
        "The resulting state is observed in the final runtime record."
    ),
    "derives_explanation": (
        "The final runtime record supports the separately derived explanation."
    ),
}


class AtlasCausalProjectionError(ValueError):
    """Raised when an audience projection is not an exact C01 derivation."""


def _content_id(prefix: str, value: dict[str, Any], field: str) -> str:
    return provenance.content_id(prefix, value, field)


def _final_node(result: dict[str, Any]) -> dict[str, Any]:
    final_identity = result["request"]["final_runtime_record"]
    matches = [
        item
        for item in result["nodes"]
        if item["node_class"] == "final-runtime-record"
        and item["identity"] == final_identity
    ]
    if len(matches) != 1:
        raise AtlasCausalProjectionError(
            "causal result lacks one exact final projection endpoint"
        )
    return matches[0]


def _developer_step(
    relation: dict[str, Any],
    *,
    position: int,
) -> dict[str, Any]:
    row = {
        "step_id": "",
        "position": position,
        "relation_id": relation["relation_id"],
        "predicate": relation["predicate"],
        "causal_strength": relation["causal_strength"],
        "lifecycle_stage_id": relation["lifecycle_stage_id"],
        "subject_node_id": relation["subject_node_id"],
        "object_node_id": relation["object_node_id"],
        "evidence_ids": copy.deepcopy(relation["evidence_ids"]),
    }
    row["step_id"] = _content_id(STEP_PREFIX, row, "step_id")
    return row


def _player_step(
    relation: dict[str, Any],
    *,
    position: int,
) -> dict[str, Any]:
    row = {
        "step_id": "",
        "position": position,
        "relation_id": relation["relation_id"],
        "action_code": relation["predicate"],
        "wording": PLAYER_ACTION_WORDING[relation["predicate"]],
        "causal_strength": relation["causal_strength"],
        "lifecycle_stage_id": relation["lifecycle_stage_id"],
        "subject_node_id": relation["subject_node_id"],
        "object_node_id": relation["object_node_id"],
        "evidence_ids": copy.deepcopy(relation["evidence_ids"]),
    }
    row["step_id"] = _content_id(STEP_PREFIX, row, "step_id")
    return row


def _developer_path(
    path: dict[str, Any],
    *,
    nodes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = {
        "projection_path_id": "",
        "path_id": path["path_id"],
        "closure_state": path["closure_state"],
        "open_reason_codes": copy.deepcopy(path["open_reason_codes"]),
        "nodes": [
            {
                "position": position,
                **copy.deepcopy(nodes[node_id]),
            }
            for position, node_id in enumerate(path["node_ids"])
        ],
        "steps": [
            _developer_step(relations[relation_id], position=position)
            for position, relation_id in enumerate(path["relation_ids"])
        ],
    }
    row["projection_path_id"] = _content_id(
        PATH_PREFIX,
        row,
        "projection_path_id",
    )
    return row


def _player_path(
    path: dict[str, Any],
    *,
    relations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = {
        "projection_path_id": "",
        "path_id": path["path_id"],
        "closure_state": path["closure_state"],
        "open_reason_codes": copy.deepcopy(path["open_reason_codes"]),
        "steps": [
            _player_step(relations[relation_id], position=position)
            for position, relation_id in enumerate(path["relation_ids"])
        ],
    }
    row["projection_path_id"] = _content_id(
        PATH_PREFIX,
        row,
        "projection_path_id",
    )
    return row


def _statements(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "statement_id": item["statement_id"],
            "kind": item["kind"],
            "subject_node_id": item["subject_node_id"],
            "bounded_wording": item["bounded_wording"],
            "evidence_ids": copy.deepcopy(item["authority_evidence_ids"]),
        }
        for item in result["negative_statements"]
    ]


def _limitations(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason_codes": copy.deepcopy(result["closure"]["reason_codes"]),
        "open_frontier_node_ids": copy.deepcopy(
            result["closure"]["open_frontier_node_ids"]
        ),
        "truncation": copy.deepcopy(result["truncation"]),
    }


def _project_validated(
    result: dict[str, Any],
) -> dict[str, Any]:
    nodes = {item["node_id"]: item for item in result["nodes"]}
    relations = {
        item["relation_id"]: item for item in result["relations"]
    }
    final = _final_node(result)
    evidence_ids = [item["evidence_id"] for item in result["evidence"]]
    developer_paths = [
        _developer_path(path, nodes=nodes, relations=relations)
        for path in result["paths"]
    ]
    player_paths = [
        _player_path(path, relations=relations)
        for path in result["paths"]
    ]
    statements = _statements(result)
    limitations = _limitations(result)
    status = result["status"]
    projection = {
        "schema_version": 1,
        "format": FORMAT,
        "projection_id": "",
        "source_result": copy.deepcopy(result),
        "binding": {
            "result_id": result["result_id"],
            "request_id": result["request"]["request_id"],
            "query_instance_id": result["request"]["query_instance_id"],
            "snapshot_id": result["request"]["snapshot_id"],
            "scope": copy.deepcopy(result["request"]["scope"]),
            "final_runtime_record": copy.deepcopy(
                result["request"]["final_runtime_record"]
            ),
            "final_node_id": final["node_id"],
            "policy_id": result["request"]["policy_id"],
            "policy_sha256": result["request"]["policy_sha256"],
        },
        "developer": {
            "audience": "developer",
            "summary_code": SUMMARY_CODES[status],
            "summary": DEVELOPER_SUMMARIES[status],
            "status": status,
            "paths": developer_paths,
            "statements": copy.deepcopy(statements),
            "limitations": copy.deepcopy(limitations),
            "evidence_ids": copy.deepcopy(evidence_ids),
        },
        "player": {
            "audience": "player",
            "summary_code": SUMMARY_CODES[status],
            "summary": PLAYER_SUMMARIES[status],
            "status": status,
            "final_runtime_node_id": final["identity"]["runtime_node_id"],
            "paths": player_paths,
            "statements": copy.deepcopy(statements),
            "limitations": copy.deepcopy(limitations),
            "evidence_ids": copy.deepcopy(evidence_ids),
        },
    }
    projection["projection_id"] = _content_id(
        PROJECTION_PREFIX,
        projection,
        "projection_id",
    )
    return projection


def _validate_schema(projection: dict[str, Any]) -> None:
    try:
        schema = read_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(projection),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            first = errors[0]
            location = ".".join(str(part) for part in first.absolute_path)
            raise AtlasCausalProjectionError(
                f"{FORMAT} schema violation at {location or '<root>'}: "
                f"{first.message}"
            )
    except (OSError, CatalogError, SchemaError) as exc:
        raise AtlasCausalProjectionError(
            f"causal audience projection schema failed: {exc}"
        ) from exc


def project(
    result: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build exact player and developer views of one C01 result."""

    if policy is None:
        policy = provenance.load_policy()
    try:
        provenance.validate_result(result, policy)
    except provenance.ProvenanceContractError as exc:
        raise AtlasCausalProjectionError(str(exc)) from exc
    projection = _project_validated(result)
    validate_projection(projection, policy)
    return projection


def validate_projection(
    projection: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute both audience views from the embedded C01 result."""

    if policy is None:
        policy = provenance.load_policy()
    _validate_schema(projection)
    try:
        provenance.validate_result(projection["source_result"], policy)
    except provenance.ProvenanceContractError as exc:
        raise AtlasCausalProjectionError(str(exc)) from exc
    expected = _project_validated(projection["source_result"])
    if projection != expected:
        raise AtlasCausalProjectionError(
            "causal audience projection differs from its exact C01 derivation"
        )
    return projection


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(provenance.canonical_json(value) + b"\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("project", help="build both audience views")
    run.add_argument("--result", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    check = subparsers.add_parser("check", help="validate one projection")
    check.add_argument("projection", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "check":
        validate_projection(read_json(arguments.projection))
        print(f"valid Atlas causal audience projection: {arguments.projection}")
        return 0
    projection = project(read_json(arguments.result))
    _write(arguments.output, projection)
    print(
        f"wrote {projection['projection_id']} "
        f"status={projection['developer']['status']} "
        f"paths={len(projection['developer']['paths'])}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AtlasCausalProjectionError,
        provenance.ProvenanceContractError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
