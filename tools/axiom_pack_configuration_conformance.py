#!/usr/bin/env python3
"""Observe original saved-config/conditional-expansion behavior in fresh workers.

This is a bounded regression lane, not an independent whole-pack parity oracle.
It never edits the supplied pack or substitutes native configuration semantics.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import time

from axiom_material_program_cases import cases, Edit, EDITS, identity, source_acknowledgement, matches_source_acknowledgement
from axiom_material_program_sources import selected_revisions
from axiom_runtime import verify_runtime
from axiom_source_conformance import engine_inputs, LOCK
from build_axiom_native_materials import jar_bytes
from build_axiom_target import git

CONFIG = 'config/sussypatches.cfg'
ANCHOR = b'B:"Enable Recipe Info"=true'


def corpus(configuration):
    if configuration.count(ANCHOR) != 1:
        raise ValueError('Expected one exact selected recipeInfo setting')
    program = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)',
                   'Titanate.addFlags(NO_SMELTING)\n        new gregtech.api.fluids.FluidBuilder().temperature(300).basic()').apply(cases()[0]['files'])
    result = []
    for name, replacement in [('enabled', ANCHOR), ('disabled', ANCHOR.replace(b'true', b'false')),
                              ('default', b''), ('invalid', ANCHOR.replace(b'true', b'not_a_boolean')),
                              ('missing', None), ('enabled-again', ANCHOR)]:
        files = dict(program)
        if replacement is not None:
            files[CONFIG] = configuration.replace(ANCHOR, replacement)
        result.append({'name': name, 'files': files})
    for name, setting, expression in [('acidic-enabled', configuration, '.acidic()'),
                                     ('acidic-disabled', configuration.replace(ANCHOR, ANCHOR.replace(b'true', b'false')), '.acidic()'),
                                     ('acidic-native-error', configuration, '.acidic(1)')]:
        files = Edit(EDITS, '.basic()', expression).apply(program)
        files[CONFIG] = setting
        result.append({'name': name, 'files': files})
    return result


def check_initialization(case, execution, *, native_failure=False):
    failures = []
    trace = execution.get('initialization', {})
    rows = trace.get('steps', [])
    steps = {row['id']: row for row in rows}
    if trace.get('schema') != 'axiom.native-initialization-trace.v1' or len(steps) != len(rows):
        return ['initialization trace absent or step identities duplicated']
    required = {'native-context-setup': 'returned', 'sussypatches-configuration': 'returned',
                'sussypatches-native-registration': 'returned',
                'gt-groovy-compatibility': 'returned', 'susy-groovy-compatibility': 'returned',
                'susy-native-subscriber-registration': 'returned', 'native-object-mapper-bindings': 'returned', 'generated-material-content': 'deferred',
                'addon-material-hooks': 'deferred', 'post-init-recipes': 'deferred',
                'fluid-registration-and-prefix-processing': 'deferred'}
    if case['name'] == 'missing':
        required.update({'sussypatches-configuration': 'threw', 'gt-groovy-compatibility': 'not-reached',
                         'sussypatches-native-registration': 'not-reached',
                         'susy-groovy-compatibility': 'not-reached', 'pre-init-scripts': 'not-reached',
                         'susy-native-subscriber-registration': 'not-reached',
                         'native-object-mapper-bindings': 'not-reached',
                         'gt-material-catalog': 'not-reached'})
    elif native_failure or case['name'] in {'disabled', 'acidic-native-error'}:
        required.update({'pre-init-scripts': 'returned', 'gt-material-catalog': 'returned',
                         'material-event': 'returned', 'post-material-event': 'threw',
                         'material-freeze': 'not-reached'})
    elif case['name'] != 'pack':
        required.update({'pre-init-scripts': 'returned', 'gt-material-catalog': 'returned',
                         'material-event': 'returned', 'post-material-event': 'returned',
                         'material-freeze': 'returned'})
    for name, status in required.items():
        if steps.get(name, {}).get('status') != status:
            failures.append(name + ': native initialization visit state differs')
    visited = sorted((row for row in rows if 'sequence' in row), key=lambda row: row['sequence'])
    if [row['sequence'] for row in visited] != list(range(1, len(visited) + 1)):
        failures.append('native initialization visit sequence differs')
    for row in rows:
        if row.get('status') in {'returned', 'threw'} and (type(row.get('elapsedNanos')) is not int or row['elapsedNanos'] < 0):
            failures.append('native initialization timing absent')
        if row.get('status') in {'deferred', 'not-reached'} and ('elapsedNanos' in row or 'sequence' in row):
            failures.append('unvisited initialization has fabricated execution evidence')
    return failures


def check_result(case, response, exit_code, *, native_failure=None, native_logged_error=None):
    failures = []
    def require(condition, message):
        if not condition:
            failures.append(message)
    body = response.get('result', {}); execution = body.get('execution', {})
    failures.extend(check_initialization(case, execution, native_failure=native_failure is not None))
    config = execution.get('packConfiguration')
    require(exit_code == 4 and response.get('status') == body.get('nativeOutcome') == 'incomplete',
            'pending pack context must remain incomplete')
    require(matches_source_acknowledgement(body.get('sourceProgram'), case['files']), 'complete input custody differs')
    require(body.get('wholePackParity') is False, 'whole-pack parity must not be claimed')
    if case['name'] == 'missing':
        require(config is None, 'missing configuration must not be fabricated')
        require(execution.get('stage') == 'pack-configuration-and-groovy-integration', 'missing config did not stop setup')
        require('requires saved config/sussypatches.cfg' in execution.get('nativeException', ''), 'missing config error absent')
        return failures
    require(isinstance(config, dict), 'original configuration observation absent')
    if not isinstance(config, dict):
        return failures
    disabled = case.get('configurationDisabled', False) or case['name'] in {'disabled', 'acidic-disabled'}
    invalid = case['name'] == 'invalid'
    require(config.get('recipeInfo') is (not disabled), 'native configuration field differs')
    require(config.get('recipeInfoProperty') == {'value': 'not_a_boolean' if invalid else 'false' if disabled else 'true',
                                                'isBooleanValue': not invalid}, 'native property observation differs')
    require(config.get('inputSha256') == sha256(case['files'][CONFIG]).hexdigest(), 'native input hash differs')
    require(config.get('wholePackConfigurationQualified') is False, 'one configuration must not qualify the pack')
    require(config.get('callback') == 'supersymmetry.integration.groovyscript.GrSModule#onCompatLoaded', 'original callback absent')
    require(config.get('gtObjectMappers') == ['element', 'material', 'metaitem', 'oreprefix', 'recipemap'], 'full native GT mapper registration absent')
    subscriber = execution.get('susySubscriber', {})
    require(subscriber.get('subscriber') == 'supersymmetry.common.CommonProxy'
            and subscriber.get('registrantOwner') == 'susy' and subscriber.get('activeOwnerRestored') is True
            and subscriber.get('registrationMethod') == 'native-EventBus.register-complete-class'
            and subscriber.get('discoveryOrderQualified') is False, 'original complete Susy subscriber registration absent')
    for method in ('registerMaterials', 'postRegisterMaterials'):
        require(any(row.get('method') == method and row.get('priority') == 'HIGH'
                    for row in subscriber.get('handlers', [])), 'native Susy material handler absent: ' + method)
    require(execution.get('contentProgress', {}).get('phase') == 'NOT_STARTED', 'uncomposed pack content must not execute')
    require(not any(key in execution.get('vocabulary', {}) for key in ('gt-prefix-items', 'gt-material-blocks', 'gt-ore-blocks')),
            'unexecuted generated content has fabricated vocabulary')
    require(not any('FluidBuilder#' + name in row for row in execution.get('candidateAdmissionViolations', [])
                    for name in ('basic', 'acidic')), 'native fluid extension rejected by admission')
    if case['name'] == 'pack':
        require(execution.get('stage') != 'pack-configuration-and-groovy-integration', 'pack integration did not complete')
        require('FluidBuilder.basic()' not in execution.get('nativeException', ''), 'pack basic dispatch still failed')
        require('FluidBuilder.acidic()' not in execution.get('nativeException', ''), 'pack acidic dispatch still failed')
    elif native_failure is not None or case['name'] in {'disabled', 'acidic-native-error'}:
        expected = native_failure or ('groovy.lang.MissingMethodException', '')
        require(all(value in execution.get('nativeException', '') for value in expected), 'expected original native error absent')
        locations = [location for row in execution.get('diagnostics', []) for location in row.get('locations', [])]
        require(any(row.get('path') == EDITS and row.get('line') == 10 for row in locations), 'native saved-source location absent')
    elif native_logged_error is not None:
        require(not execution.get('nativeException'), 'logged error was replaced by a thrown exception')
        require(any(native_logged_error in row for row in execution.get('nativeErrors', [])), 'original logged native error absent')
        require(execution.get('phase') == 'FROZEN', 'native logged error did not retain continuation')
        require(execution.get('candidateAdmissionViolations') == [], 'logged error hit admission instead')
        locations = [location for row in execution.get('diagnostics', []) for location in row.get('locations', [])]
        require(any(row.get('path') == EDITS and row.get('line') == 10 for row in locations), 'native logged-error source location absent')
    else:
        require(not execution.get('nativeException') and execution.get('nativeErrors') == [], 'enabled extension encountered native errors')
        require(execution.get('phase') == 'FROZEN', 'bounded material execution did not reach frozen state')
        require(execution.get('candidateAdmissionViolations') == [], 'bounded operation was rejected')
    return failures


def run(java, engine, runtime, pack, report, whole_pack=False, *, case_selector=corpus,
        result_checker=check_result, receipt_schema='axiom.pack-configuration-observations.v1',
        whole_pack_observations=()):
    if report.exists():
        raise ValueError('Receipt must be new')
    retained = report.absolute().with_name(report.name + '.inputs')
    if retained.exists():
        raise ValueError('Retained case inputs must be new')
    observation_sources = {Path(name).resolve(): Path(name).read_bytes() for name in
                           (__file__, case_selector.__code__.co_filename, result_checker.__code__.co_filename)}
    java, engine, runtime, pack = [p.resolve(strict=True) for p in (java, engine, runtime, pack)]
    jvm = verify_runtime(java)
    engine_raw, _, jars = engine_inputs(engine, LOCK.read_bytes())
    runtime_raw = (runtime / 'runtime.json').read_bytes(); manifest = json.loads(runtime_raw)
    if manifest['context']['id'] != 'supersymmetry:material-authoring-pack':
        raise ValueError('Explicit pack context required')
    request = {key: manifest[key] for key in ('contextPolicySha256', 'admissionPolicySha256')}
    request['context'] = manifest['context']['id']
    revision = selected_revisions()['supersymmetry']
    configuration = git(pack, 'show', revision + ':' + CONFIG)
    selected = case_selector(configuration)
    original = None
    def snapshot():
        return {p.relative_to(pack).as_posix(): p.read_bytes()
                for name in ('groovy', 'config') for p in (pack / name).rglob('*') if p.is_file()}
    if whole_pack:
        if git(pack, 'rev-parse', 'HEAD').decode().strip() != revision or git(pack, 'status', '--porcelain'):
            raise ValueError('Whole-pack witness requires unchanged selected checkout')
        original = snapshot()
        selected.append({'name': 'pack', 'files': original, 'observeMaterials': list(whole_pack_observations)})
    receipt = {'schema': receipt_schema, 'status': 'failed', 'runs': [],
               'packRevision': revision, 'jvm': jvm, 'engineSha256': sha256(engine_raw).hexdigest(),
               'runtimeSha256': sha256(runtime_raw).hexdigest(), 'minecraftLaunched': False,
               'observationSources': {path.name: sha256(raw).hexdigest() for path, raw in observation_sources.items()},
               'wholePackParity': False, 'configurationApplicationQualified': False}
    retained.mkdir(parents=True)
    try:
        for index, case in enumerate(selected):
            case_request = dict(request)
            if 'observeMaterials' in case:
                case_request['observeMaterials'] = case['observeMaterials']
            # Complete inputs belong to the observer receipt, outside the bounded
            # native response. Never try to reconstruct them from a digest ACK.
            archive = retained / f'{index:04d}-program.zip'
            jar_bytes(archive, case['files'])
            archive_raw = archive.read_bytes()
            request_raw = json.dumps(case_request, sort_keys=True, separators=(',', ':')).encode()
            record = {'name': case['name'], 'sourceProgram': identity(case['files']),
                      'programArchive': {'path': archive.relative_to(report.absolute().parent).as_posix(),
                                         'size': len(archive_raw), 'sha256': sha256(archive_raw).hexdigest()},
                      'request': case_request, 'requestSha256': sha256(request_raw).hexdigest()}
            receipt['runs'].append(record)
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix='axiom-config-observation-') as temporary:
                process = subprocess.run([str(java / 'bin/java'), '-Xmx512m', '-XX:ActiveProcessorCount=2',
                                          '-cp', ':'.join(map(str, jars)), 'research.orthrus.axiom.Main',
                                          'material-program', '--runtime-home', str(runtime), '--program', str(archive)],
                                         input=request_raw, cwd=temporary, env={'LANG': 'C.UTF-8'},
                                         capture_output=True, timeout=90)
                response = json.loads(process.stdout)
            failures = result_checker(case, response, process.returncode)
            if archive.read_bytes() != archive_raw:
                failures.append('Retained program archive changed during native execution')
            record.update({'seconds': round(time.monotonic() - started, 3),
                                    'nativeResponseBytes': len(process.stdout),
                                    'exitCode': process.returncode, 'response': response, 'failures': failures,
                           'stderr': process.stderr.decode(errors='replace')})
            print(json.dumps({'case': case['name'], 'failures': failures}), flush=True)
        if original is not None and (original != snapshot() or git(pack, 'status', '--porcelain')):
            raise ValueError('Supplied pack changed during observation')
        if any(path.read_bytes() != raw for path, raw in observation_sources.items()):
            raise ValueError('Observation lane changed during execution')
        if any(row['failures'] for row in receipt['runs']):
            raise ValueError('Native configuration regression failed')
        receipt['status'] = 'passed-bounded-observations'
    finally:
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open('x') as out:
            json.dump(receipt, out, indent=2); out.write('\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack)
