package research.orthrus.axiom.nativeconstruction;

import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.objects.ObjectOpenHashSet;
import java.util.*;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fml.common.FMLLog;

/** One bounded native producer context. Fluid registration and dispatch belong to Cleanroom. */
final class FluidEnvironment implements AutoCloseable {
    record Tooltip(Fluid fluid, FluidMaterial material, FluidState state) {}
    private static final ThreadLocal<FluidEnvironment> CURRENT = new ThreadLocal<>();
    private final RegistryRuntime runtime = new RegistryRuntime();
    private final FluidRegistryAccess registry = new FluidRegistryAccess();
    private final Map<ResourceLocation, FluidStorageKey> keys = new Object2ObjectOpenHashMap<>();
    private final Collection<ResourceLocation> sprites = new ObjectOpenHashSet<>();
    private final Map<String, Integer> addonColors = new HashMap<>();
    private final List<Tooltip> tooltips = new ArrayList<>();
    private final FluidRegistrationService registration = new FluidRegistrationService();
    private final FluidUnifier unifier = new FluidUnifier();
    private final MarkerMaterialRegistry markers = new MarkerMaterialRegistry();
    private final FluidStorageKeys storageKeys;
    private final FluidAttributes attributes;
    private final Logger logger = new Logger();
    private boolean closed;
    private PrefixDependencies.Inputs prefixInputs;

    FluidEnvironment() {
        if (CURRENT.get() != null) throw new IllegalStateException("Nested native material evaluation");
        CURRENT.set(this);
        try { FluidDomain.activate(); storageKeys = new FluidStorageKeys(); attributes = new FluidAttributes(); }
        catch (Throwable failure) { CURRENT.remove(); throw failure; }
    }
    static FluidEnvironment current() {
        var value = CURRENT.get();
        if (value == null || value.closed) throw new IllegalStateException("Native material evaluation is not active");
        return value;
    }
    RegistryRuntime runtime() { active(); return runtime; }
    FluidRegistryAccess registry() { active(); return registry; }
    Map<ResourceLocation, FluidStorageKey> keys() { active(); return keys; }
    FluidStorageKeys storageKeys() { active(); return storageKeys; }
    FluidAttributes attributes() { active(); return attributes; }
    MarkerMaterialRegistry markers() { active(); return markers; }
    FluidRegistrationService registration() { active(); return registration; }
    FluidUnifier unifier() { active(); return unifier; }
    Collection<ResourceLocation> sprites() { active(); return sprites; }
    Map<String, Integer> addonColors() { active(); return addonColors; }
    Logger logger() { active(); return logger; }
    List<Tooltip> tooltips() { active(); return List.copyOf(tooltips); }
    void registerTooltip(Fluid fluid, FluidMaterial material, FluidState state) {
        active(); tooltips.add(new Tooltip(fluid, material, state));
    }
    PrefixDependencies.Inputs prefixInputs() {
        active();
        if (prefixInputs == null) throw new Failure("incomplete", "material.ore-prefix", "Full material catalog/configuration is not bound");
        return prefixInputs;
    }
    void bindCatalog(PrefixDependencies.Inputs inputs) {
        active();
        if (prefixInputs != null || runtime.materials().getPhase() != MaterialPhase.PRE)
            throw new IllegalStateException("Catalog inputs must be bound once before material registration");
        prefixInputs = Objects.requireNonNull(inputs);
    }
    String[] modPriorities() {
        if (!(prefixInputs() instanceof CatalogInputs catalog))
            throw new Failure("incomplete", "item.configuration", "Native catalog configuration is required");
        return catalog.modPriorities().toArray(String[]::new);
    }
    boolean topAddonsLoaded() {
        active(); return false; // This admitted producer universe explicitly excludes addon execution.
    }
    FluidRegistration<FluidStorageKey, Fluid> newStorage() {
        active(); return new FluidRegistration<>(storageKeys.LIQUID, FluidBuilder::new,
                material -> logger.error("{} already has an associated fluid for material {}", material));
    }
    void registerMaterialFluids() {
        active();
        for (MaterialState state : runtime.materials().getRegisteredMaterials()) {
            FluidProperty property = state.getProperties().getProperty(FluidDomain.FLUID);
            if (property != null) property.registerFluids(FluidMaterial.require(state));
        }
    }
    final class Logger {
        void warn(String template, Object... args) { active(); FMLLog.log.warn(template, args); }
        void error(String template, Object... args) { active(); FMLLog.log.error(template, args); }
    }
    private void active() { if (current() != this) throw new IllegalStateException("Wrong native material evaluation"); }
    @Override public void close() { active(); closed = true; CURRENT.remove(); }
}
