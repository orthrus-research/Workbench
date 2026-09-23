"""Compatibility of retained observations, independent of native qualification.

Owners select supported section contracts. Core compares verified descriptors and
records disposable reader bindings; it never invents a migration or pack scope.
"""

from hashlib import sha256
from copy import deepcopy
from pathlib import Path

from . import check_snapshot_contract as contract
from . import check_storage as storage
from . import check_snapshot_index as index

COMPARISON = 'workbench-check-section-comparison-v1'


def interpretation(manifest, supported_schemas, *, scope_supported=True):
    result = contract.reader_coverage(manifest, supported_schemas)
    result['scope_supported'] = scope_supported
    result['required_interpretation_complete'] &= scope_supported
    result['state'] = ('complete' if result['required_interpretation_complete'] else
                       'unsupported' if not scope_supported or result['affected_sections'] else 'incomplete')
    return result


def derived_view(source, *, snapshot_id, input_contract, output_contract, converter,
                 converter_sha256, convert):
    """Bind a disposable owner conversion; no rewrite or missing-field defaults.

    convert is trusted owner code selected by an explicit reader-support table.
    Failure leaves the original evidence intact and produces no successful view.
    """
    before = b''.join(index.chunks(source))
    isolated = deepcopy(source)
    output = convert(isolated)
    if b''.join(index.chunks(isolated)) != before:
        raise ValueError('view converter mutated retained input')
    binding = storage.seal('check-snapshot-derived-view', {
        'format': 'workbench-check-derived-view-v1', 'snapshot_id': snapshot_id,
        'input_contract': input_contract, 'input_sha256': sha256(before).hexdigest(),
        'output_contract': output_contract, 'output_sha256': sha256(b''.join(index.chunks(output))).hexdigest(),
        'converter': converter, 'converter_sha256': converter_sha256})
    return output, binding


def compare(before, after, supported_schemas, *, before_scope=True, after_scope=True):
    """Compare stored representations with transitive section dependencies.

    Callers must verify both snapshots/custody first. Equality covers exact
    section content and dependency closure, not arbitrary semantic equivalence.
    Different snapshot-local IDs may change representation without behavior.
    """
    left = {s['id']: s for s in before['sections']}
    right = {s['id']: s for s in after['sections']}
    scope_changes, comparable, incompatible = [], [], []

    def closure(key, sections):
        found, pending = set(), [key]
        while pending:
            name = pending.pop()
            if name not in found:
                found.add(name)
                pending.extend(sections[name]['dependencies'])
        return found

    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        if a is None or b is None:
            scope_changes.append({'section': key, 'change': 'added-section' if a is None else 'removed-section'})
            item = a if b is None else b
            if item['state'] != 'not-applicable' and (item['schema'] not in supported_schemas or item['state'] != 'observed'):
                incompatible.append({'section': key, 'reasons': ['added/removed section has an unsupported or unavailable observation']})
            continue
        if a['required'] != b['required']:
            scope_changes.append({'section': key, 'change': 'requirement-changed',
                                  'before_required': a['required'], 'after_required': b['required']})
        if a['state'] == 'not-applicable' or b['state'] == 'not-applicable':
            if a['state'] != b['state']:
                scope_changes.append({'section': key, 'change': 'applicability-changed',
                                      'before': a['state'], 'after': b['state']})
            continue
        reasons = []
        if not before_scope or not after_scope:
            reasons.append('unsupported historical scope')
        ac, bc = closure(key, left), closure(key, right)
        if ac != bc:
            reasons.append('different dependency closure; no approved mapping')
        for name in sorted(ac | bc):
            x, y = left.get(name), right.get(name)
            if x is None or y is None:
                continue
            if x['schema'] != y['schema'] or x['schema'] not in supported_schemas:
                reasons.append('unsupported or different observation contract: ' + name)
            if x['state'] != 'observed' or y['state'] != 'observed':
                reasons.append('observation unavailable or incomplete: ' + name)
            if x['dependencies'] != y['dependencies']:
                reasons.append('different dependency relationships: ' + name)
        if reasons:
            incompatible.append({'section': key, 'reasons': sorted(set(reasons))})
        else:
            changed = [name for name in sorted(ac) if
                       left[name]['content'] != right[name]['content'] or left[name]['count'] != right[name]['count']]
            comparable.append({'section': key, 'state': 'changed' if changed else 'unchanged',
                               'changed_content_sections': changed})
    # Scope changes can only be asserted under independently supported scopes.
    if not before_scope or not after_scope:
        incompatible.extend({'section': row['section'], 'reasons': ['unsupported historical scope']} for row in scope_changes)
        scope_changes = []
    changed_bindings = [key for key in before['bindings'] if before['bindings'][key] != after['bindings'][key]]
    return storage.seal('check-snapshot-comparison', {
        'format': COMPARISON, 'before_snapshot': before['id'], 'after_snapshot': after['id'],
        'comparator_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'reader_contracts': sorted(supported_schemas),
        'producer_changed': before['producer'] != after['producer'], 'changed_bindings': changed_bindings,
        'meaning': 'stored-section-representation-and-dependency-closure-only',
        'native_support_established': False,
        'state': 'incompatible' if incompatible else 'scope-changed' if scope_changes else
                 'changed' if any(row['state'] == 'changed' for row in comparable) else 'unchanged',
        'comparable': comparable, 'scope_changes': scope_changes, 'incompatible': incompatible})
