"""Disposable runtime staging and orchestration checks."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from workbench_blueprints.reviewed_stage import ReviewedStageError, stage_reviewed_feature_plan
from unittest.mock import patch

from test_developer_feature import (
    DEPENDENCY_PATHS,
    ROOT,
    TARGET_PATHS,
    _bytes,
    _checkout,
    _git,
)

from workbench_shell.developer_feature import build_material_fluid_recipe_plan
from workbench_shell.developer_feature_runtime import (
    DeveloperFeatureRuntimeError,
    run_material_fluid_recipe,
    stage_material_fluid_recipe_plan,
    validate_material_fluid_recipe_run,
)
from workbench_shell import developer_feature_runtime as runtime
from workbench_shell.runtime_observe import RuntimeObserveError


class _Authority:
    @staticmethod
    def interpret_material_fluid_recipe_observation(
        _spec: object,
        *,
        groovy_log_bytes: bytes,
        capture: dict,
    ) -> dict:
        if groovy_log_bytes != b"groovy-log" or capture != {"bound": True}:
            raise AssertionError("runtime capture was not transported exactly")
        return {
            "state": "observed",
            "developer_assertions": {
                name: "observed"
                for name in runtime._ASSERTION_MEANINGS
                if name != "fml_client_load"
            },
        }


class DeveloperFeatureRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.state = self.root / "feature-state"
        self.plan = build_material_fluid_recipe_plan(
            ROOT,
            self.checkout,
            name="Runtime Solvent",
            color="0x425d73",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="MIXER",
            input_fluid="steam",
            input_amount=750,
            output_amount=250,
            duration=320,
            voltage_tier="MV",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stage_applies_only_to_a_tracked_disposable_copy(self) -> None:
        source_before = _bytes(self.checkout)
        untracked = self.checkout / "developer-notes.txt"
        untracked.write_text("do not project me\n", encoding="utf-8")
        destination = self.root / "staged"

        result = stage_material_fluid_recipe_plan(ROOT, self.plan, destination)

        self.assertEqual("staged", result["state"])
        self.assertEqual(self.plan["id"], result["plan_id"])
        self.assertEqual("", _git(destination, "status", "--porcelain=v1"))
        self.assertFalse((destination / "developer-notes.txt").exists())
        self.assertEqual(1, result["untracked_excluded"]["file_count"])
        self.assertEqual(source_before, _bytes(self.checkout))
        for operation in self.plan["operations"]:
            self.assertEqual(
                base64.b64decode(operation["after_base64"], validate=True),
                (destination / operation["path"]).read_bytes(),
            )

    def test_generic_stage_can_retain_the_exact_unmodified_baseline(self) -> None:
        source_before = _bytes(self.checkout)
        destination = self.root / "baseline"

        result = stage_reviewed_feature_plan(
            self.plan,
            destination,
            workspace=self.checkout,
            verify=lambda: {"state": "ready", "reason": None},
            apply_operations=False,
            result_format="workbench-test-baseline-stage-v1",
        )

        self.assertEqual("workbench-test-baseline-stage-v1", result["format"])
        self.assertEqual(result["baseline_revision"], result["revision"])
        self.assertEqual("", _git(destination, "status", "--porcelain=v1"))
        self.assertEqual(source_before, _bytes(destination))
        for operation, output in zip(self.plan["operations"], result["outputs"]):
            self.assertEqual(operation["before_sha256"], output["sha256"])
            self.assertEqual(operation["before_size"], output["size"])

    def test_git_commits_preserve_reviewed_crlf_with_autocrlf_input(self) -> None:
        for relative in (*TARGET_PATHS, *DEPENDENCY_PATHS):
            path = self.checkout / relative
            payload = path.read_bytes().replace(b"\n", b"\r\n")
            self.assertIn(b"\r\n", payload)
            path.write_bytes(payload)
        _git(self.checkout, "config", "core.autocrlf", "false")
        _git(self.checkout, "add", "--all")
        _git(self.checkout, "commit", "--quiet", "-m", "retain exact CRLF")
        plan = build_material_fluid_recipe_plan(
            ROOT,
            self.checkout,
            name="Runtime Solvent",
            color="0x425d73",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="MIXER",
            input_fluid="steam",
            input_amount=750,
            output_amount=250,
            duration=320,
            voltage_tier="MV",
        )
        baseline_path = self.root / "crlf-baseline"
        candidate_path = self.root / "crlf-candidate"
        forced_input = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "input",
        }
        with patch.dict(os.environ, forced_input, clear=False):
            baseline = stage_reviewed_feature_plan(
                plan,
                baseline_path,
                workspace=self.checkout,
                verify=lambda: {"state": "ready", "reason": None},
                apply_operations=False,
                result_format="workbench-test-baseline-stage-v1",
            )
            candidate = stage_reviewed_feature_plan(
                plan,
                candidate_path,
                workspace=self.checkout,
                verify=lambda: {"state": "ready", "reason": None},
                apply_operations=True,
                result_format="workbench-test-candidate-stage-v1",
            )

        def blob(root: Path, revision: str, relative: str) -> bytes:
            return subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(root),
                    "cat-file",
                    "blob",
                    f"{revision}:{relative}",
                ],
                timeout=60,
            )

        for root in (baseline_path, candidate_path):
            self.assertEqual(
                b"* -text\n",
                (root / ".git/info/attributes").read_bytes(),
            )
        for operation in plan["operations"]:
            before = base64.b64decode(operation["before_base64"], validate=True)
            after = base64.b64decode(operation["after_base64"], validate=True)
            self.assertIn(b"\r\n", before)
            self.assertIn(b"\r\n", after)
            self.assertEqual(
                before,
                blob(baseline_path, baseline["revision"], operation["path"]),
            )
            self.assertEqual(
                before,
                blob(
                    candidate_path,
                    candidate["baseline_revision"],
                    operation["path"],
                ),
            )
            self.assertEqual(
                after,
                blob(candidate_path, candidate["revision"], operation["path"]),
            )
        for dependency in plan["dependencies"]:
            expected = (self.checkout / dependency["path"]).read_bytes()
            self.assertIn(b"\r\n", expected)
            self.assertEqual(
                expected,
                blob(baseline_path, baseline["revision"], dependency["path"]),
            )
            self.assertEqual(
                expected,
                blob(
                    candidate_path,
                    candidate["baseline_revision"],
                    dependency["path"],
                ),
            )
            self.assertEqual(
                expected,
                blob(candidate_path, candidate["revision"], dependency["path"]),
            )

    def test_stage_rejects_a_stale_dependency_without_creating_output(self) -> None:
        dependency = self.checkout / "groovy/preInit/MaterialChanges.groovy"
        dependency.write_text(
            dependency.read_text(encoding="utf-8").replace(
                "SuSyMaterials.init()", "if (false) SuSyMaterials.init()"
            ),
            encoding="utf-8",
        )
        destination = self.root / "stale-stage"

        with self.assertRaisesRegex(ReviewedStageError, "stale"):
            stage_material_fluid_recipe_plan(ROOT, self.plan, destination)

        self.assertFalse(destination.exists())

    def test_run_orders_stage_runtime_observation_and_retains_success(self) -> None:
        source_before = _bytes(self.checkout)
        calls: list[str] = []

        def runtime_plan(_suite, staged, **kwargs):
            calls.append("plan-runtime")
            self.assertEqual(self.state / "cleanroom-runtime", kwargs["state_root"])
            return {
                "state": "ready",
                "blockers": [],
                "plan_id": "sha256:" + "a" * 64,
                "workspace": {
                    "root_uri": Path(staged).as_uri(),
                    "revision": _git(Path(staged), "rev-parse", "HEAD"),
                    "dirty": False,
                },
            }

        def prepare_probe(_suite, _plan, attempt, destination):
            calls.append("prepare-probe")
            self.assertEqual(attempt, destination)
            overlay = attempt / "overlay.json"
            overlay.write_text("{}\n", encoding="utf-8")
            return object(), overlay, {
                "probe_id": "probe",
                "script_sha256": "b" * 64,
                "script_size": 1,
                "overlay_id": "overlay",
                "overlay_spec_sha256": "c" * 64,
                "projection_target": ".minecraft/groovy/postInit/probe.groovy",
            }

        def observe(_suite, _staged, **kwargs):
            calls.append("observe")
            self.assertEqual(self.state / "cleanroom-runtime", kwargs["state_root"])
            return {"outcome": "completed"}

        base_assertions = {
            name: {"state": "observed", "meaning": meaning}
            for name, meaning in runtime._ASSERTION_MEANINGS.items()
            if name != "recipe_registration"
        }
        with (
            patch.object(
                runtime,
                "material_fluid_runtime_compatibility_policy",
                return_value=((), {"patches": []}),
            ),
            patch.object(runtime, "plan_project_runtime", side_effect=runtime_plan),
            patch.object(runtime, "_prepare_probe", side_effect=prepare_probe),
            patch.object(runtime, "observe_project_runtime", side_effect=observe),
            patch.object(
                runtime,
                "summarize_material_fluid_runtime",
                return_value=({"state": "observed"}, base_assertions),
            ),
            patch.object(
                runtime,
                "captured_material_fluid_groovy_log",
                return_value=(b"groovy-log", {"bound": True}),
            ),
            patch.object(runtime, "_profile_authority", return_value=_Authority),
            patch.object(
                runtime,
                "material_fluid_runtime_capture_completed",
                return_value=True,
            ),
        ):
            receipt = run_material_fluid_recipe(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=self.root / "launcher",
            )

        self.assertEqual(["plan-runtime", "prepare-probe", "observe"], calls)
        self.assertEqual("complete", receipt["state"])
        self.assertEqual("runtime-completed", receipt["outcome"])
        self.assertTrue(all(
            row["state"] == "observed" for row in receipt["assertions"].values()
        ))
        self.assertEqual(
            receipt,
            validate_material_fluid_recipe_run(receipt, self.plan),
        )
        self.assertEqual(source_before, _bytes(self.checkout))
        attempt = Path(receipt["target"]["attempt_root_uri"].removeprefix("file://"))
        self.assertTrue((attempt / "workspace/.git").is_dir())
        self.assertTrue((attempt / "receipt.json").is_file())

    def test_runtime_failure_retains_an_incomplete_attempt(self) -> None:
        def runtime_plan(_suite, staged, **_kwargs):
            return {
                "state": "ready",
                "blockers": [],
                "plan_id": "sha256:" + "a" * 64,
                "workspace": {
                    "root_uri": Path(staged).as_uri(),
                    "revision": _git(Path(staged), "rev-parse", "HEAD"),
                    "dirty": False,
                },
            }

        with (
            patch.object(
                runtime,
                "material_fluid_runtime_compatibility_policy",
                return_value=((), {"patches": []}),
            ),
            patch.object(runtime, "plan_project_runtime", side_effect=runtime_plan),
            patch.object(
                runtime,
                "_prepare_probe",
                side_effect=lambda _suite, _plan, root, _destination: (
                    object(),
                    root / "missing-overlay.json",
                    {"probe_id": "probe"},
                ),
            ),
            patch.object(
                runtime,
                "observe_project_runtime",
                side_effect=RuntimeObserveError("client stopped before checkpoint"),
            ),
        ):
            receipt = run_material_fluid_recipe(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=self.root / "launcher",
            )

        self.assertEqual("incomplete", receipt["state"])
        self.assertEqual("failed", receipt["outcome"])
        self.assertEqual(
            "RuntimeObserveError",
            receipt["runtime"]["error"]["kind"],
        )
        self.assertEqual(
            receipt,
            validate_material_fluid_recipe_run(receipt, self.plan),
        )
        receipt_path = Path(
            receipt["target"]["receipt_uri"].removeprefix("file://")
        )
        self.assertTrue(receipt_path.is_file())


if __name__ == "__main__":
    unittest.main()
