"""Blueprint publication uses a supplied host instead of POSIX directory opens."""
from pathlib import Path
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
