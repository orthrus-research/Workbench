"""Retained repair attempts remain readable before Setup can be inspected."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import ModuleError
from workbench_core import repair_cli
from workbench_core.repair_operations import RepairOperationStore, RESULT_FORMAT, default_repair_operation_root


class RepairOperationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "core-operations" / "repair"
        self.store = RepairOperationStore(self.root)
        self.plan_id = "workbench-repair-plan:sha256:" + "a" * 64

    def test_default_root_is_fixed_to_account_home(self):
        expected = default_repair_operation_root()
        with patch.dict("os.environ", {
            "HOME": str(self.base / "other-home"),
            "WORKBENCH_STATE_ROOT": str(self.base / "other-state"),
            "XDG_STATE_HOME": str(self.base / "other-xdg"),
            "LOCALAPPDATA": str(self.base / "other-local"),
        }):
            self.assertEqual(expected, default_repair_operation_root())

    def _result(self, outcome="repaired"):
        return {
            "format": RESULT_FORMAT,
            "schema_version": 1,
            "applied_plan_id": self.plan_id,
            "outcome": outcome,
            "installed": [],
            "git": {"state": "ready", "executable": "/fixture/git", "version": "fixture"},
            "setup_record_id": None,
            "recovered_setup_record": None,
            "failure": None if outcome != "partial" else "fixture package failure",
            "next_commands": [["workbench", "repair", "--check"]],
        }

    def test_completed_result_reopens_before_invalid_setup_probe(self):
        with self.store.apply_scope():
            operation = self.store.begin(self.plan_id)
            running = self.store.inspect(operation.record["operation_id"])
            self.assertEqual("incomplete", running["state"])
            self.assertIsNone(running["finished_at"])
            result = self._result()
            operation.finish(result=result)
        row, = self.store.list()
        self.assertEqual("completed", row["state"])
        self.assertEqual(result, row["result"])
        with patch.object(repair_cli, "inspect_repair", side_effect=AssertionError("Setup was inspected")), \
             patch.dict("os.environ", {"WORKBENCH_CONFIG_HOME": str(self.base / "invalid-config")}), \
             io.StringIO() as output:
            status = repair_cli.main(
                ["--history", row["operation_id"], "--json"],
                output=output, error=io.StringIO(),
                record_path=self.base / "invalid-setup.json",
                operation_root=self.root,
            )
            self.assertEqual(0, status)
            self.assertEqual(row, json.loads(output.getvalue()))

    def test_partial_and_interrupted_attempts_keep_original_result_meaning(self):
        with self.store.apply_scope():
            partial = self.store.begin(self.plan_id)
            partial.finish(result=self._result("partial"), error="fixture package failure")
            interrupted = self.store.begin(self.plan_id)
        rows = {row["operation_id"]: row for row in self.store.list()}
        self.assertEqual("partial", rows[partial.record["operation_id"]]["state"])
        self.assertEqual("partial", rows[partial.record["operation_id"]]["result"]["outcome"])
        self.assertEqual("incomplete", rows[interrupted.record["operation_id"]]["state"])
        self.assertIsNone(rows[interrupted.record["operation_id"]]["finished_at"])

    def test_failed_apply_retains_incomplete_receipt(self):
        plan = {"plan_id": self.plan_id, "blockers": [], "consent": {"required": True}}
        with patch.object(repair_cli, "inspect_repair", return_value={}), \
             patch.object(repair_cli, "build_repair_plan", return_value=plan), \
             patch.object(repair_cli, "_apply_plan", side_effect=repair_cli.RepairError("fixture failure")):
            status = repair_cli.main(
                ["--apply", self.plan_id, "--json"],
                output=io.StringIO(), error=io.StringIO(), operation_root=self.root,
            )
        self.assertEqual(2, status)
        row, = self.store.list()
        self.assertEqual("incomplete", row["state"])
        self.assertIsNone(row["result"])
        self.assertIn("fixture failure", row["error"])

    def test_changed_receipt_cannot_be_reopened(self):
        with self.store.apply_scope():
            operation = self.store.begin(self.plan_id)
        path = self.root / f"{operation.record['operation_id']}.json"
        changed = json.loads(path.read_text())
        changed["state"] = "completed"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ModuleError, "identity or state"):
            self.store.inspect(operation.record["operation_id"])



if __name__ == "__main__":
    unittest.main()
