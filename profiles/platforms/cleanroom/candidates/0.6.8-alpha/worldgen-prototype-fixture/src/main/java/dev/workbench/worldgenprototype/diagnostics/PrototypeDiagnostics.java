package dev.workbench.worldgenprototype.diagnostics;

import dev.workbench.worldgenprototype.world.PrototypeFeature;
import dev.workbench.worldgenprototype.world.WorldStudioTerrain;
import dev.workbench.worldgenprototype.world.hydrology.WatershedEngine;
import dev.workbench.worldgenprototype.world.plan.WorldStudioPlan;
import dev.workbench.worldgenprototype.world.plan.WorldStudioPlans;
import java.util.Locale;
import net.minecraft.world.World;
import net.minecraft.world.biome.Biome;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/** Sampled JSON-line diagnostics with causal macro/climate/hydrology fields. */
public final class PrototypeDiagnostics {

    public static final String PREFIX = "WORLDGEN_PROTOTYPE";
    private static final Logger LOGGER = LogManager.getLogger("workbench-worldgen-prototype");
    private static final boolean ENABLED = Boolean.parseBoolean(
            System.getProperty("workbench.worldgen.diagnostics", "true")
    );
    private static final int SAMPLE_MODULO = positiveIntegerProperty(
            "workbench.worldgen.diagnostics.sample_modulo",
            16
    );

    private PrototypeDiagnostics() {
    }

    public static void planPublished(WorldStudioPlans.Snapshot snapshot) {
        if (!ENABLED) {
            return;
        }
        emit(String.format(
                Locale.ROOT,
                "{\"event\":\"plan.publish\",\"plan_version\":%d,"
                        + "\"plan_hash\":\"%s\",\"profile\":\"%s\"}",
                snapshot.version(),
                escape(snapshot.hash()),
                escape(snapshot.plan().profileId())
        ));
    }

    public static void generatorConstructed(
            World world,
            String generatorClass,
            WorldStudioTerrain.Session session,
            String caveGenerator,
            String ravineGenerator,
            boolean sharedSampling
    ) {
        if (!ENABLED) {
            return;
        }
        String providerClass = world.provider == null
                ? "unavailable"
                : world.provider.getClass().getName();
        WorldStudioPlan.WatershedDefinition watershed = session.watershedDefinition();
        emit(String.format(
                Locale.ROOT,
                "{\"event\":\"generator.construct\",\"seed\":%d,"
                        + "\"dimension\":%d,\"world_type\":\"%s\","
                        + "\"provider\":\"%s\",\"generator\":\"%s\","
                        + "\"profile\":\"%s\",\"plan_version\":%d,"
                        + "\"plan_hash\":\"%s\",\"cave_generator\":\"%s\","
                        + "\"ravine_generator\":\"%s\","
                        + "\"shared_sampling\":%s,"
                        + "\"chunk_sample_cache_capacity\":%d,"
                        + "\"biome_point_cache_capacity\":%d,"
                        + "\"watershed_algorithm\":\"%s\","
                        + "\"watershed_cell_size_blocks\":%d,"
                        + "\"watershed_tile_size_cells\":%d,"
                        + "\"watershed_halo_cells\":%d,"
                        + "\"watershed_tile_cache_capacity\":%d,"
                        + "\"watershed_stream_discharge\":%.6f,"
                        + "\"watershed_river_discharge\":%.6f}",
                world.getSeed(),
                world.provider == null ? Integer.MIN_VALUE : world.provider.getDimension(),
                escape(world.getWorldInfo().getTerrainType().getName()),
                escape(providerClass),
                escape(generatorClass),
                escape(session.profileId()),
                session.planVersion(),
                escape(session.planHash()),
                escape(caveGenerator),
                escape(ravineGenerator),
                sharedSampling,
                session.chunkSampleCacheCapacity(),
                session.biomePointCacheCapacity(),
                escape(session.watershedAlgorithmVersion()),
                watershed.cellSizeBlocks(),
                watershed.tileSizeCells(),
                watershed.haloCells(),
                session.watershedTileCacheCapacity(),
                watershed.streamDischarge(),
                watershed.riverDischarge()
        ));
    }

    public static void generated(
            World world,
            int chunkX,
            int chunkZ,
            int minimumHeight,
            int maximumHeight,
            int heightSum,
            int waterColumns,
            int riverColumns,
            int streamColumns,
            int filledDepressionColumns,
            double maximumWatershedDischarge,
            double maximumWatershedFillDepth,
            long watershedFingerprint,
            long heightHash,
            int carvedBlocks,
            boolean featurePlaced,
            Biome centerBiome,
            WorldStudioTerrain.TerrainSample center,
            WorldStudioTerrain.Session session,
            long elapsedNanos
    ) {
        if (!shouldSample(chunkX, chunkZ)) {
            return;
        }
        int centerX = (chunkX << 4) + 8;
        int centerZ = (chunkZ << 4) + 8;
        emit(String.format(
                Locale.ROOT,
                "{\"event\":\"chunk.generate\",\"seed\":%d,\"dimension\":%d,"
                        + "\"chunk_x\":%d,\"chunk_z\":%d,\"height_min\":%d,"
                        + "\"height_max\":%d,\"height_average\":%.3f,"
                        + "\"height_hash\":\"%016x\",\"water_columns\":%d,"
                        + "\"river_columns\":%d,\"stream_columns\":%d,"
                        + "\"filled_depression_columns\":%d,"
                        + "\"watershed_max_discharge\":%.6f,"
                        + "\"watershed_max_fill_depth\":%.6f,"
                        + "\"watershed_fingerprint\":\"%016x\","
                        + "\"carved_blocks\":%d,\"biome\":\"%s\","
                        + "\"mega_region_id\":\"%016x\","
                        + "\"mega_region_cell\":[%d,%d],\"mega_region\":\"%s\","
                        + "\"neighbor_region\":\"%s\",\"region_weight\":%.6f,"
                        + "\"lithology\":\"%s\",\"continentalness\":%.6f,"
                        + "\"temperature\":%.6f,\"moisture\":%.6f,"
                        + "\"relief\":%.6f,\"base_surface\":%.3f,"
                        + "\"river_distance\":%.6f,\"stream_distance\":%.6f,"
                        + "\"river_strength\":%.6f,\"stream_strength\":%.6f,"
                        + "\"watershed_cell\":[%d,%d],"
                        + "\"watershed_tile\":[%d,%d],"
                        + "\"watershed_discharge\":%.6f,"
                        + "\"watershed_runoff\":%.6f,"
                        + "\"watershed_drainage_elevation\":%.6f,"
                        + "\"watershed_fill_depth\":%.6f,"
                        + "\"watershed_erodibility\":%.6f,"
                        + "\"watershed_stream_order\":%d,"
                        + "\"watershed_flow_direction\":\"%s\","
                        + "\"continentalness_octaves\":%s,"
                        + "\"temperature_octaves\":%s,\"moisture_octaves\":%s,"
                        + "\"relief_octaves\":%s,"
                        + "\"profile\":\"%s\",\"plan_version\":%d,"
                        + "\"plan_hash\":\"%s\","
                        + "\"chunk_sample_cache_hits\":%d,"
                        + "\"chunk_sample_cache_misses\":%d,"
                        + "\"cached_biome_lookups\":%d,"
                        + "\"point_biome_cache_hits\":%d,"
                        + "\"scalar_biome_samples\":%d,"
                        + "\"watershed_tile_cache_hits\":%d,"
                        + "\"watershed_tile_cache_misses\":%d,"
                        + "\"watershed_tile_cache_evictions\":%d,"
                        + "\"watershed_computed_grid_cells\":%d,"
                        + "\"feature_stage\":\"%s\","
                        + "\"feature_placed\":%s,\"elapsed_us\":%d}",
                world.getSeed(),
                world.provider.getDimension(),
                chunkX,
                chunkZ,
                minimumHeight,
                maximumHeight,
                heightSum / 256.0D,
                heightHash,
                waterColumns,
                riverColumns,
                streamColumns,
                filledDepressionColumns,
                maximumWatershedDischarge,
                maximumWatershedFillDepth,
                watershedFingerprint,
                carvedBlocks,
                escape(String.valueOf(centerBiome.getRegistryName())),
                center.megaRegionId(),
                center.megaRegionCellX(),
                center.megaRegionCellZ(),
                escape(center.megaRegion().id()),
                escape(center.neighboringRegion().id()),
                center.megaRegionWeight(),
                escape(center.megaRegion().lithology()),
                center.continentalness(),
                center.temperature(),
                center.moisture(),
                center.relief(),
                center.baseSurface(),
                center.riverDistance(),
                center.streamDistance(),
                center.riverStrength(),
                center.streamStrength(),
                center.watershedCellX(),
                center.watershedCellZ(),
                center.watershedTileX(),
                center.watershedTileZ(),
                center.watershedDischarge(),
                center.watershedRunoff(),
                center.watershedDrainageElevation(),
                center.watershedFillDepth(),
                center.watershedErodibility(),
                center.watershedStreamOrder(),
                escape(WatershedEngine.directionName(center.watershedFlowDirection())),
                doubles(session.fieldContributions("continentalness", centerX, centerZ)),
                doubles(session.fieldContributions("temperature", centerX, centerZ)),
                doubles(session.fieldContributions("moisture", centerX, centerZ)),
                doubles(session.fieldContributions("relief", centerX, centerZ)),
                escape(session.profileId()),
                session.planVersion(),
                escape(session.planHash()),
                session.chunkSampleCacheHits(),
                session.chunkSampleCacheMisses(),
                session.cachedBiomeLookups(),
                session.pointBiomeCacheHits(),
                session.scalarBiomeSamples(),
                session.watershedTileCacheHits(),
                session.watershedTileCacheMisses(),
                session.watershedTileCacheEvictions(),
                session.watershedComputedGridCells(),
                PrototypeFeature.ACTIVE_STAGE.id(),
                featurePlaced,
                elapsedNanos / 1_000L
        ));
    }

    public static void populated(
            World world,
            int chunkX,
            int chunkZ,
            boolean populationAllowed,
            boolean decorationInvoked,
            boolean animalsAllowed,
            boolean iceAllowed,
            boolean featurePlaced,
            long elapsedNanos
    ) {
        if (!shouldSample(chunkX, chunkZ)) {
            return;
        }
        emit(String.format(
                Locale.ROOT,
                "{\"event\":\"chunk.populate\",\"seed\":%d,\"dimension\":%d,"
                        + "\"chunk_x\":%d,\"chunk_z\":%d,"
                        + "\"population_custom_allowed\":%s,"
                        + "\"biome_decoration_invoked\":%s,"
                        + "\"animals_allowed\":%s,\"ice_allowed\":%s,"
                        + "\"feature_placed\":%s,\"elapsed_us\":%d}",
                world.getSeed(),
                world.provider.getDimension(),
                chunkX,
                chunkZ,
                populationAllowed,
                decorationInvoked,
                animalsAllowed,
                iceAllowed,
                featurePlaced,
                elapsedNanos / 1_000L
        ));
    }

    public static void failed(
            World world,
            String phase,
            int chunkX,
            int chunkZ,
            Throwable failure
    ) {
        String record = String.format(
                Locale.ROOT,
                "{\"event\":\"chunk.failure\",\"seed\":%d,\"dimension\":%d,"
                        + "\"phase\":\"%s\",\"chunk_x\":%d,\"chunk_z\":%d,"
                        + "\"exception\":\"%s\",\"message\":\"%s\"}",
                world.getSeed(),
                world.provider.getDimension(),
                escape(phase),
                chunkX,
                chunkZ,
                escape(failure.getClass().getName()),
                escape(String.valueOf(failure.getMessage()))
        );
        LOGGER.error(PREFIX + " " + record, failure);
    }

    private static boolean shouldSample(int chunkX, int chunkZ) {
        if (!ENABLED) {
            return false;
        }
        if (Math.abs(chunkX) <= 1 && Math.abs(chunkZ) <= 1) {
            return true;
        }
        int mixed = chunkX * 73428767 ^ chunkZ * 912931;
        return Math.floorMod(mixed, SAMPLE_MODULO) == 0;
    }

    private static String doubles(double[] values) {
        StringBuilder output = new StringBuilder("[");
        for (int index = 0; index < values.length; index++) {
            if (index > 0) {
                output.append(',');
            }
            output.append(String.format(Locale.ROOT, "%.8f", values[index]));
        }
        return output.append(']').toString();
    }

    private static void emit(String record) {
        LOGGER.info(PREFIX + " " + record);
    }

    private static int positiveIntegerProperty(String name, int fallback) {
        String raw = System.getProperty(name);
        if (raw == null) {
            return fallback;
        }
        try {
            int parsed = Integer.parseInt(raw);
            return parsed > 0 ? parsed : fallback;
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    private static String escape(String value) {
        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }
}
