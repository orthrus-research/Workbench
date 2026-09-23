#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    CleanroomHookHealthCase,
    EXCLUDED_INCOMPLETE_CASE_ROLE,
    HOOK_HEALTH_EVALUATION_PREFIX,
    REQUIRED_CASE_ROLE,
    canonical_json_bytes,
    canonical_json_sha256,
    evaluate_cleanroom_hook_health,
    parse_cleanroom_hook_health_evaluation,
    write_cleanroom_hook_health_evaluation,
)


CANDIDATE_ROOT = (
    REPOSITORY_ROOT
    / "profiles"
    / "platforms"
    / "cleanroom"
    / "candidates"
    / "0.6.8-alpha"
)
SCHEMA_PATH = (
    CANDIDATE_ROOT
    / "worldgen-observatory-fixture"
    / "src"
    / "main"
    / "resources"
    / "workbench-worldgen-observatory-raw-v1.schema.json"
)
PLAN_PATH = CANDIDATE_ROOT / "worldgen-observatory-probe-plan-v1.json"
CANDIDATE_PATH = CANDIDATE_ROOT / "candidate-lock-v1.json"
RAW_FORMAT = "workbench-cleanroom-worldgen-observatory-raw-v1"
MODE = "lossless-fixture"


def digest(value: str | bytes) -> str:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def actor(fixture_sha256: str) -> dict[str, Any]:
    return {
        "binding": "workbench",
        "mod_id": "workbench_worldgen_observer",
        "class_name": "dev.workbench.worldgenobservatory.probe.ProbeRuntime",
        "method_name": "emit",
        "method_descriptor": "()V",
        "mapping_namespace": "mcp-stable_39",
        "code_source_sha256": fixture_sha256,
        "transformed_class_sha256": digest("actor-class"),
    }


def raw_row(
    capture_id: str,
    fixture_sha256: str,
    record_type: str,
    payload: dict[str, Any],
    *,
    state: str,
) -> dict[str, Any]:
    return {
        "format": RAW_FORMAT,
        "record_type": record_type,
        "sequence": -1,
        "capture_id": capture_id,
        "scope": {"dimension_id": 0, "chunk_x": None, "chunk_z": None},
        "causality": {
            "trace_id": None,
            "root_trigger_id": None,
            "span_id": None,
            "parent_span_id": None,
        },
        "actor": actor(fixture_sha256),
        "order": {
            "thread_name": "Server thread",
            "thread_id": 41,
            "thread_sequence": -1,
            "lamport": -1,
            "monotonic_ns": -1,
        },
        "outcome": {
            "state": state,
            "exception_class": None,
            "exception_message_sha256": None,
        },
        "coverage": {
            "mode": MODE,
            "detail_state": "complete",
            "dropped_record_count": 0,
        },
        "payload": payload,
    }


def health_row(
    capture_id: str,
    fixture_sha256: str,
    hook: dict[str, Any],
    state: str,
) -> dict[str, Any]:
    hook_id = hook["raw_hook_id"]
    return raw_row(
        capture_id,
        fixture_sha256,
        "probe_health",
        {
            "hook_id": hook_id,
            "health_state": state,
            "target_class": hook["target_class"],
            "target_method": hook["target_method"],
            "target_descriptor": hook["target_descriptor"],
            "original_class_sha256": digest("original:" + hook_id),
            "transformed_class_sha256": digest("transformed:" + hook_id),
            "expected_injection_count": hook["expected_cardinality"],
            "observed_injection_count": hook["expected_cardinality"],
        },
        state="observed",
    )


def reindex(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thread_counts: dict[tuple[str, int], int] = {}
    for sequence, row in enumerate(rows):
        row["sequence"] = sequence
        order = row["order"]
        thread = (order["thread_name"], order["thread_id"])
        order["thread_sequence"] = thread_counts.get(thread, 0)
        thread_counts[thread] = order["thread_sequence"] + 1
        order["lamport"] = sequence
        order["monotonic_ns"] = sequence * 10
    return rows


def rows_for_case(
    case_id: str,
    fixture_sha256: str,
    *,
    complete: bool,
) -> list[dict[str, Any]]:
    capture_id = "hook-health:" + case_id
    plan = load_json(PLAN_PATH)
    hooks = plan["hooks"]
    rows = [
        raw_row(
            capture_id,
            fixture_sha256,
            "capture_control",
            {
                "control": "start",
                "capture_control_id": capture_id + ":fixture",
                "controller": "dedicated_server_fixture_v1",
                "requested_mode": MODE,
                "world_seed_sha256": digest("seed"),
                "route_order": "forward",
                "selector_sha256": digest("selector"),
                "route_sha256": digest("route"),
                "selected_chunks": ["0,0"],
            },
            state="entered",
        )
    ]
    for hook in hooks:
        rows.append(health_row(capture_id, fixture_sha256, hook, "applied_not_reached"))
    if complete:
        for hook in hooks:
            rows.append(health_row(capture_id, fixture_sha256, hook, "reached"))
        rows.append(
            raw_row(
                capture_id,
                fixture_sha256,
                "capture_control",
                {
                    "control": "stop",
                    "capture_control_id": capture_id + ":fixture",
                    "controller": "dedicated_server_fixture_v1",
                    "requested_mode": MODE,
                    "route_order": "forward",
                    "route_sha256": digest("route"),
                    "completion_state": "complete",
                    "open_span_count": 0,
                    "open_write_count": 0,
                },
                state="returned",
            )
        )
    return reindex(rows)


class CleanroomHookHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture_sha256 = digest("fixture-artifact")
        self.plan = load_json(PLAN_PATH)
        self.candidate = load_json(CANDIDATE_PATH)

    def write_case(
        self,
        case_id: str,
        role: str,
        rows: list[dict[str, Any]],
    ) -> CleanroomHookHealthCase:
        raw_path = self.root / f"{case_id}.ndjson"
        raw_path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))
        raw_sha256 = file_digest(raw_path)
        complete = role == REQUIRED_CASE_ROLE
        capture_id = rows[0]["capture_id"]
        material: dict[str, Any] = {
            "schema": "workbench.crucible.exact-runtime-session-audit.v1",
            "case_id": case_id,
            "outcome": "completed" if complete else "crash",
            "observer_enabled": True,
            "candidate_lock": {
                "file_sha256": file_digest(CANDIDATE_PATH),
                "canonical_sha256": canonical_json_sha256(self.candidate),
            },
            "fixture_artifact": {
                "file_sha256": self.fixture_sha256,
                "size_bytes": 1024,
            },
            "installed_mod_set": {
                "canonical_sha256": digest("installed-mod-set"),
                "file_sha256": digest("installed-mod-set-file"),
                "runtime_class_source_sha256": digest("runtime-class-source"),
                "verified_artifacts": [
                    {
                        "relative_path": "mods/fixture.jar",
                        "sha256": self.fixture_sha256,
                        "size_bytes": 1024,
                    }
                ],
            },
            "configuration_set": {},
            "launch_log": {},
            "raw_capture": {
                "capture_id": capture_id,
                "file_sha256": raw_sha256,
                "fixture_completion_marker_count": 1 if complete else 0,
                "row_count": len(rows),
                "size_bytes": raw_path.stat().st_size,
                "stop_control_count": 1 if complete else 0,
                "terminal_sequence": len(rows) - 1,
                "terminal_state": (
                    "complete_and_stopped" if complete else "incomplete_without_stop"
                ),
            },
            "fixture_result": (
                {
                    "completion_state": "complete",
                    "runtime_inventory_sha256": digest("runtime-inventory:" + case_id),
                }
                if complete
                else None
            ),
            "foundation_class_dump": {
                "format": "workbench-foundation-class-dump-manifest-v1",
                "class_count": 42,
                "total_size_bytes": 4096,
                "manifest_sha256": digest("foundation:" + case_id),
            },
        }
        audit = {
            "audit_id": "crucible-exact-runtime-session-audit:sha256:"
            + canonical_json_sha256(material),
            **material,
        }
        audit_path = self.root / f"{case_id}-audit.json"
        audit_path.write_bytes(canonical_json_bytes(audit) + b"\n")
        return CleanroomHookHealthCase(
            case_id=case_id,
            case_role=role,
            raw_path=raw_path,
            expected_raw_sha256=raw_sha256,
            session_audit_path=audit_path,
            expected_session_audit_sha256=file_digest(audit_path),
        )

    def evaluate(self, cases: list[CleanroomHookHealthCase]) -> dict[str, Any]:
        return evaluate_cleanroom_hook_health(
            schema_path=SCHEMA_PATH,
            expected_schema_sha256=file_digest(SCHEMA_PATH),
            expected_schema_id=self.plan["raw_transport"]["schema_id"],
            probe_plan_path=PLAN_PATH,
            expected_probe_plan_sha256=file_digest(PLAN_PATH),
            expected_probe_plan_id=self.plan["plan_id"],
            candidate_lock_path=CANDIDATE_PATH,
            expected_candidate_lock_sha256=file_digest(CANDIDATE_PATH),
            expected_candidate_id=self.candidate["candidate_id"],
            cases=cases,
        )

    def assert_health_rejected(
        self,
        pattern: str,
        mutation: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        rows = rows_for_case("required", self.fixture_sha256, complete=True)
        mutation(rows)
        reindex(rows)
        case = self.write_case("required", REQUIRED_CASE_ROLE, rows)
        with self.assertRaisesRegex(CaptureValidationError, pattern):
            self.evaluate([case])

    def test_required_cases_and_explicit_crash_exclusion_are_content_addressed(self) -> None:
        aa1_rows = rows_for_case("aa-1", self.fixture_sha256, complete=True)
        reached = next(
            row
            for row in aa1_rows
            if row["record_type"] == "probe_health"
            and row["payload"]["health_state"] == "reached"
        )
        reached["actor"].update(
            binding="exact_target",
            mod_id="minecraft",
            code_source_sha256=digest("runtime-class-source"),
            transformed_class_sha256=reached["payload"]["transformed_class_sha256"],
        )
        aa1 = self.write_case(
            "aa-1",
            REQUIRED_CASE_ROLE,
            aa1_rows,
        )
        aa2 = self.write_case(
            "aa-2",
            REQUIRED_CASE_ROLE,
            rows_for_case("aa-2", self.fixture_sha256, complete=True),
        )
        crash = self.write_case(
            "crash-before-seal",
            EXCLUDED_INCOMPLETE_CASE_ROLE,
            rows_for_case("crash-before-seal", self.fixture_sha256, complete=False),
        )
        evaluation = self.evaluate([crash, aa2, aa1])

        self.assertEqual("passed", evaluation["status"])
        self.assertEqual(evaluation, parse_cleanroom_hook_health_evaluation(evaluation))
        self.assertIsNone(evaluation["cases"][0]["hook_health"]["hooks"][0]["canonical_role"])
        self.assertEqual(2, evaluation["required_case_count"])
        self.assertEqual(1, evaluation["excluded_incomplete_case_count"])
        self.assertEqual(
            ["aa-1", "aa-2", "crash-before-seal"],
            [case["case_id"] for case in evaluation["cases"]],
        )
        for case in evaluation["cases"][:2]:
            health = case["hook_health"]
            self.assertEqual(14, health["declared_hook_count"])
            self.assertEqual(14, health["observed_hook_count"])
            self.assertEqual([], health["missing_hook_ids"])
            self.assertEqual([], health["latest_not_reached_hook_ids"])
            self.assertTrue(all(row["latest_state"] == "reached" for row in health["hooks"]))
        self.assertEqual("excluded_incomplete_case", evaluation["cases"][2]["verdict"])

        material = dict(evaluation)
        evaluation_id = material.pop("evaluation_id")
        self.assertEqual(
            HOOK_HEALTH_EVALUATION_PREFIX + canonical_json_sha256(material),
            evaluation_id,
        )
        output = self.root / "nested" / "hook-health.json"
        write_cleanroom_hook_health_evaluation(output, evaluation)
        self.assertEqual(canonical_json_bytes(evaluation) + b"\n", output.read_bytes())

        invalid_role = deepcopy(evaluation)
        invalid_role["cases"][0]["hook_health"]["hooks"][0]["canonical_role"] = ""
        invalid_material = dict(invalid_role)
        invalid_material.pop("evaluation_id")
        invalid_role["evaluation_id"] = (
            HOOK_HEALTH_EVALUATION_PREFIX + canonical_json_sha256(invalid_material)
        )
        with self.assertRaisesRegex(
            CaptureValidationError,
            "canonical_role must be null or nonempty",
        ):
            parse_cleanroom_hook_health_evaluation(invalid_role)

    def test_rejects_missing_and_latest_not_reached_hooks(self) -> None:
        hook_id = self.plan["hooks"][0]["raw_hook_id"]

        def remove_hook(rows: list[dict[str, Any]]) -> None:
            rows[:] = [
                row
                for row in rows
                if not (
                    row["record_type"] == "probe_health"
                    and row["payload"]["hook_id"] == hook_id
                )
            ]

        self.assert_health_rejected("required hooks lack health records", remove_hook)

        def remove_reached(rows: list[dict[str, Any]]) -> None:
            rows[:] = [
                row
                for row in rows
                if not (
                    row["record_type"] == "probe_health"
                    and row["payload"]["hook_id"] == hook_id
                    and row["payload"]["health_state"] == "reached"
                )
            ]

        self.assert_health_rejected("required hooks were not latest reached", remove_reached)

    def test_rejects_duplicate_failed_and_inconsistent_health(self) -> None:
        def duplicate_reached(rows: list[dict[str, Any]]) -> None:
            reached = next(
                row
                for row in rows
                if row["record_type"] == "probe_health"
                and row["payload"]["health_state"] == "reached"
            )
            rows.insert(-1, deepcopy(reached))

        self.assert_health_rejected("duplicate probe health state reached", duplicate_reached)

        def failed(rows: list[dict[str, Any]]) -> None:
            health = next(row for row in rows if row["record_type"] == "probe_health")
            health["payload"]["health_state"] = "failed"

        self.assert_health_rejected("probe health reported failure", failed)

        def changed_identity(rows: list[dict[str, Any]]) -> None:
            first_hook = self.plan["hooks"][0]["raw_hook_id"]
            reached = next(
                row
                for row in rows
                if row["record_type"] == "probe_health"
                and row["payload"]["hook_id"] == first_hook
                and row["payload"]["health_state"] == "reached"
            )
            reached["payload"]["transformed_class_sha256"] = digest("other-class")

        self.assert_health_rejected("probe health class identity changed", changed_identity)

        def changed_count(rows: list[dict[str, Any]]) -> None:
            health = next(row for row in rows if row["record_type"] == "probe_health")
            health["payload"]["observed_injection_count"] = 2

        self.assert_health_rejected("probe health cardinality mismatch", changed_count)

    def test_rejects_raw_digest_actor_custody_and_session_content_tampering(self) -> None:
        rows = rows_for_case("required", self.fixture_sha256, complete=True)
        case = self.write_case("required", REQUIRED_CASE_ROLE, rows)
        with self.assertRaisesRegex(CaptureValidationError, "session audit raw digest mismatch"):
            self.evaluate([replace(case, expected_raw_sha256="0" * 64)])

        def foreign_actor(rows: list[dict[str, Any]]) -> None:
            health = next(row for row in rows if row["record_type"] == "probe_health")
            health["actor"]["code_source_sha256"] = digest("foreign-fixture")

        self.assert_health_rejected("probe health actor lacks session custody", foreign_actor)

        audit_path = Path(case.session_audit_path)
        audit = load_json(audit_path)
        audit["foundation_class_dump"]["class_count"] += 1
        audit_path.write_bytes(canonical_json_bytes(audit) + b"\n")
        tampered = replace(case, expected_session_audit_sha256=file_digest(audit_path))
        with self.assertRaisesRegex(CaptureValidationError, "session audit content identity mismatch"):
            self.evaluate([tampered])

    def test_rejects_transport_sequence_drop_and_unpinned_static_inputs(self) -> None:
        rows = rows_for_case("required", self.fixture_sha256, complete=True)
        rows[2]["sequence"] = 99
        case = self.write_case("required", REQUIRED_CASE_ROLE, rows)
        with self.assertRaisesRegex(CaptureValidationError, "global sequence is not contiguous"):
            self.evaluate([case])

        def dropped(rows: list[dict[str, Any]]) -> None:
            rows[3]["coverage"]["dropped_record_count"] = 1

        self.assert_health_rejected("coverage is incomplete or dropped records", dropped)

        valid = self.write_case(
            "valid",
            REQUIRED_CASE_ROLE,
            rows_for_case("valid", self.fixture_sha256, complete=True),
        )
        with self.assertRaisesRegex(CaptureValidationError, "raw schema digest mismatch"):
            evaluate_cleanroom_hook_health(
                schema_path=SCHEMA_PATH,
                expected_schema_sha256="0" * 64,
                expected_schema_id=self.plan["raw_transport"]["schema_id"],
                probe_plan_path=PLAN_PATH,
                expected_probe_plan_sha256=file_digest(PLAN_PATH),
                expected_probe_plan_id=self.plan["plan_id"],
                candidate_lock_path=CANDIDATE_PATH,
                expected_candidate_lock_sha256=file_digest(CANDIDATE_PATH),
                expected_candidate_id=self.candidate["candidate_id"],
                cases=[valid],
            )

    def test_cli_publishes_the_same_atomic_receipt(self) -> None:
        case = self.write_case(
            "cli-case",
            REQUIRED_CASE_ROLE,
            rows_for_case("cli-case", self.fixture_sha256, complete=True),
        )
        output = self.root / "cli-output" / "hook-health.json"
        script = MODULE_ROOT / "tools" / "evaluate_cleanroom_hook_health.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--schema",
                str(SCHEMA_PATH),
                "--schema-sha256",
                file_digest(SCHEMA_PATH),
                "--schema-id",
                self.plan["raw_transport"]["schema_id"],
                "--probe-plan",
                str(PLAN_PATH),
                "--probe-plan-sha256",
                file_digest(PLAN_PATH),
                "--probe-plan-id",
                self.plan["plan_id"],
                "--candidate-lock",
                str(CANDIDATE_PATH),
                "--candidate-lock-sha256",
                file_digest(CANDIDATE_PATH),
                "--candidate-id",
                self.candidate["candidate_id"],
                "--case",
                case.case_id,
                case.case_role,
                str(case.raw_path),
                case.expected_raw_sha256,
                str(case.session_audit_path),
                case.expected_session_audit_sha256,
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        receipt = load_json(output)
        self.assertEqual(receipt["evaluation_id"], completed.stdout.strip())
        self.assertEqual(canonical_json_bytes(receipt) + b"\n", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
