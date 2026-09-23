#!/usr/bin/env python3
"""Build the bounded native GroovyScript target. Compilation is not qualification."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile

import axiom_groovy_language_sources as source
from axiom_material_program_sources import selected_revisions
from axiom_runtime import checked_path,verify_runtime
from axiom_native_identity_conformance import ordinary,POLICY
from axiom_source_conformance import engine_inputs,LOCK
from build_axiom_native_materials import compile_sources,jar_bytes
from build_axiom_material_api import recipe_inputs as api_recipe_inputs

ROOT=Path(__file__).resolve().parents[1]


def recipe_inputs():
    return {**api_recipe_inputs(),Path(__file__):Path(__file__).read_bytes()}


def build(java,engine,images,libraries,api,groovy,gtceu,output):
    output=ordinary(output)
    if output.exists(): raise ValueError('Groovy language output must be new')
    java,engine,images,libraries,api,groovy,gtceu=[ordinary(p).resolve(strict=True) for p in (java,engine,images,libraries,api,groovy,gtceu)]
    runtime=verify_runtime(java,compiler=True)
    manifest,engine_manifest,jars=engine_inputs(engine,LOCK.read_bytes())
    language=[p for p in jars if p.name=='groovy-4.0.30.jar']
    if len(language)!=1: raise ValueError('Pinned Groovy 4.0.30 required')
    policy_raw=POLICY.read_bytes(); policy=json.loads(policy_raw)
    api_manifest_raw=(api/'program.json').read_bytes(); api_manifest=json.loads(api_manifest_raw)
    for name,digest in api_manifest['artifacts'].items(): checked_path(api,{'path':name,'sha256':digest})
    dependencies=[api/'material-api.jar',*language,*[checked_path(images,r) for r in policy['images']],*[checked_path(libraries,r) for r in policy['libraries']]]
    frozen=recipe_inputs()
    originals=source.read_sources(groovy,gtceu); generated=source.assemble(originals)
    resources=source.read_resources(groovy)
    original_config=json.loads(resources['mixin.groovyscript.json'])
    selected_mixins=[*['groovy.'+name for name in source.MIXINS],'EventBusMixin']
    if not set(selected_mixins).issubset(original_config['mixins']): raise ValueError('Required original mixin selection differs')
    configuration={key:original_config[key] for key in ('package','target','minVersion','compatibilityLevel')}
    configuration.update(required=True,mixins=selected_mixins)
    source_paths={name.replace('.','/'):text for name,text in generated.items()}
    with tempfile.TemporaryDirectory(prefix='axiom-groovy-language-') as temporary:
        work=Path(temporary)
        classes=compile_sources(java,source_paths,[api/'compiler-view.jar',*dependencies],work/'compile')
        packaged_resources={'assets/groovyscript/mappings.srg':resources['assets/groovyscript/mappings.srg'],
            'axiom-native-groovy-language.json':json.dumps(configuration,sort_keys=True).encode()}
        jar_bytes(work/'groovy-language.jar',{**classes,**packaged_resources})
        jar_bytes(work/'groovy-language-sources.jar',{n.replace('.','/')+'.java':t.encode() for n,t in generated.items()})
        result={'schema':'axiom.groovy-language-build.v1','revisions':selected_revisions(),
                'engineManifestSha256':sha256(manifest).hexdigest(),'apiManifestSha256':sha256(api_manifest_raw).hexdigest(),
                'recipeInputs':{str(p.relative_to(ROOT)):sha256(raw).hexdigest() for p,raw in frozen.items()},
                'sourceInputs':{n:sha256(t.encode()).hexdigest() for n,t in originals.items()},
                'resourceInputs':{n:sha256(raw).hexdigest() for n,raw in resources.items()},
                'resources':{n:sha256(raw).hexdigest() for n,raw in packaged_resources.items()},
                'mixinConfiguration':configuration,
                'sources':{n:sha256(t.encode()).hexdigest() for n,t in generated.items()},
                'classes':{n:sha256(raw).hexdigest() for n,raw in classes.items()},
                'artifacts':{n:sha256((work/n).read_bytes()).hexdigest() for n in ('groovy-language.jar','groovy-language-sources.jar')},
                'images':policy['images'],'libraries':policy['libraries'],'runtimeInputs':runtime['runtimeFiles']+runtime['compilerFiles'],
                'requiredGroovyMixins':source.MIXINS,'groovyExecutionQualified':False,'wholePackParity':False}
        if recipe_inputs()!=frozen or source.read_sources(groovy,gtceu)!=originals or source.read_resources(groovy)!=resources:
            raise ValueError('Language source input drift')
        if source.assemble(originals)!=generated or POLICY.read_bytes()!=policy_raw or (api/'program.json').read_bytes()!=api_manifest_raw:
            raise ValueError('Language target input drift')
        for path in dependencies:
            # Authoritative paths are rechecked below against their original manifests.
            if not path.is_file(): raise ValueError('Language dependency disappeared')
        for root,rows in ((images,policy['images']),(libraries,policy['libraries'])):
            for row in rows: checked_path(root,row)
        for name,digest in api_manifest['artifacts'].items(): checked_path(api,{'path':name,'sha256':digest})
        verify_runtime(java,compiler=True)
        if engine_inputs(engine,LOCK.read_bytes())[0]!=manifest: raise ValueError('Language engine input drift')
        output.mkdir(parents=True)
        for name in result['artifacts']:
            with (output/name).open('xb') as out: out.write((work/name).read_bytes())
        with (output/'program.json').open('x') as out: json.dump(result,out,indent=2);out.write('\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','images','library-root','api-program','groovyscript','gtceu','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(argv)
    result=build(args.java_home,args.engine_home,args.images,args.library_root,args.api_program,args.groovyscript,args.gtceu,args.output)
    print(json.dumps({'sources':len(result['sources']),'classes':len(result['classes']),'groovyExecutionQualified':False}))


if __name__=='__main__': main()
