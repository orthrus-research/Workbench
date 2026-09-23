"""Core's generic retained-input publication, independent of domain execution."""

from pathlib import Path
import tempfile
import unittest

from workbench_core import check_storage as storage


class RetainedCheckStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "state"

    def test_domain_attempts_have_unique_private_core_managed_directories(self):
        first = storage.allocate_attempt(self.root, "material-check")
        second = storage.allocate_attempt(self.root, "material-check")
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root / ".workbench/check-attempts")
        self.assertEqual(first.stat().st_mode & 0o777, 0o700)

    def test_binary_and_json_publication_preserve_exact_data_without_overwrite(self):
        attempt = storage.allocate_attempt(self.root, "material-check")
        binary = attempt / "program.zip"
        storage.write_bytes(binary, b"exact archive bytes\0\xff")
        self.assertEqual(binary.read_bytes(), b"exact archive bytes\0\xff")
        self.assertEqual(binary.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(storage.CheckStorageError):
            storage.write_bytes(binary, b"replacement")
        record = attempt / "result.json"
        storage.write_json(record, {"state": "fixture-only"})
        self.assertEqual(storage.read_json(record), {"state": "fixture-only"})
        self.assertEqual(sorted(path.name for path in attempt.iterdir()), ["program.zip", "result.json"])

    def test_owner_can_retain_and_reopen_complete_evidence_without_a_byte_target(self):
        attempt = storage.allocate_attempt(self.root, "material-check")
        record = attempt / "result.json"
        value = {"trace": "native observation\n" * (2 * 1024**2)}
        storage.write_json(record, value, byte_limit=None)
        self.assertGreater(record.stat().st_size, 32 * 1024**2)
        self.assertEqual(value, storage.read_json(record, byte_limit=None))

    def test_invalid_allocation_and_nonbytes_do_not_publish(self):
        for name in ("../outside", "", "/absolute", "has spaces"):
            with self.assertRaises(storage.CheckStorageError):
                storage.allocate_attempt(self.root, name)
        attempt = storage.allocate_attempt(self.root, "check")
        with self.assertRaises(storage.CheckStorageError):
            storage.write_bytes(attempt / "source", "not bytes")
        self.assertFalse((attempt / "source").exists())

    def test_execution_observation_tracks_the_lease_and_refuses_unsafe_storage(self):
        attempt = storage.allocate_attempt(self.root, "check")
        self.assertFalse(storage.execution_active(attempt))
        with storage.execution_lock(attempt):
            self.assertTrue(storage.execution_active(attempt))
            with self.assertRaises(storage.CheckExecutionBusy):
                with storage.execution_lock(attempt):
                    self.fail("a second execution acquired the same lease")
        self.assertFalse(storage.execution_active(attempt))
        (attempt / "unsafe.lock").hardlink_to(attempt / "execution.lock")
        with self.assertRaisesRegex(storage.CheckStorageError, "independent file"):
            storage.execution_active(attempt)
