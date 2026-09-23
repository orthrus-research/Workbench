package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.swing.tree.DefaultMutableTreeNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Live CLI proof for graph search -> exact gt-recipe -> bounded impact V1. */
final class LiveAtlasRecipeImpactContractTest {
    @TempDir
    Path temporary;

    @Test
    void currentShellRoutesProduceOneStrictlyLinkedNativeCandidateTree() throws Exception {
        Path root = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath()
                .resolve("../..")
                .normalize();
        Path graph = temporary.resolve("graph");
        String buildFixture = """
                from pathlib import Path
                import sys
                root = Path(sys.argv[1])
                graph = Path(sys.argv[2])
                sys.path.insert(0, str(root / 'api/src'))
                sys.path.insert(0, str(root / 'modules/atlas/src'))
                sys.path.insert(0, str(root / 'modules/atlas/tests'))
                from test_recipe_impact_report_v1 import RecipeImpactReportV1Tests
                fixture = RecipeImpactReportV1Tests().build_graph(graph)
                print(fixture['selected']['id'])
                """;
        String selectionId = CommandProcess.capture(
                CoreLaunch.resolve("python3", false, null),
                List.of("-c", buildFixture, root.toString(), graph.toString()),
                64 * 1024,
                60,
                root.toString()
        ).trim();

        CoreLaunch shell = new CoreLaunch(
                "python3 " + root.resolve("tools/workbench.py"),
                "python3",
                List.of(root.resolve("tools/workbench.py").toString()),
                "native",
                null
        );
        AtlasRecipeClient.GraphPath explicitGraph = AtlasRecipeClient.graphPath(
                shell, graph.toString()
        );
        String searchJson = CommandProcess.capture(
                shell,
                AtlasRecipeClient.searchArguments(explicitGraph, "selected", 100),
                AtlasRecipeContract.MAX_OUTPUT_BYTES,
                60,
                root.toString()
        );
        AtlasRecipeSearch search = AtlasRecipeSearch.parse(searchJson, "selected", 100);
        AtlasRecipeClient.validateSearch(explicitGraph, search);
        AtlasRecipeSearch.Result selection = search.recipes().stream()
                .filter(row -> row.selectionId().equals(selectionId))
                .findFirst()
                .orElseThrow();

        String impactJson = CommandProcess.capture(
                shell,
                AtlasRecipeClient.impactArguments(explicitGraph, selectionId, 4, 100),
                AtlasRecipeContract.MAX_OUTPUT_BYTES,
                60,
                root.toString()
        );
        AtlasRecipeImpact impact = AtlasRecipeImpact.parse(
                impactJson, selectionId, 4, 100
        );
        AtlasRecipeClient.validateImpact(
                explicitGraph, selectionId, search, selection, impact
        );

        assertEquals(selectionId, impact.selectionId());
        assertEquals(2, AtlasRecipeContract.nonnegativeInteger(
                impact.summary(), "at_risk_resource_candidate_count"
        ));
        assertEquals(1, AtlasRecipeContract.nonnegativeInteger(
                impact.summary(), "at_risk_recipe_candidate_count"
        ));
        DefaultMutableTreeNode tree = AtlasRecipeImpactTree.build(impact);
        List<String> labels = new ArrayList<>();
        Enumeration<?> children = tree.children();
        while (children.hasMoreElements()) {
            labels.add(children.nextElement().toString());
        }
        assertEquals(List.of(
                "Claim boundary (always visible)",
                "Verified graph and exact selection",
                "Summary",
                "Direct",
                "Propagation candidates",
                "Progression signals",
                "Frontiers (0)",
                "Unknowns (0)",
                "Evidence gaps (7)"
        ), labels);
        Enumeration<?> rendered = tree.depthFirstEnumeration();
        while (rendered.hasMoreElements()) {
            String label = rendered.nextElement().toString().toLowerCase();
            assertFalse(label.contains("broken"));
            assertFalse(label.contains("unreachable"));
        }
        assertTrue(impact.rawJson().contains("\"progression_signals\""));

        JsonObject unresolved = JsonParser.parseString(impactJson).getAsJsonObject();
        JsonObject unknown = new JsonObject();
        unknown.addProperty("code", "alternative-viability-unknown");
        unknown.addProperty("message", "An observed alternative is not proved viable");
        unresolved.getAsJsonArray("unknowns").add(unknown);
        JsonObject cycle = new JsonObject();
        cycle.addProperty("interpretation", "the bounded alternative-producer dependency neighborhood contains a cycle");
        cycle.addProperty("root_resource_id", "resource:fixture");
        cycle.addProperty("viability_effect", "unknown");
        cycle.add("path", JsonParser.parseString("[\"resource:fixture\",\"recipe:fixture\",\"resource:fixture\"]"));
        // Isolate this presentation case from cycle signals in the owner fixture.
        unresolved.getAsJsonObject("propagation").add("alternative_dependency_cycle_signals", new JsonArray());
        unresolved.getAsJsonObject("propagation").getAsJsonArray("alternative_dependency_cycle_signals").add(cycle);
        unresolved.getAsJsonObject("summary").addProperty("alternative_dependency_cycle_signal_count", 1);
        AtlasRecipeImpact caution = AtlasRecipeImpact.parse(unresolved.toString(), selectionId, 4, 100);
        String status = AtlasRecipeImpactTree.statusText(caution);
        assertTrue(status.contains("Unresolved within bounds"));
        assertTrue(status.contains("1 unknown(s)"));
        assertTrue(status.contains("1 alternative dependency cycle signal(s)"));
        assertFalse(status.contains("complete"));

        JsonObject frontier = new JsonObject();
        frontier.addProperty("kind", "node-bound");
        unresolved.getAsJsonArray("frontiers").add(frontier);
        unresolved.getAsJsonObject("summary").addProperty("truncated", true);
        assertTrue(AtlasRecipeImpactTree.statusText(AtlasRecipeImpact.parse(
                unresolved.toString(), selectionId, 4, 100)).startsWith("Bounded frontiers remain"));

        JsonObject inactive = JsonParser.parseString(impactJson).getAsJsonObject();
        JsonObject output = inactive.getAsJsonObject("direct").getAsJsonArray("outputs").get(0).getAsJsonObject();
        JsonObject alternative = new JsonObject();
        JsonObject alternativeRecipe = inactive.getAsJsonObject("selection").deepCopy();
        alternativeRecipe.addProperty("selection_id", "workbench-atlas-node-v2:gt-recipe:inactive-presentation-fixture");
        alternativeRecipe.getAsJsonObject("properties").addProperty("lookup_active", false);
        alternative.add("recipe", alternativeRecipe);
        JsonObject alternativeEdge = output.getAsJsonObject("output").deepCopy();
        alternativeEdge.remove("node");
        alternative.add("output_edge", alternativeEdge);
        output.getAsJsonObject("producer_portfolio").getAsJsonArray("alternative_producers").add(alternative);
        // Keep the owner's sole-active-producer status: an inactive observation is not a viable escape.
        String inactiveStatus = AtlasRecipeImpactTree.statusText(AtlasRecipeImpact.parse(
                inactive.toString(), selectionId, 4, 100));
        assertTrue(inactiveStatus.contains("1 lookup-inactive alternative recipe(s)"));
        assertTrue(inactiveStatus.startsWith("Unresolved within bounds"));

        JsonObject limited = JsonParser.parseString(impactJson).getAsJsonObject();
        JsonObject gap = new JsonObject();
        gap.addProperty("code", "graph-projection-limitations");
        gap.addProperty("message", "Chance and dynamic matching are not modeled.");
        limited.getAsJsonArray("evidence_gaps").add(gap);
        assertEquals(8, AtlasRecipeImpact.parse(limited.toString(), selectionId, 4, 100).evidenceGaps().size());
        gap.addProperty("code", "undocumented-gap");
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeImpact.parse(
                limited.toString(), selectionId, 4, 100));
        gap.addProperty("code", "graph-projection-limitations");
        limited.getAsJsonArray("evidence_gaps").add(gap.deepCopy());
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeImpact.parse(
                limited.toString(), selectionId, 4, 100));

        JsonObject manyOmissions = JsonParser.parseString(impactJson).getAsJsonObject();
        manyOmissions.getAsJsonObject("summary").addProperty("truncated", true);
        for (int index = 0; index < 501; index++) {
            JsonObject omission = new JsonObject();
            omission.addProperty("kind", "node-bound");
            omission.addProperty("phase", "fixture-" + index);
            manyOmissions.getAsJsonArray("frontiers").add(omission);
            JsonObject uncertain = new JsonObject();
            uncertain.addProperty("code", "fixture-" + index);
            uncertain.addProperty("message", "An omitted edge or phase remains unknown.");
            manyOmissions.getAsJsonArray("unknowns").add(uncertain);
        }
        var many = AtlasRecipeImpact.parse(manyOmissions.toString(), selectionId, 4, 100);
        assertEquals(501, many.frontiers().size());
        assertEquals(501, many.unknowns().size());
        for (String section : List.of("frontiers", "unknowns")) {
            JsonObject excessive = manyOmissions.deepCopy();
            while (excessive.getAsJsonArray(section).size() <= 10_000) {
                excessive.getAsJsonArray(section).add(excessive.getAsJsonArray(section).get(0).deepCopy());
            }
            assertThrows(IllegalArgumentException.class, () -> AtlasRecipeImpact.parse(
                    excessive.toString(), selectionId, 4, 100));
        }

        JsonObject wrongCount = JsonParser.parseString(impactJson).getAsJsonObject();
        wrongCount.getAsJsonObject("summary")
                .addProperty("at_risk_recipe_candidate_count", 999);
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeImpact.parse(
                wrongCount.toString(), selectionId, 4, 100
        ));

        JsonObject unknownField = JsonParser.parseString(impactJson).getAsJsonObject();
        unknownField.addProperty("ide_guess", true);
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeImpact.parse(
                unknownField.toString(), selectionId, 4, 100
        ));

        JsonObject nestedUnknown = JsonParser.parseString(impactJson).getAsJsonObject();
        nestedUnknown.getAsJsonObject("direct").getAsJsonArray("inputs")
                .get(0).getAsJsonObject().addProperty("ide_guess", true);
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeImpact.parse(
                nestedUnknown.toString(), selectionId, 4, 100
        ));

        JsonObject wrongContext = JsonParser.parseString(impactJson).getAsJsonObject();
        wrongContext.getAsJsonObject("context").addProperty(
                "root", temporary.resolve("different-graph").toString()
        );
        AtlasRecipeImpact contextChanged = AtlasRecipeImpact.parse(
                wrongContext.toString(), selectionId, 4, 100
        );
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeClient.validateImpact(
                explicitGraph, selectionId, search, selection, contextChanged
        ));

        JsonObject wrongSelection = JsonParser.parseString(impactJson).getAsJsonObject();
        wrongSelection.getAsJsonObject("selection").getAsJsonObject("properties")
                .addProperty("ide_guess", "changed");
        AtlasRecipeImpact selectionChanged = AtlasRecipeImpact.parse(
                wrongSelection.toString(), selectionId, 4, 100
        );
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeClient.validateImpact(
                explicitGraph, selectionId, search, selection, selectionChanged
        ));
    }
}
