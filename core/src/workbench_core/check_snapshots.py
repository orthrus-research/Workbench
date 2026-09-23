"""Core publication, recovery and reads for complete retained check snapshots.

Domain owners verify source meaning and supply section declarations. Core owns
files, hashes, publication and leases. No native execution or retention policy is
implied. Legacy raw results are preserved until their owner explicitly retires them.
"""

import base64
from contextlib import contextmanager
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
import zlib

from . import check_snapshot_contract as contract
from . import check_snapshot_index as index
from . import check_storage as storage
from .host_filesystem import fsync_directory

DIRECTORY = 'snapshot'
PUBLICATION = 'workbench-check-snapshot-publication-v1'
INDEX = 'workbench-check-snapshot-index-v2'


class SnapshotError(ValueError):
    pass


class SnapshotCancelled(SnapshotError):
    pass


class SnapshotBusy(SnapshotError):
    pass


def _cancel(cancelled):
    if cancelled():
        raise SnapshotCancelled('snapshot operation cancelled')


def file_content(path, cancelled=lambda: False):
    path = storage.ordinary(Path(path))
    before = path.stat()
    digest, size = sha256(), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            _cancel(cancelled)
            digest.update(block)
            size += len(block)
    after = path.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise SnapshotError('snapshot file changed during verification')
    return {'sha256': digest.hexdigest(), 'bytes': size}


@contextmanager
def _lease(attempt, *, shared=False):
    from . import check_lifecycle
    attempt = Path(attempt)
    with check_lifecycle.lease(attempt.parent.parent.parent):
        with _attempt_lease(attempt, shared=shared):
            yield


@contextmanager
def _attempt_lease(attempt, *, shared=False):
    # Same Linux custody primitive as Core check execution. Collection must honor
    # this lease too; a busy read/build is never evidence that deletion is safe.
    import fcntl
    attempt = storage.ordinary(Path(attempt), directory=True)
    path = attempt / 'snapshot.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        storage.ordinary(path)
        if os.fstat(descriptor).st_ino != path.stat().st_ino:
            raise SnapshotError('snapshot lease changed')
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SnapshotBusy('snapshot has an active reader or writer') from exc
        yield
    finally:
        os.close(descriptor)


def _sealed(path, kind):
    value = storage.read_json(path, byte_limit=None)
    if not isinstance(value, dict) or storage.seal(kind, {k: v for k, v in value.items() if k != 'id'}) != value:
        raise SnapshotError('snapshot metadata identity differs')
    return value


def _write(path, value):
    storage.write_json(path, value, byte_limit=None)


def _replace(path, value):
    storage.ordinary(path)
    temporary = path.parent / ('.pointer-' + uuid4().hex)
    try:
        _write(temporary, value)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _views(views, scope):
    if not isinstance(views, list) or [v['id'] for v in views] != sorted(scope['sections']):
        raise SnapshotError('snapshot views differ from selected scope')
    for view in views:
        if (set(view) != {'id', 'schema', 'required', 'state', 'dependencies', 'reason', 'path', 'records'}
                or view['required'] != scope['sections'][view['id']]
                or view['records'] not in {'single', 'mapping', 'sequence'}):
            raise SnapshotError('invalid snapshot section declaration')
        path = view['path']
        if path is not None and (not isinstance(path, list) or any(
                not isinstance(p, str) and not (type(p) is int and p >= 0) for p in path)):
            raise SnapshotError('invalid snapshot logical path')
        if (view['state'] == 'observed' and path is None
                or view['state'] in {'not-applicable', 'unavailable'} and path is not None):
            raise SnapshotError('snapshot section state differs from its content')


def _sections(views, observed):
    return [{**{key: view[key] for key in ('id', 'schema', 'required', 'state', 'dependencies', 'reason')},
             **observed.get(view['id'], {'count': None, 'content': None})} for view in views]


def _operation(attempt, operation):
    directory = attempt / 'snapshot-operations'
    if not directory.exists():
        directory.mkdir(mode=0o700)
        fsync_directory(attempt)
    storage.ordinary(directory, directory=True)
    journal = directory / uuid4().hex
    journal.mkdir(mode=0o700)
    _write(journal / 'started.json', storage.seal('check-snapshot-operation', {
        'format': 'workbench-check-snapshot-operation-v1', 'operation': operation,
        'attempt': attempt.name, 'operation_id': journal.name, 'staging': 'staging'}))
    stage = journal / 'staging'
    stage.mkdir(mode=0o700)
    fsync_directory(journal)
    return journal, stage


def _failed(journal, error):
    # If the filesystem cannot retain a failure receipt, started.json still
    # records the incomplete operation for recovery; preserve the original error.
    try:
        _write(journal / 'failed.json', {'state': 'incomplete', 'type': type(error).__name__, 'message': str(error)})
    except OSError:
        pass


def _completed(journal, identity):
    _write(journal / 'completed.json', {'state': 'completed', 'snapshot_id': identity})


def _archive(source, destination, expected, cancelled):
    digest, size = sha256(), 0
    with storage.ordinary(source).open('rb') as incoming, destination.open('xb') as output:
        os.chmod(destination, 0o600)
        with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0, compresslevel=6) as archive:
            for block in iter(lambda: incoming.read(1024 * 1024), b''):
                _cancel(cancelled)
                digest.update(block)
                size += len(block)
                archive.write(block)
        output.flush()
        os.fsync(output.fileno())
    if {'sha256': digest.hexdigest(), 'bytes': size} != expected:
        raise SnapshotError('original result changed during archiving')
    _verify_archive(destination, expected, cancelled)
    return file_content(destination, cancelled)


def _verify_archive(path, expected, cancelled):
    digest, size = sha256(), 0
    with gzip.open(storage.ordinary(path), 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            _cancel(cancelled)
            digest.update(block)
            size += len(block)
            if size > expected['bytes']:
                raise SnapshotError('archive exceeds its declared content')
    if digest.hexdigest() != expected['sha256'] or size != expected['bytes']:
        raise SnapshotError('archive content differs from original result')


def _index_record(directory, manifest, views, cancelled):
    path = directory / 'query.sqlite3'
    reader = index.Reader(path, lambda: _cancel(cancelled))
    try:
        reader.verify_document()
    finally:
        reader.close()
    with path.open('rb') as stream:
        os.fsync(stream.fileno())
    os.chmod(path, 0o400)
    record = storage.seal('check-snapshot-index', {
        'format': INDEX, 'layout': index.LAYOUT, 'snapshot_id': manifest['id'],
        'generation': directory.name, 'page_bytes': index.PAGE_BYTES,
        'builder_sha256': sha256(Path(index.__file__).read_bytes()).hexdigest(),
        'inputs': _index_inputs(manifest, views),
        'file': file_content(path, cancelled)})
    _write(directory / 'index.json', record)
    fsync_directory(directory)
    return storage.seal('check-snapshot-index-selection', {
        'snapshot_id': manifest['id'], 'generation': directory.name, 'index_id': record['id']})


def _index_inputs(manifest, views):
    return {'source_result': manifest['source_result'],
            'views_sha256': sha256(storage.canonical(views)).hexdigest(),
            'converter': 'workbench-check-json-index-v1'}


def _scope(scope, manifest):
    # A resolver is trusted owner code, not a scope inferred from observed rows.
    return scope(manifest) if callable(scope) else scope


def publish(attempt, source, *, scope, describe, verify, cancelled=lambda: False):
    """Retain a verified result. Callbacks are supplied by its authorized owner.

    describe(value) returns (manifest fields, section views, small summary).
    verify(value) must establish selected request/source/native relationships.
    An existing publication is never overwritten, including after cancellation.
    """
    attempt, source = Path(attempt), Path(source)
    with _lease(attempt):
        if (attempt / DIRECTORY).exists() or (attempt / DIRECTORY).is_symlink():
            raise SnapshotError('snapshot already published')
        journal, stage = _operation(attempt, 'publish')
        try:
            _cancel(cancelled)
            original = file_content(source, cancelled)
            value = storage.read_json(source, byte_limit=None)
            verify(value)
            fields, views, summary = describe(value)
            _views(views, scope)
            archives = _archive(source, stage / 'result.json.gz', original, cancelled)
            generations = stage / 'indexes'
            generations.mkdir(mode=0o700)
            generation = generations / uuid4().hex
            generation.mkdir(mode=0o700)
            observed = index.build(generation / 'query.sqlite3', value, views, lambda: _cancel(cancelled))
            # Bind to the same parsed input even if a source file changed during
            # callbacks or indexing. Domain custody is rechecked before commit.
            verify(value)
            if file_content(source, cancelled) != original:
                raise SnapshotError('original result changed during publication')
            manifest = contract.seal_snapshot({**fields, 'format': 'workbench-check-snapshot-v1',
                'schema_version': 1, 'source_result': {**original, 'media_type': 'application/json'},
                'scope_id': contract.scope_identity(scope), 'sections': _sections(views, observed)}, scope)
            pointer = _index_record(generation, manifest, views, cancelled)
            publication = storage.seal('check-snapshot-publication', {
                'format': PUBLICATION, 'manifest': manifest, 'views': views, 'summary': summary,
                'archive': archives, 'layout': index.LAYOUT})
            _write(stage / 'publication.json', publication)
            _write(stage / 'current.json', pointer)
            fsync_directory(generations)
            fsync_directory(stage)
            _cancel(cancelled)
            os.rename(stage, attempt / DIRECTORY)
            fsync_directory(attempt)
            _completed(journal, manifest['id'])
            return manifest
        except BaseException as exc:
            _failed(journal, exc)
            raise


def _publication(attempt, scope, expected, cancelled):
    directory = storage.ordinary(attempt / DIRECTORY, directory=True)
    value = _sealed(directory / 'publication.json', 'check-snapshot-publication')
    if set(value) != {'id', 'format', 'manifest', 'views', 'summary', 'archive', 'layout'} or value['format'] != PUBLICATION:
        raise SnapshotError('unsupported snapshot publication')
    selected = _scope(scope, value['manifest'])
    manifest = (contract.validate_envelope(value['manifest']) if selected is None
                else contract.validate_snapshot(value['manifest'], selected))
    if any(manifest.get(key) != item for key, item in expected.items()):
        raise SnapshotError('snapshot differs from selected request or inputs')
    _views(value['views'], {'sections': {s['id']: s['required'] for s in manifest['sections']}})
    for view, section in zip(value['views'], manifest['sections']):
        if any(view[key] != section[key] for key in ('id', 'schema', 'required', 'state', 'dependencies', 'reason')):
            raise SnapshotError('snapshot views contradict retained sections')
    if file_content(directory / 'result.json.gz', cancelled) != value['archive']:
        raise SnapshotError('snapshot archive is missing or changed')
    # Transitional legacy evidence is still authoritative while retained.
    source = attempt / 'result.json'
    if source.exists() or source.is_symlink():
        if file_content(source, cancelled) != {k: manifest['source_result'][k] for k in ('sha256', 'bytes')}:
            raise SnapshotError('retained original result changed')
    return value


class Snapshot:
    """Read lease for an owner-selected snapshot; closes on context exit."""
    def __init__(self, attempt, *, scope, expected, supported_schemas=None, cancelled=lambda: False):
        self.attempt, self.scope, self.expected = Path(attempt), scope, expected
        self.cancelled, self.reader = cancelled, None
        self.lease = None
        self.supported_schemas = supported_schemas

    def __enter__(self):
        self.lease = _lease(self.attempt, shared=True)
        self.lease.__enter__()
        try:
            self.publication = _publication(self.attempt, self.scope, self.expected, self.cancelled)
            self.manifest = self.publication['manifest']
            self.scope_supported = _scope(self.scope, self.manifest) is not None
            self.unsupported_sections = (set(contract.reader_coverage(self.manifest, self.supported_schemas)['affected_sections'])
                                         if self.supported_schemas is not None else set())
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        if self.lease is not None:
            self.lease.__exit__(*args)
            self.lease = None

    def _reader(self):
        if self.lease is None:
            raise SnapshotError('snapshot read lease is closed')
        if self.reader is None:
            root = self.attempt / DIRECTORY
            pointer = _sealed(root / 'current.json', 'check-snapshot-index-selection')
            if (set(pointer) != {'id', 'snapshot_id', 'generation', 'index_id'}
                    or pointer['snapshot_id'] != self.manifest['id']
                    or not re.fullmatch('[0-9a-f]{32}', pointer['generation'])):
                raise SnapshotError('snapshot index selection differs')
            directory = storage.ordinary(root / 'indexes' / pointer['generation'], directory=True)
            record = _sealed(directory / 'index.json', 'check-snapshot-index')
            version = record.get('format')
            keys = {'id', 'format', 'layout', 'snapshot_id', 'generation', 'page_bytes', 'builder_sha256', 'file'}
            if version == INDEX:
                keys.add('inputs')
            if (set(record) != keys
                    or version not in {INDEX, 'workbench-check-snapshot-index-v1'} or record['layout'] != index.LAYOUT
                    or record['page_bytes'] != index.PAGE_BYTES or record['generation'] != directory.name
                    or record['snapshot_id'] != self.manifest['id'] or record['id'] != pointer['index_id']):
                raise SnapshotError('snapshot index needs a supported rebuild')
            if version == INDEX and record['inputs'] != _index_inputs(self.manifest, self.publication['views']):
                raise SnapshotError('snapshot index converter inputs differ')
            if file_content(directory / 'query.sqlite3', self.cancelled) != record['file']:
                raise SnapshotError('snapshot index is corrupt; rebuild from the verified archive')
            self.reader = index.Reader(directory / 'query.sqlite3', lambda: _cancel(self.cancelled))
        return self.reader

    def _response_fields(self, query, *, payload=None, offset=None, state='ready', reason=None):
        cursor = None if offset is None else {'snapshot_id': self.manifest['id'],
            'query_id': contract.query_identity(query), 'offset': offset}
        return {'format': 'workbench-check-snapshot-response-v1', 'snapshot_id': self.manifest['id'],
            'view_id': query['view_id'], 'query_id': contract.query_identity(query),
            'request_id': contract.read_identity(query), 'state': state, 'payload': payload,
            'complete': state == 'ready' and cursor is None, 'next_cursor': cursor, 'reason': reason}

    def _response(self, query, **fields):
        return contract.validate_response(self._response_fields(query, **fields), query, self.manifest)

    def query(self, query):
        contract.validate_query(query, self.manifest)
        if self.lease is None:
            raise SnapshotError('snapshot read lease is closed')
        try:
            _cancel(self.cancelled)
            result = self._query(query)
            if len(storage.canonical(result)) > query['preferred_bytes']:
                return self._response(query, state='incomplete', reason='Presentation preference cannot contain the response envelope.')
            return result
        except SnapshotCancelled as exc:
            result = self._response(query, state='cancelled', reason=str(exc))
            self.__exit__(None, None, None)
            return result
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, zlib.error) as exc:
            return self._response(query, state='unavailable', reason=str(exc))

    def _query(self, query):
        operation = query['operation']
        offset = 0 if query['cursor'] is None else query['cursor']['offset']
        if operation == 'summary':
            return self._response(query, payload={'summary': self.publication['summary'],
                'native_outcome': self.manifest['native_outcome'], 'coverage': self.manifest['coverage'],
                'scope_supported': self.scope_supported,
                'reader_coverage': None if self.supported_schemas is None else {
                    **contract.reader_coverage(self.manifest, self.supported_schemas),
                    'required_interpretation_complete': self.scope_supported and
                        contract.reader_coverage(self.manifest, self.supported_schemas)['required_interpretation_complete']},
                'result_id': self.manifest['result_id'], 'bindings': self.manifest['bindings']})
        if operation == 'sections':
            return self._page(query, self.manifest['sections'], offset, 'sections')
        if not self.scope_supported or query['section_id'] in self.unsupported_sections:
            return self._response(query, state='unsupported', reason='Historical section scope is unsupported; original evidence can be exported.')
        section = next(item for item in self.manifest['sections'] if item['id'] == query['section_id'])
        if section['state'] != 'observed':
            return self._response(query, state='incomplete' if section['state'] == 'incomplete' else 'unavailable',
                                  reason=section['reason'] or 'Section content is unavailable.')
        reader = self._reader()
        if operation == 'records':
            total = section['count']
            if offset > total:
                raise SnapshotError('record cursor exceeds section count')
            # Fetch candidates in small batches; the presentation preference
            # controls only this response, never how many records are retained.
            def items():
                position = offset
                while position < total:
                    rows = reader.records(section['id'], position, 64)
                    if not rows:
                        raise SnapshotError('snapshot record page is missing')
                    for row in rows:
                        if row[1] != position or position >= total:
                            raise SnapshotError('snapshot record order or count differs')
                        yield self._record_value(reader, row, query['preferred_bytes'])
                        position += 1
            return self._page(query, items(), offset, 'records', total=total)
        row = reader.record(section['id'], query['record_key'])
        if operation == 'record':
            return self._response(query, payload=self._record_value(reader, row, query['preferred_bytes']))
        if row[4] != query['blob_sha256'] or offset > row[3]:
            raise SnapshotError('blob digest or range differs from the selected record')
        length = min(row[3] - offset, max(0, query['preferred_bytes'] * 3 // 4))
        while True:
            raw = b''.join(reader.ranges(row[2] + offset, length))
            result = self._response(query, payload={'data': base64.b64encode(raw).decode('ascii'),
                'offset': offset, 'total_bytes': row[3], 'sha256': row[4],
                'chunk_sha256': sha256(raw).hexdigest()},
                offset=offset + length if offset + length < row[3] else None)
            if len(storage.canonical(result)) <= query['preferred_bytes']:
                return result
            if length == 0:
                return self._response(query, state='incomplete', reason='Presentation preference cannot contain a blob chunk.')
            length //= 2
            if length == 0 and offset < row[3]:
                return self._response(query, state='incomplete', reason='Presentation preference cannot contain a blob chunk.')

    def _record_value(self, reader, row, preference):
        reference = {'sha256': row[4], 'bytes': row[3], 'media_type': 'application/json'}
        # Leave room for metadata; _page checks the complete encoded response.
        if row[3] > preference // 2:
            return {'key': row[0], 'content': reference}
        return {'key': row[0], 'value': reader.value(row)}

    def read_record(self, section, key):
        """Explicit owner-side full detail, outside a paged client response.

        Routine clients use query() and content references. This supports owners
        that need one complete diagnostic to verify/navigate retained source.
        """
        if not self.scope_supported or section in self.unsupported_sections:
            raise SnapshotError('historical section scope is unsupported')
        descriptor = next((item for item in self.manifest['sections'] if item['id'] == section), None)
        if descriptor is None or descriptor['state'] != 'observed':
            raise SnapshotError('snapshot section is unavailable')
        reader = self._reader()
        return reader.value(reader.record(section, key))

    def _page(self, query, items, offset, field, *, total=None):
        if total is None:
            total = len(items)
            if offset > total:
                raise SnapshotError('cursor exceeds section count')
            items = iter(items[offset:])
        page, encoded_items = [], 0
        for item in items:
            position = offset + len(page) + 1
            # The empty array supplies exact envelope/cursor overhead only. It
            # is never returned as a response. Encode each complete item once;
            # validate the final populated response below.
            envelope = self._response_fields(query, payload={field: [], 'offset': offset, 'total': total},
                                             offset=position if position < total else None)
            candidate_bytes = encoded_items + len(storage.canonical(item)) + (1 if page else 0)
            if len(storage.canonical(envelope)) + candidate_bytes > query['preferred_bytes']:
                if not page:
                    return self._response(query, state='incomplete', reason='Presentation preference cannot contain one record reference.')
                break
            page.append(item)
            encoded_items = candidate_bytes
        position = offset + len(page)
        return self._response(query, payload={field: page, 'offset': offset, 'total': total},
                              offset=position if position < total else None)

    def export(self, destination):
        """Stream and verify the complete original into a fresh caller-selected file."""
        if self.lease is None:
            raise SnapshotError('snapshot read lease is closed')
        destination = Path(destination)
        storage.ordinary(destination.parent, directory=True)
        expected = self.manifest['source_result']
        digest, size = sha256(), 0
        # Publish only verified complete bytes, preserving any existing target.
        temporary = destination.parent / ('.snapshot-export-' + uuid4().hex)
        try:
            with temporary.open('xb') as output, gzip.open(self.attempt / DIRECTORY / 'result.json.gz', 'rb') as incoming:
                os.chmod(temporary, 0o600)
                for block in iter(lambda: incoming.read(1024 * 1024), b''):
                    _cancel(self.cancelled)
                    digest.update(block)
                    size += len(block)
                    if size > expected['bytes']:
                        raise SnapshotError('archive exceeds declared original result')
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
                raise SnapshotError('archive export content differs')
            os.link(temporary, destination)
            fsync_directory(destination.parent)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {**file_content(destination), 'snapshot_id': self.manifest['id']}

    def export_record(self, section, key, expected_sha256, destination):
        """Write one complete selected JSON value without a client-sized string."""
        descriptor = next((item for item in self.manifest['sections'] if item['id'] == section), None)
        if descriptor is None or descriptor['state'] != 'observed':
            raise SnapshotError('snapshot section is unavailable')
        reader = self._reader()
        row = reader.record(section, key)
        if row[4] != expected_sha256:
            raise SnapshotError('selected record content differs')
        destination = Path(destination)
        storage.ordinary(destination.parent, directory=True)
        temporary = destination.parent / ('.snapshot-export-' + uuid4().hex)
        digest, size = sha256(), 0
        try:
            with temporary.open('xb') as output:
                os.chmod(temporary, 0o600)
                for block in reader.ranges(row[2], row[3]):
                    _cancel(self.cancelled)
                    output.write(block)
                    digest.update(block)
                    size += len(block)
                output.flush()
                os.fsync(output.fileno())
            if size != row[3] or digest.hexdigest() != row[4]:
                raise SnapshotError('exported record content differs')
            os.link(temporary, destination)
            fsync_directory(destination.parent)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {**file_content(destination), 'snapshot_id': self.manifest['id'],
                'section_id': section, 'record_key': key}


def rebuild(attempt, *, scope, expected, cancelled=lambda: False):
    """Rebuild from the complete archive, independent of an old database/spool."""
    attempt = Path(attempt)
    with _lease(attempt):
        publication = _publication(attempt, scope, expected, cancelled)
        if _scope(scope, publication['manifest']) is None:
            raise SnapshotError('historical section scope is unsupported; cannot rebuild its views')
        if publication['layout'] != index.LAYOUT:
            raise SnapshotError('unsupported retained snapshot layout')
        manifest = publication['manifest']
        journal, stage = _operation(attempt, 'rebuild')
        try:
            root = attempt / DIRECTORY
            _verify_archive(root / 'result.json.gz', manifest['source_result'], cancelled)
            with gzip.open(root / 'result.json.gz', 'rb') as stream:
                value = json.load(stream)
            observed = index.build(stage / 'query.sqlite3', value, publication['views'], lambda: _cancel(cancelled))
            if _sections(publication['views'], observed) != manifest['sections']:
                raise SnapshotError('rebuilt sections differ from immutable snapshot')
            # Deterministic operation-to-generation custody survives a crash
            # between moving the database and switching the current pointer.
            generation = root / 'indexes' / journal.name
            _cancel(cancelled)
            os.rename(stage, generation)
            pointer = _index_record(generation, manifest, publication['views'], cancelled)
            fsync_directory(generation.parent)
            _cancel(cancelled)
            if (root / 'current.json').exists():
                _replace(root / 'current.json', pointer)
            else:
                _write(root / 'current.json', pointer)
            _completed(journal, manifest['id'])
            return pointer
        except BaseException as exc:
            _failed(journal, exc)
            raise


def recover(attempt, *, scope=None, expected=None):
    """Reconcile abandoned staging, keeping committed generations and all journals.

    Only recognized staging files under a bound operation are discarded. Unknown
    paths/symlinks refuse cleanup. Historical archives and indexes are never purged.
    """
    attempt = Path(attempt)
    with _lease(attempt):
        operations = attempt / 'snapshot-operations'
        if not operations.exists():
            return []
        storage.ordinary(operations, directory=True)
        results = []
        for journal in sorted(operations.iterdir()):
            storage.ordinary(journal, directory=True)
            started = _sealed(journal / 'started.json', 'check-snapshot-operation')
            if (started.get('attempt') != attempt.name or started.get('operation_id') != journal.name
                    or started.get('staging') != 'staging' or started.get('operation') not in {'publish', 'rebuild'}
                    or not re.fullmatch('[0-9a-f]{32}', journal.name)):
                raise SnapshotError('unrecognized snapshot operation custody')
            stage = journal / 'staging'
            if stage.exists() or stage.is_symlink():
                storage.ordinary(stage, directory=True)
                paths = list(stage.rglob('*'))
                for path in paths:
                    storage.ordinary(path, directory=path.is_dir())
                    relative = path.relative_to(stage).as_posix()
                    if not (relative in {'query.sqlite3', 'query.sqlite3-journal', 'result.json.gz', 'publication.json', 'current.json', 'indexes'}
                            or re.fullmatch(r'indexes/[0-9a-f]{32}(?:/(?:query\.sqlite3(?:-journal)?|index\.json))?', relative)):
                        raise SnapshotError('unknown file in snapshot staging; no cleanup performed')
                released = sum(path.stat().st_blocks * 512 for path in paths if path.is_file())
                for path in sorted(paths, key=lambda p: len(p.parts), reverse=True):
                    path.rmdir() if path.is_dir() else path.unlink()
                stage.rmdir()
                fsync_directory(journal)
                if not (journal / 'recovered.json').exists():
                    _write(journal / 'recovered.json', {'state': 'discarded-unpublished-staging', 'allocated_bytes_released': released})
                results.append({'operation_id': journal.name, 'state': 'discarded-unpublished-staging', 'allocated_bytes_released': released})
            elif not (journal / 'completed.json').exists():
                if scope is None or expected is None:
                    results.append({'operation_id': journal.name, 'state': 'requires-selected-snapshot-verification'})
                    continue
                publication = _publication(attempt, scope, expected, lambda: False)
                root = attempt / DIRECTORY
                pointer = _sealed(root / 'current.json', 'check-snapshot-index-selection')
                if pointer.get('snapshot_id') != publication['manifest']['id']:
                    raise SnapshotError('recovery index selection differs from snapshot')
                selected = started['operation'] == 'publish' or pointer.get('generation') == journal.name
                if selected:
                    generation = pointer.get('generation')
                    if not isinstance(generation, str) or not re.fullmatch('[0-9a-f]{32}', generation):
                        raise SnapshotError('recovery index generation differs')
                    directory = root / 'indexes' / generation
                    record = _sealed(directory / 'index.json', 'check-snapshot-index')
                    if (record.get('id') != pointer.get('index_id')
                            or record.get('snapshot_id') != publication['manifest']['id']
                            or file_content(directory / 'query.sqlite3') != record.get('file')):
                        raise SnapshotError('recovery cannot verify selected index')
                    _verify_archive(root / 'result.json.gz', publication['manifest']['source_result'], lambda: False)
                    _completed(journal, publication['manifest']['id'])
                    results.append({'operation_id': journal.name, 'state': 'reconciled-published-snapshot'})
                else:
                    # An unselected moved rebuild remains explicitly discoverable;
                    # it is neither a new snapshot nor automatic purge authority.
                    orphan = root / 'indexes' / journal.name
                    results.append({'operation_id': journal.name, 'state': 'unselected-index-generation',
                                    'generation': str(orphan), 'exists': orphan.exists()})
        return results
