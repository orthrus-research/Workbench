package dev.workbench.syntheticworldgen;

import java.util.Random;
import net.minecraft.block.state.IBlockState;
import net.minecraft.init.Blocks;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.world.chunk.IChunkProvider;
import net.minecraft.world.gen.IChunkGenerator;
import net.minecraftforge.fml.common.IWorldGenerator;

/**
 * An intentionally unrelated external decorator. Its only integration point
 * is GameRegistry.registerWorldGenerator / IWorldGenerator.
 */
public final class SyntheticWorldGenerator implements IWorldGenerator {

    public static final int WEIGHT = 100;
    private static final int CHUNK_MASK = 7;

    @Override
    public void generate(
            Random random,
            int chunkX,
            int chunkZ,
            World world,
            IChunkGenerator chunkGenerator,
            IChunkProvider chunkProvider
    ) {
        if (!SyntheticWorldgenConfig.enabled
                || world.provider.getDimension() != 0
                || (chunkX & CHUNK_MASK) != 0
                || (chunkZ & CHUNK_MASK) != 0) {
            return;
        }

        int blockX = (chunkX << 4) + 4 + random.nextInt(8);
        int blockZ = (chunkZ << 4) + 4 + random.nextInt(8);
        BlockPos position = world.getHeight(new BlockPos(blockX, 0, blockZ));
        IBlockState before = world.getBlockState(position);
        IBlockState marker = Blocks.GOLD_BLOCK.getDefaultState();
        boolean changed = world.setBlockState(position, marker, 2);

        SyntheticWriteRecord.emit(
                world,
                chunkX,
                chunkZ,
                position,
                before,
                marker,
                changed
        );
    }
}
