package research.orthrus.axiom.materialhost;

import com.cleanroommc.common.CleanroomEnvironment;
import com.google.gson.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraft.launchwrapper.LaunchClassLoader;
import net.minecraftforge.common.ForgeEarlyConfig;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.launcher.FMLServerTweaker;
import net.minecraftforge.fml.common.launcher.FMLTweaker;
import net.minecraftforge.fml.relauncher.CoreModManager;
import net.minecraftforge.fml.relauncher.FMLInjectionData;
import top.outlands.foundation.TransformerDelegate;
import java.io.File;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.jar.JarFile;

/** Bounded original coremod registration and injection, not a parallel mod loader. */
public final class NativeCoremodComposition {
    public static final String SCOPE = "original-ivtoolkit-coremod-injection-not-complete-launcher";
    private static final String PLUGIN = "ivorius.ivtoolkit.IvToolkitLoadingPlugin";
    private static final String CONTAINER = "ivorius.ivtoolkit.IvToolkitCoreContainer";
    private static Map<String,Object> observation;
    private static ModContainer injectedContainer;
    private NativeCoremodComposition() {}

    public static Map<String,Object> initialize() throws Exception {
        if (observation != null) throw new IllegalStateException("Native coremod prefix cannot be repeated");
        JsonObject inventory;
        try (var input = Launch.classLoader.getResourceAsStream("axiom-addon-inventory.json")) {
            if (input == null) return deferred("no-bound-artifact-inventory");
            byte[] raw = input.readNBytes((4 << 20) + 1);
            if (raw.length > 4 << 20) throw new IllegalStateException("Native coremod inventory exceeds bound");
            inventory = JsonParser.parseString(new String(raw, StandardCharsets.UTF_8)).getAsJsonObject();
        }
        if (!"axiom.native-addon-inventory.v6".equals(inventory.get("schema").getAsString())
                || !"server".equals(inventory.get("side").getAsString())
                || inventory.get("activationQualified").getAsBoolean())
            throw new IllegalStateException("Native coremod inventory identity differs");
        if (!"complete-artifacts".equals(inventory.get("candidateInputScope").getAsString()))
            return deferred("original-complete-ivtoolkit-artifact-required");
        var selected = new ArrayList<JsonObject>();
        for (var input : inventory.getAsJsonArray("artifacts")) {
            var artifact = input.getAsJsonObject();
            if ("mods/ivtoolkit.pw.toml".equals(artifact.get("descriptor").getAsString())) selected.add(artifact);
        }
        if (selected.size() != 1) throw new IllegalStateException("Exactly one profile-bound IVToolkit artifact is required");
        JsonObject artifact = selected.getFirst();
        Path jar = NativeAddonLibraryCandidates.inputPath(artifact);
        var identity = artifact.getAsJsonObject("candidateInput");
        if (!identity.get("sha256").getAsString().equals(artifact.get("sha256").getAsString())
                || identity.get("size").getAsLong() != artifact.get("size").getAsLong()
                || !Files.isRegularFile(jar) || Files.size(jar) != artifact.get("size").getAsLong()
                || Files.size(jar) > 16L << 20 || !digest(Files.readAllBytes(jar)).equals(artifact.get("sha256").getAsString()))
            throw new IllegalStateException("Original IVToolkit artifact bytes differ");
        try (var original = new JarFile(jar.toFile())) {
            var manifest = original.getManifest();
            if (manifest == null || manifest.getMainAttributes().size() != 2
                    || !"1.0".equals(manifest.getMainAttributes().getValue("Manifest-Version"))
                    || !PLUGIN.equals(manifest.getMainAttributes().getValue("FMLCorePlugin")))
                throw new IllegalStateException("IVToolkit manifest is outside the bounded native injection scope");
            // The original classes are supplied by the source-bound API image.
            // Verify their bytes against this actual complete artifact; do not
            // report that the whole target launcher class space was composed.
            for (String name : List.of(PLUGIN, CONTAINER)) {
                var entry = original.getJarEntry(name.replace('.', '/') + ".class");
                if (entry == null || entry.getSize() > 1 << 20)
                    throw new IllegalStateException("Original IVToolkit plugin/container is missing");
                try (var stream = original.getInputStream(entry)) {
                    if (!Arrays.equals(stream.readAllBytes(), Launch.classLoader.getClassBytes(name)))
                        throw new IllegalStateException("Executable IVToolkit class differs from the complete artifact: " + name);
                }
                if (entry.getCertificates() != null && entry.getCertificates().length != 0)
                    throw new IllegalStateException("Signed IVToolkit requires original target code-source composition");
            }
        }
        // This is a scope-admission guard, not a replacement implementation of
        // CleanroomModDiscoverer.discoverCoreMod or its blacklist predicate. A
        // blacklisted required plugin remains incomplete and is never injected.
        if (Arrays.asList(ForgeEarlyConfig.LOADING_PLUGIN_BLACKLIST).contains(PLUGIN)) {
            NativeBoundary.unsupported("material-context.ivtoolkit-coremod-blacklisted");
            return deferred("saved-blacklist-requires-native-discovery-decision");
        }
        if (read("loadPlugins") != null || read("mcDir") != null || read("tweaker") != null
                || !CoreModManager.getTransformers().isEmpty() || !CoreModManager.getAccessTransformers().isEmpty()
                || CoreModManager.isCoreModLoaded(PLUGIN) || !FMLInjectionData.containers.isEmpty())
            throw new IllegalStateException("Original coremod initialization requires fresh native state");
        Object pending = Launch.blackboard.get("TweakClasses");
        if (!(pending instanceof List<?> tweaks) || !tweaks.isEmpty())
            throw new IllegalStateException("Original coremod initialization requires a fresh cascading-tweak queue");
        var tweaker = new FMLServerTweaker();
        // NativeCoremodPrefix adds a distinct method to the exact original class.
        // It retains the complete contiguous bytecode prefix through loadPlugins,
        // including native side/environment decisions and PatchingTransformer.
        Method initialize = CoreModManager.class.getDeclaredMethod(NativeCoremodPrefix.METHOD,
                File.class, LaunchClassLoader.class, FMLTweaker.class);
        initialize.invoke(null, Launch.minecraftHome, Launch.classLoader, tweaker);
        Object plugins = read("loadPlugins");
        if (!(plugins instanceof List<?> list) || !list.isEmpty() || read("tweaker") != tweaker
                || !Launch.minecraftHome.equals(read("mcDir"))
                || !Objects.equals(read("deobfuscatedEnvironment"), CleanroomEnvironment.isDev())
                || !"SERVER".equals(CleanroomEnvironment.side().name()))
            throw new IllegalStateException("Original coremod environment prefix did not establish its native inputs");
        List<String> expectedTweaks = List.of("net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker",
                "org.spongepowered.asm.launch.MixinTweaker");
        if (Launch.blackboard.get("TweakClasses") != pending || !tweaks.equals(expectedTweaks)
                || TransformerDelegate.getTransformers().stream().filter(t ->
                "net.minecraftforge.fml.common.asm.transformers.PatchingTransformer".equals(t.getClass().getName())).count() != 1)
            throw new IllegalStateException("Original coremod native prefix side effects differ");
        if (!CoreModManager.loadCoreModFromDiscoveredJar(Launch.classLoader, PLUGIN, jar.toFile())
                || !CoreModManager.isCoreModLoaded(PLUGIN) || list.size() != 1
                || !(list.getFirst() instanceof CoreModManager.FMLPluginWrapper wrapper)
                || !PLUGIN.equals(wrapper.coreModInstance.getClass().getName())
                || !jar.toFile().equals(wrapper.location) || !wrapper.predepends.isEmpty())
            throw new IllegalStateException("Original IVToolkit coremod registration did not complete");
        // Original wrapper owns getASMTransformerClass, injectData, setup and the
        // injection list. No member is set to imitate a completed native call.
        wrapper.injectIntoClassLoader(Launch.classLoader);
        if (!FMLInjectionData.containers.equals(List.of(CONTAINER))
                || !CoreModManager.getAccessTransformers().isEmpty())
            throw new IllegalStateException("Original IVToolkit container injection differs");
        var actual = (ModContainer) Class.forName(CONTAINER, true, Loader.instance().getModClassLoader()).getConstructor().newInstance();
        // Same original wrapping expression as CleanroomModDiscoverer.identifyMods.
        injectedContainer = new InjectedModContainer(actual, actual.getSource());
        var result = new LinkedHashMap<String,Object>();
        result.put("status", "injected"); result.put("scope", SCOPE);
        result.put("artifact", artifact.get("outputPath").getAsString());
        result.put("artifactSha256", artifact.get("sha256").getAsString());
        result.put("classBytesMatchedOriginalArtifact", true);
        result.put("nativePluginRegistered", true); result.put("injectDataExecuted", true);
        result.put("injectedContainerNames", List.copyOf(FMLInjectionData.containers));
        result.put("container", Map.of("id", injectedContainer.getModId(), "class", actual.getClass().getName(),
                "wrapper", injectedContainer.getClass().getName(), "version", injectedContainer.getVersion(),
                "source", injectedContainer.getSource().getPath(), "nativeNullSourceFallback", actual.getSource() == null));
        result.put("initializationPrefix", Map.of("owner", NativeCoremodPrefix.TARGET,
                "method", NativeCoremodPrefix.METHOD, "originalClassSha256", NativeCoremodPrefix.ORIGINAL_SHA256,
                "boundary", "after-original-loadPlugins-write-before-root-plugin-loop",
                "nativeDeobfuscatedEnvironment", read("deobfuscatedEnvironment"),
                "side", CleanroomEnvironment.side().name(), "queuedTweaks", List.copyOf(tweaks),
                "patchingTransformerRegistered", true, "rootPluginSetupExecuted", false));
        result.put("launcherCompositionQualified", false); result.put("fullCoremodInjectionExecuted", false);
        result.put("nativeCandidateSelectionExecuted", false);
        result.put("remaining", List.of("target-launcher-class-space", "root-plugin-sanity-and-deobfuscation-setup",
                "native-coremod-selection-and-order", "complete-container-api-and-dependency-composition",
                "original-early-and-late-mixin-loader-conditions", "required-mod-construction"));
        observation = Collections.unmodifiableMap(result);
        return observation;
    }

    public static Map<String,Object> activate(NativeInitializationTrace trace) throws Exception {
        if (injectedContainer == null) return Map.of("status", "not-executed", "scope", SCOPE,
                "reason", observation == null ? "coremod-prefix-not-run" : observation.get("reason"),
                "packActivationQualified", false);
        return NativeAddonActivation.inspectInjectedCoremod(injectedContainer, trace);
    }

    private static Map<String,Object> deferred(String reason) {
        observation = Map.of("status", "not-admitted", "scope", SCOPE, "reason", reason,
                "nativePluginRegistered", false, "injectDataExecuted", false,
                "launcherCompositionQualified", false, "fullCoremodInjectionExecuted", false);
        return observation;
    }

    private static Object read(String name) throws Exception {
        Field field = CoreModManager.class.getDeclaredField(name); field.setAccessible(true);
        return field.get(null);
    }

    private static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
