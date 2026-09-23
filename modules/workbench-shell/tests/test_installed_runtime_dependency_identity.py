"""Installed service dependencies bind native metadata, never source Pixi."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_shell import runtime_dependency_identity as identity


class Distribution:
    def __init__(self, root, name, version="0.1.0", requires=()):
        self.root, self.version, self.requires = root, version, list(requires)
        self.metadata = {"Name": name, "Requires-Python": ">=3.12,<3.15"}
        self.record = "installed-record"

    def locate_file(self, path):
        return self.root / path

    def read_text(self, name):
        if name == "METADATA":
            return f"Name: {self.metadata['Name']}\nVersion: {self.version}\n"
        return self.record if name == "RECORD" else None


class InstalledDependencyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.prefix = Path(self.temporary.name)
        self.root = self.prefix / "workbench_resources"
        self.distributions = []
        for name, relative in (("workbench-api", "api"), ("workbench-core", "core"), ("workbench-crucible", "modules/crucible"), ("workbench-shell", "modules/workbench-shell")):
            path = self.root / relative / "pyproject.toml"
            path.parent.mkdir(parents=True)
            path.write_text(f'[project]\nname = "{name}"\nversion = "0.1.0"\n')
            self.distributions.append(Distribution(self.prefix, name, requires=("dependency==1.0.0",)))
        self.distributions.append(Distribution(self.prefix, "dependency", "1.0.0"))

    def build(self):
        with patch.object(identity.sys, "prefix", str(self.prefix)), patch.object(identity.metadata, "distributions", return_value=self.distributions):
            return identity.build_runtime_dependency_lock_manifest_v2(self.root, format_name="test-installed-dependencies")

    def test_installed_closure_does_not_need_or_claim_source_pixi(self):
        with patch.object(identity, "bind_pixi_inputs", side_effect=AssertionError("installed runtime must not bind Pixi")):
            manifest = self.build()
        contract = manifest["installed_runtime_contract"]
        self.assertNotIn("source_runtime_contract", manifest)
        self.assertEqual("installed-native-runtime", contract["execution_scope"])
        self.assertFalse(contract["source_pixi_qualification"])
        self.assertFalse(contract["release_qualified"])
        self.assertEqual(5, len(contract["distributions"]))

    def test_external_dependency_metadata_changes_identity(self):
        before = self.build()["id"]
        self.distributions[-1].record = "changed-installed-record"
        self.assertNotEqual(before, self.build()["id"])

    def test_unrelated_broken_optional_metadata_does_not_enter_service_closure(self):
        before = self.build()["id"]
        optional = Distribution(self.prefix, "workbench-unrelated-profile", "not-a-version", ("missing-package",))
        optional.record = None
        self.distributions.extend((optional, optional, Distribution(self.prefix, "")))
        self.assertEqual(before, self.build()["id"])

    def test_required_external_dependency_duplicates_and_direct_urls_fail_closed(self):
        self.distributions.append(self.distributions[-1])
        with self.assertRaisesRegex(identity.RuntimeDependencyIdentityV2Error, "duplicated"):
            self.build()
        self.distributions.pop()
        self.distributions[0].requires = ["dependency @ https://example.invalid/dependency.whl"]
        with self.assertRaisesRegex(identity.RuntimeDependencyIdentityV2Error, "direct URL"):
            self.build()

    def test_required_extras_extend_the_transitive_closure(self):
        self.distributions[0].requires = ["dependency[feature]==1.0.0"]
        self.distributions[-1].requires = ["extra-dependency==1.0.0; extra == 'feature'"]
        self.distributions.append(Distribution(self.prefix, "extra-dependency", "1.0.0"))
        rows = {row["name"]: row for row in self.build()["installed_runtime_contract"]["distributions"]}
        self.assertIn("extra-dependency", rows)
        self.assertEqual(["feature"], rows["dependency"]["requested_extras"])

    def test_missing_or_incompatible_dependency_fails_closed(self):
        self.distributions[-1].version = "2.0.0"
        with self.assertRaisesRegex(identity.RuntimeDependencyIdentityV2Error, "incompatible"):
            self.build()
        self.distributions.pop()
        with self.assertRaisesRegex(identity.RuntimeDependencyIdentityV2Error, "missing"):
            self.build()

    def test_mismatched_manifest_or_duplicate_distribution_fails_closed(self):
        self.distributions.append(self.distributions[0])
        with self.assertRaisesRegex(identity.RuntimeDependencyIdentityV2Error, "duplicated"):
            self.build()
        self.distributions.pop()
        self.distributions[0].version = "0.2.0"
        with self.assertRaisesRegex(identity.RuntimeDependencyIdentityV2Error, "differs from native"):
            self.build()
