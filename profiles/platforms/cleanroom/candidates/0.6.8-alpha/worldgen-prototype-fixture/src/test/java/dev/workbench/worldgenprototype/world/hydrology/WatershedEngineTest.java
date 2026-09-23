package dev.workbench.worldgenprototype.world.hydrology;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class WatershedEngineTest {

    @Test
    void priorityFloodResolvesAnEnclosedDepression() {
        WatershedEngine engine = engine(
                7L,
                (blockX, blockZ, output) -> output.set(
                        50.0D + Math.abs(blockX) + Math.abs(blockZ),
                        0.8D,
                        0.2D,
                        0.5D
                ),
                4
        );
        WatershedEngine.MutableHydrologySample sample =
                new WatershedEngine.MutableHydrologySample();

        engine.sampleBlock(2, 2, sample);

        assertTrue(sample.fillDepth() > 1.0D);
        assertTrue(sample.flowDirection() >= 0 && sample.flowDirection() < 8);
        assertTrue(sample.discharge() >= sample.runoff());
    }

    @Test
    void planarTerrainRoutesTowardLowerElevation() {
        WatershedEngine engine = engine(
                11L,
                (blockX, blockZ, output) -> output.set(
                        100.0D + blockX * 0.25D,
                        0.9D,
                        0.1D,
                        0.4D
                ),
                4
        );
        WatershedEngine.MutableHydrologySample sample =
                new WatershedEngine.MutableHydrologySample();

        engine.sampleBlock(18, 18, sample);

        assertTrue(WatershedEngine.directionName(sample.flowDirection()).contains("west"));
        assertTrue(sample.streamOrder() >= 1);
    }

    @Test
    void planarRunoffAccumulatesIntoDownstreamCells() {
        WatershedEngine engine = engine(
                13L,
                (blockX, blockZ, output) -> output.set(
                        100.0D + blockX * 0.25D,
                        0.9D,
                        0.1D,
                        0.4D
                ),
                4
        );
        WatershedEngine.MutableHydrologySample sample =
                new WatershedEngine.MutableHydrologySample();

        engine.sampleBlock(2, 18, sample);

        assertTrue(sample.discharge() > sample.runoff());
    }

    @Test
    void chunkResultsDoNotDependOnTileRequestOrder() {
        WatershedEngine first = noisyEngine(91L, 8);
        WatershedEngine second = noisyEngine(91L, 8);

        long expectedOrigin = first.sampleChunk(0, 0).fingerprint();
        long expectedEast = first.sampleChunk(32, 0).fingerprint();
        long actualEast = second.sampleChunk(32, 0).fingerprint();
        long actualOrigin = second.sampleChunk(0, 0).fingerprint();

        assertEquals(expectedOrigin, actualOrigin);
        assertEquals(expectedEast, actualEast);
    }

    @Test
    void primitiveTileCacheStaysBoundedAndEvicts() {
        WatershedEngine engine = noisyEngine(31L, 2);

        engine.sampleChunk(0, 0);
        engine.sampleChunk(32, 0);
        engine.sampleChunk(64, 0);

        assertTrue(engine.tileCacheSize() <= 2);
        assertTrue(engine.tileCacheMisses() >= 3);
        assertTrue(engine.tileCacheEvictions() >= 1);
    }

    @Test
    void oneSamplingWindowResolvesEachDistinctTileOnce() {
        WatershedEngine engine = noisyEngine(37L, 16);

        engine.sampleChunk(0, 0);

        assertEquals(0L, engine.tileCacheHits());
        assertTrue(engine.tileCacheMisses() >= 1L);
        assertTrue(engine.tileCacheMisses() <= 9L);
    }

    @Test
    void negativeBlocksUseFloorBasedCellAndTileCoordinates() {
        WatershedEngine engine = noisyEngine(19L, 4);
        WatershedEngine.MutableHydrologySample sample =
                new WatershedEngine.MutableHydrologySample();

        engine.sampleBlock(-1, -1, sample);

        assertEquals(-1L, sample.cellX());
        assertEquals(-1L, sample.cellZ());
        assertEquals(-1L, sample.tileX());
        assertEquals(-1L, sample.tileZ());
    }

    @Test
    void chunkBatchMatchesScalarHydrologyAtPositiveAndNegativeCoordinates() {
        WatershedEngine engine = noisyEngine(101L, 8);
        assertBatchMatchesScalar(engine, 3, -2, 0, 0);
        assertBatchMatchesScalar(engine, 3, -2, 8, 8);
        assertBatchMatchesScalar(engine, 3, -2, 15, 15);
    }

    private static void assertBatchMatchesScalar(
            WatershedEngine engine,
            int chunkX,
            int chunkZ,
            int localX,
            int localZ
    ) {
        WatershedEngine.ChunkHydrology batch = engine.sampleChunk(chunkX, chunkZ);
        WatershedEngine.MutableHydrologySample batched =
                new WatershedEngine.MutableHydrologySample();
        WatershedEngine.MutableHydrologySample scalar =
                new WatershedEngine.MutableHydrologySample();
        batch.sample(localX, localZ, batched);
        engine.sampleBlock((chunkX << 4) + localX, (chunkZ << 4) + localZ, scalar);

        assertEquals(scalar.cellX(), batched.cellX());
        assertEquals(scalar.cellZ(), batched.cellZ());
        assertEquals(scalar.riverStrength(), batched.riverStrength(), 0.000001D);
        assertEquals(scalar.streamStrength(), batched.streamStrength(), 0.000001D);
        assertEquals(scalar.discharge(), batched.discharge(), 0.000001D);
        assertEquals(scalar.fillDepth(), batched.fillDepth(), 0.000001D);
        assertEquals(scalar.streamOrder(), batched.streamOrder());
        assertEquals(scalar.flowDirection(), batched.flowDirection());
    }

    private static WatershedEngine noisyEngine(long seed, int cacheCapacity) {
        return engine(
                seed,
                (blockX, blockZ, output) -> {
                    double elevation = 72.0D
                            + StrictMath.sin(blockX * 0.03125D) * 9.0D
                            + StrictMath.cos(blockZ * 0.046875D) * 7.0D;
                    double rainfall = 0.5D
                            + StrictMath.sin((blockX + blockZ) * 0.015625D) * 0.25D;
                    output.set(elevation, rainfall, 0.3D, 0.45D);
                },
                cacheCapacity
        );
    }

    private static WatershedEngine engine(
            long seed,
            WatershedEngine.CellSampler sampler,
            int cacheCapacity
    ) {
        return new WatershedEngine(
                seed,
                new WatershedEngine.Configuration(
                        4,
                        8,
                        2,
                        0.1D,
                        1.0D,
                        0.6D,
                        0.5D,
                        1000.0D,
                        1.25D,
                        3.0D,
                        1.0D,
                        3.0D,
                        1.0D,
                        0.5D
                ),
                cacheCapacity,
                sampler
        );
    }
}
