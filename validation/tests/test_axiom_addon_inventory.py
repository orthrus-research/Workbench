"""Selection/custody tests. Native identity/configuration checks use fresh workers."""
from hashlib import sha1, sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
import zipfile
from copy import deepcopy
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_axiom_addon_inventory import selections, verified_artifact, candidate_projection, manifest_inputs
import axiom_pack_addon_discovery_conformance as conformance
import axiom_pack_candidate_metadata_conformance as metadata
import axiom_pack_gtfo_configuration_conformance as gtfo


def descriptor(filename='example.jar', side='both', option=''):
    return (f'filename="{filename}"\nside="{side}"\n[download]\nhash-format="sha1"\nhash="'+sha1(b'jar').hexdigest()+'"\n'+option).encode()


class AddonInventoryTests(unittest.TestCase):
    def test_library_enumeration_requires_original_names_order_and_bounded_scope(self):
        self.assertTrue(conformance.check_libraries({}))
        value = {'method': 'original LibraryManager.getCandidates',
                 'scope': 'flat-profile-artifact-directory-only-not-complete-Cleanroom-selection',
                 'originalFilenames': True, 'launcherArgumentsRestored': True,
                 'hostLibraryStateRestored': True, 'nativeCacheIdentityObserved': True,
                 'containedDependencyExtractionExecuted': False,
                 'externalModListsComposed': False, 'classpathCandidatesComposed': False,
                 'coremodFilteringApplied': False, 'completeSelectionQualified': False,
                 'artifactOrder': ['mods/'+str(i).zfill(3)+'-original.jar' for i in range(184)]}
        value['manifestSelection'] = {'reader': 'original java.util.jar.JarFile.getManifest', 'artifacts': 184,
            'present': 184, 'rawBytesVerified': True, 'bansoukouPresent': False, 'identitySha256': 'a'*64,
            'containedDependencyCarriers': {name: 'Ladylib-2.1.jar' for name in conformance.CONTAINED_DEPENDENCY_CARRIERS}}
        execution = {'addonDiscovery': {'nativeLibraryCandidates': value}, 'initialization': {'steps': [
            {'id': 'addon-candidate-discovery', 'sequence': 1},
            {'id': 'addon-library-candidates', 'sequence': 2, 'status': 'returned', 'elapsedNanos': 1},
            {'id': 'addon-api-providers', 'sequence': 3}]}}
        self.assertEqual([], conformance.check_libraries(execution))
        for key in ('originalFilenames', 'launcherArgumentsRestored', 'completeSelectionQualified',
                    'classpathCandidatesComposed', 'coremodFilteringApplied', 'hostLibraryStateRestored',
                    'nativeCacheIdentityObserved', 'containedDependencyExtractionExecuted'):
            changed = deepcopy(execution); changed['addonDiscovery']['nativeLibraryCandidates'][key] = not value[key]
            self.assertTrue(conformance.check_libraries(changed))
        changed = deepcopy(execution); changed['addonDiscovery']['nativeLibraryCandidates']['artifactOrder'].reverse()
        self.assertTrue(conformance.check_libraries(changed))
        for key, wrong in [('present', 0), ('rawBytesVerified', False), ('bansoukouPresent', True),
                           ('identitySha256', 'not a hash'), ('containedDependencyCarriers', {})]:
            changed = deepcopy(execution); changed['addonDiscovery']['nativeLibraryCandidates']['manifestSelection'][key] = wrong
            self.assertTrue(conformance.check_libraries(changed))
        changed = deepcopy(execution); changed['addonDiscovery']['nativeLibraryCandidates']['artifactOrder'][1] = value['artifactOrder'][0]
        self.assertTrue(conformance.check_libraries(changed))
        changed = deepcopy(execution); changed['initialization']['steps'][1]['sequence'] = 4
        self.assertTrue(conformance.check_libraries(changed))

    def test_api_projection_keeps_original_annotations_and_embedded_package_witnesses(self):
        data=BytesIO();entries=[('shared/Embedded.class',b'embedded original'),
                               ('outside/Other.class',b'not selected'),('shared/package-info.class',b'original annotation')]
        with zipfile.ZipFile(data,'w') as archive:
            for name,raw in entries:archive.writestr(name,raw)
        declaration={'entry':entries[-1][0],'sha256':sha256(entries[-1][1]).hexdigest()}
        witness={'entry':entries[0][0],'sha256':sha256(entries[0][1]).hexdigest()}
        with zipfile.ZipFile(BytesIO(data.getvalue())) as archive:
            projected=candidate_projection(archive,[],[declaration],{'shared':witness})
            with self.assertRaisesRegex(ValueError,'identity differs'):
                candidate_projection(archive,[],[declaration],{'shared':{**witness,'sha256':'0'*64}})
        with zipfile.ZipFile(BytesIO(projected)) as archive:
            self.assertEqual([entries[0][0],entries[-1][0]],archive.namelist())
            for name,raw in (entries[0],entries[-1]):self.assertEqual(raw,archive.read(name))

    def test_absent_api_providers_cannot_pass(self):
        self.assertTrue(conformance.check_apis({}))

    def test_api_checker_preserves_aliases_embedded_copies_and_side_rejected_owners(self):
        value={'scope':'discovered-jar-candidates-only-not-complete-pack-api-composition',
               'method':'original ModAPIManager.registerDataTableAndParseAPI','declarationCount':79,'providerCount':79,
               'hostApiStateRestored':True,'candidateTransformerInstalled':False,'packApiCompositionQualified':False,
               'providers':{},'packageMembership':{}}
        def add(id,owner,version,pkg,members,before):
            value['providers'][id]={'owner':owner,'version':version,'sourceArtifactSha256':'a'*64,
                                    'packages':[pkg],'after':[owner],'before':before,'selfReferenced':False}
            value['packageMembership'][pkg]={'a'*64:members}
        for i in range(76):add('example'+str(i),'owner','1','example.'+str(i),['owner'],[])
        add('AppleCoreAPI','applecore','3.4.0','squeek.applecore.api',['applecore'],[])
        add('ComputerCraft|API','ComputerCraft','1.89.2','dan200.computercraft.api',
            ['cctweaked','computercraft'],['cctweaked','computercraft','opencomputers'])
        value['packageMembership']['dan200.computercraft.api']['b'*64]=['opencomputers']
        add('ctm-api','ctm','0.1.0','team.chisel.ctm.api',[],['gregicprobe'])
        value['packageMembership']['team.chisel.ctm.api']['b'*64]=['gregicprobe']
        execution={'addonDiscovery':{'nativeApiProviders':value},'initialization':{'steps':[
            {'id':'addon-candidate-discovery','sequence':1},{'id':'addon-api-providers','sequence':2,'status':'returned','elapsedNanos':1},
            {'id':'addon-candidate-bus','sequence':3}]}}
        self.assertEqual([],conformance.check_apis(execution))
        for key,wrong in [('hostApiStateRestored',False),('candidateTransformerInstalled',True),
                          ('packApiCompositionQualified',True),('providerCount',78)]:
            changed=deepcopy(execution);changed['addonDiscovery']['nativeApiProviders'][key]=wrong
            self.assertTrue(conformance.check_apis(changed))
        changed=deepcopy(execution);changed['addonDiscovery']['nativeApiProviders']['packageMembership']['team.chisel.ctm.api']['a'*64]=['ctm']
        self.assertTrue(conformance.check_apis(changed))
        changed=deepcopy(execution);changed['addonDiscovery']['nativeApiProviders']['providers']['ComputerCraft|API']['before']=['computercraft']
        self.assertTrue(conformance.check_apis(changed))

    def test_explicit_side_and_default_optional_selection(self):
        rows = selections({'mods/a.pw.toml': descriptor(), 'mods/b.pw.toml': descriptor('b.jar', 'client'),
                           'mods/c.pw.toml': descriptor('c.jar', option='[option]\noptional=true\ndefault=false')}, 'server')
        self.assertEqual([r['selected'] for r in rows], [True, False, False])
        self.assertEqual(rows[0]['descriptorSha256'], sha256(descriptor()).hexdigest())

    def test_optional_selection_is_explicit_and_boolean(self):
        data = {'mods/a.pw.toml': descriptor(option='[option]\noptional=true\ndefault=false')}
        self.assertTrue(selections(data, 'server', {'mods/a.pw.toml': True})[0]['selected'])
        for options in ({'mods/b.pw.toml': True}, {'mods/a.pw.toml': 1}):
            with self.assertRaises(ValueError): selections(data, 'server', options)

    def test_unknown_side_and_duplicate_outputs_are_rejected(self):
        for data, side in (({'a': descriptor()}, 'unknown'), ({'a': descriptor(side='invalid')}, 'server'),
                           ({'a': descriptor(), 'b': descriptor()}, 'server')):
            with self.assertRaises(ValueError): selections(data, side)

    def test_paths_are_not_authority_for_native_mod_ids(self):
        row, = selections({'mods/not-the-modid.pw.toml': descriptor('another-name.jar')}, 'server')
        self.assertNotIn('modid', row)
        for filename in ('../escape.jar', '/escape.jar', 'a\\b.jar', 'a:bad.jar', 'nonjar.zip'):
            with self.assertRaises(ValueError): selections({'mods/a.pw.toml': descriptor(filename)}, 'server')

    def verify(self, raw=b'jar', **change):
        row, = selections({'mods/a.pw.toml': descriptor()}, 'server')
        digest = sha256(raw).hexdigest()
        record = {'metadataPath': 'mods/a.pw.toml', 'outputPath': 'mods/example.jar', 'sha256': digest, 'size': len(raw), **change}
        data = BytesIO()
        with zipfile.ZipFile(data, 'w') as archive: archive.writestr('blobs/'+digest, raw)
        with zipfile.ZipFile(BytesIO(data.getvalue())) as archive: return verified_artifact(archive, row, record)

    def test_declared_artifact_bytes_are_rechecked(self):
        self.assertEqual(self.verify(), b'jar')
        with self.assertRaises(ValueError): self.verify(b'changed')

    def test_bundle_metadata_is_not_trusted(self):
        for change in ({'outputPath': 'mods/other.jar'}, {'metadataPath': 'mods/other.pw.toml'},
                       {'size': 1}, {'sha256': '../bad'}):
            with self.assertRaises(ValueError): self.verify(**change)

    def test_missing_native_observation_cannot_pass(self):
        self.assertTrue(conformance.check_discovery({'files': {}}, {}))

    def test_forge_observation_requires_original_state_and_pre_discovery_order(self):
        execution = {'forgeInitialization': {
            'status': 'returned', 'side': 'SERVER', 'nativeHandler': 'net.minecraftforge.fml.server.FMLServerHandler',
            'serverStarted': False, 'activeOwnerAbsent': True, 'toolInitializationComplete': True,
            'usernameCacheEntries': 0, 'modActivationQualified': False, 'oreDictionaryNames': 155,
            'harvestLevels': {'minecraft:obsidian': 3, 'minecraft:iron_ore': 1,
                              'minecraft:diamond_ore': 2, 'minecraft:quartz_ore': 0}},
            'initialization': {'steps': [{'id': 'forge-platform-initialization', 'status': 'returned', 'sequence': 2},
                                       {'id': 'addon-candidate-discovery', 'sequence': 3}]}}
        self.assertEqual(conformance.check_forge(execution), [])
        for key, wrong in [('side', 'CLIENT'), ('serverStarted', True), ('activeOwnerAbsent', False),
                           ('toolInitializationComplete', False), ('modActivationQualified', True),
                           ('harvestLevels', {}), ('usernameCacheEntries', 1)]:
            changed = deepcopy(execution); changed['forgeInitialization'][key] = wrong
            self.assertTrue(conformance.check_forge(changed), key)
        changed = deepcopy(execution); changed['initialization']['steps'][0]['sequence'] = 4
        self.assertTrue(conformance.check_forge(changed))

    def test_native_side_observation_is_not_just_a_count(self):
        side = {'method': 'original FMLModContainer.shouldLoadInEnvironment', 'side': 'SERVER',
                'scope': 'raw-Mod-annotations-not-filtered-discovery',
                'evaluated': 194, 'eligible': 170, 'rejected': 24}
        self.assertTrue(conformance.check_side({'nativeSideEligibility': side}))
        self.assertTrue(conformance.check_side({}))

    def test_source_lock_includes_complete_forge_initialization_dependencies(self):
        from axiom_material_program_sources import LOCK, PATHS
        locked = {(row['repository'], row['path']) for row in json.loads(LOCK.read_bytes())['references']}
        for owner in ('MinecraftForge', 'ForgeHooks', 'UsernameCache'):
            path = 'src/main/java/net/minecraftforge/common/'+owner+'.java'
            self.assertIn(path, PATHS['cleanroom'])
            self.assertIn(('cleanroom', path), locked)
        self.assertIn(('cleanroom', 'src/main/java/net/minecraftforge/fluids/FluidRegistry.java'), locked)

    def test_corpus_changes_only_native_mod_state_input(self):
        saved = b'general {' + gtfo.SECTION + b'\n' + gtfo.APPLE + b'\n' + gtfo.DIVISOR + b'\n' + gtfo.DIRTS + b'\n>\n}\n}'
        corpus = conformance.corpus(b'config', saved)
        self.assertEqual(len(corpus), 9)
        for case in corpus:
            self.assertEqual({k: v for k, v in case['files'].items() if k != conformance.CONFIG}, corpus[0]['files'])
        self.assertEqual(corpus[0]['files'], corpus[-1]['files'])

    def project(self, entries, declarations):
        data = BytesIO()
        with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in entries: archive.writestr(name, raw)
        with zipfile.ZipFile(BytesIO(data.getvalue())) as archive:
            return candidate_projection(archive, [{'entry': name} for name in declarations])

    def test_candidate_projection_keeps_original_bytes_and_entry_order(self):
        selected = [('z/Mod$.class', b'companion'), ('mcmod.info', b'\n[{"modid":"z"}]'),
                    ('a/Mod.class', b'original class'), ('version.properties', b'z.version=1.2\n')]
        raw = self.project([selected[0], ('other/Subscriber.class', b'not a candidate'),
                            *selected[1:], ('assets/example.txt', b'asset')], ['a/Mod.class', 'z/Mod$.class'])
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            self.assertEqual(archive.namelist(), [name for name, _ in selected])
            self.assertEqual([(name, archive.read(name)) for name in archive.namelist()], selected)
            self.assertTrue(all(row.date_time == (1980, 1, 1, 0, 0, 0) for row in archive.infolist()))

    def test_empty_candidate_projection_is_a_valid_empty_jar(self):
        raw = self.project([('example/Helper.class', b'not a mod')], [])
        with zipfile.ZipFile(BytesIO(raw)) as archive: self.assertEqual(archive.namelist(), [])

    def test_manifest_projection_preserves_raw_folding_sections_casing_and_absence(self):
        original = b'Manifest-Version: 1.0\r\nContainedDeps: one.jar\r\n  two.jar\r\n\r\nName: x/Class.class\r\nSHA-256-Digest: evidence\r\n\r\n'
        for name in ('META-INF/MANIFEST.MF', 'meta-inf/manifest.mf', 'META-INF/Manifest.mf'):
            raw = self.project([('other.txt', b'omit'), (name, original), ('M.class', b'original')], ['M.class'])
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                self.assertEqual(archive.namelist(), [name, 'M.class'])
                self.assertEqual(archive.read(name), original)
                self.assertEqual(manifest_inputs(archive), [{'entry': name, 'size': len(original), 'sha256': sha256(original).hexdigest()}])
        with zipfile.ZipFile(BytesIO(self.project([], []))) as archive:
            self.assertEqual(manifest_inputs(archive), [])

    def test_manifest_projection_keeps_native_parse_failures_and_bounds_bytes(self):
        original = b'not a valid manifest\r\n'
        with zipfile.ZipFile(BytesIO(self.project([('META-INF/MANIFEST.MF', original)], []))) as archive:
            self.assertEqual(archive.read('META-INF/MANIFEST.MF'), original)
        with self.assertRaisesRegex(ValueError, 'manifest exceeds bound'):
            self.project([('META-INF/MANIFEST.MF', b'x' * ((1 << 20) + 1))], [])

    def test_candidate_projection_retains_both_original_server_language_spellings(self):
        entries = {'M.class': b'original class', 'assets/m/lang/en_us.lang': b'a=A\n',
                   'assets/m/lang/en_US.lang': b'a=Fallback\n', 'assets/m/lang/fr_fr.lang': b'a=French\n'}
        data = BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for name, raw in entries.items(): archive.writestr(name, raw)
        with zipfile.ZipFile(BytesIO(data.getvalue())) as archive:
            projected = candidate_projection(archive, [{'entry': 'M.class', 'annotation': {'modid': 'm'}}])
        with zipfile.ZipFile(BytesIO(projected)) as archive:
            self.assertEqual(archive.namelist(), list(entries)[:3])
            for name in archive.namelist(): self.assertEqual(archive.read(name), entries[name])

    def test_missing_candidate_activation_cannot_pass(self):
        self.assertTrue(conformance.check_activation({}, {}))

    def test_activation_observation_requires_native_states_order_and_deferred_sort(self):
        names = ['applecore', *['candidate'+str(i) for i in range(161)]]
        activation = {'scope': 'discovered-jar-candidates-only-not-complete-pack-activation',
                      'method': 'original LoadController transition(LOADING) and FMLLoadEvent dispatch',
                      'loaderState': 'LOADING', 'hostLoaderStateRestored': True,
                      'modInstancesConstructed': False, 'packActivationQualified': False,
                      'containerStates': {id: 'LOADED' for id in names},
                      'presentCandidateQueries': dict.fromkeys(names, True), 'activeCandidateOrder': names,
                      'language': {'method': 'original FMLServerHandler.addModAsResource -> LanguageMap.inject',
                                   'entries': 1, 'sha256': 'a'*64},
                      'dependencyOrder': {'status': 'deferred', 'versionRequirementsChecked': False,
                                          'requiredIdsOutsideCandidateSet': ['forge']}}
        execution = {'addonDiscovery': {'nativeCandidateDiscovery': {'candidateOrder': names},
                                       'nativeCandidateActivation': activation},
                     'initialization': {'steps': [{'id': 'addon-candidate-discovery', 'sequence': 1},
                                                 {'id': 'addon-candidate-bus', 'sequence': 2,
                                                  'status': 'returned', 'elapsedNanos': 0}]}}
        self.assertEqual(conformance.check_activation({}, execution), [])
        for key, wrong in [('packActivationQualified', True), ('hostLoaderStateRestored', False),
                           ('containerStates', {}), ('presentCandidateQueries', {}), ('activeCandidateOrder', []),
                           ('dependencyOrder', {'status': 'returned'}), ('language', {})]:
            changed = deepcopy(execution); changed['addonDiscovery']['nativeCandidateActivation'][key] = wrong
            self.assertTrue(conformance.check_activation({}, changed), key)
        activation['containerStates']['applecore'] = 'DISABLED'
        activation['presentCandidateQueries']['applecore'] = False
        activation['activeCandidateOrder'] = names[1:]
        self.assertEqual(conformance.check_activation({'apple_enabled': False}, execution), [])

    def test_candidate_projection_does_not_require_metadata_or_remove_native_errors(self):
        for entries in ([('M.class', b'class')], [('mcmod.info', b'invalid json')]):
            raw = self.project(entries, ['M.class'])
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                self.assertEqual([(name, archive.read(name)) for name in archive.namelist()], entries)

    def test_candidate_projection_bounds_selected_entry_size(self):
        with self.assertRaisesRegex(ValueError, 'exceeds bound'):
            self.project([('mcmod.info', b'x' * ((4 << 20) + 1))], [])

    def test_dependency_corpus_preserves_source_and_native_partial_errors(self):
        saved = b'general {' + gtfo.SECTION + b'\n' + gtfo.APPLE + b'\n' + gtfo.DIVISOR + b'\n' + gtfo.DIRTS + b'\n>\n}\n}'
        corpus = metadata.corpus(b'config', saved)
        self.assertEqual(len(corpus), 8)
        for case in corpus:
            self.assertEqual({k: v for k, v in case['files'].items() if k != metadata.INJECTED}, corpus[0]['files'])
        self.assertEqual(corpus[0]['files'], corpus[-1]['files'])
        self.assertEqual(corpus[3]['after'], ['applecore@[3.4,)'] * 2)
        self.assertTrue(corpus[5]['dependency_error'])
        self.assertEqual(corpus[5]['after'], ['applecore@[3.4,)'])
        self.assertEqual(json.loads(corpus[5]['files'][metadata.INJECTED])[0]['deps'][-1]['type'], 'invalid')

    def test_missing_candidate_metadata_cannot_pass(self):
        self.assertTrue(metadata.check_metadata({'files': {}}, {}))

    def test_native_dependency_errors_use_original_log_diagnostics_not_groovy_errors(self):
        case = {'dependency_error': True}
        execution = {'nativeErrors': [], 'diagnostics': [
            {'severity': 'error', 'message': 'Unable to parse /tmp/worker/config/injectedDependencies.json - skipping',
             'locations': [], 'locationStatus': 'unlocated'},
            {'severity': 'error', 'message': 'Throwing', 'trace': 'original trace',
             'causality': {'exceptions': [{'type': 'java.lang.RuntimeException', 'message': 'Unable to parse type'}]}}]}
        self.assertEqual(metadata.check_dependency_errors(case, execution), [])
        self.assertTrue(metadata.check_dependency_errors({}, execution))
        self.assertTrue(metadata.check_dependency_errors(case, {'nativeErrors': [], 'diagnostics': []}))
        changed = deepcopy(execution); changed['diagnostics'][0]['locations'] = [{'line': 1}]
        self.assertTrue(metadata.check_dependency_errors(case, changed))
        changed = deepcopy(execution); changed['diagnostics'].pop()
        self.assertTrue(metadata.check_dependency_errors(case, changed))

    def test_complete_candidate_discovery_sources_are_locked(self):
        from axiom_material_program_sources import LOCK, PATHS
        locked = {(row['repository'], row['path']) for row in json.loads(LOCK.read_bytes())['references']}
        for name in ('discovery/ITypeDiscoverer', 'discovery/ContainerType', 'MetadataCollection',
                     'versioning/DependencyParser', 'versioning/DefaultArtifactVersion', 'versioning/VersionParser'):
            path = 'src/main/java/net/minecraftforge/fml/common/'+name+'.java'
            self.assertIn(path, PATHS['cleanroom'])
            self.assertIn(('cleanroom', path), locked)
        for name in ('LibraryManager', 'ModList'):
            path = 'src/main/java/net/minecraftforge/fml/relauncher/libraries/'+name+'.java'
            self.assertIn(path, PATHS['cleanroom'])
            self.assertIn(('cleanroom', path), locked)

    def test_projection_comparison_does_not_accept_wrong_scope_or_different_metadata(self):
        execution = {'addonDiscovery': {'nativeCandidateDiscovery': {'inputScope': 'entrypoints-and-metadata',
                      'inputBytes': 10, 'metadataSha256': 'a'*64}, 'gtfoNativeCandidate': {}, 'queries': {},
                      'gtfoConfiguredEnabled': True}, 'phase': 'FROZEN', 'registeredMaterials': 1, 'nativeErrors': []}
        projected = {'status': 'passed-bounded-observations', 'runs': [{'name': 'test', 'response': {'result': {'execution': execution}}}]}
        reference = deepcopy(projected)
        native = reference['runs'][0]['response']['result']['execution']['addonDiscovery']['nativeCandidateDiscovery']
        native.update(inputScope='complete-artifacts', inputBytes=100)
        with TemporaryDirectory() as tmp:
            a, b, out = [Path(tmp)/name for name in ('a.json', 'b.json', 'comparison.json')]
            a.write_text(json.dumps(projected)); b.write_text(json.dumps(reference))
            self.assertEqual(metadata.compare(a, b, out)['status'], 'passed')
            with self.assertRaisesRegex(ValueError, 'must be new'): metadata.compare(a, b, out)
            native['metadataSha256'] = 'b'*64; b.write_text(json.dumps(reference))
            with self.assertRaisesRegex(ValueError, 'disagreement'): metadata.compare(a, b, Path(tmp)/'different.json')
            native['inputScope'] = 'entrypoints-and-metadata'; b.write_text(json.dumps(reference))
            with self.assertRaisesRegex(ValueError, 'not complete'): metadata.compare(a, b, Path(tmp)/'wrong-scope.json')


if __name__ == '__main__': unittest.main()
