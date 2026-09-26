from __future__ import annotations

import json
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_strata_micro_region import (  # noqa: E402
    StrataViewerHandoffValidationError,
    parse_strata_viewer_handoff,
)


class StrataViewerHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".workbench").mkdir()
        self.viewer = self.root / "strata/tools/render-explorer"
        self.viewer.mkdir(parents=True)
        (self.viewer / "package.json").write_text("{}\n", encoding="utf-8")
        self.artifacts = self.root / ".workbench/evidence/sample.strataview.d"
        self.artifacts.mkdir(parents=True)
        self.manifest = self.artifacts / "manifest.json"
        self.manifest.write_text("{}\n", encoding="utf-8")
        self.handoff = self.root / ".workbench/evidence/viewer-handoff.json"
        self.write_handoff()

    def write_handoff(self, **changes: object) -> None:
        value = {
            "cwd": str(self.viewer),
            "externalArtifactRoot": str(self.artifacts),
            "manifest": str(self.manifest),
            "port": 5173,
            "url": "http://127.0.0.1:5173/?view=region&manifest=%2Fmanifest.json",
        }
        value.update(changes)
        self.handoff.parent.mkdir(parents=True, exist_ok=True)
        self.handoff.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def test_exact_handoff_becomes_a_local_server_command(self) -> None:
        parsed = parse_strata_viewer_handoff(
            self.handoff,
            workbench_root=self.root,
        )
        self.assertEqual(parsed["port"], 5173)
        self.assertEqual(
            parsed["command"],
            [
                "npm",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
            ],
        )

    def test_stale_or_nonlocal_handoffs_fail_closed(self) -> None:
        self.write_handoff(url="http://0.0.0.0:5173/?view=region&manifest=x")
        with self.assertRaisesRegex(
            StrataViewerHandoffValidationError,
            "127.0.0.1",
        ):
            parse_strata_viewer_handoff(self.handoff, workbench_root=self.root)

        self.write_handoff(manifest=str(self.root / "missing.json"))
        with self.assertRaises(FileNotFoundError):
            parse_strata_viewer_handoff(self.handoff, workbench_root=self.root)

    def test_selected_workspace_controls_handoff_custody(self) -> None:
        workspace = self.root / "selected-workspace"
        artifacts = workspace / ".workbench/evidence/sample.strataview.d"
        artifacts.mkdir(parents=True)
        manifest = artifacts / "manifest.json"
        manifest.write_text("{}\n", encoding="utf-8")
        handoff = workspace / ".workbench/evidence/viewer-handoff.json"
        handoff.write_text(json.dumps({
            "cwd": str(self.viewer),
            "externalArtifactRoot": str(artifacts),
            "manifest": str(manifest),
            "port": 5173,
            "url": "http://127.0.0.1:5173/?view=region&manifest=%2Fmanifest.json",
        }) + "\n", encoding="utf-8")
        self.assertEqual(str(handoff), parse_strata_viewer_handoff(
            handoff, workbench_root=workspace,
        )["handoff"])
        with self.assertRaisesRegex(StrataViewerHandoffValidationError, "must be under"):
            parse_strata_viewer_handoff(handoff, workbench_root=self.root)

        tool_path = ROOT / "modules/crucible/tools/serve_strata_observation.py"
        spec = importlib.util.spec_from_file_location("serve_strata_observation_test", tool_path)
        assert spec and spec.loader
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        output = io.StringIO()
        with patch.object(sys, "argv", ["serve_strata_observation.py", str(handoff),
                                        "--workspace", str(workspace), "--check"]), \
                patch.object(tool.shutil, "which", return_value="/usr/bin/npm"), \
                redirect_stdout(output):
            self.assertEqual(0, tool.main())
        self.assertIn("handoff check passed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
