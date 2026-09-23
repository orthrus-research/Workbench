"""Read registered check evidence through Core custody and producer admission.

This opens existing evidence only. It does not publish, rebuild, execute, restore
or infer which workspace the caller meant. A caller selects an exact snapshot.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from workbench_api.retained_snapshots import RetainedSnapshotAdmission, RetainedSnapshotProvider
from . import check_lifecycle as lifecycle
from . import check_snapshots as snapshots
from . import check_storage as storage
from .check_snapshot_contract import scope_identity


class _Inputs:
    def __init__(self, attempt, custody, cancelled):
        self.attempt = attempt
        self.attempt_id = attempt.name
        self.context = dict(custody['context'])
        self.custody = custody
        self.cancelled = cancelled

    seal = staticmethod(storage.seal)
    scope_identity = staticmethod(scope_identity)

    def _bytes(self, name):
        snapshots._cancel(self.cancelled)
        name = str(storage.safe_path(name))
        expected = self.custody['files'].get(name)
        if expected is None:
            raise snapshots.SnapshotError('selected input is outside retained custody: ' + name)
        path = storage.ordinary(self.attempt / name)
        before = path.stat()
        chunks, size = [], 0
        with path.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                snapshots._cancel(self.cancelled)
                size += len(block)
                if size > expected['bytes']:
                    raise snapshots.SnapshotError('retained input grew beyond its custody binding: ' + name)
                chunks.append(block)
        raw = b''.join(chunks)
        after = path.stat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (stamp(before) != stamp(after)
                or {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)} != expected):
            raise snapshots.SnapshotError('retained input changed: ' + name)
        return raw

    def read_input(self, role):
        name = self.custody['inputs'].get(role)
        if name is None:
            raise snapshots.SnapshotError('required retained input role is absent: ' + role)
        return self._bytes(name)

    def source_files(self, directory, files):
        """Verify exact source membership, bytes and modes; reject added files."""
        directory = str(storage.safe_path(directory))
        root = storage.ordinary(self.attempt / directory, directory=True)
        expected = {}
        folded = set()
        if not isinstance(files, list):
            raise snapshots.SnapshotError('saved source manifest is malformed')
        for row in files:
            if (not isinstance(row, dict) or set(row) != {'path', 'mode', 'size', 'sha256'}
                    or type(row['mode']) is not int or row['mode'] not in (0o100644, 0o100755)
                    or type(row['size']) is not int or row['size'] < 0):
                raise snapshots.SnapshotError('saved source file descriptor is malformed')
            name = str(storage.safe_path(row['path']))
            if name.casefold() in folded:
                raise snapshots.SnapshotError('saved source paths collide')
            expected[name] = row
            folded.add(name.casefold())
        if list(expected) != sorted(expected):
            raise snapshots.SnapshotError('saved source manifest is not ordered')
        result = {}
        for base, directories, names in os.walk(root, followlinks=False):
            snapshots._cancel(self.cancelled)
            for name in directories:
                storage.ordinary(Path(base) / name, directory=True)
            for name in names:
                path = Path(base) / name
                relative = path.relative_to(root).as_posix()
                row = expected.get(relative)
                if row is None:
                    raise snapshots.SnapshotError('retained source has an unexpected file: ' + relative)
                raw = self._bytes(directory + '/' + relative)
                if (stat.S_IMODE(storage.ordinary(path).stat().st_mode) != row['mode'] & 0o777
                        or len(raw) != row['size'] or sha256(raw).hexdigest() != row['sha256']):
                    raise snapshots.SnapshotError('retained source differs from candidate: ' + relative)
                result[relative] = raw
        if set(result) != set(expected):
            raise snapshots.SnapshotError('retained source is missing a candidate file')
        return result


class _Reader:
    def __init__(self, opened, request, custody):
        self._opened = opened
        self.request, self.custody = request, custody
        self.manifest = opened.manifest
        self.scope_supported = opened.scope_supported
        self.unsupported_sections = opened.unsupported_sections

    def read_record(self, section, key):
        return self._opened.read_record(section, key)

    def query(self, query):
        return self._opened.query(query)


def provider():
    """Expose the Core custody reader through the installed API provider port."""
    return RetainedSnapshotProvider(open_retained_snapshot)


@contextmanager
def open_retained_snapshot(attempt, *, snapshot_id=None, owner_id, admit,
                           expected_context=None, cancelled=lambda: False):
    """Yield a leased reader after verifying custody and injected owner policy.

`admit(request, inputs)` is an installed owner's historical contract reader,
not a callback selected by retained data. Profile/module admission is the
    composition caller's responsibility. The exact attempt and owner are required.
    An omitted snapshot ID selects the verified publication at that exact path.
"""
    attempt = storage.ordinary(Path(attempt).absolute(), directory=True)
    root = attempt.parent.parent.parent
    if attempt != root / '.workbench/check-attempts' / lifecycle._identity(attempt.name):
        raise snapshots.SnapshotError('select an exact Core retained check attempt')
    if not isinstance(owner_id, str) or not owner_id or not callable(admit):
        raise snapshots.SnapshotError('retained owner admission is required')
    with snapshots._lease(attempt, shared=True):
        snapshots._cancel(cancelled)
        custody = lifecycle._validate(lifecycle._sealed(attempt / lifecycle.MANIFEST, 'check-custody'), root)
        ledger = lifecycle._ledger(root) / (attempt.name + '.json')
        if ledger.exists() or ledger.is_symlink():
            if lifecycle._validate(lifecycle._sealed(ledger, 'check-custody'), root) != custody:
                raise snapshots.SnapshotError('registered custody differs from selected attempt')
        if snapshot_id is None:
            snapshot_id = custody['snapshot_id']
        if custody['snapshot_id'] != snapshot_id or custody['context'].get('owner') != owner_id:
            raise snapshots.SnapshotError('retained custody differs from selected snapshot or owner')
        if any(custody['context'].get(k) != v for k, v in (expected_context or {}).items()):
            raise snapshots.SnapshotError('retained custody differs from selected context')
        lifecycle.verify_payload(attempt, custody)
        for name, content in custody['files'].items():
            if snapshots.file_content(attempt / name, cancelled) != content:
                raise snapshots.SnapshotError('required retained evidence changed: ' + name)
        inputs = _Inputs(attempt, custody, cancelled)
        request = json.loads(inputs.read_input('request'))
        admission = admit(request, inputs)
        if not isinstance(admission, RetainedSnapshotAdmission):
            raise snapshots.SnapshotError('owner returned an invalid retained admission')
        expected = {**admission.expected, 'id': snapshot_id, 'attempt_id': attempt.name}
        with snapshots.Snapshot(attempt, scope=admission.scope, expected=expected,
                supported_schemas=admission.supported_schemas, cancelled=cancelled) as opened:
            if opened.manifest['producer']['id'] != owner_id:
                raise snapshots.SnapshotError('snapshot producer differs from selected owner')
            # Verify the archive's decoded identity too; a valid compressed-file
            # digest alone does not establish the declared original result.
            snapshots._verify_archive(attempt / 'snapshot/result.json.gz',
                                      opened.manifest['source_result'], cancelled)
            if set(custody['inputs']) != {row['role'] for row in opened.manifest['retained_inputs']}:
                raise snapshots.SnapshotError('retained input roles differ from snapshot')
            for row in opened.manifest['retained_inputs']:
                descriptor = custody['files'][custody['inputs'][row['role']]]
                if descriptor != {k: row['content'][k] for k in ('sha256', 'bytes')}:
                    raise snapshots.SnapshotError('retained input differs from snapshot binding')
            snapshots._cancel(cancelled)
            yield _Reader(opened, request, custody)
