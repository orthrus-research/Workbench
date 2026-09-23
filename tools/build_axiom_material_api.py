#!/usr/bin/env python3
"""Build original-name native material API sources; not Groovy execution qualification."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import os
import subprocess

import axiom_material_api_sources as source
from axiom_native_identity_conformance import ordinary
from axiom_runtime import checked_path, verify_runtime
from build_axiom_native_materials import compile_sources, jar_bytes
from axiom_recipe_native_inputs import read_inputs as recipe_native_inputs

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'profiles/platforms/cleanroom/native-identity-runtime.json'


def check_native_overrides(overrides):
    original_owners = ('gregtech/api/GTValues', 'gregtech/common/ConfigHolder',
                       'gregtech/api/items/metaitem/MetaItem', 'gregtech/api/items/metaitem/StandardMetaItem',
                       'gregtech/api/items/metaitem/ElectricStats', 'gregtech/integration/baubles/BaubleBehavior',
                       'gregtech/api/unification/material/properties/BlastProperty')
    for name in overrides:
        if ((name.startswith('gregtech/api/recipes/') and name != 'gregtech/api/recipes/ModHandler.class')
                or any(name == owner + '.class' or name.startswith(owner + '$') for owner in original_owners)):
            raise ValueError('Native catalog/configuration/blast implementation must remain unchanged: ' + name)


def recipe_inputs():
    """Bind the complete local generation/compilation recipe, including helpers."""
    paths = [Path(__file__), ROOT / 'tools/axiom_recipe_native_inputs.py', POLICY, ROOT / 'profiles/platforms/cleanroom/jvm-runtime.json',
             *sorted((ROOT / 'tools').glob('axiom_*sources.py')),
             *(ROOT / 'tools' / name for name in ('axiom_runtime.py', 'axiom_native_identity_conformance.py',
               'axiom_source_conformance.py', 'build_axiom_target.py', 'build_axiom_native_materials.py')),
             ROOT/'tools/axiom_material_access.py',
             ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/NativeMaterialAccess.java',
             *sorted((ROOT / 'modules/axiom/sources').glob('*.lock.json')),
             *sorted((ROOT / 'modules/axiom/jvm/src/materialProgram').rglob('*.java')),
             *sorted((ROOT / 'modules/axiom/jvm/src/materialProgramTooling').rglob('*.java'))]
    return {p: p.read_bytes() for p in paths}


def build(java, images, libraries, gtceu, groovyscript, output, pack=None, mods=None):
    output = ordinary(output)
    if output.exists():
        raise ValueError('API build output must be new')
    if pack is None or mods is None:
        raise ValueError('Selected pack source and explicit pinned mod artifacts required')
    native_classes, native_inputs = recipe_native_inputs(ordinary(pack).resolve(strict=True), ordinary(mods).resolve(strict=True))
    java, images, libraries, gtceu, groovyscript = [ordinary(p).resolve(strict=True) for p in (java,images,libraries,gtceu,groovyscript)]
    frozen = recipe_inputs()
    runtime = verify_runtime(java, compiler=True)
    policy_raw = POLICY.read_bytes(); policy = json.loads(policy_raw)
    inputs = [(images, row) for row in policy['images']] + [(libraries,row) for row in policy['libraries']]
    dependencies = [checked_path(root,row) for root,row in inputs]
    original, mappings = source.read_sources(gtceu, groovyscript)
    generated = source.assemble(original,mappings)
    names = {name.rsplit('.',1)[1]: text for name,text in generated.items()}
    if len(names) != len(generated):
        raise ValueError('API source basenames collide')
    with tempfile.TemporaryDirectory(prefix='axiom-material-api-') as temporary:
        work = Path(temporary)
        jar_bytes(work / 'native-recipes.jar', native_classes)
        dependencies.append(work / 'native-recipes.jar')
        view_source = ROOT / 'modules/axiom/jvm/src/materialProgramTooling/java/MaterialApiCompilerView.java'
        view_raw = view_source.read_bytes()
        view_classes = compile_sources(java, {'MaterialApiCompilerView':view_raw.decode()}, dependencies, work / 'compiler-tool')
        jar_bytes(work / 'compiler-tool.jar',view_classes)
        environment = {k:v for k,v in os.environ.items() if k not in {'JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH'}}
        subprocess.run([str(java/'bin/java'), '-cp', os.pathsep.join(map(str,[work/'compiler-tool.jar',*dependencies])),
                        'research.orthrus.axiom.tooling.MaterialApiCompilerView',str(images/'cleanroom-classes.jar'),str(work/'native-recipes.jar'),str(work/'compiler-view.jar')],
                       env=environment, check=True, capture_output=True, timeout=30)
        classes = compile_sources(java,names,[work/'compiler-view.jar',*dependencies],work / 'compile')
        # Source-bounded owners deliberately override the original binary owners.
        # Record every overlap; do not silently change RecipeMap or its builders.
        overrides = sorted(set(classes) & set(native_classes))
        check_native_overrides(overrides)
        jar_bytes(work / 'material-api.jar',{**native_classes, **classes})
        jar_bytes(work / 'material-api-sources.jar', {name.replace('.','/') + '.java': text.encode() for name,text in generated.items()})
        report = {'schema':'axiom.material-api-build.v1', 'revisions':source.selected_revisions(),
                  'nativeRecipeInputs': native_inputs, 'nativeRecipeSourceOverrides': overrides,
                  'recipeInputs':{p.relative_to(ROOT).as_posix():sha256(raw).hexdigest() for p,raw in frozen.items()},
                  'sourceInputs':{n:sha256(raw.encode()).hexdigest() for n,raw in original.items()},
                  'mappingsSha256':sha256(mappings.encode()).hexdigest(),
                  'compilerView':{'sourceSha256':sha256(view_raw).hexdigest(), 'jarSha256':sha256((work/'compiler-view.jar').read_bytes()).hexdigest(),
                                  'scope':'compiler-only-generic-arity-and-optional-interface-metadata', 'runtimeImageChanged':False},
                  'policySha256':sha256(policy_raw).hexdigest(),
                  'images':policy['images'], 'libraries':policy['libraries'],
                  'runtimeInputs':runtime['runtimeFiles'] + runtime['compilerFiles'],
                  'sources':{n:sha256(t.encode()).hexdigest() for n,t in generated.items()},
                  'classes':{n:sha256(raw).hexdigest() for n,raw in classes.items()},
                  'artifacts':{n:sha256((work / n).read_bytes()).hexdigest() for n in ('material-api.jar','material-api-sources.jar','compiler-view.jar')},
                  'groovyExecutionQualified':False, 'wholePackParity':False}
        if source.read_sources(gtceu,groovyscript) != (original,mappings) or source.assemble(original,mappings) != generated or recipe_inputs()!=frozen:
            raise ValueError('API source inputs changed during build')
        if recipe_native_inputs(pack, mods) != (native_classes, native_inputs):
            raise ValueError('Native recipe inputs changed during build')
        for root,row in inputs: checked_path(root,row)
        verify_runtime(java,compiler=True)
        output.mkdir(parents=True)
        for name in report['artifacts']:
            with (output / name).open('xb') as stream: stream.write((work / name).read_bytes())
        with (output / 'program.json').open('x') as stream: json.dump(report,stream,indent=2);stream.write('\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','images','library-root','gtceu','groovyscript','output','supersymmetry','pack-mods'):
        parser.add_argument('--'+name, type=Path,required=True)
    args=parser.parse_args(argv)
    report=build(args.java_home,args.images,args.library_root,args.gtceu,args.groovyscript,args.output,args.supersymmetry,args.pack_mods)
    print(json.dumps({'sources':len(report['sources']),'classes':len(report['classes']),'output':str(args.output),'groovyExecutionQualified':False}))


if __name__=='__main__': main()
