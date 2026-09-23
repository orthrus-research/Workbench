"""Saved Axiom diagnostic access before complete snapshot finalization."""

from hashlib import sha256
from pathlib import Path
import re

from workbench_core import check_diagnostics as store
from workbench_core import check_storage as storage
from . import developer_material_snapshots as snapshots

VIEW = 'workbench-material-diagnostic-view-v1'
STATUS = 'workbench-material-delivery-status-v1'
GROUPS = tuple(f'{severity}-{location}' for severity in ('error', 'warning', 'information')
               for location in ('located', 'unlocated'))


def finding_group(finding):
    severity = finding.get('severity', '').lower()
    return f"{severity if severity in ('error', 'warning') else 'information'}-{'located' if finding.get('location') else 'unlocated'}"


def _binding(request):
    return {key: request[key] for key in ('id', 'attempt_id', 'selection_id', 'workspace_uri')}


def request(root, identity, selection):
    """Read owner metadata only; progress must not recapture the whole program."""
    from . import developer_material_checks as owner
    attempt = owner._attempt(root, identity)
    saved = owner._sealed(storage.read_json(attempt / 'request.json', byte_limit=None), 'material-check-request')
    if (saved['format'] != owner.REQUEST or saved['attempt_id'] != identity
            or saved['selection_id'] != selection.id or saved['workspace_uri'] != selection.pack_uri
            or saved['authority'] != owner.AUTHORITY):
        raise ValueError('diagnostic request belongs to another selection')
    return attempt, saved


def publish(attempt, saved, record):
    from workbench_axiom import retained_snapshots as domain
    from . import developer_material_checks as owner
    components = {Path(module.__file__).name: sha256(Path(module.__file__).read_bytes()).hexdigest()
                  for module in (domain, owner, snapshots, store)}
    components[Path(__file__).name] = sha256(Path(__file__).read_bytes()).hexdigest()
    producer = sha256(storage.canonical(components)).hexdigest()
    summary, streams = _contents(saved, record, producer)
    return store.publish(attempt, binding=_binding(saved), summary=summary, streams=streams)


def _contents(saved, record, producer, *, grouped=True):
    from workbench_axiom import retained_snapshots as domain
    from . import developer_material_checks as owner
    fields, declarations, summary = domain.describe({**record, 'id': None}, saved,
                                                   retained_inputs=[], producer_sha256=producer)
    streams = {}
    for declaration in declarations:
        name = declaration['id']
        if name != 'findings' and not name.endswith(('diagnostics', 'admission-findings')):
            continue
        if declaration['state'] != 'observed':
            continue
        value = record
        for key in declaration['path']:
            value = value[key]
        streams[name] = value
    labelled = []
    for finding in streams['findings']:
        section, key = snapshots.diagnostic_address(finding, saved['baseline'] is not None)
        diagnostic = streams[section][int(key.split(':')[1])]
        labelled.append({'finding': finding, 'label': snapshots.finding_label(finding, diagnostic)})
    streams['findings'] = labelled
    summary.update({key: fields[key] for key in ('native_outcome', 'coverage', 'bindings', 'producer')})
    summary.update({'request_id': saved['id'], 'selection_id': saved['selection_id'],
                    'workspace_uri': saved['workspace_uri'], 'authority': owner.AUTHORITY,
                    'capture': (record['native'] or {}).get('invocation', {}).get('capture'),
                    'failure': record['failure']})
    # Presentation order is separate from stable original diagnostic identities.
    # Native startup logs often precede every source-located Groovy finding.
    summary['finding_order'] = sorted(range(len(labelled)), key=lambda index: (
        labelled[index]['finding']['location'] is None,
        labelled[index]['finding']['side'] != 'candidate',
        {'error': 0, 'warning': 1}.get(labelled[index]['finding'].get('severity', '').lower(), 2), index))
    if grouped:
        groups = {name: [] for name in GROUPS}
        for index in summary['finding_order']:
            groups[finding_group(labelled[index]['finding'])].append(index)
        summary['finding_groups'] = groups
    return summary, streams


def verify(attempt, saved, result):
    revision = result.get('diagnostic_delivery')
    if revision is None:
        if (attempt / store.DIRECTORY / 'publication.json').exists():
            raise ValueError('material result lost its diagnostic delivery binding')
        return  # Historical result before diagnostic delivery existed.
    record = store.publication(attempt, binding=_binding(saved), revision=revision)
    summary, streams = _contents(saved, result, record['summary']['producer']['build_sha256'],
                                grouped='finding_groups' in record['summary'])
    if summary != record['summary'] or set(streams) != set(record['streams']):
        raise ValueError('diagnostic delivery differs from retained outcome or evidence streams')
    for name, values in streams.items():
        section = record['streams'][name]
        if section['count'] != len(values):
            raise ValueError('diagnostic delivery lost original evidence')
        for descriptor in section['pages']:
            page = store.page(attempt, record, name, descriptor['offset'])
            if page['records'] != values[page['offset']:page['offset'] + len(page['records'])]:
                raise ValueError('diagnostic delivery differs from original native evidence')


def status(root, identity, selection):
    attempt, saved = request(root, identity, selection)
    path = attempt / store.DIRECTORY / 'publication.json'
    revision = store.publication(attempt, binding=_binding(saved))['id'] if path.exists() else None
    complete = (attempt / 'snapshot/publication.json').exists()
    active = storage.execution_active(attempt)
    return {'format': STATUS, 'attempt_id': identity, 'request_id': saved['id'],
            'diagnostic_id': revision, 'detail_state': 'ready' if complete else (
                'preparing' if active else 'interrupted' if (attempt / 'started.json').exists() else 'not-started')}


def view(root, identity, selection, *, revision=None, offset=0, group=None):
    attempt, saved = request(root, identity, selection)
    record = store.publication(attempt, binding=_binding(saved), revision=revision)
    summary = dict(record['summary'])
    order = summary.pop('finding_order')
    if (sorted(order) != list(range(record['streams']['findings']['count']))
            or summary['findings_count'] != len(order)):
        raise ValueError('diagnostic presentation lost or duplicated findings')
    groups = summary.pop('finding_groups', None)
    if groups is not None:
        if (set(groups) != set(GROUPS)
                or sorted(index for values in groups.values() for index in values) != sorted(order)):
            raise ValueError('diagnostic groups lost or duplicated findings')
        summary['finding_counts'] = {name: len(values) for name, values in groups.items()}
    if group is not None:
        if groups is None or group not in GROUPS:
            raise ValueError('selected diagnostic group is unavailable for this revision')
        order = groups[group]
    if type(offset) is not int or not 0 <= offset <= len(order):
        raise ValueError('select an exact diagnostic presentation offset')
    cache, rows = {}, []
    for index in order[offset:offset + store.PAGE_RECORDS]:
        page_offset = index // store.PAGE_RECORDS * store.PAGE_RECORDS
        if page_offset not in cache:
            cache[page_offset] = store.page(attempt, record, 'findings', page_offset)['records']
        rows.append(cache[page_offset][index - page_offset])
    if group is not None and any(finding_group(row['finding']) != group for row in rows):
        raise ValueError('diagnostic group differs from original finding')
    next_offset = offset + len(rows)
    return {**summary, 'format': VIEW, 'id': record['id'], 'diagnostic_id': record['id'],
            'snapshot_id': None, 'view_id': record['id'], 'native': summary['overview']['native'],
            'findings': [row['finding'] for row in rows], 'offset': offset,
            'group': group, 'group_count': len(order),
            'next_offset': None if next_offset == len(order) else next_offset,
            'detail_state': status(root, identity, selection)['detail_state']}, {
                row['finding']['id']: row['label'] for row in rows}


def diagnostic(root, identity, selection, revision, diagnostic_id):
    attempt, saved = request(root, identity, selection)
    record = store.publication(attempt, binding=_binding(saved), revision=revision)
    if not re.fullmatch('diagnostic-(0|[1-9][0-9]*)', diagnostic_id):
        raise ValueError('select an exact diagnostic')
    finding = store.item(attempt, record, 'findings', int(diagnostic_id.split('-')[1]))['finding']
    if finding['id'] != diagnostic_id:
        raise ValueError('diagnostic identity changed')
    section, key = snapshots.diagnostic_address(finding, saved['baseline'] is not None)
    return {'format': 'workbench-material-diagnostic-evidence-v1', 'attempt_id': identity,
            'diagnostic_id': record['id'], 'finding': finding,
            'native': store.item(attempt, record, section, int(key.split(':')[1]))}


def source(root, identity, selection, revision, diagnostic_id):
    from . import developer_material_checks as owner
    # Verify retained source custody for navigation, independently of indexing.
    attempt, saved, _ = owner._load(root, identity, selection)
    finding = diagnostic(root, identity, selection, revision, diagnostic_id)['finding']
    if finding['location'] is None:
        raise ValueError('diagnostic has no retained source location')
    directory = 'baseline-source' if finding['side'] == 'baseline' else 'source'
    raw = storage.ordinary(attempt / directory / storage.safe_path(finding['location']['path'])).read_bytes()
    return {'format': 'workbench-material-source-view-v1', 'attempt_id': identity,
            'result_id': revision, 'source': finding, 'text': raw.decode('utf-8'), 'read_only': True}
