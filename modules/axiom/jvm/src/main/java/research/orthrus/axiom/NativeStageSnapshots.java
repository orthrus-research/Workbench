package research.orthrus.axiom;

import java.util.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

/** Lossless representation of earlier native stages and their diagnostic lists. */
public final class NativeStageSnapshots {
    private static final String SCHEMA = "axiom.native-stage-snapshots.v1";
    private static final List<String> STAGES = List.of("earlyStage", "selectionStage", "constructionStage", "groovyBoundary");
    private static final List<String> CATALOGS = List.of("materials", "fluids", "prefixItems", "materialBlocks", "oreBlocks");
    private static final List<String> EXECUTION_FIELDS = List.of("registrationEffects", "customMetaItems", "materials",
            "lookups", "missingMaterials", "vocabulary", "prefixItems", "materialBlocks", "materialOres", "deferredWork", "selectedObservations");
    private NativeStageSnapshots() {}

    public static Map<String,Object> compact(Map<String,Object> original, List<Object> diagnostics) {
        return compact(original, diagnostics, "/diagnostics");
    }

    private static Map<String,Object> compact(Map<String,Object> original, List<Object> diagnostics, String pointer) {
        if (original.containsKey("snapshotEncoding")) throw new IllegalArgumentException("Native snapshots already encoded");
        var nativeRows = Json.array(original.getOrDefault("nativeDiagnostics", List.of()));
        var groovyRows = Json.array(original.getOrDefault("groovyDiagnostics", List.of()));
        var combined = new ArrayList<>(nativeRows); combined.addAll(groovyRows);
        if (combined.size() > diagnostics.size() || !combined.equals(diagnostics.subList(0, combined.size()))
                || pointer.equals("/diagnostics") && combined.size() != diagnostics.size())
            throw new IllegalArgumentException("Native diagnostic reference differs");
        var result = new LinkedHashMap<>(original);
        for (String stage : STAGES) if (original.get(stage) instanceof Map<?,?> snapshot) {
            var inherit = new ArrayList<String>(); var values = new LinkedHashMap<String,Object>();
            for (var entry : Json.object(snapshot).entrySet()) {
                if (original.containsKey(entry.getKey()) && Objects.equals(original.get(entry.getKey()), entry.getValue()))
                    inherit.add(entry.getKey());
                else values.put(entry.getKey(), entry.getValue());
            }
            result.put(stage, Map.of("inheritedKeys", inherit, "values", values));
        }
        result.remove("nativeDiagnostics"); result.remove("groovyDiagnostics");
        fingerprints(result, false);
        var encoding = new LinkedHashMap<String,Object>(Map.of("schema", SCHEMA, "diagnosticsPointer", pointer,
                "nativeDiagnosticCount", nativeRows.size(), "groovyDiagnosticCount", groovyRows.size(),
                "nativeDiagnosticsPresent", original.containsKey("nativeDiagnostics"),
                "groovyDiagnosticsPresent", original.containsKey("groovyDiagnostics"),
                "fingerprintEncoding", "base64url-sha256"));
        if (combined.size() != diagnostics.size()) encoding.put("diagnosticEnvelopeCount", diagnostics.size());
        if (original.get("nativeConsole") instanceof Map<?,?> console && console.get("head") instanceof String
                && console.get("tail") instanceof String) {
            consoleText(result, false); encoding.put("consoleEncoding", "gzip-base64-json");
        }
        result.put("snapshotEncoding", encoding);
        return result;
    }

    public static Map<String,Object> expand(Map<String,Object> encoded, List<Object> diagnostics) {
        return expand(encoded, diagnostics, "/diagnostics");
    }

    private static Map<String,Object> expand(Map<String,Object> encoded, List<Object> diagnostics, String pointer) {
        if (!encoded.containsKey("snapshotEncoding")) return new LinkedHashMap<>(encoded);
        var result = new LinkedHashMap<>(encoded);
        var format = Json.object(result.remove("snapshotEncoding"));
        if (!SCHEMA.equals(format.get("schema")) || !pointer.equals(format.get("diagnosticsPointer")))
            throw new IllegalArgumentException("Native snapshot encoding differs");
        int nativeCount = ((Number)format.get("nativeDiagnosticCount")).intValue();
        int groovyCount = ((Number)format.get("groovyDiagnosticCount")).intValue();
        long referenced = (long)nativeCount + groovyCount;
        if (nativeCount < 0 || groovyCount < 0 || referenced > diagnostics.size()
                || ((Number)format.getOrDefault("diagnosticEnvelopeCount", referenced)).longValue() != diagnostics.size())
            throw new IllegalArgumentException("Native diagnostic reference is incomplete");
        if (Boolean.TRUE.equals(format.get("nativeDiagnosticsPresent")))
            result.put("nativeDiagnostics", List.copyOf(diagnostics.subList(0, nativeCount)));
        if (Boolean.TRUE.equals(format.get("groovyDiagnosticsPresent")))
            result.put("groovyDiagnostics", List.copyOf(diagnostics.subList(nativeCount, (int)referenced)));
        if (format.containsKey("fingerprintEncoding")) {
            if (!"base64url-sha256".equals(format.get("fingerprintEncoding")))
                throw new IllegalArgumentException("Native fingerprint encoding differs");
            fingerprints(result, true);
        }
        if (format.containsKey("consoleEncoding")) {
            if (!"gzip-base64-json".equals(format.get("consoleEncoding")))
                throw new IllegalArgumentException("Native console encoding differs");
            consoleText(result, true);
        }
        var expanded = new HashSet<String>();
        for (String stage : STAGES) if (result.containsKey(stage)) expandStage(result, stage, expanded, new HashSet<>());
        return result;
    }

    /** Keep each native effect collection once in the normal command's execution envelope. */
    public static Map<String,Object> compactExecution(Map<String,Object> original) {
        if (!(original.get("nativeInitialization") instanceof Map<?,?>)) return new LinkedHashMap<>(original);
        var result = new LinkedHashMap<>(original);
        var nativeState = compact(Json.object(original.get("nativeInitialization")),
                Json.array(original.get("diagnostics")), "/execution/diagnostics");
        var fields = new ArrayList<String>();
        for (String field : EXECUTION_FIELDS) if (nativeState.containsKey(field)) {
            if (!Objects.equals(original.get(field), Json.object(original.get("nativeInitialization")).get(field)))
                throw new IllegalArgumentException("Native execution collection differs: " + field);
            result.put(field, nativeState.remove(field)); fields.add(field);
        }
        var encoding = new LinkedHashMap<>(Json.object(nativeState.get("snapshotEncoding")));
        encoding.put("executionFields", fields); nativeState.put("snapshotEncoding", encoding);
        result.put("nativeInitialization", nativeState);
        return result;
    }

    public static Map<String,Object> expandExecution(Map<String,Object> original) {
        var result = new LinkedHashMap<>(original);
        if (!(original.get("nativeInitialization") instanceof Map<?,?> value) || !value.containsKey("snapshotEncoding")) return result;
        var nativeState = new LinkedHashMap<>(Json.object(value));
        var format = Json.object(nativeState.get("snapshotEncoding"));
        var fields = Json.array(format.getOrDefault("executionFields", List.of()));
        for (Object field : fields) {
            String name = Json.string(field);
            if (!EXECUTION_FIELDS.contains(name) || !original.containsKey(name))
                throw new IllegalArgumentException("Native execution collection is unavailable: " + name);
            nativeState.put(name, original.get(name));
        }
        nativeState = new LinkedHashMap<>(expand(nativeState, Json.array(original.get("diagnostics")), "/execution/diagnostics"));
        for (Object field : fields) result.put(Json.string(field), nativeState.get(field));
        result.put("nativeInitialization", nativeState);
        return result;
    }

    private static void fingerprints(Map<String,Object> root, boolean expand) {
        if (!(root.get("registrationEffects") instanceof Map<?,?>)) return;
        var effects = new LinkedHashMap<>(Json.object(root.get("registrationEffects")));
        for (String name : CATALOGS) {
            if (!(effects.get(name) instanceof Map<?,?> value) || !(value.get("entries") instanceof Map<?,?>)) continue;
            var catalog = new LinkedHashMap<>(Json.object(value)); var entries = new TreeMap<String,Object>();
            for (var entry : Json.object(value.get("entries")).entrySet()) {
                String text = Json.string(entry.getValue());
                byte[] digest = expand ? Base64.getUrlDecoder().decode(text) : HexFormat.of().parseHex(text);
                if (digest.length != 32 || !expand && !HexFormat.of().formatHex(digest).equals(text))
                    throw new IllegalArgumentException("Native catalog fingerprint differs");
                entries.put(entry.getKey(), expand ? HexFormat.of().formatHex(digest)
                        : Base64.getUrlEncoder().withoutPadding().encodeToString(digest));
            }
            catalog.put("entries", entries); effects.put(name, catalog);
        }
        root.put("registrationEffects", effects);
    }

    /** Compress only retained console text; completeness, byte count and full-stream digest stay visible. */
    private static void consoleText(Map<String,Object> root, boolean expand) {
        var console = new LinkedHashMap<>(Json.object(root.get("nativeConsole")));
        try {
            if (expand) {
                byte[] packed = Base64.getDecoder().decode(Json.string(console.remove("textGzipBase64")));
                try (var input = new GZIPInputStream(new ByteArrayInputStream(packed))) {
                    byte[] bytes = input.readAllBytes();
                    var text = Json.object(Json.parse(new String(bytes, StandardCharsets.UTF_8)));
                    Json.keys(text, "head", "tail");
                    console.put("head", Json.string(text.get("head"))); console.put("tail", Json.string(text.get("tail")));
                }
            } else {
                var text = Map.of("head", Json.string(console.remove("head")), "tail", Json.string(console.remove("tail")));
                var output = new ByteArrayOutputStream();
                try (var gzip = new GZIPOutputStream(output)) { gzip.write(Json.write(text).getBytes(StandardCharsets.UTF_8)); }
                console.put("textGzipBase64", Base64.getEncoder().encodeToString(output.toByteArray()));
            }
        } catch (IOException failure) { throw new IllegalArgumentException("Native console text could not be restored", failure); }
        root.put("nativeConsole", console);
    }

    private static void expandStage(Map<String,Object> root, String stage, Set<String> expanded, Set<String> visiting) {
        if (expanded.contains(stage)) return;
        if (!visiting.add(stage)) throw new IllegalArgumentException("Native snapshot reference cycle");
        var snapshot = Json.object(root.get(stage));
        var result = new LinkedHashMap<String,Object>();
        for (Object value : Json.array(snapshot.get("inheritedKeys"))) {
            String key = Json.string(value);
            if (!root.containsKey(key)) throw new IllegalArgumentException("Native snapshot reference is missing: " + key);
            if (STAGES.contains(key)) expandStage(root, key, expanded, visiting);
            result.put(key, root.get(key));
        }
        result.putAll(Json.object(snapshot.get("values")));
        root.put(stage, result); expanded.add(stage); visiting.remove(stage);
    }
}
