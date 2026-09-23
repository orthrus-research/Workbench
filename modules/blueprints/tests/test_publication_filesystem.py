"""Blueprint publication uses a supplied host instead of POSIX directory opens."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_blueprints import application_transaction, fresh_project


class PublicationFilesystemTests(unittest.TestCase):
    def test_directory_barrier_follows_exact_replacement(self):
        for owner, barrier_name in ((fresh_project, 'fsync_directory'),
                                    (application_transaction, '_fsync_directory')):
            with self.subTest(owner=owner.__name__), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'record.json'
                calls = []
                def barrier(path):
                    calls.append(path)
                    self.assertEqual(b'published', target.read_bytes())
                with patch.object(owner, barrier_name, side_effect=barrier):
                    owner._atomic_replace(target, b'published')
                self.assertEqual([target.parent], calls)
                self.assertEqual([target], list(target.parent.iterdir()))

    def test_host_barrier_failure_is_never_silently_accepted(self):
        for owner, barrier_name in ((fresh_project, 'fsync_directory'),
                                    (application_transaction, '_fsync_directory')):
            with self.subTest(owner=owner.__name__), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'record.json'
                with patch.object(owner, barrier_name, side_effect=OSError('host barrier failed')):
                    with self.assertRaisesRegex(OSError, 'host barrier failed'):
                        owner._atomic_replace(target, b'published')
                self.assertEqual(b'published', target.read_bytes())
                self.assertEqual([target], list(target.parent.iterdir()))
