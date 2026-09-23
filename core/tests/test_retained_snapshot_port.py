"""Owner admission cannot bypass retained Core custody or a read lease."""
from copy import deepcopy
from hashlib import sha256
import unittest

from workbench_api.retained_snapshots import RetainedSnapshotAdmission
from workbench_core import check_lifecycle as lifecycle
from workbench_core import check_snapshots as snapshots
from workbench_core import check_storage as storage
from workbench_core.retained_snapshots import open_retained_snapshot, provider
import test_check_snapshots as fixtures


class RetainedSnapshotPortTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CheckSnapshotsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.attempt = self.fixture.attempt
        self.root = self.attempt.parent.parent.parent
        (self.attempt / 'source').mkdir()
        self.source = self.attempt / 'source/original.txt'
        self.source.write_bytes(b'original saved source\n')
        self.source.chmod(0o644)
        self.files = [{'path': 'original.txt', 'mode': 0o100644,
                       'size': self.source.stat().st_size,
                       'sha256': sha256(self.source.read_bytes()).hexdigest()}]
        self.request = {'id': 'fixture-request', 'meaning': 'original fixture request'}
        storage.write_json(self.attempt / 'request.json', self.request)
        describe = self.fixture.describe
        def description(value):
            fields, views, summary = describe(value)
            fields['retained_inputs'] = [{'role': 'request', 'content': {
                **snapshots.file_content(self.attempt / 'request.json'), 'media_type': 'application/json'}}]
            return fields, views, summary
        self.fixture.describe = description
        self.manifest = self.fixture.publish()
        self.custody = lifecycle.register(self.root, self.attempt,
            inputs={'request': 'request.json'}, source_directories=['source'],
            context={'owner': 'fixture', 'workspace_uri': 'file:///saved/workspace', 'selection_id': 'selected'})
        self.admission_calls = 0

    def admit(self, request, inputs):
        self.admission_calls += 1
        self.assertEqual(self.request, request)
        self.assertEqual({'original.txt': b'original saved source\n'}, inputs.source_files('source', self.files))
        return RetainedSnapshotAdmission(lambda manifest: self.fixture.scope,
            self.fixture.expected, frozenset('fixture-' + name + '-v1' for name in self.fixture.scope['sections']))

    def open(self, **kwargs):
        return provider().open_snapshot(self.attempt, owner_id='fixture', admit=self.admit, **kwargs)

    def test_original_report_and_native_failure_survive_optional_snapshot_selection(self):
        with self.open() as reader:
            self.assertEqual(self.manifest, reader.manifest)
            self.assertEqual(self.request, reader.request)
            self.assertEqual(self.custody, reader.custody)
            self.assertEqual('native-failed', reader.manifest['native_outcome'])
            self.assertEqual(self.fixture.value, reader.read_record('report', 'value'))
        with self.assertRaisesRegex(ValueError, 'lease is closed'):
            reader.read_record('report', 'value')
        with self.open(snapshot_id=self.manifest['id']) as reader:
            self.assertTrue(reader.scope_supported)

    def test_explicit_snapshot_and_context_must_match(self):
        for fields in ({'snapshot_id': 'check-snapshot:sha256:' + '0' * 64},
                       {'expected_context': {'workspace_uri': 'file:///other'}}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, 'selected'):
                with self.open(**fields):
                    self.fail('mismatched selection was admitted')
        with self.assertRaisesRegex(ValueError, 'owner'):
            with open_retained_snapshot(self.attempt, owner_id='other', admit=self.admit):
                self.fail('another producer was admitted')
        self.assertEqual(0, self.admission_calls)

    def test_same_size_source_change_is_rejected_before_owner_admission(self):
        self.source.write_bytes(b'changed! saved source\n')
        self.assertEqual(self.files[0]['size'], self.source.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            with self.open():
                self.fail('changed source was admitted')
        self.assertEqual(0, self.admission_calls)

    def test_added_source_and_mode_changes_are_rejected_by_exact_inventory(self):
        extra = self.source.with_name('extra.txt')
        extra.write_text('not in captured candidate')
        with self.assertRaisesRegex(ValueError, 'unexpected file'):
            with self.open():
                self.fail('added source was admitted')
        extra.unlink()
        self.source.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'differs from candidate'):
            with self.open():
                self.fail('changed mode was admitted')

    def test_conflicting_registered_custody_is_not_replaced(self):
        changed = deepcopy(self.custody)
        changed.pop('id')
        changed['context']['workspace_uri'] = 'file:///different'
        path = self.root / '.workbench/runtime-manager/checks' / (self.attempt.name + '.json')
        path.write_bytes(storage.canonical(storage.seal('check-custody', changed)))
        with self.assertRaisesRegex(ValueError, 'registered custody differs'):
            with self.open():
                self.fail('conflicting custody was admitted')

    def test_unknown_scope_preserves_manifest_and_refuses_interpretation(self):
        def unknown(request, inputs):
            admission = self.admit(request, inputs)
            return RetainedSnapshotAdmission(lambda manifest: None, admission.expected, admission.supported_schemas)
        with open_retained_snapshot(self.attempt, owner_id='fixture', admit=unknown) as reader:
            self.assertFalse(reader.scope_supported)
            self.assertEqual('complete', reader.manifest['coverage'])
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                reader.read_record('report', 'value')

    def test_cancellation_during_custody_verification_releases_leases(self):
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 3
        with self.assertRaises(snapshots.SnapshotCancelled):
            with self.open(cancelled=cancelled):
                self.fail('cancelled evidence was admitted')
        self.assertEqual(0, self.admission_calls)
        with self.open() as reader:
            self.assertEqual(self.manifest['id'], reader.manifest['id'])


if __name__ == '__main__':
    unittest.main()
