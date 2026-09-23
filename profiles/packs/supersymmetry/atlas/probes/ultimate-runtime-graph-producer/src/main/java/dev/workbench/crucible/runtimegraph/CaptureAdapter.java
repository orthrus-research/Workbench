package dev.workbench.crucible.runtimegraph;

/** One category-local, checkpoint-bound runtime observation adapter. */
public interface CaptureAdapter {
    String adapterId();
    String categoryId();
    String checkpointId();
    AdapterSnapshot snapshot();
}
