"""Plain-English Sentinel surface over profile-owned diagnostic reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, TextIO

from workbench_api.events import sanitize_terminal


DEFAULT_POLICY_RELATIVE = Path(
    "profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-policy-v1.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench diagnose mixins",
        description=(
            "Check exact JAR or ZIP bytes with the Cleanroom profile's Mixin "
            "rules. This is a static check: it does not load archive code or "
            "claim that the assembled game works."
        ),
    )
    parser.add_argument(
        "artifacts",
        metavar="JAR",
        nargs="+",
        help="one or more exact JAR/ZIP files to check",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help=(
            "exact Cleanroom Mixin policy JSON; defaults to the policy shipped "
            "with this Workbench build"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete profile-owned Mixin Doctor report unchanged",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit 1 when the owner report requires review or rejects an archive",
    )
    return parser


def _profile_api(root: Path):
    """Load the profile owner without copying its policy into Sentinel."""

    for source in (
        root / "modules/project-intelligence/src",
        root / "profiles/platforms/cleanroom/src",
    ):
        value = str(source)
        if value not in sys.path:
            sys.path.insert(0, value)
    from workbench_cleanroom_mixin_doctor import (  # noqa: PLC0415
        DoctorError,
        inspect_artifact_paths,
        render_report,
    )

    return DoctorError, inspect_artifact_paths, render_report


def _safe(value: object) -> str:
    return sanitize_terminal(str(value))


def _observed(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(rendered) > 600:
        rendered = rendered[:597] + "..."
    return _safe(rendered)


def render_mixin_diagnosis(report: Mapping[str, Any], output: TextIO) -> None:
    """Render only facts and decisions already present in the owner report."""

    summary = report["summary"]
    disposition = str(summary["disposition"])
    output.write(
        "Sentinel · Cleanroom Mixin check\n"
        f"Result: {_safe(disposition)}\n"
        f"Archives checked: {summary['artifact_count']}\n"
        f"Findings: {summary['finding_count']} "
        f"({summary['reject_finding_count']} reject, "
        f"{summary['review_finding_count']} review)\n"
        f"Static coverage: {_safe(summary['coverage_state'])} "
        f"({summary['evaluated_rule_count']} of {summary['rule_count']} rules evaluated)\n"
        f"Policy: {_safe(report['policy']['policy_id'])}\n"
        f"Report: {_safe(report['report_id'])}\n"
    )

    findings = report.get("findings", [])
    if findings:
        output.write("\nWhat needs attention\n")
        for index, finding in enumerate(findings, 1):
            output.write(
                f"\n{index}. {_safe(finding['artifact_label'])} · "
                f"{_safe(finding['disposition'])} · {_safe(finding['severity'])}\n"
                f"   Where: {_safe(finding['locator'])}\n"
                f"   Observed: {_observed(finding['observed'])}\n"
                f"   Why: {_safe(finding['rationale'])}\n"
                f"   Rule: {_safe(finding['rule_id'])}\n"
            )
    else:
        output.write("\nNo policy findings were produced by this static scan.\n")

    if disposition == "reject":
        action = (
            "Stop: do not treat these archives as accepted for this Cleanroom "
            "profile. Review each reject finding with the archive owner."
        )
    elif disposition == "review":
        action = (
            "Review the listed findings and unevaluated static coverage before "
            "deciding whether to use these archives."
        )
    else:
        action = (
            "No profile rule requires action from this static scan. This does "
            "not replace a runtime check."
        )
    output.write(f"\nNext: {action}\n")

    limitations = report.get("limitations", [])
    if limitations:
        output.write("\nLimits\n")
        for limitation in limitations:
            output.write(f"- {_safe(limitation)}\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    suite_root = Path.cwd() if root is None else root
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    DoctorError, inspect_artifact_paths, render_report = _profile_api(suite_root)
    policy = (
        suite_root / DEFAULT_POLICY_RELATIVE
        if arguments.policy is None
        else arguments.policy
    )
    try:
        report = inspect_artifact_paths(arguments.artifacts, policy_path=policy)
        if arguments.json:
            stdout.write(render_report(report).decode("utf-8"))
        else:
            render_mixin_diagnosis(report, stdout)
    except (DoctorError, OSError, ValueError) as exc:
        stderr.write(f"Sentinel Mixin check failed: {_safe(exc)}\n")
        return 2
    if arguments.strict and report["summary"]["disposition"] in {"review", "reject"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
