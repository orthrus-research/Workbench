"""Core custody of retained checks, using the existing storage transaction engine.

Registration and pins are separate from immutable producer receipts. The registry
is rebuildable from supported custody manifests; it is never missing evidence.
No age/count/byte policy is activated by this module.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from . import check_storage as files
from .host_filesystem import file_lease, fsync_directory

FORMAT = 'workbench-check-custody-v1'
MANIFEST = '.workbench-check-custody-v1.json'
_HELD = ContextVar('check_store_leases', default={})


def _manager():
    from .storage import manager
    return manager


def _snapshots():
    from . import check_snapshots
    return check_snapshots


def _root(root):
    return _manager()._workspace(Path(root))[0]


def _ledger(root):
    return root / '.workbench/runtime-manager/checks'


def _identity(identity):
    if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9-]*-[0-9a-f]{32}', identity):
        raise ValueError('select an exact retained check attempt')
    return identity


def _sealed(path, kind):
    return _snapshots()._sealed(path, kind)


def _write(path, value):
    if path.exists():
        _snapshots()._replace(path, value)
    else:
        files.write_json(path, value, byte_limit=None)


@contextmanager
def lease(root, *, exclusive=False):
    """Store lease precedes attempt leases and survives retirement's rename.

    Shared readers/publishers can coexist. Collection and pin changes require an
    exclusive lease. Context-local nesting never upgrades a live shared lease.
    """
    root = _root(root)
    key = str(root)
    held = _HELD.get()
    if key in held:
        if exclusive and not held[key]:
            raise ValueError('cannot collect during an active check operation')
        yield
        return
    directory = root / '.workbench/runtime-manager'
    _manager()._assert_no_symlink_ancestors(directory, root)
    directory.mkdir(parents=True, exist_ok=True)
    files.ordinary(directory, directory=True)
    path = directory / 'checks.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        files.ordinary(path)
        if os.fstat(descriptor).st_ino != path.stat().st_ino:
            raise ValueError('check storage lease changed')
        with ExitStack() as locks:
            try:
                locks.enter_context(file_lease(descriptor, exclusive=exclusive))
            except BlockingIOError as exc:
                raise ValueError('check storage has an active reader, writer or collector') from exc
            token = _HELD.set({**held, key: exclusive})
            try:
                yield
            finally:
                _HELD.reset(token)
    finally:
        os.close(descriptor)


def busy(root):
    """Read-only probe; inventories never create a lease file."""
    if str(root) in _HELD.get():
        return not _HELD.get()[str(root)]
    path = root / '.workbench/runtime-manager/checks.lock'
    if not path.exists():
        return False
    files.ordinary(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        files.ordinary(path)
        if os.fstat(descriptor).st_ino != path.stat().st_ino:
            raise ValueError('check storage lease changed')
        try:
            with file_lease(descriptor, exclusive=True):
                return False
        except BlockingIOError:
            return True
    finally:
        os.close(descriptor)


def collection(function):
    @wraps(function)
    def run(root, plan, **kwargs):
        try:
            expected = {'execute_cleanup': 'cleanup', 'execute_restore_trash': 'restore-trash',
                        'execute_purge_trash': 'purge-trash'}[function.__name__]
            _manager()._validate_executable_plan(root, plan, expected)
            with lease(root, exclusive=True):
                free_before = shutil.disk_usage(root).free if plan.get('operation') == 'purge-trash' else None
                result = function(root, plan, **kwargs)
                if expected == 'restore-trash':
                    from . import check_retention
                    check_retention.restored(Path(root), result, now=kwargs.get('now'))
                if free_before is not None:
                    free_after = shutil.disk_usage(root).free
                    receipt = files.seal('storage-reclaim-observation', {
                        'format': 'workbench-storage-reclaim-observation-v1', 'receipt_id': result['receipt_id'],
                        'allocated_bytes_unlinked': result['result']['purged_bytes'],
                        'filesystem_free_before': free_before, 'filesystem_free_after': free_after,
                        'filesystem_free_delta': free_after - free_before,
                        'meaning': 'File allocation unlinked; free-space movement also includes filesystem and concurrent activity.'})
                    directory = Path(root) / '.workbench/runtime-manager/reclaim-observations'
                    directory.mkdir(parents=True, exist_ok=True)
                    files.ordinary(directory, directory=True)
                    files.write_json(directory / (plan['plan_id'].split(':')[-1] + '.json'), receipt, byte_limit=None)
                return result
        except ValueError as exc:
            raise _manager().RuntimeManagerError(str(exc)) from exc
    return run


def _validate(record, root):
    expected = {'id', 'format', 'root', 'attempt_id', 'snapshot_id', 'publication', 'files',
                'inputs', 'references', 'context', 'registered_at', 'reproduction', 'summary'}
    if (set(record) != expected or record['format'] != FORMAT or record['root'] != str(root)
            or files.seal('check-custody', {k: v for k, v in record.items() if k != 'id'}) != record):
        raise ValueError('unsupported or changed check custody manifest')
    _identity(record['attempt_id'])
    if not isinstance(record['context'], dict) or not isinstance(record['summary'], dict):
        raise ValueError('invalid check custody context or summary')
    if not isinstance(record['files'], dict) or not record['files']:
        raise ValueError('check custody has no evidence closure')
    for name, content in record['files'].items():
        files.safe_path(name)
        if (not isinstance(content, dict) or set(content) != {'sha256', 'bytes'}
                or not re.fullmatch('[0-9a-f]{64}', str(content['sha256']))
                or type(content['bytes']) is not int or content['bytes'] < 0):
            raise ValueError('invalid check custody file descriptor')
    if record['publication'] != record['files'].get('snapshot/publication.json'):
        raise ValueError('custody publication is outside evidence closure')
    if not isinstance(record['inputs'], dict) or not isinstance(record['references'], list):
        raise ValueError('invalid check custody inputs or dependencies')
    for name in record['inputs'].values():
        if name not in record['files']:
            raise ValueError('retained input is outside evidence closure')
    for name in record['references']:
        relative = files.safe_path(name)
        if len(relative.parts) < 3 or relative.parts[0] != '.workbench':
            raise ValueError('required dependency is outside this managed store')
    return record


def registrations(root):
    root = _root(root)
    directory = _ledger(root)
    errors, result = [], {}
    try:
        if directory.exists():
            files.ordinary(directory, directory=True)
        for path in sorted(directory.iterdir()) if directory.exists() else []:
            try:
                if path.suffix != '.json':
                    raise ValueError('unknown check registry entry')
                row = _validate(_sealed(path, 'check-custody'), root)
                if path.name != row['attempt_id'] + '.json':
                    raise ValueError('check registry identity differs from filename')
                result[row['attempt_id']] = row
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append({'path': str(path), 'reason': str(exc)})
    except (OSError, ValueError) as exc:
        errors.append({'path': str(directory), 'reason': str(exc)})
    # A lost derived row must not erase a live/trash consumer's dependencies.
    candidates = list((root / '.workbench/check-attempts').glob('*/' + MANIFEST))
    candidates += list((root / '.workbench/trash').glob('*/' + MANIFEST))
    for path in candidates:
        try:
            row = _validate(_sealed(path, 'check-custody'), root)
            if row['attempt_id'] not in result:
                result[row['attempt_id']] = row
                errors.append({'path': str(path), 'reason': 'registry-row-missing'})
            elif result[row['attempt_id']] != row:
                errors.append({'path': str(path), 'reason': 'custody-registry-conflict'})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({'path': str(path), 'reason': str(exc)})
    for row in result.values():
        _, state = locate(root, row)
        if state == 'unavailable':
            errors.append({'path': row['attempt_id'], 'reason': 'registered-evidence-unavailable'})
    return result, errors


def register(root, attempt, *, inputs, source_directories=(), context=None, references=(), reproduction=()):
    """Called by the owner only after verifying its saved source and result.

    inputs maps every retained-input role to its exact saved file. Source trees
    include the complete navigation closure, not just the program archive.
    """
    root, attempt = _root(root), Path(attempt)
    if attempt != root / '.workbench/check-attempts' / _identity(attempt.name):
        raise ValueError('check custody requires its exact Core attempt path')
    with lease(root):
        publication = _snapshots()._publication(attempt, lambda manifest: None, {}, lambda: False)
        manifest = publication['manifest']
        if manifest['attempt_id'] != attempt.name:
            raise ValueError('publication differs from retained attempt')
        if set(inputs) != {row['role'] for row in manifest['retained_inputs']}:
            raise ValueError('every retained input must have custody')
        names = {'snapshot/publication.json', 'snapshot/result.json.gz'}
        for row in manifest['retained_inputs']:
            name = str(files.safe_path(inputs[row['role']]))
            if _snapshots().file_content(attempt / name) != {k: row['content'][k] for k in ('sha256', 'bytes')}:
                raise ValueError('saved input differs from snapshot')
            names.add(name)
        for name in source_directories:
            source = files.ordinary(attempt / files.safe_path(name), directory=True)
            for row in files.tree_manifest(source):
                names.add((Path(name) / row['path']).as_posix())
        closure = {name: _snapshots().file_content(attempt / name) for name in sorted(names)}
        _snapshots()._verify_archive(attempt / 'snapshot/result.json.gz', manifest['source_result'], lambda: False)
        path = attempt / MANIFEST
        if path.exists():
            record = _validate(_sealed(path, 'check-custody'), root)
            if (record['files'] != closure or record['inputs'] != inputs
                    or record['references'] != sorted(set(references)) or record['context'] != (context or {})):
                raise ValueError('existing custody differs; original registration is immutable')
        else:
            record = files.seal('check-custody', {'format': FORMAT, 'root': str(root),
                'attempt_id': attempt.name, 'snapshot_id': manifest['id'],
                'publication': closure['snapshot/publication.json'], 'files': closure,
                'inputs': inputs, 'references': sorted(set(references)), 'context': context or {},
                'registered_at': _manager()._utc(), 'summary': publication['summary'],
                'reproduction': {'state': 'inspectable-evidence-only', 'missing_native_inputs': list(reproduction)}})
            _validate(record, root)
            # The explicit empty pin record distinguishes 'unpinned' from lost
            # pin authority. Rebuilding a registry must never silently clear pins.
            pin_path = _pin_path(root, attempt.name)
            pin_path.parent.mkdir(parents=True, exist_ok=True)
            files.ordinary(pin_path.parent, directory=True)
            if not pin_path.exists():
                files.write_json(pin_path, files.seal('check-pin', {'attempt_id': attempt.name, 'reasons': []}))
            pins(root, attempt.name)
            files.write_json(path, record, byte_limit=None)
        directory = _ledger(root)
        directory.mkdir(parents=True, exist_ok=True)
        files.ordinary(directory, directory=True)
        destination = directory / (attempt.name + '.json')
        if destination.exists() and _sealed(destination, 'check-custody') != record:
            raise ValueError('registry differs from retained custody manifest')
        if not destination.exists():
            files.write_json(destination, record, byte_limit=None)
        return record


def locate(root, record, trash=None):
    original = '.workbench/check-attempts/' + record['attempt_id']
    if (root / original).exists():
        return root / original, 'retained'
    trash = _manager()._trash_records(root / '.workbench') if trash is None else trash
    matches = [root / name for name, row in trash.items()
               if row['original_relative_path'] == original and (root / name).exists()]
    if len(matches) == 1:
        return matches[0], 'retired'
    from . import check_retention
    if any(row['attempt_id'] == record['attempt_id'] and row['snapshot_id'] == record['snapshot_id']
           for row in check_retention.expiry(root)):
        return None, 'expired'
    # A missing directory is never by itself evidence of expiry.
    operations = root / '.workbench/runtime-manager/operations'
    if operations.exists():
        for path in operations.glob('*.json'):
            receipt = _manager()._read_json(path, 'storage receipt')
            _manager().validate_operation_receipt(receipt)
            if receipt['operation'] == 'purge-trash' and receipt['status'] == 'complete':
                for name, row in trash.items():
                    if row['original_relative_path'] == original and any(action['source_relative_path'] == name for action in receipt['actions']):
                        return None, 'expired'
    return None, 'unavailable'


def _pin_path(root, identity):
    return root / '.workbench/runtime-manager/check-pins' / (identity + '.json')


def pins(root, identity):
    path = _pin_path(root, identity)
    if not path.exists():
        raise ValueError('check pin authority is missing; unpinned state cannot be inferred')
    record = _sealed(path, 'check-pin')
    if set(record) != {'id', 'attempt_id', 'reasons'} or record['attempt_id'] != identity or not isinstance(record['reasons'], list):
        raise ValueError('unsupported check pin record')
    if any(not isinstance(value, str) or not value.strip() for value in record['reasons']):
        raise ValueError('invalid check pin reason')
    return record['reasons']


def pin(root, identity, reason, *, remove=False):
    root = _root(root)
    _identity(identity)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('pin or unpin requires an explicit reason')
    with lease(root, exclusive=True):
        records, errors = registrations(root)
        if errors or identity not in records:
            raise ValueError('select supported registered check custody')
        path, state = locate(root, records[identity])
        if path is None:
            raise ValueError('missing or expired evidence cannot be pinned')
        reasons = set(pins(root, identity))
        reasons.discard(reason) if remove else reasons.add(reason)
        destination = _pin_path(root, identity)
        destination.parent.mkdir(parents=True, exist_ok=True)
        files.ordinary(destination.parent, directory=True)
        value = files.seal('check-pin', {'attempt_id': identity, 'reasons': sorted(reasons)})
        _write(destination, value)
        return value


def verify_payload(path, record, *, full=False):
    if _sealed(path / MANIFEST, 'check-custody') != record:
        raise ValueError('payload custody differs from registered custody')
    publication = _sealed(path / 'snapshot/publication.json', 'check-snapshot-publication')
    from .check_snapshot_contract import validate_envelope
    validate_envelope(publication['manifest'])
    if (publication['format'] != _snapshots().PUBLICATION
            or publication['manifest']['id'] != record['snapshot_id']
            or _snapshots().file_content(path / 'snapshot/publication.json') != record['publication']):
        raise ValueError('unsupported or changed snapshot publication')
    for name, content in record['files'].items():
        file = files.ordinary(path / files.safe_path(name))
        if file.stat().st_size != content['bytes'] or full and _snapshots().file_content(file) != content:
            raise ValueError('required evidence is missing or changed: ' + name)
    if full:
        _snapshots()._verify_archive(path / 'snapshot/result.json.gz', publication['manifest']['source_result'], lambda: False)


def inventory_policy(root, path, policy, records, errors, *, active, trash, unsafe=False):
    """Add check ownership to Core's existing inventory; no deletion engine here."""
    from . import check_retention
    check_retention.owned_unit(root, path, policy['deletion'], trash=trash)
    identity = path.name
    if trash is not None:
        identity = Path(trash['original_relative_path']).name
    record = records.get(identity)
    if record is None:
        record = next((row for row in records.values() if locate(root, row)[0] == path), None)
        if record is not None:
            identity = record['attempt_id']
            unsafe = True  # A changed/ambiguous trash transaction retains dependencies, never collection authority.
    if errors:
        policy['deletion'].update(state='protected', recoverability='none', reason_codes=['check-reference-accounting-incomplete'])
    if record is None:
        return None
    try:
        verify_payload(path, record)
        reasons = pins(root, identity)
        policy['custody'].update(state='managed', custodian='core', producer='workbench-check-custody-v1',
                                evidence_paths=[str((path / MANIFEST).relative_to(root))])
        if trash is None:
            policy.update(kind='snapshot', resource_id=record['snapshot_id'])
            policy['deletion'].update(state='eligible', recoverability='trash', reason_codes=['registered-check-history'])
        policy['last_use'] = {'state': 'observed', 'at': record['registered_at'], 'basis': 'manager-ledger',
                             'evidence_paths': [str((path / MANIFEST).relative_to(root))]}
        policy['reproducibility'] = {'state': 'retained-only', 'reason': 'Inspectable saved evidence; exact native reproduction is not established.', 'evidence_paths': []}
        if reasons or errors:
            policy['deletion'].update(state='protected', recoverability='none',
                reason_codes=['pinned-check-history' if reasons else 'check-reference-accounting-incomplete'])
        if unsafe:
            policy['deletion'].update(state='protected', recoverability='none', reason_codes=['unsafe-check-filesystem'])
        if active or policy['deletion']['state'] == 'active':
            policy['deletion'].update(state='active', recoverability='none', reason_codes=['check-store-lease-active'])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        policy['deletion'].update(state='protected', recoverability='none', reason_codes=['check-custody-unavailable'])
    return record


def export_bundle(root, identity, destination, *, _retention=False):
    """Export complete inspectable evidence to a new local directory and verify it."""
    root, destination = _root(root), Path(destination)
    internal = False
    if _retention:
        from . import check_retention
        internal = (destination == root / '.workbench/cache' / ('check-recovery-' + _identity(identity)) / 'evidence'
                    and _HELD.get().get(str(root)) is True
                    and check_retention.policy(root)['settings']['mode'] == 'finite')
        if not internal:
            raise ValueError('temporary export requires enabled retention and exclusive Core custody')
    if not destination.is_absolute() or (destination.is_relative_to(root) and not internal) or destination.exists() or destination.is_symlink():
        raise ValueError('bundle destination must be a new absolute directory outside this store')
    files.ordinary(destination.parent, directory=True)
    with lease(root):
        records, errors = registrations(root)
        if errors or identity not in records:
            raise ValueError('select supported check custody before export')
        record = records[identity]
        path, state = locate(root, record)
        if path is None:
            raise ValueError('missing evidence cannot be reconstructed by export')
        verify_payload(path, record, full=True)
        stage = destination.parent / ('.check-bundle-' + uuid4().hex)
        stage.mkdir(mode=0o700)
        # On interruption retain the explicit partial bundle; never certify it.
        files.write_json(stage / 'export-started.json', {'attempt_id': identity, 'destination': str(destination)})
        payload = stage / 'payload'
        payload.mkdir(mode=0o700)
        for name in record['files']:
            target = payload / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(files.ordinary(path / name), target)
            os.chmod(target, 0o600)
            with target.open('rb') as stream:
                os.fsync(stream.fileno())
        dependencies = {}
        pending = list(record['references'])
        while pending:
            reference = pending.pop()
            if reference in dependencies:
                continue
            source = root / reference
            for owner in records.values():
                original = root / '.workbench/check-attempts' / owner['attempt_id']
                if source == original or source.is_relative_to(original):
                    retained, _ = locate(root, owner)
                    if retained is None:
                        raise ValueError('required bundle dependency is unavailable')
                    source = retained / source.relative_to(original)
                    pending.extend(owner['references'])
                    break
            files.ordinary(source, directory=source.is_dir())
            descriptor = {'path': reference, 'directory': source.is_dir(), 'files': {}}
            dependency_root = stage / 'dependencies' / str(len(dependencies))
            dependency_root.mkdir(parents=True, mode=0o700)
            candidates = [source / row['path'] for row in files.tree_manifest(source)] if source.is_dir() else [source]
            for file in candidates:
                name = file.relative_to(source).as_posix() if source.is_dir() else 'payload'
                descriptor['files'][name] = _snapshots().file_content(file)
                target = dependency_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                with target.open('rb') as stream:
                    os.fsync(stream.fileno())
            descriptor['payload'] = dependency_root.relative_to(stage).as_posix()
            dependencies[reference] = descriptor
        bundle = files.seal('check-evidence-bundle', {'format': 'workbench-check-evidence-bundle-v1',
            'custody': record, 'closure': 'complete-inspectable-evidence',
            'native_reproduction': record['reproduction'], 'dependencies': dependencies})
        files.write_json(stage / 'bundle.json', bundle, byte_limit=None)
        verify_bundle(stage, expected=record)
        fsync_directory(stage)
        os.rename(stage, destination)
        fsync_directory(destination.parent)
        receipt = files.seal('check-evidence-export', {'format': 'workbench-check-evidence-export-v1',
            'attempt_id': identity, 'snapshot_id': record['snapshot_id'], 'bundle_id': bundle['id'],
            'destination': str(destination), 'verified_at': _manager()._utc(), 'state': 'verified-local-bundle'})
        directory = root / '.workbench/runtime-manager/check-exports'
        directory.mkdir(parents=True, exist_ok=True)
        files.ordinary(directory, directory=True)
        files.write_json(directory / (uuid4().hex + '.json'), receipt, byte_limit=None)
        return receipt


def verify_bundle(destination, *, expected=None):
    destination = files.ordinary(Path(destination), directory=True)
    bundle = _sealed(destination / 'bundle.json', 'check-evidence-bundle')
    if (set(bundle) != {'id', 'format', 'custody', 'closure', 'native_reproduction', 'dependencies'}
            or bundle['format'] != 'workbench-check-evidence-bundle-v1'
            or bundle['closure'] != 'complete-inspectable-evidence'):
        raise ValueError('unsupported local evidence bundle')
    record = bundle['custody']
    _validate(record, Path(record['root']))
    if expected is not None and record != expected:
        raise ValueError('bundle differs from selected check custody')
    if bundle['native_reproduction'] != record['reproduction']:
        raise ValueError('bundle reproduction claim differs from custody')
    if not isinstance(bundle['dependencies'], dict) or not set(record['references']).issubset(bundle['dependencies']):
        raise ValueError('bundle omits required dependencies')
    for reference, dependency in bundle['dependencies'].items():
        files.safe_path(reference)
        if dependency['path'] != reference or not re.fullmatch(r'dependencies/[0-9]+', dependency['payload']):
            raise ValueError('invalid bundle dependency identity')
        directory = files.ordinary(destination / dependency['payload'], directory=True)
        if {row['path'] for row in files.tree_manifest(directory)} != set(dependency['files']):
            raise ValueError('bundle dependency closure differs')
        for name, content in dependency['files'].items():
            if _snapshots().file_content(directory / files.safe_path(name)) != content:
                raise ValueError('bundle dependency bytes changed')
    payload = files.ordinary(destination / 'payload', directory=True)
    if {row['path'] for row in files.tree_manifest(payload)} != set(record['files']):
        raise ValueError('bundle file closure differs')
    for name, content in record['files'].items():
        if _snapshots().file_content(payload / name) != content:
            raise ValueError('bundle payload is missing or changed')
    publication = _sealed(payload / 'snapshot/publication.json', 'check-snapshot-publication')
    _snapshots()._verify_archive(payload / 'snapshot/result.json.gz', publication['manifest']['source_result'], lambda: False)
    return bundle


def require_export(root, record):
    directory = root / '.workbench/runtime-manager/check-exports'
    for path in sorted(directory.glob('*.json')):
        try:
            row = _sealed(path, 'check-evidence-export')
            if row['attempt_id'] == record['attempt_id'] and row['snapshot_id'] == record['snapshot_id']:
                bundle = verify_bundle(Path(row['destination']), expected=record)
                if bundle['id'] == row['bundle_id']:
                    return row
        except (ValueError, OSError, KeyError, TypeError):
            continue
    raise ValueError('permanent release requires a currently verified local evidence bundle')


def before_purge(root, item):
    records, errors = registrations(root)
    record, _ = _manager()._trash_record_context(root / '.workbench', item)
    identity = Path(record['original_relative_path']).name
    if identity in records:
        if errors:
            raise ValueError('check reference accounting is incomplete')
        verify_payload(Path(item['path']), records[identity], full=True)
        require_export(root, records[identity])


def history(root):
    root = _root(root)
    records, errors = registrations(root)
    result = []
    for identity, record in records.items():
        path, state = locate(root, record)
        reason = None
        try:
            if path is not None:
                verify_payload(path, record)
            pin_reasons = pins(root, identity)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            state, reason, pin_reasons = 'unavailable', str(exc), []
        result.append({'attempt_id': identity, 'snapshot_id': record['snapshot_id'], 'state': state,
            'path': None if path is None else str(path), 'context': record['context'],
            'original_summary': record['summary'], 'pins': pin_reasons, 'reason': reason, 'reproduction': record['reproduction']})
    from . import check_retention
    for row in check_retention.expiry(root):
        if row['attempt_id'] not in records:
            result.append({key: row[key] for key in ('attempt_id', 'snapshot_id', 'context', 'original_summary', 'state', 'reproduction')} |
                          {'path': None, 'pins': [], 'reason': row['details']})
    return {'format': 'workbench-check-storage-history-v1', 'checks': result, 'coverage_gaps': errors,
            'history_compaction': 'Enabled finite retention may retire old expiry summaries; absence is not proof that a check never ran.'}


def group_preview(root, selectors):
    """Inode union for this selected set, never a sum of per-item lower bounds."""
    manager = _manager()
    root = _root(root)
    report = manager.inventory_storage(root)
    items = {manager.resolve_inventory_item(report, selector)['item_id']: manager.resolve_inventory_item(report, selector)
             for selector in selectors}
    inodes, counts = {}, {}
    for item in items.values():
        scan = manager._scan_tree(Path(item['path']), root)
        for inode, values in scan['_inodes'].items():
            inodes[inode] = values
        for inode, count in scan['_inode_occurrences'].items():
            counts[inode] = counts.get(inode, 0) + count
    blockers = [item['item_id'] for item in items.values() if item['deletion']['state'] not in {'eligible', 'review'}]
    return {'format': 'workbench-storage-group-preview-v1', 'inventory_id': report['inventory_id'],
        'item_ids': sorted(items), 'logical_bytes': sum(row['size']['logical_bytes'] for row in items.values()),
        'unique_allocated_bytes': sum(row[0] for row in inodes.values()),
        'reclaimable_allocated_bytes': 0 if blockers else sum(row[0] for inode, row in inodes.items() if counts[inode] >= row[2]),
        'blocked_items': blockers, 'operation': 'preview-only',
        'limitations': ['Trash retains allocation until explicit purge.', 'Filesystem free-space movement is separate from removed file allocation.',
                       'Reflink and filesystem compression savings are not inferred from inode accounting.']}


def reconcile(root):
    """Rebuild missing registry rows from supported owned manifests, preserving pins."""
    root = _root(root)
    with lease(root, exclusive=True):
        records, errors = registrations(root)
        if any(row['reason'] != 'registry-row-missing' for row in errors):
            raise ValueError('unknown registry schema or missing evidence requires review; reconciliation refused')
        candidates = list((root / '.workbench/check-attempts').glob('*/' + MANIFEST))
        candidates += list((root / '.workbench/trash').glob('*/' + MANIFEST))
        recovered = []
        for path in candidates:
            row = _validate(_sealed(path, 'check-custody'), root)
            verify_payload(path.parent, row, full=True)
            pins(root, row['attempt_id'])
            target = _ledger(root) / (row['attempt_id'] + '.json')
            if target.exists():
                if records[row['attempt_id']] != row:
                    raise ValueError('conflicting custody manifests; no replacement authorized')
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            files.ordinary(target.parent, directory=True)
            files.write_json(target, row, byte_limit=None)
            recovered.append(row['attempt_id'])
        maintenance = []
        current, _ = registrations(root)
        for record in current.values():
            path, state = locate(root, record)
            if state == 'retained':
                maintenance.extend(_snapshots().recover(path))
                maintenance.extend(reconcile_indexes(root, path, record))
        return {'state': 'reconciled', 'registered': recovered, 'maintenance': maintenance,
                'missing_evidence_reconstructed': False}


def overview(root):
    root = _root(root)
    report = _manager().inventory_storage(root)
    retained = history(root)
    records, _ = registrations(root)
    for row in retained['checks']:
        path = Path(row['path']) if row['path'] else None
        categories = {}
        record = records.get(row['attempt_id'])
        if path is not None:
            for file in path.rglob('*'):
                if file.is_symlink() or not file.is_file():
                    continue
                name = file.relative_to(path).as_posix()
                if name.startswith('snapshot/indexes/') or name == 'snapshot/current.json':
                    category = 'query-indexes'
                elif name.startswith('snapshot-operations/'):
                    category = 'temporary-projections' if '/staging/' in name else 'maintenance-records'
                elif name in record['files'] and not name.startswith('snapshot/'):
                    category = 'saved-inputs'
                elif name.startswith('snapshot/') or name == 'result.json':
                    category = 'evidence'
                else:
                    category = 'maintenance-records'
                size = file.stat()
                value = categories.setdefault(category, {'logical_bytes': 0, 'allocated_bytes': 0})
                value['logical_bytes'] += size.st_size
                value['allocated_bytes'] += _manager()._allocated(size)
        row['categories'] = categories
    from . import check_retention
    mode = check_retention.policy(root)['settings']['mode']
    return {**retained, 'format': 'workbench-check-storage-overview-v1', 'inventory': report,
            'last_scan': report['scan']['completed_at'], 'automatic_retention': mode,
            'coverage': 'selected-Core-store-all-contexts',
            'limitations': ['External inputs are reproduction requirements, not deletion authority.',
                'Other custom state locations require explicit --checks-root selection.',
                'Category allocation is descriptive; use preview-group for shared allocation and reclamation.']}



def reconcile_indexes(root, attempt, record):
    """Retire verified unselected derived indexes to existing reclaimable cache.

    Archive, source, selected index and old operation receipts are preserved.
    A durable intent binds both locations so interrupted moves can be reconciled.
    """
    with _snapshots()._lease(attempt):
        verify_payload(attempt, record, full=True)
        publication = _sealed(attempt / 'snapshot/publication.json', 'check-snapshot-publication')
        pointer = _sealed(attempt / 'snapshot/current.json', 'check-snapshot-index-selection')
        if pointer.get('snapshot_id') != record['snapshot_id'] or not re.fullmatch('[0-9a-f]{32}', str(pointer.get('generation'))):
            raise ValueError('selected query index identity is unavailable')
        selected = attempt / 'snapshot/indexes' / pointer['generation']
        def verify(directory):
            files.ordinary(directory, directory=True)
            if {path.name for path in directory.iterdir()} != {'query.sqlite3', 'index.json'}:
                raise ValueError('unknown derived index contents; retirement refused')
            value = _sealed(directory / 'index.json', 'check-snapshot-index')
            if (value.get('format') not in {'workbench-check-snapshot-index-v1', _snapshots().INDEX}
                    or value.get('snapshot_id') != record['snapshot_id']
                    or value.get('layout') != _snapshots().index.LAYOUT
                    or _snapshots().file_content(directory / 'query.sqlite3') != value.get('file')):
                raise ValueError('unsupported or changed derived index; retirement refused')
            if value['format'] == _snapshots().INDEX and value.get('inputs') != _snapshots()._index_inputs(publication['manifest'], publication['views']):
                raise ValueError('derived index inputs differ')
            return value
        if verify(selected)['id'] != pointer['index_id']:
            raise ValueError('selected query index differs')
        candidates = []
        for directory in sorted((attempt / 'snapshot/indexes').iterdir()):
            if not re.fullmatch('[0-9a-f]{32}', directory.name):
                raise ValueError('unknown query generation; reconciliation refused')
            if directory != selected:
                value = verify(directory)
                if value['generation'] != directory.name:
                    raise ValueError('derived index generation differs')
                candidates.append((directory, value))
        journal = root / '.workbench/runtime-manager/check-index-maintenance'
        journal.mkdir(parents=True, exist_ok=True)
        files.ordinary(journal, directory=True)
        results = []
        # Complete an interrupted move only from its independently bound intent.
        for path in sorted(journal.glob('*.json')):
            if path.name.endswith('-completed.json'):
                continue
            intent = _sealed(path, 'check-derived-retirement')
            if intent.get('attempt_id') != attempt.name:
                continue
            expected_source = (attempt / 'snapshot/indexes' / intent['generation']).relative_to(root).as_posix()
            expected_destination = '.workbench/cache/check-index-' + attempt.name + '-' + intent['generation']
            if (intent.get('source') != expected_source or intent.get('destination') != expected_destination
                    or not re.fullmatch('[0-9a-f]{32}', intent['generation'])):
                raise ValueError('unknown derived retirement intent')
            complete = path.with_name(path.stem + '-completed.json')
            if not complete.exists() and not (root / intent['source']).exists():
                destination = root / intent['destination']
                if verify(destination)['id'] != intent['index_id']:
                    raise ValueError('interrupted derived retirement differs')
                files.write_json(complete, {'state': 'retired-derived-index', 'intent_id': intent['id']})
                results.append({'state': 'recovered-derived-retirement', 'path': str(destination)})
        for directory, value in candidates:
            destination = root / ('.workbench/cache/check-index-' + attempt.name + '-' + directory.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            files.ordinary(destination.parent, directory=True)
            intent = files.seal('check-derived-retirement', {'attempt_id': attempt.name, 'generation': directory.name,
                'source': directory.relative_to(root).as_posix(), 'destination': destination.relative_to(root).as_posix(),
                'index_id': value['id']})
            path = journal / (intent['id'].split(':')[-1] + '.json')
            if path.exists():
                if _sealed(path, 'check-derived-retirement') != intent:
                    raise ValueError('derived retirement intent differs')
            else:
                files.write_json(path, intent)
            if destination.exists() or destination.is_symlink():
                raise ValueError('derived retirement destination already exists')
            os.rename(directory, destination)
            fsync_directory(directory.parent)
            fsync_directory(destination.parent)
            files.write_json(path.with_name(path.stem + '-completed.json'), {'state': 'retired-derived-index', 'intent_id': intent['id']})
            results.append({'state': 'retired-derived-index', 'path': str(destination), 'rebuildable_from': record['snapshot_id']})
        return results



def restorable(item):
    return item['deletion']['state'] == 'review' or (
        item['kind'] == 'trash' and item['deletion']['state'] == 'protected'
        and not item['problems'] and set(item['deletion']['reason_codes']).issubset({
            'managed-trash', 'interrupted-cleanup', 'pinned-check-history', 'referenced-resource'}))
