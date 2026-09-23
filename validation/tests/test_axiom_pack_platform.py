"""Assessment/input tests; native execution is a separate conformance lane."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_pack_platform_conformance as lane


class PlatformTests(unittest.TestCase):
    def body(self, case):
        expected = case.get('platform', {}); raw = case['files'].get(lane.CONFIG)
        rows = []
        for name, owner, version in (
                ('cleanroom', 'com.cleanroommc.common.CleanroomContainer', '0.6.12-alpha'),
                ('mixinbooter', 'zone.rong.mixinbooter.MixinBooterModContainer',
                 expected.get('mixin', '11.8') if expected.get('custom') else '11.8'),
                ('configanytime', 'com.cleanroommc.common.ConfigAnytimeContainer',
                 expected.get('anytime', '3.0') if expected.get('custom') else '3.0')):
            rows.append({'id': name, 'class': owner, 'version': version,
                         'processedRange': 'any' if name == 'configanytime' else version,
                         'processedVersion': 'unknown' if name == 'configanytime' else version})
        platform = {'status': 'returned', 'side': 'SERVER', 'minecraftVersion': '1.12.2', 'mcpVersion': '9.42',
                    'forgeVersion': '14.23.5.2864', 'inputPresent': raw is not None, 'workerFileSha256': '0' * 64,
                    'method': 'original FMLInjectionData.build -> ConfigManager.register; Loader.injectData',
                    'containerScope': lane.SCOPE, 'launcherCompositionQualified': False, 'fullCoremodInjectionExecuted': False,
                    'customBuiltInVersions': expected.get('custom', False), 'mixinBooterVersion': expected.get('mixin', '11.8'),
                    'configAnytimeVersion': expected.get('anytime', '3.0'), 'builtInContainers': rows,
                    'loadingPluginBlacklist': expected.get('blacklist', ['zone.rong.mixinbooter.MixinBooterPlugin']),
                    'ivToolkit': {'plugin': 'ivorius.ivtoolkit.IvToolkitLoadingPlugin', 'asmTransformers': [],
                                  'nativeJavaCheckReturned': True, 'setupClassPresent': False, 'accessTransformerPresent': False,
                                  'injectDataExecuted': False, 'registeredWithCoreModManager': False,
                                  'container': {'class': 'ivorius.ivtoolkit.IvToolkitCoreContainer', 'id': 'ivtoolkit',
                                                'version': '1.3.3-1.12'}}}
        if raw is not None: platform['inputSha256'] = sha256(raw).hexdigest()
        return {'bootstrap': {'platformInitialization': platform, 'packMaterialLinkage': {
            'elementMixinObserved': True, 'orePrefixMixinObserved': True, 'wholeInstalledMixinSet': False}}}

    def test_complete_sources_and_fresh_reset_cases(self):
        cases = lane.corpus(lane.configuration.ANCHOR)
        self.assertEqual(8, len(cases))
        self.assertEqual(cases[0]['files'], cases[5]['files'])
        self.assertEqual(cases[0]['files'], cases[-1]['files'])
        for case in cases[:6]:
            self.assertEqual(cases[0]['files'], {p: raw for p, raw in case['files'].items() if p != lane.CONFIG})
        for case in cases: self.assertEqual([], lane.check_platform(case, self.body(case)))

    def test_absent_values_and_favorable_qualification_are_rejected(self):
        case = lane.corpus(lane.configuration.ANCHOR)[1]; good = self.body(case)
        for key, value in (('status', 'threw'), ('side', 'CLIENT'), ('builtInContainers', []),
                           ('inputSha256', 'wrong'), ('customBuiltInVersions', False),
                           ('launcherCompositionQualified', True), ('fullCoremodInjectionExecuted', True),
                           ('containerScope', 'loaded'), ('loadingPluginBlacklist', [])):
            bad = deepcopy(good); bad['bootstrap']['platformInitialization'][key] = value
            self.assertTrue(lane.check_platform(case, bad), key)

    def test_configanytime_native_unbounded_version_is_preserved(self):
        case = lane.corpus(lane.configuration.ANCHOR)[1]; good = self.body(case)
        bad = deepcopy(good)
        bad['bootstrap']['platformInitialization']['builtInContainers'][2]['processedRange'] = '7.8'
        self.assertTrue(lane.check_platform(case, bad))

    def test_ivtoolkit_contract_is_not_activation_and_mixins_remain_required(self):
        case = lane.corpus(lane.configuration.ANCHOR)[0]; good = self.body(case)
        for key, value in (('nativeJavaCheckReturned', False), ('registeredWithCoreModManager', True),
                           ('injectDataExecuted', True), ('asmTransformers', ['replacement'])):
            bad = deepcopy(good); bad['bootstrap']['platformInitialization']['ivToolkit'][key] = value
            self.assertTrue(lane.check_platform(case, bad), key)
        for key in ('elementMixinObserved', 'orePrefixMixinObserved'):
            bad = deepcopy(good); bad['bootstrap']['packMaterialLinkage'][key] = False
            self.assertTrue(lane.check_platform(case, bad), key)


if __name__ == '__main__': unittest.main()
