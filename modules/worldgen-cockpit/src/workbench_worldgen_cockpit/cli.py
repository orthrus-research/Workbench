"""Terminal-first Worldgen Cockpit command surface."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence, TextIO
import webbrowser

from .analysis import analyze_pair
from .model import (
    CockpitError,
    load_profile,
    load_report,
    resolve_profile_path,
    write_json_atomic,
)
from .orchestrator import (
    build_run_plan,
    execute_run_plan,
    render_run_plan,
    reproduction_command,
)
from .render import render_report, write_html


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _default_label() -> str:
    return time.strftime("cockpit-%Y%m%d-%H%M%S", time.gmtime())


def _profile_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--profile",
        help="explicit pack profile; Supersymmetry is never selected universally",
    )
    group.add_argument(
        "--profile-file",
        type=Path,
        help="exact workbench-worldgen-cockpit-pack-profile-v1 JSON",
    )


def _evidence_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--baseline-observatory-bundle", type=Path)
    parser.add_argument("--candidate-observatory-bundle", type=Path)
    parser.add_argument("--comparison-scope-sha256")
    parser.add_argument("--baseline-inventory", type=Path)
    parser.add_argument("--candidate-inventory", type=Path)
    parser.add_argument("--baseline-impact", type=Path)
    parser.add_argument("--candidate-impact", type=Path)
    parser.add_argument("--baseline-trace", type=Path)
    parser.add_argument("--candidate-trace", type=Path)
    parser.add_argument("--baseline-observer-off-jfr", type=Path)
    parser.add_argument("--candidate-observer-off-jfr", type=Path)


def build_parser(*, prog: str = "workbench cockpit") -> argparse.ArgumentParser:
    parser = _Parser(
        prog=prog,
        description=(
            "Run or analyze aligned baseline/candidate fresh worlds and receive "
            "semantic, exact-state, statistical, causal, and performance differences."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="freeze and execute a paired fresh-world experiment")
    _profile_arguments(run)
    run.add_argument("--mode", choices=("fast", "debug", "performance"), default="fast")
    run.add_argument("--label", default=_default_label())
    run.add_argument("--seed", type=int)
    run.add_argument("--region", help="minChunkX,minChunkZ,widthChunks,heightChunks")
    run.add_argument("--order", choices=("baseline-first", "candidate-first"), default="baseline-first")
    run.add_argument("--baseline-plan", type=Path)
    run.add_argument("--candidate-plan", type=Path)
    run.add_argument("--artifact", type=Path, help="one exact artifact used by both sides")
    run.add_argument("--baseline-artifact", type=Path)
    run.add_argument("--candidate-artifact", type=Path)
    run.add_argument("--runtime-template", type=Path)
    run.add_argument("--strata-root", type=Path)
    run.add_argument("--java-cmd")
    run.add_argument("--gradle-cmd")
    run.add_argument("--heap")
    run.add_argument("--diagnostic-sample-modulo", type=int)
    run.add_argument("--startup-timeout", type=int, default=300)
    run.add_argument("--scan-timeout", type=int, default=600)
    run.add_argument("--stop-timeout", type=int, default=60)
    _evidence_arguments(run)
    run.add_argument("--open", action="store_true", help="open the generated local HTML review after completion")
    preview = run.add_mutually_exclusive_group()
    preview.add_argument("--show", action="store_true", help="show the inert paired run plan")
    preview.add_argument("--json", action="store_true", help="emit the inert paired run plan as JSON")

    compare = commands.add_parser("compare", help="analyze two already completed iteration reports")
    _profile_arguments(compare)
    compare.add_argument("--baseline-report", type=Path, required=True)
    compare.add_argument("--candidate-report", type=Path, required=True)
    _evidence_arguments(compare)
    compare.add_argument("--output", type=Path, help="fresh report path; also writes an adjacent HTML review")
    compare.add_argument("--json", action="store_true", help="emit the complete content-addressed report")
    compare.add_argument("--sources", action="store_true")

    show = commands.add_parser("show", help="render a retained cockpit report")
    show.add_argument("--report", type=Path, required=True)
    show.add_argument("--json", action="store_true")
    show.add_argument("--sources", action="store_true")

    open_parser = commands.add_parser("open", help="open a retained self-contained cockpit review")
    open_parser.add_argument("--report", type=Path, required=True)
    return parser


def _selected_profile(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], Any]:
    path = args.profile_file if args.profile_file is not None else resolve_profile_path(root, args.profile)
    profile, binding = load_profile(path, root=root)
    if args.profile is not None and profile["pack_profile"] != args.profile:
        raise CockpitError(
            f"selected profile {args.profile!r} resolves to pack {profile['pack_profile']!r}"
        )
    return profile, binding


def _plan_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "profile_name": args.profile,
        "mode": args.mode,
        "label": args.label,
        "seed": args.seed,
        "region": args.region,
        "order": args.order,
        "baseline_plan": args.baseline_plan,
        "candidate_plan": args.candidate_plan,
        "artifact": args.artifact,
        "baseline_artifact": args.baseline_artifact,
        "candidate_artifact": args.candidate_artifact,
        "runtime_template": args.runtime_template,
        "strata_root": args.strata_root,
        "java_cmd": args.java_cmd,
        "gradle_cmd": args.gradle_cmd,
        "heap": args.heap,
        "diagnostic_sample_modulo": args.diagnostic_sample_modulo,
        "startup_timeout": args.startup_timeout,
        "scan_timeout": args.scan_timeout,
        "stop_timeout": args.stop_timeout,
        "baseline_observatory_bundle": args.baseline_observatory_bundle,
        "candidate_observatory_bundle": args.candidate_observatory_bundle,
        "comparison_scope_sha256": args.comparison_scope_sha256,
        "baseline_inventory": args.baseline_inventory,
        "candidate_inventory": args.candidate_inventory,
        "baseline_impact": args.baseline_impact,
        "candidate_impact": args.candidate_impact,
        "baseline_trace": args.baseline_trace,
        "candidate_trace": args.candidate_trace,
        "baseline_observer_off_jfr": args.baseline_observer_off_jfr,
        "candidate_observer_off_jfr": args.candidate_observer_off_jfr,
    }


def _analysis_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "baseline_observatory_bundle": args.baseline_observatory_bundle,
        "candidate_observatory_bundle": args.candidate_observatory_bundle,
        "comparison_scope_sha256": args.comparison_scope_sha256,
        "baseline_inventory": args.baseline_inventory,
        "candidate_inventory": args.candidate_inventory,
        "baseline_impact": args.baseline_impact,
        "candidate_impact": args.candidate_impact,
        "baseline_trace": args.baseline_trace,
        "candidate_trace": args.candidate_trace,
        "baseline_observer_off_jfr": args.baseline_observer_off_jfr,
        "candidate_observer_off_jfr": args.candidate_observer_off_jfr,
    }


def _open_report_review(report: dict[str, Any]) -> bool:
    target = next(
        (
            row.get("target")
            for row in report["navigation"]
            if row.get("kind") == "cockpit-review"
        ),
        None,
    )
    if not isinstance(target, str):
        raise CockpitError("cockpit report has no retained HTML review")
    path = Path(target).resolve(strict=True)
    if not path.is_file():
        raise CockpitError(f"cockpit review is unavailable: {path}")
    return webbrowser.open(path.as_uri(), new=2)


def run(
    argv: Sequence[str] | None = None,
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    arguments = list(argv) if argv is not None else list(sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        resolved_root = root.expanduser().resolve(strict=True)
        if args.command == "show":
            report, _ = load_report(args.report)
            output.write(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
                if args.json
                else render_report(report, show_sources=args.sources)
            )
            return 0
        if args.command == "open":
            report, _ = load_report(args.report)
            opened = _open_report_review(report)
            output.write(f"Cockpit review: {'opened' if opened else 'browser did not accept the request'}\n")
            return 0 if opened else 1

        profile, profile_binding = _selected_profile(args, resolved_root)
        if args.command == "run":
            plan = build_run_plan(
                root=resolved_root,
                cockpit_profile=profile,
                cockpit_profile_binding=profile_binding,
                **_plan_arguments(args),
            )
            if args.json:
                output.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
                return 0
            if args.show:
                output.write(render_run_plan(plan))
                return 0
            reproduction = reproduction_command(arguments)
            report, report_path, review_path, session_path = execute_run_plan(
                root=resolved_root,
                cockpit_profile=profile,
                cockpit_profile_binding=profile_binding,
                plan=plan,
                reproduction_command=reproduction,
            )
            output.write(render_report(report))
            output.write(f"Retained report: {report_path}\nRetained session: {session_path}\n")
            if args.open:
                opened = webbrowser.open(review_path.as_uri(), new=2)
                output.write(f"Review browser request: {'accepted' if opened else 'not accepted'}\n")
            return 0

        assert args.command == "compare"
        review_path = None
        if args.output is not None:
            requested = args.output.expanduser()
            review_path = requested.with_suffix(".html")
        reproduction = "python3 tools/workbench.py worldgen cockpit " + " ".join(
            json.dumps(item) for item in arguments
        )
        report = analyze_pair(
            root=resolved_root,
            cockpit_profile=profile,
            cockpit_profile_binding=profile_binding,
            baseline_report=args.baseline_report,
            candidate_report=args.candidate_report,
            reproduction_command=reproduction,
            review_path=review_path,
            **_analysis_arguments(args),
        )
        if args.output is not None:
            write_json_atomic(args.output.expanduser(), report)
            assert review_path is not None
            write_html(review_path, report)
        output.write(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
            if args.json
            else render_report(report, show_sources=args.sources)
        )
        return 0
    except (CockpitError, OSError, TypeError, ValueError) as exc:
        error.write(f"Worldgen Cockpit failed: {exc}\n")
        return 2


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    selected_root = root or _repository_resource_root(__file__)
    return run(argv, root=selected_root)


__all__ = ["build_parser", "main", "run"]
