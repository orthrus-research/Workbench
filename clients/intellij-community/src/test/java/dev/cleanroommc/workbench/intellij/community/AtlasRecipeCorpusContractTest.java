package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/** Opt-in parity against retained installed CLI records, separate from native IDE acceptance. */
final class AtlasRecipeCorpusContractTest {
    @Test
    void exactInstalledCliCorpusRetainsIdentityAndEvidenceInNativePresentation() throws Exception {
        String location = System.getenv("WORKBENCH_TEST_ATLAS_CORPUS");
        assumeTrue(location != null && !location.isBlank(), "explicit installed Atlas corpus required");
        JsonObject corpus = JsonParser.parseString(Files.readString(Path.of(location))).getAsJsonObject();
        assertEquals("workbench-atlas-client-corpus-v1", corpus.get("format").getAsString());
        assertTrue(!corpus.getAsJsonArray("cases").isEmpty());
        Set<String> identities = new HashSet<>();
        for (var value : corpus.getAsJsonArray("cases")) {
            JsonObject row = value.getAsJsonObject();
            assertTrue(identities.add(row.get("id").getAsString()));
            String graphRoot = row.get("graph").getAsString();
            String selectionId = row.get("selection_id").getAsString();
            assertTrue(Path.of(graphRoot).isAbsolute());
            var graph = new AtlasRecipeClient.GraphPath(graphRoot, graphRoot, graphRoot);
            var search = AtlasRecipeSearch.parse(Files.readString(Path.of(row.get("search_record").getAsString())),
                    row.get("query").getAsString(), 200);
            AtlasRecipeClient.validateSearch(graph, search);
            var selected = search.recipes().stream().filter(recipe -> recipe.selectionId().equals(selectionId))
                    .findFirst().orElseThrow();
            String raw = Files.readString(Path.of(row.get("impact_record").getAsString()));
            String exploration = row.has("exploration") ? row.get("exploration").getAsString() : "bounded";
            if (exploration.equals("complete-finite")) {
                assertTrue(!row.has("max_depth") && !row.has("max_nodes"));
                var complete = AtlasCompleteRecipeImpact.parse(raw, selectionId);
                AtlasRecipeClient.validateCompleteImpact(graph, selectionId, search, selected, complete);
                assertEquals(raw, complete.rawJson());
                var tree = AtlasCompleteRecipeImpactTree.build(complete);
                assertNotNull(tree);
                assertEquals(complete.statusText(), tree.getChildAt(0).toString());
                assertTrue(complete.statusText().contains("viability unknown"));
                continue;
            }
            assertEquals("bounded", exploration);
            var impact = AtlasRecipeImpact.parse(raw, selectionId,
                    row.get("max_depth").getAsInt(), row.get("max_nodes").getAsInt());
            AtlasRecipeClient.validateImpact(graph, selectionId, search, selected, impact);
            assertEquals(JsonParser.parseString(raw), JsonParser.parseString(impact.rawJson()));
            String status = AtlasRecipeImpactTree.statusText(impact);
            assertTrue(status.contains(impact.frontiers().size() + " frontier(s)"));
            assertTrue(status.contains(impact.unknowns().size() + " unknown(s)"));
            assertTrue(status.contains(impact.propagation().getAsJsonArray("alternative_dependency_cycle_signals").size()
                    + " alternative dependency cycle signal(s)"));
            var tree = AtlasRecipeImpactTree.build(impact);
            assertNotNull(tree);
            assertEquals(status, ((javax.swing.tree.DefaultMutableTreeNode) tree.getChildAt(0)).getChildAt(0).toString());
        }
        System.out.println("Atlas installed CLI corpus contract cases: " + identities.size());
    }
}
