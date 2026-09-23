"""CLI and service-callable composition over one explicit developer selection."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from workbench_api.resources import repository_root
from workbench_api.state_paths import default_product_spine_state_root

from .developer_context import (
    DeveloperContextError,
    DeveloperSelection,
    observe_developer_context,
    retain_selection,
    selected_context,
    verify_developer_owner_reference,
    require_private_storage,
)


def run_selected_action(
    selection: DeveloperSelection,
    argv: Sequence[str],
    *,
    suite_root: Path,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Pin one developer operation; return owner output with exact input IDs.

    The same function serves CLI and service callers. Runtime preparation never
    launches a process. Mutation/execution remain explicit owner commands over
    retained exact requests, not an implicit consequence of selecting a pack.
    """
    arguments = list(argv)
    if arguments[:1] == ["checks"]:
        from .developer_checks import run_checks

        return run_checks(selection, arguments[1:], state_root=state_root or default_product_spine_state_root(suite_root))
    if arguments[:2] == ["review", "local"]:
        from .developer_source_review import run_local_review

        return run_local_review(selection, arguments[2:])
    if arguments[:1] == ["source"]:
        from .developer_source_navigation import run_source_action

        return run_source_action(selection, arguments[1:])
    owner_reference = None
    prefix = tuple(arguments[:2])
    if prefix not in {
        ("review", "recipes"),
        ("feature", "options"),
        ("feature", "plan"),
    } and arguments[:1] != ["runtime-plan"]:
        raise DeveloperContextError(
            "context run supports source navigation, local/recipe review, explicit checks, feature options/plan, and runtime-plan"
        )
    if prefix == ("review", "recipes"):
        from .review_commands import _review_single_option

        explicit = _review_single_option(arguments, "--source")
        profile = _review_single_option(arguments, "--profile")
        if explicit is not None:
            selection = replace(
                selection, pack_uri=Path(explicit).expanduser().resolve().as_uri()
            )
        if profile is not None and profile != selection.pack_profile:
            raise DeveloperContextError(
                "explicit profile differs from the selected context; select that profile first"
            )
        if explicit is None:
            arguments += ["--source", str(selection.workspace)]
        if profile is None:
            arguments += ["--profile", selection.pack_profile]
        if "--json" not in arguments:
            arguments += ["--json"]
    elif arguments[:1] == ["runtime-plan"]:
        from .runtime_plan import plan_project_runtime

        parser = argparse.ArgumentParser(prog="context run runtime-plan")
        parser.add_argument("--side", choices=("client", "server"), default="client")
        parser.add_argument("--launcher", default="prism")
        options = parser.parse_args(arguments[1:])
    else:
        if len(arguments) < 3 or arguments[2] not in {
            "recipe-change",
            "quest-for-process",
            "material-fluid-recipe",
        }:
            raise DeveloperContextError("select one supported construction family")
        if len(arguments) > 3 and not arguments[3].startswith("-"):
            selection = replace(
                selection, pack_uri=Path(arguments[3]).expanduser().resolve().as_uri()
            )
        else:
            arguments.insert(3, str(selection.workspace))
        if "--json" not in arguments:
            arguments.append("--json")
        if prefix == ("feature", "plan"):
            from .review_commands import _review_single_option
            from .developer_feature import default_feature_state_root

            explicit_state = _review_single_option(arguments, "--state-root")
            feature_state = (
                Path(explicit_state).expanduser().resolve()
                if explicit_state
                else (
                    default_feature_state_root(suite_root)
                    if state_root is None
                    else state_root / "developer-features"
                )
            )
            if explicit_state is None:
                arguments.extend(["--state-root", str(feature_state)])
            require_private_storage(selection, feature_state)

    operation = observe_developer_context(selection)
    if (selection.pack_profile, selection.platform_profile, selection.variant) != (
        "supersymmetry",
        "cleanroom",
        "cleanroom-provisional",
    ):
        raise DeveloperContextError(
            "this first developer vertical requires Supersymmetry / Cleanroom provisional"
        )
    if arguments[:1] == ["runtime-plan"]:
        result = plan_project_runtime(
            suite_root,
            selection.workspace,
            side=options.side,
            launcher=options.launcher,
        )
        code, diagnostics = 0, ""
    else:
        # Native dispatch in a separate process keeps concurrent service/CLI
        # streams isolated. Never redirect process-global sys.stdout in a job.
        completed = subprocess.run(
            [sys.executable, "-m", "workbench_core", *arguments],
            cwd=selection.workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        code, diagnostics = completed.returncode, completed.stderr
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            result = {"text": completed.stdout}
    operation.require_fresh()
    if prefix == ("feature", "plan") and code == 0:
        from .developer_feature import reference_feature_record

        owner_reference = reference_feature_record(feature_state, "plans", result["id"])
    return {
        "format": "workbench-developer-action-v1",
        "operation_id": operation.id,
        "context": operation.as_dict(),
        "exit_code": code,
        "result": result,
        "owner_record_ref": owner_reference,
        "diagnostics": diagnostics,
    }


def _bind_retained_artifact(store, session, before, **arguments):
    """Retry only additive artifact links that raced with another reader.

    The owner operation has already finished. Never rerun it, and never rebase
    across navigation, workspace, lifecycle or execution changes. Each retry
    still verifies the owner record and wins the store's ordinary sequence CAS.
    """
    from .work_session import WorkSessionConflictError

    sequence = before["summary"]["latest_sequence"]
    for attempt in range(3):
        try:
            return store.bind_owner_artifacts(
                session, expected_sequence=sequence, **arguments
            )
        except WorkSessionConflictError as exc:
            if exc.code != "work-session.stale-sequence" or attempt == 2:
                raise
            current = store.open(session)
            intervening = [row for row in current["events"] if row["sequence"] > sequence]
            if (
                current["integrity"]["journal_state"] != "verified"
                or current["session"] != before["session"]
                or not intervening
                or any(
                    row["kind"] != "owner-artifacts-retained"
                    or row["lifecycle"] != before["summary"]["lifecycle"]
                    or row["closed"]
                    or not row["owner_record_refs"]
                    or any(row[key] for key in (
                        "stage", "action", "result_refs", "problems", "next_actions",
                        "recovery", "workspace_observation", "limitations", "unknowns",
                    ))
                    for row in intervening
                )
            ):
                raise
            sequence = current["summary"]["latest_sequence"]


def main(argv: Sequence[str], *, suite_root: Path | None = None) -> int:
    suite = repository_root(__file__) if suite_root is None else suite_root
    parser = argparse.ArgumentParser(prog="workbench context")
    parser.add_argument(
        "--state-root", type=Path, default=default_product_spine_state_root(suite)
    )
    commands = parser.add_subparsers(dest="action", required=True)
    select = commands.add_parser(
        "select", help="retain explicit selection in a new Work Session"
    )
    select.add_argument("pack", type=Path)
    select.add_argument("--pack-profile", required=True)
    select.add_argument("--platform-profile", required=True)
    select.add_argument("--variant", required=True)
    select.add_argument("--mod-checkout", type=Path)
    show = commands.add_parser(
        "show", help="observe current inputs for one selected session"
    )
    show.add_argument("session")
    run = commands.add_parser(
        "run", help="run a developer action with selected inputs; runtime execution requires exact check consent"
    )
    run.add_argument("session")
    run.add_argument("command", nargs=argparse.REMAINDER)
    prepare = commands.add_parser(
        "prepare", help="stage a reviewed recipe comparison without a runtime launch"
    )
    prepare.add_argument("session")
    prepare.add_argument("plan_file", type=Path)
    args = parser.parse_args(list(argv))
    try:
        if args.action == "select":
            selection = DeveloperSelection(
                args.pack.expanduser().resolve().as_uri(),
                args.pack_profile,
                args.platform_profile,
                args.variant,
                None
                if args.mod_checkout is None
                else args.mod_checkout.expanduser().resolve().as_uri(),
            )
            result = retain_selection(
                selection,
                args.state_root,
                frontend={
                    "frontend_id": "workbench-cli",
                    "kind": "cli",
                    "version": "1",
                    "instance_id": None,
                    "process_id": None,
                },
            )
        else:
            selection = selected_context(args.state_root, args.session)
            from .work_session import WorkSessionStore, WorkSessionConflictError

            store = WorkSessionStore(args.state_root)
            before = store.open(args.session)
            if args.action == "show":
                operation = observe_developer_context(selection)
                result = {
                    "session_id": args.session,
                    "operation_id": operation.id,
                    "observation": operation.as_dict(),
                    "owner_record_refs": before["summary"]["owner_record_refs"],
                    "session_integrity": before["integrity"],
                }
            elif args.action == "prepare":
                from .developer_feature import resolve_feature_record
                from .developer_recipe_preparation import prepare_recipe_comparison

                plan = resolve_feature_record(
                    args.state_root / "developer-features", "plans", args.plan_file
                )
                result = prepare_recipe_comparison(
                    selection, plan, suite_root=suite, state_root=args.state_root
                )
            else:
                arguments = (
                    args.command[1:] if args.command[:1] == ["--"] else args.command
                )
                result = run_selected_action(
                    selection, arguments, suite_root=suite, state_root=args.state_root
                )
            reference = result.get("owner_record_ref")
            if reference is not None:
                same_selection = (
                    result.get("context", {}).get("selection_id", selection.id)
                    == selection.id
                )
                if not same_selection:
                    result["session_link"] = {
                        "state": "not-linked",
                        "reason": "explicit workspace override belongs to another selection",
                    }
                else:
                    try:
                        _bind_retained_artifact(
                            store,
                            args.session,
                            before,
                            frontend={
                                "frontend_id": "workbench-cli",
                                "kind": "cli",
                                "version": "1",
                                "instance_id": None,
                                "process_id": None,
                            },
                            owner_record_refs=[reference],
                            owner_reference_verifier=lambda row: (
                                verify_developer_owner_reference(
                                    row, selection, suite_root=suite
                                )
                            ),
                        )
                        result["session_link"] = {
                            "state": "linked",
                            "session_id": args.session,
                        }
                    except WorkSessionConflictError as exc:
                        result["session_link"] = {
                            "state": "conflict",
                            "reason": str(exc),
                            "owner_record_ref": reference,
                        }
                        result["exit_code"] = 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return int(result.get("exit_code", 0))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "unavailable", "reason": str(exc)}))
        return 2
