"""Complete persistence, range access and failure recovery through Core APIs."""

from copy import deepcopy
from hashlib import sha256
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import check_snapshot_index as index
from workbench_core import check_snapshots as snapshots
from workbench_core import check_storage as storage


class CheckSnapshotsTests(unittest.TestCase):
    def test_historical_scope_resolver_and_unsupported_scope_preserve_export(self):
        manifest = self.publish()
        original = self.source.read_bytes()
        with snapshots.Snapshot(self.attempt, scope=lambda value: None, expected=self.expected) as opened:
            self.assertFalse(opened.scope_supported)
            self.assertEqual('unsupported', opened.query(self.query(opened))['state'])
            self.assertEqual('ready', opened.query(self.query(opened, 'summary', None))['state'])
            self.assertEqual(manifest, opened.manifest)
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                opened.read_record('recipes', 'item:0')
            destination = self.root / 'unsupported-export.json'
            opened.export(destination)
            self.assertEqual(original, destination.read_bytes())
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            snapshots.rebuild(self.attempt, scope=lambda value: None, expected=self.expected)

    def test_unknown_required_dependency_refuses_detail_without_rewriting_capture_coverage(self):
        self.publish()
        with snapshots.Snapshot(self.attempt, scope=self.scope, expected=self.expected,
                                supported_schemas={'fixture-recipes-v1', 'fixture-report-v1'}) as opened:
            self.assertEqual('complete', opened.manifest['coverage'])
            self.assertEqual('unsupported', opened.query(self.query(opened))['state'])

    def test_old_index_reader_and_new_rebuild_bind_inputs_without_rewriting_manifest(self):
        self.publish()
        publication = (self.attempt / 'snapshot/publication.json').read_bytes()
        root = self.index_path().parent
        record = storage.read_json(root / 'index.json')
        record.pop('id'); record.pop('inputs'); record['format'] = 'workbench-check-snapshot-index-v1'
        record = storage.seal('check-snapshot-index', record)
        (root / 'index.json').write_bytes(storage.canonical(record))
        pointer_path = self.attempt / 'snapshot/current.json'
        pointer = storage.read_json(pointer_path); pointer.pop('id'); pointer['index_id'] = record['id']
        pointer_path.write_bytes(storage.canonical(storage.seal('check-snapshot-index-selection', pointer)))
        with self.open() as opened:
            self.assertEqual('ready', opened.query(self.query(opened))['state'])
        snapshots.rebuild(self.attempt, scope=self.scope, expected=self.expected)
        rebuilt = storage.read_json(self.index_path().parent / 'index.json')
        self.assertEqual('workbench-check-snapshot-index-v2', rebuilt['format'])
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(), rebuilt['inputs']['source_result']['sha256'])
        self.assertEqual(publication, (self.attempt / 'snapshot/publication.json').read_bytes())
        # Even a consistently resealed index cannot be used with other inputs.
        rebuilt.pop('id'); rebuilt['inputs']['converter'] = 'unapproved-v2'
        rebuilt = storage.seal('check-snapshot-index', rebuilt)
        (self.index_path().parent / 'index.json').write_bytes(storage.canonical(rebuilt))
        pointer = storage.read_json(pointer_path); pointer.pop('id'); pointer['index_id'] = rebuilt['id']
        pointer_path.write_bytes(storage.canonical(storage.seal('check-snapshot-index-selection', pointer)))
        with self.open() as opened:
            self.assertEqual('unavailable', opened.query(self.query(opened))['state'])

    def test_record_export_streams_complete_selected_content_and_preserves_existing_files(self):
        self.value['values']['v1']['large'] = 'unicode 🌍\n' * 50000
        self.publish()
        destination = self.root / 'selected.json'
        with self.open() as opened:
            query = self.query(opened, 'record', 'values', record_key=index.record_key('v1'))
            response = opened.query(query)
            content = response['payload']['content']
            with self.assertRaisesRegex(ValueError, 'content differs'):
                opened.export_record('values', index.record_key('v1'), '0' * 64, destination)
            self.assertFalse(destination.exists())
            exported = opened.export_record('values', index.record_key('v1'), content['sha256'], destination)
            self.assertEqual(json.loads(destination.read_bytes()), self.value['values']['v1'])
            self.assertEqual(exported['sha256'], content['sha256'])
            self.assertEqual(exported['bytes'], content['bytes'])
            with self.assertRaises(FileExistsError):
                opened.export_record('values', index.record_key('v1'), content['sha256'], destination)
            self.cancelled = True
            with self.assertRaises(snapshots.SnapshotCancelled):
                opened.export_record('values', index.record_key('v1'), content['sha256'], self.root / 'cancelled.json')
        self.assertFalse(list(self.root.glob('.snapshot-export-*')))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.attempt = storage.allocate_attempt(self.root / 'state', 'fixture-check')
        self.value = {'recipes': [{'valueRef': 'v1'}, {'valueRef': 'v1'}],
                      'values': {'v1': {'type': 'fixture', 'items': [1, True, None, 'é\n🌍']}},
                      'unselected': {'complete': 'must remain in the archive and report'}}
        self.scope = {'name': 'fixture-check-v1', 'sections': {'recipes': True, 'report': True, 'values': True}}
        self.source = self.attempt / 'result.json'
        self.cancelled = False
        self.expected = {'attempt_id': self.attempt.name, 'request_id': 'fixture-request'}

    def describe(self, value):
        fields = {'attempt_id': self.attempt.name, 'request_id': 'fixture-request', 'result_id': 'fixture-result',
                  'producer': {'id': 'fixture', 'build_sha256': '1' * 64},
                  'bindings': {key: '2' * 64 for key in ('source', 'configuration', 'context', 'engine', 'runtime', 'jvm')},
                  'native_outcome': 'native-failed', 'coverage': 'complete',
                  'retained_inputs': [{'role': 'fixture-source', 'content': {'sha256': '3' * 64, 'bytes': 1, 'media_type': 'application/octet-stream'}}]}
        views = [{'id': name, 'schema': 'fixture-' + name + '-v1', 'required': True,
                  'state': 'observed', 'dependencies': ['values'] if name == 'recipes' else [], 'reason': None,
                  'path': [] if name == 'report' else [name],
                  'records': {'recipes': 'sequence', 'report': 'single', 'values': 'mapping'}[name]}
                 for name in sorted(self.scope['sections'])]
        return fields, views, {'state': 'completed', 'native_state': 'native-failed'}

    def verify(self, value):
        if any(row['valueRef'] not in value['values'] for row in value['recipes']):
            raise ValueError('unresolved native reference')

    def publish(self):
        if not self.source.exists():
            storage.write_json(self.source, self.value, byte_limit=None)
        return snapshots.publish(self.attempt, self.source, scope=self.scope, describe=self.describe,
                                 verify=self.verify, cancelled=lambda: self.cancelled)

    def open(self):
        return snapshots.Snapshot(self.attempt, scope=self.scope, expected=self.expected,
                                  cancelled=lambda: self.cancelled)

    def query(self, opened, operation='records', section='recipes', **values):
        request = {'format': 'workbench-check-snapshot-query-v1', 'snapshot_id': opened.manifest['id'],
                   'view_id': 'fixture-view-1', 'operation': operation, 'section_id': section,
                   'record_key': None, 'blob_sha256': None, 'preferred_bytes': 2048, 'cursor': None}
        return {**request, **values}

    def index_path(self):
        pointer = storage.read_json(self.attempt / 'snapshot/current.json')
        return self.attempt / 'snapshot/indexes' / pointer['generation'] / 'query.sqlite3'

    def test_complete_archive_and_index_round_trip_preserve_duplicates_and_types(self):
        manifest = self.publish()
        original = self.source.read_bytes()
        with self.open() as opened:
            response = opened.query(self.query(opened))
            self.assertEqual('ready', response['state'])
            self.assertEqual(self.value['recipes'], [row['value'] for row in response['payload']['records']])
            row = opened._reader().record('report', 'value')
            self.assertEqual(self.value, opened._reader().value(row))
            output = self.root / 'export.json'
            receipt = opened.export(output)
            self.assertEqual(original, output.read_bytes())
            self.assertEqual(sha256(original).hexdigest(), receipt['sha256'])
            self.assertEqual('native-failed', manifest['native_outcome'])
            self.assertEqual('complete', manifest['coverage'])
        with self.assertRaisesRegex(ValueError, 'already published'):
            self.publish()

    def test_arbitrary_large_string_and_large_mapping_keys_are_chunk_addressable(self):
        self.value['values'][''] = {'huge': '🌍"\\\n' * 160000}
        self.value['values']['x' * 300000] = 'long key retained'
        self.publish()
        with self.open() as opened:
            key = index.record_key('')
            result = opened.query(self.query(opened, 'record', 'values', record_key=key))
            self.assertEqual('ready', result['state'])
            reference = result['payload']['content']
            request = self.query(opened, 'blob', 'values', record_key=key, blob_sha256=reference['sha256'], preferred_bytes=65536)
            blocks = []
            while True:
                response = opened.query(request)
                self.assertEqual('ready', response['state'])
                self.assertLessEqual(len(storage.canonical(response)), request['preferred_bytes'])
                data = base64.b64decode(response['payload']['data'])
                self.assertEqual(sha256(data).hexdigest(), response['payload']['chunk_sha256'])
                self.assertEqual(sum(map(len, blocks)), response['payload']['offset'])
                blocks.append(data)
                if response['complete']:
                    break
                request['cursor'] = response['next_cursor']
            raw = b''.join(blocks)
            self.assertEqual(reference['sha256'], sha256(raw).hexdigest())
            self.assertEqual(self.value['values'][''], json.loads(raw))
            self.assertEqual(self.value, opened._reader().value(opened._reader().record('report', 'value')))
            sizes = opened._reader().db.execute('SELECT max(size) FROM pages').fetchone()[0]
            self.assertLessEqual(sizes, index.PAGE_BYTES)

    def test_small_and_bulk_pages_preserve_exact_unicode_records_order_and_duplicates(self):
        self.value['recipes'] = [{'valueRef': 'v1', 'ordinal': i, 'message': 'quoted " \n café 🌍 ' * 5}
                                 for i in range(1200)]
        self.publish()
        for preference in (2048, 65536, 1048576):
            with self.subTest(preference=preference), self.open() as opened:
                query = self.query(opened, preferred_bytes=preference)
                observed = []
                while True:
                    response = opened.query(query)
                    self.assertEqual('ready', response['state'])
                    self.assertLessEqual(len(storage.canonical(response)), preference)
                    self.assertEqual(len(observed), response['payload']['offset'])
                    observed.extend(row['value'] for row in response['payload']['records'])
                    if response['complete']:
                        self.assertIsNone(response['next_cursor'])
                        break
                    query = {**query, 'cursor': response['next_cursor']}
                self.assertEqual(self.value['recipes'], observed)

    def test_history_and_small_records_do_not_parse_the_complete_archive(self):
        self.publish()
        with patch.object(storage, 'read_json', wraps=storage.read_json) as reads:
            with self.open() as opened, patch.object(snapshots.gzip, 'open', side_effect=AssertionError('no archive parsing for queries')):
                summary = opened.query(self.query(opened, 'summary', None))
                records = opened.query(self.query(opened))
                self.assertEqual('ready', summary['state'])
                self.assertEqual('ready', records['state'])
            self.assertNotIn(self.source, [call.args[0] for call in reads.call_args_list])

    def test_pages_reach_exact_total_and_stale_cursor_cannot_cross_view(self):
        self.value['recipes'] *= 200
        self.publish()
        with self.open() as opened:
            request = self.query(opened)
            values = []
            while True:
                response = opened.query(request)
                self.assertEqual('ready', response['state'])
                self.assertEqual(len(values), response['payload']['offset'])
                values.extend(row['value'] for row in response['payload']['records'])
                if response['complete']:
                    break
                request['cursor'] = response['next_cursor']
                with self.assertRaisesRegex(ValueError, 'cursor'):
                    opened.query({**request, 'view_id': 'other-view'})
            self.assertEqual(self.value['recipes'], values)

    def test_missing_and_corrupt_indexes_rebuild_from_archive_without_rewriting_manifest(self):
        self.publish()
        publication = (self.attempt / 'snapshot/publication.json').read_bytes()
        for corrupt in (False, True):
            path = self.index_path()
            if corrupt:
                path.chmod(0o600)
                path.write_bytes(b'corrupt')
            else:
                path.unlink()
            with self.open() as opened:
                self.assertEqual('ready', opened.query(self.query(opened, 'summary', None))['state'])
                self.assertEqual('unavailable', opened.query(self.query(opened))['state'])
            snapshots.rebuild(self.attempt, scope=self.scope, expected=self.expected)
            self.assertEqual(publication, (self.attempt / 'snapshot/publication.json').read_bytes())
            with self.open() as opened:
                self.assertEqual('ready', opened.query(self.query(opened))['state'])

    def test_missing_archive_does_not_rebuild_from_unrelated_raw_result(self):
        self.publish()
        (self.attempt / 'snapshot/result.json.gz').unlink()
        with self.assertRaises(OSError):
            snapshots.rebuild(self.attempt, scope=self.scope, expected=self.expected)
        self.assertTrue(self.source.exists())

    def test_reader_lease_blocks_rebuild_and_closed_read_cannot_continue(self):
        self.publish()
        with self.open() as opened:
            with self.assertRaises(snapshots.SnapshotBusy):
                snapshots.rebuild(self.attempt, scope=self.scope, expected=self.expected)
            request = self.query(opened)
        with self.assertRaisesRegex(ValueError, 'closed'):
            opened.query(request)
        snapshots.rebuild(self.attempt, scope=self.scope, expected=self.expected)

    def test_cancelled_queries_and_failed_export_do_not_publish_empty_success(self):
        self.publish()
        with self.open() as opened:
            self.cancelled = True
            self.assertEqual('cancelled', opened.query(self.query(opened))['state'])
            self.assertIsNone(opened.lease)
        self.cancelled = False
        with self.open() as opened:
            self.cancelled = True
            target = self.root / 'cancelled-export'
            with self.assertRaises(snapshots.SnapshotCancelled):
                opened.export(target)
            self.assertFalse(target.exists())
        self.cancelled = False
        with self.open() as opened:
            target = self.root / 'existing'
            target.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                opened.export(target)
            self.assertEqual(b'existing', target.read_bytes())

    def test_unresolved_reference_and_interrupted_index_never_publish(self):
        self.value['recipes'][0]['valueRef'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.publish()
        self.assertFalse((self.attempt / 'snapshot').exists())
        self.source.unlink()
        self.value['recipes'][0]['valueRef'] = 'v1'
        with patch.object(index, 'build', side_effect=OSError('disk full')), self.assertRaisesRegex(OSError, 'disk full'):
            self.publish()
        self.assertFalse((self.attempt / 'snapshot').exists())
        results = snapshots.recover(self.attempt)
        self.assertEqual(2, len(results))
        self.assertTrue(all(row['state'] == 'discarded-unpublished-staging' for row in results))
        self.publish()

    def test_recovery_refuses_unknown_or_linked_staging_without_deleting_evidence(self):
        with patch.object(index, 'build', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            self.publish()
        stage = next((self.attempt / 'snapshot-operations').glob('*/staging'))
        unknown = stage / 'unknown'
        unknown.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'unknown'):
            snapshots.recover(self.attempt)
        self.assertEqual(b'keep', unknown.read_bytes())
        unknown.unlink()
        unknown.symlink_to(self.source)
        with self.assertRaises(ValueError):
            snapshots.recover(self.attempt)
        self.assertTrue(self.source.exists())

    def test_changed_index_during_read_or_wrong_selected_request_is_rejected(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, 'selected request'):
            with snapshots.Snapshot(self.attempt, scope=self.scope, expected={'request_id': 'wrong'}):
                pass
        with self.open() as opened:
            self.assertEqual('ready', opened.query(self.query(opened))['state'])
            self.index_path().chmod(0o600)
            with self.index_path().open('r+b') as stream:
                stream.seek(100)
                stream.write(b'tampered')
            self.assertEqual('unavailable', opened.query(self.query(opened))['state'])

    def test_crash_after_publish_is_reconciled_without_rerunning_or_rewriting_result(self):
        with patch.object(snapshots, '_completed', side_effect=OSError('receipt interrupted')), self.assertRaises(OSError):
            self.publish()
        publication = (self.attempt / 'snapshot/publication.json').read_bytes()
        results = snapshots.recover(self.attempt, scope=self.scope, expected=self.expected)
        self.assertEqual('reconciled-published-snapshot', results[0]['state'])
        self.assertEqual(publication, (self.attempt / 'snapshot/publication.json').read_bytes())
        with self.open() as opened:
            self.assertEqual('ready', opened.query(self.query(opened))['state'])

    def test_interrupted_rebuild_keeps_old_readable_generation_and_identifies_new_generation(self):
        self.publish()
        original = (self.attempt / 'snapshot/current.json').read_bytes()
        with patch.object(snapshots, '_replace', side_effect=OSError('pointer interrupted')), self.assertRaises(OSError):
            snapshots.rebuild(self.attempt, scope=self.scope, expected=self.expected)
        self.assertEqual(original, (self.attempt / 'snapshot/current.json').read_bytes())
        results = snapshots.recover(self.attempt, scope=self.scope, expected=self.expected)
        self.assertEqual('unselected-index-generation', results[0]['state'])
        self.assertTrue(results[0]['exists'])
        with self.open() as opened:
            self.assertEqual('ready', opened.query(self.query(opened))['state'])

    def test_empty_sections_wrong_blob_digest_and_unreachable_offsets_are_distinct(self):
        self.value['recipes'] = []
        self.publish()
        with self.open() as opened:
            response = opened.query(self.query(opened))
            self.assertEqual({'records': [], 'offset': 0, 'total': 0}, response['payload'])
            self.assertTrue(response['complete'])
            request = self.query(opened, 'blob', 'values', record_key=index.record_key('v1'), blob_sha256='0' * 64)
            self.assertEqual('unavailable', opened.query(request)['state'])
            from workbench_core.check_snapshot_contract import query_identity
            request = self.query(opened)
            request['cursor'] = {'snapshot_id': opened.manifest['id'], 'query_id': query_identity(request), 'offset': 1}
            response = opened.query(request)
            self.assertEqual('unavailable', response['state'])
            self.assertFalse(response['complete'])

    def test_raw_input_change_during_publication_cannot_bind_the_prechange_value(self):
        storage.write_json(self.source, self.value)
        def mutate(value):
            self.source.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'changed during archiving'):
            snapshots.publish(self.attempt, self.source, scope=self.scope, describe=self.describe, verify=mutate)
        self.assertFalse((self.attempt / 'snapshot').exists())

    def test_corrupt_encoded_page_is_detected_before_publication(self):
        import sqlite3
        import zlib
        original = index.build
        def damaged(path, *args):
            observed = original(path, *args)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE pages SET payload=? WHERE id=0', (zlib.compress(b'wrong'),))
            return observed
        with patch.object(index, 'build', damaged), self.assertRaisesRegex(ValueError, 'page content differs'):
            self.publish()
        self.assertFalse((self.attempt / 'snapshot').exists())


if __name__ == '__main__':
    unittest.main()
