"""GCYM witness integrity; native semantics are exercised in separate JVMs."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_pack_gcym_conformance as lane
from axiom_material_program_cases import PRODUCER


class PackGCYMTests(unittest.TestCase):
    def test_complete_programs_change_only_declaration_in_fixture_copies(self):
        cases = lane.corpus(b'# exact saved configuration\r\n')
        self.assertEqual(9, len(cases))
        self.assertEqual(cases[0]['files'], cases[-1]['files'])
        self.assertEqual(cases[5]['files'], cases[7]['files'])
        for case in cases:
            self.assertEqual(set(cases[0]['files']), set(case['files']))
            self.assertIn(b'class DeveloperMaterials', case['files'][PRODUCER])
            for path, raw in case['files'].items():
                if path != PRODUCER: self.assertEqual(cases[0]['files'][path], raw)
        self.assertNotIn(b"named(31003", cases[4]['files'][PRODUCER])
        self.assertIn(b'components(Iron)', cases[6]['files'][PRODUCER])

    def state(self):
        return {'materials': [{'name': lane.TARGET, 'nativePropertyState': {'properties': {
            'blast_alloy': {'class': lane.ALLOY, 'valuesObserved': True,
                            'values': {'temperature': 1800, 'forceGenerateMolten': False}}}}}],
            'deferredWork': {'fluids': [{'material': lane.TARGET, 'registrationCompleted': False,
                'stored': [], 'queued': [{'key': 'gcym:molten', 'temperature': 1800}]}]}}

    def test_property_witness_requires_original_identity_and_native_values(self):
        good = self.state()
        self.assertEqual([], lane.check_material(good, lane.TARGET, 1800, True))
        for key, value in [('class', 'fabricated.Property'), ('valuesObserved', False), ('values', {})]:
            bad = deepcopy(good)
            bad['materials'][0]['nativePropertyState']['properties']['blast_alloy'][key] = value
            self.assertTrue(lane.check_material(bad, lane.TARGET, 1800, True))
        self.assertTrue(lane.check_material(good, lane.TARGET, 1000, True))
        self.assertTrue(lane.check_material(good, lane.TARGET, 1800, True, forced=True))

    def test_queue_witness_cannot_be_promoted_to_registered_fluid(self):
        for key, value in [('registrationCompleted', True), ('stored', [{'fluid': 'molten'}]),
                           ('queued', []), ('queued', [{'key': 'gcym:molten', 'temperature': 99}])]:
            bad = self.state(); bad['deferredWork']['fluids'][0][key] = value
            self.assertTrue(lane.check_material(bad, lane.TARGET, 1800, True))
        self.assertTrue(lane.check_material(self.state(), lane.TARGET, 1800, False))

    def test_removed_material_needs_an_actual_missing_lookup(self):
        self.assertEqual([], lane.check_material({'missingMaterials': [lane.TARGET]}, lane.TARGET,
                                                 None, False, removed=True))
        self.assertTrue(lane.check_material({}, lane.TARGET, None, False, removed=True))
        self.assertTrue(lane.check_material(self.state(), lane.TARGET, None, False, removed=True))

    def test_missing_native_registration_is_not_success(self):
        self.assertTrue(lane.check_composition({}))


if __name__ == '__main__': unittest.main()
