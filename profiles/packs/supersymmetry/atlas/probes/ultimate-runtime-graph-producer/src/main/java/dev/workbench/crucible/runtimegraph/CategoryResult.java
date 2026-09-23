package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.List;

/** Repeated-sample outcome for one category adapter. */
public final class CategoryResult {
    private final String adapterId;
    private final String status;
    private final JsonObject value;

    private CategoryResult(String adapterId, String status, JsonObject value) {
        this.adapterId = adapterId;
        this.status = status;
        this.value = value;
    }

    public static CategoryResult capture(
        CaptureConfiguration configuration,
        CaptureAdapter adapter
    ) {
        AdapterSnapshot first = adapter.snapshot();
        AdapterSnapshot second = adapter.snapshot();
        boolean stable = first.recordsSha256().equals(second.recordsSha256());
        List<String> diagnostics = new ArrayList<String>(first.diagnostics());
        for (String diagnostic : second.diagnostics()) {
            if (!diagnostics.contains(diagnostic)) diagnostics.add(diagnostic);
        }
        if (!stable) diagnostics.add("unstable-repeated-sample");
        int unsupported = Math.max(
            first.unsupportedValueCount(), second.unsupportedValueCount()
        );
        String status = stable && unsupported == 0 && diagnostics.isEmpty()
            ? "complete"
            : "partial";

        JsonObject result = base(configuration, adapter);
        result.addProperty("status", status);
        result.addProperty("stable", stable);
        JsonArray samples = new JsonArray();
        samples.add(sample(1, first));
        samples.add(sample(2, second));
        result.add("samples", samples);
        result.addProperty("records_sha256", first.recordsSha256());
        result.addProperty("record_count", first.recordCount());
        result.addProperty("unsupported_value_count", unsupported);
        result.add("diagnostics", strings(diagnostics));
        result.add("records", first.records());
        result.addProperty("result_sha256", CanonicalJson.sha256(result));
        return new CategoryResult(adapter.adapterId(), status, result);
    }

    public static CategoryResult failure(
        CaptureConfiguration configuration,
        CaptureAdapter adapter,
        Throwable failure
    ) {
        JsonObject result = base(configuration, adapter);
        result.addProperty("status", "failed");
        result.addProperty("stable", false);
        result.add("samples", new JsonArray());
        result.addProperty("records_sha256", Hashing.sha256(new byte[] {'[', ']'}));
        result.addProperty("record_count", 0);
        result.addProperty("unsupported_value_count", 0);
        JsonArray diagnostics = new JsonArray();
        diagnostics.add("adapter-exception");
        result.add("diagnostics", diagnostics);
        result.add("records", new JsonArray());
        JsonObject detail = new JsonObject();
        detail.addProperty("exception_class", failure.getClass().getName());
        detail.addProperty("message", bounded(failure.getMessage()));
        result.add("failure", detail);
        result.addProperty("result_sha256", CanonicalJson.sha256(result));
        return new CategoryResult(adapter.adapterId(), "failed", result);
    }

    private static JsonObject base(
        CaptureConfiguration configuration,
        CaptureAdapter adapter
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("format", "workbench-crucible-runtime-category-result-v1");
        result.addProperty("schema_version", 1);
        result.addProperty("capture_id", configuration.getCaptureId());
        result.addProperty("launch_id", configuration.getLaunchId());
        result.addProperty("input_manifest_sha256", configuration.getInputManifestSha256());
        result.addProperty("candidate_lock_sha256", configuration.getCandidateLockSha256());
        result.addProperty("adapter_profile_sha256", configuration.getAdapterProfileSha256());
        result.addProperty("physical_side", configuration.getPhysicalSide());
        result.addProperty("adapter_id", adapter.adapterId());
        result.addProperty("category_id", adapter.categoryId());
        result.addProperty("checkpoint_id", adapter.checkpointId());
        return result;
    }

    private static JsonObject sample(int ordinal, AdapterSnapshot snapshot) {
        JsonObject result = new JsonObject();
        result.addProperty("ordinal", ordinal);
        result.addProperty("record_count", snapshot.recordCount());
        result.addProperty("records_sha256", snapshot.recordsSha256());
        result.addProperty("unsupported_value_count", snapshot.unsupportedValueCount());
        result.add("diagnostics", strings(snapshot.diagnostics()));
        return result;
    }

    private static JsonArray strings(List<String> values) {
        java.util.Collections.sort(values);
        JsonArray result = new JsonArray();
        for (String value : values) result.add(value);
        return result;
    }

    private static String bounded(String value) {
        if (value == null) return "";
        value = value.replace('\r', ' ').replace('\n', ' ').replace('\0', ' ');
        return value.length() <= 4096 ? value : value.substring(0, 4096);
    }

    public String adapterId() { return adapterId; }
    public String status() { return status; }
    public JsonObject value() { return value.deepCopy(); }
}
