"""A V3 share can link independently acquired Core inputs without overstating closure."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_core.environment_reconstruction import (
    ReconstructionError, apply_environment_composition,
    apply_environment_input_composition, apply_fixture_import, apply_import,
    apply_tool_import, apply_wheel_import, build_share, plan_environment_composition,
    plan_environment_input_composition, plan_fixture_import, plan_import,
    plan_tool_import, plan_wheel_import, reopen_environment_input_composition,
)
from workbench_core import tooling_provision
from workbench_core.user_preferences import set_workspace_selection

import test_environment_project_import as project_fixture


class EnvironmentCompositionTests(unittest.TestCase):
    # Reuse the local-Git, two-suite fixture without inheriting its test cases.
    setUp = project_fixture.EnvironmentProjectImportTests.setUp
    _git = project_fixture.EnvironmentProjectImportTests._git
    _attach_source_lock = project_fixture.EnvironmentProjectImportTests._attach_source_lock
    _plan = project_fixture.EnvironmentProjectImportTests._plan
    _apply = project_fixture.EnvironmentProjectImportTests._apply

    def _acquired(self, *, acquire_java: bool = False) -> tuple[dict, dict, dict]:
        self.share = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        reviewed = plan_import(
            self.target_suite, self.share, workspace_name="shared",
            workspace=self.target_workspace, acquire_managed_java=acquire_java,
            environment=self.target_environment,
        )
        selection = apply_import(
            self.target_suite, self.share, expected_plan_id=reviewed["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=acquire_java, environment=self.target_environment,
        )
        project = self._apply(self._plan())
        tool_plan = plan_tool_import(
            self.target_suite, self.share, workspace=self.target_workspace,
            environment=self.target_environment,
        )
        tools = apply_tool_import(
            self.target_suite, self.share, expected_plan_id=tool_plan["plan_id"],
            workspace=self.target_workspace, environment=self.target_environment,
        )
        return selection, project, tools

    def _arguments(self, selected: dict, project: dict, tools: dict) -> dict:
        return {
            "workspace_name": "shared", "workspace": self.target_workspace,
            "selection_resource_id": selected["resource"]["resource_id"],
            "project_resource_id": project["resource"]["resource_id"],
            "tool_resource_id": tools["resource"]["resource_id"],
            "environment": self.target_environment,
        }

    def _five_inputs(self, *, acquire_java: bool = False) -> tuple[dict, dict]:
        from test_environment_fixture_import import _ImportFixtureOwner
        from test_environment_input_candidates import _wheel
        from workbench_api.profiles import Profile, ProfileStatus
        from workbench_core.environment_input_candidates import build_input_candidate

        self._ready_tools()
        selection, project, tools = self._acquired(acquire_java=acquire_java)
        self.five_selection = selection
        owner = _ImportFixtureOwner(self.root / "candidate-owner")
        wheel = _wheel(self.root / "workbench_demo-0.1.0-py3-none-any.whl")
        identity = {
            "profile_id": "cleanroom", "group": "workbench.workspace_home_fixtures",
            "module": "fixture_owner", "distribution": "workbench-profile-cleanroom",
            "version": "0.1.1", "api_version": 1,
            "sha256": "a" * 64, "size": 100, "package_source_sha256": "b" * 64,
        }
        profile_owner = ProfileStatus(
            "cleanroom", "workbench-profile-cleanroom", "0.1.1", "available",
            profile=Profile(
                "cleanroom", "platform", self.source_suite / "profiles/platforms/cleanroom",
                {"profile": "provisional.yaml"},
            ),
        )
        for name, replacement in (
            ("workbench_core.environment_input_candidates.profile_status", (profile_owner,)),
            ("workbench_core.environment_input_candidates.require_profile_extension", owner),
            ("workbench_core.environment_input_candidates.profile_extension_identity", identity),
            ("workbench_core.environment_fixture_import.require_profile_extension", owner),
        ):
            started = patch(name, return_value=replacement)
            started.start()
            self.addCleanup(started.stop)
        candidate = build_input_candidate(
            self.share, wheels=(wheel,), profile_owner_id="cleanroom",
        )
        wheel_plan = plan_wheel_import(
            self.target_suite, self.share, candidate, workspace=self.target_workspace,
            wheels=(wheel,), environment=self.target_environment,
        )
        wheel_result = apply_wheel_import(
            self.target_suite, self.share, candidate, workspace=self.target_workspace,
            wheels=(wheel,), expected_plan_id=wheel_plan["plan_id"],
            environment=self.target_environment,
        )
        fixture_plan = plan_fixture_import(
            self.target_suite, self.share, candidate, workspace=self.target_workspace,
            environment=self.target_environment,
        )
        fixture_result = apply_fixture_import(
            self.target_suite, self.share, candidate, workspace=self.target_workspace,
            expected_plan_id=fixture_plan["plan_id"], environment=self.target_environment,
        )
        arguments = {
            **self._arguments(selection, project, tools),
            "wheel_resource_id": wheel_result["resource"]["resource_id"],
            "fixture_resource_id": fixture_result["resource"]["resource_id"],
        }
        self.five_wheel = wheel
        self.five_fixture_owner = owner
        self.five_wheel_result = wheel_result
        self.five_fixture_result = fixture_result
        return candidate, arguments

    def _ready_tools(self) -> None:
        def inspect(state_root: Path, *, key: str) -> dict:
            state = "ready" if self.tool_ready else "missing"
            return {
                "format": tooling_provision.FORMAT,
                "host": key,
                "state": "initialized" if self.tool_ready else "attention",
                "tools": {
                    name: {
                        "state": state,
                        "executable": str(state_root / name) if self.tool_ready else None,
                    }
                    for name in ("prism", "packwiz")
                },
            }

        self.tool_ready = True
        for target, name, replacement in (
            (tooling_provision, "inspect_tools", inspect),
            ("workbench_core.environment_tool_import", "inspect_locked_managed_tools",
             lambda lock, *, state_root: {"state": "ready" if self.tool_ready else "bytes-unavailable"}),
            ("workbench_core.environment_composition", "inspect_locked_managed_tools",
             lambda lock, *, state_root: {"state": "ready" if self.tool_ready else "bytes-unavailable"}),
        ):
            started = patch.object(target, name, replacement) if not isinstance(target, str) else patch(f"{target}.{name}", replacement)
            started.start()
            self.addCleanup(started.stop)

    def test_reviewed_composition_links_three_core_results_and_keeps_missing_inputs(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        plan = plan_environment_composition(self.target_suite, self.share, **arguments)
        self.assertEqual("ready", plan["state"])
        self.assertEqual({"selection", "project", "managed_tools"}, set(plan["resources"]))
        self.assertNotIn("workspace-project-bytes", plan["unresolved_inputs"])
        self.assertIn("optional-module-packages", plan["unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", plan["unresolved_inputs"])
        self.assertFalse(list((self.target_user / "state/evidence/outputs/workbench-core").glob(
            "*-environment-composition.json")))
        result = apply_environment_composition(
            self.target_suite, self.share, expected_plan_id=plan["plan_id"], **arguments,
        )
        self.assertEqual(plan["resources"], result["resources"])
        self.assertTrue(Path(result["resource"]["path"]).is_file())

    def test_changed_project_and_tools_refuse_a_reviewed_composition(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        plan = plan_environment_composition(self.target_suite, self.share, **arguments)
        checkout = Path(project["managed_destination"])
        marker = checkout / "pack.marker"
        marker.write_bytes(b"changed project\n")
        with self.assertRaisesRegex((ReconstructionError, OSError), "worktree differs"):
            apply_environment_composition(
                self.target_suite, self.share, expected_plan_id=plan["plan_id"], **arguments,
            )
        marker.write_bytes(b"exact project bytes\n")
        self.tool_ready = False
        with self.assertRaisesRegex(ReconstructionError, "managed-tool bytes changed"):
            apply_environment_composition(
                self.target_suite, self.share, expected_plan_id=plan["plan_id"], **arguments,
            )
        self.assertFalse(list((self.target_user / "state/evidence/outputs/workbench-core").glob(
            "*-environment-composition.json")))

    def test_swapped_or_stale_result_identity_is_refused(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        plan = plan_environment_composition(self.target_suite, self.share, **arguments)
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_environment_composition(
                self.target_suite, self.share, expected_plan_id="stale", **arguments,
            )
        arguments["tool_resource_id"] = project["resource"]["resource_id"]
        with self.assertRaisesRegex(ReconstructionError, "distinct Core result"):
            plan_environment_composition(self.target_suite, self.share, **arguments)
        self.assertEqual("ready", plan["state"])

    def test_changed_local_selection_cannot_be_composed(self) -> None:
        self._ready_tools()
        selection, project, tools = self._acquired()
        arguments = self._arguments(selection, project, tools)
        set_workspace_selection(
            "shared", profile_config=str(self.target_suite / "another-selection.toml"),
            environment=self.target_environment,
        )
        with self.assertRaisesRegex(ReconstructionError, "local workspace selection changed"):
            plan_environment_composition(self.target_suite, self.share, **arguments)

    def test_v2_links_five_results_and_reopens_without_original_sources(self) -> None:
        candidate, arguments = self._five_inputs()
        plan = plan_environment_input_composition(
            self.target_suite, self.share, candidate, **arguments,
        )
        self.assertEqual("ready", plan["state"])
        self.assertEqual(5, len(plan["resources"]))
        self.assertEqual("unresolved", plan["java"]["state"])
        self.assertNotIn("workspace-project-bytes", plan["unresolved_inputs"])
        self.assertIn("optional-module-packages", plan["unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", plan["unresolved_inputs"])
        self.assertIn("managed-java-archive", plan["unresolved_inputs"])
        result = apply_environment_input_composition(
            self.target_suite, self.share, candidate,
            expected_plan_id=plan["plan_id"], **arguments,
        )
        self.five_wheel.unlink()
        for row in self.five_fixture_owner.inputs:
            row["path"].unlink()
        (self.five_fixture_owner.root / "src/main.txt").unlink()
        reopened = reopen_environment_input_composition(
            self.target_suite, self.share, candidate, workspace_name="shared",
            workspace=self.target_workspace, result_resource_id=result["resource"]["resource_id"],
            environment=self.target_environment,
        )
        self.assertEqual(result["resources"], reopened["resources"])
        self.assertEqual(result["unresolved_inputs"], reopened["unresolved_inputs"])

    def test_v2_refuses_changed_retained_wheel_and_fixture_bytes(self) -> None:
        candidate, arguments = self._five_inputs()
        plan = plan_environment_input_composition(
            self.target_suite, self.share, candidate, **arguments,
        )
        retained_wheel = Path(self.five_wheel_result["tree_path"]) / self.five_wheel.name
        with retained_wheel.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ReconstructionError):
            apply_environment_input_composition(
                self.target_suite, self.share, candidate,
                expected_plan_id=plan["plan_id"], **arguments,
            )

    def test_v2_refuses_changed_retained_fixture_and_swapped_results(self) -> None:
        candidate, arguments = self._five_inputs()
        switched = {**arguments, "wheel_resource_id": arguments["fixture_resource_id"]}
        with self.assertRaises(ReconstructionError):
            plan_environment_input_composition(
                self.target_suite, self.share, candidate, **switched,
            )
        plan = plan_environment_input_composition(
            self.target_suite, self.share, candidate, **arguments,
        )
        result = apply_environment_input_composition(
            self.target_suite, self.share, candidate,
            expected_plan_id=plan["plan_id"], **arguments,
        )
        retained = (Path(self.five_fixture_result["tree_path"])
                    / "profiles/platforms/cleanroom/fixtures/demo/src/main.txt")
        retained.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            apply_environment_input_composition(
                self.target_suite, self.share, candidate,
                expected_plan_id=plan["plan_id"], **arguments,
            )
        with self.assertRaises(ReconstructionError):
            reopen_environment_input_composition(
                self.target_suite, self.share, candidate, workspace_name="shared",
                workspace=self.target_workspace,
                result_resource_id=result["resource"]["resource_id"],
                environment=self.target_environment,
            )

    def test_v2_stale_review_does_not_publish_result(self) -> None:
        candidate, arguments = self._five_inputs()
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_environment_input_composition(
                self.target_suite, self.share, candidate,
                expected_plan_id="stale", **arguments,
            )
        evidence = self.target_user / "state/evidence/outputs/workbench-core"
        self.assertFalse(list(evidence.glob("*-environment-input-composition.json")))

    def test_v2_refuses_another_sealed_input_candidate(self) -> None:
        from workbench_core.environment_reconstruction import _seal

        candidate, arguments = self._five_inputs()
        changed = deepcopy(candidate)
        changed["profile_fixture"]["owner_code"]["sha256"] = "0" * 64
        changed.pop("candidate_id")
        other = _seal(changed, "workbench-environment-input-candidate", "candidate_id")
        with self.assertRaises(ReconstructionError):
            plan_environment_input_composition(
                self.target_suite, self.share, other, **arguments,
            )

    def test_v2_user_java_binding_remains_unchecked(self) -> None:
        from workbench_core.environment_composition import _java_evidence

        local_path_share = deepcopy(self.share)
        local_path_share["intent"]["java"]["mode"] = "local-binding-required"
        with patch("workbench_core.environment_composition.inspect_managed_java_runtime",
                   side_effect=AssertionError("user path must not be probed")):
            evidence = _java_evidence(
                self.target_suite, local_path_share,
                {"java_home": "/user/selected/jdk"},
                state_root=self.target_user / "state",
            )
        self.assertEqual({"state": "user-local-binding-unchecked", "marker": "local-java-home"},
                         evidence)

    def test_v2_rechecks_acquired_managed_java_before_linking(self) -> None:
        from workbench_core.runtime_java import (
            host_platform, load_java_runtime_policy, select_managed_java_policy,
        )
        from workbench_core.configuration import load_workbench_configuration

        set_workspace_selection(
            "pack", profile_config=str(self.source_suite / "workbench.toml"),
            managed_java_feature=8, environment=self.source_environment,
        )

        def acquire_java(suite, **options):
            policy = select_managed_java_policy(
                load_java_runtime_policy(suite, configuration=options["configuration"]),
                options["managed_feature_version"],
            )
            return {"format": "workbench-java-runtime-result-v2", "source": "managed",
                    "outcome": "provisioned", "receipt": {
                        "format": "workbench-java-runtime-receipt-v2", "state": "ready",
                        "policy": policy, "host": host_platform(),
                        "runtime_id": "workbench-java-runtime-v2:test",
                        "target": {"receipt_uri": "file:///local/state/receipts/java-runtime-v2.json"},
                    }}

        with patch("workbench_core.environment_reconstruction.ensure_java_runtime",
                   side_effect=acquire_java):
            candidate, arguments = self._five_inputs(acquire_java=True)
        selection = self.five_selection
        configuration = load_workbench_configuration(
            self.target_suite, Path(selection["configuration_manifest"]["path"]),
        )
        policy = select_managed_java_policy(
            load_java_runtime_policy(self.target_suite, configuration=configuration), 8,
        )
        receipt = {
            "runtime_id": selection["managed_java"]["runtime_id"],
            "target": {"receipt_uri": selection["managed_java"]["receipt_uri"]},
            "policy": policy,
        }
        with patch("workbench_core.environment_composition.inspect_managed_java_runtime",
                   return_value={"source": "managed", "receipt": receipt}) as inspect:
            plan = plan_environment_input_composition(
                self.target_suite, self.share, candidate, **arguments,
            )
            self.assertEqual("current-managed-runtime", plan["java"]["state"])
            self.assertNotIn("managed-java-archive", plan["unresolved_inputs"])
            result = apply_environment_input_composition(
                self.target_suite, self.share, candidate,
                expected_plan_id=plan["plan_id"], **arguments,
            )
            self.assertEqual("current-managed-runtime", result["java"]["state"])
            self.assertGreaterEqual(inspect.call_count, 2)
        with patch("workbench_core.environment_composition.inspect_managed_java_runtime",
                   return_value={"source": "managed", "receipt": {
                       **receipt, "runtime_id": "changed",
                   }}):
            with self.assertRaisesRegex(ReconstructionError, "differs"):
                plan_environment_input_composition(
                    self.target_suite, self.share, candidate, **arguments,
                )


if __name__ == "__main__":
    unittest.main()
