package dev.workbench.crucible.runtimegraph;

import dev.workbench.crucible.runtimegraph.worldgen.RealizedWorldObservationTrace;
import dev.workbench.crucible.runtimegraph.reload.ProgramReloadExperiment;

import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.event.FMLPostInitializationEvent;
import net.minecraftforge.fml.common.event.FMLPreInitializationEvent;
import net.minecraftforge.fml.common.event.FMLServerStartedEvent;
import net.minecraftforge.fml.common.event.FMLServerStoppingEvent;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.gameevent.TickEvent;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/** Armed-only side-separated entry point for the categorical producer. */
@Mod(
    modid = UltimateRuntimeGraphProducerMod.MOD_ID,
    name = "Workbench Ultimate Runtime Graph Producer",
    version = "0.16.0",
    acceptableRemoteVersions = "*",
    dependencies = "required-after:appliedenergistics2;required-after:betterquesting;required-after:biomesoplenty;required-after:cavegenerator;required-after:gregtech;required-after:groovyscript;required-after:industrialrenewal;required-after:jei;required-after:opencomputers;required-after:pyrotech;required-after:reccomplex;required-after:rftools;required-after:rsgauges;required-after:supercritical;required-after:susy;required-after:techguns"
)
public final class UltimateRuntimeGraphProducerMod {
    public static final String MOD_ID = "workbench_runtime_graph";
    private static final String SHUTDOWN_COMPATIBILITY_ID =
        "universal-mod-core-clean-shutdown-v1";
    private static final Logger LOGGER = LogManager.getLogger(MOD_ID);

    private CaptureConfiguration configuration;
    private CaptureCoordinator coordinator;
    private UniversalModCoreShutdownCompatibility shutdownCompatibility;
    private boolean serverStarted;
    private boolean finalTickObserved;
    private boolean reloadExperimentCompleted;

    @Mod.EventHandler
    public void preInit(FMLPreInitializationEvent event) {
        configuration = CaptureConfiguration.read();
        if (configuration.isCompatibilityOnly()) {
            installShutdownCompatibility();
            LOGGER.info(
                "Workbench shutdown compatibility armed as {} with manifest {} and launch {}",
                configuration.getShutdownCompatibilityId(),
                configuration.getShutdownCompatibilityManifestSha256(),
                configuration.getShutdownCompatibilityLaunchSha256()
            );
            return;
        }
        if (!configuration.isEnabled()) {
            LOGGER.info("Workbench runtime graph capture is disabled");
            return;
        }
        coordinator = new CaptureCoordinator(configuration, LOGGER);
        if (configuration.isClientPresentationCapture()) {
            installClientController();
            LOGGER.info(
                "Workbench client presentation capture armed as {}",
                configuration.getCaptureId()
            );
            return;
        }
        installShutdownCompatibility();
        RealizedWorldObservationTrace.global().markRegistered();
        MinecraftForge.EVENT_BUS.register(RealizedWorldObservationTrace.global());
        MinecraftForge.EVENT_BUS.register(this);
        LOGGER.info("Workbench runtime graph capture armed as {}", configuration.getCaptureId());
    }

    private void installShutdownCompatibility() {
        if (configuration.isCompatibilityOnly()
            && !SHUTDOWN_COMPATIBILITY_ID.equals(
                configuration.getShutdownCompatibilityId()
            )) {
            throw new IllegalArgumentException(
                "runtime graph shutdown compatibility identity is unsupported"
            );
        }
        shutdownCompatibility = new UniversalModCoreShutdownCompatibility(LOGGER);
        MinecraftForge.EVENT_BUS.register(shutdownCompatibility);
    }

    @Mod.EventHandler
    public void postInit(FMLPostInitializationEvent event) {
        if (coordinator != null) {
            coordinator.captureCheckpoint(CaptureCoordinator.MATERIAL_FROZEN);
        }
    }

    @Mod.EventHandler
    public void serverStarted(FMLServerStartedEvent event) {
        if (coordinator == null || configuration.isClientPresentationCapture()) return;
        coordinator.captureCheckpoint(CaptureCoordinator.SERVER_STARTED);
        serverStarted = true;
    }

    @Mod.EventHandler
    public void serverStopping(FMLServerStoppingEvent event) {
        if (shutdownCompatibility != null) shutdownCompatibility.arm();
    }

    @SubscribeEvent
    public void serverTick(TickEvent.ServerTickEvent event) {
        if (coordinator == null || configuration.isClientPresentationCapture()
            || !serverStarted || finalTickObserved
            || event.phase != TickEvent.Phase.END) return;
        if (configuration.isProgramReloadExperiment() && !reloadExperimentCompleted) {
            LOGGER.info("Workbench runtime graph executing controlled program reload");
            ProgramReloadExperiment.execute();
            reloadExperimentCompleted = true;
            LOGGER.info("Workbench runtime graph controlled program reload completed");
            return;
        }
        finalTickObserved = true;
        coordinator.captureCheckpoint(CaptureCoordinator.POST_START_END_TICK);
        if (coordinator.isCommitted()) MinecraftForge.EVENT_BUS.unregister(this);
    }

    private void installClientController() {
        try {
            Class<?> controller = Class.forName(
                "dev.workbench.crucible.runtimegraph.client.ClientCaptureController"
            );
            controller.getMethod(
                "install",
                CaptureCoordinator.class,
                org.apache.logging.log4j.Logger.class
            ).invoke(null, coordinator, LOGGER);
        } catch (ReflectiveOperationException failure) {
            throw new IllegalStateException("cannot install client presentation controller", failure);
        }
    }
}
