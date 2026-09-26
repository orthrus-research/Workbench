"""Core reopens owner-specific Java 25 preflight without running the fixture."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from unittest import TestCase, skipIf
from unittest.mock import patch
import sys

from workbench_core.environment_fixture_java_binding import (
    apply_fixture_java_binding, plan_fixture_java_binding,
)
from workbench_core.environment_fixture_java_preflight import (
    apply_fixture_java_preflight, plan_fixture_java_preflight,
    reopen_fixture_java_preflight,
)
from workbench_core.environment_reconstruction import ReconstructionError

import test_environment_fixture_java_binding as binding_tests


class _Owner:
    def inspect_portable_java_home(self, *, java_home: Path) -> dict:
        if java_home.is_symlink():
            raise RuntimeError("owner refuses a Java alias")
        release = (java_home / "release").read_bytes()
        if not release.startswith(b'JAVA_VERSION="25.'):
            raise RuntimeError("the fixture requires Java 25")
        executable = (java_home / "bin/java").read_bytes()
        if not (java_home / "bin/java").stat().st_mode & 0o111:
            raise RuntimeError("Java executable is not executable")
        return {
            "format": "workbench-cleanroom-fixture-java25-preflight-v1",
            "schema_version": 1, "feature_version": 25,
            "runtime_version": "25.0.4",
            "requested_home": str(java_home), "resolved_home": str(java_home),
            "release_sha256": "sha256:" + sha256(release).hexdigest(),
            "release_size": len(release),
            "executable_sha256": "sha256:" + sha256(executable).hexdigest(),
            "executable_size": len(executable),
        }


@skipIf(not sys.platform.startswith("linux"), "fixture Core tree custody is Linux/WSL only")
class EnvironmentFixtureJavaPreflightTests(TestCase):
    setUp = binding_tests.EnvironmentFixtureJavaBindingTests.setUp
    _refresh = binding_tests.EnvironmentFixtureJavaBindingTests._refresh
    _selection = binding_tests.EnvironmentFixtureJavaBindingTests._selection
    _retain_fixture_and_gradle = binding_tests.EnvironmentFixtureJavaBindingTests._retain_fixture_and_gradle
    _inputs = binding_tests.EnvironmentFixtureJavaBindingTests._inputs
    _arguments = binding_tests.EnvironmentFixtureJavaBindingTests._arguments

    def _ready(self, *, alias: bool = False) -> None:
        actual = self.root / "selected-java25"
        (actual / "bin").mkdir(parents=True)
        (actual / "release").write_bytes(b'JAVA_VERSION="25.0.4"\n')
        java = actual / "bin/java"
        java.write_bytes(b"#!/bin/sh\nexit 0\n")
        java.chmod(0o700)
        self.java_home = actual
        chosen = actual
        if alias:
            chosen = self.root / "selected-java-alias"
            chosen.symlink_to(actual, target_is_directory=True)
        self._refresh(java_home=str(chosen))
        self._selection()
        self._retain_fixture_and_gradle()
        self._inputs()
        binding_plan = plan_fixture_java_binding(
            self.suite, self.share, self.candidate, **self._arguments(),
        )
        binding = apply_fixture_java_binding(
            self.suite, self.share, self.candidate,
            expected_plan_id=binding_plan["plan_id"], **self._arguments(),
        )
        self.binding_id = binding["resource"]["resource_id"]
        owner_code = self.candidate["profile_fixture"]["owner_code"]
        for name, value in (
            ("workbench_core.environment_fixture_java_preflight.profile_extension_identity", owner_code),
            ("workbench_core.environment_fixture_java_preflight.require_profile_extension", _Owner()),
        ):
            replacement = patch(name, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def _plan(self):
        return plan_fixture_java_preflight(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            binding_result_resource_id=self.binding_id, environment=self.environment,
        )

    def _apply(self, plan):
        return apply_fixture_java_preflight(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            binding_result_resource_id=self.binding_id,
            expected_plan_id=plan["plan_id"], environment=self.environment,
        )

    def _reopen(self, result):
        return reopen_fixture_java_preflight(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            binding_result_resource_id=self.binding_id,
            result_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_preflight_retains_owner_identity_and_reopens_without_original_sources(self) -> None:
        self._ready()
        plan = self._plan()
        self.assertEqual("review-only-unqualified", plan["state"])
        self.assertEqual(25, plan["owner_java25"]["feature_version"])
        result = self._apply(plan)
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        self.assertIn("Gradle execution", result["scope"])
        self.archive.unlink()
        for row in self.owner.inputs:
            row["path"].unlink()
        self.assertEqual(result["owner_java25"], self._reopen(result)["owner_java25"])

    def test_owner_code_and_java_drift_refuse_preflight_or_reopen(self) -> None:
        self._ready()
        plan = self._plan()
        result = self._apply(plan)
        (self.java_home / "release").write_bytes(b'JAVA_VERSION="8.0.472"\n')
        with self.assertRaisesRegex(ReconstructionError, "Java 25"):
            self._reopen(result)
        (self.java_home / "release").write_bytes(b'JAVA_VERSION="25.0.4"\n')
        with patch(
            "workbench_core.environment_fixture_java_preflight.profile_extension_identity",
            return_value={"another": "owner"},
        ):
            with self.assertRaisesRegex(ReconstructionError, "owner code differs"):
                self._plan()

    def test_alias_is_preserved_by_selection_then_rejected_by_owner(self) -> None:
        self._ready(alias=True)
        with self.assertRaisesRegex(ReconstructionError, "owner refuses a Java alias"):
            self._plan()

    def test_changed_java_after_review_refuses_result_publication(self) -> None:
        self._ready()
        plan = self._plan()
        (self.java_home / "bin/java").write_bytes(b"different executable")
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            self._apply(plan)
