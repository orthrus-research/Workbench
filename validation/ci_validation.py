#!/usr/bin/env python3
"""Plan CI stages and reject missing required outcomes without running tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from orchestration import OrchestrationFailure, fingerprint_paths, load_suite_report
from suite_measurement import inventory_digest

STAGES = ("workbench", "source-ci", "native-packages", "axiom", "ide", "physical-cleanroom")
EVENTS = {"pull_request", "push", "schedule", "workflow_dispatch"}
FORMAT = "workbench-ci-validation-plan-v1"
REQUIRED_TESTS = {
    "pip": (("core", "test_package_lifecycle.InstallerConfinementTests.test_real_pip_ignores_injected_module_and_all_configuration"),),
    "windows-paths": (
        ("pack-program-studio", "test_pack_program_studio.PackProgramStudioTests.test_long_windows_candidate_and_profile_paths_are_readable"),
        ("project-intelligence", "test_git_tree.GitTreeMaterializationTests.test_materializes_beyond_legacy_windows_max_path"),
        ("project-intelligence", "test_git_tree.GitTreeMaterializationTests.test_rejects_long_candidate_source_with_git_specific_guidance"),
    ),
    "physical-cleanroom": (
        ("cleanroom-profile", "test_generic_mod_daily_loop_fixture.GenericModDailyLoopFixtureTests.test_real_build_and_corrupt_artifact_fail_closed"),
        ("workbench-shell", "test_product_spine_cli_v2.ProductSpineCliV2Tests.test_physical_cleanroom_job_runs_through_public_session_custody"),
        ("workbench-shell", "test_product_spine_cli_v2.ProductSpineCliV2Tests.test_physical_frontend_kill_reopens_exact_cleanroom_custody"),
    ),
}


def plan(event: str, changed_paths: list[str], *, revision: str) -> dict:
    if event not in EVENTS or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("CI plan requires a supported event and exact source revision")
    paths = sorted(set(changed_paths))
    for name in paths:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("changed paths must be repository-relative")
    # Only known documentation-only changes omit IDE execution. Unknown source,
    # shared contracts and dependency changes receive the complete client lane.
    docs_only = bool(paths) and all(
        name.endswith(".md") and (name.startswith("docs/") or "/" not in name)
        for name in paths
    )
    ide = event != "pull_request" or not docs_only
    reasons = {
        "source-ci": "all public source suites except explicit original-input native fixtures",
        "native-packages": "Linux x64 installed-package coverage",
        "axiom": "hosted Axiom JVM and native worker checks",
    }
    rows = [{"name": name, "required": True,
             "reason": reasons.get(name, "developer environment coverage")}
            for name in STAGES if name not in {"ide", "physical-cleanroom"}]
    rows.append({"name": "ide", "required": ide, "reason": "complete client sweep or source change" if ide else "known documentation-only pull request"})
    physical = event in {"schedule", "workflow_dispatch"}
    rows.append({"name": "physical-cleanroom", "required": physical,
                 "reason": "scheduled/manual physical build and process-custody sweep" if physical else "physical checks reserved for scheduled/manual sweep"})
    return {"format": FORMAT, "revision": revision, "event": event,
            "changed_paths": paths, "stages": rows,
            "excluded_suites": [{"name": "validation-native-fixtures", "state": "not-run",
                                 "reason": "Requires explicit original candidate, engine, JVM and fresh report roots; run canonical qualification separately."}]}


def gate(document: dict, results: dict) -> list[str]:
    """A GitHub skipped/cancelled/unrun job never satisfies a required stage."""
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        return ["missing or invalid CI plan"]
    try:
        expected = plan(document["event"], document["changed_paths"], revision=document["revision"])
    except (KeyError, TypeError, ValueError):
        return ["invalid CI selection inputs"]
    if document != expected:
        return ["CI stage selection differs from the declared routing policy"]
    if not isinstance(results, dict):
        return ["missing CI job outcomes"]
    failures = []
    for name in ("plan", *(row["name"] for row in expected["stages"] if row["required"])):
        observed = results.get(name, {}).get("result") if isinstance(results.get(name, {}), dict) else None
        if observed != "success":
            failures.append(f"required stage {name}: {observed or 'unrun'}")
    for row in expected["stages"]:
        observed = results.get(row["name"], {})
        if not row["required"] and (not isinstance(observed, dict) or observed.get("result") not in {"skipped", "success"}):
            failures.append(f"optional stage {row['name']} has an unexpected outcome")
    return failures


def _current_source_fingerprint(root: Path) -> str:
    from validate import repository_files
    return fingerprint_paths(root, repository_files())


def required_test_failures(result_path: Path, group: str, *, root: Path) -> list[str]:
    """Require actual passing probes, bound to this invocation's exact report."""
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if (result.get("format") != "workbench-validation-invocation-v1" or result["state"] != "passed"
                or not {"preflight", "python"} <= result.get("phases", {}).keys()
                or any(row["state"] != "passed" for row in result["phases"].values())):
            raise ValueError("validation invocation did not pass")
        if result["source_fingerprint"] != _current_source_fingerprint(root):
            raise ValueError("validation source no longer matches this checkout")
        run_path = Path(result["python_run_path"])
        if not run_path.is_absolute():
            run_path = root / run_path
        run_path = run_path.resolve(strict=True)
        if not run_path.is_relative_to((root / ".workbench/validation/runs").resolve(strict=True)):
            raise ValueError("Python run is outside this checkout's report directory")
        run = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
        if run["state"] != "passed" or any(run[key] != result[key] for key in ("run_id", "source_fingerprint")):
            raise ValueError("invocation and Python run identities disagree")
        failures = []
        for suite, test_id in REQUIRED_TESTS[group]:
            admission = json.loads((run_path / "reports" / f"{suite}.admitted.json").read_text(encoding="utf-8"))
            if (admission.get("format") != "workbench-python-test-admission-v1"
                    or any(admission[key] != run[key] for key in ("run_id", "source_fingerprint"))
                    or admission["inventory_digest"] != inventory_digest(admission["test_ids"])):
                raise ValueError("required test admission differs from the run")
            report = load_suite_report(run_path / "reports" / f"{suite}.json",
                                       expected_suite=suite, expected_run_id=run["run_id"],
                                       expected_source_fingerprint=run["source_fingerprint"],
                                       expected_test_ids=admission["test_ids"])
            rows = [row for row in report.tests if row.test == test_id]
            if len(rows) != 1 or rows[0].outcome != "passed":
                failures.append(f"required probe did not pass: {suite}/{test_id}")
        return failures
    except (OSError, KeyError, TypeError, ValueError, OrchestrationFailure) as error:
        return [f"required probe evidence is unavailable or invalid: {error}"]


def changed_paths(event: str, document: dict) -> list[str]:
    if event != "pull_request":
        return []
    base = document["pull_request"]["base"]["sha"]
    head = document["pull_request"]["head"]["sha"]
    if any(re.fullmatch(r"[0-9a-f]{40}", value) is None for value in (base, head)):
        raise ValueError("pull request diff requires exact revisions")
    raw = subprocess.check_output(["git", "diff", "--name-only", "-z", f"{base}...{head}", "--"])
    return [name.decode("utf-8") for name in raw.split(b"\0") if name]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("plan")
    select.add_argument("--event-name", required=True)
    select.add_argument("--event-file", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    select.add_argument("--github-output", type=Path)
    check = commands.add_parser("gate")
    check.add_argument("--plan-json", required=True)
    check.add_argument("--results-json", required=True)
    probes = commands.add_parser("assert-tests")
    probes.add_argument("--result", type=Path, required=True)
    probes.add_argument("--group", choices=tuple(REQUIRED_TESTS), required=True)
    args = parser.parse_args(argv)
    if args.command == "assert-tests":
        failures = required_test_failures(args.result, args.group, root=Path(__file__).resolve().parents[1])
        print("\n".join(failures) if failures else f"Required {args.group} probes passed in the referenced invocation.")
        return int(bool(failures))
    if args.command == "gate":
        try:
            failures = gate(json.loads(args.plan_json), json.loads(args.results_json))
        except (ValueError, TypeError):
            failures = ["CI plan or outcomes are not valid JSON"]
        for failure in failures:
            print(f"CI VALIDATION FAILED: {failure}")
        if not failures:
            print("All planned required CI stages passed.")
        return int(bool(failures))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    document = plan(args.event_name, changed_paths(args.event_name, json.loads(args.event_file.read_text())), revision=revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("plan=" + json.dumps(document, separators=(",", ":")) + "\n")
            for name in ("ide", "physical-cleanroom"):
                output.write(name.replace("-", "_") + "=" + str(next(row["required"] for row in document["stages"] if row["name"] == name)).lower() + "\n")
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
