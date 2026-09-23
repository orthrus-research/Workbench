"""Version choices do not bypass profile policy or select ambient Java."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_core.java_inventory import inspect_java_inventory, java_candidates
from workbench_core import setup_cli
from test_runtime_java import POLICY, HOST


class JavaInventoryTests(unittest.TestCase):
    def test_multiple_versions_are_reported_without_selection(self):
        def probe(path):
            version = '21.0.8+9' if 'jdk21' in str(path) else '25.0.4+7-LTS'
            return {'runtime_version': version, 'java_version': version.split('+')[0],
                    'vendor': 'Eclipse Adoptium', 'vendor_version': '', 'os_arch': 'amd64',
                    'java_home': str(path.parent.parent)}
        with tempfile.TemporaryDirectory() as temporary:
            homes = [Path(temporary) / name for name in ('jdk21', 'jdk25')]
            for home in homes:
                (home / 'bin').mkdir(parents=True)
                (home / 'bin/javac').touch()
            with patch('workbench_core.java_inventory.probe_java', side_effect=probe):
                record = inspect_java_inventory(policy=POLICY, homes=homes, roots=[], environment={}, host=HOST)
            self.assertIsNone(record['selected'])
            self.assertEqual([21, 25], [r['feature_version'] for r in record['candidates']])
            self.assertEqual(['incompatible', 'matches-profile'], [r['compatibility'] for r in record['candidates']])
            self.assertTrue(all(r['jdk'] for r in record['candidates']))
            policy21 = {**POLICY, 'release_name': 'jdk-21.0.8+9', 'feature_version': 21}
            with patch('workbench_core.java_inventory.probe_java', side_effect=probe):
                alternate = inspect_java_inventory(policy=policy21, homes=homes, roots=[], environment={}, host=HOST)
            self.assertEqual(['matches-profile', 'incompatible'], [r['compatibility'] for r in alternate['candidates']])

    def test_release_build_prefix_does_not_match_another_build(self):
        from workbench_core.runtime_java import _probe_mismatch
        probe = {'runtime_version': '25.0.4+70', 'vendor': 'Eclipse Adoptium', 'os_arch': 'amd64'}
        self.assertIn('requires runtime', _probe_mismatch(probe, POLICY, HOST))

    def test_native_windows_paths_and_explicit_candidates_are_preserved(self):
        host = {**HOST, 'os': 'windows'}
        rows = java_candidates(homes=[Path('JDK 21 é')], roots=[], environment={}, host=host)
        self.assertEqual('java.exe', rows[0][1].name)
        self.assertIn('JDK 21 é', str(rows[0][1]))

    def test_inventory_does_not_require_or_write_setup_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / 'missing/setup.json'
            output = io.StringIO()
            with patch('workbench_core.java_inventory.java_candidates', return_value=[]):
                result = setup_cli.main(['--list-java', '--json'], root=temporary,
                                        environment={}, record_path=record, output=output)
            self.assertEqual(0, result)
            self.assertEqual([], json.loads(output.getvalue())['candidates'])
            self.assertFalse(record.parent.exists())


if __name__ == '__main__':
    unittest.main()
