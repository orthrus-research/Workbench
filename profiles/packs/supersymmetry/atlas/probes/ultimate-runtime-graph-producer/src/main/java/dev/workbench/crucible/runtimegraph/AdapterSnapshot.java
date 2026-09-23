package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Canonically ordered output and explicit projection frontiers for one sample. */
public final class AdapterSnapshot {
    private final JsonArray records;
    private final List<String> diagnostics;
    private final int unsupportedValueCount;
    private final String recordsSha256;

    public AdapterSnapshot(
        List<JsonObject> records,
        List<String> diagnostics,
        int unsupportedValueCount
    ) {
        if (records == null || diagnostics == null || unsupportedValueCount < 0) {
            throw new IllegalArgumentException("invalid adapter snapshot");
        }
        this.records = CanonicalJson.sortedRecords(records);
        this.diagnostics = new ArrayList<String>(diagnostics);
        Collections.sort(this.diagnostics);
        if (this.diagnostics.size() != new java.util.LinkedHashSet<String>(
            this.diagnostics
        ).size()) {
            throw new IllegalArgumentException("adapter snapshot diagnostics contain duplicates");
        }
        this.unsupportedValueCount = unsupportedValueCount;
        this.recordsSha256 = CanonicalJson.sha256(this.records);
    }

    public JsonArray records() { return records.deepCopy(); }
    public List<String> diagnostics() { return new ArrayList<String>(diagnostics); }
    public int unsupportedValueCount() { return unsupportedValueCount; }
    public int recordCount() { return records.size(); }
    public String recordsSha256() { return recordsSha256; }
}
