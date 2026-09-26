"""Core reviews the owner command over retained inputs without launching it."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch

from workbench_core.environment_fixture_command import plan_fixture_command
from workbench_core.environment_fixture_java_binding import (
    apply_fixture_java_binding, plan_fixture_java_binding,
)
from workbench_core.environment_fixture_java_preflight import (
    apply_fixture_java_preflight, plan_fixture_java_preflight,
)
from workbench_core.environment_fixture_projection import (
    apply_fixture_projection, plan_fixture_projection,
)
from workbench_core.environment_reconstruction import ReconstructionError

import test_environment_fixture_java_binding as binding_tests
from test_environment_fixture_java_preflight import _Owner as JavaOwner
from test_environment_fixture_projection import portable_test_owner


class _CommandOwner(JavaOwner):
    wrong_command = False

    def portable_projection_rules(self, *, policy):
        return {
            "generated_parts": [".gradle", "__pycache__", "build", "out"],
            "generated_suffixes": [".class", ".jar", ".pyc"],
            "generated_roots": [
                policy["paths"]["generated_root_relative_to_projection_digest"],
            ],
        }

    def build_portable_command(
        self, *, policy, project, gradle_cmd, java_home, cleanup_init, state_root,
    ):
        java = self.inspect_portable_java_home(java_home=java_home)
        fields = {
            "gradle_bin": str(gradle_cmd), "project": str(project),
            "project_cache": str(state_root / policy["paths"]["project_cache_relative_to_state"]),
            "cleanup_init": str(cleanup_init), "java_home": java["resolved_home"],
            "gradle_home": str(state_root / policy["paths"]["gradle_home_relative_to_state"]),
        }
        command = {
            "argv": [value.format_map(fields) for value in policy["argv_template"]],
            "cwd": str(project),
            "environment_overrides": {
                name: value.format_map(fields)
                for name, value in policy["environment"].items()
            },
            "capture": policy["capture"], "restart": policy["restart"],
        }
        if self.wrong_command:
            command["argv"][0] = "/another/gradle"
        return command


@skipIf(not sys.platform.startswith("linux"), "fixture command uses Linux/WSL exact trees")
class EnvironmentFixtureCommandTests(TestCase):
    setUp = binding_tests.EnvironmentFixtureJavaBindingTests.setUp
    _refresh = binding_tests.EnvironmentFixtureJavaBindingTests._refresh
    _selection = binding_tests.EnvironmentFixtureJavaBindingTests._selection
    _retain_fixture_and_gradle = binding_tests.EnvironmentFixtureJavaBindingTests._retain_fixture_and_gradle
    _inputs = binding_tests.EnvironmentFixtureJavaBindingTests._inputs
    _arguments = binding_tests.EnvironmentFixtureJavaBindingTests._arguments

    def _ready(self) -> None:
        portable_test_owner(self.owner, self.root)
        java = self.root / "selected-java25"
        (java / "bin").mkdir(parents=True)
        (java / "release").write_bytes(b'JAVA_VERSION="25.0.4"\n')
        (java / "bin/java").write_bytes(b"#!/bin/sh\nexit 0\n")
        (java / "bin/java").chmod(0o700)
        self._refresh(java_home=str(java))
        self._selection()
        self._retain_fixture_and_gradle()
        self._inputs()
        self.command_owner = _CommandOwner()
        for module in (
            "environment_fixture_java_preflight",
            "environment_fixture_projection",
            "environment_fixture_command",
        ):
            for name, value in (
                ("profile_extension_identity", self.candidate["profile_fixture"]["owner_code"]),
                ("require_profile_extension", self.command_owner),
            ):
                replacement = patch(f"workbench_core.{module}.{name}", return_value=value)
                replacement.start()
                self.addCleanup(replacement.stop)
        filesystem = patch(
            "workbench_core.environment_fixture_projection._qualified_filesystem",
            return_value=True,
        )
        filesystem.start()
        self.addCleanup(filesystem.stop)
        binding_plan = plan_fixture_java_binding(
            self.suite, self.share, self.candidate, **self._arguments(),
        )
        binding = apply_fixture_java_binding(
            self.suite, self.share, self.candidate,
            expected_plan_id=binding_plan["plan_id"], **self._arguments(),
        )
        self.binding_id = binding["resource"]["resource_id"]
        preflight_kwargs = {
            "workspace": self.workspace, "binding_result_resource_id": self.binding_id,
            "environment": self.environment,
        }
        preflight_plan = plan_fixture_java_preflight(
            self.suite, self.share, self.candidate, **preflight_kwargs,
        )
        preflight = apply_fixture_java_preflight(
            self.suite, self.share, self.candidate,
            expected_plan_id=preflight_plan["plan_id"], **preflight_kwargs,
        )
        self.preflight_id = preflight["resource"]["resource_id"]
        projection_kwargs = {
            "workspace": self.workspace,
            "fixture_result_resource_id": self.fixture_result_id,
            "expected_review_id": self.review_id, "environment": self.environment,
        }
        projection_plan = plan_fixture_projection(
            self.suite, self.share, self.candidate, **projection_kwargs,
        )
        projection = apply_fixture_projection(
            self.suite, self.share, self.candidate,
            expected_plan_id=projection_plan["plan_id"], **projection_kwargs,
        )
        self.projection_id = projection["resource"]["resource_id"]
        self.project = Path(projection["project"])

    def _plan(self):
        return plan_fixture_command(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            projection_result_resource_id=self.projection_id,
            binding_result_resource_id=self.binding_id,
            preflight_result_resource_id=self.preflight_id,
            environment=self.environment,
        )

    def test_read_only_command_plan_binds_retained_inputs(self) -> None:
        self._ready()
        plan = self._plan()
        self.assertEqual("review-only-unqualified", plan["state"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], plan["unresolved_inputs"])
        self.assertEqual(str(self.project), plan["command"]["cwd"])
        self.assertEqual("UTC", plan["command"]["environment_overrides"]["TZ"])
        self.assertFalse((self.project / "build").exists())
        self.archive.unlink()
        for row in self.owner.inputs:
            row["path"].unlink()
        self.assertEqual(plan["plan_id"], self._plan()["plan_id"])

    def test_owner_command_drift_and_project_change_refuse(self) -> None:
        self._ready()
        self.command_owner.wrong_command = True
        with self.assertRaisesRegex(ReconstructionError, "differs from retained policy"):
            self._plan()
        self.command_owner.wrong_command = False
        (self.project / "src/main.txt").write_bytes(b"changed")
        with self.assertRaisesRegex(ReconstructionError, "projection cannot reopen"):
            self._plan()
