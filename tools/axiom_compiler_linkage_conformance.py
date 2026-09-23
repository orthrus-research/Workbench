#!/usr/bin/env python3
"""Remove individual native compiler dependencies; require refusal before compilation."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from axiom_groovy_language_sources import MIXINS
from axiom_material_program_cases import cases
from axiom_material_program_sources import selected_revisions, GR
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK
from build_axiom_native_materials import compile_sources, jar_bytes
from build_axiom_target import git

ROOT=Path(__file__).resolve().parents[1]
CORE=('InvokerHelperVisitor','CachedClassFieldsVisitor','CachedClassConstructorsVisitor','CachedClassMethodsVisitor','StaticVerifierVisitor')
TRANSFORMER=GR+'core/GroovyScriptTransformer.java'


def mixin_target(text):
    named=re.findall(r'@Mixin\(targets = "([^"]+)", remap = false\)',text)
    if len(named)==1:return named[0].replace('/','.')
    simple=re.findall(r'@Mixin\(value = (\w+)\.class, remap = false\)',text)
    imports=re.findall(r'^import ([\w.]+);$',text,re.M)
    matches=[name for name in imports if len(simple)==1 and name.rsplit('.',1)[-1]==simple[0]]
    # The selected MetaClassImplMixin alone imports its target through this
    # wildcard. Keep that pinned exception explicit, never guess a package.
    if not matches and simple==['MetaClassImpl'] and '\nimport groovy.lang.*;\n' in text:
        matches=['groovy.lang.MetaClassImpl']
    if len(matches)!=1:raise ValueError('Selected original mixin needs one exact target')
    return matches[0]


def omit_core_dispatch(text,visitor):
    marker='case '+visitor+'.CLASS_NAME:'
    if visitor not in CORE or text.count(marker)!=1:raise ValueError('Selected core dispatch anchor differs')
    return text.replace(marker,'case "axiom.omitted.'+visitor+'":',1)


def check_response(response,exit_code,omission=None):
    body=response.get('result',{});bootstrap=body.get('bootstrap',{});linkage=bootstrap.get('compilerLinkage',{})
    errors=[]
    if response.get('status')!='incomplete' or exit_code!=4 or body.get('groovyExecutionQualified') is not False:
        errors.append('Outcome must remain incomplete and unqualified')
    if omission is None:
        if linkage.get('status')!='observed' or linkage.get('missing')!=[] or not bootstrap.get('admitted'):
            errors.append('Complete selected compiler linkage did not pass')
        if not body.get('execution',{}).get('cleanObservation') or body.get('nativeOutcome')!='completed-without-observed-error':
            errors.append('Unchanged complete program lost native execution')
    else:
        if (linkage.get('status')!='incomplete' or bootstrap.get('admitted') is not False
                or bootstrap.get('compilerInvoked') is not False or body.get('candidateCompilationStarted') is not False
                or body.get('nativeOutcome')!='not-run' or 'execution' in body):
            errors.append('Required compiler omission did not refuse before native compilation')
        if not any(row.get('kind')==omission['kind'] and row.get('target')==omission['target']
                   and (omission['kind']!='mixin' or row.get('required')==omission['required']) for row in linkage.get('missing',[])):
            errors.append('Refusal did not identify the removed native dependency')
    return errors


def qualify(java,engine,runtime,groovyscript,report,baseline_only=False):
    java,engine,runtime,groovyscript=[p.resolve(strict=True) for p in (java,engine,runtime,groovyscript)]
    if report.exists():raise ValueError('Compiler linkage receipt must be new')
    jvm=verify_runtime(java,compiler=True)
    engine_raw,_,jars=engine_inputs(engine,LOCK.read_bytes())
    runtime_raw=(runtime/'runtime.json').read_bytes();manifest=json.loads(runtime_raw)
    for row in manifest['files']:checked_path(runtime,row)
    language_name,=[p for p in manifest['classpath'] if p.endswith('-groovy-language.jar')]
    language=runtime/language_name
    with zipfile.ZipFile(language) as archive:language_files={name:archive.read(name) for name in archive.namelist()}
    config=json.loads(language_files['axiom-native-groovy-language.json'])
    expected=[*['groovy.'+name for name in MIXINS],'EventBusMixin']
    if config['mixins']!=expected:raise ValueError('Complete selected mixin configuration differs')
    revision=selected_revisions()['groovyscript'];originals={}
    omissions=[]
    for mixin in expected:
        path=GR+'core/mixin/'+mixin.replace('.','/')+'.java'
        raw=git(groovyscript,'show',revision+':'+path);originals[path]=raw
        omissions.append({'name':'without-'+mixin.rsplit('.',1)[-1],'kind':'mixin',
                          'target':mixin_target(raw.decode()),'required':config['package']+'.'+mixin,'mixin':mixin})
    originals[TRANSFORMER]=git(groovyscript,'show',revision+':'+TRANSFORMER)
    for visitor in CORE:
        path=GR+'core/visitors/'+visitor+'.java';raw=git(groovyscript,'show',revision+':'+path);originals[path]=raw
        target,=re.findall(r'CLASS_NAME = "([^"]+)";',raw.decode())
        omissions.append({'name':'without-'+visitor,'kind':'core-hook','target':target,'visitor':visitor})
    paths=[Path(__file__),ROOT/'tools/axiom_groovy_language_sources.py',ROOT/'tools/axiom_material_program_cases.py',
           *sorted((ROOT/'modules/axiom/jvm/src/materialRuntime/java').rglob('*.java')),
           ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialProgram.java',
           ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialProgramAssessment.java']
    frozen={p:p.read_bytes() for p in paths}
    base=cases()[0];request={key:manifest[key] for key in ('contextPolicySha256','admissionPolicySha256')}
    request.update(context=manifest['context']['id'],observeMaterials=['supersymmetry:developer_aluminosilicate'])
    runs=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='axiom-compiler-linkage-') as temporary:
        work=Path(temporary);program=work/'program.zip';jar_bytes(program,base['files'])
        dependencies=[runtime/p for p in manifest['classpath']]
        for omission in [None,*([] if baseline_only else omissions)]:
            selected=runtime;mutation=None
            if omission is not None:
                selected=work/omission['name'];selected.mkdir()
                # Fault fixtures are independent copies: /tmp may be on a
                # different filesystem, and original artifacts stay untouched.
                for entry in manifest['files']:
                    if entry['path']==language_name:continue
                    target=selected/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(runtime/entry['path'],target)
                files=dict(language_files)
                if omission['kind']=='mixin':
                    edited={**config,'mixins':[name for name in expected if name!=omission['mixin']]}
                    files['axiom-native-groovy-language.json']=json.dumps(edited,sort_keys=True).encode()
                    mutation={'entry':'axiom-native-groovy-language.json','removed':omission['required']}
                else:
                    text=omit_core_dispatch(originals[TRANSFORMER].decode(),omission['visitor'])
                    compiled=compile_sources(java,{'GroovyScriptTransformer':text},dependencies,work/(omission['name']+'-compile'))
                    if not all(p.startswith('com/cleanroommc/groovyscript/core/GroovyScriptTransformer') for p in compiled):
                        raise ValueError('Compiler omission altered another native class')
                    files.update(compiled)
                    mutation={'source':TRANSFORMER,'sourceSha256':sha256(text.encode()).hexdigest(),'dispatchRemoved':omission['visitor']}
                target=selected/language_name;target.parent.mkdir(parents=True,exist_ok=True);jar_bytes(target,files)
                modified=json.loads(runtime_raw)
                for entry in modified['files']:
                    if entry['path']==language_name:entry.update(sha256=sha256(target.read_bytes()).hexdigest(),size=target.stat().st_size)
                (selected/'runtime.json').write_text(json.dumps(modified,indent=2)+'\n')
                mutation['jarSha256']=sha256(target.read_bytes()).hexdigest()
                mutation['scope']='deliberate-inventoried-runtime-fault-not-source-qualified-package'
            command=[str(java/'bin/java'),'-Xmx256m','-XX:ActiveProcessorCount=2','-cp',os.pathsep.join(map(str,jars)),
                     'research.orthrus.axiom.Main','material-program','--runtime-home',str(selected),'--program',str(program)]
            process=subprocess.run(command,input=json.dumps(request).encode(),cwd=work,env={'LANG':'C.UTF-8'},capture_output=True,timeout=60)
            try:response=json.loads(process.stdout)
            except ValueError:response={'invalidResponse':process.stdout.decode(errors='replace')}
            differences=check_response(response,process.returncode,omission)
            name=omission['name'] if omission else 'complete-compiler'
            errors.extend(name+': '+reason for reason in differences)
            runs.append({'name':name,'omission':omission,'mutation':mutation,'response':response,'exitCode':process.returncode,
                         'stderr':process.stderr.decode(errors='replace'),'errors':differences})
            print(json.dumps({'case':name,'errors':differences}),flush=True)
            if omission is None and differences:break
    for row in manifest['files']:checked_path(runtime,row)
    if any(p.read_bytes()!=raw for p,raw in frozen.items()) or (runtime/'runtime.json').read_bytes()!=runtime_raw:
        raise ValueError('Compiler linkage source/runtime drift')
    if engine_inputs(engine,LOCK.read_bytes())[0]!=engine_raw or cases()[0]!=base:raise ValueError('Compiler linkage engine/program drift')
    result={'schema':'axiom.compiler-linkage-conformance.v1','status':'failed' if errors else 'passed-linkage-witnesses',
            'scope':'baseline-only' if baseline_only else 'ten-mixin-five-core-hook-omissions',
            'runs':runs,'errors':errors,'sourceProgram':base['candidateIdentity'],'request':request,
            'engineManifestSha256':sha256(engine_raw).hexdigest(),'runtimeManifestSha256':sha256(runtime_raw).hexdigest(),
            'sourceRevision':revision,'sourceInputs':{path:sha256(raw).hexdigest() for path,raw in originals.items()},
            'qualificationInputs':{str(p.relative_to(ROOT)):sha256(raw).hexdigest() for p,raw in frozen.items()},'jvm':jvm,
            'nativeBehaviorQualified':False,'groovyExecutionQualified':False,'wholePackParity':False,'minecraftLaunched':False}
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','runtime-home','groovyscript','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--baseline-only',action='store_true')
    args=parser.parse_args(argv)
    result=qualify(args.java_home,args.engine_home,args.runtime_home,args.groovyscript,args.report,args.baseline_only)
    print(json.dumps({'status':result['status'],'runs':len(result['runs']),'errors':result['errors']}))
    return 1 if result['errors'] else 0


if __name__=='__main__':raise SystemExit(main())
