package dev.workbench.crucible.forgerecipes;

import java.util.List;
import java.util.Map;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.event.FMLPreInitializationEvent;
import net.minecraftforge.fml.common.event.FMLServerStartedEvent;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.gameevent.TickEvent;

/** Declared original capability preparation and observation, then ordinary server shutdown. */
@Mod(modid="workbench_forge_recipe_observer", name="Workbench Forge Recipe Observer", version="0.1.0", acceptableRemoteVersions="*")
public final class ForgeRecipeObserverMod {
    private boolean armed, started, attempted;
    @Mod.EventHandler public void preInit(FMLPreInitializationEvent event) {
        armed=ForgeCapturePublisher.isEnabled();
        if(armed) {
            Object handler=ForgeReflection.call(ForgeReflection.type("net.minecraftforge.fml.common.FMLCommonHandler"),"instance");
            if(!"SERVER".equals(ForgeReflection.call(handler,"getSide").toString()))
                throw new IllegalStateException("Forge recipe observation requires the physical SERVER side");
            MinecraftForge.EVENT_BUS.register(this);
        }
    }
    @Mod.EventHandler public void serverStarted(FMLServerStartedEvent event) {started=true;}
    @SubscribeEvent public void tick(TickEvent.ServerTickEvent event) {
        if(!armed || !started || attempted || event.phase!=TickEvent.Phase.END)return;
        attempted=true;
        try {
            Object handler=ForgeReflection.call(ForgeReflection.type("net.minecraftforge.fml.common.FMLCommonHandler"),"instance");
            Object server=ForgeReflection.call(handler,"getMinecraftServerInstance");
            if(!Boolean.TRUE.equals(ForgeReflection.callNames(server,new String[]{"isDedicatedServer","func_71262_S"})))
                throw new IllegalStateException("Forge recipe observation requires a dedicated server instance");
            ForgeCapturePublisher.requirePreparation();
            ForgeCapturePublisher.retainPreparation(ForgeRecipeSnapshot.prepareCapabilities());
            Map<String,List<Map<String,Object>>> first=ForgeRecipeSnapshot.capture();
            Map<String,List<Map<String,Object>>> second=ForgeRecipeSnapshot.capture();
            ForgeCapturePublisher.publish(first,second,ForgeReflection.row("checkpoint_id","post-start-end-tick",
                "physical_side","dedicated_server","server_started",true,"actual_event","ServerTickEvent.END",
                "observation_preparation_policy",ForgeCapabilityPreparation.POLICY));
        } catch(Throwable failure) {
            ForgeCapturePublisher.failure(failure);
        } finally {
            try {
                Object handler=ForgeReflection.call(ForgeReflection.type("net.minecraftforge.fml.common.FMLCommonHandler"),"instance");
                Object server=ForgeReflection.call(handler,"getMinecraftServerInstance");
                ForgeReflection.callNames(server,new String[]{"initiateShutdown","func_71263_m"});
                System.out.println("[WORKBENCH-FORGE-OBSERVATION] normal shutdown requested");
            } catch(Throwable failure) {
                ForgeCapturePublisher.failure(failure);
                System.err.println("[WORKBENCH-FORGE-OBSERVATION] shutdown request failed; Core must close the process");
            }
        }
    }
}
