from __future__ import annotations

import unittest

from workbench_atlas_projection import canonical_semantic_identity


class SemanticIdentityTests(unittest.TestCase):
    def test_canonical_identity_ignores_layer_and_provenance(self) -> None:
        descriptor = {
            "domain": "material-form",
            "kind": "item-form",
            "key": {"id": "metaitem:dustLimestone"},
        }
        source_semantic = canonical_semantic_identity(descriptor)
        runtime_semantic = canonical_semantic_identity(
            {"kind": "item-form", "key": {"id": "metaitem:dustLimestone"}, "domain": "material-form"}
        )
        self.assertEqual(source_semantic, runtime_semantic)
        self.assertTrue(source_semantic.startswith("workbench-atlas-semantic:sha256:"))


if __name__ == "__main__":
    unittest.main()
