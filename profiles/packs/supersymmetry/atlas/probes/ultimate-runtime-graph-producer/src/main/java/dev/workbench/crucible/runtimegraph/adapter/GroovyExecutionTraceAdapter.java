package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Ordered stage and per-script execution facts, independent from mutations. */
public final class GroovyExecutionTraceAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "groovy-execution-trace"; }
    @Override public String categoryId() { return "pack-program-execution"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        ProgramProvenanceTrace.requireSettled();
        GroovySourceCatalog.Catalog catalog = GroovySourceCatalog.capture();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (ProgramProvenanceTrace.Epoch epoch : ProgramProvenanceTrace.epochs()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "groovy-execution-epoch");
            row.addProperty("epoch_ordinal", epoch.getOrdinal());
            row.addProperty("epoch_kind", epoch.getKind());
            row.addProperty("begin_sequence", epoch.getBeginSequence());
            row.addProperty("end_sequence", epoch.getEndSequence());
            row.addProperty("stage", epoch.getStage());
            row.addProperty("reloadable", epoch.isReloadable());
            row.addProperty("initial_load", epoch.isInitialLoad());
            if (epoch.getTriggerClassName() == null) {
                row.add("trigger_class_name", JsonNull.INSTANCE);
            } else {
                row.addProperty("trigger_class_name", epoch.getTriggerClassName());
            }
            row.addProperty("completed", epoch.isCompleted());
            records.add(row);
        }
        for (ProgramProvenanceTrace.Execution execution : ProgramProvenanceTrace.executions()) {
            String source = catalog.resolveSource(execution.getClassName());
            if (source == null) {
                throw new IllegalStateException(
                    "executed Groovy class does not resolve to a source resource: "
                        + execution.getClassName()
                );
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "groovy-script-execution");
            row.addProperty("execution_ordinal", execution.getOrdinal());
            row.addProperty("epoch_ordinal", execution.getEpochOrdinal());
            row.addProperty("begin_sequence", execution.getBeginSequence());
            row.addProperty("end_sequence", execution.getEndSequence());
            row.addProperty("execution_kind", execution.getKind());
            row.addProperty("compiled_class_name", execution.getClassName());
            if (execution.getParentExecutionOrdinal() == null) {
                row.add("parent_execution_ordinal", JsonNull.INSTANCE);
            } else {
                row.addProperty(
                    "parent_execution_ordinal",
                    execution.getParentExecutionOrdinal()
                );
            }
            if (execution.getTriggerClassName() == null) {
                row.add("trigger_class_name", JsonNull.INSTANCE);
            } else {
                row.addProperty("trigger_class_name", execution.getTriggerClassName());
            }
            nullable(row, "event_bus", execution.getEventBus());
            nullable(row, "event_priority", execution.getEventPriority());
            nullable(row, "declared_event_class", execution.getDeclaredEventClass());
            row.addProperty("source_path", source);
            row.addProperty("mutation_count", execution.getMutationCount());
            row.addProperty("completed", execution.isCompleted());
            row.addProperty("outcome", execution.getFailureClass() == null ? "success" : "failure");
            if (execution.getFailureClass() == null) {
                row.add("failure_class", JsonNull.INSTANCE);
            } else {
                row.addProperty("failure_class", execution.getFailureClass());
            }
            records.add(row);
        }
        if (records.isEmpty()) throw new IllegalStateException("Groovy execution trace is empty");
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
    }

    private static void nullable(JsonObject row, String key, String value) {
        if (value == null) row.add(key, JsonNull.INSTANCE);
        else row.addProperty(key, value);
    }
}
