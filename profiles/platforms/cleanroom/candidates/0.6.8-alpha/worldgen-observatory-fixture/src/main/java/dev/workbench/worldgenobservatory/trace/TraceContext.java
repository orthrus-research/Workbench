package dev.workbench.worldgenobservatory.trace;

import net.minecraft.world.World;

public final class TraceContext {

    private final long worldSeed;
    private final int dimension;
    private final int chunkX;
    private final int chunkZ;

    private TraceContext(long worldSeed, int dimension, int chunkX, int chunkZ) {
        this.worldSeed = worldSeed;
        this.dimension = dimension;
        this.chunkX = chunkX;
        this.chunkZ = chunkZ;
    }

    public static TraceContext of(World world, int chunkX, int chunkZ) {
        return new TraceContext(world.getSeed(), world.provider.getDimension(), chunkX, chunkZ);
    }

    long worldSeed() {
        return worldSeed;
    }

    int dimension() {
        return dimension;
    }

    int chunkX() {
        return chunkX;
    }

    int chunkZ() {
        return chunkZ;
    }
}
