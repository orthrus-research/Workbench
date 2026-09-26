"""Core acquires exact bytes and verifies its historical cache on reuse."""

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from workbench_api.verified_artifacts import VerifiedArtifactError, acquire_verified_artifact
from workbench_core.host_services import install_local_host_services


class CoreVerifiedArtifactHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="artifact-port-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "locked input.jar"
        self.payload = b"synthetic locked artifact bytes"
        self.source.write_bytes(self.payload)
        self.state = self.root / "state"
        self.digest = sha256(self.payload).hexdigest()
        install_local_host_services()

    def acquire(self):
        return acquire_verified_artifact(
            url=self.source.as_uri(), expected_sha256=self.digest,
            expected_size=len(self.payload), state_root=self.state,
            label="synthetic installer",
        )

    def test_download_reopen_and_tamper_refusal(self):
        first = self.acquire()
        self.assertEqual("downloaded", first.outcome)
        self.assertEqual(self.state / "artifacts/sha256" / self.digest, first.path)
        self.assertEqual(self.payload, first.path.read_bytes())
        self.source.unlink()
        reopened = self.acquire()
        self.assertEqual("reused", reopened.outcome)
        self.assertEqual(first.path, reopened.path)
        first.path.write_bytes(b"x" * len(self.payload))
        with self.assertRaisesRegex(VerifiedArtifactError, "cached synthetic installer SHA-256 mismatch"):
            self.acquire()

    def test_rejected_input_does_not_publish_cache(self):
        with self.assertRaisesRegex(VerifiedArtifactError, "size mismatch"):
            acquire_verified_artifact(
                url=self.source.as_uri(), expected_sha256=self.digest,
                expected_size=len(self.payload) + 1, state_root=self.state,
                label="synthetic installer",
            )
        self.assertFalse((self.state / "artifacts/sha256" / self.digest).exists())

    def test_relative_state_root_is_refused(self):
        with self.assertRaisesRegex(VerifiedArtifactError, "state root must be absolute"):
            acquire_verified_artifact(
                url=self.source.as_uri(), expected_sha256=self.digest,
                expected_size=len(self.payload), state_root=Path("relative"),
                label="synthetic installer",
            )


if __name__ == "__main__":
    unittest.main()
