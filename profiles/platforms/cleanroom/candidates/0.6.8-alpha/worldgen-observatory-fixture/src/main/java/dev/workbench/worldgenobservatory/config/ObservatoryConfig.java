package dev.workbench.worldgenobservatory.config;

import dev.workbench.worldgenobservatory.WorldgenObservatoryMod;
import net.minecraftforge.common.config.Config;

@Config(modid = WorldgenObservatoryMod.MOD_ID, name = "workbench_worldgen_observatory")
public final class ObservatoryConfig {

    @Config.Comment({
            "Enable stable JSON-line stage, decision, RNG-lane, and checkpoint records.",
            "Disabled is the default: the tracer becomes a no-op and computes no checkpoints."
    })
    public static boolean enabled = false;

    @Config.Comment("Hash all block states in each emitted chunk checkpoint.")
    public static boolean emitBlockStateCheckpoints = true;

    private ObservatoryConfig() {
    }
}
