package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.PackCoreReflection;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.item.ItemStack;
import supercritical.api.nuclear.fission.FissionFuelRegistry;
import supercritical.api.nuclear.fission.IFissionFuelStats;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Supercritical's identified fuel physics and concrete rod bindings. */
public final class SupercriticalFissionFuelRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "supercritical-fission-fuel-registry"; }
    @Override public String categoryId() { return "supercritical-fission-fuels"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<String, IFissionFuelStats> identified = PackCoreReflection.staticMap(
            FissionFuelRegistry.class, "IDENTIFIED_FUELS"
        );
        Map<ItemStack, IFissionFuelStats> rods = PackCoreReflection.staticMap(
            FissionFuelRegistry.class, "FUELS"
        );
        for (Map.Entry<String, IFissionFuelStats> entry : identified.entrySet()) {
            IFissionFuelStats stats = required(entry.getValue());
            JsonObject row = stats(stats, encoder);
            row.addProperty("record_type", "supercritical-fission-fuel-stats");
            row.addProperty("registry_id", entry.getKey());
            row.addProperty("registry_id_matches", entry.getKey().equals(stats.getId()));
            records.add(row);
        }
        for (Map.Entry<ItemStack, IFissionFuelStats> entry : rods.entrySet()) {
            IFissionFuelStats stats = required(entry.getValue());
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "supercritical-fission-fuel-rod");
            row.add("item", encoder.encode(entry.getKey()));
            row.addProperty("fuel_id", stats.getId());
            row.addProperty("public_lookup_consistent", FissionFuelRegistry.getFissionFuel(entry.getKey()) == stats);
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "supercritical-fission-fuel-registry-authority");
        authority.addProperty("identified_fuel_count", identified.size());
        authority.addProperty("rod_binding_count", rods.size());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static IFissionFuelStats required(IFissionFuelStats stats) {
        if (stats == null) throw new IllegalStateException("Supercritical fission fuel stats are null");
        return stats;
    }

    private static JsonObject stats(IFissionFuelStats value, StableValueEncoder encoder) {
        JsonObject row = new JsonObject();
        row.addProperty("id", value.getId());
        row.addProperty("runtime_class", value.getClass().getName());
        row.addProperty("maximum_temperature", value.getMaxTemperature());
        row.addProperty("duration", value.getDuration());
        row.add("slow_neutron_capture_cross_section", encoder.encode(Double.valueOf(value.getSlowNeutronCaptureCrossSection())));
        row.add("fast_neutron_capture_cross_section", encoder.encode(Double.valueOf(value.getFastNeutronCaptureCrossSection())));
        row.add("slow_neutron_fission_cross_section", encoder.encode(Double.valueOf(value.getSlowNeutronFissionCrossSection())));
        row.add("fast_neutron_fission_cross_section", encoder.encode(Double.valueOf(value.getFastNeutronFissionCrossSection())));
        row.add("released_neutrons", encoder.encode(Double.valueOf(value.getReleasedNeutrons())));
        row.add("required_neutrons", encoder.encode(Double.valueOf(value.getRequiredNeutrons())));
        row.add("released_heat_energy", encoder.encode(Double.valueOf(value.getReleasedHeatEnergy())));
        row.add("decay_rate", encoder.encode(Double.valueOf(value.getDecayRate())));
        row.add("neutron_generation_time", encoder.encode(Double.valueOf(value.getNeutronGenerationTime())));
        row.addProperty("neutron_generation_time_category", value.getNeutronGenerationTimeCategory());
        row.add("fast_fission_multiplier", encoder.encode(Double.valueOf(value.getFastFissionMultiplier())));
        row.add("slow_fission_multiplier", encoder.encode(Double.valueOf(value.getSlowFissionMultiplier())));
        JsonArray depleted = new JsonArray();
        for (ItemStack item : value.getDepletedFuels()) depleted.add(encoder.encode(item));
        row.add("depleted_fuels", depleted);
        return row;
    }
}
