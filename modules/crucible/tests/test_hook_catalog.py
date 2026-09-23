from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
CANDIDATE = ROOT / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
BINDING_PATH = CANDIDATE / "candidate-lock-v1.json"
SPEC_PATH = CANDIDATE / "worldgen-hook-spec-v1.json"
CATALOG_PATH = CANDIDATE / "worldgen-hook-catalog-v1.json"
TOOL_PATH = ROOT / "modules/crucible/tools/generate_worldgen_hook_catalog.py"
OPTIONAL_SOURCE_ROOT = ROOT / ".workbench/cache/cleanroom-src"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "generate_worldgen_hook_catalog", TOOL_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load catalog generator: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorldgenHookCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()
        cls.binding = json.loads(BINDING_PATH.read_text(encoding="utf-8"))
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def test_candidate_lock_is_exact(self) -> None:
        binding = self.binding
        self.assertEqual(binding["minecraft"]["version"], "1.12.2")
        self.assertEqual(binding["cleanroom"]["version"], "0.6.8-alpha")
        self.assertEqual(binding["forge"]["version"], "14.23.5.2864")
        self.assertEqual(binding["mappings"]["mcp_version"], "9.42")
        self.assertEqual(binding["mappings"]["coordinate"], "stable_39")
        self.assertEqual(
            binding["cleanroom"]["source_revision"],
            "9946eb1f17a66a72d518e5e4a92d45c62c6d33fc",
        )
        self.assertEqual(
            binding["cleanroom"]["release"],
            {
                "sha256": (
                    "64e4d8af4f117224f7b69efb5e270f75b05feef7fd034fcce"
                    "185ae2e5b5ec9eb"
                ),
                "size": 58129,
                "url": (
                    "https://github.com/CleanroomMC/Cleanroom/releases/download/"
                    "0.6.8-alpha/cleanroom-0.6.8-alpha.zip"
                ),
            },
        )
        self.assertEqual(self.catalog["binding"], binding)
        self.assertEqual(
            self.catalog["candidate_lock_sha256"],
            hashlib.sha256(BINDING_PATH.read_bytes()).hexdigest(),
        )

    def test_required_buses_and_probe_boundaries_are_present(self) -> None:
        bus_ids = {row["id"] for row in self.catalog["buses"]}
        self.assertEqual(
            bus_ids,
            {
                "forge.event_bus",
                "forge.ore_gen_bus",
                "forge.terrain_gen_bus",
            },
        )
        hook_ids = {row["id"] for row in self.catalog["hooks"]}
        self.assertTrue(
            {
                "asm_event_handler.invoke",
                "biome_provider.init_layers",
                "biome_provider.override_surface",
                "biome_decorator.gen_decorations",
                "biome_decorator.generate_ores",
                "chunk.populate",
                "chunk.populate_owned",
                "chunk_data_event.load",
                "chunk_data_event.save",
                "chunk_event.load",
                "chunk_event.unload",
                "chunk.set_block_state",
                "chunk_primer.set_block_state",
                "chunk_provider_server.load_chunk",
                "chunk_provider_server.provide_chunk",
                "dimension_manager.register_dimension",
                "dimension_type.register",
                "event_bus.post",
                "game_registry.generate_world",
                "game_registry.register_world_generator",
                "i_chunk_generator.generate_chunk",
                "i_chunk_generator.generate_structures",
                "i_chunk_generator.get_nearest_structure_pos",
                "i_chunk_generator.get_possible_creatures",
                "i_chunk_generator.is_inside_structure",
                "i_chunk_generator.populate",
                "i_chunk_generator.recreate_structures",
                "i_world_generator.generate",
                "world.set_block_state",
                "world_event.create_spawn_position",
                "world_event.load",
                "world_event.save",
                "world_event.unload",
                "world_provider.can_coordinate_be_spawn",
                "world_provider.create_chunk_generator",
                "world_provider.init",
                "world_type.create",
                "world_type.get_biome_provider",
                "world_type.get_biome_layer",
                "world_type.get_chunk_generator",
            }.issubset(hook_ids)
        )

        population = next(
            row
            for row in self.catalog["call_orders"]
            if row["id"] == "chunk_population"
        )
        self.assertEqual(
            [step["related_hook"] for step in population["steps"]],
            [
                "chunk.populate",
                "chunk.populate_owned",
                "i_chunk_generator.populate",
                "game_registry.generate_world",
                "chunk.populate_owned",
            ],
        )

        maintenance = next(
            row
            for row in self.catalog["call_orders"]
            if row["id"] == "chunk_structure_maintenance"
        )
        self.assertEqual(
            [step["related_hook"] for step in maintenance["steps"]],
            [
                "chunk.populate_owned",
                "i_chunk_generator.generate_structures",
                "chunk.populate_owned",
            ],
        )

    def test_complete_replacement_and_biome_provider_contracts(self) -> None:
        hooks = {row["id"]: row for row in self.catalog["hooks"]}
        generator_methods = {
            row["probe_target"]
            for identity, row in hooks.items()
            if identity.startswith("i_chunk_generator.")
        }
        self.assertEqual(
            generator_methods,
            {
                "net.minecraft.world.gen.IChunkGenerator#generateChunk(int,int)",
                "net.minecraft.world.gen.IChunkGenerator#populate(int,int)",
                (
                    "net.minecraft.world.gen.IChunkGenerator#"
                    "generateStructures(Chunk,int,int)"
                ),
                (
                    "net.minecraft.world.gen.IChunkGenerator#"
                    "recreateStructures(Chunk,int,int)"
                ),
                (
                    "net.minecraft.world.gen.IChunkGenerator#"
                    "getPossibleCreatures(EnumCreatureType,BlockPos)"
                ),
                (
                    "net.minecraft.world.gen.IChunkGenerator#"
                    "getNearestStructurePos(World,String,BlockPos,boolean)"
                ),
                (
                    "net.minecraft.world.gen.IChunkGenerator#"
                    "isInsideStructure(World,String,BlockPos)"
                ),
            },
        )
        self.assertEqual(
            hooks["biome_provider.override_surface"]["methods"],
            [
                "getBiomesToSpawnIn()",
                "getBiome(BlockPos)",
                "getBiome(BlockPos,Biome)",
                "getTemperatureAtHeight(float,int)",
                "getBiomesForGeneration(Biome[],int,int,int,int)",
                "getBiomes(Biome[],int,int,int,int)",
                "getBiomes(Biome[],int,int,int,int,boolean)",
                "areBiomesViable(int,int,int,List<Biome>)",
                "findBiomePosition(int,int,int,List<Biome>,Random)",
                "cleanupCache()",
                "getModdedBiomeGenerators(WorldType,long,GenLayer[])",
                "isFixedBiome()",
                "getFixedBiome()",
            ],
        )
        self.assertEqual(
            "BiomeProvider()",
            hooks["biome_provider.override_surface"]["subclass_constructor"],
        )

    def test_automatic_and_opt_in_compatibility_are_explicit_and_generic(self) -> None:
        rows = self.catalog["hooks"] + self.catalog["terraingen_events"]
        delivery = {
            row["id"]: row["compatibility_delivery"]
            for row in rows
            if "compatibility_delivery" in row
        }
        self.assertEqual(
            "framework-automatic",
            delivery["game_registry.generate_world"],
        )
        self.assertEqual(
            "framework-automatic",
            delivery["i_world_generator.generate"],
        )
        self.assertEqual(
            "independent-registration",
            delivery["game_registry.register_world_generator"],
        )
        for identity in (
            "terraingen.decorate.pre",
            "terraingen.decorate.post",
            "terraingen.decorate.feature",
            "terraingen.ore.pre",
            "terraingen.ore.post",
            "terraingen.ore.generate_minable",
            "terraingen.populate.pre",
            "terraingen.populate.post",
            "terraingen.populate.feature",
        ):
            self.assertEqual("generator-opt-in", delivery[identity])

        rendered = CATALOG_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("recurrent complex", rendered)
        self.assertNotIn("recurrentcomplex", rendered)

    def test_supported_lifecycle_events_and_decorate_callsite_are_exact(self) -> None:
        hooks = {row["id"]: row for row in self.catalog["hooks"]}
        for identity in (
            "chunk_data_event.load",
            "chunk_data_event.save",
            "chunk_event.load",
            "chunk_event.unload",
            "world_event.create_spawn_position",
            "world_event.load",
            "world_event.save",
            "world_event.unload",
        ):
            self.assertEqual("forge.event_bus", hooks[identity]["bus"])
            self.assertEqual("supported-forge-event", hooks[identity]["stability"])

        events = {row["id"]: row for row in self.catalog["terraingen_events"]}
        decorate = events["terraingen.decorate.feature"]
        self.assertIn("ChunkPos,EventType", decorate["callsite"])
        self.assertNotIn("Random,BlockPos,EventType", decorate["callsite"])
        self.assertIn("null placementPos", decorate["semantics"])
        self.assertIn(
            "biome_decorator_patch",
            {source["ref"] for source in decorate["sources"]},
        )

    def test_known_terraingen_event_families_and_variants_are_complete(self) -> None:
        events = {row["id"]: row for row in self.catalog["terraingen_events"]}
        self.assertTrue(
            {
                "terraingen.biome.create_decorator",
                "terraingen.biome.get_foliage_color",
                "terraingen.biome.get_grass_color",
                "terraingen.biome.get_village_block_id",
                "terraingen.biome.get_water_color",
                "terraingen.chunk_generator.init_noise_field",
                "terraingen.chunk_generator.replace_biome_blocks",
                "terraingen.decorate.feature",
                "terraingen.decorate.post",
                "terraingen.decorate.pre",
                "terraingen.init_map_gen",
                "terraingen.init_noise_gens",
                "terraingen.ore.generate_minable",
                "terraingen.ore.post",
                "terraingen.ore.pre",
                "terraingen.populate.feature",
                "terraingen.populate.post",
                "terraingen.populate.pre",
                "terraingen.sapling_grow_tree",
                "terraingen.world_type.biome_size",
                "terraingen.world_type.init_biome_gens",
            }.issubset(events)
        )
        self.assertEqual(
            set(events["terraingen.init_noise_gens"]["context_types"]),
            {"ContextEnd", "ContextHell", "ContextOverworld"},
        )
        self.assertEqual(
            set(events["terraingen.init_map_gen"]["event_types"]),
            {
                "CAVE",
                "CUSTOM",
                "END_CITY",
                "MINESHAFT",
                "NETHER_BRIDGE",
                "NETHER_CAVE",
                "OCEAN_MONUMENT",
                "RAVINE",
                "SCATTERED_FEATURE",
                "STRONGHOLD",
                "VILLAGE",
                "WOODLAND_MANSION",
            },
        )
        self.assertEqual(
            events["terraingen.decorate.pre"]["bus"], "forge.event_bus"
        )
        self.assertEqual(
            events["terraingen.decorate.post"]["bus"], "forge.event_bus"
        )
        self.assertIn(
            "No in-tree caller",
            events["terraingen.world_type.biome_size"]["semantics"],
        )

    def test_every_probe_has_bound_or_explicitly_unresolved_sources(self) -> None:
        for group in ("buses", "hooks", "terraingen_events"):
            for row in self.catalog[group]:
                self.assertTrue(row["sources"], row["id"])
                for source in row["sources"]:
                    self.assertIn(source["status"], {"bound", "unresolved"})
                    if source["status"] == "bound":
                        self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
                        self.assertNotIn("reason", source)
                    else:
                        self.assertIsNone(source["sha256"])
                        self.assertTrue(source["reason"])

    def test_checked_in_catalog_regenerates_byte_identically(self) -> None:
        first = self.tool.render_catalog(
            self.tool.build_catalog(BINDING_PATH, SPEC_PATH)
        )
        second = self.tool.render_catalog(
            self.tool.build_catalog(BINDING_PATH, SPEC_PATH)
        )
        self.assertEqual(first, second)
        self.assertEqual(first, CATALOG_PATH.read_bytes())

    def test_optional_exact_cleanroom_source_checkout(self) -> None:
        if not OPTIONAL_SOURCE_ROOT.is_dir():
            self.skipTest(
                "optional .workbench/cache/cleanroom-src checkout is absent"
            )
        verified = self.tool.verify_source_root(
            OPTIONAL_SOURCE_ROOT,
            binding_path=BINDING_PATH,
            spec_path=SPEC_PATH,
        )
        self.assertGreater(len(verified), 0)
        self.assertIn("gradle_properties", verified)


if __name__ == "__main__":
    unittest.main()
