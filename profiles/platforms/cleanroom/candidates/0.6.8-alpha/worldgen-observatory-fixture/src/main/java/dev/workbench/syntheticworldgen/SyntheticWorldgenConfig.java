package dev.workbench.syntheticworldgen;

import net.minecraftforge.common.config.Config;

@Config(modid = SyntheticWorldgenMod.MOD_ID, name = "workbench_synthetic_worldgen")
public final class SyntheticWorldgenConfig {

    @Config.Comment("Enable the independent marker-block IWorldGenerator.")
    public static boolean enabled = true;

    @Config.Comment({
            "Emit a producer-owned generic write record.",
            "This logger has no dependency on or call into the observatory."
    })
    public static boolean emitWriteRecord = true;

    private SyntheticWorldgenConfig() {
    }
}
