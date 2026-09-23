package dev.workbench.worldgenprototype.world;

import java.util.List;
import java.util.Random;
import javax.annotation.Nullable;
import net.minecraft.init.Biomes;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.world.biome.Biome;
import net.minecraft.world.biome.BiomeProvider;
import net.minecraft.world.gen.layer.GenLayer;
import net.minecraft.world.gen.layer.IntCache;

/** Complete provider whose final layers remain visible to Forge biome-gen listeners. */
public final class WorldStudioBiomeProvider extends BiomeProvider {

    private final WorldStudioTerrain terrain;
    private final GenLayer generationLayer;
    private final GenLayer indexLayer;

    public WorldStudioBiomeProvider(World world) {
        super();
        this.terrain = new WorldStudioTerrain(world.getSeed());
        GenLayer generation = new WorldStudioGenLayer(0x776f726c6467656eL, terrain, 4);
        GenLayer index = new WorldStudioGenLayer(0x62696f6d65696e64L, terrain, 1);
        GenLayer[] layers = getModdedBiomeGenerators(
                world.getWorldInfo().getTerrainType(),
                world.getSeed(),
                new GenLayer[]{generation, index, generation}
        );
        this.generationLayer = layers[0];
        this.indexLayer = layers[1];
        this.generationLayer.initWorldGenSeed(world.getSeed());
        this.indexLayer.initWorldGenSeed(world.getSeed());
    }

    WorldStudioTerrain terrain() {
        return terrain;
    }

    @Override
    public List<Biome> getBiomesToSpawnIn() {
        return terrain.session().spawnBiomes();
    }

    @Override
    public Biome getBiome(BlockPos position) {
        return getBiome(position, Biomes.PLAINS);
    }

    @Override
    public Biome getBiome(BlockPos position, Biome fallback) {
        int[] ids = indexLayer.getInts(position.getX(), position.getZ(), 1, 1);
        return Biome.getBiome(ids[0], fallback);
    }

    @Override
    public Biome[] getBiomesForGeneration(
            @Nullable Biome[] output,
            int areaX,
            int areaZ,
            int areaWidth,
            int areaHeight
    ) {
        IntCache.resetIntCache();
        return resolve(
                output,
                generationLayer.getInts(areaX, areaZ, areaWidth, areaHeight),
                areaWidth * areaHeight
        );
    }

    @Override
    public Biome[] getBiomes(
            @Nullable Biome[] output,
            int areaX,
            int areaZ,
            int areaWidth,
            int areaHeight
    ) {
        return getBiomes(output, areaX, areaZ, areaWidth, areaHeight, true);
    }

    @Override
    public Biome[] getBiomes(
            @Nullable Biome[] output,
            int areaX,
            int areaZ,
            int areaWidth,
            int areaHeight,
            boolean cacheFlag
    ) {
        IntCache.resetIntCache();
        return resolve(
                output,
                indexLayer.getInts(areaX, areaZ, areaWidth, areaHeight),
                areaWidth * areaHeight
        );
    }

    @Override
    public boolean areBiomesViable(
            int centerX,
            int centerZ,
            int radius,
            List<Biome> allowed
    ) {
        int minimumX = (centerX - radius) >> 2;
        int minimumZ = (centerZ - radius) >> 2;
        int maximumX = (centerX + radius) >> 2;
        int maximumZ = (centerZ + radius) >> 2;
        int width = maximumX - minimumX + 1;
        int height = maximumZ - minimumZ + 1;
        IntCache.resetIntCache();
        int[] ids = generationLayer.getInts(minimumX, minimumZ, width, height);
        for (int id : ids) {
            if (!allowed.contains(Biome.getBiome(id, Biomes.PLAINS))) {
                return false;
            }
        }
        return true;
    }

    @Nullable
    @Override
    public BlockPos findBiomePosition(
            int centerX,
            int centerZ,
            int radius,
            List<Biome> allowed,
            Random random
    ) {
        int minimumX = (centerX - radius) >> 2;
        int minimumZ = (centerZ - radius) >> 2;
        int maximumX = (centerX + radius) >> 2;
        int maximumZ = (centerZ + radius) >> 2;
        int width = maximumX - minimumX + 1;
        int height = maximumZ - minimumZ + 1;
        IntCache.resetIntCache();
        int[] ids = generationLayer.getInts(minimumX, minimumZ, width, height);
        BlockPos selected = null;
        int matches = 0;
        for (int index = 0; index < ids.length; index++) {
            int blockX = (minimumX + index % width) << 2;
            int blockZ = (minimumZ + index / width) << 2;
            Biome biome = Biome.getBiome(ids[index], Biomes.PLAINS);
            if (allowed.contains(biome)) {
                if (selected == null || random.nextInt(matches + 1) == 0) {
                    selected = new BlockPos(blockX, 0, blockZ);
                }
                matches++;
            }
        }
        return selected;
    }

    @Override
    public void cleanupCache() {
        terrain.cleanupCache();
    }

    @Override
    public boolean isFixedBiome() {
        return false;
    }

    private static Biome[] resolve(@Nullable Biome[] output, int[] ids, int required) {
        if (output == null || output.length < required) {
            output = new Biome[required];
        }
        for (int index = 0; index < required; index++) {
            output[index] = Biome.getBiome(ids[index], Biomes.PLAINS);
        }
        return output;
    }
}
