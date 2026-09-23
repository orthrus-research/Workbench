package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.PackCoreReflection;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.util.ResourceLocation;
import supersymmetry.common.faction.FactionBaselineRegistry;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** SuSy faction baseline classifications without player-dependent evaluation. */
public final class SusyFactionBaselineAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-faction-baselines"; }
    @Override public String categoryId() { return "susy-faction-baselines"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<ResourceLocation, Integer> registry = PackCoreReflection.staticMap(
            FactionBaselineRegistry.class, "BASELINE_HATE"
        );
        for (Map.Entry<ResourceLocation, Integer> entry : registry.entrySet()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-faction-baseline");
            row.addProperty("faction", entry.getKey().toString());
            row.addProperty("baseline_hate", entry.getValue());
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-faction-baseline-authority");
        authority.addProperty("faction_count", registry.size());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
