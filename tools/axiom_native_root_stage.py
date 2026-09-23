#!/usr/bin/env python3
"""Build and observe original raw SERVER root setup; no game or material launch."""
import argparse
import base64
import gzip
import io
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as ENGINE_LOCK
from axiom_native_identity_conformance import ordinary
from build_axiom_native_materials import jar_bytes
from build_axiom_target import git, ordinary_path

ROOT=Path(__file__).resolve().parents[1]
for source in (ROOT / 'api/src', ROOT / 'modules/axiom/src'):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
from workbench_axiom import native_assembly

POLICY=ROOT/'profiles/platforms/cleanroom/native-root-class-space.json'
LIBRARIES=ROOT/'profiles/platforms/cleanroom/native-identity-runtime.json'
LOCK=ROOT/'modules/axiom/sources/native-root-class-space.lock.json'
BUILDER=ROOT/'modules/axiom/tests/oracles/NativeIdentityInputs.java'
PROBE=ROOT/'modules/axiom/tests/oracles/NativeRootStageProbe.java'
CONSOLE=ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/NativeConsoleCapture.java'
ORIGINAL_PROGRAM=CONSOLE.with_name('OriginalNativeProgram.java')
SNAPSHOTS=CONSOLE.with_name('NativeStageSnapshots.java')
HELPER=ROOT/'modules/axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRootClassSpace.java'
PREFIX=ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/materialhost/NativeCoremodPrefix.java'
EARLY_PREFIX=PREFIX.with_name('NativeEarlyLaunchPrefix.java')
EARLY_HELPER=HELPER.with_name('NativeEarlyClassSpace.java')
SELECTION_PREFIX=PREFIX.with_name('NativeLoaderPrefix.java')
SELECTION_HELPER=HELPER.with_name('NativeSelectionClassSpace.java')
CONSTRUCTION_HELPER=SELECTION_HELPER.with_name("NativeConstructionClassSpace.java")
CONFIGURATION_OBSERVATIONS=SELECTION_HELPER.with_name("NativeConfigurationObservations.java")
SELECTED_OBSERVATIONS=SELECTION_HELPER.with_name("NativeSelectedMaterialObservations.java")
GROOVY_HELPER=SELECTION_HELPER.with_name("NativeGroovyClassSpace.java")
GROOVY_OBSERVATIONS=SELECTION_HELPER.with_name("NativeProgramObservations.java")
EFFECT_OBSERVATIONS=[SELECTION_HELPER.with_name(name+'.java') for name in (
    'NativeObservationAccess','NativeMaterialObservations','NativeMaterialPropertyState',
    'NativeRegistrationEffects','NativeMetaItemObservations','NativeFluidObservations','NativeGeneratedContentObservations',
    'NativeBiomeObservations','NativeServerOwnerClassSpace','NativeRecipeValues','NativeEffectiveRecipeObservations',
    'NativeVanillaRecipeObservations')]
GROOVY_PREFIX=PREFIX.with_name("NativeGroovyInitializationPrefix.java")
FOUNDATION_BOOTSTRAP=PREFIX.with_name("NativeFoundationBootstrap.java")
ADMISSION_SOURCES=[PREFIX.with_name(name+'.java') for name in (
    'MaterialCallGate','MaterialBytecodeGate','MaterialRecordBytecode','MaterialTraitClasses','MaterialTraitLoaderHook',
    'MaterialAdmissionPolicy','MaterialMapperBindings','MaterialPropertyBindings','MaterialScriptBindings','NativeServerOwnerPrefix','RecipeStateCatalog','NativeRecipeFunctionObservations','NativeDiagnosticOrigins','MaterialDiagnosticCauses','MaterialDiagnosticGroups')]
PACK_PROFILE=ROOT/'profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/axiom-native-early-context.json'
PROGRAM_CONTEXTS=PACK_PROFILE.with_name('axiom-material-contexts.json')
EARLY_INPUTS=ROOT/'tools/axiom_native_early_inputs.py'
RESOURCES=['binpatches.pack.lzma','deobf_data-1.12.2.tsrg','forge_at.cfg','mcpmod.info']
TARGETS={'net/minecraft/block/Block','net/minecraft/item/ItemStack','net/minecraft/util/EnumFacing',
         'net/minecraft/util/math/Vec3d','net/minecraft/enchantment/Enchantment'}
ROOTS=[('net.minecraftforge.fml.relauncher.FMLCorePlugin','net.minecraftforge.fml.common.asm.FMLSanityChecker'),
       ('net.minecraftforge.classloading.FMLForgePlugin','none')]
EARLY_WRAPPER_PARENTS=['net.minecraftforge.fml.common.asm.transformers.'+name for name in (
    'SideTransformer','EventSubscriptionTransformer','EventSubscriberTransformer','SoundEngineFixTransformer','LWJGLTransformer')]
# Source-locked empty-home SERVER order: CleanMix PREINIT proxy, root registration,
# wrapped FMLCorePlugin transforms, complete FMLDeobf transition, then INIT proxy.
EARLY_TRANSFORMERS=['org.spongepowered.asm.mixin.transformer.Proxy',
    'net.minecraftforge.fml.common.asm.transformers.PatchingTransformer',
    *['$wrapper.'+parent for parent in EARLY_WRAPPER_PARENTS],
    *['net.minecraftforge.fml.common.asm.transformers.'+name for name in (
        'DeobfuscationTransformer','AccessTransformer','ModAccessTransformer','ItemStackTransformer',
        'ItemBlockTransformer','ItemBlockSpecialTransformer','PotionEffectTransformer')],
    'org.spongepowered.asm.mixin.transformer.Proxy']
VEC3D_METHODS=['func_72431_c(Lnet/minecraft/util/math/Vec3d;)Lnet/minecraft/util/math/Vec3d;',
               'func_189985_c()D','func_189986_a(FF)Lnet/minecraft/util/math/Vec3d;',
               'func_189984_a(Lnet/minecraft/util/math/Vec2f;)Lnet/minecraft/util/math/Vec3d;']


def verify_policy(policy, libraries, lock, library_raw):
    if (policy.get('schema')!='axiom.native-root-class-space-policy.v1' or policy.get('profile')!='cleanroom'
            or policy.get('side')!='SERVER' or policy.get('inputStage')!='raw-original-artifacts'
            or policy.get('libraryPolicy')!=LIBRARIES.name
            or policy.get('libraryPolicySha256')!=sha256(library_raw).hexdigest()
            or policy.get('cleanroomRevision')!=libraries.get('cleanroomRevision')
            or policy.get('cleanroomRevision')!=lock.get('revisions',{}).get('cleanroom')
            or policy.get('requiredResources')!=RESOURCES
            or policy.get('qualification')!={'materialInitializationComplete':False,'fullLauncherCompositionQualified':False,'wholePackParity':False}):
        raise ValueError('Native root policy stage, side, source or qualification differs')
    rows=policy.get('inputs',[])
    if [row.get('role') for row in rows]!=['cleanroom-universal','minecraft-server']:
        raise ValueError('Original universal and raw SERVER inputs required')
    if any(rows[0].get(k)!=libraries['inputs'][0].get(k) for k in ('path','size','sha256','url')):
        raise ValueError('Root universal differs from selected original Cleanroom artifact')
    for row in rows:
        ordinary_path(row['path'])
        if (type(row.get('size')) is not int or row['size']<=0 or not isinstance(row.get('sha256'),str)
                or len(row['sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['sha256'])):
            raise ValueError('Native root artifact pin is malformed')


def verify_sources(roots, lock):
    if (lock.get('schema')!='axiom.native-root-class-space-source-lock.v1'
            or set(lock.get('revisions',{}))!={'cleanroom','foundation'}):
        raise ValueError('Native root source lock differs')
    observed={}
    for row in lock['references']:
        repo=row['repository'];path=ordinary_path(row['path'])
        if repo not in roots or (repo,path) in observed:raise ValueError('Duplicate or unknown native root source')
        revision=lock['revisions'][repo]
        if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ValueError('Native source revision is mutable')
        raw=git(roots[repo],'show',revision+':'+path)
        if (len(raw)!=row['size'] or sha256(raw).hexdigest()!=row['sha256']
                or sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=row['gitBlob']):
            raise ValueError('Original native root source changed: '+path)
        observed[repo,path]=raw
    if len(observed)!=39:raise ValueError('Native root source closure differs')
    return observed


def check_result(response, descriptor=None):
    result=response.get('result',{})
    failures=[]
    if (response.get('schema')!='axiom.result.v1' or response.get('operation')!='native-root-stage'
            or response.get('scope')!='original-server-root-stage-not-material-initialization'
            or response.get('status')!='incomplete'):
        failures.append('Native root envelope differs or promotes complete initialization')
    if not isinstance(result,dict):return failures+['Native root result is not an object']
    if (result.get('schema')!='axiom.native-root-stage.v1' or result.get('side')!='SERVER'
            or result.get('inputStage')!='original-obfuscated-artifacts' or result.get('stage')!='root-stage-ready'):
        failures.append('Native root side, stage or schema differs')
    if any('failure' in key.lower() for key in result) or response.get('diagnostics')!=[]:
        failures.append('Native root failure cannot qualify setup')
    for key in ('materialInitializationComplete','fullLauncherCompositionQualified','minecraftLaunched','candidateCompilationStarted','targetClassesDefined'):
        if result.get(key) is not False:failures.append('Root observation has unsupported claim: '+key)
    for key in ('rootStageReady','patchInventoryMatched','nativeHomeInitialized','nativeOptionsAccepted'):
        if result.get(key) is not True:failures.append('Original SERVER root observation missing: '+key)
    count=result.get('binaryPatches')
    if type(count) is not int or count<=0:failures.append('Nonempty original SERVER patch inventory required')
    sources=result.get('artifactCodeSources',{})
    if (not isinstance(sources,dict) or set(sources)!={'cleanroom','minecraft'}
            or any(not isinstance(p,str) or not Path(p).is_absolute() for p in sources.values())
            or sources.get('cleanroom')==sources.get('minecraft')):
        failures.append('Distinct original artifact CodeSources required')
        sources={}
    witnesses=result.get('witnesses',{})
    if not isinstance(witnesses,dict) or set(witnesses)!=TARGETS:
        failures.append('Five exact patch/remap/access byte witnesses required')
    else:
        for target,row in witnesses.items():
            if (not isinstance(row,dict) or row.get('targetDefined') is not False
                    or not isinstance(row.get('source'),str) or not row['source'] or row['source']==target
                    or any(not isinstance(row.get(key),str) or re.fullmatch('[0-9a-f]{64}',row[key]) is None
                           for key in ('patchedSha256','remappedSha256','accessSha256'))):
                failures.append('Malformed original byte witness: '+target)
                continue
            if descriptor is not None and any(row[key]!=descriptor.get('witnesses',{}).get(target,{}).get(key)
                    for key in ('source','patchedSha256','remappedSha256','accessSha256')):
                failures.append('Original byte witness differs from selected descriptor: '+target)
    callbacks=result.get('rootCallbacks',[])
    if not isinstance(callbacks,list) or len(callbacks)!=2:
        failures.append('Both original root wrapper callbacks required')
    else:
        for row,(plugin,setup) in zip(callbacks,ROOTS):
            if (not isinstance(row,dict) or row.get('plugin')!=plugin or row.get('setupClass')!=setup
                    or row.get('wrapperInjectionReturned') is not True or row.get('codeSource')!=sources.get('cleanroom')):
                failures.append('Original root callback identity/order/CodeSource differs: '+plugin)
    if result.get('injectedContainers')!=['net.minecraftforge.fml.common.FMLContainer','net.minecraftforge.common.ForgeModContainer']:
        failures.append('Original root container injection differs')
    if result.get('queuedTweaks')!=['net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker','org.spongepowered.asm.launch.MixinTweaker']:
        failures.append('Original root queued tweaks differ')
    if descriptor is not None and count!=descriptor.get('binaryPatches'):
        failures.append('Original patch count differs from selected descriptor')
    return failures


def check_early_result(response, descriptor=None, *, context=None, source_program=None):
    failures=[]
    if (response.get('schema')!='axiom.result.v1' or response.get('operation')!='native-early-stage'
            or response.get('scope')!='original-server-early-stage-not-material-initialization'
            or response.get('status')!='incomplete'):
        failures.append('Native early envelope differs or promotes material completion')
    result=response.get('result')
    if not isinstance(result,dict):return failures+['Native early result is not an object']
    if (result.get('schema')!='axiom.native-early-stage.v1' or result.get('side')!='SERVER'
            or result.get('inputStage')!='original-obfuscated-artifacts' or result.get('stage')!='early-stage-ready'
            or result.get('mixinPhase')!='INIT' or result.get('executionStage')!='early'):
        failures.append('Native early side, stage or phase differs')
    for key in ('earlyPipelineReady','deobfTransitionReturned','loaderInstanceCreated',
                'nativeOptionsAccepted','nativeHomeInitialized','targetClassesDefined','nativeDiagnosticsComplete'):
        if result.get(key) is not True:failures.append('Native early observation missing: '+key)
    for key in ('rootStageReady','materialInitializationComplete','fullLauncherCompositionQualified','minecraftLaunched',
                'candidateCompilationStarted','lateMixinSelectionQualified','constructionDispatched','loadControllerDefinedBeforeDefault'):
        if result.get(key) is not False:failures.append('Early observation has unsupported claim: '+key)
    diagnostics=result.get('nativeDiagnostics')
    if (not isinstance(diagnostics,list) or response.get('diagnostics')!=diagnostics
            or any(not isinstance(row,dict) or row.get('severity')!='warning'
                   or any(not isinstance(row.get(field),str) for field in ('logger','message','trace'))
                   or row.get('locationStatus')!='unlocated' for row in diagnostics)
            or any('failure' in key.lower() for key in result)):
        failures.append('Native early failure or logged error cannot qualify initialization')
    sources=result.get('artifactCodeSources',{})
    if (not isinstance(sources,dict) or set(sources)!={'cleanroom','minecraft'}
            or any(not isinstance(path,str) or not Path(path).is_absolute() for path in sources.values())
            or sources.get('cleanroom')==sources.get('minecraft')):
        failures.append('Original early artifact CodeSources differ');sources={}
    artifact_sources=dict(sources)
    if context is not None:
        if result.get('nativeArtifactPlacement')!='links-to-verified-read-only-inputs':
            failures.append('Original native artifacts were not reused from verified immutable inputs')
        if result.get('nativeContext')!=context['id']:
            failures.append('Required native early context differs')
        acknowledgement=result.get('sourceProgram')
        if (not isinstance(acknowledgement,dict) or acknowledgement!=source_program
                or type(acknowledgement.get('fileCount')) is not int):
            failures.append('Complete saved Groovy/configuration acknowledgement differs')
        native_artifacts=result.get('nativeArtifacts')
        expected_artifacts={row['descriptor']:row for row in context['artifacts']}
        if not isinstance(native_artifacts,dict) or set(native_artifacts)!=set(expected_artifacts):
            failures.append('Required original early artifact inventory differs')
        else:
            for name,pin in expected_artifacts.items():
                row=native_artifacts[name]
                code_source=row.get('codeSource') if isinstance(row,dict) else None
                output_parts=Path(pin['outputPath']).parts
                if (not isinstance(row,dict) or not isinstance(code_source,str) or not Path(code_source).is_absolute()
                        or Path(code_source).parts[-len(output_parts):]!=output_parts
                        or type(row.get('size')) is not int or row.get('size')!=pin['size']
                        or row.get('sha256')!=pin['sha256']):
                    failures.append('Original early artifact identity or CodeSource differs: '+name)
                else:
                    artifact_sources[name]=code_source
        contained_pins={row['id']:row for row in context.get('containedArtifacts',[])}
        contained=result.get('nativeContainedArtifacts',{})
        contained_root=result.get('containedArtifactRoot')
        if not isinstance(contained,dict) or set(contained)!=set(contained_pins):
            failures.append('Original contained artifact inventory differs')
        else:
            for name,pin in contained_pins.items():
                row=contained[name]
                if (not isinstance(row,dict) or not isinstance(contained_root,str) or not Path(contained_root).is_absolute()
                        or row.get('codeSource')!=str(Path(contained_root)/pin['outputPath'])
                        or row.get('parentArtifact')!=pin['parentArtifact']
                        or row.get('parentCodeSource')!=artifact_sources.get(pin['parentArtifact'])
                        or row.get('entry')!=pin['entry'] or row.get('sha256')!=pin['sha256']
                        or type(row.get('size')) is not int or row.get('size')!=pin['size']
                        or row.get('extractedByObserver') is not False):
                    failures.append('Original contained artifact identity, parent or CodeSource differs: '+name)
                else:
                    artifact_sources[name]=row['codeSource']
        if result.get('queuedAccessTransformers')!=context.get('accessTransformers'):
            failures.append('Original required coremod access transformer queue differs')
        callbacks=result.get('coremodCallbacks')
        if not isinstance(callbacks,list) or len(callbacks)!=len(context['coremods']):
            failures.append('Required original coremod callbacks are incomplete')
        else:
            for row,expected in zip(callbacks,context['coremods']):
                if (not isinstance(row,dict) or row.get('plugin')!=expected['plugin']
                        or row.get('setupClass')!=expected['setupClass'] or row.get('wrapperInjectionReturned') is not True
                        or row.get('codeSource')!=artifact_sources.get(expected['artifact'])):
                    failures.append('Original required coremod callback identity, order or CodeSource differs: '+expected['plugin'])
    callbacks=result.get('rootCallbacks')
    if not isinstance(callbacks,list) or len(callbacks)!=2:
        failures.append('Both original root callbacks must return exactly once')
    else:
        for row,(plugin,setup) in zip(callbacks,ROOTS):
            if (not isinstance(row,dict) or row.get('plugin')!=plugin or row.get('setupClass')!=setup
                    or row.get('wrapperInjectionReturned') is not True or row.get('codeSource')!=sources.get('cleanroom')):
                failures.append('Original early callback identity/order differs: '+plugin)
    containers=context['injectedContainers'] if context is not None else [
        'net.minecraftforge.fml.common.FMLContainer','net.minecraftforge.common.ForgeModContainer']
    if result.get('injectedContainers')!=containers:
        failures.append('Original early injected containers differ')
    transformers=result.get('transformers')
    if transformers!=(context['transformers'] if context is not None else EARLY_TRANSFORMERS):
        failures.append('Original early transformer queue identity or order differs')
    proxy_states=result.get('mixinProxyStates')
    if (not isinstance(proxy_states,list) or proxy_states!=[False,True]
            or any(type(state) is not bool for state in proxy_states)):
        failures.append('Original PREINIT and INIT Mixin proxy activation differs')
    wrappers=result.get('transformerWrappers')
    expected_wrappers=context['transformerWrappers'] if context is not None else [
        {'parent':parent,'coremod':'FMLCorePlugin','artifact':'cleanroom'} for parent in EARLY_WRAPPER_PARENTS]
    if not isinstance(wrappers,list) or len(wrappers)!=len(expected_wrappers):
        failures.append('Original early FML transformer wrapper inventory differs')
    else:
        for row,expected in zip(wrappers,expected_wrappers):
            parent=expected['parent']
            if (not isinstance(row,dict) or row.get('transformer')!='$wrapper.'+parent
                    or row.get('parent')!=parent or row.get('coremod')!=expected['coremod']
                    or row.get('codeSource')!=artifact_sources.get(expected['artifact'])):
                failures.append('Original early transformer wrapper parent, owner or CodeSource differs: '+parent)
    definitions=result.get('definitions')
    targets={'net/minecraft/util/math/Vec3d','net/minecraft/util/EnumFacing'}
    groovy=context['definitionWitness'] if context is not None else None
    if groovy is not None:targets.add(groovy['target'])
    if not isinstance(definitions,dict) or set(definitions)!=targets:
        failures.append('Actual early-safe definition witnesses missing')
    else:
        for name,row in definitions.items():
            is_groovy=groovy is not None and name==groovy['target']
            expected_source=artifact_sources.get(groovy['artifact']) if is_groovy else sources.get('minecraft')
            if (not isinstance(row,dict) or row.get('targetDefined') is not True
                    or row.get('definingLoader')!='net.minecraft.launchwrapper.LaunchClassLoader'
                    or row.get('codeSource')!=expected_source
                    or not isinstance(row.get('definitionSha256'),str)
                    or re.fullmatch('[0-9a-f]{64}',row['definitionSha256']) is None):
                failures.append('Native definition identity differs: '+name);continue
            if is_groovy:
                if (row.get('source')!=name or row.get('inputSha256')!=groovy['inputSha256']
                        or row['definitionSha256']==groovy['inputSha256']
                        or row.get('observedHook')!=groovy['hookOwner']+'.'+groovy['hookName']+groovy['hookDescriptor']
                        or row.get('initializationRequested') is not False):
                    failures.append('Original Groovy field-hook transformation or definition boundary differs')
                continue
            if descriptor is not None:
                witness=descriptor.get('witnesses',{}).get(name,{})
                if row.get('source')!=witness.get('source') or row['definitionSha256']==witness.get('inputSha256'):
                    failures.append('Raw bytes or wrong source cannot qualify native definition: '+name)
            if name.endswith('/EnumFacing'):
                access=row.get('fieldAccess',{})
                if not isinstance(access,dict) or any(type(access.get(field)) is not int or not access[field]&1
                        for field in ('field_82609_l','field_176754_o')):
                    failures.append('Original EnumFacing access transformation absent')
            elif row.get('patchedMethods')!=VEC3D_METHODS:
                failures.append('Original Vec3d SERVER patch methods absent')
    return failures


def check_pack_early_result(response, descriptor, context, source_program):
    return check_early_result(response, descriptor, context=context, source_program=source_program)


def check_selection_result(response, descriptor, context, source_program):
    """Qualify the declared pre-construction boundary, never material completion."""
    failures=[]
    if (response.get('schema')!='axiom.result.v1' or response.get('operation')!='native-selection-stage'
            or response.get('scope')!='original-server-selection-stage-not-material-initialization'
            or response.get('status')!='incomplete'):
        failures.append('Native selection envelope differs')
    result=response.get('result')
    if not isinstance(result,dict):return failures+['Native selection result is not an object']
    early=result.get('earlyStage')
    if isinstance(early,dict):
        early={**early,'executionStage':'early','candidateCompilationStarted':result.get('candidateCompilationStarted'),
               'sourceProgram':result.get('sourceProgram')}
        failures.extend(check_early_result({'schema':'axiom.result.v1','operation':'native-early-stage',
            'scope':'original-server-early-stage-not-material-initialization','status':'incomplete',
            'result':early,'diagnostics':early.get('nativeDiagnostics')},descriptor,
            context=context,source_program=source_program))
    else:failures.append('Original early prerequisite did not complete before selection')
    if (result.get('schema')!='axiom.native-selection-stage.v1' or result.get('executionStage')!='selection'
            or result.get('mixinPhase')!='DEFAULT' or result.get('loaderState')!='LOADING'
            or result.get('stage')!='selection-prefix-completed'):
        failures.append('Original pre-construction selection stage or phase differs')
    for key in ('selectionReady','selectionPrefixCompleted','constructionBoundaryReached','selectionDefinitionsVerified',
                'lateMixinSelectionQualified','launchArgumentsReturned','vanillaRegistrationReturned',
                'nativeServerHandlerInitialized','nativeDiagnosticsComplete'):
        if result.get(key) is not True:failures.append('Original selection observation missing: '+key)
    for key in ('constructionDispatched','minecraftLaunched','candidateCompilationStarted','materialInitializationComplete',
                'fullLauncherCompositionQualified','serverStartupConfirmationPerformed'):
        if result.get(key) is not False:failures.append('Selection has an unsupported later-scope claim: '+key)
    diagnostics=result.get('nativeDiagnostics')
    if (not isinstance(diagnostics,list) or response.get('diagnostics')!=diagnostics
            or any(not isinstance(row,dict) or row.get('severity')!='warning'
                   or any(not isinstance(row.get(field),str) for field in ('logger','message','trace'))
                   or row.get('locationStatus')!='unlocated' for row in diagnostics)
            or any('failure' in key.lower() for key in result)):
        failures.append('Original native selection failure or logged error remains')
    if result.get('launchArgumentCallbacks')!=result.get('executedTweaks') or not result.get('launchArgumentCallbacks'):
        failures.append('Original launch-argument callback order differs')
    failures.extend(check_server_owner(result, context))
    selection=context['selection']
    mods=result.get('selectedMods');active=result.get('activeMods')
    if (not isinstance(mods,list) or not mods or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in mods)
            or not isinstance(active,list) or any(not isinstance(value,str) for value in active)):
        failures.append('Native container or active-order observations are missing')
    else:
        ids=[row['id'] for row in mods]
        if len(ids)!=len(set(ids)) or len(active)!=len(set(active)) or active!=[i for i in ids if i in active]:
            failures.append('Native selected identities or active order differ')
        if not set(selection['requiredMods']).issubset(active):failures.append('Required native containers are inactive or missing')
        for row in mods:
            if (not all(isinstance(row.get(k),str) and row[k] for k in ('version','source','container','state'))
                    or row.get('nativeLoaded') is not (row['id'] in active)
                    or any(not isinstance(row.get(k),list) or any(not isinstance(v,str) for v in row[k])
                           for k in ('requirements','dependencies','dependants'))
                    or (row.get('container')=='net.minecraftforge.fml.common.FMLModContainer'
                        and row.get('instanceCreated') is not False)):
                failures.append('Native container state or construction boundary differs: '+row['id'])
    configs=result.get('mixinConfigurations')
    if (not isinstance(configs,list) or any(not isinstance(value,str) for value in configs)
            or not set(selection['requiredMixinConfigurations']).issubset(configs)):
        failures.append('Required original late mixin configurations are missing')
    definitions=result.get('selectionDefinitions')
    expected=selection['definitionWitnesses']
    if not isinstance(definitions,dict) or set(definitions)!={w['target'] for w in expected}:
        return failures+['Actual selection definition witnesses are missing']
    for witness in expected:
        name=witness['target'];row=definitions[name]
        early_sources=early.get('artifactCodeSources',{}) if isinstance(early,dict) else {}
        early_artifacts=early.get('nativeArtifacts',{}) if isinstance(early,dict) else {}
        source=(early_sources.get(witness['artifact']) if witness['artifact']=='cleanroom'
                else early_artifacts.get(witness['artifact'],{}).get('codeSource'))
        if (not isinstance(row,dict) or row.get('targetDefined') is not True or row.get('definedBeforeObservation') is not False
                or type(row.get('definitionCount')) is not int or row['definitionCount']!=1
                or row.get('mixinPhase')!='DEFAULT' or row.get('requiredMixinApplied') is not True
                or row.get('definingLoader')!='net.minecraft.launchwrapper.LaunchClassLoader'
                or not source or row.get('codeSource')!=source or row.get('inputCodeSource')!=source
                or not witness.get('inputSha256') or row.get('inputSha256')!=witness['inputSha256']
                or not isinstance(row.get('definitionSha256'),str) or len(row['definitionSha256'])!=64
                or any(c not in '0123456789abcdef' for c in row['definitionSha256'])
                or row['definitionSha256']==row.get('inputSha256') or 'observationFailure' in row):
            failures.append('Actual selection definition identity or phase differs: '+name);continue
        methods=row.get('methods')
        if (not isinstance(methods,list) or any(not isinstance(m,dict)
                or any(not isinstance(m.get(k),str) for k in ('name','descriptor','mixin'))
                or not isinstance(m.get('calls'),list) or any(not isinstance(c,str) for c in m['calls']) for m in methods)):
            failures.append('Selection method observations are missing: '+name);continue
        matches=[m for m in methods if m['mixin']==witness['mixin'] and m['descriptor']==witness['methodDescriptor']
                 and ('methodName' not in witness or m['name']==witness['methodName']) and witness['hook'] in m['calls']]
        if len(matches)!=1:
            failures.append('Original selected mixin hook is absent or ambiguous: '+name);continue
        if 'dispatchMethod' in witness:
            call=name.replace('.','/')+'.'+matches[0]['name']+matches[0]['descriptor']
            if not any(m['name']==witness['dispatchMethod'] and m['descriptor']==witness['dispatchDescriptor']
                       and call in m['calls'] for m in methods):
                failures.append('Original lifecycle dispatch does not call the selected mixin hook')
    return failures


def check_server_owner(result, context):
    """Exact native constructor/owner evidence and the unstarted boundary."""
    if 'serverOwner' not in context:return []
    row=result.get('nativeServerOwner')
    if not isinstance(row,dict):return ['Original server ownership evidence is missing']
    expected={'mode':'original-dedicated-server-construction',
        'serverClass':'net.minecraft.server.dedicated.DedicatedServer',
        'dataFixerClass':'net.minecraftforge.common.util.CompoundDataFixer','threadGroup':'SERVER',
        'constructorReturned':True,'nativeDefiningLoader':True,'originalOwnershipPrefixReturned':True,
        'gameThreadAbsent':True,'snooperStarted':False,'worldCount':0,
        'networkEndpointCount':0,'networkConnectionCount':0}
    failures=[]
    if context['serverOwner']!=expected['mode'] or result.get('nativeEffectiveSide')!='SERVER':
        failures.append('Original server ownership mode or effective side differs')
    for key,value in expected.items():
        if type(row.get(key)) is not type(value) or row[key]!=value:
            failures.append('Original server ownership observation differs: '+key)
    for key,artifact in [('serverArtifact','minecraft'),('dataFixerArtifact','cleanroom')]:
        source=result.get('artifactCodeSources',{}).get(artifact)
        if not isinstance(source,str) or row.get(key)!=source:
            failures.append('Original server ownership source differs: '+key)
    return failures


def check_construction_result(response, descriptor, context, source_program):
    """Retain original construction evidence until its expanded exit is accepted."""
    failures=[]
    if (response.get('schema')!='axiom.result.v1' or response.get('operation')!='native-construction-stage' or response.get('status')!='incomplete'
            or response.get('scope')!='original-server-construction-stage-not-material-initialization'):
        failures.append('Native construction envelope differs')
    result=response.get('result',{})
    selection=result.get('selectionStage') if isinstance(result,dict) else None
    if isinstance(selection,dict):
        selection={**selection,'executionStage':'selection',
                   'candidateCompilationStarted':result.get('candidateCompilationStarted'),
                   'sourceProgram':result.get('sourceProgram')}
        failures.extend(check_selection_result({'schema':'axiom.result.v1','operation':'native-selection-stage','status':'incomplete',
            'scope':'original-server-selection-stage-not-material-initialization','result':selection,
            'diagnostics':selection.get('nativeDiagnostics')},descriptor,context,source_program))
    else:failures.append('Original selection did not complete before construction')
    if isinstance(result,dict) and result.get('failure'):failures.append('Original native construction failed')
    failures.append('Construction stage alone does not establish Groovy-stage acceptance')
    return failures


def check_constructed_state(result, context):
    """Require original instances and affecting construction effects, beyond return flags."""
    failures=[]
    for field in ('constructionReady','constructionMethodReturned','constructionDispatched',
                  'constructionDispatchStarted','nativeGroovySandboxCreated'):
        if result.get(field) is not True:failures.append('Original construction observation missing: '+field)
    if result.get('loaderState')!='PREINITIALIZATION' or result.get('preInitializationDispatched') is not False:
        failures.append('Original construction crossed or did not reach its preInit boundary')
    if any(key in result for key in ('constructionObservationFailure','constructionEffectsObservationFailure')):
        failures.append('Original construction effects could not be observed')
    selected=result.get('selectedMods',[]); constructed=result.get('constructedMods')
    artifacts=result.get('nativeArtifacts',{})
    if (not isinstance(selected,list) or any(not isinstance(row,dict) for row in selected)
            or not isinstance(artifacts,dict) or not isinstance(result.get('artifactCodeSources'),dict)):
        return failures+['Original construction selection or artifact observations are malformed']
    sources={row.get('codeSource') for row in artifacts.values() if isinstance(row,dict)}
    sources.update(result.get('artifactCodeSources',{}).values());sources.discard(None)
    if (not isinstance(constructed,list) or not constructed
            or any(not isinstance(row,dict) for row in constructed)
            or [row.get('id') for row in constructed]!=[row.get('id') for row in selected]
            or any(row.get('state')!='CONSTRUCTED' or row.get('container')!=original.get('container')
                   or row.get('container')=='net.minecraftforge.fml.common.FMLModContainer' and
                   (row.get('instanceCreated') is not True or not isinstance(row.get('instanceClass'),str)
                    or not row['instanceClass'] or row.get('instanceCodeSource') not in sources
                    or Path(row['instanceCodeSource']).name!=Path(original.get('source','')).name)
                   for row,original in zip(constructed or [],selected))):
        failures.append('Original selected containers did not construct with verified native instances')
    state=result.get('nativeGroovyConstruction',{})
    bindings=state.get('bindings',{}) if isinstance(state,dict) else {}
    expected={row['class']:artifacts.get(row['artifact'],{}).get('codeSource')
              for row in context.get('construction',{}).get('groovyPlugins',[])}
    plugins=state.get('externalPlugins') if isinstance(state,dict) else None
    if (not isinstance(state,dict)
            or state.get('sandboxClass')!='com.cleanroommc.groovyscript.sandbox.GroovyScriptSandbox'
            or state.get('engineClass')!='com.cleanroommc.groovyscript.sandbox.CustomGroovyScriptEngine'
            or state.get('modSupportFrozen') is not False or state.get('scriptOwnerAssigned') is not False
            or not isinstance(bindings,dict)
            or any(bindings.get(name)!=owner for name,owner in {
                'Mods':'com.cleanroommc.groovyscript.compat.mods.ModSupport',
                'Log':'com.cleanroommc.groovyscript.sandbox.GroovyLogImpl',
                'EventManager':'com.cleanroommc.groovyscript.event.GroovyEventManager'}.items())
            or not expected or None in expected.values() or not isinstance(plugins,list)
            or any(not isinstance(row,dict) for row in plugins)
            or len(plugins)!=len(expected) or {row.get('class'):row.get('codeSource') for row in plugins or []}!=expected):
        failures.append('Original Groovy construction engine, bindings or external plugins differ')
    susy=result.get('nativeSusyConstruction',{})
    required=context.get('construction',{}).get('susyStockLoaders',[])
    if (not isinstance(susy,dict) or susy.get('irInstanceClass')!='cam72cam.immersiverailroading.ImmersiveRailroading'
            or not required or not isinstance(susy.get('irStockLoaderKeys'),list)
            or not set(required).issubset(susy['irStockLoaderKeys']) or susy.get('supercriticalMaterialModifications') is not False):
        failures.append('Original Susy construction effects are missing')
    return failures


def check_groovy_result(response, descriptor, context, source_program):
    """Check the original construction/Groovy boundary; material scope stays incomplete."""
    failures=[]
    if (response.get('schema')!='axiom.result.v1' or response.get('operation')!='native-groovy-stage'
            or response.get('status')!='incomplete'
            or response.get('scope')!='original-server-groovy-stage-not-material-initialization'):
        failures.append('Native Groovy envelope differs')
    result=response.get('result',{})
    if not isinstance(result,dict):return failures+['Native Groovy result is not an object']
    construction=result.get('constructionStage')
    if isinstance(construction,dict):
        prior={**construction,'executionStage':'construction','sourceProgram':result.get('sourceProgram')}
        earlier=check_construction_result({'schema':'axiom.result.v1','operation':'native-construction-stage',
            'status':'incomplete','scope':'original-server-construction-stage-not-material-initialization','result':prior},
            descriptor,context,source_program)
        failures.extend(message for message in earlier if message!='Construction stage alone does not establish Groovy-stage acceptance')
        failures.extend(check_constructed_state(construction,context))
        if construction.get('constructionReady') is not True or construction.get('constructionMethodReturned') is not True:
            failures.append('Original construction did not return before Groovy initialization')
    else:failures.append('Original construction evidence is missing before Groovy initialization')
    boot=result.get('foundationBootstrap',{})
    if (not isinstance(boot,dict) or boot.get('method')!='top.outlands.foundation.boot.Foundation#breakModuleAndReflection'
            or boot.get('methodReturned') is not True or boot.get('nativeClassLoaderCreated') is not False):
        failures.append('Original Foundation VM preparation did not precede native classloader construction')
    for key in ('groovyInitializationReady','groovyInitializationReturned','nativeMapperAdmissionBound','nativeDiagnosticsComplete'):
        if result.get(key) is not True:failures.append('Original Groovy observation missing: '+key)
    state=result.get('nativeGroovyInitialization',{})
    if (not isinstance(state,dict) or state.get('modSupportFrozen') is not True
            or state.get('scriptOwnerIsOriginalContainer') is not True or state.get('scriptOwner')!='supersymmetry'
            or state.get('scriptOwnerAssigned') is not True
            or state.get('scriptOwnerClass')!='net.minecraftforge.fml.common.InjectedModContainer'):
        failures.append('Original Groovy compatibility or script ownership differs')
    if (result.get('candidateCompilationStarted') is not True or result.get('candidateAdmissionViolations')!=[]
            or result.get('candidateResourceFailure') is not False or result.get('candidateLinkageFailure') is not False
            or result.get('nativeGroovyErrors')!=[]):
        failures.append('Saved Groovy compilation or native execution did not complete cleanly')
    if result.get('preInitializationDispatched') is not False or result.get('materialInitializationComplete') is not False:
        failures.append('Groovy prefix claims a later mod initialization scope')
    console=result.get('nativeConsole',{})
    if not isinstance(console,dict) or console.get('complete') is not True:
        failures.append('Original native console evidence exceeded its retained bound')
    if result.get('failure') or result.get('groovyObservationFailure'):
        failures.append('Original Groovy initialization failed or its observation is incomplete')
    diagnostics=result.get('nativeDiagnostics')
    groovy_diagnostics=result.get('groovyDiagnostics')
    if (not isinstance(diagnostics,list)
            or any(not isinstance(row,dict) or row.get('severity')!='warning' for row in diagnostics)):
        failures.append('Original Groovy initialization logged native errors')
    if (not isinstance(groovy_diagnostics,list)
            or any(not isinstance(row,dict) or row.get('severity')!='warning' for row in groovy_diagnostics)
            or result.get('groovyLoggedError') is not False or result.get('groovyCompilationFailure') is not False):
        failures.append('Original Groovy diagnostic channels are incomplete or contain native errors')
    if (not isinstance(diagnostics,list) or not isinstance(groovy_diagnostics,list)
            or response.get('diagnostics')!=diagnostics+groovy_diagnostics):
        failures.append('Original Groovy diagnostic channels were not preserved in the envelope')
    scripts=result.get('nativeScriptIndex')
    if (not isinstance(scripts,list) or not scripts or any(not isinstance(row,dict)
            or not isinstance(row.get('path'),str) or not row['path']
            or row.get('classDefined') is not True or row.get('preprocessorCheckFailed') is not False for row in scripts)):
        failures.append('Original Groovy indexed script coverage is incomplete')
    return failures


def expand_stage_evidence(response, *, diagnostics_pointer='/diagnostics'):
    """Expand the lossless stage representation without changing the retained receipt."""
    result=response.get('result',{})
    if not isinstance(result,dict) or 'snapshotEncoding' not in result:return response
    result=dict(result);encoding=result.pop('snapshotEncoding')
    if (encoding.get('schema')!='axiom.native-stage-snapshots.v1'
            or encoding.get('diagnosticsPointer')!=diagnostics_pointer):
        raise ValueError('Native snapshot encoding differs')
    native_count=encoding['nativeDiagnosticCount'];groovy_count=encoding['groovyDiagnosticCount']
    diagnostics=response.get('diagnostics')
    if (not isinstance(diagnostics,list) or not isinstance(native_count,int) or not isinstance(groovy_count,int)
            or native_count<0 or groovy_count<0 or native_count+groovy_count>len(diagnostics)
            or encoding.get('diagnosticEnvelopeCount',native_count+groovy_count)!=len(diagnostics)):
        raise ValueError('Native diagnostic reference is incomplete')
    if encoding.get('nativeDiagnosticsPresent'):result['nativeDiagnostics']=diagnostics[:native_count]
    if encoding.get('groovyDiagnosticsPresent'):result['groovyDiagnostics']=diagnostics[native_count:native_count+groovy_count]
    if 'fingerprintEncoding' in encoding:
        if encoding['fingerprintEncoding']!='base64url-sha256':raise ValueError('Native fingerprint encoding differs')
        if isinstance(result.get('registrationEffects'),dict):
            effects=dict(result['registrationEffects'])
            for name in ('materials','fluids','prefixItems','materialBlocks','oreBlocks'):
                catalog=effects.get(name)
                if not isinstance(catalog,dict) or not isinstance(catalog.get('entries'),dict):continue
                entries={}
                for identity,encoded in catalog['entries'].items():
                    digest=base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4))
                    if len(digest)!=32:raise ValueError('Native catalog fingerprint differs')
                    entries[identity]=digest.hex()
                effects[name]={**catalog,'entries':entries}
            result['registrationEffects']=effects
    if 'consoleEncoding' in encoding:
        if encoding['consoleEncoding']!='gzip-base64-json':raise ValueError('Native console encoding differs')
        console=dict(result['nativeConsole'])
        with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(console.pop('textGzipBase64'),validate=True))) as source:
            raw=source.read()
        text=json.loads(raw)
        if set(text)!={'head','tail'} or not all(isinstance(value,str) for value in text.values()):
            raise ValueError('Native console text differs')
        result['nativeConsole']={**console,**text}
    stages=('earlyStage','selectionStage','constructionStage','groovyBoundary');expanded=set();visiting=set()
    def expand(stage):
        if stage in expanded:return
        if stage in visiting:raise ValueError('Native snapshot reference cycle')
        visiting.add(stage);snapshot=result[stage];value={}
        for key in snapshot['inheritedKeys']:
            if key not in result:raise ValueError('Native snapshot reference is missing: '+key)
            if key in stages:expand(key)
            value[key]=result[key]
        value.update(snapshot['values']);result[stage]=value;expanded.add(stage);visiting.remove(stage)
    for stage in stages:
        if stage in result:expand(stage)
    return {**response,'result':result}


def expand_execution_evidence(execution):
    """Restore native observations while retaining the original production receipt."""
    native=execution.get('nativeInitialization')
    if not isinstance(native,dict) or 'snapshotEncoding' not in native:return execution
    native=dict(native);fields=native['snapshotEncoding'].get('executionFields',[])
    for name in fields:
        if name not in ('registrationEffects','customMetaItems','materials','lookups','missingMaterials',
                        'vocabulary','prefixItems','materialBlocks','materialOres','deferredWork','selectedObservations') or name not in execution:
            raise ValueError('Native execution collection is unavailable: '+str(name))
        native[name]=execution[name]
    native=expand_stage_evidence({'result':native,'diagnostics':execution['diagnostics']},
                                diagnostics_pointer='/execution/diagnostics')['result']
    return {**execution,**{name:native[name] for name in fields},'nativeInitialization':native}


def check_preinit_effects(result):
    """Verify the source-bound current collections, not complete saved-scope validity."""
    effects=result.get('registrationEffects')
    if not isinstance(effects,dict) or effects.get('schema')!='axiom.native-registration-effects.v1':
        return ['Native preInit effect inventory is unavailable']
    failures=[]
    if effects.get('phase')!='FROZEN':failures.append('Native material effect checkpoint is not FROZEN')
    catalogs={
        'materials':('native-material-registry','native-material-observation-selected-property-values-v2'),
        'fluids':('native-forge-fluid-registry','native-fluid-default-scalars-v1'),
        'prefixItems':('native-meta-prefix-item-collections','original-prefix-item-variants-and-registry-identities-v1'),
        'materialBlocks':('native-material-block-collections','original-block-item-state-property-and-unifier-identities-v1'),
        'oreBlocks':('native-ore-block-collection','original-ore-stone-variants-and-block-item-registry-identities-v1')}
    counts={}
    for name,(membership,scope) in catalogs.items():
        catalog=effects.get(name,{});entries=catalog.get('entries') if isinstance(catalog,dict) else None
        if (not isinstance(catalog,dict) or catalog.get('status')!='observed' or catalog.get('inventoryComplete') is not True
                or catalog.get('membership')!=membership or catalog.get('stateScope')!=scope
                or not isinstance(entries,dict) or not entries
                or any(not isinstance(k,str) or not k or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v)
                       for k,v in entries.items())):
            failures.append('Complete native preInit catalog is unavailable or differs: '+name)
        else:counts[name]=len(entries)
    registries=result.get('nativeMaterialRegistries',{});rows=registries.get('registries',[]) if isinstance(registries,dict) else []
    if (not isinstance(registries,dict) or registries.get('status')!='observed' or registries.get('phase')!='FROZEN'
            or not isinstance(rows,list) or not rows
            or any(not isinstance(row,dict) or type(row.get('registeredMaterials')) is not int
                   or row['registeredMaterials']<0 for row in rows)
            or registries.get('totalRegisteredMaterials')!=counts.get('materials')
            or sum(row['registeredMaterials'] for row in rows)!=counts.get('materials')):
        failures.append('Native named material registry and complete catalog differ')
    bindings=effects.get('materialFluidBindings',{})
    if (not isinstance(bindings,dict) or bindings.get('status')!='observed' or bindings.get('bindingsComplete') is not True
            or bindings.get('affectingGaps')!=[] or bindings.get('fingerprintedIn')!='materials'
            or type(bindings.get('materialsWithFluidProperty')) is not int
            or not 0<bindings['materialsWithFluidProperty']<=counts.get('materials',0)):
        failures.append('Native material-to-fluid storage and Forge bindings are incomplete')
    custom=result.get('customMetaItems',{});items=custom.get('items') if isinstance(custom,dict) else None
    reference=effects.get('customItems',{})
    if (not isinstance(reference,dict) or reference.get('status')!='reference'
            or reference.get('sourcePointer') not in ('/result/customMetaItems','/execution/customMetaItems')
            or not isinstance(custom,dict) or custom.get('status')!='observed' or not isinstance(items,list) or not items):
        failures.append('Native custom-item collection or reference is unavailable')
    else:
        owners=set()
        for item in items:
            if (not isinstance(item,dict) or not isinstance(item.get('registryName'),str) or not item['registryName']
                    or item['registryName'] in owners or item.get('forgeRegistered') is not True
                    or item.get('nativeClassSpace') is not True or not isinstance(item.get('variants'),list)):
                failures.append('Original custom-item owner or Forge membership differs');continue
            owners.add(item['registryName']);metas=set()
            for variant in item['variants']:
                if (not isinstance(variant,dict) or type(variant.get('meta')) is not int or variant['meta'] in metas
                        or not isinstance(variant.get('name'),str) or variant.get('ownerIdentity') is not True
                        or type(variant.get('nameLookupIdentity')) is not bool):
                    failures.append('Original custom-item variant identity is incomplete');continue
                metas.add(variant['meta'])
    witnesses=result.get('nativeMaterialWitnesses',[])
    if (not isinstance(witnesses,list) or {row.get('name') for row in witnesses if isinstance(row,dict)}!={'gregtech:iron','gregtech:diamond'}
            or any(not isinstance(row,dict) or row.get('registryIdentity') is not True
                   or not isinstance(row.get('nativePropertyState'),dict)
                   or row['nativePropertyState'].get('schema')!='axiom.native-material-property-state.v1' for row in witnesses)):
        failures.append('Selected native material/property witnesses are incomplete')
    def maps(value):
        if isinstance(value,dict):
            yield value
            for child in value.values():yield from maps(child)
        elif isinstance(value,list):
            for child in value:yield from maps(child)
    for name in ('prefixItems','materialBlocks','oreBlocks'):
        catalog=effects.get(name,{});witnesses=catalog.get('witnesses') if isinstance(catalog,dict) else None
        if (not isinstance(witnesses,list) or not witnesses
                or any(not isinstance(row,dict) or row.get('material') not in ('gregtech:iron','gregtech:diamond')
                       or not isinstance(row.get('generated'),list) for row in witnesses)
                or any(any(key in row and row[key] is not True for key in
                           ('registryIdentity','blockItemIdentity','stateRoundTripIdentity','propertyRoundTripIdentity'))
                       or row.get('empty') is False and row.get('registryIdentity') is not True for row in maps(witnesses))):
            failures.append('Original generated-form registry/state witnesses are incomplete: '+name)
    # Numeric/name shadows and currently absent generated unifier lookups are
    # original native facts. Do not reject or repair them as R4 observation gaps.
    return list(dict.fromkeys(failures))


def check_biome_registrations(result):
    """Require original configured memberships and artifact identities, not a count."""
    observed=result.get('nativeBiomes',{})
    if (not isinstance(observed,dict) or observed.get('schema')!='axiom.native-biome-registrations.v1'
            or observed.get('status')!='observed' or observed.get('observationsComplete') is not True
            or observed.get('affectingGaps')!=[] or observed.get('configurationDirectoryIdentity') is not True
            or observed.get('configurationPath')!='config/biomesoplenty/biome_ids.json'
            or observed.get('serverProxy')!='biomesoplenty.core.CommonProxy' or observed.get('modState')!='PREINITIALIZED'):
        return ['Original configured biome observation is incomplete']
    ids=observed.get('configuredIds'); rows=observed.get('biomes'); disabled=observed.get('disabledBiomes')
    if (not isinstance(ids,dict) or not ids or any(not isinstance(k,str) or type(v) is not int for k,v in ids.items())
            or not isinstance(rows,dict) or not rows or not isinstance(disabled,list)):
        return ['Original configured biome inventory is unavailable']
    if (set(rows)!={'biomesoplenty:'+k for k,v in ids.items() if v>=0}
            or disabled!=sorted('biomesoplenty:'+k for k,v in ids.items() if v<0)):
        return ['Original configured biome membership differs']
    source=result.get('nativeArtifacts',{}).get('mods/biomes-o-plenty.pw.toml',{}).get('codeSource')
    if (not isinstance(source,str) or not source or any(not isinstance(row,dict)
            or row.get('codeSource')!=source or not str(row.get('class','')).startswith('biomesoplenty.')
            or any(row.get(key) is not True for key in ('nativeClassLoaderIdentity','nativeForgeRegistryIdentity','nativePresentBiomeIdentity'))
            or not isinstance(row.get('dictionaryTypes'),list) for row in rows.values())):
        return ['Original biome artifact, class space or registry identity differs']
    return []


def check_worldgen_biome_bindings(result, expected=None):
    """Optionally bind all observed biome-map functions to independent saved JSON."""
    observed=result.get('nativeWorldgenBiomeBindings',{})
    if (not isinstance(observed,dict) or observed.get('schema')!='axiom.native-worldgen-biome-bindings.v1'
            or observed.get('status')!='observed' or observed.get('observationsComplete') is not True
            or observed.get('affectingGaps')!=[] or not isinstance(observed.get('definitions'),dict)):
        return ['Original worldgen biome bindings are incomplete']
    rows=observed['definitions']
    if expected is not None and set(rows)!=set(expected):return ['Saved and native worldgen biome-map definitions differ']
    for path,row in rows.items():
        if (not isinstance(row,dict) or row.get('nativeDefinitionMembership') is not True
                or not isinstance(row.get('biomeWeights'),dict)):
            return ['Original worldgen definition membership differs']
        weights=row['biomeWeights']
        if expected is not None and set(weights)!=set(expected[path]):return ['Saved and native biome-map keys differ']
        for name,weight in weights.items():
            if (not isinstance(weight,dict) or weight.get('nativeForgeRegistryIdentity') is not True
                    or type(weight.get('configuredWeight')) is not int or type(weight.get('nativeWeight')) is not int
                    or weight['configuredWeight']!=weight['nativeWeight']
                    or expected is not None and weight['nativeWeight']!=expected[path][name]):
                return ['Original biome-map function differs from saved configuration']
    return []


def check_preinit_result(response, descriptor, context, source_program):
    """Verify original preInit and current effects without certifying the full saved scope."""
    failures=[]
    try:response=expand_stage_evidence(response)
    except (ValueError,KeyError,TypeError):return ['Native preInit snapshot evidence is incomplete']
    if (response.get('schema')!='axiom.result.v1' or response.get('operation')!='native-preinit-stage'
            or response.get('status')!='incomplete'
            or response.get('scope')!='original-server-preinit-stage-not-material-initialization'):
        failures.append('Native preInit envelope differs')
    result=response.get('result',{})
    if not isinstance(result,dict):return failures+['Native preInit result is not an object']
    boundary=result.get('groovyBoundary')
    if isinstance(boundary,dict):
        # Validate the recorded earlier boundary separately from later failures;
        # the complete original failure remains in the preInit result below.
        earlier={key:value for key,value in result.items() if key!='failure'}
        earlier.update(boundary)
        failures.extend(check_groovy_result({'schema':'axiom.result.v1','operation':'native-groovy-stage',
            'status':'incomplete','scope':'original-server-groovy-stage-not-material-initialization',
            'result':earlier,'diagnostics':earlier.get('nativeDiagnostics',[])+earlier.get('groovyDiagnostics',[])},
            descriptor,context,source_program))
    else:failures.append('Original Groovy boundary was not observed before mod preInit')
    for key in ('preInitializationReady','preInitializationReturned','preInitializationDispatchStarted',
                'preInitializationDispatched','preInitializationDispatchReturned','nonRecipeRegistryEventsReturned'):
        if result.get(key) is not True:failures.append('Original preInit observation missing: '+key)
    if result.get('modPreInitDispatchCount')!=1 or result.get('loaderState')!='INITIALIZATION':
        failures.append('Original preInit dispatch count or final state differs')
    if (result.get('failure') or result.get('groovyObservationFailure') or result.get('groovyLoggedError') is not False
            or result.get('nativeGroovyErrors')!=[] or result.get('candidateAdmissionViolations')!=[]
            or result.get('candidateResourceFailure') is not False or result.get('candidateLinkageFailure') is not False):
        failures.append('Original preInit failed or its observations are incomplete')
    rows=result.get('preInitializedMods')
    if (not isinstance(rows,list) or not rows or any(not isinstance(row,dict) or row.get('state')!='PREINITIALIZED' for row in rows)
            or [row.get('id') for row in rows]!=[row.get('id') for row in result.get('selectedMods',[])]):
        failures.append('Original selected mods did not all reach PREINITIALIZED')
    diagnostics=result.get('nativeDiagnostics'); groovy_diagnostics=result.get('groovyDiagnostics')
    if (not isinstance(diagnostics,list) or not isinstance(groovy_diagnostics,list)
            or any(not isinstance(row,dict) or row.get('severity')!='warning' for row in diagnostics+groovy_diagnostics)
            or response.get('diagnostics')!=diagnostics+groovy_diagnostics):
        failures.append('Original preInit diagnostics contain errors or are incomplete')
    if result.get('materialInitializationComplete') is not False or result.get('minecraftLaunched') is not False:
        failures.append('PreInit observation claims an unverified scope')
    failures.extend(check_server_owner(result, context))
    failures.extend(check_preinit_effects(result))
    if 'biomesoplenty' in context.get('selection',{}).get('requiredMods',[]):
        failures.extend(check_biome_registrations(result))
    return failures


def check_recipe_result(response, descriptor, context, source_program):
    """Verify the owning engine's complete native recipe evidence assessment."""
    result=response.get('result',{})
    failures=[]
    if (result.get('schema')!='axiom.native-recipe-stage.v1'
            or result.get('recipeInitializationStarted') is not True
            or result.get('recipeInitializationReturned') is not True
            or result.get('recipeInitializationReady') is not True
            or result.get('loaderState')!='AVAILABLE'):
        failures.append('Original recipe initialization did not complete')
    if (result.get('recipeScopeGaps') != [] or result.get('effectiveRecipeRegistryObserved') is not True
            or result.get('recipeScopeStatus') not in ('completed', 'native-failed')):
        failures.append('Effective native recipe registry evidence is incomplete')
    for key in ('nativeStoredRecipes', 'nativeStoredCraftingRecipes', 'nativeStoredFurnaceRecipes'):
        stored = result.get(key, {})
        if (stored.get('status') != 'observed' or stored.get('storedValuesComplete') is not True
                or stored.get('affectingGaps') != []):
            failures.append('Complete stored native recipe values are unavailable: ' + key)
    if 'biomesoplenty' in context.get('selection',{}).get('requiredMods',[]):
        failures.extend(check_biome_registrations(result))
    failures.extend(check_worldgen_biome_bindings(result))
    failures.extend(check_server_owner(result, context))
    if result.get('minecraftLaunched') is not False:
        failures.append('Recipe observation claims an unverified scope')
    return failures

def build(java,engine,libraries,server,cleanroom,foundation,output,stage='roots',
          native_context='root-only',addon_inventory=None,pack=None,program_archive=None,*,assemble_only=False):
    if stage not in ('roots','early','selection','construction','groovy','preinit','recipes'):raise ValueError('Unadmitted native execution stage')
    if native_context not in ('root-only','supersymmetry:required-early'):
        raise ValueError('Unknown native early context')
    prepared=None; context={}; program_context=None
    if native_context!='root-only':
        if stage not in ('early','selection','construction','groovy','preinit','recipes') or any(path is None for path in (addon_inventory,pack,program_archive)):
            raise ValueError('Required context needs stage early or selection, addon inventory, pack and complete program archive')
        from axiom_native_early_inputs import prepare_inputs
        context=json.loads(PACK_PROFILE.read_bytes())
        prepared=prepare_inputs(addon_inventory,pack,program_archive,context)
        context['artifacts']=prepared['artifacts']
        witness=context['definitionWitness']
        artifact=next(row for row in prepared['artifacts'] if row['descriptor']==witness['artifact'])
        with zipfile.ZipFile(prepared['nativeHomeFiles'][artifact['outputPath']]) as archive:
            witness['inputSha256']=sha256(archive.read(witness['target']+'.class')).hexdigest()
        program_context=next(row for row in json.loads(PROGRAM_CONTEXTS.read_bytes())['contexts']
                             if row['id']=='supersymmetry:material-authoring-pack')
    elif any(path is not None for path in (addon_inventory,pack,program_archive)):
        raise ValueError('Root-only context does not consume pack input')
    if stage in ('selection','construction','groovy','preinit','recipes') and prepared is None:raise ValueError('Native selection requires the explicit pack context')
    java,engine,libraries,server,cleanroom,foundation=[ordinary(p).resolve(strict=True) for p in (java,engine,libraries,server,cleanroom,foundation)]
    output=ordinary(output).absolute()
    if output.exists():raise ValueError('Native root output must be new')
    policy_raw=POLICY.read_bytes();library_raw=LIBRARIES.read_bytes();lock_raw=LOCK.read_bytes()
    policy=json.loads(policy_raw);library_policy=json.loads(library_raw);lock=json.loads(lock_raw)
    verify_policy(policy,library_policy,lock,library_raw)
    originals=verify_sources({'cleanroom':cleanroom,'foundation':foundation},lock)
    jvm=verify_runtime(java,compiler=True);engine_raw,_,engine_jars=engine_inputs(engine,ENGINE_LOCK.read_bytes())
    universal=checked_path(libraries,policy['inputs'][0]);server_pin=policy['inputs'][1]
    if prepared is not None:
        for witness in context['selection']['definitionWitnesses']:
            artifact_path=universal
            if witness['artifact']!='cleanroom':
                artifact=next(row for row in prepared['artifacts'] if row['descriptor']==witness['artifact'])
                artifact_path=prepared['nativeHomeFiles'][artifact['outputPath']]
            with zipfile.ZipFile(artifact_path) as archive:
                witness['inputSha256']=sha256(archive.read(witness['target'].replace('.','/')+'.class')).hexdigest()
    if (server.stat().st_size!=server_pin['size'] or sha256(server.read_bytes()).hexdigest()!=server_pin['sha256']
            or sha1(server.read_bytes()).hexdigest()!=server_pin['sha1']):raise ValueError('Original raw SERVER bytes differ')
    native_jars=[checked_path(libraries,row) for row in library_policy['libraries']]
    with zipfile.ZipFile(universal) as archive:
        for name in RESOURCES:
            if not archive.read(name):raise ValueError('Required original root resource is empty: '+name)
    source_paths=[POLICY,LIBRARIES,LOCK,BUILDER,PROBE,CONSOLE,ORIGINAL_PROGRAM,SNAPSHOTS,HELPER,PREFIX,EARLY_PREFIX,EARLY_HELPER,
                  SELECTION_PREFIX,SELECTION_HELPER,CONSTRUCTION_HELPER,CONFIGURATION_OBSERVATIONS,SELECTED_OBSERVATIONS,GROOVY_PREFIX,GROOVY_HELPER,GROOVY_OBSERVATIONS,
                  *EFFECT_OBSERVATIONS,FOUNDATION_BOOTSTRAP,*ADMISSION_SOURCES,Path(__file__),
                  ROOT/'modules/axiom/src/workbench_axiom/native_assembly.py',
                  ROOT/'modules/axiom'/native_assembly.SCANNER_SOURCE,
                  ROOT/'api/src/workbench_api/resources.py']
    # Bind the same packaged compilation closure consumed below, including
    # helpers newly referenced across the isolated native classloader boundary.
    source_paths += [path for paths in native_assembly.source_groups().values() for path in paths]
    if prepared is not None:
        source_paths += [PACK_PROFILE,PROGRAM_CONTEXTS,EARLY_INPUTS,ROOT/'modules/axiom/src/workbench_axiom/material_checks.py',
                         ROOT/'tools/build_axiom_addon_inventory.py',ROOT/'tools/axiom_material_program_sources.py',
                         ROOT/'modules/axiom/sources/supersymmetry.lock.json']
    admission_raw=None
    if stage in ('groovy','preinit','recipes'):
        import importlib.util
        profile_path=PROGRAM_CONTEXTS.with_name('axiom.py')
        template=PROGRAM_CONTEXTS.with_name('axiom-material-admission.json')
        spec=importlib.util.spec_from_file_location('native_groovy_profile_policy',profile_path)
        profile=importlib.util.module_from_spec(spec);spec.loader.exec_module(profile)
        admission_raw=profile.bind_material_admission(template.read_bytes(),program_context['id'])
        source_paths += [profile_path,template]
    frozen={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in source_paths}
    output.mkdir(parents=True);logs=output/'logs';logs.mkdir();steps=[]
    record={'schema':'axiom.native-root-stage-build.v1','status':'failed','steps':steps,'sourceInputs':frozen,
            'jvm':jvm,'engineSha256':sha256(engine_raw).hexdigest(),'minecraftLaunched':False,
            'materialInitializationComplete':False,'sourceReferences':len(originals),'executionStage':stage,'nativeContext':native_context}
    environment={'LANG':'C.UTF-8','PATH':os.environ.get('PATH','/usr/bin:/bin')}
    def call(name,command,work):
        started=time.monotonic()
        try:process=subprocess.run(list(map(str,command)),cwd=work,env=environment,capture_output=True)
        except subprocess.TimeoutExpired as failure:
            (logs/(name+'.stdout')).write_bytes(failure.stdout or b'');(logs/(name+'.stderr')).write_bytes(failure.stderr or b'')
            steps.append({'name':name,'seconds':round(time.monotonic()-started,3),'failure':'timeout'});raise
        (logs/(name+'.stdout')).write_bytes(process.stdout);(logs/(name+'.stderr')).write_bytes(process.stderr)
        steps.append({'name':name,'seconds':round(time.monotonic()-started,3),'exitCode':process.returncode})
        if process.returncode:raise RuntimeError(name+' failed: '+process.stderr.decode(errors='replace')[-6000:])
        return process
    try:
        with tempfile.TemporaryDirectory(prefix='axiom-native-root-build-') as temporary:
            work=Path(temporary);probe=work/'probe';probe.mkdir()
            dependencies=[*engine_jars,universal,*native_jars]
            compiled=native_assembly.compile_inputs(java,dependencies,work,call)
            builder,host=compiled['builder'],compiled['host']
            if any(frozen.get(path)!=digest for path,digest in compiled['sourceInputs'].items()):
                raise ValueError('Packaged assembly sources differ from the selected source freeze')
            def compile_group(name,destination,sources,classpath):
                call(name,[java/'bin/javac','--release','25','-proc:none','-encoding','UTF-8','-cp',classpath,'-d',destination,*sources],work)
            if not assemble_only:
                compile_group('compile-probe',probe,[PROBE,CONSOLE],os.pathsep.join(map(str,engine_jars)))
            generated=work/'generated'
            native_assembly.build_server_witnesses(java,builder,dependencies,server,universal,generated,call)
            package=output/'runtime'
            native_files={} if prepared is None else {name:path for name,path in prepared['nativeHomeFiles'].items() if name.startswith('mods/')}
            classpath=native_assembly.stage_runtime(host,generated/'root-class-space.json',[universal,*native_jars],
                server,libraries,package,native_files,admission_raw,maven_layout=stage!='roots')
            if not assemble_only:
                jar_bytes(output/'root-stage-probe.jar',{p.relative_to(probe).as_posix():p.read_bytes() for p in probe.rglob('*.class')})
            if prepared is not None:
                shutil.copyfile(program_archive,package/'saved-program.zip')
            files=[{'path':p.relative_to(package).as_posix(),'size':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()}
                   for p in sorted(package.rglob('*')) if p.is_file()]
            program={'schema':'axiom.native-root-stage-runtime.v1','side':'SERVER','inputStage':'raw-original-artifacts',
                     'executionStage':stage,
                     'files':files,'classpath':classpath,'sourceInputs':frozen,'policySha256':sha256(policy_raw).hexdigest(),
                     'sourceLockSha256':sha256(lock_raw).hexdigest(),'engineSha256':sha256(engine_raw).hexdigest()}
            if prepared is not None:
                program.update(nativeContext=context,programContext=program_context,sourceProgram=prepared['sourceProgram'],
                               programArchive='saved-program.zip',inputBindings=prepared['bindings'])
                record['inputBindings']=prepared['bindings']
            (package/'program.json').write_text(json.dumps(program,indent=2)+'\n')
            if assemble_only:
                record.update(status='assembled-unqualified-native-inputs',failures=[],
                              runtimeSha256=sha256((package/'program.json').read_bytes()).hexdigest(),
                              descriptorSha256=sha256((package/'root-class-space.json').read_bytes()).hexdigest())
            else:
                run=call('native-root-stage',[java/'bin/java','-cp',
                         os.pathsep.join(map(str,[output/'root-stage-probe.jar',*engine_jars])),
                         'research.orthrus.axiom.NativeRootStageProbe',package],work)
                response=json.loads(run.stdout)
                checker=check_early_result if stage=='early' else check_result
                descriptor=json.loads((package/'root-class-space.json').read_bytes())
                failures=(check_recipe_result(response,descriptor,context,prepared['sourceProgram']) if stage=='recipes'
                          else check_preinit_result(response,descriptor,context,prepared['sourceProgram']) if stage=='preinit'
                          else check_groovy_result(response,descriptor,context,prepared['sourceProgram']) if stage=='groovy'
                          else check_construction_result(response,descriptor,context,prepared['sourceProgram']) if stage=='construction'
                          else check_selection_result(response,descriptor,context,prepared['sourceProgram']) if stage=='selection'
                          else check_pack_early_result(response,descriptor,context,prepared['sourceProgram']) if prepared is not None
                          else checker(response,descriptor))
                record.update(response=response,failures=failures,outputBytes=len(run.stdout),
                              runtimeSha256=sha256((package/'program.json').read_bytes()).hexdigest(),
                              descriptorSha256=sha256((package/'root-class-space.json').read_bytes()).hexdigest())
            for row in files:checked_path(package,row)
            for n,digest in frozen.items():
                if sha256((ROOT/n).read_bytes()).hexdigest()!=digest:raise ValueError('Root source changed during execution: '+n)
            if prepared is not None:
                checked_path(package,{'path':'saved-program.zip',**prepared['bindings']['programArchive']})
                for row in prepared['artifacts']:
                    checked_path(package,{'path':'native-home/'+row['outputPath'],'size':row['size'],'sha256':row['sha256']})
            if not assemble_only:
                record['status']='failed' if failures else 'passed-bounded-native-'+('root' if stage=='roots' else stage)+'-stage'
    except BaseException as failure:
        record['failure']={'type':type(failure).__name__,'message':str(failure)[:8192]};raise
    finally:
        (output/'build.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def build_from_runtime(java, engine, runtime_home, program_archive, cleanroom, foundation, output, stage):
    """Build stage fixtures from Core's verified production assembly, without acquisition.

    The production assembly owns the original artifacts, scanner and host. This
    adapter only packages the existing stage probe and a complete saved program.
    """
    from workbench_axiom.material_checks import archive_inventory, source_acknowledgement
    java, engine, runtime_home, program_archive = [ordinary(Path(p)).resolve(strict=True)
        for p in (java, engine, runtime_home, program_archive)]
    if stage not in ('early', 'selection', 'groovy'):
        raise ValueError('Core runtime fixtures support early, selection and Groovy conformance')
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Native root output must be new')
    jvm = verify_runtime(java, compiler=True)
    engine_raw, _, jars = engine_inputs(engine, ENGINE_LOCK.read_bytes())
    runtime_raw = (runtime_home / 'runtime.json').read_bytes()
    runtime = json.loads(runtime_raw)
    inputs = native_assembly.assembly_inputs(engine, java, 'supersymmetry', 'cleanroom',
                                            'supersymmetry:material-authoring-pack')
    receipt = json.loads((runtime_home / 'native-assembly.json').read_bytes())
    if (receipt['inputs'] != inputs or runtime['recipeInputs'] != inputs['sourceInputs']
            or runtime['runtimeInputs'] != inputs['runtimeInputs']
            or runtime['context'] != inputs['context']
            or runtime['engineBuildInputSha256'] != sha256(engine_raw).hexdigest()):
        raise ValueError('Core runtime assembly differs from current selected inputs; prepare it again through Core')
    for row in runtime['files']:
        checked_path(runtime_home, row)
    context = runtime['nativeInitialization']['nativeContext']
    if context.get('id') != 'supersymmetry:required-early':
        raise ValueError('Core runtime must contain the required original native context')
    originals = verify_sources({'cleanroom': Path(cleanroom), 'foundation': Path(foundation)}, json.loads(LOCK.read_bytes()))
    source = source_acknowledgement(archive_inventory(program_archive))
    frozen = {**inputs['sourceInputs'], **{str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
              for path in (PROBE, CONSOLE, LOCK, POLICY, Path(__file__), ROOT/'tools/axiom_runtime.py')}}
    output.mkdir(parents=True)
    logs = output/'logs'; logs.mkdir()
    record = {'schema':'axiom.native-root-stage-build.v1', 'status':'failed', 'steps':[],
        'sourceInputs':frozen, 'jvm':jvm, 'engineSha256':sha256(engine_raw).hexdigest(),
        'minecraftLaunched':False, 'materialInitializationComplete':False,
        'sourceReferences':len(originals), 'executionStage':stage, 'nativeContext':context['id'],
        'preparedRuntimeSha256':sha256(runtime_raw).hexdigest()}
    def call(name, argv, cwd):
        from threading import Event
        from workbench_api.processes import execute_process
        from workbench_core.host_services import install_local_host_services
        install_local_host_services()
        started = time.monotonic()
        result = execute_process(list(map(str, argv)), cwd=cwd, stdin=b'', environment={'LANG':'C.UTF-8'},
            cancelled=Event(), timeout_seconds=None, output_limit=None, input_limit=None)
        (logs/(name+'.stdout')).write_bytes(result.stdout)
        (logs/(name+'.stderr')).write_bytes(result.stderr)
        record['steps'].append({'name':name,'seconds':round(time.monotonic()-started,3),'exitCode':result.exit_code})
        if result.exit_code:
            raise ValueError(name+' failed; see retained logs')
        return result
    try:
        probe = output/'probe-classes'; probe.mkdir()
        call('compile-probe', [java/'bin/javac','--release','25','-proc:none','-encoding','UTF-8',
             '-cp',os.pathsep.join(map(str,jars)),'-d',probe,PROBE,CONSOLE], output)
        jar_bytes(output/'root-stage-probe.jar', {p.relative_to(probe).as_posix():p.read_bytes() for p in probe.rglob('*.class')})
        package = output/'runtime'; package.mkdir()
        for row in runtime['files']:
            target = package/row['path']; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(checked_path(runtime_home,row),target)
        shutil.copyfile(program_archive,package/'saved-program.zip')
        files=[{'path':p.relative_to(package).as_posix(),'size':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()}
               for p in sorted(package.rglob('*')) if p.is_file()]
        binding={'preparedRuntimeSha256':sha256(runtime_raw).hexdigest(),
                 'programArchive':{'size':program_archive.stat().st_size,'sha256':sha256(program_archive.read_bytes()).hexdigest()}}
        program={'schema':'axiom.native-root-stage-runtime.v1','side':'SERVER','inputStage':'raw-original-artifacts',
            'executionStage':stage,'files':files,'classpath':runtime['classpath'],'sourceInputs':frozen,
            'policySha256':sha256(POLICY.read_bytes()).hexdigest(),'sourceLockSha256':sha256(LOCK.read_bytes()).hexdigest(),
            'engineSha256':sha256(engine_raw).hexdigest(),'nativeContext':context,'programContext':runtime['context'],
            'sourceProgram':source,'programArchive':'saved-program.zip','inputBindings':binding}
        (package/'program.json').write_text(json.dumps(program,indent=2)+'\n')
        run=call('native-root-stage',[java/'bin/java','-cp',os.pathsep.join(map(str,[output/'root-stage-probe.jar',*jars])),
                 'research.orthrus.axiom.NativeRootStageProbe',package], output)
        response=json.loads(run.stdout); descriptor=json.loads((package/'root-class-space.json').read_bytes())
        checker={'early':check_pack_early_result,'selection':check_selection_result,'groovy':check_groovy_result}[stage]
        failures=checker(response,descriptor,context,source)
        record.update(response=response,failures=failures,outputBytes=len(run.stdout),inputBindings=binding,
            runtimeSha256=sha256((package/'program.json').read_bytes()).hexdigest(),
            descriptorSha256=sha256((package/'root-class-space.json').read_bytes()).hexdigest())
        for row in files: checked_path(package,row)
        for name,digest in frozen.items(): checked_path(ROOT,{'path':name,'sha256':digest})
        if native_assembly.assembly_inputs(engine,java,'supersymmetry','cleanroom',runtime['context']['id']) != inputs:
            raise ValueError('Assembly inputs changed during fixture execution')
        record['status']='failed' if failures else 'passed-bounded-native-'+stage+'-stage'
    except BaseException as exc:
        record['failure']={'type':type(exc).__name__,'message':str(exc)}
        raise
    finally:
        (output/'build.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','cleanroom','foundation','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('library-root','server','runtime-home'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--stage',choices=('roots','early','selection','construction','groovy','preinit','recipes'),default='roots',
                        help='Original root, early, selection, construction or unqualified Groovy continuation')
    parser.add_argument('--native-context',choices=('root-only','supersymmetry:required-early'),default='root-only')
    parser.add_argument('--addon-inventory',type=Path)
    parser.add_argument('--pack',type=Path)
    parser.add_argument('--program',type=Path,help='Complete saved Groovy/configuration archive')
    args=parser.parse_args()
    if args.runtime_home is not None:
        if args.program is None or any(p is not None for p in (args.library_root,args.server,args.addon_inventory,args.pack)):
            parser.error('Core runtime fixtures require --program and omit raw assembly inputs')
        # Contributor tools use the same installed owner discovery and Core process port.
        sys.path[:0]=[str(ROOT/'api/src'),str(ROOT/'core/src')]
        from workbench_core.development import enable_source_checkout
        enable_source_checkout(ROOT)
        result=build_from_runtime(args.java_home,args.engine_home,args.runtime_home,args.program,
                                 args.cleanroom,args.foundation,args.output,args.stage)
        print(json.dumps({'status':result['status'],'failures':result.get('failures',[]),'output':str(args.output)}))
        return 0 if result['status'].startswith('passed-bounded-native-') else 1
    if args.library_root is None or args.server is None:
        parser.error('Raw assembly requires --library-root and --server')
    result=build(args.java_home,args.engine_home,args.library_root,args.server,args.cleanroom,args.foundation,args.output,args.stage,
                 args.native_context,args.addon_inventory,args.pack,args.program)
    print(json.dumps({'status':result['status'],'failures':result.get('failures',[]),'output':str(args.output)}))
    return 0 if result['status'].startswith('passed-bounded-native-') else 1


if __name__=='__main__':raise SystemExit(main())
