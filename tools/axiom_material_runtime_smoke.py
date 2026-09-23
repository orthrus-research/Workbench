#!/usr/bin/env python3
"""Exercise the installed material-program operation with ordinary saved sources."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

from axiom_material_program_cases import cases, Edit, EDITS, identity, source_acknowledgement, matches_source_acknowledgement
from axiom_source_conformance import engine_inputs, LOCK
from axiom_runtime import verify_runtime
from build_axiom_native_materials import jar_bytes

ROOT = Path(__file__).resolve().parents[1]


def retain_programs(report, selected):
    directory = report.absolute().with_name(report.name + '.inputs')
    if directory.exists():
        raise ValueError('Smoke retained inputs must be new')
    directory.mkdir(parents=True)
    records = {}
    for index, case in enumerate(selected):
        if case['name'] in records:
            raise ValueError('Duplicate smoke program name')
        archive = directory / f'{index:04d}-program.zip'
        jar_bytes(archive, case['files'])
        raw = archive.read_bytes()
        records[case['name']] = {'sourceProgram': identity(case['files']),
            'programArchive': {'path': archive.relative_to(report.absolute().parent).as_posix(),
                               'size': len(raw), 'sha256': sha256(raw).hexdigest()}}
    # Retained even if a later subprocess fails before the final smoke receipt.
    (directory / 'programs.json').write_text(json.dumps(records, indent=2) + '\n')
    return records


def smoke(java, engine, runtime, report, reference_runtime=None):
    java, engine, runtime = [p.resolve(strict=True) for p in (java, engine, runtime)]
    if report.exists() or report.absolute().with_name(report.name + '.inputs').exists():
        raise ValueError('Smoke receipt must be new')
    jvm = verify_runtime(java)
    engine_raw, _, jars = engine_inputs(engine, LOCK.read_bytes())
    runtime_raw = (runtime / 'runtime.json').read_bytes(); manifest = json.loads(runtime_raw)
    request = {key: manifest[key] for key in ('contextPolicySha256', 'admissionPolicySha256')}
    request['context'] = manifest['context']['id']
    request['observeMaterials'] = ['supersymmetry:developer_' + name for name in ('aluminosilicate', 'phosphate', 'titanate')]
    intent_path = ROOT / 'modules/axiom/tests/fixtures/material-program/intent.json'
    intent_raw = intent_path.read_bytes()
    request.update(json.loads(intent_raw))
    runs = []; paired = []; observation_comparisons = []
    reference_raw = None
    if reference_runtime is not None:
        reference_runtime = reference_runtime.resolve(strict=True)
        reference_raw = (reference_runtime / 'runtime.json').read_bytes()
        reference_manifest = json.loads(reference_raw)
        for key in ('contextPolicySha256', 'admissionPolicySha256', 'revisions'):
            if reference_manifest[key] != manifest[key]:
                raise ValueError('Observation comparison native context differs: ' + key)
        # The reference disables the new host observations, not the selected
        # native API/language/transformation dependencies or their versions.
        def dependencies(value):
            return {row['path']: row['sha256'] for row in value['files']
                    if row['path'].startswith('lib/') and row['path'] != 'lib/material-runtime.jar'}
        if dependencies(reference_manifest) != dependencies(manifest):
            raise ValueError('Observation comparison native dependencies differ')
    corpus = cases()
    selected = [*corpus[:7]]
    for case in selected:
        native_error = case['name'] in {'logged-components-error', 'property-setter-error', 'late-registration'}
        case.update(wantStatus='source-error' if native_error else 'incomplete',
                    wantIntent='matched' if case['name'] == 'complete-program' else 'incomplete',
                    wantClean=case['name'] == 'complete-program', locatedError=native_error)
    renamed = {}
    for path, raw in selected[0]['files'].items():
        target = path.replace('DeveloperMaterials', 'PackMaterials').replace('MaterialEdits', 'PackEdits').replace('preInit/Materials', 'preInit/Program')
        renamed[target] = raw.replace(b'DeveloperMaterials', b'PackMaterials').replace(b'MaterialEdits', b'PackEdits').replace(b'developer_', b'pack_')
    renamed['groovy/classes/AdditionalEdit.groovy'] = b'package classes\nimport material.PackMaterials\nclass AdditionalEdit { static void apply() { PackMaterials.Titanate.setFormula("TiO2") } }\n'
    renamed['groovy/preInit/Program.groovy'] = renamed['groovy/preInit/Program.groovy'].replace(b'PackEdits.apply()', b'PackEdits.apply()\n    classes.AdditionalEdit.apply()')
    selected += [{'name': 'renamed-five-file-program', 'files': renamed, 'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True}]
    plate = {'id': 'required-plate', 'material': request['observeMaterials'][0], 'kind': 'form',
             'family': 'gt-prefix-items', 'prefix': 'plate', 'fact': 'generated', 'equals': True}
    selected += [
        {'name': 'required-plate-absent', 'files': corpus[0]['files'], 'intent': [plate], 'wantStatus': 'rejected', 'wantIntent': 'mismatch', 'wantClean': True},
        {'name': 'required-plate-added', 'files': Edit(EDITS, 'Aluminosilicate.setMaterialRGB(0x99ccbb)',
            'Aluminosilicate.setMaterialRGB(0x99ccbb)\n        Aluminosilicate.addFlags(GENERATE_PLATE)').apply(corpus[0]['files']),
            'intent': [plate], 'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True},
        {'name': 'unsupported-family', 'files': corpus[0]['files'], 'intent': [{**plate, 'family': 'susy-custom-items'}],
            'wantStatus': 'incomplete', 'wantIntent': 'incomplete', 'wantClean': True},
        {'name': 'mismatch-with-unsupported-family', 'files': corpus[0]['files'],
            'intent': [plate, {**plate, 'id': 'uncovered-form', 'family': 'susy-custom-items'}],
            'wantStatus': 'incomplete', 'wantIntent': 'incomplete', 'wantClean': True},
        {'name': 'absent-and-aliased-identities', 'files': corpus[0]['files'], 'intent': [
            {'id': 'not-created', 'material': 'supersymmetry:not_created', 'kind': 'registration', 'equals': False},
            {'id': 'not-a-new-namespace', 'material': 'different:developer_aluminosilicate', 'kind': 'registration', 'equals': False}],
            'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True},
    ]
    fluid = {'id': 'liquid-queued', 'material': request['observeMaterials'][0], 'kind': 'fluid',
             'storageKey': 'gregtech:liquid', 'fact': 'queued', 'equals': True}
    pending = {'id': 'dust-processing-pending', 'material': request['observeMaterials'][0], 'kind': 'processing',
               'prefix': 'dust', 'fact': 'queued', 'equals': True}
    selected += [
        {'name': 'native-pending-fluid', 'files': Edit('groovy/material/DeveloperMaterials.groovy',
            '.dust().ore().color', '.dust().ore().liquid().color').apply(corpus[0]['files']),
         'intent': [fluid, {**fluid, 'id': 'liquid-not-stored', 'fact': 'stored', 'equals': False}, pending],
         'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True},
        {'name': 'no-fluid-property', 'files': corpus[0]['files'], 'intent': [{**fluid, 'equals': False}, pending],
         'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True},
    ]
    priorities = dict(corpus[0]['files'])
    # Register in deliberately non-priority order. Native Forge/GroovyScript must
    # produce 1 -> 12 -> 123 -> 1234 -> 12345, not registration/insertion order.
    priorities['groovy/preInit/Materials.groovy'] = b'''package preInit
import material.DeveloperMaterials
import classes.MaterialEdits
import gregtech.api.unification.material.event.MaterialEvent
import gregtech.api.unification.material.event.PostMaterialEvent
import net.minecraftforge.fml.common.eventhandler.EventPriority
eventManager.listen(EventPriority.LOWEST) { MaterialEvent event -> DeveloperMaterials.register() }
eventManager.listen(EventPriority.LOWEST) { PostMaterialEvent event ->
    DeveloperMaterials.Aluminosilicate.setMaterialRGB(DeveloperMaterials.Aluminosilicate.getMaterialRGB() * 10 + 5)
}
eventManager.listen(EventPriority.NORMAL) { PostMaterialEvent event ->
    DeveloperMaterials.Aluminosilicate.setMaterialRGB(DeveloperMaterials.Aluminosilicate.getMaterialRGB() * 10 + 3)
}
eventManager.listen(EventPriority.HIGHEST) { PostMaterialEvent event ->
    MaterialEdits.apply()
    DeveloperMaterials.Aluminosilicate.setMaterialRGB(1)
}
eventManager.listen(EventPriority.LOW) { PostMaterialEvent event ->
    DeveloperMaterials.Aluminosilicate.setMaterialRGB(DeveloperMaterials.Aluminosilicate.getMaterialRGB() * 10 + 4)
}
eventManager.listen(EventPriority.HIGH) { PostMaterialEvent event ->
    DeveloperMaterials.Aluminosilicate.setMaterialRGB(DeveloperMaterials.Aluminosilicate.getMaterialRGB() * 10 + 2)
}
'''
    selected.append({'name': 'competing-native-priorities', 'files': priorities,
        'intent': [{'id': 'priority-result', 'material': request['observeMaterials'][0], 'kind': 'value', 'key': 'color', 'equals': 12345}],
        'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True})
    for label, ordinal, failed_phase, completed in (
            ('block-registration-source-error', 1, 'BLOCK_REGISTERING', ['CONSTRUCTED']),
            ('item-registration-source-error', 2, 'REGISTERING', ['CONSTRUCTED', 'BLOCKS_REGISTERED'])):
        files = dict(corpus[0]['files'])
        files['groovy/classes/RegistrationFailure.groovy'] = ('''package classes
import material.DeveloperMaterials
import gregtech.api.unification.material.properties.PropertyKey
class RegistrationFailure {
    static int calls = 0
    static void visit() {
        calls = calls + 1
        if (calls == ''' + str(ordinal) + ''') {
            DeveloperMaterials.Aluminosilicate.getProperty(PropertyKey.DUST).setHarvestLevel(0)
        }
    }
}
''').encode()
        files['groovy/preInit/Materials.groovy'] += b'''
eventManager.listen(EventPriority.LOWEST) { net.minecraftforge.event.RegistryEvent.Register event ->
    classes.RegistrationFailure.visit()
}
'''
        selected.append({'name': label, 'files': files, 'wantStatus': 'source-error', 'wantIntent': 'incomplete',
                         'wantClean': False, 'locatedError': True, 'failedPhase': failed_phase, 'completed': completed})
    # The exact same programs must still execute and expose native errors when
    # the developer supplies neither observation selectors nor expectations.
    selected += [{**case, 'name': 'diagnostics-' + case['name'], 'diagnosticsReference': case['name'],
                  'wantIntent': 'not-requested'} for case in selected[:6]]
    workspace = dict(corpus[0]['files'])
    config = json.loads(workspace['groovy/runConfig.json'])
    config['loaders']['postInit'] = ['prePostInit/', 'postInit/']
    workspace['groovy/runConfig.json'] = json.dumps(config).encode()
    workspace['groovy/groovy.iml'] = b'<module />\r\n'
    workspace['groovy/postInit/SharedFormula.groovy'] = b'package postInit\nclass SharedFormula { static String formula() { "(Li,Na)AlPO4(F,OH)" } }\n'
    workspace[EDITS] = workspace[EDITS].replace(b"Phosphate.setFormula('(Li,Na)AlPO4(F,OH)', true)",
        b'Phosphate.setFormula(postInit.SharedFormula.formula(), true)')
    workspace['groovy/postInit/DeferredRecipe.groovy'] = b'log.error("DEFERRED_RECIPE_BODY_MUST_NOT_RUN")\n'
    selected.append({'name': 'cross-loader-workspace', 'files': workspace, 'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True})
    removed = dict(workspace); del removed['groovy/postInit/DeferredRecipe.groovy']
    selected.append({'name': 'removed-deferred-recipe', 'files': removed, 'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True})
    tuples = dict(corpus[0]['files'])
    tuples['groovy/classes/AuthoringData.groovy'] = b'''package classes
import groovy.transform.TupleConstructor
class AuthoringData {
    @TupleConstructor
    static class Fuel {
        String name
        int amountRequired
        int duration
        String byproduct
        int byproductAmount
        int tier
    }
    @TupleConstructor
    static class Defaults { String name = 'default'; int amount = 10 }
}
'''
    tuples[EDITS] = tuples[EDITS].replace(b'    static void apply() {', b'''    static void apply() {
        def fuel = new AuthoringData.Fuel('methane', 10, 50, 'carbon_dioxide', 5)
        assert fuel.name == 'methane'
        assert fuel.amountRequired == 10
        assert fuel.duration == 50
        assert fuel.byproduct == 'carbon_dioxide'
        assert fuel.byproductAmount == 5
        assert fuel.tier == 0
        assert new AuthoringData.Fuel().name == null
        assert new AuthoringData.Fuel().amountRequired == 0
        assert new AuthoringData.Defaults().name == 'default'
        assert new AuthoringData.Defaults().amount == 10
        assert new AuthoringData.Defaults('edited', 12).amount == 12
''')
    selected.append({'name': 'native-tuple-constructor-defaults', 'files': tuples, 'wantStatus': 'incomplete', 'wantIntent': 'matched', 'wantClean': True})
    records=dict(workspace)
    records['groovy/postInit/SharedFormula.groovy']=b'''package postInit
record SharedFormula(String formula) {}
'''
    records[EDITS]=records[EDITS].replace(b'postInit.SharedFormula.formula()',
        b"new postInit.SharedFormula('(Li,Na)AlPO4(F,OH)').formula()")
    selected.append({'name':'native-cross-loader-record','files':records,'wantStatus':'incomplete','wantIntent':'matched','wantClean':True})
    # Ordinary custom declarations must survive generated-prefix construction
    # and the original shared MetaItem registration loop without list resets.
    for name, declaration, names in (
            ('custom-generated-coexistence', "addItem(1, 'first'); addItem(2, 'second')", ['first', 'second']),
            ('custom-generated-removal', "addItem(2, 'second')", ['second']),
            ('custom-generated-restored', "addItem(1, 'first'); addItem(2, 'second')", ['first', 'second'])):
        custom=dict(corpus[0]['files'])
        custom['groovy/preInit/CustomItems.groovy']=('''package preInit
import gregtech.api.items.metaitem.StandardMetaItem
import gregtech.api.unification.material.event.PostMaterialEvent
eventManager.listen { PostMaterialEvent event ->
    new StandardMetaItem(2 as short).with {
        setRegistryName('axiom_custom')
        '''+declaration+'''
    }
}
''').encode()
        selected.append({'name':name,'files':custom,'wantStatus':'incomplete','wantIntent':'matched','wantClean':True,'customNames':names})
    inputs = {str(p.relative_to(ROOT)): p.read_bytes() for p in [Path(__file__), ROOT / 'tools/axiom_material_program_cases.py',
        *sorted((ROOT / 'modules/axiom/jvm/src/materialRuntime/java').rglob('*.java')),
        ROOT / 'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialProgram.java',
        ROOT / 'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialProgramAssessment.java',
        ROOT / 'modules/axiom/jvm/src/main/java/research/orthrus/axiom/Main.java',
        ROOT / 'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialExpectations.java',
        ROOT / 'modules/axiom/jvm/src/main/java/research/orthrus/axiom/MaterialProgramComparison.java', intent_path]}
    programs = retain_programs(report, selected)
    def archive_path(name):
        return report.absolute().parent / programs[name]['programArchive']['path']
    with tempfile.TemporaryDirectory(prefix='axiom-installed-material-') as temporary:
        work = Path(temporary)
        for case in selected:
            archive = archive_path(case['name'])
            invocation = dict(request)
            if 'intent' in case:
                invocation.pop('observeMaterials'); invocation['expectations'] = case['intent']
            if case['name'] == 'renamed-five-file-program':
                invocation['observeMaterials'] = [value.replace('developer_', 'pack_') for value in request['observeMaterials']]
                invocation['expectations'] = [{**check, 'material': check['material'].replace('developer_', 'pack_')} for check in request['expectations']]
            if 'diagnosticsReference' in case:
                invocation = {key: invocation[key] for key in ('context', 'contextPolicySha256', 'admissionPolicySha256')}
            command = [str(java / 'bin/java'), '-cp', os.pathsep.join(map(str, jars)),
                'research.orthrus.axiom.Main', 'material-program', '--runtime-home', str(runtime), '--program', str(archive)]
            process = subprocess.run(command, input=json.dumps(invocation).encode(), cwd=work,
                env={'LANG': 'C.UTF-8'}, capture_output=True)
            try:
                result = json.loads(process.stdout)
            except ValueError:
                raise ValueError('Installed engine returned no JSON: ' + process.stderr.decode(errors='replace')[-4000:])
            runs.append({'case': case['name'], **programs[case['name']],
                         'nativeResponseBytes': len(process.stdout),
                         'sourceArchiveSha256': sha256(archive.read_bytes()).hexdigest(),
                         'request': invocation, 'exitCode': process.returncode, 'result': result,
                         'diagnosticsReference': case.get('diagnosticsReference'),
                         'expected': {key: case.get(key, False) for key in ('wantStatus', 'wantIntent', 'wantClean', 'locatedError', 'failedPhase', 'completed', 'customNames')}})
            if reference_runtime is not None and case['name'] in {'complete-program', 'native-pending-fluid', 'competing-native-priorities'}:
                reference_command = list(command)
                reference_command[reference_command.index('--runtime-home') + 1] = str(reference_runtime)
                reference_process = subprocess.run(reference_command, input=json.dumps(invocation).encode(), cwd=work,
                    env={'LANG': 'C.UTF-8'}, capture_output=True)
                reference_result = json.loads(reference_process.stdout)
                fields = ('materials', 'lookups', 'prefixItems', 'materialBlocks', 'materialOres', 'registeredMaterials',
                          'phase', 'scriptIndex', 'effectiveSide', 'physicalSide', 'executionCompleted', 'coverageGaps', 'nativeErrors')
                before = reference_result.get('result', {}).get('execution', {})
                after = result.get('result', {}).get('execution', {})
                observation_comparisons.append({'case': case['name'], 'sourceArchiveSha256': sha256(archive.read_bytes()).hexdigest(),
                    'reference': reference_result, 'referenceExitCode': reference_process.returncode,
                    'comparedFields': fields, 'changedFields': [key for key in fields if key not in before or before[key] != after.get(key)],
                    'meaning': 'host-observation-differential-with-shared-native-dependencies-not-independent-upstream-oracle'})
        for name, candidate_name, baseline_name, intent, wanted in (
                ('same-program-replay', 'complete-program', 'complete-program', request['expectations'], 'unchanged'),
                ('late-registration-outcome-change', 'late-registration', 'complete-program', request['expectations'], 'changed'),
                ('saved-plate-edit', 'required-plate-added', 'complete-program', [plate], 'changed'),
                ('deferred-recipe-removal', 'removed-deferred-recipe', 'cross-loader-workspace', request['expectations'], 'unchanged')):
            invocation = {**request, 'expectations': intent}
            command = [str(java / 'bin/java'), '-cp', os.pathsep.join(map(str, jars)),
                'research.orthrus.axiom.Main', 'material-program', '--runtime-home', str(runtime),
                '--program', str(archive_path(candidate_name)), '--baseline-program', str(archive_path(baseline_name))]
            process = subprocess.run(command, input=json.dumps(invocation).encode(), cwd=work, env={'LANG': 'C.UTF-8'},
                                     capture_output=True)
            paired.append({'case': name, 'expectedComparison': wanted, 'exitCode': process.returncode, 'result': json.loads(process.stdout),
                           'baselineProgram': baseline_name, 'candidateProgram': candidate_name,
                           'request': invocation, 'nativeResponseBytes': len(process.stdout)})
    errors = []
    selected_by_name = {case['name']: case['files'] for case in selected}
    for run in runs:
        result = run['result']; body = result.get('result', {})
        execution = body.get('execution', {})
        bootstrap = body.get('bootstrap', {})
        if not matches_source_acknowledgement(body.get('sourceProgram'), selected_by_name[run['case']]):
            errors.append(run['case'] + ': complete native source acknowledgement differs')
        linkage = bootstrap.get('compilerLinkage', {})
        if bootstrap.get('admitted') is not True or linkage.get('status') != 'observed' or linkage.get('missing') != []:
            errors.append(run['case'] + ': required native compiler linkage was not observed')
        expected = run['expected']; clean = expected['wantClean']
        if expected['customNames']:
            custom=execution.get('customMetaItems',{}).get('items',[])
            if (len(custom)!=1 or custom[0].get('forgeRegistered') is not True
                    or custom[0].get('nativeClassSpace') is not True or custom[0].get('registryName')!='gregtech:axiom_custom'
                    or [value['name'] for value in custom[0].get('variants',[])]!=expected['customNames']
                    or execution.get('contentProgress',{}).get('phase')!='COMPLETE'
                    or not execution.get('vocabulary',{}).get('gt-prefix-items')):
                errors.append(run['case']+': original custom/generated shared-registry coexistence missing')
        if result.get('status') != expected['wantStatus'] or run['exitCode'] != (4 if expected['wantStatus'] == 'incomplete' else 1):
            errors.append(run['case'] + ': outcome differs')
        if body.get('expectations', {}).get('status') != expected['wantIntent']:
            errors.append(run['case'] + ': developer intent result differs')
        assessment = body.get('assessment', {})
        checks = body.get('expectations', {}).get('checks', [])
        counts = {key: sum(row.get('status') == key for row in checks)
                  for key in ('matched', 'mismatch', 'unsupported', 'not-evaluated')}
        reasons = {row['code'] for row in assessment.get('reasons', [])}
        if (assessment.get('schema') != 'axiom.material-program-assessment.v1'
                or assessment.get('status') != result.get('status') or assessment.get('qualifiedValidity') is not False
                or assessment.get('intentCounts') != counts or 'qualification-pending' not in reasons):
            errors.append(run['case'] + ': decision assessment differs from retained evidence')
        if run['case'] == 'mismatch-with-unsupported-family' and (counts != {
                'matched': 0, 'mismatch': 1, 'unsupported': 1, 'not-evaluated': 0}
                or not {'expectation-mismatch', 'expectations-incomplete'} <= reasons):
            errors.append(run['case'] + ': known intent mismatch was hidden by unresolved coverage')
        if run['case'] == 'complete-program' and reasons != {'qualification-pending'}:
            errors.append(run['case'] + ': clean native observations were confused with a context gap')
        if run['case'] in {'cross-loader-workspace', 'removed-deferred-recipe','native-cross-loader-record'}:
            scope = body.get('sourceScope', {})
            if (scope.get('selectedLoader') != 'preInit' or scope.get('deferredLoaders') != ['postInit']
                    or 'deferred-loaders' not in reasons or assessment.get('coverage') != 'incomplete'
                    or 'DEFERRED_RECIPE_BODY_MUST_NOT_RUN' in json.dumps(execution.get('diagnostics', []))):
                errors.append(run['case'] + ': deferred source availability was conflated with execution')
            if 'groovy/groovy.iml' not in selected_by_name[run['case']]:
                errors.append(run['case'] + ': workspace metadata was omitted')
        if clean and (not execution.get('cleanObservation') or execution.get('registeredMaterials') != 605):
            errors.append(run['case'] + ': native execution did not complete cleanly')
        if expected['locatedError'] and not any(d['severity'] == 'error' and d['locations'] for d in execution.get('diagnostics', [])):
            errors.append(run['case'] + ': missing native source location')
        if run['case'] == 'unknown-addon' and not execution.get('nativeCompilationFailure'):
            errors.append(run['case'] + ': compiler failure must remain a context gap')
        if run['case']=='caught-unqualified-operation':
            if (execution.get('coverageGaps')!=['material.localization'] or execution.get('executionCompleted') is not True
                    or execution.get('phase')!='FROZEN' or execution.get('registeredMaterials')!=605
                    or body.get('nativeOutcome')!='incomplete' or assessment.get('coverage')!='incomplete'):
                errors.append(run['case']+': caught ordinary unsupported call did not retain sticky coverage and native continuation')
        if run['case'] == 'unknown-addon' and not any(finding.get('location', {}).get('path') == 'groovy/material/DeveloperMaterials.groovy'
                for diagnostic in execution.get('diagnostics', []) for finding in diagnostic.get('compilerFindings', [])):
            errors.append(run['case'] + ': native compiler source location missing')
        if run['case'] == 'absent-and-aliased-identities' and not any(row.get('resolved') == request['observeMaterials'][0]
                and row['exactIdentity'] is False and row['storageRegistry'] == 'gregtech' for row in execution.get('lookups', [])):
            errors.append(run['case'] + ': original namespace fallback observation differs')
        if body.get('groovyExecutionQualified') is not False or body.get('wholePackParity') is not False:
            errors.append(run['case'] + ': qualification boundary differs')
        if expected['failedPhase']:
            progress = execution.get('contentProgress', {})
            if (progress.get('phase') != 'FAILED' or progress.get('failedPhase') != expected['failedPhase']
                    or [row['phase'] for row in progress.get('completedCheckpoints', [])] != expected['completed']
                    or progress.get('interruptedState', {}).get('registeredBlocks', 0) <= 0
                    or 'Harvest Level must be greater than zero!' not in execution.get('nativeException', '')
                    or 'prefixItems' in execution or execution.get('executionCompleted') is not False):
                errors.append(run['case'] + ': partial native content state or original failure was lost')
            if not execution.get('deferredWork', {}).get('prefixProcessing'):
                errors.append(run['case'] + ': stopped native queues were not retained')
        if run['diagnosticsReference'] is not None:
            reference = next(row for row in runs if row['case'] == run['diagnosticsReference'])
            reference_body = reference['result']['result']; reference_execution = reference_body['execution']
            if (body.get('nativeOutcome') != reference_body.get('nativeOutcome')
                    or checks or execution.get('materials') != [] or execution.get('lookups') != []
                    or any(execution.get(key) != reference_execution.get(key) for key in
                           ('phase', 'registeredMaterials', 'executionCompleted', 'cleanObservation',
                            'nativeCompilationFailure', 'candidateLinkageFailure', 'coverageGaps'))):
                errors.append(run['case'] + ': omitting observations changed native execution or lost coverage')
            if reference_execution.get('diagnostics') and not execution.get('diagnostics'):
                errors.append(run['case'] + ': native diagnostics disappeared without expectations')
        if run['case'] == 'property-setter-error':
            work = execution.get('deferredWork', {})
            if execution.get('contentProgress', {}).get('phase') != 'NOT_STARTED' or work.get('phase') != 'CLOSED' or 'prefixProcessing' in work:
                errors.append(run['case'] + ': unvisited content was confused with an empty completed queue')
        if clean:
            lifecycle = execution.get('lifecycle', {}).get('checkpoints', [])
            phases = ['PRE', 'PRE', 'PRE', 'OPEN', 'OPEN', 'OPEN', 'CLOSED', 'CLOSED', 'FROZEN', 'FROZEN']
            if [row['phase'] for row in lifecycle] != phases or any(row['activeOwner'] != 'gregtech' for row in lifecycle):
                errors.append(run['case'] + ': native host phase/owner restoration differs')
            work = execution.get('deferredWork', {})
            if work.get('recipeHandlersExecuted') is not False or work.get('fluidRegistrationExecuted') is not False:
                errors.append(run['case'] + ': pending work was implicitly executed')
            if run['case'] == 'native-pending-fluid':
                row, = [row for row in work.get('fluids', []) if row['material'] == fluid['material']]
                if row.get('registrationCompleted') is not False or row.get('stored') or not row.get('queued'):
                    errors.append(run['case'] + ': native builder and stored-fluid state conflated')
    for run in paired:
        body = run['result']['result']; comparison = body.get('comparison', {})
        expected_exit = 1 if run['case'] == 'late-registration-outcome-change' else 4
        if run['exitCode'] != expected_exit or comparison.get('status') != run['expectedComparison']:
            errors.append(run['case'] + ': fresh-worker comparison differs')
        if comparison.get('workers') != 'separate-fresh-native-class-spaces':
            errors.append(run['case'] + ': native worker separation absent')
        for side, selected_name in [('baseline', run['baselineProgram']), ('candidate', run['candidateProgram'])]:
            if not matches_source_acknowledgement(body.get(side, {}).get('result', {}).get('sourceProgram'), selected_by_name[selected_name]):
                errors.append(run['case'] + ': paired source acknowledgement differs for ' + side)
        if run['case'] == 'deferred-recipe-removal':
            source_delta = body.get('sourceComparison', {})
            if (source_delta.get('status') != 'requires-retained-inventories'
                    or source_delta.get('baselineSourceSha256') != source_acknowledgement(selected_by_name[run['baselineProgram']])['sha256']
                    or source_delta.get('candidateSourceSha256') != source_acknowledgement(selected_by_name[run['candidateProgram']])['sha256']
                    or any(key in source_delta for key in ('added', 'removed', 'modified'))
                    or body['candidate']['result']['assessment']['coverage'] != 'incomplete'):
                errors.append(run['case'] + ': native source custody failed or absent inline inventory was treated as empty; saved file deltas belong to verified retained inputs')
        if run['case'] == 'same-program-replay' and comparison.get('baselineSemanticSha256') != comparison.get('candidateSemanticSha256'):
            errors.append(run['case'] + ': native semantic replay differs')
        if run['case'] == 'late-registration-outcome-change':
            before = body['baseline']['result']; after = body['candidate']['result']
            if (before.get('nativeOutcome') != 'completed-without-observed-error' or after.get('nativeOutcome') != 'source-error'
                    or before['execution']['lookups'] != after['execution']['lookups']
                    or before['execution']['registeredMaterials'] != after['execution']['registeredMaterials']
                    or {row['field'] for row in comparison.get('changedSections', [])} != {'nativeOutcome', 'cleanObservation'}):
                errors.append(run['case'] + ': native error was not distinguished from unchanged registry state')
        if run['case'] == 'saved-plate-edit' and (body['baseline']['result']['expectations']['status'] != 'mismatch'
                or body['candidate']['result']['expectations']['status'] != 'matched'):
            errors.append(run['case'] + ': intent transition differs')
    for comparison in observation_comparisons:
        if comparison['referenceExitCode'] != 4 or comparison['changedFields'] or not comparison['reference'].get('result', {}).get('execution', {}).get('cleanObservation'):
            errors.append(comparison['case'] + ': enabling native state observations changed existing native behavior')
    receipt = {'schema': 'axiom.material-runtime-smoke.v1', 'status': 'failed' if errors else 'observed-not-qualified',
        'errors': errors, 'runs': runs, 'pairedRuns': paired, 'programs': programs, 'observationComparisons': observation_comparisons,
        'referenceRuntimeManifestSha256': sha256(reference_raw).hexdigest() if reference_raw is not None else None,
        'engineManifestSha256': sha256(engine_raw).hexdigest(),
        'runtimeManifestSha256': sha256(runtime_raw).hexdigest(), 'jvm': jvm,
        'qualificationInputs': {path: sha256(raw).hexdigest() for path, raw in inputs.items()},
        'groovyExecutionQualified': False, 'wholePackParity': False}
    if any((ROOT / path).read_bytes() != raw for path, raw in inputs.items()):
        raise ValueError('Smoke sources changed during execution')
    if engine_inputs(engine, LOCK.read_bytes())[0] != engine_raw or (runtime / 'runtime.json').read_bytes() != runtime_raw:
        raise ValueError('Installed inputs changed during execution')
    if reference_raw is not None and (reference_runtime / 'runtime.json').read_bytes() != reference_raw:
        raise ValueError('Reference runtime changed during observation comparison')
    for name, row in programs.items():
        raw = archive_path(name).read_bytes()
        if len(raw) != row['programArchive']['size'] or sha256(raw).hexdigest() != row['programArchive']['sha256']:
            raise ValueError('Retained smoke source changed during execution: ' + name)
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as out:
        json.dump(receipt, out, indent=2); out.write('\n')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--reference-runtime', type=Path, help='Explicit prior host without the new observations, sharing identical native dependencies')
    parser.add_argument('--saved-program', type=Path, help='Complete ordinary archive for original native bootstrap command checks')
    parser.add_argument('--saved-case', action='append', help='Run a named saved-program case; repeat to select several')
    args = parser.parse_args(argv)
    if args.saved_program is not None:
        if args.reference_runtime is not None:
            parser.error('Original saved-program checks do not compare prepared runtime contexts')
        value = smoke_original(args.java_home, args.engine_home, args.runtime_home, args.saved_program, args.report, args.saved_case)
    else:
        if args.saved_case:
            parser.error('--saved-case requires --saved-program')
        value = smoke(args.java_home, args.engine_home, args.runtime_home, args.report, args.reference_runtime)
    print(json.dumps({'status': value['status'], 'runs': len(value['runs']), 'pairs': len(value['pairedRuns']), 'errors': value['errors']}))
    return 1 if value['errors'] else 0


def check_original_config_witnesses(execution, enabled):
    """Check the selected GT configuration's original Diamond form effects."""
    rows = execution.get('registrationEffects', {}).get('prefixItems', {}).get('witnesses', [])
    errors = []
    for prefix in ('gemChipped', 'gemFlawed'):
        selected = [row for row in rows if row.get('material') == 'gregtech:diamond' and row.get('prefix') == prefix]
        if len(selected) != 1 or len(selected[0].get('generated', [])) != (1 if enabled else 0):
            errors.append('native saved configuration did not produce the expected Diamond form: ' + prefix)
        elif enabled:
            stack = selected[0]['generated'][0]
            if (stack.get('empty') is not False or stack.get('registryIdentity') is not True
                    or stack.get('materialIdentity') is not True or stack.get('item') != selected[0].get('generator')):
                errors.append('configured Diamond form lacks original item/material identity: ' + prefix)
    return errors


def smoke_original(java, engine, runtime, program, report, case_names=None):
    """Check the existing material command through its original construction/Groovy path."""
    from axiom_runtime import checked_path
    from axiom_native_root_stage import check_preinit_result, expand_execution_evidence
    java, engine, runtime, program = [p.resolve(strict=True) for p in (java, engine, runtime, program)]
    if report.exists() or report.absolute().with_name(report.name + '.inputs').exists():
        raise ValueError('Smoke receipt must be new')
    jvm = verify_runtime(java)
    engine_raw, _, jars = engine_inputs(engine, LOCK.read_bytes())
    runtime_raw = (runtime / 'runtime.json').read_bytes(); manifest = json.loads(runtime_raw)
    selection = manifest['nativeInitialization']
    if selection['scope'] != 'original-preinit-through-non-recipe-registry-events':
        raise ValueError('Original material smoke requires the current native initialization boundary')
    for row in manifest['files']:
        checked_path(runtime, row)
    if any(row['path'] in ('saved-program.zip', 'program.json') for row in manifest['files']):
        raise ValueError('Production runtime must not package an obsolete saved program')
    with zipfile.ZipFile(runtime / manifest['classpath'][0]) as archive:
        if any(name.endswith(('/GroovyNativeBootstrap.class', '/NativeMaterialProgram.class')) for name in archive.namelist()):
            raise ValueError('Original production runtime contains the superseded prepared bootstrap')
    with zipfile.ZipFile(program) as archive:
        baseline = {name: archive.read(name) for name in archive.namelist()}
    diagnostic_path = 'groovy/preInit/AxiomCommandDiagnostic.groovy'
    missing_path = 'groovy/preInit/AxiomCommandMissingImport.groovy'
    if diagnostic_path in baseline or missing_path in baseline:
        raise ValueError('Original smoke additions collide with saved source')
    material_path = 'groovy/material/FirstDegreeMaterialsA.groovy'
    material_source = baseline[material_path]
    calcium = (b"CalciumHydroxide = new Material.Builder(8100, SuSyUtility.susyId('calcium_hydroxide'))\n"
               b"                .dust()\n                .components(Calcium, Oxygen * 2, Hydrogen * 2)\n"
               b"                .color(0xcfcabc)")
    potassium = b'.dust().liquid(new FluidBuilder().temperature(683).basic())'
    if material_source.count(calcium) != 1 or material_source.count(potassium) != 1:
        raise ValueError('Saved material effect witnesses differ from the selected ordinary program')
    changed = material_source.replace(calcium, calcium.replace(b'.dust()', b'.dust().flags(GENERATE_PLATE)')
                                     .replace(b'0xcfcabc', b'0xcfcabd')).replace(potassium, potassium.replace(b'683', b'684'))
    failed = material_source.replace(calcium, calcium.replace(b'.color(0xcfcabc)', b".color('axiom invalid color')"))
    config_path = 'config/gregtech/gregtech.cfg'
    config_source = baseline[config_path]
    config_flag = b'B:generateLowQualityGems=false'
    if config_source.count(config_flag) != 1:
        raise ValueError('Saved native gem-generation configuration differs from the selected ordinary program')
    config_changed = config_source.replace(config_flag, b'B:generateLowQualityGems=true')
    helper_path = 'groovy/classes/AxiomSavedContent.groovy'
    item_path = 'groovy/preInit/RegisterMetaItems.groovy'
    item_source = baseline[item_path]
    material_import = b'package material\n'
    material_call = b'    static void register() {\n'
    item_anchor = b'        setRegistryName("meta_item_2")\n'
    if (helper_path in baseline or material_source.count(material_import) != 1
            or material_source.count(material_call) != 1 or item_source.count(item_anchor) != 1):
        raise ValueError('Saved content addition anchors differ from the selected ordinary program')
    helper = (b'package classes\n\n'
              b'import gregtech.api.unification.material.Material\n'
              b'import gregtech.api.fluids.FluidBuilder\n'
              b'import supersymmetry.api.util.SuSyUtility\n'
              b'import static gregtech.api.unification.material.info.MaterialFlags.GENERATE_PLATE\n\n'
              b'class AxiomSavedContent {\n'
              b'    static void register() {\n'
              b"        def base = new Material.Builder(31900, SuSyUtility.susyId('axiom_saved_content'))\n"
              b'                .dust().liquid(new FluidBuilder().temperature(333))\n'
              b'                .flags(GENERATE_PLATE).color(0x665544).build()\n'
              b"        new Material.Builder(31901, SuSyUtility.susyId('axiom_saved_dependent'))\n"
              b'                .dust().components(base * 1).colorAverage().build()\n'
              b'    }\n}\n')
    item_addition = item_anchor + b'        addItem(30000, "axiom.saved_item")\n'
    added = {**baseline, helper_path: helper,
             material_path: material_source.replace(material_import, material_import + b'\nimport classes.AxiomSavedContent\n')
                 .replace(material_call, material_call + b'        AxiomSavedContent.register()\n'),
             item_path: item_source.replace(item_anchor, item_addition)}
    modified = {**added, helper_path: helper.replace(b'temperature(333)', b'temperature(444)')
                .replace(b'0x665544', b'0x887766'),
                item_path: added[item_path].replace(b'addItem(30000, "axiom.saved_item")',
                                                   b'addItem(30000, "axiom.saved_item").setMaxStackSize(16)')}
    broken_reference = {path: raw for path, raw in modified.items() if path != helper_path}
    item_failed = {**modified, item_path: modified[item_path].replace(b'setMaxStackSize(16)', b'setMaxStackSize(0)', 1)}
    fluid_failed = {**modified, helper_path: modified[helper_path].replace(b'temperature(444)', b'temperature(0)')}
    logged_error = {**modified, helper_path: modified[helper_path].replace(b'components(base * 1)', b'components(base, 1)')}
    selected = [{'name': 'ordinary-saved-baseline', 'files': baseline},
                {'name': 'saved-material-form-fluid-change', 'files': {**baseline, material_path: changed}},
                {'name': 'saved-native-config-change', 'files': {**baseline, config_path: config_changed}},
                {'name': 'saved-content-addition', 'files': added},
                {'name': 'saved-content-modification', 'files': modified},
                {'name': 'saved-content-broken-reference', 'files': broken_reference},
                {'name': 'saved-item-native-failure', 'files': item_failed},
                {'name': 'saved-fluid-native-failure', 'files': fluid_failed},
                {'name': 'saved-content-logged-error', 'files': logged_error},
                {'name': 'saved-content-correction', 'files': modified},
                {'name': 'saved-content-deletion', 'files': baseline},
                {'name': 'saved-material-native-failure', 'files': {**baseline, material_path: failed}},
                {'name': 'saved-file-only-error', 'files': {**baseline, diagnostic_path:
                    b"log.warn('axiom command warning')\nlog.error('axiom command file-only error')\n"}},
                {'name': 'saved-missing-import', 'files': {**baseline, missing_path: b'import axiom.missing.NativeInput\n'}},
                {'name': 'saved-correction', 'files': baseline}]
    if case_names is not None:
        unknown = set(case_names) - {case['name'] for case in selected}
        if unknown:
            raise ValueError('Unknown saved-program cases: ' + ', '.join(sorted(unknown)))
        selected = [case for case in selected if case['name'] in case_names]
    retained = retain_programs(report, selected)
    request = {key: manifest[key] for key in ('contextPolicySha256', 'admissionPolicySha256')}
    request['context'] = manifest['context']['id']
    inputs = {str(Path(__file__).relative_to(ROOT)): sha256(Path(__file__).read_bytes()).hexdigest(), **manifest['recipeInputs']}
    runs = []; errors = []; descriptor = json.loads((runtime / 'root-class-space.json').read_bytes())
    destination = report.absolute().with_name(report.name + '.inputs')
    for case in selected:
        archive = report.absolute().parent / retained[case['name']]['programArchive']['path']
        command = [str(java / 'bin/java'), '-cp', os.pathsep.join(map(str, jars)),
                   'research.orthrus.axiom.Main', 'material-program', '--runtime-home', str(runtime), '--program', str(archive)]
        invocation = {'case': case['name'], 'command': command, 'request': request,
                      'sourceProgram': source_acknowledgement(case['files'])}
        (destination / (case['name'] + '-invocation.json')).write_text(json.dumps(invocation, indent=2) + '\n')
        started = time.monotonic()
        process = subprocess.run(command, input=json.dumps(request).encode(), cwd=destination, env={'LANG': 'C.UTF-8'},
                                 capture_output=True)
        (destination / (case['name'] + '-stdout.json')).write_bytes(process.stdout)
        (destination / (case['name'] + '-stderr.log')).write_bytes(process.stderr)
        response = json.loads(process.stdout)
        run = {**invocation, 'exitCode': process.returncode, 'seconds': time.monotonic() - started,
               'outputBytes': len(process.stdout), 'response': response}
        runs.append(run)
        (destination / (case['name'] + '-execution.json')).write_text(json.dumps(run, indent=2) + '\n')
        body = response.get('result', {}); execution = expand_execution_evidence(body.get('execution', {})); native = execution.get('nativeInitialization', {})
        case_errors = []
        positive = case['name'] in ('ordinary-saved-baseline', 'saved-material-form-fluid-change', 'saved-native-config-change', 'saved-correction',
                                    'saved-content-addition', 'saved-content-modification', 'saved-content-correction', 'saved-content-deletion')
        if (process.returncode != (0 if positive else 1) or response.get('status') != ('accepted' if positive else 'source-error')
                or body.get('bootstrap', {}).get('route') != 'original-native-initialization'
                or not matches_source_acknowledgement(body.get('sourceProgram'), case['files'])
                or body.get('initialization', {}).get('status') != ('completed' if positive else 'native-failed')
                or body.get('initialization', {}).get('nativeScopeQualified') is not True
                or body.get('initialization', {}).get('recipeEffectsChecked') is not False
                or execution.get('coverageGaps') != []
                or body.get('candidateCompilationStarted') is not True
                or native.get('materialInitializationComplete') is not False):
            case_errors.append('source, production route or scoped native startup result differs')
        if positive:
            if execution.get('executionCompleted') is not True or execution.get('cleanObservation') is not True:
                case_errors.append('native startup did not complete with a clean observation')
            native_envelope = {'schema': 'axiom.result.v1', 'operation': 'native-preinit-stage', 'status': 'incomplete',
                'scope': 'original-server-preinit-stage-not-material-initialization', 'result': native,
                'diagnostics': native.get('nativeDiagnostics', []) + native.get('groovyDiagnostics', [])}
            observed = check_preinit_result(native_envelope, descriptor, selection['nativeContext'], source_acknowledgement(case['files']))
            if observed:
                case_errors.extend(observed)
            for family in ('materials', 'fluids', 'prefixItems', 'materialBlocks', 'oreBlocks'):
                catalog = execution.get('registrationEffects', {}).get(family, {})
                if catalog.get('status') != 'observed' or catalog.get('inventoryComplete') is not True or not catalog.get('entries'):
                    case_errors.append('complete native effect catalog missing: ' + family)
            bindings = execution.get('registrationEffects', {}).get('materialFluidBindings', {})
            if bindings.get('bindingsComplete') is not True or bindings.get('affectingGaps') != []:
                case_errors.append('native material/fluid bindings are incomplete')
            if native.get('nativeGroovyErrors') != [] or body.get('initialization', {}).get('nativeErrorObserved') is not False:
                case_errors.append('unchanged native program logged errors')
            if case['name'] in ('ordinary-saved-baseline', 'saved-native-config-change', 'saved-correction'):
                case_errors.extend(check_original_config_witnesses(execution, case['name'] == 'saved-native-config-change'))
            if case['name'] in ('ordinary-saved-baseline', 'saved-content-addition', 'saved-content-modification',
                                'saved-content-correction', 'saved-content-deletion'):
                present = case['name'] not in ('ordinary-saved-baseline', 'saved-content-deletion')
                effects = execution.get('registrationEffects', {})
                for family, names in [('materials', ('susy:axiom_saved_content', 'susy:axiom_saved_dependent')),
                                      ('fluids', ('axiom_saved_content',))]:
                    for name in names:
                        if (name in effects.get(family, {}).get('entries', {})) != present:
                            case_errors.append('saved native content membership differs: ' + name)
                variants = [(item, variant) for item in execution.get('customMetaItems', {}).get('items', [])
                            for variant in item.get('variants', []) if variant.get('name') == 'axiom.saved_item']
                if len(variants) != (1 if present else 0):
                    case_errors.append('saved custom item membership differs')
                elif present:
                    item, variant = variants[0]
                    stack_size = 64 if case['name'] == 'saved-content-addition' else 16
                    if (item.get('registryName') != 'gregtech:meta_item_2' or item.get('forgeRegistered') is not True
                            or variant.get('ownerIdentity') is not True or variant.get('nameLookupIdentity') is not True
                            or variant.get('meta') != 30000 or variant.get('maxStackSize') != stack_size):
                        case_errors.append('saved custom item native owner or property differs')
        else:
            if execution.get('cleanObservation') is not False:
                case_errors.append('native source failure was reported as clean')
            if (body.get('initialization', {}).get('nativeErrorObserved') is not True
                    or case['name'] in ('saved-file-only-error', 'saved-missing-import')
                    and native.get('groovyInitializationReady') is not False):
                case_errors.append('native saved failure was not retained')
            rows = execution.get('diagnostics', [])
            if case['name'] == 'saved-file-only-error':
                for severity, marker, line in [('warning', 'axiom command warning', 1), ('error', 'axiom command file-only error', 2)]:
                    if not any(row.get('channel') == 'groovy-log' and row.get('severity') == severity and marker in row.get('message', '')
                               and any(location.get('path') == diagnostic_path and location.get('line') == line
                                       for location in row.get('locations', [])) for row in rows):
                        case_errors.append('native file-only diagnostic or saved line missing: ' + severity)
            elif case['name'] == 'saved-material-native-failure':
                # The original compiler attributes this chained call to line 127;
                # retain that native frame instead of inventing the edited token's line.
                if not any(any(location.get('path') == material_path and location.get('line') == 127
                               for location in row.get('locations', [])) for row in rows):
                    case_errors.append('native material failure saved location missing')
            elif case['name'] in ('saved-item-native-failure', 'saved-fluid-native-failure'):
                path, marker = ((item_path, 'Cannot set Max Stack Size to negative or zero value.')
                                if case['name'] == 'saved-item-native-failure' else (helper_path, 'temperature must be > 0'))
                if not any(cause.get('type') == 'java.lang.IllegalArgumentException' and marker == cause.get('message')
                           and any(location.get('path') == path for location in cause.get('locations', []))
                           for row in rows for cause in row.get('causality', {}).get('exceptions', [])):
                    case_errors.append('native saved content failure cause or source location missing')
            elif case['name'] == 'saved-content-logged-error':
                if not any(row.get('channel') == 'groovy-log' and row.get('severity') == 'error'
                           and 'Tried to use old method for material components' in row.get('message', '')
                           and any(location.get('path') == helper_path for location in row.get('locations', []))
                           for row in rows):
                    case_errors.append('native logged component error or saved location missing')
                if not all(name in execution.get('registrationEffects', {}).get('materials', {}).get('entries', {})
                           for name in ('susy:axiom_saved_content', 'susy:axiom_saved_dependent')):
                    case_errors.append('native continuation after logged component error was not observed')
            elif case['name'] == 'saved-content-broken-reference':
                if not any('AxiomSavedContent' in finding.get('message', '')
                           and finding.get('location', {}).get('path') == material_path
                           for row in rows for finding in row.get('compilerFindings', [])):
                    case_errors.append('native dependent source compiler failure missing')
            elif not any(finding.get('location', {}).get('path') == missing_path and finding['location'].get('line') == 1
                         for row in rows for finding in row.get('compilerFindings', [])):
                case_errors.append('native missing-import compiler location missing')
        errors.extend(case['name'] + ': ' + message for message in case_errors)
    for path, digest in inputs.items():
        checked_path(ROOT, {'path': path, 'sha256': digest})
    for row in manifest['files']:
        checked_path(runtime, row)
    if engine_inputs(engine, LOCK.read_bytes())[0] != engine_raw or (runtime / 'runtime.json').read_bytes() != runtime_raw:
        raise ValueError('Installed inputs changed during original native checks')
    receipt = {'schema': 'axiom.material-runtime-smoke.v1', 'status': 'failed' if errors else 'observed-not-qualified',
        'scope': 'production original startup scope and current native effects; installed MVP and recipe qualification remain incomplete',
        'errors': errors, 'runs': runs, 'pairedRuns': [], 'programs': retained, 'jvm': jvm,
        'engineManifestSha256': sha256(engine_raw).hexdigest(), 'runtimeManifestSha256': sha256(runtime_raw).hexdigest(),
        'qualificationInputs': inputs, 'groovyExecutionQualified': False, 'wholePackParity': False}
    with report.open('x') as out:
        json.dump(receipt, out, indent=2); out.write('\n')
    return receipt


if __name__ == '__main__':
    raise SystemExit(main())
