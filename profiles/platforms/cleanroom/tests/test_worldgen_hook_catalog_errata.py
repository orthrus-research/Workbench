from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[4]
CANDIDATE = ROOT / "profiles" / "platforms" / "cleanroom" / "candidates" / "0.6.8-alpha"
CATALOG = CANDIDATE / "worldgen-hook-catalog-v1.json"
ERRATA = CANDIDATE / "worldgen-hook-catalog-v1-errata-v1.json"


class WorldgenHookCatalogErrataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.errata = json.loads(ERRATA.read_text(encoding="utf-8"))

    def test_overlay_binds_the_unchanged_v1_catalog(self) -> None:
        self.assertEqual(
            "worldgen-hook-catalog:sha256:"
            "a91a820832548e7d6bd19ec1efd92fe55684cf77552d67fd6b2bf86910a999b3",
            self.catalog["catalog_id"],
        )
        self.assertEqual(self.catalog["catalog_id"], self.errata["base_catalog_id"])
        self.assertIn("V1 catalog remains unchanged", self.errata["disposition"])

    def test_overlay_id_is_canonical_content_identity(self) -> None:
        payload = dict(self.errata)
        claimed = payload.pop("overlay_id")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        actual = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(
            f"worldgen-hook-catalog-v1-errata:sha256:{actual}",
            claimed,
        )

    def test_material_v1_corrections_are_explicit(self) -> None:
        corrected = {
            hook_id
            for row in self.errata["corrections"]
            for hook_id in row["hook_ids"]
        }
        self.assertTrue(
            {
                "terraingen.ore.pre",
                "terraingen.ore.post",
                "i_chunk_generator.populate",
                "game_registry.generate_world",
                "world_event.load",
                "world_event.unload",
                "biome_provider.init_layers",
                "chunk_provider_server.provide_chunk",
            }.issubset(corrected)
        )

    def test_total_replacement_additions_cover_missing_domains(self) -> None:
        groups = {row["id"] for row in self.errata["supported_addition_groups"]}
        self.assertEqual(
            {
                "biome.registry",
                "biome.compatibility_taxonomy",
                "biome.virtual_surface",
                "biome_provider.supported_shape",
                "world_selection.spawn_policy",
                "world.runtime_potential_spawns",
            },
            groups,
        )
        unresolved = {
            row["id"]: row["state"] for row in self.errata["unsupported_or_unresolved"]
        }
        self.assertEqual(
            "unsupported-public-seam",
            unresolved["vanilla_structure_persistence_registration"],
        )
        self.assertEqual(
            "unresolved-authority",
            unresolved["generated_minecraft_artifact_lock"],
        )


if __name__ == "__main__":
    unittest.main()
