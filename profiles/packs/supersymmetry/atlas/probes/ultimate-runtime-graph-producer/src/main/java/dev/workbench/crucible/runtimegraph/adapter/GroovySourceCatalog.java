package dev.workbench.crucible.runtimegraph.adapter;

import com.cleanroommc.groovyscript.GroovyScript;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import dev.workbench.crucible.runtimegraph.Hashing;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/** Exact runtime Groovy source-resource catalog and class-name reconciliation. */
final class GroovySourceCatalog {
    private static final long MAX_SOURCE_BYTES = 16L * 1024L * 1024L;
    private static final int MAX_SOURCE_FILES = 4096;

    private GroovySourceCatalog() {}

    static Catalog capture() {
        try {
            Path root = GroovyScript.getScriptFile().toPath().toRealPath(LinkOption.NOFOLLOW_LINKS);
            if (Files.isSymbolicLink(root) || !Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException("Groovy source root is not a regular directory");
            }
            Path runConfig = GroovyScript.getRunConfigFile().toPath();
            if (Files.isSymbolicLink(runConfig)
                || !Files.isRegularFile(runConfig, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException("Groovy runConfig is not a regular file");
            }
            byte[] runConfigBytes = Files.readAllBytes(runConfig);
            if (runConfigBytes.length > 1024 * 1024) {
                throw new IllegalStateException("Groovy runConfig exceeds its capture bound");
            }
            JsonElement parsed = new JsonParser().parse(
                new String(runConfigBytes, StandardCharsets.UTF_8)
            );
            if (!parsed.isJsonObject()) {
                throw new IllegalStateException("Groovy runConfig root is not an object");
            }
            JsonObject config = parsed.getAsJsonObject();
            Map<String, List<String>> loaderPaths = loaders(config);

            List<Path> sources = new ArrayList<Path>();
            Stream<Path> stream = Files.walk(root);
            try {
                stream.forEach(path -> {
                    if (Files.isSymbolicLink(path)) {
                        throw new IllegalStateException("Groovy source tree contains a symlink: " + path);
                    }
                    if (Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)
                        && path.getFileName().toString().endsWith(".groovy")) {
                        sources.add(path);
                    }
                });
            } finally {
                stream.close();
            }
            if (sources.isEmpty() || sources.size() > MAX_SOURCE_FILES) {
                throw new IllegalStateException("Groovy source-file universe is empty or unbounded");
            }
            Collections.sort(sources, Comparator.comparing(
                path -> root.relativize(path).toString().replace('\\', '/')
            ));

            List<JsonObject> records = new ArrayList<JsonObject>();
            Map<String, String> byClass = new LinkedHashMap<String, String>();
            long totalBytes = 0;
            for (Path source : sources) {
                if (!Files.isRegularFile(source, LinkOption.NOFOLLOW_LINKS)) {
                    throw new IllegalStateException("Groovy source is not a regular file: " + source);
                }
                long size = Files.size(source);
                totalBytes += size;
                if (size <= 0 || size > MAX_SOURCE_BYTES || totalBytes > MAX_SOURCE_BYTES) {
                    throw new IllegalStateException("Groovy source-byte universe is empty or unbounded");
                }
                String relative = root.relativize(source).toString().replace('\\', '/');
                String className = relative.substring(0, relative.length() - ".groovy".length())
                    .replace('/', '.');
                String sourcePath = "groovy/" + relative;
                if (byClass.put(className, sourcePath) != null) {
                    throw new IllegalStateException("duplicate Groovy source class name: " + className);
                }
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "groovy-source-resource");
                row.addProperty("source_path", sourcePath);
                row.addProperty("runtime_relative_path", relative);
                row.addProperty("compiled_class_name", className);
                row.addProperty("size", size);
                row.addProperty("sha256", Hashing.sha256(source));
                JsonArray stages = new JsonArray();
                for (String stage : configuredStages(relative, loaderPaths)) stages.add(stage);
                row.add("configured_stages", stages);
                records.add(row);
            }

            JsonObject authority = new JsonObject();
            authority.addProperty("record_type", "groovy-run-config-authority");
            authority.addProperty("runtime_path", "groovy/runConfig.json");
            authority.addProperty("size", runConfigBytes.length);
            authority.addProperty("sha256", Hashing.sha256(runConfigBytes));
            authority.addProperty("pack_name", text(config, "packName"));
            authority.addProperty("pack_id", text(config, "packId"));
            authority.addProperty("pack_version", text(config, "version"));
            JsonObject loaders = new JsonObject();
            for (Map.Entry<String, List<String>> entry : loaderPaths.entrySet()) {
                JsonArray values = new JsonArray();
                for (String value : entry.getValue()) values.add(value);
                loaders.add(entry.getKey(), values);
            }
            authority.add("loader_paths", loaders);
            authority.addProperty("source_file_count", sources.size());
            authority.addProperty("source_total_bytes", totalBytes);
            records.add(authority);
            return new Catalog(records, byClass);
        } catch (IOException failure) {
            throw new IllegalStateException("cannot capture Groovy source catalog", failure);
        }
    }

    private static Map<String, List<String>> loaders(JsonObject config) {
        JsonElement raw = config.get("loaders");
        if (raw == null || !raw.isJsonObject()) {
            throw new IllegalStateException("Groovy runConfig lacks loader definitions");
        }
        List<String> stages = new ArrayList<String>();
        for (Map.Entry<String, JsonElement> entry : raw.getAsJsonObject().entrySet()) {
            stages.add(entry.getKey());
        }
        Collections.sort(stages);
        Map<String, List<String>> result = new LinkedHashMap<String, List<String>>();
        for (String stage : stages) {
            JsonElement value = raw.getAsJsonObject().get(stage);
            if (!value.isJsonArray()) {
                throw new IllegalStateException("Groovy loader paths are not an array: " + stage);
            }
            List<String> paths = new ArrayList<String>();
            for (JsonElement item : value.getAsJsonArray()) {
                if (!item.isJsonPrimitive() || !item.getAsJsonPrimitive().isString()) {
                    throw new IllegalStateException("Groovy loader path is not a string");
                }
                String path = item.getAsString().replace('\\', '/');
                if (path.isEmpty() || path.startsWith("/") || path.contains("..")) {
                    throw new IllegalStateException("Groovy loader path is unsafe: " + path);
                }
                if (!path.endsWith("/")) path += "/";
                paths.add(path);
            }
            Collections.sort(paths);
            result.put(stage, paths);
        }
        return result;
    }

    private static List<String> configuredStages(
        String relative,
        Map<String, List<String>> loaderPaths
    ) {
        List<String> result = new ArrayList<String>();
        for (Map.Entry<String, List<String>> entry : loaderPaths.entrySet()) {
            for (String prefix : entry.getValue()) {
                if (relative.startsWith(prefix)) {
                    result.add(entry.getKey());
                    break;
                }
            }
        }
        Collections.sort(result);
        return result;
    }

    private static String text(JsonObject value, String key) {
        JsonElement raw = value.get(key);
        if (raw == null || !raw.isJsonPrimitive() || !raw.getAsJsonPrimitive().isString()) {
            throw new IllegalStateException("Groovy runConfig lacks string " + key);
        }
        String result = raw.getAsString();
        if (result.isEmpty()) throw new IllegalStateException("Groovy runConfig has empty " + key);
        return result;
    }

    static final class Catalog {
        private final List<JsonObject> records;
        private final Map<String, String> byClass;

        private Catalog(List<JsonObject> records, Map<String, String> byClass) {
            this.records = records;
            this.byClass = byClass;
        }

        List<JsonObject> records() {
            List<JsonObject> result = new ArrayList<JsonObject>();
            for (JsonObject record : records) result.add(record.deepCopy());
            return result;
        }

        String resolveSource(String className) {
            String current = className;
            while (current != null && !current.isEmpty()) {
                String exact = byClass.get(current);
                if (exact != null) return exact;
                int marker = current.lastIndexOf('$');
                current = marker < 0 ? null : current.substring(0, marker);
            }
            return null;
        }
    }
}
