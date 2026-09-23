package research.orthrus.axiom.materialhost;

import com.cleanroommc.common.CleanroomEnvironment;
import com.google.gson.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraft.launchwrapper.LaunchClassLoader;
import net.minecraftforge.fml.common.launcher.FMLServerTweaker;
import net.minecraftforge.fml.common.launcher.FMLTweaker;
import net.minecraftforge.fml.relauncher.CoreModManager;
import net.minecraftforge.fml.relauncher.FMLInjectionData;
import net.minecraftforge.fml.common.patcher.ClassPatchManager;
import net.minecraftforge.fml.common.asm.transformers.AccessTransformer;
import net.minecraftforge.fml.common.asm.transformers.deobf.FMLDeobfuscatingRemapper;
import net.minecraftforge.fml.common.asm.transformers.deobf.FMLRemappingAdapter;
import top.outlands.foundation.TransformerDelegate;
import top.outlands.foundation.boot.ActualClassLoader;
import top.outlands.foundation.boot.TransformerHolder;
import top.outlands.foundation.transformer.ASMVisitorTransformer;
import top.outlands.foundation.transformer.ASMClassWriterTransformer;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import java.io.File;
import java.lang.reflect.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Fresh raw SERVER root setup. No mod discovery, content initialization or game target. */
public final class NativeRootClassSpace {
    private static final List<String> ROOTS = List.of("net.minecraftforge.fml.relauncher.FMLCorePlugin",
            "net.minecraftforge.classloading.FMLForgePlugin");
    private static final List<String> TARGETS = List.of("net/minecraft/block/Block", "net/minecraft/item/ItemStack",
            "net/minecraft/util/EnumFacing", "net/minecraft/util/math/Vec3d", "net/minecraft/enchantment/Enchantment");
    private static final Map<String,Object> observed = new LinkedHashMap<>();
    private NativeRootClassSpace() {}

    public static Map<String,Object> observations() { return new LinkedHashMap<>(observed); }

    public static Map<String,Object> start(String descriptorText) throws Exception {
        if (!observed.isEmpty()) throw new IllegalStateException("Native root class space cannot be reused");
        observed.put("schema", "axiom.native-root-stage.v1"); observed.put("rootStageReady", false);
        observed.put("materialInitializationComplete", false); observed.put("minecraftLaunched", false);
        observed.put("targetClassesDefined", false); observed.put("fullLauncherCompositionQualified", false);
        observed.put("stage", "input-admission");
        JsonObject descriptor = JsonParser.parseString(descriptorText).getAsJsonObject();
        require("axiom.native-root-class-space.v1".equals(descriptor.get("schema").getAsString())
                && "SERVER".equals(descriptor.get("side").getAsString())
                && "original-obfuscated-artifacts".equals(descriptor.get("inputStage").getAsString())
                && "original-server-patch-remap-access-byte-probe".equals(descriptor.get("witnessStage").getAsString()),
                "Native root class-space side or stage differs");
        require(descriptor.getAsJsonObject("witnesses").keySet().equals(new HashSet<>(TARGETS)), "Native root witness inventory differs");
        observed.put("descriptorSha256", digest(descriptorText.getBytes(StandardCharsets.UTF_8)));
        observed.put("side", "SERVER"); observed.put("inputStage", "original-obfuscated-artifacts");
        Method fill = TransformerDelegate.class.getDeclaredMethod("fillTransformerHolder", TransformerHolder.class);
        fill.setAccessible(true); fill.invoke(null, ActualClassLoader.getTransformerHolder());
        require(TransformerDelegate.getTransformers().isEmpty(), "Native root requires a fresh transformer holder");
        TransformerDelegate.registerExplicitTransformer(new ASMVisitorTransformer(),
                "org.objectweb.asm.FieldVisitor", "org.objectweb.asm.ClassVisitor", "org.objectweb.asm.MethodVisitor");
        TransformerDelegate.registerExplicitTransformer(new ASMClassWriterTransformer(), "org.objectweb.asm.ClassWriter");
        TransformerDelegate.registerExplicitTransformer(NativeCoremodPrefix::applyRoots, NativeCoremodPrefix.TARGET);
        for (String name : List.of("net.minecraft.launchwrapper.IClassTransformer", "net.minecraft.launchwrapper.ITweaker",
                "net.minecraft.launchwrapper.IClassNameTransformer", "org.objectweb.asm.FieldVisitor",
                "org.objectweb.asm.ClassVisitor", "org.objectweb.asm.MethodVisitor", "org.objectweb.asm.ClassWriter"))
            Launch.classLoader.findClass(name);
        // Complete original FMLLaunchHandler constructor filtering, before patch-manager initialization.
        for (String name : List.of("org.spongepowered.asm.launch.", "org.spongepowered.asm.service.",
                "org.spongepowered.asm.mixin.", "org.spongepowered.asm.logging.", "org.spongepowered.asm.util.",
                "org.spongepowered.asm.lib.", "org.objectweb.asm.", "com.cleanroommc.loader.",
                "net.minecraftforge.fml.relauncher.", "net.minecraftforge.classloading.",
                "net.minecraftforge.fml.common.asm.transformers.", "net.minecraftforge.fml.common.patcher.",
                "net.minecraftforge.fml.repackage.", "LZMA.", "scala.", "it.unimi.dsi.", "oshi."))
            Launch.classLoader.addTransformerExclusion(name);
        var tweaker = new FMLServerTweaker();
        require(!CleanroomEnvironment.isDev() && "SERVER".equals(CleanroomEnvironment.side().name()),
                "Raw SERVER root inputs require the actual native production environment");
        require(read(CoreModManager.class, "loadPlugins", null) == null
                && read(CoreModManager.class, "mcDir", null) == null
                && FMLInjectionData.data()[6] == null && FMLInjectionData.containers.isEmpty(), "Native root state is not fresh");
        for (String property : List.of("fml.ignorePatchDiscrepancies", "fml.ignoreInvalidMinecraftCertificates"))
            require(!Boolean.parseBoolean(System.getProperty(property, "false")), "Native integrity override is not admitted: " + property);
        var rootArtifact = descriptor.getAsJsonObject("cleanroom");
        Path originalRoot = sourceFile(FMLServerTweaker.class.getProtectionDomain().getCodeSource().getLocation());
        verifyArtifact(originalRoot, rootArtifact);
        for (String resource : List.of("binpatches.pack.lzma", "deobf_data-1.12.2.tsrg", "forge_at.cfg", "mcpmod.info")) {
            URL location = Launch.classLoader.findResource(resource);
            require(location != null && sourceFile(location).equals(originalRoot), "Original root resource CodeSource differs: " + resource);
            var pin = descriptor.getAsJsonObject("resources").getAsJsonObject(resource);
            try (var input = location.openStream()) {
                byte[] raw = input.readNBytes((16 << 20) + 1);
                require(raw.length == pin.get("size").getAsInt() && digest(raw).equals(pin.get("sha256").getAsString()),
                        "Original root resource bytes differ: " + resource);
            }
        }
        Path originalMinecraft = null;
        for (String target : TARGETS) {
            var witness = descriptor.getAsJsonObject("witnesses").getAsJsonObject(target);
            String rawName = witness.get("source").getAsString();
            require(!rawName.equals(target) && Launch.classLoader.getClassBytes(target.replace('/', '.')) == null,
                    "Prepared target classes cannot be raw patch inputs: " + target);
            URL location = Launch.classLoader.findResource(rawName.replace('.', '/') + ".class");
            require(location != null, "Original raw SERVER witness is missing: " + target);
            Path artifact = sourceFile(location);
            require(originalMinecraft == null || originalMinecraft.equals(artifact), "Raw SERVER witnesses have mixed CodeSources");
            originalMinecraft = artifact;
            require(digest(Launch.classLoader.getClassBytes(rawName.replace('/', '.'))).equals(witness.get("inputSha256").getAsString()),
                    "Original raw SERVER witness bytes differ: " + target);
        }
        verifyArtifact(originalMinecraft, descriptor.getAsJsonObject("minecraft"));
        observed.put("artifactCodeSources", Map.of("cleanroom", originalRoot.toString(), "minecraft", originalMinecraft.toString()));
        observed.put("stage", "native-options-and-home");
        tweaker.acceptOptions(List.of(), Launch.minecraftHome, null, "axiom-native-root-stage");
        require(new File(FMLTweaker.getJarLocation()).toPath().toRealPath().equals(originalRoot)
                && tweaker.getGameDir().equals(Launch.minecraftHome), "Original native option/CodeSource binding differs");
        Method build = FMLInjectionData.class.getDeclaredMethod("build", File.class, LaunchClassLoader.class);
        build.setAccessible(true); build.invoke(null, Launch.minecraftHome, Launch.classLoader);
        require(Launch.minecraftHome.equals(FMLInjectionData.data()[6]), "Original native home binding differs");
        observed.put("nativeOptionsAccepted", true); observed.put("nativeHomeInitialized", true);
        observed.put("stage", "native-root-registration");
        CoreModManager.class.getDeclaredMethod(NativeCoremodPrefix.ROOT_METHOD, File.class, LaunchClassLoader.class, FMLTweaker.class)
                .invoke(null, Launch.minecraftHome, Launch.classLoader, tweaker);
        var plugins = (List<?>)read(CoreModManager.class, "loadPlugins", null);
        require(plugins.size() == ROOTS.size(), "Original root plugin count differs");
        var callbacks = new ArrayList<Object>(); observed.put("rootCallbacks", callbacks);
        for (int i = 0; i < ROOTS.size(); i++) {
            var wrapper = (CoreModManager.FMLPluginWrapper)plugins.get(i);
            require(ROOTS.get(i).equals(wrapper.coreModInstance.getClass().getName())
                    && originalRoot.equals(wrapper.location.toPath().toRealPath())
                    && originalRoot.equals(sourceFile(wrapper.coreModInstance.getClass().getProtectionDomain().getCodeSource().getLocation())),
                    "Original root plugin identity/order/CodeSource differs");
            observed.put("stage", "native-root-injection:" + ROOTS.get(i));
            wrapper.injectIntoClassLoader(Launch.classLoader);
            callbacks.add(Map.of("plugin", ROOTS.get(i), "wrapperInjectionReturned", true,
                    "setupClass", Objects.toString(wrapper.coreModInstance.getSetupClass(), "none"),
                    "codeSource", originalRoot.toString()));
        }
        require(FMLInjectionData.containers.equals(List.of("net.minecraftforge.fml.common.FMLContainer", "net.minecraftforge.common.ForgeModContainer")),
                "Original root injected container list differs");
        require(CoreModManager.getAccessTransformers().equals(List.of("net.minecraftforge.fml.common.asm.transformers.AccessTransformer")),
                "Original root access-transformer queue differs");
        observed.put("injectedContainers", List.copyOf(FMLInjectionData.containers));
        observed.put("queuedTweaks", List.copyOf((List<?>)Launch.blackboard.get("TweakClasses")));
        observed.put("stage", "native-patch-remap-observation");
        var patches = (com.google.common.collect.ListMultimap<?, ?>)read(ClassPatchManager.class, "patches", ClassPatchManager.INSTANCE);
        require(patches != null && patches.size() == descriptor.get("binaryPatches").getAsInt() && !patches.isEmpty(),
                "Original root setup returned without the selected SERVER patch set");
        var expectedPatches = descriptor.getAsJsonObject("patchInventory");
        require(patches.keySet().size() == expectedPatches.size(), "Original SERVER patch keys differ");
        for (var group : patches.asMap().entrySet()) {
            var expected = expectedPatches.getAsJsonObject(group.getKey().toString());
            require(expected != null && group.getValue().size() == 1, "Original SERVER patch grouping differs");
            var patch = group.getValue().iterator().next(); var type = patch.getClass();
            require(expected.get("target").getAsString().equals(type.getField("targetClassName").get(patch))
                    && expected.get("inputChecksum").getAsInt() == type.getField("inputChecksum").getInt(patch)
                    && expected.get("patchSha256").getAsString().equals(digest((byte[])type.getField("patch").get(patch))),
                    "Original SERVER patch identity differs: " + group.getKey());
        }
        // Byte probes use original transformations after real root setup. They do
        // not claim the downstream FMLDeobfTweaker/mixin pipeline has executed.
        var access = new AccessTransformer(); var witnesses = new LinkedHashMap<String,Object>();
        for (String target : TARGETS) {
            var expected = descriptor.getAsJsonObject("witnesses").getAsJsonObject(target);
            String source = expected.get("source").getAsString();
            require(target.equals(FMLDeobfuscatingRemapper.INSTANCE.map(source))
                    && source.equals(FMLDeobfuscatingRemapper.INSTANCE.unmap(target)), "Original root remapping differs: " + target);
            byte[] raw = Launch.classLoader.getClassBytes(source.replace('/', '.'));
            byte[] patched = ClassPatchManager.INSTANCE.applyPatch(source.replace('/', '.'), target.replace('/', '.'), raw);
            var writer = new ClassWriter(0); new ClassReader(patched).accept(new FMLRemappingAdapter(writer), 0);
            byte[] remapped = writer.toByteArray(); byte[] transformed = access.transform(source, target.replace('/', '.'), remapped);
            require(digest(patched).equals(expected.get("patchedSha256").getAsString())
                    && digest(remapped).equals(expected.get("remappedSha256").getAsString())
                    && digest(transformed).equals(expected.get("accessSha256").getAsString())
                    && target.equals(new ClassReader(transformed).getClassName()), "Native root byte witness differs: " + target);
            require(!Launch.classLoader.isClassLoaded(target.replace('/', '.')), "Native byte probe unexpectedly defined target: " + target);
            witnesses.put(target, Map.of("source", source, "patchedSha256", digest(patched),
                    "remappedSha256", digest(remapped), "accessSha256", digest(transformed), "targetDefined", false));
        }
        observed.put("binaryPatches", patches.size()); observed.put("patchInventoryMatched", true);
        observed.put("witnesses", witnesses); observed.put("rootStageReady", true); observed.put("stage", "root-stage-ready");
        observed.put("transformers", TransformerDelegate.getTransformers().stream().map(value -> value.getClass().getName()).toList());
        observed.put("remainingScope", List.of("target-class-definition-through-full-native-transformer-order",
                "required-coremod-selection-and-mixin-conditions", "native-container-api-dependency-state",
                "required-construction-material-item-fluid-registration", "native-recipe-registration"));
        return observations();
    }

    private static Object read(Class<?> type, String name, Object owner) throws Exception {
        var field = type.getDeclaredField(name); field.setAccessible(true); return field.get(owner);
    }
    private static Path sourceFile(URL location) throws Exception {
        if (location.openConnection() instanceof JarURLConnection jar) return Path.of(jar.getJarFileURL().toURI()).toRealPath();
        require("file".equals(location.getProtocol()), "Native artifact CodeSource is not a local file");
        return Path.of(location.toURI()).toRealPath();
    }
    private static void verifyArtifact(Path path, JsonObject pin) throws Exception {
        require(path != null && Files.isRegularFile(path) && Files.size(path) == pin.get("size").getAsLong()
                && digest(Files.readAllBytes(path)).equals(pin.get("sha256").getAsString()), "Original native artifact bytes differ");
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalStateException(message); }
    private static String digest(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }
}
