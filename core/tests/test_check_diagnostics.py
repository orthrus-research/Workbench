"""Diagnostic publication, partial-write visibility, identity and exact paging."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import check_diagnostics as delivery
from workbench_core import check_storage as storage


class DiagnosticDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.attempt = Path(temporary.name)
        self.binding = {'request': 'saved-request'}

    def publish(self, values):
        return delivery.publish(self.attempt, binding=self.binding, summary={'outcome': 'native-failed'}, streams={'findings': values})

    def test_complete_paging_preserves_large_and_empty_values(self):
        values = [{'message': '🌍' * 200000}] + list(range(400))
        record = self.publish(values)
        reopened = delivery.publication(self.attempt, binding=self.binding, revision=record['id'])
        offset, observed = 0, []
        while offset is not None:
            page = delivery.page(self.attempt, reopened, 'findings', offset)
            observed.extend(page['records']); offset = page['next_offset']
        self.assertEqual(values, observed)
        self.assertEqual(values[128], delivery.item(self.attempt, reopened, 'findings', 128))
        self.assertEqual([], delivery.page(self.attempt, reopened, 'findings', len(values))['records'])
        self.assertTrue(delivery.retained_files(self.attempt))

    def test_wrong_owner_revision_offset_and_changed_page_refuse(self):
        record = self.publish([1, 2])
        for binding, revision in [({'request': 'other'}, record['id']), (self.binding, 'other')]:
            with self.assertRaisesRegex(ValueError, 'another input or revision'):
                delivery.publication(self.attempt, binding=binding, revision=revision)
        for offset in (-1, True, 3):
            with self.assertRaises(ValueError): delivery.page(self.attempt, record, 'findings', offset)
        path = self.attempt / delivery.DIRECTORY / 'findings-0.json'
        original = path.read_bytes(); path.write_bytes(original.replace(b'1', b'9'))
        with self.assertRaisesRegex(ValueError, 'content changed'): delivery.item(self.attempt, record, 'findings', 0)
        path.unlink(); path.symlink_to(self.attempt / 'elsewhere')
        with self.assertRaises(ValueError): delivery.item(self.attempt, record, 'findings', 0)

    def test_interrupted_write_never_publishes_a_ready_revision(self):
        original = storage.write_bytes
        def fail(path, raw, **kwargs):
            if path.name == 'findings-128.json': raise OSError('disk unavailable')
            return original(path, raw, **kwargs)
        with patch.object(storage, 'write_bytes', side_effect=fail):
            with self.assertRaises(OSError): self.publish(list(range(256)))
        self.assertFalse((self.attempt / delivery.DIRECTORY / 'publication.json').exists())
        self.assertEqual(1, len(delivery.retained_files(self.attempt)))
        # A failed fsync can leave Core's private atomic-write temporary file.
        temporary = self.attempt / delivery.DIRECTORY / ('.record-' + 'a' * 32)
        temporary.write_bytes(b'partial')
        self.assertEqual(2, len(delivery.retained_files(self.attempt)))
        with self.assertRaises(FileNotFoundError): delivery.publication(self.attempt, binding=self.binding)

    def test_empty_stream_is_complete_and_publication_is_immutable(self):
        record = self.publish([])
        self.assertEqual({'offset': 0, 'records': [], 'total': 0, 'next_offset': None}, delivery.page(self.attempt, record, 'findings'))
        with self.assertRaises(FileExistsError): self.publish([1])
