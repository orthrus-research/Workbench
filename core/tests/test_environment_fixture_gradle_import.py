"""Exact Gradle archive custody stays distinct from fixture execution."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from workbench_core.environment_fixture_execution_policy import review_fixture_execution_policy
from workbench_core.environment_fixture_gradle_import import (
    apply_fixture_gradle_import, plan_fixture_gradle_import,
    reopen_fixture_gradle_import,
)
from workbench_core.environment_fixture_import import apply_fixture_import, plan_fixture_import
from workbench_core.environment_input_candidates import build_input_candidate
from workbench_core.environment_reconstruction import ReconstructionError
from workbench_core.environment_reconstruction import _resource_host
from workbench_core.output_routing import _private_directory
from workbench_core.storage.registered import CoreDurableResources

import test_environment_input_candidates as candidate_tests
from test_environment_fixture_execution_policy import _PortableFixtureOwner
from test_environment_reconstruction import _environment


@skipIf(not sys.platform.startswith("linux"), "exact fixture archive custody is a Linux/WSL slice")
class EnvironmentFixtureGradleImportTests(TestCase):
    def setUp(self) -> None:
        candidate_tests.EnvironmentInputCandidateTests.setUp(self)
        self.suite = self.root / "suite"
        self.workspace = self.root / "workspace"
        self.environment = _environment(self.root / "user")
        self.owner = _PortableFixtureOwner(self.root)
        for name in (
            "workbench_core.environment_input_candidates.require_profile_extension",
            "workbench_core.environment_fixture_import.require_profile_extension",
        ):
            replacement = patch(name, return_value=self.owner)
            replacement.start()
            self.addCleanup(replacement.stop)
        filesystem = patch(
            "workbench_core.environment_fixture_gradle_import._qualified_filesystem",
            return_value=True,
        )
        filesystem.start()
        self.addCleanup(filesystem.stop)
        self.archive = self.root / "gradle-9.6.1-bin.zip"
        with ZipFile(self.archive, "w", compression=ZIP_STORED) as bundle:
            bundle.writestr("gradle-9.6.1/bin/gradle", b"#!/bin/sh\nexit 0\n")
            bundle.writestr("gradle-9.6.1/lib/gradle-launcher-9.6.1.jar", b"launcher")
        self._bind_archive()
        self.candidate = build_input_candidate(
            self.share, wheels=(self.wheel,), profile_owner_id="cleanroom",
        )
        fixture_plan = plan_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            environment=self.environment,
        )
        fixture_result = apply_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            expected_plan_id=fixture_plan["plan_id"], environment=self.environment,
        )
        self.fixture_result_id = fixture_result["resource"]["resource_id"]
        self.review_id = review_fixture_execution_policy(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            environment=self.environment,
        )["review_id"]

    def _bind_archive(self) -> None:
        policy = self.owner.read_execution_policy()
        policy["gradle"]["archive_sha256"] = "sha256:" + sha256(self.archive.read_bytes()).hexdigest()
        policy["gradle"]["archive_size"] = self.archive.stat().st_size
        self.owner.policy_path.write_text(json.dumps(policy), encoding="utf-8")

    def _plan(self, archive: Path | None = None):
        return plan_fixture_gradle_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id, archive=archive,
            environment=self.environment,
        )

    def _apply(self, plan, archive: Path | None = None):
        return apply_fixture_gradle_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id, expected_plan_id=plan["plan_id"],
            archive=archive, environment=self.environment,
        )

    def _reopen(self, result):
        return reopen_fixture_gradle_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            result_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_acquire_reopen_and_reuse_after_source_disappears(self) -> None:
        plan = self._plan(self.archive)
        self.assertEqual("ready", plan["state"])
        self.assertEqual("acquire", plan["action"])
        result = self._apply(plan, self.archive)
        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(2, result["archive"]["member_count"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        self.assertEqual(result["tree_id"], self._reopen(result)["tree_id"])
        self.archive.unlink()
        reuse = self._plan()
        self.assertEqual("reuse", reuse["action"])
        self.assertEqual(result["tree_id"], self._apply(reuse)["tree_id"])

    def test_changed_source_and_redirect_refuse_before_publication(self) -> None:
        plan = self._plan(self.archive)
        self.archive.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            self._apply(plan, self.archive)
        self.assertFalse(Path(plan["target"]).exists())
        other = self.archive.with_name("elsewhere.zip")
        self.archive.rename(other)
        self.archive.symlink_to(other)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            self._plan(self.archive)

    def test_redirected_source_parent_refuses_even_with_matching_bytes(self) -> None:
        selected_parent = self.root / "selected-archive"
        selected_parent.mkdir(mode=0o700)
        selected = selected_parent / self.archive.name
        selected.write_bytes(self.archive.read_bytes())
        plan = self._plan(selected)
        actual_parent = self.root / "moved-archive"
        selected_parent.rename(actual_parent)
        selected_parent.symlink_to(actual_parent, target_is_directory=True)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            self._apply(plan, selected)
        self.assertFalse(Path(plan["target"]).exists())

    def test_changed_retained_bytes_and_collision_refuse(self) -> None:
        plan = self._plan(self.archive)
        target = Path(plan["target"])
        _private_directory(target.parent)
        target.mkdir(mode=0o700)
        with self.assertRaisesRegex(ReconstructionError, "outside Core custody"):
            self._plan(self.archive)
        target.rmdir()
        result = self._apply(plan, self.archive)
        retained = Path(result["tree_path"]) / self.archive.name
        retained.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)

    def test_result_interruption_reuses_exact_tree(self) -> None:
        plan = self._plan(self.archive)
        original = CoreDurableResources.publish_bytes

        def interrupted(service, role, name, data, **kwargs):
            if name == "environment-fixture-gradle-import.json":
                raise RuntimeError("interrupted after Gradle tree commit")
            return original(service, role, name, data, **kwargs)

        with patch.object(CoreDurableResources, "publish_bytes", interrupted):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan, self.archive)
        self.archive.unlink()
        reuse = self._plan()
        self.assertEqual("reuse", reuse["action"])
        result = self._apply(reuse)
        self.assertEqual("reused", result["outcome"])
        self._reopen(result)

    def test_changed_prepared_attempt_refuses_result_reopen(self) -> None:
        result = self._apply(self._plan(self.archive), self.archive)
        service = _resource_host(self.suite, self.workspace, self.environment)
        attempt = service.describe(result["attempt_resource_id"])
        attempt.path.write_bytes(b"{}")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)

    def test_corrupt_crc_and_symlink_member_refuse_even_with_matching_outer_hash(self) -> None:
        # Rebuild the policy/candidate fixture in a separate test below would be
        # expensive; exercise the exact archive parser with a matching policy row.
        from workbench_core.environment_fixture_gradle_import import _snapshot_archive

        with ZipFile(self.archive, "w", compression=ZIP_STORED) as bundle:
            bundle.writestr("gradle-9.6.1/bin/gradle", b"unique launcher payload")
        raw = self.archive.read_bytes().replace(b"unique launcher payload", b"broken launcher payload")
        self.archive.write_bytes(raw)
        gradle = {**self.owner.read_execution_policy()["gradle"],
                  "archive_sha256": "sha256:" + sha256(raw).hexdigest(),
                  "archive_size": len(raw)}
        with self.assertRaisesRegex(ReconstructionError, "safe complete ZIP"):
            _snapshot_archive(self.archive, gradle)
        link = ZipInfo("gradle-9.6.1/bin/gradle")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with ZipFile(self.archive, "w") as bundle:
            bundle.writestr(link, "target")
        gradle = {**gradle,
                  "archive_sha256": "sha256:" + sha256(self.archive.read_bytes()).hexdigest(),
                  "archive_size": self.archive.stat().st_size}
        with self.assertRaisesRegex(ReconstructionError, "safe complete ZIP"):
            _snapshot_archive(self.archive, gradle)

    def test_unsupported_filesystem_blocks_before_copy(self) -> None:
        with patch(
            "workbench_core.environment_fixture_gradle_import._qualified_filesystem",
            return_value=False,
        ):
            plan = self._plan(self.archive)
            self.assertEqual("blocked", plan["state"])
            with self.assertRaisesRegex(ReconstructionError, "blocked"):
                self._apply(plan, self.archive)
        self.assertFalse(Path(plan["target"]).exists())
