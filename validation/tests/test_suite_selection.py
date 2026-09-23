"""Conservative selection must never silently omit an unknown boundary."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

VALIDATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VALIDATION))
import suite_selection
from suite_catalog import PYTHON_TEST_SUITES


class SuiteSelectionTests(unittest.TestCase):
    def assert_canonical(self, paths):
        plan = suite_selection.select_impacted_suites(paths)
        self.assertEqual("canonical", plan.selection_kind)
        self.assertEqual(tuple(row.name for row in PYTHON_TEST_SUITES), plan.selected_suites)
        self.assertTrue(plan.broadening_reasons)
        self.assertTrue(all(row.selected and row.reasons for row in plan.decisions))
        return plan

    def test_shared_and_unknown_boundaries_broaden_canonical(self):
        for path in (
            "api/src/workbench_api/canonical.py", "core/src/workbench_core/sessions.py",
            "modules/material-semantics/src/workbench_material_semantics/__init__.py",
            "validation/suite_execution.py", "modules/atlas/schemas/new.schema.json",
            "modules/workbench-shell/src/workbench_shell/service_host_v3.py",
            "modules/atlas/contracts/public-v4.json", "modules/atlas/pyproject.toml",
            "profiles/packs/supersymmetry/profile.yaml", "unmapped/code.py",
            "modules/new-owner/src/new.py", "docs/architecture/new.md",
        ):
            with self.subTest(path=path):
                self.assert_canonical((path,))

    def test_no_change_evidence_and_invalid_paths_never_mean_zero_tests(self):
        self.assert_canonical(())
        for path in ("", "/tmp/file.py", "../api/src/x.py", "modules/atlas/../core/x.py", "modules\\atlas\\x.py", "C:/repo/x.py", "modules//atlas/x.py", "\0"):
            with self.subTest(path=path):
                self.assert_canonical((path,))

    def test_project_intelligence_selects_transitive_runtime_and_profile_consumers(self):
        plan = suite_selection.select_impacted_suites(("modules/project-intelligence/src/workbench_project_intelligence/inspector.py",))
        self.assertEqual("focused", plan.selection_kind)
        self.assertFalse(plan.broadening_reasons)
        self.assertTrue({"project-intelligence", "pack-program-studio", "atlas", "runtime-explorer", "relay", "workbench-shell", "developer-feature", "cleanroom-profile", "supersymmetry-atlas"}.issubset(plan.selected_suites))
        self.assertNotIn("core-api", plan.selected_suites)
        self.assertTrue(all(row.reasons for row in plan.decisions))

    def test_profile_tests_and_data_select_profile_owner_and_product_consumers(self):
        plan = suite_selection.select_impacted_suites(("profiles/packs/supersymmetry/atlas/semantic-projection/fixtures/cement-reachability.json",))
        self.assertEqual("focused", plan.selection_kind)
        self.assertTrue({"supersymmetry-atlas", "supersymmetry-blueprints", "supersymmetry-runtime", "supersymmetry-worldgen", "workbench-shell", "developer-feature", "validation-authority"}.issubset(plan.selected_suites))

    def test_deleted_files_keep_owner_and_renames_union_both_sides(self):
        old = "modules/process-studio/tests/deleted_test.py"
        new = "modules/worldgen-qualifier/tests/renamed_test.py"
        self.assertFalse((VALIDATION.parent / old).exists())
        plan = suite_selection.select_impacted_suites((old, new, old))
        self.assertEqual((old, new), plan.changed_paths)
        self.assertTrue({"process-studio", "worldgen-qualifier"}.issubset(plan.selected_suites))
        self.assertEqual("focused", plan.selection_kind)

    def test_unknown_mixed_with_known_cannot_hide_behind_focused_selection(self):
        self.assert_canonical(("modules/process-studio/src/change.py", "unknown/change.py"))

    def test_missing_dependency_metadata_broadens_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = suite_selection.select_impacted_suites(("modules/atlas/src/change.py",), root=Path(temporary))
        self.assertEqual("canonical", plan.selection_kind)
        self.assertTrue(any("dependency map could not be validated" in row for row in plan.broadening_reasons))

    def test_default_root_is_resolved_at_call_time(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(suite_selection, "ROOT", Path(temporary)):
            plan = suite_selection.select_impacted_suites(("modules/atlas/src/change.py",))
        self.assertEqual("canonical", plan.selection_kind)

    def test_cli_emits_every_selected_and_omitted_reason_without_execution(self):
        process = subprocess.run([sys.executable, str(VALIDATION / "suite_selection.py"), "--changed-file", "modules/process-studio/src/change.py", "--json"], text=True, capture_output=True, check=True)
        document = json.loads(process.stdout)
        self.assertEqual("focused", document["selection_kind"])
        self.assertEqual(len(PYTHON_TEST_SUITES), len(document["decisions"]))
        self.assertTrue(any(not row["selected"] for row in document["decisions"]))
        self.assertTrue(all(row["reasons"] for row in document["decisions"]))


if __name__ == "__main__":
    unittest.main()
