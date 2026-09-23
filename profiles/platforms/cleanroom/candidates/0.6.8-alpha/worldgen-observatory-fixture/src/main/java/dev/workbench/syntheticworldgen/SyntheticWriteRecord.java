package dev.workbench.syntheticworldgen;

import java.util.Locale;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.ResourceLocation;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/** Producer-owned, generic attribution evidence independent of any observer. */
final class SyntheticWriteRecord {

    private static final Logger LOGGER = LogManager.getLogger("WorkbenchSyntheticWorldgen");

    private SyntheticWriteRecord() {
    }

    static void emit(
            World world,
            int chunkX,
            int chunkZ,
            BlockPos position,
            IBlockState before,
            IBlockState after,
            boolean changed
    ) {
        if (!SyntheticWorldgenConfig.emitWriteRecord) {
            return;
        }

        LOGGER.info(
                "WORLDGEN_WRITE {{\"schema\":\"workbench.worldgen-write.v1\"," +
                        "\"owner\":\"{}\",\"source\":\"forge.iworldgenerator\"," +
                        "\"world_seed\":{},\"dimension\":{},\"chunk_x\":{},\"chunk_z\":{}," +
                        "\"x\":{},\"y\":{},\"z\":{},\"before\":\"{}\"," +
                        "\"after\":\"{}\",\"changed\":{}}}",
                SyntheticWorldGenerator.class.getName(),
                world.getSeed(),
                world.provider.getDimension(),
                chunkX,
                chunkZ,
                position.getX(),
                position.getY(),
                position.getZ(),
                blockName(before),
                blockName(after),
                changed
        );
    }

    private static String blockName(IBlockState state) {
        ResourceLocation name = state.getBlock().getRegistryName();
        String identity = name == null ? "unregistered" : name.toString();
        return String.format(
                Locale.ROOT,
                "%s@%d",
                identity,
                state.getBlock().getMetaFromState(state)
        );
    }
}
