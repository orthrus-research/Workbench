"""Raw capture custody tests with independently encoded synthetic records."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_runtime_snapshot.capture import (
    RuntimeCaptureError, capture_canonical_bytes, read_runtime_capture, read_capture_payload,
)
import workbench_crucible_runtime_snapshot.capture as capture_module


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RetainedRuntimeCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='retained-capture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'capture'
        self.root.mkdir()
        self.inputs = self.root.parent / 'inputs.json'
        self.inputs.write_bytes(encoded({'physical_side': 'dedicated_server',
                                        'capture_id': 'capture-one', 'launch_id': 'launch-one'}))
        self.binding = {
            'capture_id': 'capture-one', 'launch_id': 'launch-one',
            'input_manifest_sha256': digest(self.inputs.read_bytes()),
            'candidate_lock_sha256': 'a' * 64, 'adapter_profile_sha256': 'b' * 64,
            'physical_side': 'dedicated_server',
        }
        self.category = {
            'format': 'workbench-crucible-runtime-category-result-v1', 'schema_version': 1,
            **self.binding, 'adapter_id': 'alpha', 'category_id': 'opaque-records',
            'checkpoint_id': 'retained-checkpoint', 'status': 'complete', 'stable': True,
            'record_count': 1, 'unsupported_value_count': 0, 'diagnostics': [],
        }
        self.records = b'[{"opaque":{"value":7}}]'
        self.samples = [dict(ordinal=ordinal, record_count=1, unsupported_value_count=0,
                             diagnostics=[], records_sha256=digest(self.records))
                        for ordinal in (1, 2)]
        self.manifest = {
            'format': 'workbench-runtime-graph-raw-bundle-v1', 'schema_version': 1,
            **self.binding, 'producer': 'synthetic-producer',
            'categories': [{'adapter_id': 'alpha', 'status': 'complete', 'file': 'alpha.json'}],
        }

    def publish(self, *, category_changes=None, manifest_changes=None, records=None,
                samples=None, manifest_encoding='utf-8', extra_payloads=()):
        records = self.records if records is None else records
        value = {**self.category, 'records': None, 'records_sha256': digest(records),
                 'samples': deepcopy(self.samples if samples is None else samples)}
        value.update(category_changes or {})
        def category_bytes(row):
            return encoded(row).replace(b'"records":null', b'"records":' + records)
        value['result_sha256'] = digest(category_bytes(value))
        raw = category_bytes(value)
        (self.root / 'alpha.json').write_bytes(raw)
        payloads = [('alpha.json', raw), *extra_payloads]
        for name, content in extra_payloads:
            (self.root / name).write_bytes(content)
        manifest = {**self.manifest, 'payloads': [
            {'file': name, 'sha256': digest(content), 'size': len(content)}
            for name, content in sorted(payloads)
        ]}
        manifest.update(manifest_changes or {})
        manifest['manifest_sha256'] = digest(encoded(manifest))
        manifest_raw = encoded(manifest).decode().encode(manifest_encoding)
        (self.root / 'manifest.json').write_bytes(manifest_raw)
        (self.root / '.capture-complete').write_bytes(b'')

    def read(self, **kwargs):
        return read_runtime_capture(self.root, categories=('alpha',), input_manifest=self.inputs, **kwargs)

    def rewrite_manifest(self, mutate):
        path = self.root / 'manifest.json'
        manifest = json.loads(path.read_bytes())
        del manifest['manifest_sha256']
        mutate(manifest)
        manifest['manifest_sha256'] = digest(encoded(manifest))
        path.write_bytes(encoded(manifest))

    def replace_category_bytes(self, raw):
        (self.root / 'alpha.json').write_bytes(raw)
        self.rewrite_manifest(lambda manifest: manifest['payloads'][0].update(
            size=len(raw), sha256=digest(raw)))

    def test_admits_opaque_records_hashes_nested_fields_and_never_writes_inputs(self):
        self.publish()
        original = {path: path.read_bytes() for path in (*self.root.iterdir(), self.inputs)}
        result = self.read(record_digest_fields={'alpha': ('opaque',)})
        self.assertEqual({'opaque': {'value': 7}}, result.categories['alpha']['records'][0])
        self.assertEqual((digest(b'{"opaque":{"value":7}}'),), result.record_sha256['alpha'])
        self.assertEqual(({'opaque': digest(b'{"value":7}')},), result.record_field_sha256['alpha'])
        self.assertEqual(sum(map(len, original.values())), result.verified_payload_bytes)
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def test_numeric_spelling_and_control_escape_hashes_are_preserved(self):
        record = b'{"opaque":{"a":1.0E-7,"b":-0.0,"c":100000000000000000001,"text":"line\\u000aend"}}'
        records = b'[' + record + b']'
        samples = [{**row, 'records_sha256': digest(records)} for row in self.samples]
        self.publish(records=records, samples=samples)
        result = self.read(record_digest_fields={'alpha': ('opaque',)})
        self.assertEqual((digest(record),), result.record_sha256['alpha'])
        self.assertEqual(digest(record[len(b'{"opaque":'):-1]), result.record_field_sha256['alpha'][0]['opaque'])
        self.assertEqual('line\nend', result.categories['alpha']['records'][0]['opaque']['text'])
        self.assertEqual(b'{"text":"\\u0000\\u0008\\u0009\\u000a\\u000c\\u000d"}',
                         capture_canonical_bytes({'text': '\0\b\t\n\f\r'}))

    def test_boolean_or_float_counts_and_sample_ordinals_are_refused(self):
        mutations = [({'unsupported_value_count': False}, None),
                     ({'record_count': True}, None), ({'schema_version': True}, None)]
        for field, value in [('ordinal', True), ('record_count', True),
                             ('record_count', 1.0), ('unsupported_value_count', False)]:
            samples = deepcopy(self.samples)
            samples[0][field] = value
            mutations.append(({}, samples))
        for changes, samples in mutations:
            with self.subTest(changes=changes, samples=samples):
                self.publish(category_changes=changes, samples=samples)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_unsorted_or_nonobject_records_are_refused_even_when_resealed(self):
        for records in (b'[{"z":0},{"a":0}]', b'[null]', b'[7]'):
            with self.subTest(records=records):
                count = len(json.loads(records))
                samples = [{**row, 'record_count': count, 'records_sha256': digest(records)}
                           for row in self.samples]
                self.publish(records=records, category_changes={'record_count': count}, samples=samples)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_utf16_or_noncanonical_capture_files_are_refused(self):
        self.publish(manifest_encoding='utf-16')
        with self.assertRaises(RuntimeCaptureError):
            self.read()
        self.publish()
        path = self.root / 'manifest.json'
        path.write_bytes(b' ' + path.read_bytes())
        with self.assertRaises(RuntimeCaptureError):
            self.read()

    def test_numeric_overflow_is_refused_instead_of_returning_infinity(self):
        for number in (b'1e999', b'1e-999'):
            with self.subTest(number=number):
                records = b'[{"opaque":' + number + b'}]'
                samples = [{**row, 'records_sha256': digest(records)} for row in self.samples]
                self.publish(records=records, samples=samples)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_root_and_payload_symlinks_are_refused(self):
        self.publish()
        link = self.root.parent / 'capture-link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(RuntimeCaptureError):
            read_runtime_capture(link, categories=('alpha',), input_manifest=self.inputs)
        original = self.root / 'alpha.json'
        original.rename(self.root.parent / 'alpha.json')
        original.symlink_to(self.root.parent / 'alpha.json')
        with self.assertRaises(RuntimeCaptureError):
            self.read()

    def test_requested_category_argument_is_not_a_string(self):
        self.publish()
        with self.assertRaises(RuntimeCaptureError):
            read_runtime_capture(self.root, categories='alpha', input_manifest=self.inputs)

    def test_category_metadata_and_digest_field_requests_are_validated(self):
        for changes in ({'category_id': ''}, {'checkpoint_id': 1}, {'adapter_id': 'other'}):
            with self.subTest(changes=changes):
                self.publish(category_changes=changes)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()
        self.publish()
        for fields in ({'missing': ('opaque',)}, {'alpha': 'opaque'}, {'alpha': ('',)}, {'alpha': ('missing',)}):
            with self.subTest(fields=fields), self.assertRaises(RuntimeCaptureError):
                self.read(record_digest_fields=fields)

    def test_bound_accounts_for_input_manifest_and_every_unselected_payload(self):
        self.publish(extra_payloads=(('opaque.bin', b'uncategorized payload'),))
        total = sum(path.stat().st_size for path in (*self.root.iterdir(), self.inputs))
        self.assertEqual(total, self.read(max_source_bytes=total).verified_payload_bytes)
        with self.assertRaises(RuntimeCaptureError):
            self.read(max_source_bytes=total-1)
        for bound in (True, 0, -1, 1.0):
            with self.subTest(bound=bound), self.assertRaises(RuntimeCaptureError):
                self.read(max_source_bytes=bound)
        (self.root / 'opaque.bin').write_bytes(b'corrupted')
        with self.assertRaises(RuntimeCaptureError):
            self.read()

    def test_input_binding_and_repeated_samples_are_refused_when_different(self):
        self.publish()
        self.inputs.write_bytes(encoded({'physical_side': 'client'}))
        with self.assertRaises(RuntimeCaptureError):
            self.read()
        self.inputs.write_bytes(encoded({'physical_side': 'dedicated_server',
                                        'capture_id': 'capture-one', 'launch_id': 'launch-one'}))
        for samples in (list(reversed(self.samples)), self.samples[:1],
                        [self.samples[0], {**self.samples[1], 'records_sha256': 'f'*64}]):
            with self.subTest(samples=samples):
                self.publish(samples=samples)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_unsafe_or_duplicate_payload_paths_are_refused(self):
        self.publish()
        for name in ('../alpha.json', '/alpha.json', 'sub/alpha.json', 'manifest.json'):
            with self.subTest(name=name):
                self.publish(manifest_changes={'payloads': [{'file': name, 'sha256': 'a'*64, 'size': 1}]})
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_complete_status_requires_exact_stability_and_clean_diagnostics(self):
        for changes in ({'status': 'partial'}, {'status': 'failed'}, {'stable': 1},
                        {'stable': False}, {'diagnostics': ['observer-warning']},
                        {'unsupported_value_count': 1}, {'record_count': 2}):
            with self.subTest(changes=changes):
                self.publish(category_changes=changes)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_each_integrity_layer_is_checked(self):
        self.publish()
        path = self.root / 'manifest.json'
        manifest = json.loads(path.read_bytes())
        manifest['producer'] = 'tampered'
        path.write_bytes(encoded(manifest))
        with self.assertRaisesRegex(RuntimeCaptureError, 'manifest_sha256'):
            self.read()
        for layer in ('result', 'records'):
            with self.subTest(layer=layer):
                self.publish()
                category = json.loads((self.root / 'alpha.json').read_bytes())
                category['records_sha256'] = 'f' * 64
                if layer == 'records':
                    del category['result_sha256']
                    category['result_sha256'] = digest(encoded(category))
                self.replace_category_bytes(encoded(category))
                with self.assertRaisesRegex(RuntimeCaptureError, 'result_sha256|records seal'):
                    self.read()

    def test_duplicate_keys_nonfinite_numbers_and_unpaired_surrogates_are_refused(self):
        for raw in (b'{"status":1,"status":2}', b'{"value":NaN}', b'{"value":"\\ud800"}'):
            with self.subTest(raw=raw):
                self.publish()
                self.replace_category_bytes(raw)
                with self.assertRaises(RuntimeCaptureError):
                    self.read()

    def test_resealed_input_scope_mismatch_is_refused(self):
        for field in ('launch_id', 'capture_id', 'physical_side'):
            with self.subTest(field=field):
                self.publish()
                inputs = {'physical_side': 'dedicated_server', 'capture_id': 'capture-one',
                          'launch_id': 'launch-one', field: 'different'}
                self.inputs.write_bytes(encoded(inputs))
                self.rewrite_manifest(lambda manifest: manifest.update(
                    input_manifest_sha256=digest(self.inputs.read_bytes())))
                with self.assertRaisesRegex(RuntimeCaptureError, 'launch, capture or physical side'):
                    self.read()

    def test_unselected_partial_category_remains_uninterpreted_but_byte_verified(self):
        self.manifest['categories'].append({'adapter_id': 'beta', 'status': 'partial', 'file': 'beta.json'})
        self.publish(extra_payloads=(('beta.json', b'opaque partial evidence'),))
        self.assertEqual({'alpha'}, set(self.read().categories))
        (self.root / 'beta.json').write_bytes(b'changed partial evidence')
        with self.assertRaises(RuntimeCaptureError):
            self.read()

    def test_unselected_binary_payload_preserves_crlf_and_control_z(self):
        self.manifest['categories'].append({'adapter_id': 'beta', 'status': 'partial', 'file': 'beta.json'})
        self.publish(extra_payloads=(('beta.json', b'opaque\r\npartial\x1aevidence'),))
        original = {path: path.read_bytes() for path in (*self.root.iterdir(), self.inputs)}
        result = self.read()
        self.assertEqual({'alpha'}, set(result.categories))
        self.assertEqual(sum(map(len, original.values())), result.verified_payload_bytes)
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def auxiliary(self, raw=b'{"preparation":{"recipes_before":[{"recipe":{"value":7}}]}}'):
        self.publish(extra_payloads=(('auxiliary.json', raw),))
        return self.read()

    @staticmethod
    def changed_metadata(metadata, **changes):
        names = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        return SimpleNamespace(**{**{name: getattr(metadata, name) for name in names}, **changes})

    def test_auxiliary_accepts_distinct_stable_handle_and_path_clocks(self):
        capture = self.auxiliary()
        original = {path: path.read_bytes() for path in (*self.root.iterdir(), self.inputs)}
        fstat = os.fstat

        def handle_state(descriptor):
            state = fstat(descriptor)
            return self.changed_metadata(state, st_ctime_ns=state.st_ctime_ns + 1_000_000)

        with patch.object(capture_module.os, 'fstat', side_effect=handle_state):
            result = read_capture_payload(capture, 'auxiliary.json')
        self.assertEqual(digest(original[self.root / 'auxiliary.json']), result.file_sha256)
        self.assertEqual(7, result.value['preparation']['recipes_before'][0]['recipe']['value'])
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def test_auxiliary_refuses_clock_changes_on_either_observation_channel(self):
        capture = self.auxiliary()
        path = self.root / 'auxiliary.json'
        inode = path.stat().st_ino
        fstat, lstat = os.fstat, Path.lstat
        for channel in ('handle', 'path'):
            calls = 0

            def changed(state):
                nonlocal calls
                if state.st_ino == inode:
                    calls += 1
                    if calls >= 2:
                        return self.changed_metadata(state, st_ctime_ns=state.st_ctime_ns + 1)
                return state

            with self.subTest(channel=channel):
                if channel == 'handle':
                    target = patch.object(capture_module.os, 'fstat', side_effect=lambda fd: changed(fstat(fd)))
                else:
                    target = patch.object(Path, 'lstat', lambda selected, *a, **kw: changed(lstat(selected, *a, **kw)))
                with target, self.assertRaisesRegex(RuntimeCaptureError, 'changed while read'):
                    read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_refuses_opened_file_identity_differing_from_path(self):
        capture = self.auxiliary()
        inode = (self.root / 'auxiliary.json').stat().st_ino
        fstat = os.fstat

        def another_file(descriptor):
            state = fstat(descriptor)
            return self.changed_metadata(state, st_ino=state.st_ino + 1) if state.st_ino == inode else state

        with patch.object(capture_module.os, 'fstat', side_effect=another_file):
            with self.assertRaisesRegex(RuntimeCaptureError, 'changed before read'):
                read_capture_payload(capture, 'auxiliary.json')

    @unittest.skipUnless(os.name == 'nt', 'native Windows path and handle clocks')
    def test_native_windows_auxiliary_payload_reopens_exact_bytes(self):
        capture = self.auxiliary()
        path = self.root / 'auxiliary.json'
        # Updating an existing file's metadata exercises NTFS change time;
        # path stat and handle stat can expose it differently from creation time.
        metadata = path.stat()
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 10_000_000))
        original = path.read_bytes()
        with path.open('rb') as stream:
            handle = os.fstat(stream.fileno())
        observed = path.lstat()
        self.assertEqual(capture_module._file_state(observed)[:-1], capture_module._file_state(handle)[:-1])
        result = read_capture_payload(capture, 'auxiliary.json')
        self.assertEqual(digest(original), result.file_sha256)
        self.assertEqual(original, path.read_bytes())
        path.write_bytes(original.replace(b'"value":7', b'"value":8'))
        with self.assertRaisesRegex(RuntimeCaptureError, 'digest/size mismatch'):
            read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_hashes_preserve_java_number_tokens_and_original_array_order(self):
        first = b'{"recipe":{"value":1.0E-7,"zero":-0.0}}'
        second = b'{"recipe":{"large":100000000000000000001,"text":"line\\u000aend"}}'
        raw = b'{"preparation":{"recipes_before":[' + first + b',' + second + b']}}'
        capture = self.auxiliary(raw)
        original = {path: path.read_bytes() for path in (*self.root.iterdir(), self.inputs)}
        result = read_capture_payload(capture, 'auxiliary.json',
            record_arrays={'before': '/preparation/recipes_before'}, record_digest_fields={'before': ('recipe',)})
        self.assertEqual(digest(raw), result.file_sha256)
        self.assertEqual((digest(first), digest(second)), result.record_sha256['before'])
        self.assertEqual(({'recipe': digest(first[len(b'{"recipe":'):-1])},
                          {'recipe': digest(second[len(b'{"recipe":'):-1])}), result.record_field_sha256['before'])
        values = result.value['preparation']['recipes_before']
        self.assertEqual(1.0e-7, values[0]['recipe']['value'])
        self.assertEqual(100000000000000000001, values[1]['recipe']['large'])
        self.assertEqual('line\nend', values[1]['recipe']['text'])
        self.assertEqual(original, {path: path.read_bytes() for path in original})

    def test_auxiliary_pointer_escapes_array_indices_empty_fields_and_empty_arrays(self):
        capture = self.auxiliary(encoded({'a/b': {'~key': [{'': [{'recipe': 7}]}]}, 'empty': [], '~1': [{'z': 2}]}))
        result = read_capture_payload(capture, 'auxiliary.json',
            record_arrays={'nested': '/a~1b/~0key/0/', 'empty': '/empty', 'literal': '/~01'},
            record_digest_fields={'nested': ('recipe',)})
        self.assertEqual((digest(b'{"recipe":7}'),), result.record_sha256['nested'])
        self.assertEqual(({'recipe': digest(b'7')},), result.record_field_sha256['nested'])
        self.assertEqual((), result.record_sha256['empty'])
        self.assertEqual((digest(b'{"z":2}'),), result.record_sha256['literal'])
        self.assertEqual(({},), result.record_field_sha256['literal'])
        plain = read_capture_payload(capture, 'auxiliary.json')
        self.assertEqual({}, plain.record_sha256)
        self.assertEqual({}, plain.record_field_sha256)

    def test_auxiliary_reopens_descriptor_instead_of_trusting_mutable_capture_manifest(self):
        capture = self.auxiliary()
        descriptor = next(row for row in capture.manifest['payloads'] if row['file'] == 'auxiliary.json')
        descriptor.update(size=1, sha256='0' * 64)
        result = read_capture_payload(capture, 'auxiliary.json')
        self.assertEqual(7, result.value['preparation']['recipes_before'][0]['recipe']['value'])
        changed = b'{"preparation":{"recipes_before":[{"recipe":{"value":8}}]}}'
        (self.root / 'auxiliary.json').write_bytes(changed)
        descriptor.update(size=len(changed), sha256=digest(changed))
        with self.assertRaisesRegex(RuntimeCaptureError, 'digest/size mismatch'):
            read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_refuses_resealed_or_unsealed_replaced_manifest(self):
        capture = self.auxiliary()
        self.rewrite_manifest(lambda value: value.update(producer='another-producer'))
        with self.assertRaisesRegex(RuntimeCaptureError, 'manifest file hash differs'):
            read_capture_payload(capture, 'auxiliary.json')
        raw = (self.root / 'manifest.json').read_bytes().replace(b'another-producer', b'changed-producer')
        (self.root / 'manifest.json').write_bytes(raw)
        # Even a caller-supplied matching raw-file digest cannot bypass the document seal.
        forged = replace(capture, manifest_file_sha256=digest(raw))
        with self.assertRaisesRegex(RuntimeCaptureError, 'manifest_sha256'):
            read_capture_payload(forged, 'auxiliary.json')

    def test_auxiliary_requires_current_empty_seal_and_regular_files(self):
        capture = self.auxiliary()
        marker = self.root / '.capture-complete'
        marker.write_bytes(b'not-empty')
        with self.assertRaises(RuntimeCaptureError):
            read_capture_payload(capture, 'auxiliary.json')
        marker.unlink()
        with self.assertRaises(RuntimeCaptureError):
            read_capture_payload(capture, 'auxiliary.json')
        marker.write_bytes(b'')
        for name in ('auxiliary.json', 'manifest.json', '.capture-complete'):
            with self.subTest(name=name):
                member, saved = self.root / name, self.root.parent / name
                member.rename(saved)
                member.symlink_to(saved)
                with self.assertRaises(RuntimeCaptureError):
                    read_capture_payload(capture, 'auxiliary.json')
                member.unlink()
                saved.rename(member)
        saved_root = self.root.with_name('saved-capture')
        self.root.rename(saved_root)
        self.root.symlink_to(saved_root, target_is_directory=True)
        with self.assertRaises(RuntimeCaptureError):
            read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_refuses_unsafe_undeclared_paths_and_size_changes(self):
        capture = self.auxiliary()
        for name in ('../auxiliary.json', '/auxiliary.json', 'sub/auxiliary.json', 'sub\\auxiliary.json',
                     'manifest.json', '.capture-complete', 'missing.json', '', None):
            with self.subTest(name=name), self.assertRaises(RuntimeCaptureError):
                read_capture_payload(capture, name)
        path = self.root / 'auxiliary.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(RuntimeCaptureError, 'byte bound'):
            read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_manifest_read_keeps_its_fixed_byte_bound(self):
        capture = self.auxiliary()
        with (self.root / 'manifest.json').open('wb') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(RuntimeCaptureError, 'byte bound'):
            read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_refuses_noncanonical_objects_and_unrepresentable_numbers(self):
        for raw in (b'{"x": 1}', b'{"x":1}\n', b'[{}]', b'{"x":1,"x":2}', b'{"x":NaN}',
                    b'{"x":1e999}', b'{"x":1e-999}', b'{"x":"\\ud800"}', '{"x":1}'.encode('utf-16')):
            with self.subTest(raw=raw):
                capture = self.auxiliary(raw)
                with self.assertRaises(RuntimeCaptureError):
                    read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_refuses_malformed_pointers_nonobject_records_and_missing_fields(self):
        capture = self.auxiliary(encoded({'arrays': [[{'recipe': 1}], [{'other': 2}]], 'mixed': [{}, 7]}))
        for pointer in ('arrays', '#/arrays', '/arrays~', '/arrays~2', '/arrays/00', '/arrays/-',
                        '/arrays/+0', '/arrays/20', '/missing', '/mixed', '/arrays/0/0/recipe', ''):
            with self.subTest(pointer=pointer), self.assertRaises(RuntimeCaptureError):
                read_capture_payload(capture, 'auxiliary.json', record_arrays={'rows': pointer})
        with self.assertRaisesRegex(RuntimeCaptureError, 'field is absent'):
            read_capture_payload(capture, 'auxiliary.json', record_arrays={'rows': '/arrays/1'},
                                 record_digest_fields={'rows': ('recipe',)})
        for arrays, fields in [('rows', None), ({'': '/arrays/0'}, None), ({'rows': 1}, None),
                              ({'rows': '/arrays/0'}, {'missing': ('recipe',)}),
                              ({'rows': '/arrays/0'}, {'rows': 'recipe'}),
                              ({'rows': '/arrays/0'}, {'rows': ('recipe', 'recipe')})]:
            with self.subTest(arrays=arrays, fields=fields), self.assertRaises(RuntimeCaptureError):
                read_capture_payload(capture, 'auxiliary.json', record_arrays=arrays, record_digest_fields=fields)

    def test_auxiliary_detects_payload_replacement_before_readback_finishes(self):
        capture = self.auxiliary()
        path = self.root / 'auxiliary.json'
        inode = path.stat().st_ino
        original_fdopen = capture_module.os.fdopen

        class ReplacingStream:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
                # Windows refuses replacing an open file. Replace after close,
                # before the reader's final path observation, on both hosts.
                replacement = path.with_suffix('.replacement')
                replacement.write_bytes(self.raw)
                replacement.replace(path)
            def fileno(self):
                return self.stream.fileno()
            def read(self, count):
                self.raw = self.stream.read(count)
                return self.raw

        def opening(descriptor, *args, **kwargs):
            stream = original_fdopen(descriptor, *args, **kwargs)
            return ReplacingStream(stream) if capture_module.os.fstat(descriptor).st_ino == inode else stream

        with patch.object(capture_module.os, 'fdopen', side_effect=opening):
            with self.assertRaisesRegex(RuntimeCaptureError, 'changed while read'):
                read_capture_payload(capture, 'auxiliary.json')

    def test_auxiliary_detects_capture_changes_during_parsing(self):
        capture = self.auxiliary()
        raw = (self.root / 'auxiliary.json').read_bytes()
        original = capture_module._canonical_document

        def parse(value):
            result = original(value)
            if value == raw:
                (self.root / '.capture-complete').unlink()
            return result

        with patch.object(capture_module, '_canonical_document', side_effect=parse):
            with self.assertRaises(RuntimeCaptureError):
                read_capture_payload(capture, 'auxiliary.json')


if __name__ == '__main__':
    unittest.main()
