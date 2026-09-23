"""Plan and execute a bounded fresh-JVM world-generation qualification matrix."""

from __future__ import annotations

from copy import deepcopy
import itertools
from pathlib import Path
import re
import shlex
import shutil
from typing import Any, Mapping, Sequence

from workbench_worldgen_cockpit.analysis import analyze_pair
from workbench_worldgen_cockpit.model import (
    FileBinding,
    load_json_file,
    sha256_file,
    write_json_atomic,
)
from workbench_worldgen_cockpit.orchestrator import build_run_plan, execute_run_plan
from workbench_crucible_worldgen_iteration.iteration import parse_region

from .assessment import assess_matrix
from .model import (
    LABEL_RE,
    PLAN_FORMAT,
    SESSION_FORMAT,
    require,
    sha256_json,
    utc_now,
)
from .render import write_html
from .risk import scan_jars


def _regular(path: Path | None, context: str, *, suffix: str | None = None) -> Path | None:
    if path is None:
        return None
    requested = path.expanduser()
    require(not requested.is_symlink(), f"{context} cannot be a symlink: {requested}")
    resolved = requested.resolve(strict=True)
    require(resolved.is_file(), f"{context} is not a regular file: {resolved}")
    if suffix is not None:
        require(resolved.suffix.lower() == suffix, f"{context} must be a {suffix} file: {resolved}")
    return resolved


def _binding(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _copy_frozen(source: Path, destination: Path) -> Path:
    source = _regular(source, "qualification input")
    assert source is not None
    require(not destination.exists() and not destination.is_symlink(), f"frozen destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = _binding(source)
    shutil.copy2(source, destination)
    after = _binding(destination)
    require(before["sha256"] == after["sha256"] and before["size_bytes"] == after["size_bytes"], f"frozen qualification input drift: {source}")
    return destination.resolve(strict=True)


def _values(override: Sequence[Any] | None, default: Sequence[Any]) -> list[Any]:
    return list(override) if override else list(default)


def build_qualification_plan(
    *,
    root: Path,
    profile: Mapping[str, Any],
    profile_binding: FileBinding,
    cockpit_profile: Mapping[str, Any],
    cockpit_profile_binding: FileBinding,
    profile_name: str | None,
    suite_id: str,
    intent_id: str,
    label: str,
    subject_plan: Path | None,
    artifact: Path | None,
    seeds: Sequence[int] | None,
    regions: Sequence[str] | None,
    orders: Sequence[str] | None,
    heaps: Sequence[str] | None,
    pair_repetitions: int | None,
    allow_inconclusive: bool,
    risk_jars: Sequence[Path],
    runtime_template: Path | None,
    strata_root: Path | None,
    java_cmd: str | None,
    gradle_cmd: str | None,
    diagnostic_sample_modulo: int | None,
    startup_timeout: int,
    scan_timeout: int,
    stop_timeout: int,
) -> dict[str, Any]:
    require(suite_id in profile["suites"], f"unknown qualification suite: {suite_id}")
    require(intent_id in profile["intents"], f"unknown qualification intent: {intent_id}")
    require(LABEL_RE.fullmatch(label) is not None and len(label) <= 74, "qualification label must be path-safe and at most 74 characters so cell labels remain safe")
    if profile_name is not None:
        require(profile_name == profile["pack_profile"], "selected pack differs from the qualification profile")
    require(profile["pack_profile"] == cockpit_profile["pack_profile"], "qualification and Cockpit pack profiles differ")
    suite = profile["suites"][suite_id]
    selected_seeds = _values(seeds, suite["seeds"])
    selected_regions = _values(regions, suite["regions"])
    selected_orders = _values(orders, suite["orders"])
    selected_heaps = _values(heaps, suite["heaps"])
    repetitions = pair_repetitions if pair_repetitions is not None else suite["pair_repetitions"]
    require(isinstance(repetitions, int) and not isinstance(repetitions, bool) and repetitions > 0, "pair repetitions must be positive")
    require(all(isinstance(seed, int) and not isinstance(seed, bool) for seed in selected_seeds), "qualification seeds must be integers")
    require(len(selected_seeds) == len(set(selected_seeds)), "qualification seeds must be unique; use pair repetitions for repeats")
    require(all(isinstance(region, str) and region for region in selected_regions), "qualification regions are invalid")
    require(len(selected_regions) == len(set(selected_regions)), "qualification regions must be unique")
    for region in selected_regions:
        parsed = parse_region(region)
        require(parsed[2] * parsed[3] <= cockpit_profile["limits"]["max_chunks"], f"qualification region exceeds Cockpit profile bounds: {region}")
    require(all(order in {"baseline-first", "candidate-first"} for order in selected_orders), "qualification execution order is invalid")
    require(len(selected_orders) == len(set(selected_orders)), "qualification execution orders must be unique")
    require(all(isinstance(heap, str) and re.fullmatch(r"[1-9][0-9]*[KMGkmg]", heap) for heap in selected_heaps), "qualification heaps are invalid")
    require(len(selected_heaps) == len(set(selected_heaps)), "qualification heaps must be unique")
    cells: list[dict[str, Any]] = []
    for ordinal, (seed, region, order, heap, repeat_index) in enumerate(
        itertools.product(selected_seeds, selected_regions, selected_orders, selected_heaps, range(repetitions)),
        start=1,
    ):
        cells.append(
            {
                "cell_id": f"cell-{ordinal:03d}",
                "seed": seed,
                "region": region,
                "order": order,
                "heap": heap,
                "pair_repeat": repeat_index + 1,
                "independent_jvms": 2,
                "fresh_worlds": 2,
            }
        )
    require(bool(cells), "qualification matrix is empty")
    require(len(cells) <= profile["limits"]["max_cells"], f"qualification matrix has {len(cells)} cells; profile limit is {profile['limits']['max_cells']}")

    iteration_profile, _ = load_json_file(cockpit_profile["_iteration_profile"], context="qualification iteration profile")
    raw_default_plan = iteration_profile.get("plan")
    require(isinstance(raw_default_plan, str) and raw_default_plan, "iteration profile has no default plan")
    selected_plan = _regular(subject_plan or (root / raw_default_plan), "qualification subject plan")
    selected_artifact = _regular(artifact, "qualification subject artifact", suffix=".jar")
    selected_risks = [_regular(path, "qualification risk JAR", suffix=".jar") for path in risk_jars]
    for timeout, name in ((startup_timeout, "startup"), (scan_timeout, "scan"), (stop_timeout, "stop")):
        require(isinstance(timeout, int) and timeout > 0, f"{name} timeout must be positive")
    if diagnostic_sample_modulo is not None:
        require(diagnostic_sample_modulo > 0, "diagnostic sample modulo must be positive")

    required_capabilities = list(dict.fromkeys([*suite["required_capabilities"], *profile["intents"][intent_id]["required_capabilities"]]))
    planned_capabilities = {
        "independent-jvm": True,
        "fresh-world": True,
        "final-exact-capture": True,
        "multi-seed": len(set(selected_seeds)) >= 2,
        "execution-order": {"baseline-first", "candidate-first"} <= set(selected_orders),
        "heap-shape": len(set(selected_heaps)) >= 2,
        "repeated-pairs": repetitions >= 2,
        "static-risk-scan": bool(profile["risk_policy"]["scan_runtime_mods"] or selected_risks),
        "cross-perturbation-final-state": True,
        "stage-checkpoints": False,
        "traversal-order": False,
        "scheduled-settle": False,
        "warm-cache-replay": False,
    }
    known_gaps = [
        f"capability.{capability}: declared={profile['capabilities'].get(capability, 'unsupported')}, planned={planned_capabilities.get(capability, False)}"
        for capability in required_capabilities
        if profile["capabilities"].get(capability) != "supported" or not planned_capabilities.get(capability, False)
    ]
    automatically_acquired_evidence = {"semantic", "final_state", "statistical"}
    if suite["mode"] == "performance":
        automatically_acquired_evidence.add("performance")
    known_gaps.extend(
        f"evidence.{evidence}: this runner does not automatically acquire it per matrix cell"
        for evidence in profile["intents"][intent_id]["required_evidence"]
        if evidence not in automatically_acquired_evidence
    )
    plan: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "schema_version": 1,
        "plan_id": "",
        "status": "ready" if not known_gaps else "attention",
        "profile": {
            "profile_id": profile["profile_id"],
            "pack_profile": profile["pack_profile"],
            "path": str(profile_binding.path),
            "sha256": profile_binding.sha256,
            "cockpit_profile": {
                "profile_id": cockpit_profile["profile_id"],
                "path": str(cockpit_profile_binding.path),
                "sha256": cockpit_profile_binding.sha256,
                "iteration_profile": _binding(Path(cockpit_profile["_iteration_profile"])),
                "subsurface_profile": _binding(Path(cockpit_profile["_subsurface_profile"])),
            },
        },
        "qualification": {
            "label": label,
            "suite": suite_id,
            "intent": intent_id,
            "mode": suite["mode"],
            "output_root": str(root / ".workbench/qualifications/worldgen" / label),
            "estimated_jvm_launches": len(cells) * 2,
            "allow_known_inconclusive": allow_inconclusive,
            "acceptance_reachable_from_planned_acquisition": not known_gaps,
            "known_preflight_gaps": known_gaps,
        },
        "subject": {
            "plan": _binding(selected_plan),
            "artifact": None if selected_artifact is None else _binding(selected_artifact),
            "artifact_policy": "caller-supplied-global-exact-artifact" if selected_artifact else "build-first-cell-once-then-freeze-global-exact-artifact",
        },
        "matrix": cells,
        "declared_capabilities": deepcopy(profile["capabilities"]),
        "required_capabilities": required_capabilities,
        "planned_capabilities": planned_capabilities,
        "risk": {
            "scan_runtime_mods": profile["risk_policy"]["scan_runtime_mods"],
            "additional_jars": [_binding(path) for path in selected_risks if path is not None],
        },
        "runner_options": {
            "runtime_template": None if runtime_template is None else str(runtime_template.expanduser()),
            "strata_root": None if strata_root is None else str(strata_root.expanduser()),
            "java_cmd": java_cmd,
            "gradle_cmd": gradle_cmd,
            "diagnostic_sample_modulo": diagnostic_sample_modulo,
            "startup_timeout": startup_timeout,
            "scan_timeout": scan_timeout,
            "stop_timeout": stop_timeout,
        },
        "effects": [
            "freeze the qualification profile, subject plan, and one exact subject artifact under ignored .workbench storage",
            "execute every cell as two independent disposable Cleanroom runtimes and fresh worlds",
            "compare exact final-state and semantic evidence within each A/A pair and across matching seed/scope perturbations",
            "scan exact runtime mod JAR class surfaces for generic nondeterminism risk candidates",
            "fail closed when a required perturbation, stage, domain, byte surface, or evidence authority is unavailable",
        ],
    }
    plan["plan_id"] = "workbench-worldgen-qualification-plan:sha256:" + sha256_json({key: value for key, value in plan.items() if key != "plan_id"})
    return plan


def render_plan(plan: Mapping[str, Any]) -> str:
    qualification = plan["qualification"]
    lines = [
        "WORLDGEN QUALIFICATION PLAN",
        f"{str(plan['status']).upper()} · {qualification['suite']} suite · {qualification['intent']} intent · {qualification['mode']} Cockpit mode",
        f"{len(plan['matrix'])} matrix cells · {qualification['estimated_jvm_launches']} fresh JVM/world launches",
        f"Artifact: {plan['subject']['artifact_policy']}",
        f"Output:   {qualification['output_root']}",
        "",
        "Matrix",
    ]
    for cell in plan["matrix"]:
        lines.append(f"  {cell['cell_id']} seed={cell['seed']} region={cell['region']} order={cell['order']} heap={cell['heap']} repeat={cell['pair_repeat']}")
    lines.extend(["", "Required coverage", "  " + ", ".join(plan["required_capabilities"]), "", "Effects"])
    lines.extend("  - " + item for item in plan["effects"])
    if qualification["known_preflight_gaps"]:
        lines.extend(["", "Known preflight acceptance gaps"])
        lines.extend("  - " + item for item in qualification["known_preflight_gaps"])
        lines.append("  This matrix cannot reach acceptance as planned; use --allow-inconclusive only when partial evidence is worth its cost.")
    launch_hint = (
        "Pass --allow-inconclusive only to launch this knowingly partial matrix."
        if qualification["known_preflight_gaps"]
        else "Use the console Execute action or omit --show to launch the matrix."
    )
    lines.extend(["", f"Plan: {plan['plan_id']}", launch_hint])
    return "\n".join(lines) + "\n"


def _write_session(path: Path, session: Mapping[str, Any]) -> None:
    write_json_atomic(path, session, replace=True)


def _runtime_jars(cockpit_report: Mapping[str, Any]) -> list[Path]:
    iteration_path = cockpit_report.get("sides", {}).get("baseline", {}).get("iteration_report", {}).get("path")
    require(isinstance(iteration_path, str), "Cockpit report lacks a baseline iteration source")
    iteration, _ = load_json_file(iteration_path, context="qualification runtime inventory source")
    runtime_raw = iteration.get("outputs", {}).get("runtime")
    require(isinstance(runtime_raw, str), "iteration report lacks a runtime path")
    runtime = Path(runtime_raw).resolve(strict=True)
    require(runtime.is_dir() and not runtime.is_symlink(), "iteration runtime is unavailable or unsafe")
    result: list[Path] = []
    for row in iteration.get("inputs", {}).get("runtime_mods", []):
        if not isinstance(row, Mapping):
            continue
        filename = row.get("file")
        digest = row.get("sha256")
        size = row.get("size_bytes")
        require(isinstance(filename, str) and Path(filename).name == filename, "runtime mod inventory contains an unsafe filename")
        path = (runtime / "mods" / filename).resolve(strict=True)
        require(path.is_file() and not path.is_symlink() and runtime in path.parents, f"runtime mod is unavailable: {filename}")
        require(sha256_file(path) == digest and path.stat().st_size == size, f"runtime mod drifted after capture: {filename}")
        result.append(path)
    return result


def _cross_reports(
    *,
    root: Path,
    output_root: Path,
    cockpit_profile: Mapping[str, Any],
    cockpit_profile_binding: FileBinding,
    entries: Sequence[Mapping[str, Any]],
    limit: int,
    reproduction_command: str,
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for entry in entries:
        perturbations = entry["perturbations"]
        key = (perturbations["seed"], perturbations["region"], entry["report"].get("mode"))
        groups.setdefault(key, []).append(entry)
    produced: list[dict[str, Any]] = []
    ordinal = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        anchor = members[0]
        anchor_iteration = Path(anchor["report"]["sides"]["baseline"]["iteration_report"]["path"])
        for candidate in members[1:]:
            ordinal += 1
            require(ordinal <= limit, "cross-perturbation comparison count exceeds the profile bound")
            candidate_iteration = Path(candidate["report"]["sides"]["baseline"]["iteration_report"]["path"])
            cross = analyze_pair(
                root=root,
                cockpit_profile=cockpit_profile,
                cockpit_profile_binding=cockpit_profile_binding,
                baseline_report=anchor_iteration,
                candidate_report=candidate_iteration,
                reproduction_command=reproduction_command,
            )
            path = output_root / "cross-comparisons" / f"cross-{ordinal:03d}.json"
            write_json_atomic(path, cross)
            produced.append(
                {
                    "report": cross,
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "run_paths": [str(anchor_iteration), str(candidate_iteration)],
                    "perturbations": {
                        "kind": "cross-perturbation",
                        "seed": anchor["perturbations"]["seed"],
                        "region": anchor["perturbations"]["region"],
                        "from_cell": anchor["perturbations"]["cell_id"],
                        "to_cell": candidate["perturbations"]["cell_id"],
                        "order": f"{anchor['perturbations']['order']}->{candidate['perturbations']['order']}",
                        "heap": f"{anchor['perturbations']['heap']}->{candidate['perturbations']['heap']}",
                    },
                }
            )
    return produced


def execute_qualification_plan(
    *,
    root: Path,
    profile: Mapping[str, Any],
    profile_binding: FileBinding,
    cockpit_profile: Mapping[str, Any],
    cockpit_profile_binding: FileBinding,
    plan: Mapping[str, Any],
    reproduction_command: str,
) -> tuple[dict[str, Any], Path, Path, Path]:
    output_root = Path(plan["qualification"]["output_root"])
    require(not output_root.exists() and not output_root.is_symlink(), f"qualification label already exists: {output_root}")
    output_root.mkdir(parents=True)
    session_path = output_root / "qualification-session-v1.json"
    session: dict[str, Any] = {
        "format": SESSION_FORMAT,
        "schema_version": 1,
        "status": "incomplete",
        "started_at": utc_now(),
        "completed_at": None,
        "plan_id": plan["plan_id"],
        "stages": [],
        "cockpit_reports": [],
        "report": None,
        "failure": None,
    }
    _write_session(session_path, session)
    current: dict[str, Any] | None = None

    def start(stage_id: str, purpose: str) -> dict[str, Any]:
        stage = {"id": stage_id, "purpose": purpose, "status": "running", "started_at": utc_now(), "completed_at": None, "details": {}, "error": None}
        session["stages"].append(stage)
        _write_session(session_path, session)
        return stage

    def complete(stage: dict[str, Any]) -> None:
        stage["status"] = "complete"
        stage["completed_at"] = utc_now()
        _write_session(session_path, session)

    try:
        current = start("freeze", "Freeze reusable qualification inputs before the first JVM launches.")
        inputs = output_root / "inputs"
        frozen_profile = _copy_frozen(Path(profile_binding.path), inputs / "worldgen-qualification-profile-v1.json")
        frozen_cockpit_profile = _copy_frozen(Path(cockpit_profile_binding.path), inputs / "worldgen-cockpit-profile-v1.json")
        frozen_iteration_profile = _copy_frozen(Path(cockpit_profile["_iteration_profile"]), inputs / "worldgen-iteration-profile-v2.json")
        frozen_subsurface_profile = _copy_frozen(Path(cockpit_profile["_subsurface_profile"]), inputs / "subsurface-profile-v1.json")
        frozen_plan = _copy_frozen(Path(plan["subject"]["plan"]["path"]), inputs / "subject-plan.groovy")
        require(sha256_file(frozen_profile) == plan["profile"]["sha256"], "qualification profile changed after planning")
        require(sha256_file(frozen_cockpit_profile) == plan["profile"]["cockpit_profile"]["sha256"], "Cockpit profile changed after planning")
        require(sha256_file(frozen_iteration_profile) == plan["profile"]["cockpit_profile"]["iteration_profile"]["sha256"], "iteration profile changed after planning")
        require(sha256_file(frozen_subsurface_profile) == plan["profile"]["cockpit_profile"]["subsurface_profile"]["sha256"], "subsurface profile changed after planning")
        require(_binding(frozen_plan)["sha256"] == plan["subject"]["plan"]["sha256"] and frozen_plan.stat().st_size == plan["subject"]["plan"]["size_bytes"], "subject plan changed after planning")
        frozen_profile_binding = FileBinding(frozen_profile, sha256_file(frozen_profile), frozen_profile.stat().st_size)
        frozen_cockpit_binding = FileBinding(frozen_cockpit_profile, sha256_file(frozen_cockpit_profile), frozen_cockpit_profile.stat().st_size)
        frozen_cockpit_value = deepcopy(dict(cockpit_profile))
        frozen_cockpit_value["_path"] = str(frozen_cockpit_profile)
        frozen_cockpit_value["_iteration_profile"] = str(frozen_iteration_profile)
        frozen_cockpit_value["_subsurface_profile"] = str(frozen_subsurface_profile)
        common_artifact: Path | None = None
        if plan["subject"]["artifact"] is not None:
            common_artifact = _copy_frozen(Path(plan["subject"]["artifact"]["path"]), inputs / "common-worldgen.jar")
            require(_binding(common_artifact)["sha256"] == plan["subject"]["artifact"]["sha256"] and common_artifact.stat().st_size == plan["subject"]["artifact"]["size_bytes"], "subject artifact changed after planning")
        additional_risk_jars = [Path(row["path"]) for row in plan["risk"]["additional_jars"]]
        for path, binding in zip(additional_risk_jars, plan["risk"]["additional_jars"], strict=True):
            require(sha256_file(path) == binding["sha256"] and path.stat().st_size == binding["size_bytes"], f"additional risk JAR changed after planning: {path}")
        current["details"] = {"profile": str(frozen_profile), "cockpit_profile": str(frozen_cockpit_profile), "iteration_profile": str(frozen_iteration_profile), "subsurface_profile": str(frozen_subsurface_profile), "plan": str(frozen_plan), "artifact": None if common_artifact is None else str(common_artifact)}
        complete(current)

        entries: list[dict[str, Any]] = []
        for index, cell in enumerate(plan["matrix"], start=1):
            current = start(f"run-{cell['cell_id']}", "Execute one independent two-JVM A/A Cockpit cell.")
            cockpit_label = f"{plan['qualification']['label']}-{cell['cell_id']}"
            cockpit_plan = build_run_plan(
                root=root,
                cockpit_profile=frozen_cockpit_value,
                cockpit_profile_binding=frozen_cockpit_binding,
                profile_name=profile["pack_profile"],
                mode=plan["qualification"]["mode"],
                label=cockpit_label,
                seed=cell["seed"],
                region=cell["region"],
                order=cell["order"],
                baseline_plan=frozen_plan,
                candidate_plan=frozen_plan,
                artifact=common_artifact,
                baseline_artifact=None,
                candidate_artifact=None,
                runtime_template=None if plan["runner_options"]["runtime_template"] is None else Path(plan["runner_options"]["runtime_template"]),
                strata_root=None if plan["runner_options"]["strata_root"] is None else Path(plan["runner_options"]["strata_root"]),
                java_cmd=plan["runner_options"]["java_cmd"],
                gradle_cmd=plan["runner_options"]["gradle_cmd"],
                heap=cell["heap"],
                diagnostic_sample_modulo=plan["runner_options"]["diagnostic_sample_modulo"],
                startup_timeout=plan["runner_options"]["startup_timeout"],
                scan_timeout=plan["runner_options"]["scan_timeout"],
                stop_timeout=plan["runner_options"]["stop_timeout"],
                baseline_observatory_bundle=None,
                candidate_observatory_bundle=None,
                comparison_scope_sha256=None,
                baseline_inventory=None,
                candidate_inventory=None,
                baseline_impact=None,
                candidate_impact=None,
                baseline_trace=None,
                candidate_trace=None,
                baseline_observer_off_jfr=None,
                candidate_observer_off_jfr=None,
            )
            cockpit_report, cockpit_path, _, _ = execute_run_plan(
                root=root,
                cockpit_profile=frozen_cockpit_value,
                cockpit_profile_binding=frozen_cockpit_binding,
                plan=cockpit_plan,
                reproduction_command=reproduction_command,
            )
            if common_artifact is None:
                artifact_path = Path(cockpit_report["sides"]["candidate"]["worldgen_artifact"]["path"])
                common_artifact = _copy_frozen(artifact_path, inputs / "common-worldgen.jar")
                require(sha256_file(common_artifact) == cockpit_report["sides"]["candidate"]["worldgen_artifact"]["sha256"], "first-cell artifact drifted before global freeze")
            entry = {
                "report": cockpit_report,
                "path": str(cockpit_path),
                "sha256": sha256_file(cockpit_path),
                "run_paths": [
                    cockpit_report["sides"]["baseline"]["iteration_report"]["path"],
                    cockpit_report["sides"]["candidate"]["iteration_report"]["path"],
                ],
                "perturbations": {"kind": "within-cell", **deepcopy(cell)},
            }
            entries.append(entry)
            session["cockpit_reports"].append(str(cockpit_path))
            current["details"] = {"report": str(cockpit_path), "report_id": cockpit_report["report_id"], "status": cockpit_report["status"], "artifact_sha256": sha256_file(common_artifact)}
            complete(current)

        current = start("cross-compare", "Compare matching seed/scope cells so heap and execution-order sensitivity cannot hide behind stable within-cell pairs.")
        cross_entries = _cross_reports(
            root=root,
            output_root=output_root,
            cockpit_profile=frozen_cockpit_value,
            cockpit_profile_binding=frozen_cockpit_binding,
            entries=entries,
            limit=profile["limits"]["max_cross_comparisons"],
            reproduction_command=reproduction_command,
        )
        entries.extend(cross_entries)
        current["details"] = {"cross_comparison_count": len(cross_entries), "reports": [row["path"] for row in cross_entries]}
        complete(current)

        current = start("risk-scan", "Scan exact runtime JAR class surfaces for generic order, entropy, filesystem, reflection, GC, and concurrency risks.")
        risk_paths: list[Path] = list(additional_risk_jars)
        if plan["risk"]["scan_runtime_mods"]:
            risk_paths.extend(_runtime_jars(entries[0]["report"]))
        unique_risks = list(dict.fromkeys(path.resolve(strict=True) for path in risk_paths))
        risk_report = None if not unique_risks else scan_jars(unique_risks, limits=profile["limits"], dispositions=profile["risk_policy"]["dispositions"])
        risk_path = None if risk_report is None else output_root / "worldgen-static-risk-report-v1.json"
        if risk_path is not None:
            write_json_atomic(risk_path, risk_report)
        current["details"] = {"report": None if risk_path is None else str(risk_path), "coverage": None if risk_report is None else risk_report["coverage"], "summary": None if risk_report is None else risk_report["summary"]}
        complete(current)

        current = start("qualify", "Evaluate domain, evidence, perturbation, and risk gates without inventing an allowed-noise path.")
        report_path = output_root / "worldgen-qualification-report-v1.json"
        review_path = output_root / "worldgen-qualification-review.html"
        nav = [{"kind": "qualification-review", "label": "Open qualification decision", "target": str(review_path)}]
        if risk_path is not None:
            nav.append({"kind": "static-risk-report", "label": "Open exact static risk report", "target": str(risk_path)})
        nav.extend({"kind": "cockpit-report", "label": f"Open {entry['perturbations'].get('cell_id', 'cross comparison')}", "target": entry["path"]} for entry in entries)
        report = assess_matrix(
            profile={**profile, "_path": str(frozen_profile)},
            profile_binding=frozen_profile_binding,
            cockpit_entries=entries,
            suite_id=plan["qualification"]["suite"],
            intent_id=plan["qualification"]["intent"],
            risk_scan=risk_report,
            reproduction_command=reproduction_command,
            navigation=nav,
        )
        write_json_atomic(report_path, report)
        write_html(review_path, report)
        current["details"] = {"report": str(report_path), "review": str(review_path), "report_id": report["report_id"], "status": report["status"]}
        complete(current)
        session["status"] = "complete"
        session["completed_at"] = utc_now()
        session["report"] = str(report_path)
        _write_session(session_path, session)
        return report, report_path, review_path, session_path
    except Exception as exc:
        if current is not None and current.get("status") == "running":
            current["status"] = "failed"
            current["completed_at"] = utc_now()
            current["error"] = f"{type(exc).__name__}: {exc}"
        session["failure"] = {"stage": None if current is None else current.get("id"), "error": f"{type(exc).__name__}: {exc}"}
        _write_session(session_path, session)
        raise


def reproduction_command(argv: Sequence[str]) -> str:
    reusable: list[str] = []
    skip = False
    for index, argument in enumerate(argv):
        if skip:
            skip = False
            continue
        if argument == "--label":
            skip = True
            continue
        if argument.startswith("--label="):
            continue
        reusable.append(argument)
    return shlex.join(["python3", "tools/workbench.py", "qualify", *reusable])


__all__ = ["build_qualification_plan", "execute_qualification_plan", "render_plan", "reproduction_command"]
