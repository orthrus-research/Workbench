"""Shell-owned run commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


from workbench_api.resources import repository_root

ROOT = repository_root(__file__)

from .inspection_commands import _worldgen_doctor_report


def _managed_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench run",
        description=(
            "Resolve a pack-owned named run through Workspace Doctor, preview its "
            "exact effects, and delegate execution to the existing disposable runner."
        ),
    )
    parser.add_argument(
        "recipe",
        help="pack-owned recipe name, such as fast, debug, worldgen, performance, or proof",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="explicit pack profile; Workbench never assumes Supersymmetry universally",
    )
    parser.add_argument("--side", default="dedicated-server")
    parser.add_argument("--runtime-template", type=Path)
    parser.add_argument("--strata-root", type=Path)
    parser.add_argument("--java-cmd")
    parser.add_argument("--gradle-cmd")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--region",
        help="minChunkX,minChunkZ,widthChunks,heightChunks; up to 1024 chunks",
    )
    parser.add_argument("--label")
    parser.add_argument("--heap")
    preview = parser.add_mutually_exclusive_group()
    preview.add_argument(
        "--show",
        action="store_true",
        help="show the concise inert plan and do not execute it",
    )
    preview.add_argument(
        "--json",
        action="store_true",
        help="emit the complete inert plan as JSON and do not execute it",
    )
    return parser

def _managed_run_main(argv: list[str]) -> int:
    from workbench_crucible_run_profiles import (
        ManagedRunProfileError,
        execute_managed_run_plan,
        render_managed_run_plan,
        resolve_managed_run_plan,
    )
    from workbench_project_intelligence.workspace_doctor import WorkspaceDoctorError

    parser = _managed_run_parser()
    args = parser.parse_args(argv)
    try:
        doctor_report = _worldgen_doctor_report(
            ROOT,
            profile=args.profile,
            runtime_template=args.runtime_template,
            strata_root=args.strata_root,
            java_cmd=args.java_cmd,
            gradle_cmd=args.gradle_cmd,
        )
        plan = resolve_managed_run_plan(
            ROOT,
            profile_name=args.profile,
            recipe_name=args.recipe,
            doctor_report=doctor_report,
            side=args.side,
            seed=args.seed,
            region=args.region,
            label=args.label,
            heap=args.heap,
        )
        if args.json:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 1 if plan["status"] == "blocked" else 0
        sys.stdout.write(render_managed_run_plan(plan))
        if args.show:
            return 1 if plan["status"] == "blocked" else 0
        if plan["status"] == "blocked":
            raise ManagedRunProfileError("blocked managed run plan cannot be executed")
        runner_arguments = plan["runner"]["arguments"]
        label_index = runner_arguments.index("--label")
        resolved_label = runner_arguments[label_index + 1]
        fresh_doctor_report = _worldgen_doctor_report(
            ROOT,
            profile=args.profile,
            runtime_template=args.runtime_template,
            strata_root=args.strata_root,
            java_cmd=args.java_cmd,
            gradle_cmd=args.gradle_cmd,
        )
        fresh_plan = resolve_managed_run_plan(
            ROOT,
            profile_name=args.profile,
            recipe_name=args.recipe,
            doctor_report=fresh_doctor_report,
            side=args.side,
            seed=args.seed,
            region=args.region,
            label=resolved_label,
            heap=args.heap,
        )
        if fresh_plan["plan_id"] != plan["plan_id"]:
            raise ManagedRunProfileError(
                "managed run inputs changed after preview; review a fresh plan"
            )
        plan = fresh_plan
        if plan["status"] == "attention":
            print("Proceeding with the bounded warnings shown by Workspace Doctor.")
        print("Executing the resolved plan with the existing disposable worldgen runner.")
        from workbench_crucible_worldgen_iteration.cli import main as iteration_main

        return execute_managed_run_plan(plan, root=ROOT, runner=iteration_main)
    except (OSError, ValueError, ManagedRunProfileError, WorkspaceDoctorError) as exc:
        print(f"Managed run failed: {exc}", file=sys.stderr)
        return 2
