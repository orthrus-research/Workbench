package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.unification.material.Material;
import net.minecraft.util.Tuple;
import supersymmetry.api.rocketry.fuels.RocketFuelEntry;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Final SuSy rocket-fuel registry and ordered material composition. */
public final class SusyRocketFuelRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-rocket-fuel-registry"; }
    @Override public String categoryId() { return "susy-rocket-fuels"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<String, RocketFuelEntry> registry = RocketFuelEntry.getFuelRegistry();
        if (registry == null) throw new IllegalStateException("SuSy rocket fuel registry is null");
        for (Map.Entry<String, RocketFuelEntry> entry : registry.entrySet()) {
            RocketFuelEntry fuel = entry.getValue();
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-rocket-fuel");
            row.addProperty("registry_name", entry.getKey());
            row.addProperty("entry_registry_name", fuel.getRegistryName());
            row.addProperty("registry_name_matches", entry.getKey().equals(fuel.getRegistryName()));
            row.add("density", encoder.encode(Double.valueOf(fuel.getDensity())));
            row.add("specific_impulse_vacuum", encoder.encode(Double.valueOf(fuel.getsIVacuum())));
            row.add("specific_impulse_per_pressure", encoder.encode(Double.valueOf(fuel.getsIPerPressure())));
            row.add("specific_impulse", encoder.encode(Double.valueOf(fuel.getSpecificImpulse())));
            row.add("specific_impulse_variation", encoder.encode(Double.valueOf(fuel.getSIVariation())));
            JsonArray composition = new JsonArray();
            int ordinal = 0;
            for (Tuple<Material, Integer> component : fuel.getComposition()) {
                JsonObject value = new JsonObject();
                value.addProperty("ordinal", ordinal++);
                value.addProperty("material", component.getFirst().getRegistryName());
                value.addProperty("amount", component.getSecond());
                composition.add(value);
            }
            row.add("composition", composition);
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-rocket-fuel-registry-authority");
        authority.addProperty("entry_count", registry.size());
        authority.addProperty("authority", "RocketFuelEntry.getFuelRegistry");
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
