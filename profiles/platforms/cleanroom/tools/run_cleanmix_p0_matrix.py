#!/usr/bin/env python3
"""Execute every CleanMix V1 P0 row in an isolated exact-candidate JVM."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[4]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_mixins import (  # noqa: E402
    parse_raw_config_lifecycle,
    parse_raw_discovery_trace,
    parse_raw_final_definition,
    parse_raw_service_components,
    parse_raw_transformer_chain,
)


MATRIX = (
    ROOT / "profiles/platforms/cleanroom/mixins"
    / "cleanmix-0.7.0-runtime-regression-matrix-v1.json"
)
CANDIDATE = (
    ROOT / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "worldgen-observatory-fixture"
)
CANDIDATE_LOCK = CANDIDATE.parent / "candidate-lock-v1.json"
TOOLCHAIN_LOCK = CANDIDATE.parent / "transformer-toolchain-lock-v1.json"
BUILD_LIBS = (
    ROOT / ".workbench/build/cleanroom/0.6.8-alpha"
    / "worldgen-observatory-fixture/libs"
)
GRADLE = ROOT / ".workbench/toolchains/gradle-9.6.1/bin/gradle"
JAVA_HOME = ROOT / ".workbench/toolchains/ide-validation-v1/jdk-25.0.4+7"

RESULT_PATTERNS = {
    "handler": re.compile(r"WORKBENCH_CLEANMIX_HANDLER_RESULT_V1 (\{.*\})"),
    "p0": re.compile(r"WORKBENCH_CLEANMIX_P0_RESULT_V1 (\{.*\})"),
}
TARGET_ALREADY_LOADED_PATTERN = re.compile(
    r"MixinTargetAlreadyLoadedException: Critical problem: .* target "
    r"(?P<target>[A-Za-z0-9_.$]+) was loaded too early\."
)
PARSERS: dict[str, Callable[[bytes], list[dict[str, Any]]]] = {
    "discovery": parse_raw_discovery_trace,
    "components": parse_raw_service_components,
    "config": parse_raw_config_lifecycle,
    "chain": parse_raw_transformer_chain,
    "final": parse_raw_final_definition,
}


@dataclass(frozen=True)
class RowSpec:
    row_id: str
    fixture: str
    side: str
    targets: tuple[str, ...]
    properties: tuple[tuple[str, str], ...]

    @property
    def slug(self) -> str:
        return self.row_id.lower().replace("_", "-")


ROWS = (
    RowSpec(
        "P0-HANDLER-PARENT-FIRST-SERVER", "handler", "dedicated_server",
        (
            "dev.workbench.cleanmixhandler.target.ParentTarget",
            "dev.workbench.cleanmixhandler.target.ChildTarget",
        ),
        (("workbench.cleanmix.handler.target_order", "parent_first"),),
    ),
    RowSpec(
        "P0-HANDLER-CHILD-FIRST-SERVER", "handler", "dedicated_server",
        (
            "dev.workbench.cleanmixhandler.target.ParentTarget",
            "dev.workbench.cleanmixhandler.target.ChildTarget",
        ),
        (("workbench.cleanmix.handler.target_order", "child_first"),),
    ),
    RowSpec(
        "P0-PHASE-SEQUENCE-SERVER", "p0", "dedicated_server",
        (
            "dev.workbench.cleanmixp0.PhaseTargets$Preinit",
            "dev.workbench.cleanmixp0.PhaseTargets$Init",
            "dev.workbench.cleanmixp0.PhaseTargets$Default",
        ), (),
    ),
    RowSpec(
        "P0-PHASE-SEQUENCE-CLIENT", "p0", "client",
        (
            "dev.workbench.cleanmixp0.PhaseTargets$Preinit",
            "dev.workbench.cleanmixp0.PhaseTargets$Init",
            "dev.workbench.cleanmixp0.PhaseTargets$Default",
        ), (),
    ),
    RowSpec(
        "P0-LATE-DEFAULT-BEFORE-TARGET-SERVER", "p0", "dedicated_server",
        ("dev.workbench.cleanmixp0.LateTargets$Target",), (),
    ),
    RowSpec(
        "P0-LATE-DEFAULT-AFTER-TARGET-SERVER", "p0", "dedicated_server",
        ("dev.workbench.cleanmixp0.LateTargets$Target",), (),
    ),
    RowSpec(
        "P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER", "p0", "dedicated_server",
        (
            "dev.workbench.cleanmixp0.KnownGapTargets$LazyParent",
            "dev.workbench.cleanmixp0.KnownGapTargets$LazyChild",
        ), (),
    ),
    RowSpec(
        "P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER", "p0", "dedicated_server",
        ("dev.workbench.cleanmixp0.KnownGapTargets$Reentrant",), (),
    ),
    RowSpec(
        "P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER", "p0", "dedicated_server",
        (
            "dev.workbench.cleanmixp0.KnownGapTargets$ThreeBase",
            "dev.workbench.cleanmixp0.KnownGapTargets$ThreeMiddle",
            "dev.workbench.cleanmixp0.KnownGapTargets$ThreeLeaf",
        ), (),
    ),
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def load_matrix() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    material = deepcopy(value)
    identity = material.pop("matrix_id")
    expected = (
        "cleanroom-cleanmix-runtime-regression-matrix:sha256:"
        + hashlib.sha256(canonical(material)).hexdigest()
    )
    if identity != expected:
        raise ValueError("regression matrix identity is stale")
    by_id = {row["id"]: row for row in value["rows"]}
    expected_ids = {row.row_id for row in ROWS}
    actual_ids = {
        row["id"] for row in value["rows"] if row["priority"] == "P0"
    }
    if actual_ids != expected_ids:
        raise ValueError("runner P0 row set differs from the matrix")
    return value, by_id


def build_fixtures() -> None:
    environment = dict(os.environ)
    environment["JAVA_HOME"] = str(JAVA_HOME)
    command = [
        str(GRADLE), "--no-daemon",
        "cleanmixDiscoveryTraceAgentJar",
        "cleanmixHandlerRegressionJar",
        "cleanmixP0RegressionJar",
    ]
    subprocess.run(command, cwd=CANDIDATE, env=environment, check=True)


def prepare_case(root: Path, row: RowSpec, port: int) -> tuple[Path, Path, Path]:
    case = root / row.slug
    server = case / "runtime"
    mods = server / "mods"
    mods.mkdir(parents=True)
    jar_name = (
        "cleanmix-handler-regression-0.1.0.jar"
        if row.fixture == "handler" else "cleanmix-p0-regression-0.1.0.jar"
    )
    harness = BUILD_LIBS / jar_name
    observer = (
        BUILD_LIBS
        / "worldgen-observatory-fixture-0.1.0-cleanmix-discovery-trace-agent.jar"
    )
    if not harness.is_file() or not observer.is_file():
        raise ValueError("built harness or observer artifact is missing")
    shutil.copyfile(harness, mods / jar_name)
    shutil.copyfile(observer, case / "observer-agent.jar")
    (server / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    (server / "server.properties").write_text(
        "online-mode=false\nserver-ip=127.0.0.1\nserver-port="
        + str(port) + "\n",
        encoding="utf-8",
    )
    return case, server, mods / jar_name


def execute_row(root: Path, row: RowSpec, ordinal: int) -> dict[str, Any]:
    case, runtime, harness = prepare_case(root, row, 25600 + ordinal)
    launch_id = "cleanmix-x01-" + row.slug + "-1"
    environment = dict(os.environ)
    environment["JAVA_HOME"] = str(JAVA_HOME)
    system_properties = [
        "-Djava.awt.headless=" + ("false" if row.side == "client" else "true"),
    ]
    if row.fixture == "handler":
        system_properties.append("-Dworkbench.cleanmix.handler.row_id=" + row.row_id)
    else:
        system_properties.append("-Dworkbench.cleanmix.p0.row_id=" + row.row_id)
    system_properties.extend("-D" + key + "=" + value for key, value in row.properties)
    environment["JAVA_TOOL_OPTIONS"] = " ".join(system_properties)

    command = [
        str(GRADLE), "--no-daemon",
        "runClient" if row.side == "client" else "runServer",
        "-PworkbenchServerRunDir=" + str(runtime),
    ]
    output_properties = {
        "workbenchCleanMixTraceOutput": case / "discovery.ndjson",
        "workbenchCleanMixComponentOutput": case / "components.ndjson",
        "workbenchCleanMixConfigLifecycleOutput": case / "config.ndjson",
        "workbenchCleanMixTransformerChainOutput": case / "chain.ndjson",
        "workbenchCleanMixFinalDefinitionOutput": case / "final.ndjson",
    }
    capture_properties = {
        "workbenchCleanMixTraceCaptureId",
        "workbenchCleanMixComponentCaptureId",
        "workbenchCleanMixConfigLifecycleCaptureId",
        "workbenchCleanMixTransformerChainCaptureId",
        "workbenchCleanMixFinalDefinitionCaptureId",
    }
    command.extend(
        "-P" + key + "=" + str(value)
        for key, value in output_properties.items()
    )
    command.extend("-P" + key + "=" + launch_id for key in sorted(capture_properties))
    command.append(
        "-PworkbenchCleanMixFinalDefinitionTargets=" + ",".join(row.targets)
    )
    launch_log = case / "launch.log"
    timed_out = False
    with launch_log.open("wb") as output:
        try:
            completed = subprocess.run(
                command, cwd=CANDIDATE, env=environment,
                stdout=output, stderr=subprocess.STDOUT, timeout=120,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124

    log_text = launch_log.read_text(encoding="utf-8", errors="replace")
    matches = RESULT_PATTERNS[row.fixture].findall(log_text)
    harness_result = json.loads(matches[-1]) if len(matches) == 1 else None
    runtime_signals = [
        {
            "signal": "mixin_target_already_loaded",
            "exception_class": (
                "org.spongepowered.asm.mixin.transformer.throwables."
                "MixinTargetAlreadyLoadedException"
            ),
            "target_class": target,
        }
        for target in sorted(set(TARGET_ALREADY_LOADED_PATTERN.findall(log_text)))
    ]
    traces: dict[str, Any] = {}
    parse_failures: list[str] = []
    for name, parser in PARSERS.items():
        path = case / (name + ".ndjson")
        if not path.is_file():
            parse_failures.append(name + ":missing")
            continue
        try:
            rows = parser(path.read_bytes())
            traces[name] = {
                "event_count": len(rows),
                "footer": rows[-1]["payload"],
            }
            if name == "final":
                traces[name]["definitions"] = [
                    event["payload"] for event in rows
                    if event["event"] == "final_definition"
                ]
            if name == "config":
                traces[name]["events"] = [
                    {
                        "event": event["event"],
                        "payload": event["payload"],
                    }
                    for event in rows
                    if event["event"] in {
                        "phase_pass_started", "phase_pass_completed",
                        "phase_eligibility", "config_stage",
                        "batch_promotion", "terminal_state",
                    }
                ]
        except Exception as exc:  # validation type differs by producer
            parse_failures.append(name + ":" + str(exc))

    disposition, failures = classify(
        row, exit_code, timed_out, harness_result, traces, runtime_signals,
        parse_failures
    )
    evidence_paths = [launch_log, harness, case / "observer-agent.jar"]
    evidence_paths.extend(
        case / (name + ".ndjson") for name in PARSERS
        if (case / (name + ".ndjson")).is_file()
    )
    result = {
        "row_id": row.row_id,
        "side": row.side,
        "launch_id": launch_id,
        "fresh_jvm": True,
        "process_exit_code": exit_code,
        "timed_out": timed_out,
        "actual_disposition": disposition,
        "failures": failures,
        "harness_result": harness_result,
        "runtime_signals": runtime_signals,
        "traces": traces,
        "evidence": [file_record(path, root) for path in evidence_paths],
    }
    (case / "row-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def classify(
    row: RowSpec, exit_code: int, timed_out: bool,
    result: dict[str, Any] | None, traces: dict[str, Any],
    runtime_signals: list[dict[str, str]],
    parse_failures: list[str],
) -> tuple[str, list[str]]:
    failures = list(parse_failures)
    if timed_out:
        failures.append("process_timeout")
    if exit_code != 0:
        failures.append("nonzero_process_exit")
    if result is None:
        failures.append("missing_or_duplicate_harness_result")
    for name in ("discovery", "components", "chain", "final"):
        if traces.get(name, {}).get("footer", {}).get("health") != "healthy":
            failures.append(name + "_capture_not_healthy")
    if result is None or failures:
        return "invalid", sorted(set(failures))

    final_footer = traces["final"]["footer"]
    if sorted(final_footer["defined_targets"]) != sorted(row.targets):
        return "invalid", ["final_target_set_mismatch"]
    if result.get("transform_observer_health") != "active":
        return "invalid", ["target_transform_observer_not_active"]

    if row.fixture == "handler":
        wanted = {
            "parent_handler_count": 1, "child_handler_count": 1,
            "original_body_count": 2, "parent_control_count": 1,
            "child_control_count": 1, "transform_order_oracle": True,
        }
        if all(result.get(key) == value for key, value in wanted.items()):
            return "pass", []
        return "fail", ["handler_behavior_or_first_seen_order_mismatch"]

    if row.row_id.startswith("P0-PHASE-SEQUENCE-"):
        wanted = {
            "preinit_value": 101, "init_value": 102, "default_value": 103,
            "preinit_marker_count": 1, "init_marker_count": 1,
            "default_marker_count": 1, "original_body_count": 3,
        }
        events = traces.get("config", {}).get("events", [])
        starts = [
            event["payload"]["pending_count"] for event in events
            if event["event"] == "phase_pass_started"
        ]
        completions = [
            event["payload"]["pending_count"] for event in events
            if event["event"] == "phase_pass_completed"
        ]
        if (all(result.get(key) == value for key, value in wanted.items())
                and starts == [3, 2, 1] and completions == [2, 1, 0]):
            return "pass", []
        return "fail", ["phase_queue_or_marker_oracle_mismatch"]

    if row.row_id == "P0-LATE-DEFAULT-BEFORE-TARGET-SERVER":
        wanted = {
            "loaded_before_registration": False,
            "loaded_after_registration": False,
            "value": 19, "late_marker_count": 1,
        }
        if all(result.get(key) == value for key, value in wanted.items()):
            return "pass", []
        return "fail", ["late_before_oracle_mismatch"]

    if row.row_id == "P0-LATE-DEFAULT-AFTER-TARGET-SERVER":
        events = traces.get("config", {}).get("events", [])
        stable = (
            result.get("loaded_before_registration") is True
            and result.get("baseline_value") == 7
            and result.get("after_value") == 7
            and result.get("class_identity_before") == result.get("class_identity_after")
            and result.get("late_marker_count") == 0
        )
        lifecycle_rejected = any(
            event["event"] == "phase_pass_completed"
            and event["payload"].get("outcome") == "threw"
            and event["payload"].get("exception_class")
                == "org.spongepowered.asm.mixin.throwables.MixinApplyError"
            for event in events
        )
        exact_cause = any(
            signal == {
                "signal": "mixin_target_already_loaded",
                "exception_class": (
                    "org.spongepowered.asm.mixin.transformer.throwables."
                    "MixinTargetAlreadyLoadedException"
                ),
                "target_class": "dev.workbench.cleanmixp0.LateTargets$Target",
            }
            for signal in runtime_signals
        )
        rejected = (
            result.get("trigger_failure_class") == "java.lang.ClassNotFoundException"
            and lifecycle_rejected
            and exact_cause
        )
        if stable and rejected:
            return "must_reject", []
        return "fail", ["target_already_loaded_rejection_not_observed"]

    if row.row_id == "P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER":
        if (
            result.get("child_handler_count") == 1
            and result.get("callback_info_null_count") == 1
            and result.get("original_body_count") == 1
        ):
            return "xfail", []
        if (
            result.get("child_handler_count") == 1
            and result.get("callback_info_null_count") == 0
            and result.get("original_body_count") == 1
        ):
            return "xpass", ["named_lazy_callbackinfo_gap_not_observed"]
        return "fail", ["lazy_callbackinfo_oracle_mismatch"]

    if row.row_id == "P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER":
        if (
            result.get("reentrant_action") == "target_defined_during_getMixins"
            and result.get("value") == 23
            and result.get("reentrant_marker_count") == 0
        ):
            return "xfail", []
        if result.get("value") == 29 and result.get("reentrant_marker_count") == 1:
            return "xpass", ["named_reentrant_pending_target_gap_not_observed"]
        return "fail", ["reentrant_pending_target_oracle_mismatch"]

    if row.row_id == "P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER":
        xfail_counts = (
            result.get("base_handler_count") == 1
            and result.get("middle_handler_count") == 2
            and result.get("leaf_handler_count") == 0
        )
        control = all(
            result.get(key) == 1
            for key in ("base_control_count", "middle_control_count", "leaf_control_count")
        )
        if xfail_counts and control and result.get("original_body_count") == 3:
            return "xfail", []
        if (
            control and result.get("original_body_count") == 3
            and all(result.get(key) == 1 for key in (
                "base_handler_count", "middle_handler_count", "leaf_handler_count"
            ))
        ):
            return "xpass", ["named_three_deep_order_gap_not_observed"]
        return "fail", ["three_deep_handler_oracle_mismatch"]
    return "invalid", ["runner_has_no_classifier"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if output.exists() or output.is_symlink():
        parser.error(f"output must be initially absent: {output}")
    matrix, by_id = load_matrix()
    build_fixtures()
    output.mkdir(parents=True)
    results: list[dict[str, Any]] = []
    for ordinal, row in enumerate(ROWS, 1):
        print(f"[{ordinal}/{len(ROWS)}] {row.row_id}", flush=True)
        result = execute_row(output, row, ordinal)
        result["expected_disposition"] = by_id[row.row_id]["expected_disposition"]
        results.append(result)
        print(f"  -> {result['actual_disposition']}", flush=True)

    counts: dict[str, int] = {}
    for result in results:
        disposition = result["actual_disposition"]
        counts[disposition] = counts.get(disposition, 0) + 1
    unexpected_passes = sorted(
        result["row_id"] for result in results
        if result["actual_disposition"] == "xpass"
    )
    matrix_record = file_record(MATRIX, ROOT)
    report = {
        "format": "workbench-cleanmix-p0-runtime-matrix-execution-v1",
        "schema_version": 1,
        "execution_id": None,
        "matrix": {
            "matrix_id": matrix["matrix_id"],
            "sha256": matrix_record["sha256"],
        },
        "profile": {
            "candidate_lock_sha256": sha256(CANDIDATE_LOCK),
            "toolchain_lock_sha256": sha256(TOOLCHAIN_LOCK),
            "cleanroom": "0.6.8-alpha",
            "cleanmix": "0.7.0",
            "foundation": "0.19.11",
            "java": "25.0.4+7",
        },
        "execution_state": "complete",
        "acceptance": {
            "state": "review",
            "supported_compatibility_claim": False,
            "historical_handler_control": "not_bound_to_execution",
            "unexpected_passes": unexpected_passes,
            "blockers": [
                "historical_handler_control_not_reproduced_or_bound",
                *(
                    ["unexpected_pass_requires_matrix_drift_review"]
                    if unexpected_passes else []
                ),
            ],
        },
        "row_isolation": "fresh_jvm_per_row",
        "rows": results,
        "summary": {
            "p0_row_count": len(results),
            "executed_row_count": len(results),
            "dispositions": dict(sorted(counts.items())),
        },
        "limitations": [
            "The V1 child-first oracle records first entry into a native no-op transformer; Foundation may invoke live and delegated paths around superclass definition, so that order is not equivalent to CleanMix application order.",
            "The historical pre-PR control is retained separately and is required before the V1 child-first behavior claim can be accepted.",
            "Expected-failure and must-reject rows are findings, not supported compatibility claims.",
        ],
    }
    material = deepcopy(report)
    material.pop("execution_id")
    report["execution_id"] = (
        "cleanmix-p0-runtime-matrix-execution:sha256:"
        + hashlib.sha256(canonical(material)).hexdigest()
    )
    (output / "execution-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(report["execution_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
