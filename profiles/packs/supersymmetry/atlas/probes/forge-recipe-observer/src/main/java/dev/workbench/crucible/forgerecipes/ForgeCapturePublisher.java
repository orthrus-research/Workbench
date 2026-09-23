package dev.workbench.crucible.forgerecipes;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.Hashing;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Publishes retained Crucible evidence; a seal is not a native-success receipt. */
public final class ForgeCapturePublisher {
    private static final String PREFIX = "workbench.runtimeGraph.";
    private static final String PREPARATION_POLICY = "forge-loli-original-capability-materialization-v1";
    private static final Gson GSON = new GsonBuilder().serializeNulls().disableHtmlEscaping().create();
    private static final Map<String, String> CATEGORIES = new java.util.TreeMap<String, String>();
    private static Path staging;
    private static Path output;
    private static JsonObject binding;
    private static boolean published;
    private static boolean ownsDestination;
    private static boolean preparationDeclared;
    private static boolean preparationStarted;
    private static boolean preparationRetained;

    static {
        CATEGORIES.put("gt-item-matching", "transformation-item-matching");
        CATEGORIES.put("gt-item-names", "reference-item-names");
        CATEGORIES.put("gt-ordinary-item-matching", "transformation-ordinary-item-matching");
        CATEGORIES.put("gt-machine-recipe-maps", "machine-transformation-binding");
        CATEGORIES.put("gt-meta-tile-entities", "machine-core");
        CATEGORIES.put("gt-recipe-maps", "transformation-recipe-map");
        CATEGORIES.put("gt-recipes", "transformation-recipe");
    }

    private ForgeCapturePublisher() {}

    public static boolean isEnabled() {
        String value = System.getProperty(PREFIX + "enabled", "false");
        if (!"true".equals(value) && !"false".equals(value)) {
            throw new IllegalArgumentException("capture enabled must be an exact Boolean");
        }
        return "true".equals(value);
    }

    private static String required(String key, String pattern) {
        String value = System.getProperty(PREFIX + key);
        if (value == null || !value.matches(pattern)) {
            throw new IllegalArgumentException("missing/invalid capture property " + key);
        }
        return value;
    }

    private static synchronized void reserve() throws IOException {
        if (binding != null) return;
        if (!isEnabled()) throw new IllegalStateException("capture is not enabled");
        if (!"false".equals(System.getProperty(PREFIX + "compatibility_only", "false"))
                || !"none".equals(System.getProperty(PREFIX + "experiment", "none"))) {
            throw new IllegalArgumentException("Forge observer does not support compatibility or experiment modes");
        }
        JsonObject selected = new JsonObject();
        for (String field : Arrays.asList("capture_id", "launch_id")) {
            selected.addProperty(field, required(field, "[A-Za-z0-9._:-]{1,160}"));
        }
        for (String field : Arrays.asList("input_manifest_sha256", "candidate_lock_sha256", "adapter_profile_sha256")) {
            selected.addProperty(field, required(field, "[0-9a-f]{64}"));
        }
        selected.addProperty("physical_side", "dedicated_server");
        Path input = Paths.get(required("input_manifest_path", "[^\\x00\\r\\n]+"));
        if (!input.isAbsolute() || !Files.isRegularFile(input, LinkOption.NOFOLLOW_LINKS)
                || Files.size(input) > 32L * 1024L * 1024L) {
            throw new IllegalArgumentException("input manifest must be a bounded absolute regular file");
        }
        byte[] inputBytes = Files.readAllBytes(input);
        if (!Hashing.sha256(inputBytes).equals(selected.get("input_manifest_sha256").getAsString())) {
            throw new IllegalArgumentException("input manifest bytes differ from the launch binding");
        }
        JsonObject inputs = new JsonParser().parse(new String(inputBytes, StandardCharsets.UTF_8)).getAsJsonObject();
        for (String field : Arrays.asList("capture_id", "launch_id", "physical_side",
                "candidate_lock_sha256", "adapter_profile_sha256")) {
            if (!selected.get(field).equals(inputs.get(field))) {
                throw new IllegalArgumentException("input manifest has a different " + field);
            }
        }
        if (inputs.has("observation_preparation")) {
            JsonObject expected = new JsonObject();
            expected.addProperty("policy", PREPARATION_POLICY);
            expected.addProperty("phase", "before-two-effective-samples");
            if (!expected.equals(inputs.get("observation_preparation"))) {
                throw new IllegalArgumentException("unsupported observation preparation declaration");
            }
            preparationDeclared = true;
        }
        Path destination = Paths.get(required("output", "[^\\x00\\r\\n]+")).normalize();
        if (!destination.isAbsolute() || destination.getParent() == null
                || !Files.isDirectory(destination.getParent(), LinkOption.NOFOLLOW_LINKS)
                || !destination.getParent().equals(destination.getParent().toRealPath())
                || Files.exists(destination, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("capture output must be absent beneath an existing real directory");
        }
        Path temporary = destination.resolveSibling(destination.getFileName() + ".staging-"
                + selected.get("capture_id").getAsString());
        Files.createDirectory(temporary);
        output = destination;
        staging = temporary;
        binding = selected;
        JsonObject opened = base("workbench-runtime-graph-capture-open-v1");
        write("capture-open.json", CanonicalJson.bytes(opened));
    }

    private static JsonObject base(String format) {
        // The original Minecraft server bundles Gson 2.8.0, whose deepCopy is
        // package-private. Binding values are immutable JSON primitives.
        JsonObject value = new JsonObject();
        for (Map.Entry<String, com.google.gson.JsonElement> entry : binding.entrySet()) {
            value.add(entry.getKey(), entry.getValue());
        }
        value.addProperty("format", format);
        value.addProperty("schema_version", 1);
        return value;
    }

    private static JsonArray records(List<Map<String, Object>> rows) {
        if (rows == null) throw new IllegalArgumentException("missing category records");
        List<JsonObject> converted = new ArrayList<JsonObject>();
        for (Map<String, Object> row : rows) {
            if (row == null || !(row.get("record_type") instanceof String)) {
                throw new IllegalArgumentException("category record has no record_type");
            }
            converted.add(GSON.toJsonTree(row).getAsJsonObject());
        }
        return CanonicalJson.sortedRecords(converted);
    }

    /** Check the exact launch declaration before invoking any mutating initializer. */
    public static synchronized void requirePreparation() throws IOException {
        reserve();
        if (!preparationDeclared) throw new IllegalStateException("capture preparation was not declared");
        if (published || preparationStarted) throw new IllegalStateException("capture preparation already started or published");
        preparationStarted = true;
    }

    /** Retain actual mutations separately from the two later effective-state samples. */
    public static synchronized void retainPreparation(Map<String, Object> preparation) throws IOException {
        reserve();
        if (published || preparationRetained) throw new IllegalStateException("preparation already retained or capture published");
        if (!preparationDeclared || !preparationStarted || preparation == null
                || !PREPARATION_POLICY.equals(preparation.get("policy"))
                || !"complete".equals(preparation.get("state"))) {
            throw new IllegalArgumentException("preparation does not match its declared complete policy");
        }
        JsonObject payload = base("workbench-forge-item-capability-preparation-v1");
        payload.add("preparation", GSON.toJsonTree(preparation));
        write("capability-preparation.json", CanonicalJson.bytes(payload));
        preparationRetained = true;
    }

    public static synchronized void publish(
            Map<String, List<Map<String, Object>>> first,
            Map<String, List<Map<String, Object>>> second,
            Map<String, Object> checkpointMetadata) throws IOException {
        reserve();
        if (published) throw new IllegalStateException("capture already published");
        if (preparationDeclared != preparationRetained
                || (preparationDeclared && !PREPARATION_POLICY.equals(checkpointMetadata.get("observation_preparation_policy")))
                || (!preparationDeclared && checkpointMetadata.containsKey("observation_preparation_policy"))) {
            throw new IllegalStateException("capture preparation or checkpoint policy differs from the launch declaration");
        }
        if (!first.keySet().equals(CATEGORIES.keySet()) || !second.keySet().equals(CATEGORIES.keySet())) {
            throw new IllegalArgumentException("capture category inventory differs");
        }
        if (!"post-start-end-tick".equals(checkpointMetadata.get("checkpoint_id"))
                || !"dedicated_server".equals(checkpointMetadata.get("physical_side"))
                || !Boolean.TRUE.equals(checkpointMetadata.get("server_started"))
                || !"ServerTickEvent.END".equals(checkpointMetadata.get("actual_event"))) {
            throw new IllegalArgumentException("capture did not reach its declared lifecycle checkpoint");
        }
        write("checkpoint.json", CanonicalJson.bytes(GSON.toJsonTree(checkpointMetadata)));
        JsonArray categories = new JsonArray();
        for (Map.Entry<String, String> entry : CATEGORIES.entrySet()) {
            String adapter = entry.getKey();
            JsonArray a = records(first.get(adapter));
            JsonArray b = records(second.get(adapter));
            String digest = CanonicalJson.sha256(a);
            if (!digest.equals(CanonicalJson.sha256(b))) {
                throw new IllegalStateException("unstable repeated category sample: " + adapter);
            }
            if (("gt-recipes".equals(adapter) || "gt-recipe-maps".equals(adapter)
                    || "gt-item-matching".equals(adapter)
                    || "gt-ordinary-item-matching".equals(adapter)) && a.size() == 0) {
                throw new IllegalStateException("required category is empty: " + adapter);
            }
            JsonObject category = base("workbench-crucible-runtime-category-result-v1");
            category.addProperty("adapter_id", adapter);
            category.addProperty("category_id", entry.getValue());
            category.addProperty("checkpoint_id", "post-start-end-tick");
            category.addProperty("status", "complete");
            category.addProperty("stable", true);
            category.addProperty("record_count", a.size());
            category.addProperty("records_sha256", digest);
            category.addProperty("unsupported_value_count", 0);
            category.add("diagnostics", new JsonArray());
            category.add("records", a);
            JsonArray samples = new JsonArray();
            for (int ordinal = 1; ordinal <= 2; ordinal++) {
                JsonObject sample = new JsonObject();
                sample.addProperty("ordinal", ordinal);
                sample.addProperty("record_count", a.size());
                sample.addProperty("records_sha256", digest);
                sample.addProperty("unsupported_value_count", 0);
                sample.add("diagnostics", new JsonArray());
                samples.add(sample);
            }
            category.add("samples", samples);
            category.addProperty("result_sha256", CanonicalJson.sha256(category));
            write(adapter + ".json", CanonicalJson.bytes(category));
            JsonObject descriptor = new JsonObject();
            descriptor.addProperty("adapter_id", adapter);
            descriptor.addProperty("status", "complete");
            descriptor.addProperty("file", adapter + ".json");
            categories.add(descriptor);
        }
        JsonObject manifest = base("workbench-runtime-graph-raw-bundle-v1");
        manifest.addProperty("producer", "dev.workbench.crucible.forgerecipes");
        manifest.add("categories", categories);
        JsonArray payloads = new JsonArray();
        List<String> filenames = new ArrayList<String>();
        filenames.add("capture-open.json");
        filenames.add("checkpoint.json");
        if (preparationRetained) filenames.add("capability-preparation.json");
        for (String adapter : CATEGORIES.keySet()) filenames.add(adapter + ".json");
        Collections.sort(filenames);
        for (String filename : filenames) {
            byte[] bytes = Files.readAllBytes(staging.resolve(filename));
            JsonObject descriptor = new JsonObject();
            descriptor.addProperty("file", filename);
            descriptor.addProperty("size", bytes.length);
            descriptor.addProperty("sha256", Hashing.sha256(bytes));
            payloads.add(descriptor);
        }
        manifest.add("payloads", payloads);
        manifest.addProperty("manifest_sha256", CanonicalJson.sha256(manifest));
        write("manifest.json", CanonicalJson.bytes(manifest));
        // CREATE_NEW directory reservation cannot replace another capture.
        // Readers refuse this directory until its final completion marker.
        // Keep the original staging bytes as failure/recovery evidence.
        Files.createDirectory(output);
        ownsDestination = true;
        filenames.add("manifest.json");
        for (String filename : filenames) {
            writeTo(output.resolve(filename), Files.readAllBytes(staging.resolve(filename)));
        }
        writeTo(output.resolve(".capture-complete"), new byte[0]);
        published = true;
    }

    /** Keep failure evidence in staging; never publish a completion marker. */
    public static synchronized void failure(Throwable problem) {
        try {
            reserve();
            JsonObject value = base("workbench-forge-recipe-observer-failure-v1");
            value.addProperty("exception_class", problem.getClass().getName());
            String message = String.valueOf(problem.getMessage());
            value.addProperty("message", message.substring(0, Math.min(message.length(), 4096)));
            StringWriter trace = new StringWriter();
            problem.printStackTrace(new PrintWriter(trace));
            value.addProperty("stack_trace", trace.toString());
            value.addProperty("capture_published", published);
            if (!published) {
                Files.deleteIfExists(staging.resolve(".capture-complete"));
                if (ownsDestination) Files.deleteIfExists(output.resolve(".capture-complete"));
                write("failure.json", CanonicalJson.bytes(value));
            } else {
                System.err.println(new String(CanonicalJson.bytes(value), StandardCharsets.UTF_8));
            }
        } catch (Throwable retentionFailure) {
            System.err.println("Forge recipe observer could not retain failure: " + retentionFailure);
            problem.printStackTrace(System.err);
        }
    }

    private static void write(String filename, byte[] bytes) throws IOException {
        writeTo(staging.resolve(filename), bytes);
    }

    private static void writeTo(Path path, byte[] bytes) throws IOException {
        try (FileChannel file = FileChannel.open(path,
                StandardOpenOption.WRITE, StandardOpenOption.CREATE_NEW)) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            while (buffer.hasRemaining()) file.write(buffer);
            file.force(true);
        }
    }
}
