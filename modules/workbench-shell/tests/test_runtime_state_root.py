"""Focused propagation tests for an external runtime state root."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.runtime_bootstrap import (  # noqa: E402
    bootstrap_project_runtime,
)
from workbench_core.configuration import (  # noqa: E402
    load_workbench_configuration,
)
from workbench_shell.runtime_launch import (  # noqa: E402
    RuntimeLaunchError,
    launch_project_runtime,
)
from workbench_shell.runtime_materialize import (  # noqa: E402
    materialize_project_runtime,
)
from workbench_shell.runtime_plan import plan_project_runtime  # noqa: E402


REVISION = "1" * 40
PLAN_ID = "sha256:" + ("2" * 64)


def _suite(root: Path) -> tuple[Path, bytes]:
    suite = root / "suite"
    profile = suite / "profiles/platforms/cleanroom/test.yaml"
    profile.parent.mkdir(parents=True)
    payload = (
        b"schema_version: 1\n"
        b"profile_id: workbench-platform:cleanroom:test\n"
        b"java: {}\n"
    )
    profile.write_bytes(payload)
    pack = suite / "profiles/packs/example/profile.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_text(
        "schema_version: 1\n"
        "profile_family_id: workbench-pack:example\n"
        "profiles:\n"
        "  cleanroom-test:\n"
        "    platform_profile_id: workbench-platform:cleanroom:test\n",
        encoding="utf-8",
    )
    (suite / "workbench.toml").write_text(
        'schema = "workbench/config/v1"\n\n'
        "[selection]\n"
        'pack_document = "profiles/packs/example/profile.yaml"\n'
        'pack_variant = "cleanroom-test"\n'
        'platform_document = "profiles/platforms/cleanroom/test.yaml"\n\n'
        "[bindings]\n",
        encoding="utf-8",
    )
    return suite, payload


class RuntimeStateRootTest(unittest.TestCase):
    def test_planner_resolves_external_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite, profile = _suite(root)
            state = root / "outer/../runtime-state"
            context = {
                "platform": {
                    "document_sha256": sha256(profile).hexdigest(),
                },
            }
            expected = {"format": "test-runtime-plan"}
            with (
                patch(
                    "workbench_shell.runtime_plan.inspect_project",
                    return_value={"workspace_context": context},
                ),
                patch(
                    "workbench_shell.runtime_plan.build_runtime_plan",
                    return_value=expected,
                ) as build,
            ):
                result = plan_project_runtime(
                    suite,
                    root / "workspace",
                    state_root=state,
                )

            self.assertIs(result, expected)
            self.assertEqual(
                build.call_args.kwargs["state_root"],
                state.resolve(),
            )

    def test_bootstrap_uses_one_external_root_for_plan_and_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite, _profile = _suite(root)
            state = (root / "runtime-state").resolve()
            plan = {"plan_id": PLAN_ID}
            expected = {"outcome": "created"}
            with (
                patch(
                    "workbench_shell.runtime_bootstrap.plan_project_runtime",
                    return_value=plan,
                ) as planner,
                patch(
                    "workbench_shell.runtime_bootstrap._load_bootstrap_artifact",
                    return_value=(123, REVISION),
                ),
                patch(
                    "workbench_shell.runtime_bootstrap.materialize_client_bootstrap",
                    return_value=expected,
                ) as materialize,
            ):
                result = bootstrap_project_runtime(
                    suite,
                    root / "workspace",
                    state_root=state,
                )

            self.assertIs(result, expected)
            self.assertEqual(planner.call_args.kwargs["state_root"], state)
            self.assertEqual(materialize.call_args.kwargs["state_root"], state)

    def test_materializer_propagates_external_fixture_artifact_and_jdk_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite, _profile = _suite(root)
            workspace = (root / "workspace").resolve()
            state = (root / "runtime-state").resolve()
            plan = {"plan_id": PLAN_ID, "blockers": []}
            bootstrap = {
                "outcome": "reused",
                "receipt": {"plan_id": PLAN_ID},
            }
            java_result = {"outcome": "discovered"}
            lock = {
                "url": "https://example.invalid/installer.jar",
                "sha256": "3" * 64,
                "size": 123,
            }
            expected = {"outcome": "installed"}
            with (
                patch(
                    "workbench_shell.runtime_materialize.plan_project_runtime",
                    return_value=plan,
                ) as planner,
                patch(
                    "workbench_shell.runtime_materialize._installer_lock",
                    return_value=lock,
                ),
                patch(
                    "workbench_shell.runtime_materialize.bootstrap_project_runtime",
                    return_value=bootstrap,
                ) as bootstrapper,
                patch(
                    "workbench_shell.runtime_materialize.ensure_java_runtime",
                    return_value=java_result,
                ) as ensure_java,
                patch(
                    "workbench_shell.runtime_materialize._selected_java",
                    return_value=(root / "java", {"runtime_id": "test"}),
                ),
                patch(
                    "workbench_shell.runtime_materialize.fetch_verified_artifact",
                    return_value=(root / "installer.jar", "reused"),
                ) as fetch,
                patch(
                    "workbench_shell.runtime_materialize._resolve_packwiz",
                    return_value=root / "packwiz",
                ),
                patch(
                    "workbench_shell.runtime_materialize.materialize_packwiz_workspace_v2",
                    return_value=expected,
                ) as materialize,
            ):
                result = materialize_project_runtime(
                    suite,
                    workspace,
                    state_root=state,
                )

            self.assertIs(result, expected)
            self.assertEqual(planner.call_args.kwargs["state_root"], state)
            self.assertEqual(bootstrapper.call_args.kwargs["state_root"], state)
            ensure_java.assert_called_once()
            self.assertEqual(ensure_java.call_args.args, (suite.resolve(),))
            self.assertEqual(
                ensure_java.call_args.kwargs["state_root"],
                state,
            )
            self.assertEqual(
                ensure_java.call_args.kwargs["configuration"].selection_digest,
                planner.call_args.kwargs["configuration"].selection_digest,
            )
            self.assertEqual(
                ensure_java.call_args.kwargs[
                    "resolved_bindings"
                ].binding("java_candidate_home").source,
                "undeclared",
            )
            self.assertEqual(fetch.call_args.kwargs["state_root"], state)
            self.assertEqual(materialize.call_args.kwargs["state_root"], state)

    def test_launcher_keeps_cross_host_jdk_on_launcher_filesystem(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite, _profile = _suite(root)
            configuration = load_workbench_configuration(suite)
            state = (root / "runtime-state").resolve()
            selected_host = {
                "os": "windows",
                "architecture": "x64",
                "system": "Windows",
                "machine": "AMD64",
            }
            native_host = {
                "os": "linux",
                "architecture": "x64",
                "system": "Linux",
                "machine": "x86_64",
            }
            materialization = {"outcome": "installed"}
            java_result = {"outcome": "provisioned"}
            expected = {"outcome": "checkpoint-reached"}
            with (
                patch(
                    "workbench_shell.runtime_launch.materialize_project_runtime",
                    return_value=materialization,
                ) as materialize,
                patch(
                    "workbench_shell.runtime_launch.load_workbench_configuration",
                    return_value=configuration,
                ) as load_configuration,
                patch(
                    "workbench_shell.runtime_launch._probe_launcher",
                    return_value=(root / "launcher.exe", {}, selected_host),
                ),
                patch(
                    "workbench_shell.runtime_launch.host_platform",
                    return_value=native_host,
                ),
                patch(
                    "workbench_shell.runtime_launch.ensure_java_runtime",
                    return_value=java_result,
                ) as ensure_java,
                patch(
                    "workbench_shell.runtime_launch.launch_materialized_client",
                    return_value=expected,
                ) as launch,
            ):
                result = launch_project_runtime(
                    suite,
                    root / "workspace",
                    state_root=state,
                    launcher_executable=root / "launcher.exe",
                    launcher_root=root / "launcher-data",
                )

            self.assertIs(result, expected)
            load_configuration.assert_called_once_with(
                suite.resolve(),
                Path("workbench.toml"),
            )
            self.assertEqual(materialize.call_args.kwargs["state_root"], state)
            self.assertEqual(
                ensure_java.call_args.kwargs["state_root"],
                (root / "launcher-data/.workbench").resolve(),
            )
            self.assertEqual(launch.call_args.kwargs["state_root"], state)
            self.assertIs(
                materialize.call_args.kwargs["configuration"],
                configuration,
            )
            self.assertIs(
                ensure_java.call_args.kwargs["configuration"],
                configuration,
            )
            self.assertIs(
                materialize.call_args.kwargs["resolved_bindings"],
                ensure_java.call_args.kwargs["resolved_bindings"],
            )

    def test_launcher_accepts_explicit_cross_host_jdk_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite, _profile = _suite(root)
            selected_host = {
                "os": "windows",
                "architecture": "x64",
                "system": "Windows",
                "machine": "AMD64",
            }
            native_host = {
                "os": "linux",
                "architecture": "x64",
                "system": "Linux",
                "machine": "x86_64",
            }
            java_state = (root / "launcher-java-state").resolve()
            java_candidate = root / "launcher-java"
            with (
                patch(
                    "workbench_shell.runtime_launch.materialize_project_runtime",
                    return_value={"outcome": "installed"},
                ),
                patch(
                    "workbench_shell.runtime_launch._probe_launcher",
                    return_value=(root / "launcher.exe", {}, selected_host),
                ),
                patch(
                    "workbench_shell.runtime_launch.host_platform",
                    return_value=native_host,
                ),
                patch(
                    "workbench_shell.runtime_launch.ensure_java_runtime",
                    return_value={"outcome": "provisioned"},
                ) as ensure_java,
                patch(
                    "workbench_shell.runtime_launch.launch_materialized_client",
                    return_value={"outcome": "checkpoint-reached"},
                ),
            ):
                launch_project_runtime(
                    suite,
                    root / "workspace",
                    state_root=root / "runtime-state",
                    launcher_java=java_candidate,
                    launcher_java_state=java_state,
                    launcher_executable=root / "launcher.exe",
                    launcher_root=root / "launcher-data",
                )

            self.assertEqual(
                ensure_java.call_args.kwargs["state_root"],
                java_state,
            )
            self.assertEqual(
                ensure_java.call_args.kwargs["candidates"],
                [("launcher-java", java_candidate)],
            )

    def test_launcher_rejects_configuration_and_path_together(self) -> None:
        with self.assertRaisesRegex(
            RuntimeLaunchError,
            "mutually exclusive",
        ):
            launch_project_runtime(
                REPOSITORY_ROOT,
                REPOSITORY_ROOT,
                launcher_executable=REPOSITORY_ROOT / "launcher",
                launcher_root=REPOSITORY_ROOT / "launcher-root",
                configuration=object(),  # type: ignore[arg-type]
                config_path="workbench.toml",
            )


if __name__ == "__main__":
    unittest.main()
