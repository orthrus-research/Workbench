package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.PackCoreReflection;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import supersymmetry.common.event.DimensionBreathabilityHandler;
import supersymmetry.common.event.DimensionBreathabilityHandler.BreathabilityInfo;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Dimension hazard policy from SuSy's final breathability table and space fallback. */
public final class SusyDimensionBreathabilityAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-dimension-breathability"; }
    @Override public String categoryId() { return "susy-dimension-breathability"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<Integer, BreathabilityInfo> registry = PackCoreReflection.staticMap(
            DimensionBreathabilityHandler.class, "dimensionBreathabilityMap"
        );
        for (Map.Entry<Integer, BreathabilityInfo> entry : registry.entrySet()) {
            records.add(info("susy-dimension-breathability", entry.getKey(), entry.getValue(), encoder));
        }
        Field fallbackField = PackCoreReflection.field(
            DimensionBreathabilityHandler.class, "SPACE", BreathabilityInfo.class, true
        );
        BreathabilityInfo fallback = (BreathabilityInfo) PackCoreReflection.value(fallbackField, null);
        records.add(info("susy-space-breathability-fallback", null, fallback, encoder));
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-dimension-breathability-authority");
        authority.addProperty("explicit_dimension_count", registry.size());
        authority.addProperty("space_fallback_present", fallback != null);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static JsonObject info(
        String type, Integer dimension, BreathabilityInfo value, StableValueEncoder encoder
    ) {
        if (value == null) throw new IllegalStateException("SuSy breathability entry is null");
        JsonObject row = new JsonObject();
        row.addProperty("record_type", type);
        if (dimension != null) row.addProperty("dimension", dimension);
        row.addProperty("damage_type", value.damageType.getDamageType());
        row.add("default_damage", encoder.encode(Double.valueOf(value.defaultDamage)));
        return row;
    }
}
