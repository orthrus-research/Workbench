package research.orthrus.axiom.materialhost;

import com.cleanroommc.common.CleanroomEnvironment;
import com.cleanroommc.common.CleanroomContainer;
import com.cleanroommc.common.ConfigAnytimeContainer;
import net.minecraft.launchwrapper.Launch;
import net.minecraft.launchwrapper.LaunchClassLoader;
import net.minecraftforge.common.ForgeEarlyConfig;
import net.minecraftforge.common.ForgeVersion;
import net.minecraftforge.common.config.ConfigManager;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.ModContainer;
import net.minecraftforge.fml.relauncher.FMLInjectionData;
import net.minecraftforge.fml.relauncher.IFMLLoadingPlugin;
import zone.rong.mixinbooter.MixinBooterModContainer;
import java.io.File;
import java.lang.reflect.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Original early configuration/version initialization; not whole-launcher setup. */
public final class NativePlatformBootstrap {
    private static Map<String,Object> observation;
    private NativePlatformBootstrap() {}

    public static Map<String,Object> initialize() throws Exception {
        if (observation != null || FMLInjectionData.data()[6] != null || !FMLInjectionData.containers.isEmpty())
            throw new IllegalStateException("Native platform initialization requires fresh state");
        Path home = Launch.minecraftHome.toPath().toRealPath();
        Path early = home.resolve("config/forge_early.cfg");
        byte[] before = Files.isRegularFile(early) ? Files.readAllBytes(early) : null;
        Method build = FMLInjectionData.class.getDeclaredMethod("build", File.class, LaunchClassLoader.class);
        build.setAccessible(true);
        // Complete original method: native version values and both configuration
        // registrations. ModListConfig is skipped by original SERVER-side logic,
        // not by a host-written condition or a removed native call.
        build.invoke(null, home.toFile(), Launch.classLoader);
        Object[] data = FMLInjectionData.data();
        Loader.injectData(data);
        if (!((File)data[6]).toPath().equals(home) || data[7] != FMLInjectionData.containers
                || !ForgeVersion.mcVersion.equals(data[4]) || !ForgeVersion.mcpVersion.equals(data[5]))
            throw new IllegalStateException("Native FML injection data differs");
        if (!Arrays.asList(ConfigManager.getModConfigClasses(ForgeVersion.MOD_ID)).contains(ForgeEarlyConfig.class))
            throw new IllegalStateException("Original early configuration was not registered");
        var result = new LinkedHashMap<String,Object>();
        result.put("status", "returned");
        result.put("method", "original FMLInjectionData.build -> ConfigManager.register; Loader.injectData");
        result.put("side", CleanroomEnvironment.side().name());
        result.put("minecraftVersion", data[4]); result.put("mcpVersion", data[5]);
        result.put("forgeVersion", String.join(".", Arrays.copyOfRange(data,0,4,String[].class)));
        result.put("inputPresent", before != null);
        if (before != null) result.put("inputSha256", digest(before));
        result.put("workerFileSha256", digest(Files.readAllBytes(early)));
        result.put("customBuiltInVersions", ForgeEarlyConfig.CUSTOM_BUILT_IN_MOD_VERSION);
        result.put("mixinBooterVersion", ForgeEarlyConfig.MIXIN_BOOTER_VERSION);
        result.put("configAnytimeVersion", ForgeEarlyConfig.CONFIG_ANY_TIME_VERSION);
        result.put("loadingPluginBlacklist", List.of(ForgeEarlyConfig.LOADING_PLUGIN_BLACKLIST));
        // These are original constructors, not host-created DummyModContainer
        // metadata. This bounded prefix does NOT populate Loader.namedMods or
        // claim the discoverer's full built-in/injected roster has been selected.
        result.put("builtInContainers", List.of(container(new CleanroomContainer()),
                container(new MixinBooterModContainer()), container(new ConfigAnytimeContainer())));
        result.put("ivToolkit", inspectIvToolkit());
        // The previous contract observation remains a pre-injection snapshot.
        // A separately scoped native transition is performed only when the full
        // original IVToolkit artifact is supplied, never from metadata projection.
        result.put("coremodComposition", NativeCoremodComposition.initialize());
        result.put("containerScope", "original-constructors-and-ivtoolkit-plugin-contract-not-loader-activation");
        result.put("fullCoremodInjectionExecuted", false);
        result.put("launcherCompositionQualified", false);
        observation = Collections.unmodifiableMap(result);
        return observation;
    }

    private static Map<String,Object> container(ModContainer container) {
        var version = container.getProcessedVersion();
        return Map.of("class", container.getClass().getName(), "id", container.getModId(),
                "version", container.getVersion(), "processedVersion", version.getVersionString(),
                "processedRange", version.getRangeString());
    }

    private static Map<String,Object> inspectIvToolkit() throws Exception {
        // Foundation otherwise delegates this package to its parent. Keep the
        // original plugin and its FML interfaces in the same native class space.
        Method inclusion = Launch.classLoader.getClass().getSuperclass().getDeclaredMethod("addClassLoaderInclusion", String.class);
        inclusion.setAccessible(true);
        inclusion.invoke(Launch.classLoader, "ivorius.ivtoolkit.");
        Class<?> type = Class.forName("ivorius.ivtoolkit.IvToolkitLoadingPlugin", true, Launch.classLoader);
        IFMLLoadingPlugin plugin = (IFMLLoadingPlugin) type.getConstructor().newInstance();
        // The complete original method runs JavaCompatibility.requireJava8(true).
        // It accepts newer Java; do not patch it into a no-op or a Java-8 equality test.
        String[] transformers = plugin.getASMTransformerClass();
        String setup = plugin.getSetupClass(), access = plugin.getAccessTransformerClass();
        String containerName = plugin.getModContainerClass();
        if (transformers == null || transformers.length != 0 || setup != null || access != null
                || !"ivorius.ivtoolkit.IvToolkitCoreContainer".equals(containerName))
            throw new IllegalStateException("IVToolkit loading-plugin contract changed; native injection is required");
        var actual = (ModContainer) Class.forName(containerName, true, Launch.classLoader).getConstructor().newInstance();
        return Map.of("plugin", type.getName(), "container", container(actual),
                "asmTransformers", List.of(transformers), "setupClassPresent", false,
                "accessTransformerPresent", false, "nativeJavaCheckReturned", true,
                "injectDataExecuted", false, "registeredWithCoreModManager", false);
    }

    public static void verifyHome(Path home) throws Exception {
        if (observation == null || !home.toRealPath().equals(((File)FMLInjectionData.data()[6]).toPath()))
            throw new IllegalStateException("Native platform home was not initialized for this program");
    }

    private static String digest(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }
}
