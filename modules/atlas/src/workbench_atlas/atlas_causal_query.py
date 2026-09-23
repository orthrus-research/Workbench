#!/usr/bin/env python3

"""Build request-bound causal paths from an accepted N01 normalization."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

if __package__:
    from . import atlas_causal_provenance_contract as provenance
    from . import atlas_provenance_normalizer as normalization
    from .knowledge_catalog import read_json
    from .layout import EXAMPLE_ROOT
else:  # Direct module loading.
    import workbench_atlas.atlas_causal_provenance_contract as provenance
    import workbench_atlas.atlas_provenance_normalizer as normalization
    from workbench_atlas.knowledge_catalog import read_json
    from workbench_atlas.layout import EXAMPLE_ROOT


PATH_PREFIX = provenance.PATH_PREFIX
RESULT_PREFIX = provenance.RESULT_PREFIX
STATEMENT_PREFIX = provenance.STATEMENT_PREFIX
FORMAT = "susy-atlas-causal-provenance-result-v1"
EXAMPLE_PATH = EXAMPLE_ROOT / "atlas-causal-query-example-v1.json"

ROOT_CLASSES = {"source-span", "configuration-entry"}


class AtlasCausalQueryError(ValueError):
    """Raised when P03 cannot produce a contract-valid causal result."""


@dataclass(frozen=True)
class PathCandidate:
    row: dict[str, Any]
    frontier_node_ids: tuple[str, ...]


def _scope_key(scope: dict[str, Any]) -> tuple[str, str]:
    return (scope["profile"], scope["physical_side"])


def _path(
    node_ids: Sequence[str],
    relation_ids: Sequence[str],
    *,
    closure_state: str,
    open_reason_codes: Iterable[str],
) -> dict[str, Any]:
    row = {
        "path_id": "",
        "node_ids": list(node_ids),
        "relation_ids": list(relation_ids),
        "closure_state": closure_state,
        "open_reason_codes": sorted(set(open_reason_codes)),
    }
    row["path_id"] = provenance.content_id(
        PATH_PREFIX, row, "path_id"
    )
    return row


def _statement(
    *,
    kind: str,
    subject_node_id: str | None,
    evidence_ids: Iterable[str],
    policy: dict[str, Any],
) -> dict[str, Any]:
    statement_policy = next(
        item
        for item in policy["negative_statement_policies"]
        if item["kind"] == kind
    )
    row = {
        "statement_id": "",
        "kind": kind,
        "subject_node_id": subject_node_id,
        "authority_evidence_ids": sorted(set(evidence_ids)),
        "bounded_wording": statement_policy["bounded_wording"],
    }
    row["statement_id"] = provenance.content_id(
        STATEMENT_PREFIX, row, "statement_id"
    )
    return row


def _validation_evidence(
    *,
    request_id: str,
    normalization_id: str,
    status: str,
    reason_codes: Iterable[str],
    path_ids: Iterable[str],
) -> dict[str, Any]:
    record = {
        "request_id": request_id,
        "normalization_id": normalization_id,
        "derived_status": status,
        "reason_codes": sorted(set(reason_codes)),
        "path_ids": sorted(set(path_ids)),
        "mechanical_claim": "none-validator-result-only",
    }
    return normalization._evidence(
        "derived-validation", "causal-validation", record
    )


def _index_map(
    entries: list[dict[str, Any]],
    label: str,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for row in entries:
        if set(row) != {"node_id", "relation_ids"}:
            raise AtlasCausalQueryError(f"{label} index entry fields differ")
        if row["node_id"] in result:
            raise AtlasCausalQueryError(f"{label} index repeats a node")
        result[row["node_id"]] = tuple(row["relation_ids"])
    return result


def _admit(
    document: dict[str, Any],
    request: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    try:
        normalization.validate_normalization(document, policy=policy)
        provenance.validate_request(request, policy)
    except (
        normalization.AtlasProvenanceNormalizationError,
        provenance.ProvenanceContractError,
    ) as exc:
        raise AtlasCausalQueryError(str(exc)) from exc
    if (
        document["snapshot_id"] != request["snapshot_id"]
        or document["source_lock_id"] != request["source_lock_id"]
        or document["policy_id"] != request["policy_id"]
        or document["policy_sha256"] != request["policy_sha256"]
    ):
        raise AtlasCausalQueryError(
            "request and normalization authority binding differs"
        )
    normalization_scopes = {
        (item["profile"], item["physical_side"])
        for item in document["scopes"]
    }
    if _scope_key(request["scope"]) not in normalization_scopes:
        raise AtlasCausalQueryError(
            "request primary scope is absent from normalization"
        )
    nodes = {row["node_id"]: row for row in document["nodes"]}
    relations = {
        row["relation_id"]: row for row in document["relations"]
    }
    evidence = {
        row["evidence_id"]: row for row in document["evidence"]
    }
    final_nodes = [
        row
        for row in document["nodes"]
        if row["node_class"] == "final-runtime-record"
        and row["identity"] == request["final_runtime_record"]
        and _scope_key(row["scope"]) == _scope_key(request["scope"])
    ]
    if len(final_nodes) != 1:
        raise AtlasCausalQueryError(
            "normalization lacks one exact request final runtime record"
        )
    final_node = final_nodes[0]
    capture_id = request["final_runtime_record"]["capture_evidence_id"]
    if capture_id not in final_node["evidence_ids"] or capture_id not in evidence:
        raise AtlasCausalQueryError(
            "request final capture evidence is absent from normalization"
        )
    forward = _index_map(document["indexes"]["forward"], "forward")
    reverse = _index_map(document["indexes"]["reverse"], "reverse")
    for relation in relations.values():
        if relation["relation_id"] not in forward.get(
            relation["subject_node_id"], ()
        ) or relation["relation_id"] not in reverse.get(
            relation["object_node_id"], ()
        ):
            raise AtlasCausalQueryError(
                "normalization indexes do not expose a relation bidirectionally"
            )
    return nodes, relations, evidence, final_node, forward, reverse


def _relevant_frontiers(
    document: dict[str, Any],
) -> dict[str, set[str]]:
    candidates = {
        row["candidate_id"]: row
        for row in document["operation_candidates"]
    }
    result: dict[str, set[str]] = {}
    for frontier in document["frontiers"]:
        anchor = frontier["anchor_node_id"]
        if frontier["primitive_kind"] == "operation":
            candidate = candidates[frontier["operation_candidate_id"]]
            # A static source anchor can host many unrelated lexical
            # candidates. It does not become target-relevant without an exact
            # promoted operation node.
            if (
                candidate["promoted_node_id"] is None
                or candidate["promoted_node_id"] != anchor
            ):
                continue
        result.setdefault(anchor, set()).add(frontier["reason_code"])
    return result


def _first_lifecycle_reversal(
    relation_ids: Sequence[str],
    relations: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    profile: str,
) -> int | None:
    reachability = provenance._lifecycle_reachability(policy, profile)
    previous_stage: str | None = None
    for index, relation_id in enumerate(relation_ids):
        stage = relations[relation_id]["lifecycle_stage_id"]
        if stage is None:
            continue
        if (
            previous_stage is not None
            and stage != previous_stage
            and stage not in reachability[previous_stage]
        ):
            return index
        previous_stage = stage
    return None


def _classify_path(
    node_ids: Sequence[str],
    relation_ids: Sequence[str],
    *,
    nodes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    final_node_id: str,
    frontier_reasons: dict[str, set[str]],
    forced_reason: str | None = None,
) -> PathCandidate:
    if not node_ids:
        raise AtlasCausalQueryError("cannot classify an empty path")
    reversal = _first_lifecycle_reversal(
        relation_ids,
        relations,
        policy,
        nodes[final_node_id]["scope"]["profile"],
    )
    if reversal is not None:
        prefix_nodes = list(node_ids[: reversal + 1])
        prefix_relations = list(relation_ids[:reversal])
        row = _path(
            prefix_nodes,
            prefix_relations,
            closure_state="partial",
            open_reason_codes=["lifecycle-order-unresolved"],
        )
        return PathCandidate(row, (prefix_nodes[-1],))

    reasons = set()
    frontier_nodes: set[str] = set()
    for node_id in node_ids:
        observed = frontier_reasons.get(node_id, set())
        if observed:
            reasons.update(observed)
            frontier_nodes.add(node_id)
    if forced_reason is not None:
        reasons.add(forced_reason)
        frontier_nodes.add(node_ids[0])

    strengths = {
        relations[item]["causal_strength"] for item in relation_ids
    }
    if "possibility" in strengths:
        reasons.add("runtime-transition-unobserved")
        frontier_nodes.add(
            node_ids[-2] if len(node_ids) > 1 else node_ids[0]
        )
    if not strengths.intersection({"transformation", "causation"}):
        reasons.add("runtime-transition-unobserved")
        frontier_nodes.add(
            node_ids[-2] if len(node_ids) > 1 else node_ids[0]
        )
    structurally_closed = (
        nodes[node_ids[0]]["node_class"] in ROOT_CLASSES
        and node_ids[-1] == final_node_id
        and bool(relation_ids)
        and relations[relation_ids[-1]]["predicate"] == "observed_as_final"
    )
    if not structurally_closed and forced_reason is None:
        reasons.add("runtime-transition-unobserved")
        frontier_nodes.add(node_ids[0])
    closure_state = "closed" if structurally_closed and not reasons else "partial"
    row = _path(
        node_ids,
        relation_ids,
        closure_state=closure_state,
        open_reason_codes=reasons,
    )
    return PathCandidate(row, tuple(sorted(frontier_nodes)))


def _enumerate_paths(
    *,
    document: dict[str, Any],
    request: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    final_node: dict[str, Any],
    forward: dict[str, tuple[str, ...]],
    reverse: dict[str, tuple[str, ...]],
    policy: dict[str, Any],
) -> tuple[list[PathCandidate], str | None]:
    bounds = request["bounds"]
    frontier_reasons = _relevant_frontiers(document)
    candidates: list[PathCandidate] = []
    seen_candidate_ids: set[str] = set()
    touched_nodes = {final_node["node_id"]}
    touched_relations: set[str] = set()
    bound_reason: str | None = None
    admitted_scopes = {
        _scope_key(request["scope"]),
        *(_scope_key(item) for item in request["supporting_scopes"]),
    }

    def retain(candidate: PathCandidate) -> bool:
        nonlocal bound_reason
        if candidate.row["path_id"] in seen_candidate_ids:
            return True
        if len(candidates) >= bounds["max_paths"]:
            bound_reason = "path-bound-reached"
            return False
        seen_candidate_ids.add(candidate.row["path_id"])
        candidates.append(candidate)
        return True

    def walk(
        current: str,
        reverse_nodes: list[str],
        reverse_relations: list[str],
    ) -> bool:
        nonlocal bound_reason
        if bound_reason is not None:
            return False
        current_node = nodes[current]
        if current_node["node_class"] in ROOT_CLASSES:
            return retain(
                _classify_path(
                    list(reversed(reverse_nodes)),
                    list(reversed(reverse_relations)),
                    nodes=nodes,
                    relations=relations,
                    policy=policy,
                    final_node_id=final_node["node_id"],
                    frontier_reasons=frontier_reasons,
                )
            )
        incoming = reverse.get(current, ())
        admitted = []
        cycle_only = False
        for relation_id in incoming:
            relation = relations[relation_id]
            subject_id = relation["subject_node_id"]
            if relation_id not in forward.get(subject_id, ()):
                raise AtlasCausalQueryError(
                    "reverse traversal found no matching forward index entry"
                )
            if subject_id in reverse_nodes or relation_id in reverse_relations:
                cycle_only = True
                continue
            if _scope_key(nodes[subject_id]["scope"]) not in admitted_scopes:
                continue
            if relation_id not in touched_relations:
                if len(touched_relations) >= bounds["max_relations"]:
                    bound_reason = "relation-bound-reached"
                    return False
                touched_relations.add(relation_id)
            if subject_id not in touched_nodes:
                if len(touched_nodes) >= bounds["max_nodes"]:
                    bound_reason = "node-bound-reached"
                    return False
                touched_nodes.add(subject_id)
            admitted.append((relation_id, subject_id))
        if not admitted:
            if len(reverse_nodes) == 1:
                if cycle_only:
                    bound_reason = None
                return True
            forced = (
                "identity-reconciliation-unresolved"
                if cycle_only
                else "source-span-unavailable"
            )
            return retain(
                _classify_path(
                    list(reversed(reverse_nodes)),
                    list(reversed(reverse_relations)),
                    nodes=nodes,
                    relations=relations,
                    policy=policy,
                    final_node_id=final_node["node_id"],
                    frontier_reasons=frontier_reasons,
                    forced_reason=forced,
                )
            )
        for relation_id, subject_id in admitted:
            if not walk(
                subject_id,
                [*reverse_nodes, subject_id],
                [*reverse_relations, relation_id],
            ):
                return False
        return True

    walk(final_node["node_id"], [final_node["node_id"]], [])
    return sorted(candidates, key=lambda item: item.row["path_id"]), bound_reason


def _project(
    *,
    candidates: list[PathCandidate],
    bound_reason: str | None,
    request: dict[str, Any],
    document: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    final_node: dict[str, Any],
    policy: dict[str, Any],
    unavailable: bool,
) -> dict[str, Any]:
    bounds = request["bounds"]
    selected: list[PathCandidate] = []
    selected_node_ids = {final_node["node_id"]}
    selected_relation_ids: set[str] = set()
    selected_evidence_ids = set(final_node["evidence_ids"])
    if (
        len(selected_node_ids) > bounds["max_nodes"]
        or len(selected_evidence_ids) > bounds["max_evidence_records"]
    ):
        raise AtlasCausalQueryError(
            "request bounds cannot contain the required final endpoint"
        )
    omitted = 0
    projection_bound = bound_reason
    if not unavailable:
        for candidate in candidates:
            path_node_ids = set(candidate.row["node_ids"])
            path_relation_ids = set(candidate.row["relation_ids"])
            path_evidence_ids = {
                item
                for node_id in path_node_ids
                for item in nodes[node_id]["evidence_ids"]
            } | {
                item
                for relation_id in path_relation_ids
                for item in relations[relation_id]["evidence_ids"]
            }
            next_nodes = selected_node_ids | path_node_ids
            next_relations = selected_relation_ids | path_relation_ids
            next_evidence = selected_evidence_ids | path_evidence_ids
            exceeded = None
            if len(selected) >= bounds["max_paths"]:
                exceeded = "path-bound-reached"
            elif len(next_nodes) > bounds["max_nodes"]:
                exceeded = "node-bound-reached"
            elif len(next_relations) > bounds["max_relations"]:
                exceeded = "relation-bound-reached"
            elif len(next_evidence) > bounds["max_evidence_records"]:
                exceeded = "evidence-bound-reached"
            if exceeded is not None:
                projection_bound = projection_bound or exceeded
                omitted += 1
                continue
            selected.append(candidate)
            selected_node_ids = next_nodes
            selected_relation_ids = next_relations
            selected_evidence_ids = next_evidence
    if bound_reason is not None:
        omitted = max(1, omitted)
    if projection_bound is not None:
        omitted = max(1, omitted)

    partial = [
        item for item in selected if item.row["closure_state"] == "partial"
    ]
    closed = [
        item for item in selected if item.row["closure_state"] == "closed"
    ]
    if unavailable:
        status = "unavailable"
        reason_codes = ["required-evidence-unavailable"]
        frontier_node_ids: list[str] = []
        selected = []
        selected_node_ids = {final_node["node_id"]}
        selected_relation_ids = set()
        selected_evidence_ids = set(final_node["evidence_ids"])
        projection_bound = None
        omitted = 0
    elif projection_bound is not None:
        status = "bounded"
        reason_codes = sorted(
            {
                projection_bound,
                *(
                    reason
                    for item in partial
                    for reason in item.row["open_reason_codes"]
                ),
            }
        )
        frontier_node_ids = sorted(
            {
                final_node["node_id"],
                *(
                    node_id
                    for item in partial
                    for node_id in item.frontier_node_ids
                ),
            }
        )
    elif partial:
        status = "partial"
        reason_codes = sorted(
            {
                reason
                for item in partial
                for reason in item.row["open_reason_codes"]
            }
        )
        frontier_node_ids = sorted(
            {
                node_id
                for item in partial
                for node_id in item.frontier_node_ids
            }
        )
    elif closed:
        status = "closed"
        reason_codes = []
        frontier_node_ids = []
    else:
        status = "unresolved"
        reason_codes = ["runtime-transition-unobserved"]
        frontier_node_ids = []

    path_rows = sorted(
        (copy.deepcopy(item.row) for item in selected),
        key=lambda item: item["path_id"],
    )
    negative_statements: list[dict[str, Any]] = []
    extra_evidence: list[dict[str, Any]] = []
    if not closed:
        validation = _validation_evidence(
            request_id=request["request_id"],
            normalization_id=document["normalization_id"],
            status=status,
            reason_codes=reason_codes,
            path_ids=(item["path_id"] for item in path_rows),
        )
        if (
            len(selected_evidence_ids | {validation["evidence_id"]})
            <= bounds["max_evidence_records"]
        ):
            selected_evidence_ids.add(validation["evidence_id"])
            extra_evidence.append(validation)
            negative_statements.append(
                _statement(
                    kind="no-closed-causal-path",
                    subject_node_id=final_node["node_id"],
                    evidence_ids=[validation["evidence_id"]],
                    policy=policy,
                )
            )

    result_nodes = sorted(
        (copy.deepcopy(nodes[item]) for item in selected_node_ids),
        key=lambda item: item["node_id"],
    )
    result_relations = sorted(
        (copy.deepcopy(relations[item]) for item in selected_relation_ids),
        key=lambda item: item["relation_id"],
    )
    result_evidence = sorted(
        [
            *(
                copy.deepcopy(evidence[item])
                for item in selected_evidence_ids
                if item in evidence
            ),
            *extra_evidence,
        ],
        key=lambda item: item["evidence_id"],
    )
    result = {
        "schema_version": 1,
        "format": FORMAT,
        "result_id": "",
        "request": copy.deepcopy(request),
        "status": status,
        "closure": {
            "state": status,
            "reason_codes": reason_codes,
            "open_frontier_node_ids": frontier_node_ids,
        },
        "nodes": result_nodes,
        "relations": result_relations,
        "paths": path_rows,
        "evidence": result_evidence,
        "negative_statements": sorted(
            negative_statements,
            key=lambda item: item["statement_id"],
        ),
        "truncation": {
            "truncated": status == "bounded",
            "reason": projection_bound if status == "bounded" else None,
            "emitted_paths": len(path_rows),
            "emitted_nodes": len(result_nodes),
            "emitted_relations": len(result_relations),
            "emitted_evidence_records": len(result_evidence),
            "omitted_at_least": omitted if status == "bounded" else 0,
        },
        "pagination": {
            "mode": "none-v1-single-bounded-result",
            "continuation": None,
        },
    }
    result["result_id"] = provenance.content_id(
        RESULT_PREFIX, result, "result_id"
    )
    try:
        provenance.validate_result(result, policy)
    except provenance.ProvenanceContractError as exc:
        raise AtlasCausalQueryError(
            f"constructed causal result failed C01: {exc}"
        ) from exc
    return result


def query(
    document: dict[str, Any],
    request: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query one exact final runtime record over one accepted normalization."""

    if policy is None:
        policy = provenance.load_policy()
    (
        nodes,
        relations,
        evidence,
        final_node,
        forward,
        reverse,
    ) = _admit(document, request, policy)
    unavailable = any(
        evidence[item]["record_kind"] == "authority-unavailable"
        and evidence[item]["record"].get("required_for")
        == "causal-provenance-query"
        for item in final_node["evidence_ids"]
    )
    candidates: list[PathCandidate] = []
    bound_reason: str | None = None
    if not unavailable:
        candidates, bound_reason = _enumerate_paths(
            document=document,
            request=request,
            nodes=nodes,
            relations=relations,
            final_node=final_node,
            forward=forward,
            reverse=reverse,
            policy=policy,
        )
    return _project(
        candidates=candidates,
        bound_reason=bound_reason,
        request=request,
        document=document,
        nodes=nodes,
        relations=relations,
        evidence=evidence,
        final_node=final_node,
        policy=policy,
        unavailable=unavailable,
    )


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(provenance.canonical_json(value) + b"\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("query", help="build one C01 result")
    run.add_argument("--normalization", type=Path, required=True)
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    check = subparsers.add_parser("check", help="validate one C01 result")
    check.add_argument("result", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "check":
        provenance.validate_result(read_json(arguments.result))
        print(f"valid Atlas causal query result: {arguments.result}")
        return 0
    result = query(
        read_json(arguments.normalization),
        read_json(arguments.request),
    )
    _write(arguments.output, result)
    print(
        f"wrote {result['result_id']} status={result['status']} "
        f"paths={len(result['paths'])}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AtlasCausalQueryError,
        provenance.ProvenanceContractError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
