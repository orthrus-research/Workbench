package research.orthrus.axiom;

import java.io.*;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.zip.*;

/** Independently verifies platform metadata and artifact custody. No classes are defined or initialized. */
final class PlatformBundle {
    private PlatformBundle() {}
    static Map<String, Object> summary(Map<String, Object> inspected, Object request) {
        Map<String, Object> result = new LinkedHashMap<>(inspected), metadata = new LinkedHashMap<>(Json.object(inspected.get("metadata")));
        metadata.put("libraryDeclarations", Json.array(metadata.remove("declarations")).size());
        metadata.put("selectedLibraries", Json.array(metadata.remove("libraries")).size());
        result.put("metadata", metadata);
        result.remove("artifacts");
        Set<Object> selected = request == null ? Set.of() : new HashSet<>(Json.array(Json.object(request).getOrDefault("libraryPaths", List.of())));
        result.put("artifactDetails", Json.array(inspected.get("artifacts")).stream().map(Json::object).filter(row -> selected.contains(row.get("path"))).toList());
        result.put("inventoryPresentation", "combined summary; full declarations and artifact inventory are available through axiom platform");
        return result;
    }
    static Map<String, Object> inspect(Path path, Object request) throws IOException {
        if (Files.isSymbolicLink(path) || !Files.isRegularFile(path) || Files.size(path) > ArtifactBundle.TOTAL_BYTES)
            throw Failure.request("Platform bundle must be a bounded ordinary file");
        try (ZipFile archive = new ZipFile(path.toFile())) {
            Map<String, ZipEntry> entries = new TreeMap<>(); long total = 0;
            for (var enumeration = archive.entries(); enumeration.hasMoreElements();) {
                ZipEntry entry = enumeration.nextElement(); String name = entry.getName(); total += entry.getSize();
                if (!(Set.of("manifest.json", "policy.json", "bootstrap.zip").contains(name) || name.matches("blobs/[0-9a-f]{64}"))
                        || entry.isDirectory() || entry.getMethod() != ZipEntry.STORED || entry.getSize() < 0 || entry.getSize() > ArtifactBundle.FILE_BYTES
                        || entries.putIfAbsent(name, entry) != null || entries.size() > 2003 || total > ArtifactBundle.TOTAL_BYTES)
                    throw Failure.request("Invalid, duplicate or oversized platform bundle member");
            }
            byte[] rawManifest = read(archive, entries, "manifest.json", 1_048_576), rawPolicy = read(archive, entries, "policy.json", 1_048_576);
            Map<String, Object> manifest = Json.object(Json.parse(Main.utf8(rawManifest))), policy = Json.object(Json.parse(Main.utf8(rawPolicy)));
            Json.keys(manifest, "schema", "profile", "policySha256", "bootstrapSha256", "selection", "artifacts");
            if (!"axiom.platform.v1".equals(manifest.get("schema")) || !"cleanroom".equals(manifest.get("profile"))
                    || !Json.bytesDigest(rawPolicy).equals(manifest.get("policySha256")))
                throw Failure.request("Platform manifest schema/profile/policy identity differs");
            byte[] bootstrap = read(archive, entries, "bootstrap.zip", 16 * 1024 * 1024);
            if (!Json.bytesDigest(bootstrap).equals(manifest.get("bootstrapSha256"))) throw Failure.request("Platform bootstrap identity differs");
            Map<String, Object> selection = Json.object(manifest.get("selection"));
            Map<String, Object> metadata = PlatformMetadata.inspect(bootstrap, policy, selection);
            String id = "axiom-platform:sha256:" + Json.bytesDigest(rawManifest);
            Set<String> details = new HashSet<>();
            if (request != null) {
                Map<String, Object> query = Json.object(request); Json.keys(query, "schema", "platformId", "libraryPaths");
                if (!"axiom.platform-request.v1".equals(query.get("schema")) || !id.equals(query.get("platformId")))
                    throw Failure.request("Platform inspection request has a stale identity or unsupported schema");
                if (query.containsKey("libraryPaths")) {
                    List<Object> paths = Json.array(query.get("libraryPaths"));
                    if (paths.isEmpty() || paths.size() > 16) throw Failure.request("libraryPaths needs 1..16 explicit paths");
                    for (Object value : paths) if (!details.add(SourceTarget.path(Json.string(value)))) throw Failure.request("Duplicate library detail path");
                }
            }
            Map<String, Map<String, Object>> expected = new LinkedHashMap<>(), provided = new LinkedHashMap<>();
            for (Object value : Json.array(metadata.get("libraries"))) {
                Map<String, Object> row = Json.object(value); expected.put(Json.string(row.get("path")), row);
            }
            Set<String> members = new HashSet<>(Set.of("manifest.json", "policy.json", "bootstrap.zip"));
            List<Object> records = Json.array(manifest.get("artifacts"));
            if (records.size() > 2000) throw Failure.request("Too many provided platform artifacts");
            for (Object value : records) {
                Map<String, Object> record = Json.object(value); Json.keys(record, "path", "sha256", "size");
                String name = SourceTarget.path(Json.string(record.get("path"))), digest = Json.string(record.get("sha256"));
                Map<String, Object> declaration = expected.get(name);
                if (declaration == null || !digest.matches("[0-9a-f]{64}") || provided.putIfAbsent(name, record) != null)
                    throw Failure.request("Unselected, repeated or malformed platform artifact");
                ZipEntry entry = entries.get("blobs/" + digest);
                if (entry == null || entry.getSize() != Json.integer(record.get("size")) || entry.getSize() != Json.integer(declaration.get("size")))
                    throw Failure.request("Platform artifact size differs from metadata");
                if (declaration.containsKey("policySha256") && !digest.equals(declaration.get("policySha256")))
                    throw Failure.request("Platform artifact identity differs from profile pin");
                members.add(entry.getName()); verify(archive, entry, digest, Json.string(declaration.get("sha1")));
            }
            if (!members.equals(entries.keySet())) throw Failure.request("Unreferenced platform bundle members");
            if (!provided.keySet().containsAll(details)) throw Failure.request("Library detail paths must name provided selected artifacts");
            List<Map<String, Object>> inspected = new ArrayList<>();
            Map<String, String> owners = new HashMap<>(); Set<String> duplicates = new TreeSet<>();
            long bytes = 0, expanded = 0;
            for (var item : expected.entrySet()) {
                Map<String, Object> record = provided.get(item.getKey());
                if (record == null) continue;
                bytes += Json.integer(record.get("size"));
                Map<String, Object> row = new LinkedHashMap<>(record); row.put("role", item.getValue().get("role"));
                if (!"native-extraction".equals(row.get("role")) && item.getKey().endsWith(".jar")) {
                    try (InputStream input = archive.getInputStream(entries.get("blobs/" + record.get("sha256")))) {
                        Map<String, Object> binary = BinaryInventory.read(input, (name, member, sha) -> {
                            if (owners.putIfAbsent(name, item.getKey()) != null) duplicates.add(name);
                            if (owners.size() > 250000) throw new Failure("incomplete", "platform.class-bound", "Platform class inventory exceeds bound");
                        }, details.contains(item.getKey()), 4L * 1024 * 1024 * 1024 - expanded);
                        expanded += Json.integer(binary.get("expandedBytes")); row.put("binary", binary);
                    }
                } else row.put("binary", Map.of("scope", "bytes-only; native libraries never loaded or extracted"));
                inspected.add(row);
            }
            List<String> missing = expected.keySet().stream().filter(name -> !provided.containsKey(name)).toList();
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("schema", "axiom.platform-inspection.v1"); result.put("profile", "cleanroom"); result.put("policySha256", manifest.get("policySha256"));
            result.put("platformId", id); result.put("bootstrapSha256", manifest.get("bootstrapSha256")); result.put("metadata", metadata);
            result.put("providedArtifactBytesVerified", true); result.put("providedArtifacts", inspected.size()); result.put("artifactBytes", bytes);
            result.put("artifacts", inspected); result.put("missingArtifacts", missing);
            result.put("selectedArtifactCoverage", missing.isEmpty() ? "complete-declared-selection" : "partial-declared-selection");
            result.put("uniqueClassNames", owners.size()); result.put("duplicateClassCount", duplicates.size());
            result.put("duplicateClassNames", duplicates.stream().limit(128).toList()); result.put("duplicateClassListTruncated", duplicates.size() > 128);
            result.put("classesLoaded", false); result.put("installedCompositionQualified", false); result.put("wholePackParity", false);
            result.put("gaps", List.of("source/build correspondence and active class transformations", "embedded/transitive loader-discovered dependencies",
                    "class-provider resolution and mod activation", "registry/material construction", "target Java implementation and native execution semantics"));
            return result;
        } catch (ZipException failure) { throw Failure.request("Malformed platform bundle: " + failure.getMessage()); }
    }
    private static byte[] read(ZipFile zip, Map<String, ZipEntry> entries, String name, int limit) throws IOException {
        ZipEntry entry = entries.get(name);
        if (entry == null || entry.getSize() > limit) throw Failure.request("Missing or oversized platform member: " + name);
        try (InputStream input = zip.getInputStream(entry)) { return Main.read(input, limit); }
    }
    private static void verify(ZipFile zip, ZipEntry entry, String sha256, String sha1) throws IOException {
        MessageDigest full = BinaryInventory.digest(), declared;
        try { declared = MessageDigest.getInstance("SHA-1"); } catch (NoSuchAlgorithmException failure) { throw new AssertionError(failure); }
        long bytes = 0; byte[] buffer = new byte[65536]; int count;
        try (InputStream input = zip.getInputStream(entry)) {
            while ((count = input.read(buffer)) >= 0) {
                bytes += count; if (bytes > ArtifactBundle.FILE_BYTES) throw Failure.request("Platform artifact exceeds byte bound");
                full.update(buffer, 0, count); declared.update(buffer, 0, count);
            }
        }
        if (bytes != entry.getSize() || !HexFormat.of().formatHex(full.digest()).equals(sha256) || !HexFormat.of().formatHex(declared.digest()).equals(sha1))
            throw Failure.request("Platform artifact bytes differ from original metadata");
    }
}
