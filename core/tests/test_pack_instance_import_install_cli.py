"""A complete Prism ZIP uses its own launcher platform during installation."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workbench_core import pack_instance_cli as instance_cli


ROOT = Path(__file__).resolve().parents[2]
POLICY = (ROOT / "profiles/packs/supersymmetry/runtime"
          / "release-client-install-policy-v1.json")


class ImportedPrismInstallCliTests(unittest.TestCase):
    def test_zip_resource_lookup_does_not_require_cleanroom_toolchain(self) -> None:
        def resources(role: str) -> dict:
            if role != "release-client-install-policy":
                raise AssertionError("ZIP installation looked up the Cleanroom bootstrap")
            return {"supersymmetry": POLICY}

        with patch.object(instance_cli, "profile_resources", side_effect=resources):
            policy_path, bootstrap = instance_cli._install_resources(
                require_bootstrap=False)
        self.assertEqual(POLICY, policy_path)
        self.assertIsNone(bootstrap)

    def test_zip_install_prepare_skips_bootstrap_fetch(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".workbench") as temporary:
            root = Path(temporary)
            state, config, launcher = (root / name for name in (
                "state", "config", "Prism"))
            choice = {"source_kind": "user-prism-zip", "source_plan_id": "source-plan"}
            source = {"source_kind": "user-prism-zip", "plan_id": "source-plan"}
            selection = Mock(profile_configuration=None, java_home=None,
                             managed_java_feature=None)
            java = {"source": "synthetic"}
            plan = {"plan_id": "install-plan", "state": "ready",
                    "source_platform": {"kind": "forge",
                                        "component_version": "14.23.5.2860"}}
            with (patch.object(instance_cli, "_selected_install_context",
                               return_value=(choice, source, launcher, state, selection)),
                  patch.object(instance_cli, "_install_resources",
                               return_value=(POLICY, None)) as resources,
                  patch.object(instance_cli, "CoreManagedJava") as managed_java,
                  patch("workbench_core.runtime_java.load_java_runtime_policy",
                        return_value={"feature_version": 8}),
                  patch("workbench_core.pack_release_client_install.plan_prism_data_root",
                        return_value={"action": "reuse", "state": "ready"}),
                  patch("workbench_core.tooling_provision.prepare_prism_launcher"),
                  patch("workbench_core.pack_release_client_install.plan_release_client_install",
                        return_value=plan) as install_plan,
                  patch.object(instance_cli, "fetch_verified_artifact") as fetch):
                managed_java.return_value.ensure.return_value = java
                result = instance_cli._install(
                    "install-prepare", state_root=state, config_home=config,
                    suite_root=ROOT, expected_plan_id=None,
                )
            resources.assert_called_once_with(require_bootstrap=False)
            fetch.assert_not_called()
            self.assertEqual(plan, result["installation"])
            install_plan.assert_called_once()
            self.assertIs(source, install_plan.call_args.args[0])

    def test_implicit_cleanroom_java25_blocks_imported_forge_before_acquisition(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".workbench") as temporary:
            root = Path(temporary)
            state, config, launcher = (root / name for name in (
                "state", "config", "Prism"))
            choice = {"source_kind": "user-prism-zip", "source_plan_id": "source-plan"}
            source = {"source_platform": {"kind": "forge"}}
            selection = Mock(profile_configuration=None, java_home=None,
                             managed_java_feature=None)
            with (patch.object(instance_cli, "_selected_install_context",
                               return_value=(choice, source, launcher, state, selection)),
                  patch("workbench_core.runtime_java.load_java_runtime_policy",
                        return_value={"feature_version": 25}),
                  patch.object(instance_cli, "CoreManagedJava") as managed_java,
                  patch("workbench_core.tooling_provision.prepare_prism_launcher") as prism,
                  patch.object(instance_cli, "fetch_verified_artifact") as fetch):
                with self.assertRaisesRegex(ValueError, "managed Java 8 or a custom Java path"):
                    instance_cli._install(
                        "install-prepare", state_root=state, config_home=config,
                        suite_root=ROOT, expected_plan_id=None,
                    )
            managed_java.assert_not_called()
            prism.assert_not_called()
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
