package dev.workbench.crucible.runtimegraph.client;

import dev.workbench.crucible.runtimegraph.CaptureCoordinator;

import net.minecraft.client.Minecraft;
import net.minecraft.client.network.NetHandlerPlayClient;
import net.minecraft.network.NetworkManager;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.LoaderState;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.gameevent.TickEvent;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.fml.relauncher.SideOnly;

import org.apache.logging.log4j.Logger;

/** Publishes only after one live connection has retained settled HEI state. */
@SideOnly(Side.CLIENT)
public final class ClientCaptureController {
    private final CaptureCoordinator coordinator;
    private final Logger logger;
    private NetworkManager connection;
    private int settledEndTicks;

    private ClientCaptureController(CaptureCoordinator coordinator, Logger logger) {
        this.coordinator = coordinator;
        this.logger = logger;
    }

    public static void install(CaptureCoordinator coordinator, Logger logger) {
        MinecraftForge.EVENT_BUS.register(new ClientCaptureController(coordinator, logger));
    }

    @SubscribeEvent
    public void clientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END
            || !Loader.instance().hasReachedState(LoaderState.AVAILABLE)) return;
        Minecraft minecraft = Minecraft.getMinecraft();
        if (!minecraft.isCallingFromMinecraftThread()) return;
        NetHandlerPlayClient handler = minecraft.getConnection();
        NetworkManager current = handler == null ? null : handler.getNetworkManager();
        if (minecraft.world == null || current == null || !current.isChannelOpen()
            || !ClientJeiCapture.isSettled()) {
            connection = current;
            settledEndTicks = 0;
            return;
        }
        if (connection != current) {
            connection = current;
            settledEndTicks = 0;
            return;
        }
        if (++settledEndTicks < 40) return;
        logger.info("Workbench client presentation state settled; capturing");
        coordinator.captureCheckpoint(CaptureCoordinator.CLIENT_SETTLED_END_TICK);
        if (coordinator.isCommitted()) {
            logger.info("Workbench client presentation capture committed; shutting down client");
            MinecraftForge.EVENT_BUS.unregister(this);
            minecraft.shutdown();
        }
    }
}
