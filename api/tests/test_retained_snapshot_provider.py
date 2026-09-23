"""Installed reader selection must respect ownership and host admission."""
from contextlib import nullcontext
from importlib import metadata
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from workbench_api.modules import ModuleError
from workbench_api.profiles import profile_scope
from workbench_api.retained_snapshots import RetainedSnapshotProvider, retained_snapshot_provider


class RetainedSnapshotProviderTests(unittest.TestCase):
    def setUp(self):
        self.opened = Mock(return_value=nullcontext("retained reader"))
        self.factory = Mock(return_value=RetainedSnapshotProvider(self.opened))
        self.entry = SimpleNamespace(
            dist=SimpleNamespace(metadata={"Name": "fixture-reader"}, version="1.2.0"),
            load=Mock(return_value=self.factory))
        entries = patch.object(metadata, "entry_points", return_value=(self.entry,))
        version = patch.object(metadata, "version", return_value="1.2.0")
        self.entries, self.version = entries.start(), version.start()
        self.addCleanup(entries.stop)
        self.addCleanup(version.stop)

    def select(self):
        return retained_snapshot_provider("fixture", "fixture-reader>=1,<2")

    def test_explicit_provider_defers_opening_and_preserves_reader_arguments(self):
        provider = self.select()
        self.opened.assert_not_called()
        self.entries.assert_called_once_with(group="workbench.retained_snapshot_providers", name="fixture")
        admission = object()
        with provider.open_snapshot("exact-attempt", owner_id="owner", admit=admission) as reader:
            self.assertEqual("retained reader", reader)
        self.opened.assert_called_once_with("exact-attempt", owner_id="owner", admit=admission)

    def test_missing_and_duplicate_providers_refuse_without_loading(self):
        for entries in ((), (self.entry, self.entry)):
            self.entries.return_value = entries
            with self.subTest(entries=len(entries)), self.assertRaisesRegex(ModuleError, "exactly one"):
                self.select()
        self.entry.load.assert_not_called()

    def test_distribution_and_version_must_match_admission(self):
        for dist in (None, SimpleNamespace(metadata={"Name": "other"}, version="1.2.0"),
                     SimpleNamespace(metadata={"Name": "fixture-reader"}, version="1.3.0")):
            self.entry.dist = dist
            with self.subTest(dist=dist), self.assertRaisesRegex(ModuleError, "admitted distribution"):
                self.select()
        self.entry.load.assert_not_called()

    def test_missing_and_incompatible_installations_do_not_load_provider(self):
        self.version.side_effect = metadata.PackageNotFoundError("fixture-reader")
        with self.assertRaisesRegex(ModuleError, "not installed"):
            self.select()
        self.version.side_effect = None
        self.version.return_value = "2.0.0"
        with self.assertRaisesRegex(ModuleError, "incompatible"):
            self.select()
        self.entry.load.assert_not_called()

    def test_disabled_provider_is_rechecked_after_successful_load(self):
        self.select()
        self.entry.load.reset_mock()
        with profile_scope(unavailable_distributions=("fixture-reader",)):
            with self.assertRaisesRegex(ModuleError, "disabled or unavailable"):
                self.select()
        self.entry.load.assert_not_called()
        self.select()

    def test_incompatible_ports_and_load_failures_refuse(self):
        for value in (object(), RetainedSnapshotProvider(self.opened, api_version=2),
                      RetainedSnapshotProvider(self.opened, api_version=True),
                      RetainedSnapshotProvider(None)):
            self.factory.return_value = value
            with self.subTest(value=value), self.assertRaisesRegex(ModuleError, "incompatible API"):
                self.select()
        self.entry.load.side_effect = ImportError("absent implementation")
        with self.assertRaisesRegex(ModuleError, "could not be loaded"):
            self.select()


if __name__ == "__main__":
    unittest.main()
