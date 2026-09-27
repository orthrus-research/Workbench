"""CLI handoff from one user ZIP to a recoverable Core source choice."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import pack_instance_cli as cli
from workbench_core import pack_release_client_install as install
from workbench_core.pack_instance_cli import main


ROOT = Path(__file__).resolve().parents[2]


class PackInstanceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config.mkdir(mode=0o700)
        self.archive = self.root / "instance.zip"
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("instance.cfg", b"[General]\nname=Imported\n")
            archive.writestr("mmc-pack.json", json.dumps({"components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "0.6.8-alpha"},
            ]}).encode())
            archive.writestr("minecraft/mods/Susy-Core.jar", b"complete-instance-mod")
        environment = patch.dict(os.environ, {
            "WORKBENCH_CONFIG_HOME": str(self.config),
            "WORKBENCH_STATE_ROOT": str(self.state),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def _call(self, *arguments: str) -> dict:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main([*arguments, "--profile", "supersymmetry", "--json"]))
        return json.loads(output.getvalue())

    def _select_source(self) -> dict:
        initial = self._call("choice-show")
        plan = self._call("zip-plan", "--archive", str(self.archive))["source"]
        imported = self._call(
            "zip-import", "--archive", str(self.archive),
            "--expected-plan-id", plan["plan_id"],
        )["source"]
        self._call(
            "choice-select", "--source-plan-id", imported["plan_id"],
            "--launcher-root", str(self.root / "Prism"),
            "--workspace-name", "dev", "--expected-record-id",
            initial["choice"]["record_id"],
        )
        return imported

    def test_review_import_select_and_reopen_without_original_zip(self) -> None:
        initial = self._call("choice-show")
        self.assertEqual("none", initial["source_state"])
        plan = self._call("zip-plan", "--archive", str(self.archive))["source"]
        imported = self._call(
            "zip-import", "--archive", str(self.archive),
            "--expected-plan-id", plan["plan_id"],
        )["source"]
        self.assertEqual(plan["plan_id"], imported["plan_id"])
        choice = self._call(
            "choice-select", "--source-plan-id", imported["plan_id"],
            "--launcher-root", str(self.root / "Prism"),
            "--workspace-name", "dev", "--expected-record-id",
            initial["choice"]["record_id"],
        )["choice"]
        self.archive.unlink()
        reopened = self._call("choice-show")
        self.assertEqual("retained", reopened["source_state"])
        self.assertEqual(choice, reopened["choice"])
        self.assertEqual(imported["tree_id"], self._call(
            "zip-reopen", "--source-plan-id", plan["plan_id"],
        )["source"]["tree_id"])

    def test_prism_root_review_and_explicit_reconcile(self) -> None:
        self._select_source()
        launcher = self.root / "Prism"
        plan = self._call("root-plan")["prism_root"]
        self.assertEqual("initialize", plan["action"])
        calls = 0

        def cancel() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            install.initialize_prism_data_root(
                launcher, state_root=self.state,
                expected_plan_id=plan["plan_id"], check_cancelled=cancel,
            )
        pending = self._call("root-plan")["prism_root"]
        self.assertEqual("reconcile", pending["action"])
        self.assertEqual("blocked", pending["state"])
        with self.assertRaisesRegex(ValueError, "no interrupted initialization"):
            self._call("root-reconcile", "--expected-root-plan-id",
                       "workbench-prism-data-root-plan:sha256:" + "0" * 64)
        recovered = self._call(
            "root-reconcile", "--expected-root-plan-id", pending["plan_id"],
        )["prism_root"]
        self.assertEqual("reconciled", recovered["outcome"])
        self.assertTrue((launcher / "instances").is_dir())
        self.assertEqual("reuse", self._call("root-plan")["prism_root"]["action"])

    def test_prism_root_abandon_retains_incomplete_stage(self) -> None:
        self._select_source()
        launcher = self.root / "Prism"
        plan = self._call("root-plan")["prism_root"]
        original = install._write_file

        def interrupt(path: Path, data: bytes) -> None:
            if path.name == "prismlauncher.cfg":
                raise RuntimeError("interrupted")
            original(path, data)

        with patch.object(install, "_write_file", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                install.initialize_prism_data_root(
                    launcher, state_root=self.state,
                    expected_plan_id=plan["plan_id"],
                )
        pending = self._call("root-plan")["prism_root"]
        self.assertEqual("reconcile", pending["action"])
        retained = self._call(
            "root-abandon", "--expected-root-plan-id", pending["plan_id"],
        )["prism_root"]
        self.assertEqual("abandoned", retained["outcome"])
        self.assertTrue(Path(retained["retained_stage_path"]).is_dir())
        self.assertEqual("initialize", self._call("root-plan")["prism_root"]["action"])

    def test_install_abandon_routes_exact_plan_without_acquiring_tools(self) -> None:
        plan_id = "workbench-pack-release-client-install-plan:sha256:" + "a" * 64
        choice = {"source_plan_id": "workbench-pack-release-client-composition-plan:sha256:"
                  + "b" * 64}
        context = (choice, {"tree_id": "source"}, self.root / "Prism", self.state,
                   SimpleNamespace(profile_configuration=None))
        operation = {"plan_id": plan_id, "outcome": "abandoned",
                     "retained_stage_path": str(self.root / "retained")}
        with (patch.object(cli, "_selected_install_context", return_value=context),
              patch.object(cli, "_install_resources", return_value=(self.root / "policy.json", {})),
              patch.object(cli.CoreManagedJava, "ensure", return_value={"source": "user-path"}),
              patch.object(install, "abandon_interrupted_release_client_install",
                           return_value=operation) as abandon,
              patch("workbench_core.tooling_provision.prepare_prism_launcher",
                    side_effect=AssertionError("recovery must not acquire launcher"))):
            result = self._call("install-abandon", "--expected-install-plan-id", plan_id)
        self.assertEqual(operation, result["installation"])
        self.assertEqual(plan_id, abandon.call_args.kwargs["expected_plan_id"])

    def test_install_prepare_requires_explicit_root_recovery_choice(self) -> None:
        launcher = self.root / "Prism"
        plan = install.plan_prism_data_root(launcher, state_root=self.state)
        calls = 0

        def cancel() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            install.initialize_prism_data_root(
                launcher, state_root=self.state,
                expected_plan_id=plan["plan_id"], check_cancelled=cancel,
            )
        choice = {"source_plan_id": "workbench-pack-release-client-composition-plan:sha256:"
                  + "b" * 64}
        context = (choice, {"tree_id": "source"}, launcher, self.state,
                   SimpleNamespace(profile_configuration=None))
        with (patch.object(cli, "_selected_install_context", return_value=context),
              patch.object(cli, "_install_resources", return_value=(self.root / "policy.json", {})),
              patch("workbench_core.tooling_provision.prepare_prism_launcher",
                    side_effect=AssertionError("root recovery must be selected"))):
            with self.assertRaisesRegex(ValueError, "needs a recovery choice"):
                self._call("install-prepare")
        self.assertFalse(launcher.exists())

    def test_root_recovery_requires_exact_plan_argument(self) -> None:
        output = StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as failure:
            main(["root-abandon", "--profile", "supersymmetry", "--json"])
        self.assertEqual(2, failure.exception.code)
        self.assertIn("--expected-root-plan-id", output.getvalue())


if __name__ == "__main__":
    unittest.main()
