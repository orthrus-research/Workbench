"""Public Atlas recipe-health queries and bounded derived-index repair."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Protocol, Sequence, TextIO

from workbench_api import ExecutionContext

from workbench_atlas_recipe_health import (
    DEFAULT_MAX_INDEX_BYTES,
    DEFAULT_MAX_SOURCE_BYTES,
    RecipeHealthError,
    audit_recipe_dead_ends,
    compare_runtime_recipe_graphs,
    discover_recipe_health_operational_context,
    open_recipe_health,
    rebuild_recipe_health_index,
    validate_proposed_recipe_assessment,
)


class PlanAssessor(Protocol):
    """API 1 callback: validate a retained owner plan before Atlas assessment."""

    def __call__(
        self,
        graph_root: Path,
        plan_reference: str,
        *,
        state_root: Path | None,
        max_depth: int,
        max_nodes: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RecipePlanAssessmentAdapter:
    """Explicit optional composition; Atlas never imports the plan owner."""

    assess: PlanAssessor
    api_version: int = 1


def _assess_plan(
    args: argparse.Namespace, adapter: RecipePlanAssessmentAdapter | None
) -> dict[str, Any]:
    if adapter is None:
        raise RecipeHealthError(
            "assess-plan requires an enabled plan-assessment adapter; install and "
            "enable Workbench Shell with the plan's owning profile, or supply an "
            "API-1 RecipePlanAssessmentAdapter"
        )
    if (
        not isinstance(adapter, RecipePlanAssessmentAdapter)
        or type(adapter.api_version) is not int
        or adapter.api_version != 1
        or not callable(adapter.assess)
    ):
        raise RecipeHealthError("plan-assessment adapter has an incompatible API")
    try:
        record = adapter.assess(
            args.path,
            args.plan,
            state_root=args.state_root,
            max_depth=args.max_depth,
            max_nodes=args.max_nodes,
        )
    except (Exception, SystemExit) as exc:
        raise RecipeHealthError(f"plan-assessment adapter failed: {exc}") from exc
    try:
        return validate_proposed_recipe_assessment(record)
    except RecipeHealthError as exc:
        raise RecipeHealthError(f"plan-assessment adapter returned an invalid assessment: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench atlas recipes",
        description=(
            "Search and inspect recipe evidence in one explicit categorical graph "
            "or source checkout. Source-only answers remain explicitly limited."
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True)

    session = actions.add_parser("session", help="reuse one verified graph for serial JSONL search and relationship pages")
    session.add_argument("path", type=Path)

    audit = actions.add_parser("audit-dead-ends", help="audit every captured GT recipe for missing producer/use candidates and circular dependencies")
    audit.add_argument("path", type=Path, help="verified categorical V2 recipe graph root or manifest")
    audit_format = audit.add_mutually_exclusive_group()
    audit_format.add_argument("--json", action="store_true", help="emit the complete audit, coverage, resources and cycle evidence")
    audit_format.add_argument("--csv", action="store_true", help="emit one row per captured recipe with graph-bound findings and input/output details")

    ingest = actions.add_parser("import-capture", help="project a retained runtime capture through an explicit pack adapter")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--output", type=Path, required=True)
    ingest.add_argument("--input-manifest", type=Path, required=True)
    ingest.add_argument("--pack-profile", required=True)
    ingest.add_argument("--max-source-bytes", type=int, default=1024 * 1024 * 1024)
    ingest.add_argument("--json", action="store_true")

    context = actions.add_parser(
        "context",
        help="describe the recipe evidence available at PATH",
    )
    context.add_argument("path", type=Path)
    context.add_argument(
        "--json",
        action="store_true",
        help="emit the complete Atlas operational context record",
    )

    index = actions.add_parser(
        "index",
        help="rebuild disposable query storage under explicit byte and disk bounds",
    )
    index.add_argument("path", type=Path)
    index.add_argument(
        "--max-source-bytes",
        type=int,
        default=DEFAULT_MAX_SOURCE_BYTES,
        help=(
            "maximum authoritative stream bytes read "
            f"(default: {DEFAULT_MAX_SOURCE_BYTES})"
        ),
    )
    index.add_argument(
        "--max-index-bytes",
        type=int,
        default=DEFAULT_MAX_INDEX_BYTES,
        help=(
            "maximum staged/published SQLite bytes; twice this much free disk is "
            f"preflighted (default: {DEFAULT_MAX_INDEX_BYTES})"
        ),
    )
    index.add_argument(
        "--json",
        action="store_true",
        help="emit the complete derived-index operation record",
    )

    search = actions.add_parser(
        "search",
        help="find selectable recipes, resources, or source occurrences",
    )
    search.add_argument("path", type=Path)
    search.add_argument("query")
    search.add_argument(
        "--limit",
        type=int,
        default=50,
        help="maximum results to return (1..10000; default: 50)",
    )
    search.add_argument(
        "--json",
        action="store_true",
        help="emit the complete Atlas V1 search record",
    )

    inspect = actions.add_parser(
        "inspect",
        help="inspect one exact selection ID returned by search",
    )
    inspect.add_argument("path", type=Path)
    inspect.add_argument("selection_id")
    inspect.add_argument(
        "--json",
        action="store_true",
        help="emit the complete Atlas V1 inspection record",
    )

    browse = actions.add_parser("browse", help="page through exact observed relationships without expanding a route")
    browse.add_argument("path", type=Path)
    browse.add_argument("selection_id")
    browse.add_argument("--offset", type=int, default=0)
    browse.add_argument("--limit", type=int, default=50)
    browse.add_argument("--expect-graph", help="refuse a changed graph when following a search or reopening")
    browse.add_argument("--json", action="store_true")

    routes = actions.add_parser(
        "routes",
        help="trace observed producer routes and their unresolved prerequisites",
    )
    routes.add_argument("path", type=Path)
    routes.add_argument("selection_id", help="exact item or fluid selection ID returned by search")
    routes.add_argument("--max-depth", type=int, default=None, help="optional dependency depth bound; omitted means complete finite traversal")
    routes.add_argument("--max-resources", type=int, default=None, help="optional visited resource bound")
    routes.add_argument("--max-recipes", type=int, default=None, help="optional recipe occurrence bound")
    routes.add_argument("--json", action="store_true", help="emit the complete finite route model")

    impact = actions.add_parser(
        "impact",
        help="trace dependency exposure if one observed recipe disappears",
    )
    impact.add_argument("path", type=Path)
    impact.add_argument("selection_id")
    impact.add_argument(
        "--exploration", choices=("bounded", "complete-finite"), default="bounded",
        help="bounded V1 exploration or complete finite V2 exploration with evidence gaps",
    )
    impact.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="maximum finite-recipe propagation depth (1..12; default: 4)",
    )
    impact.add_argument(
        "--max-nodes",
        type=int,
        default=None,
        help="maximum observed graph nodes to inspect (10..2000; default: 500)",
    )
    impact.add_argument(
        "--json",
        action="store_true",
        help="emit the exact V1 bounded or V2 complete finite impact report",
    )

    assess_plan = actions.add_parser(
        "assess-plan",
        help="assess one retained recipe-change ADD plan against an observed graph",
    )
    assess_plan.add_argument("path", type=Path)
    assess_plan.add_argument("plan")
    assess_plan.add_argument(
        "--state-root",
        type=Path,
        help="retained developer-feature state containing the selected plan",
    )
    assess_plan.add_argument(
        "--max-depth",
        type=int,
        default=4,
        help="maximum observed dependency depth (1..12; default: 4)",
    )
    assess_plan.add_argument(
        "--max-nodes",
        type=int,
        default=500,
        help="maximum observed graph nodes to inspect (10..2000; default: 500)",
    )
    assess_plan.add_argument(
        "--json",
        action="store_true",
        help="emit the complete Atlas V1 proposed-recipe assessment",
    )

    compare = actions.add_parser(
        "compare-runtime",
        help="compare finite recipe and progression candidates across two graphs",
    )
    compare.add_argument("before_path", type=Path)
    compare.add_argument("after_path", type=Path)
    compare.add_argument(
        "--max-recipes",
        type=int,
        default=100_000,
        help="maximum finite recipes scanned per graph (1..250000)",
    )
    compare.add_argument(
        "--max-recipe-deltas",
        type=int,
        default=500,
        help="maximum exact signature delta details (1..10000)",
    )
    compare.add_argument(
        "--max-resources",
        type=int,
        default=500,
        help="maximum resource-flow delta details (1..10000)",
    )
    compare.add_argument("--max-depth", type=int, default=4)
    compare.add_argument("--max-nodes", type=int, default=2000)
    compare.add_argument(
        "--json",
        action="store_true",
        help="emit the complete Atlas V1 runtime comparison",
    )
    return parser


def _capability_lines(context: dict[str, Any]) -> list[str]:
    capabilities = context.get("capabilities", {})
    if type(capabilities) is not dict:
        return []
    available = sorted(
        key.replace("_", "-")
        for key, value in capabilities.items()
        if value is True
    )
    unavailable = sorted(
        key.replace("_", "-")
        for key, value in capabilities.items()
        if value is False
    )
    lines: list[str] = []
    if available:
        lines.append("Available evidence: " + ", ".join(available))
    if unavailable:
        lines.append("Evidence unavailable: " + ", ".join(unavailable))
    return lines


def _gap_lines(record: dict[str, Any]) -> list[str]:
    gaps = record.get("evidence_gaps", [])
    if type(gaps) is not list or not gaps:
        return []
    lines = ["Evidence gaps:"]
    for gap in gaps:
        if type(gap) is not dict:
            continue
        code = gap.get("code", "unspecified")
        message = gap.get("message", "")
        lines.append(f"- {code}: {message}".rstrip())
        paths = gap.get("paths")
        if type(paths) is list and paths:
            lines.append("  Paths: " + ", ".join(str(path) for path in paths))
    return lines


def _render_context(record: dict[str, Any]) -> str:
    context_type = record.get("context_type", "unknown")
    lines = [
        f"Recipe evidence context: {context_type}",
        f"Root: {record.get('root', 'unknown')}",
    ]
    if "graph_set_id" in record:
        lines.append(f"Graph set: {record['graph_set_id']}")
    if "recipe_count" in record:
        lines.append(f"Recipes: {record['recipe_count']}")
    if "source_file_count" in record:
        lines.append(f"Source files: {record['source_file_count']}")
    index = record.get("query_index")
    if type(index) is dict:
        lines.append(
            "Query index: "
            f"{index.get('state', 'unknown')} ({index.get('reason_code', 'unknown')})"
        )
        search = record.get("search", {})
        executable = search.get("executable", False) if type(search) is dict else False
        lines.append(f"Search executable now: {executable}")
        reason = index.get("reason")
        if type(reason) is str and reason:
            lines.append(f"Reason: {reason}")
        repair = record.get("repair")
        if type(repair) is dict:
            argv = repair.get("argv")
            if type(argv) is list and argv:
                lines.append("Repair: " + " ".join(str(item) for item in argv))
            lines.append("Repair custody: derived index storage only; graph evidence is unchanged.")
        evidence = record.get("evidence_capabilities")
        if type(evidence) is dict:
            lines.extend(_capability_lines({"capabilities": evidence}))
    else:
        lines.extend(_capability_lines(record))
    return "\n".join(lines) + "\n"


def _render_index_operation(record: dict[str, Any]) -> str:
    bounds = record.get("bounds", {})
    custody = record.get("custody", {})
    published = custody.get("published_query_index", {}) if type(custody) is dict else {}
    return "\n".join(
        [
            f"Atlas recipe query index: {record.get('state', 'unknown')}",
            f"Graph set: {record.get('graph_set_id', 'unknown')}",
            (
                "Authoritative input: "
                f"{bounds.get('source_record_count', '?')} record(s), "
                f"{bounds.get('source_stream_bytes', '?')} byte(s)"
            ),
            (
                "Disk preflight: "
                f"{bounds.get('free_bytes_before', '?')} free; "
                f"{bounds.get('required_free_bytes', '?')} required"
            ),
            (
                "Published derived index: "
                f"{published.get('size', '?')} byte(s), "
                f"sha256:{published.get('sha256', 'unknown')}"
            ),
            (
                "Custody: graph identity preserved; only query-index.sqlite3 "
                "and the manifest query_index descriptor changed."
            ),
            f"Operation: {record.get('operation_id', 'unknown')}",
        ]
    ) + "\n"


def _closure_summary(record: dict[str, Any]) -> dict[str, Any]:
    frontiers = record.get("frontiers", [])
    unknowns = record.get("unknowns", [])
    if type(frontiers) is not list:
        frontiers = []
    if type(unknowns) is not list:
        unknowns = []
    phases: dict[str, dict[str, Any]] = {}
    for frontier in frontiers:
        if type(frontier) is not dict:
            continue
        phase = str(frontier.get("phase", "unspecified"))
        row = phases.setdefault(
            phase,
            {"status": "truncated", "frontier_count": 0, "kinds": {}},
        )
        row["frontier_count"] += 1
        kind = str(frontier.get("kind", "unspecified"))
        row["kinds"][kind] = row["kinds"].get(kind, 0) + 1
    summary = record.get("summary", {})
    summary_truncated = (
        type(summary) is dict and summary.get("truncated") is True
    )
    if frontiers:
        status = "truncated"
    elif summary_truncated:
        status = "truncated-unitemized"
    elif unknowns:
        status = "unresolved-within-bounds"
    else:
        status = "bounded-complete-within-model"
    return {
        "status": status,
        "frontier_count": len(frontiers),
        "unknown_count": len(unknowns),
        "phases": {phase: phases[phase] for phase in sorted(phases)},
    }


def _closure_lines(record: dict[str, Any]) -> list[str]:
    closure = _closure_summary(record)
    lines = [
        (
            "Overall closure: "
            f"{closure['status']} ({closure['frontier_count']} frontier(s), "
            f"{closure['unknown_count']} unknown(s))"
        )
    ]
    for phase, row in closure["phases"].items():
        kinds = ", ".join(
            f"{kind}={count}" for kind, count in sorted(row["kinds"].items())
        )
        lines.append(
            f"- {phase}: truncated; {row['frontier_count']} retained frontier(s)"
            + (f" ({kinds})" if kinds else "")
        )
    if closure["frontier_count"]:
        lines.append(
            "Frontier detail is capped here; --json retains every exact frontier row."
        )
    return lines


def _render_search(record: dict[str, Any]) -> str:
    results = record.get("results", [])
    query = record.get("query", "")
    lines = [f"Recipe search: {len(results)} result(s) for {query!r}"]
    for result in results:
        if type(result) is not dict:
            continue
        selection = result.get("selection_id", "unknown")
        kind = result.get("kind", "unknown")
        if kind == "source-occurrence":
            location = (
                f"{result.get('source_path', 'unknown')}:"
                f"{result.get('line', '?')}:{result.get('column', '?')}"
            )
            lines.append(f"- {selection} [{kind}] {location}")
            snippet = result.get("snippet")
            if type(snippet) is str and snippet:
                lines.append(f"  {snippet}")
        else:
            lines.append(
                f"- {selection} [{kind}] {result.get('semantic_key', 'unknown')}"
            )
    if record.get("truncated") is True:
        lines.append("Results truncated; increase --limit to inspect more matches.")
    gaps = _gap_lines(record)
    if gaps:
        lines.extend(gaps)
    else:
        context = record.get("context")
        if type(context) is dict:
            lines.extend(_capability_lines(context))
    return "\n".join(lines) + "\n"


def _render_inspect(record: dict[str, Any]) -> str:
    selection = record.get("selection", {})
    if type(selection) is not dict:
        selection = {}
    role = record.get("role", "unknown")
    lines = [
        f"Recipe inspection: {selection.get('selection_id', 'unknown')}",
        f"Role: {role}",
        f"Kind: {selection.get('kind', 'unknown')}",
    ]
    if role == "source-occurrence":
        lines.append(
            "Source: "
            f"{selection.get('source_path', 'unknown')}:"
            f"{selection.get('line', '?')}:{selection.get('column', '?')}"
        )
        ownership = record.get("ownership", {})
        if type(ownership) is dict:
            lines.append(f"Ownership: {ownership.get('status', 'unknown')}")
    elif role == "recipe":
        duplicate = record.get("duplicate_signature", {})
        ownership = record.get("ownership", {})
        if type(duplicate) is dict:
            lines.append(
                "Signature: "
                f"{duplicate.get('status', 'unknown')} "
                f"({duplicate.get('exact_match_count', 'unknown')} exact match(es))"
            )
        if type(ownership) is dict:
            lines.append(f"Ownership: {ownership.get('status', 'unknown')}")
            source_paths = ownership.get("source_paths")
            if type(source_paths) is list and source_paths:
                lines.append("Sources: " + ", ".join(str(path) for path in source_paths))
        lines.append(f"Inputs: {len(record.get('inputs', []))}")
        lines.append(f"Outputs: {len(record.get('outputs', []))}")
    elif role == "target":
        flow = record.get("flow", {})
        if type(flow) is dict:
            lines.append(
                f"Producers: {len(flow.get('producers', []))} "
                f"({flow.get('producer_status', 'unknown')})"
            )
            lines.append(
                f"Consumers: {len(flow.get('consumers', []))} "
                f"({flow.get('consumer_status', 'unknown')})"
            )
    lines.extend(_gap_lines(record))
    return "\n".join(lines) + "\n"


def _render_impact(record: dict[str, Any]) -> str:
    if record.get("format") == "workbench-atlas-recipe-impact-report-v2":
        summary = record["summary"]
        exploration = record["exploration"]
        lines = [
            f"Recipe impact candidate analysis: {record['selection']['selection_id']}",
            "Scenario: remove the exact observed recipe",
            "Observed finite dependency exploration: complete",
            f"Evidence completeness: {record['evidence_completeness']['status']}",
            f"Inspected structure: {exploration['visited_node_count']} nodes, {exploration['traversed_edge_count']} edges",
            f"Candidate exposure: {summary['at_risk_resource_candidate_count']} resource(s), {summary['at_risk_recipe_candidate_count']} downstream recipe(s)",
            f"Alternative dependency components: {summary['alternative_dependency_component_count']}; viability remains unknown",
            f"Unknowns: {len(record['unknowns'])}",
            "Claim boundary: candidates are not proof of broken or unreachable progression.",
        ]
        lines.extend(_gap_lines(record))
        return "\n".join(lines) + "\n"
    selection = record.get("selection", {})
    summary = record.get("summary", {})
    bounds = record.get("bounds", {})
    if type(selection) is not dict:
        selection = {}
    if type(summary) is not dict:
        summary = {}
    if type(bounds) is not dict:
        bounds = {}
    lines = [
        f"Recipe impact candidate analysis: {selection.get('selection_id', 'unknown')}",
        "Scenario: remove the exact observed recipe",
        (
            "Candidate exposure: "
            f"{summary.get('at_risk_resource_candidate_count', '?')} resource(s), "
            f"{summary.get('at_risk_recipe_candidate_count', '?')} downstream recipe(s)"
        ),
        (
            "Progression signals: "
            f"{summary.get('quest_requirement_exposure_count', '?')} quest requirement(s), "
            f"{summary.get('structural_quest_dependent_count', '?')} structural dependent(s)"
        ),
        (
            "Alternative dependency-cycle signals: "
            f"{summary.get('alternative_dependency_cycle_signal_count', '?')}"
        ),
        (
            "Bounds: "
            f"depth {bounds.get('max_depth', '?')}, "
            f"{bounds.get('visited_node_count', '?')}/{bounds.get('max_nodes', '?')} nodes"
        ),
        "Claim boundary: candidates are not proof of broken or unreachable progression.",
    ]
    lines.extend(_closure_lines(record))
    lines.extend(_gap_lines(record))
    return "\n".join(lines) + "\n"


def _render_dead_end_audit(record: dict[str, Any]) -> str:
    counts = record["summary"]
    return "\n".join([
        "Atlas captured recipe dead-end audit",
        f"Graph set: {record['context']['graph_set_id']}",
        f"Recipes audited: {counts['recipe_count']} (complete captured inventory)",
        f"Active: {counts['active_recipe_count']}; inactive: {counts['inactive_recipe_count']}; unknown activity: {counts['unknown_activity_recipe_count']}",
        f"Missing upstream producer candidates: {counts['missing_producer_candidate_count']}",
        f"No output use candidates: {counts['no_output_use_candidate_count']}",
        f"Both sides: {counts['both_sides_candidate_count']}",
        f"Recipes with stranded output candidates: {counts['stranded_output_candidate_count']}",
        f"Cycle components: {counts['cycle_count']}; recipes with unresolved evidence: {counts['unresolved_recipe_count']}",
        "Findings concern active captured recipe links, not proven acquisition or gameplay validity.",
        "External supplies, terminal uses and uncaptured recipe families remain unknown.",
        "Circular references do not provide their own starting supply.",
        "Summary only: use --json for complete evidence or --csv for every recipe row.",
    ]) + "\n"


def _write_dead_end_csv(record: dict[str, Any], output: TextIO) -> None:
    fields = ("selection_id", "semantic_key", "lookup_state", "recipe_maps", "recipe_values",
              "findings", "issues", "input_inventory", "upstream", "downstream",
              "inputs", "outputs", "cycle_component_ids")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("format", "graph_set_id", "policy_id", "policy_implementation_sha256", "scope", *fields))
    for row in record["recipes"]:
        values = [json.dumps(row[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                  if isinstance(row[key], (dict, list)) else row[key] for key in fields]
        writer.writerow((record["format"], record["context"]["graph_set_id"],
                         record["policy"]["id"], record["policy"]["implementation_sha256"],
                         "captured GT recipes only; acquisition and terminal use unknown", *values))


def _render_routes(record: dict[str, Any]) -> str:
    summary = record["summary"]
    selection = record["selection"]
    exploration = record["exploration"]
    lines = [
        f"Observed recipe routes: {selection.get('semantic_key', selection.get('id', 'unknown'))}",
        f"Finite exploration: {exploration['status']} ({exploration['mode']})",
        f"Structure: {summary['resource_count']} resource(s), {summary['recipe_count']} recipe occurrence(s), {summary['route_count']} route(s)",
        f"Dependency cycles: {summary['cycle_component_count']}",
        f"Unresolved requirements: {summary['unresolved_count']}; traversal frontiers: {summary['frontier_count']}",
        f"Craftability: {summary['craftability']}",
        "Recipe inputs are jointly required; alternatives within each input remain choices.",
        "Reusable tools, recipe conditions and ordered chance outputs remain explicit in --json.",
        "Cycles do not supply their own starting materials. Inventory, machines and world access remain unknown.",
    ]
    return "\n".join(lines) + "\n"


def _render_plan_assessment(record: dict[str, Any]) -> str:
    proposal = record.get("proposal", {})
    summary = record.get("summary", {})
    if type(proposal) is not dict:
        proposal = {}
    if type(summary) is not dict:
        summary = {}
    lines = [
        f"Proposed recipe assessment: {proposal.get('proposal_id', 'unknown')}",
        "Scenario: add one owner-validated source recipe; runtime bytes are not observed",
        (
            "Resolution: "
            f"{summary.get('exact_observed_resource_key_count', '?')} exact resource key(s), "
            f"{summary.get('unresolved_or_ambiguous_resource_count', '?')} unresolved/ambiguous"
        ),
        (
            "Structural review candidates: "
            f"{summary.get('structural_collision_candidate_count', '?')} collision-shape, "
            f"{summary.get('output_input_cycle_candidate_count', '?')} output-to-input cycle"
        ),
        (
            "Observed output links: "
            f"{summary.get('output_with_existing_producer_count', '?')} with producers, "
            f"{summary.get('output_with_existing_consumer_count', '?')} with consumers, "
            f"{summary.get('quest_output_reference_count', '?')} quest reference(s)"
        ),
        "Claim boundary: structural candidates are not runtime collisions, bypasses, or broken progression.",
    ]
    lines.extend(_closure_lines(record))
    lines.extend(_gap_lines(record))
    return "\n".join(lines) + "\n"


def _render_runtime_comparison(record: dict[str, Any]) -> str:
    summary = record.get("summary", {})
    compatibility = record.get("compatibility", {})
    bounds = record.get("bounds", {})
    if type(summary) is not dict:
        summary = {}
    if type(compatibility) is not dict:
        compatibility = {}
    if type(bounds) is not dict:
        bounds = {}
    lines = [
        "Runtime recipe graph comparison",
        f"Comparability: {compatibility.get('state', 'unknown')}",
        (
            "Exact signatures: "
            f"-{summary.get('removed_exact_signature_count', '?')} "
            f"+{summary.get('added_exact_signature_count', '?')}"
        ),
        (
            "Progression exposure candidates: "
            f"{summary.get('newly_exposed_resource_candidate_count', '?')} resource(s), "
            f"{summary.get('at_risk_recipe_candidate_count', '?')} recipe(s), "
            f"{summary.get('quest_requirement_exposure_count', '?')} quest requirement(s)"
        ),
        (
            "Bounds: "
            f"depth {bounds.get('max_depth', '?')}, "
            f"{bounds.get('visited_node_count', '?')}/{bounds.get('max_nodes', '?')} nodes"
        ),
        "Claim boundary: observed candidates are not proof of broken, dead, or unreachable player progression.",
    ]
    if compatibility.get("state") != "compatible":
        lines.append("No semantic delta was computed because graph capture protocols are not comparable.")
    if summary.get("truncated") is True:
        lines.append("Comparison reached a declared frontier; inspect JSON before drawing conclusions.")
    lines.extend(_gap_lines(record))
    return "\n".join(lines) + "\n"


def _write_json(record: dict[str, Any], output: TextIO) -> None:
    json.dump(record, output, ensure_ascii=False, indent=2, sort_keys=True)
    output.write("\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    plan_adapter: RecipePlanAssessmentAdapter | None = None,
    context: ExecutionContext | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    output = sys.stdout if output is None else output
    error = sys.stderr if error is None else error
    try:
        if context is not None:
            context.check_cancelled()
        if args.action == "session":
            from .session import serve_recipe_session
            return serve_recipe_session(args.path, output=output,
                                        check_cancelled=None if context is None else context.check_cancelled)
        if (args.action == "impact" and args.exploration == "complete-finite"
                and (args.max_depth is not None or args.max_nodes is not None)):
            raise RecipeHealthError("complete finite exploration cannot be combined with depth or node bounds")
        if args.action == "import-capture":
            from workbench_api.profile_extensions import require_profile_extension
            from workbench_atlas_categorical_graph import validate_bundle_directory

            adapter = require_profile_extension("workbench.recipe_graphs", args.pack_profile)
            if (type(getattr(adapter, "RECIPE_GRAPH_API_VERSION", None)) is not int
                    or adapter.RECIPE_GRAPH_API_VERSION != 1
                    or not callable(getattr(adapter, "project_capture", None))):
                raise RecipeHealthError("selected profile has no compatible recipe graph API 1 adapter")
            record = adapter.project_capture(
                args.path, args.output, input_manifest=args.input_manifest,
                max_source_bytes=args.max_source_bytes,
                check_cancelled=None if context is None else context.check_cancelled,
            )
            manifest = validate_bundle_directory(args.output)
            if (type(record) is not dict or record.get("state") != "complete"
                    or record.get("graph_set_id") != manifest["graph_set_id"]
                    or record.get("root") != str(args.output.resolve())):
                raise RecipeHealthError("profile returned an invalid recipe graph projection receipt")
            rendered = f"Imported retained recipe capture: {record['graph_set_id']}\n"
        elif args.action == "context":
            record = discover_recipe_health_operational_context(args.path)
            rendered = _render_context(record)
        elif args.action == "index":
            last_percentage = -1

            def progress(update: dict[str, Any]) -> None:
                nonlocal last_percentage
                if context is not None:
                    context.check_cancelled()
                completed = update.get("records_completed", 0)
                total = update.get("records_total", 0)
                percentage = int(completed * 100 / total) if total else 0
                terminal = update.get("phase") in {"verified", "published"}
                if not terminal and percentage // 10 == last_percentage // 10:
                    return
                last_percentage = percentage
                print(
                    "Atlas index progress: "
                    f"{update.get('phase', 'unknown')} {percentage}% "
                    f"({completed}/{total} records)",
                    file=error,
                )

            record = rebuild_recipe_health_index(
                args.path,
                max_source_bytes=args.max_source_bytes,
                max_index_bytes=args.max_index_bytes,
                progress=progress,
            )
            rendered = _render_index_operation(record)
        elif args.action == "assess-plan":
            record = _assess_plan(args, plan_adapter)
            rendered = _render_plan_assessment(record)
        elif args.action == "compare-runtime":
            with open_recipe_health(args.before_path) as before, open_recipe_health(
                args.after_path
            ) as after:
                record = compare_runtime_recipe_graphs(
                    before,
                    after,
                    max_recipes=args.max_recipes,
                    max_recipe_deltas=args.max_recipe_deltas,
                    max_resources=args.max_resources,
                    max_depth=args.max_depth,
                    max_nodes=args.max_nodes,
                )
            rendered = _render_runtime_comparison(record)
        else:
            # Search and inspection each open and verify their explicit graph
            # once, then serve the complete answer from that one view.
            opening = ({"check_cancelled": None if context is None else context.check_cancelled}
                       if args.action == "audit-dead-ends" else {})
            with open_recipe_health(args.path, **opening) as view:
                if args.action == "audit-dead-ends":
                    record = audit_recipe_dead_ends(
                        view, check_cancelled=None if context is None else context.check_cancelled,
                    )
                    rendered = _render_dead_end_audit(record)
                elif args.action == "browse":
                    from .browse import browse_recipe_evidence
                    record = browse_recipe_evidence(view, args.selection_id, offset=args.offset,
                                                   limit=args.limit, expected_graph=args.expect_graph)
                    rendered = (f"{record['selection']['semantic_key']}\n{record['scope']}\n"
                                + "\n".join(f"{row['direction']} {row['relationship']['relation']}: {row['node']['semantic_key']}"
                                            for row in record['links'])
                                + f"\nRelationships {args.offset + len(record['links'])} of {record['page']['total']}\n")
                elif args.action == "search":
                    record = view.search(args.query, limit=args.limit)
                    rendered = _render_search(record)
                elif args.action == "impact":
                    if args.exploration == "complete-finite":
                        record = view.complete_impact(
                            args.selection_id,
                            check_cancelled=None if context is None else context.check_cancelled,
                        )
                    else:
                        record = view.impact(
                            args.selection_id,
                            max_depth=4 if args.max_depth is None else args.max_depth,
                            max_nodes=500 if args.max_nodes is None else args.max_nodes,
                        )
                    rendered = _render_impact(record)
                elif args.action == "routes":
                    from workbench_atlas_recipe_health.routes import RecipeRouteOptions, derive_recipe_routes

                    record = derive_recipe_routes(
                        view,
                        args.selection_id,
                        check_cancelled=None if context is None else context.check_cancelled,
                        options=RecipeRouteOptions(
                            max_depth=args.max_depth,
                            max_resources=args.max_resources,
                            max_recipes=args.max_recipes,
                        ),
                    )
                    rendered = _render_routes(record)
                else:
                    record = view.inspect(args.selection_id)
                    rendered = _render_inspect(record)
        if context is not None:
            context.check_cancelled()
        if args.json:
            _write_json(record, output)
        elif args.action == "audit-dead-ends" and args.csv:
            _write_dead_end_csv(record, output)
        else:
            output.write(rendered)
        return 0
    except (
        OSError,
        UnicodeError,
        RecipeHealthError,
        ValueError,
    ) as exc:
        print(f"Atlas recipes failed: {exc}", file=error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
