package dev.workbench.worldgenprototype.world;

import dev.workbench.worldgenprototype.diagnostics.WorldgenJfrEvents;
import net.minecraft.world.gen.layer.GenLayer;
import net.minecraft.world.gen.layer.IntCache;

/** Forge-compatible adapter around the continuous World Studio resolver. */
final class WorldStudioGenLayer extends GenLayer {

    private final WorldStudioTerrain terrain;
    private final int coordinateScale;

    WorldStudioGenLayer(long layerSeed, WorldStudioTerrain terrain, int coordinateScale) {
        super(layerSeed);
        this.terrain = terrain;
        this.coordinateScale = coordinateScale;
    }

    @Override
    public int[] getInts(int areaX, int areaZ, int areaWidth, int areaHeight) {
        int[] output = IntCache.getIntCache(areaWidth * areaHeight);
        WorldStudioTerrain.Session session = terrain.session();
        boolean chunkFastPath = coordinateScale == 1
                && areaWidth == WorldStudioTerrain.CHUNK_SIZE
                && areaHeight == WorldStudioTerrain.CHUNK_SIZE
                && (areaX & 15) == 0
                && (areaZ & 15) == 0;
        WorldgenJfrEvents.BiomeAreaEvent event = WorldgenJfrEvents.beginBiomeArea(
                areaX,
                areaZ,
                areaWidth,
                areaHeight,
                coordinateScale,
                chunkFastPath,
                session.planVersion(),
                session.planHash()
        );
        try {
            if (chunkFastPath) {
                session.sampleChunk(areaX >> 4, areaZ >> 4).copyBiomeIds(output);
                return output;
            }
            for (int localZ = 0; localZ < areaHeight; localZ++) {
                for (int localX = 0; localX < areaWidth; localX++) {
                    int blockX = (areaX + localX) * coordinateScale;
                    int blockZ = (areaZ + localZ) * coordinateScale;
                    output[localX + localZ * areaWidth] = session.biomeIdAt(
                            blockX,
                            blockZ
                    );
                }
            }
            return output;
        } finally {
            WorldgenJfrEvents.endBiomeArea(event);
        }
    }
}
