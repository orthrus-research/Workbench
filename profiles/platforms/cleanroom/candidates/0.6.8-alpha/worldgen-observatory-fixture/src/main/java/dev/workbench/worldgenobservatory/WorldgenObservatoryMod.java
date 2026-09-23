package dev.workbench.worldgenobservatory;

import dev.workbench.worldgenobservatory.fixture.DedicatedServerFixtureDriver;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import dev.workbench.worldgenobservatory.world.FinalCheckpointWorldGenerator;
import dev.workbench.worldgenobservatory.world.ObservatoryWorldType;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.WorldType;
import net.minecraftforge.fml.common.FMLCommonHandler;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.event.FMLPreInitializationEvent;
import net.minecraftforge.fml.common.event.FMLServerStartedEvent;
import net.minecraftforge.fml.common.event.FMLServerStoppingEvent;
import net.minecraftforge.fml.common.registry.GameRegistry;

@Mod(
        modid = WorldgenObservatoryMod.MOD_ID,
        name = WorldgenObservatoryMod.NAME,
        version = WorldgenObservatoryMod.VERSION,
        acceptedMinecraftVersions = "[1.12.2]",
        dependencies = "required-after:cleanroom@[0.6.8-alpha]"
)
public final class WorldgenObservatoryMod {

    public static final String MOD_ID = "workbench_worldgen_observatory";
    public static final String NAME = "Workbench Worldgen Observatory Fixture";
    public static final String VERSION = "0.1.0";

    public static final WorldType WORLD_TYPE = new ObservatoryWorldType();

    @Mod.EventHandler
    public void preInit(FMLPreInitializationEvent event) {
        // This observer is itself an ordinary Forge world generator. Its high
        // weight places its read-only checkpoint after normal fixture writers.
        GameRegistry.registerWorldGenerator(
                new FinalCheckpointWorldGenerator(),
                FinalCheckpointWorldGenerator.WEIGHT
        );
    }

    @Mod.EventHandler
    public void serverStarted(FMLServerStartedEvent event) {
        if (Boolean.parseBoolean(System.getProperty(
                ProbeRuntime.ITERATION_AUTO_ENABLE_PROPERTY,
                "false"
        )) && !ProbeRuntime.armForWorldgenIteration()) {
            throw new IllegalStateException(
                    "Worldgen Iteration requested an Observatory capture that could not arm"
            );
        }
        if (!DedicatedServerFixtureDriver.enabled()) {
            return;
        }
        MinecraftServer server = FMLCommonHandler.instance().getMinecraftServerInstance();
        if (server == null) {
            throw new IllegalStateException("Started server event has no MinecraftServer instance");
        }
        DedicatedServerFixtureDriver.run(server);
    }

    @Mod.EventHandler
    public void serverStopping(FMLServerStoppingEvent event) {
        ProbeRuntime.stopWorldgenIterationCapture();
    }
}
