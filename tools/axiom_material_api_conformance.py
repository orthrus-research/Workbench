#!/usr/bin/env python3
"""Isolated original-name Java API witnesses; not material Groovy qualification."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

import build_axiom_material_api as build
from build_axiom_native_materials import compile_sources, jar_bytes
from axiom_source_conformance import engine_inputs, LOCK
from axiom_native_identity_conformance import ordinary
from axiom_runtime import checked_path, verify_runtime

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / 'modules/axiom/tests/oracles/MaterialApiConformance.java'
PROBE = ROOT / 'modules/axiom/tests/oracles/MaterialApiProbe.java'
COMPILER_PROBE = ROOT / 'modules/axiom/tests/oracles/MaterialApiCompilerViewProbe.java'


def qualify(java, engine, images, libraries, program, gtceu, groovy, report):
    java, engine, images, libraries, program, gtceu, groovy = [ordinary(p).resolve(strict=True)
        for p in (java, engine, images, libraries, program, gtceu, groovy)]
    report = ordinary(report)
    if report.exists(): raise ValueError('API qualification report must be new')
    frozen = {**build.recipe_inputs(), **{p:p.read_bytes() for p in (DRIVER, PROBE, COMPILER_PROBE, Path(__file__))}}
    program_files = {p.name:p.read_bytes() for p in program.iterdir() if p.is_file()}
    if set(program_files) != {'material-api.jar', 'material-api-sources.jar', 'program.json'}:
        raise ValueError('API program inventory differs')
    manifest, engine_manifest, jars = engine_inputs(engine, LOCK.read_bytes())
    policy = json.loads(build.POLICY.read_bytes())
    runtime = verify_runtime(java, compiler=True)
    native = [checked_path(images,row) for row in policy['images']] + [checked_path(libraries,row) for row in policy['libraries']]
    environment = {k:v for k,v in os.environ.items() if k not in {'JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH'}}
    with tempfile.TemporaryDirectory(prefix='axiom-material-api-conformance-') as temporary:
        work = Path(temporary)
        build.build(java, images, libraries, gtceu, groovy, work/'rebuilt')
        if {p.name:p.read_bytes() for p in (work/'rebuilt').iterdir()} != program_files:
            raise ValueError('API program differs from immutable source rebuild')
        compiler_tool = ROOT/'modules/axiom/jvm/src/materialProgramTooling/java/MaterialApiCompilerView.java'
        compiler_witness = compile_sources(java, {COMPILER_PROBE.stem:frozen[COMPILER_PROBE].decode(),
            compiler_tool.stem:frozen[compiler_tool].decode()}, native, work/'compiler-witness')
        jar_bytes(work/'compiler-witness.jar',compiler_witness)
        compiler_result = subprocess.run([str(java/'bin/java'), '-cp',
            os.pathsep.join(map(str,[work/'compiler-witness.jar',*native])),
            'research.orthrus.axiom.tooling.MaterialApiCompilerViewProbe', str(images/'cleanroom-classes.jar'), str(work/'view-witness')],
            cwd=work, env=environment, capture_output=True,text=True, timeout=30)
        if compiler_result.returncode:
            raise ValueError('API compiler-view witness failed: '+compiler_result.stderr[-7000:])
        compiler_observation=json.loads(compiler_result.stdout)
        if compiler_observation != {'deterministicView':True,'onlySignatureChanged':True,'negativeInputs':7}:
            raise ValueError('API compiler-view witness coverage changed')
        driver = compile_sources(java, {DRIVER.stem:frozen[DRIVER].decode()}, jars, work/'driver')
        jar_bytes(work/'driver.jar',driver)
        probe = compile_sources(java, {PROBE.stem:frozen[PROBE].decode()}, [program/'material-api.jar',*native], work/'probe')
        jar_bytes(work/'probe.jar',probe)
        def run(mode, digest=None, rejection=None):
            command = [str(java/'bin/java'), '--enable-native-access=ALL-UNNAMED', '-cp',
                os.pathsep.join(map(str,[work/'driver.jar',*jars])), 'research.orthrus.axiom.MaterialApiConformance',
                'supervisor', str(images), str(libraries), str(program/'material-api.jar'),
                digest or sha256(program_files['material-api.jar']).hexdigest(), str(work/'probe.jar'),
                sha256((work/'probe.jar').read_bytes()).hexdigest(), mode]
            result = subprocess.run(command, cwd=work, env=environment, capture_output=True, text=True, timeout=50)
            if rejection:
                if result.returncode == 0 or rejection not in result.stderr:
                    raise ValueError('API negative input not rejected: '+result.stderr[-5000:])
                return
            if result.returncode:
                raise ValueError('API witness failed:\n'+result.stdout[-3000:]+result.stderr[-9000:])
            value = json.loads(result.stdout)
            if any(value.get(k) is not True for k in ('kernelIsolation','namespaceIsolation')) or value.get('groovyExecutionQualified') is not False:
                raise ValueError('API witness isolation/claim changed')
            return value
        runs = []
        for mode in ('api','catalog','content-open','content-custom-item','content-owner'):
            left, right = run(mode), run(mode)
            if left != right: raise ValueError('API fresh-process replay differs: '+mode)
            runs.append(left)
        run('api', '0'*64, 'API witness input digest differs')
        receipt = {'schema':'axiom.material-api-conformance.v1', 'status':'passed-java-linkage-witnesses',
            'programManifestSha256':sha256(program_files['program.json']).hexdigest(),
            'engineManifestSha256':sha256(manifest).hexdigest(), 'engineJars':engine_manifest['jars'],
            'qualificationInputs':{p.relative_to(ROOT).as_posix():sha256(raw).hexdigest() for p,raw in frozen.items()},
            'images':policy['images'], 'libraries':policy['libraries'], 'runtimeInputs':runtime['runtimeFiles']+runtime['compilerFiles'],
            'runs':runs, 'freshProcessReplays':2, 'negativeInputs':1, 'sourceRebuildIdentical':True,
            'compilerViewWitnesses':compiler_observation,
            'probeSha256':sha256((work/'probe.jar').read_bytes()).hexdigest(),
            'oracleScope':'original-named-source-rebuild-plus-trusted-native-Java-witnesses',
            'groovyExecutionQualified':False, 'independentGroovyOracle':False, 'wholePackParity':False}
    if any(p.read_bytes()!=raw for p,raw in frozen.items()) or engine_inputs(engine, LOCK.read_bytes())[0] != manifest:
        raise ValueError('API qualification inputs changed')
    if {p.name:p.read_bytes() for p in program.iterdir() if p.is_file()} != program_files:
        raise ValueError('API program changed during qualification')
    for root,rows in ((images,policy['images']),(libraries,policy['libraries']),(java,runtime['runtimeFiles']+runtime['compilerFiles'])):
        for row in rows: checked_path(root,row)
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x') as stream: json.dump(receipt,stream,indent=2);stream.write('\n')
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','images','library-root','program','gtceu','groovyscript','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(argv)
    receipt=qualify(args.java_home,args.engine_home,args.images,args.library_root,args.program,args.gtceu,args.groovyscript,args.report)
    print(json.dumps({'status':receipt['status'],'runs':len(receipt['runs']),'groovyExecutionQualified':False}))


if __name__=='__main__': main()
