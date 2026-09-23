package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.personthecat.cavegenerator.Main;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.Hashing;
import dev.workbench.crucible.runtimegraph.worldgen.RealizedWorldObservationTrace;

import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.init.Blocks;
import net.minecraft.util.ResourceLocation;
import net.minecraft.world.DimensionType;
import net.minecraft.world.WorldServer;
import net.minecraft.world.biome.Biome;
import net.minecraft.world.chunk.Chunk;
import net.minecraft.world.chunk.storage.ExtendedBlockStorage;
import net.minecraftforge.common.DimensionManager;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * Final chunk state plus naturally emitted world-generation stage custody.
 *
 * <p>Definitions, placements, block states, and fluid/block registry joins stay
 * independently typed. In particular, a realized block is never promoted to
 * an item, material, or fluid merely because of its namespace.</p>
 */
public final class RealizedWorldObservationAdapter implements CaptureAdapter {
    private static final String FINAL_STAGE = "post-start-end-tick-final-state";

    @Override public String adapterId() { return "realized-world-observations"; }
    @Override public String categoryId() { return "realized-world-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        RealizedWorldObservationTrace.Snapshot trace =
            RealizedWorldObservationTrace.global().snapshot();
        List<JsonObject> records = new ArrayList<JsonObject>();
        List<String> diagnostics = new ArrayList<String>();

        Map<String, Integer> stageCounts = captureWorldStages(trace, records);
        RecurrentCounts recurrentCounts = captureRecurrentComplex(trace, records);
        int caveStageCount = captureCaveStages(trace, records);
        WorldCounts worldCounts = captureWorlds(records);

        if (worldCounts.worldCount == 0) diagnostics.add("no-server-world-observed");
        if (worldCounts.chunkCount == 0) diagnostics.add("no-loaded-chunk-observed");
        if (caveStageCount == 0) diagnostics.add("no-cave-generator-stage-observed");
        if (countStage(stageCounts, "population-post") + caveStageCount == 0) {
            diagnostics.add("no-completed-generation-stage-observed");
        }
        if (recurrentCounts.postWithoutPreCount > 0) {
            diagnostics.add("recurrent-complex-post-without-pre");
        }

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "realized-world-observation-authority");
        authority.addProperty("observer_registered_before_world_load", true);
        authority.addProperty("generation_invoked_by_observer", false);
        authority.addProperty("world_count", worldCounts.worldCount);
        authority.addProperty("loaded_chunk_count", worldCounts.chunkCount);
        authority.addProperty("biome_assignment_count", worldCounts.biomeAssignmentCount);
        authority.addProperty("block_state_occurrence_count", worldCounts.blockStateCount);
        authority.addProperty("fluid_block_occurrence_count", worldCounts.fluidBlockCount);
        authority.addProperty("world_stage_observation_count", sum(stageCounts));
        authority.addProperty(
            "completed_population_observation_count",
            countStage(stageCounts, "population-post")
        );
        authority.addProperty(
            "completed_generation_stage_observation_count",
            countStage(stageCounts, "population-post") + caveStageCount
        );
        authority.addProperty(
            "recurrent_complex_placement_attempt_count",
            recurrentCounts.preCount
        );
        authority.addProperty(
            "recurrent_complex_completed_placement_count",
            recurrentCounts.postCount
        );
        authority.addProperty(
            "recurrent_complex_incomplete_placement_count",
            recurrentCounts.incompleteCount
        );
        authority.addProperty("cave_generator_stage_observation_count", caveStageCount);
        authority.addProperty("event_loss_detected", recurrentCounts.postWithoutPreCount > 0);
        authority.addProperty("final_state_stage", FINAL_STAGE);
        authority.addProperty("item_stack_occurrence_count", 0);
        authority.addProperty(
            "item_absence_reason",
            "block-state-observation-does-not-imply-item-stack-occurrence"
        );
        records.add(authority);

        return new AdapterSnapshot(records, diagnostics, 0);
    }

    private static Map<String, Integer> captureWorldStages(
        RealizedWorldObservationTrace.Snapshot trace,
        List<JsonObject> records
    ) {
        CountedRows rows = new CountedRows("observation_count");
        Map<String, Integer> counts = new LinkedHashMap<String, Integer>();
        for (RealizedWorldObservationTrace.WorldStageEvent event : trace.worldStages) {
            JsonObject row = worldScope(
                "realized-world-stage-observation",
                event.worldSeed,
                event.dimensionId
            );
            row.addProperty("chunk_x", event.chunkX);
            row.addProperty("chunk_z", event.chunkZ);
            row.addProperty("generation_stage", event.stage);
            row.addProperty(
                "chunk_coordinates_applicable",
                !"world-load".equals(event.stage)
            );
            rows.add(row);
            Integer current = counts.get(event.stage);
            counts.put(event.stage, Integer.valueOf(current == null ? 1 : current + 1));
        }
        records.addAll(rows.finish());
        return counts;
    }

    private static RecurrentCounts captureRecurrentComplex(
        RealizedWorldObservationTrace.Snapshot trace,
        List<JsonObject> records
    ) {
        Map<String, PlacementAggregate> aggregates =
            new TreeMap<String, PlacementAggregate>();
        int preCount = 0;
        int postCount = 0;
        for (RealizedWorldObservationTrace.RecurrentComplexEvent event
            : trace.recurrentComplex) {
            JsonObject identity = worldScope(
                "recurrent-complex-placement-observation",
                event.worldSeed,
                event.dimensionId
            );
            identity.addProperty("structure_id", event.structureId);
            identity.addProperty("min_x", event.minX);
            identity.addProperty("min_y", event.minY);
            identity.addProperty("min_z", event.minZ);
            identity.addProperty("max_x", event.maxX);
            identity.addProperty("max_y", event.maxY);
            identity.addProperty("max_z", event.maxZ);
            identity.addProperty("min_chunk_x", Math.floorDiv(event.minX, 16));
            identity.addProperty("min_chunk_z", Math.floorDiv(event.minZ, 16));
            identity.addProperty("max_chunk_x", Math.floorDiv(event.maxX, 16));
            identity.addProperty("max_chunk_z", Math.floorDiv(event.maxZ, 16));
            identity.addProperty("generation_layer", event.generationLayer);
            identity.addProperty("first_time", event.firstTime);
            String key = canonicalKey(identity);
            PlacementAggregate aggregate = aggregates.get(key);
            if (aggregate == null) {
                aggregate = new PlacementAggregate(identity);
                aggregates.put(key, aggregate);
            }
            if ("placement-pre".equals(event.stage)) {
                aggregate.preCount++;
                preCount++;
            } else if ("placement-post".equals(event.stage)) {
                aggregate.postCount++;
                postCount++;
            } else {
                throw new IllegalStateException(
                    "unknown Recurrent Complex event stage " + event.stage
                );
            }
        }
        int incomplete = 0;
        int postWithoutPre = 0;
        for (PlacementAggregate aggregate : aggregates.values()) {
            JsonObject row = aggregate.identity.deepCopy();
            row.addProperty("placement_pre_count", aggregate.preCount);
            row.addProperty("placement_post_count", aggregate.postCount);
            row.addProperty(
                "completed_placement_count",
                Math.min(aggregate.preCount, aggregate.postCount)
            );
            row.addProperty(
                "incomplete_placement_count",
                Math.max(0, aggregate.preCount - aggregate.postCount)
            );
            row.addProperty("generation_stage", "recurrent-complex-placement");
            row.addProperty("definition_link_observed", true);
            incomplete += Math.max(0, aggregate.preCount - aggregate.postCount);
            postWithoutPre += Math.max(0, aggregate.postCount - aggregate.preCount);
            records.add(row);
        }
        return new RecurrentCounts(preCount, postCount, incomplete, postWithoutPre);
    }

    private static int captureCaveStages(
        RealizedWorldObservationTrace.Snapshot trace,
        List<JsonObject> records
    ) {
        CountedRows rows = new CountedRows("completed_invocation_count");
        Set<String> observedControllers = new LinkedHashSet<String>();
        for (RealizedWorldObservationTrace.CaveStageEvent event : trace.caveStages) {
            JsonObject row = worldScope(
                "cave-generator-stage-observation",
                event.worldSeed,
                event.dimensionId
            );
            row.addProperty("chunk_x", event.chunkX);
            row.addProperty("chunk_z", event.chunkZ);
            row.addProperty("generation_stage", event.stage);
            row.addProperty("controller_id", event.controllerId);
            row.addProperty("definition_link_observed", true);
            rows.add(row);
            observedControllers.add(event.controllerId);
        }
        if (Main.instance == null) {
            throw new IllegalStateException("Cave Generator instance unavailable");
        }
        Set<String> materialized = new LinkedHashSet<String>(Main.instance.generators.keySet());
        if (!observedControllers.equals(materialized)) {
            throw new IllegalStateException(
                "Cave Generator stage/controller custody differs from materialized controllers"
            );
        }
        List<JsonObject> finished = rows.finish();
        records.addAll(finished);
        int count = 0;
        for (JsonObject row : finished) {
            count += row.get("completed_invocation_count").getAsInt();
        }
        return count;
    }

    private static WorldCounts captureWorlds(List<JsonObject> records) {
        WorldServer[] values = DimensionManager.getWorlds();
        List<WorldServer> worlds = new ArrayList<WorldServer>(Arrays.asList(values));
        Collections.sort(worlds, new Comparator<WorldServer>() {
            @Override
            public int compare(WorldServer left, WorldServer right) {
                return Integer.compare(
                    left.provider.getDimension(),
                    right.provider.getDimension()
                );
            }
        });
        Set<Integer> dimensions = new LinkedHashSet<Integer>();
        int chunkCount = 0;
        int biomeAssignments = 0;
        int blockStates = 0;
        int fluidBlocks = 0;
        for (WorldServer world : worlds) {
            int dimension = world.provider.getDimension();
            if (!dimensions.add(Integer.valueOf(dimension))) {
                throw new IllegalStateException("duplicate loaded server dimension " + dimension);
            }
            String worldId = worldId(world);
            DimensionType dimensionType = world.provider.getDimensionType();
            JsonObject worldRow = worldScope(
                "realized-world-identity",
                world.getSeed(),
                dimension
            );
            worldRow.addProperty("world_id", worldId);
            worldRow.addProperty("level_name", world.getWorldInfo().getWorldName());
            worldRow.addProperty("dimension_type_id", dimensionType.getId());
            worldRow.addProperty("dimension_type_name", dimensionType.getName());
            worldRow.addProperty("provider_class", world.provider.getClass().getName());
            worldRow.addProperty(
                "chunk_generator_class",
                world.getChunkProvider().chunkGenerator.getClass().getName()
            );
            worldRow.addProperty("map_features_enabled", world.getWorldInfo().isMapFeaturesEnabled());
            worldRow.addProperty("spawn_x", world.getSpawnPoint().getX());
            worldRow.addProperty("spawn_y", world.getSpawnPoint().getY());
            worldRow.addProperty("spawn_z", world.getSpawnPoint().getZ());
            worldRow.addProperty("generation_stage", FINAL_STAGE);
            records.add(worldRow);

            Collection<Chunk> loaded = world.getChunkProvider().getLoadedChunks();
            List<Chunk> chunks = new ArrayList<Chunk>(loaded);
            Collections.sort(chunks, new Comparator<Chunk>() {
                @Override
                public int compare(Chunk left, Chunk right) {
                    int x = Integer.compare(left.x, right.x);
                    return x != 0 ? x : Integer.compare(left.z, right.z);
                }
            });
            Set<String> chunkKeys = new LinkedHashSet<String>();
            for (Chunk chunk : chunks) {
                String chunkKey = chunk.x + ":" + chunk.z;
                if (!chunkKeys.add(chunkKey)) {
                    throw new IllegalStateException("duplicate loaded chunk " + chunkKey);
                }
                JsonObject chunkRow = worldChunk(
                    "realized-world-chunk-observation",
                    world,
                    worldId,
                    chunk
                );
                chunkRow.addProperty("terrain_populated", chunk.isTerrainPopulated());
                chunkRow.addProperty("light_populated", chunk.isLightPopulated());
                chunkRow.addProperty("inhabited_time", chunk.getInhabitedTime());
                chunkRow.addProperty("biome_array_sha256", Hashing.sha256(chunk.getBiomeArray()));
                records.add(chunkRow);
                biomeAssignments += captureBiomes(world, worldId, chunk, records);
                StateCounts stateCounts = captureBlockStates(world, worldId, chunk, records);
                blockStates += stateCounts.blockStateRows;
                fluidBlocks += stateCounts.fluidBlockRows;
                chunkCount++;
            }
        }
        return new WorldCounts(
            worlds.size(), chunkCount, biomeAssignments, blockStates, fluidBlocks
        );
    }

    private static int captureBiomes(
        WorldServer world,
        String worldId,
        Chunk chunk,
        List<JsonObject> records
    ) {
        Map<Integer, Integer> counts = new TreeMap<Integer, Integer>();
        for (byte raw : chunk.getBiomeArray()) {
            int id = raw & 0xff;
            Integer current = counts.get(Integer.valueOf(id));
            counts.put(Integer.valueOf(id), Integer.valueOf(current == null ? 1 : current + 1));
        }
        for (Map.Entry<Integer, Integer> entry : counts.entrySet()) {
            int biomeId = entry.getKey().intValue();
            Biome biome = Biome.getBiome(biomeId);
            JsonObject row = worldChunk(
                "realized-world-biome-assignment",
                world,
                worldId,
                chunk
            );
            row.addProperty("biome_numeric_id", biomeId);
            row.addProperty("column_count", entry.getValue().intValue());
            row.addProperty("chunk_column_count", 256);
            row.addProperty("biome_resolved", biome != null);
            if (biome != null) {
                ResourceLocation id = Biome.REGISTRY.getNameForObject(biome);
                if (id == null) {
                    throw new IllegalStateException("loaded biome lacks registry identity");
                }
                row.addProperty("biome_id", id.toString());
                row.addProperty("biomes_o_plenty_definition_link", "biomesoplenty".equals(id.getNamespace()));
            }
            records.add(row);
        }
        return counts.size();
    }

    private static StateCounts captureBlockStates(
        WorldServer world,
        String worldId,
        Chunk chunk,
        List<JsonObject> records
    ) {
        Map<String, BlockStateCount> counts = new TreeMap<String, BlockStateCount>();
        ExtendedBlockStorage[] sections = chunk.getBlockStorageArray();
        for (int sectionIndex = 0; sectionIndex < sections.length; sectionIndex++) {
            ExtendedBlockStorage section = sections[sectionIndex];
            if (section == Chunk.NULL_BLOCK_STORAGE || section == null) {
                addState(counts, Blocks.AIR.getDefaultState(), 4096);
                continue;
            }
            for (int y = 0; y < 16; y++) {
                for (int z = 0; z < 16; z++) {
                    for (int x = 0; x < 16; x++) {
                        addState(counts, section.get(x, y, z), 1);
                    }
                }
            }
        }
        int total = 0;
        int fluidRows = 0;
        for (BlockStateCount state : counts.values()) {
            JsonObject row = worldChunk(
                "realized-world-block-state-occurrence",
                world,
                worldId,
                chunk
            );
            row.addProperty("block_id", state.blockId);
            row.addProperty("block_metadata", state.metadata);
            row.addProperty("block_state", state.stateText);
            row.addProperty("block_count", state.count);
            row.addProperty("item_stack_implied", false);
            row.addProperty("material_relation_implied", false);
            records.add(row);
            total += state.count;

            Fluid fluid = FluidRegistry.lookupFluidForBlock(state.block);
            if (fluid != null) {
                JsonObject fluidRow = worldChunk(
                    "realized-world-fluid-block-occurrence",
                    world,
                    worldId,
                    chunk
                );
                fluidRow.addProperty("block_id", state.blockId);
                fluidRow.addProperty("block_metadata", state.metadata);
                fluidRow.addProperty("block_state", state.stateText);
                fluidRow.addProperty("block_count", state.count);
                fluidRow.addProperty("fluid_id", fluid.getName());
                fluidRow.addProperty("relation_basis", "forge-fluid-block-registry-lookup");
                records.add(fluidRow);
                fluidRows++;
            }
        }
        if (total != 16 * 16 * 256) {
            throw new IllegalStateException("chunk block-state count is not exhaustive");
        }
        return new StateCounts(counts.size(), fluidRows);
    }

    private static void addState(
        Map<String, BlockStateCount> counts,
        IBlockState state,
        int amount
    ) {
        if (state == null) throw new IllegalStateException("chunk contains null block state");
        Block block = state.getBlock();
        ResourceLocation id = Block.REGISTRY.getNameForObject(block);
        if (id == null) throw new IllegalStateException("chunk block lacks registry identity");
        int metadata = block.getMetaFromState(state);
        String stateText = state.toString();
        String key = id.toString() + "\u001f" + metadata + "\u001f" + stateText;
        BlockStateCount current = counts.get(key);
        if (current == null) {
            current = new BlockStateCount(block, id.toString(), metadata, stateText);
            counts.put(key, current);
        }
        current.count += amount;
    }

    private static JsonObject worldScope(
        String recordType,
        long worldSeed,
        int dimensionId
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", recordType);
        row.addProperty("world_seed", worldSeed);
        row.addProperty("dimension_id", dimensionId);
        return row;
    }

    private static JsonObject worldChunk(
        String recordType,
        WorldServer world,
        String worldId,
        Chunk chunk
    ) {
        JsonObject row = worldScope(recordType, world.getSeed(), world.provider.getDimension());
        row.addProperty("world_id", worldId);
        row.addProperty("chunk_x", chunk.x);
        row.addProperty("chunk_z", chunk.z);
        row.addProperty("generation_stage", FINAL_STAGE);
        return row;
    }

    private static String worldId(WorldServer world) {
        JsonObject identity = new JsonObject();
        identity.addProperty("level_name", world.getWorldInfo().getWorldName());
        identity.addProperty("world_seed", world.getSeed());
        identity.addProperty("dimension_id", world.provider.getDimension());
        identity.addProperty("dimension_type", world.provider.getDimensionType().getName());
        return "runtime-world:sha256:" + CanonicalJson.sha256(identity);
    }

    private static String canonicalKey(JsonObject value) {
        return new String(CanonicalJson.bytes(value), StandardCharsets.UTF_8);
    }

    private static int countStage(Map<String, Integer> counts, String stage) {
        Integer value = counts.get(stage);
        return value == null ? 0 : value.intValue();
    }

    private static int sum(Map<String, Integer> counts) {
        int result = 0;
        for (Integer value : counts.values()) result += value.intValue();
        return result;
    }

    private static final class CountedRows {
        private final String countProperty;
        private final Map<String, CountedRow> values = new TreeMap<String, CountedRow>();

        private CountedRows(String countProperty) { this.countProperty = countProperty; }

        private void add(JsonObject row) {
            String key = canonicalKey(row);
            CountedRow current = values.get(key);
            if (current == null) {
                current = new CountedRow(row);
                values.put(key, current);
            }
            current.count++;
        }

        private List<JsonObject> finish() {
            List<JsonObject> result = new ArrayList<JsonObject>();
            for (CountedRow value : values.values()) {
                JsonObject row = value.row.deepCopy();
                row.addProperty(countProperty, value.count);
                result.add(row);
            }
            return result;
        }
    }

    private static final class CountedRow {
        private final JsonObject row;
        private int count;
        private CountedRow(JsonObject row) { this.row = row.deepCopy(); }
    }

    private static final class PlacementAggregate {
        private final JsonObject identity;
        private int preCount;
        private int postCount;
        private PlacementAggregate(JsonObject identity) { this.identity = identity.deepCopy(); }
    }

    private static final class RecurrentCounts {
        private final int preCount;
        private final int postCount;
        private final int incompleteCount;
        private final int postWithoutPreCount;
        private RecurrentCounts(int preCount, int postCount, int incompleteCount, int postWithoutPreCount) {
            this.preCount = preCount;
            this.postCount = postCount;
            this.incompleteCount = incompleteCount;
            this.postWithoutPreCount = postWithoutPreCount;
        }
    }

    private static final class WorldCounts {
        private final int worldCount;
        private final int chunkCount;
        private final int biomeAssignmentCount;
        private final int blockStateCount;
        private final int fluidBlockCount;
        private WorldCounts(
            int worldCount,
            int chunkCount,
            int biomeAssignmentCount,
            int blockStateCount,
            int fluidBlockCount
        ) {
            this.worldCount = worldCount;
            this.chunkCount = chunkCount;
            this.biomeAssignmentCount = biomeAssignmentCount;
            this.blockStateCount = blockStateCount;
            this.fluidBlockCount = fluidBlockCount;
        }
    }

    private static final class StateCounts {
        private final int blockStateRows;
        private final int fluidBlockRows;
        private StateCounts(int blockStateRows, int fluidBlockRows) {
            this.blockStateRows = blockStateRows;
            this.fluidBlockRows = fluidBlockRows;
        }
    }

    private static final class BlockStateCount {
        private final Block block;
        private final String blockId;
        private final int metadata;
        private final String stateText;
        private int count;
        private BlockStateCount(Block block, String blockId, int metadata, String stateText) {
            this.block = block;
            this.blockId = blockId;
            this.metadata = metadata;
            this.stateText = stateText;
        }
    }
}
