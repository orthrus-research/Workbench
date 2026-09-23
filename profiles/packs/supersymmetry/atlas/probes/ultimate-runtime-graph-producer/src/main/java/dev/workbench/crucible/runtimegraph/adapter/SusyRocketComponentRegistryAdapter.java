package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import supersymmetry.api.rocketry.components.AbstractComponent;
import supersymmetry.api.rocketry.components.MaterialCost;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** SuSy rocket-component prototype registry without executing detection predicates. */
public final class SusyRocketComponentRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-rocket-component-registry"; }
    @Override public String categoryId() { return "susy-rocket-components"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<AbstractComponent<?>> registry = AbstractComponent.getRegistry();
        Map<String, Class<? extends AbstractComponent<?>>> names = AbstractComponent.getNameRegistry();
        if (registry == null || names == null) {
            throw new IllegalStateException("SuSy rocket component registry is unavailable");
        }
        for (AbstractComponent<?> component : registry) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-rocket-component");
            row.addProperty("name", component.getName());
            row.addProperty("type", component.getType());
            row.addProperty("runtime_class", component.getClass().getName());
            row.add("mass", encoder.encode(Double.valueOf(component.getMass())));
            row.add("radius", encoder.encode(Double.valueOf(component.getRadius())));
            row.add("height", encoder.encode(Double.valueOf(component.getHeight())));
            row.addProperty("localization_key", component.getLocalizationKey());
            Class<? extends AbstractComponent<?>> named = names.get(component.getName());
            if (named == null) row.add("name_registry_class", JsonNull.INSTANCE);
            else row.addProperty("name_registry_class", named.getName());
            row.addProperty("name_registry_consistent", named == component.getClass());
            JsonArray costs = new JsonArray();
            int ordinal = 0;
            for (MaterialCost cost : component.getMaterials()) {
                JsonObject value = new JsonObject();
                value.addProperty("ordinal", ordinal++);
                value.add("cost", encoder.encode(cost));
                costs.add(value);
            }
            row.add("material_costs", costs);
            row.add("detection_predicate", executable(component.getDetectionPredicate()));
            row.add("compatibility_predicate", executable(component.getCompatabilityValidationPredicate()));
            row.add("slot_predicate", executable(component.getComponentSlotValidator()));
            row.addProperty("predicate_execution_invoked", false);
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-rocket-component-registry-authority");
        authority.addProperty("prototype_count", registry.size());
        authority.addProperty("name_class_count", names.size());
        authority.addProperty("registry_locked", AbstractComponent.getRegistryLock());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static JsonObject executable(Object value) {
        JsonObject row = new JsonObject();
        row.addProperty(
            "runtime_class",
            value == null ? "<null>" : StableValueEncoder.stableClassName(value.getClass())
        );
        row.addProperty("behavior_body_captured", false);
        return row;
    }
}
