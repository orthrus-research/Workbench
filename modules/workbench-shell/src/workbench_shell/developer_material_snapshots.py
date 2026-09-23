"""Small material-check views and explicit complete detail/export operations."""

from uuid import uuid4
import re

from workbench_core import check_snapshots as snapshots
from workbench_core import check_storage as storage
from workbench_core import check_snapshot_evolution as evolution

VIEW = 'workbench-material-check-view-v1'
PREFERENCE = 65536  # Page presentation preference; no observation is truncated.


def request(snapshot_id, view_id, operation, section=None, **fields):
    return {'format': 'workbench-check-snapshot-query-v1', 'snapshot_id': snapshot_id,
            'view_id': view_id, 'operation': operation, 'section_id': section,
            'record_key': None, 'blob_sha256': None, 'preferred_bytes': PREFERENCE,
            'cursor': None, **fields}


def diagnostic_address(finding, paired):
    side = finding.get('side')
    if side not in {'candidate', 'baseline'} or side == 'baseline' and not paired:
        raise ValueError('diagnostic belongs to an unselected side')
    admission = finding.get('channel') == 'source-admission'
    prefix = ('/result/' + side if paired else '') + (
        '/result/sourceAdmission/findings/' if admission else '/result/execution/diagnostics/')
    pointer = finding.get('pointer', '')
    if not pointer.startswith(prefix) or not re.fullmatch('[0-9]+', pointer[len(prefix):]):
        raise ValueError('diagnostic has an unsupported native pointer')
    return ((side + '-' if paired else '') + ('admission-findings' if admission else 'diagnostics'),
            'item:' + str(int(pointer[len(prefix):])))


def finding_label(finding, diagnostic):
    """Shared presentation of the exact original diagnostic before/after indexing."""
    label = finding.get('message', '')
    if label == 'Throwing':
        matches = [item['message'] for item in diagnostic.get('compilerFindings', [])
                   if isinstance(item.get('message'), str) and item.get('location') == finding.get('nativeLocation')]
        if not matches:
            matches = [item['message'] for item in finding.get('sourceRelationships', [])
                       if item.get('kind') == 'exception-frame' and item.get('message')]
        if matches:
            label = '\n'.join(dict.fromkeys(matches))
    return label if len(label) <= 2048 else label[:2048] + ' … [message preview; open native diagnostic for complete text]'


def labels(opened, page, paired):
    result, cache = {}, {}
    for row in (page.get('payload') or {}).get('records', []):
        finding = row.get('value')
        if not isinstance(finding, dict) or not finding.get('id'):
            continue
        address = diagnostic_address(finding, paired)
        if finding.get('message') == 'Throwing' and address not in cache:
            cache[address] = opened.read_record(*address)
        result[finding['id']] = finding_label(finding, cache.get(address, {}))
    return result


def ensure(root, identity, selection, cancelled):
    from . import developer_material_checks as owner
    attempt = owner._attempt(root, identity)
    if not (attempt / snapshots.DIRECTORY).exists() and (attempt / 'result.json').exists():
        owner.publish_snapshot(root, identity, selection, cancelled=cancelled)


def unpublished(record, saved):
    """Cancelled ingestion retains original evidence without resuming indexing."""
    from workbench_axiom.retained_snapshots import native_status_and_outcome, overview
    native_status, native_outcome = native_status_and_outcome(record)
    return {'format': VIEW, 'id': record['id'], 'attempt_id': record['attempt_id'],
            'request_id': saved['id'], 'candidate_id': record['candidate_id'],
            'selection_id': record['selection_id'], 'workspace_uri': record['workspace_uri'],
            'context_id': saved['inputs']['context']['id'], 'state': record['state'],
            'native_exit_code': record['native_exit_code'], 'failure_present': record['failure'] is not None,
            'native_status': native_status, 'native_outcome': native_outcome,
            'coverage': 'incomplete', 'authority': record['authority'],
            'snapshot_id': None, 'view_id': 'material-view-' + uuid4().hex, 'bindings': None,
            'sections': [], 'findings_count': len(record['findings']), 'finding_query': None,
            'finding_page': None, 'overview_state': 'loaded', 'native': overview(record)['native'],
            'detail_state': 'cancelled-before-snapshot-publication'}


def view(root, identity, selection, *, cancelled=lambda: False):
    from . import developer_material_checks as owner
    from workbench_axiom import retained_snapshots as domain
    attempt, saved, _ = owner._load(root, identity, selection)
    if not (attempt / 'result.json').exists() and not (attempt / snapshots.DIRECTORY).exists():
        return saved, owner._reference(attempt / 'request.json', saved), {}
    ensure(root, identity, selection, cancelled)
    with owner.open_snapshot(root, identity, selection, cancelled=cancelled) as opened:
        manifest = opened.manifest
        summary = opened.publication['summary']
        query = request(manifest['id'], 'material-view-' + uuid4().hex, 'records', 'findings')
        page = opened.query(query)
        overview = summary.get('overview', {})
        reader = domain.read_overview(overview, manifest['id'], derive_view=evolution.derived_view)
        support = evolution.interpretation(manifest, domain.SUPPORTED_SCHEMAS, scope_supported=opened.scope_supported)
        result = {'format': VIEW, 'id': manifest['result_id'], 'attempt_id': identity,
                  'request_id': saved['id'], 'candidate_id': saved['candidate']['id'],
                  'selection_id': selection.id, 'workspace_uri': selection.pack_uri,
                  'context_id': summary['context_id'], 'state': summary['state'],
                  'native_exit_code': summary['native_exit_code'], 'native_status': summary['native_status'],
                  'failure_present': summary['failure_present'], 'native_outcome': manifest['native_outcome'],
                  'coverage': manifest['coverage'], 'authority': owner.AUTHORITY,
                  'snapshot_id': manifest['id'], 'view_id': query['view_id'], 'bindings': manifest['bindings'],
                  'sections': manifest['sections'], 'findings_count': summary['findings_count'],
                  'finding_query': query, 'finding_page': page,
                  'overview_state': reader['state'], 'interpretation': support,
                  'reader_binding': reader['binding'], 'overview_reason': reader['reason'],
                  'native': reader['view']['native'] if reader['view'] is not None else None,
                  'detail_state': 'not-loaded'}
        # Owner linkage points at the immutable manifest, never at a fabricated
        # small version of the complete original result.
        reference = owner._reference(attempt / snapshots.DIRECTORY / 'publication.json', opened.publication)
        return result, reference, labels(opened, page, saved['baseline'] is not None)


def query(root, identity, selection, value, *, cancelled=lambda: False):
    from . import developer_material_checks as owner
    _, saved, _ = owner._load(root, identity, selection)
    with owner.open_snapshot(root, identity, selection, cancelled=cancelled) as opened:
        result = opened.query(value)
        messages = labels(opened, result, saved['baseline'] is not None) if (
            value['operation'] == 'records' and value['section_id'] == 'findings') else {}
        return result, messages


def export(root, identity, selection, snapshot_id, destination, *, section=None,
           key=None, digest=None, cancelled=lambda: False):
    from . import developer_material_checks as owner
    with owner.open_snapshot(root, identity, selection, cancelled=cancelled) as opened:
        if opened.manifest['id'] != snapshot_id:
            raise ValueError('export differs from selected snapshot')
        content = opened.export(destination) if section is None else opened.export_record(section, key, digest, destination)
        return {'format': 'workbench-material-export-v1', 'attempt_id': identity,
                'destination': str(destination), 'content': content, 'state': 'verified', 'read_only': True}


def compare(root, before, after, selection, *, crafting_key=None, cancelled=lambda: False):
    from . import developer_material_checks as owner
    from workbench_axiom import retained_snapshots as domain
    from workbench_core import check_snapshot_evolution as evolution
    from workbench_core.check_snapshot_index import record_key
    with owner.open_snapshot(root, before, selection, cancelled=cancelled) as left, \
            owner.open_snapshot(root, after, selection, cancelled=cancelled) as right:
        sections = evolution.compare(left.manifest, right.manifest, domain.SUPPORTED_SCHEMAS,
                                     before_scope=left.scope_supported, after_scope=right.scope_supported)
        graph = None
        if crafting_key is not None:
            compatible = {row['section'] for row in sections['comparable']}
            if not {'crafting-recipes', 'crafting-values'} <= compatible:
                graph = {'state': 'incompatible', 'reason': 'required crafting contracts are not comparable'}
            else:
                try:
                    graph = domain.compare_graph(left.read_record('crafting-recipes', record_key(crafting_key)),
                        right.read_record('crafting-recipes', record_key(crafting_key)),
                        lambda key: left.read_record('crafting-values', record_key(key)),
                        lambda key: right.read_record('crafting-values', record_key(key)))
                except (ValueError, KeyError) as exc:
                    graph = {'state': 'incompatible', 'reason': str(exc)}
        return {'format': 'workbench-material-retained-comparison-v1', 'sections': sections,
                'crafting_key': crafting_key, 'stored_graph': graph,
                'graph_contract': 'axiom-stored-crafting-graph-comparison-v1',
                'graph_comparator': snapshots.file_content(domain.__file__, cancelled),
                'native_support_established': False, 'read_only': True}
