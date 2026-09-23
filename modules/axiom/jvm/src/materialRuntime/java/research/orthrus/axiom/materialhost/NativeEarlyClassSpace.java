package research.orthrus.axiom.materialhost;

import com.cleanroommc.common.CleanroomEnvironment;
import com.google.gson.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.relauncher.CoreModManager;
import net.minecraftforge.fml.relauncher.FMLInjectionData;
import top.outlands.foundation.TransformerDelegate;
import top.outlands.foundation.boot.ActualClassLoader;
import top.outlands.foundation.boot.TransformerHolder;
import org.objectweb.asm.ClassReader;
import java.io.File;
import java.lang.reflect.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Fresh original SERVER bootstrap. Native ASM users are loaded only after its original setup. */
public final class NativeEarlyClassSpace {
    private static final List<String> ROOTS = List.of("net.minecraftforge.fml.relauncher.FMLCorePlugin",
            "net.minecraftforge.classloading.FMLForgePlugin");
    private static final List<String> TARGETS = List.of("net/minecraft/block/Block", "net/minecraft/item/ItemStack",
            "net/minecraft/util/EnumFacing", "net/minecraft/util/math/Vec3d", "net/minecraft/enchantment/Enchantment");
    private static final Map<String,Object> observed = new LinkedHashMap<>();
    private static boolean selectionRequested;
    private static boolean groovyRequested;
    private static boolean preInitRequested;
    private static boolean recipesRequested;
    private static Map<String,String> sourceClasses;
    private static Set<String> sourceTraits;
    private static Map<String,Object> sourceAdmission;
    private static Map<String,Object> materialObservations = Map.of();
    private NativeEarlyClassSpace() {}
    public static Map<String,Object> observations() { return new LinkedHashMap<>(observed); }

    public static Map<String,Object> start(String descriptorText) throws Exception {
        return start(descriptorText, "{}");
    }

    public static Map<String,Object> start(String descriptorText, String contextText) throws Exception {
        return start(descriptorText, contextText, false);
    }

    public static Map<String,Object> startSelection(String descriptorText, String contextText) throws Exception {
        return start(descriptorText, contextText, true);
    }

    public static Map<String,Object> startConstruction(String descriptorText, String contextText) throws Exception {
        return start(descriptorText, contextText, true, true);
    }

    public static Map<String,Object> startGroovy(String descriptorText, String contextText,
            Map<String,String> classes, Set<String> traits, Map<String,Object> admission) throws Exception {
        groovyRequested = true; sourceClasses = classes; sourceTraits = traits; sourceAdmission = admission;
        return start(descriptorText, contextText, true, true);
    }

    static boolean groovyRequested() { return groovyRequested; }
    static boolean preInitRequested() { return preInitRequested; }
    public static Map<String,Object> startPreInit(String descriptorText, String contextText,
            Map<String,String> classes, Set<String> traits, Map<String,Object> admission) throws Exception {
        preInitRequested = true;
        return startGroovy(descriptorText, contextText, classes, traits, admission);
    }
    public static Map<String,Object> startRecipes(String descriptorText, String contextText,
            Map<String,String> classes, Set<String> traits, Map<String,Object> admission) throws Exception {
        recipesRequested = true;
        return startPreInit(descriptorText, contextText, classes, traits, admission);
    }
    public static Map<String,Object> startRecipes(String descriptorText, String contextText,
            Map<String,String> classes, Set<String> traits, Map<String,Object> admission,
            Map<String,Object> observations) throws Exception {
        materialObservations = Map.copyOf(observations);
        return startRecipes(descriptorText, contextText, classes, traits, admission);
    }
    public static Map<String,Object> startPreInit(String descriptorText, String contextText,
            Map<String,String> classes, Set<String> traits, Map<String,Object> admission,
            Map<String,Object> observations) throws Exception {
        materialObservations = Map.copyOf(observations);
        return startPreInit(descriptorText, contextText, classes, traits, admission);
    }
    static Map<String,Object> materialObservations() { return materialObservations; }
    static void prepareGroovyAdmission() {
        if (groovyRequested) NativeGroovyClassSpace.prepare(sourceClasses, sourceTraits, sourceAdmission);
    }

    /** Called only by the extracted original prefix immediately after its transformer reset. */
    public static void installSelectionPrefix() {
        require(selectionRequested && "original-early-launch-loop".equals(observed.get("stage"))
                        && !Launch.classLoader.isClassLoaded(NativeLoaderPrefix.TARGET)
                        && !TransformerDelegate.getExplicitTransformers().containsKey(NativeLoaderPrefix.TARGET),
                "Native selection prefix installation is out of phase");
        require(NativeLoaderPrefix.class.getClassLoader() != Launch.classLoader,
                "Loader prefix extraction must use bridge ASM");
        TransformerDelegate.registerExplicitTransformer(bytes -> {
            try { observed.put("loaderAfterNativeTransformsSha256", digest(bytes)); }
            catch (Exception failure) { throw new IllegalStateException("Native Loader byte observation failed", failure); }
            byte[] selected = NativeLoaderPrefix.applyAfterNativeTransforms(bytes);
            if (groovyRequested) {
                byte[] evidence = NativeGroovyInitializationPrefix.preInitMethodEvidence(selected);
                try { observed.put("loaderPreInitAfterNativeTransformsSha256", digest(evidence)); }
                catch (Exception failure) { throw new IllegalStateException("PreInit method observation failed", failure); }
                return preInitRequested ? NativeGroovyInitializationPrefix.applyCompleteLoaderAfterNativeTransforms(selected)
                        : NativeGroovyInitializationPrefix.applyLoaderAfterNativeTransforms(selected);
            }
            return selected;
        }, NativeLoaderPrefix.TARGET);
        if (serverOwnerRequested) {
            require(!Launch.classLoader.isClassLoaded(NativeServerOwnerPrefix.TARGET)
                    && !TransformerDelegate.getExplicitTransformers().containsKey(NativeServerOwnerPrefix.TARGET)
                    && NativeServerOwnerPrefix.class.getClassLoader() != Launch.classLoader, "Server ownership prefix installation is out of phase");
            TransformerDelegate.registerExplicitTransformer(NativeServerOwnerPrefix::applyAfterNativeTransforms, NativeServerOwnerPrefix.TARGET);
        }
    }

    private static boolean serverOwnerRequested;

    private static Map<String,Object> start(String descriptorText, String contextText, boolean selection) throws Exception {
        return start(descriptorText, contextText, selection, false);
    }

    private static Map<String,Object> start(String descriptorText, String contextText, boolean selection, boolean construction) throws Exception {
        require(observed.isEmpty(), "Native early class space cannot be reused");
        selectionRequested = selection;
        observed.put("schema", "axiom.native-early-stage.v1");
        for (String flag : List.of("rootStageReady", "earlyPipelineReady", "materialInitializationComplete",
                "minecraftLaunched", "targetClassesDefined", "fullLauncherCompositionQualified",
                "lateMixinSelectionQualified", "constructionDispatched", "candidateCompilationStarted")) observed.put(flag, false);
        observed.put("stage", "input-admission");
        JsonObject descriptor = JsonParser.parseString(descriptorText).getAsJsonObject();
        JsonObject context = JsonParser.parseString(contextText).getAsJsonObject();
        boolean pack = !context.keySet().isEmpty();
        serverOwnerRequested = context.has("serverOwner");
        require(!serverOwnerRequested || NativeServerOwnerClassSpace.MODE.equals(context.get("serverOwner").getAsString()),
                "Selected server ownership mode differs");
        require(!selection || pack, "Native selection requires the explicit required pack context");
        require(!pack || ("axiom.native-pack-early-context.v1".equals(context.get("schema").getAsString())
                && "supersymmetry:required-early".equals(context.get("id").getAsString())
                && "server".equals(context.get("side").getAsString())
                && "INIT".equals(context.get("mixinPhase").getAsString())
                && "DEFAULT".equals(context.get("deferredMixinPhase").getAsString())),
                "Native pack early context side or stage differs");
        observed.put("nativeContext", pack ? context.get("id").getAsString() : "root-only");
        require("axiom.native-root-class-space.v1".equals(descriptor.get("schema").getAsString())
                && "SERVER".equals(descriptor.get("side").getAsString())
                && "original-obfuscated-artifacts".equals(descriptor.get("inputStage").getAsString())
                && "original-server-patch-remap-access-byte-probe".equals(descriptor.get("witnessStage").getAsString()),
                "Native root class-space side or stage differs");
        require(descriptor.getAsJsonObject("witnesses").keySet().equals(new HashSet<>(TARGETS)), "Native root witness inventory differs");
        observed.put("descriptorSha256", digest(descriptorText.getBytes(StandardCharsets.UTF_8)));
        observed.put("side", "SERVER"); observed.put("inputStage", "original-obfuscated-artifacts");
        for (String property : List.of("fml.ignorePatchDiscrepancies", "fml.ignoreInvalidMinecraftCertificates"))
            require(!Boolean.parseBoolean(System.getProperty(property, "false")), "Native integrity override is not admitted: " + property);
        var rootArtifact = descriptor.getAsJsonObject("cleanroom");
        Path originalRoot = sourceFile(Launch.classLoader.findResource("net/minecraftforge/fml/common/launcher/FMLServerTweaker.class"));
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
        var contextArtifacts = new LinkedHashMap<String,Path>();
        contextArtifacts.put("cleanroom", originalRoot);
        if (pack) {
            var artifactObservations = new LinkedHashMap<String,Object>();
            observed.put("nativeArtifacts", artifactObservations);
            Path home = Launch.minecraftHome.toPath().toRealPath();
            Path artifactHome = Path.of(context.get("artifactHome").getAsString()).toRealPath();
            for (var element : context.getAsJsonArray("artifacts")) {
                var artifact = element.getAsJsonObject();
                String name = artifact.get("descriptor").getAsString();
                Path output = Path.of(artifact.get("outputPath").getAsString());
                require(!output.isAbsolute(), "Native addon output path is not relative to its fresh home");
                Path source = home.resolve(output).toRealPath();
                require(Files.isSymbolicLink(home.resolve(output))
                        && source.startsWith(artifactHome) && source.equals(artifactHome.resolve(output).toRealPath())
                        && !contextArtifacts.containsKey(name), "Native addon source or descriptor differs");
                verifyArtifact(source, artifact);
                contextArtifacts.put(name, source);
                artifactObservations.put(name, Map.of("codeSource", source.toString(),
                        "sha256", artifact.get("sha256").getAsString(), "size", Files.size(source)));
            }
            var declared = strings(context.getAsJsonArray("artifactDescriptors"));
            require(!declared.isEmpty() && new HashSet<>(declared).equals(artifactObservations.keySet())
                    && declared.size() == artifactObservations.size(), "Native required addon inventory differs");
            observed.put("nativeArtifactPlacement", "links-to-verified-read-only-inputs");
        }

        for (String property : List.of("crl.dev.extrapath", "crl.dev.mixin", "fml.coreMods.load", "forge.lib_folder",
                "fml.modLists", "fml.skipContainedDeps"))
            require(System.getProperty(property) == null, "Unbound native early input: " + property);
        if (pack) require(System.getProperty("groovyscript.use_examples_folder") == null,
                "Unbound native early input: groovyscript.use_examples_folder");
        for (String name : List.of("org.objectweb.asm.ClassVisitor", "org.objectweb.asm.ClassWriter",
                "org.objectweb.asm.MethodVisitor", "org.objectweb.asm.FieldVisitor",
                "net.minecraftforge.fml.relauncher.CoreModManager", "net.minecraftforge.fml.common.Loader"))
            require(!Launch.classLoader.isClassLoaded(name), "Native early bootstrap class was preloaded: " + name);
        require(NativeEarlyLaunchPrefix.class.getClassLoader() != Launch.classLoader,
                "Early prefix extraction must use bridge ASM, separate from native bootstrap ASM");
        require(TransformerDelegate.getTransformers() == null || TransformerDelegate.getTransformers().isEmpty(),
                "Native early bootstrap requires fresh transformers");
        Method fill = TransformerDelegate.class.getDeclaredMethod("fillTransformerHolder", TransformerHolder.class);
        fill.setAccessible(true); fill.invoke(null, ActualClassLoader.getTransformerHolder());
        TransformerDelegate.registerExplicitTransformer(selection ? NativeEarlyLaunchPrefix::applySelection
                : NativeEarlyLaunchPrefix::apply, NativeEarlyLaunchPrefix.TARGET);
        try (var diagnostics = new EarlyDiagnostics()) {
            observed.put("nativeDiagnostics", diagnostics.rows);
            observed.put("nativeDiagnosticsComplete", true);
            observed.put("stage", "original-early-launch-loop");
            Class<?> launcher = Class.forName(NativeEarlyLaunchPrefix.TARGET, true, Launch.classLoader);
            var executed = (List<?>)launcher.getMethod(NativeEarlyLaunchPrefix.METHOD, String[].class).invoke(
                    launcher.getConstructor().newInstance(), (Object)new String[]{"--gameDir", Launch.minecraftHome.getAbsolutePath(),
                            "--version", "axiom-native-early-stage", "--tweakClass", "net.minecraftforge.fml.common.launcher.FMLServerTweaker"});
            AfterLaunch.observe(executed, descriptor, context, contextArtifacts, originalRoot, originalMinecraft);
            require(!diagnostics.hasErrors(), "Original early initialization logged native errors or exceeded the diagnostic bound");
            observed.put("earlyPipelineReady", true);
            if (selection) {
                var early = observations();
                early.put("nativeDiagnostics", List.copyOf(diagnostics.rows));
                observed.put("earlyStage", early);
                observed.put("schema", "axiom.native-selection-stage.v1");
                observed.put("selectionReady", false);
                NativeSelectionClassSpace.advance(executed, observed, context, construction, () -> {
                    require(!diagnostics.hasErrors(), "Original selection logged native errors or exceeded the diagnostic bound");
                    observed.put("selectionReady", true);
                    observed.put("lateMixinSelectionQualified", true);
                }, () -> {
                if (construction) {
                    // An original callback may log an error and return. Retain
                    // its failed verdict without inventing a native abort before
                    // later initialization and passive state collection.
                    observed.put("constructionReady", !diagnostics.hasErrors());
                    if (groovyRequested) {
                        NativeGroovyClassSpace.advance(observed, preInitRequested);
                        observed.put("nativeExecutionLoggedErrors", diagnostics.hasErrors());
                        observed.put(preInitRequested ? "preInitializationReady" : "groovyInitializationReady", !diagnostics.hasErrors());
                        if (recipesRequested) {
                            NativeGroovyClassSpace.advanceRecipes(observed);
                            // Scope acceptance is derived after complete console
                            // and registry evidence return to the owning engine.
                            observed.put("nativeExecutionLoggedErrors",diagnostics.hasErrors());
                        }
                    }
                }
                });
            }
            return observations();
        }
    }

    private static final class AfterLaunch {
        static Map<String,Object> observe(List<?> executed, JsonObject descriptor, JsonObject context,
                Map<String,Path> contextArtifacts, Path originalRoot, Path originalMinecraft) throws Exception {
            boolean pack = !context.keySet().isEmpty();
            observed.put("executedTweaks", executed.stream().map(value -> value.getClass().getName()).toList());
            require(!CleanroomEnvironment.isDev() && "SERVER".equals(CleanroomEnvironment.side().name()),
                    "Original early launch did not select production SERVER");
            require(((List<?>)Launch.blackboard.get("TweakClasses")).isEmpty()
                    && ((List<?>)Launch.blackboard.get("Tweaks")).isEmpty(), "Original early tweak queue did not drain");
            require(executed.stream().filter(value -> value.getClass().getName().equals(
                    "net.minecraftforge.fml.common.launcher.FMLDeobfTweaker")).count() == 1,
                    "Complete original deobfuscation transition did not return exactly once");
            observed.put("deobfTransitionReturned", true);
            observed.put("nativeOptionsAccepted", Launch.blackboard.containsKey("forgeLaunchArgs"));
            require(Launch.minecraftHome.equals(FMLInjectionData.data()[6]), "Original native home binding differs");
            observed.put("nativeHomeInitialized", true);
            String phase = org.spongepowered.asm.mixin.MixinEnvironment.getCurrentEnvironment().getPhase().toString();
            observed.put("mixinPhase", phase);
            require("INIT".equals(phase), "Original early launch did not stop in native INIT");
            Class<?> loaderClass = Class.forName("net.minecraftforge.fml.common.Loader", false, Launch.classLoader);
            Object nativeLoader = readExactField(loaderClass, "instance", loaderClass, null);
            require(nativeLoader != null && !Launch.classLoader.isClassLoaded("net.minecraftforge.fml.common.LoadController")
                    && readExactField(loaderClass, "mods", List.class, nativeLoader) == null
                    && Launch.minecraftHome.equals(readExactField(loaderClass, "minecraftDir", File.class, null)),
                    "Native Loader construction/selection boundary differs");
            observed.put("loaderInstanceCreated", true);
            var discoverer = com.cleanroommc.discovery.CleanroomModDiscoverer.instance();
            var discovered = new TreeMap<String,Object>();
            for (String mod : discoverer.presentMods())
                discovered.put(mod, discoverer.modSources(mod).stream().map(File::toString).sorted().toList());
            observed.put("discoveredMods", discovered);
            observed.put("mixinConfigurations", org.spongepowered.asm.mixin.Mixins.getConfigs().stream().map(Object::toString).sorted().toList());
            observed.put("nativeCoremodOrder", executed.stream().filter(CoreModManager.FMLPluginWrapper.class::isInstance)
                    .map(CoreModManager.FMLPluginWrapper.class::cast).map(wrapper -> wrapper.coreModInstance.getClass().getName()).toList());
            if(pack) {
                var contained=new TreeMap<String,Object>();
                if(context.has("containedArtifacts"))for(var element:context.getAsJsonArray("containedArtifacts")) {
                    var pin=element.getAsJsonObject();String id=pin.get("id").getAsString();
                    String parent=pin.get("parentArtifact").getAsString(),entry=pin.get("entry").getAsString();
                    require(contextArtifacts.containsKey(parent)&&!contextArtifacts.containsKey(id),
                            "Contained native artifact parent or identity differs");
                    Path source=NativeContainedArtifacts.verify(contextArtifacts.get(parent),entry,Launch.minecraftHome.toPath(),
                            pin.get("outputPath").getAsString(),pin.get("sha256").getAsString(),pin.get("size").getAsLong());
                    contained.put(id,Map.of("parentArtifact",parent,"parentCodeSource",contextArtifacts.get(parent).toString(),
                            "entry",entry,"codeSource",source.toString(),"sha256",pin.get("sha256").getAsString(),
                            "size",Files.size(source),"extractedByObserver",false));
                    contextArtifacts.put(id,source);
                }
                observed.put("containedArtifactRoot",Launch.minecraftHome.toPath().toRealPath().toString());
                observed.put("nativeContainedArtifacts",contained);
            }
            var callbacks = new ArrayList<Map<String,Object>>();
            var allCallbacks = new ArrayList<Map<String,Object>>();
            for (Object value : executed) if (value instanceof CoreModManager.FMLPluginWrapper wrapper) {
                String plugin = wrapper.coreModInstance.getClass().getName();
                int index = allCallbacks.size();
                require(index < (pack ? context.getAsJsonArray("coremods").size() : ROOTS.size()),
                        "Unexpected original early coremod callback");
                var expected = pack ? context.getAsJsonArray("coremods").get(index).getAsJsonObject() : null;
                String expectedPlugin = pack ? expected.get("plugin").getAsString() : ROOTS.get(index);
                String expectedSetup = pack ? expected.get("setupClass").getAsString()
                        : index == 0 ? "net.minecraftforge.fml.common.asm.FMLSanityChecker" : "none";
                Path expectedSource = pack ? contextArtifacts.get(expected.get("artifact").getAsString()) : originalRoot;
                Path source = sourceFile(wrapper.coreModInstance.getClass().getProtectionDomain().getCodeSource().getLocation());
                require(expectedPlugin.equals(plugin) && source.equals(expectedSource)
                        && wrapper.coreModInstance.getClass().getClassLoader() == Launch.classLoader,
                        "Original early coremod callback identity or CodeSource differs at " + index + ": " + plugin);
                // setupClass is the pinned declaration; the original returned wrapper
                // proves its dispatch completed. Do not replay a native plugin getter.
                var row = Map.<String,Object>of("plugin", plugin, "wrapperInjectionReturned", true,
                        "setupClass", expectedSetup, "codeSource", source.toString());
                allCallbacks.add(row);
                if (ROOTS.contains(plugin)) callbacks.add(row);
            }
            require(callbacks.stream().map(row -> row.get("plugin")).toList().equals(ROOTS),
                    "Both original root callbacks must return exactly once in native order");
            require(allCallbacks.size() == (pack ? context.getAsJsonArray("coremods").size() : ROOTS.size()),
                    "Original early coremod callbacks are incomplete");
            observed.put("rootCallbacks", callbacks);
            if (pack) observed.put("coremodCallbacks", allCallbacks);
            var expectedContainers = pack ? strings(context.getAsJsonArray("injectedContainers"))
                    : List.of("net.minecraftforge.fml.common.FMLContainer", "net.minecraftforge.common.ForgeModContainer");
            require(FMLInjectionData.containers.equals(expectedContainers),
                    "Original early injected containers differ");
            observed.put("injectedContainers", List.copyOf(FMLInjectionData.containers));
            if (pack) {
                var accessTransformers = List.copyOf(CoreModManager.getAccessTransformers());
                observed.put("queuedAccessTransformers", accessTransformers);
                require(accessTransformers.equals(strings(context.getAsJsonArray("accessTransformers"))),
                        "Original required coremod access transformer queue differs");
            }
            var transformers = TransformerDelegate.getTransformers().stream().map(value -> value.getClass().getName()).toList();
            observed.put("transformers", transformers);
            // Foundation sorts stably by native priority. All selected root
            // transformers have priority zero; CleanMix retains the inactive
            // PREINIT proxy and appends the active INIT proxy at transition.
            String forge = "net.minecraftforge.fml.common.asm.transformers.";
            String proxy = "org.spongepowered.asm.mixin.transformer.Proxy";
            List<String> wrapperParents = List.of("SideTransformer", "EventSubscriptionTransformer",
                    "EventSubscriberTransformer", "SoundEngineFixTransformer", "LWJGLTransformer");
            var expectedTransformers = new ArrayList<String>();
            expectedTransformers.add(proxy); expectedTransformers.add(forge + "PatchingTransformer");
            wrapperParents.forEach(name -> expectedTransformers.add("$wrapper." + forge + name));
            for (String name : List.of("DeobfuscationTransformer", "AccessTransformer", "ModAccessTransformer",
                    "ItemStackTransformer", "ItemBlockTransformer", "ItemBlockSpecialTransformer", "PotionEffectTransformer"))
                expectedTransformers.add(forge + name);
            expectedTransformers.add(proxy);
            if (pack) {
                expectedTransformers.clear();
                expectedTransformers.addAll(strings(context.getAsJsonArray("transformers")));
            }
            require(transformers.equals(expectedTransformers), "Original early transformer identity/order differs");
            var proxies = TransformerDelegate.getTransformers().stream().filter(value -> proxy.equals(value.getClass().getName())).toList();
            require(proxies.size() == 2, "Original Mixin proxy count differs");
            Object oldProxy = proxies.getFirst();
            Object activeProxy = proxies.getLast();
            Class<?> proxyType = oldProxy.getClass();
            var proxyRegistry = (List<?>)read(proxyType, "proxies", null);
            require(proxyType == activeProxy.getClass() && proxyType.getClassLoader() == Launch.classLoader
                    && proxyRegistry.size() == 2 && proxyRegistry.getFirst() == oldProxy && proxyRegistry.getLast() == activeProxy,
                    "Original Mixin proxy registration differs");
            var proxyStates = List.of(read(proxyType, "isActive", oldProxy), read(proxyType, "isActive", activeProxy));
            observed.put("mixinProxyStates", proxyStates);
            require(proxyStates.equals(List.of(false, true)), "Original Mixin INIT proxy activation differs");
            var wrappers = new ArrayList<Object>(); observed.put("transformerWrappers", wrappers);
            Class<?> wrapperType = net.minecraftforge.fml.common.asm.ASMTransformerWrapper.TransformerWrapper.class;
            Method ownerMethod = wrapperType.getDeclaredMethod("getCoreMod"); ownerMethod.setAccessible(true);
            for (Object transformer : TransformerDelegate.getTransformers()) if (wrapperType.isInstance(transformer)) {
                Object parent = read(wrapperType, "parent", transformer);
                int index = wrappers.size();
                require(index < (pack ? context.getAsJsonArray("transformerWrappers").size() : wrapperParents.size()),
                        "Unexpected original coremod transformer wrapper");
                var expected = pack ? context.getAsJsonArray("transformerWrappers").get(index).getAsJsonObject() : null;
                String expectedParent = pack ? expected.get("parent").getAsString() : forge + wrapperParents.get(index);
                String expectedOwner = pack ? expected.get("coremod").getAsString() : "FMLCorePlugin";
                Path expectedSource = pack ? contextArtifacts.get(expected.get("artifact").getAsString()) : originalRoot;
                Path source = sourceFile(parent.getClass().getProtectionDomain().getCodeSource().getLocation());
                require(parent.getClass().getName().equals(expectedParent)
                        && transformer.getClass().getName().equals("$wrapper." + expectedParent)
                        && transformer.getClass().getClassLoader() == Launch.classLoader
                        && parent.getClass().getClassLoader() == Launch.classLoader
                        && source.equals(expectedSource)
                        && expectedOwner.equals(ownerMethod.invoke(transformer)),
                        "Original coremod transformer parent, owner or CodeSource differs");
                wrappers.add(Map.of("transformer", transformer.getClass().getName(), "parent", expectedParent,
                        "coremod", expectedOwner, "codeSource", source.toString()));
            }
            require(wrappers.size() == (pack ? context.getAsJsonArray("transformerWrappers").size() : wrapperParents.size()),
                    "Original coremod transformer wrappers are incomplete");
            observed.put("stage", "early-safe-class-definition");
            var definitions = new LinkedHashMap<String,Object>(); observed.put("definitions", definitions);
            for (String target : List.of("net/minecraft/util/math/Vec3d", "net/minecraft/util/EnumFacing")) {
                String name = target.replace('/', '.');
                require(!Launch.classLoader.isClassLoaded(name), "Early witness was already defined: " + name);
                require(!TransformerDelegate.getExplicitTransformers().containsKey(name), "Unbound explicit target transformation: " + name);
                var seen = new ArrayList<String>();
                var patchedMethods = new ArrayList<String>();
                // Last explicit observer receives the actual definition bytes and returns the same reference.
                TransformerDelegate.registerExplicitTransformer(bytes -> {
                    try {
                        require(bytes != null && target.equals(new ClassReader(bytes).getClassName()), "Early definition name differs");
                        if (target.endsWith("/Vec3d")) {
                            var node = new org.objectweb.asm.tree.ClassNode();
                            new ClassReader(bytes).accept(node, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                            var methods = node.methods.stream().map(method -> method.name + method.desc).toList();
                            for (String method : List.of("func_72431_c(Lnet/minecraft/util/math/Vec3d;)Lnet/minecraft/util/math/Vec3d;",
                                    "func_189985_c()D", "func_189986_a(FF)Lnet/minecraft/util/math/Vec3d;",
                                    "func_189984_a(Lnet/minecraft/util/math/Vec2f;)Lnet/minecraft/util/math/Vec3d;")) {
                                require(methods.contains(method), "Original SERVER patch method absent: " + method);
                                patchedMethods.add(method);
                            }
                        }
                        seen.add(digest(bytes)); return bytes;
                    } catch (Exception failure) { throw new IllegalStateException("Early definition observation failed", failure); }
                }, name);
                Class<?> defined = Class.forName(name, false, Launch.classLoader);
                require(defined.getClassLoader() == Launch.classLoader && Launch.classLoader.isClassLoaded(name)
                        && originalMinecraft.equals(sourceFile(defined.getProtectionDomain().getCodeSource().getLocation()))
                        && seen.size() == 1, "Actual early definition/CodeSource differs: " + name);
                var expected = descriptor.getAsJsonObject("witnesses").getAsJsonObject(target);
                require(!seen.getFirst().equals(expected.get("inputSha256").getAsString()), "Early raw class was not transformed");
                var access = new TreeMap<String,Integer>();
                if (target.endsWith("/EnumFacing")) for (String field : List.of("field_82609_l", "field_176754_o")) {
                    int modifiers = defined.getDeclaredField(field).getModifiers();
                    require(Modifier.isPublic(modifiers), "Original access transformation was not applied: " + field);
                    access.put(field, modifiers);
                }
                definitions.put(target, Map.of("source", expected.get("source").getAsString(), "targetDefined", true,
                        "definitionSha256", seen.getFirst(), "codeSource", originalMinecraft.toString(),
                        "definingLoader", defined.getClassLoader().getClass().getName(), "fieldAccess", access,
                        "patchedMethods", patchedMethods));
            }
            if (pack) observeGroovyDefinition(context.getAsJsonObject("definitionWitness"), contextArtifacts, definitions);
            require("INIT".equals(org.spongepowered.asm.mixin.MixinEnvironment.getCurrentEnvironment().getPhase().toString()),
                    "Early definition advanced beyond original INIT");
            require(!Launch.classLoader.isClassLoaded("net.minecraftforge.fml.common.LoadController"),
                    "Early observation defined the DEFAULT-phase LoadController mixin target");
            observed.put("loadControllerDefinedBeforeDefault", false);
            observed.put("targetClassesDefined", true);
            observed.put("stage", "early-stage-ready");
            var remaining = new ArrayList<String>();
            if (!pack) remaining.add("required-stack-early-coremods-and-mixins");
            remaining.addAll(List.of("native-container-api-dependency-state", "late-mixin-selection",
                    "required-construction-material-item-fluid-registration", "native-recipe-registration"));
            observed.put("remainingScope", remaining);
            return observations();
        }

        private static void observeGroovyDefinition(JsonObject witness, Map<String,Path> artifacts,
                Map<String,Object> definitions) throws Exception {
            String target = witness.get("target").getAsString();
            String method = witness.get("method").getAsString();
            String hookOwner = witness.get("hookOwner").getAsString();
            String hookName = witness.get("hookName").getAsString();
            String hookDescriptor = witness.get("hookDescriptor").getAsString();
            String inputSha256 = witness.get("inputSha256").getAsString();
            require("org/codehaus/groovy/reflection/CachedClass$1".equals(target)
                    && "initValue()[Lorg/codehaus/groovy/reflection/CachedField;".equals(method)
                    && "com/cleanroommc/groovyscript/sandbox/transformer/GroovyCodeFactory".equals(hookOwner)
                    && "makeFieldsHook".equals(hookName)
                    && "(Lorg/codehaus/groovy/reflection/CachedClass;)Ljava/security/PrivilegedAction;".equals(hookDescriptor),
                    "Original early-safe Groovy definition witness differs");
            String name = target.replace('/', '.');
            Path source = artifacts.get(witness.get("artifact").getAsString());
            require(source != null && !Launch.classLoader.isClassLoaded(name), "Groovy early witness source is missing or already defined");
            require(!TransformerDelegate.getExplicitTransformers().containsKey(name), "Unbound explicit Groovy target transformation");
            require(source.equals(sourceFile(Launch.classLoader.findResource(target + ".class"))),
                    "Original Groovy witness resource CodeSource differs");
            try (var jar = new java.util.zip.ZipFile(source.toFile())) {
                var entry = jar.getEntry(target + ".class");
                require(entry != null, "Original Groovy witness bytes are missing");
                try (var input = jar.getInputStream(entry)) {
                    require(inputSha256.equals(digest(input.readAllBytes())), "Original Groovy witness input differs");
                }
            }
            var seen = new ArrayList<String>();
            // Observe the final native definition without evaluating Groovy or invoking its new hook.
            TransformerDelegate.registerExplicitTransformer(bytes -> {
                try {
                    require(bytes != null, "Groovy early definition bytes are missing");
                    var node = new org.objectweb.asm.tree.ClassNode();
                    new ClassReader(bytes).accept(node, ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                    require(target.equals(node.name), "Groovy early definition name differs");
                    var methods = node.methods.stream().filter(value -> (value.name + value.desc).equals(method)).toList();
                    require(methods.size() == 1, "Groovy early definition method differs");
                    int hooks = 0; int dynamics = 0;
                    for (var instruction : methods.getFirst().instructions) {
                        if (instruction instanceof org.objectweb.asm.tree.MethodInsnNode call
                                && call.owner.equals(hookOwner) && call.name.equals(hookName) && call.desc.equals(hookDescriptor)) {
                            require(call.getOpcode() == org.objectweb.asm.Opcodes.INVOKESTATIC && !call.itf,
                                    "Original Groovy field hook invocation differs");
                            hooks++;
                        }
                        if (instruction instanceof org.objectweb.asm.tree.InvokeDynamicInsnNode) dynamics++;
                    }
                    require(hooks == 1 && dynamics == 0, "Original Groovy field hook was not applied exactly once");
                    seen.add(digest(bytes));
                    return bytes;
                } catch (Exception failure) { throw new IllegalStateException("Groovy early definition observation failed", failure); }
            }, name);
            Class<?> defined = Class.forName(name, false, Launch.classLoader);
            require(defined.getClassLoader() == Launch.classLoader && Launch.classLoader.isClassLoaded(name)
                    && source.equals(sourceFile(defined.getProtectionDomain().getCodeSource().getLocation()))
                    && seen.size() == 1 && !inputSha256.equals(seen.getFirst()),
                    "Actual Groovy early definition, transformation or CodeSource differs");
            definitions.put(target, Map.of("source", target, "targetDefined", true, "definitionSha256", seen.getFirst(),
                    "inputSha256", inputSha256, "codeSource", source.toString(),
                    "definingLoader", defined.getClassLoader().getClass().getName(),
                    "observedHook", hookOwner + "." + hookName + hookDescriptor, "initializationRequested", false));
        }
    }

    /** Bounded passive original Log4j observations across the requested native stages. */
    private static final class EarlyDiagnostics extends org.apache.logging.log4j.core.appender.AbstractAppender implements AutoCloseable {
        private final List<Map<String,Object>> rows = new ArrayList<>();
        private final org.apache.logging.log4j.core.Logger root;
        private final org.apache.logging.log4j.core.LoggerContext context;
        private final java.beans.PropertyChangeListener configurationListener;
        private final int maximumBytes;
        private int retainedBytes = 2; // JSON array delimiters.
        private boolean overflow;
        EarlyDiagnostics() { this(0); }
        EarlyDiagnostics(int maximumBytes) {
            super("axiom-native-early-observation", null, null, false, org.apache.logging.log4j.core.config.Property.EMPTY_ARRAY);
            require(maximumBytes == 0 || maximumBytes == 65536 || maximumBytes == 131072 || maximumBytes == 262144, "Diagnostic allocation differs");
            this.maximumBytes = maximumBytes;
            root = (org.apache.logging.log4j.core.Logger)org.apache.logging.log4j.LogManager.getRootLogger();
            context = root.getContext();
            // FMLServerTweaker reconfigures Log4j in acceptOptions. Observe the new
            // configuration without changing that callback or its logging settings.
            configurationListener = event -> {
                if ("config".equals(event.getPropertyName())) {
                    // Log4j stops appenders from the retired configuration first.
                    start(); root.addAppender(this);
                }
            };
            context.addPropertyChangeListener(configurationListener);
            start(); root.addAppender(this);
        }
        @Override public synchronized void append(org.apache.logging.log4j.core.LogEvent event) {
            if (!event.getLevel().isMoreSpecificThan(org.apache.logging.log4j.Level.WARN)) return;
            if (overflow) return;
            String message = event.getMessage().getFormattedMessage();
            String logger = Objects.toString(event.getLoggerName(), "");
            // A character is at least one encoded byte. Refuse oversized fields
            // before serialization, without interrupting the native logging call.
            if (maximumBytes > 0 && (message.length() > maximumBytes || logger.length() > maximumBytes)) {
                markOverflow(); return;
            }
            String trace = "";
            if (event.getThrown() != null) {
                var output = new BoundedTrace();
                event.getThrown().printStackTrace(new java.io.PrintWriter(output));
                if (output.overflow) { markOverflow(); return; }
                trace = output.text.toString();
            }
            var row = new LinkedHashMap<String,Object>(Map.of("severity", event.getLevel() == org.apache.logging.log4j.Level.WARN ? "warning" : "error",
                    "logger", logger, "message", message, "trace", trace, "locationStatus", "unlocated"));
            if(event.getLevel().isMoreSpecificThan(org.apache.logging.log4j.Level.ERROR)) {
                try {row.put("nativeOrigins",NativeDiagnosticOrigins.capture());}
                catch(RuntimeException|LinkageError failure) {row.put("nativeOriginFailure",failure.getClass().getName()+": "+failure.getMessage());}
            }
            int bytes = 2 + row.size() - 1 + (rows.isEmpty() ? 0 : 1);
            for (var entry : row.entrySet())
                bytes += encodedStringBytes(entry.getKey()) + 1 + encodedValueBytes(entry.getValue());
            if (maximumBytes > 0 && bytes > maximumBytes - retainedBytes) { markOverflow(); return; }
            rows.add(row); retainedBytes += bytes;
        }
        // Match the existing Json.write transport: control characters and UTF-16
        // surrogates use six-byte escapes, including each half of a valid pair.
        // Tests size these rows with that actual serializer, not this counter.
        private static int encodedStringBytes(String value) {
            int bytes = 2;
            for (int i = 0; i < value.length(); i++) {
                char c = value.charAt(i);
                if (c < 32 || Character.isSurrogate(c)) bytes += 6;
                else if (c == '"' || c == '\\') bytes += 2;
                else bytes += c < 128 ? 1 : c < 2048 ? 2 : 3;
            }
            return bytes;
        }
        private static int encodedValueBytes(Object value) {
            if(value instanceof String text)return encodedStringBytes(text);
            if(value instanceof Boolean||value instanceof Number)return value.toString().length();
            if(value instanceof List<?> list)return 2+Math.max(0,list.size()-1)+list.stream().mapToInt(EarlyDiagnostics::encodedValueBytes).sum();
            if(value instanceof Map<?,?> map)return 2+Math.max(0,map.size()-1)+map.entrySet().stream()
                    .mapToInt(row->encodedStringBytes((String)row.getKey())+1+encodedValueBytes(row.getValue())).sum();
            throw new IllegalArgumentException("Unsupported diagnostic observation value");
        }
        private void markOverflow() {
            overflow = true;
            observed.put("nativeDiagnosticsComplete", false);
        }
        /** Never throw from native Throwable printing or retain an incomplete trace. */
        private final class BoundedTrace extends java.io.Writer {
            private final StringBuilder text = new StringBuilder();
            private boolean overflow;
            @Override public void write(char[] chars, int offset, int length) {
                if (overflow) return;
                if (maximumBytes > 0 && length > maximumBytes - text.length()) { overflow = true; return; }
                text.append(chars, offset, length);
            }
            @Override public void flush() {}
            @Override public void close() {}
        }

        synchronized boolean hasErrors() { return overflow || rows.stream().anyMatch(row -> "error".equals(row.get("severity"))); }
        @Override public void close() { context.removePropertyChangeListener(configurationListener); root.removeAppender(this); stop(); }
    }

    private static Object readExactField(Class<?> type, String name, Class<?> valueType, Object owner) throws Exception {
        // getDeclaredField resolves every declared field type on HotSpot. Loader
        // has a LoadController field whose target must remain undefined at INIT.
        // Resolve only this exact field instead, without inspecting that type.
        var lookup = java.lang.invoke.MethodHandles.privateLookupIn(type, java.lang.invoke.MethodHandles.lookup());
        var getter = owner == null ? lookup.findStaticGetter(type, name, valueType) : lookup.findGetter(type, name, valueType);
        try { return owner == null ? getter.invoke() : getter.invoke(owner); }
        catch (Exception | Error failure) { throw failure; }
        catch (Throwable failure) { throw new java.lang.reflect.InvocationTargetException(failure); }
    }
    private static Object read(Class<?> type, String name, Object owner) throws Exception {
        var field = type.getDeclaredField(name); field.setAccessible(true); return field.get(owner);
    }
    static Path sourceFile(URL location) throws Exception {
        require(location != null, "Native artifact resource is missing");
        if (location.openConnection() instanceof JarURLConnection jar) return Path.of(jar.getJarFileURL().toURI()).toRealPath();
        require("file".equals(location.getProtocol()), "Native artifact CodeSource is not a local file");
        return Path.of(location.toURI()).toRealPath();
    }
    private static void verifyArtifact(Path path, JsonObject pin) throws Exception {
        require(path != null && Files.isRegularFile(path) && Files.size(path) == pin.get("size").getAsLong()
                && digest(Files.readAllBytes(path)).equals(pin.get("sha256").getAsString()), "Original native artifact bytes differ");
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalStateException(message); }
    private static List<String> strings(JsonArray array) {
        var values = new ArrayList<String>();
        for (var element : array) values.add(element.getAsString());
        return values;
    }
    private static String digest(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }
}
