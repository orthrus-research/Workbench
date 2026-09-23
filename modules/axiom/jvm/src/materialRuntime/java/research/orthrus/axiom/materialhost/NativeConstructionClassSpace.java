package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.LoaderState;
import net.minecraftforge.fml.common.FMLModContainer;
import java.util.*;

/** Original construction dispatch after the accepted native selection boundary. */
public final class NativeConstructionClassSpace {
    @FunctionalInterface
    interface SelectionObserver { void observe() throws Exception; }
    private static SelectionObserver selectionObserver;
    private static Map<String,Object> observed;
    private static boolean selectionObserved;
    private NativeConstructionClassSpace() {}

    static void advance(Loader loader, List<String> injected, Map<String,Object> observations,
            SelectionObserver observer) throws Exception {
        require(selectionObserver == null, "Native construction cannot be reused");
        selectionObserver = observer; observed = observations;
        observed.put("constructionReady", false);
        observed.put("constructionDispatchStarted", false);
        observed.put("constructionMethodReturned", false);
        try {
            Loader.class.getMethod(NativeLoaderPrefix.CONSTRUCTION_METHOD, List.class).invoke(loader, injected);
            observed.put("constructionMethodReturned", true);
            require(loader.isInState(LoaderState.PREINITIALIZATION), "Original construction did not reach the preInit boundary");
            observed.put("loaderState", "PREINITIALIZATION");
            observed.put("stage", "construction-returned");
            Class<?> groovy = Class.forName("com.cleanroommc.groovyscript.GroovyScript", false, Launch.classLoader);
            boolean loaded = (boolean)groovy.getMethod("isSandboxLoaded").invoke(null);
            observed.put("nativeGroovySandboxCreated", loaded);
            require(loaded, "Original Groovy construction did not create its sandbox");
            // The original constructor creates/configures the engine. Script
            // execution starts on the later PREINITIALIZATION event, not this
            // final Loader state transition; this method dispatches no such event.
            observed.put("preInitializationDispatched", false);
            observeConstructionEffects(groovy);
            observed.put("nativeConfiguration", NativeConfigurationObservations.collect(loader));
        } finally {
            try {
                observeContainers(loader);
                observeMemory("afterConstructionAttempt");
            }
            catch (Throwable failure) {
                observed.put("constructionObservationFailure", failure.getClass().getName() + ": " + failure.getMessage());
            }
        }
    }

    /** Inserted immediately after original late-mixin setup, with all locals intact. */
    public static void beforeConstruction() throws Exception {
        require(selectionObserver != null && !selectionObserved, "Native construction selection observation is out of phase");
        selectionObserver.observe(); selectionObserved = true;
        var selection = new LinkedHashMap<String,Object>(observed);
        selection.put("nativeDiagnostics", List.copyOf((List<?>)observed.get("nativeDiagnostics")));
        observed.put("selectionStage", selection);
        observed.put("schema", "axiom.native-construction-stage.v1");
        observed.put("stage", "original-loader-construction");
        observeMemory("beforeConstruction");
    }

    /** Inserted at the original construction event invocation without changing its operands. */
    public static void constructionDispatchStarted() {
        require(selectionObserved && Loader.instance().isInState(LoaderState.CONSTRUCTING),
                "Original construction dispatch is out of phase");
        observed.put("constructionDispatchStarted", true);
        observed.put("constructionDispatched", true);
    }

    private static void observeContainers(Loader loader) throws Exception {
        var rows = new ArrayList<Map<String,Object>>();
        for (var container : loader.getModList()) {
            var row = new LinkedHashMap<String,Object>();
            row.put("id", container.getModId());
            row.put("state", loader.getModState(container).name());
            row.put("container", container.getClass().getName());
            if (container instanceof FMLModContainer) {
                Object instance = container.getMod();
                row.put("instanceCreated", instance != null);
                if (instance != null) {
                    row.put("instanceClass", instance.getClass().getName());
                    row.put("instanceCodeSource", NativeEarlyClassSpace.sourceFile(
                            instance.getClass().getProtectionDomain().getCodeSource().getLocation()).toString());
                }
            }
            rows.add(row);
        }
        observed.put("constructedMods", rows);
        var active = loader.activeModContainer();
        observed.put("nativeActiveOwner", active == null ? "none" : active.getModId());
        for (var state : LoaderState.values()) if (loader.isInState(state)) observed.put("loaderState", state.name());
        // A later native failure must not erase earlier Groovy construction.
        // Only inspect a class whose original constructor already returned.
        for (var container : loader.getModList()) {
            if (container.getModId().equals("groovyscript") && container.getMod() != null
                    && loader.getModState(container) == LoaderState.ModState.CONSTRUCTED) {
                observed.put("nativeGroovySandboxCreated", container.getMod().getClass()
                        .getMethod("isSandboxLoaded").invoke(null));
            }
        }
    }

    /** Inspect fields reached by original construction; never invoke a later initializer. */
    private static void observeConstructionEffects(Class<?> groovy) {
        try {
            Object sandbox = field(groovy, "sandbox", null);
            Object engine = field(sandbox.getClass(), "engine", sandbox);
            Map<?,?> bindings = (Map<?,?>)field(sandbox.getClass(), "bindings", sandbox);
            Class<?> support = cached("com.cleanroommc.groovyscript.compat.mods.ModSupport");
            var plugins = new ArrayList<Map<String,Object>>();
            for (Object plugin : (Set<?>)field(support, "externalPluginClasses", null)) {
                Class<?> type = (Class<?>)plugin;
                plugins.add(Map.of("class", type.getName(), "codeSource", NativeEarlyClassSpace.sourceFile(
                        type.getProtectionDomain().getCodeSource().getLocation()).toString()));
            }
            plugins.sort(Comparator.comparing(row -> row.get("class").toString()));
            var bindingsByClass = new TreeMap<String,String>();
            for (var binding : bindings.entrySet()) bindingsByClass.put(binding.getKey().toString(), binding.getValue().getClass().getName());
            var groovyState = new LinkedHashMap<String,Object>();
            groovyState.put("sandboxClass", sandbox.getClass().getName());
            groovyState.put("engineClass", engine.getClass().getName());
            groovyState.put("bindings", bindingsByClass);
            groovyState.put("externalPlugins", plugins);
            groovyState.put("modSupportFrozen", field(support, "frozen", null));
            groovyState.put("scriptOwnerAssigned", field(groovy, "scriptMod", null) != null);
            observed.put("nativeGroovyConstruction", groovyState);

            Map<?,?> stockLoaders = (Map<?,?>)field(cached("cam72cam.immersiverailroading.registry.DefinitionManager"), "stockLoaders", null);
            Class<?> ir = cached("cam72cam.immersiverailroading.ImmersiveRailroading");
            Object config = field(cached("supercritical.common.SCConfigHolder"), "misc", null);
            observed.put("nativeSusyConstruction", Map.of(
                    "irInstanceClass", field(ir, "instance", null).getClass().getName(),
                    "irStockLoaderKeys", stockLoaders.keySet().stream().map(Object::toString).sorted().toList(),
                    "supercriticalMaterialModifications", field(config.getClass(), "enableMaterialModifications", config)));
        } catch (Throwable failure) {
            observed.put("constructionEffectsObservationFailure", failure.getClass().getName() + ": " + failure.getMessage());
        }
    }

    private static Class<?> cached(String name) {
        Class<?> type = Launch.classLoader.getCachedClasses().get(name);
        require(type != null, "Construction observation requires an already defined class: " + name);
        return type;
    }

    private static Object field(Class<?> type, String name, Object owner) throws Exception {
        var field = type.getDeclaredField(name); field.setAccessible(true); return field.get(owner);
    }

    /** Read aggregate retained-cache sizes without clearing caches or requesting GC. */
    private static void observeMemory(String phase) {
        try { collectMemory(phase); }
        catch (Throwable failure) {
            observed.put(phase + "MemoryObservationFailure", failure.getClass().getName() + ": " + failure.getMessage());
        }
    }

    private static void collectMemory(String phase) throws Exception {
        var runtime = Runtime.getRuntime();
        var row = new LinkedHashMap<String,Object>();
        row.put("heapMaximumBytes", runtime.maxMemory());
        row.put("heapCommittedBytes", runtime.totalMemory());
        row.put("heapUsedBytes", runtime.totalMemory() - runtime.freeMemory());
        row.put("nativeDefinedClasses", Launch.classLoader.getCachedClasses().size());
        var field = Launch.classLoader.getClass().getSuperclass().getDeclaredField("resourceCache");
        field.setAccessible(true);
        var resources = (Map<?,?>)field.get(Launch.classLoader);
        long bytes = 0;
        for (Object value : resources.values()) bytes += ((byte[])value).length;
        row.put("originalResourceCacheEntries", resources.size());
        row.put("originalResourceCacheBytes", bytes);
        Class<?> remapper = Class.forName("net.minecraftforge.fml.common.asm.transformers.deobf.FMLDeobfuscatingRemapper", false, Launch.classLoader);
        Object instance = remapper.getField("INSTANCE").get(null);
        var mappings = new LinkedHashMap<String,Object>();
        for (String name : List.of("rawFieldMaps", "rawMethodMaps", "fieldNameMaps", "methodNameMaps")) {
            var member = remapper.getDeclaredField(name); member.setAccessible(true);
            var maps = (Map<?,?>)member.get(instance);
            long entries = 0;
            for (Object value : maps.values()) entries += ((Map<?,?>)value).size();
            mappings.put(name, Map.of("owners", maps.size(), "entries", entries));
        }
        row.put("originalRemapperMaps", mappings);
        observed.put(phase + "Memory", row);
        var options = java.lang.management.ManagementFactory.getPlatformMXBean(com.sun.management.HotSpotDiagnosticMXBean.class);
        row.put("stringDeduplicationEnabled", Boolean.parseBoolean(options.getVMOption("UseStringDeduplication").getValue()));
        row.put("g1GarbageCollectorEnabled", Boolean.parseBoolean(options.getVMOption("UseG1GC").getValue()));
    }
    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
