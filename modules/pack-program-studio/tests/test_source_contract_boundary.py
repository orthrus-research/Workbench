"""Compatibility facade errors and retained producer identities."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_api import source_declarations, source_locations
from workbench_pack_program_studio import source_intelligence
from workbench_pack_program_studio.declarations import (
    PackProgramError,
    declaration_set_identity,
    validate_source_declarations,
)

ROOT = Path(__file__).resolve().parents[3]


class SourceContractBoundaryTests(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / "api/tests/fixtures/source-navigation-v1.json").read_text())

    def test_pps_facades_preserve_original_error_classes(self):
        feed = self.fixture()
        feed["authority"]["owner"] = "another owner"
        feed["declaration_set_id"] = declaration_set_identity(feed)
        for validate in (
            validate_source_declarations,
            source_intelligence.validate_navigation_declarations,
        ):
            with self.subTest(validate=validate.__name__):
                with self.assertRaisesRegex(PackProgramError, "authority"):
                    validate(feed)
        with self.assertRaises(source_intelligence.SourceIntelligenceError):
            source_intelligence.key_identity({})
        feed = self.fixture()
        feed["binding"]["navigation_contract"] = 2
        feed["declaration_set_id"] = declaration_set_identity(feed)
        with self.assertRaises(source_intelligence.SourceIntelligenceError):
            source_intelligence.validate_navigation_declarations(feed)

    def test_api_contract_byte_changes_invalidate_loaded_normalizer_identity(self):
        original_read = Path.read_bytes
        for contract in (source_declarations, source_locations):
            with self.subTest(contract=contract.__name__):
                target = Path(contract.__file__)
                with patch.object(source_intelligence, "_LOADED_NORMALIZER", None):
                    source_intelligence.normalizer_identity()

                    def changed(path):
                        original = original_read(path)
                        return original + b"\n# changed contract\n" if path == target else original

                    with patch.object(Path, "read_bytes", changed):
                        with self.assertRaisesRegex(
                            source_intelligence.SourceIntelligenceError, "restart"
                        ):
                            source_intelligence.normalizer_identity()


if __name__ == "__main__":
    unittest.main()
