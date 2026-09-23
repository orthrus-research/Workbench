// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom;

/** Material-registration block only. Dependency ports are mandatory; this does
 * not imply that any GT, addon, pack or marker producer has been supplied. */
final class MaterialLifecycle {
    interface Dependencies {
        void initializeMarkers();
        void registerMaterials();
        MaterialState aluminium();
    }
    private static final org.apache.logging.log4j.Logger logger = org.apache.logging.log4j.LogManager.getLogger("axiom.material-lifecycle");
    private final RegistryRuntime runtime;
    private final MaterialEvents events;
    private final Dependencies dependencies;
    private boolean started;
    MaterialLifecycle(RegistryRuntime runtime, MaterialEvents events, Dependencies dependencies) {
        this.runtime=java.util.Objects.requireNonNull(runtime);
        this.events=java.util.Objects.requireNonNull(events);
        this.dependencies=java.util.Objects.requireNonNull(dependencies);
    }
    void execute() {
        if (started || runtime.materials().getPhase()!=MaterialPhase.PRE)
            throw new IllegalStateException("Material lifecycle requires one fresh registry universe");
        started=true;
        registerMaterials();
    }
    private void registerMaterials() {


        dependencies.initializeMarkers();

        // First, register other mods' Registries
        MaterialRegistryManager managerInternal = runtime.materials();

        logger.info("Registering material registries");
        events.post(events.construct("research.orthrus.axiom.materialevents.MaterialRegistryEvent"));

        // First, register CEu Materials
        managerInternal.unfreezeRegistries();
        Object materialEvent = events.construct("research.orthrus.axiom.materialevents.MaterialEvent");
        logger.info("Registering GTCEu Materials");
        dependencies.registerMaterials();
        runtime.materials()
                .getRegistry("gregtech")
                .setFallbackMaterial(dependencies.aluminium());

        // Then, register addon Materials
        logger.info("Registering addon Materials");
        events.post(materialEvent);

        // Fire Post-Material event, intended for when Materials need to be iterated over in-full before freezing
        // Block entirely new Materials from being added in the Post event
        managerInternal.closeRegistries();
        events.post(events.construct("research.orthrus.axiom.materialevents.PostMaterialEvent"));

        // Freeze Material Registry before processing Items, Blocks, and Fluids
        managerInternal.freezeRegistries();
    }
}
