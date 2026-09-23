package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Machine-to-recipe-map relationships, kept independent of machine classification. */
public final class GtMachineRecipeMapAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "gt-machine-recipe-maps"; }
    @Override public String categoryId() { return "machine-transformation-binding"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (GtMteCapture.Entry entry : GtMteCapture.entries()) {
            records.addAll(GtMteCapture.bindings(entry));
        }
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
    }
}
