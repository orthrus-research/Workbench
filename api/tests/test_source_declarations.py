from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api/src"))

from workbench_api.source_declarations import (
    SourceDeclarationError,
    SourceNavigationContractError,
    declaration_set_identity,
    key_identity,
    validate_navigation_declarations,
    validate_source_declarations,
)
from workbench_api.source_locations import SourceLocationError


class SourceDeclarationContractTests(unittest.TestCase):
    def fixture(self):
        # Produced by the original PPS V1 implementation before extraction.
        return json.loads(
            (Path(__file__).parent / "fixtures/source-navigation-v1.json").read_text()
        )

    def test_historical_unicode_feed_and_key_encoding_are_unchanged(self):
        feed = self.fixture()
        self.assertEqual(
            "workbench-pack-source-declarations:sha256:"
            "091ab2655a8099d9215c148dfb77e80a3eec3147deac9be636275e9047f0dcfe",
            declaration_set_identity(feed),
        )
        self.assertEqual(
            feed, validate_navigation_declarations(feed, sources={"quests.json": b"x"})
        )
        self.assertEqual(
            '{"domain":"test","key":{"id":"\\u786b\\u9178-\\u00df-\\ud83d\\ude00"},"kind":"quest"}',
            key_identity(feed["declarations"][0]["semantic_descriptor"]),
        )

    def test_changed_record_content_is_not_admitted_after_resealing_feed(self):
        feed = self.fixture()
        feed["declarations"][0]["attributes"]["navigation"]["label"] = "changed"
        feed["declaration_set_id"] = declaration_set_identity(feed)
        with self.assertRaisesRegex(SourceDeclarationError, "declaration identity"):
            validate_source_declarations(feed)

    def test_original_owner_and_static_authority_cannot_be_upgraded(self):
        for change in ({"owner": "another producer"}, {"runtime_authority": "observed"}):
            with self.subTest(change=change):
                feed = self.fixture()
                feed["authority"].update(change)
                feed["declaration_set_id"] = declaration_set_identity(feed)
                with self.assertRaises(SourceDeclarationError):
                    validate_source_declarations(feed)

    def test_duplicate_occurrences_are_rejected_even_with_matching_summary(self):
        feed = self.fixture()
        feed["declarations"].append(deepcopy(feed["declarations"][0]))
        feed["summary"]["declarations"] = 2
        feed["declaration_set_id"] = declaration_set_identity(feed)
        with self.assertRaisesRegex(SourceDeclarationError, "duplicate"):
            validate_source_declarations(feed)

    def test_incomplete_navigation_binding_is_rejected(self):
        feed = self.fixture()
        feed["binding"]["source_observation"]["source_sha256"] = "c" * 64
        feed["declaration_set_id"] = declaration_set_identity(feed)
        with self.assertRaisesRegex(SourceNavigationContractError, "binding"):
            validate_navigation_declarations(feed)

    def test_supplied_sources_must_contain_exact_bound_location_bytes(self):
        with self.assertRaisesRegex(SourceNavigationContractError, "escaped"):
            validate_navigation_declarations(self.fixture(), sources={})
        with self.assertRaisesRegex(SourceLocationError, "changed"):
            validate_navigation_declarations(self.fixture(), sources={"quests.json": b"y"})


if __name__ == "__main__":
    unittest.main()
