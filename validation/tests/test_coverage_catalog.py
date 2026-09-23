"""Coverage descriptions must remain grounded in owned executable files."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coverage_catalog
from suite_catalog import PYTHON_TEST_SUITES


class CoverageCatalogTests(unittest.TestCase):
    def test_every_owner_has_live_behavior_references_without_dropping_other_tests(self):
        document = coverage_catalog.coverage_inventory()
        self.assertEqual([row.name for row in PYTHON_TEST_SUITES], [row['suite'] for row in document['suites']])
        for suite, row in zip(PYTHON_TEST_SUITES, document['suites']):
            referenced = {path for area in row['areas'] for path in area['test_files']}
            all_files = {path.relative_to(coverage_catalog.ROOT).as_posix() for path in suite.test_files()}
            self.assertTrue(row['areas'], suite.name)
            self.assertEqual(all_files, referenced | set(row['other_assigned_test_files']))
            self.assertTrue(referenced.isdisjoint(row['other_assigned_test_files']))
        self.assertEqual(document, json.loads(json.dumps(document)))

    def test_missing_owner_cannot_silently_erase_coverage_obligations(self):
        changed = dict(coverage_catalog.OWNER_COVERAGE)
        changed.pop('crucible')
        with patch.object(coverage_catalog, 'OWNER_COVERAGE', changed):
            with self.assertRaisesRegex(ValueError, 'owners must match'):
                coverage_catalog.coverage_inventory()

    def test_reference_to_another_suite_file_is_rejected(self):
        changed = dict(coverage_catalog.OWNER_COVERAGE)
        changed['atlas'] = (replace(changed['atlas'][0], test_files=('test_crucible_v2_contracts.py',)),)
        with patch.object(coverage_catalog, 'OWNER_COVERAGE', changed):
            with self.assertRaisesRegex(ValueError, 'unowned test files for atlas'):
                coverage_catalog.coverage_inventory()

    def test_unknown_category_cannot_be_mistaken_for_runtime_proof(self):
        changed = dict(coverage_catalog.OWNER_COVERAGE)
        changed['core-api'] = (replace(changed['core-api'][0], category='release-qualified'),)
        with patch.object(coverage_catalog, 'OWNER_COVERAGE', changed):
            with self.assertRaisesRegex(ValueError, 'invalid coverage area'):
                coverage_catalog.coverage_inventory()

    def test_physical_and_installed_areas_preserve_prerequisites_and_bounded_claims(self):
        document = coverage_catalog.coverage_inventory()
        physical = [area for row in document['suites'] for area in row['areas'] if area['category']=='physical-runtime']
        self.assertEqual(4, len(physical))
        self.assertTrue(all(area['prerequisites'] for area in physical))
        self.assertTrue(any('do not prove' in limitation for limitation in document['limitations']))
        shell = next(row for row in document['suites'] if row['suite']=='workbench-shell')
        self.assertIn('installed-product', {area['category'] for area in shell['areas']})


if __name__ == '__main__':
    unittest.main()
