package research.orthrus.axiom;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class SourceTargetTest {
    @TempDir Path temporary;
    static final String SCRIPT = "groovy/postInit/a.groovy";
    record Fixture(Map<String, byte[]> entries, Map<String, Object> lock, String targetId, String scriptSha) {}
    static byte[] json(Object value) { return Json.write(value).getBytes(StandardCharsets.UTF_8); }
    static Fixture fixture() throws IOException {
        Map<String, byte[]> entries = new TreeMap<>();
        Map<String, byte[]> sources = Map.of("groovy/runConfig.json", json(Map.of("loaders", Map.of("postInit", List.of("postInit/")))),
                SCRIPT, "fluid('water')\n".getBytes(StandardCharsets.UTF_8));
        String root = tree(sources, "", entries);
        var repository = Map.of("id", "supersymmetry", "commit", "a".repeat(40), "tree", root);
        String sha = Json.bytesDigest(sources.get(SCRIPT));
        Map<String, Object> lock = Map.of("repositories", List.of(repository), "references", List.of(Map.of("id", "required-script",
                "repository", "supersymmetry", "path", SCRIPT, "sha256", sha)));
        Map<String, Object> policy = Map.of("schema", "axiom.target-policy.v1", "profile", "supersymmetry", "scope", "source-composition",
                "repositories", Map.of("supersymmetry", Map.of("prefixes", List.of("groovy/"), "paths", List.of())),
                "scriptRoot", "groovy", "runConfig", "groovy/runConfig.json");
        entries.put("source-lock.json", json(lock)); entries.put("policy.json", json(policy));
        List<Map<String, Object>> records = new ArrayList<>();
        for (var source : sources.entrySet()) {
            String digest = Json.bytesDigest(source.getValue()); entries.put("blobs/" + digest, source.getValue());
            records.add(Map.of("path", source.getKey(), "blob", SourceTarget.gitDigest("blob", source.getValue()), "sha256", digest, "size", source.getValue().length));
        }
        Map<String, Object> manifest = Map.of("schema", "axiom.target.v1", "profile", "supersymmetry", "bindingId", "fixture-binding",
                "sourceLockSha256", Json.bytesDigest(json(lock)), "policySha256", Json.bytesDigest(json(policy)),
                "repositories", List.of(Map.of("id", "supersymmetry", "commit", "a".repeat(40), "tree", root, "files", records)));
        entries.put("manifest.json", json(manifest));
        return new Fixture(entries, lock, "axiom-source-target:sha256:" + Json.bytesDigest(json(manifest)), sha);
    }
    static String tree(Map<String, byte[]> sources, String prefix, Map<String, byte[]> entries) throws IOException {
        Map<String, Boolean> names = new TreeMap<>();
        for (String path : sources.keySet()) if (path.startsWith(prefix)) {
            String rest = path.substring(prefix.length()); int slash = rest.indexOf('/');
            names.put(slash < 0 ? rest : rest.substring(0, slash), slash >= 0);
        }
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        for (var name : names.entrySet()) {
            String path = prefix + name.getKey();
            String object = name.getValue() ? tree(sources, path + "/", entries) : SourceTarget.gitDigest("blob", sources.get(path));
            data.write(((name.getValue() ? "40000 " : "100644 ") + name.getKey() + "\0").getBytes(StandardCharsets.UTF_8));
            data.write(HexFormat.of().parseHex(object));
        }
        String id = SourceTarget.gitDigest("tree", data.toByteArray()); entries.put("trees/" + id, data.toByteArray()); return id;
    }
    Path write(Fixture fixture, String name) throws IOException {
        Path path = temporary.resolve(name);
        try (ZipOutputStream archive = new ZipOutputStream(Files.newOutputStream(path))) {
            for (var entry : fixture.entries.entrySet()) { archive.putNextEntry(new ZipEntry(entry.getKey())); archive.write(entry.getValue()); archive.closeEntry(); }
        }
        return path;
    }
    SourceTarget open(Fixture fixture, String name) throws IOException { return new SourceTarget(write(fixture, name), fixture.lock, "fixture-binding"); }
    Map<String, Object> request(Fixture fixture, List<?> overlays) {
        return Map.of("schema", "axiom.target-request.v1", "targetId", fixture.targetId, "overlays", overlays);
    }
    @Test void membershipAndCompletenessAreIndependentOfWorkingCheckout() throws Exception {
        var fixture = fixture();
        try (var target = open(fixture, "target.zip")) {
            var result = target.inspect(null); var plan = Json.object(result.get("sourcePlan"));
            assertEquals(fixture.targetId, result.get("targetId")); assertEquals(true, result.get("sourceTreeMembershipVerified"));
            assertEquals(1, plan.get("scheduledFiles")); assertEquals(1, plan.get("literalRegistryReferenceCount"));
            assertEquals(false, plan.get("definitionsExecuted")); assertEquals("not-constructed", result.get("registryState"));
        }
    }
    @Test void wrongBlobCannotBeBlessedByItsSha256ManifestEntry() throws Exception {
        var fixture = fixture();
        fixture.entries.put("blobs/" + fixture.scriptSha, new byte[]{1});
        assertThrows(Failure.class, () -> open(fixture, "corrupt.zip"));
    }
    @Test void omittedSelectedFileIsDetectedFromGitTree() throws Exception {
        var fixture = fixture();
        Map<String, Object> manifest = Json.object(Json.parse(Main.utf8(fixture.entries.get("manifest.json"))));
        List<Object> files = Json.array(Json.object(Json.array(manifest.get("repositories")).get(0)).get("files"));
        files.removeIf(row -> Json.object(row).get("path").equals("groovy/runConfig.json"));
        fixture.entries.put("manifest.json", json(manifest));
        assertThrows(Failure.class, () -> open(fixture, "omitted.zip"));
    }
    @Test void alteredTreeAndUnreferencedEntriesFailClosed() throws Exception {
        var fixture = fixture(); String tree = fixture.entries.keySet().stream().filter(name -> name.startsWith("trees/")).findFirst().orElseThrow();
        fixture.entries.put(tree, new byte[0]); var corrupt = fixture; assertThrows(Failure.class, () -> open(corrupt, "tree.zip"));
        fixture = fixture(); fixture.entries.put("blobs/" + "0".repeat(64), new byte[0]);
        var extra = fixture; assertThrows(Failure.class, () -> open(extra, "extra.zip"));
    }
    @Test void traversalEntriesNeverGetExtracted() throws Exception {
        var fixture = fixture(); fixture.entries.put("../outside", new byte[0]);
        assertThrows(Failure.class, () -> open(fixture, "traversal.zip")); assertFalse(Files.exists(temporary.resolve("outside")));
    }
    @Test void explicitEditsAndDeletionsChangeCandidateWithoutMutatingBase() throws Exception {
        var fixture = fixture();
        try (var target = open(fixture, "overlays.zip")) {
            var original = target.inspect(null);
            var overlay = new LinkedHashMap<String, Object>(Map.of("repository", "supersymmetry", "path", SCRIPT, "expectedSha256", fixture.scriptSha, "text", "ore('new')"));
            var edited = target.inspect(request(fixture, List.of(overlay)));
            assertNotEquals(original.get("candidateId"), edited.get("candidateId"));
            overlay.put("text", null);
            var deleted = target.inspect(request(fixture, List.of(overlay)));
            assertEquals(0, Json.object(deleted.get("sourcePlan")).get("scheduledFiles"));
            assertEquals(original.get("candidateId"), target.inspect(null).get("candidateId"));
        }
    }
    @Test void overlaysCannotUseStaleHashesOrDuplicatePaths() throws Exception {
        var fixture = fixture();
        try (var target = open(fixture, "stale.zip")) {
            var edit = new LinkedHashMap<String, Object>(Map.of("repository", "supersymmetry", "path", SCRIPT, "expectedSha256", "0".repeat(64), "text", ""));
            assertThrows(Failure.class, () -> target.inspect(request(fixture, List.of(edit))));
            edit.put("expectedSha256", fixture.scriptSha);
            assertThrows(Failure.class, () -> target.inspect(request(fixture, List.of(edit, edit))));
            assertThrows(Failure.class, () -> target.inspect(Map.of("schema", "axiom.target-request.v1", "targetId", "old", "overlays", List.of())));
        }
    }
    @Test void detailedSourceSelectionDoesNotClaimFullProgramEvaluation() throws Exception {
        var fixture = fixture();
        try (var target = open(fixture, "detail.zip")) {
            var request = new LinkedHashMap<>(request(fixture, List.of())); request.put("includeFiles", true); request.put("sourcePaths", List.of(SCRIPT));
            var plan = Json.object(target.inspect(request).get("sourcePlan"));
            assertEquals(1, Json.array(plan.get("files")).size()); assertEquals("selected-scheduled-scripts", plan.get("inventoryScope"));
            assertEquals(false, plan.get("registryReferencesResolved"));
        }
    }
    @Test void newFilesNeedAbsentIdentityAndCannotCollideWithDirectories() throws Exception {
        var fixture = fixture();
        try (var target = open(fixture, "creation.zip")) {
            var create = new LinkedHashMap<String, Object>();
            create.put("repository", "supersymmetry"); create.put("path", "groovy/postInit/new.groovy");
            create.put("expectedSha256", null); create.put("text", "ore('new')");
            assertEquals(2, Json.object(target.inspect(request(fixture, List.of(create))).get("sourcePlan")).get("scheduledFiles"));
            create.put("path", "groovy/postInit");
            assertThrows(Failure.class, () -> target.inspect(request(fixture, List.of(create))));
            create.put("path", SCRIPT + "/child.groovy");
            assertThrows(Failure.class, () -> target.inspect(request(fixture, List.of(create))));
        }
    }
}
