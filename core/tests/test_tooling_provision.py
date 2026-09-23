"""Focused custody and recovery tests for managed developer tools."""

from pathlib import Path
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import check_storage, tooling_provision as tooling


class ToolingProvisionTests(unittest.TestCase):
    def test_bundled_packwiz_source_is_exact_and_contains_license_and_vendor(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = tooling._bundle(Path(temporary) / tooling.SOURCE_BUNDLE)
            self.assertEqual((tooling.SOURCE_SHA256, tooling.SOURCE_SIZE),
                             tooling.sha256_file(bundle))
            target = Path(temporary) / "source"
            tooling._extract_tar(bundle, target)
            self.assertTrue((target / "LICENSE").is_file())
            self.assertTrue((target / "vendor/modules.txt").is_file())
            self.assertTrue((target / "vendor/gopkg.in/yaml.v3/LICENSE").is_file())
            self.assertFalse(any(
                any(part.startswith(".") for part in path.relative_to(target).parts)
                for path in target.rglob("*")
            ))

    def test_exact_seed_provisions_prism_and_retains_invalid_tree_for_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed"
            seed.mkdir()
            archive = seed / "prism-test.zip"
            with ZipFile(archive, "w") as target:
                target.writestr("PrismLauncher", b"#!/bin/sh\nexit 0\n")
            digest, size = tooling.sha256_file(archive)
            policy = {
                "filename": archive.name,
                "url": "https://github.com/PrismLauncher/PrismLauncher/releases/download/test/prism-test.zip",
                "sha256": digest,
                "size": size,
                "executable": "PrismLauncher",
                "archive": "zip",
            }
            policies = {
                "linux-x64": {**tooling.ASSETS["linux-x64"], "prism": policy},
                "windows-x64": tooling.ASSETS["windows-x64"],
            }
            state = root / "state"
            check_storage.initialize(state)
            (tooling._managed_root(state) / "linux-x64").mkdir(parents=True)
            with patch.object(tooling, "ASSETS", policies):
                tooling._prepare_prism(state, "linux-x64", seed)
                ready = tooling.inspect_tools(state, key="linux-x64")
                self.assertEqual("ready", ready["tools"]["prism"]["state"])
                executable = Path(ready["tools"]["prism"]["executable"])
                executable.write_bytes(b"changed")
                cached = (tooling._managed_root(state) / "artifacts/sha256" / digest)
                cached.write_bytes(b"corrupt cached archive")
                invalid = tooling.inspect_tools(state, key="linux-x64")
                self.assertEqual("invalid", invalid["tools"]["prism"]["state"])
                tooling._prepare_prism(state, "linux-x64", seed)
                repaired = tooling.inspect_tools(state, key="linux-x64")
                self.assertEqual("ready", repaired["tools"]["prism"]["state"])
                retained = list((tooling._managed_root(state) / "linux-x64" /
                                 "quarantine").glob("prism-*/content/PrismLauncher"))
                self.assertEqual(1, len(retained))
                self.assertEqual(b"changed", retained[0].read_bytes())
                archived = list((tooling._managed_root(state) / "artifacts" /
                                 "quarantine").glob(digest + "-*"))
                self.assertEqual(1, len(archived))
                self.assertEqual(b"corrupt cached archive", archived[0].read_bytes())

    def test_seed_selection_refuses_wrong_bytes_without_contacting_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seeds"
            seed.mkdir()
            (seed / tooling.ASSETS["linux-x64"]["prism"]["filename"]).write_bytes(b"wrong")
            with self.assertRaisesRegex(tooling.ToolingProvisionError, "pinned bytes"):
                tooling._plan(root / "state", "linux-x64", seed, None)

    def test_plan_identity_changes_when_retained_tool_state_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            before = tooling._plan(state, "linux-x64", None, None)
            destination = tooling._managed_root(state) / "linux-x64" / "prism"
            destination.mkdir(parents=True)
            after = tooling._plan(state, "linux-x64", None, None)
            self.assertNotEqual(before["plan_id"], after["plan_id"])
            self.assertEqual("invalid", after["check"]["tools"]["prism"]["state"])

    def test_apply_rejects_unreviewed_plan_without_writing_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = tooling.main(
                    ["--apply", "unreviewed", "--state-root", str(state)]
                )
            self.assertEqual(2, status)
            self.assertIn("plan changed", error.getvalue())
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
