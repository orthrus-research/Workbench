package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.reload.ProgramReloadExperiment;

import java.util.List;

/** Exact before/after snapshots and mutation summaries for one controlled reload. */
public final class ProgramReloadObservationAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "program-reload-observations"; }
    @Override public String categoryId() { return "program-reload-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        List<JsonObject> records = ProgramReloadExperiment.records();
        return new AdapterSnapshot(records, ProgramReloadExperiment.diagnostics(), 0);
    }
}
