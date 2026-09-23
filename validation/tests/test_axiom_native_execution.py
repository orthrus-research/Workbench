"""Original-input native conformance; requires explicit candidate, engine, JVM and fresh report roots."""
import zipfile

from test_axiom_native_early_stage import NativeStageExecutionCase, PACK_COREMODS, lane


class NativeEarlyExecutionTests(NativeStageExecutionCase):
    """Complete baseline and original SideOnlyConfig behavior through INIT."""

    def assert_failure(self, response, message, *, cause=None):
        body = response['result']
        self.assertIsNot(body.get('earlyPipelineReady'), True)
        failures = body['failure']
        self.assertTrue(any(message in row['message'] for row in failures), failures)
        if cause is not None:
            self.assertTrue(any(row['class'] == cause for row in failures), failures)

    def test_complete_saved_baseline_executes_original_early_pipeline(self):
        response, program = self.run_case('baseline')
        self.assertEqual([], lane.check_pack_early_result(response, self.descriptor,
                                                         program['nativeContext'], program['sourceProgram']))

    def test_native_configuration_type_failure_keeps_original_cause_and_fatal_log(self):
        response, _ = self.run_case('configuration-type', addition=('groovy/sideOnly.json', b'{"client": []}\n'))
        self.assert_failure(response, 'JsonArray cannot be cast', cause='java.lang.ClassCastException')
        self.assertTrue(any(row['logger'] == 'Foundation' and row['message'] == 'Unable to launch'
                            and 'SideOnlyConfig.readFile' in row['trace'] for row in response['diagnostics']))

    def test_malformed_json_preserves_original_early_logging_linkage_gap(self):
        response, _ = self.run_case('malformed-json', addition=('groovy/sideOnly.json', b'{"client":\n'))
        self.assert_failure(response, 'net/minecraft/command/ICommand', cause='java.lang.NoClassDefFoundError')
        self.assertTrue(any('SideOnlyConfig.readFile' in row['trace'] for row in response['diagnostics']))

    def test_native_configuration_removing_required_patch_method_prevents_readiness(self):
        response, _ = self.run_case('removed-patch-method', addition=('groovy/sideOnly.json',
            b'{"common":{"net.minecraft.util.math.Vec3d":["func_189985_c()"]}}\n'))
        self.assert_failure(response, 'Original SERVER patch method absent: func_189985_c()D')
        self.assertEqual('early-safe-class-definition', response['result']['stage'])

    def test_logged_native_error_prevents_readiness_after_callbacks_and_definitions(self):
        response, _ = self.run_case('logged-write-error', addition=(
            'groovy/sideOnlyGenerated.json/native-write-collision.txt', b'Native output directory collision.\n'))
        self.assert_failure(response, 'Original early initialization logged native errors')
        body = response['result']
        self.assertEqual(len(PACK_COREMODS), len(body['coremodCallbacks']))
        self.assertEqual(3, len(body['definitions']))
        self.assertTrue(any(row['logger'] == 'GroovyScript-Core' and row['severity'] == 'error'
                            and 'Failed to save file on path' in row['message']
                            and 'SideOnlyConfig.writeGeneratedConfig' in row['trace'] for row in response['diagnostics']))

    def test_wrong_side_refuses_before_native_bootstrap(self):
        def change(program, package):
            program['side'] = 'CLIENT'
            return {'side': 'CLIENT'}
        response, _ = self.run_case('wrong-side', package_change=change)
        self.assert_failure(response, 'Native root package side or stage differs')

    def test_wrong_stage_refuses_before_native_bootstrap(self):
        def change(program, package):
            program['executionStage'] = 'preinitialization'
            return {'executionStage': 'preinitialization'}
        response, _ = self.run_case('wrong-stage', package_change=change)
        self.assert_failure(response, 'Native execution stage differs')

    def test_missing_original_artifact_refuses_before_native_bootstrap(self):
        def change(program, package):
            name = next(row['path'] for row in program['files'] if row['path'].endswith('minecraft-1.12.2-server.jar'))
            (package / name).unlink()
            return {'removedArtifact': name}
        response, _ = self.run_case('missing-server', package_change=change)
        self.assert_failure(response, 'minecraft-1.12.2-server.jar', cause='java.nio.file.NoSuchFileException')

    def test_wrong_original_artifact_refuses_before_native_bootstrap(self):
        def change(program, package):
            name = next(row['path'] for row in program['files'] if row['path'].endswith('minecraft-1.12.2-server.jar'))
            with (package / name).open('r+b') as stream:
                first = stream.read(1)
                stream.seek(0)
                stream.write(bytes([first[0] ^ 1]))
            return {'changedArtifact': name, 'changedOffset': 0}
        response, _ = self.run_case('wrong-server', package_change=change)
        self.assert_failure(response, 'Native root input digest differs')

    def test_saved_correction_after_native_failure_recovers_in_fresh_worker(self):
        failed, _ = self.run_case('correction-before', addition=('groovy/sideOnly.json', b'{"client": []}\n'))
        self.assert_failure(failed, 'JsonArray cannot be cast')
        response, program = self.run_case('correction-after')
        self.assertEqual([], lane.check_pack_early_result(response, self.descriptor,
                                                         program['nativeContext'], program['sourceProgram']))
        self.assertEqual(self.program['sourceProgram'], response['result']['sourceProgram'])
        self.assertEqual({name: row['definitionSha256'] for name, row in self.build['response']['result']['definitions'].items()},
                         {name: row['definitionSha256'] for name, row in response['result']['definitions'].items()})


class NativeGroovyExecutionTests(NativeStageExecutionCase):
    execution_stage = 'groovy'
    environment_prefix = 'AXIOM_GROOVY'

    @classmethod
    def check_response(cls, response, descriptor, program):
        return lane.check_groovy_result(response,descriptor,program['nativeContext'],program['sourceProgram'])

    @staticmethod
    def native_errors(response):
        errors = [row for row in response['result']['nativeDiagnostics'] if row.get('severity') == 'error']
        for row in errors:
            origins = row.get('nativeOrigins', [])
            if (not origins or origins[0].get('class') != 'net.minecraftforge.fml.common.FMLModContainer'
                    or origins[0].get('method') != 'constructMod' or origins[0].get('line') != 577):
                raise ValueError('Baseline signature error lost its original FML construction origin')
        # Fresh workers have different absolute runtime paths. Complete origin
        # records remain in each receipt; compare original diagnostic meaning.
        return [{key: row[key] for key in ('logger', 'severity', 'message', 'trace', 'locationStatus')}
                for row in errors]

    @classmethod
    def verify_baseline(cls):
        if not cls.baseline_failures:
            return super().verify_baseline()
        # The expanded pinned pack has these original FML errors. Its separate
        # narrow construction checker also refuses LadyLib's contained source.
        # Keep that candidate failed: these cases qualify diagnostic deltas,
        # not clean construction or a positive Groovy initialization verdict.
        expected_failures = [
            'Original construction observation missing: constructionReady',
            'Original selected containers did not construct with verified native instances',
            'Original construction did not return before Groovy initialization',
            'Original Groovy observation missing: groovyInitializationReady',
            'Original Groovy initialization logged native errors',
        ]
        expected_errors = [
            {'logger': 'FML', 'severity': 'error', 'trace': '', 'locationStatus': 'unlocated',
             'message': 'The mod appliedenergistics2 is expecting signature dfa4d3ac143316c6f32aa1a1beda1e34d42132e5 for source ae2-uel-v0.56.6.jar, however there is no signature matching that description'},
            {'logger': 'FML', 'severity': 'error', 'trace': '', 'locationStatus': 'unlocated',
             'message': 'The mod torchmaster is expecting signature 5e9a436b366831c8f54a7e80b015784da69278c6 for source torchmaster_1.12.2-1.8.5.0.jar, however there is no signature matching that description'},
        ]
        if (cls.build['status'] != 'failed' or cls.build['failures'] != expected_failures
                or cls.baseline_failures != expected_failures
                or cls.native_errors(cls.build['response']) != expected_errors):
            raise ValueError('Groovy diagnostic baseline differs from its explicit native-failure expectation')

    def test_native_file_only_error_has_saved_location_and_correction_recovers(self):
        path='groovy/preInit/AxiomNativeDiagnostic.groovy'
        failed, program=self.run_case('groovy-file-only-error',addition=(path,
            b"log.warn('axiom native warning')\nlog.error('axiom native file-only error')\n"))
        result=failed['result']
        self.assertIs(result['groovyInitializationReturned'],True)
        self.assertIs(result['groovyInitializationReady'],False)
        self.assertIs(result['groovyLoggedError'],True)
        self.assertEqual([],result['candidateAdmissionViolations'])
        self.assertFalse(any('axiom native file-only error' in row['message'] for row in result['nativeDiagnostics']))
        for level,text,line in [('warning','axiom native warning',1),('error','axiom native file-only error',2)]:
            rows=[row for row in failed['diagnostics'] if row.get('channel')=='groovy-log'
                  and row['severity']==level and text in row['message']]
            self.assertTrue(rows)
            self.assertTrue(any(any(location['path']==path and location['line']==line
                                    for location in row['locations']) for row in rows))
        self.assertTrue(self.check_response(failed,self.descriptor,program))
        corrected, program=self.run_case('groovy-saved-correction')
        self.assertEqual(self.baseline_failures,self.check_response(corrected,self.descriptor,program))
        self.assertEqual(self.program['sourceProgram'],corrected['result']['sourceProgram'])
        self.assertIs(corrected['result']['groovyInitializationReady'],
                      self.build['response']['result']['groovyInitializationReady'])
        self.assertEqual([],corrected['result']['groovyDiagnostics'])
        self.assertEqual(self.native_errors(self.build['response']),self.native_errors(corrected))

    def test_native_compiler_missing_import_preserves_source_location(self):
        path='groovy/preInit/AxiomNativeMissingImport.groovy'
        response, program=self.run_case('groovy-native-compiler-error',addition=(path,
            b'import axiom.missing.NativeInput\n'))
        result=response['result']
        self.assertIs(result['groovyInitializationReturned'],True)
        self.assertIs(result['groovyInitializationReady'],False)
        self.assertIs(result['groovyCompilationFailure'],True)
        findings=[finding for row in result['groovyDiagnostics'] for finding in row.get('compilerFindings',[])]
        self.assertTrue(any(finding.get('location',{}).get('path')==path
                            and finding['location']['line']==1 and 'NativeInput' in finding['message'] for finding in findings))
        self.assertTrue(self.check_response(response,self.descriptor,program))


class NativeSelectionExecutionTests(NativeStageExecutionCase):
    execution_stage = 'selection'
    environment_prefix = 'AXIOM_SELECTION'

    def assert_selection(self, response, program):
        self.assertEqual([], self.check_response(response, self.descriptor, program))
        self.assertEqual('LOADING', response['result']['loaderState'])
        for row in response['result']['selectionDefinitions'].values():
            self.assertIs(row['requiredMixinApplied'], True)

    def test_complete_saved_baseline_applies_early_and_late_mixins_before_construction(self):
        response, program = self.run_case('selection-baseline')
        self.assert_selection(response, program)
        self.assertIn('sussypatches/compat/mixins.variousgrsissue.json', response['result']['mixinConfigurations'])
        # SuSyLateMixinLoader.shouldMixinConfigQueue follows native mod presence.
        self.assertEqual('appliedenergistics2' in response['result']['discoveredMods'],
                         'mixins.susy.appliedenergistics2.json' in response['result']['mixinConfigurations'])

    def test_saved_disabled_required_dependency_preserves_original_failure(self):
        response, program = self.run_case('disabled-required-dependency',
                                        addition=('config/fmlModState.properties', b'codechickenlib=false\n'))
        self.assertTrue(self.check_response(response, self.descriptor, program))
        self.assertIs(response['result']['earlyPipelineReady'], True)
        missing = [row for row in response['result']['nativeDiagnostics']
                   if row['severity'] == 'error' and 'MissingModsException:' in row['message']
                   and 'codechickenlib@' in row['message']]
        self.assertTrue(missing)
        # Original Loader.sortModList throws the individual exception for one
        # dependent mod, or MultipleModsErrored for several in the expanded pack.
        expected = 'net.minecraftforge.fml.common.' + ('MultipleModsErrored' if len(missing) > 1 else 'MissingModsException')
        self.assertTrue(any(row['class'] == expected for row in response['result']['failure']))

    def test_original_coremod_blacklist_prevents_required_context_readiness(self):
        plugins = ['com.cleanroommc.configanytime.ConfigAnytimePlugin', 'zone.rong.mixinbooter.MixinBooterPlugin',
                   'ilib.asm.Loader', 'org.dimdev.jeid.JEIDLoadingPlugin', 'lain.mods.skins.init.forge.asm.Plugin',
                   'advancedshader.core.Core', 'net.shadowfacts.forgelin.preloader.ForgelinPlugin',
                   'com.cleanroommc.relauncher.CleanroomEntrypoint', 'supersymmetry.asm.SusyLoadingPlugin']
        config = ('general {\n S:LOADING_PLUGIN_BLACKLIST <\n' + '\n'.join('  ' + p for p in plugins) + '\n >\n}\n').encode()
        response, program = self.run_case('coremod-blacklist', addition=('config/forge_early.cfg', config))
        self.assertTrue(self.check_response(response, self.descriptor, program))
        self.assertIsNot(response['result'].get('selectionReady'), True)
        self.assertTrue(any('blacklist' in row['message'] and 'supersymmetry.asm.SusyLoadingPlugin' in row['message']
                            for row in response['diagnostics']))

    def test_saved_optional_configuration_changes_original_late_selection(self):
        path = 'config/sussypatches.cfg'
        with zipfile.ZipFile(self.package / self.program['programArchive']) as archive: original = archive.read(path)
        old = b'B:"Fix various GrS issues"=true'; new = b'B:"Fix various GrS issues"=false'
        self.assertEqual(1, original.count(old))
        response, program = self.run_case('optional-mixin-disabled', replacement=(path, original.replace(old, new)))
        self.assert_selection(response, program)
        self.assertNotIn('sussypatches/compat/mixins.variousgrsissue.json', response['result']['mixinConfigurations'])

    def test_native_method_removal_cannot_qualify_a_queued_late_mixin(self):
        response, program = self.run_case('late-hook-removed', addition=('groovy/sideOnly.json',
            b'{"common":{"gregtech.api.unification.OreDictUnifier":["registerOre()"]}}\n'))
        self.assertTrue(self.check_response(response, self.descriptor, program))
        self.assertIsNot(response['result'].get('selectionReady'), True)
        self.assertIs(response['result']['selectionPrefixCompleted'], True)
        self.assertTrue(any('selected mixin hook' in row['message'] for row in response['result']['failure']))

    def test_saved_dependency_correction_recovers_in_a_fresh_worker(self):
        failed, _ = self.run_case('selection-correction-before',
                                  addition=('config/fmlModState.properties', b'codechickenlib=false\n'))
        self.assertIsNot(failed['result'].get('selectionReady'), True)
        response, program = self.run_case('selection-correction-after')
        self.assert_selection(response, program)
        self.assertEqual(self.program['sourceProgram'], response['result']['sourceProgram'])
