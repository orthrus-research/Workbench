#!/usr/bin/env python3
"""Run the bounded CRUCIBLE-M2-X02 candidate and historical controls."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules/crucible/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_cleanmix_p0_matrix import (  # noqa: E402
    BUILD_LIBS,
    CANDIDATE,
    GRADLE,
    JAVA_HOME,
    PARSERS,
    RESULT_PATTERNS,
    RowSpec,
    build_fixtures,
    prepare_case,
)
from workbench_crucible_mixins import (  # noqa: E402
    build_direct_application_receipt,
    parse_raw_direct_application,
    write_direct_application_receipt,
)


BASE_ARTIFACT = (
    ROOT / ".workbench/cache/cleanmix-runtime-conformance/base-ac2b06c-maven"
    / "com/cleanroommc/cleanmix/0.6.0/cleanmix-0.6.0.jar"
)
GRADLE_USER_HOME = Path(
    os.environ.get("GRADLE_USER_HOME", str(Path.home() / ".gradle"))
)
CANDIDATE_ARTIFACTS = tuple(
    (GRADLE_USER_HOME / "caches/modules-2/files-2.1/com.cleanroommc/cleanmix/0.7.0")
    .glob("*/cleanmix-0.7.0.jar")
)
CANDIDATE_ARTIFACT = (
    CANDIDATE_ARTIFACTS[0]
    if len(CANDIDATE_ARTIFACTS) == 1
    else GRADLE_USER_HOME / "unresolved-cleanmix-0.7.0.jar"
)
BASE_INIT_SCRIPT = (
    ROOT / ".workbench/cache/cleanmix-runtime-conformance"
    / "force-cleanmix-base-ac2b06c.gradle"
)
@dataclass(frozen=True)
class Control:
    epoch: str
    row: RowSpec
    five_family: bool

    @property
    def slug(self) -> str:
        return self.epoch + "-" + self.row.slug


HIERARCHICAL = (
    "dev.workbench.cleanmixp0.KnownGapTargets$ThreeBase",
    "dev.workbench.cleanmixp0.KnownGapTargets$ThreeMiddle",
    "dev.workbench.cleanmixp0.KnownGapTargets$ThreeLeaf",
)
DETACHED_TWO = (
    "dev.workbench.cleanmixp0.DetachedTargets$TwoParent",
    "dev.workbench.cleanmixp0.DetachedTargets$TwoChild",
)
DETACHED_THREE = (
    "dev.workbench.cleanmixp0.DetachedTargets$ThreeBase",
    "dev.workbench.cleanmixp0.DetachedTargets$ThreeMiddle",
    "dev.workbench.cleanmixp0.DetachedTargets$ThreeLeaf",
)


def row(row_id: str, targets: tuple[str, ...]) -> RowSpec:
    return RowSpec(row_id, "p0", "dedicated_server", targets, ())


CONTROLS = (
    Control("candidate-0.7.0", row("P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER", HIERARCHICAL), True),
    Control("candidate-0.7.0", row("X02-THREE-DEEP-PARENT-FIRST-SERVER", HIERARCHICAL), True),
    Control(
        "candidate-0.7.0",
        RowSpec(
            "P0-HANDLER-CHILD-FIRST-SERVER", "handler", "dedicated_server",
            (
                "dev.workbench.cleanmixhandler.target.ParentTarget",
                "dev.workbench.cleanmixhandler.target.ChildTarget",
            ),
            (("workbench.cleanmix.handler.target_order", "child_first"),),
        ),
        True,
    ),
    Control("candidate-0.7.0", row("X02-DETACHED-TWO-CHILD-FIRST-SERVER", DETACHED_TWO), True),
    Control("candidate-0.7.0", row("X02-DETACHED-TWO-PARENT-FIRST-SERVER", DETACHED_TWO), True),
    Control("candidate-0.7.0", row("X02-DETACHED-THREE-CHILD-FIRST-SERVER", DETACHED_THREE), True),
    Control("candidate-0.7.0", row("X02-DETACHED-THREE-PARENT-FIRST-SERVER", DETACHED_THREE), True),
    Control("base-ac2b06c", row("P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER", HIERARCHICAL), False),
    Control(
        "base-ac2b06c",
        RowSpec(
            "P0-HANDLER-CHILD-FIRST-SERVER", "handler", "dedicated_server",
            (
                "dev.workbench.cleanmixhandler.target.ParentTarget",
                "dev.workbench.cleanmixhandler.target.ChildTarget",
            ),
            (("workbench.cleanmix.handler.target_order", "child_first"),),
        ),
        False,
    ),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")


def file_input(path: Path, label: str) -> dict[str, Any]:
    return {"label": label, "sha256": sha256(path), "size_bytes": path.stat().st_size}


def behavior(result: dict[str, Any] | None, row_id: str) -> str:
    if result is None:
        return "invalid"
    if "DETACHED-TWO" in row_id:
        values = (result.get("parent_handler_count"), result.get("child_handler_count"))
        controls = (result.get("parent_control_count"), result.get("child_control_count"))
        if controls != (1, 1) or result.get("original_body_count") != 2:
            return "invalid"
        return "non_discriminating" if values in {(1, 1), (1, 0)} else "unexpected"
    if row_id.startswith("P0-HANDLER-"):
        values = (result.get("parent_handler_count"), result.get("child_handler_count"))
        controls = (result.get("parent_control_count"), result.get("child_control_count"))
        if controls != (1, 1) or result.get("original_body_count") != 2:
            return "invalid"
        return "conforming" if values == (1, 1) else "affected"
    values = (
        result.get("base_handler_count"), result.get("middle_handler_count"),
        result.get("leaf_handler_count"),
    )
    controls = (
        result.get("base_control_count"), result.get("middle_control_count"),
        result.get("leaf_control_count"),
    )
    if controls != (1, 1, 1) or result.get("original_body_count") != 3:
        return "invalid"
    if values == (1, 1, 1):
        return "conforming"
    if row_id.startswith("X02-DETACHED-"):
        return "non_discriminating"
    return "unexpected"


def execute(output: Path, control: Control, ordinal: int) -> dict[str, Any]:
    case, runtime, harness = prepare_case(
        output / control.epoch, control.row, 25700 + ordinal
    )
    launch_id = "cleanmix-x02-" + control.slug
    agent = BUILD_LIBS / "worldgen-observatory-fixture-0.1.0-cleanmix-discovery-trace-agent.jar"
    shutil.copyfile(agent, case / "observer-agent.jar")
    environment = dict(os.environ)
    environment["JAVA_HOME"] = str(JAVA_HOME)
    environment["JAVA_TOOL_OPTIONS"] = (
        "-Djava.awt.headless=true -D"
        + (
            "workbench.cleanmix.handler.row_id="
            if control.row.fixture == "handler"
            else "workbench.cleanmix.p0.row_id="
        )
        + control.row.row_id
        + " "
        + " ".join(
            "-D" + key + "=" + value for key, value in control.row.properties
        )
    )
    command = [str(GRADLE), "--no-daemon"]
    if control.epoch == "base-ac2b06c":
        command.extend([
            "--init-script", str(BASE_INIT_SCRIPT),
            "-Pcleanroom_version=0.6.6-alpha",
        ])
    command.extend(["runServer", "-PworkbenchServerRunDir=" + str(runtime)])
    trace_names = ["final", "direct"]
    if control.five_family:
        trace_names = ["discovery", "components", "config", "chain", *trace_names]
    properties = {
        "discovery": "workbenchCleanMixTraceOutput",
        "components": "workbenchCleanMixComponentOutput",
        "config": "workbenchCleanMixConfigLifecycleOutput",
        "chain": "workbenchCleanMixTransformerChainOutput",
        "final": "workbenchCleanMixFinalDefinitionOutput",
        "direct": "workbenchCleanMixDirectApplicationOutput",
    }
    capture_properties = {
        "discovery": "workbenchCleanMixTraceCaptureId",
        "components": "workbenchCleanMixComponentCaptureId",
        "config": "workbenchCleanMixConfigLifecycleCaptureId",
        "chain": "workbenchCleanMixTransformerChainCaptureId",
        "final": "workbenchCleanMixFinalDefinitionCaptureId",
        "direct": "workbenchCleanMixDirectApplicationCaptureId",
    }
    for name in trace_names:
        command.append("-P" + properties[name] + "=" + str(case / (name + ".ndjson")))
        command.append("-P" + capture_properties[name] + "=" + launch_id)
    joined_targets = ",".join(control.row.targets)
    command.extend([
        "-PworkbenchCleanMixFinalDefinitionTargets=" + joined_targets,
        "-PworkbenchCleanMixDirectApplicationTargets=" + joined_targets,
    ])
    if control.epoch == "base-ac2b06c":
        command.append(
            "-PworkbenchCleanMixFinalDefinitionInputSha256="
            "e91b78341e98b7141666c128df0cc4917591430042bc0cb762858f7c0c0e46a2"
        )
    launch_log = case / "launch.log"
    timed_out = False
    with launch_log.open("wb") as stream:
        try:
            completed = subprocess.run(
                command, cwd=CANDIDATE, env=environment,
                stdout=stream, stderr=subprocess.STDOUT, timeout=120,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
    log_text = launch_log.read_text(encoding="utf-8", errors="replace")
    matches = RESULT_PATTERNS[control.row.fixture].findall(log_text)
    harness_result = json.loads(matches[0]) if len(matches) == 1 else None
    fixture_result = case / "harness-result.json"
    fixture_result.write_text(
        json.dumps(harness_result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    failures: list[str] = []
    traces: dict[str, Any] = {}
    for name in trace_names:
        path = case / (name + ".ndjson")
        parser = parse_raw_direct_application if name == "direct" else PARSERS[name]
        try:
            events = parser(path.read_bytes())
            traces[name] = {
                "event_count": len(events),
                "health": events[-1]["payload"]["health"],
            }
            if name == "direct":
                traces[name]["applications"] = [
                    event["payload"] for event in events
                    if event["event"] == "application_completed"
                ]
            if name == "final":
                traces[name]["definitions"] = [
                    event["payload"] for event in events
                    if event["event"] == "final_definition"
                ]
        except Exception as exc:
            failures.append(name + ":" + str(exc))
    if timed_out:
        failures.append("process_timeout")
    if exit_code != 0:
        failures.append("nonzero_process_exit")
    if harness_result is None:
        failures.append("missing_or_duplicate_harness_result")
    if not failures:
        direct_events = parse_raw_direct_application((case / "direct.ndjson").read_bytes())
        transform = next(
            event["payload"] for event in direct_events
            if event["event"] == "transform_applied"
        )
        artifact_path = CANDIDATE_ARTIFACT if control.epoch == "candidate-0.7.0" else BASE_ARTIFACT
        receipt = build_direct_application_receipt(
            launch_id=launch_id,
            profile_id=(
                "cleanroom-0.6.8-alpha-cleanmix-0.7.0"
                if control.epoch == "candidate-0.7.0"
                else "cleanroom-0.6.6-alpha-cleanmix-base-ac2b06c"
            ),
            side="dedicated_server",
            inputs={
                "agent_artifact": file_input(case / "observer-agent.jar", "direct observer agent"),
                "fixture_result": file_input(fixture_result, "fixture result"),
                "launch_log": file_input(launch_log, "launch log"),
                "raw_trace": file_input(case / "direct.ndjson", "direct application raw trace"),
            },
            target_artifact={
                "label": control.epoch + " CleanMix artifact",
                "sha256": sha256(artifact_path),
                "size_bytes": artifact_path.stat().st_size,
                "code_source_uri": transform["target_code_source_uri"],
            },
            raw_events=direct_events,
            limitations=(
                "Historical exact-base launches omit the four candidate-byte-bound discovery, component, lifecycle, and chain observers; enabling them would reject or contaminate the historical epoch.",
            ) if not control.five_family else (),
        )
        write_direct_application_receipt((case / "direct-receipt.json").resolve(), receipt)
        receipt_id = receipt["receipt_id"]
    else:
        receipt_id = None
    result = {
        "epoch": control.epoch,
        "row_id": control.row.row_id,
        "launch_id": launch_id,
        "fresh_jvm": True,
        "process_exit_code": exit_code,
        "timed_out": timed_out,
        "evidence_families": trace_names,
        "five_family_requirement": "satisfied" if control.five_family else "unavailable_version_guard",
        "failures": failures,
        "behavior": behavior(harness_result, control.row.row_id),
        "harness_result": harness_result,
        "traces": traces,
        "direct_receipt_id": receipt_id,
    }
    (case / "control-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if output.exists() or output.is_symlink():
        parser.error(f"output must be initially absent: {output}")
    for required in (BASE_ARTIFACT, CANDIDATE_ARTIFACT, BASE_INIT_SCRIPT):
        if not required.is_file():
            parser.error(f"required exact input is absent: {required}")
    if sha256(BASE_ARTIFACT) != "2ef7b9718506b6f56b55c87c45d280809ab055abc43dd4545a423eba86c3883c":
        parser.error("exact base artifact hash drifted")
    if sha256(CANDIDATE_ARTIFACT) != "a41daa71398e948bc09fa578ae2460be639df0fa82fccdb630b316418d5a4a36":
        parser.error("candidate artifact hash drifted")
    build_fixtures()
    output.mkdir(parents=True)
    results = []
    for ordinal, control in enumerate(CONTROLS, 1):
        print(f"[{ordinal}/{len(CONTROLS)}] {control.slug}", flush=True)
        result = execute(output, control, ordinal)
        results.append(result)
        print(f"  -> {result['behavior']} failures={len(result['failures'])}", flush=True)
    report = {
        "format": "workbench-cleanmix-x02-bounded-control-execution-v1",
        "schema_version": 1,
        "execution_id": "",
        "state": "complete" if all(not item["failures"] for item in results) else "failed",
        "exact_artifacts": {
            "candidate_0_7_0_sha256": sha256(CANDIDATE_ARTIFACT),
            "base_ac2b06c_sha256": sha256(BASE_ARTIFACT),
        },
        "rows": results,
        "limitations": [
            "The four legacy X01 observer implementations are exact-byte-bound to the candidate and cannot be admitted against the pre-PR artifact.",
            "Historical controls therefore bind direct application, Foundation final definition, unchanged fixture behavior, and process outcome, but do not claim healthy candidate-bound discovery/component/lifecycle/chain streams.",
        ],
    }
    material = deepcopy(report)
    material.pop("execution_id")
    report["execution_id"] = "cleanmix-x02-bounded-control-execution:sha256:" + hashlib.sha256(canonical(material)).hexdigest()
    (output / "execution-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(report["execution_id"])
    return 0 if report["state"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
