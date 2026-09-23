package dev.workbench.worldgenprototype.world;

import dev.workbench.worldgenprototype.diagnostics.WorldgenJfrEvents;
import dev.workbench.worldgenprototype.world.field.FractalValueField;
import dev.workbench.worldgenprototype.world.hydrology.WatershedEngine;
import dev.workbench.worldgenprototype.world.plan.WorldStudioPlan;
import dev.workbench.worldgenprototype.world.plan.WorldStudioPlans;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import net.minecraft.block.BlockStone;
import net.minecraft.block.state.IBlockState;
import net.minecraft.init.Biomes;
import net.minecraft.init.Blocks;
import net.minecraft.util.ResourceLocation;
import net.minecraft.world.biome.Biome;
import net.minecraftforge.fml.common.registry.ForgeRegistries;

/** Seed-bound, cached sampling authority shared by the provider and chunk generator. */
public final class WorldStudioTerrain {

    public static final int SEA_LEVEL = 62;
    public static final int CHUNK_SIZE = 16;
    public static final int COLUMN_COUNT = CHUNK_SIZE * CHUNK_SIZE;

    private static final int MIN_SURFACE = 20;
    private static final int MAX_SURFACE = 176;
    private static final int DEFAULT_CHUNK_CACHE_CAPACITY = 256;
    private static final int MAX_CHUNK_CACHE_CAPACITY = 8192;
    private static final int DEFAULT_REGION_CACHE_CAPACITY = 256;
    private static final int MAX_REGION_CACHE_CAPACITY = 65536;
    private static final int DEFAULT_BIOME_POINT_CACHE_CAPACITY = 8192;
    private static final int MAX_BIOME_POINT_CACHE_CAPACITY = 262144;
    private static final int DEFAULT_WATERSHED_TILE_CACHE_CAPACITY = 16;
    private static final int MAX_WATERSHED_TILE_CACHE_CAPACITY = 256;
    private static final int NO_BIOME_ID = Integer.MIN_VALUE;
    private static final short NO_WATER = Short.MIN_VALUE;
    private static final byte FLAG_OCEAN = 1;
    private static final byte FLAG_RIVER = 1 << 1;
    private static final byte FLAG_STREAM = 1 << 2;

    private final long seed;
    private volatile BoundPlan boundPlan;

    public WorldStudioTerrain(long seed) {
        this.seed = seed;
    }

    public Session session() {
        WorldStudioPlans.Snapshot snapshot = WorldStudioPlans.current();
        BoundPlan current = boundPlan;
        if (current == null || current.snapshot.version() != snapshot.version()) {
            synchronized (this) {
                current = boundPlan;
                if (current == null || current.snapshot.version() != snapshot.version()) {
                    current = new BoundPlan(seed, snapshot);
                    boundPlan = current;
                }
            }
        }
        return current.session;
    }

    public void cleanupCache() {
        BoundPlan current = boundPlan;
        if (current != null) {
            current.cleanupCache();
        }
    }

    public static final class Session {

        private final BoundPlan bound;

        private Session(BoundPlan bound) {
            this.bound = bound;
        }

        /** Scalar compatibility path. Chunk generation should use {@link #sampleChunk}. */
        public TerrainSample sample(int blockX, int blockZ) {
            return bound.sampleScalar(blockX, blockZ);
        }

        public ChunkSample sampleChunk(int chunkX, int chunkZ) {
            return bound.sampleChunk(chunkX, chunkZ);
        }

        public int biomeIdAt(int blockX, int blockZ) {
            return bound.biomeIdAt(blockX, blockZ);
        }

        public Biome resolveBiome(TerrainSample sample, int blockX, int blockZ) {
            return bound.resolveBiome(sample, blockX, blockZ);
        }

        public IBlockState lithologyState(TerrainSample sample, int y) {
            return lithologyStateFor(sample.megaRegion().lithology());
        }

        public long planVersion() {
            return bound.snapshot.version();
        }

        public String planHash() {
            return bound.snapshot.hash();
        }

        public String profileId() {
            return bound.plan.profileId();
        }

        public boolean cavesEnabled() {
            return bound.plan.cavesEnabled();
        }

        public boolean ravinesEnabled() {
            return bound.plan.ravinesEnabled();
        }

        public List<Biome> spawnBiomes() {
            return bound.spawnBiomes;
        }

        public int chunkSampleCacheCapacity() {
            return bound.chunkSampleCacheCapacity;
        }

        public long chunkSampleCacheHits() {
            return bound.chunkSampleCacheHits();
        }

        public long chunkSampleCacheMisses() {
            return bound.chunkSampleCacheMisses();
        }

        public long cachedBiomeLookups() {
            return bound.cachedBiomeLookups();
        }

        public int biomePointCacheCapacity() {
            return bound.biomePointCache.capacity();
        }

        public long pointBiomeCacheHits() {
            return bound.pointBiomeCacheHits();
        }

        public long scalarBiomeSamples() {
            return bound.scalarBiomeSamples();
        }

        public String watershedAlgorithmVersion() {
            return WatershedEngine.ALGORITHM_VERSION;
        }

        public int watershedTileCacheCapacity() {
            return bound.watershed.tileCacheCapacity();
        }

        public long watershedTileCacheHits() {
            return bound.watershed.tileCacheHits();
        }

        public long watershedTileCacheMisses() {
            return bound.watershed.tileCacheMisses();
        }

        public long watershedTileCacheEvictions() {
            return bound.watershed.tileCacheEvictions();
        }

        public long watershedComputedGridCells() {
            return bound.watershed.computedGridCells();
        }

        public WorldStudioPlan.WatershedDefinition watershedDefinition() {
            return bound.plan.watershed();
        }

        public double[] fieldContributions(String fieldId, int blockX, int blockZ) {
            return bound.field(fieldId).octaveContributions(blockX, blockZ);
        }
    }

    /** Immutable, compact result of evaluating every terrain column in one chunk. */
    public static final class ChunkSample {

        private final int chunkX;
        private final int chunkZ;
        private final short[] surfaceHeights = new short[COLUMN_COUNT];
        private final short[] waterSurfaceHeights = new short[COLUMN_COUNT];
        private final double[] surfaceNoise = new double[COLUMN_COUNT];
        private final float[] watershedDischarge = new float[COLUMN_COUNT];
        private final float[] watershedFillDepth = new float[COLUMN_COUNT];
        private final byte[] watershedStreamOrder = new byte[COLUMN_COUNT];
        private final byte[] watershedFlowDirection = new byte[COLUMN_COUNT];
        private final byte[] flags = new byte[COLUMN_COUNT];
        private final int[] regionIndexes = new int[COLUMN_COUNT];
        private final Biome[] biomes = new Biome[COLUMN_COUNT];
        private final IBlockState[] lithologyStates;
        private final long watershedFingerprint;
        private TerrainSample centerSample;

        private ChunkSample(
                int chunkX,
                int chunkZ,
                IBlockState[] lithologyStates,
                long watershedFingerprint
        ) {
            this.chunkX = chunkX;
            this.chunkZ = chunkZ;
            this.lithologyStates = lithologyStates;
            this.watershedFingerprint = watershedFingerprint;
            Arrays.fill(waterSurfaceHeights, NO_WATER);
        }

        public int chunkX() {
            return chunkX;
        }

        public int chunkZ() {
            return chunkZ;
        }

        public int surfaceHeight(int localX, int localZ) {
            return surfaceHeights[index(localX, localZ)];
        }

        public int waterSurfaceHeight(int localX, int localZ) {
            short value = waterSurfaceHeights[index(localX, localZ)];
            return value == NO_WATER ? Integer.MIN_VALUE : value;
        }

        public double surfaceNoise(int localX, int localZ) {
            return surfaceNoise[index(localX, localZ)];
        }

        public boolean ocean(int localX, int localZ) {
            return hasFlag(localX, localZ, FLAG_OCEAN);
        }

        public boolean river(int localX, int localZ) {
            return hasFlag(localX, localZ, FLAG_RIVER);
        }

        public boolean stream(int localX, int localZ) {
            return hasFlag(localX, localZ, FLAG_STREAM);
        }

        public double watershedDischarge(int localX, int localZ) {
            return watershedDischarge[index(localX, localZ)];
        }

        public double watershedFillDepth(int localX, int localZ) {
            return watershedFillDepth[index(localX, localZ)];
        }

        public int watershedStreamOrder(int localX, int localZ) {
            return Byte.toUnsignedInt(watershedStreamOrder[index(localX, localZ)]);
        }

        public int watershedFlowDirection(int localX, int localZ) {
            return Byte.toUnsignedInt(watershedFlowDirection[index(localX, localZ)]);
        }

        public long watershedFingerprint() {
            return watershedFingerprint;
        }

        public Biome biome(int localX, int localZ) {
            return biomes[index(localX, localZ)];
        }

        public int biomeId(int localX, int localZ) {
            return Biome.getIdForBiome(biome(localX, localZ));
        }

        public IBlockState lithologyState(int localX, int localZ, int y) {
            return lithologyStates[regionIndexes[index(localX, localZ)]];
        }

        public TerrainSample centerSample() {
            return centerSample;
        }

        public void copyBiomeIds(int[] output) {
            if (output.length < COLUMN_COUNT) {
                throw new IllegalArgumentException("biome output must contain at least 256 entries");
            }
            for (int column = 0; column < COLUMN_COUNT; column++) {
                output[column] = Biome.getIdForBiome(biomes[column]);
            }
        }

        private void setColumn(
                int localX,
                int localZ,
                MutableSample sample,
                Biome biome
        ) {
            int column = index(localX, localZ);
            surfaceHeights[column] = (short) sample.surfaceHeight;
            if (sample.waterSurfaceHeight != Integer.MIN_VALUE) {
                waterSurfaceHeights[column] = (short) sample.waterSurfaceHeight;
            }
            surfaceNoise[column] = sample.surfaceNoise;
            watershedDischarge[column] = (float) sample.watershedDischarge;
            watershedFillDepth[column] = (float) sample.watershedFillDepth;
            watershedStreamOrder[column] = (byte) sample.watershedStreamOrder;
            watershedFlowDirection[column] = (byte) sample.watershedFlowDirection;
            byte columnFlags = 0;
            if (sample.ocean) {
                columnFlags |= FLAG_OCEAN;
            }
            if (sample.river) {
                columnFlags |= FLAG_RIVER;
            }
            if (sample.stream) {
                columnFlags |= FLAG_STREAM;
            }
            flags[column] = columnFlags;
            regionIndexes[column] = sample.primaryRegionIndex;
            biomes[column] = biome;
            if (localX == 8 && localZ == 8) {
                centerSample = new TerrainSample(sample);
            }
        }

        private boolean hasFlag(int localX, int localZ, byte flag) {
            return (flags[index(localX, localZ)] & flag) != 0;
        }

        private static int index(int localX, int localZ) {
            return localX + localZ * CHUNK_SIZE;
        }
    }

    /** Scalar view retained for diagnostics and compatibility queries, not the chunk hot path. */
    public static final class TerrainSample {

        private final long megaRegionId;
        private final long megaRegionCellX;
        private final long megaRegionCellZ;
        private final WorldStudioPlan.MegaRegionDefinition megaRegion;
        private final WorldStudioPlan.MegaRegionDefinition neighboringRegion;
        private final double megaRegionWeight;
        private final double continentalness;
        private final double temperature;
        private final double moisture;
        private final double relief;
        private final double baseSurface;
        private final double riverDistance;
        private final double streamDistance;
        private final double riverStrength;
        private final double streamStrength;
        private final long watershedCellX;
        private final long watershedCellZ;
        private final long watershedTileX;
        private final long watershedTileZ;
        private final double watershedDischarge;
        private final double watershedRunoff;
        private final double watershedDrainageElevation;
        private final double watershedFillDepth;
        private final double watershedErodibility;
        private final int watershedStreamOrder;
        private final int watershedFlowDirection;
        private final int surfaceHeight;
        private final int waterSurfaceHeight;
        private final double surfaceNoise;
        private final boolean ocean;
        private final boolean river;
        private final boolean stream;

        private TerrainSample(MutableSample sample) {
            this.megaRegionId = sample.megaRegionId;
            this.megaRegionCellX = sample.megaRegionCellX;
            this.megaRegionCellZ = sample.megaRegionCellZ;
            this.megaRegion = sample.primaryRegion;
            this.neighboringRegion = sample.secondaryRegion;
            this.megaRegionWeight = sample.primaryWeight;
            this.continentalness = sample.continentalness;
            this.temperature = sample.temperature;
            this.moisture = sample.moisture;
            this.relief = sample.relief;
            this.baseSurface = sample.baseSurface;
            this.riverDistance = sample.riverDistance;
            this.streamDistance = sample.streamDistance;
            this.riverStrength = sample.riverStrength;
            this.streamStrength = sample.streamStrength;
            this.watershedCellX = sample.watershedCellX;
            this.watershedCellZ = sample.watershedCellZ;
            this.watershedTileX = sample.watershedTileX;
            this.watershedTileZ = sample.watershedTileZ;
            this.watershedDischarge = sample.watershedDischarge;
            this.watershedRunoff = sample.watershedRunoff;
            this.watershedDrainageElevation = sample.watershedDrainageElevation;
            this.watershedFillDepth = sample.watershedFillDepth;
            this.watershedErodibility = sample.watershedErodibility;
            this.watershedStreamOrder = sample.watershedStreamOrder;
            this.watershedFlowDirection = sample.watershedFlowDirection;
            this.surfaceHeight = sample.surfaceHeight;
            this.waterSurfaceHeight = sample.waterSurfaceHeight;
            this.surfaceNoise = sample.surfaceNoise;
            this.ocean = sample.ocean;
            this.river = sample.river;
            this.stream = sample.stream;
        }

        public long megaRegionId() {
            return megaRegionId;
        }

        public long megaRegionCellX() {
            return megaRegionCellX;
        }

        public long megaRegionCellZ() {
            return megaRegionCellZ;
        }

        public WorldStudioPlan.MegaRegionDefinition megaRegion() {
            return megaRegion;
        }

        public WorldStudioPlan.MegaRegionDefinition neighboringRegion() {
            return neighboringRegion;
        }

        public double megaRegionWeight() {
            return megaRegionWeight;
        }

        public double continentalness() {
            return continentalness;
        }

        public double temperature() {
            return temperature;
        }

        public double moisture() {
            return moisture;
        }

        public double relief() {
            return relief;
        }

        public double baseSurface() {
            return baseSurface;
        }

        public double riverDistance() {
            return riverDistance;
        }

        public double streamDistance() {
            return streamDistance;
        }

        public double riverStrength() {
            return riverStrength;
        }

        public double streamStrength() {
            return streamStrength;
        }

        public long watershedCellX() {
            return watershedCellX;
        }

        public long watershedCellZ() {
            return watershedCellZ;
        }

        public long watershedTileX() {
            return watershedTileX;
        }

        public long watershedTileZ() {
            return watershedTileZ;
        }

        public double watershedDischarge() {
            return watershedDischarge;
        }

        public double watershedRunoff() {
            return watershedRunoff;
        }

        public double watershedDrainageElevation() {
            return watershedDrainageElevation;
        }

        public double watershedFillDepth() {
            return watershedFillDepth;
        }

        public double watershedErodibility() {
            return watershedErodibility;
        }

        public int watershedStreamOrder() {
            return watershedStreamOrder;
        }

        public int watershedFlowDirection() {
            return watershedFlowDirection;
        }

        public int surfaceHeight() {
            return surfaceHeight;
        }

        public int waterSurfaceHeight() {
            return waterSurfaceHeight;
        }

        public double surfaceNoise() {
            return surfaceNoise;
        }

        public boolean ocean() {
            return ocean;
        }

        public boolean river() {
            return river;
        }

        public boolean stream() {
            return stream;
        }
    }

    private static final class BoundPlan {

        private final long seed;
        private final WorldStudioPlans.Snapshot snapshot;
        private final WorldStudioPlan plan;
        private final Session session;
        private final Map<String, FractalValueField> fields = new LinkedHashMap<>();
        private final Map<WorldStudioPlan.MegaRegionDefinition, List<Biome>> regionBiomes =
                new IdentityHashMap<>();
        private final Map<Biome, FractalValueField> biomeSelectionFields =
                new IdentityHashMap<>();
        private final Map<WorldStudioPlan.MegaRegionDefinition, Integer> regionIndexes =
                new IdentityHashMap<>();
        private final List<Biome> spawnBiomes;
        private final WorldStudioPlan.MegaRegionDefinition[] regions;
        private final IBlockState[] lithologyStates;
        private final double totalRegionWeight;
        private final int chunkSampleCacheCapacity;
        private final LinkedHashMap<Long, ChunkSample> chunkSamples;
        private final RegionNeighborhood[] regionNeighborhoods;
        private final int regionNeighborhoodMask;
        private final BiomePointCache biomePointCache;
        private final WatershedEngine watershed;
        private final ThreadLocal<MutableSample> watershedBaseScratch =
                ThreadLocal.withInitial(MutableSample::new);

        private long chunkCacheHits;
        private long chunkCacheMisses;
        private long cachedBiomeLookupCount;
        private long pointBiomeCacheHitCount;
        private long scalarBiomeSampleCount;
        private long scalarTerrainSampleCount;

        private BoundPlan(long seed, WorldStudioPlans.Snapshot snapshot) {
            this.seed = seed;
            this.snapshot = snapshot;
            this.plan = snapshot.plan();
            this.regions = plan.megaRegions().toArray(
                    new WorldStudioPlan.MegaRegionDefinition[0]
            );
            this.lithologyStates = new IBlockState[regions.length];
            for (WorldStudioPlan.FieldDefinition definition : plan.fields().values()) {
                fields.put(definition.id(), new FractalValueField(seed, definition));
            }
            double weight = 0.0D;
            Set<Biome> possibleSpawns = new LinkedHashSet<>();
            for (int regionIndex = 0; regionIndex < regions.length; regionIndex++) {
                WorldStudioPlan.MegaRegionDefinition region = regions[regionIndex];
                regionIndexes.put(region, regionIndex);
                lithologyStates[regionIndex] = lithologyStateFor(region.lithology());
                weight += region.weight();
                List<Biome> resolved = resolvePalette(region.biomeIds());
                regionBiomes.put(region, resolved);
                possibleSpawns.addAll(resolved);
                for (Biome biome : resolved) {
                    ResourceLocation id = biome.getRegistryName();
                    if (id != null && !biomeSelectionFields.containsKey(biome)) {
                        biomeSelectionFields.put(
                                biome,
                                new FractalValueField(
                                        seed,
                                        new WorldStudioPlan.FieldDefinition(
                                                "biome_selector:" + id,
                                                420.0D,
                                                2,
                                                2.0D,
                                                0.52D
                                        )
                                )
                        );
                    }
                }
            }
            this.totalRegionWeight = weight;
            if (possibleSpawns.isEmpty()) {
                possibleSpawns.add(Biomes.PLAINS);
            }
            this.spawnBiomes = Collections.unmodifiableList(new ArrayList<>(possibleSpawns));
            this.chunkSampleCacheCapacity = boundedPositiveIntegerProperty(
                    "workbench.worldgen.chunk_sample_cache_chunks",
                    DEFAULT_CHUNK_CACHE_CAPACITY,
                    MAX_CHUNK_CACHE_CAPACITY
            );
            this.chunkSamples = new LinkedHashMap<Long, ChunkSample>(
                    chunkSampleCacheCapacity + 1,
                    0.75F,
                    true
            ) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<Long, ChunkSample> eldest) {
                    return size() > BoundPlan.this.chunkSampleCacheCapacity;
                }
            };
            int regionCacheCapacity = powerOfTwoCapacity(boundedPositiveIntegerProperty(
                    "workbench.worldgen.region_cache_cells",
                    DEFAULT_REGION_CACHE_CAPACITY,
                    MAX_REGION_CACHE_CAPACITY
            ));
            this.regionNeighborhoods = new RegionNeighborhood[regionCacheCapacity];
            this.regionNeighborhoodMask = regionCacheCapacity - 1;
            this.biomePointCache = new BiomePointCache(powerOfTwoCapacity(
                    boundedPositiveIntegerProperty(
                            "workbench.worldgen.biome_point_cache_columns",
                            DEFAULT_BIOME_POINT_CACHE_CAPACITY,
                            MAX_BIOME_POINT_CACHE_CAPACITY
                    )
            ));
            WorldStudioPlan.WatershedDefinition definition = plan.watershed();
            this.watershed = new WatershedEngine(
                    seed,
                    new WatershedEngine.Configuration(
                            definition.cellSizeBlocks(),
                            definition.tileSizeCells(),
                            definition.haloCells(),
                            definition.baseRunoff(),
                            definition.rainfallScale(),
                            definition.permeabilityInfluence(),
                            definition.streamDischarge(),
                            definition.riverDischarge(),
                            definition.streamHalfWidthBlocks(),
                            definition.riverHalfWidthBlocks(),
                            definition.streamDepthBlocks(),
                            definition.riverDepthBlocks(),
                            definition.bankBlendBlocks(),
                            definition.lakeMinimumFillDepth()
                    ),
                    boundedPositiveIntegerProperty(
                            "workbench.worldgen.watershed_tile_cache_tiles",
                            DEFAULT_WATERSHED_TILE_CACHE_CAPACITY,
                            MAX_WATERSHED_TILE_CACHE_CAPACITY
                    ),
                    this::sampleWatershedCell
            );
            this.session = new Session(this);
        }

        private TerrainSample sampleScalar(int blockX, int blockZ) {
            MutableSample output = new MutableSample();
            watershed.sampleBlock(blockX, blockZ, output.hydrology);
            sampleInto(blockX, blockZ, output);
            synchronized (chunkSamples) {
                scalarTerrainSampleCount++;
            }
            return new TerrainSample(output);
        }

        private ChunkSample sampleChunk(int chunkX, int chunkZ) {
            WorldgenJfrEvents.ChunkSampleEvent event = WorldgenJfrEvents.beginChunkSample(
                    seed,
                    chunkX,
                    chunkZ,
                    snapshot.version(),
                    snapshot.hash()
            );
            boolean cacheHit = false;
            int cacheSize = 0;
            try {
                long key = chunkKey(chunkX, chunkZ);
                synchronized (chunkSamples) {
                    ChunkSample cached = chunkSamples.get(key);
                    if (cached != null) {
                        cacheHit = true;
                        chunkCacheHits++;
                        cacheSize = chunkSamples.size();
                        return cached;
                    }
                    ChunkSample computed = computeChunk(chunkX, chunkZ);
                    chunkSamples.put(key, computed);
                    chunkCacheMisses++;
                    cacheSize = chunkSamples.size();
                    return computed;
                }
            } finally {
                WorldgenJfrEvents.endChunkSample(event, cacheHit, cacheSize);
            }
        }

        private ChunkSample computeChunk(int chunkX, int chunkZ) {
            WatershedEngine.ChunkHydrology hydrology = watershed.sampleChunk(chunkX, chunkZ);
            ChunkSample chunk = new ChunkSample(
                    chunkX,
                    chunkZ,
                    lithologyStates,
                    hydrology.fingerprint()
            );
            MutableSample output = new MutableSample();
            int originX = chunkX << 4;
            int originZ = chunkZ << 4;
            for (int localX = 0; localX < CHUNK_SIZE; localX++) {
                for (int localZ = 0; localZ < CHUNK_SIZE; localZ++) {
                    int blockX = originX + localX;
                    int blockZ = originZ + localZ;
                    hydrology.sample(localX, localZ, output.hydrology);
                    sampleInto(blockX, blockZ, output);
                    chunk.setColumn(
                            localX,
                            localZ,
                            output,
                            resolveBiome(output, blockX, blockZ)
                    );
                }
            }
            if (chunk.centerSample == null) {
                throw new IllegalStateException("chunk sample did not capture its center column");
            }
            return chunk;
        }

        private int biomeIdAt(int blockX, int blockZ) {
            int chunkX = blockX >> 4;
            int chunkZ = blockZ >> 4;
            ChunkSample cached;
            synchronized (chunkSamples) {
                cached = chunkSamples.get(chunkKey(chunkX, chunkZ));
                if (cached != null) {
                    cachedBiomeLookupCount++;
                }
            }
            if (cached != null) {
                return cached.biomeId(blockX & 15, blockZ & 15);
            }
            long pointKey = chunkKey(blockX, blockZ);
            int pointBiome = biomePointCache.get(pointKey);
            if (pointBiome != NO_BIOME_ID) {
                synchronized (chunkSamples) {
                    pointBiomeCacheHitCount++;
                }
                return pointBiome;
            }
            synchronized (chunkSamples) {
                scalarBiomeSampleCount++;
            }
            MutableSample output = new MutableSample();
            watershed.sampleBlock(blockX, blockZ, output.hydrology);
            sampleInto(blockX, blockZ, output);
            int biomeId = Biome.getIdForBiome(resolveBiome(output, blockX, blockZ));
            biomePointCache.put(pointKey, biomeId);
            return biomeId;
        }

        private void sampleInto(int blockX, int blockZ, MutableSample output) {
            sampleBaseInto(blockX, blockZ, output);
            WatershedEngine.MutableHydrologySample hydrology = output.hydrology;
            WorldStudioPlan.WatershedDefinition definition = plan.watershed();

            double riverDistance = hydrology.riverDistance();
            double streamDistance = hydrology.streamDistance();
            double riverStrength = hydrology.riverStrength();
            double streamStrength = hydrology.streamStrength();
            boolean ocean = output.ocean;
            double baseSurface = output.baseSurface;
            if (ocean || baseSurface <= SEA_LEVEL + 2.0D) {
                riverStrength = 0.0D;
                streamStrength = 0.0D;
            }

            double carvedSurface = baseSurface;
            int waterSurfaceHeight = ocean ? SEA_LEVEL : Integer.MIN_VALUE;
            boolean river = riverStrength >= 0.58D;
            boolean stream = !river && streamStrength >= 0.58D;
            double channelWater = Math.min(
                    baseSurface - 0.75D,
                    hydrology.drainageElevation() - 0.25D
            );
            double erosionMultiplier = 0.75D + hydrology.erodibility() * 0.50D;
            if (riverStrength > 0.12D) {
                double riverBed = channelWater - definition.riverDepthBlocks()
                        * erosionMultiplier * (0.60D + riverStrength * 0.40D);
                carvedSurface = lerp(
                        carvedSurface,
                        riverBed,
                        smoothstep(0.12D, 0.95D, riverStrength)
                );
                if (river) {
                    waterSurfaceHeight = (int) StrictMath.floor(channelWater);
                }
            }
            if (streamStrength > 0.25D) {
                double streamWater = Math.min(channelWater, carvedSurface - 0.75D);
                double streamBed = streamWater
                        - definition.streamDepthBlocks() * erosionMultiplier;
                carvedSurface = lerp(
                        carvedSurface,
                        streamBed,
                        smoothstep(0.25D, 0.95D, streamStrength)
                );
                if (stream) {
                    waterSurfaceHeight = (int) StrictMath.floor(streamWater);
                }
            }

            int surfaceHeight = clamp(
                    (int) StrictMath.round(carvedSurface),
                    MIN_SURFACE,
                    MAX_SURFACE
            );
            if (waterSurfaceHeight <= surfaceHeight) {
                waterSurfaceHeight = Integer.MIN_VALUE;
            }
            output.riverDistance = riverDistance;
            output.streamDistance = streamDistance;
            output.riverStrength = riverStrength;
            output.streamStrength = streamStrength;
            output.watershedCellX = hydrology.cellX();
            output.watershedCellZ = hydrology.cellZ();
            output.watershedTileX = hydrology.tileX();
            output.watershedTileZ = hydrology.tileZ();
            output.watershedDischarge = hydrology.discharge();
            output.watershedRunoff = hydrology.runoff();
            output.watershedDrainageElevation = hydrology.drainageElevation();
            output.watershedFillDepth = hydrology.fillDepth();
            output.watershedErodibility = hydrology.erodibility();
            output.watershedStreamOrder = hydrology.streamOrder();
            output.watershedFlowDirection = hydrology.flowDirection();
            output.surfaceHeight = surfaceHeight;
            output.waterSurfaceHeight = waterSurfaceHeight;
            output.river = river;
            output.stream = stream;
        }

        private void sampleBaseInto(int blockX, int blockZ, MutableSample output) {
            selectRegion(blockX, blockZ, output);
            double primaryWeight = output.primaryWeight;
            double secondaryWeight = 1.0D - primaryWeight;
            WorldStudioPlan.MegaRegionDefinition primary = output.primaryRegion;
            WorldStudioPlan.MegaRegionDefinition secondary = output.secondaryRegion;

            double continentalness = field("continentalness").sample(blockX, blockZ);
            double relief = field("relief").sample(blockX, blockZ);
            double regionBase = primary.baseHeight() * primaryWeight
                    + secondary.baseHeight() * secondaryWeight;
            double regionRelief = primary.relief() * primaryWeight
                    + secondary.relief() * secondaryWeight;
            double continentalShape = continentalness < 0.0D
                    ? continentalness * 42.0D
                    : continentalness * 23.0D;
            double baseSurface = regionBase + continentalShape + relief * regionRelief;
            boolean ocean = baseSurface <= SEA_LEVEL - 0.5D;

            double temperatureBias = primary.temperatureBias() * primaryWeight
                    + secondary.temperatureBias() * secondaryWeight;
            double moistureBias = primary.moistureBias() * primaryWeight
                    + secondary.moistureBias() * secondaryWeight;
            double temperature = clamp01(
                    0.50D
                            + field("temperature").sample(blockX, blockZ) * 0.48D
                            + temperatureBias
                            - Math.max(0.0D, baseSurface - SEA_LEVEL) * 0.0035D
            );
            double moisture = clamp01(
                    0.50D
                            + field("moisture").sample(blockX, blockZ) * 0.50D
                            + moistureBias
            );
            output.continentalness = continentalness;
            output.temperature = temperature;
            output.moisture = moisture;
            output.relief = relief;
            output.baseSurface = baseSurface;
            output.surfaceNoise = field("surface").sample(blockX, blockZ) * 8.0D;
            output.ocean = ocean;
        }

        private void sampleWatershedCell(
                int blockX,
                int blockZ,
                WatershedEngine.CellData output
        ) {
            MutableSample scratch = watershedBaseScratch.get();
            sampleBaseInto(blockX, blockZ, scratch);
            double primaryWeight = scratch.primaryWeight;
            double secondaryWeight = 1.0D - primaryWeight;
            WorldStudioPlan.LithologyDefinition primary = plan.lithology(
                    scratch.primaryRegion.lithology()
            );
            WorldStudioPlan.LithologyDefinition secondary = plan.lithology(
                    scratch.secondaryRegion.lithology()
            );
            output.set(
                    scratch.baseSurface,
                    scratch.moisture,
                    primary.permeability() * primaryWeight
                            + secondary.permeability() * secondaryWeight,
                    primary.erodibility() * primaryWeight
                            + secondary.erodibility() * secondaryWeight
            );
        }

        private Biome resolveBiome(TerrainSample sample, int blockX, int blockZ) {
            return resolveBiome(
                    sample.ocean(),
                    sample.river(),
                    sample.surfaceHeight(),
                    sample.temperature(),
                    sample.moisture(),
                    sample.megaRegion(),
                    blockX,
                    blockZ
            );
        }

        private Biome resolveBiome(MutableSample sample, int blockX, int blockZ) {
            return resolveBiome(
                    sample.ocean,
                    sample.river,
                    sample.surfaceHeight,
                    sample.temperature,
                    sample.moisture,
                    sample.primaryRegion,
                    blockX,
                    blockZ
            );
        }

        private Biome resolveBiome(
                boolean ocean,
                boolean river,
                int surfaceHeight,
                double temperature,
                double moisture,
                WorldStudioPlan.MegaRegionDefinition region,
                int blockX,
                int blockZ
        ) {
            if (ocean) {
                return surfaceHeight < SEA_LEVEL - 9 ? Biomes.DEEP_OCEAN : Biomes.OCEAN;
            }
            if (river) {
                return temperature < 0.18D ? Biomes.FROZEN_RIVER : Biomes.RIVER;
            }
            if (surfaceHeight <= SEA_LEVEL + 1) {
                return Biomes.BEACH;
            }
            List<Biome> candidates = regionBiomes.get(region);
            if (candidates == null || candidates.isEmpty()) {
                return Biomes.PLAINS;
            }
            Biome selected = candidates.get(0);
            double selectedScore = Double.POSITIVE_INFINITY;
            double targetTemperature = temperature * 1.5D;
            for (int candidateIndex = 0; candidateIndex < candidates.size(); candidateIndex++) {
                Biome candidate = candidates.get(candidateIndex);
                double climateDistance = Math.abs(
                        targetTemperature - candidate.getDefaultTemperature()
                ) / 1.5D + Math.abs(moisture - candidate.getRainfall());
                FractalValueField selector = biomeSelectionFields.get(candidate);
                double patchBias = selector == null
                        ? 0.0D
                        : (selector.sample(blockX, blockZ) + 1.0D) * 0.09D;
                double score = climateDistance + patchBias;
                if (score < selectedScore) {
                    selectedScore = score;
                    selected = candidate;
                }
            }
            return selected;
        }

        private void selectRegion(int blockX, int blockZ, MutableSample output) {
            double scale = plan.megaRegionScale();
            long centerCellX = floor(blockX / scale);
            long centerCellZ = floor(blockZ / scale);
            RegionNeighborhood neighborhood = output.neighborhood;
            if (neighborhood == null
                    || neighborhood.centerCellX != centerCellX
                    || neighborhood.centerCellZ != centerCellZ) {
                neighborhood = regionNeighborhood(centerCellX, centerCellZ, scale);
                output.neighborhood = neighborhood;
            }

            int nearest = -1;
            int second = -1;
            double nearestDistance = 0.0D;
            double secondDistance = 0.0D;
            for (int cell = 0; cell < neighborhood.ids.length; cell++) {
                double dx = blockX - neighborhood.pointX[cell];
                double dz = blockZ - neighborhood.pointZ[cell];
                double distanceSquared = dx * dx + dz * dz;
                if (nearest < 0 || distanceSquared < nearestDistance) {
                    second = nearest;
                    secondDistance = nearestDistance;
                    nearest = cell;
                    nearestDistance = distanceSquared;
                } else if (second < 0 || distanceSquared < secondDistance) {
                    second = cell;
                    secondDistance = distanceSquared;
                }
            }
            if (nearest < 0 || second < 0) {
                throw new IllegalStateException("mega region selection produced fewer than two cells");
            }
            double separation = (
                    StrictMath.sqrt(secondDistance) - StrictMath.sqrt(nearestDistance)
            ) / scale;
            output.megaRegionId = neighborhood.ids[nearest];
            output.megaRegionCellX = neighborhood.cellX[nearest];
            output.megaRegionCellZ = neighborhood.cellZ[nearest];
            output.primaryRegion = neighborhood.definitions[nearest];
            output.secondaryRegion = neighborhood.definitions[second];
            output.primaryRegionIndex = neighborhood.definitionIndexes[nearest];
            output.primaryWeight = 0.5D + 0.5D * smoothstep(0.0D, 0.35D, separation);
        }

        private RegionNeighborhood regionNeighborhood(
                long centerCellX,
                long centerCellZ,
                double scale
        ) {
            int slot = (int) mix64(
                    centerCellX * 0x632be59bd9b4e019L
                            ^ centerCellZ * 0x9e3779b97f4a7c15L
            ) & regionNeighborhoodMask;
            synchronized (regionNeighborhoods) {
                RegionNeighborhood cached = regionNeighborhoods[slot];
                if (cached != null
                        && cached.centerCellX == centerCellX
                        && cached.centerCellZ == centerCellZ) {
                    return cached;
                }
                RegionNeighborhood computed = createRegionNeighborhood(
                        centerCellX,
                        centerCellZ,
                        scale
                );
                regionNeighborhoods[slot] = computed;
                return computed;
            }
        }

        private RegionNeighborhood createRegionNeighborhood(
                long centerCellX,
                long centerCellZ,
                double scale
        ) {
            RegionNeighborhood neighborhood = new RegionNeighborhood(centerCellX, centerCellZ);
            int index = 0;
            for (long cellX = centerCellX - 1L; cellX <= centerCellX + 1L; cellX++) {
                for (long cellZ = centerCellZ - 1L; cellZ <= centerCellZ + 1L; cellZ++) {
                    long id = mix64(
                            seed
                                    ^ cellX * 0x632be59bd9b4e019L
                                    ^ cellZ * 0x9e3779b97f4a7c15L
                                    ^ 0x51ed270b9179f0d7L
                    );
                    double jitterX = (unit(id ^ 0x94d049bb133111ebL) - 0.5D) * 0.70D;
                    double jitterZ = (unit(id ^ 0xbf58476d1ce4e5b9L) - 0.5D) * 0.70D;
                    int definitionIndex = selectRegionDefinitionIndex(id);
                    neighborhood.ids[index] = id;
                    neighborhood.cellX[index] = cellX;
                    neighborhood.cellZ[index] = cellZ;
                    neighborhood.pointX[index] = (cellX + 0.5D + jitterX) * scale;
                    neighborhood.pointZ[index] = (cellZ + 0.5D + jitterZ) * scale;
                    neighborhood.definitionIndexes[index] = definitionIndex;
                    neighborhood.definitions[index] = regions[definitionIndex];
                    index++;
                }
            }
            return neighborhood;
        }

        private int selectRegionDefinitionIndex(long id) {
            double selection = unit(id ^ 0xd6e8feb86659fd93L) * totalRegionWeight;
            for (int index = 0; index < regions.length; index++) {
                selection -= regions[index].weight();
                if (selection <= 0.0D) {
                    return index;
                }
            }
            return regions.length - 1;
        }

        private FractalValueField field(String id) {
            FractalValueField field = fields.get(id);
            if (field == null) {
                throw new IllegalStateException("Bound plan lost field: " + id);
            }
            return field;
        }

        private void cleanupCache() {
            synchronized (chunkSamples) {
                chunkSamples.clear();
            }
            synchronized (regionNeighborhoods) {
                Arrays.fill(regionNeighborhoods, null);
            }
            biomePointCache.clear();
            watershed.clearCache();
            watershedBaseScratch.remove();
        }

        private long chunkSampleCacheHits() {
            synchronized (chunkSamples) {
                return chunkCacheHits;
            }
        }

        private long chunkSampleCacheMisses() {
            synchronized (chunkSamples) {
                return chunkCacheMisses;
            }
        }

        private long cachedBiomeLookups() {
            synchronized (chunkSamples) {
                return cachedBiomeLookupCount;
            }
        }

        private long pointBiomeCacheHits() {
            synchronized (chunkSamples) {
                return pointBiomeCacheHitCount;
            }
        }

        private long scalarBiomeSamples() {
            synchronized (chunkSamples) {
                return scalarBiomeSampleCount + scalarTerrainSampleCount;
            }
        }

        private static List<Biome> resolvePalette(List<String> ids) {
            List<Biome> all = new ArrayList<>();
            List<Biome> bop = new ArrayList<>();
            for (int index = 0; index < ids.size(); index++) {
                ResourceLocation location = new ResourceLocation(ids.get(index));
                Biome biome = ForgeRegistries.BIOMES.getValue(location);
                if (biome != null) {
                    all.add(biome);
                    if ("biomesoplenty".equals(location.getNamespace())) {
                        bop.add(biome);
                    }
                }
            }
            List<Biome> selected = bop.isEmpty() ? all : bop;
            if (selected.isEmpty()) {
                selected = Collections.singletonList(Biomes.PLAINS);
            }
            return Collections.unmodifiableList(new ArrayList<>(selected));
        }
    }

    private static final class MutableSample {

        private final WatershedEngine.MutableHydrologySample hydrology =
                new WatershedEngine.MutableHydrologySample();
        private RegionNeighborhood neighborhood;
        private long megaRegionId;
        private long megaRegionCellX;
        private long megaRegionCellZ;
        private WorldStudioPlan.MegaRegionDefinition primaryRegion;
        private WorldStudioPlan.MegaRegionDefinition secondaryRegion;
        private int primaryRegionIndex;
        private double primaryWeight;
        private double continentalness;
        private double temperature;
        private double moisture;
        private double relief;
        private double baseSurface;
        private double riverDistance;
        private double streamDistance;
        private double riverStrength;
        private double streamStrength;
        private long watershedCellX;
        private long watershedCellZ;
        private long watershedTileX;
        private long watershedTileZ;
        private double watershedDischarge;
        private double watershedRunoff;
        private double watershedDrainageElevation;
        private double watershedFillDepth;
        private double watershedErodibility;
        private int watershedStreamOrder;
        private int watershedFlowDirection;
        private int surfaceHeight;
        private int waterSurfaceHeight;
        private double surfaceNoise;
        private boolean ocean;
        private boolean river;
        private boolean stream;
    }

    private static final class RegionNeighborhood {

        private final long centerCellX;
        private final long centerCellZ;
        private final long[] ids = new long[9];
        private final long[] cellX = new long[9];
        private final long[] cellZ = new long[9];
        private final double[] pointX = new double[9];
        private final double[] pointZ = new double[9];
        private final int[] definitionIndexes = new int[9];
        private final WorldStudioPlan.MegaRegionDefinition[] definitions =
                new WorldStudioPlan.MegaRegionDefinition[9];

        private RegionNeighborhood(long centerCellX, long centerCellZ) {
            this.centerCellX = centerCellX;
            this.centerCellZ = centerCellZ;
        }
    }

    /** Fixed-size primitive point cache; collisions affect reuse, never generated output. */
    private static final class BiomePointCache {

        private final long[] keys;
        private final int[] biomeIds;
        private final boolean[] occupied;
        private final int mask;

        private BiomePointCache(int capacity) {
            this.keys = new long[capacity];
            this.biomeIds = new int[capacity];
            this.occupied = new boolean[capacity];
            this.mask = capacity - 1;
        }

        private synchronized int get(long key) {
            int slot = (int) mix64(key) & mask;
            return occupied[slot] && keys[slot] == key ? biomeIds[slot] : NO_BIOME_ID;
        }

        private synchronized void put(long key, int biomeId) {
            int slot = (int) mix64(key) & mask;
            keys[slot] = key;
            biomeIds[slot] = biomeId;
            occupied[slot] = true;
        }

        private synchronized void clear() {
            Arrays.fill(occupied, false);
        }

        private int capacity() {
            return keys.length;
        }
    }

    private static IBlockState lithologyStateFor(String lithology) {
        if ("granite".equals(lithology)) {
            return Blocks.STONE.getDefaultState().withProperty(
                    BlockStone.VARIANT,
                    BlockStone.EnumType.GRANITE
            );
        }
        if ("diorite".equals(lithology)) {
            return Blocks.STONE.getDefaultState().withProperty(
                    BlockStone.VARIANT,
                    BlockStone.EnumType.DIORITE
            );
        }
        if ("andesite".equals(lithology)) {
            return Blocks.STONE.getDefaultState().withProperty(
                    BlockStone.VARIANT,
                    BlockStone.EnumType.ANDESITE
            );
        }
        return Blocks.STONE.getDefaultState();
    }

    private static int boundedPositiveIntegerProperty(String name, int fallback, int maximum) {
        String raw = System.getProperty(name);
        if (raw == null) {
            return fallback;
        }
        try {
            int parsed = Integer.parseInt(raw);
            return parsed > 0 ? Math.min(parsed, maximum) : fallback;
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }

    private static int powerOfTwoCapacity(int requested) {
        int capacity = 1;
        while (capacity < requested) {
            capacity <<= 1;
        }
        return capacity;
    }

    private static long chunkKey(int chunkX, int chunkZ) {
        return ((long) chunkX << 32) ^ (chunkZ & 0xffffffffL);
    }

    private static long floor(double value) {
        long integral = (long) value;
        return value < integral ? integral - 1L : integral;
    }

    private static long mix64(long value) {
        value = (value ^ (value >>> 30)) * 0xbf58476d1ce4e5b9L;
        value = (value ^ (value >>> 27)) * 0x94d049bb133111ebL;
        return value ^ (value >>> 31);
    }

    private static double unit(long value) {
        long magnitude = (mix64(value) >>> 11) & ((1L << 53) - 1L);
        return magnitude * 0x1.0p-53;
    }

    private static double smoothstep(double edge0, double edge1, double value) {
        if (edge0 == edge1) {
            return value < edge0 ? 0.0D : 1.0D;
        }
        double normalized = clamp01((value - edge0) / (edge1 - edge0));
        return normalized * normalized * (3.0D - 2.0D * normalized);
    }

    private static double clamp01(double value) {
        return Math.max(0.0D, Math.min(1.0D, value));
    }

    private static int clamp(int value, int minimum, int maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    private static double lerp(double left, double right, double amount) {
        return left + (right - left) * amount;
    }
}
