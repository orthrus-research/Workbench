#!/usr/bin/env python3
"""Qualify native Foundation/CleanMix transformation linkage, never candidate execution."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

from axiom_runtime import checked_path,verify_runtime
from axiom_source_conformance import engine_inputs,LOCK
from axiom_native_identity_conformance import ordinary,POLICY
from axiom_material_program_sources import selected_revisions,GR
from build_axiom_target import git
from build_axiom_native_materials import compile_sources,jar_bytes

ROOT=Path(__file__).resolve().parents[1]
DRIVER=ROOT/'modules/axiom/tests/oracles/GroovyTransformConformance.java'
PROBE=ROOT/'modules/axiom/tests/oracles/GroovyTransformProbe.java'
SERVICE=ROOT/'modules/axiom/tests/oracles/ContextMixinService.java'
MIXINS=('core/mixin/groovy/ModuleNodeAccessor','core/mixin/EventBusMixin','event/EventBusExtended')
SERVICES=('org.spongepowered.asm.service.IMixinService','org.spongepowered.asm.service.IMixinServiceBootstrap')


def qualify(java,engine,images,libraries,groovy,cleanroom,report):
    java,engine,images,libraries,groovy,cleanroom=[ordinary(p).resolve(strict=True) for p in (java,engine,images,libraries,groovy,cleanroom)]
    report=ordinary(report)
    if report.exists(): raise ValueError('Groovy transformation receipt must be new')
    runtime=verify_runtime(java,compiler=True); policy=json.loads(POLICY.read_bytes())
    manifest,engine_manifest,jars=engine_inputs(engine,LOCK.read_bytes())
    language=[p for p in jars if p.name=='groovy-4.0.30.jar']
    if len(language)!=1: raise ValueError('Pinned Groovy 4.0.30 required')
    lang=language[0]
    dependencies=[checked_path(images,r) for r in policy['images']]+[checked_path(libraries,r) for r in policy['libraries']]+[lang]
    revisions=selected_revisions()
    originals={name:git(groovy,'show',revisions['groovyscript']+':'+GR+name+'.java') for name in MIXINS}
    services={name:git(cleanroom,'show',revisions['cleanroom']+':src/main/resources/META-INF/services/'+name) for name in SERVICES}
    frozen={p:p.read_bytes() for p in (DRIVER,PROBE,SERVICE,Path(__file__),POLICY,LOCK)}
    environment={k:v for k,v in os.environ.items() if k not in {'JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH'}}
    with tempfile.TemporaryDirectory(prefix='axiom-groovy-transform-') as temporary:
        work=Path(temporary)
        classes=compile_sources(java,{**{name.rsplit('/',1)[1]:raw.decode() for name,raw in originals.items()},
            PROBE.stem:frozen[PROBE].decode(),SERVICE.stem:frozen[SERVICE].decode()},dependencies,work/'native')
        config={'package':'com.cleanroommc.groovyscript.core.mixin','required':True,'minVersion':'0.8',
                'compatibilityLevel':'JAVA_8','target':'@env(DEFAULT)','mixins':['groovy.ModuleNodeAccessor','EventBusMixin']}
        installed_services={**services,'org.spongepowered.asm.service.IMixinService':b'research.orthrus.axiom.materialtest.ContextMixinService'}
        jar_bytes(work/'native.jar',{**classes,**{'META-INF/services/'+n:r for n,r in installed_services.items()},
            'axiom-native-groovy-transform-witness.json':json.dumps(config,sort_keys=True).encode()})
        driver=compile_sources(java,{DRIVER.stem:frozen[DRIVER].decode()},jars,work/'driver')
        jar_bytes(work/'driver.jar',driver)
        def run(mode):
            command=[str(java/'bin/java'),'--enable-native-access=ALL-UNNAMED','-cp',os.pathsep.join(map(str,[work/'driver.jar',*jars])),
                'research.orthrus.axiom.GroovyTransformConformance','supervisor',str(images),str(libraries),str(work/'native.jar'),
                sha256((work/'native.jar').read_bytes()).hexdigest(),str(lang),sha256(lang.read_bytes()).hexdigest(),mode]
            result=subprocess.run(command,cwd=work,env=environment,capture_output=True,text=True,timeout=50)
            if result.returncode: raise ValueError('Native Groovy transform witness failed:\n'+result.stdout[-2000:]+result.stderr[-14000:])
            return json.loads(result.stdout)
        runs=[run(mode) for mode in ('present','missing')]
        receipt={'schema':'axiom.groovy-transform-conformance.v1','status':'passed-bootstrap-witnesses','runs':runs,
                 'sourceInputs':{n:sha256(raw).hexdigest() for n,raw in originals.items()},'revisions':revisions,
                 'services':{n:sha256(raw).hexdigest() for n,raw in services.items()},'mixinConfiguration':config,
                 'serviceBinding':{'class':'research.orthrus.axiom.materialtest.ContextMixinService',
                    'nativeParent':'com.cleanroommc.cleanmix.service.CleanMixService','override':'resolveSourceId:verified-artifact-audit-identities'},
                 'nativeProgramSha256':sha256((work/'native.jar').read_bytes()).hexdigest(),
                 'qualificationInputs':{str(p.relative_to(ROOT)):sha256(raw).hexdigest() for p,raw in frozen.items()},
                 'engineManifestSha256':sha256(manifest).hexdigest(),'engineJars':engine_manifest['jars'],
                 'images':policy['images'],'libraries':policy['libraries'],'runtimeInputs':runtime['runtimeFiles']+runtime['compilerFiles'],
                 'allGroovyMixinsQualified':False,'groovyExecutionQualified':False,'wholePackParity':False}
    if any(p.read_bytes()!=raw for p,raw in frozen.items()): raise ValueError('Groovy transform input drift')
    for root,rows in ((images,policy['images']),(libraries,policy['libraries']),(java,runtime['runtimeFiles']+runtime['compilerFiles'])):
        for row in rows: checked_path(root,row)
    if engine_inputs(engine,LOCK.read_bytes())[0]!=manifest: raise ValueError('Groovy engine input drift')
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x') as out: json.dump(receipt,out,indent=2);out.write('\n')
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','images','library-root','groovyscript','cleanroom','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(argv)
    value=qualify(args.java_home,args.engine_home,args.images,args.library_root,args.groovyscript,args.cleanroom,args.report)
    print(json.dumps({'status':value['status'],'runs':len(value['runs']),'groovyExecutionQualified':False}))


if __name__=='__main__': main()
