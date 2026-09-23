"""CI selection must never turn missing required coverage into a green gate."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation"))
from ci_validation import REQUIRED_TESTS, STAGES, gate, plan, required_test_failures
from suite_measurement import inventory_digest


class CiValidationTests(unittest.TestCase):
    def selected(self, event="pull_request", paths=None):
        return plan(event, paths if paths is not None else ["modules/crucible/src/graph.py"], revision="a" * 40)

    def outcomes(self, document):
        return {"plan": {"result": "success"}, **{
            row["name"]: {"result": "success" if row["required"] else "skipped"}
            for row in document["stages"]}}

    def test_every_source_or_unknown_change_requires_complete_stages(self):
        for path in ("api/src/workbench_api/canonical.py", "modules/workbench-shell/src/main.py",
                     "clients/vscode/package-lock.json", "new-owner/something", "docs/contract.schema.json"):
            with self.subTest(path=path):
                document = self.selected(paths=[path])
                self.assertEqual(set(STAGES) - {"physical-cleanroom"}, {row["name"] for row in document["stages"] if row["required"]})
                self.assertEqual([], gate(document, self.outcomes(document)))

    def test_only_known_documentation_prs_omit_ide(self):
        document = self.selected(paths=["README.md", "docs/guide.md"])
        self.assertEqual(["validation-native-fixtures"], [row["name"] for row in document["excluded_suites"]])
        self.assertEqual(["not-run"], [row["state"] for row in document["excluded_suites"]])
        self.assertFalse(next(row["required"] for row in document["stages"] if row["name"] == "ide"))
        self.assertEqual([], gate(document, self.outcomes(document)))
        for event in ("push", "schedule", "workflow_dispatch"):
            self.assertTrue(next(row["required"] for row in self.selected(event, ["README.md"])["stages"] if row["name"] == "ide"))
        self.assertTrue(next(row["required"] for row in self.selected(paths=[])["stages"] if row["name"] == "ide"))

    def test_required_skip_failure_cancellation_and_absence_fail(self):
        document = self.selected("schedule")
        for stage in ("plan", *STAGES):
            for outcome in ("skipped", "failure", "cancelled", None):
                with self.subTest(stage=stage, outcome=outcome):
                    results = self.outcomes(document)
                    if outcome is None:
                        del results[stage]
                    else:
                        results[stage]["result"] = outcome
                    self.assertTrue(gate(document, results))

    def test_forged_selection_or_malformed_manifest_cannot_pass(self):
        document = self.selected()
        altered = copy.deepcopy(document)
        altered["stages"][1]["required"] = False
        self.assertTrue(gate(altered, self.outcomes(altered)))
        self.assertTrue(gate({}, {}))
        self.assertTrue(gate(document, []))
        with self.assertRaises(ValueError):
            self.selected(paths=["../outside"])

    def test_workflow_wires_complete_lanes_and_an_always_running_gate(self):
        workflow = yaml.load((ROOT / ".github/workflows/validate.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertTrue({"pull_request", "push", "schedule", "workflow_dispatch"} <= set(workflow["on"]))
        repository = json.loads((ROOT / "packaging/release/public-repository-v1.json").read_text())
        branch = repository["history_policy"]["public_default_branch"]
        self.assertEqual([branch], workflow["on"]["push"]["branches"])
        jobs = workflow["jobs"]
        self.assertEqual(set(STAGES) | {"plan"}, set(jobs["required-validation"]["needs"]))
        self.assertEqual("always()", jobs["required-validation"]["if"])
        self.assertEqual("ubuntu-24.04", jobs["native-packages"]["runs-on"])
        source = "\n".join(step.get("run", "") for step in jobs["source-ci"]["steps"])
        self.assertIn("--node-only", source)
        self.assertIn("--tier source-ci", source)
        self.assertIn("validation-native-fixtures --collect-only --report", source)
        portability = yaml.load((ROOT / ".github/workflows/portability.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual({"windows-2025", "macos-15"}, set(portability["jobs"]["native-portability"]["strategy"]["matrix"]["os"]))
        native = "\n".join(step.get("run", "") for step in jobs["native-packages"]["steps"])
        self.assertIn("--from-wheelhouse", native)
        self.assertIn("--wheelhouse .workbench/native-suite", native)
        self.assertIn("tools/install_workbench.py", native)
        ide = "\n".join(step.get("run", "") for step in jobs["ide"]["steps"])
        self.assertIn("xvfb-run", ide)
        self.assertIn("--full --installed-core", ide)
        axiom = "\n".join(step.get("run", "") for step in jobs["axiom"]["steps"])
        for tool in ("build_axiom.py", "axiom_sources.py", "build_axiom_target.py", "axiom_target_smoke.py", "axiom_source_conformance.py", "axiom_loader_conformance.py", "axiom_composition_conformance.py"):
            self.assertIn(tool, axiom)

    @patch("ci_validation._current_source_fingerprint", return_value="source:a")
    def test_required_probe_assertion_rejects_skips_stale_ids_and_missing_rows(self, current_source):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_path = root / ".workbench/validation/runs/current"
            (run_path / "reports").mkdir(parents=True)
            identity = {"run_id": "current", "source_fingerprint": "source:a"}
            result = root / "result.json"
            result.write_text(json.dumps({**identity, "format": "workbench-validation-invocation-v1", "phases": {"preflight": {"state": "passed"}, "python": {"state": "passed"}}, "state": "passed", "python_run_path": str(run_path)}))
            (run_path / "run.json").write_text(json.dumps({**identity, "state": "passed"}))
            suite, test_id = REQUIRED_TESTS["pip"][0]
            report = {**identity, "format": "workbench-python-test-timing-v3", "suite": suite, "authority": "Core",
                      "successful": True, "state": "passed", "discovered_tests": 1, "executed_tests": 1,
                      "seconds": 0.1, "phases": {}, "fixture_events": [], "collected_ids": [test_id],
                      "inventory_digest": inventory_digest([test_id]),
                      "tests": [{"test": test_id, "outcome": "passed", "seconds": 0.1}]}
            (run_path / "reports" / f"{suite}.admitted.json").write_text(json.dumps({
                **identity, "format": "workbench-python-test-admission-v1", "test_ids": [test_id],
                "inventory_digest": inventory_digest([test_id]),
            }))
            path = run_path / "reports" / f"{suite}.json"
            path.write_text(json.dumps(report))
            self.assertEqual([], required_test_failures(result, "pip", root=root))
            current_source.return_value = "source:changed"
            self.assertTrue(required_test_failures(result, "pip", root=root))
            current_source.return_value = "source:a"
            for outcome in ("skipped", "failed", "expected-failure"):
                report["tests"][0]["outcome"] = outcome
                path.write_text(json.dumps(report))
                self.assertTrue(required_test_failures(result, "pip", root=root))
            report["tests"] = []
            path.write_text(json.dumps(report))
            self.assertTrue(required_test_failures(result, "pip", root=root))
            report.update(run_id="older-run", tests=[{"test": test_id, "outcome": "passed"}])
            path.write_text(json.dumps(report))
            self.assertTrue(required_test_failures(result, "pip", root=root))
