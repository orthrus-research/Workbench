"""The Shell runtime command forwards Core's selected Java authority."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workbench_api import EnvironmentSelection, ExecutionContext
from workbench_shell import commands
import workbench_shell.cli
from workbench_shell import runtime_launch, runtime_materialize


class ManagedJavaCommandTests(unittest.TestCase):
    def test_saved_profile_and_core_service_reach_the_runtime_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = Mock()
            context = ExecutionContext(
                root, root / "state", managed_java=service,
                selection=EnvironmentSelection(
                    resolution_id="snapshot", workspace_id=None,
                    workspace_name=None, workspace_source="argument",
                    profile_configuration=root / "selected.toml",
                    profile_source="user-workspaces-v2", java_home=root / "jdk-25",
                    java_source="user-workspaces-v2", git_executable=None,
                ),
            )
            with patch("workbench_shell.cli.main", return_value=0) as main:
                self.assertEqual(0, commands.runtime_java(["--json"], context=context))
            self.assertEqual(
                ["runtime-java", "--json", "--config", str(root / "selected.toml")],
                main.call_args.args[0],
            )
            self.assertIs(service, main.call_args.kwargs["runtime_java_service"])
            with patch("workbench_shell.cli.main", return_value=0) as main:
                commands.runtime_java(["--config", str(root / "explicit.toml")], context=context)
            self.assertEqual(
                ["runtime-java", "--config", str(root / "explicit.toml")],
                main.call_args.args[0],
            )

    def test_materialize_and_launch_use_core_java_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = Mock()
            context = ExecutionContext(
                root, root / "state", managed_java=service,
                selection=EnvironmentSelection(
                    resolution_id="snapshot", workspace_id=None, workspace_name=None,
                    workspace_source="argument", profile_configuration=root / "selected.toml",
                    profile_source="user-workspaces-v3", java_home=None,
                    java_source="none", git_executable=None, managed_java_feature=8,
                ),
            )
            with patch("workbench_shell.cli.main", return_value=0) as main:
                self.assertEqual(0, commands.runtime_materialize([str(root / "project")], context=context))
            self.assertEqual(
                ["runtime-materialize", str(root / "project"), "--config", str(root / "selected.toml")],
                main.call_args.args[0],
            )
            self.assertIs(service, main.call_args.kwargs["runtime_java_service"])
            self.assertEqual(root / "state", main.call_args.kwargs["runtime_state_root"])
            with patch("workbench_shell.launcher_setup.launcher_defaults_for_runtime", side_effect=lambda args: args), patch(
                "workbench_shell.cli.main", return_value=0
            ) as main:
                self.assertEqual(0, commands.runtime_launch([str(root / "project")], context=context))
            self.assertEqual(
                ["runtime-launch", str(root / "project"), "--config", str(root / "selected.toml")],
                main.call_args.args[0],
            )
            self.assertIs(service, main.call_args.kwargs["runtime_java_service"])

    def test_execution_consumers_record_user_path_without_profile_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            java = root / "jdk/bin/java"
            java.parent.mkdir(parents=True)
            java.write_bytes(b"synthetic executable")
            host = {"os": "linux", "architecture": "x64", "system": "Linux", "machine": "x86_64"}
            probe = {"runtime_version": "1.8.0_504-b01", "vendor": "Example", "java_home": str(root / "jdk")}
            result = {
                "format": "workbench-java-runtime-result-v3", "schema_version": 3,
                "source": "user-path", "outcome": "observed", "host": host, "runtime": {
                    "java_uri": java.as_uri(), "probe": probe,
                },
            }
            path, identity = runtime_materialize._selected_java(result)
            self.assertEqual(java, path)
            self.assertEqual("user-path", identity["source"])
            self.assertEqual("user-supplied:1.8.0_504-b01", identity["runtime_identity"])
            with patch("workbench_shell.runtime_launch.probe_java", return_value=probe):
                path, identity = runtime_launch._selected_java(result, host)
            self.assertEqual(java, path)
            self.assertEqual("user-path", identity["source"])
            self.assertEqual({"selection_kind": "user-path"}, identity["policy"])


if __name__ == "__main__":
    unittest.main()
