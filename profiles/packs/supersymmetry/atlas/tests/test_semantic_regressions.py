from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import unittest

from supersymmetry_semantic_adapter import (
    build_fixture_projection,
    run_acceptance_gate,
)
from workbench_atlas_projection import explain_why, impact_report
from workbench_atlas_projection import cli
from workbench_api.profile_extensions import require_profile_extension
from workbench_api.profiles import profile_scope
from workbench_profile_supersymmetry import semantic_projections


ROOT = Path(__file__).resolve().parents[5]


class SupersymmetrySemanticRegressionTests(unittest.TestCase):
    def test_native_adapter_preserves_legacy_fixture_projection_identities(self) -> None:
        adapter = require_profile_extension("workbench.semantic_projections", "supersymmetry")
        self.assertIs(semantic_projections, adapter)
        expected = {
            "cement-reachability": "9569f4e4898edbaac289add71f603b112d8bcd81cc24c8fd37d89eaf0e8e1a12",
            "lv-mixer-capacity": "6605b2cbc8e08f66353645120f85e59f02280f97da9638515e7b2192db928c18",
            "duplicate-gas-registration": "076d59c0d56306c517e52cab87894812a36e570e475720d72f059bf9d7c51670",
        }
        for fixture, digest in expected.items():
            with self.subTest(fixture=fixture):
                projection = adapter.build_fixture_projection(ROOT, fixture)
                self.assertEqual(
                    "workbench-atlas-semantic-projection:sha256:" + digest,
                    projection["projection_id"],
                )
                self.assertEqual(projection, build_fixture_projection(ROOT, fixture))

    def test_cached_semantic_adapter_does_not_bypass_disabled_profile(self) -> None:
        require_profile_extension("workbench.semantic_projections", "supersymmetry")
        output, errors = StringIO(), StringIO()
        with profile_scope(disabled=("supersymmetry",)), redirect_stdout(output), redirect_stderr(errors):
            status = cli.main(["check", "--profile", "supersymmetry", "--json"], root=ROOT)
        self.assertEqual(2, status)
        self.assertEqual("", output.getvalue())
        self.assertIn("unavailable", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_native_semantic_cli_resolves_check_why_and_impact(self) -> None:
        commands = (
            (["check"], lambda result: result["summary"]["status"] == "pass"),
            (["why", "limestone.dust", "--fixture", "cement-reachability"],
             lambda result: result["conclusion"] == "registered-not-proven-reachable"),
            (["impact", "metaitem:limestone.dust", "--fixture", "cement-reachability"],
             lambda result: result["summary"]["affected_semantics"] == 2),
        )
        for command, expected in commands:
            with self.subTest(command=command):
                output, errors = StringIO(), StringIO()
                paths = list(sys.path)
                with redirect_stdout(output), redirect_stderr(errors):
                    status = cli.main([*command, "--profile", "supersymmetry", "--json"], root=ROOT)
                self.assertEqual(0, status, errors.getvalue())
                self.assertTrue(expected(json.loads(output.getvalue())))
                self.assertEqual(paths, sys.path)

    def test_first_gate_replays_cement_and_lv_mixer(self) -> None:
        result = run_acceptance_gate(ROOT)
        self.assertEqual("pass", result["summary"]["status"])
        self.assertEqual(
            ["cement-reachability", "lv-mixer-capacity"],
            [row["fixture"] for row in result["fixtures"]],
        )
        self.assertEqual(2, result["summary"]["passed"])

    def test_duplicate_gas_is_the_next_admitted_fixture(self) -> None:
        result = run_acceptance_gate(ROOT, include_next=True)
        self.assertEqual("pass", result["summary"]["status"])
        self.assertEqual(3, result["summary"]["fixtures"])
        duplicate = result["fixtures"][-1]
        self.assertEqual("duplicate-gas-registration", duplicate["fixture"])
        self.assertEqual(["DUPLICATE_REGISTRATION"], duplicate["actual_diagnostic_codes"])

    def test_layers_retain_distinct_authorities(self) -> None:
        projection = build_fixture_projection(ROOT, "cement-reachability")
        self.assertEqual("Pack Program Studio", projection["layers"]["SOURCE"]["authority"])
        self.assertEqual("Crucible", projection["layers"]["RUNTIME"]["authority"])
        self.assertEqual("Atlas", projection["layers"]["PLAYABLE"]["authority"])
        self.assertEqual("PROGRESSION_UNREACHABLE", projection["diagnostics"][0]["code"])

    def test_why_and_impact_preserve_evidence_chain(self) -> None:
        projection = build_fixture_projection(ROOT, "cement-reachability")
        why = explain_why(projection, "limestone.dust")
        self.assertEqual("registered-not-proven-reachable", why["conclusion"])
        self.assertEqual("PROGRESSION_UNREACHABLE", why["diagnostics"][0]["code"])
        impact = impact_report(projection, "metaitem:limestone.dust")
        self.assertEqual(2, impact["summary"]["affected_semantics"])
        self.assertEqual(1, impact["summary"]["diagnostics"])

    def test_workbench_check_json_surface(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "tools/workbench.py",
                "check",
                "--profile",
                "supersymmetry",
                "--json",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("pass", result["summary"]["status"])


if __name__ == "__main__":
    unittest.main()
