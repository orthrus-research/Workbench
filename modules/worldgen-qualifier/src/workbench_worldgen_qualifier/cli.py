"""Terminal-first Worldgen Qualifier command surface."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
import json
from pathlib import Path
import shlex
import sys
import time
from typing import Any, Mapping, Sequence, TextIO
import webbrowser

from workbench_worldgen_cockpit.model import (
    load_profile as load_cockpit_profile,
    load_report as load_cockpit_report,
    sha256_file,
    write_json_atomic,
)

from .assessment import assess_matrix
from .model import (
    QualifierError,
    load_profile,
    load_qualification,
    resolve_profile_path,
)
from .orchestrator import (
    _runtime_jars,
    build_qualification_plan,
    execute_qualification_plan,
    render_plan,
    reproduction_command,
)
from .render import render_report, render_risk_report, write_html
from .risk import scan_jars


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _default_label() -> str:
    return time.strftime("qualify-%Y%m%d-%H%M%S", time.gmtime())


def _profile_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile", help="explicit pack profile; no pack is selected universally")
    group.add_argument("--profile-file", type=Path, help="exact workbench-worldgen-qualification-profile-v1 JSON")


def build_parser(*, prog: str = "workbench qualify") -> argparse.ArgumentParser:
    parser = _Parser(
        prog=prog,
        description="Qualify exact modded world generation across fresh-JVM perturbations, domain gates, and static edge-case risks.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="freeze and execute a qualification matrix")
    _profile_arguments(run)
    run.add_argument("--suite", default="smoke", help="pack-profile suite id")
    run.add_argument("--intent", default="development", help="pack-profile decision intent id")
    run.add_argument("--label", default=_default_label())
    run.add_argument("--plan", type=Path, help="one subject plan used byte-identically by every control")
    run.add_argument("--artifact", type=Path, help="one exact subject artifact; otherwise build the first cell once")
    run.add_argument("--seed", type=int, action="append", dest="seeds", help="matrix seed; repeat to replace suite defaults")
    run.add_argument("--region", action="append", dest="regions", help="matrix chunk region; repeat to replace suite defaults")
    run.add_argument("--order", action="append", dest="orders", choices=("baseline-first", "candidate-first"), help="pair execution order; repeat to replace suite defaults")
    run.add_argument("--heap", action="append", dest="heaps", help="heap shape; repeat to replace suite defaults")
    run.add_argument("--pair-repetitions", type=int)
    run.add_argument("--allow-inconclusive", action="store_true", help="execute despite known preflight gaps that make acceptance unreachable")
    run.add_argument("--risk-jar", action="append", type=Path, default=[], help="additional exact JAR to scan; runtime mods are discovered automatically")
    run.add_argument("--runtime-template", type=Path)
    run.add_argument("--strata-root", type=Path)
    run.add_argument("--java-cmd")
    run.add_argument("--gradle-cmd")
    run.add_argument("--diagnostic-sample-modulo", type=int)
    run.add_argument("--startup-timeout", type=int, default=300)
    run.add_argument("--scan-timeout", type=int, default=600)
    run.add_argument("--stop-timeout", type=int, default=60)
    run.add_argument("--open", action="store_true", help="open the retained local review after execution")
    preview = run.add_mutually_exclusive_group()
    preview.add_argument("--show", action="store_true", help="show the inert matrix plan")
    preview.add_argument("--json", action="store_true", help="emit the inert matrix plan as JSON")

    assess = commands.add_parser("assess", help="qualify already completed A/A Cockpit reports")
    _profile_arguments(assess)
    assess.add_argument("--suite", default="smoke", help="pack-profile suite id")
    assess.add_argument("--intent", default="development", help="pack-profile decision intent id")
    assess.add_argument("--cockpit-report", type=Path, action="append", required=True)
    assess.add_argument("--risk-jar", action="append", type=Path, default=[])
    assess.add_argument("--output", type=Path, help="fresh retained JSON path; also writes adjacent HTML")
    assess.add_argument("--json", action="store_true")
    assess.add_argument("--sources", action="store_true")

    scan = commands.add_parser("scan", help="screen exact JARs for generic nondeterminism edge cases")
    _profile_arguments(scan)
    scan.add_argument("--jar", type=Path, action="append", required=True)
    scan.add_argument("--output", type=Path)
    scan.add_argument("--json", action="store_true")

    show = commands.add_parser("show", help="render a retained qualification report")
    show.add_argument("--report", type=Path, required=True)
    show.add_argument("--json", action="store_true")
    show.add_argument("--sources", action="store_true")

    open_parser = commands.add_parser("open", help="open a retained qualification review")
    open_parser.add_argument("--report", type=Path, required=True)
    return parser


def _selected(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], Any, dict[str, Any], Any]:
    profile_path = args.profile_file if args.profile_file is not None else resolve_profile_path(root, args.profile)
    profile, binding = load_profile(profile_path, root=root)
    if args.profile is not None and profile["pack_profile"] != args.profile:
        raise QualifierError(f"selected profile {args.profile!r} resolves to pack {profile['pack_profile']!r}")
    cockpit, cockpit_binding = load_cockpit_profile(profile["_cockpit_profile"], root=root)
    return profile, binding, cockpit, cockpit_binding


def _status_code(report: Mapping[str, Any]) -> int:
    return 0 if str(report.get("status", "")).startswith("accepted-") else 1


def _open_review(report: Mapping[str, Any]) -> bool:
    target = next((row.get("target") for row in report.get("navigation", []) if isinstance(row, Mapping) and row.get("kind") == "qualification-review"), None)
    if not isinstance(target, str):
        raise QualifierError("qualification report has no retained HTML review")
    path = Path(target).resolve(strict=True)
    if not path.is_file():
        raise QualifierError(f"qualification review is unavailable: {path}")
    return webbrowser.open(path.as_uri(), new=2)


def _run_plan_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "profile_name": args.profile,
        "suite_id": args.suite,
        "intent_id": args.intent,
        "label": args.label,
        "subject_plan": args.plan,
        "artifact": args.artifact,
        "seeds": args.seeds,
        "regions": args.regions,
        "orders": args.orders,
        "heaps": args.heaps,
        "pair_repetitions": args.pair_repetitions,
        "allow_inconclusive": args.allow_inconclusive,
        "risk_jars": args.risk_jar,
        "runtime_template": args.runtime_template,
        "strata_root": args.strata_root,
        "java_cmd": args.java_cmd,
        "gradle_cmd": args.gradle_cmd,
        "diagnostic_sample_modulo": args.diagnostic_sample_modulo,
        "startup_timeout": args.startup_timeout,
        "scan_timeout": args.scan_timeout,
        "stop_timeout": args.stop_timeout,
    }


def run(
    argv: Sequence[str] | None = None,
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    arguments = list(argv) if argv is not None else list(sys.argv[1:])
    args = build_parser().parse_args(arguments)
    try:
        resolved_root = root.expanduser().resolve(strict=True)
        if args.command == "show":
            report, _ = load_qualification(args.report)
            output.write(json.dumps(report, indent=2, sort_keys=True) + "\n" if args.json else render_report(report, show_sources=args.sources))
            return 0
        if args.command == "open":
            report, _ = load_qualification(args.report)
            opened = _open_review(report)
            output.write(f"Qualification review: {'opened' if opened else 'browser did not accept the request'}\n")
            return 0 if opened else 1

        profile, profile_binding, cockpit, cockpit_binding = _selected(args, resolved_root)
        if args.command == "scan":
            report = scan_jars(args.jar, limits=profile["limits"], dispositions=profile["risk_policy"]["dispositions"])
            if args.output is not None:
                write_json_atomic(args.output.expanduser(), report)
            output.write(json.dumps(report, indent=2, sort_keys=True) + "\n" if args.json else render_risk_report(report))
            return 0 if report["coverage"] == "complete" else 1

        if args.command == "run":
            plan = build_qualification_plan(
                root=resolved_root,
                profile=profile,
                profile_binding=profile_binding,
                cockpit_profile=cockpit,
                cockpit_profile_binding=cockpit_binding,
                **_run_plan_args(args),
            )
            if args.json:
                output.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
                return 0
            if args.show:
                output.write(render_plan(plan))
                return 0
            if plan["status"] == "attention" and not args.allow_inconclusive:
                raise QualifierError("planned acquisition cannot reach the requested acceptance intent; review --show and pass --allow-inconclusive only for intentional partial evidence")
            reproduction = reproduction_command(arguments)
            report, report_path, review_path, session_path = execute_qualification_plan(
                root=resolved_root,
                profile=profile,
                profile_binding=profile_binding,
                cockpit_profile=cockpit,
                cockpit_profile_binding=cockpit_binding,
                plan=plan,
                reproduction_command=reproduction,
            )
            output.write(render_report(report))
            output.write(f"Retained report: {report_path}\nRetained session: {session_path}\n")
            if args.open:
                opened = webbrowser.open(review_path.as_uri(), new=2)
                output.write(f"Review browser request: {'accepted' if opened else 'not accepted'}\n")
            return _status_code(report)

        assert args.command == "assess"
        entries: list[dict[str, Any]] = []
        for path in args.cockpit_report:
            report, binding = load_cockpit_report(path)
            entries.append({"report": report, "path": str(binding.path), "sha256": binding.sha256, "run_paths": [report["sides"][side]["iteration_report"]["path"] for side in ("baseline", "candidate")], "perturbations": {"kind": "supplied-control"}})
        risk_paths = list(args.risk_jar)
        if profile["risk_policy"]["scan_runtime_mods"]:
            risk_paths.extend(_runtime_jars(entries[0]["report"]))
        unique_risks = list(dict.fromkeys(path.resolve(strict=True) for path in risk_paths))
        risk_report = None if not unique_risks else scan_jars(unique_risks, limits=profile["limits"], dispositions=profile["risk_policy"]["dispositions"])
        reproduction = shlex.join(["python3", "tools/workbench.py", "qualify", *arguments])
        review_path = args.output.with_suffix(".html") if args.output is not None else None
        nav = [] if review_path is None else [{"kind": "qualification-review", "label": "Open qualification decision", "target": str(review_path)}]
        report = assess_matrix(
            profile=profile,
            profile_binding=profile_binding,
            cockpit_entries=entries,
            suite_id=args.suite,
            intent_id=args.intent,
            risk_scan=risk_report,
            reproduction_command=reproduction,
            navigation=nav,
        )
        if args.output is not None:
            write_json_atomic(args.output.expanduser(), report)
            assert review_path is not None
            write_html(review_path, report)
        output.write(json.dumps(report, indent=2, sort_keys=True) + "\n" if args.json else render_report(report, show_sources=args.sources))
        return _status_code(report)
    except (OSError, TypeError, ValueError, QualifierError) as exc:
        error.write(f"Worldgen Qualifier failed: {exc}\n")
        return 2


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    selected_root = root or _repository_resource_root(__file__)
    return run(argv, root=selected_root)


__all__ = ["build_parser", "main", "run"]
