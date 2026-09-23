"""Private executable custody binds staged bytes, not a mutable source path."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.executable_custody import (  # noqa: E402
    ExecutableCustodyError,
    stage_executable,
)


class ExecutableCustodyTests(unittest.TestCase):
    def test_source_replacement_does_not_change_the_private_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "tool"
            source.write_bytes(b"verified tool bytes")
            source.chmod(0o755)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with stage_executable(
                source,
                logical_name="tool",
                maximum=1024,
                expected_sha256=digest,
            ) as staged:
                source.write_bytes(b"replacement PATH bytes")
                staged.require_unchanged()
                self.assertEqual(digest, staged.sha256)
                self.assertEqual(b"verified tool bytes", staged.path.read_bytes())

    def test_wrong_required_digest_fails_before_custody_is_yielded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "tool"
            source.write_bytes(b"verified tool bytes")
            source.chmod(0o755)
            with self.assertRaisesRegex(
                ExecutableCustodyError,
                "digest differs",
            ):
                with stage_executable(
                    source,
                    logical_name="tool",
                    maximum=1024,
                    expected_sha256="0" * 64,
                ):
                    self.fail("wrong executable digest unexpectedly entered custody")


if __name__ == "__main__":
    unittest.main()
