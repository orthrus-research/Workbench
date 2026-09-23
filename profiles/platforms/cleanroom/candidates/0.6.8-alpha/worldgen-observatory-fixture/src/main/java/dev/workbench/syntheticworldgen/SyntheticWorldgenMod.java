package dev.workbench.syntheticworldgen;

import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.event.FMLPreInitializationEvent;
import net.minecraftforge.fml.common.registry.GameRegistry;

/**
 * A separate FML mod container. It knows only the standard Forge worldgen API
 * and has no dependency on the observatory mod in this fixture jar.
 */
@Mod(
        modid = SyntheticWorldgenMod.MOD_ID,
        name = SyntheticWorldgenMod.NAME,
        version = SyntheticWorldgenMod.VERSION,
        acceptedMinecraftVersions = "[1.12.2]",
        dependencies = "required-after:cleanroom@[0.6.8-alpha]"
)
public final class SyntheticWorldgenMod {

    public static final String MOD_ID = "workbench_synthetic_worldgen";
    public static final String NAME = "Workbench Independent Synthetic Worldgen";
    public static final String VERSION = "0.1.0";

    @Mod.EventHandler
    public void preInit(FMLPreInitializationEvent event) {
        if (!Boolean.parseBoolean(System.getProperty(
                "workbench.worldgen.observatory.synthetic.enabled",
                "true"
        ))) {
            return;
        }
        GameRegistry.registerWorldGenerator(
                new SyntheticWorldGenerator(),
                SyntheticWorldGenerator.WEIGHT
        );
        MinecraftForge.ORE_GEN_BUS.register(new SyntheticOreEventListener());
    }
}
