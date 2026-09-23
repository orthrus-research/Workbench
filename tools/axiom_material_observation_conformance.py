#!/usr/bin/env python3
"""Fixed-program observation differential with an upstream compiler/lifecycle reference.

The native API, content declarations and platform remain shared dependencies.
This is not a whole-upstream oracle, a public execution mode, or validity promotion.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import axiom_pack_helper_cases as pack_helpers
import zipfile

from axiom_material_program_cases import cases, Edit, EDITS, LISTENERS, PRODUCER, identity
from axiom_material_program_sources import selected_revisions
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK
from build_axiom_native_materials import compile_sources, jar_bytes
from build_axiom_target import git
import axiom_material_authoring_cases as authoring
import axiom_recipe_map_cases as recipe_maps

ROOT = Path(__file__).resolve().parents[1]
ORACLES = ROOT / 'modules/axiom/tests/oracles'
COUNTS = ('variants', 'registeredBlocks', 'registeredBlockItems', 'registeredOreBlocks', 'registeredOreItems')


def registration_source(original):
    start, end = '/* Start Material Registration */', '/* End Material Registration */'
    if original.count(start) != 1 or original.count(end) != 1 or original.index(start) >= original.index(end):
        raise ValueError('Original material registration section is not unique and ordered')
    section = original[original.index(start):original.index(end) + len(end)]
    # Keep every source statement, including original logger calls and registry selection.
    source = '''package research.orthrus.axiom.materialhost;
import gregtech.api.GregTechAPI;
import gregtech.api.GTValues;
import gregtech.api.unification.material.Materials;
import gregtech.api.unification.material.event.*;
import gregtech.api.unification.material.registry.MarkerMaterialRegistry;
import gregtech.core.unification.material.internal.MaterialRegistryManager;
import net.minecraftforge.common.MinecraftForge;
public final class OriginalMaterialRegistration {
    private static final org.apache.logging.log4j.Logger logger = org.apache.logging.log4j.LogManager.getLogger("gregtech");
    public static void run() {
''' + section + '\n    }\n}\n'
    return source, section


def corpus():
    selected = [dict(c) for c in cases()[:4]]
    base = selected[0]['files']
    late = selected[3]
    late['files'] = Edit(LISTENERS, "DeveloperMaterials.named(31003, 'developer_too_late').dust().build()",
        "log.infoMC(DeveloperMaterials.named(31003, 'developer_too_late').dust().build().getRegistryName())").apply(late['files'])
    selected.append({'name': 'pending-fluid', 'files': Edit(PRODUCER, '.dust().ore().color', '.dust().ore().liquid().color').apply(base)})
    helpers = '''class MaterialEdits {
    static int evaluated = 0
    static int colorValue(int value) { evaluated = evaluated + 1; return value + evaluated }
    static int colorValue(String value) { return 17 }
'''
    files = Edit(EDITS, 'class MaterialEdits {', helpers).apply(base)
    files = Edit(EDITS, 'Aluminosilicate.setMaterialRGB(0x99ccbb)', 'Aluminosilicate.setMaterialRGB(colorValue(0x99ccbb))').apply(files)
    selected.append({'name': 'overloaded-side-effecting-helper', 'files': files})
    nested='''class MaterialEdits {
    static void invalidProperty() {
        Aluminosilicate.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(0)
    }
    static void nestedEdit() { invalidProperty() }
'''
    files=Edit(EDITS,'class MaterialEdits {',nested).apply(base)
    files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)','nestedEdit()').apply(files)
    selected.append({'name':'nested-deferred-property-error','files':files,'nativeError':True,'executionCompleted':False})
    for ordinal, phase in ((1, 'BLOCK_REGISTERING'), (2, 'REGISTERING')):
        helper = '''class MaterialEdits {
    static int calls = 0
    static void registrationVisit() {
        calls = calls + 1
        if (calls == ''' + str(ordinal) + ''') {
            Aluminosilicate.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(0)
        }
    }
'''
        files = Edit(EDITS, 'class MaterialEdits {', helper).apply(base)
        files[LISTENERS] += b'''
eventManager.listen(EventPriority.LOWEST) { net.minecraftforge.event.RegistryEvent.Register event ->
    classes.MaterialEdits.registrationVisit()
}
'''
        selected.append({'name': 'registry-failure-' + str(ordinal), 'files': files, 'failedPhase': phase})
    return selected


def reference_compiler_inputs(api, dependencies, annotations, host):
    # javac cannot run Cleanroom's Optional transformer. Use the same already
    # hash-verified compiler-only metadata as the native runtime build, first.
    # This view must never enter native_programs or the execution classpath.
    return [api / 'compiler-view.jar', *dependencies, annotations, host]


def compare(reference, native):
    """Compare only independently collected facts; never normalize real event/frame order."""
    changed = []
    for key in ('phase', 'registeredMaterials', 'executionCompleted', 'coverageGaps', 'nativeErrors'):
        if key not in reference or key not in native or reference[key] != native[key]: changed.append(key)
    materials = [{key: row.get(key) for key in ('name', 'formula', 'color', 'components','properties','propertyValues')} for row in native.get('materials', [])]
    if reference.get('materials') != materials: changed.append('materials')
    checkpoints = native.get('lifecycle', {}).get('checkpoints', [])
    if not checkpoints or reference.get('activeOwner') != checkpoints[-1].get('activeOwner'): changed.append('activeOwner')
    progress = native.get('contentProgress', {})
    if reference.get('contentPhase') != progress.get('failedPhase', progress.get('phase')): changed.append('contentPhase')
    counts = progress.get('interruptedState') or (progress.get('completedCheckpoints') or [{}])[-1]
    if reference.get('counts') != ({key: counts[key] for key in COUNTS} if all(key in counts for key in COUNTS) else None): changed.append('counts')
    work = native.get('deferredWork', {})
    queues = {r['prefix']: r['pendingMaterials'] for r in work['prefixProcessing']} if 'prefixProcessing' in work else None
    if reference.get('prefixQueues') != queues: changed.append('prefixQueues')
    fluids = []
    for row in work.get('fluids', []):
        value = {key: row[key] for key in ('material', 'hasFluidProperty')}
        if row['hasFluidProperty']:
            value.update(queuedKeys=[v['key'] for v in row['queued']], storedCount=len(row['stored']), registrationCompleted=row['registrationCompleted'])
        fluids.append(value)
    if reference.get('fluids') != fluids: changed.append('fluids')
    if 'failure' in reference:
        failure = reference['failure']
        if not native.get('nativeException', '').startswith(failure['type'] + ': ' + failure['message'] + '\n'): changed.append('failure')
        diagnostics = [row for row in native.get('diagnostics', []) if row['channel'] == 'native-exception']
        frames = [{key: frame[key] for key in ('path', 'line', 'class', 'method')} for row in diagnostics for frame in row['locations']]
        if (frames != [frame for frame in failure['frames'] if frame['line']>0]
                or native.get('nativeSourceFrames') != failure['frames']): changed.append('sourceFrames')
        if 'causality' in failure and (len(diagnostics)!=1 or diagnostics[0].get('causality')!=failure['causality']): changed.append('nativeExceptionCausality')
    elif native.get('nativeException'): changed.append('unexpectedFailure')
    return changed


def check_record_compilation(reference, native):
    """Bind executed native records to the original compiler's actual class bytes."""
    errors=[]
    records={name:row for name,row in reference.get('compilerBytecode',{}).items() if row.get('superName')=='java/lang/Record'}
    if not records or reference.get('compilerTargetBytecode')!='25':errors.append('nativeRecordCompilerTarget')
    compiled=native.get('recordCompilations',{})
    if set(compiled)!=set(records):errors.append('nativeRecordClassSet')
    for name,row in records.items():
        actual=compiled.get(name,{})
        if (row.get('classVersion')!=69 or actual.get('classVersion')!=row['classVersion']
                or actual.get('originalSha256')!=row.get('sha256')
                or actual.get('components')!=row.get('recordComponents') or not actual.get('guardedSha256')):
            errors.append('nativeRecordCompilerBytes:'+name)
    if native.get('candidateDispatchObservations',{}).get('compiler.constant AnnotationCollectorMode#PREFER_EXPLICIT_MERGED',0)<1:
        errors.append('nativeRecordCollectorMode')
    return errors


def check_cached(case, reference, installed, guarded):
    """Require actual compiler/cache evidence as well as whole-program behavior."""
    changed=compare(reference,installed)+authoring.check(case,reference,installed)
    cache=reference.get('compiledCache',{})
    compiled=cache.get('actualGroovyCompiledClasses',{})
    if not compiled or not any('$_' in name for name in compiled): changed.append('nativeCompiledClosureBytesAbsent')
    if cache.get('cachedClassesLoaded')!=sorted(compiled): changed.append('cachedClassSet')
    for key,expected in (('guarded',guarded),('materialExecutions',1),('registryReplay',False),
                         ('compilerFallback',False),('savedSourceUnchanged',True),('cacheBytesUnchanged',True),
                         ('installedCacheReuseAvailable',False)):
        if cache.get(key)!=expected: changed.append('compiledCache.'+key)
    if '_index.json' not in cache.get('nativeCacheFiles',{}): changed.append('nativeIndexAbsent')
    definitions=cache.get('definitions',[])
    if guarded:
        if len(definitions)!=len(compiled) or {row.get('name') for row in definitions}!=set(compiled): changed.append('configuredCacheProcessorCoverage')
        for row in definitions:
            if (row.get('inputSha256')!=compiled.get(row.get('name')) or row.get('inputSha256')==row.get('outputSha256')
                    or row.get('route')!='CompiledClass.ensureLoaded -> GroovyScriptClassLoader.defineClass(String,byte[])'):
                changed.append('nativeCachedDefinitionRoute')
    elif definitions: changed.append('originalCacheUnexpectedProcessor')
    if reference.get('originalCompilerWithoutHook') is not (not guarded) or reference.get('productionObserversUsed') is not False:
        changed.append('cachedReferenceNotEstablished')
    return changed


def check_trait_compilation(reference, native, traits):
    """Match source helpers and loader-distinct native definitions, not just behavior."""
    errors=[]
    names={name+suffix for name in traits for suffix in ('','$Trait$Helper','$Trait$FieldHelper','$Trait$StaticFieldHelper')}
    expected={name:row for name,row in reference.get('compilerBytecode',{}).items() if name in names}
    actual=native.get('traitCompilations',{})
    if not expected or set(expected)!=set(actual):errors.append('nativeTraitClassSet')
    for name,row in expected.items():
        observed=actual.get(name,{})
        if (observed.get('originalSha256')!=row['sha256'] or not observed.get('guardedSha256')
                or any(observed.get(k)!=row[k] for k in ('classVersion','superName','interfaces'))):
            errors.append('nativeTraitCompilerBytes:'+name)
    before=reference.get('traitDefinitions',[]);after=native.get('traitDefinitions',[])
    if not before or len(before)!=len(after):errors.append('nativeTraitDefinitionSet')
    for original,installed in zip(before,after):
        row=original['bytecode']
        if (any(original.get(k)!=installed.get(k) for k in ('name','definitionScope'))
                or any(row.get(k)!=installed.get(k) for k in ('classVersion','superName','interfaces'))
                or not installed.get('originalSha256') or not installed.get('guardedSha256')
                or original.get('methodOrderIndependentSha256')!=installed.get('methodOrderIndependentSha256')
                or not original.get('methodOrderIndependentSha256')
                or installed.get('kind')=='adapter' and row['sha256']!=installed.get('originalSha256')):
            errors.append('nativeTraitDefinitionBytes:'+original['name'])
    return errors


def qualify(java, engine, runtime, images, libraries, api, language, groovyscript, gtceu, compiler_annotations, report, authoring_only=False, cached_authoring=False, argument_authoring=False, property_authoring=False, language_authoring=False, record_authoring=False, trait_authoring=False, supersymmetry=None, recipe_map_authoring=False, pack_helper_authoring=False):
    if pack_helper_authoring and (not authoring_only or any((cached_authoring,argument_authoring,property_authoring,language_authoring,record_authoring,trait_authoring,recipe_map_authoring))):
        raise ValueError('Pack helper qualification requires its own authoring selection')
    if cached_authoring and not authoring_only: raise ValueError('Cached qualification requires the complete ordinary-authoring corpus')
    if argument_authoring and not authoring_only: raise ValueError('Argument qualification requires the authoring lane')
    if property_authoring and (not authoring_only or argument_authoring): raise ValueError('Property qualification requires its own authoring selection')
    if language_authoring and (not authoring_only or argument_authoring or property_authoring):
        raise ValueError('Language qualification requires its own authoring selection')
    if record_authoring and (not authoring_only or argument_authoring or property_authoring or language_authoring):
        raise ValueError('Record qualification requires its own authoring selection')
    if trait_authoring and (not authoring_only or any((cached_authoring,argument_authoring,property_authoring,language_authoring,record_authoring))):
        raise ValueError('Trait qualification requires its own authoring selection')
    if recipe_map_authoring and (not authoring_only or any((cached_authoring,argument_authoring,property_authoring,language_authoring,record_authoring,trait_authoring))):
        raise ValueError('RecipeMap qualification requires its own authoring selection')
    select=recipe_maps.corpus if recipe_map_authoring else authoring.trait_corpus if trait_authoring else authoring.record_corpus if record_authoring else authoring.language_corpus if language_authoring else authoring.property_corpus if property_authoring else authoring.argument_corpus if argument_authoring else authoring.corpus if authoring_only else corpus
    if pack_helper_authoring:
        select=pack_helpers.corpus
    java, engine, runtime, images, libraries, api, language, groovyscript, gtceu, compiler_annotations = [p.resolve(strict=True)
        for p in (java, engine, runtime, images, libraries, api, language, groovyscript, gtceu, compiler_annotations)]
    if report.exists(): raise ValueError('Observation receipt must be new')
    jvm = verify_runtime(java, compiler=True)
    engine_raw, engine_manifest, jars = engine_inputs(engine, LOCK.read_bytes())
    runtime_raw = (runtime / 'runtime.json').read_bytes(); runtime_manifest = json.loads(runtime_raw)
    for row in runtime_manifest['files']: checked_path(runtime, row)
    policy = json.loads((runtime / 'native-input-policy.json').read_bytes())
    deps = [checked_path(images, row) for row in policy['images']] + [checked_path(libraries, row) for row in policy['libraries']]
    manifests = {directory: (directory / 'program.json').read_bytes() for directory in (api, language)}
    for directory, raw in manifests.items():
        for name, digest in json.loads(raw)['artifacts'].items(): checked_path(directory, {'path': name, 'sha256': digest})
    if json.loads(manifests[language])['apiManifestSha256'] != sha256(manifests[api]).hexdigest(): raise ValueError('API/language mismatch')
    if (runtime / 'api-build.json').read_bytes() != manifests[api] or (runtime / 'language-build.json').read_bytes() != manifests[language]:
        raise ValueError('Installed runtime does not use the reference shared native dependencies')
    rev = selected_revisions()
    sintering=None
    if pack_helper_authoring:
        if supersymmetry is None:raise ValueError('Pack helper qualification requires the selected pack source')
        sintering={path:git(supersymmetry.resolve(strict=True),'show',rev['supersymmetry']+':'+path) for path in pack_helpers.PATHS}
    if trait_authoring:
        if supersymmetry is None:raise ValueError('Native trait qualification requires the selected pack source')
        sintering=git(supersymmetry.resolve(strict=True),'show',rev['supersymmetry']+':groovy/globals/Sintering.groovy')
    if recipe_map_authoring:
        if supersymmetry is None:raise ValueError('Native RecipeMap qualification requires the selected pack source')
        sintering=git(supersymmetry.resolve(strict=True),'show',rev['supersymmetry']+':'+recipe_maps.EXTENSION)
    classloader_path = 'src/main/java/com/cleanroommc/groovyscript/sandbox/GroovyScriptClassLoader.java'
    core_path = 'src/main/java/gregtech/core/CoreModule.java'
    original_classloader = git(groovyscript, 'show', rev['groovyscript'] + ':' + classloader_path)
    original_core = git(gtceu, 'show', rev['gtceu'] + ':' + core_path)
    mechanism_sources=[]
    if pack_helper_authoring:
        mechanism_sources.extend({'repository':'supersymmetry','revision':rev['supersymmetry'],
            'path':path,'sha256':sha256(raw).hexdigest(),'change':'none'} for path,raw in sintering.items())
    elif sintering is not None:
        mechanism_sources.append({'repository':'supersymmetry','revision':rev['supersymmetry'],
            'path':recipe_maps.EXTENSION if recipe_map_authoring else 'groovy/globals/Sintering.groovy','sha256':sha256(sintering).hexdigest(),'change':'none'})
    if authoring_only:
        for repository,root,paths in (
            ('groovyscript',groovyscript,[
                'sandbox/GroovyScriptSandbox.java','sandbox/CustomGroovyScriptEngine.java',
                'sandbox/CompiledClass.java','sandbox/CompiledScript.java','sandbox/mapper/GroovyDeobfMapper.java',
                'sandbox/transformer/GroovyScriptCompiler.java','sandbox/transformer/GroovyScriptEarlyCompiler.java',
                'sandbox/transformer/GroovyScriptTransformer.java','sandbox/transformer/GroovyCodeFactory.java',
                'core/visitors/InvokerHelperVisitor.java','core/visitors/StaticVerifierVisitor.java']),
            ('gtceu',gtceu,['integration/groovy/GroovyMaterialBuilderExpansion.java','api/unification/material/info/MaterialFlags.java']),
        ):
            prefix='src/main/java/'+('com/cleanroommc/groovyscript/' if repository=='groovyscript' else 'gregtech/')
            for path in paths:
                raw=git(root,'show',rev[repository]+':'+prefix+path)
                mechanism_sources.append({'repository':repository,'path':prefix+path,'revision':rev[repository],'sha256':sha256(raw).hexdigest()})
        mapping_path='src/main/resources/assets/groovyscript/mappings.srg'
        raw=git(groovyscript,'show',rev['groovyscript']+':'+mapping_path)
        mechanism_sources.append({'repository':'groovyscript','path':mapping_path,'revision':rev['groovyscript'],'sha256':sha256(raw).hexdigest()})
        if runtime_manifest['nativeMethodMappings']['sha256']!=sha256(raw).hexdigest():
            raise ValueError('Native authoring mapping differs from selected source')
    registration, section = registration_source(original_core.decode())
    native_programs = [language / 'groovy-language.jar', api / 'material-api.jar', next(p for p in jars if p.name == 'groovy-4.0.30.jar')]
    deps += native_programs
    source_paths = [Path(__file__), ROOT / 'tools/axiom_material_program_cases.py', ROOT / 'tools/build_axiom_native_materials.py',
        ROOT / 'modules/axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRecipeContext.java',
        *[ORACLES / (name + '.java') for name in ('MaterialObservationReference', 'RecipeMapReference', 'MaterialArgumentBytecode', 'MaterialTraitReference', 'NativeCompiledCacheProbe', 'GroovyLanguageConformance', 'GroovyNativeBootstrap', 'ContextMixinService', 'NativeTransformAudit')]]
    if authoring_only:
        source_paths += [ROOT / 'tools/axiom_material_authoring_cases.py', ROOT / 'tools/axiom_groovy_language_conformance.py', ROOT / 'tools/axiom_recipe_map_cases.py', ROOT / 'tools/axiom_pack_helper_cases.py']
    frozen = {p: p.read_bytes() for p in source_paths}
    host_jar=runtime/'lib/material-runtime.jar'
    compiler_inputs = reference_compiler_inputs(api, deps, compiler_annotations, host_jar)
    bound_files = {p: sha256(p.read_bytes()).hexdigest() for p in compiler_inputs}
    with zipfile.ZipFile(host_jar) as archive:
        gate_classes={name:archive.read(name) for name in archive.namelist() if any(name.startswith(
            'research/orthrus/axiom/materialhost/'+prefix) for prefix in ('MaterialCallGate','MaterialBytecodeGate','MaterialRecordBytecode','MaterialTraitClasses','MaterialTraitLoaderHook','MaterialAdmissionPolicy')) and name.endswith('.class')}
    if not gate_classes: raise ValueError('Installed class-definition policy implementation absent')
    runs = []; errors = []
    with tempfile.TemporaryDirectory(prefix='axiom-material-reference-') as temporary:
        work = Path(temporary)
        sources = {p.stem: frozen[p].decode() for p in source_paths if p.suffix == '.java' and p.stem != 'GroovyLanguageConformance'}
        sources.update(GroovyScriptClassLoader=original_classloader.decode(), OriginalMaterialRegistration=registration)
        classes = compile_sources(java, sources, compiler_inputs, work / 'reference')
        classes.update(gate_classes) # exact installed classes, not a rewritten test gate
        service={'META-INF/services/org.spongepowered.asm.service.IMixinService': b'research.orthrus.axiom.materialtest.ContextMixinService'}
        jar_bytes(work / 'reference.jar', {**classes,
            'META-INF/services/org.spongepowered.asm.service.IMixinService': b'research.orthrus.axiom.materialtest.ContextMixinService'})
        if cached_authoring:
            # Removing the original override exposes the installed language JAR's
            # source-assembled classloader, including its two-argument cache hook.
            cached_classes={name:raw for name,raw in classes.items() if not name.startswith('com/cleanroommc/groovyscript/sandbox/GroovyScriptClassLoader')}
            jar_bytes(work/'cached-guarded.jar',{**cached_classes,**service})
        driver = frozen[ORACLES / 'GroovyLanguageConformance.java'].decode()
        old = 'research.orthrus.axiom.materialtest.GroovyLanguageProbe'
        if driver.count(old) != 1: raise ValueError('Reference entry point anchor differs')
        driver = driver.replace(old, 'research.orthrus.axiom.materialhost.MaterialObservationReference')
        old='.invoke(null,home.toString(),args[13],structure.classes(),admissionPolicy)'
        if driver.count(old)!=1: raise ValueError('Reference probe mode anchor differs')
        driver=driver.replace(old,'.invoke(null,home.toString(),args.length>14?args[14]:args[13],structure.classes(),admissionPolicy)')
        driver_classes = compile_sources(java, {'GroovyLanguageConformance': driver}, jars, work / 'driver')
        jar_bytes(work / 'driver.jar', {**driver_classes, 'axiom/material-admission.json': (runtime / 'admission-policy.json').read_bytes()})
        selected = select(sintering) if trait_authoring or recipe_map_authoring or pack_helper_authoring else select()
        for case in selected:
            archive = work / (case['name'] + '.zip'); jar_bytes(archive, case['files'])
            command = [str(java / 'bin/java'), '--enable-native-access=ALL-UNNAMED', '-cp', os.pathsep.join(map(str, [work / 'driver.jar', *jars])),
                'research.orthrus.axiom.GroovyLanguageConformance', 'supervisor', str(images), str(libraries)]
            for path in [work / 'reference.jar', *native_programs, archive]: command += [str(path), sha256(path.read_bytes()).hexdigest()]
            command.append('no-audit')
            if pack_helper_authoring: command.append('pack-helper-reference')
            elif recipe_map_authoring: command.append('recipe-map-reference')
            elif trait_authoring: command.append('trait-reference')
            elif argument_authoring or property_authoring or language_authoring or record_authoring: command.append('argument-reference')
            process = subprocess.run(command, cwd=work, env={'LANG': 'C.UTF-8'}, capture_output=True, text=True, timeout=50)
            reference = json.loads(process.stdout) if process.returncode == 0 else {'supervisorFailure': process.returncode, 'stderr': process.stderr}
            request = {key: runtime_manifest[key] for key in ('contextPolicySha256', 'admissionPolicySha256')}
            request.update(context=runtime_manifest['context']['id'], observeMaterials=['supersymmetry:developer_' + name for name in ('aluminosilicate', 'phosphate', 'titanate')])
            if case['name'] == 'late-registration': request['observeMaterials'].append('supersymmetry:developer_too_late')
            command = [str(java / 'bin/java'), '-Xmx256m', '-XX:ActiveProcessorCount=2', '-cp', os.pathsep.join(map(str, jars)),
                'research.orthrus.axiom.Main', 'material-program', '--runtime-home', str(runtime), '--program', str(archive)]
            installed = subprocess.run(command, input=json.dumps(request), cwd=work, env={'LANG': 'C.UTF-8'}, capture_output=True, text=True, timeout=50)
            native_result = json.loads(installed.stdout)
            before = reference.get('result', {}).get('execution', {})
            after = native_result.get('result', {}).get('execution', {})
            differences = compare(before, after)
            if authoring_only: differences += authoring.check(case, before, after)
            if recipe_map_authoring: differences += recipe_maps.check(case, before, after)
            if record_authoring: differences += check_record_compilation(before, after)
            if trait_authoring:
                differences += check_trait_compilation(before,after,reference.get('result',{}).get('sourceAdmission',{}).get('traitClasses',[]))
                if case['name']=='trait-loader-distinct-compositions':
                    adapters=[r for r in after.get('traitDefinitions',[]) if r.get('kind')=='adapter']
                    if len(adapters)!=2 or adapters[0]['name']!=adapters[1]['name'] or adapters[0]['definitionScope']==adapters[1]['definitionScope']:
                        differences.append('loaderDistinctAdaptersNotObserved')
            if not before.get('originalCompilerWithoutHook') or before.get('productionObserversUsed') is not False: differences.append('referenceNotEstablished')
            if before.get('registeredMaterials') != 605 or len(before.get('materials', [])) != 3: differences.append('completeProgramNotObserved')
            if before.get('executionCompleted') is not case.get('executionCompleted',case['name'] not in ('property-setter-error', 'registry-failure-1', 'registry-failure-2')):
                differences.append('expectedCompletion')
            if case.get('failedPhase') and before.get('contentPhase') != case['failedPhase']: differences.append('failureNotReached')
            if case['name'] == 'overloaded-side-effecting-helper' and (not before.get('materials') or before['materials'][0]['color'] != 0x99ccbc): differences.append('helperEvaluationChanged')
            if case['name'] == 'pending-fluid' and (not before.get('fluids') or before['fluids'][0].get('queuedKeys') != ['gregtech:liquid']): differences.append('fluidNotQueued')
            if case['name'] == 'logged-components-error' and 'Tried to use old method for material components' not in before.get('log', ''):
                differences.append('originalLoggedErrorMissing')
            if case['name'] == 'late-registration':
                if before.get('lateRegistered') is not False or not any(row['requested'] == 'supersymmetry:developer_too_late'
                        and row['exactIdentity'] is False for row in after.get('lookups', [])): differences.append('lateRegistrySkip')
                if 'Materials cannot be registered in the PostMaterialEvent' not in before.get('log4j', ''): differences.append('originalRegistryErrorMissing')
                for observed in (before, after):
                    if 'supersymmetry:developer_too_late' not in observed.get('log', ''): differences.append('lateObjectConstructionMissing')
            if case['name']=='nested-deferred-property-error':
                methods=[frame['method'] for frame in before.get('failure',{}).get('frames',[])]
                if methods!=['invalidProperty','nestedEdit','apply','doCall']: differences.append('nestedDeferredFrames')
            expected_error = 'source-error' if not case.get('nativeLinkageFailure') and (case.get('nativeError') or case['name'] in ('logged-components-error', 'property-setter-error', 'late-registration', 'registry-failure-1', 'registry-failure-2')) else 'incomplete'
            if case.get('nativeLinkageFailure') and after.get('candidateLinkageFailure') is not True: differences.append('nativeLinkageNotDistinguished')
            if native_result.get('status') != expected_error: differences.append('installedOutcome')
            if native_result.get('result', {}).get('groovyExecutionQualified') is not False: differences.append('qualificationPromoted')
            runs.append({'case': case['name'], 'source': identity(case['files']), 'archiveSha256': sha256(archive.read_bytes()).hexdigest(),
                'reference': reference, 'referenceStderr': process.stderr, 'installed': native_result, 'installedExitCode': installed.returncode, 'changedFacts': differences})
            if cached_authoring:
                cached_runs=[]
                for guarded in (False,True):
                    reference_jar=work/('cached-guarded.jar' if guarded else 'reference.jar')
                    command=[str(java/'bin/java'),'--enable-native-access=ALL-UNNAMED','-cp',os.pathsep.join(map(str,[work/'driver.jar',*jars])),
                        'research.orthrus.axiom.GroovyLanguageConformance','supervisor',str(images),str(libraries)]
                    for path in [reference_jar,*native_programs,archive]: command += [str(path),sha256(path.read_bytes()).hexdigest()]
                    command += ['no-audit','cache-guarded' if guarded else 'cache-original']
                    cached_process=subprocess.run(command,cwd=work,env={'LANG':'C.UTF-8'},capture_output=True,text=True,timeout=50)
                    cached=json.loads(cached_process.stdout) if cached_process.returncode==0 else {'supervisorFailure':cached_process.returncode,'stderr':cached_process.stderr}
                    facts=cached.get('result',{}).get('execution',{})
                    cache_changes=check_cached(case,facts,after,guarded)
                    cached_runs.append({'guarded':guarded,'result':cached,'stderr':cached_process.stderr,'changedFacts':cache_changes})
                    differences.extend(('cached-guarded: ' if guarded else 'cached-original: ')+value for value in cache_changes)
                # Independent fresh workers must produce identical original class bytes,
                # regardless of whether the execution definition hook is configured.
                originals=[r['result'].get('result',{}).get('execution',{}).get('compiledCache',{}).get('actualGroovyCompiledClasses') for r in cached_runs]
                if not originals[0] or originals[0]!=originals[1]: differences.append('originalCompilerBytesDifferAcrossWorkers')
                runs[-1]['cachedRuns']=cached_runs
            errors.extend(case['name'] + ': ' + value for value in differences)
            print(json.dumps({'case':case['name'],'changedFacts':differences}),flush=True)
        artifacts = {p.name: sha256(p.read_bytes()).hexdigest() for p in [work/'reference.jar',work/'driver.jar',*([work/'cached-guarded.jar'] if cached_authoring else [])]}
    if any(p.read_bytes() != raw for p, raw in frozen.items()) or selected != (select(sintering) if trait_authoring or recipe_map_authoring or pack_helper_authoring else select()):
        raise ValueError('Qualification source drift')
    if any(sha256(p.read_bytes()).hexdigest() != digest for p, digest in bound_files.items()): raise ValueError('Reference dependency drift')
    if (runtime / 'runtime.json').read_bytes() != runtime_raw or engine_inputs(engine, LOCK.read_bytes())[0] != engine_raw: raise ValueError('Installed identity drift')
    for row in runtime_manifest['files']: checked_path(runtime, row)
    result = {'schema': 'axiom.material-observation-conformance.v1', 'status': 'passed-observation-differential' if not errors else 'failed',
        'scope': 'source-compiled-cached-authoring' if cached_authoring else ('ordinary-material-authoring' if authoring_only else 'material-lifecycle-and-observation'),
        'authoringSelection':'native-pack-helpers' if pack_helper_authoring else 'native-recipe-map' if recipe_map_authoring else 'native-traits' if trait_authoring else 'native-records' if record_authoring else 'native-language' if language_authoring else 'native-properties' if property_authoring else 'native-arguments' if argument_authoring else 'ordinary' if authoring_only else None,
        'errors': errors, 'runs': runs, 'revisions': rev, 'runtimeManifestSha256': sha256(runtime_raw).hexdigest(),
        'engineManifestSha256': sha256(engine_raw).hexdigest(), 'engineJars': engine_manifest['jars'],
        'qualificationInputs': {str(p.relative_to(ROOT)): sha256(raw).hexdigest() for p, raw in frozen.items()},
        'referenceSources': [{'path': classloader_path, 'revision': rev['groovyscript'], 'sha256': sha256(original_classloader).hexdigest(), 'change': 'none'},
            {'path': core_path, 'revision': rev['gtceu'], 'sha256': sha256(original_core).hexdigest(), 'sectionSha256': sha256(section.encode()).hexdigest(),
             'change': 'verbatim-material-registration-section-in-standalone-static-method-not-full-CoreModule'}],
        'referenceArtifacts': artifacts, 'compilerOnlyAnnotations': {'path': str(compiler_annotations), 'sha256': bound_files[compiler_annotations]},
        'mechanismSources':mechanism_sources,
        'installedGateClasses':{name:sha256(raw).hexdigest() for name,raw in gate_classes.items()},
        'installedHostJar':{'path':str(host_jar),'sha256':bound_files[host_jar]},
        'cachedAuthoring':cached_authoring,'installedCacheReuseAvailable':False,
        'sharedDependencies': [{'path': str(p), 'sha256': bound_files[p]} for p in deps],
        'runtimeInputs': jvm['runtimeFiles'] + jvm['compilerFiles'],
        'claim': 'original-classloader-and-material-registration-reference; observer differential over shared API/content/platform',
        'independentNativeApiOracle': False, 'groovyExecutionQualified': False, 'wholePackParity': False}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'images', 'library-root', 'api-program', 'language-program', 'groovyscript', 'gtceu', 'compiler-annotations', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--authoring-only',action='store_true',help='Complete ordinary authoring against original compiler and installed operation; no security campaign')
    parser.add_argument('--cached-authoring',action='store_true',help='Also compare actual Groovy-compiled native disk-cache definitions in fresh qualification workers; requires --authoring-only')
    parser.add_argument('--argument-authoring',action='store_true',help='Select complete native argument/overload/coercion programs; requires --authoring-only')
    parser.add_argument('--property-authoring',action='store_true',help='Select complete native property constructor/mutation programs; requires --authoring-only')
    parser.add_argument('--language-authoring',action='store_true',help='Select complete native pack language/transform programs; requires --authoring-only')
    parser.add_argument('--record-authoring',action='store_true',help='Select complete native record programs; requires --authoring-only')
    parser.add_argument('--trait-authoring',action='store_true',help='Select complete native trait/composition programs; requires --authoring-only')
    parser.add_argument('--recipe-map-authoring',action='store_true',help='Select native RecipeMap extension and constructor programs; requires --authoring-only')
    parser.add_argument('--pack-helper-authoring',action='store_true',help='Select whole pinned pack helper programs; not whole-pack acceptance')
    parser.add_argument('--supersymmetry',type=Path,help='Selected source checkout, required by --trait-authoring for unchanged Sintering')
    a = parser.parse_args()
    result = qualify(a.java_home, a.engine_home, a.runtime_home, a.images, a.library_root, a.api_program, a.language_program, a.groovyscript, a.gtceu, a.compiler_annotations, a.report,a.authoring_only,a.cached_authoring,a.argument_authoring,a.property_authoring,a.language_authoring,a.record_authoring,a.trait_authoring,a.supersymmetry,a.recipe_map_authoring,a.pack_helper_authoring)
    print(json.dumps({'status': result['status'], 'runs': len(result['runs']), 'errors': result['errors']}))
    return 1 if result['errors'] else 0


if __name__ == '__main__': raise SystemExit(main())
