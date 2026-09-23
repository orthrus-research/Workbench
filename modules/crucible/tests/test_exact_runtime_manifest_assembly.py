#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_sha256,
)


TOOL_PATH = MODULE_ROOT / "tools" / "assemble_exact_runtime_manifests.py"
SPEC = importlib.util.spec_from_file_location("exact_runtime_manifest_assembly", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ExactRuntimeManifestAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.server = self.root / "case" / "server"
        (self.server / "mods" / "nested").mkdir(parents=True)
        (self.server / "config" / "nested").mkdir(parents=True)
        (self.server / "mods" / "z.jar").write_bytes(b"z-mod")
        (self.server / "mods" / "nested" / "a.jar").write_bytes(b"a-mod")
        (self.server / "config" / "z.cfg").write_bytes(b"z-config\n")
        (self.server / "config" / "nested" / "a.cfg").write_bytes(b"a-config\n")
        (self.server / "server.properties").write_bytes(b"level-type=wb_observe\n")
        self.candidate_sha = digest(b"candidate")
        self.runtime_sha = digest(b"runtime")

    def test_builds_closed_canonical_manifests(self) -> None:
        installed = tool.build_installed_mod_set_manifest(
            self.server,
            candidate_lock_sha256=self.candidate_sha,
            runtime_class_source_sha256=self.runtime_sha,
        )
        self.assertEqual(tool.INSTALLED_MOD_SET_FORMAT, installed["format"])
        self.assertEqual(
            ["mods/nested/a.jar", "mods/z.jar"],
            [row["relative_path"] for row in installed["installed_artifacts"]],
        )
        self.assertEqual(
            digest(b"a-mod"), installed["installed_artifacts"][0]["sha256"]
        )

        configuration = tool.build_configuration_set_manifest(
            self.server,
            case_id="aa-1",
            route_order="forward",
            probe_enabled=True,
            probe_capture_id="capture-aa-1",
        )
        self.assertEqual(tool.CONFIGURATION_FORMAT, configuration["format"])
        self.assertEqual(
            ["config/nested/a.cfg", "config/z.cfg", "server.properties"],
            [row["relative_path"] for row in configuration["files"]],
        )
        self.assertEqual("lossless-fixture", configuration["runtime_settings"]["probe_mode"])

        output = self.root / "manifests" / "installed.json"
        reported = tool.write_new_manifest(output, installed)
        self.assertEqual(canonical_json_sha256(installed), reported)
        self.assertEqual(installed, json.loads(output.read_text(encoding="utf-8")))
        with self.assertRaisesRegex(CaptureValidationError, "already exists"):
            tool.write_new_manifest(output, installed)

    def test_disabled_probe_forbids_capture_identity(self) -> None:
        with self.assertRaisesRegex(CaptureValidationError, "forbids a capture ID"):
            tool.build_configuration_set_manifest(
                self.server,
                case_id="off-1",
                route_order="forward",
                probe_enabled=False,
                probe_capture_id="unexpected",
            )
        manifest = tool.build_configuration_set_manifest(
            self.server,
            case_id="off-1",
            route_order="reverse",
            probe_enabled=False,
            probe_capture_id=None,
        )
        settings = manifest["runtime_settings"]
        self.assertFalse(settings["probe_enabled"])
        self.assertIsNone(settings["probe_capture_id"])
        self.assertIsNone(settings["probe_mode"])
        self.assertIsNone(settings["probe_output_relative_path"])

    def test_rejects_symlinked_inventory_entry(self) -> None:
        target = self.root / "foreign.jar"
        target.write_bytes(b"foreign")
        link = self.server / "mods" / "foreign.jar"
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(CaptureValidationError, "contains a symlink"):
            tool.build_installed_mod_set_manifest(
                self.server,
                candidate_lock_sha256=self.candidate_sha,
                runtime_class_source_sha256=self.runtime_sha,
            )

    def test_rejects_invalid_identity_and_empty_mod_set(self) -> None:
        with self.assertRaisesRegex(CaptureValidationError, "lowercase SHA-256"):
            tool.build_installed_mod_set_manifest(
                self.server,
                candidate_lock_sha256="BAD",
                runtime_class_source_sha256=self.runtime_sha,
            )
        for path in tuple((self.server / "mods").rglob("*")):
            if path.is_file():
                path.unlink()
        with self.assertRaisesRegex(CaptureValidationError, "mods directory is empty"):
            tool.build_installed_mod_set_manifest(
                self.server,
                candidate_lock_sha256=self.candidate_sha,
                runtime_class_source_sha256=self.runtime_sha,
            )


if __name__ == "__main__":
    unittest.main()
