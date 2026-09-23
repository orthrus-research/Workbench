"""Paired fresh-world orchestration for Worldgen Cockpit."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import shutil
from typing import Any, Mapping, Sequence

from workbench_crucible_worldgen_iteration.cli import main as run_iteration
from workbench_crucible_worldgen_iteration.iteration import parse_region

from .analysis import analyze_pair
from .model import (
    CockpitError,
    FileBinding,
    LABEL_RE,
    SESSION_FORMAT,
    canonical_json_bytes,
    load_json_file,
    require,
    sha256_file,
    sha256_json,
    write_json_atomic,
)
from .render import write_html


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _regular(path: Path | None, context: str) -> Path | None:
    if path is None:
        return None
    requested = path.expanduser()
    require(not requested.is_symlink(), f"{context} cannot be a symlink: {requested}")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise CockpitError(f"cannot resolve {context}: {requested}: {exc}") from exc
    require(resolved.is_file(), f"{context} is not a regular file: {resolved}")
    return resolved


def _binding(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _copy_frozen(source: Path, destination: Path) -> Path:
    source = _regular(source, "frozen input")
    assert source is not None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise CockpitError(f"frozen input destination already exists: {destination}")
    before = _binding(source)
    shutil.copy2(source, destination)
    after = _binding(destination)
    require(before["sha256"] == after["sha256"] and before["size_bytes"] == after["size_bytes"], f"frozen input copy drift: {source}")
    return destination.resolve(strict=True)


def _optional_pair(left: Path | None, right: Path | None, context: str) -> None:
    require((left is None) == (right is None), f"{context} requires both baseline and candidate inputs")


def build_run_plan(
    *,
    root: Path,
    cockpit_profile: Mapping[str, Any],
    cockpit_profile_binding: FileBinding,
    profile_name: str | None,
    mode: str,
    label: str,
    seed: int | None,
    region: str | None,
    order: str,
    baseline_plan: Path | None,
    candidate_plan: Path | None,
    artifact: Path | None,
    baseline_artifact: Path | None,
    candidate_artifact: Path | None,
    runtime_template: Path | None,
    strata_root: Path | None,
    java_cmd: str | None,
    gradle_cmd: str | None,
    heap: str | None,
    diagnostic_sample_modulo: int | None,
    startup_timeout: int,
    scan_timeout: int,
    stop_timeout: int,
    baseline_observatory_bundle: Path | None,
    candidate_observatory_bundle: Path | None,
    comparison_scope_sha256: str | None,
    baseline_inventory: Path | None,
    candidate_inventory: Path | None,
    baseline_impact: Path | None,
    candidate_impact: Path | None,
    baseline_trace: Path | None,
    candidate_trace: Path | None,
    baseline_observer_off_jfr: Path | None,
    candidate_observer_off_jfr: Path | None,
) -> dict[str, Any]:
    require(mode in {"fast", "debug", "performance"}, f"unsupported cockpit mode: {mode}")
    require(order in {"baseline-first", "candidate-first"}, f"unsupported execution order: {order}")
    require(LABEL_RE.fullmatch(label) is not None and len(label) <= 84, "cockpit label must be path-safe and at most 84 characters")
    if profile_name is not None:
        require(profile_name == cockpit_profile["pack_profile"], "selected pack name differs from the cockpit profile")
    require(not (artifact is not None and (baseline_artifact is not None or candidate_artifact is not None)), "--artifact cannot be combined with side-specific artifacts")
    _optional_pair(baseline_artifact, candidate_artifact, "side-specific artifact selection")
    _optional_pair(baseline_observatory_bundle, candidate_observatory_bundle, "causal comparison")
    _optional_pair(baseline_inventory, candidate_inventory, "GTCEu inventory comparison")
    _optional_pair(baseline_observer_off_jfr, candidate_observer_off_jfr, "observer-off comparison")
    require(comparison_scope_sha256 is None or len(comparison_scope_sha256) == 64, "comparison scope SHA-256 must contain 64 hexadecimal characters")

    iteration_profile, iteration_binding = load_json_file(
        Path(cockpit_profile["_iteration_profile"]), context="worldgen iteration profile"
    )
    defaults = iteration_profile.get("defaults")
    require(isinstance(defaults, Mapping), "worldgen iteration profile defaults are invalid")
    raw_seed = seed if seed is not None else defaults.get("seed")
    require(isinstance(raw_seed, int) and not isinstance(raw_seed, bool), "cockpit seed must be an integer")
    if region is None:
        region_key = cockpit_profile["modes"][mode]["region_default"]
        raw_region = defaults.get(region_key)
        require(isinstance(raw_region, list) and len(raw_region) == 4, f"iteration profile lacks {region_key}")
        parsed_region = parse_region(",".join(str(value) for value in raw_region))
    else:
        parsed_region = parse_region(region)
    require(parsed_region[2] * parsed_region[3] <= cockpit_profile["limits"]["max_chunks"], "selected cockpit region exceeds the pack-profile chunk limit")
    if diagnostic_sample_modulo is not None:
        require(diagnostic_sample_modulo > 0, "diagnostic sample modulo must be positive")
    for timeout, name in ((startup_timeout, "startup"), (scan_timeout, "scan"), (stop_timeout, "stop")):
        require(timeout > 0, f"{name} timeout must be positive")

    default_plan = Path(root / str(iteration_profile.get("plan")))
    left_plan = _regular(baseline_plan or default_plan, "baseline plan")
    right_plan = _regular(candidate_plan or default_plan, "candidate plan")
    common_artifact = _regular(artifact, "common worldgen artifact")
    left_artifact = _regular(baseline_artifact, "baseline worldgen artifact")
    right_artifact = _regular(candidate_artifact, "candidate worldgen artifact")
    optional_paths = {
        "baseline_observatory_bundle": _regular(baseline_observatory_bundle, "baseline Observatory bundle"),
        "candidate_observatory_bundle": _regular(candidate_observatory_bundle, "candidate Observatory bundle"),
        "baseline_inventory": _regular(baseline_inventory, "baseline GTCEu inventory"),
        "candidate_inventory": _regular(candidate_inventory, "candidate GTCEu inventory"),
        "baseline_impact": _regular(baseline_impact, "baseline GTCEu impact inventory"),
        "candidate_impact": _regular(candidate_impact, "candidate GTCEu impact inventory"),
        "baseline_trace": _regular(baseline_trace, "baseline GTCEu trace"),
        "candidate_trace": _regular(candidate_trace, "candidate GTCEu trace"),
        "baseline_observer_off_jfr": _regular(baseline_observer_off_jfr, "baseline observer-off JFR summary"),
        "candidate_observer_off_jfr": _regular(candidate_observer_off_jfr, "candidate observer-off JFR summary"),
    }
    plan_digests = {_binding(left_plan)["sha256"], _binding(right_plan)["sha256"]}
    if common_artifact is not None:
        artifacts_are_shared = True
    elif left_artifact is not None and right_artifact is not None:
        artifacts_are_shared = _binding(left_artifact)["sha256"] == _binding(right_artifact)["sha256"]
    else:
        # The first side is built once and that exact output is frozen for the
        # second side. This is deliberately an A/A control when the plans also
        # match, regardless of which side executes first.
        artifacts_are_shared = True
    comparison_intent = (
        "identical-input-control"
        if len(plan_digests) == 1 and artifacts_are_shared
        else "before-after"
    )
    plan = {
        "format": "workbench-worldgen-cockpit-run-plan-v1",
        "schema_version": 1,
        "plan_id": "",
        "status": "ready",
        "profile": {
            "profile_id": cockpit_profile["profile_id"],
            "pack_profile": cockpit_profile["pack_profile"],
            "path": str(cockpit_profile_binding.path),
            "sha256": cockpit_profile_binding.sha256,
            "iteration_profile": {"path": str(iteration_binding.path), "sha256": iteration_binding.sha256},
        },
        "experiment": {
            "label": label,
            "mode": mode,
            "order": order,
            "seed": raw_seed,
            "region": list(parsed_region),
            "fresh_worlds": True,
            "comparison_intent": comparison_intent,
            "output_root": str(root / ".workbench/experiments/worldgen" / label),
        },
        "sides": {
            "baseline": {
                "label": f"{label}-baseline",
                "plan": _binding(left_plan),
                "artifact": None if common_artifact is None and left_artifact is None else _binding(common_artifact or left_artifact),
            },
            "candidate": {
                "label": f"{label}-candidate",
                "plan": _binding(right_plan),
                "artifact": None if common_artifact is None and right_artifact is None else _binding(common_artifact or right_artifact),
            },
        },
        "artifact_policy": (
            "common-exact-artifact"
            if common_artifact is not None
            else ("side-specific-exact-artifacts" if left_artifact is not None else "build-first-side-once-and-freeze-for-second")
        ),
        "runner_options": {
            "runtime_template": None if runtime_template is None else str(runtime_template.expanduser()),
            "strata_root": None if strata_root is None else str(strata_root.expanduser()),
            "java_cmd": java_cmd,
            "gradle_cmd": gradle_cmd,
            "heap": heap,
            "diagnostic_sample_modulo": diagnostic_sample_modulo,
            "startup_timeout": startup_timeout,
            "scan_timeout": scan_timeout,
            "stop_timeout": stop_timeout,
        },
        "optional_evidence": {
            key: None if value is None else _binding(value)
            for key, value in optional_paths.items()
        },
        "comparison_scope_sha256": comparison_scope_sha256,
        "effects": [
            "freeze both plans, the selected profile, and supplied artifacts/evidence under ignored .workbench storage",
            "provision two independent disposable runtimes and fresh worlds",
            "generate and capture the exact same seed, dimension, mode, and chunk window",
            "validate alignment before semantic, exact-state, statistical, causal, or performance comparison",
            "write one content-addressed report and self-contained local review",
        ],
    }
    plan["plan_id"] = "workbench-worldgen-cockpit-plan:sha256:" + sha256_json(
        {key: value for key, value in plan.items() if key != "plan_id"}
    )
    return plan


def render_run_plan(plan: Mapping[str, Any]) -> str:
    experiment = plan["experiment"]
    sides = plan["sides"]
    lines = [
        "WORLDGEN COCKPIT PLAN",
        f"READY · {experiment['mode']} mode · {experiment['order']}",
        f"Seed {experiment['seed']} · region {experiment['region']} · two fresh disposable worlds",
        f"Intent: {experiment['comparison_intent']}",
        "",
        f"Baseline:  {sides['baseline']['plan']['path']}",
        f"Candidate: {sides['candidate']['plan']['path']}",
        f"Artifacts: {plan['artifact_policy']}",
        f"Output:    {experiment['output_root']}",
        "",
        "Effects",
    ]
    lines.extend("  - " + effect for effect in plan["effects"])
    lines.extend(["", f"Plan: {plan['plan_id']}", "Use the console Execute action or omit --show to run it."])
    return "\n".join(lines) + "\n"


def _session_start(path: Path, plan: Mapping[str, Any], reproduction_command: str) -> dict[str, Any]:
    session = {
        "format": SESSION_FORMAT,
        "schema_version": 1,
        "label": plan["experiment"]["label"],
        "status": "incomplete",
        "started_at": _utc_now(),
        "completed_at": None,
        "plan_id": plan["plan_id"],
        "plan": deepcopy(dict(plan)),
        "stages": [],
        "side_reports": {},
        "report": None,
        "review": None,
        "failure": None,
        "reproduction_command": reproduction_command,
    }
    write_json_atomic(path, session)
    return session


def _session_write(path: Path, session: Mapping[str, Any]) -> None:
    write_json_atomic(path, session, replace=True)


def _stage_start(session: dict[str, Any], path: Path, stage_id: str, purpose: str) -> dict[str, Any]:
    stage = {
        "id": stage_id,
        "purpose": purpose,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "details": {},
        "error": None,
    }
    session["stages"].append(stage)
    _session_write(path, session)
    return stage


def _stage_complete(session: dict[str, Any], path: Path, stage: dict[str, Any]) -> None:
    stage["status"] = "complete"
    stage["completed_at"] = _utc_now()
    _session_write(path, session)


def _stage_fail(session: dict[str, Any], path: Path, stage: dict[str, Any], exc: Exception) -> None:
    message = f"{type(exc).__name__}: {exc}"
    stage["status"] = "failed"
    stage["completed_at"] = _utc_now()
    stage["error"] = message
    session["failure"] = {"stage": stage["id"], "error": message}
    _session_write(path, session)


def _append_option(arguments: list[str], flag: str, value: Any) -> None:
    if value is not None:
        arguments.extend([flag, str(value)])


def _iteration_arguments(
    *,
    frozen_iteration_profile: Path,
    plan: Mapping[str, Any],
    side: str,
    frozen_plan: Path,
    artifact: Path | None,
) -> list[str]:
    experiment = plan["experiment"]
    options = plan["runner_options"]
    arguments = [
        "--profile-file",
        str(frozen_iteration_profile),
        "--mode",
        experiment["mode"],
        "--seed",
        str(experiment["seed"]),
        "--region",
        ",".join(str(value) for value in experiment["region"]),
        "--plan",
        str(frozen_plan),
        "--label",
        plan["sides"][side]["label"],
        "--startup-timeout",
        str(options["startup_timeout"]),
        "--scan-timeout",
        str(options["scan_timeout"]),
        "--stop-timeout",
        str(options["stop_timeout"]),
        "--no-open",
    ]
    _append_option(arguments, "--runtime-template", options["runtime_template"])
    _append_option(arguments, "--strata-root", options["strata_root"])
    _append_option(arguments, "--java-cmd", options["java_cmd"])
    _append_option(arguments, "--gradle-cmd", options["gradle_cmd"])
    _append_option(arguments, "--heap", options["heap"])
    _append_option(arguments, "--diagnostic-sample-modulo", options["diagnostic_sample_modulo"])
    _append_option(arguments, "--artifact", artifact)
    return arguments


def _freeze_optional_inputs(
    plan: Mapping[str, Any], input_root: Path
) -> dict[str, Path | None]:
    result: dict[str, Path | None] = {}
    for key, binding in plan["optional_evidence"].items():
        if binding is None:
            result[key] = None
            continue
        suffix = Path(binding["path"]).suffix or ".json"
        result[key] = _copy_frozen(Path(binding["path"]), input_root / f"{key}{suffix}")
    return result


def execute_run_plan(
    *,
    root: Path,
    cockpit_profile: Mapping[str, Any],
    cockpit_profile_binding: FileBinding,
    plan: Mapping[str, Any],
    reproduction_command: str,
) -> tuple[dict[str, Any], Path, Path, Path]:
    experiment_root = Path(plan["experiment"]["output_root"])
    if experiment_root.exists() or experiment_root.is_symlink():
        raise CockpitError(f"cockpit experiment label already exists: {experiment_root}")
    experiment_root.mkdir(parents=True)
    session_path = experiment_root / "cockpit-session-v1.json"
    session = _session_start(session_path, plan, reproduction_command)
    current_stage: dict[str, Any] | None = None
    try:
        current_stage = _stage_start(session, session_path, "freeze", "Freeze both plans, profiles, artifacts, and optional evidence before either world starts.")
        inputs = experiment_root / "inputs"
        frozen_cockpit_profile = _copy_frozen(Path(cockpit_profile_binding.path), inputs / "worldgen-cockpit-profile-v1.json")
        frozen_iteration_profile = _copy_frozen(Path(cockpit_profile["_iteration_profile"]), inputs / "worldgen-iteration-profile-v2.json")
        frozen_subsurface_profile = _copy_frozen(Path(cockpit_profile["_subsurface_profile"]), inputs / "subsurface-profile-v1.json")
        frozen_cockpit_binding = FileBinding(
            frozen_cockpit_profile,
            sha256_file(frozen_cockpit_profile),
            frozen_cockpit_profile.stat().st_size,
        )
        frozen_cockpit_value = deepcopy(dict(cockpit_profile))
        frozen_cockpit_value["_path"] = str(frozen_cockpit_profile)
        frozen_cockpit_value["_iteration_profile"] = str(frozen_iteration_profile)
        frozen_cockpit_value["_subsurface_profile"] = str(frozen_subsurface_profile)
        frozen_plans = {
            side: _copy_frozen(Path(plan["sides"][side]["plan"]["path"]), inputs / f"{side}-plan.groovy")
            for side in ("baseline", "candidate")
        }
        frozen_artifacts: dict[str, Path | None] = {"baseline": None, "candidate": None}
        if plan["artifact_policy"] == "common-exact-artifact":
            source = Path(plan["sides"]["baseline"]["artifact"]["path"])
            common = _copy_frozen(source, inputs / "common-worldgen.jar")
            frozen_artifacts = {"baseline": common, "candidate": common}
        elif plan["artifact_policy"] == "side-specific-exact-artifacts":
            for side in ("baseline", "candidate"):
                frozen_artifacts[side] = _copy_frozen(
                    Path(plan["sides"][side]["artifact"]["path"]), inputs / f"{side}-worldgen.jar"
                )
        frozen_optional = _freeze_optional_inputs(plan, inputs)
        current_stage["details"] = {
            "cockpit_profile": str(frozen_cockpit_profile),
            "iteration_profile": str(frozen_iteration_profile),
            "subsurface_profile": str(frozen_subsurface_profile),
            "plans": {key: str(value) for key, value in frozen_plans.items()},
            "artifacts": {key: None if value is None else str(value) for key, value in frozen_artifacts.items()},
            "optional_evidence": {key: None if value is None else str(value) for key, value in frozen_optional.items()},
        }
        _stage_complete(session, session_path, current_stage)

        order = ["baseline", "candidate"] if plan["experiment"]["order"] == "baseline-first" else ["candidate", "baseline"]
        built_once: Path | None = None
        side_reports: dict[str, Path] = {}
        for index, side in enumerate(order):
            current_stage = _stage_start(session, session_path, f"run-{side}", f"Run the {side} case in its own disposable runtime and fresh world.")
            selected_artifact = frozen_artifacts[side]
            if plan["artifact_policy"] == "build-first-side-once-and-freeze-for-second" and index == 1:
                selected_artifact = built_once
            arguments = _iteration_arguments(
                frozen_iteration_profile=frozen_iteration_profile,
                plan=plan,
                side=side,
                frozen_plan=frozen_plans[side],
                artifact=selected_artifact,
            )
            current_stage["details"]["arguments"] = arguments
            _session_write(session_path, session)
            code = run_iteration(arguments, root=root)
            report_path = root / ".workbench/iterations/worldgen" / plan["sides"][side]["label"] / "iteration-report-v1.json"
            if code != 0:
                raise CockpitError(f"{side} iteration failed; retained report: {report_path}")
            report, _ = load_json_file(report_path, context=f"{side} completed iteration report")
            require(report.get("status") == "complete", f"{side} iteration returned success without a complete report")
            side_reports[side] = report_path.resolve(strict=True)
            session["side_reports"][side] = str(side_reports[side])
            if plan["artifact_policy"] == "build-first-side-once-and-freeze-for-second" and index == 0:
                artifact_path = Path(report["inputs"]["worldgen_artifact"]["path"])
                built_once = _copy_frozen(artifact_path, inputs / "common-built-worldgen.jar")
                current_stage["details"]["frozen_built_artifact"] = str(built_once)
            _stage_complete(session, session_path, current_stage)

        current_stage = _stage_start(session, session_path, "compare", "Validate alignment and compose semantic, exact-state, statistical, causal, and performance evidence.")
        report_path = experiment_root / "worldgen-cockpit-report-v1.json"
        review_path = experiment_root / "worldgen-cockpit-review.html"
        report = analyze_pair(
            root=root,
            cockpit_profile=frozen_cockpit_value,
            cockpit_profile_binding=frozen_cockpit_binding,
            baseline_report=side_reports["baseline"],
            candidate_report=side_reports["candidate"],
            reproduction_command=reproduction_command,
            baseline_observatory_bundle=frozen_optional["baseline_observatory_bundle"],
            candidate_observatory_bundle=frozen_optional["candidate_observatory_bundle"],
            comparison_scope_sha256=plan["comparison_scope_sha256"],
            baseline_inventory=frozen_optional["baseline_inventory"],
            candidate_inventory=frozen_optional["candidate_inventory"],
            baseline_impact=frozen_optional["baseline_impact"],
            candidate_impact=frozen_optional["candidate_impact"],
            baseline_trace=frozen_optional["baseline_trace"],
            candidate_trace=frozen_optional["candidate_trace"],
            baseline_observer_off_jfr=frozen_optional["baseline_observer_off_jfr"],
            candidate_observer_off_jfr=frozen_optional["candidate_observer_off_jfr"],
            review_path=review_path,
        )
        write_json_atomic(report_path, report)
        write_html(review_path, report)
        current_stage["details"] = {
            "report": str(report_path),
            "report_id": report["report_id"],
            "review": str(review_path),
            "status": report["status"],
            "coverage": report["coverage"],
        }
        _stage_complete(session, session_path, current_stage)
        session["status"] = "complete"
        session["completed_at"] = _utc_now()
        session["report"] = str(report_path)
        session["review"] = str(review_path)
        _session_write(session_path, session)
        return report, report_path, review_path, session_path
    except Exception as exc:
        if current_stage is not None and current_stage.get("status") == "running":
            _stage_fail(session, session_path, current_stage, exc)
        raise


def reproduction_command(argv: Sequence[str]) -> str:
    reusable: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--label":
            index += 2
            continue
        if argument.startswith("--label="):
            index += 1
            continue
        reusable.append(argument)
        index += 1
    return shlex.join(["python3", "tools/workbench.py", "cockpit", *reusable])


__all__ = [
    "build_run_plan",
    "execute_run_plan",
    "render_run_plan",
    "reproduction_command",
]
