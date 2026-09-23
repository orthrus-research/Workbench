"""Material effect witness/custody tests, without original native execution."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import axiom_pack_material_effect_conformance as effects


class MaterialEffectWitnessTests(unittest.TestCase):
    def rows(self):
        return {row['name']: row for row in effects.corpus(b'saved config\r\n', b'original ordered runConfig')}

    def test_complete_source_corpus_preserves_configuration_loaders_and_exact_manual_correction(self):
        rows = self.rows()
        self.assertEqual(8, len(rows))
        baseline = rows['baseline']['files']
        for row in rows.values():
            self.assertEqual(set(baseline), set(row['files']))
            self.assertNotIn('observeMaterials', row)
            self.assertNotIn('expectations', row)
            for path in baseline.keys() - {effects.PRODUCER, effects.EDITS}:
                self.assertEqual(baseline[path], row['files'][path])
        self.assertEqual(baseline, rows['deletion']['files'])
        self.assertEqual(baseline, rows['baseline-repeat']['files'])
        self.assertEqual(rows['addition']['files'], rows['manual-correction']['files'])
        self.assertIn(effects.ADDITION.encode(), rows['addition']['files'][effects.PRODUCER])

    def test_same_count_identity_state_edits_and_original_setter_failure_are_explicit(self):
        rows = self.rows()
        self.assertEqual(effects.delta(modified=(effects.IDENTITY,)), rows['same-count-property']['effectDelta'])
        self.assertEqual(effects.delta(added=(effects.REPLACEMENT,), removed=(effects.IDENTITY,)),
                         rows['same-count-identity-replacement']['effectDelta'])
        self.assertEqual(effects.delta(removed=(effects.IDENTITY,)), rows['deletion']['effectDelta'])
        self.assertEqual('addition', rows['deletion']['comparisonReference'])
        source = rows['native-error']['files'][effects.EDITS].decode().splitlines()
        self.assertIn(effects.BAD_SETTER, source[9])
        self.assertEqual([effects.IDENTITY], rows['native-error']['probeIdentities'])
        self.assertIsNone(rows['native-error']['comparisonReference'])
        self.assertIsNone(rows['native-error']['effectDelta'])

    def execution(self, entries=None, phase='FROZEN'):
        if entries is None:
            entries = {'gregtech:iron': 'a' * 64, effects.IDENTITY: 'b' * 64}
        return {'phase': phase, 'materialRegistries': {'totalRegisteredMaterials': len(entries)},
                'registrationEffects': {'schema': 'axiom.native-registration-effects.v1', 'phase': phase,
                    'materials': {'status': 'observed', 'inventoryComplete': True, 'membership': 'native-material-registry',
                                  'stateScope': effects.STATE_SCOPE, 'entries': entries}}}

    def test_complete_catalog_observes_all_identities_and_same_count_state_changes(self):
        before = self.execution()
        after = deepcopy(before)
        after['registrationEffects']['materials']['entries'][effects.IDENTITY] = 'c' * 64
        self.assertEqual(effects.delta(modified=(effects.IDENTITY,)),
                         effects.identity_delta(effects.native_materials(before), effects.native_materials(after)))
        replacement = self.execution({'gregtech:iron': 'a' * 64, effects.REPLACEMENT: 'b' * 64})
        self.assertEqual(effects.delta(added=(effects.REPLACEMENT,), removed=(effects.IDENTITY,)),
                         effects.identity_delta(effects.native_materials(before), effects.native_materials(replacement)))

    def test_missing_partial_count_mismatch_invalid_digest_and_wrong_scope_refuse(self):
        with self.assertRaisesRegex(ValueError, 'phase or schema'):
            effects.native_materials({})
        mutations = [lambda row: row['registrationEffects']['materials'].update(status='unavailable'),
                     lambda row: row['registrationEffects']['materials'].update(inventoryComplete=False),
                     lambda row: row['registrationEffects']['materials'].update(stateScope='other'),
                     lambda row: row['registrationEffects']['materials'].update(entries={effects.IDENTITY: 'bad'}),
                     lambda row: row['materialRegistries'].update(totalRegisteredMaterials=1),
                     lambda row: row['materialRegistries'].update(totalRegisteredMaterials=True),
                     lambda row: row['registrationEffects'].update(phase='POST')]
        for mutation in mutations:
            invalid = self.execution()
            mutation(invalid)
            with self.assertRaises(ValueError):
                effects.native_materials(invalid)
        self.assertEqual({}, effects.native_materials(self.execution({}))['entries'])

    def test_complete_interrupted_phase_does_not_imply_frozen_comparability(self):
        partial = effects.native_materials(self.execution(phase='POST'))
        complete = effects.native_materials(self.execution())
        self.assertIn(effects.IDENTITY, partial['entries'])
        with self.assertRaisesRegex(ValueError, 'no cross-checkpoint delta'):
            effects.identity_delta(partial, complete)
        self.assertEqual(effects.delta(), effects.identity_delta(partial, partial))


if __name__ == '__main__':
    unittest.main()
