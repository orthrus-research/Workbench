"""Blueprint publication uses a supplied host instead of POSIX directory opens."""
from pathlib import Path
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.host_filesystem import bind_host_filesystem
from workbench_core import durable_records, host_filesystem
from workbench_blueprints import application_transaction, fresh_project


class PublicationFilesystemTests(unittest.TestCase):
    def setUp(self):
        bind_host_filesystem(host_filesystem)

    def test_directory_barrier_follows_exact_replacement(self):
        for owner, barrier_module, barrier_name in (
            (fresh_project, fresh_project, 'fsync_directory'),
            (application_transaction, durable_records, 'fsync_directory'),
        ):
            with self.subTest(owner=owner.__name__), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'record.json'
                calls = []
                def barrier(path):
                    calls.append((path, target.read_bytes() if target.exists() else None))
                with patch.object(barrier_module, barrier_name, side_effect=barrier):
                    owner._atomic_replace(target, b'published')
                self.assertEqual((target.parent, b'published'), calls[-1])
                self.assertEqual(b'published', target.read_bytes())
                self.assertFalse(any(path.suffix == '.tmp' for path in target.parent.iterdir()))

    def test_host_barrier_failure_is_never_silently_accepted(self):
        for owner, barrier_module, barrier_name in (
            (fresh_project, fresh_project, 'fsync_directory'),
            (application_transaction, durable_records, 'fsync_directory'),
        ):
            with self.subTest(owner=owner.__name__), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'record.json'
                original = getattr(barrier_module, barrier_name)
                def barrier(path):
                    if target.exists():
                        raise OSError('host barrier failed')
                    original(path)
                with patch.object(barrier_module, barrier_name, side_effect=barrier):
                    with self.assertRaisesRegex(OSError, 'host barrier failed'):
                        owner._atomic_replace(target, b'published')
                self.assertEqual(b'published', target.read_bytes())
                self.assertFalse(any(path.suffix == '.tmp' for path in target.parent.iterdir()))

    def test_core_record_replacement_allows_a_shorter_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'record.json'
            target.write_bytes(b'older, longer retained journal')
            target.chmod(0o600)
            application_transaction._atomic_replace(target, b'new')
            self.assertEqual(b'new', target.read_bytes())

    def test_core_upgrades_historical_nonprivate_transaction_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / 'transaction-state'
            parent.mkdir()
            parent.chmod(0o755)
            target = parent / 'active-transaction.json'
            application_transaction._atomic_new(target, b'prepared')
            self.assertEqual(0o700, stat.S_IMODE(parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
            application_transaction._atomic_replace(target, b'committed')
            self.assertEqual(b'committed', target.read_bytes())

    def test_m2_lock_keeps_v1_bytes_and_old_exclusion_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / 'active-transaction.lock'
            held = application_transaction._acquire_transaction_lock(lock, 'plan:test')
            self.assertIsNotNone(held)
            lease, token, _digest = held
            self.assertEqual({
                'binding': 'plan:test',
                'format': 'workbench-blueprints-m2-transaction-lock-v1',
                'pid': os.getpid(),
                'token': token,
            }, json.loads(lock.read_bytes()))
            self.assertIsNone(application_transaction._acquire_transaction_lock(lock, 'plan:test'))
            application_transaction._release_transaction_lock(lease)
            self.assertFalse(lock.exists())

    def test_m2_cleanup_refuses_a_second_link_to_a_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / 'active-transaction.json'
            alternate = Path(temporary) / 'alternate.json'
            journal.write_bytes(b'{"phase":"prepared"}\n')
            journal.chmod(0o600)
            try:
                os.link(journal, alternate)
            except OSError as exc:
                self.skipTest(f'host cannot create hardlinks: {exc}')
            with self.assertRaisesRegex(
                application_transaction.ApplicationTransactionError,
                'cannot remove active transaction journal',
            ):
                application_transaction._remove_transaction_record_if_present(
                    journal, 'active transaction journal',
                )
            self.assertTrue(journal.is_file())
            self.assertEqual(journal.read_bytes(), alternate.read_bytes())
