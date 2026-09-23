package dev.workbench.crucible.runtimegraph;

import dev.workbench.crucible.runtimegraph.adapter.FmlModContainerAdapter;
import dev.workbench.crucible.runtimegraph.adapter.AppliedEnergisticsDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.BetterQuestingDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeFluidAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeCraftingRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeOreDictionaryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeSmeltingRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SerializedRuntimeValueAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtDynamicRecipeRuleAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtMachineRecipeMapAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtMaterialAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtMaterialFluidRelationAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtMetaTileEntityAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtOrePrefixAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtProceduralMachineRuleAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtRecipeMapAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtUnificationAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtWorldgenAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GroovyExecutionTraceAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GroovyMutationEventAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GroovySourceCatalogAdapter;
import dev.workbench.crucible.runtimegraph.adapter.PyrotechRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SupercriticalCoolantRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SupercriticalFissionFuelRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SupercriticalModeratorRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyCatalystRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyCelestialRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyDimensionBreathabilityAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyFactionBaselineAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyParticleRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyRocketBlueprintRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyRocketComponentRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyRocketFuelRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.SusyWorldPlanetRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.TechGunsDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.IndustrialRenewalDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.OpenComputersDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.RfToolsDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.RsGaugesDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.BiomesOPlentyDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.RecurrentComplexDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.CaveGeneratorDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.RealizedWorldObservationAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ProgramReloadObservationAdapter;
import dev.workbench.crucible.runtimegraph.client.ClientJeiAdapter;

import org.apache.logging.log4j.Logger;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** Runs each adapter only at its declared lifecycle checkpoint. */
public final class CaptureCoordinator {
    public static final String MATERIAL_FROZEN = "material-manager-frozen";
    public static final String SERVER_STARTED = "server-started";
    public static final String POST_START_END_TICK = "post-start-end-tick";
    public static final String CLIENT_SETTLED_END_TICK = "client-settled-end-tick";

    private final CaptureConfiguration configuration;
    private final CapturePublisher publisher;
    private final Logger logger;
    private final List<CaptureAdapter> adapters;
    private final Set<String> completedCheckpoints = new LinkedHashSet<String>();
    private boolean committed;

    public CaptureCoordinator(CaptureConfiguration configuration, Logger logger) {
        this.configuration = configuration;
        this.publisher = new CapturePublisher(configuration);
        this.logger = logger;
        List<CaptureAdapter> selected;
        if (configuration.isClientPresentationCapture()) {
            selected = new ArrayList<CaptureAdapter>(Arrays.<CaptureAdapter>asList(
                new ClientJeiAdapter(
                    "jei-ingredient-types", "client-presentation-ingredient-catalog"
                ),
                new ClientJeiAdapter(
                    "jei-ingredients", "client-presentation-ingredient-catalog"
                ),
                new ClientJeiAdapter(
                    "jei-recipe-categories", "client-presentation-recipe-catalog"
                ),
                new ClientJeiAdapter(
                    "jei-recipe-wrappers", "client-presentation-recipe-catalog"
                ),
                new ClientJeiAdapter(
                    "jei-recipe-catalysts", "client-presentation-recipe-catalog"
                ),
                new ClientJeiAdapter(
                    "client-resource-localization-state", "client-resource-state"
                )
            ));
        } else {
            selected = new ArrayList<CaptureAdapter>(Arrays.<CaptureAdapter>asList(
            new FmlModContainerAdapter(),
            new ForgeRegistryAdapter(),
            new ForgeFluidAdapter(),
            new SerializedRuntimeValueAdapter(),
            new ForgeOreDictionaryAdapter(),
            new GtMaterialAdapter(),
            new GtOrePrefixAdapter(),
            new GtUnificationAdapter(),
            new GtMaterialFluidRelationAdapter(),
            new ForgeCraftingRecipeAdapter(),
            new ForgeSmeltingRecipeAdapter(),
            new GtRecipeMapAdapter(),
            new GtRecipeAdapter(),
            new GtDynamicRecipeRuleAdapter(),
            new GtMetaTileEntityAdapter(),
            new GtMachineRecipeMapAdapter(),
            new GtProceduralMachineRuleAdapter(),
            new SusyCatalystRegistryAdapter(),
            new SusyParticleRegistryAdapter(),
            new SusyRocketFuelRegistryAdapter(),
            new SusyRocketComponentRegistryAdapter(),
            new SusyRocketBlueprintRegistryAdapter(),
            new SusyCelestialRegistryAdapter(),
            new SusyWorldPlanetRegistryAdapter(),
            new SusyDimensionBreathabilityAdapter(),
            new SusyFactionBaselineAdapter(),
            new SupercriticalFissionFuelRegistryAdapter(),
            new SupercriticalCoolantRegistryAdapter(),
            new SupercriticalModeratorRegistryAdapter(),
            new GtWorldgenAdapter(),
            new GroovySourceCatalogAdapter(),
            new GroovyExecutionTraceAdapter(),
            new GroovyMutationEventAdapter(),
            new PyrotechRecipeAdapter(),
            new BetterQuestingDefinitionAdapter(),
            new AppliedEnergisticsDefinitionAdapter(),
            new TechGunsDefinitionAdapter(),
            new IndustrialRenewalDefinitionAdapter(),
            new RsGaugesDefinitionAdapter(),
            new RfToolsDefinitionAdapter(),
            new OpenComputersDefinitionAdapter(),
            new BiomesOPlentyDefinitionAdapter(),
            new RecurrentComplexDefinitionAdapter(),
            new CaveGeneratorDefinitionAdapter(),
            new RealizedWorldObservationAdapter()
            ));
            if (configuration.isProgramReloadExperiment()) {
                selected.add(new ProgramReloadObservationAdapter());
            }
        }
        this.adapters = selected;
    }

    public synchronized void captureCheckpoint(String checkpoint) {
        if (committed || !completedCheckpoints.add(checkpoint)) return;
        logger.info("Workbench runtime graph capturing checkpoint {}", checkpoint);
        for (CaptureAdapter adapter : adapters) {
            if (!checkpoint.equals(adapter.checkpointId())) continue;
            CategoryResult result;
            try {
                result = CategoryResult.capture(configuration, adapter);
            } catch (Throwable failure) {
                logger.error("Runtime graph adapter failed: " + adapter.adapterId(), failure);
                result = CategoryResult.failure(configuration, adapter, failure);
            }
            publisher.writeCategory(result);
            logger.info(
                "Workbench runtime graph adapter {} completed with {}",
                adapter.adapterId(),
                result.status()
            );
        }
        if (POST_START_END_TICK.equals(checkpoint)
            || CLIENT_SETTLED_END_TICK.equals(checkpoint)) commit();
    }

    private void commit() {
        List<String> required = new ArrayList<String>();
        for (CaptureAdapter adapter : adapters) required.add(adapter.adapterId());
        publisher.commit(required);
        committed = true;
        logger.info("Workbench runtime graph capture committed to {}", configuration.getOutput());
    }

    public synchronized boolean isCommitted() { return committed; }
}
