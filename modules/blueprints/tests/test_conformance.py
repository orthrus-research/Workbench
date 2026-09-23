#!/usr/bin/env python3

"""V01 whole-engine conformance and independent proof validation."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

from _support import EXAMPLE_ROOT, SCHEMA_ROOT, SOURCE_ROOT

if str(SOURCE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT.parent))

from engine_conformance_fixture import run_conformance  # noqa: E402
from workbench_blueprints import conformance  # noqa: E402


EXAMPLE_PATH = EXAMPLE_ROOT / "engine-conformance-proof-example-v1.json"


class EngineConformanceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.proof = run_conformance()

    def test_schema_is_closed_and_valid(self) -> None:
        schema = json.loads(
            (SCHEMA_ROOT / "blueprints-engine-conformance-proof-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        self.assertFalse(schema["additionalProperties"])

    def test_executable_scenarios_reproduce_the_canonical_proof(self) -> None:
        expected = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(expected, self.proof)
        self.assertEqual(expected, conformance.parse_engine_conformance_proof(expected))

    def test_proof_rejects_scenario_identity_and_authority_promotion(self) -> None:
        missing = deepcopy(self.proof)
        missing["scenarios"] = missing["scenarios"][:-1]
        with self.assertRaises(conformance.ConformanceValidationError):
            conformance.parse_engine_conformance_proof(missing)

        promoted = deepcopy(self.proof)
        promoted["fixture_scope"] = "stable-authority"
        with self.assertRaises(conformance.ConformanceValidationError):
            conformance.parse_engine_conformance_proof(promoted)

        tampered = deepcopy(self.proof)
        tampered["scenarios"][0]["assertions"] = ["release-withheld"]
        material = dict(tampered)
        material.pop("proof_id")
        tampered["proof_id"] = conformance.PROOF_PREFIX + conformance._digest(material)
        with self.assertRaises(conformance.ConformanceValidationError):
            conformance.parse_engine_conformance_proof(tampered)


if __name__ == "__main__":
    unittest.main()
