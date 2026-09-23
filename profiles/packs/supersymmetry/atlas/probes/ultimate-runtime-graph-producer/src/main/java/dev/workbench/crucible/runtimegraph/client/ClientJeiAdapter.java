package dev.workbench.crucible.runtimegraph.client;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;

/** One immutable view of the shared settled client-presentation snapshot. */
public final class ClientJeiAdapter implements CaptureAdapter {
    private final String adapterId;
    private final String categoryId;

    public ClientJeiAdapter(String adapterId, String categoryId) {
        this.adapterId = adapterId;
        this.categoryId = categoryId;
    }

    @Override public String adapterId() { return adapterId; }
    @Override public String categoryId() { return categoryId; }
    @Override public String checkpointId() {
        return CaptureCoordinator.CLIENT_SETTLED_END_TICK;
    }
    @Override public AdapterSnapshot snapshot() {
        return ClientJeiCapture.snapshot(adapterId);
    }
}
