"""Built wheels must satisfy the same public boundary as source exports."""
from pathlib import Path
import base64
import csv
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from validate_native_artifacts import audit


class NativeArtifactTests(unittest.TestCase):
    def wheel(self, directory, *, extra=None, wrong_notice=False, tamper=False):
        prefix = "workbench_probe-0.1.0.dist-info"
        files = {
            prefix + "/METADATA": b"Metadata-Version: 2.4\nName: workbench-probe\nVersion: 0.1.0\nLicense-Expression: LGPL-3.0-only\n",
            prefix + "/licenses/LICENSE": (ROOT / "LICENSE").read_bytes(),
            prefix + "/licenses/NOTICE.md": b"wrong" if wrong_notice else (ROOT / "NOTICE.md").read_bytes(),
            "workbench_probe/__init__.py": b"VALUE = 1\n",
        }
        files.update(extra or {})
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        for name, raw in files.items():
            digest = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
            writer.writerow((name, digest, len(raw)))
        writer.writerow((prefix + "/RECORD", "", ""))
        files[prefix + "/RECORD"] = output.getvalue().encode("utf-8")
        if tamper:
            files["workbench_probe/__init__.py"] = b"VALUE = 2\n"
        path = directory / "workbench_probe-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        return path

    def test_complete_owned_wheel_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual([], audit(self.wheel(Path(temporary))))

    def test_private_files_and_retired_names_are_rejected(self):
        for name in ("AGENTS.md", ".codex/notes.md", "workbench_portable/runtime.py", "workbench_probe_v2/__init__.py", "../outside.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                self.assertTrue(audit(self.wheel(Path(temporary), extra={name: b"private"})))

    def test_notices_must_match_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIn("missing or altered NOTICE.md", audit(self.wheel(Path(temporary), wrong_notice=True)))

    def test_record_is_verified_not_merely_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIn("RECORD identity mismatch: workbench_probe/__init__.py", audit(self.wheel(Path(temporary), tamper=True)))
