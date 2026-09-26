"""Fixture Java binding preserves explicit paths and verifies managed Java 25."""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256
import json
import stat
from unittest import TestCase, skipIf
from unittest.mock import patch
import sys
from zipfile import ZipFile, ZipInfo

from workbench_core.configuration import load_workbench_configuration
from workbench_core.environment_fixture_gradle_extract import (
    apply_fixture_gradle_extraction, plan_fixture_gradle_extraction,
)
from workbench_core.environment_fixture_gradle_import import (
    apply_fixture_gradle_import, plan_fixture_gradle_import,
)
from workbench_core.environment_fixture_import import apply_fixture_import, plan_fixture_import
from workbench_core.environment_fixture_execution_policy import review_fixture_execution_policy
from workbench_core.environment_fixture_java_binding import (
    apply_fixture_java_binding, plan_fixture_java_binding,
    reopen_fixture_java_binding,
)
from workbench_core.environment_input_candidates import build_input_candidate
from workbench_core.environment_reconstruction import (
    ReconstructionError, apply_import, build_share, plan_import,
)
from workbench_core.runtime_java import (
    host_platform, load_java_runtime_policy, select_managed_java_policy,
)
from workbench_core.user_preferences import set_workspace_selection

import test_environment_input_candidates as candidate_tests
from test_environment_fixture_execution_policy import _PortableFixtureOwner
from test_environment_reconstruction import _environment


@skipIf(not sys.platform.startswith("linux"), "fixture Java binding uses Linux/WSL exact trees")
class EnvironmentFixtureJavaBindingTests(TestCase):
    def setUp(self) -> None:
        candidate_tests.EnvironmentInputCandidateTests.setUp(self)
        self.suite = self.root / "suite"
        self.workspace = self.root / "workspace"
        self.environment = _environment(self.root / "user")
        self.owner = _PortableFixtureOwner(self.root)
        self.selected_java_path = None
        for name in (
            "workbench_core.environment_input_candidates.require_profile_extension",
            "workbench_core.environment_fixture_import.require_profile_extension",
        ):
            replacement = patch(name, return_value=self.owner)
            replacement.start()
            self.addCleanup(replacement.stop)
        for name in (
            "workbench_core.environment_fixture_gradle_import._qualified_filesystem",
            "workbench_core.environment_fixture_gradle_extract._qualified_filesystem",
        ):
            replacement = patch(name, return_value=True)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.archive = self.root / "gradle-9.6.1-bin.zip"
        with ZipFile(self.archive, "w") as bundle:
            launcher = ZipInfo("gradle-9.6.1/bin/gradle")
            launcher.create_system = 3
            launcher.external_attr = (stat.S_IFREG | 0o755) << 16
            bundle.writestr(launcher, b"#!/bin/sh\nexit 0\n")
            bundle.writestr("gradle-9.6.1/lib/gradle-launcher-9.6.1.jar", b"launcher")
        policy = self.owner.read_execution_policy()
        policy["gradle"]["archive_sha256"] = "sha256:" + sha256(self.archive.read_bytes()).hexdigest()
        policy["gradle"]["archive_size"] = self.archive.stat().st_size
        self.owner.policy_path.write_text(json.dumps(policy), encoding="utf-8")

    def _refresh(self, *, java_home: str | None = None, feature: int | None = None) -> None:
        self.selected_java_path = java_home
        set_workspace_selection(
            "pack", java_home=java_home, managed_java_feature=feature,
            environment=self.environment,
        )
        self.share = build_share(
            self.suite, "pack", environment=self.environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        self.candidate = build_input_candidate(
            self.share, wheels=(self.wheel,), profile_owner_id="cleanroom",
        )

    def _retain_fixture_and_gradle(self) -> None:
        fixture_plan = plan_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            environment=self.environment,
        )
        fixture = apply_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            expected_plan_id=fixture_plan["plan_id"], environment=self.environment,
        )
        self.fixture_result_id = fixture["resource"]["resource_id"]
        self.review_id = review_fixture_execution_policy(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            environment=self.environment,
        )["review_id"]
        archive_plan = plan_fixture_gradle_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id, archive=self.archive,
            environment=self.environment,
        )
        archive = apply_fixture_gradle_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            expected_plan_id=archive_plan["plan_id"], archive=self.archive,
            environment=self.environment,
        )
        self.archive_result_id = archive["resource"]["resource_id"]

    def _inputs(self) -> None:
        extraction_plan = plan_fixture_gradle_extraction(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            gradle_result_resource_id=self.archive_result_id,
            environment=self.environment,
        )
        extraction = apply_fixture_gradle_extraction(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            gradle_result_resource_id=self.archive_result_id,
            expected_plan_id=extraction_plan["plan_id"], environment=self.environment,
        )
        self.extraction_result_id = extraction["resource"]["resource_id"]

    def _selection(self, *, acquire_java: bool = False) -> None:
        reviewed = plan_import(
            self.suite, self.share, workspace_name="pack", workspace=self.workspace,
            java_home=self.selected_java_path,
            acquire_managed_java=acquire_java, environment=self.environment,
        )
        self.assertEqual("ready", reviewed["state"], reviewed["blockers"])
        selection = apply_import(
            self.suite, self.share, expected_plan_id=reviewed["plan_id"],
            workspace_name="pack", workspace=self.workspace,
            java_home=self.selected_java_path,
            acquire_managed_java=acquire_java, environment=self.environment,
        )
        self.selection_result_id = selection["resource"]["resource_id"]

    def _arguments(self) -> dict:
        return {
            "workspace_name": "pack", "workspace": self.workspace,
            "selection_result_resource_id": self.selection_result_id,
            "fixture_result_resource_id": self.fixture_result_id,
            "expected_review_id": self.review_id,
            "gradle_result_resource_id": self.archive_result_id,
            "extraction_result_resource_id": self.extraction_result_id,
            "environment": self.environment,
        }

    def test_user_path_binding_is_lexical_and_owner_preflight_remains_required(self) -> None:
        chosen = self.root / "missing-user-jdk-alias"
        chosen.symlink_to(self.root / "not-installed-java25")
        self._refresh(java_home=str(chosen))
        self._selection()
        self._retain_fixture_and_gradle()
        self._inputs()
        with patch(
            "workbench_core.environment_fixture_java_binding.inspect_managed_java_runtime",
            side_effect=AssertionError("user JDK must not be inspected"),
        ):
            plan = plan_fixture_java_binding(
                self.suite, self.share, self.candidate, **self._arguments(),
            )
            self.assertEqual("binding-only", plan["state"])
            self.assertEqual({
                "kind": "user-path", "state": "owner-java25-preflight-required",
                "java_home": str(chosen),
            }, plan["java"])
            result = apply_fixture_java_binding(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._arguments(),
            )
            self.assertEqual("binding-only-unqualified", result["state"])
            self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
            self.archive.unlink()
            for row in self.owner.inputs:
                row["path"].unlink()
            reopened = reopen_fixture_java_binding(
                self.suite, self.share, self.candidate,
                result_resource_id=result["resource"]["resource_id"],
                **self._arguments(),
            )
            self.assertEqual(result["java"], reopened["java"])
        set_workspace_selection(
            "pack", java_home=str(self.root / "another-user-jdk"),
            environment=self.environment,
        )
        with self.assertRaises(ReconstructionError):
            reopen_fixture_java_binding(
                self.suite, self.share, self.candidate,
                result_resource_id=result["resource"]["resource_id"],
                **self._arguments(),
            )

    def test_managed_java8_selection_cannot_satisfy_fixture_policy(self) -> None:
        self._refresh(feature=8)
        self._selection()
        self._retain_fixture_and_gradle()
        self._inputs()
        with patch(
            "workbench_core.environment_fixture_java_binding.inspect_managed_java_runtime",
            side_effect=AssertionError("Java 8 must be rejected first"),
        ):
            with self.assertRaisesRegex(ReconstructionError, "requires Java 25"):
                plan_fixture_java_binding(
                    self.suite, self.share, self.candidate, **self._arguments(),
                )

    def test_managed_java25_requires_retained_acquisition_then_reopens(self) -> None:
        self._refresh()
        self.assertEqual(25, self.share["lock"]["java_policy"]["feature_version"])
        self._selection()
        self._retain_fixture_and_gradle()
        self._inputs()
        pending = plan_fixture_java_binding(
            self.suite, self.share, self.candidate, **self._arguments(),
        )
        self.assertEqual("blocked", pending["state"])
        self.assertEqual("managed-java25-unacquired", pending["java"]["state"])
        with self.assertRaisesRegex(ReconstructionError, "blocked"):
            apply_fixture_java_binding(
                self.suite, self.share, self.candidate,
                expected_plan_id=pending["plan_id"], **self._arguments(),
            )

        configuration = load_workbench_configuration(self.suite, self.suite / "workbench.toml")
        policy = select_managed_java_policy(
            load_java_runtime_policy(self.suite, configuration=configuration), None,
        )
        receipt = {
            "format": "workbench-java-runtime-receipt-v2", "state": "ready",
            "policy": policy, "host": host_platform(),
            "runtime_id": "workbench-java-runtime-v2:fixture-test",
            "target": {
                "receipt_uri": (self.root / "managed-jdk/receipt.json").as_uri(),
                "java_home_uri": (self.root / "managed-jdk/home").as_uri(),
            },
        }
        def acquired(*args, **kwargs):
            return {
                "format": "workbench-java-runtime-result-v2", "source": "managed",
                "outcome": "provisioned", "receipt": receipt,
            }
        with patch("workbench_core.environment_reconstruction.ensure_java_runtime", side_effect=acquired):
            self._selection(acquire_java=True)
        with patch(
            "workbench_core.environment_fixture_java_binding.inspect_managed_java_runtime",
            return_value={"source": "managed", "receipt": receipt},
        ) as inspected:
            plan = plan_fixture_java_binding(
                self.suite, self.share, self.candidate, **self._arguments(),
            )
            self.assertEqual("binding-only", plan["state"])
            self.assertEqual("verified-managed-java25", plan["java"]["state"])
            self.assertEqual(str(self.root / "managed-jdk/home"), plan["java"]["java_home"])
            result = apply_fixture_java_binding(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._arguments(),
            )
            reopened = reopen_fixture_java_binding(
                self.suite, self.share, self.candidate,
                result_resource_id=result["resource"]["resource_id"],
                **self._arguments(),
            )
            self.assertEqual(result["java"], reopened["java"])
            self.assertGreaterEqual(inspected.call_count, 3)
        with patch(
            "workbench_core.environment_fixture_java_binding.inspect_managed_java_runtime",
            return_value=None,
        ):
            with self.assertRaisesRegex(ReconstructionError, "retained acquisition"):
                reopen_fixture_java_binding(
                    self.suite, self.share, self.candidate,
                    result_resource_id=result["resource"]["resource_id"],
                    **self._arguments(),
                )

    def test_changed_extracted_gradle_refuses_java_binding(self) -> None:
        self._refresh(java_home=str(self.root / "selected-local-jdk"))
        self._selection()
        self._retain_fixture_and_gradle()
        self._inputs()
        plan = plan_fixture_java_binding(
            self.suite, self.share, self.candidate, **self._arguments(),
        )
        Path(plan["gradle_launcher_path"]).write_bytes(b"changed launcher")
        with self.assertRaises(ReconstructionError):
            apply_fixture_java_binding(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._arguments(),
            )
