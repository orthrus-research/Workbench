#!/usr/bin/env python3
"""Witness incomplete composition using complete pinned pack files, never excerpts."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

import axiom_material_program_sources as source
from axiom_material_program_cases import cases, identity, FIXTURE, source_acknowledgement, matches_source_acknowledgement
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK
from build_axiom_native_materials import jar_bytes

ROOT = Path(__file__).resolve().parents[1]
PACK_FILES = {
    'full-register-meta-items': 'groovy/preInit/RegisterMetaItems.groovy',
    'full-ore-materials': 'groovy/material/OreMaterials.groovy',
}


def pack_programs(pack, *, lock=None):
    lock = json.loads(source.LOCK.read_bytes()) if lock is None else lock
    revisions = source.selected_revisions()
    if lock.get('schema') != 'axiom.material-program-source-lock.v1' or lock.get('revisions') != revisions:
        raise ValueError('Pack witness source authority differs')
    base = cases()[0]['files']
    result = []
    for name, path in PACK_FILES.items():
        references = [row for row in lock['references'] if row['repository'] == 'supersymmetry' and row['path'] == path]
        if len(references) != 1 or path in base:
            raise ValueError('Pack witness needs one locked whole file without replacing the base program')
        raw = source.git(pack, 'show', revisions['supersymmetry'] + ':' + path)
        reference = references[0]
        if source.source_identity(raw) != {key: reference[key] for key in ('size', 'sha256', 'gitBlob')}:
            raise ValueError('Whole pack file differs from selected source: ' + path)
        files = {**base, path: raw}
        result.append({'name': name, 'files': files, 'program': identity(files),
                       'source': {**reference, 'revision': revisions['supersymmetry']},
                       'baseProgram': identity(base)})
    return result


def check_result(case, response, exit_code):
    """These are expected-context-gap witnesses, not evidence that pack source is invalid."""
    errors = []; body = response.get('result', {}); execution = body.get('execution', {})
    if response.get('status') != 'incomplete' or exit_code != 4 or body.get('nativeOutcome') != 'incomplete':
        errors.append('Missing surrounding composition was not reported as incomplete')
    if body.get('groovyExecutionQualified') is not False or body.get('wholePackParity') is not False:
        errors.append('Unqualified whole-file execution was promoted')
    if not matches_source_acknowledgement(body.get('sourceProgram'), case['files']):
        errors.append('Complete saved source inventory was not retained unchanged')
    if not body.get('sourceAdmission', {}).get('structurallyAdmitted') or not execution.get('nativeCompilationFailure'):
        errors.append('Expected native missing-composition compiler boundary was not reached')
    compiler_findings = [finding for diagnostic in execution.get('diagnostics', [])
                         for finding in diagnostic.get('compilerFindings', [])]
    if not any(finding.get('location', {}).get('path') == case['source']['path'] for finding in compiler_findings):
        errors.append('Native compiler evidence did not identify the original complete file')
    if execution.get('cleanObservation') is not False:
        errors.append('Missing composition was reported as a clean observation')
    if (execution.get('executionCompleted') is not True or execution.get('registeredMaterials') != 605
            or execution.get('phase') != 'FROZEN' or execution.get('contentProgress', {}).get('phase') != 'COMPLETE'):
        errors.append('Native continuation of the unchanged admitted base program was lost')
    return errors


def qualify(java, engine, runtime, pack, report):
    java, engine, runtime, pack = [p.resolve(strict=True) for p in (java, engine, runtime, pack)]
    if report.exists(): raise ValueError('Pack boundary receipt must be new')
    jvm = verify_runtime(java)
    engine_raw, _, jars = engine_inputs(engine, LOCK.read_bytes())
    runtime_raw = (runtime / 'runtime.json').read_bytes(); manifest = json.loads(runtime_raw)
    for row in manifest['files']: checked_path(runtime, row)
    paths = [Path(__file__), Path(source.__file__), source.LOCK, source.TARGET, source.PLATFORM,
             ROOT / 'tools/axiom_material_program_cases.py', *sorted(p for p in FIXTURE.rglob('*') if p.is_file())]
    frozen = {p: p.read_bytes() for p in paths}
    corpus = pack_programs(pack)
    request = {key: manifest[key] for key in ('contextPolicySha256', 'admissionPolicySha256')}
    request.update(context=manifest['context']['id'], observeMaterials=['supersymmetry:developer_aluminosilicate'])
    runs = []; errors = []
    with tempfile.TemporaryDirectory(prefix='axiom-pack-material-boundary-') as temporary:
        work = Path(temporary)
        for case in corpus:
            archive = work / (case['name'] + '.zip'); jar_bytes(archive, case['files'])
            command = [str(java / 'bin/java'), '-Xmx256m', '-XX:ActiveProcessorCount=2', '-cp', os.pathsep.join(map(str, jars)),
                       'research.orthrus.axiom.Main', 'material-program', '--runtime-home', str(runtime), '--program', str(archive)]
            process = subprocess.run(command, input=json.dumps(request).encode(), cwd=work, env={'LANG': 'C.UTF-8'},
                                     capture_output=True, timeout=60)
            try: response = json.loads(process.stdout)
            except ValueError: response = {'invalidResponse': process.stdout.decode(errors='replace')}
            differences = check_result(case, response, process.returncode)
            errors.extend(case['name'] + ': ' + reason for reason in differences)
            runs.append({key: case[key] for key in ('name', 'source', 'program', 'baseProgram')} | {
                'sourceArchiveSha256': sha256(archive.read_bytes()).hexdigest(), 'request': request,
                'exitCode': process.returncode, 'response': response, 'stderr': process.stderr.decode(errors='replace'),
                'errors': differences, 'claim': 'unchanged-whole-file-added-to-declared-program-not-complete-pack'})
    if any(p.read_bytes() != raw for p, raw in frozen.items()) or pack_programs(pack) != corpus:
        raise ValueError('Pack boundary sources changed during qualification')
    if engine_inputs(engine, LOCK.read_bytes())[0] != engine_raw or (runtime / 'runtime.json').read_bytes() != runtime_raw:
        raise ValueError('Pack boundary installed inputs changed')
    for row in manifest['files']: checked_path(runtime, row)
    result = {'schema': 'axiom.material-pack-boundary.v1', 'status': 'failed' if errors else 'passed-incomplete-composition-witnesses',
              'runs': runs, 'errors': errors, 'engineManifestSha256': sha256(engine_raw).hexdigest(),
              'runtimeManifestSha256': sha256(runtime_raw).hexdigest(), 'jvm': jvm,
              'qualificationInputs': {str(p.relative_to(ROOT)): sha256(raw).hexdigest() for p, raw in frozen.items()},
              'groovyExecutionQualified': False, 'wholePackParity': False, 'minecraftLaunched': False}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'supersymmetry', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = qualify(args.java_home, args.engine_home, args.runtime_home, args.supersymmetry, args.report)
    print(json.dumps({'status': result['status'], 'runs': len(result['runs']), 'errors': result['errors']}))
    return 1 if result['errors'] else 0


if __name__ == '__main__': raise SystemExit(main())
