"""Witness integrity only; original semantics execute in separate native workers."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_pack_supercritical_conformance as lane


class PackSupercriticalTests(unittest.TestCase):
    def config(self):
        return b'\n'.join((lane.MATERIALS, lane.MODIFICATIONS, lane.DIVISOR))

    def test_complete_saved_programs_and_fresh_correction(self):
        cases = lane.corpus(b'saved sussy config', self.config())
        self.assertEqual(12, len(cases))
        self.assertEqual(cases[0]['files'], cases[-1]['files'])
        self.assertNotIn(lane.CONFIG, cases[7]['files'])
        for case in cases:
            self.assertIn('groovy/runConfig.json', case['files'])
            self.assertEqual(b'saved sussy config', case['files'][lane.configuration.CONFIG])
            self.assertEqual([lane.CORIUM, lane.TARGET], case['observeMaterials'])
        self.assertIn(lane.DECLARATION.encode(), cases[9]['files'][lane.PRODUCER])
        self.assertNotIn(lane.DECLARATION.encode(), cases[-1]['files'][lane.PRODUCER])

    def test_config_replacements_require_exact_anchors(self):
        for raw in (b'', self.config() + b'\n' + lane.MODIFICATIONS):
            with self.assertRaises(ValueError): lane.corpus(b'sussy', raw)

    def state(self):
        return {'status': 'observed', 'coriumStaticIdentity': True, 'coriumStorageRegistry': 'supercritical',
                'damageFunctionsInvoked': False, 'generatedItemsRegistered': False, 'fluidsRegistered': False,
                'prefixes': [{'name': name, 'nativePrefixIdentity': True, 'metaItemDeclarationCount': 1,
                              'radiationFunctionPresent': name in lane.PREFIXES[:6],
                              'heatFunctionPresent': name == 'fuelRodHotDepleted'} for name in lane.PREFIXES]}

    def test_prefix_inventory_must_be_native_and_unique(self):
        self.assertEqual([], lane.check_prefixes(self.state()))
        for key, value in [('nativePrefixIdentity', False), ('metaItemDeclarationCount', 2),
                           ('radiationFunctionPresent', False), ('heatFunctionPresent', True)]:
            bad = self.state(); bad['prefixes'][0][key] = value
            self.assertTrue(lane.check_prefixes(bad))
        bad = self.state(); bad['prefixes'][-1] = deepcopy(bad['prefixes'][0])
        self.assertTrue(lane.check_prefixes(bad))

    def test_observation_cannot_qualify_gameplay_or_generated_content(self):
        for key, value in [('damageFunctionsInvoked', True), ('fluidsRegistered', True),
                           ('generatedItemsRegistered', True), ('coriumStorageRegistry', 'gregtech'),
                           ('coriumStaticIdentity', False)]:
            bad = self.state(); bad[key] = value
            self.assertTrue(lane.check_prefixes(bad))

    def test_absent_registration_cannot_pass_either_path(self):
        self.assertTrue(lane.check_composition({}))
        self.assertTrue(lane.check_composition({}, True))

    def test_deferral_requires_gap_and_unvisited_observer(self):
        e = {'supercriticalSubscribers': {'status': 'deferred', 'reason': lane.GAP, 'subscribers': [],
                                          'constructionOverrideApplied': False, 'discoveryOrderQualified': False},
             'initialization': {'steps': [{'id': 'supercritical-configuration', 'status': 'returned'},
                *[{'id': name, 'status': 'deferred'} for name in
                  ('supercritical-native-proxy-registration', 'supercritical-native-events-registration')]]},
             'coverageGaps': [lane.GAP],
             'supercriticalState': {'status': 'not-observed-before-material-callback-completion'}}
        self.assertEqual([], lane.check_composition({'execution': e}, True))
        for key, value in [('coverageGaps', []), ('supercriticalState', self.state())]:
            bad = deepcopy(e); bad[key] = value
            self.assertTrue(lane.check_composition({'execution': bad}, True))
        bad = deepcopy(e); bad['initialization']['steps'][1]['elapsedNanos'] = 0
        self.assertTrue(lane.check_composition({'execution': bad}, True))


if __name__ == '__main__': unittest.main()
