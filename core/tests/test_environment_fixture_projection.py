"""Retained fixture source is projected under Core without running Gradle."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch

from workbench_core.environment_fixture_projection import (
    apply_fixture_projection, plan_fixture_projection, reopen_fixture_projection,
)
from workbench_core.environment_reconstruction import ReconstructionError

import test_environment_fixture_java_binding as binding_tests


def portable_test_owner(owner, root: Path) -> None:
    """Align the older synthetic owner with the retained V1 projection path."""

    old_root = owner.root
    owner.root = old_root.with_name("generic-mod-daily-loop")
    old_root.rename(owner.root)
    old_lock = owner.root / "fixture-lock.json"
    owner.lock_path = owner.root / "fixture-lock-v1.json"
    old_lock.rename(owner.lock_path)
    for row in owner.inputs:
        if row["path"].is_relative_to(old_root):
            row["path"] = owner.lock_path
            row["display_path"] = owner.lock_path.relative_to(root).as_posix()


@skipIf(not sys.platform.startswith("linux"), "fixture projection uses Linux/WSL custody")
class EnvironmentFixtureProjectionTests(TestCase):
    setUp = binding_tests.EnvironmentFixtureJavaBindingTests.setUp
    _refresh = binding_tests.EnvironmentFixtureJavaBindingTests._refresh
    _retain_fixture_and_gradle = binding_tests.EnvironmentFixtureJavaBindingTests._retain_fixture_and_gradle

    def _ready(self) -> None:
        portable_test_owner(self.owner, self.root)
        self._refresh()
        self._retain_fixture_and_gradle()
        rules = {
            "generated_parts": [".gradle", "__pycache__", "build", "out"],
            "generated_suffixes": [".class", ".jar", ".pyc"],
            "generated_roots": [
                ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop",
            ],
        }
        for name, value in (
            ("workbench_core.environment_fixture_projection.profile_extension_identity",
             self.candidate["profile_fixture"]["owner_code"]),
            ("workbench_core.environment_fixture_projection.require_profile_extension",
             type("ProjectionOwner", (), {
                 "portable_projection_rules": lambda self, *, policy: rules,
             })()),
            ("workbench_core.environment_fixture_projection._qualified_filesystem", True),
        ):
            replacement = patch(name, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def _kwargs(self) -> dict:
        return {
            "workspace": self.workspace,
            "fixture_result_resource_id": self.fixture_result_id,
            "expected_review_id": self.review_id,
            "environment": self.environment,
        }

    def _plan(self) -> dict:
        return plan_fixture_projection(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )

    def _apply(self, plan: dict) -> dict:
        return apply_fixture_projection(
            self.suite, self.share, self.candidate,
            expected_plan_id=plan["plan_id"], **self._kwargs(),
        )

    def _reopen(self, result: dict) -> dict:
        return reopen_fixture_projection(
            self.suite, self.share, self.candidate,
            result_resource_id=result["resource"]["resource_id"], **self._kwargs(),
        )

    def test_projected_source_reopens_without_original_owner_sources(self) -> None:
        self._ready()
        plan = self._plan()
        self.assertEqual("ready", plan["state"])
        result = self._apply(plan)
        self.assertEqual("source-projected-unexecuted", result["state"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        project = Path(result["project"])
        self.assertEqual(
            (self.owner.root / "src/main.txt").read_bytes(),
            (project / "src/main.txt").read_bytes(),
        )
        self.archive.unlink()
        for row in self.owner.inputs:
            row["path"].unlink()
        self.assertEqual(result["projection_id"], self._reopen(result)["projection_id"])

    def test_source_change_and_target_collision_refuse(self) -> None:
        self._ready()
        plan = self._plan()
        target = Path(plan["target"])
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            self._apply(plan)
        target.unlink()
        result = self._apply(plan)
        (Path(result["project"]) / "src/main.txt").write_bytes(b"changed")
        with self.assertRaisesRegex(ReconstructionError, "projection cannot reopen"):
            self._reopen(result)

    def test_interrupted_publication_reuses_exact_projection(self) -> None:
        self._ready()
        plan = self._plan()
        with patch(
            "workbench_core.environment_fixture_projection._projection_binding",
            side_effect=RuntimeError("interrupted after Core publication"),
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan)
        result = self._apply(plan)
        self.assertEqual(result["projection_id"], self._reopen(result)["projection_id"])

    def test_unqualified_wsl_filesystem_is_blocked_before_projection(self) -> None:
        self._ready()
        with patch(
            "workbench_core.environment_fixture_projection._qualified_filesystem",
            return_value=False,
        ):
            plan = self._plan()
            self.assertEqual("blocked", plan["state"])
            self.assertIn("filesystem", plan["blockers"][0])
            self.assertFalse(Path(plan["target"]).exists())
            with self.assertRaisesRegex(ReconstructionError, "blocked"):
                self._apply(plan)
