"""Core Java acquisition receives the resolved choice and managed location."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import EnvironmentSelection
from workbench_core.managed_java import CoreManagedJava


def selection(java_home: Path | None, feature: int | None = None) -> EnvironmentSelection:
    return EnvironmentSelection(
        resolution_id="resolved", workspace_id="workbench-workspace-v1:" + "a" * 32,
        workspace_name="selected", workspace_source="user-workspaces",
        profile_configuration=None, profile_source="none",
        java_home=java_home, java_source="user-workspaces-v2" if java_home else "none",
        git_executable=None, managed_java_feature=feature,
    )


class ManagedJavaTests(unittest.TestCase):
    def test_selected_path_bypasses_inventory_and_policy_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            candidate = root / "jdk-25"
            service = CoreManagedJava(state_root=state, selection=selection(candidate))
            with patch("workbench_core.managed_java.ensure_java_runtime") as acquire:
                result = service.ensure(root, config_path=root / "profile.toml")
            acquire.assert_not_called()
            self.assertEqual("user-path", result["source"])
            self.assertEqual("unverified", result["runtime"]["state"])
            self.assertEqual(candidate.as_uri(), result["runtime"]["java_home_uri"])
            self.assertFalse(candidate.exists())
            self.assertFalse(state.exists())

    def test_no_choice_uses_profile_managed_default_without_ambient_java(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CoreManagedJava(state_root=root / "state", selection=selection(None))
            with patch("workbench_core.managed_java.ensure_java_runtime", return_value={"outcome": "reused"}) as acquire:
                service.ensure(root, config_path=Path("workbench.toml"))
            self.assertEqual((), acquire.call_args.kwargs["candidates"])
            self.assertIsNone(acquire.call_args.kwargs["managed_feature_version"])

    def test_explicit_managed_java_8_uses_core_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            service = CoreManagedJava(state_root=state, selection=selection(None, 8))
            with patch("workbench_core.managed_java.ensure_java_runtime", return_value={"outcome": "reused"}) as acquire:
                service.ensure(root, config_path=root / "workbench.toml")
            self.assertEqual(8, acquire.call_args.kwargs["managed_feature_version"])
            self.assertEqual(state, acquire.call_args.kwargs["state_root"])
            self.assertEqual((), acquire.call_args.kwargs["candidates"])

    def test_user_path_is_probed_only_for_an_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "unlisted-jdk"
            service = CoreManagedJava(state_root=root / "state", selection=selection(candidate))
            with patch("workbench_core.managed_java.host_platform", return_value={"os": "linux"}), patch(
                "workbench_core.managed_java.probe_java", return_value={"runtime_version": "1.8.0_504-b01"}
            ) as probe, patch("workbench_core.managed_java.ensure_java_runtime") as acquire:
                result = service.for_execution(root, config_path=root / "profile.toml")
            acquire.assert_not_called()
            probe.assert_called_once_with(candidate / "bin/java")
            self.assertEqual("user-path", result["source"])
            self.assertEqual("observed", result["runtime"]["state"])
            self.assertEqual("1.8.0_504-b01", result["runtime"]["probe"]["runtime_version"])


if __name__ == "__main__":
    unittest.main()
