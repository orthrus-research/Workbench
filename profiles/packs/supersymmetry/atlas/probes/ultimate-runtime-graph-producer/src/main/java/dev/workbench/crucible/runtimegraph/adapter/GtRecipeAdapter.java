package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.recipes.RecipeMap;

import java.util.ArrayList;
import java.util.List;

/** Complete finite GT recipe occurrence union with lookup/category provenance. */
public final class GtRecipeAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "gt-recipes"; }
    @Override public String categoryId() { return "transformation-recipe"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        GtRecipeCapture.beginIdentityIndex();
        for (RecipeMap<?> map : GtRecipeCapture.maps()) {
            records.addAll(GtRecipeCapture.recipes(map, encoder));
        }
        if (records.isEmpty()) throw new IllegalStateException("GT recipe occurrence union is empty");
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
