"""Admit Axiom's existing saved-check evidence through an injected Core read port.

No Shell, Project Intelligence, Core implementation or native runtime is needed
to interpret this historical owner contract. Storage access belongs to the port.
"""
from workbench_api.retained_snapshots import RetainedSnapshotAdmission
from . import material_checks, retained_snapshots

_AUTHORITY = {'source_mutated': False, 'minecraft_launched': False,
              'runtime_image_required': False, 'validity_qualified': False,
              'whole_pack_parity': False}


def _sealed(inputs, value, kind):
    if (not isinstance(value, dict)
            or inputs.seal(kind, {k: v for k, v in value.items() if k != 'id'}) != value):
        raise ValueError('retained ' + kind + ' identity differs')


def _program(inputs, record, directory, role):
    candidate = record['candidate']
    _sealed(inputs, candidate, 'candidate')
    if (set(candidate) != {'format', 'source', 'files', 'id'}
            or candidate['format'] != 'workbench-saved-candidate-v1'
            or not isinstance(candidate['source'], dict)):
        raise ValueError('unsupported retained source candidate')
    sources = inputs.source_files(directory, candidate['files'])
    raw, observed = material_checks.program_snapshot(sources, record['program_root'])
    if observed != record['program'] or inputs.read_input(role) != raw:
        raise ValueError('retained native program differs from saved source')
    return sources


def admit_retained_snapshot(request, inputs):
    """Validate the V1 saved request, complete source/ZIP/intent and scope binding."""
    _sealed(inputs, request, 'material-check-request')
    if (request.get('format') != 'workbench-material-check-request-v1'
            or request.get('state') != 'prepared-not-run'
            or request.get('authority') != _AUTHORITY
            or request.get('attempt_id') != inputs.attempt_id
            or request.get('workspace_uri') != inputs.context.get('workspace_uri')
            or request.get('selection_id') != inputs.context.get('selection_id')
            or request['inputs']['context']['id'] != inputs.context.get('context_id')
            or inputs.context.get('owner') != 'axiom'):
        raise ValueError('retained Axiom request differs from selected custody or authority')
    sources = _program(inputs, request, 'source', 'saved-program')
    intent = inputs.read_input('intent')
    expected_intent = b'{}' if request['intent_path'] is None else sources[request['intent_path']]
    if intent != expected_intent:
        raise ValueError('retained Axiom intent differs from saved source')
    material_checks.intent(intent)
    if request['baseline'] is not None:
        _program(inputs, request['baseline'], 'baseline-source', 'saved-baseline-program')
    return RetainedSnapshotAdmission(
        scope=lambda manifest: retained_snapshots.resolve_scope(
            request, manifest, scope_identity=inputs.scope_identity),
        expected={'request_id': request['id'], 'bindings': retained_snapshots.bindings(request)},
        supported_schemas=retained_snapshots.SUPPORTED_SCHEMAS)
