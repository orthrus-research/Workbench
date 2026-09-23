"""Config witness custody/deferral tests; native parsing executes in fresh workers."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_pack_gtfo_configuration_conformance as lane


class GTFOConfigurationTests(unittest.TestCase):
    def saved(self):
        return (b'general {\n    gtfoapplecoreconfig {\nB:appleCoreCompat=true\nI:constantFoodStatsDivisor=1\n}\n'
                b'S:greenhouseDirts <\n>\n' + lane.SECTION + b'\n' + lane.APPLE + b'\n' + lane.DIVISOR + b'\n}\n}')

    def test_current_setting_does_not_rewrite_old_category(self):
        raw = self.saved(); changed = lane.edit_current(raw, lane.DIVISOR, b'I:constantFoodStatsDivisor=3')
        self.assertEqual(raw.split(lane.SECTION)[0], changed.split(lane.SECTION)[0])
        self.assertIn(b'I:constantFoodStatsDivisor=3', changed.split(lane.SECTION)[1])

    def test_ambiguous_or_missing_anchors_refuse(self):
        for raw in (b'', self.saved() + lane.SECTION, self.saved().replace(lane.APPLE, b'')):
            with self.assertRaises(ValueError): lane.edit_current(raw, lane.APPLE, b'')

    def test_complete_programs_and_fresh_baseline(self):
        cases = lane.corpus(b'sussy', self.saved())
        self.assertEqual(8, len(cases)); self.assertEqual(cases[0]['files'], cases[-1]['files'])
        self.assertNotIn(lane.CONFIG, cases[6]['files'])
        for case in cases:
            for name, raw in case['files'].items():
                if name != lane.CONFIG: self.assertEqual(cases[0]['files'][name], raw)
        old_only = cases[2]['files'][lane.CONFIG]
        self.assertIn(b'B:appleCoreCompat=true', old_only.split(lane.SECTION)[0])
        self.assertNotIn(b'B:appleCoreCompat=', old_only.split(lane.SECTION)[1])

    def test_missing_native_observation_is_not_success(self):
        self.assertTrue(lane.check_configuration({'files': {}}, {}))


if __name__ == '__main__': unittest.main()
