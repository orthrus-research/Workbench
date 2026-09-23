package research.orthrus.axiom.materialhost;

import com.google.gson.JsonObject;
import net.minecraft.launchwrapper.ITweaker;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.LoaderState;
import net.minecraftforge.fml.common.ModContainer;
import org.spongepowered.asm.mixin.MixinEnvironment;
import org.spongepowered.asm.mixin.Mixins;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.tree.*;
import top.outlands.foundation.TransformerDelegate;
import java.lang.reflect.Field;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.*;

/** Continuation of the original early path; no game entry or construction dispatch. */
public final class NativeSelectionClassSpace {
    private NativeSelectionClassSpace() {}

    static void advance(List<?> executedTweaks, Map<String,Object> observed, JsonObject context,
            boolean construction, Runnable acceptSelection, NativeServerOwnerClassSpace.Action afterConstruction) throws Exception {
        // GroovyLogImpl.setupLog owns its file but expects the launcher's logs
        // directory to exist. Prepare that output directory in this fresh home.
        java.nio.file.Files.createDirectories(Launch.minecraftHome.toPath().resolve("logs"));
        observed.put("stage", "original-launch-arguments");
        var callbacks = new ArrayList<String>();
        observed.put("launchArgumentCallbacks", callbacks);
        @SuppressWarnings("unchecked")
        var arguments = (List<String>)Launch.blackboard.get("ArgumentList");
        // Foundation LaunchHandler's original continuation iterates these actual
        // completed instances in this order. Every native getter executes once;
        // EnvironmentStateTweaker performs DEFAULT itself. No phase flag is set.
        for (Object value : executedTweaks) {
            var tweak = (ITweaker)value;
            arguments.addAll(Arrays.asList(tweak.getLaunchArguments()));
            callbacks.add(tweak.getClass().getName());
        }
        observed.put("mixinPhase", MixinEnvironment.getCurrentEnvironment().getPhase().toString());
        require("DEFAULT".equals(observed.get("mixinPhase")), "Original launch arguments did not reach DEFAULT");
        observed.put("launchArgumentsReturned", true);
        NativeEarlyClassSpace.prepareGroovyAdmission();
        var definitions = new LinkedHashMap<String,Object>();
        observed.put("selectionDefinitions", definitions);
        @SuppressWarnings("unchecked")
        var sources = (Map<String,String>)observed.get("artifactCodeSources");
        var controller = observeDefinition("net.minecraftforge.fml.common.LoadController",
                sources.get("cleanroom"), definitions);

        // Original MinecraftServer.main calls Bootstrap.register before the
        // SERVER handler is initialized. Execute that complete registration
        // method through the native transformer chain, without calling main or
        // substituting the earlier material-only Bootstrap extraction.
        observed.put("stage", "original-vanilla-registration");
        Class.forName("net.minecraft.init.Bootstrap", true, Launch.classLoader)
                .getMethod("func_151354_b").invoke(null);
        observed.put("vanillaRegistrationReturned", true);

        Object server = NativeServerOwnerClassSpace.construct(context, observed);
        NativeServerOwnerClassSpace.initialize(server, observed, () -> {
        // The original handler constructor calls beginLoading(this), including
        // MinecraftForge.initialize. Its native side and resource callbacks are
        // prerequisites of FMLModContainer and LoadController selection.
        observed.put("stage", "original-server-handler-initialization");
        Class<?> handlerType = Class.forName("net.minecraftforge.fml.server.FMLServerHandler", true, Launch.classLoader);
        Object handler = handlerType.getMethod("instance").invoke(null);
        require(read(handlerType, "server", handler) == null, "Native selection requires an unstarted SERVER handler");
        observed.put("nativeServerHandlerInitialized", true);
        NativeServerOwnerClassSpace.bind(handler, server, observed);
        observed.put("nativeEffectiveSide", net.minecraftforge.fml.common.FMLCommonHandler.instance().getEffectiveSide().toString());
        @SuppressWarnings("unchecked")
        var injected = (List<String>)read(handlerType, "injectedModContainers", handler);
        // The original pre-load common hook precedes Loader.loadMods.
        observed.put("stage", "original-pre-selection-hooks");
        Class.forName("com.cleanroommc.kirino.KirinoCommonCore", true, Launch.classLoader)
                .getMethod("configEvent").invoke(null);
        // Retain the original advisory. Its /fml confirm query belongs to game
        // startup, which this initialization scope does not invoke or approve.
        Class<?> checker = Class.forName("com.cleanroommc.common.PatchModPresentChecker", true, Launch.classLoader);
        boolean patchMissing = (boolean)checker.getMethod("isNotPresent").invoke(null);
        boolean hasForgeMods = com.cleanroommc.discovery.CleanroomModDiscoverer.instance().hasForgeMods();
        observed.put("nativePatchConfirmationRequired", patchMissing && hasForgeMods);
        observed.put("serverStartupConfirmationPerformed", false);

        observed.put("stage", "original-loader-selection");
        var loader = Loader.instance();
        require(read(Loader.class, "modController", loader) == null && read(Loader.class, "mods", loader) == null,
                "Original Loader selection requires fresh state");
        NativeConstructionClassSpace.SelectionObserver observer = () -> {
            observeSelection(loader, observed, context, definitions, controller);
            acceptSelection.run();
        };
        if (construction) NativeConstructionClassSpace.advance(loader, injected, observed, observer);
        else {
            Loader.class.getMethod(NativeLoaderPrefix.METHOD, List.class).invoke(loader, injected);
            observer.observe();
        }
        afterConstruction.run();
        });
    }

    private static void observeSelection(Loader loader, Map<String,Object> observed, JsonObject context,
            Map<String,Object> definitions, Map<String,Object> controller) throws Exception {
        require(loader.isInState(LoaderState.LOADING), "Native Loader prefix crossed the pre-construction boundary");
        observed.put("loaderState", "LOADING");
        observed.put("selectedMods", loader.getModList().stream().map(NativeSelectionClassSpace::container).toList());
        observed.put("activeMods", loader.getActiveModList().stream().map(ModContainer::getModId).toList());
        observeMixinConfigurations(observed);
        observed.put("selectionPrefixCompleted", true);
        observed.put("constructionBoundaryReached", true);
        finishDefinition("net.minecraftforge.fml.common.LoadController", controller);

        // Late targets must remain undefined until original Loader selection has
        // finished. Request definition only; no static initializer or method is
        // invoked to obtain this witness. The original transformer chain applies
        // the selected mixins and the explicit observer returns the same bytes.
        observed.put("stage", "selection-definition-witnesses");
        @SuppressWarnings("unchecked")
        var artifacts = (Map<String,Map<String,Object>>)observed.get("nativeArtifacts");
        var ore = observeDefinition("gregtech.api.unification.OreDictUnifier",
                (String)artifacts.get("mods/gregtech-ce-unofficial.pw.toml").get("codeSource"), definitions);
        Class.forName("gregtech.api.unification.OreDictUnifier", false, Launch.classLoader);
        finishDefinition("gregtech.api.unification.OreDictUnifier", ore);
        var selection = context.getAsJsonObject("selection");
        for (var id : selection.getAsJsonArray("requiredMods"))
            require(Loader.isModLoaded(id.getAsString()), "Required native selection is inactive: " + id.getAsString());
        for (var config : selection.getAsJsonArray("requiredMixinConfigurations"))
            require(((List<?>)observed.get("mixinConfigurations")).contains(config.getAsString()),
                    "Required late mixin configuration was not selected: " + config.getAsString());
        for (var witness : selection.getAsJsonArray("definitionWitnesses")) {
            var expected = witness.getAsJsonObject();
            @SuppressWarnings("unchecked")
            var row = (Map<String,Object>)definitions.get(expected.get("target").getAsString());
            requireAppliedMixin(expected, row);
        }
        require(loader.isInState(LoaderState.LOADING), "Definition observation crossed the construction boundary");
        observed.put("stage", "selection-prefix-completed");
        observed.put("selectionDefinitionsVerified", true);
        // The caller also requires complete native diagnostics without errors
        // before exposing bounded selection readiness.
    }

    private static void observeMixinConfigurations(Map<String,Object> observed) throws Exception {
        // CleanMix removes consumed configurations from Mixins.getConfigs().
        // A late loader can trigger native preparation before this observation
        // seam. Read both original stores, without preparing or requeuing any.
        var pending=Mixins.getConfigs().stream().map(org.spongepowered.asm.mixin.transformer.Config::getName).sorted().toList();
        Object transformer=MixinEnvironment.getCurrentEnvironment().getActiveTransformer();
        require(transformer!=null&&transformer.getClass().getName().equals("org.spongepowered.asm.mixin.transformer.MixinTransformer"),
                "Original active Mixin transformer is unavailable");
        Object processor=read(transformer.getClass(),"processor",transformer);
        require(processor!=null&&processor.getClass().getName().equals("org.spongepowered.asm.mixin.transformer.MixinProcessor"),
                "Original active Mixin processor is unavailable");
        var prepared=new ArrayList<String>();
        for(Object value:(List<?>)read(processor.getClass(),"configs",processor)) {
            require(value!=null&&value.getClass().getName().equals("org.spongepowered.asm.mixin.transformer.MixinConfig"),
                    "Original prepared Mixin configuration type differs");
            prepared.add(((org.spongepowered.asm.mixin.extensibility.IMixinConfig)value).getName());
        }
        Collections.sort(prepared);
        var selected=new TreeSet<String>(pending);selected.addAll(prepared);
        observed.put("mixinConfigurationStates",Map.of("schema","axiom.native-mixin-configurations.v1",
                "scope","active-transformer-prepared-and-global-pending","pending",pending,"prepared",List.copyOf(prepared),
                "configurationsPreparedByObserver",0));
        observed.put("mixinConfigurations",List.copyOf(selected));
    }
    private static Map<String,Object> observeDefinition(String name, String expectedSource,
            Map<String,Object> definitions) throws Exception {
        require(!Launch.classLoader.isClassLoaded(name), "Selection witness was defined before its observation boundary: " + name);
        require(!TransformerDelegate.getExplicitTransformers().containsKey(name), "Selection witness has an unbound explicit transformer: " + name);
        var location = Launch.classLoader.findResource(name.replace('.', '/') + ".class");
        require(location != null && location.getProtocol().equals("jar"), "Original selection witness resource is missing: " + name);
        var connection = (java.net.JarURLConnection)location.openConnection();
        Path source = Path.of(connection.getJarFileURL().toURI()).toRealPath();
        require(source.equals(Path.of(expectedSource).toRealPath()), "Original selection witness resource CodeSource differs: " + name);
        var row = new LinkedHashMap<String,Object>();
        row.put("inputCodeSource", source.toString());
        try (var input = location.openStream()) { row.put("inputSha256", digest(input.readAllBytes())); }
        row.put("definedBeforeObservation", false);
        row.put("definitionCount", 0);
        definitions.put(name, row);
        TransformerDelegate.registerExplicitTransformer(bytes -> {
            if (NativeEarlyClassSpace.groovyRequested() && name.equals("net.minecraftforge.fml.common.LoadController"))
                bytes = NativeEarlyClassSpace.preInitRequested() ? NativeGroovyInitializationPrefix.applyCompleteController(bytes)
                        : NativeGroovyInitializationPrefix.applyController(bytes);
            row.put("definitionCount", (int)row.get("definitionCount") + 1);
            try {
                var node = new ClassNode();
                new ClassReader(bytes).accept(node, ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
                require(node.name.equals(name.replace('.', '/')), "Selection witness definition name differs");
                var methods = new ArrayList<Map<String,Object>>();
                for (MethodNode method : node.methods) {
                    String mixin = mergedMixin(method);
                    if (mixin.isEmpty() && !method.name.equals("distributeStateMessage")) continue;
                    var calls = new ArrayList<String>();
                    for (var instruction : method.instructions)
                        if (instruction instanceof MethodInsnNode call) calls.add(call.owner + "." + call.name + call.desc);
                    methods.add(Map.of("name", method.name, "descriptor", method.desc, "mixin", mixin, "calls", calls));
                }
                row.put("methods", methods);
                row.put("definitionSha256", digest(bytes));
                row.put("mixinPhase", MixinEnvironment.getCurrentEnvironment().getPhase().toString());
            } catch (Throwable failure) {
                // Observation must not replace native definition or its failure.
                row.put("observationFailure", failure.getClass().getName() + ": " + failure.getMessage());
            }
            return bytes;
        }, name);
        return row;
    }

    private static void finishDefinition(String name, Map<String,Object> row) throws Exception {
        require(Launch.classLoader.isClassLoaded(name) && (int)row.get("definitionCount") == 1,
                "Selection witness was not defined exactly once: " + name);
        Class<?> type = Class.forName(name, false, Launch.classLoader);
        require(type.getClassLoader() == Launch.classLoader, "Selection witness defining loader differs: " + name);
        Path source = NativeEarlyClassSpace.sourceFile(type.getProtectionDomain().getCodeSource().getLocation());
        require(source.toString().equals(row.get("inputCodeSource")), "Selection definition CodeSource differs: " + name);
        row.put("codeSource", source.toString());
        row.put("definingLoader", type.getClassLoader().getClass().getName());
        row.put("targetDefined", true);
        require(!row.containsKey("observationFailure") && "DEFAULT".equals(row.get("mixinPhase")),
                "Selection definition observation failed or occurred outside DEFAULT: " + name);
    }

    private static String mergedMixin(MethodNode method) {
        var annotations = new ArrayList<AnnotationNode>();
        if (method.visibleAnnotations != null) annotations.addAll(method.visibleAnnotations);
        if (method.invisibleAnnotations != null) annotations.addAll(method.invisibleAnnotations);
        for (var annotation : annotations) {
            if (!annotation.desc.equals("Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;") || annotation.values == null) continue;
            for (int i = 0; i < annotation.values.size(); i += 2)
                if (annotation.values.get(i).equals("mixin")) return (String)annotation.values.get(i + 1);
        }
        return "";
    }
    private static void requireAppliedMixin(JsonObject expected, Map<String,Object> row) {
        require(row != null && expected.get("inputSha256").getAsString().equals(row.get("inputSha256"))
                        && !row.get("inputSha256").equals(row.get("definitionSha256")),
                "Selected mixin did not transform its target: " + expected.get("target").getAsString());
        @SuppressWarnings("unchecked")
        var methods = (List<Map<String,Object>>)row.get("methods");
        var matches = methods.stream().filter(method -> expected.get("mixin").getAsString().equals(method.get("mixin"))
                && expected.get("methodDescriptor").getAsString().equals(method.get("descriptor"))
                && (!expected.has("methodName") || expected.get("methodName").getAsString().equals(method.get("name"))))
                .filter(method -> ((List<?>)method.get("calls")).contains(expected.get("hook").getAsString())).toList();
        require(matches.size() == 1, "Original selected mixin hook is absent or ambiguous: " + expected.get("target").getAsString());
        if (expected.has("dispatchMethod")) {
            String call = expected.get("target").getAsString().replace('.', '/') + "."
                    + matches.getFirst().get("name") + matches.getFirst().get("descriptor");
            require(methods.stream().anyMatch(method -> expected.get("dispatchMethod").getAsString().equals(method.get("name"))
                    && expected.get("dispatchDescriptor").getAsString().equals(method.get("descriptor"))
                    && ((List<?>)method.get("calls")).contains(call)), "Original lifecycle dispatch does not call the selected mixin hook");
        }
        row.put("requiredMixinApplied", true);
    }
    private static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static Map<String,Object> container(ModContainer value) {
        var row = new LinkedHashMap<String,Object>();
        row.put("id", value.getModId()); row.put("container", value.getClass().getName());
        row.put("version", value.getVersion()); row.put("source", Objects.toString(value.getSource(), ""));
        row.put("requirements", value.getRequirements().stream().map(Object::toString).sorted().toList());
        row.put("dependencies", value.getDependencies().stream().map(Object::toString).toList());
        row.put("dependants", value.getDependants().stream().map(Object::toString).toList());
        row.put("state", Loader.instance().getModState(value).name());
        row.put("nativeLoaded", Loader.isModLoaded(value.getModId()));
        if (value instanceof net.minecraftforge.fml.common.FMLModContainer) {
            Object instance = value.getMod();
            row.put("instanceCreated", instance != null);
            require(instance == null, "User mod instance exists before construction boundary: " + value.getModId());
        }
        return row;
    }
    private static Object read(Class<?> type, String name, Object receiver) throws Exception {
        Field field = type.getDeclaredField(name); field.setAccessible(true); return field.get(receiver);
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalStateException(message); }
}
