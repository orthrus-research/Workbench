#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import CaptureValidationError  # noqa: E402
from workbench_crucible_observatory.cleanroom_raw import (  # noqa: E402
    PROBE_PLAN_PREFIX,
    RawAdmission,
    admit_cleanroom_raw,
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
RAW_FORMAT = "workbench-cleanroom-worldgen-observatory-raw-v1"
CAPTURE_NONCE = "raw-capture-nonce:test"
MODE = "lossless-fixture"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def actor() -> dict[str, Any]:
    return {
        "binding": "workbench",
        "mod_id": "workbench_worldgen_observer",
        "class_name": "dev.workbench.worldgenobservatory.probe.ProbeRuntime",
        "method_name": "emit",
        "method_descriptor": "()V",
        "mapping_namespace": "mcp-stable_39",
        "code_source_sha256": digest("observer-code"),
        "transformed_class_sha256": digest("observer-class"),
    }


def row(record_type: str, payload: dict[str, Any], *, state: str) -> dict[str, Any]:
    return {
        "format": RAW_FORMAT,
        "record_type": record_type,
        "sequence": -1,
        "capture_id": CAPTURE_NONCE,
        "scope": {"dimension_id": 0, "chunk_x": None, "chunk_z": None},
        "causality": {
            "trace_id": None,
            "root_trigger_id": None,
            "span_id": None,
            "parent_span_id": None,
        },
        "actor": actor(),
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


def reindex(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per_thread: dict[tuple[str, int], int] = {}
    for sequence, item in enumerate(rows):
        item["sequence"] = sequence
        order = item["order"]
        thread = (order["thread_name"], order["thread_id"])
        order["thread_sequence"] = per_thread.get(thread, 0)
        per_thread[thread] = order["thread_sequence"] + 1
        order["lamport"] = sequence
        order["monotonic_ns"] = sequence * 10
    return rows


def valid_rows() -> list[dict[str, Any]]:
    plan = load_json(PLAN_PATH)
    hooks = plan["hooks"]
    rows = [
        row(
            "capture_control",
            {
                "control": "start",
                "capture_control_id": CAPTURE_NONCE + ":dedicated_server_fixture_v1",
                "controller": "dedicated_server_fixture_v1",
                "requested_mode": MODE,
                "world_seed_sha256": digest("world-seed"),
                "route_order": "forward",
                "selector_sha256": digest("selector"),
                "route_sha256": digest("route"),
                "selected_chunks": ["0,0", "1,0"],
            },
            state="entered",
        )
    ]
    for hook in hooks:
        rows.append(health_row(hook, "applied_not_reached"))
    for hook in hooks:
        if hook["canonical_role"] is not None:
            rows.append(health_row(hook, "reached"))
    rows.append(
        row(
            "capture_control",
            {
                "control": "stop",
                "capture_control_id": CAPTURE_NONCE + ":dedicated_server_fixture_v1",
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


def health_row(hook: dict[str, Any], health_state: str) -> dict[str, Any]:
    hook_id = hook["raw_hook_id"]
    return row(
        "probe_health",
        {
            "hook_id": hook_id,
            "health_state": health_state,
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


class CleanroomRawAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.raw_path = Path(self.temporary.name) / "capture.ndjson"

    def write_rows(self, rows: list[dict[str, Any]]) -> None:
        self.raw_path.write_text(
            "\n".join(
                json.dumps(item, allow_nan=False, separators=(",", ":"))
                for item in rows
            )
            + "\n",
            encoding="utf-8",
        )

    def admit(
        self,
        rows: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> RawAdmission:
        self.write_rows(valid_rows() if rows is None else rows)
        arguments = {
            "schema_path": SCHEMA_PATH,
            "probe_plan_path": PLAN_PATH,
            "requested_mode": MODE,
        }
        arguments.update(overrides)
        return admit_cleanroom_raw(self.raw_path, **arguments)

    def assert_rejected(
        self,
        pattern: str,
        mutation: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        rows = valid_rows()
        mutation(rows)
        with self.assertRaisesRegex(CaptureValidationError, pattern):
            self.admit(rows)

    def test_admits_frozen_file_order_and_raw_summaries_only(self) -> None:
        admitted = self.admit()
        self.assertIsInstance(admitted, RawAdmission)
        self.assertEqual(CAPTURE_NONCE, admitted.capture_nonce)
        self.assertEqual(
            list(range(len(admitted.rows))),
            [item["sequence"] for item in admitted.rows],
        )
        self.assertTrue(admitted.control_summary["present"])
        self.assertEqual("complete", admitted.control_summary["completion_state"])
        self.assertEqual(len(admitted.rows), admitted.coverage_summary["row_count"])
        self.assertEqual("complete", admitted.coverage_summary["detail_state"])
        required_roles = {
            hook["canonical_role"]
            for hook in load_json(PLAN_PATH)["hooks"]
            if hook["canonical_role"] is not None
        }
        reached_roles = {
            item["canonical_role"]
            for item in admitted.health_summary.values()
            if item["health_state"] == "reached"
        }
        self.assertEqual(required_roles, reached_roles)
        with self.assertRaises(TypeError):
            admitted.rows[0]["sequence"] = 9  # type: ignore[index]
        with self.assertRaises(TypeError):
            admitted.health_summary["new"] = {}  # type: ignore[index]
        self.assertFalse(hasattr(admitted, "publication"))
        self.assertFalse(hasattr(admitted, "semantic_fingerprints"))

    def test_admission_streams_rows_without_a_whole_file_text_read(self) -> None:
        rows = valid_rows()
        self.write_rows(rows)
        original_read_text = Path.read_text

        def reject_capture_read_text(
            path: Path,
            *args: Any,
            **kwargs: Any,
        ) -> str:
            if path == self.raw_path:
                raise AssertionError("whole-file text read is forbidden")
            return original_read_text(path, *args, **kwargs)

        with patch.object(
            Path,
            "read_text",
            reject_capture_read_text,
        ):
            admitted = admit_cleanroom_raw(
                self.raw_path,
                schema_path=SCHEMA_PATH,
                probe_plan_path=PLAN_PATH,
                requested_mode=MODE,
            )

        self.assertEqual(
            [item["record_type"] for item in rows],
            [item["record_type"] for item in admitted.rows],
        )
        self.assertEqual(
            list(range(len(rows))),
            [item["sequence"] for item in admitted.rows],
        )

    def test_rejects_blank_physical_lines(self) -> None:
        lines = [
            json.dumps(item, allow_nan=False, separators=(",", ":"))
            for item in valid_rows()
        ]
        lines.insert(1, "   ")
        self.raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(
            CaptureValidationError,
            "raw NDJSON line 2 is blank",
        ):
            admit_cleanroom_raw(
                self.raw_path,
                schema_path=SCHEMA_PATH,
                probe_plan_path=PLAN_PATH,
                requested_mode=MODE,
            )

    def test_rejects_invalid_utf8_and_invalid_json(self) -> None:
        self.raw_path.write_bytes(b'{"record_type":"capture_control"}\n\xff\n')
        with self.assertRaisesRegex(
            CaptureValidationError,
            "cannot read raw NDJSON",
        ):
            admit_cleanroom_raw(
                self.raw_path,
                schema_path=SCHEMA_PATH,
                probe_plan_path=PLAN_PATH,
                requested_mode=MODE,
            )

        first = json.dumps(valid_rows()[0], separators=(",", ":"))
        self.raw_path.write_text(first + "\n{not-json}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            CaptureValidationError,
            "cannot parse raw NDJSON line 2",
        ):
            admit_cleanroom_raw(
                self.raw_path,
                schema_path=SCHEMA_PATH,
                probe_plan_path=PLAN_PATH,
                requested_mode=MODE,
            )

    def test_rejects_duplicate_json_keys_and_closed_schema_extras(self) -> None:
        rows = valid_rows()
        lines = [json.dumps(item, separators=(",", ":")) for item in rows]
        lines[0] = lines[0][:-1] + ',"sequence":0}'
        self.raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(CaptureValidationError, "duplicate JSON key"):
            admit_cleanroom_raw(
                self.raw_path,
                schema_path=SCHEMA_PATH,
                probe_plan_path=PLAN_PATH,
                requested_mode=MODE,
            )

        self.assert_rejected(
            "raw schema violation.*Additional properties",
            lambda value: value[1].update(undeclared=True),
        )

    def test_rejects_global_and_per_thread_order_corruption(self) -> None:
        self.assert_rejected(
            "duplicate global sequence",
            lambda rows: rows[2].update(sequence=rows[1]["sequence"]),
        )
        self.assert_rejected(
            "global sequence is not contiguous",
            lambda rows: rows[2].update(sequence=99),
        )
        self.assert_rejected(
            "duplicate thread sequence",
            lambda rows: rows[2]["order"].update(
                thread_sequence=rows[1]["order"]["thread_sequence"]
            ),
        )
        self.assert_rejected(
            "monotonic time moved backward",
            lambda rows: rows[2]["order"].update(monotonic_ns=0),
        )

    def test_rejects_nonce_and_lossless_coverage_corruption(self) -> None:
        self.assert_rejected(
            "multiple capture nonces",
            lambda rows: rows[2].update(capture_id="raw-capture-nonce:other"),
        )
        self.assert_rejected(
            "empty or unbound",
            lambda rows: [item.update(capture_id="unbound") for item in rows],
        )
        self.assert_rejected(
            "requested mode mismatch",
            lambda rows: rows[2]["coverage"].update(mode="trace"),
        )
        self.assert_rejected(
            "detail is not complete",
            lambda rows: rows[2]["coverage"].update(detail_state="unavailable"),
        )
        self.assert_rejected(
            "cumulative dropped count is nonzero",
            lambda rows: rows[2]["coverage"].update(dropped_record_count=1),
        )

    def test_requires_matching_completed_start_stop_controls(self) -> None:
        def remove_stop(rows: list[dict[str, Any]]) -> None:
            rows.pop()
            reindex(rows)

        self.assert_rejected(
            "exactly one start and one stop",
            remove_stop,
        )
        self.assert_rejected(
            "control ID is mismatched",
            lambda rows: rows[-1]["payload"].update(
                capture_control_id=CAPTURE_NONCE + ":other"
            ),
        )
        self.assert_rejected(
            "stop requested mode mismatch",
            lambda rows: rows[-1]["payload"].update(requested_mode="trace"),
        )
        self.assert_rejected(
            "outcomes do not prove a completed pair",
            lambda rows: rows[-1]["outcome"].update(state="incomplete"),
        )
        self.assert_rejected(
            "reports open spans or writes",
            lambda rows: rows[-1]["payload"].update(open_span_count=1),
        )
        self.assert_rejected(
            "route_sha256 mismatch",
            lambda rows: rows[-1]["payload"].update(route_sha256=digest("other")),
        )

    def test_incomplete_mode_admits_only_a_valid_raw_prefix_or_explicit_stop(self) -> None:
        without_stop = valid_rows()
        without_stop.pop()
        reindex(without_stop)
        admitted = self.admit(without_stop, allow_incomplete=True)
        self.assertEqual("incomplete", admitted.control_summary["completion_state"])
        self.assertEqual("missing_stop", admitted.control_summary["incomplete_reason"])
        self.assertIsNone(admitted.control_summary["stop_sequence"])
        self.assertEqual((), admitted.coverage_summary["required_roles"])

        explicit = valid_rows()
        explicit[-1]["payload"]["completion_state"] = "incomplete"
        explicit[-1]["payload"]["open_span_count"] = 1
        explicit[-1]["outcome"]["state"] = "incomplete"
        admitted = self.admit(explicit, allow_incomplete=True)
        self.assertEqual("incomplete", admitted.control_summary["completion_state"])
        self.assertEqual(
            "explicit_incomplete_stop",
            admitted.control_summary["incomplete_reason"],
        )

        with self.assertRaisesRegex(CaptureValidationError, "explicitly incomplete"):
            self.admit(valid_rows(), allow_incomplete=True)

        midstream = valid_rows()
        stop = midstream.pop()
        midstream.insert(2, stop)
        reindex(midstream)
        with self.assertRaisesRegex(CaptureValidationError, "must be the last"):
            self.admit(midstream, allow_incomplete=True)

        foreign = deepcopy(explicit)
        foreign[-1]["payload"]["capture_control_id"] = "foreign:control"
        with self.assertRaisesRegex(CaptureValidationError, "ID is mismatched"):
            self.admit(foreign, allow_incomplete=True)

    def test_probe_health_is_plan_bound_and_latest_required_role_reached(self) -> None:
        def first_health(rows: list[dict[str, Any]]) -> dict[str, Any]:
            return next(item for item in rows if item["record_type"] == "probe_health")

        self.assert_rejected(
            "unplanned hook",
            lambda rows: first_health(rows)["payload"].update(
                hook_id="cleanroom-worldgen:unplanned"
            ),
        )
        self.assert_rejected(
            "target tuple mismatch",
            lambda rows: first_health(rows)["payload"].update(
                target_method="wrongMethod"
            ),
        )
        self.assert_rejected(
            "cardinality mismatch",
            lambda rows: first_health(rows)["payload"].update(
                observed_injection_count=0
            ),
        )

        plan = load_json(PLAN_PATH)
        required_hook = next(
            hook for hook in plan["hooks"] if hook["canonical_role"] is not None
        )

        def remove_reach(rows: list[dict[str, Any]]) -> None:
            rows[:] = [
                item
                for item in rows
                if not (
                    item["record_type"] == "probe_health"
                    and item["payload"]["hook_id"] == required_hook["raw_hook_id"]
                    and item["payload"]["health_state"] == "reached"
                )
            ]
            reindex(rows)

        self.assert_rejected("was not latest reached", remove_reach)

    def test_schema_bytes_must_match_the_candidate_plan_binding(self) -> None:
        schema = load_json(SCHEMA_PATH)
        schema["title"] = schema["title"] + " tampered"
        altered_schema = Path(self.temporary.name) / "altered.schema.json"
        altered_schema.write_text(
            json.dumps(schema, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CaptureValidationError, "schema digest mismatch"):
            self.admit(schema_path=altered_schema)

    def test_tampered_probe_plan_identity_is_rejected(self) -> None:
        plan = load_json(PLAN_PATH)
        plan["hooks"][0]["target_method"] = "tampered"
        altered_plan = Path(self.temporary.name) / "altered-plan.json"
        altered_plan.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(CaptureValidationError, "plan ID digest mismatch"):
            self.admit(probe_plan_path=altered_plan)

        material = deepcopy(plan)
        material.pop("plan_id")
        plan["plan_id"] = PROBE_PLAN_PREFIX + canonical_digest(material)
        altered_plan.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(CaptureValidationError, "target tuple mismatch"):
            self.admit(probe_plan_path=altered_plan)


if __name__ == "__main__":
    unittest.main()
