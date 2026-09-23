package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import supersymmetry.api.rocketry.components.AbstractComponent;
import supersymmetry.api.rocketry.rockets.AbstractRocketBlueprint;
import supersymmetry.api.rocketry.rockets.RocketStage;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Registered SuSy rocket blueprints and stage/component topology. */
public final class SusyRocketBlueprintRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-rocket-blueprint-registry"; }
    @Override public String categoryId() { return "susy-rocket-blueprints"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<String, AbstractRocketBlueprint> registry = AbstractRocketBlueprint.getBlueprintsRegistry();
        if (registry == null) throw new IllegalStateException("SuSy rocket blueprint registry is null");
        for (Map.Entry<String, AbstractRocketBlueprint> entry : registry.entrySet()) {
            AbstractRocketBlueprint blueprint = entry.getValue();
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-rocket-blueprint");
            row.addProperty("registry_name", entry.getKey());
            row.addProperty("name", blueprint.getName());
            row.addProperty("runtime_class", blueprint.getClass().getName());
            row.addProperty("related_entity", blueprint.getRelatedEntity().toString());
            row.addProperty("full_blueprint", blueprint.isFullBlueprint());
            row.add("afs_success_chance", encoder.encode(Double.valueOf(blueprint.getAFSSuccessChance())));
            row.add("mass", encoder.encode(Double.valueOf(blueprint.getMass())));
            row.add("maximum_radius", encoder.encode(Double.valueOf(blueprint.getMaxRadius())));
            row.add("total_radius_mismatch", encoder.encode(Double.valueOf(blueprint.getTotalRadiusMismatch())));
            row.add("height", encoder.encode(Double.valueOf(blueprint.getHeight())));
            row.add("ignition_stages", encoder.encode(blueprint.getIgnitionStages()));
            JsonArray stages = new JsonArray();
            int stageOrdinal = 0;
            for (RocketStage stage : blueprint.getStages()) {
                JsonObject stageRow = new JsonObject();
                stageRow.addProperty("ordinal", stageOrdinal++);
                stageRow.addProperty("name", stage.getName());
                stageRow.addProperty("localization_key", stage.getLocalizationKey());
                stageRow.addProperty("populated", stage.isPopulated());
                stageRow.add("component_limits", encoder.encode(stage.getComponentLimits()));
                JsonArray slots = new JsonArray();
                for (Map.Entry<String, List<AbstractComponent<?>>> slot : stage.getComponents().entrySet()) {
                    JsonObject slotRow = new JsonObject();
                    slotRow.addProperty("slot", slot.getKey());
                    JsonArray components = new JsonArray();
                    int ordinal = 0;
                    for (AbstractComponent<?> component : slot.getValue()) {
                        JsonObject componentRow = new JsonObject();
                        componentRow.addProperty("ordinal", ordinal++);
                        componentRow.addProperty("component", component.getName());
                        components.add(componentRow);
                    }
                    slotRow.add("components", components);
                    slots.add(slotRow);
                }
                stageRow.add("component_slots", slots);
                stageRow.addProperty(
                    "validation_function_class",
                    StableValueEncoder.stableClassName(
                        stage.getComponentValidationFunction().getClass()
                    )
                );
                stageRow.addProperty("validation_function_executed", false);
                stages.add(stageRow);
            }
            row.add("stages", stages);
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-rocket-blueprint-registry-authority");
        authority.addProperty("blueprint_count", registry.size());
        authority.addProperty("registry_locked", AbstractRocketBlueprint.getRegistryLock());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
