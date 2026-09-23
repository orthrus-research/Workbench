package research.orthrus.axiom;

import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class SourcePlanTest {
    @Test void nativeSuffixesAndSpecificityAreNotGlobalAlphabeticalSort() {
        var files = List.of("a/x.groovy", "a/sub/a.gvy", "a/sub/b.gy", "z.gsh", "a/no.txt");
        assertEquals(List.of("a/x.groovy", "a/sub/a.gvy", "a/sub/b.gy", "z.gsh"),
                SourcePlan.ordered(files, List.of("a", "a/sub", "z.gsh")));
    }
    @Test void repeatedEqualSpecificityDoesNotMoveFileAndSpecificFileDoesMoveIt() {
        var files = List.of("a/a.groovy", "a/b.groovy", "b/a.groovy");
        assertEquals(List.of("a/a.groovy", "a/b.groovy", "b/a.groovy"), SourcePlan.ordered(files, List.of("", "a")));
        assertEquals(List.of("a/b.groovy", "b/a.groovy", "a/a.groovy"), SourcePlan.ordered(files, List.of("", "a/a.groovy")));
    }
    @Test void loaderCrossStageRejectionUsesTextPrefixNotDirectoryContainment() {
        Map<String, Object> phases = new LinkedHashMap<>();
        phases.put("preInit", List.of("pre/")); phases.put("postInit", List.of("prelude/", "post/"));
        var diagnostics = new ArrayList<Map<String, Object>>();
        var result = SourcePlan.loaders(Map.of("loaders", phases), diagnostics);
        assertEquals(List.of("post"), result.get("postInit"));
        assertEquals("loader.cross-stage-path", diagnostics.get(0).get("rule"));
    }
    @Test void separatorsAndDuplicatePathsAreNormalizedBeforeSelection() {
        var result = SourcePlan.loaders(Map.of("loaders", Map.of("preInit", List.of("classes\\", "classes/", "classes\\nested/"))), new ArrayList<>());
        assertEquals(List.of("classes", "classes/nested"), result.get("preInit"));
    }
    @Test void missingLoadersDoNotInventTheDefaultProgram() {
        var diagnostics = new ArrayList<Map<String, Object>>();
        assertTrue(SourcePlan.loaders(Map.of(), diagnostics).isEmpty());
        assertEquals("loader.no-loaders", diagnostics.get(0).get("rule"));
    }
    @Test void unsafeInputPathsAndLegacyMigrationDoNotEscapePackage() {
        assertThrows(Failure.class, () -> SourcePlan.loaders(Map.of("loaders", Map.of("preInit", List.of("../outside"))), new ArrayList<>()));
        assertThrows(Failure.class, () -> SourcePlan.loaders(Map.of("classes", List.of("classes")), new ArrayList<>()));
        for (String path : List.of("/tmp/file", "a//b", "a/../b", "a\\b", "C:/file", "a/", "./a"))
            assertThrows(Failure.class, () -> SourceTarget.path(path));
    }
    @Test void syntaxInventoryVisitsHelpersWithoutExecutingThem() {
        var facts = SourcePlan.syntax("classes/Recipes.groovy", "package classes\nimport static prePostInit.Recipemaps.*\nclass Recipes { static void make(String name) { while(true) { ore(name); fluid('water'); new File('/tmp/must-not-exist').text = 'bad' } } }");
        assertNull(facts.error); assertTrue(facts.classes.contains("classes.Recipes"));
        assertTrue(facts.imports.contains("prePostInit.Recipemaps.*"));
        assertEquals(Set.of("fluid:water"), facts.registryReferences);
        assertEquals(1, facts.dynamicReferences); assertEquals(1L, facts.methods.get("ore"));
    }
    @Test void syntaxFailureIsRecordedWithoutClaimingNativeRegistrationFailure() {
        var facts = SourcePlan.syntax("broken.groovy", "def broken(");
        assertNotNull(facts.error); assertTrue(facts.registryReferences.isEmpty());
    }
    @Test void gitBlobIdentityIncludesItsHeader() {
        assertEquals("e69de29bb2d1d6434b8b29ae775ad8c2e48c5391", SourceTarget.gitDigest("blob", new byte[0]));
        assertEquals("4b825dc642cb6eb9a060e54bf8d69288fbee4904", SourceTarget.gitDigest("tree", new byte[0]));
    }
}
