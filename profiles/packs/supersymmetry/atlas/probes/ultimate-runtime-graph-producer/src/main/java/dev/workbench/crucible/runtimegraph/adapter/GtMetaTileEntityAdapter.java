package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import java.util.ArrayList;
import java.util.List;

/** Frozen GT MetaTileEntity identities and their own prototype properties. */
public final class GtMetaTileEntityAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "gt-meta-tile-entities"; }
    @Override public String categoryId() { return "machine-core"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (GtMteCapture.Entry entry : GtMteCapture.entries()) {
            records.add(GtMteCapture.mte(entry, encoder));
        }
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
