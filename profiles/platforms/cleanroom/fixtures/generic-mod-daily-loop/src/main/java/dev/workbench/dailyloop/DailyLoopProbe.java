package dev.workbench.dailyloop;

import java.util.concurrent.atomic.AtomicLong;
import net.minecraft.block.Block;
import net.minecraft.item.Item;
import org.apache.logging.log4j.LogManager;

public final class DailyLoopProbe {
    public static final String COMMON_READY_MARKER = "WORKBENCH_DAILY_LOOP_COMMON_READY";
    public static final String CLIENT_READY_MARKER = "WORKBENCH_DAILY_LOOP_CLIENT_RESOURCE_READY";
    private static final AtomicLong MIXIN_OBSERVATIONS = new AtomicLong();

    private DailyLoopProbe() {
    }

    public static String markerFor(String registryId) {
        return COMMON_READY_MARKER + " registry=" + registryId + " fixture=1.0.0";
    }

    public static void assertCommonRegistry() {
        Block block = Block.REGISTRY.getObject(DailyLoopContent.PROBE_BLOCK_ID);
        Item item = Item.REGISTRY.getObject(DailyLoopContent.PROBE_BLOCK_ID);
        if (block != DailyLoopContent.PROBE_BLOCK || item == null ||
            item.getRegistryName() == null ||
            !DailyLoopContent.PROBE_BLOCK_ID.equals(item.getRegistryName())) {
            throw new IllegalStateException(
                "daily-loop common registry object is absent or has drifted"
            );
        }
        LogManager.getLogger(DailyLoopMod.MOD_ID).info(
            markerFor(DailyLoopContent.PROBE_BLOCK_ID.toString())
        );
    }

    public static void observeTranslationKey(String translationKey) {
        if (("tile." + DailyLoopMod.MOD_ID + ".probe_block").equals(translationKey)) {
            MIXIN_OBSERVATIONS.incrementAndGet();
        }
    }

    public static long mixinObservationCount() {
        return MIXIN_OBSERVATIONS.get();
    }
}
