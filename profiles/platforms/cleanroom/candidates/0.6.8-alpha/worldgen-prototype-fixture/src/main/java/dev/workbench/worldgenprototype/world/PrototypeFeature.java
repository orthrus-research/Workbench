package dev.workbench.worldgenprototype.world;

import dev.workbench.worldgenprototype.diagnostics.WorldgenCausalTrace;
import java.util.Random;
import net.minecraft.block.state.IBlockState;
import net.minecraft.init.Blocks;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.world.chunk.ChunkPrimer;

/** One deliberately obvious edit point for short worldgen experiments. */
public final class PrototypeFeature {

    public enum Stage {
        GENERATE_BASE("generate.base"),
        POPULATE_CUSTOM("populate.custom"),
        DECORATE_CUSTOM("decorate.custom");

        private final String id;

        Stage(String id) {
            this.id = id;
        }

        public String id() {
            return id;
        }
    }

    public static final Stage ACTIVE_STAGE = Stage.POPULATE_CUSTOM;

    public boolean generateBase(
            ChunkPrimer primer,
            Random random,
            int chunkX,
            int chunkZ,
            WorldStudioTerrain.ChunkSample terrain
    ) {
        if (ACTIVE_STAGE == Stage.GENERATE_BASE) {
            primer.setBlockState(
                    8,
                    terrain.surfaceHeight(8, 8) + 1,
                    8,
                    Blocks.MOSSY_COBBLESTONE.getDefaultState()
            );
            return true;
        }
        return false;
    }

    public boolean populateCustom(World world, Random random, int chunkX, int chunkZ) {
        if (ACTIVE_STAGE == Stage.POPULATE_CUSTOM) {
            return placeBoulder(world, random, chunkX, chunkZ);
        }
        return false;
    }

    public boolean decorateCustom(World world, Random random, int chunkX, int chunkZ) {
        if (ACTIVE_STAGE == Stage.DECORATE_CUSTOM) {
            return placeBoulder(world, random, chunkX, chunkZ);
        }
        return false;
    }

    private static boolean placeBoulder(World world, Random random, int chunkX, int chunkZ) {
        int eligibilityDraw = random.nextInt(10);
        if (eligibilityDraw != 0) {
            WorldgenCausalTrace.rejected(
                    world.getSeed(),
                    world.provider.getDimension(),
                    chunkX,
                    chunkZ,
                    eligibilityDraw
            );
            return false;
        }
        int xDraw = random.nextInt(8);
        int zDraw = random.nextInt(8);
        int blockX = chunkX * 16 + 4 + xDraw;
        int blockZ = chunkZ * 16 + 4 + zDraw;
        BlockPos base = world.getHeight(new BlockPos(blockX, 0, blockZ));
        BlockPos east = base.east();
        BlockPos top = base.up();
        IBlockState mossy = Blocks.MOSSY_COBBLESTONE.getDefaultState();
        IBlockState cobblestone = Blocks.COBBLESTONE.getDefaultState();

        IBlockState baseBefore = world.getBlockState(base);
        boolean baseResult = world.setBlockState(base, mossy, 2);
        IBlockState baseAfter = world.getBlockState(base);
        IBlockState eastBefore = world.getBlockState(east);
        boolean eastResult = world.setBlockState(east, cobblestone, 2);
        IBlockState eastAfter = world.getBlockState(east);
        IBlockState topBefore = world.getBlockState(top);
        boolean optionalTopDraw = random.nextBoolean();
        boolean topResult = false;
        if (optionalTopDraw) {
            topResult = world.setBlockState(top, cobblestone, 2);
        }
        IBlockState topAfter = world.getBlockState(top);

        WorldgenCausalTrace.placed(
                world.getSeed(),
                world.provider.getDimension(),
                chunkX,
                chunkZ,
                eligibilityDraw,
                xDraw,
                zDraw,
                optionalTopDraw,
                base,
                WorldgenCausalTrace.write(
                        "mandatory-base",
                        base,
                        baseBefore,
                        mossy,
                        baseAfter,
                        true,
                        baseResult,
                        baseResult ? "written" : "write-returned-false"
                ),
                WorldgenCausalTrace.write(
                        "mandatory-east",
                        east,
                        eastBefore,
                        cobblestone,
                        eastAfter,
                        true,
                        eastResult,
                        eastResult ? "written" : "write-returned-false"
                ),
                WorldgenCausalTrace.write(
                        "optional-top",
                        top,
                        topBefore,
                        cobblestone,
                        topAfter,
                        optionalTopDraw,
                        topResult,
                        optionalTopDraw
                                ? (topResult ? "written" : "write-returned-false")
                                : "rejected-by-optional-draw"
                )
        );
        return true;
    }
}
