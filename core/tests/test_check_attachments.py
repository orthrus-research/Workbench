"""Attachment custody/build contract, independently of any game profile."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_core import check_attachments as attachments
from workbench_core.check_storage import CheckStorageError


class CheckAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.java = self.root / "jdk/bin/java"
        self.java.parent.mkdir(parents=True)
        self.java.write_bytes(b"java")
        self.java.with_name("javac").write_bytes(b"compiler")
        self.spec = {"sources": {"Agent.java": b"source", "Bridge.java": b"bridge"}, "main_class": "example.Agent",
                     "bootstrap_classes": ["example.Bridge"], "configuration": b"nonce=one\n", "policy": {"id": "fixture"}}

    def build(self, name):
        def compiler(argv, **kwargs):
            self.assertNotIn("JAVA_TOOL_OPTIONS", kwargs["env"])
            self.assertIn("-proc:none", argv)
            output = Path(argv[argv.index("-d") + 1]) / "example"
            output.mkdir(exist_ok=True)
            (output / "Agent.class").write_bytes(b"synthetic class bytes")
            (output / "Bridge.class").write_bytes(b"synthetic bridge bytes")
            return type("Result", (), {"returncode": 0})()
        with patch("workbench_core.check_attachments.subprocess.run", side_effect=compiler):
            return attachments.build(self.root / name, self.java, self.spec)

    def test_deterministic_artifacts_separate_bootstrap_bridge_and_configuration(self):
        first = self.build("first")
        self.spec["configuration"] = b"nonce=two\n"
        second = self.build("second")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(attachments.comparison_identity(first), attachments.comparison_identity(second))
        with zipfile.ZipFile(self.root / "first/payload" / attachments.JAR) as jar:
            self.assertEqual(set(jar.namelist()), {"META-INF/MANIFEST.MF", "example/Agent.class"})
            self.assertIn(b"Boot-Class-Path: bridge.jar", jar.read("META-INF/MANIFEST.MF"))
        with zipfile.ZipFile(self.root / "first/payload" / attachments.BRIDGE) as jar:
            self.assertEqual(jar.namelist(), ["example/Bridge.class"])

    def test_materialization_and_launch_recheck_all_artifacts(self):
        value = self.build("build")
        runtime = self.root / "runtime"; runtime.mkdir()
        attachments.materialize(runtime, self.root / "build", value)
        self.assertEqual(attachments.arguments(runtime, value), ["-javaagent:workbench-check-attachment/observer.jar=workbench-check-attachment/observer.properties"])
        (runtime / attachments.BRIDGE).write_bytes(b"changed")
        with self.assertRaisesRegex(CheckStorageError, "changed before launch"):
            attachments.arguments(runtime, value)

    def test_retained_tampering_and_reserved_directory_are_rejected(self):
        value = self.build("build")
        runtime = self.root / "runtime"; runtime.mkdir()
        (runtime / attachments.DIRECTORY).symlink_to(self.root / "build/payload", target_is_directory=True)
        with self.assertRaisesRegex(CheckStorageError, "already exists"):
            attachments.materialize(runtime, self.root / "build", value)
        (runtime / attachments.DIRECTORY).unlink()
        (self.root / "build/payload" / attachments.CONFIG).write_bytes(b"changed")
        with self.assertRaisesRegex(CheckStorageError, "bytes changed"):
            attachments.materialize(runtime, self.root / "build", value)

    def test_unsealed_arbitrary_launch_options_and_unsafe_source_paths_reject(self):
        value = self.build("build")
        forged = deepcopy(value); forged["arguments"] = ["-Danything=true"]
        with self.assertRaises(CheckStorageError): attachments.validate(forged)
        self.spec["sources"] = {"../Outside.java": b"source"}
        with self.assertRaises(CheckStorageError): attachments.build(self.root / "unsafe", self.java, self.spec)

    def test_snapshot_only_has_no_attachment_or_launch_change(self):
        self.assertIsNone(attachments.build(self.root / "none", self.java, None))
        self.assertEqual(attachments.arguments(self.root, None), [])
        self.assertFalse((self.root / "none").exists())
