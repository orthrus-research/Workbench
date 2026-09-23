package research.orthrus.axiom.materialhost;

import java.lang.reflect.Field;
import java.nio.file.Path;
import java.util.*;
import net.minecraftforge.fml.common.FMLCommonHandler;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.server.FMLServerHandler;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.common.UsernameCache;
import net.minecraftforge.common.ForgeHooks;
import net.minecraftforge.oredict.OreDictionary;
import net.minecraft.block.Block;

/** Original sided-handler/Forge initialization, not server startup or mod loading. */
public final class NativeForgeInitialization {
    private NativeForgeInitialization() {}

    public static Map<String,Object> initialize(Path home, NativeInitializationTrace trace) throws Exception {
        trace.begin("forge-platform-initialization");
        var common = FMLCommonHandler.instance();
        Field delegate = FMLCommonHandler.class.getDeclaredField("sidedDelegate"); delegate.setAccessible(true);
        if (delegate.get(common) != null || Loader.instance().activeModContainer() != null)
            throw new IllegalStateException("Forge platform initialization requires fresh, unowned native state");
        // Complete original constructor -> beginLoading -> MinecraftForge.initialize.
        // Do not assign a substitute handler or suppress any initialization call.
        var handler = FMLServerHandler.instance();
        Field server = FMLServerHandler.class.getDeclaredField("server"); server.setAccessible(true);
        if (delegate.get(common) != handler || common.getSide() != Side.SERVER || server.get(handler) != null)
            throw new IllegalStateException("Native sided-handler initialization did not establish an unstarted server-side context");
        Field tools = ForgeHooks.class.getDeclaredField("toolInit"); tools.setAccessible(true);
        if (!tools.getBoolean(null)) throw new IllegalStateException("Original Forge tool initialization did not complete");
        Field cache = UsernameCache.class.getDeclaredField("saveFile"); cache.setAccessible(true);
        var file = (java.io.File)cache.get(null);
        // An absent native cache must not require a synthetic file to be created.
        if (!file.getCanonicalFile().toPath().equals(home.toRealPath().resolve("usernamecache.json")))
            throw new IllegalStateException("Native username cache escaped worker home");
        var levels = new LinkedHashMap<String,Integer>();
        for (String name : List.of("minecraft:obsidian", "minecraft:iron_ore", "minecraft:diamond_ore", "minecraft:quartz_ore")) {
            Block block = Block.func_149684_b(name);
            levels.put(name, block.getHarvestLevel(block.func_176223_P()));
        }
        trace.returned("forge-platform-initialization");
        return Map.of("status", "returned", "side", common.getSide().name(),
                "nativeHandler", handler.getClass().getName(), "serverStarted", false,
                "activeOwnerAbsent", Loader.instance().activeModContainer() == null,
                "toolInitializationComplete", true, "harvestLevels", levels,
                "oreDictionaryNames", OreDictionary.getOreNames().length,
                "usernameCacheEntries", UsernameCache.getMap().size(), "modActivationQualified", false);
    }
}
