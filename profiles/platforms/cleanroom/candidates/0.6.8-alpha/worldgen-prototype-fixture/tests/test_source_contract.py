from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re
import unittest


FIXTURE = Path(__file__).resolve().parents[1]
JAVA = FIXTURE / "src" / "main" / "java" / "dev" / "workbench" / "worldgenprototype"
GENERATOR = JAVA / "world" / "PrototypeChunkGenerator.java"
FEATURE = JAVA / "world" / "PrototypeFeature.java"
WORLD_TYPE = JAVA / "world" / "PrototypeWorldType.java"
PROVIDER = JAVA / "world" / "WorldStudioBiomeProvider.java"
TERRAIN = JAVA / "world" / "WorldStudioTerrain.java"
PLAN = JAVA / "world" / "plan" / "WorldStudioPlan.java"
DIAGNOSTICS = JAVA / "diagnostics" / "PrototypeDiagnostics.java"
CAUSAL_TRACE = JAVA / "diagnostics" / "WorldgenCausalTrace.java"
JFR_EVENTS = JAVA / "diagnostics" / "WorldgenJfrEvents.java"
WATERSHED = JAVA / "world" / "hydrology" / "WatershedEngine.java"
GEN_LAYER = JAVA / "world" / "WorldStudioGenLayer.java"
GROOVY_PLUGIN = JAVA / "compat" / "groovy" / "WorldStudioGroovyPlugin.java"
GROOVY_EXAMPLE = (
    FIXTURE / "examples" / "groovy" / "postInit" / "world_studio_mega_regions.groovy"
)
PATTERN = FIXTURE.parent / "worldgen-prototype-pattern-v1.json"


class PrototypeSourceContractTests(unittest.TestCase):
    def test_exact_candidate_and_slice_version_are_pinned(self):
        properties = (FIXTURE / "gradle.properties").read_text(encoding="utf-8")
        self.assertIn("minecraft_version=1.12.2", properties)
        self.assertIn("mcp_version=39-1.12", properties)
        self.assertIn("cleanroom_version=0.6.8-alpha", properties)
        self.assertIn("fixture_version=0.4.0", properties)

    def test_subject_has_no_observer_or_external_structure_integration(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(JAVA.rglob("*.java"))
        )
        for forbidden in (
            "worldgenobservatory",
            "syntheticworldgen",
            "recurrentcomplex",
            "Recurrent Complex",
            "GameRegistry",
            "IWorldGenerator",
            "java.lang.reflect",
        ):
            self.assertNotIn(forbidden, sources)

    def test_generator_implements_all_seven_interface_methods(self):
        source = GENERATOR.read_text(encoding="utf-8")
        for method in (
            "generateChunk",
            "populate",
            "generateStructures",
            "getPossibleCreatures",
            "getNearestStructurePos",
            "isInsideStructure",
            "recreateStructures",
        ):
            self.assertRegex(source, rf"public\s+[^\n]+\s+{method}\s*\(")

    def test_declared_edit_stages_are_closed_and_stable(self):
        source = FEATURE.read_text(encoding="utf-8")
        ids = re.findall(r'\("([a-z.]+)"\)', source)
        self.assertEqual(
            ["generate.base", "populate.custom", "decorate.custom"],
            ids,
        )
        self.assertIn("Stage.POPULATE_CUSTOM", source)

    def test_boulder_causal_v2_trace_is_opt_in_and_does_not_consume_rng(self):
        feature = FEATURE.read_text(encoding="utf-8")
        trace = CAUSAL_TRACE.read_text(encoding="utf-8")

        self.assertIn("workbench-world-studio-causal-trace-v2", trace)
        self.assertIn("workbench.worldgen.execution_envelope_id", trace)
        for property_name in (
            "workbench.worldgen.causal.min_chunk_x",
            "workbench.worldgen.causal.min_chunk_z",
            "workbench.worldgen.causal.max_chunk_x_exclusive",
            "workbench.worldgen.causal.max_chunk_z_exclusive",
        ):
            self.assertIn(property_name, trace)
        self.assertIn("return ENVELOPE_ID != null", trace)
        self.assertIn("chunkX >= MIN_CHUNK_X", trace)
        self.assertIn("chunkZ < MAX_CHUNK_Z", trace)
        self.assertIn("WORLDGEN_PROTOTYPE_CAUSAL_V2", trace)
        self.assertIn(r'\"rule_id\":\"world-studio.boulder.v1\"', trace)
        self.assertIn(r'\"rule_id\":\"forge.populate.custom\"', trace)
        self.assertIn(r'\"record_type\":\"gate-decision\"', trace)
        self.assertIn(r'\"record_type\":\"feature-decision\"', trace)
        self.assertIn(r'\"stage_id\":\"populate.custom\"', trace)
        self.assertIn(r'\"rng_algorithm\":\"java.util.Random.v1\"', trace)
        self.assertIn(r'\"seed_derivation_id\":\"world-studio.stage-random.v1\"', trace)
        self.assertIn("0x6a09e667f3bcc909L", trace)
        self.assertIn(r'\"eligibility_accepted\":false', trace)
        self.assertIn(r'\"writes\":[]', trace)
        for role in ("mandatory-base", "mandatory-east", "optional-top"):
            self.assertIn(f'"{role}"', feature)

        place = feature[feature.index("private static boolean placeBoulder"):]
        self.assertEqual(1, place.count("random.nextInt(10)"))
        self.assertEqual(2, place.count("random.nextInt(8)"))
        self.assertEqual(1, place.count("random.nextBoolean()"))
        self.assertLess(place.index("int eligibilityDraw"), place.index("if (eligibilityDraw != 0)"))
        self.assertLess(place.index("boolean optionalTopDraw"), place.index("if (optionalTopDraw)"))
        self.assertIn("WorldgenCausalTrace.rejected", place)
        self.assertIn("WorldgenCausalTrace.placed", place)

        generator = GENERATOR.read_text(encoding="utf-8")
        gate = generator.index("WorldgenCausalTrace.populationGate")
        self.assertLess(generator.index("TerrainGen.populate(", generator.index("populate.custom")), gate)
        self.assertLess(gate, generator.index("if (populationAllowed)", gate))

    def test_forge_lifecycle_delegates_native_biome_decoration_once(self):
        source = GENERATOR.read_text(encoding="utf-8")
        populate = source.index("public void populate(int chunkX, int chunkZ)")
        pre_event = source.index("ForgeEventFactory.onChunkPopulate(", populate)
        post_event = source.index("ForgeEventFactory.onChunkPopulate(", pre_event + 1)
        offsets = [
            pre_event,
            source.index("PopulateChunkEvent.Populate.EventType.CUSTOM", populate),
            source.index("biome.decorate(world, eventRandom, origin)", populate),
            source.index("PopulateChunkEvent.Populate.EventType.ANIMALS", populate),
            source.index("PopulateChunkEvent.Populate.EventType.ICE", populate),
            post_event,
        ]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(1, source.count("biome.decorate(world, eventRandom, origin)"))
        self.assertNotIn("new DecorateBiomeEvent.Pre", source)
        self.assertNotIn("GameRegistry.generateWorld", source)

    def test_carvers_are_selected_through_forge_and_run_after_surface(self):
        source = GENERATOR.read_text(encoding="utf-8")
        self.assertIn("TerrainGen.getModdedMapGen(new MapGenCaves(), CAVE)", source)
        self.assertIn("TerrainGen.getModdedMapGen(new MapGenRavine(), RAVINE)", source)
        surface = source.index("genTerrainBlocks(")
        caves = source.index("caveGenerator.generate(")
        ravines = source.index("ravineGenerator.generate(")
        self.assertLess(surface, caves)
        self.assertLess(caves, ravines)

    def test_post_events_are_not_fabricated_by_cleanup_finally(self):
        source = GENERATOR.read_text(encoding="utf-8")
        failure = source.index('PrototypeDiagnostics.failed(world, "populate"')
        finally_start = source.index("} finally {", failure)
        finally_end = source.index("\n        }", finally_start)
        finally_body = source[finally_start:finally_end]
        self.assertIn("BlockFalling.fallInstantly = previousInstantFalling", finally_body)
        self.assertNotIn("onChunkPopulate", finally_body)

    def test_world_type_owns_a_complete_world_studio_provider(self):
        world_type = WORLD_TYPE.read_text(encoding="utf-8")
        match = re.search(r'WORLD_TYPE_NAME = "([^"]+)"', world_type)
        self.assertIsNotNone(match)
        self.assertLessEqual(len(match.group(1)), 16)
        self.assertIn("new WorldStudioBiomeProvider(world)", world_type)
        self.assertIn("new PrototypeChunkGenerator(world)", world_type)

        provider = PROVIDER.read_text(encoding="utf-8")
        for method in (
            "getBiomesToSpawnIn",
            "getBiome",
            "getBiomesForGeneration",
            "getBiomes",
            "areBiomesViable",
            "findBiomePosition",
            "cleanupCache",
            "isFixedBiome",
        ):
            self.assertIn(method + "(", provider)
        self.assertIn("getModdedBiomeGenerators(", provider)

    def test_plan_places_macro_lithology_above_biome_palette(self):
        plan = PLAN.read_text(encoding="utf-8")
        terrain = TERRAIN.read_text(encoding="utf-8")
        for field in (
            "continentalness",
            "temperature",
            "moisture",
            "relief",
            "surface",
        ):
            self.assertIn(f'"{field}"', plan)
        for region in ("stable_craton", "sedimentary_basin", "orogenic_belt"):
            self.assertIn(f'"{region}"', plan)
        self.assertIn('"biomesoplenty:bayou"', plan)
        self.assertIn('"biomesoplenty:alps"', plan)
        self.assertIn("selectRegion(blockX, blockZ, output)", terrain)
        self.assertIn("lithologyState", terrain)
        self.assertIn("riverStrength", terrain)
        self.assertIn("streamStrength", terrain)
        self.assertIn("LithologyDefinition", plan)
        self.assertIn("WatershedDefinition", plan)
        self.assertIn("watershedGrid", plan)
        self.assertIn("watershedRunoff", plan)
        self.assertIn("watershedChannels", plan)

    def test_groovy_is_configuration_time_and_publishes_immutable_plan(self):
        plugin = GROOVY_PLUGIN.read_text(encoding="utf-8")
        example = GROOVY_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("implements GroovyPlugin", plugin)
        self.assertIn('"worldStudio"', plugin)
        self.assertIn("mods.worldStudio", example)
        self.assertIn("studio.publish()", example)
        self.assertNotIn("groovy.lang.Closure", TERRAIN.read_text(encoding="utf-8"))
        self.assertNotIn("groovy.lang.Closure", GENERATOR.read_text(encoding="utf-8"))

    def test_diagnostics_explain_primitive_fields_without_hot_path_arrays(self):
        diagnostics = DIAGNOSTICS.read_text(encoding="utf-8")
        terrain = TERRAIN.read_text(encoding="utf-8")
        for field in (
            "base_surface",
            "river_distance",
            "stream_distance",
            "continentalness_octaves",
            "temperature_octaves",
            "moisture_octaves",
            "relief_octaves",
            "watershed_discharge",
            "watershed_fill_depth",
            "watershed_stream_order",
            "watershed_flow_direction",
            "watershed_fingerprint",
        ):
            self.assertIn(f'\\"{field}\\"', diagnostics)
        sample_body = terrain.split(
            "private void sampleInto(int blockX, int blockZ, MutableSample output)", 1
        )[1].split("private Biome resolveBiome", 1)[0]
        self.assertNotIn("octaveContributions", sample_body)

    def test_chunk_hot_path_uses_one_compact_shared_sample(self):
        terrain = TERRAIN.read_text(encoding="utf-8")
        generator = GENERATOR.read_text(encoding="utf-8")
        provider = PROVIDER.read_text(encoding="utf-8")
        gen_layer = GEN_LAYER.read_text(encoding="utf-8")

        self.assertIn("public static final class ChunkSample", terrain)
        for primitive_array in (
            "short[] surfaceHeights",
            "short[] waterSurfaceHeights",
            "double[] surfaceNoise",
            "float[] watershedDischarge",
            "float[] watershedFillDepth",
            "byte[] flags",
            "int[] regionIndexes",
        ):
            self.assertIn(primitive_array, terrain)
        self.assertIn("session.sampleChunk(chunkX, chunkZ)", generator)
        self.assertNotIn("TerrainSample[][]", generator)
        self.assertNotIn("new RegionCell", terrain)
        self.assertNotIn("class RegionSelection", terrain)
        self.assertIn("RegionNeighborhood", terrain)
        self.assertIn("WorldStudioTerrain terrain()", provider)
        self.assertIn("((WorldStudioBiomeProvider) provider).terrain()", generator)
        self.assertIn("chunkFastPath", gen_layer)
        self.assertIn("copyBiomeIds(output)", gen_layer)

    def test_sampling_caches_are_bounded_and_plan_scoped(self):
        terrain = TERRAIN.read_text(encoding="utf-8")
        self.assertIn("DEFAULT_CHUNK_CACHE_CAPACITY", terrain)
        self.assertIn("MAX_CHUNK_CACHE_CAPACITY", terrain)
        self.assertIn("DEFAULT_BIOME_POINT_CACHE_CAPACITY", terrain)
        self.assertIn("MAX_BIOME_POINT_CACHE_CAPACITY", terrain)
        self.assertIn("class BiomePointCache", terrain)
        self.assertIn("removeEldestEntry", terrain)
        self.assertIn("snapshot.version()", terrain)
        self.assertIn("cleanupCache()", terrain)
        self.assertIn("Long2ObjectLinkedOpenHashMap", WATERSHED.read_text(encoding="utf-8"))

    def test_watershed_is_haloed_deterministic_and_chunk_batched(self):
        watershed = WATERSHED.read_text(encoding="utf-8")
        terrain = TERRAIN.read_text(encoding="utf-8")
        generator = GENERATOR.read_text(encoding="utf-8")
        for construct in (
            "record Configuration",
            "PrimitiveCellHeap",
            "Priority-Flood",
            "D8",
            "resolveD8Receivers",
            "tileSizeCells",
            "haloCells",
            "streamOrder",
            "fingerprint()",
            "resolvedTiles",
        ):
            self.assertIn(construct, watershed)
        self.assertIn("watershed.sampleChunk(chunkX, chunkZ)", terrain)
        self.assertIn("hydrology.sample(localX, localZ, output.hydrology)", terrain)
        self.assertIn("samples.watershedFingerprint()", generator)
        for forbidden in ("newVirtualThread", "ParallelJob", "CompletableFuture"):
            self.assertNotIn(forbidden, watershed)

    def test_jfr_events_cover_sampling_provider_and_chunk_stages(self):
        events = JFR_EVENTS.read_text(encoding="utf-8")
        generator = GENERATOR.read_text(encoding="utf-8")
        gen_layer = GEN_LAYER.read_text(encoding="utf-8")
        for event_name in (
            "dev.workbench.worldgen.ChunkSample",
            "dev.workbench.worldgen.ChunkStage",
            "dev.workbench.worldgen.BiomeArea",
            "dev.workbench.worldgen.WatershedTile",
        ):
            self.assertIn(event_name, events)
        self.assertIn("workbench.worldgen.jfr.biome_points", events)
        for stage in (
            "sample.chunk",
            "primer.base",
            "surface.biome",
            "carver.cave",
            "decorate.biome",
            "lighting.skylight_map",
        ):
            self.assertIn(f'"{stage}"', generator)
        self.assertIn("beginBiomeArea", gen_layer)

    def test_build_does_not_claim_to_reconstruct_production_mod_transforms(self):
        build = (FIXTURE / "build.gradle").read_text(encoding="utf-8")
        self.assertNotIn("workbenchRuntimeModJars", build)
        self.assertNotIn("runtimeAccessTransformers", build)
        self.assertIn("compileOnly('com.cleanroommc:groovyscript:1.4.3')", build)
        self.assertIn("compileOnly('it.unimi.dsi:fastutil:8.5.18')", build)

    def test_initial_pattern_snapshot_remains_self_identifying(self):
        pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
        claimed = pattern.pop("pattern_id")
        canonical = json.dumps(
            pattern,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(
            "cleanroom-worldgen-prototype-pattern:sha256:"
            + hashlib.sha256(canonical).hexdigest(),
            claimed,
        )


if __name__ == "__main__":
    unittest.main()
