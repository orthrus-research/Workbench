"""Coremod receipt/corpus checks; actual native execution is a separate lane."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_pack_coremod_conformance as lane


class CoremodTests(unittest.TestCase):
    def body(self):
        coremod = {'status': 'injected', 'scope': lane.SCOPE, 'launcherCompositionQualified': False,
                   'fullCoremodInjectionExecuted': False, 'nativePluginRegistered': True, 'injectDataExecuted': True,
                   'classBytesMatchedOriginalArtifact': True, 'artifact': 'mods/IvToolkit-1.3.3-1.12.jar',
                   'artifactSha256': 'ffb745111790e27cb7810a2e03d5270265dee7ebd3ef98fde897c409d58b1e59',
                   'injectedContainerNames': [lane.CONTAINER], 'nativeCandidateSelectionExecuted': False,
                   'container': {'class': lane.CONTAINER, 'id': 'ivtoolkit', 'version': '1.3.3-1.12',
                                 'wrapper': 'net.minecraftforge.fml.common.InjectedModContainer',
                                 'source': 'minecraft.jar', 'nativeNullSourceFallback': True},
                   'initializationPrefix': {'owner': 'net.minecraftforge.fml.relauncher.CoreModManager',
                                           'method': 'axiom$initializeCoremodEnvironment', 'originalClassSha256': lane.CLASS_SHA256,
                                           'boundary': 'after-original-loadPlugins-write-before-root-plugin-loop',
                                           'nativeDeobfuscatedEnvironment': False, 'side': 'SERVER',
                                           'patchingTransformerRegistered': True, 'rootPluginSetupExecuted': False,
                                           'queuedTweaks': ['net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker',
                                                            'org.spongepowered.asm.launch.MixinTweaker']}}
        activation = {'scope': lane.BUS_SCOPE, 'method': 'original LoadController transition(LOADING) and FMLLoadEvent dispatch',
                      'loaderState': 'LOADING', 'containerStates': {'ivtoolkit': 'LOADED'},
                      'presentCandidateQueries': {'ivtoolkit': True}, 'activeCandidateOrder': ['ivtoolkit'],
                      'hostLoaderStateRestored': True, 'packActivationQualified': False, 'modInstancesConstructed': False,
                      'dependencyOrder': {'versionRequirementsChecked': False}}
        return {'bootstrap': {'platformInitialization': {'coremodComposition': coremod}},
                'execution': {'addonDiscovery': {'nativeInjectedCoremodActivation': activation}}}

    def test_corpus_preserves_complete_program_and_fresh_reset(self):
        cases = lane.corpus(lane.configuration.ANCHOR)
        self.assertEqual(6, len(cases))
        for index in (1, 2, 3, 5):
            self.assertEqual(cases[0]['files'], {p: raw for p, raw in cases[index]['files'].items() if p != lane.CONFIG})
        self.assertTrue(cases[2]['coremodDeferred'])
        self.assertEqual('acidic-native-error', cases[4]['name'])
        self.assertEqual([], lane.check_coremod(cases[0], self.body()))

    def test_missing_native_effects_or_favorable_qualification_are_rejected(self):
        good = self.body()
        for key, value in (('status', 'observed'), ('nativePluginRegistered', False), ('injectDataExecuted', False),
                           ('classBytesMatchedOriginalArtifact', False), ('artifactSha256', '0' * 64),
                           ('injectedContainerNames', []), ('launcherCompositionQualified', True),
                           ('fullCoremodInjectionExecuted', True), ('nativeCandidateSelectionExecuted', True)):
            bad = deepcopy(good); bad['bootstrap']['platformInitialization']['coremodComposition'][key] = value
            self.assertTrue(lane.check_coremod({}, bad), key)

    def test_source_prefix_and_pending_tweaks_are_not_whole_root_setup(self):
        good = self.body()
        for key, value in (('originalClassSha256', 'changed'), ('rootPluginSetupExecuted', True),
                           ('queuedTweaks', []), ('patchingTransformerRegistered', False),
                           ('nativeDeobfuscatedEnvironment', 'false'), ('side', 'CLIENT')):
            bad = deepcopy(good); bad['bootstrap']['platformInitialization']['coremodComposition']['initializationPrefix'][key] = value
            self.assertTrue(lane.check_coremod({}, bad), key)

    def test_partial_bus_answers_cannot_be_promoted_or_retained(self):
        good = self.body()
        for key, value in (('containerStates', {}), ('presentCandidateQueries', {'ivtoolkit': True, 'missing': False}),
                           ('hostLoaderStateRestored', False), ('packActivationQualified', True), ('modInstancesConstructed', True)):
            bad = deepcopy(good); bad['execution']['addonDiscovery']['nativeInjectedCoremodActivation'][key] = value
            self.assertTrue(lane.check_coremod({}, bad), key)

    def test_blacklist_requires_unexecuted_state_and_visible_gap(self):
        case = {'coremodDeferred': True}
        body = {'bootstrap': {'platformInitialization': {'coremodComposition': {
            'status': 'not-admitted', 'scope': lane.SCOPE, 'reason': 'saved-blacklist-requires-native-discovery-decision',
            'nativePluginRegistered': False, 'injectDataExecuted': False, 'launcherCompositionQualified': False,
            'fullCoremodInjectionExecuted': False}}}, 'execution': {
            'addonDiscovery': {'nativeInjectedCoremodActivation': {'status': 'not-executed', 'packActivationQualified': False}},
            'coverageGaps': ['material-context.ivtoolkit-coremod-blacklisted']}}
        self.assertEqual([], lane.check_coremod(case, body))
        bad = deepcopy(body); bad['execution']['coverageGaps'] = []
        self.assertTrue(lane.check_coremod(case, bad))
        self.assertTrue(lane.check_coremod(case, self.body()))


if __name__ == '__main__': unittest.main()
