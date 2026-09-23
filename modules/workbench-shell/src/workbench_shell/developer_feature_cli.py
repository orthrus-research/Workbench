"""CLI for one current-checkout, reversible developer feature."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .bootstrap import inspect_project
from .developer_feature import (
    DeveloperFeatureError,
    apply_material_fluid_recipe_plan,
    build_material_fluid_recipe_plan,
    default_feature_state_root,
    material_fluid_recipe_options,
    recover_material_fluid_recipe,
    resolve_feature_record,
    retain_feature_record,
    rollback_material_fluid_recipe,
    transaction_state_root,
    verify_material_fluid_recipe_plan,
    workspace_transaction_lock_path,
    workspace_transaction_lock_path_for_uri,
)
from .developer_feature_runtime import (
    DeveloperFeatureRuntimeError,
    run_material_fluid_recipe,
)
from workbench_profile_supersymmetry.examples import (
    EXAMPLE_KEYS,
    PLAN_RESULT_FORMAT,
    DeveloperFeatureExampleError,
    load_feature_examples,
)
from .developer_feature_presentation import (
    COLLECTIONS as PRESENTATION_COLLECTIONS,
    FAMILIES as PRESENTATION_FAMILIES,
    discover_feature_records,
    present_feature_record,
)
from .developer_feature_transaction_view import build_feature_transaction_view
from .developer_feature_workflow_views import (
    OPTIONS_FORMAT,
    build_compact_plan_result,
    project_recipe_options,
)
from .developer_source_feature import (
    QUEST_FAMILY,
    apply_source_feature_plan,
    build_quest_for_process_plan,
    recover_source_feature,
    rollback_source_feature,
    quest_for_process_options,
    verify_source_feature_plan,
)
from workbench_blueprints.profile_construction import recipe_change_authority
RECIPE_FAMILY = "recipe-change"
from .developer_recipe_runtime import run_recipe_change_runtime_comparison


FAMILY = "material-fluid-recipe"
BOUNDARY = (
    "Scope: reversible local experiment. This is not tested-profile support, "
    "stable-standard admission, or release/publication authorization."
)


def _add_family(
    parser: argparse.ArgumentParser,
    *,
    choices: tuple[str, ...] = (FAMILY, RECIPE_FAMILY, QUEST_FAMILY),
) -> None:
    parser.add_argument("family", choices=choices)


def _add_state_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-root",
        type=Path,
        help=(
            "retained developer-feature state (defaults to stable per-user state; "
            "WORKBENCH_STATE_ROOT overrides it)"
        ),
    )


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete machine-readable result",
    )


def _add_plan_json(parser: argparse.ArgumentParser) -> None:
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="emit the complete owner-defined plan, including exact bytes",
    )
    output.add_argument(
        "--compact-json",
        action="store_true",
        help=(
            "emit a small validated plan/retention/review projection without "
            "Base64 operation bytes"
        ),
    )


def _json_object(value: str) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(
            f"ingredient must be one JSON object: {exc.msg}"
        ) from exc
    if type(result) is not dict:
        raise argparse.ArgumentTypeError("ingredient must be one JSON object")
    return result


def _normalized_arguments(argv: Sequence[str]) -> list[str]:
    """Accept the concise public spelling without disturbing family syntax."""

    arguments = list(argv)
    if arguments[:2] == ["plan", "--example"]:
        return ["plan", "example", *arguments[2:]]
    if len(arguments) >= 2 and arguments[0] == "plan" and arguments[1].startswith(
        "--example="
    ):
        key = arguments[1].split("=", 1)[1]
        return ["plan", "example", key, *arguments[2:]]
    return arguments


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench feature",
        description=(
            "Browse packaged examples and retained owner records, or plan, check, "
            "run, apply, recover, and roll back one experimental current-checkout "
            "developer feature."
        ),
        epilog=(
            "Use `workbench studio` for read-only workspace projections and "
            "plan-backed investigation."
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True)

    examples = actions.add_parser(
        "examples",
        help="list the bounded Supersymmetry Blueprint examples shipped with Workbench",
    )
    examples.add_argument(
        "selector",
        nargs="?",
        help="optional feature family or exact example key",
    )
    _add_json(examples)

    records = actions.add_parser(
        "records",
        help="discover retained feature records for native IDE navigation",
    )
    records.add_argument(
        "family",
        nargs="?",
        choices=PRESENTATION_FAMILIES,
        help="optional feature-family filter",
    )
    records.add_argument(
        "collection",
        nargs="?",
        choices=PRESENTATION_COLLECTIONS,
        help="optional retained-record collection filter",
    )
    _add_state_root(records)
    _add_json(records)

    present = actions.add_parser(
        "present",
        help="project one retained owner record for a thin IDE client",
    )
    _add_family(present)
    present.add_argument("collection", choices=PRESENTATION_COLLECTIONS)
    present.add_argument("record", help="retained record ID or path")
    _add_state_root(present)
    _add_json(present)

    transaction_view = actions.add_parser(
        "transaction",
        help="show compact retained lineage and current workspace byte state",
    )
    _add_family(transaction_view)
    transaction_view.add_argument(
        "plan",
        help="plan retained in the selected state root (ID or record path)",
    )
    _add_state_root(transaction_view)
    _add_json(transaction_view)

    options = actions.add_parser(
        "options", help="list current recipe owners or bounded quest owners"
    )
    _add_family(options)
    options.add_argument("workspace", type=Path)
    options.add_argument(
        "--query",
        help=(
            "case-insensitive quest ID/title, recipe-map, registry-name, or "
            "recipe-owner path search"
        ),
    )
    options.add_argument(
        "--limit",
        type=int,
        default=None,
        help="maximum returned matches per option collection from 1 to 200",
    )
    _add_json(options)

    plan = actions.add_parser(
        "plan",
        help="build and retain an exact source-edit plan",
        epilog=(
            "Shortcut: `workbench feature plan --example KEY WORKSPACE` is "
            "equivalent to the `plan example KEY WORKSPACE` form."
        ),
    )
    plan_families = plan.add_subparsers(dest="family", required=True)
    material_plan = plan_families.add_parser(
        FAMILY,
        help="add one material, fluid, localization entry, and machine recipe",
    )
    material_plan.add_argument("workspace", type=Path)
    material_plan.add_argument("--name", required=True)
    material_plan.add_argument("--color", required=True)
    material_plan.add_argument("--recipe-script", required=True)
    material_plan.add_argument("--recipe-map", required=True)
    material_plan.add_argument("--input-fluid", required=True)
    material_plan.add_argument("--input-amount", type=int, default=1000)
    material_plan.add_argument("--output-amount", type=int, default=1000)
    material_plan.add_argument("--duration", type=int, default=200)
    material_plan.add_argument("--voltage-tier", default="LV")
    material_plan.add_argument("--translation")
    material_plan.add_argument("--symbol")
    _add_state_root(material_plan)
    material_plan.add_argument(
        "--show-diff",
        action="store_true",
        help="show the reviewed unified diff in human output",
    )
    _add_plan_json(material_plan)

    recipe_plan = plan_families.add_parser(
        RECIPE_FAMILY,
        help="add one exact machine recipe to an existing owner script",
    )
    recipe_plan.add_argument("workspace", type=Path)
    recipe_plan.add_argument("--mutation", choices=("add",), default="add")
    recipe_plan.add_argument("--recipe-script", required=True)
    recipe_plan.add_argument("--recipe-map", required=True)
    recipe_plan.add_argument(
        "--item-input",
        action="append",
        default=[],
        type=_json_object,
        metavar="JSON",
    )
    recipe_plan.add_argument(
        "--fluid-input",
        action="append",
        default=[],
        type=_json_object,
        metavar="JSON",
    )
    recipe_plan.add_argument(
        "--item-output",
        action="append",
        default=[],
        type=_json_object,
        metavar="JSON",
    )
    recipe_plan.add_argument(
        "--fluid-output",
        action="append",
        default=[],
        type=_json_object,
        metavar="JSON",
    )
    recipe_plan.add_argument("--duration", required=True, type=int)
    recipe_plan.add_argument("--voltage-tier", required=True)
    _add_state_root(recipe_plan)
    recipe_plan.add_argument(
        "--show-diff",
        action="store_true",
        help="show the reviewed unified diff in human output",
    )
    _add_plan_json(recipe_plan)

    quest_plan = plan_families.add_parser(
        QUEST_FAMILY,
        help="edit one existing quest prerequisite and/or localization",
    )
    quest_plan.add_argument("workspace", type=Path)
    quest_plan.add_argument("--quest-id", required=True, type=int)
    quest_plan.add_argument("--add-prerequisite-id", type=int)
    quest_plan.add_argument(
        "--requirement-type",
        choices=("NORMAL", "IMPLICIT", "HIDDEN"),
        default="IMPLICIT",
    )
    quest_plan.add_argument("--title")
    quest_plan.add_argument("--description")
    _add_state_root(quest_plan)
    quest_plan.add_argument(
        "--show-diff",
        action="store_true",
        help="show the reviewed unified diff in human output",
    )
    _add_plan_json(quest_plan)

    example_plan = plan_families.add_parser(
        "example",
        help=(
            "translate one allowlisted packaged example through its current "
            "owner planner"
        ),
    )
    example_plan.add_argument("example_key", choices=EXAMPLE_KEYS)
    example_plan.add_argument("workspace", type=Path)
    _add_state_root(example_plan)
    example_plan.add_argument(
        "--show-diff",
        action="store_true",
        help="show the owner-validated unified diff in human output",
    )
    _add_plan_json(example_plan)

    check = actions.add_parser("check", help="regenerate a retained plan for staleness")
    _add_family(check)
    check.add_argument("plan", help="retained plan ID or record path")
    _add_state_root(check)
    _add_json(check)

    run = actions.add_parser(
        "run",
        help="observe one reviewed plan in a disposable Cleanroom client",
    )
    _add_family(run, choices=(FAMILY,))
    run.add_argument("plan", help="retained plan ID or record path")
    run.add_argument(
        "--consent",
        required=True,
        help="exact reviewed plan ID authorizing this disposable runtime run",
    )
    run.add_argument("--launcher", choices=("prism", "multimc"), default="prism")
    run.add_argument("--launcher-executable", required=True, type=Path)
    run.add_argument("--launcher-root", required=True, type=Path)
    run.add_argument("--launcher-profile")
    run.add_argument("--launcher-java", type=Path)
    run.add_argument("--launcher-java-state", type=Path)
    run.add_argument("--packwiz-executable", type=Path)
    run.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help="existing payload root used only for hash-matched download seeding",
    )
    run.add_argument("--memory-mib", type=int, default=8192)
    run.add_argument("--offline-name", default="Workbench")
    run.add_argument("--timeout", type=float, default=600.0)
    run.add_argument("--attach-timeout", type=float, default=120.0)
    run.add_argument("--session-timeout", type=float, default=21_600.0)
    _add_state_root(run)
    _add_json(run)

    compare_runtime = actions.add_parser(
        "compare-runtime",
        help="compare unchanged and reviewed recipe projections in fresh clients",
    )
    _add_family(compare_runtime, choices=(RECIPE_FAMILY,))
    compare_runtime.add_argument(
        "plan", help="retained recipe-change plan ID or record path"
    )
    compare_runtime.add_argument(
        "--consent",
        required=True,
        help="exact reviewed plan ID authorizing both disposable runtime sides",
    )
    compare_runtime.add_argument(
        "--order",
        choices=("baseline-first", "candidate-first"),
        default="baseline-first",
        help="explicit one-pair execution order retained as a confound",
    )
    compare_runtime.add_argument(
        "--launcher", choices=("prism", "multimc"), default="prism"
    )
    compare_runtime.add_argument("--launcher-executable", required=True, type=Path)
    compare_runtime.add_argument("--launcher-root", required=True, type=Path)
    compare_runtime.add_argument("--launcher-profile")
    compare_runtime.add_argument("--launcher-java", type=Path)
    compare_runtime.add_argument("--launcher-java-state", type=Path)
    compare_runtime.add_argument("--packwiz-executable", type=Path)
    compare_runtime.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help="existing payload root used only for hash-matched download seeding",
    )
    compare_runtime.add_argument("--memory-mib", type=int, default=8192)
    compare_runtime.add_argument("--offline-name", default="Workbench")
    compare_runtime.add_argument("--timeout", type=float, default=600.0)
    compare_runtime.add_argument("--attach-timeout", type=float, default=120.0)
    compare_runtime.add_argument("--session-timeout", type=float, default=21_600.0)
    _add_state_root(compare_runtime)
    _add_json(compare_runtime)

    apply = actions.add_parser("apply", help="apply one exact reviewed plan locally")
    _add_family(apply)
    apply.add_argument("plan", help="retained plan ID or record path")
    apply.add_argument(
        "--consent",
        required=True,
        help="exact reviewed plan ID authorizing this local application",
    )
    _add_state_root(apply)
    _add_json(apply)

    rollback = actions.add_parser(
        "rollback", help="restore a locally applied plan from its receipt"
    )
    _add_family(rollback)
    rollback.add_argument("plan", help="retained plan ID or record path")
    rollback.add_argument("receipt", help="retained application receipt ID or path")
    _add_state_root(rollback)
    _add_json(rollback)

    recover = actions.add_parser(
        "recover",
        help="finish or restore one transaction interrupted by process death",
    )
    _add_family(recover)
    recover.add_argument("plan", help="retained plan ID or record path")
    _add_state_root(recover)
    _add_json(recover)
    return parser


def _emit_json(value: Mapping[str, Any]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _state_root(args: argparse.Namespace, suite_root: Path) -> Path:
    return (
        default_feature_state_root(suite_root)
        if args.state_root is None
        else args.state_root.expanduser().resolve()
    )


def _human_options(family: str, value: Mapping[str, Any]) -> None:
    if family == QUEST_FAMILY:
        print(
            f"{family} options: {str(value['state']).upper()} "
            f"({value['returned_count']} of {value['matched_count']} matches; "
            f"{value['total_count']} total)"
        )
        for option in value["options"]:
            title = option["localized_title"] or (
                f"<{option['localization_state']}: {option['title_key']}>"
            )
            print(f"  {option['quest_id']}: {title}")
            print(f"    Owner: {option['path']}")
            prerequisites = option["current_prerequisites"]
            if prerequisites:
                rendered = ", ".join(
                    f"{row['quest_id']} ({row['requirement_type']})"
                    for row in prerequisites
                )
                print(f"    Prerequisites: {rendered}")
            else:
                print("    Prerequisites: none")
        if value["truncated"]:
            print("Result is truncated; refine --query or raise --limit.")
        print(BOUNDARY)
        return
    if value.get("format") == OPTIONS_FORMAT:
        recipe_maps = value["counts"]["recipe_maps"]
        scripts = value["counts"]["scripts"]
        query = "all options" if value["query"] is None else repr(value["query"])
        print(
            f"{family} options: {str(value['state']).upper()} "
            f"(query {query}; limit {value['limit']} per collection)"
        )
        print(
            "Recipe maps "
            f"({recipe_maps['returned']} of {recipe_maps['matched']} matches; "
            f"{recipe_maps['total']} total):"
        )
        for option in value["recipe_maps"]:
            print(f"  {option['alias']} -> {option['registry_name']}")
        print(
            "Recipe scripts "
            f"({scripts['returned']} of {scripts['matched']} matches; "
            f"{scripts['total']} total):"
        )
        for script in value["scripts"]:
            print(f"  {script}")
        if value["truncated"]:
            print("Result is truncated; refine --query or raise --limit.")
        print(BOUNDARY)
        return
    print(f"{family} options: {str(value['state']).upper()}")
    print(f"Recipe maps ({len(value['recipe_maps'])}):")
    for option in value["recipe_maps"]:
        print(f"  {option['alias']} -> {option['registry_name']}")
    print(f"Recipe scripts ({len(value['scripts'])}):")
    for script in value["scripts"]:
        print(f"  {script}")
    print(BOUNDARY)


def _human_examples(value: Mapping[str, Any]) -> None:
    print(
        f"Supersymmetry Blueprint examples: {str(value['state']).upper()} "
        f"({value['count']})"
    )
    for example in value["examples"]:
        print(f"  {example['family']}: {example['example_key']}")
        print(f"    Runtime evidence: {example['runtime_evidence_state']}")
        print(f"    Packaged source: {example['source_path']}")
    print(
        "Boundary: read-only, non-identity-bearing examples; no profile support, "
        "action, release, or publication authorization."
    )


def _human_presentation(value: Mapping[str, Any]) -> None:
    owner = value["owner_record"]
    print(
        f"{value['family']} {value['collection']} presentation: "
        f"{str(owner['state']).upper()}"
    )
    print(f"Plan: {value['plan_id']}")
    print(f"Record: {owner['id']}")
    print(f"Changed files: {len(value['operations'])}")
    print(f"Verification: {str(value['verification']['state']).upper()}")
    print(BOUNDARY)


def _human_records(value: Mapping[str, Any]) -> None:
    print(f"Retained developer-feature records: {len(value['records'])}")
    print(f"State root: {value['state_root_uri']}")
    for record in value["records"]:
        print(
            f"  {record['family']} {record['collection']} "
            f"{record['record_state']}: {record['record_id']}"
        )
        print(
            f"    {record['operation_count']} operation(s); "
            f"verification {record['verification_state']}"
        )
        print(f"    Workspace: {record['workspace_uri']}")
    for limitation in value["limitations"]:
        print(f"Boundary: {limitation}")


def _human_transaction(value: Mapping[str, Any]) -> None:
    print(
        "Developer-feature transaction: "
        f"{str(value['current_effective_state']).upper()}"
    )
    print(f"Plan: {value['plan_id']}")
    print(f"Workspace: {value['workspace_uri']}")
    print(f"Workspace bytes: {value['workspace_match']['state']}")
    print(f"Plan freshness: {value['plan_freshness']['state']}")
    print("Retained lineage:")
    for record in value["records"]:
        print(
            f"  {record['collection']}: {record['record_state']} "
            f"({record['record_id']})"
        )
        if record["diagnostic_code"] is not None:
            print(f"    Diagnostic: {record['diagnostic_code']}")
    print("Reviewed operations:")
    for operation in value["operations"]:
        match = value["workspace_match"]["operations"]
        observed = next(
            (row for row in match if row["ordinal"] == operation["ordinal"]),
            None,
        )
        observed_state = (
            observed["state"] if observed is not None else "workspace-unavailable"
        )
        print(
            f"  [{operation['ordinal']}] {operation['path']} "
            f"({operation['role']}; {observed_state})"
        )
        print(
            f"    before {operation['before_sha256']} ({operation['before_size']} bytes)"
        )
        print(
            f"    after  {operation['after_sha256']} ({operation['after_size']} bytes)"
        )
        print(operation["diff"], end="" if operation["diff"].endswith("\n") else "\n")
    print("Eligible next actions (revalidated by the owner when invoked):")
    for action in value["actions"]:
        state = "available" if action["available"] else "unavailable"
        print(f"  {action['action']}: {state}")
        if action["reason"] is not None:
            print(f"    {action['reason']}")
        if action["consent_id"] is not None:
            print(f"    Consent: {action['consent_id']}")
        if action["record_id"] is not None:
            print(f"    Record: {action['record_id']}")
    for limitation in value["limitations"]:
        print(f"Boundary: {limitation}")


def _human_plan(
    family: str,
    value: Mapping[str, Any],
    retained: Path,
    *,
    show_diff: bool,
) -> None:
    review = value["review"]
    print(f"{family} plan: {str(value['state']).upper()}")
    print(f"Plan: {value['id']}")
    print(f"Retained plan: {retained}")
    print(
        f"Review: {len(review['changed_files'])} file(s), "
        f"+{review['additions']}/-{review['deletions']}"
    )
    print(BOUNDARY)
    if show_diff:
        print(review["unified_diff"], end="" if review["unified_diff"].endswith("\n") else "\n")


def _human_example_plan(
    value: Mapping[str, Any],
    retained: Path | None,
    *,
    show_diff: bool,
) -> None:
    example = value["example"]
    historical = value["applicability"]["historical"]
    current = value["applicability"]["current"]
    print(f"Packaged example plan: {str(value['state']).upper()}")
    print(f"Example: {example['example_key']} ({example['family']})")
    print(
        "Historical applicability: "
        f"{historical['state']} ({historical['workspace_relation']})"
    )
    print(
        "Current applicability: "
        f"{current['state']} at {current['workspace_revision']}"
    )
    if value["state"] != "ready":
        print(f"Reason: {current['reason']}")
        print(
            "No plan was retained; choose a current request and run its family "
            "planner."
        )
        print(BOUNDARY)
        return
    if current["owner_derived_differences"]:
        changed = ", ".join(
            row["field"] for row in current["owner_derived_differences"]
        )
        print(f"Owner-derived values adapted to this checkout: {changed}")
    _human_plan(
        example["family"],
        value["plan"],
        retained,
        show_diff=show_diff,
    )


def _human_check(family: str, value: Mapping[str, Any]) -> None:
    print(f"{family} check: {str(value['state']).upper()}")
    print(f"Plan: {value['plan_id']}")
    if value["reason"] is not None:
        print(f"Reason: {value['reason']}")
    print(BOUNDARY)


def _human_receipt(
    family: str,
    label: str,
    value: Mapping[str, Any],
    retained: Path,
) -> None:
    print(f"{family} {label}: {str(value['state']).upper()}")
    print(f"Plan: {value['plan_id']}")
    print(f"Receipt: {value['id']}")
    print(f"Retained receipt: {retained}")
    if value.get("diagnostic_code") is not None:
        print(f"Diagnostic: {value['diagnostic_code']}")
    print(BOUNDARY)


def _current_workspace_revision(suite: Path, workspace: Path) -> str:
    inspection = inspect_project(suite, workspace)
    context = inspection.get("workspace_context")
    observed = context.get("workspace") if type(context) is dict else None
    revision = observed.get("revision") if type(observed) is dict else None
    if type(revision) is not str or not revision:
        raise DeveloperFeatureExampleError(
            "current example target lacks an exact workspace revision"
        )
    return revision


def _historical_applicability(
    record: Mapping[str, Any],
    *,
    workspace_revision: str,
) -> dict[str, Any]:
    reference = record.get("source_reference")
    if reference is None:
        return {
            "effective_base_revision": None,
            "path": None,
            "pull_request": None,
            "repository": None,
            "result_revision": None,
            "state": "not-declared",
            "workspace_relation": "current-owner-validation-only",
        }
    expected = {
        "after_blob_sha1",
        "before_blob_sha1",
        "effective_base_revision",
        "path",
        "pull_request",
        "pull_request_url",
        "repository",
        "result_revision",
    }
    if (
        type(reference) is not dict
        or set(reference) != expected
        or type(reference.get("pull_request")) is not int
        or type(reference.get("pull_request")) is bool
        or any(
            type(reference.get(field)) is not str or not reference[field]
            for field in expected - {"pull_request"}
        )
    ):
        raise DeveloperFeatureExampleError(
            "packaged Blueprint example historical reference is invalid"
        )
    relation = (
        "exact-effective-base"
        if workspace_revision == reference["effective_base_revision"]
        else "exact-historical-result"
        if workspace_revision == reference["result_revision"]
        else "different-current-revision"
    )
    return {
        "effective_base_revision": reference["effective_base_revision"],
        "path": reference["path"],
        "pull_request": reference["pull_request"],
        "repository": reference["repository"],
        "result_revision": reference["result_revision"],
        "state": "provenance-only",
        "workspace_relation": relation,
    }


def _build_applicable_example_plan(
    suite: Path,
    workspace: Path,
    *,
    example_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = load_feature_examples(suite, selector=example_key)
    if catalog.get("count") != 1:
        raise DeveloperFeatureExampleError(
            "exact packaged Blueprint example selection changed"
        )
    example = catalog["examples"][0]
    family = example["family"]
    request = example["record"]["request"]
    if family == FAMILY:
        plan = build_material_fluid_recipe_plan(
            suite,
            workspace,
            name=request["name"],
            color=request["color"],
            recipe_script=request["recipe_script"],
            recipe_map=request["recipe_map"],
            input_fluid=request["input_fluid"],
            input_amount=request["input_amount"],
            output_amount=request["output_amount"],
            duration=request["duration"],
            voltage_tier=request["voltage_tier"],
            translation=request["translation"],
            symbol=request["symbol"],
        )
        verification = verify_material_fluid_recipe_plan(suite, plan)
        owner_inputs = {
            field
            for field in request
            if field
            not in {"material_id", "recipe_map_registry_name", "registry_name"}
        }
    elif family == RECIPE_FAMILY:
        authority = recipe_change_authority("supersymmetry")
        plan = authority.build_recipe_change_plan(
            suite,
            workspace,
            mutation=request["mutation"],
            recipe_script=request["script"],
            recipe_map=request["recipe_map"],
            item_inputs=request["item_inputs"],
            fluid_inputs=request["fluid_inputs"],
            item_outputs=request["item_outputs"],
            fluid_outputs=request["fluid_outputs"],
            duration=request["duration"],
            voltage_tier=request["voltage_tier"],
        )
        verification = authority.verify_recipe_change_plan(suite, plan)
        owner_inputs = set(request)
    elif family == QUEST_FAMILY:
        plan = build_quest_for_process_plan(
            suite,
            workspace,
            quest_id=request["quest_id"],
            add_prerequisite_id=request["add_prerequisite_id"],
            requirement_type=request["requirement_type"],
            title=request["title"],
            description=request["description"],
        )
        verification = verify_source_feature_plan(suite, plan)
        owner_inputs = set(request)
    else:  # pragma: no cover - descriptor and loader are closed above
        raise DeveloperFeatureExampleError(
            f"unsupported packaged Blueprint example family: {family}"
        )
    translated_request = plan.get("request")
    if type(translated_request) is not dict or any(
        translated_request.get(field) != request[field]
        for field in owner_inputs
    ):
        raise DeveloperFeatureExampleError(
            "current owner planner changed a packaged example input"
        )
    if verification.get("state") != "ready":
        raise DeveloperFeatureExampleError(
            "current owner plan was not reproducible after example translation"
        )
    workspace_revision = _current_workspace_revision(suite, workspace)
    derived_differences = [
        {
            "current": translated_request.get(field),
            "field": field,
            "packaged": request[field],
        }
        for field in sorted(set(request) - owner_inputs)
        if translated_request.get(field) != request[field]
    ]
    result = {
        "applicability": {
            "current": {
                "owner_derived_differences": derived_differences,
                "plan_id": plan["id"],
                "reason": None,
                "state": "owner-plan-validated",
                "verification_format": verification["format"],
                "workspace_revision": workspace_revision,
            },
            "historical": _historical_applicability(
                example["record"],
                workspace_revision=workspace_revision,
            ),
        },
        "authority_boundary": {
            "apply_requires_exact_plan_consent": True,
            "construction_authority": "existing-family-planner",
            "packaged_example_authorizes_action": False,
            "profile_support_claimed": False,
            "release_or_publication_authorized": False,
        },
        "example": {
            "example_key": example["example_key"],
            "family": family,
            "runtime_evidence_state": example["runtime_evidence_state"],
            "source_path": example["source_path"],
            "source_sha256": example["source_sha256"],
        },
        "format": PLAN_RESULT_FORMAT,
        "plan": plan,
        "retained_plan_uri": None,
        "schema_version": 1,
        "state": "ready",
    }
    return result, plan


def _build_example_plan(
    suite: Path,
    workspace: Path,
    *,
    example_key: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    catalog = load_feature_examples(suite, selector=example_key)
    if catalog.get("count") != 1:
        raise DeveloperFeatureExampleError(
            "exact packaged Blueprint example selection changed"
        )
    example = catalog["examples"][0]
    workspace_revision = _current_workspace_revision(suite, workspace)
    try:
        return _build_applicable_example_plan(
            suite,
            workspace,
            example_key=example_key,
        )
    except DeveloperFeatureExampleError:
        raise
    except (DeveloperFeatureError, OSError, ValueError) as exc:
        result = {
            "applicability": {
                "current": {
                    "owner_derived_differences": [],
                    "plan_id": None,
                    "reason": str(exc),
                    "state": "owner-plan-rejected",
                    "verification_format": None,
                    "workspace_revision": workspace_revision,
                },
                "historical": _historical_applicability(
                    example["record"],
                    workspace_revision=workspace_revision,
                ),
            },
            "authority_boundary": {
                "apply_requires_exact_plan_consent": True,
                "construction_authority": "existing-family-planner",
                "packaged_example_authorizes_action": False,
                "profile_support_claimed": False,
                "release_or_publication_authorized": False,
            },
            "example": {
                "example_key": example["example_key"],
                "family": example["family"],
                "runtime_evidence_state": example["runtime_evidence_state"],
                "source_path": example["source_path"],
                "source_sha256": example["source_sha256"],
            },
            "format": PLAN_RESULT_FORMAT,
            "plan": None,
            "retained_plan_uri": None,
            "schema_version": 1,
            "state": "not-applicable",
        }
        return result, None


def main(
    argv: Sequence[str] | None = None,
    *,
    suite_root: Path | str | None = None,
) -> int:
    args = build_parser().parse_args(
        _normalized_arguments(sys.argv[1:] if argv is None else argv)
    )
    suite = (
        _repository_resource_root(__file__)
        if suite_root is None
        else Path(suite_root).expanduser().resolve()
    )

    def retain_committed_receipt(
        state_root: Path,
        receipt: Mapping[str, Any],
    ) -> None:
        try:
            retain_feature_record(state_root, "receipts", receipt)
        except (DeveloperFeatureError, OSError) as exc:
            try:
                observed = resolve_feature_record(
                    state_root,
                    "receipts",
                    str(receipt.get("id", "")),
                )
            except (DeveloperFeatureError, OSError):
                raise exc
            if observed != dict(receipt):
                raise exc

    try:
        if args.action == "examples":
            result = load_feature_examples(suite, selector=args.selector)
            retained = None
        elif args.action == "records":
            state_root = _state_root(args, suite)
            result = discover_feature_records(
                suite,
                state_root,
                family=args.family,
                collection=args.collection,
            )
            retained = None
        elif args.action == "present":
            state_root = _state_root(args, suite)
            result = present_feature_record(
                suite,
                state_root,
                family=args.family,
                collection=args.collection,
                reference=args.record,
            )
            retained = None
        elif args.action == "transaction":
            state_root = _state_root(args, suite)
            result = build_feature_transaction_view(
                suite,
                state_root,
                family=args.family,
                plan_reference=args.plan,
            )
            retained = None
        elif args.action == "options":
            if args.family == QUEST_FAMILY:
                result = quest_for_process_options(
                    suite,
                    args.workspace,
                    query=args.query,
                    limit=50 if args.limit is None else args.limit,
                )
            elif args.family == FAMILY:
                owner_options = material_fluid_recipe_options(suite, args.workspace)
                result = (
                    owner_options
                    if args.query is None and args.limit is None
                    else project_recipe_options(
                        args.family,
                        owner_options,
                        query=args.query,
                        limit=50 if args.limit is None else args.limit,
                    )
                )
            else:
                owner_options = recipe_change_authority("supersymmetry").recipe_change_options(suite, args.workspace)
                result = (
                    owner_options
                    if args.query is None and args.limit is None
                    else project_recipe_options(
                        args.family,
                        owner_options,
                        query=args.query,
                        limit=50 if args.limit is None else args.limit,
                    )
                )
            retained = None
        elif args.action == "plan":
            state_root = _state_root(args, suite)
            if args.family == "example":
                result, owner_plan = _build_example_plan(
                    suite,
                    args.workspace,
                    example_key=args.example_key,
                )
                retained = (
                    None
                    if owner_plan is None
                    else retain_feature_record(state_root, "plans", owner_plan)
                )
                if retained is not None:
                    result["retained_plan_uri"] = retained.as_uri()
            elif args.family == FAMILY:
                result = build_material_fluid_recipe_plan(
                    suite,
                    args.workspace,
                    name=args.name,
                    color=args.color,
                    recipe_script=args.recipe_script,
                    recipe_map=args.recipe_map,
                    input_fluid=args.input_fluid,
                    input_amount=args.input_amount,
                    output_amount=args.output_amount,
                    duration=args.duration,
                    voltage_tier=args.voltage_tier,
                    translation=args.translation,
                    symbol=args.symbol,
                )
            elif args.family == RECIPE_FAMILY:
                result = recipe_change_authority("supersymmetry").build_recipe_change_plan(
                    suite,
                    args.workspace,
                    mutation=args.mutation,
                    recipe_script=args.recipe_script,
                    recipe_map=args.recipe_map,
                    item_inputs=args.item_input,
                    fluid_inputs=args.fluid_input,
                    item_outputs=args.item_output,
                    fluid_outputs=args.fluid_output,
                    duration=args.duration,
                    voltage_tier=args.voltage_tier,
                )
            else:
                result = build_quest_for_process_plan(
                    suite,
                    args.workspace,
                    quest_id=args.quest_id,
                    add_prerequisite_id=args.add_prerequisite_id,
                    requirement_type=args.requirement_type,
                    title=args.title,
                    description=args.description,
                )
            if args.family != "example":
                retained = retain_feature_record(state_root, "plans", result)
            if args.compact_json:
                compact_family = (
                    result["example"]["family"]
                    if args.family == "example"
                    else args.family
                )
                compact_owner_plan = owner_plan if args.family == "example" else result
                compact_transaction = (
                    None
                    if compact_owner_plan is None
                    else build_feature_transaction_view(
                        suite,
                        state_root,
                        family=compact_family,
                        plan_reference=compact_owner_plan["id"],
                    )
                )
                result = build_compact_plan_result(
                    family=compact_family,
                    owner_plan=compact_owner_plan,
                    retained_plan_uri=(
                        None if retained is None else retained.as_uri()
                    ),
                    transaction_view=compact_transaction,
                    example_result=result if args.family == "example" else None,
                )
        elif args.action == "check":
            state_root = _state_root(args, suite)
            plan = resolve_feature_record(state_root, "plans", args.plan)
            if args.family == FAMILY:
                result = verify_material_fluid_recipe_plan(suite, plan)
            elif args.family == RECIPE_FAMILY:
                result = recipe_change_authority("supersymmetry").verify_recipe_change_plan(suite, plan)
            else:
                result = verify_source_feature_plan(suite, plan)
            retained = None
        elif args.action == "run":
            state_root = _state_root(args, suite)
            plan = resolve_feature_record(state_root, "plans", args.plan)
            retain_feature_record(state_root, "plans", plan)
            result = run_material_fluid_recipe(
                suite,
                plan,
                state_root,
                consent_plan_id=args.consent,
                launcher=args.launcher,
                launcher_executable=args.launcher_executable,
                launcher_root=args.launcher_root,
                launcher_profile=args.launcher_profile,
                launcher_java=args.launcher_java,
                launcher_java_state=args.launcher_java_state,
                packwiz_executable=args.packwiz_executable,
                seed_roots=args.seed,
                memory_mib=args.memory_mib,
                offline_name=args.offline_name,
                timeout_seconds=args.timeout,
                attach_timeout=args.attach_timeout,
                session_timeout=args.session_timeout,
            )
            retained = retain_feature_record(state_root, "runs", result)
        elif args.action == "compare-runtime":
            state_root = _state_root(args, suite)
            plan = resolve_feature_record(state_root, "plans", args.plan)
            retain_feature_record(state_root, "plans", plan)
            result = run_recipe_change_runtime_comparison(
                suite,
                plan,
                state_root,
                consent_plan_id=args.consent,
                launcher=args.launcher,
                order=args.order,
                launcher_executable=args.launcher_executable,
                launcher_root=args.launcher_root,
                launcher_profile=args.launcher_profile,
                launcher_java=args.launcher_java,
                launcher_java_state=args.launcher_java_state,
                packwiz_executable=args.packwiz_executable,
                seed_roots=args.seed,
                memory_mib=args.memory_mib,
                offline_name=args.offline_name,
                timeout_seconds=args.timeout,
                attach_timeout=args.attach_timeout,
                session_timeout=args.session_timeout,
            )
            retained = retain_feature_record(state_root, "runs", result)
        elif args.action == "apply":
            state_root = _state_root(args, suite)
            plan = resolve_feature_record(state_root, "plans", args.plan)
            retain_feature_record(state_root, "plans", plan)
            if args.consent != plan.get("id"):
                raise DeveloperFeatureError(
                    "apply requires consent to the exact retained plan ID"
                )

            transaction = transaction_state_root(state_root, plan.get("id", ""))
            if args.family == FAMILY:
                result = apply_material_fluid_recipe_plan(
                    suite,
                    plan,
                    transaction,
                    consent_plan_id=args.consent,
                    transaction_lock=workspace_transaction_lock_path(plan),
                    commit_receipt=lambda receipt: retain_committed_receipt(
                        state_root,
                        receipt,
                    ),
                )
            elif args.family == RECIPE_FAMILY:
                result = recipe_change_authority("supersymmetry").apply_recipe_change_plan(
                    suite,
                    plan,
                    transaction,
                    consent_plan_id=args.consent,
                    transaction_lock=workspace_transaction_lock_path_for_uri(
                        plan.get("workspace_uri", "")
                    ),
                    commit_receipt=lambda receipt: retain_committed_receipt(
                        state_root,
                        receipt,
                    ),
                )
            else:
                result = apply_source_feature_plan(
                    suite,
                    plan,
                    transaction,
                    consent_plan_id=args.consent,
                    transaction_lock=workspace_transaction_lock_path_for_uri(
                        plan.get("workspace_uri", "")
                    ),
                    commit_receipt=lambda receipt: retain_committed_receipt(
                        state_root,
                        receipt,
                    ),
                )
            retained = retain_feature_record(state_root, "receipts", result)
        elif args.action == "rollback":
            state_root = _state_root(args, suite)
            plan = resolve_feature_record(state_root, "plans", args.plan)
            retain_feature_record(state_root, "plans", plan)
            receipt = resolve_feature_record(state_root, "receipts", args.receipt)
            transaction = transaction_state_root(
                state_root,
                plan.get("id", ""),
                create=False,
            )
            if args.family == FAMILY:
                result = rollback_material_fluid_recipe(
                    plan,
                    transaction,
                    application_receipt=receipt,
                    transaction_lock=workspace_transaction_lock_path(plan),
                )
            elif args.family == RECIPE_FAMILY:
                result = recipe_change_authority("supersymmetry").rollback_recipe_change(
                    plan,
                    transaction,
                    application_receipt=receipt,
                    transaction_lock=workspace_transaction_lock_path_for_uri(
                        plan.get("workspace_uri", "")
                    ),
                )
            else:
                result = rollback_source_feature(
                    plan,
                    transaction,
                    suite_root=suite,
                    application_receipt=receipt,
                    transaction_lock=workspace_transaction_lock_path_for_uri(
                        plan.get("workspace_uri", "")
                    ),
                )
            retained = retain_feature_record(state_root, "rollbacks", result)
        else:
            state_root = _state_root(args, suite)
            plan = resolve_feature_record(state_root, "plans", args.plan)
            retain_feature_record(state_root, "plans", plan)
            transaction = transaction_state_root(
                state_root,
                plan.get("id", ""),
                create=False,
            )
            if args.family == FAMILY:
                result = recover_material_fluid_recipe(
                    plan,
                    transaction,
                    transaction_lock=workspace_transaction_lock_path(plan),
                    commit_receipt=lambda receipt: retain_committed_receipt(
                        state_root,
                        receipt,
                    ),
                )
            elif args.family == RECIPE_FAMILY:
                result = recipe_change_authority("supersymmetry").recover_recipe_change(
                    plan,
                    transaction,
                    transaction_lock=workspace_transaction_lock_path_for_uri(
                        plan.get("workspace_uri", "")
                    ),
                    commit_receipt=lambda receipt: retain_committed_receipt(
                        state_root,
                        receipt,
                    ),
                )
            else:
                result = recover_source_feature(
                    plan,
                    transaction,
                    suite_root=suite,
                    transaction_lock=workspace_transaction_lock_path_for_uri(
                        plan.get("workspace_uri", "")
                    ),
                    commit_receipt=lambda receipt: retain_committed_receipt(
                        state_root,
                        receipt,
                    ),
                )
            if result.get("application_receipt") is not None:
                retain_feature_record(
                    state_root,
                    "receipts",
                    result["application_receipt"],
                )
            retained = retain_feature_record(state_root, "recoveries", result)
    except (DeveloperFeatureError, DeveloperFeatureRuntimeError, OSError, ValueError) as exc:
        print(f"Workbench feature failed: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "compact_json", False):
        _emit_json(result)
    elif args.json:
        _emit_json(result)
    elif args.action == "examples":
        _human_examples(result)
    elif args.action == "records":
        _human_records(result)
    elif args.action == "present":
        _human_presentation(result)
    elif args.action == "transaction":
        _human_transaction(result)
    elif args.action == "options":
        _human_options(args.family, result)
    elif args.action == "plan":
        if args.family == "example":
            _human_example_plan(result, retained, show_diff=args.show_diff)
        else:
            _human_plan(args.family, result, retained, show_diff=args.show_diff)
    elif args.action == "check":
        _human_check(args.family, result)
    elif args.action in {"run", "compare-runtime"}:
        _human_receipt(args.family, args.action, result, retained)
    else:
        _human_receipt(args.family, args.action, result, retained)

    if args.action == "check" and result["state"] != "ready":
        return 1
    if (
        args.action == "plan"
        and args.family == "example"
        and result["state"] != "ready"
    ):
        return 1
    if args.action in {"apply", "rollback"} and result["state"] == "rejected":
        return 1
    if args.action == "recover" and result["state"] == "review-required":
        return 1
    if args.action in {"run", "compare-runtime"} and result["state"] != "complete":
        return 1
    return 0


__all__ = ["build_parser", "main"]
