"""Axiom's retained section meaning; Core owns persistence and access.

The selected request fixes obligations. Missing native observations never turn
into empty registries. These functions do not initialize or mutate native state.
"""

from hashlib import sha256
import json
from pathlib import Path

# Persisted contracts are finite reader obligations, independent of whichever
# producer is current. Keep V1's declaration function stable when adding V2.
SCOPE_V1 = 'axiom-retained-material-check-v1'
OVERVIEW_V1 = 'axiom-check-overview-v1'
OVERVIEW_V2 = 'axiom-check-overview-v2'
OVERVIEW_MEANING = 'partial-original-native-outcomes; current-reader-support-is-separate'
SUPPORTED_SCHEMAS = frozenset('axiom-retained-' + prefix + name + '-v1'
    for prefix, names in [('', ('findings', 'report'))] + [
        (prefix, ('admission-findings', 'diagnostics', 'gt-recipes', 'crafting-recipes', 'crafting-values', 'furnace'))
        for prefix in ('', 'baseline-', 'candidate-')]
    for name in names)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _declarations_v1(request):
    recipes = request['inputs']['context'].get('initializationStage') == 'recipes'
    result = [dict(id='findings', path=['findings'], records='sequence', required=True, dependencies=[]),
              dict(id='report', path=[], records='single', required=True, dependencies=[])]
    sides = [('baseline-', ['native', 'result', 'baseline']), ('candidate-', ['native', 'result', 'candidate'])] if request['baseline'] is not None else [('', ['native'])]
    for prefix, base in sides:
        execution = base + ['result', 'execution']
        native = execution + ['nativeInitialization']
        for name, path, mode, required, dependencies in [
            ('admission-findings', base + ['result', 'sourceAdmission', 'findings'], 'sequence', False, []),
            ('diagnostics', execution + ['diagnostics'], 'sequence', True, []),
            ('gt-recipes', native + ['nativeStoredRecipes', 'maps'], 'mapping', recipes, []),
            ('crafting-recipes', native + ['nativeStoredCraftingRecipes', 'entries'], 'mapping', recipes, [prefix + 'crafting-values']),
            ('crafting-values', native + ['nativeStoredCraftingRecipes', 'nativeValues'], 'mapping', recipes, []),
            ('furnace', native + ['nativeStoredFurnaceRecipes'], 'single', recipes, []),
        ]:
            result.append(dict(id=prefix + name, path=path, records=mode, required=required, dependencies=dependencies))
    return sorted(result, key=lambda view: view['id'])


def declarations(request):
    return _declarations_v1(request)


def _scope_v1(request):
    return {'name': SCOPE_V1,
            'sections': {view['id']: view['required'] for view in _declarations_v1(request)}}


def scope(request):
    return _scope_v1(request)


def resolve_scope(request, manifest, *, scope_identity):
    """Resolve approved historical obligations from the verified saved request."""
    selected = _scope_v1(request)
    return selected if (manifest['producer']['id'] == 'axiom'
                        and manifest['scope_id'] == scope_identity(selected)) else None


def read_overview(source, snapshot_id, *, derive_view):
    """Two named formats for the CLI and IDE summary; no legacy native runner."""
    if not isinstance(source, dict):
        return {'state': 'incomplete', 'view': None, 'binding': None, 'reason': 'overview object was not captured'}
    def convert(value):
        if value.get('format') == OVERVIEW_V1:
            # Required historical fields must exist. Never fabricate native=None
            # or a failure default for a producer that did not capture them.
            return {'format': OVERVIEW_V2, 'native': value['native'],
                    'failure_type': value['failure_type'], 'detail_state': value['detail_state'],
                    'meaning': OVERVIEW_MEANING}
        if value.get('format') == OVERVIEW_V2:
            for key in ('native', 'failure_type', 'detail_state'):
                value[key]
            if value['meaning'] != OVERVIEW_MEANING:
                raise ValueError('overview meaning differs')
            return dict(value)
        raise ValueError('unsupported overview contract')
    try:
        view, binding = derive_view(source, snapshot_id=snapshot_id,
            input_contract=source.get('format'), output_contract=OVERVIEW_V2,
            converter='axiom-check-overview-reader-v1',
            converter_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), convert=convert)
        return {'state': 'loaded', 'view': view, 'binding': binding, 'reason': None}
    except (KeyError, TypeError, ValueError) as exc:
        return {'state': 'unsupported-schema' if source.get('format') not in {OVERVIEW_V1, OVERVIEW_V2}
                else 'incomplete', 'view': None, 'binding': None, 'reason': str(exc)}


def compare_graph(before, after, before_value, after_value):
    """Compare supported stored JSON graphs, preserving aliasing and local IDs.

    The caller selects compatible observation contracts and supplies snapshot-bound
    value resolvers. This does not establish native support or source causation.
    """
    pending, forward, reverse = [(before, after)], {}, {}
    try:
        while pending:
            a, b = pending.pop()
            if type(a) is not type(b):
                return {'state': 'changed', 'reason': 'stored value types differ'}
            if isinstance(a, dict):
                if list(a) != list(b):
                    return {'state': 'incompatible', 'reason': 'stored fields or field order differ'}
                if set(a) == {'nativeValueRef'}:
                    x, y = a['nativeValueRef'], b['nativeValueRef']
                    if not isinstance(x, str) or not isinstance(y, str):
                        raise ValueError('invalid native reference')
                    if x in forward or y in reverse:
                        if forward.get(x) != y or reverse.get(y) != x:
                            return {'state': 'changed', 'reason': 'stored alias relationships differ'}
                    else:
                        forward[x], reverse[y] = y, x
                        pending.append((before_value(x), after_value(y)))
                else:
                    pending.extend((a[key], b[key]) for key in a)
            elif isinstance(a, list):
                if len(a) != len(b):
                    return {'state': 'changed', 'reason': 'stored sequence multiplicity differs'}
                pending.extend(zip(a, b))
            elif json.dumps(a, allow_nan=False) != json.dumps(b, allow_nan=False):
                return {'state': 'changed', 'reason': 'stored values differ'}
        return {'state': 'unchanged', 'reason': 'compatible stored graph including referenced values'}
    except (KeyError, ValueError) as exc:
        return {'state': 'incompatible', 'reason': 'required stored value is unavailable: ' + str(exc)}


def verify_references(record):
    """Check each nativeValueRef against its enclosing retained native catalog."""
    stack = [(record, None)]
    while stack:
        value, catalog = stack.pop()
        if isinstance(value, dict):
            if 'nativeValues' in value:
                catalog = value['nativeValues']
                if not isinstance(catalog, dict):
                    raise ValueError('native value catalog is unavailable')
            if set(value) == {'nativeValueRef'}:
                reference = value['nativeValueRef']
                if not isinstance(reference, str) or catalog is None or reference not in catalog:
                    raise ValueError('native snapshot reference is unresolved in its catalog')
            else:
                stack.extend((item, catalog) for item in value.values() if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            stack.extend((item, catalog) for item in value if isinstance(item, (dict, list)))


def native_status_and_outcome(record):
    """Preserve native outcome independently of derived snapshot publication."""
    native = record['native'] or {}
    initialization = native.get('result', {}).get('initialization', {})
    status = initialization.get('status', native.get('status'))
    if record['state'] != 'completed' or not native:
        outcome = 'incomplete'
    elif status in {'native-failed', 'source-error'}:
        outcome = 'native-failed'
    elif status in {'completed', 'accepted'}:
        outcome = 'completed'
    else:
        # Expectations or missing context do not establish a native failure.
        outcome = 'incomplete'
    return status, outcome


def describe(record, request, *, retained_inputs, producer_sha256):
    views = []
    for declaration in declarations(request):
        value = record
        try:
            for part in declaration['path']:
                value = value[part]
            exists = True
        except (KeyError, TypeError):
            exists = False
        if exists:
            state, reason, path = 'observed', None, declaration['path']
        elif declaration['required']:
            state, reason, path = 'unavailable', 'The native result did not capture this required section.', None
        elif declaration['id'].endswith('admission-findings'):
            state, reason, path = 'unavailable', 'No separate source-admission findings were captured.', None
        else:
            state, reason, path = 'not-applicable', 'This section is outside the selected initialization stage.', None
        views.append({**declaration, 'schema': 'axiom-retained-' + declaration['id'] + '-v1',
                      'path': path, 'state': state, 'reason': reason,
                      'dependencies': declaration['dependencies'] if exists else []})
    by_id = {view['id']: view for view in views}
    for view in views:
        if view['state'] == 'observed' and any(by_id[key]['state'] != 'observed' for key in view['dependencies']):
            view.update(state='incomplete', reason='A required native value catalog was not captured.')
    native_status, outcome = native_status_and_outcome(record)
    inputs = request['inputs']
    fields = {'attempt_id': request['attempt_id'], 'request_id': request['id'], 'result_id': record['id'],
              'producer': {'id': 'axiom', 'build_sha256': producer_sha256},
              'bindings': bindings(request), 'native_outcome': outcome,
              'coverage': 'complete' if all(view['state'] == 'observed' for view in views if view['required']) else 'incomplete',
              'retained_inputs': retained_inputs}
    summary = {'state': record['state'], 'attempt_id': request['attempt_id'], 'result_id': record['id'],
               'candidate_id': request['candidate']['id'], 'native_status': native_status,
               'native_exit_code': record['native_exit_code'], 'failure_present': record['failure'] is not None,
               'findings_count': len(record['findings']), 'context_id': inputs['context']['id']}
    summary['overview'] = overview(record)
    return fields, views, summary


def overview(record):
    """Small, explicitly partial presentation; complete evidence stays indexed.

    Only fixed scalar outcome fields and counts cross the default client route.
    Variable diagnostic, registry, expectation and trace data is read separately.
    """
    def scalars(value, names):
        return {name: value[name] for name in names if name in value
                and (value[name] is None or type(value[name]) in (str, int, float, bool))}

    def native(value):
        body = value.get('result', {})
        result = scalars(body, ('nativeOutcome', 'nativeScopeQualified', 'qualification'))
        for name, fields in {
            'initialization': ('schema', 'status', 'scope', 'nativeErrorObserved', 'recipeEffectsChecked'),
            'expectations': ('status',), 'context': ('id',),
            'assessment': ('execution', 'coverage'),
        }.items():
            if name in body:
                result[name] = scalars(body[name], fields)
        if 'assessment' in result and 'intentCounts' in body['assessment']:
            result['assessment']['intentCounts'] = scalars(body['assessment']['intentCounts'],
                ('matched', 'mismatch', 'unsupported', 'not-evaluated'))
        return {**scalars(value, ('status',)), 'result': result}

    original = record['native']
    result = None
    if original is not None:
        body = original.get('result', {})
        if 'baseline' in body and 'candidate' in body:
            result = {**scalars(original, ('status',)), 'result': {
                side: native(body[side]) for side in ('baseline', 'candidate')}}
            for name in ('comparison', 'sourceComparison', 'effectComparison'):
                if name in body:
                    result['result'][name] = scalars(body[name], ('status',))
        else:
            result = native(original)
    return {'format': OVERVIEW_V1, 'native': result,
            'failure_type': None if record['failure'] is None else record['failure']['type'],
            'detail_state': 'not-loaded'}


def bindings(request):
    inputs = request['inputs']
    # Request captures the complete saved/configuration inventory and exact
    # selected context/engine/runtime/JVM records, not only version labels.
    return {'source': _digest({'candidate': request['candidate'], 'baseline': request['baseline']}),
            'configuration': _digest({'program': request['program'], 'intent': request['intent_path'], 'inputs': inputs}),
            'context': _digest(inputs), 'engine': inputs['engineManifestSha256'],
            'runtime': inputs['runtimeManifestSha256'],
            'jvm': _digest({'files': inputs['toolchainFiles'], 'policy': inputs['platformJvmPolicySha256']})}
