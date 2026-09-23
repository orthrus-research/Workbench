package research.orthrus.axiom;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class TargetCompositionTest {
    private final Map<String, SourceTarget.SourceFile> files = new TreeMap<>();
    private Object lock = Map.of();
    private static final String MOD = "mods/engine.pw.toml";
    private static final String SHA = "a".repeat(40);

    private void source(String repository, String path, String text) {
        byte[] raw = text.getBytes(StandardCharsets.UTF_8);
        files.put(repository + ":" + path, new SourceTarget.SourceFile(repository, path, Json.bytesDigest(raw), raw.length, null, raw));
    }
    private void source(String path, String text) { source("supersymmetry", path, text); }
    private String text(String path) { return new String(files.get("supersymmetry:" + path).overlay(), StandardCharsets.UTF_8); }
    private String mod(String name, String filename, String tail) {
        return "name = '" + name + "'\nfilename = '" + filename + "'\n[download]\nhash-format='sha1'\nhash='" + SHA
                + "'\nurl='https://example.com/mod.jar'\n" + tail;
    }
    private void fixture() {
        source(MOD, mod("Engine", "engine.jar", ""));
        source("config/registry.cfg", "I:seed=10\n");
        reindex();
    }
    private void reindex() {
        StringBuilder index = new StringBuilder("hash-format='sha256'\n");
        for (var file : new ArrayList<>(files.values())) if (file.repository().equals("supersymmetry")
                && !Set.of("pack.toml", "index.toml").contains(file.path())) {
            index.append("[[files]]\nfile='").append(file.path()).append("'\nhash='").append(file.sha256())
                    .append("'\nmetafile=").append(file.path().endsWith(".pw.toml")).append('\n');
        }
        source("index.toml", index.toString());
        repack();
    }
    private void repack() {
        source("pack.toml", "name='Fixture'\npack-format='packwiz:1.1.0'\nversion='1.0'\n[versions]\nminecraft='1.12.2'\n"
                + "[index]\nfile='index.toml'\nhash-format='sha256'\nhash='" + files.get("supersymmetry:index.toml").sha256() + "'\n");
    }
    private Map<String, Object> inspect(Object selection) throws Exception {
        String candidate = Json.digest(files.values().stream().map(SourceTarget.SourceFile::identity).toList());
        return new TargetComposition(SourceTarget.SourceFile::overlay, files, lock, selection).inspect(Map.of("artifactDirectory", "mods/"), candidate);
    }
    private List<Object> artifacts(Map<String, Object> result) { return Json.array(result.get("artifacts")); }
    private Map<String, Object> artifact(Map<String, Object> result, String path) {
        return artifacts(result).stream().map(Json::object).filter(row -> row.get("path").equals(path)).findFirst().orElseThrow();
    }
    private boolean diagnostic(Map<String, Object> result, String rule) {
        return Json.array(result.get("diagnostics")).stream().map(Json::object).anyMatch(row -> row.get("rule").equals(rule));
    }

    @Test void verifiedIndexAndCompleteDeclarationsDoNotAssertInstalledComposition() throws Exception {
        fixture(); var result = inspect(null);
        assertEquals(true, result.get("indexVerified")); assertEquals(true, result.get("declarationInventoryComplete"));
        assertEquals(1, result.get("parsedDescriptors")); assertEquals(0, result.get("selectedDeclarations"));
        assertEquals("unspecified", result.get("side"));
        assertEquals("verified-index-member", artifact(result, MOD).get("indexMembership"));
        assertEquals(false, result.get("artifactBytesVerified")); assertEquals(false, result.get("registryConstructed"));
        assertEquals(false, result.get("installedCompositionQualified"));
    }
    @Test void unindexedMetadataIsDiscoveredButNotInventedAsInstalledInput() throws Exception {
        fixture(); source("mods/extra.pw.toml", mod("Extra", "extra.jar", ""));
        var result = inspect(Map.of("side", "client"));
        assertEquals(2, result.get("descriptorFiles")); assertEquals(2, result.get("selectedDeclarations"));
        assertEquals("not-indexed", artifact(result, "mods/extra.pw.toml").get("indexMembership"));
        assertEquals("unresolved", artifact(result, "mods/extra.pw.toml").get("activation"));
    }
    @Test void badCommittedIndexDoesNotHideOtherDeclaredDependencies() throws Exception {
        fixture(); source("index.toml", "\n");
        var result = inspect(null);
        assertEquals(false, result.get("indexVerified")); assertEquals(1, result.get("parsedDescriptors"));
        assertTrue(diagnostic(result, "composition.index-hash")); assertTrue(diagnostic(result, "composition.metadata"));
    }
    @Test void staleMemberAndDuplicateIndexPathsAreDiagnosed() throws Exception {
        fixture(); source("config/registry.cfg", "I:seed=20\n");
        assertTrue(diagnostic(inspect(null), "composition.index-member-hash"));
        source("index.toml", text("index.toml") + "[[files]]\nfile='" + MOD + "'\nhash='" + "0".repeat(64) + "'\nmetafile=true\n"); repack();
        var result = inspect(null); assertTrue(diagnostic(result, "composition.index-duplicate")); assertEquals(false, result.get("indexVerified"));
    }
    @Test void sideAndOptionalChoicesAreExplicitAndBindCompositionIdentity() throws Exception {
        fixture(); source(MOD, "side='client'\n" + mod("Engine", "engine.jar", "[option]\noptional=true\ndefault=false\n")); reindex();
        var defaults = inspect(Map.of("side", "client")); assertEquals(0, defaults.get("selectedDeclarations"));
        var enabled = inspect(Map.of("side", "client", "options", Map.of(MOD, true)));
        assertEquals(1, enabled.get("selectedDeclarations")); assertNotEquals(defaults.get("compositionId"), enabled.get("compositionId"));
        assertEquals(0, inspect(Map.of("side", "server", "options", Map.of(MOD, true))).get("selectedDeclarations"));
        assertThrows(Failure.class, () -> inspect(Map.of("options", Map.of(MOD, true))));
        assertThrows(Failure.class, () -> inspect(Map.of("side", "client", "options", Map.of("unknown.pw.toml", true))));
        assertThrows(Failure.class, () -> inspect(Map.of("side", "both")));
    }
    @Test void requiredModsCannotBeDisabledAndBadBooleanIsNotTruthy() throws Exception {
        fixture(); assertThrows(Failure.class, () -> inspect(Map.of("side", "client", "options", Map.of(MOD, false))));
        source(MOD, mod("Engine", "engine.jar", "[option]\noptional='false'\n"));
        assertEquals(false, inspect(null).get("declarationInventoryComplete"));
    }
    @Test void duplicateTomlKeysAndWrongTypedValuesDoNotBecomeValidMetadata() throws Exception {
        fixture(); source(MOD, "name='Duplicate'\n" + text(MOD));
        assertTrue(diagnostic(inspect(null), "composition.toml"));
        source(MOD, mod("Engine", "engine.jar", "").replace("hash-format='sha1'", "hash-format=1"));
        assertTrue(diagnostic(inspect(null), "composition.metadata"));
    }
    @Test void outputPathsPreserveSpecialCharactersAndRejectEscapesAndCollisions() throws Exception {
        fixture(); source(MOD, mod("Engine", "nested/A [one].jar", ""));
        assertEquals("mods/nested/A [one].jar", artifact(inspect(null), MOD).get("outputPath"));
        source("mods/other.pw.toml", mod("Other", "nested/a [one].jar", ""));
        assertTrue(diagnostic(inspect(null), "composition.output-collision"));
        source(MOD, mod("Engine", "../outside.jar", "")); assertTrue(diagnostic(inspect(null), "composition.metadata"));
        source(MOD, mod("Engine", "engine.pw.toml", "")); assertTrue(diagnostic(inspect(null), "composition.output-source-collision"));
    }
    @Test void downloadOutputCannotAlsoBeAnotherOutputsDirectory() throws Exception {
        fixture(); source(MOD, mod("Engine", "nested", "")); source("mods/other.pw.toml", mod("Other", "nested/other.jar", ""));
        assertTrue(diagnostic(inspect(null), "composition.output-directory-collision"));
    }
    @Test void uppercaseHashesAreAcceptedButUnsupportedHashesAreVisible() throws Exception {
        fixture(); source(MOD, text(MOD).replace(SHA, SHA.toUpperCase(Locale.ROOT)));
        assertEquals(SHA, Json.object(artifact(inspect(null), MOD).get("downloadHash")).get("value"));
        source(MOD, text(MOD).replace("sha1", "murmur2")); assertTrue(diagnostic(inspect(null), "composition.hash"));
    }
    @Test void curseforgeModeUsesExactIdsWithoutNetworkOrInventedUrls() throws Exception {
        fixture(); source(MOD, mod("Engine", "engine.jar", "").replace("url='https://example.com/mod.jar'",
                "mode='metadata:curseforge'\n[update.curseforge]\nfile-id=42\nproject-id=100"));
        var locator = Json.object(artifact(inspect(null), MOD).get("download"));
        assertEquals(42L, locator.get("fileId")); assertFalse(locator.containsKey("url"));
        source(MOD, text(MOD).replace("file-id=42", "file-id=0")); assertTrue(diagnostic(inspect(null), "composition.metadata"));
    }
    @Test void changedArtifactCannotInheritOldSourceBinding() throws Exception {
        fixture(); lock = Map.of("selected_dependencies", List.of(Map.of("manifest", MOD, "source_repository", "gtceu", "filename", "engine.jar",
                "declared_download_hash", Map.of("algorithm", "sha1", "value", SHA))));
        assertTrue(diagnostic(inspect(null), "composition.reference-source-missing"));
        source("gtceu", "src/main/java/example/Engine.java", "class Engine {}\n");
        var original = inspect(null); assertEquals(1, original.get("referenceSourceDeclarations"));
        source(MOD, text(MOD).replace(SHA, "b".repeat(40)));
        var edited = inspect(null); assertTrue(diagnostic(edited, "composition.source-binding-drift"));
        assertEquals("descriptor-differs-from-source-lock", artifact(edited, MOD).get("sourceCorrespondence"));
        assertNotEquals(original.get("compositionId"), edited.get("compositionId"));
    }
    @Test void candidateConfigurationAndMixinChangesInvalidateIdentityWithoutPretendingActivation() throws Exception {
        fixture(); source("susy-core", "src/main/resources/mixins.susy.gregtech.json", "{\"package\":\"example\",\"mixins\":[\"Rule\"]}");
        var original = inspect(null); assertEquals(1, Json.array(original.get("transformationResources")).size());
        assertEquals(false, original.get("activeTransformationsResolved"));
        source("config/registry.cfg", "I:seed=11\n");
        var changed = inspect(null); assertNotEquals(original.get("configurationId"), changed.get("configurationId"));
        assertNotEquals(original.get("compositionId"), changed.get("compositionId"));
    }
    @Test void missingIndexedFilesPreservedInputsAndAliasesStayUnqualified() throws Exception {
        fixture(); source("index.toml", text("index.toml") + "[[files]]\nfile='missing.bin'\nhash='" + "0".repeat(64) + "'\npreserve=true\nalias='aliased.bin'\n"); repack();
        var result = inspect(null);
        assertTrue(diagnostic(result, "composition.index-member-missing")); assertTrue(diagnostic(result, "composition.preserved-input"));
        assertTrue(diagnostic(result, "composition.index-alias")); assertEquals(false, result.get("indexVerified"));
    }
    @Test void looseBinaryFilesAreNeverSilentlyDropped() throws Exception {
        fixture(); source("mods/loose.jar", "not-actually-a-jar");
        assertEquals(1, Json.array(inspect(null).get("looseArtifactFiles")).size());
    }
    @Test void unsafeUrlsUnknownModesAndExcessiveNestingAreNotNativeRecipeRejections() throws Exception {
        fixture(); source(MOD, text(MOD).replace("https://example.com/mod.jar", "file:///etc/passwd"));
        assertTrue(diagnostic(inspect(null), "composition.metadata"));
        source(MOD, mod("Engine", "engine.jar", "").replace("url=", "mode='custom'\nurl="));
        assertTrue(diagnostic(inspect(null), "composition.download-mode"));
        source(MOD, "x=" + "[".repeat(60) + "]".repeat(60));
        assertTrue(diagnostic(inspect(null), "composition.nesting-bound"));
    }
    @Test void engineIdentityIncludesEveryLockedRuntimeDependency() {
        var identities = Target.runtimeCodeIdentities();
        assertEquals(6, identities.size()); assertTrue(identities.containsKey("org.tomlj.Toml"));
        assertTrue(identities.containsKey("it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap"));
        assertTrue(identities.values().stream().allMatch(hash -> hash.matches("[0-9a-f]{64}")));
    }
    @Test void nativeEmptySideAndDownloadModeUseTheirDeclaredDefaults() throws Exception {
        fixture(); source(MOD, "side=''\n" + mod("Engine", "engine.jar", "").replace("url=", "mode=''\nurl="));
        var result = inspect(Map.of("side", "server"));
        assertEquals("both", artifact(result, MOD).get("side")); assertEquals(1, result.get("selectedDeclarations"));
        assertEquals("url", Json.object(artifact(result, MOD).get("download")).get("mode"));
    }
    @Test void resourceGuardIgnoresQuotedDelimitersButCannotBeBypassedByThem() throws Exception {
        fixture(); source(MOD, "description='''" + "[{}]".repeat(100) + "'''\n" + text(MOD));
        assertEquals(true, inspect(null).get("declarationInventoryComplete"));
        source(MOD, "x=" + "['" + "]".repeat(100) + "'," + "[".repeat(49) + "0" + "]".repeat(50));
        assertTrue(diagnostic(inspect(null), "composition.nesting-bound"));
        source(MOD, "a.".repeat(49) + "end=1");
        assertTrue(diagnostic(inspect(null), "composition.nesting-bound"));
        source(MOD, "# " + "[".repeat(100) + "\n" + mod("Engine", "engine.jar", ""));
        assertEquals(true, inspect(null).get("declarationInventoryComplete"));
    }
    @Test void invalidIndexEntryCannotHideLaterMetadataAndEmptyIndexNeedsKnownHashFormat() throws Exception {
        fixture(); source("extra/descriptor.toml", mod("Extra", "extra.jar", ""));
        source("index.toml", "hash-format='sha256'\n[[files]]\nfile='../unsafe'\nhash='x'\n[[files]]\nfile='extra/descriptor.toml'\nmetafile=true\nhash='"
                + files.get("supersymmetry:extra/descriptor.toml").sha256() + "'\n"); repack();
        var result = inspect(null);
        assertEquals(2, result.get("parsedDescriptors")); assertEquals(false, result.get("indexVerified"));
        source("index.toml", "hash-format='unknown'\n"); repack();
        assertTrue(diagnostic(inspect(null), "composition.hash")); assertEquals(false, inspect(null).get("indexVerified"));
    }
}
