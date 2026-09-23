"""Native install observations never infer release trust from metadata."""
from importlib import metadata
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from workbench_core import environment_status


class Distribution:
    version = "0.1.3"
    files = ("workbench_core/environment_status.py",)

    def __init__(self, direct_url):
        self.direct_url = direct_url

    def locate_file(self, name):
        return Path(environment_status.__file__).parent.parent / name

    def read_text(self, name):
        return self.direct_url if name == "direct_url.json" else None


class InstalledProvenanceTests(unittest.TestCase):
    def observe(self, direct_url):
        with patch.object(environment_status.metadata, "distribution", return_value=Distribution(direct_url)):
            return environment_status._installed_provenance()

    def test_hash_is_observation_not_trust_and_private_url_is_not_reported(self):
        result = self.observe(json.dumps({"url": "file:///private/customer/core.whl", "archive_info": {"hashes": {"sha256": "a" * 64}}}))
        self.assertEqual("native-wheel", result["selected"])
        self.assertEqual("a" * 64, result["direct_archive"]["workbench_wheel_sha256"])
        self.assertFalse(result["artifact_verified"])
        self.assertNotIn("private", json.dumps(result))

    def test_missing_direct_url_is_supported_without_inventing_a_digest(self):
        result = self.observe(None)
        self.assertEqual("native-wheel", result["selected"])
        self.assertEqual({"state": "absent"}, result["direct_archive"])

    def test_ambiguous_or_invalid_metadata_is_rejected(self):
        for raw in ('{"archive_info":{},"archive_info":{}}', '[]', '{', '{"archive_info":{"hash":"sha256=abc"}}', '{"dir_info":7}'):
            with self.subTest(raw=raw):
                self.assertEqual("invalid", self.observe(raw)["direct_archive"]["state"])

    def test_distribution_absence_is_not_installation_evidence(self):
        with patch.object(environment_status.metadata, "distribution", side_effect=metadata.PackageNotFoundError("workbench-core")):
            self.assertEqual("absent", environment_status._installed_provenance()["selected"])
