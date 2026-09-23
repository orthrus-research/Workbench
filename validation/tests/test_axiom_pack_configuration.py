"""Regression-lane input/assessment tests; not native execution qualification."""
from copy import deepcopy
from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_pack_configuration_conformance as lane
import axiom_pack_property_conformance as properties
import axiom_pack_lifecycle_conformance as lifecycle
import axiom_pack_mapper_conformance as mappers


class PackConfigurationTests(unittest.TestCase):
    def test_cases_preserve_source_and_only_edit_the_setting_in_fixture_copies(self):
        raw = b'# unchanged prefix\r\n' + lane.ANCHOR + b'\n# unchanged suffix\n'
        cases = lane.corpus(raw)
        self.assertEqual(['enabled', 'disabled', 'default', 'invalid', 'missing', 'enabled-again',
                          'acidic-enabled', 'acidic-disabled', 'acidic-native-error'], [row['name'] for row in cases])
        base = cases[0]['files']
        self.assertEqual(raw, base[lane.CONFIG])
        self.assertEqual(base, cases[5]['files'])
        self.assertNotIn(lane.CONFIG, cases[4]['files'])
        for case in cases[:6]:
            self.assertEqual({k: v for k, v in base.items() if k != lane.CONFIG},
                             {k: v for k, v in case['files'].items() if k != lane.CONFIG})
        for case in cases[6:]:
            self.assertNotIn(b'.basic()', case['files'][lane.EDITS])
            self.assertIn(b'.acidic(', case['files'][lane.EDITS])
        for bad in (b'', raw + raw):
            with self.assertRaises(ValueError):
                lane.corpus(bad)

    def response(self, case):
        ids=['native-context-setup', 'sussypatches-configuration', 'sussypatches-native-registration', 'gt-groovy-compatibility',
             'susy-groovy-compatibility', 'susy-native-subscriber-registration', 'native-object-mapper-bindings', 'pre-init-scripts', 'material-registry-event',
             'gt-material-catalog', 'material-event', 'post-material-event', 'material-freeze']
        stop='post-material-event' if case['name'] in {'disabled', 'acidic-native-error'} else None
        trace=[]; stopped=False
        for index, name in enumerate(ids, 1):
            row={'id': name, 'status': 'not-reached' if stopped else 'threw' if name == stop else 'returned'}
            if not stopped:
                row.update(sequence=index, elapsedNanos=1)
            stopped |= name == stop
            trace.append(row)
        trace += [{'id': name, 'status': 'deferred'} for name in
                  ('addon-material-hooks', 'post-init-recipes', 'fluid-registration-and-prefix-processing', 'generated-material-content')]
        return {'status': 'incomplete', 'result': {'nativeOutcome': 'incomplete', 'wholePackParity': False,
            'sourceProgram': lane.source_acknowledgement(case['files']), 'execution': {
                'initialization': {'schema': 'axiom.native-initialization-trace.v1', 'steps': trace},
                'phase': 'FROZEN', 'nativeErrors': [], 'candidateAdmissionViolations': [],
                'contentProgress': {'phase': 'NOT_STARTED'},
                'susySubscriber': {'subscriber': 'supersymmetry.common.CommonProxy', 'registrantOwner': 'susy',
                    'activeOwnerRestored': True, 'registrationMethod': 'native-EventBus.register-complete-class',
                    'discoveryOrderQualified': False, 'handlers': [{'method': name, 'priority': 'HIGH'}
                        for name in ('registerMaterials', 'postRegisterMaterials')]},
                'packConfiguration': {'recipeInfo': True, 'recipeInfoProperty': {'value': 'true', 'isBooleanValue': True},
                    'inputSha256': sha256(case['files'][lane.CONFIG]).hexdigest(), 'wholePackConfigurationQualified': False,
                    'gtObjectMappers': ['element', 'material', 'metaitem', 'oreprefix', 'recipemap'],
                    'callback': 'supersymmetry.integration.groovyscript.GrSModule#onCompatLoaded'}}}}

    def test_result_requires_native_observation_and_keeps_incomplete_boundary(self):
        case = lane.corpus(lane.ANCHOR)[0]; good = self.response(case)
        self.assertEqual([], lane.check_result(case, good, 4))
        for key, value in [('wholePackParity', True), ('nativeOutcome', 'accepted'), ('sourceProgram', {})]:
            bad = deepcopy(good); bad['result'][key] = value
            self.assertTrue(lane.check_result(case, bad, 4))
        for key, value in [('phase', 'OPEN'), ('nativeErrors', ['error']), ('packConfiguration', None),
                           ('candidateAdmissionViolations', ['FluidBuilder#basic'])]:
            bad = deepcopy(good); bad['result']['execution'][key] = value
            self.assertTrue(lane.check_result(case, bad, 4))
        self.assertTrue(lane.check_result(case, good, 0))

    def test_initialization_trace_does_not_turn_unvisited_work_into_success(self):
        case=lane.corpus(lane.ANCHOR)[1]; execution=self.response(case)['result']['execution']
        self.assertEqual([], lane.check_initialization(case, execution))
        for name, change in [('post-material-event', {'status': 'returned'}),
                             ('generated-material-content', {'sequence': 10, 'elapsedNanos': 1}),
                             ('post-init-recipes', {'status': 'returned'}),
                             ('gt-groovy-compatibility', {'elapsedNanos': -1})]:
            bad=deepcopy(execution)
            next(row for row in bad['initialization']['steps'] if row['id']==name).update(change)
            self.assertTrue(lane.check_initialization(case, bad))

    def test_disabled_extension_requires_original_error_and_saved_line(self):
        case = lane.corpus(lane.ANCHOR)[1]; response = self.response(case)
        execution = response['result']['execution']
        execution['packConfiguration'].update(recipeInfo=False, recipeInfoProperty={'value': 'false', 'isBooleanValue': True})
        self.assertTrue(lane.check_result(case, response, 4))
        execution['nativeException'] = 'groovy.lang.MissingMethodException'
        execution['diagnostics'] = [{'locations': [{'path': lane.EDITS, 'line': 10}]}]
        self.assertEqual([], lane.check_result(case, response, 4))

    def test_native_subscriber_and_deferred_content_cannot_be_promoted(self):
        case=lane.corpus(lane.ANCHOR)[0]; good=self.response(case)
        for key,value in [('susySubscriber', {}), ('contentProgress', {'phase':'COMPLETE'}),
                          ('vocabulary', {'gt-material-blocks': ['block']})]:
            bad=deepcopy(good);bad['result']['execution'][key]=value
            self.assertTrue(lane.check_result(case,bad,4))
        for key,value in [('discoveryOrderQualified', True), ('registrantOwner', 'gregtech'), ('activeOwnerRestored', False)]:
            bad=deepcopy(good);bad['result']['execution']['susySubscriber'][key]=value
            self.assertTrue(lane.check_result(case,bad,4))


class PackConfigurationCustodyTests(unittest.TestCase):
    def environment(self, root):
        paths = {name: root / name for name in ('java', 'engine', 'runtime', 'pack')}
        for path in paths.values(): path.mkdir()
        manifest = {'context': {'id': 'supersymmetry:material-authoring-pack'},
                    'contextPolicySha256': 'a' * 64, 'admissionPolicySha256': 'b' * 64}
        (paths['runtime'] / 'runtime.json').write_text(json.dumps(manifest))
        return paths

    def cases(self):
        files = {'groovy/runConfig.json': b'{"saved":"original"}\r\n',
                 'groovy/preInit/Probe.groovy': b'// saved source\r\n',
                 'config/nested/é😀".bin': b'\x00\xff\x80original\r\n'}
        return [{'name': 'first', 'files': files}, {'name': 'repeat', 'files': dict(files)}]

    def invoke(self, paths, report, cases, *, failed_check=False, process_failure=None, altered_archive=False):
        requests = []
        def select(config):
            self.assertEqual(lane.ANCHOR, config)
            return cases
        def check(case, response, code):
            self.assertEqual(4, code)
            self.assertTrue(lane.matches_source_acknowledgement(response['result']['sourceProgram'], case['files']))
            return ['native observation failed'] if failed_check else []
        def run(command, **kwargs):
            index = len(requests)
            archive = Path(command[command.index('--program') + 1])
            self.assertTrue(archive.is_file())
            self.assertNotEqual(archive.parent, Path(kwargs['cwd']))
            with ZipFile(archive) as saved:
                self.assertEqual(cases[index]['files'], {name: saved.read(name) for name in saved.namelist()})
            requests.append({'archive': archive, 'request': kwargs['input']})
            if altered_archive: archive.write_bytes(archive.read_bytes() + b'changed')
            if process_failure is not None: raise process_failure
            body = {'status': 'incomplete', 'result': {'sourceProgram': lane.source_acknowledgement(cases[index]['files'])}}
            return lane.subprocess.CompletedProcess(command, 4, json.dumps(body).encode(), b'native diagnostic\n')
        with patch.object(lane, 'verify_runtime', return_value={'mockRuntime': True}), \
                patch.object(lane, 'engine_inputs', return_value=(b'engine-identity', None, [])), \
                patch.object(lane, 'selected_revisions', return_value={'supersymmetry': 'a' * 40}), \
                patch.object(lane, 'git', return_value=lane.ANCHOR), \
                patch.object(lane.subprocess, 'run', side_effect=run), redirect_stdout(StringIO()):
            result = lane.run(paths['java'], paths['engine'], paths['runtime'], paths['pack'], report,
                              case_selector=select, result_checker=check)
        return result, requests

    def test_exact_inputs_inventory_and_request_survive_deterministic_repeated_runs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); paths = self.environment(root); report = root / 'receipt.json'; cases = self.cases()
            result, calls = self.invoke(paths, report, cases)
            self.assertEqual('passed-bounded-observations', result['status'])
            self.assertEqual(result, json.loads(report.read_text()))
            archives = []
            for index, (record, case, call) in enumerate(zip(result['runs'], cases, calls)):
                self.assertEqual(lane.identity(case['files']), record['sourceProgram'])
                saved = record['programArchive']; archive = report.parent / saved['path']
                self.assertEqual(f'receipt.json.inputs/{index:04d}-program.zip', saved['path'])
                self.assertEqual(call['archive'], archive)
                self.assertEqual(saved['size'], archive.stat().st_size)
                self.assertEqual(saved['sha256'], sha256(archive.read_bytes()).hexdigest())
                self.assertEqual(record['request'], json.loads(call['request']))
                self.assertEqual(record['requestSha256'], sha256(call['request']).hexdigest())
                self.assertNotIn('files', record['response']['result']['sourceProgram'])
                archives.append(archive.read_bytes())
            self.assertEqual(archives[0], archives[1])

    def test_existing_receipt_or_input_directory_refuses_before_execution(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); unused = root / 'unresolved-native-input'
            report = root / 'receipt.json'; report.write_bytes(b'previous receipt')
            with patch.object(lane.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Receipt must be new'):
                    lane.run(unused, unused, unused, unused, report)
                run.assert_not_called()
            self.assertEqual(b'previous receipt', report.read_bytes())
            fresh = root / 'other.json'; retained = root / 'other.json.inputs'; retained.mkdir()
            sentinel = retained / 'keep'; sentinel.write_bytes(b'previous retained inputs')
            with patch.object(lane.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Retained case inputs must be new'):
                    lane.run(unused, unused, unused, unused, fresh)
                run.assert_not_called()
            self.assertEqual(b'previous retained inputs', sentinel.read_bytes())
            self.assertFalse(fresh.exists())

    def test_failed_native_result_keeps_complete_archive_and_coordinator_inventory(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); paths = self.environment(root); report = root / 'failed.json'; cases = self.cases()[:1]
            with self.assertRaisesRegex(ValueError, 'Native configuration regression failed'):
                self.invoke(paths, report, cases, failed_check=True)
            saved = json.loads(report.read_text()); self.assertEqual('failed', saved['status'])
            record, = saved['runs']
            self.assertEqual(['native observation failed'], record['failures'])
            self.assertEqual(lane.identity(cases[0]['files']), record['sourceProgram'])
            archive = root / record['programArchive']['path']
            with ZipFile(archive) as inputs:
                self.assertEqual(cases[0]['files'], {name: inputs.read(name) for name in inputs.namelist()})
            self.assertEqual(record['programArchive']['sha256'], sha256(archive.read_bytes()).hexdigest())

    def test_process_failure_retains_inputs_before_any_native_response_exists(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); paths = self.environment(root); report = root / 'process-failed.json'; cases = self.cases()[:1]
            with self.assertRaisesRegex(RuntimeError, 'simulated worker failure'):
                self.invoke(paths, report, cases, process_failure=RuntimeError('simulated worker failure'))
            saved = json.loads(report.read_text()); self.assertEqual('failed', saved['status'])
            record, = saved['runs']; self.assertNotIn('response', record)
            self.assertEqual(lane.identity(cases[0]['files']), record['sourceProgram'])
            archive = root / record['programArchive']['path']
            self.assertEqual(record['programArchive']['sha256'], sha256(archive.read_bytes()).hexdigest())
            self.assertIn('requestSha256', record)

    def test_archive_change_during_execution_cannot_qualify_the_receipt(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); paths = self.environment(root); report = root / 'changed.json'
            with self.assertRaisesRegex(ValueError, 'Native configuration regression failed'):
                self.invoke(paths, report, self.cases()[:1], altered_archive=True)
            saved = json.loads(report.read_text()); self.assertEqual('failed', saved['status'])
            record, = saved['runs']
            self.assertIn('Retained program archive changed during native execution', record['failures'])
            self.assertNotEqual(record['programArchive']['sha256'], sha256((root / record['programArchive']['path']).read_bytes()).hexdigest())


class PackMapperTests(unittest.TestCase):
    def test_mapper_programs_are_complete_and_edit_only_saved_fixture_sources(self):
        cases=mappers.corpus(lane.ANCHOR)
        self.assertEqual(13,len(cases));self.assertEqual(cases[0]['files'],cases[-1]['files'])
        for case in cases:
            self.assertEqual(set(cases[0]['files']),set(case['files']))
            self.assertEqual([mappers.MATERIAL],case['observeMaterials'])
            self.assertIn('material',case['files'][lane.EDITS].decode().splitlines()[9])
            for path,raw in case['files'].items():
                if path != lane.EDITS:self.assertEqual(cases[0]['files'][path],raw)

    def test_binding_observer_requires_exact_identity_and_bounded_scope(self):
        value={'registered':['element','material','metaitem','oreprefix','recipemap'], 'admitted':['material'],
               'materialBindingIdentity':True,'registrationMethod':'native-GroovyScriptSandbox.registerBinding',
               'completeMapperInitialization':False}
        self.assertEqual([],mappers.check_bindings({'objectMapperBindings':value}))
        for key,replacement in [('registered',['material']),('admitted',['material','item']),
                                ('materialBindingIdentity',False),('completeMapperInitialization',True),
                                ('registrationMethod','replacement lookup')]:
            self.assertTrue(mappers.check_bindings({'objectMapperBindings':{**value,key:replacement}}))


class PackLifecycleTests(unittest.TestCase):
    def test_lifecycle_corpus_keeps_complete_program_and_native_source_line(self):
        cases=lifecycle.corpus(lane.ANCHOR)
        self.assertEqual(5,len(cases))
        self.assertEqual(cases[0]['files'],cases[-1]['files'])
        for case in cases:
            self.assertEqual(set(cases[0]['files']),set(case['files']))
            self.assertEqual(lifecycle.OBSERVED,case['observeMaterials'])
            self.assertIn(lifecycle.SUSY,case['files'][lane.EDITS].decode().splitlines()[9])
            self.assertEqual(lane.ANCHOR,case['files'][lane.CONFIG])

    def test_dispatch_requires_native_priority_and_does_not_claim_invocations(self):
        rows=[{'index':0,'class':'net.minecraftforge.fml.common.eventhandler.ASMEventHandler','priority':'HIGH',
               'handler':'ASM: class supersymmetry.common.CommonProxy registerMaterials(LMaterialEvent;)V'},
              {'index':1,'class':'com.cleanroommc.groovyscript.event.GroovyEventManager$EventListener'}]
        execution={'materialEventDispatch':{'scope':'native-dispatch-cache-before-post-not-completed-invocations',
                    'discoveryOrderQualified':False,'listeners':rows}}
        self.assertEqual([],lifecycle.check_dispatch(execution,'materialEventDispatch','registerMaterials'))
        for change in ({'listeners':[]},{'discoveryOrderQualified':True},{'scope':'all-listeners-returned'}):
            bad=deepcopy(execution);bad['materialEventDispatch'].update(change)
            self.assertTrue(lifecycle.check_dispatch(bad,'materialEventDispatch','registerMaterials'))
        for key,value in [('priority','NORMAL'),('handler','replacement callback'),('index',2)]:
            bad=deepcopy(execution);bad['materialEventDispatch']['listeners'][0][key]=value
            self.assertTrue(lifecycle.check_dispatch(bad,'materialEventDispatch','registerMaterials'))

    def test_missing_native_effects_do_not_pass(self):
        case=lifecycle.corpus(lane.ANCHOR)[0]
        response=PackConfigurationTests().response(case)
        self.assertTrue(lifecycle.check_result(case,response,4))


class PackPropertyTests(unittest.TestCase):
    def case(self, name):
        return next(case for case in properties.corpus(lane.ANCHOR) if case['name'] == name)

    def response(self, case):
        expected=case['expected']
        result=PackConfigurationTests().response({**case, 'name':'acidic-native-error' if 'error' in expected else 'enabled'})
        execution=result['result']['execution']
        if case.get('configurationDisabled'):
            execution['packConfiguration'].update(recipeInfo=False, recipeInfoProperty={'value':'false','isBooleanValue':True})
        if 'loggedError' in expected:
            execution.update(nativeErrors=[expected['loggedError']],
                             diagnostics=[{'locations':[{'path':properties.EDITS,'line':10}]}])
        if 'error' in expected:
            execution.update(phase='CLOSED', nativeException=expected['error'] + ': ' + expected['message'],
                             diagnostics=[{'locations':[{'path':properties.EDITS,'line':10}]}])
        import struct
        values={**expected.get('values', {}), **{key:{'type':'float64','value':value,'rawBits':struct.pack('>d',value).hex()}
                                               for key,value in expected.get('numbers', {}).items()}}
        state={'schema':'axiom.native-material-property-state.v1', 'verificationInvokedByObserver':False,
               'recipeProducerInvokedByObserver':False, 'flags':sorted(expected.get('flags', set())),
               'properties':{key:{'valuesObserved':False} for key in expected.get('properties', set())}}
        state['properties'][expected['property']]={'class':expected['class'],'valuesObserved':expected.get('valuesObserved',True),'values':values}
        execution['materials']=[{'name':properties.MATERIAL,'nativePropertyState':state,**expected.get('materialValues',{})}]
        return result

    def test_all_cases_keep_complete_fixture_and_saved_source_location(self):
        cases=properties.corpus(lane.ANCHOR)
        self.assertEqual(37,len(cases))
        self.assertEqual(len(cases),len({case['name'] for case in cases}))
        self.assertEqual(cases[4]['files'],cases[-1]['files'])
        for case in cases:
            self.assertEqual(set(cases[0]['files']),set(case['files']))
            self.assertEqual([properties.MATERIAL],case['observeMaterials'])
            self.assertIn('Target.',case['files'][properties.EDITS].decode().splitlines()[9])
            self.assertEqual([],properties.check_result(case,self.response(case),4))

    def test_passive_native_state_and_flags_are_required(self):
        case=self.case('fiber-default');good=self.response(case)
        for key,value in [('verificationInvokedByObserver',True),('recipeProducerInvokedByObserver',True),
                           ('flags',[]),('properties',{})]:
            bad=deepcopy(good);bad['result']['execution']['materials'][0]['nativePropertyState'][key]=value
            self.assertTrue(properties.check_result(case,bad,4))
        bad=deepcopy(good);bad['result']['execution']['candidateAdmissionViolations']=['candidate.dispatch: native constructor']
        self.assertTrue(properties.check_result(case,bad,4))

    def test_partial_assignment_must_retain_original_error_and_line(self):
        case=self.case('fiber-fluid-error');good=self.response(case)
        for key,value in [('nativeException','java.lang.UnsupportedOperationException: admission'),('diagnostics',[])]:
            bad=deepcopy(good);bad['result']['execution'][key]=value
            self.assertTrue(properties.check_result(case,bad,4))
        bad=deepcopy(good)
        bad['result']['execution']['materials'][0]['nativePropertyState']['flags']=['generate_thread']
        self.assertTrue(properties.check_result(case,bad,4))

    def test_duplicate_property_must_not_replace_the_first_instance(self):
        case=self.case('fiber-duplicate');bad=self.response(case)
        bad['result']['execution']['materials'][0]['nativePropertyState']['properties']['fiber']['values']['solutionSpun']=True
        self.assertTrue(properties.check_result(case,bad,4))

    def test_native_bean_write_must_change_the_observed_value(self):
        case=self.case('native-bean-read-write');bad=self.response(case)
        bad['result']['execution']['materials'][0]['color']=0
        self.assertTrue(properties.check_result(case,bad,4))

    def test_explicit_false_containment_is_not_absent_or_true(self):
        case=self.case('native-base-proof-cleared');good=self.response(case)
        for value in ({}, {'susy:base':True}):
            bad=deepcopy(good)
            bad['result']['execution']['materials'][0]['nativePropertyState']['properties']['fluid_pipe']['values']['containmentPredicate']=value
            self.assertTrue(properties.check_result(case,bad,4))

    def test_native_logged_error_keeps_continuation_and_source_location(self):
        case=self.case('native-base-proof-missing-pipe');good=self.response(case)
        for key,value in [('nativeErrors',[]),('nativeException','replacement error'),('phase','OPEN'),('diagnostics',[])]:
            bad=deepcopy(good);bad['result']['execution'][key]=value
            self.assertTrue(properties.check_result(case,bad,4))

    def test_fission_default_is_distinct_from_explicit_zero_and_callbacks_are_not_run(self):
        case=self.case('native-fission-defaults');good=self.response(case)
        state=good['result']['execution']['materials'][0]['nativePropertyState']
        self.assertEqual(False,state['properties']['fission_fuel']['values']['depletedFuelSupplierPresent'])
        state['properties']['fission_fuel']['values']['requiredNeutrons']={'type':'float64','value':0,'rawBits':'0000000000000000'}
        self.assertTrue(properties.check_result(case,good,4))

    def test_coolant_requires_exact_native_double_bits(self):
        case=self.case('coolant-edited');bad=self.response(case)
        bad['result']['execution']['materials'][0]['nativePropertyState']['properties']['coolant']['values']['slowAbsorptionFactor']['rawBits']='0000000000000000'
        self.assertTrue(properties.check_result(case,bad,4))

    def test_failed_native_default_construction_is_retained_as_null(self):
        case=self.case('mill-ball-missing-default');good=self.response(case)
        self.assertEqual([],properties.check_result(case,good,4))
        good['result']['execution']['materials'][0]['nativePropertyState']['properties']['mill_ball'].pop('class')
        self.assertTrue(properties.check_result(case,good,4))

    def test_whole_pack_must_reach_the_recorded_next_boundary(self):
        case={**self.case('fiber-default'),'name':'pack'}
        good=PackConfigurationTests().response(case)
        execution=good['result']['execution']
        execution.update(stage='frozen-materials',phase='FROZEN',registeredMaterials=3441,
                         candidateAdmissionViolations=[],coverageGaps=['material-context.pack-generated-content-incomplete'],
                         log='Finished modifying material flags',
                         diagnostics=[{'locations':[{'path':'groovy/preInit/RegisterMetaItems.groovy','line':14}]}])
        for row in execution['initialization']['steps']:
            if row['id']=='material-event':row['status']='returned'
            elif row['id']=='post-material-event':row['status']='returned'
        self.assertEqual([],properties.check_result(case,good,4))
        for key,value in [('registeredMaterials',1916),('stage','script-load'),('nativeErrors',['native error']),
                          ('candidateAdmissionViolations',['candidate.dispatch']),('phase','OPEN'),('log','Finished modifying flags')]:
            bad=deepcopy(good);bad['result']['execution'][key]=value
            self.assertTrue(properties.check_result(case,bad,4))
