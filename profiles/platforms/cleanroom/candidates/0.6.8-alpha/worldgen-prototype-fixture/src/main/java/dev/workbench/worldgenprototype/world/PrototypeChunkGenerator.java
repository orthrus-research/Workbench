package dev.workbench.worldgenprototype.world;

import static net.minecraftforge.event.terraingen.InitMapGenEvent.EventType.CAVE;
import static net.minecraftforge.event.terraingen.InitMapGenEvent.EventType.RAVINE;

import dev.workbench.worldgenprototype.diagnostics.PrototypeDiagnostics;
import dev.workbench.worldgenprototype.diagnostics.WorldgenCausalTrace;
import dev.workbench.worldgenprototype.diagnostics.WorldgenJfrEvents;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import javax.annotation.Nullable;
import net.minecraft.block.BlockFalling;
import net.minecraft.block.material.Material;
import net.minecraft.entity.EnumCreatureType;
import net.minecraft.init.Biomes;
import net.minecraft.init.Blocks;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.world.WorldEntitySpawner;
import net.minecraft.world.biome.Biome;
import net.minecraft.world.biome.BiomeProvider;
import net.minecraft.world.chunk.Chunk;
import net.minecraft.world.chunk.ChunkPrimer;
import net.minecraft.world.gen.IChunkGenerator;
import net.minecraft.world.gen.MapGenBase;
import net.minecraft.world.gen.MapGenCaves;
import net.minecraft.world.gen.MapGenRavine;
import net.minecraftforge.event.ForgeEventFactory;
import net.minecraftforge.event.terraingen.PopulateChunkEvent;
import net.minecraftforge.event.terraingen.TerrainGen;

/** Mega-region vertical slice with native biome surfaces and Forge-selected carvers. */
public final class PrototypeChunkGenerator implements IChunkGenerator {

    private static final long HEIGHT_HASH_OFFSET = 0xcbf29ce484222325L;
    private static final long HEIGHT_HASH_PRIME = 0x100000001b3L;

    private final World world;
    private final WorldStudioTerrain terrain;
    private final PrototypeFeature feature = new PrototypeFeature();
    private final MapGenBase caveGenerator;
    private final MapGenBase ravineGenerator;

    public PrototypeChunkGenerator(World world) {
        this.world = world;
        BiomeProvider provider = world.getBiomeProvider();
        boolean sharedSampling = provider instanceof WorldStudioBiomeProvider;
        this.terrain = sharedSampling
                ? ((WorldStudioBiomeProvider) provider).terrain()
                : new WorldStudioTerrain(world.getSeed());
        this.caveGenerator = TerrainGen.getModdedMapGen(new MapGenCaves(), CAVE);
        this.ravineGenerator = TerrainGen.getModdedMapGen(new MapGenRavine(), RAVINE);
        this.world.setSeaLevel(WorldStudioTerrain.SEA_LEVEL);
        PrototypeDiagnostics.generatorConstructed(
                world,
                getClass().getName(),
                terrain.session(),
                caveGenerator.getClass().getName(),
                ravineGenerator.getClass().getName(),
                sharedSampling
        );
    }

    @Override
    public Chunk generateChunk(int chunkX, int chunkZ) {
        long started = System.nanoTime();
        try {
            WorldStudioTerrain.Session session = terrain.session();
            WorldgenJfrEvents.ChunkStageEvent stage = beginStage(
                    chunkX,
                    chunkZ,
                    "sample.chunk",
                    session
            );
            WorldStudioTerrain.ChunkSample samples;
            try {
                samples = session.sampleChunk(chunkX, chunkZ);
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            ChunkPrimer primer = new ChunkPrimer();
            int minimumHeight = Integer.MAX_VALUE;
            int maximumHeight = Integer.MIN_VALUE;
            int heightSum = 0;
            int waterColumns = 0;
            int riverColumns = 0;
            int streamColumns = 0;
            int filledDepressionColumns = 0;
            double maximumWatershedDischarge = 0.0D;
            double maximumWatershedFillDepth = 0.0D;
            long heightHash = HEIGHT_HASH_OFFSET;
            double lakeMinimumFillDepth = session.watershedDefinition()
                    .lakeMinimumFillDepth();

            stage = beginStage(chunkX, chunkZ, "primer.base", session);
            try {
                for (int localX = 0; localX < 16; localX++) {
                    for (int localZ = 0; localZ < 16; localZ++) {
                        int surfaceHeight = samples.surfaceHeight(localX, localZ);
                        minimumHeight = Math.min(minimumHeight, surfaceHeight);
                        maximumHeight = Math.max(maximumHeight, surfaceHeight);
                        heightSum += surfaceHeight;
                        heightHash ^= surfaceHeight & 0xffL;
                        heightHash *= HEIGHT_HASH_PRIME;
                        if (samples.waterSurfaceHeight(localX, localZ) > surfaceHeight) {
                            waterColumns++;
                        }
                        if (samples.river(localX, localZ)) {
                            riverColumns++;
                        }
                        if (samples.stream(localX, localZ)) {
                            streamColumns++;
                        }
                        double fillDepth = samples.watershedFillDepth(localX, localZ);
                        if (fillDepth >= lakeMinimumFillDepth) {
                            filledDepressionColumns++;
                        }
                        maximumWatershedFillDepth = Math.max(
                                maximumWatershedFillDepth,
                                fillDepth
                        );
                        maximumWatershedDischarge = Math.max(
                                maximumWatershedDischarge,
                                samples.watershedDischarge(localX, localZ)
                        );
                        fillBaseColumn(primer, localX, localZ, samples);
                    }
                }
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "surface.biome", session);
            Biome[] biomes;
            try {
                biomes = world.getBiomeProvider().getBiomes(
                        null,
                        chunkX * 16,
                        chunkZ * 16,
                        16,
                        16,
                        false
                );
                if (ForgeEventFactory.onReplaceBiomeBlocks(this, chunkX, chunkZ, primer, world)) {
                    Random surfaceRandom = stageRandom(chunkX, chunkZ, 0x3c6ef372fe94f82bL);
                    for (int localZ = 0; localZ < 16; localZ++) {
                        for (int localX = 0; localX < 16; localX++) {
                            int index = localX + localZ * 16;
                            int blockX = chunkX * 16 + localX;
                            int blockZ = chunkZ * 16 + localZ;
                            biomes[index].genTerrainBlocks(
                                    world,
                                    surfaceRandom,
                                    primer,
                                    blockX,
                                    blockZ,
                                    samples.surfaceNoise(localX, localZ)
                            );
                        }
                    }
                }
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            if (session.cavesEnabled()) {
                stage = beginStage(chunkX, chunkZ, "carver.cave", session);
                try {
                    caveGenerator.generate(world, chunkX, chunkZ, primer);
                } finally {
                    WorldgenJfrEvents.endChunkStage(stage);
                }
            }
            if (session.ravinesEnabled()) {
                stage = beginStage(chunkX, chunkZ, "carver.ravine", session);
                try {
                    ravineGenerator.generate(world, chunkX, chunkZ, primer);
                } finally {
                    WorldgenJfrEvents.endChunkStage(stage);
                }
            }
            int carvedBlocks = countUndergroundAir(primer, samples);

            stage = beginStage(chunkX, chunkZ, "feature.generate_base", session);
            boolean featurePlaced;
            try {
                featurePlaced = feature.generateBase(
                        primer,
                        chunkRandom(chunkX, chunkZ),
                        chunkX,
                        chunkZ,
                        samples
                );
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "chunk.construct", session);
            Chunk chunk;
            try {
                chunk = new Chunk(world, primer, chunkX, chunkZ);
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }
            byte[] biomeArray = chunk.getBiomeArray();
            for (int index = 0; index < biomeArray.length; index++) {
                biomeArray[index] = (byte) (Biome.getIdForBiome(biomes[index]) & 0xff);
            }
            stage = beginStage(chunkX, chunkZ, "lighting.skylight_map", session);
            try {
                chunk.generateSkylightMap();
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }
            PrototypeDiagnostics.generated(
                    world,
                    chunkX,
                    chunkZ,
                    minimumHeight,
                    maximumHeight,
                    heightSum,
                    waterColumns,
                    riverColumns,
                    streamColumns,
                    filledDepressionColumns,
                    maximumWatershedDischarge,
                    maximumWatershedFillDepth,
                    samples.watershedFingerprint(),
                    heightHash,
                    carvedBlocks,
                    featurePlaced,
                    biomes[8 + 8 * 16],
                    samples.centerSample(),
                    session,
                    System.nanoTime() - started
            );
            return chunk;
        } catch (RuntimeException | Error failure) {
            PrototypeDiagnostics.failed(world, "generate", chunkX, chunkZ, failure);
            throw failure;
        }
    }

    private static void fillBaseColumn(
            ChunkPrimer primer,
            int localX,
            int localZ,
            WorldStudioTerrain.ChunkSample sample
    ) {
        primer.setBlockState(localX, 0, localZ, Blocks.BEDROCK.getDefaultState());
        for (int y = 1; y <= sample.surfaceHeight(localX, localZ); y++) {
            primer.setBlockState(
                    localX,
                    y,
                    localZ,
                    sample.lithologyState(localX, localZ, y)
            );
        }
        for (int y = sample.surfaceHeight(localX, localZ) + 1;
                y <= sample.waterSurfaceHeight(localX, localZ);
                y++) {
            primer.setBlockState(localX, y, localZ, Blocks.WATER.getDefaultState());
        }
    }

    private static int countUndergroundAir(
            ChunkPrimer primer,
            WorldStudioTerrain.ChunkSample samples
    ) {
        int carved = 0;
        for (int localX = 0; localX < 16; localX++) {
            for (int localZ = 0; localZ < 16; localZ++) {
                int ceiling = Math.max(11, samples.surfaceHeight(localX, localZ) - 4);
                for (int y = 11; y < ceiling; y++) {
                    if (primer.getBlockState(localX, y, localZ).getMaterial() == Material.AIR) {
                        carved++;
                    }
                }
            }
        }
        return carved;
    }

    @Override
    public void populate(int chunkX, int chunkZ) {
        long started = System.nanoTime();
        WorldStudioTerrain.Session session = terrain.session();
        Random eventRandom = populationRandom(chunkX, chunkZ);
        Random featureRandom = stageRandom(chunkX, chunkZ, 0x6a09e667f3bcc909L);
        BlockPos origin = new BlockPos(chunkX * 16, 0, chunkZ * 16);
        WorldgenJfrEvents.ChunkStageEvent stage = beginStage(
                chunkX,
                chunkZ,
                "populate.biome_lookup",
                session
        );
        Biome biome;
        try {
            biome = world.getBiome(origin.add(16, 0, 16));
        } finally {
            WorldgenJfrEvents.endChunkStage(stage);
        }
        boolean generatedVillage = false;
        boolean populationAllowed = false;
        boolean decorationInvoked = false;
        boolean animalsAllowed = false;
        boolean iceAllowed = false;
        boolean featurePlaced = false;
        boolean previousInstantFalling = BlockFalling.fallInstantly;
        BlockFalling.fallInstantly = true;
        try {
            stage = beginStage(chunkX, chunkZ, "populate.pre_event", session);
            try {
                ForgeEventFactory.onChunkPopulate(
                        true,
                        this,
                        world,
                        eventRandom,
                        chunkX,
                        chunkZ,
                        generatedVillage
                );
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "populate.custom", session);
            try {
                populationAllowed = TerrainGen.populate(
                        this,
                        world,
                        eventRandom,
                        chunkX,
                        chunkZ,
                        generatedVillage,
                        PopulateChunkEvent.Populate.EventType.CUSTOM
                );
                WorldgenCausalTrace.populationGate(
                        world.getSeed(),
                        world.provider.getDimension(),
                        chunkX,
                        chunkZ,
                        populationAllowed
                );
                if (populationAllowed) {
                    featurePlaced = feature.populateCustom(
                            world,
                            featureRandom,
                            chunkX,
                            chunkZ
                    );
                }
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "decorate.biome", session);
            try {
                biome.decorate(world, eventRandom, origin);
                decorationInvoked = true;
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "populate.animals", session);
            try {
                animalsAllowed = TerrainGen.populate(
                        this,
                        world,
                        eventRandom,
                        chunkX,
                        chunkZ,
                        generatedVillage,
                        PopulateChunkEvent.Populate.EventType.ANIMALS
                );
                if (animalsAllowed) {
                    WorldEntitySpawner.performWorldGenSpawning(
                            world,
                            biome,
                            chunkX * 16 + 8,
                            chunkZ * 16 + 8,
                            16,
                            16,
                            eventRandom
                    );
                }
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "populate.ice", session);
            try {
                iceAllowed = TerrainGen.populate(
                        this,
                        world,
                        eventRandom,
                        chunkX,
                        chunkZ,
                        generatedVillage,
                        PopulateChunkEvent.Populate.EventType.ICE
                );
                if (iceAllowed) {
                    freezeAndSnow(origin.add(8, 0, 8));
                }
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }

            stage = beginStage(chunkX, chunkZ, "populate.post_event", session);
            try {
                ForgeEventFactory.onChunkPopulate(
                        false,
                        this,
                        world,
                        eventRandom,
                        chunkX,
                        chunkZ,
                        generatedVillage
                );
            } finally {
                WorldgenJfrEvents.endChunkStage(stage);
            }
        } catch (RuntimeException | Error failure) {
            PrototypeDiagnostics.failed(world, "populate", chunkX, chunkZ, failure);
            throw failure;
        } finally {
            BlockFalling.fallInstantly = previousInstantFalling;
        }
        PrototypeDiagnostics.populated(
                world,
                chunkX,
                chunkZ,
                populationAllowed,
                decorationInvoked,
                animalsAllowed,
                iceAllowed,
                featurePlaced,
                System.nanoTime() - started
        );
    }

    private WorldgenJfrEvents.ChunkStageEvent beginStage(
            int chunkX,
            int chunkZ,
            String stage,
            WorldStudioTerrain.Session session
    ) {
        return WorldgenJfrEvents.beginChunkStage(
                world.getSeed(),
                world.provider.getDimension(),
                chunkX,
                chunkZ,
                stage,
                session.planVersion(),
                session.planHash()
        );
    }

    private void freezeAndSnow(BlockPos start) {
        for (int localX = 0; localX < 16; localX++) {
            for (int localZ = 0; localZ < 16; localZ++) {
                BlockPos target = world.getPrecipitationHeight(start.add(localX, 0, localZ));
                Biome targetBiome = world.getBiome(target);
                if (world.canBlockFreezeWater(target.down())) {
                    world.setBlockState(target.down(), Blocks.ICE.getDefaultState(), 2);
                }
                if (targetBiome.getRainfall() > 0.01F && world.canSnowAt(target, true)) {
                    world.setBlockState(target, Blocks.SNOW_LAYER.getDefaultState(), 2);
                }
            }
        }
    }

    private Random chunkRandom(int chunkX, int chunkZ) {
        return stageRandom(chunkX, chunkZ, 0xbb67ae8584caa73bL);
    }

    private Random stageRandom(int chunkX, int chunkZ, long salt) {
        long mixed = world.getSeed()
                ^ salt
                ^ ((long) chunkX * 341873128712L)
                ^ ((long) chunkZ * 132897987541L);
        return new Random(mixed);
    }

    private Random populationRandom(int chunkX, int chunkZ) {
        Random random = new Random(world.getSeed());
        long xSalt = random.nextLong() / 2L * 2L + 1L;
        long zSalt = random.nextLong() / 2L * 2L + 1L;
        random.setSeed(((long) chunkX * xSalt + (long) chunkZ * zSalt) ^ world.getSeed());
        return random;
    }

    @Override
    public boolean generateStructures(Chunk chunk, int chunkX, int chunkZ) {
        return false;
    }

    @Override
    public List<Biome.SpawnListEntry> getPossibleCreatures(
            EnumCreatureType creatureType,
            BlockPos position
    ) {
        Biome biome = world.getBiome(position);
        return biome == null ? Collections.emptyList() : biome.getSpawnableList(creatureType);
    }

    @Nullable
    @Override
    public BlockPos getNearestStructurePos(
            World world,
            String structureName,
            BlockPos position,
            boolean findUnexplored
    ) {
        return null;
    }

    @Override
    public boolean isInsideStructure(World world, String structureName, BlockPos position) {
        return false;
    }

    @Override
    public void recreateStructures(Chunk chunk, int chunkX, int chunkZ) {
        // No owned structures in this vertical slice.
    }
}
