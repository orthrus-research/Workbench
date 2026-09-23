"""Storage metadata publication delegates directory durability to its host."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_core.storage import manager


class StoragePublicationTests(unittest.TestCase):
    def test_directory_barrier_follows_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'operation.json'
            calls = []
            def barrier(path):
                calls.append(path)
                self.assertEqual({'state':'retained'}, json.loads(target.read_text()))
            with patch.object(manager, 'fsync_directory', side_effect=barrier):
                manager._write_json(target, {'state':'retained'})
            self.assertEqual([target.parent], calls)
            self.assertEqual([target], list(target.parent.iterdir()))

    def test_directory_barrier_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'operation.json'
            with patch.object(manager, 'fsync_directory', side_effect=OSError('host barrier failed')):
                with self.assertRaisesRegex(OSError, 'host barrier failed'):
                    manager._write_json(target, {'state':'retained'})
            self.assertEqual({'state':'retained'}, json.loads(target.read_text()))
            self.assertEqual([target], list(target.parent.iterdir()))
