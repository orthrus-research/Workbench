package research.orthrus.axiom;

import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class PlatformBundleTest {
    @TempDir Path root;
    static final Map<String, Object> SELECTION = Map.of("side", "client", "os", "linux", "architecture", "x86_64", "javaMajor", 25);
    static final String LIB = "org/example/api/1/api-1.jar", MAIN = "com/mojang/minecraft/1.12.2/minecraft-1.12.2-client.jar";
    byte[] jar() throws Exception { return BinaryInventoryTest.jar(Map.of("fixture/Mod.class", BinaryInventoryTest.fixtureClass())); }
    static byte[] text(Object value) { return BinaryInventoryTest.text(Json.write(value)); }
    static Map<String, Object> download(byte[] bytes) throws Exception {
        return new LinkedHashMap<>(Map.of("url", "https://example.invalid/original.jar", "sha1", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-1").digest(bytes)), "size", bytes.length));
    }
    static Map<String, Object> library(String name, byte[] bytes) throws Exception {
        return new LinkedHashMap<>(Map.of("name", name, "downloads", new LinkedHashMap<>(Map.of("artifact", download(bytes)))));
    }
    Map<String, Map<String, Object>> metadata(byte[] bytes) throws Exception {
        Map<String, Map<String, Object>> files = new LinkedHashMap<>();
        files.put("mmc-pack.json", new LinkedHashMap<>(Map.of("formatVersion", 1, "components", new ArrayList<>(List.of(
                new LinkedHashMap<>(Map.of("uid", "net.minecraft", "version", "1.12.2", "cachedVersion", "ignored")),
                new LinkedHashMap<>(Map.of("uid", "net.minecraftforge", "version", "fixture")))))));
        files.put("patches/net.minecraft.json", new LinkedHashMap<>(Map.of("formatVersion", 1, "uid", "net.minecraft", "version", "1.12.2",
                "compatibleJavaMajors", List.of(25, 26), "mainClass", "minecraft.Main", "mainJar", library("com.mojang:minecraft:1.12.2:client", bytes),
                "libraries", new ArrayList<>(List.of(library("org.example:api:1", bytes))))));
        files.put("patches/net.minecraftforge.json", new LinkedHashMap<>(Map.of("formatVersion", 1, "uid", "net.minecraftforge", "version", "fixture",
                "requires", List.of(Map.of("uid", "net.minecraft", "equals", "1.12.2")), "mainClass", "fixture.Foundation", "+tweakers", List.of("fixture.Tweaker"),
                "libraries", new ArrayList<>(List.of(library("org.example:api:1", bytes))))));
        return files;
    }
    byte[] bootstrap(Map<String, Map<String, Object>> files) throws Exception {
        Map<String, byte[]> entries = new LinkedHashMap<>(); files.forEach((name, data) -> entries.put(name, text(data)));
        return BinaryInventoryTest.jar(entries);
    }
    Map<String, Object> policy(byte[] bootstrap) {
        return new LinkedHashMap<>(Map.of("format", "workbench-cleanroom-native-runtime-lock-v1", "source_revision", "a".repeat(40), "cleanroom_version", "fixture",
                "bootstrap", Map.of("sha256", Json.bytesDigest(bootstrap), "metadata_entry", "patches/net.minecraftforge.json"),
                "native_service_expectations", Map.of("launch_main_class", "fixture.Foundation"), "artifacts", new ArrayList<>()));
    }
    Map<String, Object> plan(Map<String, Map<String, Object>> files) throws Exception {
        byte[] boot = bootstrap(files); return PlatformMetadata.inspect(boot, policy(boot), SELECTION);
    }
    List<Object> libraries(Map<String, Map<String, Object>> files) { return Json.array(files.get("patches/net.minecraftforge.json").get("libraries")); }
    record Fixture(Map<String, byte[]> members, Map<String, Object> manifest) {}
    Fixture fixture(boolean include) throws Exception {
        byte[] bytes = jar(), boot = bootstrap(metadata(bytes)), policy = text(policy(boot));
        Map<String, byte[]> members = new TreeMap<>(Map.of("policy.json", policy, "bootstrap.zip", boot));
        List<Object> records = new ArrayList<>();
        if (include) {
            for (String path : List.of(LIB, MAIN)) records.add(new LinkedHashMap<>(Map.of("path", path, "sha256", Json.bytesDigest(bytes), "size", bytes.length)));
            members.put("blobs/" + Json.bytesDigest(bytes), bytes);
        }
        return new Fixture(members, new LinkedHashMap<>(Map.of("schema", "axiom.platform.v1", "profile", "cleanroom", "policySha256", Json.bytesDigest(policy),
                "bootstrapSha256", Json.bytesDigest(boot), "selection", SELECTION, "artifacts", records)));
    }
    Path write(Fixture fixture) throws Exception {
        Path path = root.resolve(UUID.randomUUID() + ".zip");
        fixture.members().put("manifest.json", text(fixture.manifest()));
        try (ZipOutputStream zip = new ZipOutputStream(Files.newOutputStream(path))) {
            for (var row : fixture.members().entrySet()) {
                ZipEntry entry = new ZipEntry(row.getKey()); entry.setMethod(ZipEntry.STORED); entry.setSize(row.getValue().length);
                CRC32 crc = new CRC32(); crc.update(row.getValue()); entry.setCrc(crc.getValue());
                zip.putNextEntry(entry); zip.write(row.getValue()); zip.closeEntry();
            }
        }
        return path;
    }
    @Test void originalOrderEqualDuplicatesAndMainJarLast() throws Exception {
        var data = metadata(jar()); var later = Json.object(libraries(data).get(0));
        Json.object(Json.object(later.get("downloads")).get("artifact")).put("sha1", "0".repeat(40));
        var result = plan(data);
        assertEquals(List.of(LIB, MAIN), result.get("classpath"));
        var declarations = Json.array(result.get("declarations"));
        assertEquals("duplicate-equal-version", Json.object(declarations.get(1)).get("selection"));
        assertEquals("net.minecraft:libraries/0", Json.object(declarations.get(1)).get("retainedDeclaration"));
        assertEquals("fixture.Foundation", result.get("mainClass"));
        assertEquals(List.of("fixture.Tweaker"), result.get("tweakers"));
    }
    @Test void rulesApplyBeforeDuplicateMerging() throws Exception {
        var files = metadata(jar()); Map<String, Object> different = library("org.example:api:2", jar());
        different.put("rules", List.of(Map.of("action", "allow", "os", Map.of("name", "windows"))));
        libraries(files).add(different);
        assertEquals(List.of(LIB, MAIN), plan(files).get("classpath"));
        different.put("rules", List.of());
        Failure failure = assertThrows(Failure.class, () -> plan(files));
        assertEquals("platform.version-order", failure.rule); assertEquals("unsupported", failure.kind);
    }
    @Test void lastApplicableRuleAndRetainedButIgnoredOsVersion() {
        assertTrue(PlatformMetadata.active(List.of(), SELECTION));
        assertFalse(PlatformMetadata.active(List.of(Map.of("action", "allow", "os", Map.of("name", "windows"))), SELECTION));
        assertFalse(PlatformMetadata.active(List.of(Map.of("action", "allow"), Map.of("action", "disallow", "os", Map.of("name", "linux"))), SELECTION));
        assertTrue(PlatformMetadata.active(List.of(Map.of("action", "disallow"), Map.of("action", "allow", "os", Map.of("name", "linux-x86_64", "version", "not-a-regex["))), SELECTION));
        assertThrows(Failure.class, () -> PlatformMetadata.active(List.of(Map.of("action", "allow", "features", Map.of())), SELECTION));
    }
    @Test void nativeMapSelectsClassifierSeparatelyFromOrdinaryJar() throws Exception {
        byte[] bytes = jar(); var files = metadata(bytes);
        Map<String, Object> nativeLibrary = library("org.example:api:1", bytes);
        nativeLibrary.put("natives", Map.of("linux", "natives-32", "linux-x86_64", "natives-${arch}"));
        Json.object(nativeLibrary.get("downloads")).put("classifiers", Map.of("natives-64", download(bytes)));
        nativeLibrary.put("extract", Map.of("exclude", List.of("META-INF/"))); libraries(files).add(nativeLibrary);
        // A native-looking classifier without a natives map remains an ordinary classpath artifact.
        libraries(files).add(library("org.example:other:1:natives-linux", bytes));
        var result = plan(files);
        assertEquals(List.of(LIB, "org/example/other/1/other-1-natives-linux.jar", MAIN), result.get("classpath"));
        assertEquals(List.of("org/example/api/1/api-1-natives-64.jar"), result.get("nativeExtractions"));
        assertEquals(List.of("META-INF/"), Json.object(Json.array(result.get("libraries")).get(3)).get("extractExclude"));
    }
    @Test void absentNativeClassifierIsInactiveAndNotAnOrdinaryFallback() throws Exception {
        var files = metadata(jar()); var lib = library("org.example:native:1", jar()); lib.put("natives", Map.of("windows", "native")); libraries(files).add(lib);
        var result = plan(files); assertEquals(List.of(), result.get("nativeExtractions"));
        assertEquals("inactive-native-platform", Json.object(Json.array(result.get("declarations")).get(2)).get("selection"));
    }
    @Test void coordinateNotDownloadPathOwnsStorage() throws Exception {
        var files = metadata(jar()); var lib = Json.object(Json.array(files.get("patches/net.minecraft.json").get("libraries")).get(0));
        Json.object(Json.object(lib.get("downloads")).get("artifact")).put("path", "alternate/file.jar");
        assertEquals(List.of(LIB, MAIN), plan(files).get("classpath"));
        assertEquals("g/a/1/a-1-special.zip", PlatformMetadata.Coordinate.parse("g:a:1:special@zip").path());
        for (String invalid : List.of("../a:b:1", "a..b:c:1", "a:b:../1", "a:b:1@../zip", "a:b:"))
            assertThrows(Failure.class, () -> PlatformMetadata.Coordinate.parse(invalid));
    }
    @Test void exactRequirementsDifferFromSuggestionsAndCaches() throws Exception {
        var files = metadata(jar()); files.get("patches/net.minecraftforge.json").put("requires", List.of(Map.of("uid", "net.minecraft", "suggests", "other")));
        assertEquals(List.of(LIB, MAIN), plan(files).get("classpath"));
        files.get("patches/net.minecraftforge.json").put("requires", List.of(Map.of("uid", "net.minecraft", "equals", "other")));
        assertThrows(Failure.class, () -> plan(files));
        files.get("patches/net.minecraftforge.json").put("requires", List.of(Map.of("uid", "absent")));
        assertThrows(Failure.class, () -> plan(files));
    }
    @Test void explicitPlatformAndJavaSelectionAreRequired() throws Exception {
        byte[] boot = bootstrap(metadata(jar()));
        for (var pair : List.of(Map.entry("side", "server"), Map.entry("os", "osx"), Map.entry("architecture", "arm64"))) {
            var selection = new LinkedHashMap<>(SELECTION); selection.put(pair.getKey(), pair.getValue());
            assertEquals("unsupported", assertThrows(Failure.class, () -> PlatformMetadata.inspect(boot, policy(boot), selection)).kind);
        }
        var selection = new LinkedHashMap<>(SELECTION); selection.put("javaMajor", 21);
        assertThrows(Failure.class, () -> PlatformMetadata.inspect(boot, policy(boot), selection));
    }
    @Test void unsupportedMetadataIsNeverIgnoredAsHarmless() throws Exception {
        var files = metadata(jar()); files.get("patches/net.minecraftforge.json").put("agents", List.of());
        assertThrows(Failure.class, () -> plan(files));
        files.get("patches/net.minecraftforge.json").remove("agents"); files.put("patches/extra.json", Map.of());
        assertThrows(Failure.class, () -> plan(files));
    }
    @Test void policyPinsStrengthenButDoNotReplaceBootstrapSelection() throws Exception {
        byte[] bytes = jar(), boot = bootstrap(metadata(bytes)); var policy = policy(boot);
        var pin = new LinkedHashMap<String, Object>(download(bytes)); pin.put("coordinate", "org.example:api:1"); pin.put("sha256", Json.bytesDigest(bytes));
        policy.put("artifacts", List.of(pin));
        var result = PlatformMetadata.inspect(boot, policy, SELECTION);
        assertEquals(2, Json.array(result.get("libraries")).size());
        assertEquals(Json.bytesDigest(bytes), Json.object(Json.array(result.get("libraries")).get(0)).get("policySha256"));
        pin.put("sha1", "0".repeat(40)); assertThrows(Failure.class, () -> PlatformMetadata.inspect(boot, policy, SELECTION));
    }
    @Test void completeByteInventoryIsNotAnInstalledCompositionClaim() throws Exception {
        Path path = write(fixture(true)); var result = PlatformBundle.inspect(path, null);
        assertEquals("complete-declared-selection", result.get("selectedArtifactCoverage")); assertEquals(2, result.get("providedArtifacts"));
        assertEquals(1, result.get("duplicateClassCount")); assertEquals(List.of("fixture/Mod"), result.get("duplicateClassNames"));
        assertEquals(false, result.get("classesLoaded")); assertEquals(false, result.get("wholePackParity")); assertEquals(false, result.get("installedCompositionQualified"));
        assertEquals("accepted", new Engine().inspectPlatform(path, null).get("status"));
    }
    @Test void metadataOnlyInputReportsAllMissingWithoutClaimingClosure() throws Exception {
        var result = PlatformBundle.inspect(write(fixture(false)), null);
        assertEquals("partial-declared-selection", result.get("selectedArtifactCoverage")); assertEquals(List.of(LIB, MAIN), result.get("missingArtifacts"));
        assertEquals(0, result.get("providedArtifacts"));
    }
    @Test void combinedSummaryRetainsIdentityCoverageAndRequestedDetailsWithoutDuplicatingInventories() throws Exception {
        var complete = PlatformBundle.inspect(write(fixture(true)), null);
        var summary = PlatformBundle.summary(complete, null);
        assertEquals(complete.get("platformId"), summary.get("platformId"));
        assertEquals(complete.get("providedArtifacts"), summary.get("providedArtifacts"));
        assertEquals(complete.get("missingArtifacts"), summary.get("missingArtifacts"));
        assertFalse(summary.containsKey("artifacts")); assertEquals(List.of(), summary.get("artifactDetails"));
        assertEquals(2, Json.object(summary.get("metadata")).get("libraryDeclarations"));
        assertEquals(2, Json.object(summary.get("metadata")).get("selectedLibraries"));
        assertEquals(List.of(LIB, MAIN), Json.object(summary.get("metadata")).get("classpath"));
        assertEquals(1, Json.array(PlatformBundle.summary(complete, Map.of("libraryPaths", List.of(LIB))).get("artifactDetails")).size());
        assertTrue(complete.containsKey("artifacts")); assertTrue(Json.object(complete.get("metadata")).containsKey("declarations"));
    }
    @Test void detailFiltersPreserveIdentityAndRejectStaleOrAbsentPaths() throws Exception {
        Path path = write(fixture(true)); var result = PlatformBundle.inspect(path, null);
        var query = new LinkedHashMap<String, Object>(Map.of("schema", "axiom.platform-request.v1", "platformId", result.get("platformId"), "libraryPaths", List.of(LIB)));
        var detailed = PlatformBundle.inspect(path, query); assertEquals(result.get("platformId"), detailed.get("platformId"));
        query.put("libraryPaths", List.of("absent.jar")); assertThrows(Failure.class, () -> PlatformBundle.inspect(path, query));
        query.remove("libraryPaths"); query.put("platformId", "stale"); assertThrows(Failure.class, () -> PlatformBundle.inspect(path, query));
    }
    @Test void tamperedPolicyBootstrapAndArtifactsFailClosed() throws Exception {
        for (String member : List.of("policy.json", "bootstrap.zip")) {
            var fixture = fixture(true); fixture.members().put(member, text(Map.of()));
            assertThrows(Failure.class, () -> PlatformBundle.inspect(write(fixture), null));
        }
        var fixture = fixture(true); String blob = fixture.members().keySet().stream().filter(name -> name.startsWith("blobs/")).findFirst().orElseThrow();
        fixture.members().get(blob)[0] ^= 1;
        assertThrows(Failure.class, () -> PlatformBundle.inspect(write(fixture), null));
    }
    @Test void unselectedDuplicateAndUnreferencedArtifactsAreRejected() throws Exception {
        var fixture = fixture(true); Json.array(fixture.manifest().get("artifacts")).add(Json.array(fixture.manifest().get("artifacts")).get(0));
        var repeated = fixture; assertThrows(Failure.class, () -> PlatformBundle.inspect(write(repeated), null));
        fixture = fixture(true); Json.object(Json.array(fixture.manifest().get("artifacts")).get(0)).put("path", "unselected.jar");
        var unselected = fixture; assertThrows(Failure.class, () -> PlatformBundle.inspect(write(unselected), null));
        fixture = fixture(true); fixture.members().put("blobs/" + "0".repeat(64), new byte[]{0});
        var extra = fixture; assertThrows(Failure.class, () -> PlatformBundle.inspect(write(extra), null));
    }
    @Test void differentSelectionChangesInputIdentity() throws Exception {
        var fixture = fixture(true); var original = PlatformBundle.inspect(write(fixture), null);
        var selection = new LinkedHashMap<>(SELECTION); selection.put("javaMajor", 26); fixture.manifest().put("selection", selection);
        assertNotEquals(original.get("platformId"), PlatformBundle.inspect(write(fixture), null).get("platformId"));
    }
}
