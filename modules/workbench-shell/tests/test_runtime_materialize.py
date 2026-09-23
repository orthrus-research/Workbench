"""Focused tests for copying the tracked Packwiz workspace boundary."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from workbench_project_intelligence.working_tree import WorkingTreeError, copy_tracked_workspace
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell import PackwizMaterializationError  # noqa: E402
from workbench_shell.runtime_materialize import (  # noqa: E402
    _selected_java,
)


def _run(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def _workspace(root: Path) -> Path:
    workspace = root / "pack"
    workspace.mkdir()
    (workspace / "pack.toml").write_text("name = \"fixture\"\n", encoding="utf-8")
    _run(workspace, "init", "--quiet")
    _run(workspace, "config", "user.name", "Workbench Test")
    _run(workspace, "config", "user.email", "workbench@example.invalid")
    _run(workspace, "add", "pack.toml")
    _run(workspace, "commit", "--quiet", "-m", "fixture")
    (workspace / "untracked.txt").write_text("excluded\n", encoding="utf-8")
    return workspace


class RuntimeMaterializeTest(unittest.TestCase):
    def test_managed_java_requires_the_current_receipt_envelope(self) -> None:
        with self.assertRaisesRegex(
            PackwizMaterializationError,
            "current V2 format",
        ):
            _selected_java({"source": "managed", "receipt": {}})

    def test_managed_java_accepts_the_current_receipt_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "jdk/bin/java"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"java")
            selected, identity = _selected_java({
                "format": "workbench-java-runtime-result-v2",
                "schema_version": 2,
                "source": "managed",
                "receipt": {
                    "format": "workbench-java-runtime-receipt-v2",
                    "schema_version": 2,
                    "runtime_id": "sha256:" + "1" * 64,
                    "policy": {
                        "runtime_identity": "eclipse-temurin-25.0.4+7"
                    },
                    "probe": {
                        "runtime_version": "25.0.4+7-LTS",
                        "vendor": "Eclipse Adoptium",
                    },
                    "target": {"java_uri": executable.as_uri()},
                },
            })

            self.assertEqual(executable.resolve(), selected)
            self.assertEqual("managed", identity["source"])

    def test_tracked_copy_honors_setup_selected_git_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = _workspace(root)
            selected_git = shutil.which("git")
            self.assertIsNotNone(selected_git)
            destination = root / "copy"

            with patch.dict(
                os.environ,
                {
                    "PATH": "",
                    "WORKBENCH_GIT_EXECUTABLE": str(selected_git),
                },
            ):
                source, exclusions = copy_tracked_workspace(workspace, destination)

            self.assertEqual(source["file_count"], 1)
            self.assertEqual(exclusions["file_count"], 1)
            self.assertTrue((destination / "pack.toml").is_file())
            self.assertFalse((destination / "untracked.txt").exists())

    def test_tracked_copy_reports_missing_setup_selected_git_precisely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = _workspace(root)
            missing = root / "tools/missing-git"
            destination = root / "copy"

            with patch.dict(
                os.environ,
                {
                    "PATH": "",
                    "WORKBENCH_GIT_EXECUTABLE": str(missing),
                },
            ):
                with self.assertRaisesRegex(
                    WorkingTreeError,
                    rf"configured Git executable is unavailable: {missing}",
                ):
                    copy_tracked_workspace(workspace, destination)

            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
