from __future__ import annotations

import copy
import unittest

from workbench_pack_program_studio.declarations import (
    PackProgramError,
    build_source_declarations,
    declaration_set_identity,
    declarations_from_program,
    source_declaration,
    validate_source_declarations,
)


class SourceDeclarationFeedTests(unittest.TestCase):
    def test_build_preserves_static_authority_and_provenance(self) -> None:
        row = source_declaration(
            semantic_descriptor={
                "domain": "material-form",
                "kind": "item-form",
                "key": {"id": "metaitem:dustLimestone"},
            },
            attributes={"operation": "reference"},
            lifecycle={"stage": "postInit"},
            provenance={
                "authority": "Pack Program Studio",
                "source": {"path": "postInit/CementChain.groovy", "line": 65},
            },
        )
        feed = build_source_declarations(
            program_id="program:fixture",
            pack_profile_id="workbench-pack:fixture",
            platform_profile_id="workbench-platform:cleanroom:test",
            source_sha256="a" * 64,
            declarations=[row],
        )
        self.assertEqual("Pack Program Studio", feed["authority"]["owner"])
        self.assertEqual("none", feed["authority"]["runtime_authority"])
        self.assertEqual("static-candidate", feed["declarations"][0]["evidence_state"])
        self.assertEqual(65, feed["declarations"][0]["provenance"]["source"]["line"])

    def test_program_projection_uses_effect_identity_and_source(self) -> None:
        program = {
            "program_id": "program:fixture",
            "binding": {
                "pack_profile_id": "workbench-pack:fixture",
                "platform_profile_id": "workbench-platform:cleanroom:test",
                "source_sha256": "b" * 64,
            },
            "effects": [
                {
                    "effect_id": "effect:1",
                    "semantic_key": "semantic:1",
                    "category": "reference",
                    "kind": "item-reference",
                    "operation": "read",
                    "identity": {"registry_id": "example:item"},
                    "fields": {"registry_id": "example:item"},
                    "expression": "item('example:item')",
                    "recipe": None,
                    "lifecycle": {"stage": "postInit"},
                    "source": {"path": "postInit/Test.groovy", "line": 4},
                    "rule_id": "item-reference",
                }
            ],
        }
        feed = declarations_from_program(program)
        row = feed["declarations"][0]
        self.assertEqual({"registry_id": "example:item"}, row["semantic_descriptor"]["key"])
        self.assertEqual("effect:1", row["source_effect_id"])

    def test_runtime_authority_claim_is_rejected(self) -> None:
        row = source_declaration(
            semantic_descriptor={"domain": "x", "kind": "y", "key": {"id": "z"}},
            attributes={},
            lifecycle={},
            provenance={"authority": "Pack Program Studio"},
        )
        feed = build_source_declarations(
            program_id="p",
            pack_profile_id="pack",
            platform_profile_id="platform",
            source_sha256="c" * 64,
            declarations=[row],
        )
        tampered = copy.deepcopy(feed)
        tampered["authority"]["runtime_authority"] = "Pack Program Studio"
        tampered["declaration_set_id"] = declaration_set_identity(tampered)
        with self.assertRaises(PackProgramError):
            validate_source_declarations(tampered)


if __name__ == "__main__":
    unittest.main()
