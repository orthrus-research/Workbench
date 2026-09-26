"""Retained Gradle ZIP extraction has exact Core custody and restart behavior."""

from __future__ import annotations

from hashlib import sha256
import json
from multiprocessing import get_context
import os
from pathlib import Path
import stat
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from workbench_core.environment_fixture_execution_policy import review_fixture_execution_policy
from workbench_core.environment_fixture_gradle_extract import (
    _manifest, apply_fixture_gradle_extraction, plan_fixture_gradle_extraction,
    reopen_fixture_gradle_extraction,
)
from workbench_core.environment_fixture_gradle_import import (
    _zip_index, apply_fixture_gradle_import, plan_fixture_gradle_import,
)
from workbench_core.environment_fixture_import import apply_fixture_import, plan_fixture_import
from workbench_core.environment_input_candidates import build_input_candidate
from workbench_core.environment_reconstruction import ReconstructionError
from workbench_core.output_routing import _private_directory
from workbench_core.storage.registered import CoreDurableResources

import test_environment_input_candidates as candidate_tests
from test_environment_fixture_execution_policy import _PortableFixtureOwner
from test_environment_reconstruction import _environment


@skipIf(not sys.platform.startswith("linux"), "exact Gradle extraction is a Linux/WSL slice")
class EnvironmentFixtureGradleExtractionTests(TestCase):
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
        self.candidate = build_input_candidate(
            self.share, wheels=(self.wheel,), profile_owner_id="cleanroom",
        )
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
        archive_result = apply_fixture_gradle_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id, expected_plan_id=archive_plan["plan_id"],
            archive=self.archive, environment=self.environment,
        )
        self.archive_result_id = archive_result["resource"]["resource_id"]

    def _plan(self):
        return plan_fixture_gradle_extraction(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            gradle_result_resource_id=self.archive_result_id,
            environment=self.environment,
        )

    def _apply(self, plan):
        return apply_fixture_gradle_extraction(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            gradle_result_resource_id=self.archive_result_id,
            expected_plan_id=plan["plan_id"], environment=self.environment,
        )

    def _reopen(self, result):
        return reopen_fixture_gradle_extraction(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=self.fixture_result_id,
            expected_review_id=self.review_id,
            gradle_result_resource_id=self.archive_result_id,
            result_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_extract_reopen_and_reuse_without_original_zip(self) -> None:
        plan = self._plan()
        self.assertEqual("extract", plan["action"])
        result = self._apply(plan)
        self.assertEqual("extracted", result["outcome"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        launcher = Path(result["launcher_path"])
        self.assertEqual(b"#!/bin/sh\nexit 0\n", launcher.read_bytes())
        self.assertTrue(launcher.stat().st_mode & 0o111)
        self.assertEqual(result["tree_id"], self._reopen(result)["tree_id"])
        self.archive.unlink()
        reused = self._plan()
        self.assertEqual("reuse", reused["action"])
        second = self._apply(reused)
        self.assertEqual(result["tree_id"], second["tree_id"])
        self._reopen(second)

    def test_changed_extracted_file_and_target_collision_refuse(self) -> None:
        plan = self._plan()
        target = Path(plan["target"])
        _private_directory(target.parent)
        target.mkdir(mode=0o700)
        with self.assertRaisesRegex(ReconstructionError, "outside Core custody"):
            self._plan()
        target.rmdir()
        result = self._apply(plan)
        Path(result["launcher_path"]).write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)
        with self.assertRaises(ReconstructionError):
            self._plan()

    def test_changed_retained_zip_refuses_before_extraction(self) -> None:
        plan = self._plan()
        retained = self.root / "user/state/environment-inputs/gradle" / self.review_id.rsplit(":", 1)[-1] / "snapshot" / self.archive.name
        retained.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())

    def test_unsafe_zip_paths_and_member_types_refuse(self) -> None:
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o700)
        archive = unsafe / self.archive.name
        with ZipFile(archive, "w") as bundle:
            bundle.writestr("gradle-9.6.1/../escape", b"escape")
        digest = "sha256:" + sha256(archive.read_bytes()).hexdigest()
        gradle = {"version": "9.6.1", "archive_root": "gradle-9.6.1",
                  "archive_size": archive.stat().st_size, "archive_sha256": digest}
        dummy_index = {"sha256": digest, "size": archive.stat().st_size,
                       "member_count": 1, "expanded_bytes": 6,
                       "member_index_sha256": "sha256:" + "0" * 64}
        with self.assertRaises(ReconstructionError):
            _manifest(archive, gradle, dummy_index)

        with ZipFile(archive, "w") as bundle:
            launcher = ZipInfo("gradle-9.6.1/bin/gradle")
            launcher.create_system = 3
            launcher.external_attr = (stat.S_IFDIR | 0o755) << 16
            bundle.writestr(launcher, b"not a directory")
        gradle["archive_size"] = archive.stat().st_size
        gradle["archive_sha256"] = "sha256:" + sha256(archive.read_bytes()).hexdigest()
        with archive.open("rb") as source:
            index = _zip_index(source, gradle["archive_root"])
        with self.assertRaisesRegex(ReconstructionError, "type disagrees"):
            _manifest(archive, gradle, {
                "sha256": gradle["archive_sha256"], "size": gradle["archive_size"], **index,
            })

    def test_extracted_symlink_refuses_reopen(self) -> None:
        result = self._apply(self._plan())
        launcher = Path(result["launcher_path"])
        launcher.unlink()
        launcher.symlink_to(self.archive)
        with self.assertRaises(ReconstructionError):
            self._reopen(result)

    def test_partial_stage_and_result_interruption_recover(self) -> None:
        plan = self._plan()
        from workbench_core import environment_fixture_gradle_extract as extractor
        original = extractor._extract

        def after_copy(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("interrupted before publication")

        with patch.object(extractor, "_extract", after_copy):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())
        Path(plan["target"]).mkdir(mode=0o700)
        with self.assertRaisesRegex(ReconstructionError, "outside Core custody"):
            self._plan()
        Path(plan["target"]).rmdir()
        retry = self._plan()
        self.assertEqual("extract", retry["action"])
        original_publish = CoreDurableResources.publish_bytes

        def fail_result(service, role, name, data, **kwargs):
            if name == "environment-fixture-gradle-extraction.json":
                raise RuntimeError("interrupted after tree commit")
            return original_publish(service, role, name, data, **kwargs)

        with patch.object(CoreDurableResources, "publish_bytes", fail_result):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(retry)
        reused = self._plan()
        self.assertEqual("reuse", reused["action"])
        result = self._apply(reused)
        self.assertEqual("reused", result["outcome"])
        self._reopen(result)

    def test_hard_exit_after_rename_reconciles_exact_tree(self) -> None:
        plan = self._plan()

        def interrupted() -> None:
            from workbench_core.storage.tree_catalog import TreeCatalog
            original = TreeCatalog._write

            def exit_commit(catalog, section, *args, **kwargs):
                if section == "commits":
                    os._exit(74)
                return original(catalog, section, *args, **kwargs)

            with patch.object(TreeCatalog, "_write", exit_commit):
                self._apply(plan)

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(74, process.exitcode)
        restarted = self._plan()
        self.assertEqual("reconcile", restarted["action"])
        result = self._apply(restarted)
        self.assertEqual("reconciled", result["outcome"])
        self._reopen(result)

    def test_unsupported_filesystem_blocks_without_extraction(self) -> None:
        with patch(
            "workbench_core.environment_fixture_gradle_extract._qualified_filesystem",
            return_value=False,
        ):
            plan = self._plan()
            self.assertEqual("blocked", plan["state"])
            with self.assertRaisesRegex(ReconstructionError, "blocked"):
                self._apply(plan)
        self.assertFalse(Path(plan["target"]).exists())
