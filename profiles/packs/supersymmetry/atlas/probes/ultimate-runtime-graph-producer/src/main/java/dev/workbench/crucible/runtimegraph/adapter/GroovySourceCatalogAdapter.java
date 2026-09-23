package dev.workbench.crucible.runtimegraph.adapter;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;

import java.util.Collections;

/** Exact source resources selected by the runtime GroovyScript installation. */
public final class GroovySourceCatalogAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "groovy-source-catalog"; }
    @Override public String categoryId() { return "pack-program-source"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        return new AdapterSnapshot(
            GroovySourceCatalog.capture().records(),
            Collections.<String>emptyList(),
            0
        );
    }
}
