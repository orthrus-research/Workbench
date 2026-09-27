"""Compose saved source, Core custody, profile observation and Atlas analysis."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import Event

from workbench_api.processes import capture_process, ProcessError, open_process_output, read_captured_process
from workbench_api.host_filesystem import (
    count_uncertain_record_stages, publish_commit_witness_bytes,
    publish_immutable_bytes, read_bounded_single_link_bytes,
    read_private_single_link_bytes,
)
from workbench_api.profile_extensions import require_profile_extension, profile_extension_identity
from workbench_api.managed_attempts import managed_attempts
from workbench_api.fixture_selections import fixture_selections
from workbench_core import check_storage as storage, capture_workspace as workspace_storage
from workbench_core.filesystem_paths import native_path
from workbench_core.runtime_java import probe_java, java_execution_path
from workbench_project_intelligence.working_tree import capture_source_inputs, observe_source
from workbench_project_intelligence.saved_candidate import candidate_manifest, stage_candidate

GROUP = 'workbench.recipe_captures'
ATTEMPT_FAMILY = 'recipe-capture-v1'
ATTEMPT_PREFIX = 'recipe-capture'
REQUEST = 'workbench-developer-recipe-capture-request-v1'
PREPARED = 'workbench-developer-recipe-capture-prepared-v1'
RESULT = 'workbench-developer-recipe-capture-result-v1'
PREPARE_INTENT = 'workbench-developer-recipe-capture-prepare-intent-v1'
PREPARE_READY = 'workbench-developer-recipe-capture-prepare-ready-v1'
_CUSTODY = ('request.json', 'prepared.json', 'launch.json', 'runtime-lock.json',
            'protocol.json', 'input-manifest.json', 'audit.json')
_RECORD_BYTE_LIMIT = 32 * 1024**2
_WITNESS_BYTE_LIMIT = 4096
_PREPARE_STAGE_TARGETS = (
    'request.json', 'prepare-intent.json', 'prepare-started.json',
    'prepared.json', 'prepare-ready.json', 'prepare-failed.json',
    'run-started.json', 'result.json', 'input-manifest.json',
)


def _digest(value):
    return sha256(storage.canonical(value)).hexdigest()


def _file(path, *, check_cancelled=lambda: None):
    path = storage.ordinary(Path(path))
    before = native_path(path).stat()
    digest = sha256()
    size = 0
    with native_path(path).open('rb') as stream:
        while True:
            check_cancelled()
            raw = stream.read(1024 * 1024)
            if not raw:
                break
            digest.update(raw)
            size += len(raw)
    after = native_path(storage.ordinary(path)).stat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if identity(before) != identity(after) or size != before.st_size:
        raise ValueError('retained capture evidence changed while reading')
    return {'path': str(path), 'size': size, 'sha256': digest.hexdigest()}


def _record(attempt, name, kind, value):
    result = storage.seal(kind, value)
    storage.write_json(attempt / name, result)
    return result


def _read(attempt, name, kind, *, request_id=None):
    value = storage.read_json(attempt / name)
    if (not isinstance(value, dict)
            or storage.seal(kind, {k: v for k, v in value.items() if k != 'id'}) != value
            or request_id is not None and value.get('request_id') != request_id):
        raise ValueError('retained capture record identity changed')
    return value


def _write_input_manifest(path, value):
    # Keep the V1 canonical JSON and newline while Core owns publication.
    publish_immutable_bytes(
        path, storage.canonical(value) + b'\n',
        byte_limit=_RECORD_BYTE_LIMIT,
    )


def _decode_unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise storage.CheckStorageError('managed record has duplicate keys')
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _read_bounded_ordinary_json_with_raw(path):
    # Historical V1 files need the ordinary-file reader, including prior modes.
    storage.ordinary(path)
    raw = read_bounded_single_link_bytes(path, byte_limit=_RECORD_BYTE_LIMIT)
    return _decode_unique_json(raw), raw


def _read_bounded_ordinary_json(path):
    return _read_bounded_ordinary_json_with_raw(path)[0]


def _read_prepared_record(attempt, *, request_id):
    value, raw = _read_bounded_ordinary_json_with_raw(attempt / 'prepared.json')
    if (not isinstance(value, dict)
            or storage.seal('recipe-capture-prepared', {k: v for k, v in value.items() if k != 'id'}) != value
            or value.get('request_id') != request_id):
        raise ValueError('retained capture record identity changed')
    return value, raw


def _request_raw(attempt, request):
    raw = storage.read_bytes(attempt / 'request.json', byte_limit=_RECORD_BYTE_LIMIT)
    if raw != storage.canonical(request) + b'\n':
        raise ValueError('retained capture request bytes changed')
    return raw


def _read_preparation_witness(attempt, name, kind, *, fields):
    raw = read_private_single_link_bytes(attempt / name, byte_limit=_WITNESS_BYTE_LIMIT)
    value = _decode_unique_json(raw)
    if (not isinstance(value, dict) or set(value) != fields
            or storage.seal(kind, {k: v for k, v in value.items() if k != 'id'}) != value
            or raw != storage.canonical(value) + b'\n'):
        raise ValueError('retained preparation witness identity changed')
    return value


def _uncertain_prepare_stages(attempt):
    return count_uncertain_record_stages(attempt, targets=_PREPARE_STAGE_TARGETS) != 0


def _verify_prepared_ready(attempt, request, prepared, prepared_raw, *, check_stages=True):
    if check_stages and _uncertain_prepare_stages(attempt):
        raise ValueError('preparation has an uncertain record stage; make a new plan')
    if not os.path.lexists(native_path(attempt / 'prepare-intent.json')):
        raise ValueError('preparation has no durable intent witness; make a new plan')
    if not os.path.lexists(native_path(attempt / 'prepare-ready.json')):
        raise ValueError('preparation has no durable ready witness; make a new plan')
    intent = _read_preparation_witness(
        attempt, 'prepare-intent.json', 'recipe-capture-prepare-intent',
        fields={'format', 'attempt_id', 'request_id', 'request_sha256', 'id'},
    )
    ready = _read_preparation_witness(
        attempt, 'prepare-ready.json', 'recipe-capture-prepare-ready',
        fields={'format', 'attempt_id', 'request_id', 'request_sha256',
                'intent_id', 'prepared_id', 'prepared_sha256', 'prepared_size', 'id'},
    )
    request_sha256 = sha256(_request_raw(attempt, request)).hexdigest()
    if (intent['format'] != PREPARE_INTENT or intent['attempt_id'] != attempt.name
            or intent['request_id'] != request['id'] or intent['request_sha256'] != request_sha256
            or ready['format'] != PREPARE_READY or ready['attempt_id'] != attempt.name
            or ready['request_id'] != request['id'] or ready['request_sha256'] != request_sha256
            or ready['intent_id'] != intent['id'] or ready['prepared_id'] != prepared['id']
            or ready['prepared_sha256'] != sha256(prepared_raw).hexdigest()
            or ready['prepared_size'] != len(prepared_raw)):
        raise ValueError('retained preparation witness does not bind the prepared capture')


def _write_result_record(attempt, value):
    result = storage.seal('recipe-capture-result', value)
    publish_immutable_bytes(attempt / 'result.json', storage.canonical(result) + b'\n',
                            byte_limit=_RECORD_BYTE_LIMIT)
    return result


def _read_result_record(attempt, *, request_id):
    value = _read_bounded_ordinary_json(attempt / 'result.json')
    if (not isinstance(value, dict)
            or storage.seal('recipe-capture-result', {k: v for k, v in value.items() if k != 'id'}) != value
            or value.get('request_id') != request_id):
        raise ValueError('retained capture record identity changed')
    return value


def _owner(profile):
    owner = require_profile_extension(GROUP, profile)
    if getattr(owner, 'PROFILE_API_VERSION', None) != 1:
        raise ValueError('unsupported recipe capture profile API')
    return owner, profile_extension_identity(GROUP, profile)


def _overlap(*roots):
    roots = [Path(root).resolve() for root in roots]
    for i, root in enumerate(roots):
        for other in roots[i + 1:]:
            if root == other or root.is_relative_to(other) or other.is_relative_to(root):
                raise ValueError('capture storage, checkout, runtime and Java must be separate trees')


class Cancellation:
    def __init__(self, event, attempt=None):
        self.event, self.attempt = event, attempt

    def is_set(self):
        return self.event.is_set() or self.attempt is not None and managed_attempts().cancellation_requested(self.attempt)

    def check(self):
        if self.is_set():
            raise ValueError('recipe capture cancelled; retained evidence remains available')


def _java(home):
    executable = home / ('bin/java.exe' if os.name == 'nt' else 'bin/java')
    before = _file(executable)
    properties = probe_java(java_execution_path(executable))
    if _file(executable) != before:
        raise ValueError('selected Java executable changed during inspection')
    version = properties['java_version']
    major = int(version.split('.')[1] if version.startswith('1.') else re.split(r'[.+_-]', version)[0])
    return {**before, 'major': major}, properties


def inspect_fixture_selection(*, source, profile, runtime=None, java_home=None, cancelled=None):
    """Read Core's selected locations, then let the profile qualify their bytes."""
    try:
        selection = fixture_selections().resolve(profile, source, runtime=runtime, java_home=java_home)
        owner, _ = _owner(profile)
        descriptor = owner.descriptor()
        selected_runtime, selected_java = Path(selection['runtime']), Path(selection['java_home'])
        exclusions = sorted(set(descriptor['runtime_exclusions']) | set(descriptor['source_roots']))
        files = workspace_storage.inventory(selected_runtime, exclude=exclusions,
                                            cancelled=cancelled.is_set if cancelled is not None else lambda: False)
        artifacts = owner.select_runtime_artifacts(files)
        owner.observer_classpath(selected_runtime, files, artifacts)
        java, _ = _java(selected_java)
        if java['major'] != descriptor['platform']['java_major']:
            raise ValueError(
                f"selected Java {java['major']} differs from profile Java "
                f"{descriptor['platform']['java_major']}")
        return {**selection, 'state': 'ready-for-planning', 'artifact_paths': artifacts,
                'java': java, 'native_executed': False}
    except (OSError, ValueError) as exc:
        return {'state': 'needs-recovery', 'profile': profile, 'workspace': str(source),
                'reason': f'{type(exc).__name__}: {exc}', 'native_executed': False,
                'recovery': [
                    'Inspect the selected profile and choose a compatible prepared server and JDK.',
                    'Update locations with workbench capture recipes fixtures set, or pass --runtime and --java-home to plan.',
                    'Other Workbench source and graph workflows remain available without this native fixture.',
                ]}


def plan(root, *, source, runtime=None, java_home=None, profile, heap_mib, cancelled):
    """Retain immutable source and a reviewed exact environment inventory."""
    cancel = Cancellation(cancelled)
    cancel.check()
    source = Path(source).expanduser().absolute()
    selected_root = managed_attempts().default_root(ATTEMPT_FAMILY, workspace=source) if root is None else Path(root).expanduser().absolute()
    selection = fixture_selections().resolve(profile, source, runtime=runtime, java_home=java_home)
    runtime, java_home = Path(selection['runtime']), Path(selection['java_home'])
    _overlap(selected_root, source, runtime, java_home)
    owner, provider = _owner(profile)
    descriptor = owner.descriptor()
    inputs = capture_source_inputs(source)
    candidate = candidate_manifest(inputs)
    source_binding = owner.build_source_binding(candidate, deleted_paths=list(inputs.deleted_paths))
    if not set(descriptor['required_source_files']) <= inputs.sources.keys():
        raise ValueError('saved checkout lacks required profile source files')
    exclusions = sorted(set(descriptor['runtime_exclusions']) | set(descriptor['source_roots']))
    files = workspace_storage.inventory(runtime, exclude=exclusions, cancelled=cancel.is_set)
    try:
        artifacts = owner.select_runtime_artifacts(files)
        owner.observer_classpath(runtime, files, artifacts)
    except ValueError as exc:
        raise ValueError(f"selected runtime does not satisfy profile {profile}: {exc}. "
                         "Choose another prepared server with --runtime or update the Core fixture registration") from exc
    java, properties = _java(java_home)
    if java['major'] != descriptor['platform']['java_major']:
        raise ValueError('selected game/build JDK does not match the profile Java major; '
                         'choose --java-home or update the Core fixture registration')
    java_files = workspace_storage.inventory(java_home, contained_file_links=True,
                                             contained_directory_links=True, cancelled=cancel.is_set)
    executable_row = next((row for row in java_files if row['path'] == Path(java['path']).relative_to(java_home).as_posix()), None)
    if executable_row is None or any(executable_row[key] != java[key] for key in ('size', 'sha256')):
        raise ValueError('selected Java inventory differs from the inspected executable')
    if type(heap_mib) is not int or heap_mib < 1:
        raise ValueError('heap MiB must be positive')
    cancel.check()
    attempt = managed_attempts().allocate(
        ATTEMPT_FAMILY, ATTEMPT_PREFIX, requested_root=root, workspace=source,
    ).path
    execution_root = java_execution_path(attempt)
    stage_candidate(inputs, attempt / 'source')
    if observe_source(source) != inputs.observation:
        raise ValueError('saved source changed during capture planning; retained attempt is incomplete')
    body = {'format': REQUEST, 'attempt_id': attempt.name, 'profile': profile,
            'execution_root': str(execution_root),
            'provider': provider, 'descriptor': descriptor, 'observer_sources': owner.observer_source_manifest(),
            'workspace': str(source), 'runtime': str(runtime), 'java_home': str(java_home),
            'fixture_selection': selection,
            'candidate': candidate, 'source_binding': source_binding, 'deleted_paths': list(inputs.deleted_paths),
            'runtime_files': files, 'runtime_exclusions': exclusions, 'artifact_paths': artifacts,
            'java_files': java_files, 'java': java, 'java_properties': properties, 'heap_mib': heap_mib,
            'state': 'planned', 'native_executed': False,
            'eula_acceptance': 'required-as-explicit-run-argument',
            'retention': 'Failed attempts and complete process/capture evidence are retained in Core storage.'}
    return _record(attempt, 'request.json', 'recipe-capture-request', body)


def load(root, identity):
    if re.fullmatch(r'recipe-capture-[0-9a-f]{32}', identity or '') is None:
        raise ValueError('select an exact recipe capture attempt ID')
    reference = managed_attempts().open(
        ATTEMPT_FAMILY, ATTEMPT_PREFIX, identity,
        requested_root=root, legacy_basename='recipe-captures',
    )
    attempt = reference.path
    request = _read(attempt, 'request.json', 'recipe-capture-request')
    if request.get('format') != REQUEST or request.get('attempt_id') != identity:
        raise ValueError('unsupported capture request or changed attempt identity')
    return reference, request


def _source(attempt, request, *, cancelled=lambda: False):
    rows = request['candidate']['files']
    expected = [{**row, 'mode': row['mode'] & 0o777} for row in rows]
    actual = workspace_storage.inventory(attempt / 'source', cancelled=cancelled)
    # Git executable bits cannot always be represented on Windows. Content and
    # names remain exact; the candidate keeps the original mode declarations.
    compared = lambda rows: [{k: v for k, v in row.items() if k != 'mode' or os.name != 'nt'} for row in rows]
    if compared(actual) != compared(expected):
        raise ValueError('retained saved source changed')
    return {row['path']: storage.read_bytes(attempt / 'source' / row['path']) for row in rows}


def _provider(request):
    owner, provider = _owner(request['profile'])
    if (provider != request['provider'] or owner.descriptor() != request['descriptor']
            or owner.observer_source_manifest() != request['observer_sources']):
        raise ValueError('capture profile or observer source changed; create a fresh plan')
    return owner


def _current(attempt, request):
    owner = _provider(request)
    if observe_source(Path(request['workspace'])) != request['candidate']['source']:
        raise ValueError('saved checkout changed; create a fresh capture plan')
    _source(attempt, request)
    return owner


def _fail(attempt, filename, kind, request, stage, exc):
    if not (attempt / filename).exists():
        body = {'format': RESULT, 'request_id': request['id'], 'attempt_id': attempt.name,
                'state': 'failed', 'stage': stage, 'error': f'{type(exc).__name__}: {exc}',
                'native_admitted': False}
        if filename == 'result.json' and kind == 'recipe-capture-result':
            _write_result_record(attempt, body)
        else:
            _record(attempt, filename, kind, body)


def prepare(root, identity, confirm, *, cancelled):
    reference, request = load(root, identity)
    attempt = reference.path
    if confirm != request['id']:
        raise ValueError('preparation requires the exact reviewed plan ID')
    with managed_attempts().execution(reference):
        if any(os.path.lexists(native_path(attempt / name)) for name in (
                'prepare-intent.json', 'prepare-started.json', 'prepared.json',
                'prepare-ready.json', 'prepare-failed.json')):
            raise ValueError('this preparation was already attempted; make a new plan')
        if _uncertain_prepare_stages(attempt):
            raise ValueError('this preparation has an uncertain record stage; make a new plan')
        cancel = Cancellation(cancelled, reference)
        cancel.check()
        owner = _current(attempt, request)
        execution_root = java_execution_path(attempt)
        if str(execution_root) != request['execution_root']:
            raise ValueError('reviewed Java execution path changed; make a new plan')
        try:
            request_sha256 = sha256(_request_raw(attempt, request)).hexdigest()
            intent = storage.seal('recipe-capture-prepare-intent', {
                'format': PREPARE_INTENT, 'attempt_id': identity,
                'request_id': request['id'], 'request_sha256': request_sha256,
            })
            publish_immutable_bytes(attempt / 'prepare-intent.json',
                                    storage.canonical(intent) + b'\n',
                                    byte_limit=_WITNESS_BYTE_LIMIT)
            storage.write_json(attempt / 'prepare-started.json', {'request_id': request['id']})
            retained = workspace_storage.materialize(
                attempt, runtime_root=Path(request['runtime']), runtime_files=request['runtime_files'],
                java_home=Path(request['java_home']), java_files=request['java_files'],
                source_files=_source(attempt, request), source_rows=request['candidate']['files'],
                source_roots=request['descriptor']['source_roots'], runtime_exclude=request['runtime_exclusions'],
                cancelled=cancel.is_set)
            runtime, java_home = Path(retained['runtime']), Path(retained['java_home'])
            java, properties = _java(java_home)
            if any(java[key] != request['java'][key] for key in ('size', 'sha256', 'major')):
                raise ValueError('retained Java executable differs from the reviewed selection')
            # java.home is location custody, other properties are runtime identity.
            original = {k: v for k, v in request['java_properties'].items() if k != 'java_home'}
            if {k: v for k, v in properties.items() if k != 'java_home'} != original:
                raise ValueError('retained Java runtime identity differs from the selected runtime')
            classpath = owner.observer_classpath(execution_root / 'runtime', retained['runtime_files'], request['artifact_paths'])
            build = owner.build_observer(execution_root / 'java', classpath, execution_root / 'observer-build', cancelled=cancel)
            cancel.check()
            built = _file(Path(build['artifact']['path']))
            if built != build['artifact']:
                raise ValueError('built observer artifact changed')
            workspace_storage.replace_file(runtime, request['descriptor']['observer_path'], storage.read_bytes(Path(built['path'])))
            runtime_files = workspace_storage.inventory(runtime, cancelled=cancel.is_set)
            if workspace_storage.inventory(java_home, cancelled=cancel.is_set) != retained['java_files']:
                raise ValueError('retained Java changed during observer compilation')
            _current(attempt, request)
            prepared = storage.seal('recipe-capture-prepared',
                                    {'format': PREPARED, 'request_id': request['id'], 'attempt_id': identity,
                                     'state': 'prepared', 'native_executed': False, 'runtime_files': runtime_files,
                                     'execution_root': str(execution_root),
                                     'java_files': retained['java_files'], 'java': java, 'java_properties': properties,
                                     'observer_build': build})
            prepared_raw = storage.canonical(prepared) + b'\n'
            publish_immutable_bytes(attempt / 'prepared.json', prepared_raw,
                                    byte_limit=_RECORD_BYTE_LIMIT)
            reopened, observed_raw = _read_prepared_record(attempt, request_id=request['id'])
            if reopened != prepared or observed_raw != prepared_raw:
                raise ValueError('published prepared record changed before ready witness')
            ready = storage.seal('recipe-capture-prepare-ready', {
                'format': PREPARE_READY, 'attempt_id': identity,
                'request_id': request['id'], 'request_sha256': request_sha256,
                'intent_id': intent['id'], 'prepared_id': prepared['id'],
                'prepared_sha256': sha256(prepared_raw).hexdigest(),
                'prepared_size': len(prepared_raw),
            })
            publish_commit_witness_bytes(attempt / 'prepare-ready.json',
                                         storage.canonical(ready) + b'\n',
                                         byte_limit=_WITNESS_BYTE_LIMIT)
            return prepared
        except BaseException as exc:
            _fail(attempt, 'prepare-failed.json', 'recipe-capture-failure', request, 'prepare', exc)
            raise


def _environment(attempt):
    temporary = attempt / 'temporary'
    temporary.mkdir(mode=0o700)
    env = {'TMPDIR': str(temporary), 'TEMP': str(temporary), 'TMP': str(temporary)}
    if os.name == 'nt':
        for key in ('SystemRoot', 'WINDIR'):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def run(root, identity, confirm, *, accept_eula, cancelled):
    reference, request = load(root, identity)
    attempt = reference.path
    if not accept_eula:
        raise ValueError('Minecraft EULA acceptance is required: read https://www.minecraft.net/en-us/eula and supply --accept-eula')
    with managed_attempts().execution(reference):
        if any(os.path.lexists(native_path(attempt / name)) for name in ('run-started.json', 'result.json')):
            raise ValueError('this capture was already attempted; make a new plan')
        if os.path.lexists(native_path(attempt / 'prepare-failed.json')):
            raise ValueError('this preparation failed; make a new plan')
        if _uncertain_prepare_stages(attempt):
            raise ValueError('preparation has an uncertain record stage; make a new plan')
        prepared, prepared_raw = _read_prepared_record(attempt, request_id=request['id'])
        if prepared.get('format') != PREPARED or confirm != prepared['id']:
            raise ValueError('execution requires the exact prepared capture ID')
        _verify_prepared_ready(attempt, request, prepared, prepared_raw)
        cancel = Cancellation(cancelled, reference)
        cancel.check()
        owner = _current(attempt, request)
        execution_root = java_execution_path(attempt)
        if str(execution_root) != prepared['execution_root']:
            raise ValueError('prepared Java execution path changed; make a new plan')
        if workspace_storage.inventory(attempt / 'runtime', cancelled=cancel.is_set) != prepared['runtime_files']:
            raise ValueError('prepared runtime changed')
        if workspace_storage.inventory(attempt / 'java', cancelled=cancel.is_set) != prepared['java_files']:
            raise ValueError('prepared Java changed')
        storage.write_json(attempt / 'run-started.json', {'request_id': request['id'], 'prepared_id': prepared['id'], 'explicit_eula_acceptance': True})
        stage = 'execution-preparation'
        try:
            execution = attempt / 'execution'
            storage.copy_manifest(attempt / 'runtime', execution, prepared['runtime_files'], cancelled=cancel.is_set)
            settings = execution / 'server.properties'
            exists = native_path(settings).exists()
            raw = storage.read_bytes(settings) if exists else b''
            workspace_storage.replace_file(execution, 'server.properties', owner.prepare_server_properties(raw),
                                           expected_sha256=sha256(raw).hexdigest() if exists else None)
            workspace_storage.replace_file(execution, 'eula.txt', b'eula=true\n')
            execution_files = workspace_storage.inventory(execution, cancelled=cancel.is_set)
            environment = _environment(execution_root)
            game_java = {**prepared['java'], 'path': str(java_execution_path(Path(prepared['java']['path'])))}
            dependency_lock = {'format': 'workbench-recipe-capture-runtime-lock-v1',
                               'source_binding_id': request['source_binding']['id'], 'runtime_files': execution_files,
                               'java_files': prepared['java_files'], 'java': game_java,
                               'environment': environment, 'observer': prepared['observer_build']['artifact']}
            protocol = {'format': 'workbench-recipe-capture-protocol-v1', 'profile': request['provider'],
                        'descriptor': request['descriptor'], 'observer_sources': request['observer_sources'],
                        'heap_mib': request['heap_mib'], 'timeout_seconds': None, 'output_limit': None,
                        'explicit_eula_acceptance': True}
            storage.write_json(attempt / 'runtime-lock.json', dependency_lock)
            storage.write_json(attempt / 'protocol.json', protocol)
            manifest = owner.build_capture_input(request['candidate'], deleted_paths=request['deleted_paths'],
                platform=request['descriptor']['platform'], runtime_artifacts=request['descriptor']['runtime_artifacts'],
                capture_id=identity, launch_id=identity + '-launch', candidate_lock_sha256=_digest(dependency_lock),
                adapter_profile_sha256=_digest(protocol), observation_preparation=request['descriptor']['observation_preparation'])
            input_path = attempt / 'input-manifest.json'
            _write_input_manifest(input_path, manifest)
            launch = owner.plan_capture_launch(manifest, runtime_files=execution_files,
                artifact_paths=request['artifact_paths'], observer_path=request['descriptor']['observer_path'],
                observer_build=prepared['observer_build'], java=game_java, runtime_root=execution_root / 'execution',
                input_manifest_path=execution_root / input_path.name, input_manifest_sha256=_file(input_path)['sha256'],
                output=execution_root / 'capture', heap_mib=request['heap_mib'])
            storage.write_json(attempt / 'launch.json', launch)
            _current(attempt, request)
            cancel.check()
            if workspace_storage.inventory(execution, cancelled=cancel.is_set) != execution_files:
                raise ValueError('execution runtime changed before launch')
            if workspace_storage.inventory(attempt / 'java', cancelled=cancel.is_set) != prepared['java_files']:
                raise ValueError('execution Java changed before launch')
            stage = 'native-execution'
            process = capture_process(launch['argv'], directory=attempt / 'process', binding=launch['id'], cwd=execution_root / 'execution',
                stdin=b'', environment=environment, cancelled=cancel, timeout_seconds=None, output_limit=None)
            if process.exit_code != 0:
                raise ValueError(f'game exited with status {process.exit_code}; process evidence retained')
            cancel.check()
            stage = 'capture-admission'
            _provider(request)
            adapter = require_profile_extension('workbench.recipe_graphs', request['profile'])
            if (type(getattr(adapter, 'RECIPE_GRAPH_API_VERSION', None)) is not int
                    or adapter.RECIPE_GRAPH_API_VERSION != 1
                    or not callable(getattr(adapter, 'project_capture', None))):
                raise ValueError('selected profile has no compatible recipe graph API 1 adapter')
            projection = adapter.project_capture(attempt / 'capture', attempt / 'graph', input_manifest=input_path,
                                                  check_cancelled=cancel.check)
            stage = 'audit'
            from workbench_atlas_recipe_health import open_recipe_health, audit_recipe_dead_ends
            with open_recipe_health(attempt / 'graph', check_cancelled=cancel.check) as view:
                if (not isinstance(projection, dict) or projection.get('state') != 'complete'
                        or projection.get('graph_set_id') != view.manifest['graph_set_id']
                        or projection.get('root') != str((attempt / 'graph').resolve())
                        or projection.get('capture_manifest_sha256') != _file(attempt / 'capture/manifest.json')['sha256']):
                    raise ValueError('profile returned an invalid recipe graph projection receipt')
                report = audit_recipe_dead_ends(view, check_cancelled=cancel.check)
            storage.write_json(attempt / 'audit.json', report, byte_limit=None)
            cancel.check()
            return _write_result_record(attempt,
                                        {'format': RESULT, 'request_id': request['id'], 'prepared_id': prepared['id'],
                                         'attempt_id': identity, 'state': 'complete', 'native_admitted': True,
                                         'process': process.reference, 'launch_id': launch['id'], 'projection': projection,
                                         'input_manifest': _file(input_path), 'audit': _file(attempt / 'audit.json'),
                                         'custody': {name: _file(attempt / name, check_cancelled=cancel.check) for name in _CUSTODY},
                                         'capture_files': workspace_storage.inventory(attempt / 'capture', cancelled=cancel.is_set),
                                         'observer_files': workspace_storage.inventory(attempt / 'observer-build', cancelled=cancel.is_set),
                                         'summary': report['summary'], 'coverage': report['coverage']})
        except BaseException as exc:
            _fail(attempt, 'result.json', 'recipe-capture-result', request, stage, exc)
            raise


def show(root, identity, *, cancelled=None):
    cancel = Cancellation(cancelled if cancelled is not None else Event())
    cancel.check()
    reference, request = load(root, identity)
    attempt = reference.path
    _source(attempt, request, cancelled=cancel.is_set)
    if (attempt / 'result.json').exists():
        result = _read_result_record(attempt, request_id=request['id'])
        if result['state'] == 'complete':
            if set(result.get('custody', {})) != set(_CUSTODY):
                raise ValueError('retained capture custody is incomplete')
            for name in _CUSTODY:
                if _file(attempt / name, check_cancelled=cancel.check) != result['custody'][name]:
                    raise ValueError('retained capture evidence changed')
            for name, key in (('capture', 'capture_files'), ('observer-build', 'observer_files')):
                if workspace_storage.inventory(attempt / name, cancelled=cancel.is_set) != result[key]:
                    raise ValueError('retained capture evidence changed')
            prepared, prepared_raw = _read_prepared_record(attempt, request_id=request['id'])
            if (os.path.lexists(native_path(attempt / 'prepare-intent.json'))
                    or os.path.lexists(native_path(attempt / 'prepare-ready.json'))):
                _verify_prepared_ready(attempt, request, prepared, prepared_raw,
                                       check_stages=False)
            launch = storage.read_json(attempt / 'launch.json')
            manifest = _read_bounded_ordinary_json(attempt / 'input-manifest.json')
            capture_manifest = storage.read_json(attempt / 'capture/manifest.json')
            if (prepared['id'] != result['prepared_id'] or launch['id'] != result['launch_id']
                    or manifest['candidate_lock_sha256'] != _digest(storage.read_json(attempt / 'runtime-lock.json'))
                    or manifest['adapter_profile_sha256'] != _digest(storage.read_json(attempt / 'protocol.json'))
                    or manifest['capture_id'] != identity or manifest['launch_id'] != identity + '-launch'
                    or any(manifest[key] != capture_manifest[key] for key in
                           ('capture_id', 'launch_id', 'candidate_lock_sha256', 'adapter_profile_sha256'))
                    or result['projection']['capture_manifest_sha256'] != _file(attempt / 'capture/manifest.json')['sha256']
                    or result['input_manifest'] != result['custody']['input-manifest.json']
                    or result['audit'] != result['custody']['audit.json']):
                raise ValueError('retained capture evidence chain differs')
            process = read_captured_process(
                attempt / 'process', binding=result['launch_id'],
                expected_id=result['process']['id'],
            )
            if process.reference != result['process'] or process.exit_code != 0:
                raise ValueError('retained process result differs')
            for output in (process.stdout, process.stderr):
                with open_process_output(output) as stream:
                    while stream.read(1024 * 1024):
                        cancel.check()
                cancel.check()
            from workbench_atlas_recipe_health import open_recipe_health
            with open_recipe_health(attempt / 'graph', check_cancelled=cancel.check) as view:
                if view.manifest['graph_set_id'] != result['projection']['graph_set_id']:
                    raise ValueError('retained graph identity changed')
        return result
    if os.path.lexists(native_path(attempt / 'prepare-failed.json')):
        return _read(attempt, 'prepare-failed.json', 'recipe-capture-failure', request_id=request['id'])
    if os.path.lexists(native_path(attempt / 'prepared.json')):
        prepared, prepared_raw = _read_prepared_record(attempt, request_id=request['id'])
        if (attempt / 'run-started.json').exists():
            return {'format': RESULT, 'attempt_id': identity,
                    'state': 'running' if managed_attempts().active(reference) else 'interrupted',
                    'prepared': prepared}
        has_intent = os.path.lexists(native_path(attempt / 'prepare-intent.json'))
        has_ready = os.path.lexists(native_path(attempt / 'prepare-ready.json'))
        if _uncertain_prepare_stages(attempt) or has_intent != has_ready:
            return {'format': RESULT, 'attempt_id': identity,
                    'state': 'preparing' if managed_attempts().active(reference) else 'interrupted',
                    'prepared': prepared}
        if has_intent:
            _verify_prepared_ready(attempt, request, prepared, prepared_raw)
        # A historical V1 prepared record remains readable. Without the new
        # ready witness it cannot authorize a new native run.
        return prepared
    if (os.path.lexists(native_path(attempt / 'prepare-intent.json'))
            or os.path.lexists(native_path(attempt / 'prepare-started.json'))
            or os.path.lexists(native_path(attempt / 'prepare-ready.json'))
            or _uncertain_prepare_stages(attempt)):
        return {'format': RESULT, 'attempt_id': identity, 'state': 'preparing' if managed_attempts().active(reference) else 'interrupted', 'request': request}
    return request


def cancel(root, identity):
    reference, request = load(root, identity)
    managed_attempts().request_cancel(reference, request['id'])
    return {'format': 'workbench-recipe-capture-cancellation-v1', 'attempt_id': identity, 'state': 'requested'}


def export(root, identity, output, *, cancelled=None):
    """Export a verified completed attempt without carrying its runnable runtime."""
    cancel = Cancellation(cancelled if cancelled is not None else Event())
    cancel.check()
    reference, _ = load(root, identity)
    attempt = reference.path
    output = Path(output).expanduser().absolute()
    if output.resolve().is_relative_to(attempt.resolve()):
        raise ValueError('select an export destination outside the retained attempt')
    with managed_attempts().execution(reference):
        result = show(root, identity, cancelled=cancel.event)
        if result.get('state') != 'complete' or result.get('native_admitted') is not True:
            raise ValueError('only a complete admitted recipe capture can be exported')
        cancel.check()
        from workbench_atlas_recipe_health.completed_scan import export_completed_scan
        return export_completed_scan(attempt, output, check_cancelled=cancel.check)


def main(argv, *, context, output=None, error=None):
    import sys
    output, error = output or sys.stdout, error or sys.stderr
    parser = argparse.ArgumentParser(prog='workbench capture recipes', description='Capture a selected saved checkout and supported prepared server, then audit its recipes.')
    actions = parser.add_subparsers(dest='action', required=True)
    planning = actions.add_parser('plan', help='retain saved source and inspect an explicit runtime/JDK; no game launch')
    planning.add_argument('--workspace', type=Path, required=True)
    for name in ('runtime', 'java-home'):
        planning.add_argument('--' + name, type=Path,
                              help='override the Core user fixture selection for this plan')
    planning.add_argument('--pack-profile', required=True)
    planning.add_argument('--heap-mib', type=int, default=16384)
    fixtures = actions.add_parser('fixtures', help='register or inspect per-user recipe fixture locations')
    fixture_actions = fixtures.add_subparsers(dest='fixture_action', required=True)
    for action in ('set', 'inspect'):
        command = fixture_actions.add_parser(action)
        command.add_argument('--workspace', type=Path, required=True)
        command.add_argument('--pack-profile', required=True)
        command.add_argument('--runtime', type=Path, required=action == 'set')
        command.add_argument('--java-home', type=Path, required=action == 'set')
        command.add_argument('--json', action='store_true')
    for action in ('prepare', 'run', 'show', 'cancel', 'export'):
        command = actions.add_parser(action)
        command.add_argument('attempt')
        if action in ('prepare', 'run'):
            command.add_argument('--confirm', required=True)
        if action == 'run':
            command.add_argument('--accept-eula', action='store_true')
        if action == 'export':
            command.add_argument('--output', type=Path, required=True,
                                 help='new completed-scan archive; existing files are never replaced')
    for command in actions.choices.values():
        command.add_argument('--state-root', type=Path,
                             help='explicit capture store; otherwise Core selects the evidence store')
        command.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        context.check_cancelled()
        if args.action == 'fixtures':
            if args.fixture_action == 'set':
                registered = fixture_selections().register(args.pack_profile, args.workspace,
                                                           args.runtime, args.java_home)
                result = {'state': 'registered-unverified', 'registry_id': registered['id'],
                          'selection': fixture_selections().resolve(args.pack_profile, args.workspace)}
            else:
                result = inspect_fixture_selection(source=args.workspace, profile=args.pack_profile,
                                                   runtime=args.runtime, java_home=args.java_home,
                                                   cancelled=context.cancelled)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), file=output)
            else:
                print('Recipe fixtures: ' + result['state'] + '\n' +
                      ('Registered locations require `workbench capture recipes fixtures inspect`.'
                       if args.fixture_action == 'set' else
                       'Selected Java and runtime artifacts match the profile for planning.'
                       if result['state'] == 'ready-for-planning' else
                       'Needs recovery: ' + result['reason'] + '\n' + '\n'.join(result['recovery'])), file=output)
            return 0
        if args.action == 'plan':
            result = plan(args.state_root, source=args.workspace, runtime=args.runtime, java_home=args.java_home,
                          profile=args.pack_profile, heap_mib=args.heap_mib, cancelled=context.cancelled)
        elif args.action == 'prepare':
            result = prepare(args.state_root, args.attempt, args.confirm, cancelled=context.cancelled)
        elif args.action == 'run':
            result = run(args.state_root, args.attempt, args.confirm, accept_eula=args.accept_eula, cancelled=context.cancelled)
        elif args.action == 'show':
            result = show(args.state_root, args.attempt, cancelled=context.cancelled)
        elif args.action == 'export':
            result = export(args.state_root, args.attempt, args.output, cancelled=context.cancelled)
        else:
            result = cancel(args.state_root, args.attempt)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), file=output)
        else:
            if args.action == 'export':
                print(f"Completed scan exported: {args.output.absolute()}\nObject: {result['manifest_id']}\nOpen it with workbench atlas scans import. No game execution is needed on the receiving machine.", file=output)
                return 0
            print(f"Recipe capture: {result['state']}\nAttempt: {result.get('attempt_id', '')}", file=output)
            if result.get('id'):
                print('Confirmation ID: ' + result['id'], file=output)
            if args.action == 'plan':
                print(f"Saved revision: {result['candidate']['source']['revision']}\nRuntime: {result['runtime']}\nJava: {result['java']['path']} (major {result['java']['major']})\nHeap: {result['heap_mib']} MiB\nPreparation replaces the declared source roots in an isolated copy. Execution requires Minecraft EULA acceptance.\nUse --json to inspect the complete source, runtime and profile bindings.", file=output)
            if result.get('summary'):
                print(f"Recipes audited: {result['summary']['recipe_count']}\nAudit: {result['audit']['path']}", file=output)
        return 0
    except (OSError, ValueError, ProcessError) as exc:
        print('Recipe capture failed: ' + str(exc), file=error)
        return 2
