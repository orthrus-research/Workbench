package research.orthrus.axiom;

import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.objects.ObjectOpenHashSet;
import java.util.*;
import java.util.function.Consumer;

/** Isolated producer execution, explicitly NOT a vanilla/Forge/pack bootstrap. */
final class FluidEnvironment implements AutoCloseable {
    enum Events { ISOLATED_NO_LISTENERS, UNRESOLVED_PACK_LISTENERS }
    record Registration(String name, int id) {}
    record Tooltip(NativeFluid fluid, FluidMaterial material, FluidState state) {}
    record Log(String level, String template, List<Object> arguments) {}
    private static final ThreadLocal<FluidEnvironment> CURRENT = new ThreadLocal<>();
    private final RegistryRuntime runtime;
    private final Events events;
    private final Map<NativeLocation, FluidStorageKey> keys = new Object2ObjectOpenHashMap<>();
    private final Collection<NativeLocation> sprites = new ObjectOpenHashSet<>();
    private final List<Object> effects = new ArrayList<>();
    private final Map<NativeFluid, List<Tooltip>> tooltips = new HashMap<>();
    private final Map<String,Integer> addonColors = new HashMap<>();
    private final FluidRegistryState registry;
    private final FluidRegistrationService registration = new FluidRegistrationService();
    private final FluidUnifier unifier = new FluidUnifier();
    private final FluidStorageKeys storageKeys;
    private final FluidAttributes attributes;
    private final MarkerMaterialRegistry markers = new MarkerMaterialRegistry();
    private PrefixDependencies.Inputs prefixInputs;
    private final Logger logger = new Logger();
    private boolean closed;
    private final Consumer<Registration> observer;

    private FluidEnvironment(RegistryRuntime runtime, Events events, Consumer<Registration> observer) {
        if (CURRENT.get() != null) throw new IllegalStateException("Nested fluid evaluation is not admitted");
        this.runtime = Objects.requireNonNull(runtime); runtime.identity();
        this.events = Objects.requireNonNull(events); this.observer = Objects.requireNonNull(observer);
        CURRENT.set(this);
        try {
            FluidDomain.activate();
            registry = new FluidRegistryState(this);
            storageKeys = new FluidStorageKeys();
            attributes = new FluidAttributes();
        } catch (Throwable failure) { CURRENT.remove(); throw failure; }
    }
    static FluidEnvironment isolatedProducer(RegistryRuntime runtime) {
        return new FluidEnvironment(runtime, Events.ISOLATED_NO_LISTENERS, event -> {});
    }
    static FluidEnvironment unresolvedPack(RegistryRuntime runtime) {
        return new FluidEnvironment(runtime, Events.UNRESOLVED_PACK_LISTENERS, event -> {});
    }
    // Event callback fixtures are not qualification of the original Forge event dispatcher.
    static FluidEnvironment eventProbe(RegistryRuntime runtime, Consumer<Registration> observer) {
        return new FluidEnvironment(runtime, Events.ISOLATED_NO_LISTENERS, observer);
    }
    static FluidEnvironment current() {
        var environment = CURRENT.get();
        if (environment == null || environment.closed) throw new IllegalStateException("No active native fluid evaluation");
        return environment;
    }
    RegistryRuntime runtime() { requireActive(); return runtime; }
    Map<NativeLocation,FluidStorageKey> keys() { requireActive(); return keys; }
    FluidStorageKeys storageKeys() { requireActive(); return storageKeys; }
    FluidAttributes attributes() { requireActive(); return attributes; }
    MarkerMaterialRegistry markers() { requireActive(); return markers; }
    void bindPrefixes(PrefixDependencies.Inputs inputs) {
        requireActive();
        if (prefixInputs != null) throw new IllegalStateException("Prefix inputs are already bound");
        prefixInputs = Objects.requireNonNull(inputs);
    }
    PrefixDependencies.Inputs prefixInputs() {
        requireActive();
        if (prefixInputs == null) throw new Failure("incomplete", "material.ore-prefix",
                "Native prefix source-catalog/configuration inputs are not bound");
        return prefixInputs;
    }
    FluidRegistryState registry() { requireActive(); return registry; }
    FluidRegistrationService registration() { requireActive(); return registration; }
    FluidUnifier unifier() { requireActive(); return unifier; }
    Collection<NativeLocation> sprites() { requireActive(); return sprites; }
    Map<String,Integer> addonColors() { requireActive(); return addonColors; }
    Logger logger() { requireActive(); return logger; }
    List<Object> effects() { requireActive(); return List.copyOf(effects); }
    void postRegistration(String name, int id) {
        requireActive(); var event = new Registration(name,id); effects.add(event);
        if (events == Events.UNRESOLVED_PACK_LISTENERS)
            throw new Failure("incomplete", "fluid.registration-listeners", "Pack listener universe is unresolved; earlier registry effects are retained");
        observer.accept(event);
    }
    void registerTooltip(NativeFluid fluid, FluidMaterial material, FluidState state) {
        requireActive();
        // Upstream stores lazy suppliers here; rendering does not run during registration.
        var tooltip = new Tooltip(fluid, material, state);
        tooltips.computeIfAbsent(fluid, ignored -> new ArrayList<>(1)).add(tooltip);
        effects.add(tooltip);
    }
    List<Tooltip> tooltipBindings(NativeFluid fluid) { requireActive(); return List.copyOf(tooltips.getOrDefault(fluid,List.of())); }
    boolean topAddonsLoaded() {
        requireActive();
        if (events != Events.ISOLATED_NO_LISTENERS) throw new Failure("incomplete", "fluid.addon-composition", "Addon composition is unresolved");
        return false; // Explicit isolated producer universe contains no addons; never inferred for a pack.
    }
    FluidRegistration<FluidStorageKey,NativeFluid> newStorage() {
        requireActive();
        return new FluidRegistration<>(storageKeys.LIQUID, FluidBuilder::new,
                material -> logger.error("{} already has an associated fluid for material {}", material));
    }
    void registerMaterialFluids() {
        requireActive();
        for (MaterialState state : runtime.materials().getRegisteredMaterials()) {
            FluidProperty property = state.getProperties().getProperty(FluidDomain.FLUID);
            if (property != null) property.registerFluids(FluidMaterial.require(state));
        }
    }
    Map<String,Object> coverage() {
        requireActive();
        return Map.of("scope", "isolated-native-fluid-producer", "minecraftLaunched", false,
                "vanillaBootstrapExecuted", false, "packEventsExecuted", false, "wholePackParity", false,
                "registrationUniverse", events.name(), "presentation", "unresolved-lazy-tooltip-bindings");
    }
    final class Logger {
        void warn(String template, Object... args) { log("warn", template, args); }
        void error(String template, Object... args) { log("error", template, args); }
        private void log(String level, String template, Object[] args) {
            requireActive(); effects.add(new Log(level, template, Collections.unmodifiableList(Arrays.asList(args.clone()))));
        }
    }
    private void requireActive() { if (closed || CURRENT.get() != this) throw new IllegalStateException("Fluid evaluation is not active"); }
    @Override public void close() { requireActive(); closed = true; CURRENT.remove(); }
}
