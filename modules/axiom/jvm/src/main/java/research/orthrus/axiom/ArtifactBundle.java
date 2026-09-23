package research.orthrus.axiom;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.zip.*;

/** Immutable artifact byte custody; no classloader, Forge bootstrap or favorable registry stand-ins. */
final class ArtifactBundle {
    static final long TOTAL_BYTES = 2L * 1024 * 1024 * 1024, FILE_BYTES = 256L * 1024 * 1024;
    private ArtifactBundle() {}

    static Map<String, Object> inspect(Path path, Map<String, Object> target, Set<String> details) throws IOException {
        if (Files.isSymbolicLink(path) || !Files.isRegularFile(path) || Files.size(path) > TOTAL_BYTES)
            throw Failure.request("Artifact bundle must be a bounded ordinary file");
        Map<String, Object> composition = Json.object(target.get("composition"));
        if (!Set.of("client", "server").contains(composition.get("side")))
            throw Failure.request("Artifact inspection requires explicit composition side/options in the target request");
        try (ZipFile archive = new ZipFile(path.toFile(), StandardCharsets.UTF_8)) {
            Map<String, ZipEntry> entries = new TreeMap<>();
            long size = 0;
            for (var enumeration = archive.entries(); enumeration.hasMoreElements();) {
                ZipEntry entry = enumeration.nextElement(); String name = entry.getName(); size += entry.getSize();
                if (!(name.equals("manifest.json") || name.matches("blobs/[0-9a-f]{64}")) || entry.isDirectory()
                        || entry.getSize() < 0 || entry.getSize() > FILE_BYTES || entry.getMethod() != ZipEntry.STORED
                        || entries.putIfAbsent(name, entry) != null || entries.size() > 2001 || size > TOTAL_BYTES)
                    throw Failure.request("Invalid, duplicate or oversized artifact bundle member");
            }
            ZipEntry manifestEntry = entries.get("manifest.json");
            if (manifestEntry == null || manifestEntry.getSize() > 1_048_576) throw Failure.request("Missing or oversized artifact manifest");
            byte[] raw;
            try (InputStream input = archive.getInputStream(manifestEntry)) { raw = Main.read(input, 1_048_576); }
            Map<String, Object> manifest = Json.object(Json.parse(Main.utf8(raw)));
            Json.keys(manifest, "schema", "targetId", "candidateId", "compositionId", "side", "options", "artifacts");
            if (!"axiom.artifacts.v1".equals(manifest.get("schema")) || !Objects.equals(target.get("targetId"), manifest.get("targetId"))
                    || !Objects.equals(target.get("candidateId"), manifest.get("candidateId"))
                    || !Objects.equals(composition.get("compositionId"), manifest.get("compositionId"))
                    || !Objects.equals(composition.get("side"), manifest.get("side"))
                    || !Objects.equals(composition.get("options"), manifest.get("options")))
                throw Failure.request("Artifact bundle differs from exact candidate/side/optional selection");
            Map<String, Map<String, Object>> expected = new TreeMap<>();
            for (Object value : Json.array(composition.get("artifacts"))) {
                Map<String, Object> row = Json.object(value);
                if ("included-declaration".equals(row.get("selection"))) expected.put(Json.string(row.get("path")), row);
            }
            Set<String> members = new HashSet<>(Set.of("manifest.json")), provided = new HashSet<>();
            List<Map<String, Object>> records = new ArrayList<>();
            Map<String, String> classOwners = new HashMap<>();
            Set<String> collisions = new TreeSet<>();
            long bytes = 0, expanded = 0;
            List<Object> artifacts = Json.array(manifest.get("artifacts"));
            if (artifacts.isEmpty() || artifacts.size() > 2000) throw Failure.request("Artifact bundle needs 1..2000 selected artifacts");
            // Verify all raw byte bindings first; only then inspect any binary declarations.
            for (Object value : artifacts) {
                Map<String, Object> record = Json.object(value);
                Json.keys(record, "metadataPath", "outputPath", "sha256", "size");
                String metadata = SourceTarget.path(Json.string(record.get("metadataPath")));
                Map<String, Object> declaration = expected.get(metadata);
                String digest = Json.string(record.get("sha256"));
                if (declaration == null || !provided.add(metadata) || !digest.matches("[0-9a-f]{64}")
                        || !Objects.equals(declaration.get("outputPath"), record.get("outputPath")))
                    throw Failure.request("Bundle contains an unselected, duplicate or mismatched artifact");
                String member = "blobs/" + digest; ZipEntry entry = entries.get(member);
                if (entry == null || entry.getSize() != Json.number(record.get("size")) || entry.getSize() == 0)
                    throw Failure.request("Artifact size or member differs from manifest");
                members.add(member); bytes += entry.getSize();
                verify(archive, entry, digest, Json.object(declaration.get("downloadHash")));
            }
            if (!members.equals(entries.keySet())) throw Failure.request("Unreferenced artifact bundle entries");
            if (!provided.containsAll(details)) throw Failure.request("Artifact detail paths must name provided selected artifacts");
            for (Object value : artifacts) {
                Map<String, Object> record = Json.object(value); String metadata = Json.string(record.get("metadataPath"));
                Map<String, Object> row = new LinkedHashMap<>(record); row.put("declaredHashVerified", true);
                if (Json.string(record.get("outputPath")).toLowerCase(Locale.ROOT).endsWith(".jar")) {
                    try (InputStream input = archive.getInputStream(entries.get("blobs/" + record.get("sha256")))) {
                        Map<String, Object> inventory = BinaryInventory.read(input, (name, member, sha) -> {
                            // Preserve ambiguity; neither archive order nor filenames select a runtime class here.
                            String previous = classOwners.putIfAbsent(name, metadata);
                            if (previous != null) collisions.add(name);
                            if (classOwners.size() > 250000) throw new Failure("incomplete", "artifacts.class-bound", "Class inventory exceeds 250,000 names");
                        }, details.contains(metadata), 4L * 1024 * 1024 * 1024 - expanded);
                        expanded += ((Number)inventory.get("expandedBytes")).longValue();
                        row.put("binary", inventory);
                    }
                } else row.put("binary", Map.of("scope", "non-JAR artifact; bytes verified only"));
                records.add(row);
            }
            List<String> missing = expected.keySet().stream().filter(name -> !provided.contains(name)).toList();
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("schema", "axiom.artifact-inspection.v1"); result.put("artifactBundleId", "axiom-artifacts:sha256:" + Json.bytesDigest(raw));
            result.put("compositionId", composition.get("compositionId")); result.put("providedArtifactBytesVerified", true);
            result.put("selectedArtifactCoverage", missing.isEmpty() ? "complete-declared-selection" : "partial-declared-selection");
            result.put("providedArtifacts", records.size()); result.put("missingArtifacts", missing); result.put("artifactBytes", bytes);
            result.put("uniqueClassNames", classOwners.size()); result.put("duplicateClassNames", collisions.stream().limit(128).toList());
            result.put("duplicateClassCount", collisions.size()); result.put("duplicateClassListTruncated", collisions.size() > 128);
            result.put("artifacts", records); result.put("classesLoaded", false); result.put("installedCompositionQualified", false);
            result.put("gaps", List.of("platform and transitive/embedded library closure", "effective loader order and mod activation",
                    "source/build correspondence and active class transformations", "registry/material construction"));
            return result;
        } catch (ZipException failure) { throw Failure.request("Malformed artifact bundle: " + failure.getMessage()); }
    }
    private static void verify(ZipFile archive, ZipEntry entry, String sha256, Map<String, Object> declared) throws IOException {
        String algorithm = Json.string(declared.get("algorithm"));
        String name = switch (algorithm) { case "sha1" -> "SHA-1"; case "sha256" -> "SHA-256"; case "sha512" -> "SHA-512"; case "md5" -> "MD5";
            default -> throw Failure.unsupported("artifacts.hash", "Unsupported declared artifact hash"); };
        MessageDigest full = BinaryInventory.digest(), pack;
        try { pack = MessageDigest.getInstance(name); } catch (NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
        long size = 0; byte[] buffer = new byte[65536]; int count;
        try (InputStream input = archive.getInputStream(entry)) {
            while ((count = input.read(buffer)) >= 0) {
                size += count; if (size > FILE_BYTES) throw Failure.request("Artifact stream exceeds bound");
                full.update(buffer, 0, count); pack.update(buffer, 0, count);
            }
        }
        if (size != entry.getSize() || !HexFormat.of().formatHex(full.digest()).equals(sha256)
                || !HexFormat.of().formatHex(pack.digest()).equals(declared.get("value")))
            throw Failure.request("Artifact bytes differ from selected source declaration");
    }
}
