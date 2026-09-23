#!/usr/bin/env python3
"""Assemble an explicit local native material runtime; not a redistributable modpack."""
import argparse
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK
from axiom_native_identity_conformance import ordinary, POLICY
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git
from build_axiom_native_materials import compile_sources, jar_bytes
from axiom_recipe_native_inputs import pack_material_mixins, pack_recipe_mixins, pack_gcym_mixins

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'modules/axiom/jvm/src/materialRuntime/java'
GATES = ROOT / 'modules/axiom/jvm/src/main/java/research/orthrus/axiom/materialhost'
PROFILE = ROOT / 'profiles/packs/supersymmetry/src/workbench_profile_supersymmetry'
CONTEXT = 'supersymmetry:material-authoring-gt-base'


def host_sources():
    paths = [*sorted(NATIVE.rglob('*.java')),
             *[GATES / (name + '.java') for name in ('MaterialCallGate', 'MaterialBytecodeGate', 'MaterialRecordBytecode', 'MaterialTraitClasses', 'MaterialTraitLoaderHook', 'MaterialAdmissionPolicy', 'MaterialMapperBindings','MaterialPropertyBindings','MaterialScriptBindings','RecipeStateCatalog', 'NativeRecipeFunctionObservations', 'NativeContainedArtifacts', 'MaterialDiagnosticCauses', 'MaterialDiagnosticGroups', 'NativeDiagnosticOrigins', 'NativeInitializationTrace', 'NativeCoremodPrefix', 'NativeEarlyLaunchPrefix', 'NativeLoaderPrefix', 'NativeServerOwnerPrefix', 'NativeGroovyInitializationPrefix', 'NativeFoundationBootstrap')]]
    return {path: path.read_bytes() for path in paths}


import sys

_assembly_root = Path(__file__).resolve().parents[1]
for _assembly_source in (_assembly_root / 'api/src', _assembly_root / 'modules/axiom/src'):
    if str(_assembly_source) not in sys.path:
        sys.path.insert(0, str(_assembly_source))
from workbench_axiom.native_assembly import verify_method_mappings


def build(java, engine, images, libraries, api, language, cleanroom, output, context_id=CONTEXT, addon_inventory=None):
    if context_id == 'supersymmetry:material-authoring-pack':
        raise ValueError('The pack context requires original raw native inputs through build_raw')
    java, engine, images, libraries, api, language, cleanroom = [ordinary(p).resolve(strict=True)
        for p in (java, engine, images, libraries, api, language, cleanroom)]
    output = ordinary(output)
    if output.exists():
        raise ValueError('Material runtime output must be new')
    runtime = verify_runtime(java, compiler=True)
    engine_raw, _, jars = engine_inputs(engine, LOCK.read_bytes())
    groovy, = [path for path in jars if path.name == 'groovy-4.0.30.jar']
    policy_raw = POLICY.read_bytes(); policy = json.loads(policy_raw)
    api_raw = (api / 'program.json').read_bytes(); api_manifest = json.loads(api_raw)
    language_raw = (language / 'program.json').read_bytes(); language_manifest = json.loads(language_raw)
    if language_manifest['apiManifestSha256'] != sha256(api_raw).hexdigest():
        raise ValueError('Native language and material API identities differ')
    for directory, manifest in ((api, api_manifest), (language, language_manifest)):
        for name, digest in manifest['artifacts'].items():
            checked_path(directory, {'path': name, 'sha256': digest})
        if manifest['revisions'] != selected_revisions():
            raise ValueError('Selected native revisions differ')
    frozen = host_sources()
    context_raw = (PROFILE / 'axiom-material-contexts.json').read_bytes()
    admission_template = (PROFILE / 'axiom-material-admission.json').read_bytes()
    profile_source = (PROFILE / 'axiom.py').read_bytes()
    spec = importlib.util.spec_from_file_location('axiom_runtime_profile_policy', PROFILE / 'axiom.py')
    profile = importlib.util.module_from_spec(spec); spec.loader.exec_module(profile)
    admission_raw = profile.bind_material_admission(admission_template, context_id)
    context, = [row for row in json.loads(context_raw)['contexts'] if row['id'] == context_id]
    mixin_resources, mixin_selection, recipe_mixin_selection, gcym_mixin_selection = {}, None, None, None
    if context_id == 'supersymmetry:material-authoring-pack':
        with zipfile.ZipFile(api / 'material-api.jar') as archive:
            mixin_resources['axiom-native-pack-materials.json'], mixin_selection = pack_material_mixins(archive.read)
            mixin_resources['axiom-native-pack-recipes.json'], recipe_mixin_selection = pack_recipe_mixins(archive.read)
            mixin_resources['axiom-native-pack-gcym.json'], gcym_mixin_selection = pack_gcym_mixins(archive.read)
    with zipfile.ZipFile(language / 'groovy-language.jar') as archive:
        method_mappings=verify_method_mappings(json.loads(admission_raw),archive.read('assets/groovyscript/mappings.srg'))
    dependencies = [checked_path(images, row) for row in policy['images']]
    dependencies += [checked_path(libraries, row) for row in policy['libraries']]
    dependencies += [language / 'groovy-language.jar', api / 'material-api.jar', groovy]
    addon_raw = None
    addon_candidate_files = {}
    if addon_inventory is not None:
        if context_id != 'supersymmetry:material-authoring-pack':
            raise ValueError('Addon inventory requires the pack material context')
        addon_inventory = ordinary(addon_inventory).resolve(strict=True)
        addon_raw = (addon_inventory / 'program.json').read_bytes()
        addon_manifest = json.loads(addon_raw)
        if (addon_manifest.get('schema') != 'axiom.native-addon-inventory-build.v1'
                or addon_manifest.get('packRevision') != selected_revisions()['supersymmetry']
                or addon_manifest.get('side') != context['side']
                or addon_manifest.get('policySha256') != sha256(policy_raw).hexdigest()
                or addon_manifest.get('images') != policy['images'] or addon_manifest.get('libraries') != policy['libraries']
                or addon_manifest.get('runtimeInputs') != runtime['runtimeFiles'] + runtime['compilerFiles']):
            raise ValueError('Addon inventory differs from selected native context')
        for path, digest in addon_manifest['recipeInputs'].items():
            checked_path(ROOT, {'path': path, 'sha256': digest})
        addon_jar = checked_path(addon_inventory, {'path': 'addon-inventory.jar', 'sha256': addon_manifest['artifacts']['addon-inventory.jar']})
        with zipfile.ZipFile(addon_jar) as archive:
            inventory = json.loads(archive.read('axiom-addon-inventory.json'))
        if (inventory.get('schema') != 'axiom.native-addon-inventory.v6'
                or inventory.get('candidateLayout') != 'original-flat-profile-mod-filenames'
                or inventory.get('candidateResourceScope') != 'original-entrypoints-metadata-manifests-native-server-language-and-api-packages'
                or inventory.get('candidateInputScope') not in ('entrypoints-and-metadata', 'complete-artifacts')
                or inventory['candidateInputScope'] != addon_manifest.get('candidateInputScope')):
            raise ValueError('Native candidate input schema/scope differs')
        for row in inventory['artifacts']:
            input_row = row['candidateInput']
            name = 'native-addon-home/' + row['outputPath']
            if input_row.get('path') != name:
                raise ValueError('Native candidate filename differs from selected descriptor')
            if addon_manifest['artifacts'].get(name) != input_row['sha256']:
                raise ValueError('Native candidate artifact identity differs')
            if inventory['candidateInputScope'] == 'complete-artifacts' and input_row != {'path': name, 'sha256': row['sha256'], 'size': row['size']}:
                raise ValueError('Native reference is not the original complete artifact')
            addon_candidate_files[name] = checked_path(addon_inventory, {'path': name, **input_row})
        provided = {row['descriptor']: row['sha256'] for row in inventory['artifacts']}
        for row in api_manifest['nativeRecipeInputs']['artifacts'].values():
            if provided.get(row['descriptor']) != row['sha256']:
                raise ValueError('Addon inventory and executable API artifact differ: ' + row['descriptor'])
        dependencies.append(addon_jar)
    dependency_hashes = {p: sha256(p.read_bytes()).hexdigest() for p in dependencies}
    dependency_hashes.update({p: sha256(p.read_bytes()).hexdigest() for p in addon_candidate_files.values()})
    dependency_hashes[api / 'compiler-view.jar'] = sha256((api / 'compiler-view.jar').read_bytes()).hexdigest()
    services = {'org.spongepowered.asm.service.IMixinService': b'research.orthrus.axiom.materialhost.ContextMixinService',
                'org.spongepowered.asm.service.IMixinServiceBootstrap': git(cleanroom, 'show',
                    selected_revisions()['cleanroom'] + ':src/main/resources/META-INF/services/org.spongepowered.asm.service.IMixinServiceBootstrap')}
    recipe = {p: p.read_bytes() for p in (Path(__file__), ROOT / 'tools/build_axiom_native_materials.py',
                                         ROOT / 'tools/axiom_recipe_native_inputs.py')}
    recipe[PROFILE / 'axiom.py'] = profile_source
    with tempfile.TemporaryDirectory(prefix='axiom-material-runtime-') as temporary:
        work = Path(temporary)
        classes = compile_sources(java, {p.stem: raw.decode() for p, raw in frozen.items()}, [api / 'compiler-view.jar', *dependencies], work / 'compile')
        staged = work / 'runtime'; (staged / 'lib').mkdir(parents=True)
        jar_bytes(staged / 'lib/material-runtime.jar', {**classes,
            **mixin_resources,
            **{'META-INF/services/' + name: raw for name, raw in services.items()}})
        classpath = ['lib/material-runtime.jar']
        for index, path in enumerate(dependencies):
            name = 'lib/' + str(index).zfill(3) + '-' + path.name
            shutil.copyfile(path, staged / name); classpath.append(name)
        # Verified read-only discovery data, never executable classpath entries.
        for name, path in addon_candidate_files.items():
            (staged/name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, staged/name)
        resources = {'context-policy.json': context_raw, 'admission-policy.json': admission_raw,
                     'native-input-policy.json': policy_raw, 'api-build.json': api_raw, 'language-build.json': language_raw}
        if addon_raw is not None:
            resources['addon-build.json'] = addon_raw
        jar_bytes(staged / 'material-runtime-sources.jar', {str(p.relative_to(ROOT)): raw for p, raw in frozen.items()})
        for name, raw in resources.items():
            (staged / name).write_bytes(raw)
        files = [{'path': str(p.relative_to(staged)), 'size': p.stat().st_size, 'sha256': sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(staged.rglob('*')) if p.is_file()]
        result = {'schema': 'axiom.material-runtime.v1', 'context': context,
                  'contextPolicySha256': sha256(context_raw).hexdigest(),
                  'admissionPolicySha256': sha256(admission_raw).hexdigest(),
                  'nativeMethodMappings': method_mappings,
                  'packMaterialMixinSelection': mixin_selection,
                  'packRecipeMixinSelection': recipe_mixin_selection,
                  'packGCYMMixinSelection': gcym_mixin_selection,
                  'classpath': classpath, 'files': files, 'revisions': selected_revisions(),
                  'engineBuildInputSha256': sha256(engine_raw).hexdigest(),
                  'hostSources': {str(p.relative_to(ROOT)): sha256(raw).hexdigest() for p, raw in frozen.items()},
                  'recipeInputs': {str(p.relative_to(ROOT)): sha256(raw).hexdigest() for p, raw in recipe.items()},
                  'runtimeInputs': runtime['runtimeFiles'] + runtime['compilerFiles'],
                  'qualification': 'observed-not-qualified', 'groovyExecutionQualified': False,
                  'wholePackParity': False, 'distribution': 'local-explicit-native-inputs-only'}
        if host_sources() != frozen or any(p.read_bytes() != raw for p, raw in recipe.items()):
            raise ValueError('Native host source changed during build')
        if (PROFILE / 'axiom-material-contexts.json').read_bytes() != context_raw or (PROFILE / 'axiom-material-admission.json').read_bytes() != admission_template:
            raise ValueError('Profile policy changed during build')
        if POLICY.read_bytes() != policy_raw or (api / 'program.json').read_bytes() != api_raw or (language / 'program.json').read_bytes() != language_raw:
            raise ValueError('Native input policy changed during build')
        if any(sha256(p.read_bytes()).hexdigest() != digest for p, digest in dependency_hashes.items()):
            raise ValueError('Native dependency changed during build')
        if addon_raw is not None and (addon_inventory / 'program.json').read_bytes() != addon_raw:
            raise ValueError('Addon inventory manifest changed during build')
        verify_runtime(java, compiler=True)
        (staged / 'runtime.json').write_text(json.dumps(result, indent=2) + '\n')
        shutil.copytree(staged, output)
    return result


def build_raw(java, engine, libraries, server, cleanroom, foundation, output, addon_inventory, pack, program):
    """Assemble the same original native inputs for the production material command."""
    from axiom_native_root_stage import build as assemble_native
    context_id = 'supersymmetry:material-authoring-pack'
    output = ordinary(output).absolute()
    if output.exists():
        raise ValueError('Material runtime output must be new')
    context_raw = (PROFILE / 'axiom-material-contexts.json').read_bytes()
    context, = [row for row in json.loads(context_raw)['contexts'] if row['id'] == context_id]
    recipe_raw = Path(__file__).read_bytes()
    with tempfile.TemporaryDirectory(prefix='axiom-original-material-runtime-') as temporary:
        assembled = Path(temporary) / 'assembled'
        receipt = assemble_native(java, engine, libraries, server, cleanroom, foundation, assembled,
                                  'preinit', 'supersymmetry:required-early', addon_inventory, pack, program,
                                  assemble_only=True)
        staged = assembled / 'runtime'
        native_program = json.loads((staged / 'program.json').read_bytes())
        admission_raw = (staged / 'admission-policy.json').read_bytes()
        groovy, = [row for row in native_program['nativeContext']['artifacts']
                   if row['descriptor'] == 'mods/groovyscript.pw.toml']
        with zipfile.ZipFile(staged / 'native-home' / groovy['outputPath']) as archive:
            method_mappings = verify_method_mappings(json.loads(admission_raw), archive.read('assets/groovyscript/mappings.srg'))
        # Assembly's baseline verifies source intake; every command supplies its
        # current saved archive separately. Do not package stale saved sources.
        (staged / 'saved-program.zip').unlink()
        (staged / 'program.json').unlink()
        (staged / 'context-policy.json').write_bytes(context_raw)
        (staged / 'native-assembly.json').write_text(json.dumps(receipt, indent=2) + '\n')
        files = [{'path': p.relative_to(staged).as_posix(), 'size': p.stat().st_size, 'sha256': sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(staged.rglob('*')) if p.is_file()]
        result = {'schema': 'axiom.material-runtime.v1', 'context': context,
                  'contextPolicySha256': sha256(context_raw).hexdigest(),
                  'admissionPolicySha256': sha256(admission_raw).hexdigest(),
                  'nativeMethodMappings': method_mappings,
                  'nativeInitialization': {'schema': 'axiom.original-native-initialization.v1',
                      'inputStage': 'raw-original-artifacts', 'scope': 'original-preinit-through-non-recipe-registry-events',
                      'nativeContext': native_program['nativeContext']},
                  'classpath': native_program['classpath'], 'files': files, 'revisions': selected_revisions(),
                  'engineBuildInputSha256': receipt['engineSha256'],
                  'hostSources': {path: digest for path, digest in receipt['sourceInputs'].items() if path.endswith('.java')},
                  'recipeInputs': {**receipt['sourceInputs'], str(Path(__file__).relative_to(ROOT)): sha256(recipe_raw).hexdigest()},
                  'runtimeInputs': receipt['jvm']['runtimeFiles'] + receipt['jvm']['compilerFiles'],
                  'qualification': 'observed-not-qualified', 'groovyExecutionQualified': False,
                  'wholePackParity': False, 'distribution': 'local-explicit-native-inputs-only'}
        if Path(__file__).read_bytes() != recipe_raw or (PROFILE / 'axiom-material-contexts.json').read_bytes() != context_raw:
            raise ValueError('Original material runtime inputs changed during assembly')
        for path, digest in receipt['sourceInputs'].items():
            checked_path(ROOT, {'path': path, 'sha256': digest})
        (staged / 'runtime.json').write_text(json.dumps(result, indent=2) + '\n')
        shutil.copytree(staged, output)
        for row in files:
            checked_path(output, row)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'library-root', 'cleanroom', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('images', 'api-program', 'language-program', 'server', 'foundation', 'pack', 'program'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--raw-native', action='store_true', help='Original pack preInit path with raw SERVER inputs')
    parser.add_argument('--context', default=CONTEXT)
    parser.add_argument('--addon-inventory', type=Path)
    args = parser.parse_args(argv)
    if args.raw_native or args.context == 'supersymmetry:material-authoring-pack':
        if args.context != 'supersymmetry:material-authoring-pack' or any(value is None for value in
                (args.server, args.foundation, args.pack, args.program, args.addon_inventory)):
            parser.error('--raw-native requires pack context, --server, --foundation, --pack, --program and --addon-inventory')
        if any(value is not None for value in (args.images, args.api_program, args.language_program)):
            parser.error('Original native inputs cannot be combined with prepared runtime images')
        result = build_raw(args.java_home, args.engine_home, args.library_root, args.server, args.cleanroom,
                           args.foundation, args.output, args.addon_inventory, args.pack, args.program)
    else:
        if any(value is None for value in (args.images, args.api_program, args.language_program)):
            parser.error('The bounded prepared context requires --images, --api-program and --language-program')
        if any(value is not None for value in (args.server, args.foundation, args.pack, args.program)):
            parser.error('Original SERVER input options require --raw-native')
        result = build(args.java_home, args.engine_home, args.images, args.library_root, args.api_program,
                       args.language_program, args.cleanroom, args.output, args.context, args.addon_inventory)
    print(json.dumps({'files': len(result['files']), 'hostSources': len(result['hostSources']), 'qualification': result['qualification']}))


if __name__ == '__main__':
    main()
