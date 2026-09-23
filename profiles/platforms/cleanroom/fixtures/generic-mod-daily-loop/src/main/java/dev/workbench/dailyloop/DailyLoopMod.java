package dev.workbench.dailyloop;

import dev.workbench.dailyloop.proxy.CommonProxy;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.SidedProxy;
import net.minecraftforge.fml.common.event.FMLPostInitializationEvent;
import net.minecraftforge.fml.common.event.FMLServerStartingEvent;

@Mod(
    modid = DailyLoopMod.MOD_ID,
    name = DailyLoopMod.NAME,
    version = DailyLoopMod.VERSION,
    acceptedMinecraftVersions = "[1.12.2]"
)
public final class DailyLoopMod {
    public static final String MOD_ID = "workbench_daily_loop";
    public static final String NAME = "Workbench Cleanroom Daily Loop";
    public static final String VERSION = "1.0.0";

    @SidedProxy(
        clientSide = "dev.workbench.dailyloop.client.ClientProxy",
        serverSide = "dev.workbench.dailyloop.proxy.CommonProxy"
    )
    public static CommonProxy proxy;

    @Mod.EventHandler
    public void postInitialize(FMLPostInitializationEvent event) {
        proxy.assertClientLocalization();
    }

    @Mod.EventHandler
    public void serverStarting(FMLServerStartingEvent event) {
        DailyLoopProbe.assertCommonRegistry();
        event.registerServerCommand(new DailyLoopProbeCommand());
    }
}
