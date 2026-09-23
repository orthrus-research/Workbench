package dev.workbench.worldgenprototype.world;

import net.minecraft.world.World;
import net.minecraft.world.WorldType;
import net.minecraft.world.biome.BiomeProvider;
import net.minecraft.world.gen.IChunkGenerator;

public final class PrototypeWorldType extends WorldType {

    public static final String WORLD_TYPE_NAME = "wb_proto";

    public PrototypeWorldType() {
        super(WORLD_TYPE_NAME);
    }

    @Override
    public BiomeProvider getBiomeProvider(World world) {
        return new WorldStudioBiomeProvider(world);
    }

    @Override
    public IChunkGenerator getChunkGenerator(World world, String generatorOptions) {
        return new PrototypeChunkGenerator(world);
    }

    @Override
    public int getMinimumSpawnHeight(World world) {
        return WorldStudioTerrain.SEA_LEVEL + 1;
    }
}
