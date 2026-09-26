"""Exact acquisition is exposed only through an installed Core host."""

from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_api import verified_artifacts as artifacts


class _Host:
    def acquire(self, **options):
        return artifacts.VerifiedArtifact(
            path=Path("/cache") / options["expected_sha256"],
            sha256=options["expected_sha256"],
            size=options["expected_size"], outcome="reused",
        )


class VerifiedArtifactPortTests(unittest.TestCase):
    def test_host_required_and_exact_lock_forwarded(self):
        with patch.object(artifacts, "_host", None):
            with self.assertRaisesRegex(artifacts.VerifiedArtifactError, "no verified artifact host"):
                artifacts.acquire_verified_artifact(
                    url="file:///input", expected_sha256="a" * 64,
                    expected_size=4, state_root=Path("/state"), label="test",
                )
            host = _Host()
            artifacts.bind_verified_artifact_host(host)
            result = artifacts.acquire_verified_artifact(
                url="file:///input", expected_sha256="a" * 64,
                expected_size=4, state_root=Path("/state"), label="test",
            )
            self.assertEqual(Path("/cache") / ("a" * 64), result.path)
            self.assertEqual("reused", result.outcome)
            artifacts.bind_verified_artifact_host(host)
            with self.assertRaisesRegex(artifacts.VerifiedArtifactError, "different"):
                artifacts.bind_verified_artifact_host(_Host())


if __name__ == "__main__":
    unittest.main()
