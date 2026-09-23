package research.orthrus.axiom;

import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class ArtifactBundleTest {
    @TempDir Path root;
    static final String MOD = "mods/fixture.pw.toml";
    byte[] jar() throws Exception { return BinaryInventoryTest.jar(Map.of("fixture/Mod.class", BinaryInventoryTest.fixtureClass())); }
    Map<String, Object> target(byte[] jar) {
        return new LinkedHashMap<>(Map.of("targetId", "target", "candidateId", "candidate", "composition", new LinkedHashMap<>(Map.of(
                "compositionId", "composition", "side", "client", "options", Map.of(), "artifacts", new ArrayList<>(List.of(Map.of(
                        "path", MOD, "outputPath", "mods/fixture.jar", "selection", "included-declaration", "downloadHash",
                        Map.of("algorithm", "sha256", "value", Json.bytesDigest(jar)))))))));
    }
    Map<String, Object> manifest(byte[] jar) {
        return new LinkedHashMap<>(Map.of("schema", "axiom.artifacts.v1", "targetId", "target", "candidateId", "candidate",
                "compositionId", "composition", "side", "client", "options", Map.of(), "artifacts", new ArrayList<>(List.of(
                        Map.of("metadataPath", MOD, "outputPath", "mods/fixture.jar", "sha256", Json.bytesDigest(jar), "size", jar.length)))));
    }
    Path bundle(String name, Map<String, Object> manifest, Map<String, byte[]> content) throws Exception {
        Path path = root.resolve(name);
        Map<String, byte[]> entries = new TreeMap<>(content); entries.put("manifest.json", BinaryInventoryTest.text(Json.write(manifest)));
        try (ZipOutputStream zip = new ZipOutputStream(Files.newOutputStream(path))) {
            for (var row : entries.entrySet()) {
                ZipEntry entry = new ZipEntry(row.getKey()); entry.setMethod(ZipEntry.STORED); entry.setSize(row.getValue().length);
                CRC32 crc = new CRC32(); crc.update(row.getValue()); entry.setCrc(crc.getValue());
                zip.putNextEntry(entry); zip.write(row.getValue()); zip.closeEntry();
            }
        }
        return path;
    }
    Path bundle(byte[] jar) throws Exception { return bundle("bundle.zip", manifest(jar), Map.of("blobs/" + Json.bytesDigest(jar), jar)); }
    @Test void exactBytesAndSelectionAreVerifiedWithoutInstalledQualification() throws Exception {
        byte[] jar = jar(); Path bundle = bundle(jar); var result = ArtifactBundle.inspect(bundle, target(jar), Set.of());
        assertEquals(true, result.get("providedArtifactBytesVerified")); assertEquals("complete-declared-selection", result.get("selectedArtifactCoverage"));
        assertEquals(1, result.get("providedArtifacts")); assertEquals(1, result.get("uniqueClassNames"));
        assertEquals(false, result.get("classesLoaded")); assertEquals(false, result.get("installedCompositionQualified"));
        assertTrue(Files.isRegularFile(bundle)); assertFalse(Files.exists(root.resolve("fixture")));
    }
    @Test void candidateAndSideChangesCannotReuseOldBundle() throws Exception {
        byte[] jar = jar(); Path path = bundle(jar); var target = target(jar); target.put("candidateId", "edited");
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(path, target, Set.of()));
        var other = target(jar); Json.object(other.get("composition")).put("side", "server");
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(path, other, Set.of()));
    }
    @Test void alteredBytesCannotPassByChangingOnlyTheirBundleHash() throws Exception {
        byte[] jar = jar(), other = BinaryInventoryTest.jar(Map.of("different", new byte[]{1}));
        Path path = bundle("wrong.zip", manifest(other), Map.of("blobs/" + Json.bytesDigest(other), other));
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(path, target(jar), Set.of()));
    }
    @Test void omittedSelectedArtifactsAreExplicitPartialCoverage() throws Exception {
        byte[] jar = jar(); var target = target(jar);
        Json.array(Json.object(target.get("composition")).get("artifacts")).add(Map.of("path", "mods/missing.pw.toml", "selection", "included-declaration"));
        var result = ArtifactBundle.inspect(bundle(jar), target, Set.of());
        assertEquals("partial-declared-selection", result.get("selectedArtifactCoverage"));
        assertEquals(List.of("mods/missing.pw.toml"), result.get("missingArtifacts"));
    }
    @Test void excludedDuplicateOrUnreferencedArtifactsAreRejected() throws Exception {
        byte[] jar = jar(); var manifest = manifest(jar);
        Json.array(manifest.get("artifacts")).add(Json.array(manifest.get("artifacts")).get(0));
        Path duplicate = bundle("duplicate.zip", manifest, Map.of("blobs/" + Json.bytesDigest(jar), jar));
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(duplicate, target(jar), Set.of()));
        Path extra = bundle("extra.zip", manifest(jar), Map.of("blobs/" + Json.bytesDigest(jar), jar, "blobs/" + "0".repeat(64), new byte[]{0}));
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(extra, target(jar), Set.of()));
        var target = target(jar); Json.object(target.get("composition")).put("artifacts", List.of());
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(bundle(jar), target, Set.of()));
    }
    @Test void ambiguityAcrossJarsIsRecordedWithoutChoosingAClassProvider() throws Exception {
        byte[] jar = jar(); var manifest = manifest(jar); var target = target(jar);
        Json.array(manifest.get("artifacts")).add(Map.of("metadataPath", "mods/other.pw.toml", "outputPath", "mods/other.jar", "sha256", Json.bytesDigest(jar), "size", jar.length));
        Json.array(Json.object(target.get("composition")).get("artifacts")).add(Map.of("path", "mods/other.pw.toml", "outputPath", "mods/other.jar", "selection", "included-declaration",
                "downloadHash", Map.of("algorithm", "sha256", "value", Json.bytesDigest(jar))));
        Path path = bundle("shared.zip", manifest, Map.of("blobs/" + Json.bytesDigest(jar), jar));
        assertEquals(List.of("fixture/Mod"), ArtifactBundle.inspect(path, target, Set.of()).get("duplicateClassNames"));
    }
    @Test void staleDetailPathsAndUnspecifiedSelectionAreRequestErrors() throws Exception {
        byte[] jar = jar(); Path path = bundle(jar);
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(path, target(jar), Set.of("mods/absent.pw.toml")));
        var target = target(jar); Json.object(target.get("composition")).put("side", "unspecified");
        assertThrows(Failure.class, () -> ArtifactBundle.inspect(path, target, Set.of()));
    }
}
