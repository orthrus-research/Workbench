#!/usr/bin/env python3
"""Discover fixed complete-program native execution; no installed acceptance claim."""
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
from axiom_material_program_sources import selected_revisions
from axiom_material_program_cases import cases,Edit,EDITS,identity
from axiom_groovy_transform_conformance import SERVICES,SERVICE
from build_axiom_target import git
from build_axiom_native_materials import compile_sources,jar_bytes
import build_axiom_material_api as api_build
import build_axiom_groovy_language as language_build
from axiom_groovy_language_sources import MIXINS

ROOT=Path(__file__).resolve().parents[1]
ORACLES=ROOT/'modules/axiom/tests/oracles'
DRIVER=ORACLES/'GroovyLanguageConformance.java'
BOOTSTRAP=ORACLES/'GroovyNativeBootstrap.java'
PROBE=ORACLES/'GroovyLanguageProbe.java'
AUDIT=ORACLES/'NativeTransformAudit.java'
OBSERVATIONS=ORACLES/'GroovyNativeObservations.java'
BYTECODE=ORACLES/'GroovyCandidateBytecode.java'
ADMISSION=ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialSourceAdmission.java'
GATE=ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialBytecodeGate.java'
CALL_GATE=GATE.with_name('MaterialCallGate.java')
ADMISSION_READER=GATE.with_name('MaterialAdmissionPolicy.java')
ADMISSION_POLICY=ROOT/'profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/axiom-material-admission.json'
SUPERVISOR=ROOT/'modules/axiom/jvm/src/main/java/research/orthrus/axiom/Main.java'


def compare_program(actual,rebuilt,language=False):
    """Supplied hashes are not source authority; compare a fresh native rebuild.

    A recipe can acquire extra custody inputs without changing the artifact. The
    new receipt binds that new recipe and records the supplied historical manifest.
    """
    keys=['schema','revisions','sourceInputs','sources','classes','artifacts','images','libraries','runtimeInputs']
    keys+=['engineManifestSha256','apiManifestSha256','resourceInputs','resources','mixinConfiguration'] if language else ['mappingsSha256','compilerView']
    for key in keys:
        if key not in actual or actual[key]!=json.loads(json.dumps(rebuilt[key])):
            raise ValueError('Native program differs from exact-source rebuild: '+key)


def execution(runs,name):
    envelope=runs[name]
    if envelope.get('schema')!='axiom.result.v1' or not envelope.get('kernelIsolation') or not envelope.get('namespaceIsolation'):
        raise ValueError('Native isolated result missing: '+name)
    if envelope.get('minecraftLaunched') is not False or envelope.get('groovyExecutionQualified') is not False:
        raise ValueError('Native discovery claim boundary differs: '+name)
    result=envelope.get('result',{})
    if 'failure' in result or not result.get('nativeClassSpaceClosed'): raise ValueError('Native execution discovery failed: '+name)
    return result.get('execution',{})


def language_cases():
    """Additional complete-program mechanism witnesses; G0 corpus is unchanged."""
    original=cases()[0]
    variants=[
        ('default-ingredient-import',
         "assert IIngredient.name == 'com.cleanroommc.groovyscript.api.IIngredient'"),
        ('caught-ingredient-disjunction',
         'try { IIngredient.ANY.or(IIngredient.EMPTY) } catch (UnsupportedOperationException ignored) { }'),
        ('caught-ingredient-crafting',
         'try { IIngredient.ANY.applyTransform(null) } catch (UnsupportedOperationException ignored) { }'),
    ]
    result=[]
    for name,statement in variants:
        files=Edit(EDITS,'Titanate.addFlags(NO_SMELTING)','Titanate.addFlags(NO_SMELTING)\n        '+statement).apply(original['files'])
        result.append({'name':name,'files':files,'candidateIdentity':identity(files)})
    return result


def content_cases():
    """Developer edits whose observable result is a real native generated form."""
    original=cases()[0]
    result=[]
    for name,edit in (
        ('post-material-plate',Edit(EDITS,'Titanate.addFlags(NO_SMELTING)',
                                  'Titanate.addFlags(NO_SMELTING, GENERATE_PLATE)')),
        ('without-ore-property',Edit('groovy/material/DeveloperMaterials.groovy',
                                   '.dust().ore().color(0x88bbaa)', '.dust().color(0x88bbaa)')),
        ('post-material-compressed',Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',
                                       'Aluminosilicate.setMaterialRGB(0x99ccbb)\n        Aluminosilicate.addFlags(FORCE_GENERATE_BLOCK)')),
        ('post-material-frame',Edit(EDITS,"Phosphate.setFormula('(Li,Na)AlPO4(F,OH)', true)",
                                  "Phosphate.setFormula('(Li,Na)AlPO4(F,OH)', true)\n        Phosphate.addFlags(GENERATE_FRAME)")),
        ('post-material-disable-ore',Edit(EDITS,'Titanate.addFlags(NO_SMELTING)',
                                       'Titanate.addFlags(NO_SMELTING, DISABLE_ORE_BLOCK)'))):
        files=edit.apply(original['files'])
        result.append({'name':name,'files':files,'candidateIdentity':identity(files)})
    return result


def check_content_observations(runs):
    def forms(value,key='prefixItems'):
        family=value[key]
        if (family['phase']!='COMPLETE' or family['family']!='gt-prefix-items'
                or family['recipeHandlersExecuted'] is not False or family['wholePackParity'] is not False):
            raise ValueError('Native prefix-item lifecycle boundary differs')
        checkpoints=family['checkpoints']
        if ([c['phase'] for c in checkpoints]!=['CONSTRUCTED','BLOCKS_REGISTERED','ITEMS_REGISTERED','COMPLETE']
                or any(c['items']!=39 for c in checkpoints) or checkpoints[0]['variants']!=0
                or checkpoints[1]['variants']!=0 or checkpoints[2]['variants']!=checkpoints[3]['variants'] or checkpoints[2]['variants']<=0):
            raise ValueError('Native prefix construction/registration checkpoints differ')
        return {(row['material'],row['prefix']):row for row in family['forms']}
    def generated(row,expected):
        if row['eligible']!=expected or len(row['generated'])!=int(expected):
            raise ValueError('Native generation decision differs: '+str(row))
        if expected:
            stack=row['generated'][0]
            if (stack['empty'] or not all(stack[k] for k in ('registryIdentity','materialIdentity','unifierIdentity'))
                    or stack['count']!=1 or not stack['oreNames'] or row['selected']!=stack):
                raise ValueError('Generated stack lost native identity/unification: '+str(row))
        elif not row['selected']['empty']:
            raise ValueError('Unexpected native item selected for absent developer form')
    for name in ('baseline',*[case['name'] for case in content_cases()]):
        reference=execution(runs,name+':reference'); guarded=execution(runs,name+':guarded')
        if (not reference.get('cleanObservation') or not guarded.get('cleanObservation')
                or any(reference[k]!=guarded[k] for k in ('materials','prefixItems','nativeBaseForms','registeredMaterials','phase'))):
            raise ValueError('Original/guarded material-to-content execution differs: '+name)
        rows=forms(guarded)
        if len(rows)!=3*39:raise ValueError('Incomplete developer prefix inventory')
        for material in ('developer_aluminosilicate','developer_phosphate','developer_titanate'):
            for prefix in ('dust','dustSmall','dustTiny'):
                generated(rows['supersymmetry:'+material,prefix],True)
        generated(rows['supersymmetry:developer_titanate','plate'],name=='post-material-plate')
        generated(rows['supersymmetry:developer_aluminosilicate','crushed'],name!='without-ore-property')
        generated(rows['supersymmetry:developer_phosphate','crushed'],False)
        generated(rows['supersymmetry:developer_titanate','gem'],True)
        base=forms(guarded,'nativeBaseForms')
        for material,prefix,item in (('iron','ingot','minecraft:iron_ingot'),('diamond','gem','minecraft:diamond')):
            row=base['gregtech:'+material,prefix];stack=row['selected']
            if (row['eligible'] or row['generated'] or stack['empty'] or stack['item']!=item
                    or not stack['registryIdentity'] or not stack['unifierIdentity'] or stack['materialIdentity']):
                raise ValueError('Native vanilla unification was confused with generated content')
    return ['native-prefix-item-lifecycle','same-material-through-stack-and-unifier',
            'post-material-flag-generates-plate','removed-ore-property-removes-crushed',
            'absent-form-is-not-generated','vanilla-selection-is-not-generation']


def check_block_observations(runs):
    for name in ('baseline',*[case['name'] for case in content_cases()]):
        reference=execution(runs,name+':reference');guarded=execution(runs,name+':guarded')
        if any(reference[key]!=guarded[key] for key in ('materialBlocks','nativeBaseBlocks')):
            raise ValueError('Native material-block instrumentation comparison differs: '+name)
        family=guarded['materialBlocks']
        if (family['family']!='gt-material-blocks' or family['phase']!='COMPLETE'
                or family['recipeHandlersExecuted'] is not False or family['wholePackParity'] is not False):
            raise ValueError('Native block family scope differs')
        checkpoints=family['checkpoints']
        if [c['phase'] for c in checkpoints]!=['CONSTRUCTED','BLOCKS_REGISTERED','ITEMS_REGISTERED','COMPLETE']:
            raise ValueError('Material block lifecycle checkpoint order differs')
        for index, checkpoint in enumerate(checkpoints):
            blocks=checkpoint['compressedBlocks']+checkpoint['frameBlocks']
            if (blocks<=0 or checkpoint['registeredBlocks']!=(0 if index==0 else blocks)
                    or checkpoint['registeredBlockItems']!=(0 if index<2 else blocks)):
                raise ValueError('Construction, block registration and ItemBlock binding are conflated')
        rows={(r['material'],r['prefix']):r for r in family['forms']}
        if len(rows)!=6 or len(family['forms'])!=6:raise ValueError('Incomplete or duplicate material block results')
        for material,identifier in (('developer_aluminosilicate',31000),('developer_phosphate',31001),('developer_titanate',31002)):
            for prefix in ('block','frameGt'):
                row=rows['supersymmetry:'+material,prefix]
                expected=(prefix=='block' and (material=='developer_titanate'
                            or (material=='developer_aluminosilicate' and name=='post-material-compressed'))
                          or prefix=='frameGt' and material=='developer_phosphate' and name=='post-material-frame')
                if len(row['generated'])!=int(expected):raise ValueError('Wrong native generated block decision: '+str(row))
                selected=row['selected']
                if not expected:
                    if not selected['empty']:raise ValueError('Unexpected selected developer block')
                    continue
                stack=row['generated'][0]
                kind='compressed' if prefix=='block' else 'frame'
                if (stack['item']!=f'gregtech:meta_block_{kind}_{identifier//16}'
                        or stack['block']!=stack['item'] or stack['metadata']!=identifier%16
                        or stack['propertyName']!='supersymmetry__'+material):
                    raise ValueError('Native ID packing, namespace or property name differs')
                if (stack['empty'] or stack['count']!=1 or not stack['oreNames']
                        or not all(stack[key] for key in ('registryIdentity','materialIdentity','unifierIdentity',
                            'blockRegistryIdentity','blockItemIdentity','stateRoundTripIdentity','stateMaterialIdentity','propertyRoundTripIdentity'))
                        or any(stack[key]!=value for key,value in selected.items())):
                    raise ValueError('Generated block lost native object/state/item/unifier identity')
        for row in guarded['nativeBaseBlocks']['forms']:
            if row['prefix']!='block':continue
            material=row['material'].split(':')[1];selected=row['selected']
            if (row['generated'] or selected['empty'] or selected['item']!=f'minecraft:{material}_block'
                    or not selected['registryIdentity'] or not selected['unifierIdentity'] or selected['materialIdentity']):
                raise ValueError('Existing vanilla block selection confused with generated block')
    return ['native-block-before-item-registration','original-material-id-block-packing',
            'same-material-through-native-state-property-and-item','post-material-force-block',
            'post-material-frame','ignored-generated-block-selects-vanilla']


def check_ore_observations(runs):
    stones=('stone','netherrack','endstone','sandstone','red_sandstone','granite','diorite','andesite',
            'black_granite','red_granite','marble','basalt')
    for name in ('baseline',*[case['name'] for case in content_cases()]):
        reference=execution(runs,name+':reference');guarded=execution(runs,name+':guarded')
        family=guarded['materialOres']
        if reference['materialOres']!=family:raise ValueError('Native ore instrumentation comparison differs')
        if (family['family']!='gt-ore-blocks' or family['phase']!='COMPLETE'
                or any(family[key] is not False for key in ('recipeHandlersExecuted','worldGenerationQualified','harvestingEventsQualified','wholePackParity'))):
            raise ValueError('Ore form observation broadened its qualification scope')
        rows={(row['material'],row['stone']):row for row in family['forms']}
        if len(rows)!=36 or len(family['forms'])!=36:raise ValueError('Incomplete native GT ore/stone inventory')
        for material in ('developer_aluminosilicate','developer_phosphate','developer_titanate'):
            expected=(material=='developer_aluminosilicate' and name!='without-ore-property'
                      or material=='developer_titanate' and name!='post-material-disable-ore')
            for identifier,stone in enumerate(stones):
                row=rows['supersymmetry:'+material,stone];selected=row['selected']
                if (row['stoneId']!=identifier or row['uniqueDrop']!=(identifier<3)
                        or len(row['generated'])!=int(expected)):
                    raise ValueError('Native ore generation, stone ID or drop eligibility differs')
                if not expected:
                    if not selected['empty']:raise ValueError('Unexpected ore selected for absent native ore block')
                    continue
                value=row['generated'][0]
                if (value['empty'] or value['item']!=f'gregtech:ore_{material}_0' or value['block']!=value['item']
                        or value['metadata']!=identifier or value['count']!=1 or not value['oreNames']
                        or not all(value[key] for key in ('registryIdentity','materialIdentity','unifierIdentity',
                            'blockRegistryIdentity','stoneIdentity','stateRoundTripIdentity','propertyRoundTripIdentity'))
                        or any(value[key]!=part for key,part in selected.items())):
                    raise ValueError('Generated ore lost native material, state, registry or unifier identity')
                drop=value['ordinaryDrop']
                if (drop['empty'] or drop['item']!=value['item'] or drop['metadata']!=(identifier if identifier<3 else 0)
                        or not drop['materialIdentity'] or not drop['registryIdentity']):
                    raise ValueError('Ordinary native drop was confused with generated ore form')
        for index,checkpoint in enumerate(family['checkpoints']):
            total=checkpoint['oreBlocks']
            if ((index==0 and total!=0) or (index>0 and total<=0)
                    or checkpoint['registeredOreBlocks']!=total
                    or checkpoint['registeredOreItems']!=(0 if index<2 else total)):
                raise ValueError('Ore generation and registration phases were conflated')
        transforms=runs[name+':guarded']['result']['transformations']['net.minecraft.block.Block']
        if not any(row['inputSha256']!=row['outputSha256'] and row['fieldAccess']['field_176227_L']&1
                   and not row['fieldAccess']['field_176227_L']&16 and row['fieldAccess']['field_149782_v']&1
                   for row in transforms):
            raise ValueError('Required original GT Block access transformation missing')
    return ['native-ore-generation-in-block-event','all-twelve-native-gt-stone-types',
            'same-material-through-ore-state-item-and-unifier','disabled-ore-block-keeps-other-material-forms',
            'ordinary-drop-distinct-from-generated-ore','original-gt-block-access-rules-applied']


NATIVE_OUTCOMES=('logged-components-error','property-setter-error','late-registration')


def check_native_outcomes(runs):
    """Ordinary authoring errors: native continuation and partial state, not a security campaign."""
    for name in NATIVE_OUTCOMES:
        reference=execution(runs,'native-outcome:'+name+':reference')
        guarded=execution(runs,'native-outcome:'+name+':guarded')
        if (reference.get('cleanObservation') or guarded.get('cleanObservation')
                or guarded.get('candidateAdmissionViolations')
                or any(reference.get(key)!=guarded.get(key) for key in ('materials','registeredMaterials','phase',
                    'executionCompleted','prefixItems','materialBlocks','materialOres','lateRegistered'))):
            raise ValueError('Original native error/partial-state behavior differs: '+name)
        if guarded['registeredMaterials']!=605:raise ValueError('Native error erased or invented registered material state')
        if name=='property-setter-error':
            if (guarded['executionCompleted'] or guarded['phase']!='CLOSED' or 'prefixItems' in guarded
                    or 'Harvest Level must be greater than zero!' not in guarded.get('nativeException','')
                    or guarded['materials'][0]['color']!=0x88bbaa
                    or guarded['materials'][1]['formula']!='(Li,Na)AlPO₄(F,OH)'):
                raise ValueError('Deferred setter failure did not retain partial material state before generation')
        else:
            if not guarded['executionCompleted'] or guarded['phase']!='FROZEN' or guarded['prefixItems']['phase']!='COMPLETE':
                raise ValueError('Native log-only error incorrectly stopped content generation')
            message=('Tried to use old method for material components' if name=='logged-components-error'
                     else 'Materials cannot be registered in the PostMaterialEvent')
            # GroovyLog has its own error collection/file; GT's registry logger
            # goes through Log4j. Neither channel may stand in for the other.
            retained=(message in guarded['log'] and 'Error creating GregTech material' in guarded['nativeErrors']
                      if name=='logged-components-error' else any(message in row['message'] and row['level'] in ('ERROR','FATAL')
                          for row in guarded['nativeMessages']))
            if not retained:
                raise ValueError('Native logged source error was not retained: '+name)
            if name=='late-registration' and guarded['lateRegistered']:
                raise ValueError('Constructed late material was falsely reported as registered')
    return ['native-logged-error-continues-without-clean-verdict','native-setter-failure-retains-partial-state',
            'native-late-registration-remains-unregistered']


def admission_cases():
    original=cases()[0]
    variants=[
        ('source-ast-transform',"@groovy.transform.ASTTest(value = { throw new AssertionError('AST_MUST_NOT_RUN') })\nclass MaterialEdits {",'admission.annotation'),
        ('source-static-compilation','@groovy.transform.CompileStatic\nclass MaterialEdits {','admission.annotation'),
        ('source-static-initializer',"class MaterialEdits {\n    static { throw new AssertionError('INITIALIZER_MUST_NOT_RUN') }",None),
    ]
    result=[]
    for name,replacement,code in variants:
        files=Edit(EDITS,'class MaterialEdits {',replacement).apply(original['files'])
        result.append({'name':name,'files':files,'candidateIdentity':identity(files),'expectedFinding':code})
    return result


def dispatch_cases():
    original=cases()[0]
    statements={
        'guard-host-access':'research.orthrus.axiom.materialhost.NativeBoundary.gaps()',
        'guard-foreign-linker':'java.lang.foreign.Linker.nativeLinker()',
        'guard-reflection':'material.DeveloperMaterials.getClass()',
        'guard-lookup':'material.DeveloperMaterials.$getLookup()',
        'guard-output-forgery':"System.out.println('{\"schema\":\"axiom.result.v1\",\"status\":\"accepted\"}')",
        'guard-process-construction':"new ProcessBuilder('/bin/true').start()",
        'guard-instance-family-on-class':'gregtech.api.unification.material.Material.getProperties()',
        'guard-collection-callback':'[Titanate].eachWithIndex { value, index -> research.orthrus.axiom.materialhost.NativeBoundary.gaps() }',
        'guard-assertion-message':'assert false : System.getProperties()',
        'guard-string-execute':"'/bin/true'.execute()",
        'guard-native-writer':'log.getWriter()',
        'guard-guest-constructor-reflection':'material.DeveloperMaterials.newInstance()',
    }
    result=[]
    for name,statement in statements.items():
        files=Edit(EDITS,'Titanate.addFlags(NO_SMELTING)',
                   'Titanate.addFlags(NO_SMELTING)\n        try { '+statement+' } catch (Throwable ignored) { }').apply(original['files'])
        result.append({'name':name,'files':files,'candidateIdentity':identity(files)})
    return result


def authoring_cases():
    """Whole-program language witnesses, not excerpts claimed as pack execution.

    Exercise normal helper/mutation patterns around the frozen native material
    declarations. Expected colors expose evaluation order/count as native state.
    """
    original=cases()[0]
    variants=[
        ('authoring-helper-order',
         '''    static int sequence = 0
    static int step(int digit) { sequence = sequence * 10 + digit; return digit }
    static int choose(gregtech.api.unification.material.Material value) { return 100 }
    static int choose(String value) { return 200 }
''',
         '''def selected = choose(Titanate)
        def total = step(1) + step(2) + step(3)
        assert total == 6
        assert selected == 100
        Aluminosilicate.setMaterialRGB(sequence)''',123),
        ('authoring-collections','',
         '''def values = [Aluminosilicate, Phosphate, Titanate]
        def colors = [first: 0x112233, second: 0x223344]
        values.eachWithIndex { value, index ->
            if (index == 0) { value.setMaterialRGB(colors.first) }
        }''',0x112233),
        ('authoring-loop','',
         '''int value = 0
        for (int index = 1; index <= 3; index++) { value += index }
        assert value == 6
        Aluminosilicate.setMaterialRGB(value)''',6),
        ('authoring-safe-navigation',
         '''    static int argumentsEvaluated = 0
    static int argument() { argumentsEvaluated++; return 1 }
''',
         '''gregtech.api.unification.material.Material absent = null
        def unused = absent?.setMaterialRGB(argument())
        assert unused == null
        Aluminosilicate?.setMaterialRGB(0x112234 + argumentsEvaluated)''',0x112235),
    ]
    result=[]
    for name,helpers,statement,color in variants:
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',statement).apply(original['files'])
        if helpers:files=Edit(EDITS,'    static void apply() {',helpers+'    static void apply() {').apply(files)
        result.append({'name':name,'files':files,'candidateIdentity':identity(files),'expectedColor':color})
    return result


def check_authoring_observations(runs):
    checks=[]
    for case in authoring_cases():
        name=case['name'];reference=execution(runs,name+':reference');guarded=execution(runs,name+':guarded')
        if not reference.get('cleanObservation') or reference['materials'][0]['color']!=case['expectedColor']:
            raise ValueError('Original authoring witness differs: '+name)
        keys=('materials','nativeErrors','coverageGaps','registeredMaterials','phase','scriptIndex','effectiveSide','physicalSide')
        if (not guarded.get('cleanObservation') or guarded.get('candidateAdmissionViolations')
                or any(guarded[key]!=reference[key] for key in keys)):
            raise ValueError('Guarded ordinary authoring differs: '+name)
        if name=='authoring-collections' and guarded.get('candidateDispatchObservations',{}).get(
                'getProperty it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap#first')!=1:
            raise ValueError('Original GroovyScript map transformation was not observed')
        checks.append(name)
    return checks


def resource_cases():
    original=cases()[0];result=[]
    for name,statement,helper in (
        ('caught-heap-exhaustion','byte[] oversized = new byte[536870912]',''),
        ('caught-stack-exhaustion','exhaustStack()',
         '    static void exhaustStack() { exhaustStack() }\n')):
        files=Edit(EDITS,'Titanate.addFlags(NO_SMELTING)',
                   'Titanate.addFlags(NO_SMELTING)\n        try { '+statement+' } catch (Throwable ignored) { }').apply(original['files'])
        if helper:files=Edit(EDITS,'    static void apply() {',helper+'    static void apply() {').apply(files)
        result.append({'name':name,'files':files,'candidateIdentity':identity(files)})
    return result


def guest_resolution_cases():
    """Harmless Class fallback witnesses in the unchanged complete program."""
    original=cases()[0];result=[]
    for name,declaration in (
        ('guest-field-class-fallback','static int forName = 1'),
        ('guest-instance-class-fallback',"String forName() { return 'guest' }"),
        ('guest-overload-class-fallback',"static String forName() { return 'guest' }")):
        files=Edit(EDITS,'    static void apply() {','    '+declaration+'\n    static void apply() {').apply(original['files'])
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',
                   '''Aluminosilicate.setMaterialRGB(0x99ccbb)
        try {
            if (classes.MaterialEdits.forName('java.lang.String') == String) {
                Aluminosilicate.setMaterialRGB(0x010203)
            }
        } catch (Throwable ignored) { }''').apply(files)
        result.append({'name':name,'files':files,'candidateIdentity':identity(files)})
    return result


def check_guest_resolution_observations(runs):
    checks=[];baseline=execution(runs,'guarded')
    for case in guest_resolution_cases():
        name=case['name'];reference=execution(runs,name+':reference');guarded=execution(runs,name+':guarded')
        if not reference.get('cleanObservation') or reference['materials'][0]['color']!=0x010203:
            raise ValueError('Original Class fallback was not observed: '+name)
        if (guarded.get('cleanObservation') is not False or not guarded.get('executionCompleted')
                or 'candidate.dispatch: invoke classes.MaterialEdits#forName' not in guarded.get('candidateAdmissionViolations',[])):
            raise ValueError('Guest name admitted a native Class fallback: '+name)
        if any(guarded[key]!=baseline[key] for key in ('materials','registeredMaterials','phase')):
            raise ValueError('Refused Class fallback changed native continuation: '+name)
        checks.append(name)
    return checks


def check_resource_observations(runs):
    checks=[]
    for case in resource_cases():
        reference=execution(runs,case['name']+':reference');guarded=execution(runs,case['name']+':guarded')
        if (not reference.get('executionCompleted') or not guarded.get('executionCompleted')
                or guarded.get('cleanObservation') is not False or guarded.get('candidateAdmissionViolations')
                or not (guarded.get('candidateResourceFailure') is True or guarded.get('candidateLinkageFailure') is True)):
            raise ValueError('Caught JVM resource failure did not remain incomplete: '+case['name'])
        if case['name']=='caught-heap-exhaustion' and (guarded.get('candidateResourceFailure') is not True
                or not guarded.get('candidateResourceCause') or guarded['candidateResourceCause'][-1]!='java.lang.OutOfMemoryError'):
            raise ValueError('Caught heap failure lost its observed VM cause')
        # The selected native loader can lose the original resource cause after
        # caching a failed class load. Never relabel that as a proven VM error:
        # the observed linkage loss independently disqualifies the context.
        if case['name']=='caught-stack-exhaustion':
            cause=guarded.get('candidateResourceCause') if guarded.get('candidateResourceFailure') else guarded.get('candidateCaughtCause')
            if not cause or not set(cause)&{'java.lang.StackOverflowError','java.lang.NoClassDefFoundError',
                                           'java.lang.ClassNotFoundException','java.lang.BootstrapMethodError'}:
                raise ValueError('Stack-stress failure has no observed VM/linkage cause')
        for key in ('materials','registeredMaterials','phase'):
            if guarded[key]!=reference[key]:raise ValueError('Resource observation changed native continuation: '+case['name'])
        checks.append(case['name'])
    return checks


def structure_observation(runs,name,expected):
    envelope=runs[name];result=envelope.get('result',{});admission=result.get('sourceAdmission',{})
    if (envelope.get('schema')!='axiom.result.v1' or envelope.get('kernelIsolation') is not True
            or envelope.get('namespaceIsolation') is not True or envelope.get('groovyExecutionQualified') is not False
            or envelope.get('minecraftLaunched') is not False or result.get('candidateCompilationStarted') is not False
            or result.get('nativeClassSpaceCreated') is not False or admission.get('candidateCodeGenerated') is not False
            or admission.get('runtimeAdmissionQualified') is not False):
        raise ValueError('Source-structure witness escaped isolated parser-only scope: '+name)
    if expected is None:
        if admission.get('structurallyAdmitted') is not True or admission.get('findings'):
            raise ValueError('Parser-only initialization witness differs: '+name)
    elif admission.get('structurallyAdmitted') is not False or not any(row.get('code')==expected for row in admission.get('findings',[])):
        raise ValueError('Source-structure refusal differs: '+name)


def check_observations(runs,all_cases=False):
    """Discriminating fixed-corpus mechanism tests, never G1/G5 completion."""
    positive=execution(runs,'present')
    if (not positive.get('cleanObservation') or positive.get('registeredMaterials')!=605
            or positive.get('phase')!='FROZEN' or not positive.get('singleNativeIdentity')
            or not positive.get('scriptInitializationBeforeCatalog')
            or positive.get('physicalSide')!='SERVER' or positive.get('effectiveSide')!='SERVER'):
        raise ValueError('Complete fixed program native observation differs')
    if positive['materials'][0]['color']!=0x99ccbb or positive['materials'][1]['formula']!='(Li,Na)AlPO₄(F,OH)':
        raise ValueError('Native deferred material edits differ')
    if [v['amount'] for v in positive['materials'][0]['components']]!=[1,1,4,10]:
        raise ValueError('Original mixed-component dispatch differs')
    reference=execution(runs,'no-audit')
    semantic_keys=('materials','nativeErrors','coverageGaps','registeredMaterials','phase','scriptIndex','effectiveSide','physicalSide')
    if not reference.get('cleanObservation') or any(reference[key]!=positive[key] for key in semantic_keys):
        raise ValueError('Observation changed native program semantics')
    missing=execution(runs,'missing')
    absent=runs['missing']['result']['bootstrap']
    if (missing or absent.get('admitted') is not False or absent.get('compilerInvoked') is not False
            or absent.get('requiredTransformAbsent') is not True):
        raise ValueError('Missing transformation did not refuse before compilation')
    expected={*['com.cleanroommc.groovyscript.core.mixin.groovy.'+name for name in MIXINS],
              'com.cleanroommc.groovyscript.core.mixin.EventBusMixin'}
    transformations=runs['present']['result']['transformations']
    observed={name for rows in transformations.values() for row in rows for name in row['mergedMixins']}
    if observed!=expected: raise ValueError('Required native merged-mixin evidence differs')
    core=execution(runs,'missing-core')
    if core.get('cleanObservation') or not any("Apparent variable 'log'" in error for error in core['nativeErrors']):
        raise ValueError('Missing original static-binding transformer was not distinguished')
    expansion=execution(runs,'missing-expansion')
    failure=expansion.get('nativeException','')
    if (expansion.get('cleanObservation') or 'java.lang.ClassCastException' not in failure
            or 'gregtech.api.unification.material.Material$Builder.components' not in failure):
        raise ValueError('Missing native builder expansion was not distinguished')
    checks=['complete-native-program','deferred-native-edits','mixed-component-dispatch','uninstrumented-comparison',
            'missing-required-configuration','all-ten-merged-mixins','missing-native-core-transformer','missing-native-expansion']
    imported=execution(runs,'default-ingredient-import')
    if not imported.get('cleanObservation') or any(imported[key]!=positive[key] for key in semantic_keys if key!='scriptIndex'):
        raise ValueError('Original default ingredient import did not resolve cleanly')
    for name,gap in (('caught-ingredient-disjunction','groovy.ingredient-disjunction'),
                     ('caught-ingredient-crafting','groovy.ingredient-crafting-transform')):
        bounded=execution(runs,name)
        if bounded.get('cleanObservation') or not bounded.get('executionCompleted') or bounded.get('coverageGaps')!=[gap]:
            raise ValueError('Original ingredient API boundary was not retained after catch: '+name)
    checks+=['original-default-ingredient-import','caught-ingredient-disjunction','caught-ingredient-crafting']
    bytecode=execution(runs,'bytecode-audit')
    if not bytecode.get('cleanObservation') or any(bytecode[key]!=positive[key] for key in semantic_keys):
        raise ValueError('Native bytecode observation changed program semantics')
    if not bytecode.get('candidateBytecode') or not any(row['kind']=='indy'
            for value in bytecode['candidateBytecode'].values() for row in value['instructions']):
        raise ValueError('Native pre-definition bytecode observation absent')
    checks+=['native-predefinition-bytecode-observation']
    guarded=execution(runs,'guarded')
    if not guarded.get('cleanObservation') or any(guarded[key]!=positive[key] for key in semantic_keys):
        raise ValueError('Guarded native dispatch changed complete-program semantics')
    if guarded.get('candidateAdmissionViolations') or not guarded.get('candidateDispatchObservations'):
        raise ValueError('Guarded dispatch observation missing or violated')
    checks+=['guarded-native-dispatch-comparison']
    for mode in ('cache-reference','cache-guarded'):
        cache=execution(runs,mode)
        if (cache.get('cachedDefinitionWitness') is not True or cache.get('nativeBody')!='original-cache-body'
                or cache.get('cacheMembership')!=['classes.MaterialEdits'] or cache.get('validityQualified') is not False):
            raise ValueError('Original cached definition route differs: '+mode)
        if mode=='cache-guarded':
            if cache.get('rejectedBeforeDefinition')!=['research.orthrus.axiom.materialhost.Injected','material.DeveloperMaterials'] or len(cache.get('violations',[]))!=2:
                raise ValueError('Cached bytecode bypassed admission')
        elif cache.get('violations') or cache.get('rejectedBeforeDefinition'):
            raise ValueError('No-hook cache reference differs')
    checks+=['native-cached-definition-reference','native-cached-definition-admission']
    for case in dispatch_cases():
        value=execution(runs,case['name'])
        if value.get('cleanObservation') or not value.get('executionCompleted') or not value.get('candidateAdmissionViolations'):
            raise ValueError('Caught dispatch refusal did not remain incomplete: '+case['name'])
        if value['materials']!=guarded['materials'] or value['registeredMaterials']!=605 or value['phase']!='FROZEN':
            raise ValueError('Refused call changed native continuation: '+case['name'])
    checks+=[case['name'] for case in dispatch_cases()]
    for case in admission_cases():structure_observation(runs,case['name'],case['expectedFinding'])
    checks+=[case['name'] for case in admission_cases()]
    if all_cases:
        replay=execution(runs,'complete-program')
        if not replay.get('cleanObservation') or any(replay[key]!=positive[key] for key in semantic_keys):
            raise ValueError('Fresh native replay differs')
        for case in cases()[1:]:
            name=case['name']
            if name=='timeout':
                value=runs[name]
                if not value.get('supervisorFailure') or not any(text in value.get('stderr','') for text in (
                        'Evaluation exceeded','Isolated worker terminated')):
                    raise ValueError('Native timeout/exhaustion did not close worker')
                continue
            if name=='conflicting-package':
                structure_observation(runs,name,'admission.package')
                continue
            value=execution(runs,name)
            if value.get('cleanObservation'): raise ValueError('Negative native source case yielded a clean observation: '+name)
            if name=='logged-components-error' and (not value['executionCompleted'] or 'Tried to use old method for material components' not in value['log']):
                raise ValueError('Logged-error native continuation differs')
            if name=='property-setter-error' and ('Harvest Level must be greater than zero!' not in value.get('nativeException','')
                                                or value['phase']!='CLOSED'):
                raise ValueError('Deferred native property failure differs')
            if name=='late-registration' and (value['lateRegistered'] or not value['executionCompleted'] or not any(
                    'Materials cannot be registered in the PostMaterialEvent' in row['message'] for row in value['nativeMessages'])):
                raise ValueError('Native skipped-registration evidence differs')
            if name=='caught-unqualified-operation' and value['coverageGaps']!=['material.localization']:
                raise ValueError('Caught native coverage gap was lost')
            if name=='native-skip' and not any(row['preprocessorCheckFailed'] and 'NO_RUN' in row['preprocessors'] for row in value['scriptIndex']):
                raise ValueError('Native preprocessor skip was lost')
            if name=='unknown-addon' and not any('unable to resolve class supersymmetry.api.fluids.SusyFluidStorageKeys' in error for error in value['nativeErrors']):
                raise ValueError('Missing addon dependency evidence differs')
        checks+=['fresh-native-replay',*['corpus:'+case['name'] for case in cases()[1:]]]
        for case in cases():
            name=case['name'];guarded_name='guarded-corpus:'+name
            if name=='conflicting-package':
                structure_observation(runs,guarded_name,'admission.package');continue
            if name=='timeout':
                if not runs[guarded_name].get('supervisorFailure'):raise ValueError('Guarded timeout did not terminate')
                continue
            original=execution(runs,name);bounded=execution(runs,guarded_name)
            keys=('materials','registeredMaterials','phase','coverageGaps','missingMaterials','cleanObservation','lateRegistered')
            if bounded.get('candidateAdmissionViolations') or any(bounded[key]!=original[key] for key in keys):
                raise ValueError('Guarded G0 corpus native outcome differs: '+name)
            if name=='property-setter-error' and 'Harvest Level must be greater than zero!' not in bounded.get('nativeException',''):
                raise ValueError('Guarded native property cause was lost')
            if name=='logged-components-error' and 'Tried to use old method for material components' not in bounded['log']:
                raise ValueError('Guarded native logged-error continuation was lost')
            if name=='native-skip' and not any(row['preprocessorCheckFailed'] for row in bounded['scriptIndex']):
                raise ValueError('Guarded native skip was lost')
        checks+=['guarded-corpus:'+case['name'] for case in cases()]
    return checks


def qualify(java,engine,images,libraries,api,language,cleanroom,groovyscript,gtceu,report,all_cases=False,content_only=False):
    java,engine,images,libraries,api,language,cleanroom=[ordinary(p).resolve(strict=True) for p in (java,engine,images,libraries,api,language,cleanroom)]
    report=ordinary(report)
    if report.exists(): raise ValueError('Language discovery receipt must be new')
    groovyscript,gtceu=[ordinary(p).resolve(strict=True) for p in (groovyscript,gtceu)]
    runtime=verify_runtime(java,compiler=True);policy=json.loads(POLICY.read_bytes())
    manifest,engine_manifest,jars=engine_inputs(engine,LOCK.read_bytes())
    groovy=[p for p in jars if p.name=='groovy-4.0.30.jar']
    if len(groovy)!=1: raise ValueError('Pinned Groovy 4.0.30 required')
    program_manifests={}
    for root in (api,language):
        raw=(root/'program.json').read_bytes();program_manifests[root]=raw
        for name,digest in json.loads(raw)['artifacts'].items(): checked_path(root,{'path':name,'sha256':digest})
    if json.loads(program_manifests[language])['apiManifestSha256']!=sha256(program_manifests[api]).hexdigest():
        raise ValueError('Language and material API identity differ')
    deps=[checked_path(images,row) for row in policy['images']]+[checked_path(libraries,row) for row in policy['libraries']]
    native_programs=[language/'groovy-language.jar',api/'material-api.jar',groovy[0]]
    deps+=native_programs
    revisions=selected_revisions()
    services={name:git(cleanroom,'show',revisions['cleanroom']+':src/main/resources/META-INF/services/'+name) for name in SERVICES}
    recipe_paths=[DRIVER,BOOTSTRAP,PROBE,SERVICE,AUDIT,OBSERVATIONS,BYTECODE,ADMISSION,GATE,CALL_GATE,ADMISSION_READER,ADMISSION_POLICY,SUPERVISOR,Path(__file__),POLICY,LOCK,
                  *sorted((ROOT/'tools').glob('axiom_*sources.py')),ROOT/'tools/build_axiom_native_materials.py',
                  ROOT/'tools/axiom_groovy_transform_conformance.py',ROOT/'tools/axiom_material_program_cases.py']
    frozen={**language_build.recipe_inputs(),**{p:p.read_bytes() for p in recipe_paths}}
    corpus=cases();mechanisms=language_cases();structures=admission_cases();dispatches=dispatch_cases();authors=authoring_cases();resources=resource_cases();guests=guest_resolution_cases();contents=content_cases();candidate=corpus[0]
    environment={k:v for k,v in os.environ.items() if k not in {'JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS','CLASSPATH','LD_PRELOAD','LD_LIBRARY_PATH'}}
    with tempfile.TemporaryDirectory(prefix='axiom-native-language-') as temporary:
        work=Path(temporary)
        rebuilt_api=api_build.build(java,images,libraries,gtceu,groovyscript,work/'rebuilt-api')
        compare_program(json.loads(program_manifests[api]),rebuilt_api)
        rebuilt_language=language_build.build(java,engine,images,libraries,api,groovyscript,gtceu,work/'rebuilt-language')
        compare_program(json.loads(program_manifests[language]),rebuilt_language,language=True)
        classes=compile_sources(java,{p.stem:frozen[p].decode() for p in (BOOTSTRAP,PROBE,SERVICE,AUDIT,OBSERVATIONS,BYTECODE,GATE,CALL_GATE,ADMISSION_READER)},deps,work/'native')
        installed_services={**services,'org.spongepowered.asm.service.IMixinService':b'research.orthrus.axiom.materialtest.ContextMixinService'}
        jar_bytes(work/'native-probe.jar',{**classes,**{'META-INF/services/'+n:r for n,r in installed_services.items()}})
        driver=compile_sources(java,{p.stem:frozen[p].decode() for p in (DRIVER,ADMISSION,SUPERVISOR)},jars,work/'driver')
        jar_bytes(work/'driver.jar',{**driver,'axiom/material-admission.json':frozen[ADMISSION_POLICY]})
        for case in [*corpus,*mechanisms,*structures,*dispatches,*authors,*resources,*guests,*contents]: jar_bytes(work/(case['name']+'.zip'),case['files'])
        def run(mode,case=candidate):
            inputs=[work/'native-probe.jar',*native_programs,work/(case['name']+'.zip')]
            command=[str(java/'bin/java'),'--enable-native-access=ALL-UNNAMED','-cp',os.pathsep.join(map(str,[work/'driver.jar',*jars])),
                     'research.orthrus.axiom.GroovyLanguageConformance','supervisor',str(images),str(libraries)]
            for path in inputs: command.extend([str(path),sha256(path.read_bytes()).hexdigest()])
            command.append(mode)
            result=subprocess.run(command,cwd=work,env=environment,capture_output=True,text=True,timeout=50)
            if result.returncode: return {'supervisorFailure':result.returncode,'stdout':result.stdout[-3000:],'stderr':result.stderr[-24000:]}
            return json.loads(result.stdout)
        runs={}
        for case in [candidate,*contents]:
            name='baseline' if case is candidate else case['name']
            runs[name+':reference']=run('present',case)
            runs[name+':guarded']=run('guarded',case)
        for case in corpus:
            if case['name'] in NATIVE_OUTCOMES:
                name='native-outcome:'+case['name']
                runs[name+':reference']=run('present',case)
                runs[name+':guarded']=run('guarded',case)
        if not content_only:
            runs.update({mode:run(mode) for mode in ('missing','present','no-audit','missing-core','missing-expansion','bytecode-audit','guarded',
                                        'cache-reference','cache-guarded')}
            )
            runs.update({case['name']:run('present',case) for case in mechanisms})
            runs.update({case['name']:run('source-only',case) for case in structures})
            runs.update({case['name']:run('guarded',case) for case in dispatches})
        for case in authors if not content_only else []:
            runs[case['name']+':reference']=run('bytecode-audit',case)
            runs[case['name']+':guarded']=run('guarded',case)
        for case in resources if not content_only else []:
            runs[case['name']+':reference']=run('bytecode-audit',case)
            runs[case['name']+':guarded']=run('guarded',case)
        for case in guests if not content_only else []:
            runs[case['name']+':reference']=run('bytecode-audit',case)
            runs[case['name']+':guarded']=run('guarded',case)
        if all_cases:
            runs.update({case['name']:run('present',case) for case in corpus})
            runs.update({'guarded-corpus:'+case['name']:run('guarded',case) for case in corpus})
        checks=[];check_failure=None
        try:
            checks=check_content_observations(runs)
            checks+=check_block_observations(runs)
            checks+=check_ore_observations(runs)
            checks+=check_native_outcomes(runs)
            if not content_only:
                checks+=check_observations(runs,all_cases)
                checks+=check_authoring_observations(runs)
                checks+=check_resource_observations(runs)
                checks+=check_guest_resolution_observations(runs)
            for name,value in runs.items():
                if value.get('supervisorFailure'):continue
                if value.get('result',{}).get('admissionPolicySha256')!=sha256(frozen[ADMISSION_POLICY]).hexdigest():
                    raise ValueError('Trusted admission policy identity differs: '+name)
            checks+=['profile-admission-policy-custody']
        except (KeyError,ValueError,TypeError) as failure: check_failure=str(failure)
        receipt={'schema':'axiom.groovy-language-discovery.v1','status':'observed-not-qualified','runs':runs,
                 'scope':'native-generated-content' if content_only else 'language-and-generated-content',
                 'mechanismChecks':checks,'mechanismCheckFailure':check_failure,
                 'rebuiltApiManifestSha256':sha256((work/'rebuilt-api/program.json').read_bytes()).hexdigest(),
                 'rebuiltLanguageManifestSha256':sha256((work/'rebuilt-language/program.json').read_bytes()).hexdigest(),
                 'rebuiltApi':rebuilt_api,'rebuiltLanguage':rebuilt_language,
                 'probeJarSha256':sha256((work/'native-probe.jar').read_bytes()).hexdigest(),
                 'driverJarSha256':sha256((work/'driver.jar').read_bytes()).hexdigest(),
                 'candidateArchives':{case['name']:sha256((work/(case['name']+'.zip')).read_bytes()).hexdigest() for case in [*corpus,*mechanisms,*structures,*dispatches,*authors,*resources,*guests,*contents]},
                 'candidateIdentity':candidate['candidateIdentity'],'revisions':revisions,
                 'corpus':{case['name']:case['candidateIdentity'] for case in corpus},
                 'mechanismCorpus':{case['name']:case['candidateIdentity'] for case in mechanisms},
                 'structureCorpus':{case['name']:case['candidateIdentity'] for case in structures},
                 'dispatchCorpus':{case['name']:case['candidateIdentity'] for case in dispatches},
                 'authoringCorpus':{case['name']:case['candidateIdentity'] for case in authors},
                 'resourceCorpus':{case['name']:case['candidateIdentity'] for case in resources},
                 'guestResolutionCorpus':{case['name']:case['candidateIdentity'] for case in guests},
                 'contentCorpus':{case['name']:case['candidateIdentity'] for case in contents},
                 'admissionPolicySha256':sha256(frozen[ADMISSION_POLICY]).hexdigest(),
                 'programManifestSha256':{root.name:sha256(raw).hexdigest() for root,raw in program_manifests.items()},
                 'services':{n:sha256(raw).hexdigest() for n,raw in services.items()},
                 'qualificationInputs':{str(p.relative_to(ROOT)):sha256(raw).hexdigest() for p,raw in frozen.items()},
                 'engineManifestSha256':sha256(manifest).hexdigest(),'runtimeInputs':runtime['runtimeFiles']+runtime['compilerFiles'],
                 'images':policy['images'],'libraries':policy['libraries'],
                 'groovyExecutionQualified':False,'wholePackParity':False}
    if (any(p.read_bytes()!=raw for p,raw in frozen.items()) or cases()!=corpus or language_cases()!=mechanisms
            or admission_cases()!=structures or dispatch_cases()!=dispatches or authoring_cases()!=authors or resource_cases()!=resources
            or guest_resolution_cases()!=guests or content_cases()!=contents):
        raise ValueError('Language discovery input drift')
    for root,rows in ((images,policy['images']),(libraries,policy['libraries']),(java,runtime['runtimeFiles']+runtime['compilerFiles'])):
        for row in rows: checked_path(root,row)
    for root,raw in program_manifests.items():
        if (root/'program.json').read_bytes()!=raw: raise ValueError('Language program manifest drift')
        for name,digest in json.loads(raw)['artifacts'].items(): checked_path(root,{'path':name,'sha256':digest})
    if engine_inputs(engine,LOCK.read_bytes())[0]!=manifest: raise ValueError('Language engine drift')
    if services!={name:git(cleanroom,'show',revisions['cleanroom']+':src/main/resources/META-INF/services/'+name) for name in SERVICES}:
        raise ValueError('Native service source drift')
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x') as out: json.dump(receipt,out,indent=2);out.write('\n')
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','images','library-root','api-program','language-program','cleanroom','groovyscript','gtceu','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--all-cases',action='store_true')
    parser.add_argument('--content-only',action='store_true',help='Run native generated-form authoring witnesses only')
    args=parser.parse_args(argv)
    result=qualify(args.java_home,args.engine_home,args.images,args.library_root,args.api_program,args.language_program,args.cleanroom,args.groovyscript,args.gtceu,args.report,args.all_cases,args.content_only)
    print(json.dumps({'status':result['status'],'cleanObservations':{name:value.get('result',{}).get('execution',{}).get('cleanObservation',False)
                        for name,value in result['runs'].items()},'mechanismCheckFailure':result['mechanismCheckFailure'],
                        'checks':len(result['mechanismChecks']),'report':str(args.report)},indent=2))
    return 0 if result['mechanismCheckFailure'] is None else 1


if __name__=='__main__':raise SystemExit(main())
