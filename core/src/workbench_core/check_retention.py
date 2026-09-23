"""Opt-in finite check history; Core's exact-item manager performs every purge.

Policy preferences never limit a worker or truncate evidence. Durable per-unit
intents make interrupted maintenance resumable without copying prior history.
"""
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import shutil
from uuid import uuid4

from . import check_lifecycle as life
from . import check_storage as files
from .host_filesystem import fsync_directory

FORMAT = 'workbench-check-retention-policy-v1'
RESPONSE = 'workbench-check-retention-response-v1'
UNIT = '.workbench-check-retention-unit.json'
# History preferences, measured against complete native attempts (including raw
# compatibility JSON, indexes and saved inputs), not process resource ceilings.
RECOMMENDATION = dict(mode='finite', max_count=10, max_bytes=8 * 1024**3,
                      min_age_days=1, trash_days=7, metadata_count=128,
                      active_contexts=[], known_stores=[])
RESERVE = 16 * 1024**2  # Small transaction recovery headroom, not a worker budget.
_DISPOSING = ContextVar('check_retention_disposing', default=frozenset())


def _directory(root):
    return root / '.workbench/runtime-manager/check-retention'


def _root(root):
    root = Path(os.path.abspath(root))
    life._manager()._assert_no_symlink_ancestors(root, Path(root.anchor))
    if root.exists():
        files.ordinary(root, directory=True)
    return root


def _now(now=None):
    return datetime.now(timezone.utc) if now is None else datetime.fromisoformat(now.replace('Z', '+00:00'))


def _stamp(now=None):
    return _now(now).isoformat().replace('+00:00', 'Z')


def _age(at, now):
    return max(0, (_now(now) - _now(at)).total_seconds() / 86400)


def _read(path, kind):
    return life._sealed(path, kind)


def _write(path, kind, body):
    life._manager()._assert_no_symlink_ancestors(path, Path(path.anchor))
    path.parent.mkdir(parents=True, exist_ok=True)
    files.ordinary(path.parent, directory=True)
    value = files.seal(kind, body)
    life._write(path, value)
    return value


def context_key(context):
    return 'check-context:sha256:' + sha256(files.canonical(context)).hexdigest()


def _settings(value):
    if not isinstance(value, dict) or set(value) != set(RECOMMENDATION):
        raise ValueError('unsupported retention settings; review the current policy version')
    if value['mode'] not in {'disabled', 'finite', 'keep-everything'}:
        raise ValueError('unsupported retention mode')
    for name in ('max_count', 'max_bytes', 'metadata_count'):
        if type(value[name]) is not int or value[name] < 1:
            raise ValueError(name + ' must be a positive history preference')
    for name in ('min_age_days', 'trash_days'):
        if type(value[name]) is not int or value[name] < 0:
            raise ValueError(name + ' must be nonnegative whole days')
    for name in ('active_contexts', 'known_stores'):
        if not isinstance(value[name], list) or any(not isinstance(x, str) for x in value[name]) or len(set(value[name])) != len(value[name]):
            raise ValueError('invalid ' + name)
    for key in value['active_contexts']:
        if not life.re.fullmatch(r'check-context:sha256:[0-9a-f]{64}', key):
            raise ValueError('select an exact context from retention status')
    for name in value['known_stores']:
        path = Path(name)
        if not path.is_absolute() or str(path) != name or '..' in path.parts:
            raise ValueError('known stores must be explicit absolute paths')
    return value


def policy(root):
    root = _root(root)
    path = _directory(root) / 'policy.json'
    if not path.exists():
        return files.seal('check-retention-policy', {'format': FORMAT, 'root': str(root),
            'settings': {**deepcopy(RECOMMENDATION), 'mode': 'disabled'}, 'consent': None})
    row = _read(path, 'check-retention-policy')
    if set(row) != {'id', 'format', 'root', 'settings', 'consent'} or row['format'] != FORMAT or row['root'] != str(root):
        raise ValueError('unsupported retention policy; automatic maintenance refused')
    _settings(row['settings'])
    if not isinstance(row['consent'], str):
        raise ValueError('retention policy lacks explicit operation authorization')
    return row


def propose(root, settings):
    root = _root(root)
    current = policy(root)
    settings = _settings(settings)
    disclosure = (
        f"Scope: registered checks and owned retired indexes in {root}; other known stores are accounting only. "
        f"Mode: {settings['mode']}. Prefer at most {settings['max_count']} live checks and {settings['max_bytes']} allocated bytes "
        f"in this store, including trash, saved inputs, raw results, indexes and maintenance. Checks become eligible after "
        f"{settings['min_age_days']} days. Quarantine remains restorable for {settings['trash_days']} days. "
        "Finite mode authorizes routine exact-item quarantine and permanent expiry without another per-item prompt. "
        "At expiry Core verifies a temporary local evidence bundle, releases the original, then deletes that temporary bundle. "
        "User-selected external exports are never deleted. Pins, required proof and recovery dependencies, active work and "
        "the latest useful check for explicitly active contexts remain protected and may exceed preferences. "
        f"Terminal receipts may be compacted after recovery duties end; only the latest {settings['metadata_count']} expiry "
        "summaries remain individually discoverable, with aggregate counts thereafter. Restoring trash restarts eligibility. "
        "Unknown, partial or inaccessible data is protected and reported. Keep-everything disables automatic expiry and permits growth. "
        "This is not an execution or output limit. Low headroom can defer maintenance; new results are never truncated."
    )
    return files.seal('check-retention-proposal', {'format': 'workbench-check-retention-proposal-v1',
        'root': str(root), 'previous_policy_id': current['id'], 'settings': settings, 'disclosure': disclosure})


def configure(root, settings, confirmation):
    root = _root(root)
    if confirmation != propose(root, settings)['id']:
        raise ValueError('confirm the exact current retention proposal after reviewing its disclosure')
    root.mkdir(parents=True, exist_ok=True)
    with life.lease(root, exclusive=True):
        proposal = propose(root, settings)
        if confirmation != proposal['id']:
            raise ValueError('confirm the exact current retention proposal after reviewing its disclosure')
        return _write(_directory(root) / 'policy.json', 'check-retention-policy', {
            'format': FORMAT, 'root': str(root), 'settings': settings, 'consent': proposal['id']})


def expiry(root):
    rows = []
    for path in sorted((_directory(root) / 'expired').glob('*.json')):
        row = _read(path, 'check-retention-expiry')
        if row.get('format') != 'workbench-check-retention-expiry-v1' or row.get('root') != str(root) or path.stem != row.get('attempt_id'):
            raise ValueError('unsupported expiry summary; maintenance refused')
        life._identity(row['attempt_id'])
        rows.append(row)
    return rows


def _jobs(root):
    rows = []
    for path in sorted((_directory(root) / 'jobs').glob('*.json')):
        row = _read(path, 'check-retention-job')
        if row.get('format') != 'workbench-check-retention-job-v1' or row.get('root') != str(root) or path.stem != row.get('attempt_id'):
            raise ValueError('unsupported retention job; maintenance refused')
        life._identity(row['attempt_id'])
        if row.get('bundle') != '.workbench/cache/check-recovery-' + row['attempt_id']:
            raise ValueError('retention recovery path differs')
        rows.append(row)
    return rows


def _inventory(root):
    return life._manager().inventory_storage(root)


def status(root, *, now=None):
    root = _root(root)
    current = policy(root)
    settings = current['settings']
    if not root.exists():
        return {'format': RESPONSE, 'operation': 'status', 'policy': current, 'recommendation': deepcopy(RECOMMENDATION),
            'stores': [], 'volumes': {}, 'coverage_gaps': [], 'checks': [], 'contexts': [], 'live_count': 0,
            'allocated_bytes': 0, 'last_maintenance': None, 'notices': ['No Core check history has been created. Automatic retention is disabled.'],
            'compacted_history': {'expired_count': 0, 'forgotten_count': 0}, 'maintenance_reserve_bytes': RESERVE,
            'before_work_notice': None, 'scope': 'per-store-policy; known stores and volumes are accounting only'}
    stores, volumes, gaps = [], {}, []
    # Reject overlapping stores from totals rather than count the same tree twice.
    accepted = []
    for name in [str(root), *settings['known_stores']]:
        path = Path(name)
        if path in accepted:
            continue
        try:
            files.ordinary(path, directory=True)
            if any(path.is_relative_to(p) or p.is_relative_to(path) for p in accepted):
                raise ValueError('overlapping known store; accounting is not additive')
            scan = life._manager()._scan_tree(path / '.workbench', path) if (path / '.workbench').exists() else {'size': {'logical_bytes': 0, 'unique_allocated_bytes': 0}, 'problems': []}
            disk = shutil.disk_usage(path)
            volume = str(path.stat().st_dev)
            row = {'root': name, 'volume': volume, 'size': scan['size'], 'free_bytes': disk.free,
                   'coverage_gaps': scan['problems']}
            stores.append(row); accepted.append(path)
            total = volumes.setdefault(volume, {'allocated_bytes': 0, 'free_bytes': disk.free, 'stores': []})
            total['allocated_bytes'] += scan['size']['unique_allocated_bytes']
            total['stores'].append(name)
            gaps.extend({'root': name, 'reason': problem} for problem in scan['problems'])
        except (OSError, ValueError) as exc:
            gaps.append({'root': name, 'reason': str(exc)})
    records, errors = life.registrations(root)
    gaps.extend(errors)
    inventory = _inventory(root)
    bypath = {row['path']: row for row in inventory['items']}
    checks, latest, contexts = [], {}, {}
    for row in records.values():
        key = context_key(row['context'])
        contexts[key] = row['context']
        path, state = life.locate(root, row)
        if state in {'retained', 'retired'} and (key not in latest or row['registered_at'] > latest[key]['registered_at']):
            latest[key] = row
        item = bypath.get(str(path))
        checks.append({'attempt_id': row['attempt_id'], 'context_key': key, 'state': state,
            'registered_at': row['registered_at'], 'item_id': item['item_id'] if item else None,
            'allocated_bytes': item['size']['unique_allocated_bytes'] if item else 0,
            'protection': item['deletion']['reason_codes'] if item and item['deletion']['state'] not in {'eligible', 'review'} else []})
    jobs = {row['attempt_id']: row for row in _jobs(root)}
    for row in checks:
        key = row['context_key']
        if key in settings['active_contexts'] and key in latest and latest[key]['attempt_id'] == row['attempt_id']:
            row['protection'].append('latest-useful-active-context')
        row['age_days'] = _age(max(row['registered_at'], jobs.get(row['attempt_id'], {}).get('restored_at', row['registered_at'])), now)
    own = next((row for row in stores if row['root'] == str(root)), None)
    allocated = own['size']['unique_allocated_bytes'] if own else None
    live = [row for row in checks if row['state'] == 'retained']
    notices = []
    before_work_notice = None
    if settings['mode'] != 'finite':
        notices.append('Automatic expiry is disabled; retained checks, trash and metadata can continue growing.')
    if allocated is not None and (allocated > settings['max_bytes'] or len(live) > settings['max_count']):
        notices.append('History exceeds preferences. Protected items, grace periods and unavailable data can prevent reclamation.')
        before_work_notice = notices[-1]
    headroom = RESERVE + max((row['allocated_bytes'] for row in checks), default=0)
    if own and own['free_bytes'] < headroom:
        notices.append('Low filesystem headroom: reclaim eligible storage, export evidence or change storage preferences before more work. Maintenance needs recovery space.')
        before_work_notice = notices[-1]
    return {'format': RESPONSE, 'operation': 'status', 'policy': current, 'recommendation': deepcopy(RECOMMENDATION),
        'stores': stores, 'volumes': volumes, 'coverage_gaps': gaps, 'checks': checks,
        'protected_items': [{'path': row['path'], 'kind': row['kind'], 'allocated_bytes': row['size']['unique_allocated_bytes'],
                             'reasons': row['deletion']['reason_codes']} for row in inventory['items'] if row['deletion']['state'] not in {'eligible', 'review'}],
        'contexts': [{'key': key, 'context': value, 'active': key in settings['active_contexts']} for key, value in contexts.items()],
        'live_count': len(live), 'allocated_bytes': allocated, 'notices': notices,
        'before_work_notice': before_work_notice, 'observed_result_headroom_bytes': headroom,
        'last_maintenance': _read(_directory(root) / 'last.json', 'check-retention-report') if (_directory(root) / 'last.json').exists() else None,
        'compacted_history': _read(_directory(root) / 'counters.json', 'check-retention-counters') if (_directory(root) / 'counters.json').exists() else {'expired_count': 0, 'forgotten_count': 0},
        'maintenance_reserve_bytes': RESERVE, 'scope': 'per-store-policy; known stores and volumes are accounting only',
        'allocation_meaning': 'Unique inode allocation per store; cross-store hardlinks/reflinks are not inferred. Filesystem free space is separate.'}


def preview(root, *, now=None):
    view = status(root, now=now)
    settings = view['policy']['settings']
    count, allocated = view['live_count'], view['allocated_bytes'] or 0
    selected = []
    if settings['mode'] == 'finite' and not view['coverage_gaps']:
        for row in sorted(view['checks'], key=lambda row: (row['registered_at'], row['attempt_id'])):
            if row['state'] != 'retained' or row['protection'] or row['age_days'] < settings['min_age_days']:
                continue
            if count <= settings['max_count'] and allocated <= settings['max_bytes']:
                break
            selected.append(row['attempt_id'])
            count -= 1; allocated -= row['allocated_bytes']
    return {**view, 'operation': 'preview', 'quarantine_candidates': selected,
            'projected_live_count': count, 'projected_bytes_after_eventual_expiry': max(0, allocated),
            'immediate_reclaimed_bytes': 0,
            'notice': 'Preview only. Maintenance rechecks protections under the store lease; quarantine does not free bytes.'}


def owned_unit(root, path, deletion, *, trash=None):
    """Protect policy recovery/metadata units from generic cache deletion."""
    original = Path(trash['original_relative_path']).name if trash else path.name
    if not original.startswith(('check-recovery-', 'check-maintenance-')):
        return
    try:
        row = _read(path / UNIT, 'check-retention-unit')
        if row.get('format') != 'workbench-check-retention-unit-v1' or row.get('root') != str(root) or row.get('name') != original:
            raise ValueError('unknown retention unit')
        if (str(root), original) not in _DISPOSING.get():
            raise ValueError('retention unit has recovery duties; use enabled maintenance')
    except (ValueError, OSError, KeyError):
        deletion.update(state='protected', recoverability='none', reason_codes=['retention-recovery-obligation'])


def _item(root, path):
    return next((row for row in _inventory(root)['items'] if row['path'] == str(path)), None)


def _execute(root, current, action, item, *, now=None):
    if policy(root)['id'] != current['id'] or current['settings']['mode'] != 'finite':
        raise ValueError('retention authorization changed; review current policy')
    manager = life._manager()
    plan = (manager.plan_cleanup(root, selector=item['item_id'], now=now) if action == 'cleanup'
            else manager.plan_purge_trash(root, selector=item['item_id'], confirmation=item['resource_id'], now=now))
    if plan['status'] != 'ready':
        raise ValueError('exact retention operation is blocked: ' + str(plan['blockers']))
    # One small durable authorization for the current exact operation. Manager
    # receipts remain immutable until their recovery/proof obligations end.
    _write(_directory(root) / 'authorization.json', 'check-retention-authorization', {
        'format': 'workbench-check-retention-authorization-v1', 'root': str(root),
        'policy_id': current['id'], 'consent': current['consent'], 'plan_id': plan['plan_id'],
        'operation': action, 'item_id': item['item_id'], 'at': _stamp(now)})
    return (manager.execute_cleanup if action == 'cleanup' else manager.execute_purge_trash)(root, plan, now=now)


def _dispose(root, current, path, *, now=None):
    name = path.name
    token = _DISPOSING.set(_DISPOSING.get() | {(str(root), name)})
    try:
        item = _item(root, path)
        if item:
            receipt = _execute(root, current, 'cleanup', item, now=now)
            path = root / receipt['actions'][0]['destination_relative_path']
        else:
            matches = [(root / relative, row) for relative, row in life._manager()._trash_records(root / '.workbench').items()
                       if row['original_relative_path'] == '.workbench/cache/' + name and (root / relative).exists()]
            if not matches:
                return 0
            if len(matches) != 1:
                raise ValueError('ambiguous retention maintenance trash')
            path = matches[0][0]
        return _execute(root, current, 'purge', _item(root, path), now=now)['result']['purged_bytes']
    finally:
        _DISPOSING.reset(token)


def _unit(root, path, purpose):
    life._manager()._assert_no_symlink_ancestors(path, root)
    path.mkdir(parents=True, exist_ok=True)
    files.ordinary(path, directory=True)
    return _write(path / UNIT, 'check-retention-unit', {'format': 'workbench-check-retention-unit-v1',
        'root': str(root), 'name': path.name, 'purpose': purpose})


def _durable(path):
    """Flush completed check copies before binding their allocation observation.

    Read-only hashing does not flush delayed allocation. Capture the manager's
    exact metadata binding only after all owned files and directories are durable.
    Later changes still invalidate the existing trash receipt.
    """
    directories = [path]
    for entry in path.rglob('*'):
        if entry.is_dir():
            files.ordinary(entry, directory=True)
            directories.append(entry)
        else:
            with files.ordinary(entry).open('rb') as stream:
                os.fsync(stream.fileno())
    for directory in reversed(directories):
        fsync_directory(directory)


def _job(root, record, current, *, now=None, origin='retention'):
    path = _directory(root) / 'jobs' / (record['attempt_id'] + '.json')
    if path.exists():
        return next(row for row in _jobs(root) if row['attempt_id'] == record['attempt_id'])
    return _write(path, 'check-retention-job', {'format': 'workbench-check-retention-job-v1',
        'root': str(root), 'attempt_id': record['attempt_id'], 'snapshot_id': record['snapshot_id'],
        'policy_id': current['id'], 'started_at': _stamp(now), 'origin': origin,
        'bundle': '.workbench/cache/check-recovery-' + record['attempt_id']})


def _expire(root, record, current, *, now=None):
    path, state = life.locate(root, record)
    job = _job(root, record, current, now=now, origin='manual-expiry-compaction' if state == 'expired' else 'retention')
    bundle_root = root / job['bundle']
    destination = bundle_root / 'evidence'
    if any(row['attempt_id'] == record['attempt_id'] for row in expiry(root)):
        return _dispose(root, current, bundle_root, now=now)
    if state == 'retired':
        # Do not duplicate arbitrary dependency trees to make room. Required
        # references are protected by inventory and complete export verifies them.
        needed = sum(row['bytes'] for row in record['files'].values())
        for reference in record['references']:
            target = root / reference
            if target.exists():
                needed += life._manager()._scan_tree(target, root)['size']['logical_bytes']
        if not destination.exists():
            if shutil.disk_usage(root).free < needed + RESERVE:
                raise OSError('insufficient recovery space for a complete verified export; original trash retained')
            _unit(root, bundle_root, 'temporary-verified-evidence-before-permanent-expiry')
            # Retry partial exports only after their intent is verified; originals
            # remain in trash. The normal export stages beneath this owned unit.
            partial = list(bundle_root.glob('.check-bundle-*'))
            if partial:
                for stage in partial:
                    intent = files.read_json(stage / 'export-started.json')
                    if intent != {'attempt_id': record['attempt_id'], 'destination': str(destination)}:
                        raise ValueError('unknown partial export; original trash retained')
                    # An owned partial export is disposable only while the entire
                    # original closure is still independently verified in trash.
                    life.verify_payload(path, record, full=True)
                # Dispose this exact complete unit through the existing engine,
                # then retry the export. Unknown/unsafe entries still refuse.
                _dispose(root, current, bundle_root, now=now)
                _unit(root, bundle_root, 'temporary-verified-evidence-before-permanent-expiry')
            life.export_bundle(root, record['attempt_id'], destination, _retention=True)
        life.verify_bundle(destination, expected=record)
        receipt = _execute(root, current, 'purge', _item(root, path), now=now)
        allocated = receipt['result']['purged_bytes']
        proof = receipt['receipt_id']
    elif state == 'expired':
        # R7-05's locator requires a validated complete manager purge receipt.
        # The owned job proves that this policy initiated the export/release.
        if job.get('origin') != 'manual-expiry-compaction':
            life.verify_bundle(destination, expected=record)
        allocated, proof = 0, 'recovered-complete-manager-purge'
    else:
        return 0
    _write(_directory(root) / 'expired' / (record['attempt_id'] + '.json'), 'check-retention-expiry', {
        'format': 'workbench-check-retention-expiry-v1', 'root': str(root), 'attempt_id': record['attempt_id'],
        'snapshot_id': record['snapshot_id'], 'context': record['context'], 'original_summary': record['summary'],
        'state': 'expired', 'expired_at': _stamp(now), 'policy_id': current['id'], 'purge_proof': proof,
        'reproduction': record['reproduction'], 'expiry_time_basis': 'summary recorded at policy maintenance',
        'details': 'Permanent release recorded by Core; an enabled retention policy compacts terminal history.'})
    return allocated + _dispose(root, current, bundle_root, now=now)


def _compact(root, current, *, now=None):
    """Move only released, validated metadata into one manager disposal unit.

    The previous disposal's small terminal receipts are retired on the next pass;
    collection never recursively copies prior history or rewrites a large database.
    """
    manager = life._manager()
    expired = {row['attempt_id']: row for row in expiry(root)}
    jobs = {row['attempt_id']: row for row in _jobs(root)}
    movable = []
    for identity, row in expired.items():
        if identity not in jobs:
            continue
        bundle = root / jobs[identity]['bundle']
        if bundle.exists() or any(Path(value['original_relative_path']).name == bundle.name and (root / name).exists()
                                   for name, value in manager._trash_records(root / '.workbench').items()):
            continue
        # These rows no longer carry evidence or recovery duties. Their compact
        # expiry authority was committed before any full custody row is moved.
        movable += [life._ledger(root) / (identity + '.json'), life._pin_path(root, identity),
                    _directory(root) / 'jobs' / (identity + '.json')]
    retained, errors = life.registrations(root)
    if errors:
        raise ValueError('incomplete custody accounting blocks metadata retirement')
    references = _proof_references(root)
    for identity, record in retained.items():
        attempt, state = life.locate(root, record)
        if state != 'retained' or life.pins(root, identity):
            continue
        pointer = _read(attempt / 'snapshot/current.json', 'check-snapshot-index-selection')
        for journal in (attempt / 'snapshot-operations').iterdir():
            started = _read(journal / 'started.json', 'check-snapshot-operation')
            names = {p.name for p in journal.iterdir()}
            if (started.get('operation') != 'rebuild' or started.get('attempt') != identity
                    or started.get('operation_id') != journal.name or pointer['generation'] == journal.name
                    or (attempt / 'snapshot/indexes' / journal.name).exists()
                    or not names.issubset({'started.json', 'completed.json', 'failed.json', 'recovered.json'})):
                continue
            complete = journal / 'completed.json'
            recovered = journal / 'recovered.json'
            if (complete.exists() and files.read_json(complete) == {'state': 'completed', 'snapshot_id': record['snapshot_id']}
                    or recovered.exists() and files.read_json(recovered).get('state') == 'discarded-unpublished-staging'):
                movable.append(journal)
    ledger = root / '.workbench/runtime-manager'
    purged, restored_trash = set(), set()
    receipts = []
    for path in sorted((ledger / 'operations').glob('*.json')):
        row = manager._read_json(path, 'terminal retention operation')
        manager.validate_operation_receipt(row)
        if row['status'] != 'complete':
            continue
        actions = row['actions']
        if row['operation'] == 'purge-trash':
            purged.update(action['source_relative_path'] for action in actions)
        if row['operation'] == 'restore-trash':
            restored_trash.update(action['source_relative_path'] for action in actions)
        receipts.append((path, row))
    released_trash = set()
    for name, row in manager._trash_records(root / '.workbench').items():
        original = root / row['original_relative_path']
        own = original.name.startswith(('check-recovery-', 'check-maintenance-', 'check-index-')) or original.name in expired
        if own and not (root / name).exists() and (name in purged and not original.exists() or name in restored_trash):
            released_trash.add(name)
            movable.append(Path(row['_record_path']))
    for path, row in receipts:
        endpoints = [root / action[key] for action in row['actions'] for key in ('source_relative_path', 'destination_relative_path') if action.get(key)]
        # Restrict to this vertical's completed resources. Restored/live resources,
        # proof references and all unrelated operations retain their original rows.
        if any(str(p.relative_to(root)) in released_trash for p in endpoints) and all(not p.exists() or p.name in retained and p.parent == root / '.workbench/check-attempts' for p in endpoints):
            movable.append(path)
            movable.append(ledger / 'reclaim-observations' / path.name)
    for path in (ledger / 'check-exports').glob('*.json'):
        row = _read(path, 'check-evidence-export')
        if row['attempt_id'] in expired and (not Path(row['destination']).is_relative_to(root / '.workbench/cache') or not Path(row['destination']).exists()):
            movable.append(path)
    for path in (ledger / 'check-index-maintenance').glob('*.json'):
        if path.name.endswith('-completed.json'):
            continue
        row = _read(path, 'check-derived-retirement')
        done = path.with_name(path.stem + '-completed.json')
        if done.exists() and files.read_json(done) == {'state': 'retired-derived-index', 'intent_id': row['id']} and not (root / row['source']).exists() and not (root / row['destination']).exists():
            movable += [path, done]
    old = sorted(expired.values(), key=lambda row: (row['expired_at'], row['attempt_id']))[:-current['settings']['metadata_count']]
    movable += [_directory(root) / 'expired' / (row['attempt_id'] + '.json') for row in old if row['attempt_id'] not in jobs]
    movable = sorted({p for p in movable if p.exists() and not any(p == ref or p.is_relative_to(ref) or ref.is_relative_to(p) for ref in references)})
    if not movable:
        return 0
    capsule = root / '.workbench/cache' / ('check-maintenance-' + uuid4().hex)
    _unit(root, capsule, 'released-terminal-metadata')
    intent = {'files': {p.relative_to(root).as_posix(): _content(p) for p in movable},
              'newly_expired': sum(p.parent == _directory(root) / 'jobs' for p in movable),
              'forgotten': sum(p.parent == _directory(root) / 'expired' for p in movable)}
    _write(capsule / 'intent.json', 'check-retention-compaction', intent)
    return _finish_compaction(root, current, capsule, now=now)


def _finish_compaction(root, current, capsule, *, now=None):
    intent = _read(capsule / 'intent.json', 'check-retention-compaction')
    if set(intent) != {'id', 'files', 'newly_expired', 'forgotten'}:
        raise ValueError('unknown metadata compaction intent')
    references = _proof_references(root)
    for name, content in intent['files'].items():
        relative = files.safe_path(name)
        if not (str(relative).startswith('.workbench/runtime-manager/') or life.re.fullmatch(
                r'.workbench/check-attempts/[a-z][a-z0-9-]*-[0-9a-f]{32}/snapshot-operations/[0-9a-f]{32}', str(relative))):
            raise ValueError('compaction is outside Core metadata')
        source, destination = root / relative, capsule / 'payload' / relative
        if any(source == ref or source.is_relative_to(ref) or ref.is_relative_to(source) for ref in references):
            raise ValueError('required proof now references pending metadata retirement; inspect retained capsule')
        if destination.exists():
            if source.exists() or _content(destination) != content:
                raise ValueError('interrupted compaction bytes differ')
        else:
            if _content(source) != content:
                raise ValueError('metadata changed during compaction')
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, destination)
            fsync_directory(source.parent); fsync_directory(destination.parent)
    counter = _directory(root) / 'counters.json'
    counts = _read(counter, 'check-retention-counters') if counter.exists() else {'expired_count': 0, 'forgotten_count': 0, 'last_compaction': None}
    if counts['last_compaction'] != intent['id']:
        _write(counter, 'check-retention-counters', {'expired_count': counts['expired_count'] + intent['newly_expired'],
            'forgotten_count': counts['forgotten_count'] + intent['forgotten'], 'last_compaction': intent['id']})
    return _dispose(root, current, capsule, now=now)


def _content(path):
    return {'directory': files.tree_manifest(path)} if path.is_dir() else life._snapshots().file_content(path)


def _proof_references(root):
    return [root / row['target_path'] for item in _inventory(root)['items'] for row in item['references']
            if row['required'] and row['target_path']]


def _resume_compaction(root, current, *, now=None):
    reclaimed = 0
    for path in sorted((root / '.workbench/cache').glob('check-maintenance-*')):
        unit = _read(path / UNIT, 'check-retention-unit')
        if unit['root'] != str(root) or unit['purpose'] != 'released-terminal-metadata' or unit['name'] != path.name:
            raise ValueError('unsupported interrupted maintenance')
        reclaimed += _finish_compaction(root, current, path, now=now)
    for name, row in life._manager()._trash_records(root / '.workbench').items():
        original = Path(row['original_relative_path'])
        if original.parent == Path('.workbench/cache') and original.name.startswith('check-maintenance-') and (root / name).exists():
            # Quarantine only occurs after all moves and aggregate accounting.
            reclaimed += _dispose(root, current, root / original, now=now)
    return reclaimed


def _derived(root, path, original):
    index = _read(path / 'index.json', 'check-snapshot-index')
    if index.get('format') not in {'workbench-check-snapshot-index-v1', life._snapshots().INDEX} or life._snapshots().file_content(path / 'query.sqlite3') != index.get('file'):
        raise ValueError('retired derived index changed')
    for candidate in (root / '.workbench/runtime-manager/check-index-maintenance').glob('*.json'):
        if candidate.name.endswith('-completed.json'):
            continue
        row = _read(candidate, 'check-derived-retirement')
        done = candidate.with_name(candidate.stem + '-completed.json')
        if (row['destination'] == original and row['index_id'] == index['id'] and done.exists()
                and files.read_json(done) == {'state': 'retired-derived-index', 'intent_id': row['id']}
                and not (root / row['source']).exists()):
            return True
    return False


def maintain(root, *, now=None, protect_attempts=()):
    root = _root(root)
    current = policy(root)
    if current['settings']['mode'] != 'finite':
        return {'format': RESPONSE, 'operation': 'maintain', 'state': 'disabled', 'allocated_bytes_unlinked': 0,
                'notice': 'History is unchanged; explicitly enable a disclosed finite policy to reclaim automatically.'}
    with life.lease(root, exclusive=True):
        if policy(root)['id'] != current['id']:
            return {'format': RESPONSE, 'operation': 'maintain', 'state': 'deferred', 'allocated_bytes_unlinked': 0,
                    'notice': 'Policy changed before collection; inspect the current settings and retry.'}
        before = shutil.disk_usage(root).free
        if before < RESERVE:
            return {'format': RESPONSE, 'operation': 'maintain', 'state': 'deferred-capacity', 'allocated_bytes_unlinked': 0,
                    'notice': 'Insufficient small-transaction recovery space. Reclaim or export before more work; no evidence was truncated.'}
        reclaimed, retired, failures = 0, [], []
        try:
            reclaimed += _resume_compaction(root, current, now=now)
            view = status(root, now=now)
            if view['coverage_gaps']:
                raise ValueError('retention coverage is incomplete; inspect status before collection')
            life.reconcile(root)
            # Only derived indexes admitted by Core reconciliation; unrelated
            # generic caches never inherit this policy's deletion permission.
            for item in _inventory(root)['items']:
                name = Path(item['path']).name
                if item['relative_path'].startswith('.workbench/cache/check-index-') and item['deletion']['state'] == 'eligible':
                    if not _derived(root, Path(item['path']), item['relative_path']):
                        continue
                    receipt = _execute(root, current, 'cleanup', item, now=now)
                    trash = _item(root, root / receipt['actions'][0]['destination_relative_path'])
                    reclaimed += _execute(root, current, 'purge', trash, now=now)['result']['purged_bytes']
            for name, row in life._manager()._trash_records(root / '.workbench').items():
                path = root / name
                if row['original_relative_path'].startswith('.workbench/cache/check-index-') and path.exists() and _derived(root, path, row['original_relative_path']):
                    item = _item(root, path)
                    if item['deletion']['state'] == 'review':
                        reclaimed += _execute(root, current, 'purge', item, now=now)['result']['purged_bytes']
            view = preview(root, now=now)
            records, _ = life.registrations(root)
            for identity in view['quarantine_candidates']:
                if identity in protect_attempts:
                    continue
                record = records[identity]
                _durable(life.locate(root, record)[0])
                _job(root, record, current, now=now)
                _execute(root, current, 'cleanup', _item(root, life.locate(root, record)[0]), now=now)
                retired.append(identity)
            view = status(root, now=now)
            checks = {row['attempt_id']: row for row in view['checks']}
            trash = life._manager()._trash_records(root / '.workbench')
            for identity, record in records.items():
                path, state = life.locate(root, record)
                check = checks[identity]
                if check['protection'] or identity in protect_attempts:
                    continue
                if state == 'retired':
                    row = trash.get(path.relative_to(root).as_posix())
                    if row and _age(row['created_at'], now) >= current['settings']['trash_days']:
                        reclaimed += _expire(root, record, current, now=now)
                elif state == 'expired':
                    reclaimed += _expire(root, record, current, now=now)
            reclaimed += _compact(root, current, now=now)
        except (OSError, ValueError, KeyError, TypeError, life._manager().RuntimeManagerError) as exc:
            failures.append(str(exc))
        result = {'format': RESPONSE, 'operation': 'maintain', 'state': 'deferred' if failures else 'complete',
            'policy_id': current['id'], 'at': _stamp(now), 'quarantined': retired,
            'allocated_bytes_unlinked': reclaimed, 'filesystem_free_before': before,
            'filesystem_free_after': shutil.disk_usage(root).free, 'failures': failures,
            'notice': 'Quarantine is recoverable; expired details are permanently unavailable. Original producer outcomes are unchanged.'}
        try:
            _write(_directory(root) / 'last.json', 'check-retention-report', result)
        except OSError:
            result['state'] = 'deferred'; result['failures'].append('Unable to retain maintenance summary; inspect exact manager journals before retry.')
        return result


def dispatch(root, operation, *, settings=None, confirmation=None):
    if operation == 'status':
        return status(root)
    if operation == 'preview':
        return preview(root)
    if operation == 'maintain':
        return maintain(root)
    if operation == 'configure':
        value = configure(root, settings, confirmation) if confirmation else propose(root, settings)
        return {'format': RESPONSE, 'operation': operation, 'state': 'configured' if confirmation else 'proposal',
                'policy' if confirmation else 'proposal': value}
    raise ValueError('unsupported retention operation')


def restored(root, receipt, *, now=None):
    """A manual restore is fresh user interest, not immediately eligible again."""
    for action in receipt['actions']:
        name = Path(action['destination_relative_path']).name
        path = _directory(root) / 'jobs' / (name + '.json')
        if path.exists():
            job = next(row for row in _jobs(root) if row['attempt_id'] == name)
            _write(path, 'check-retention-job', {k: v for k, v in job.items() if k != 'id'} | {'restored_at': _stamp(now)})
