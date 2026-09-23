package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraftforge.fluids.Fluid;
import supercritical.api.nuclear.fission.CoolantRegistry;
import supercritical.api.nuclear.fission.ICoolantStats;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

/** Supercritical coolant bindings and thermodynamic classifications. */
public final class SupercriticalCoolantRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "supercritical-coolant-registry"; }
    @Override public String categoryId() { return "supercritical-coolants"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Collection<Fluid> fluids = CoolantRegistry.getAllCoolants();
        if (fluids == null) throw new IllegalStateException("Supercritical coolant registry is null");
        for (Fluid fluid : fluids) {
            ICoolantStats stats = CoolantRegistry.getCoolant(fluid);
            if (stats == null) throw new IllegalStateException("Supercritical coolant stats are null");
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "supercritical-coolant");
            row.addProperty("fluid", fluid.getName());
            row.addProperty("runtime_class", stats.getClass().getName());
            Fluid hot = stats.getHotCoolant();
            row.addProperty("hot_fluid", hot == null ? "<null>" : hot.getName());
            Fluid inverse = CoolantRegistry.originalFluid(stats);
            row.addProperty("inverse_fluid", inverse == null ? "<null>" : inverse.getName());
            row.addProperty("inverse_registry_consistent", inverse == fluid);
            row.add("specific_heat_capacity", encoder.encode(Double.valueOf(stats.getSpecificHeatCapacity())));
            row.add("moderator_factor", encoder.encode(Double.valueOf(stats.getModeratorFactor())));
            row.add("slow_absorption_factor", encoder.encode(Double.valueOf(stats.getSlowAbsorptionFactor())));
            row.add("fast_absorption_factor", encoder.encode(Double.valueOf(stats.getFastAbsorptionFactor())));
            row.add("cooling_factor", encoder.encode(Double.valueOf(stats.getCoolingFactor())));
            row.add("boiling_point", encoder.encode(Double.valueOf(stats.getBoilingPoint())));
            row.add("heat_of_vaporization", encoder.encode(Double.valueOf(stats.getHeatOfVaporization())));
            row.addProperty("accumulates_hydrogen", stats.accumulatesHydrogen());
            row.add("mass", encoder.encode(Double.valueOf(stats.getMass())));
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "supercritical-coolant-registry-authority");
        authority.addProperty("coolant_count", fluids.size());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
