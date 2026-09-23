package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.LoaderState;
import net.minecraftforge.fml.common.ModContainer;
import top.outlands.foundation.TransformerDelegate;
import java.util.*;

/** Continues the original constructed context through Groovy's preInit dispatch hook. */
public final class NativeGroovyClassSpace {
    private static Map<String,String> sources;
    private static Map<String,Object> current;
    private static NativeProgramObservations currentLogs;
    private static Object currentEngine, currentSandbox;
    private static Class<?> currentGroovy;
    private NativeGroovyClassSpace() {}

    static void prepare(Map<String,String> sourceClasses, Set<String> sourceTraits, Map<String,Object> admission) {
        sources = Map.copyOf(sourceClasses);
        MaterialCallGate.bind(sourceClasses.keySet(), admission);
        MaterialTraitClasses.bind(sourceTraits);
        require(!Launch.classLoader.isClassLoaded(MaterialTraitLoaderHook.TARGET)
                        && !TransformerDelegate.getExplicitTransformers().containsKey(MaterialTraitLoaderHook.TARGET),
                "Native trait definition admission must precede its original loader definition");
        TransformerDelegate.registerExplicitTransformer(bytes -> MaterialTraitLoaderHook.apply(bytes,
                "research/orthrus/axiom/materialhost/MaterialTraitClasses"), MaterialTraitLoaderHook.TARGET);
        require(!Launch.classLoader.isClassLoaded(NativeRecipeFunctionObservations.TARGET)
                        &&!TransformerDelegate.getExplicitTransformers().containsKey(NativeRecipeFunctionObservations.TARGET),
                "Native recipe function observation must precede its original definition");
        TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::apply,NativeRecipeFunctionObservations.TARGET);
        require(!Launch.classLoader.isClassLoaded(NativeRecipeFunctionObservations.BOMBLET_TARGET)
                        &&!TransformerDelegate.getExplicitTransformers().containsKey(NativeRecipeFunctionObservations.BOMBLET_TARGET),
                "Native bomblet function observation must precede its original definition");
        TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::applyBomblet,NativeRecipeFunctionObservations.BOMBLET_TARGET);
        for(String target:NativeRecipeFunctionObservations.STORAGE_TARGETS) {
            require(!Launch.classLoader.isClassLoaded(target)&&!TransformerDelegate.getExplicitTransformers().containsKey(target),
                    "Native storage function observation must precede its original definition");
            TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::applyStorage,target);
        }
        require(!Launch.classLoader.isClassLoaded(NativeRecipeFunctionObservations.BACKPACK_TARGET)
                        &&!TransformerDelegate.getExplicitTransformers().containsKey(NativeRecipeFunctionObservations.BACKPACK_TARGET),
                "Native backpack function observation must precede its original definition");
        TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::applyBackpack,NativeRecipeFunctionObservations.BACKPACK_TARGET);
        for(String target:List.of(NativeRecipeFunctionObservations.CARGO_TARGET,NativeRecipeFunctionObservations.CONDITION_TARGET)) {
            require(!Launch.classLoader.isClassLoaded(target)&&!TransformerDelegate.getExplicitTransformers().containsKey(target),
                    "Native crafting function observation must precede its original definition");
            TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::applyCraftingFactory,target);
        }
        for(String target:List.of(NativeRecipeFunctionObservations.MULTI_RECIPE_TARGET,NativeRecipeFunctionObservations.BLACKLIST_TARGET)) {
            require(!Launch.classLoader.isClassLoaded(target)&&!TransformerDelegate.getExplicitTransformers().containsKey(target),
                    "Native AutoRegLib observation must precede its original definition");
            TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::applyAutoRegLib,target);
        }
        for(String target:NativeRecipeFunctionObservations.VALUE_FACTORY_TARGETS) {
            require(!Launch.classLoader.isClassLoaded(target)&&!TransformerDelegate.getExplicitTransformers().containsKey(target),
                    "Native value function observation must precede its original definition");
            TransformerDelegate.registerExplicitTransformer(NativeRecipeFunctionObservations::applyValueFactory,target);
        }
    }

    static void advance(Map<String,Object> observed) throws Exception {
        advance(observed, false);
    }

    static void advance(Map<String,Object> observed, boolean completePreInit) throws Exception {
        var loader = Loader.instance();
        require(Boolean.TRUE.equals(observed.get("constructionMethodReturned")) && loader.isInState(LoaderState.PREINITIALIZATION),
                "Native Groovy initialization requires completed original construction");
        var construction = new LinkedHashMap<String,Object>(observed);
        construction.put("nativeDiagnostics", List.copyOf((List<?>)observed.get("nativeDiagnostics")));
        observed.put("constructionStage", construction);
        observed.put("schema", "axiom.native-groovy-stage.v1");
        observed.put("groovyInitializationReady", false);
        observed.put("groovyInitializationReturned", false);
        observed.put("candidateCompilationStarted", false);
        observed.put("stage", "original-preinit-prerequisites-and-groovy-hook");
        Class<?> groovy = Class.forName("com.cleanroommc.groovyscript.GroovyScript", false, Launch.classLoader);
        Object sandbox = groovy.getMethod("getSandbox").invoke(null);
        Object engine = sandbox.getClass().getMethod("getEngine").invoke(sandbox);
        var config = (org.codehaus.groovy.control.CompilerConfiguration)engine.getClass().getMethod("getConfig").invoke(engine);
        require(config.getBytecodePostprocessor() == null, "Original Groovy engine already has a bytecode postprocessor");
        @SuppressWarnings("unchecked")
        var bindings = (Map<String,Object>)sandbox.getClass().getMethod("getBindings").invoke(sandbox);
        var gate = new MaterialBytecodeGate();
        var mappersBound = new boolean[1];
        config.setBytecodePostprocessor((name, bytes) -> {
            observed.put("candidateCompilationStarted", true);
            if (!mappersBound[0]) {
                // The original preInit hook has registered its mapper objects
                // before this engine compiles the first saved script.
                MaterialCallGate.bindNativeMappers(bindings); mappersBound[0] = true;
                observed.put("nativeMapperAdmissionBound", true);
            }
            return gate.processBytecode(name, bytes);
        });
        Object active = loader.activeModContainer();
        var logs = new NativeProgramObservations(sources);
        current = observed; currentLogs = logs; currentEngine = engine; currentSandbox = sandbox; currentGroovy = groovy;
        if (completePreInit) {
            observed.put("schema", "axiom.native-preinit-stage.v1");
            observed.put("preInitializationReady", false);
            observed.put("preInitializationReturned", false);
            observed.put("preInitializationDispatchStarted", false);
            observed.put("preInitializationDispatchReturned", false);
            observed.put("nonRecipeRegistryEventsReturned", false);
            observed.put("modPreInitDispatchCount", 0);
            observed.put("stage", "original-complete-preinit");
        }
        try {
            Loader.class.getMethod(completePreInit ? NativeGroovyInitializationPrefix.PREINIT_METHOD
                    : NativeGroovyInitializationPrefix.METHOD).invoke(loader);
            if (completePreInit) {
                observed.put("preInitializationReturned", true);
                require(loader.isInState(LoaderState.INITIALIZATION), "Original preInit did not reach INITIALIZATION");
                observed.put("stage", "original-preinit-returned");
            } else {
                observed.put("groovyInitializationReturned", true);
                require(loader.isInState(LoaderState.PREINITIALIZATION), "Groovy prefix crossed the original preInit boundary");
                observed.put("stage", "groovy-preinit-hook-returned");
            }
            require(loader.activeModContainer() == active, "Original script/mod execution did not restore its active owner");
            if (completePreInit) {
                if (loader.getIndexedModList().containsKey("biomesoplenty"))
                    observed.put("nativeBiomes", NativeBiomeObservations.collect());
                observed.put("nativeMaterialRegistries", NativeRegistrationEffects.registries());
                observed.put("customMetaItems", NativeMetaItemObservations.collect());
                var effects = new LinkedHashMap<>(NativeRegistrationEffects.collectPreInit());
                effects.put("customItems", Map.of("status", "reference", "sourcePointer", "/result/customMetaItems"));
                observed.put("registrationEffects", effects);
                var witnesses = new ArrayList<Map<String,Object>>();
                var selectedMaterials = new ArrayList<Object>();
                for (String name : List.of("Iron", "Diamond")) {
                    Object material = NativeObservationAccess.field("gregtech.api.unification.material.Materials", null, name);
                    selectedMaterials.add(material); witnesses.add(NativeMaterialObservations.material(material));
                }
                observed.put("nativeMaterialWitnesses", witnesses);
                var selection = new NativeSelectedMaterialObservations(NativeEarlyClassSpace.materialObservations());
                for (Object material : selection.formMaterials())
                    if (selectedMaterials.stream().noneMatch(existing -> existing == material)) selectedMaterials.add(material);
                var generated = NativeGeneratedContentObservations.collect(selectedMaterials, selection);
                observed.putAll(selection.finish(generated));
                var fixedNames = witnesses.stream().map(row -> row.get("name")).toList();
                for (var entry : generated.entrySet()) {
                    var catalog = new LinkedHashMap<>((Map<String,Object>)entry.getValue());
                    catalog.put("witnesses", ((List<Map<String,Object>>)catalog.get("witnesses")).stream()
                            .filter(row -> fixedNames.contains(row.get("material"))).toList());
                    effects.put(entry.getKey(), catalog);
                }
            }
        } catch (Exception | LinkageError failure) {
            logs.exception(failure);
            throw failure;
        } finally {
            logs.close();
            captureGroovy(observed, logs, engine, sandbox, groovy);
            if (completePreInit) {
                observed.put("preInitializedMods", loader.getModList().stream().map(container -> Map.of(
                        "id", container.getModId(), "state", loader.getModState(container).name(),
                        "container", container.getClass().getName())).toList());
                for (var state : LoaderState.values()) if (loader.isInState(state)) observed.put("loaderState", state.name());
            }
        }
        requireCleanGroovy(observed, logs);
    }

    /** Original recipe event, init, postInit and load-complete sequence on the same native state. */
    static void advanceRecipes(Map<String,Object> observed) throws Exception {
        var loader = Loader.instance();
        require(Boolean.TRUE.equals(observed.get("preInitializationReturned"))
                        && loader.isInState(LoaderState.INITIALIZATION),
                "Native recipes require completed original preInit");
        var startup = new LinkedHashMap<String,Object>(observed);
        startup.put("nativeDiagnostics", List.copyOf((List<?>)observed.get("nativeDiagnostics")));
        observed.put("startupStage", startup);
        observed.put("schema", "axiom.native-recipe-stage.v1");
        observed.put("recipeInitializationReady", false);
        observed.put("recipeInitializationStarted", true);
        observed.put("recipeInitializationReturned", false);
        observed.put("effectiveRecipeRegistryObserved", false);
        observed.put("stage", "original-recipe-init-postinit-sequence");
        var logs = new NativeProgramObservations(sources);
        try {
            // Original ModSupport initialization installed these properties;
            // original preInit has now populated GT's map wrappers. Admission
            // retains identities and metadata without invoking their getters.
            @SuppressWarnings("unchecked")
            var bindings = (Map<String,Object>)currentSandbox.getClass().getMethod("getBindings").invoke(currentSandbox);
            Object mods = NativeObservationAccess.field("com.cleanroommc.groovyscript.compat.mods.ModSupport", null, "INSTANCE");
            var containers = new LinkedHashMap<String,Object>();
            var graph = new IdentityHashMap<Object,Map<String,?>>();
            for (String name : List.of("gregtech", "jei", "chisel", "pyrotech")) {
                if (!Loader.isModLoaded(name)) continue;
                Object container = name.equals("gregtech")
                        ? NativeObservationAccess.callStatic("gregtech.integration.groovy.GroovyScriptModule", "getInstance")
                        : NativeObservationAccess.field("com.cleanroommc.groovyscript.compat.mods.ModSupport", null,
                                name.toUpperCase(Locale.ROOT));
                if (!(boolean)NativeObservationAccess.call(container, "isLoaded")) continue;
                Object properties = NativeObservationAccess.call(container, "get");
                @SuppressWarnings("unchecked")
                var registered = (Map<String,?>)NativeObservationAccess.call(properties, "getProperties");
                containers.put(name, properties); graph.put(properties, registered);
            }
            String vanilla="com.cleanroommc.groovyscript.compat.vanilla.VanillaModule";
            Object originalVanilla=NativeObservationAccess.field(vanilla,null,"INSTANCE");
            Object crafting=NativeObservationAccess.field(vanilla,originalVanilla,"crafting");
            Object furnace=NativeObservationAccess.field(vanilla,originalVanilla,"furnace");
            Object oreDict=NativeObservationAccess.field(vanilla,originalVanilla,"oreDict");
            MaterialCallGate.bindNativeRecipeProperties(bindings, mods, containers, graph,
                    Map.of("crafting",crafting,"furnace",furnace,"oreDict",oreDict,"ore_dict",oreDict));
            observed.put("nativeRecipePropertyAdmissionBound", true);
            // This original method owns CraftingHelper's recipe event, mod init,
            // IMC, postInit, AVAILABLE/Groovy postInit and registry freezing.
            loader.initializeMods();
            observed.put("recipeInitializationReturned", true);
            require(loader.isInState(LoaderState.AVAILABLE), "Original recipe sequence did not reach AVAILABLE");
            observed.put("nativeWorldgenBiomeBindings", NativeBiomeObservations.worldgen());
            var recipes=NativeEffectiveRecipeObservations.collect();
            observed.put("nativeStoredRecipes",recipes);
            observed.put("nativeStoredCraftingRecipes",NativeVanillaRecipeObservations.crafting());
            observed.put("nativeStoredFurnaceRecipes",NativeVanillaRecipeObservations.furnace());
            observed.put("nativeStoredCraftingCallbacks",NativeEffectiveRecipeObservations.collectStoredCraftingCallbacks());
            observed.put("nativeOreMutations",NativeEffectiveRecipeObservations.collectOreMutations(oreDict));
            observed.put("nativeBlockHardness",NativeEffectiveRecipeObservations.collectBlockHardness());
            observed.put("nativeRecipeFunctions",NativeRecipeFunctionObservations.observations());
            observed.put("nativeStoredRecipeValuesObserved",Boolean.TRUE.equals(recipes.get("storedValuesComplete")));
            observed.put("stage", "original-recipe-initialization-returned");
        } catch (Exception | LinkageError failure) {
            logs.exception(failure);
            throw failure;
        } finally {
            logs.close();
            captureGroovy(observed, logs, currentEngine, currentSandbox, currentGroovy);
            observed.put("initializedMods", loader.getModList().stream().map(container -> Map.of(
                    "id", container.getModId(), "state", loader.getModState(container).name(),
                    "container", container.getClass().getName())).toList());
            for (var state : LoaderState.values()) if (loader.isInState(state)) observed.put("loaderState", state.name());
        }
        requireCoveredGroovy(observed);
    }

    /** Reached by the original transformed controller after its Groovy hook, before FML events. */
    public static void beforeModPreInit() {
        require(current != null && Integer.valueOf(0).equals(current.get("modPreInitDispatchCount")),
                "Original mod preInit dispatch was repeated or observed out of phase");
        current.put("groovyInitializationReturned", true);
        captureGroovy(current, currentLogs, currentEngine, currentSandbox, currentGroovy);
        boolean ready;
        try { requireCleanGroovy(current, currentLogs); ready = true; }
        catch (IllegalStateException incomplete) { ready = false; }
        current.put("groovyInitializationReady", ready);
        var boundary = new LinkedHashMap<String,Object>();
        for (String key : List.of("groovyInitializationReady", "groovyInitializationReturned", "groovyDiagnostics",
                "groovyCompilationFailure", "groovyLoggedError", "nativeGroovyErrors", "nativeScriptIndex",
                "nativeGroovyInitialization", "nativeMapperAdmissionBound", "candidateCompilationStarted",
                "candidateAdmissionViolations", "candidateResourceFailure", "candidateLinkageFailure", "groovyObservationFailure"))
            if (current.containsKey(key)) boundary.put(key, current.get(key));
        boundary.put("preInitializationDispatched", false);
        boundary.put("materialInitializationComplete", false);
        boundary.put("loaderState", "PREINITIALIZATION");
        boundary.put("nativeDiagnostics", List.copyOf((List<?>)current.get("nativeDiagnostics")));
        boundary.put("nativeDiagnosticsComplete", current.get("nativeDiagnosticsComplete"));
        current.put("groovyBoundary", boundary);
        current.put("modPreInitDispatchCount", 1);
        current.put("preInitializationDispatchStarted", true);
        current.put("stage", "original-mod-preinit-dispatch");
    }

    public static void afterModPreInit() {
        current.put("preInitializationDispatched", true);
        current.put("preInitializationDispatchReturned", true);
        current.put("stage", "original-mod-preinit-dispatch-returned");
    }

    public static void afterRegistryEvents() {
        current.put("nonRecipeRegistryEventsReturned", true);
        current.put("stage", "original-non-recipe-registry-events-returned");
    }

    private static void captureGroovy(Map<String,Object> observed, NativeProgramObservations logs,
            Object engine, Object sandbox, Class<?> groovy) {
        var loader = Loader.instance();
        observed.put("groovyDiagnostics", logs.diagnostics());
        observed.put("groovyCompilationFailure", logs.compilationFailure());
        observed.put("groovyLoggedError", logs.hasErrors());
        try {
            observed.put("nativeGroovyErrors", logs.nativeErrors());
            observed.put("nativeScriptIndex", NativeProgramObservations.scriptIndex(engine));
            var support = Class.forName("com.cleanroommc.groovyscript.compat.mods.ModSupport", false, Launch.classLoader);
            var state = new LinkedHashMap<String,Object>();
            state.put("modSupportFrozen", field(support, "frozen", null));
            var owner = (ModContainer)field(groovy, "scriptMod", null);
            state.put("scriptOwnerAssigned", owner != null);
            if (owner != null) {
                state.put("scriptOwner", owner.getModId());
                state.put("scriptOwnerClass", owner.getClass().getName());
                state.put("scriptOwnerIsOriginalContainer", loader.getIndexedModList().get(owner.getModId()) == owner);
            }
            Map<?,?> currentBindings = (Map<?,?>)field(sandbox.getClass(), "bindings", sandbox);
            var named = new TreeMap<String,String>();
            for (var entry : currentBindings.entrySet()) named.put(entry.getKey().toString(), entry.getValue().getClass().getName());
            state.put("bindings", named);
            observed.put("nativeGroovyInitialization", state);
            observed.put("candidateAdmissionViolations", MaterialCallGate.violations());
            // Existing gate counters are passive call-site witnesses, not proof
            // of effective registry membership or successful native return.
            observed.put("candidateDispatchObservations", MaterialCallGate.observations());
            observed.put("candidateResourceFailure", MaterialCallGate.resourceFailure());
            observed.put("candidateLinkageFailure", MaterialCallGate.linkageFailure());
        } catch (Exception | LinkageError failure) {
            observed.put("groovyObservationFailure", failure.getClass().getName() + ": " + failure.getMessage());
        }
    }

    private static void requireCleanGroovy(Map<String,Object> observed, NativeProgramObservations logs) {
        require(!logs.hasErrors(), "Original Groovy initialization logged native errors");
        requireCoveredGroovy(observed);
    }
    private static void requireCoveredGroovy(Map<String,Object> observed) {
        @SuppressWarnings("unchecked")
        var state = (Map<String,Object>)observed.get("nativeGroovyInitialization");
        require(state != null && Boolean.TRUE.equals(state.get("modSupportFrozen"))
                        && Boolean.TRUE.equals(state.get("scriptOwnerIsOriginalContainer")),
                "Original Groovy compatibility and script ownership did not initialize");
        require(MaterialCallGate.violations().isEmpty() && !MaterialCallGate.resourceFailure() && !MaterialCallGate.linkageFailure(),
                "Saved Groovy execution reported an incomplete admitted context");
        @SuppressWarnings("unchecked")
        var scripts = (List<Map<String,Object>>)observed.get("nativeScriptIndex");
        // Original findScripts/loadScript excludes files whose native
        // preprocessors returned false (for example CLIENT recipes on SERVER).
        // Read the recorded result without re-evaluating a condition or compiling
        // an excluded file. Native errors remain in the separate verdict.
        require(scripts != null && !scripts.isEmpty() && scripts.stream().allMatch(row ->
                        Boolean.TRUE.equals(row.get("preprocessorCheckFailed"))
                                ? row.get("preprocessors") instanceof List<?> rules && !rules.isEmpty()
                                : Boolean.FALSE.equals(row.get("preprocessorCheckFailed"))
                                        && (Boolean.TRUE.equals(row.get("classDefined"))
                                            || Boolean.TRUE.equals(observed.get("groovyCompilationFailure")))),
                "Original Groovy initialization did not cover every applicable indexed script");
    }
    private static Object field(Class<?> type, String name, Object owner) throws Exception {
        var field = type.getDeclaredField(name); field.setAccessible(true); return field.get(owner);
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalStateException(message); }
}
