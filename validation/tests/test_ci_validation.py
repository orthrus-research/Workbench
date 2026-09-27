"""CI selection must never turn missing required coverage into a green gate."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation"))
from ci_validation import REQUIRED_TESTS, STAGES, gate, plan, required_test_failures
from ci_validation import source_ci_collection_failures
from ci_validation import main as ci_main
from core_run_custody import (
    SOURCE_CI_COLLECTION_FILES, _source_core, publish_ci_plan,
    publish_source_ci_collection, read_source_ci_collection,
)
from suite_measurement import inventory_digest


class CiValidationTests(unittest.TestCase):
    @staticmethod
    def _collection_bytes(suite: str, ids: list[str]) -> bytes:
        document = {
            "format": "workbench-python-test-collection-v1",
            "suite": suite,
            "state": "not-run",
            "reason": "Collected for inventory only; no tests executed.",
            "test_ids": ids,
            "inventory_digest": inventory_digest(ids),
        }
        return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()

    def test_source_ci_collections_keep_exact_v1_paths_and_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            for suite, filename in SOURCE_CI_COLLECTION_FILES.items():
                target = root / ".workbench/validation" / filename
                payload = self._collection_bytes(suite, [f"{suite}.Fixture.test_one"])
                self.assertEqual(target, publish_source_ci_collection(
                    root, suite, payload, selected_path=target,
                    configuration_home=home,
                ))
                self.assertEqual(payload, target.read_bytes())
                self.assertEqual(payload, read_source_ci_collection(
                    root, suite, selected_path=target,
                    configuration_home=home,
                ))
                if os.name != "nt":
                    self.assertEqual(0o600, target.stat().st_mode & 0o777)
                    self.assertEqual(0o700, target.parent.stat().st_mode & 0o777)
            self.assertEqual([], source_ci_collection_failures(
                root=root, configuration_home=home,
            ))
            with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(home)}), \
                    patch("ci_validation.ROOT", root), redirect_stdout(io.StringIO()):
                self.assertEqual(0, ci_main(["assert-collections"]))
            from workbench_core.storage.registered import ResourceCatalog
            rows = ResourceCatalog(home).inventory(workspace=root)["record_stores"]
            self.assertEqual(["validation-ci-collections-v1"], [row["family"] for row in rows])

    def test_source_ci_collection_refuses_foreign_target_existing_file_and_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            suite, filename = next(iter(SOURCE_CI_COLLECTION_FILES.items()))
            target = root / ".workbench/validation" / filename
            payload = self._collection_bytes(suite, ["fixture.Test.test_one"])
            with self.assertRaisesRegex(ValueError, "historical path"):
                publish_source_ci_collection(
                    root, suite, payload, selected_path=root / "other.json",
                    configuration_home=home,
                )
            self.assertFalse((root / ".workbench").exists())
            with self.assertRaises(FileNotFoundError):
                read_source_ci_collection(root, suite, selected_path=target,
                                          configuration_home=home)
            self.assertFalse((root / ".workbench").exists())
            self.assertFalse(home.exists())
            target.parent.mkdir(parents=True)
            stage = target.parent / f".{filename}.interrupted"
            stage.write_bytes(payload)
            with self.assertRaisesRegex(OSError, "interrupted source-CI collection stage"):
                publish_source_ci_collection(
                    root, suite, payload, selected_path=target,
                    configuration_home=home,
                )
            self.assertFalse(target.exists())
            self.assertEqual(payload, stage.read_bytes())
            stage.unlink()
            publish_source_ci_collection(root, suite, payload, selected_path=target,
                                         configuration_home=home)
            with self.assertRaisesRegex(OSError, "already exists"):
                publish_source_ci_collection(root, suite, payload, selected_path=target,
                                             configuration_home=home)
            self.assertEqual(payload, target.read_bytes())

    @unittest.skipIf(os.name == "nt", "symbolic links may require Windows developer privileges")
    def test_source_ci_collection_rejects_redirected_parent_without_touching_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            suite, filename = next(iter(SOURCE_CI_COLLECTION_FILES.items()))
            validation = root / ".workbench/validation"
            validation.parent.mkdir()
            outside = Path(temporary) / "outside"
            outside.mkdir()
            validation.symlink_to(outside, target_is_directory=True)
            target = validation / filename
            payload = self._collection_bytes(suite, ["fixture.Test.test_one"])
            with self.assertRaises((OSError, ValueError)):
                publish_source_ci_collection(root, suite, payload, selected_path=target,
                                             configuration_home=home)
            self.assertEqual([], list(outside.iterdir()))
            self.assertFalse(home.exists())

    def test_historical_nonprivate_collection_remains_readable_without_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            suite, filename = next(iter(SOURCE_CI_COLLECTION_FILES.items()))
            target = root / ".workbench/validation" / filename
            target.parent.mkdir(parents=True)
            payload = self._collection_bytes(suite, ["fixture.Test.test_one"])
            target.write_bytes(payload)
            if os.name != "nt":
                target.parent.chmod(0o755)
                target.chmod(0o644)
            self.assertEqual(payload, read_source_ci_collection(
                root, suite, selected_path=target, configuration_home=home,
            ))
            self.assertEqual(payload, target.read_bytes())
            if os.name != "nt":
                self.assertEqual(0o644, target.stat().st_mode & 0o777)
                self.assertEqual(0o700, target.parent.stat().st_mode & 0o777)

    def test_source_ci_collection_retains_post_link_uncertainty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            suite, filename = next(iter(SOURCE_CI_COLLECTION_FILES.items()))
            target = root / ".workbench/validation" / filename
            payload = self._collection_bytes(suite, ["fixture.Test.test_one"])
            _source_core()
            from workbench_core import durable_records
            original_flush = durable_records.fsync_directory

            def fail_after_link(path):
                if path == target.parent and target.exists():
                    raise OSError("forced flush failure")
                return original_flush(path)

            with patch.object(durable_records, "fsync_directory", side_effect=fail_after_link):
                with self.assertRaisesRegex(OSError, "forced flush failure"):
                    publish_source_ci_collection(root, suite, payload, selected_path=target,
                                                 configuration_home=home)
            stages = list(target.parent.glob(f".{filename}.*.tmp"))
            self.assertEqual(1, len(stages))
            self.assertEqual(payload, stages[0].read_bytes())
            self.assertEqual(payload, target.read_bytes())
            self.assertTrue(source_ci_collection_failures(root=root, configuration_home=home))
            with self.assertRaisesRegex(OSError, "interrupted source-CI collection stage"):
                publish_source_ci_collection(root, suite, payload, selected_path=target,
                                             configuration_home=home)
            self.assertEqual(payload, stages[0].read_bytes())

    def test_source_ci_collection_admission_rejects_changed_or_malformed_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            targets = {}
            for suite, filename in SOURCE_CI_COLLECTION_FILES.items():
                target = root / ".workbench/validation" / filename
                publish_source_ci_collection(
                    root, suite, self._collection_bytes(suite, ["fixture.Test.test_one"]),
                    selected_path=target, configuration_home=home,
                )
                targets[suite] = target
            self.assertEqual([], source_ci_collection_failures(root=root, configuration_home=home))
            first = targets[next(iter(targets))]
            before = first.read_bytes()
            for changed in (
                {**json.loads(before), "state": "passed"},
                {**json.loads(before), "inventory_digest": "sha256:" + "0" * 64},
                {**json.loads(before), "test_ids": ["fixture.Test.test_one", "fixture.Test.test_one"]},
            ):
                first.write_bytes((json.dumps(changed) + "\n").encode())
                self.assertTrue(source_ci_collection_failures(root=root, configuration_home=home))
            first.write_bytes(json.dumps(json.loads(before), sort_keys=True).encode() + b"\n")
            self.assertTrue(source_ci_collection_failures(root=root, configuration_home=home))
            first.write_bytes(before)
            self.assertEqual([], source_ci_collection_failures(root=root, configuration_home=home))
            other = Path(temporary) / "foreign.json"
            other.write_bytes(before)
            first.unlink()
            if os.name == "nt":
                return
            first.symlink_to(other)
            self.assertTrue(source_ci_collection_failures(root=root, configuration_home=home))
            self.assertEqual(before, other.read_bytes())

    def selected(self, event="pull_request", paths=None):
        return plan(event, paths if paths is not None else ["modules/crucible/src/graph.py"], revision="a" * 40)

    def outcomes(self, document):
        return {"plan": {"result": "success"}, **{
            row["name"]: {"result": "success" if row["required"] else "skipped"}
            for row in document["stages"]}}

    def test_ci_plan_uses_core_namespace_and_preserves_exact_legacy_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            target = root / ".workbench/validation/ci/plan.json"
            payload = (json.dumps(self.selected("workflow_dispatch", []), indent=2) + "\n").encode()
            with self.assertRaisesRegex(ValueError, "historical path"):
                publish_ci_plan(root, root / "elsewhere.json", payload, configuration_home=home)
            self.assertFalse((root / ".workbench").exists())
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            os.chmod(target, 0o644)
            self.assertEqual(target, publish_ci_plan(
                root, target, payload, configuration_home=home,
            ))
            self.assertEqual(payload, target.read_bytes())
            self.assertEqual(target, publish_ci_plan(
                root, target, payload, configuration_home=home,
            ))
            self.assertEqual(0, target.stat().st_mode & 0o077)
            from workbench_core.storage.registered import ResourceCatalog
            rows = ResourceCatalog(home).inventory(workspace=root)["record_stores"]
            self.assertEqual(["validation-ci-plan-v1"], [row["family"] for row in rows])
            with self.assertRaisesRegex(ValueError, "different CI plan"):
                publish_ci_plan(root, target, b"different\n", configuration_home=home)
            self.assertEqual(payload, target.read_bytes())
            os.link(target, root / "linked-plan.json")
            with self.assertRaisesRegex(ValueError, "independent regular file"):
                publish_ci_plan(root, target, payload, configuration_home=home)
            self.assertEqual(payload, target.read_bytes())

    def test_ci_plan_refuses_redirected_target_and_interrupted_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            target = root / ".workbench/validation/ci/plan.json"
            target.parent.mkdir(parents=True)
            outside = Path(temporary) / "outside.json"
            outside.write_bytes(b"unrelated\n")
            target.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "independent regular file"):
                publish_ci_plan(root, target, b"planned\n", configuration_home=home)
            self.assertEqual(b"unrelated\n", outside.read_bytes())
            target.unlink()
            orphan = target.parent / ".plan.json.unknown"
            orphan.write_bytes(b"unknown\n")
            with self.assertRaisesRegex(ValueError, "interrupted CI plan stage"):
                publish_ci_plan(root, target, b"planned\n", configuration_home=home)
            self.assertEqual(b"unknown\n", orphan.read_bytes())
            self.assertFalse(target.exists())

    def test_ci_plan_command_publishes_before_github_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            root.mkdir()
            home = Path(temporary) / "config"
            event = root / "event.json"
            event.write_text("{}", encoding="utf-8")
            target = root / ".workbench/validation/ci/plan.json"
            github_output = root / "github-output"
            with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(home)}), \
                    patch("ci_validation.ROOT", root), \
                    patch("ci_validation.subprocess.check_output", return_value="a" * 40 + "\n"):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(0, ci_main([
                        "plan", "--event-name", "workflow_dispatch",
                        "--event-file", str(event), "--output", str(target),
                        "--github-output", str(github_output),
                    ]))
            expected = plan("workflow_dispatch", [], revision="a" * 40)
            self.assertEqual(expected, json.loads(target.read_bytes()))
            self.assertEqual(
                (json.dumps(expected, indent=2) + "\n").encode(), target.read_bytes(),
            )
            self.assertIn("plan=", github_output.read_text(encoding="utf-8"))
            from workbench_core.storage.registered import ResourceCatalog
            self.assertEqual(
                ["validation-ci-plan-v1"],
                [row["family"] for row in ResourceCatalog(home).inventory(
                    workspace=root,
                )["record_stores"]],
            )

    def test_every_source_or_unknown_change_requires_complete_stages(self):
        for path in ("api/src/workbench_api/canonical.py", "modules/workbench-shell/src/main.py",
                     "clients/vscode/package-lock.json", "new-owner/something", "docs/contract.schema.json"):
            with self.subTest(path=path):
                document = self.selected(paths=[path])
                self.assertEqual(set(STAGES) - {"physical-cleanroom"}, {row["name"] for row in document["stages"] if row["required"]})
                self.assertEqual([], gate(document, self.outcomes(document)))

    def test_only_known_documentation_prs_omit_ide(self):
        document = self.selected(paths=["README.md", "docs/guide.md"])
        self.assertEqual(["validation-native-fixtures", "blueprints-native-fixtures"], [row["name"] for row in document["excluded_suites"]])
        self.assertEqual(["not-run", "not-run"], [row["state"] for row in document["excluded_suites"]])
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
        self.assertEqual({"workflow_dispatch"}, set(workflow["on"]))
        jobs = workflow["jobs"]
        self.assertEqual(set(STAGES) | {"plan"}, set(jobs["required-validation"]["needs"]))
        self.assertEqual("always()", jobs["required-validation"]["if"])
        self.assertEqual("ubuntu-24.04", jobs["native-packages"]["runs-on"])
        source = "\n".join(step.get("run", "") for step in jobs["source-ci"]["steps"])
        self.assertIn("--node-only", source)
        self.assertIn("--tier source-ci", source)
        self.assertIn("validation-native-fixtures --collect-only --report", source)
        self.assertIn("blueprints-native-fixtures --collect-only --report", source)
        collections = [step for step in jobs["source-ci"]["steps"] if "--core-ci-collection" in step.get("run", "")]
        self.assertEqual(2, len(collections))
        admission = next(step for step in jobs["source-ci"]["steps"] if "assert-collections" in step.get("run", ""))
        self.assertLess(jobs["source-ci"]["steps"].index(collections[-1]), jobs["source-ci"]["steps"].index(admission))
        validation = next(step for step in jobs["source-ci"]["steps"] if "--tier source-ci" in step.get("run", ""))
        self.assertLess(jobs["source-ci"]["steps"].index(admission), jobs["source-ci"]["steps"].index(validation))
        self.assertIn("axiom_runtime.py --provision-java --github-env-file", source)
        workbench = "\n".join(step.get("run", "") for step in jobs["workbench"]["steps"])
        self.assertIn("axiom_runtime.py --provision-java --github-env-file", workbench)
        portability = yaml.load((ROOT / ".github/workflows/portability.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual({"workflow_dispatch"}, set(portability["on"]))
        self.assertEqual({"windows-2025", "macos-15"}, set(portability["jobs"]["native-portability"]["strategy"]["matrix"]["os"]))
        candidate = yaml.load((ROOT / ".github/workflows/component-release.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual({"workflow_dispatch"}, set(candidate["on"]))
        self.assertEqual("startsWith(github.ref, 'refs/tags/workbench-')", candidate["jobs"]["identify"]["if"])
        native = "\n".join(step.get("run", "") for step in jobs["native-packages"]["steps"])
        self.assertIn("--from-wheelhouse", native)
        self.assertIn("--wheelhouse .workbench/native-suite", native)
        self.assertIn("tools/install_workbench.py", native)
        ide = "\n".join(step.get("run", "") for step in jobs["ide"]["steps"])
        self.assertIn("xvfb-run", ide)
        self.assertIn("--full --installed-core", ide)
        axiom = "\n".join(step.get("run", "") for step in jobs["axiom"]["steps"])
        self.assertIn("axiom_runtime.py --provision-java --github-env-file", axiom)
        for tool in ("build_axiom.py", "axiom_sources.py", "build_axiom_target.py", "axiom_target_smoke.py", "axiom_source_conformance.py", "axiom_loader_conformance.py", "axiom_composition_conformance.py"):
            self.assertIn(tool, axiom)

    def test_python_client_candidate_uses_the_native_wheel_job(self):
        candidate = yaml.load(
            (ROOT / ".github/workflows/component-release.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        python_job = candidate["jobs"]["python"]
        self.assertEqual(
            "needs.identify.outputs.kind == 'python' || needs.identify.outputs.kind == 'python-client'",
            python_job["if"],
        )
        commands = "\n".join(step.get("run", "") for step in python_job["steps"])
        self.assertIn('tools/build_native_distribution.py --component "$COMPONENT"', commands)
        self.assertIn("tools/install_workbench.py", commands)
        self.assertIn('"$RUNNER_TEMP/workbench-candidate/bin/python" -m unittest discover -s clients/tui/tests -v', commands)
        tui_test = next(
            step for step in python_job["steps"]
            if step.get("name") == "Exercise the installed terminal client"
        )
        self.assertEqual("needs.identify.outputs.component == 'workbench-tui'", tui_test["if"])
        self.assertIn('tools/publish_component_candidate.py --component "$COMPONENT"', commands)
        self.assertIn('--source-directory .workbench/candidate-wheelhouse/wheels --github-output "$GITHUB_OUTPUT"', commands)
        candidate_step = next(step for step in python_job["steps"] if step.get("id") == "candidate")
        upload = next(step for step in python_job["steps"] if "actions/upload-artifact@" in step.get("uses", "")
                      and step.get("with", {}).get("name") == "${{ needs.identify.outputs.component }}")
        self.assertIn("tools/validate_native_artifacts.py", candidate_step["run"])
        self.assertEqual("${{ steps.candidate.outputs.path }}/", upload["with"]["path"])
        client_job = candidate["jobs"]["client"]
        client_step = next(step for step in client_job["steps"] if step.get("id") == "candidate")
        self.assertIn("tools/publish_component_candidate.py", client_step["run"])
        client_upload = next(step for step in client_job["steps"] if "actions/upload-artifact@" in step.get("uses", ""))
        self.assertEqual("${{ steps.candidate.outputs.path }}/", client_upload["with"]["path"])
        jvm = candidate["jobs"]["jvm"]
        self.assertIn("tools/build_axiom.py --provision --output .workbench/candidate",
                      "\n".join(step.get("run", "") for step in jvm["steps"]))

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
