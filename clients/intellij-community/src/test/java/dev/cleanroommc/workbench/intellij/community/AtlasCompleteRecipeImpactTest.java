package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import org.junit.jupiter.api.Test;

import javax.swing.tree.DefaultMutableTreeNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.*;

/** Owner-engine records with portable context roots, plus focused corruptions. */
final class AtlasCompleteRecipeImpactTest {
    private static final String BASE = "atlas-complete-impact-v2.json";

    private static String resource(String name) throws IOException {
        try (var stream = AtlasCompleteRecipeImpactTest.class.getResourceAsStream("/" + name)) {
            assertNotNull(stream, name);
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static JsonObject fixture(String name) throws IOException {
        return JsonParser.parseString(resource(name)).getAsJsonObject();
    }

    private static String selection(JsonObject root) {
        return root.getAsJsonObject("selection").get("selection_id").getAsString();
    }

    private static AtlasCompleteRecipeImpact parse(JsonObject root) {
        return AtlasCompleteRecipeImpact.parse(root.toString(), selection(root));
    }

    private static JsonObject component(JsonObject root) {
        return root.getAsJsonObject("propagation").getAsJsonArray("alternative_dependency_components")
                .get(0).getAsJsonObject();
    }

    private static JsonObject output(JsonObject root) {
        return root.getAsJsonObject("direct").getAsJsonArray("outputs").get(0).getAsJsonObject();
    }

    private static JsonObject portfolio(JsonObject root) {
        return output(root).getAsJsonObject("producer_portfolio");
    }

    private static AtlasRecipeClient.GraphPath graph(JsonObject root) {
        return AtlasRecipeClient.graphPath(CoreLaunch.resolve("/opt/workbench/bin/workbench", false, null),
                root.getAsJsonObject("context").get("root").getAsString());
    }

    private static AtlasRecipeSearch search(String name) throws IOException {
        String raw = resource(name);
        String query = JsonParser.parseString(raw).getAsJsonObject().get("query").getAsString();
        return AtlasRecipeSearch.parse(raw, query, 100);
    }

    private static void refuses(String name, Consumer<JsonObject> mutate) throws IOException {
        JsonObject root = fixture(name);
        mutate.accept(root);
        assertThrows(IllegalArgumentException.class, () -> parse(root));
    }

    @Test
    void acceptsEngineRecordAndPreservesExactSearchGraphSelectionAndModelLimits() throws IOException {
        JsonObject root = fixture(BASE);
        AtlasCompleteRecipeImpact impact = parse(root);
        AtlasRecipeSearch search = search("atlas-complete-search-v2.json");
        AtlasRecipeSearch.Result selected = search.recipes().stream()
                .filter(row -> row.selectionId().equals(selection(root))).findFirst().orElseThrow();
        assertDoesNotThrow(() -> AtlasRecipeClient.validateCompleteImpact(graph(root), selection(root), search, selected, impact));
        assertEquals(root, JsonParser.parseString(impact.rawJson()));
        assertTrue(impact.statusText().contains("exploration complete"));
        assertTrue(impact.statusText().contains("evidence incomplete"));
        assertTrue(impact.statusText().contains("viability unknown"));
        assertEquals("unknown", component(root).get("viability_effect").getAsString());
        assertEquals("not-assessed", portfolio(root).get("viability").getAsString());
        assertTrue(root.getAsJsonArray("frontiers").isEmpty());
        assertFalse(root.getAsJsonObject("summary").get("truncated").getAsBoolean());
        assertEquals(List.of("atlas", "recipes", "impact", "/fixture/atlas-complete", selection(root),
                "--exploration", "complete-finite", "--json"),
                AtlasRecipeClient.completeImpactArguments(graph(root), selection(root)));
    }

    @Test
    void graphRootGraphIdentityAndSelectedSnapshotRemainBoundToPriorSearch() throws IOException {
        JsonObject original = fixture(BASE);
        AtlasRecipeSearch search = search("atlas-complete-search-v2.json");
        AtlasRecipeSearch.Result selected = search.recipes().getFirst();
        for (Consumer<JsonObject> mutation : List.<Consumer<JsonObject>>of(
                root -> root.getAsJsonObject("context").addProperty("root", "/fixture/other-graph"),
                root -> root.getAsJsonObject("context").addProperty("graph_set_id", "workbench-atlas-graph-set-v2:sha256:" + "f".repeat(64)),
                root -> root.getAsJsonObject("selection").getAsJsonObject("properties").addProperty("eut", 999))) {
            JsonObject changed = original.deepCopy();
            mutation.accept(changed);
            AtlasCompleteRecipeImpact impact = parse(changed);
            assertThrows(IllegalArgumentException.class, () -> AtlasRecipeClient.validateCompleteImpact(
                    graph(original), selection(original), search, selected, impact));
        }
        AtlasCompleteRecipeImpact impact = parse(original);
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeClient.validateCompleteImpact(
                graph(original), selection(original), search, null, impact));
        assertThrows(IllegalArgumentException.class, () -> AtlasCompleteRecipeImpact.parse(original.toString(),
                "workbench-atlas-node-v2:gt-recipe:other"));
    }

    @Test
    void refusesUnknownFieldsChangedCountsAndSuccessfulResourceFrontiers() throws IOException {
        refuses(BASE, root -> root.add("bounds", new JsonObject()));
        refuses(BASE, root -> root.getAsJsonObject("exploration").addProperty("max_nodes", 500));
        refuses(BASE, root -> root.getAsJsonObject("exploration").addProperty("graph_node_count", 1));
        refuses(BASE, root -> root.getAsJsonObject("summary").addProperty("alternative_dependency_component_count", 0));
        refuses(BASE, root -> root.getAsJsonObject("summary").addProperty("truncated", true));
        refuses(BASE, root -> {
            JsonObject frontier = new JsonObject();
            frontier.addProperty("kind", "node-bound");
            frontier.addProperty("phase", "alternative-cycle-scan");
            frontier.addProperty("omitted_at_least", 1);
            root.getAsJsonArray("frontiers").add(frontier);
        });
        refuses(BASE, root -> root.getAsJsonObject("progression_signals")
                .getAsJsonObject("energy_and_machine_signals").getAsJsonObject("observed_recipe_properties")
                .addProperty("uncontracted", true));
    }

    @Test
    void rejectsDuplicateJsonKeysAtRootAndInsideRetainedNodeProperties() throws IOException {
        String raw = resource(BASE);
        String duplicated = raw.replaceFirst("\\{", "{\"schema_version\":2,");
        assertThrows(IllegalArgumentException.class, () -> AtlasCompleteRecipeImpact.parse(duplicated, selection(fixture(BASE))));
        JsonObject root = fixture(BASE);
        String canonical = root.toString();
        assertTrue(canonical.contains("\"lookup_active\":true"));
        String nested = canonical.replaceFirst("\"lookup_active\":true", "\"lookup_active\":true,\"lookup_active\":false");
        assertThrows(IllegalArgumentException.class, () -> AtlasCompleteRecipeImpact.parse(nested, selection(root)));
        assertThrows(IllegalArgumentException.class, () -> StrictJson.validateComplete("{} {}", "fixture"));
    }

    @Test
    void validatesComponentHashSortedMembershipAndClosedRealEdgeShape() throws IOException {
        JsonObject root = fixture(BASE);
        JsonObject ownerComponent = component(root);
        assertEquals(ownerComponent.get("component_id").getAsString(),
                AtlasCompleteRecipeImpact.componentId(ownerComponent.getAsJsonArray("member_node_ids")));
        refuses(BASE, changed -> component(changed).addProperty("component_id",
                "workbench-atlas-dependency-component-v2:sha256:" + "0".repeat(64)));
        refuses(BASE, changed -> {
            JsonArray members = component(changed).getAsJsonArray("member_node_ids");
            members.add(members.get(0));
        });
        refuses(BASE, changed -> {
            JsonArray path = component(changed).getAsJsonObject("witness").getAsJsonArray("node_ids");
            path.set(path.size() - 1, path.get(1));
        });
        refuses(BASE, changed -> component(changed).getAsJsonObject("witness").getAsJsonArray("arcs")
                .get(0).getAsJsonObject().addProperty("source_id", "workbench-atlas-node-v2:forge-fluid:outside"));
        refuses(BASE, changed -> component(changed).getAsJsonObject("witness").getAsJsonArray("arcs")
                .get(0).getAsJsonObject().addProperty("edge_id", "invented-edge"));
        refuses(BASE, changed -> {
            JsonObject arc = component(changed).getAsJsonObject("witness").getAsJsonArray("arcs").get(0).getAsJsonObject();
            arc.addProperty("direction", arc.get("direction").getAsString().equals("forward") ? "reverse" : "forward");
        });
        refuses(BASE, changed -> component(changed).getAsJsonArray("root_resource_ids")
                .set(0, new JsonPrimitive("workbench-atlas-node-v2:forge-fluid:unselected")));
    }

    @Test
    void refusesRemovedRecipeMembershipAndOverlappingComponentsEvenWhenResealed() throws IOException {
        refuses(BASE, changed -> {
            JsonObject component = component(changed);
            List<String> members = new ArrayList<>();
            component.getAsJsonArray("member_node_ids").forEach(value -> members.add(value.getAsString()));
            members.add(selection(changed));
            members.sort(Comparator.naturalOrder());
            JsonArray array = new JsonArray();
            members.forEach(array::add);
            component.add("member_node_ids", array);
            component.addProperty("component_id", AtlasCompleteRecipeImpact.componentId(array));
        });
        refuses(BASE, changed -> {
            JsonObject extra = component(changed).deepCopy();
            List<String> members = new ArrayList<>();
            extra.getAsJsonArray("member_node_ids").forEach(value -> members.add(value.getAsString()));
            members.add("workbench-atlas-node-v2:zz-extra:member");
            members.sort(Comparator.naturalOrder());
            JsonArray array = new JsonArray();
            members.forEach(array::add);
            extra.add("member_node_ids", array);
            extra.addProperty("component_id", AtlasCompleteRecipeImpact.componentId(array));
            changed.getAsJsonObject("propagation").getAsJsonArray("alternative_dependency_components").add(extra);
            changed.getAsJsonObject("summary").addProperty("alternative_dependency_component_count", 2);
        });
    }

    @Test
    void evidenceIncompletenessCannotBeRenamedCompleteOrUsedAsExactConsumerAcceptance() throws IOException {
        refuses(BASE, root -> root.getAsJsonObject("evidence_completeness").addProperty("status", "complete-within-declared-model"));
        refuses(BASE, root -> root.getAsJsonObject("evidence_completeness").addProperty("graph_incomplete_selector_count", 0));
        refuses(BASE, root -> {
            JsonObject sourceInput = root.getAsJsonObject("direct").getAsJsonArray("inputs").get(0).getAsJsonObject();
            JsonObject consumer = new JsonObject();
            consumer.add("recipe", portfolio(root).getAsJsonArray("alternative_producers").get(0).getAsJsonObject().get("recipe").deepCopy());
            JsonObject selector = sourceInput.getAsJsonObject("selector").deepCopy();
            selector.getAsJsonObject("properties").addProperty("acceptance_complete", false);
            consumer.add("selector", selector);
            JsonObject edge = new JsonObject();
            for (String key : List.of("relation", "semantic_key", "properties", "evidence")) edge.add(key, sourceInput.get(key).deepCopy());
            consumer.add("acceptance_edge", edge);
            output(root).getAsJsonArray("downstream_consumers").add(consumer);
        });
    }

    @Test
    void acceptsEngineInactiveUnknownAndCanonicalQuestFixtures() throws IOException {
        for (String label : List.of("selected-inactive", "selected-unknown", "quest-cycle")) {
            String name = "atlas-complete-" + label + "-impact-v2.json";
            JsonObject root = fixture(name);
            AtlasCompleteRecipeImpact impact = parse(root);
            AtlasRecipeSearch search = search("atlas-complete-" + label + "-search-v2.json");
            assertDoesNotThrow(() -> AtlasRecipeClient.validateCompleteImpact(graph(root), selection(root),
                    search, search.recipes().getFirst(), impact));
            assertEquals(root, JsonParser.parseString(impact.rawJson()));
            if (label.equals("selected-inactive")) {
                assertEquals("no-observed-active-producer", portfolio(root).get("status").getAsString());
                assertEquals(0, root.getAsJsonObject("summary").get("sole_observed_finite_producer_output_count").getAsInt());
                refuses(name, changed -> portfolio(changed).addProperty("status", "sole-observed-finite-producer"));
            } else if (label.equals("selected-unknown")) {
                assertFalse(portfolio(root).get("evidence_complete").getAsBoolean());
                assertEquals("producer-evidence-incomplete", portfolio(root).get("status").getAsString());
                refuses(name, changed -> portfolio(changed).addProperty("evidence_complete", true));
            } else {
                JsonObject quests = root.getAsJsonObject("progression_signals").getAsJsonObject("quest_signals");
                assertEquals(1, quests.getAsJsonArray("prerequisite_cycle_components").size());
                assertTrue(quests.getAsJsonArray("structural_prerequisite_dependents").asList().stream()
                        .anyMatch(row -> row.getAsJsonObject().get("depth").getAsInt() == 0));
            }
        }
    }

    @Test
    void questWitnessUsesOnlyReversedPrerequisiteEdges() throws IOException {
        String name = "atlas-complete-quest-cycle-impact-v2.json";
        refuses(name, root -> root.getAsJsonObject("progression_signals").getAsJsonObject("quest_signals")
                .getAsJsonArray("prerequisite_cycle_components").get(0).getAsJsonObject()
                .getAsJsonObject("witness").getAsJsonArray("arcs").get(0).getAsJsonObject()
                .addProperty("relation", "unrelated-runtime-relation"));
        refuses(name, root -> root.getAsJsonObject("progression_signals").getAsJsonObject("quest_signals")
                .getAsJsonArray("prerequisite_cycle_components").get(0).getAsJsonObject()
                .getAsJsonObject("witness").getAsJsonArray("arcs").get(0).getAsJsonObject()
                .addProperty("direction", "forward"));
    }

    @Test
    void completeModeAcceptsMoreThanOldJsonValueBudgetWithoutEagerTreeExpansion() throws IOException {
        JsonObject root = fixture(BASE);
        JsonArray values = new JsonArray();
        for (int index = 0; index < 500_001; index++) values.add(index);
        root.getAsJsonObject("selection").getAsJsonObject("properties").add("retained_fixture_values", values);
        String raw = root.toString();
        assertThrows(IllegalArgumentException.class, () -> StrictJson.validate(raw, "bounded fixture", 500_000));
        AtlasCompleteRecipeImpact impact = assertDoesNotThrow(() -> AtlasCompleteRecipeImpact.parse(raw, selection(root)));
        assertEquals(500_001, impact.selection().getAsJsonObject("properties").getAsJsonArray("retained_fixture_values").size());
        DefaultMutableTreeNode tree = AtlasCompleteRecipeImpactTree.build(impact);
        assertEquals(root.size() + 2, tree.getChildCount());
        for (int index = 2; index < tree.getChildCount(); index++) {
            DefaultMutableTreeNode branch = (DefaultMutableTreeNode) tree.getChildAt(index);
            assertTrue(branch.getChildCount() <= 1, "Complete reports must remain lazy until expanded");
        }
    }

    @Test
    void completeModeRetainsMoreThanTenThousandUncertaintyRows() throws IOException {
        JsonObject root = fixture(BASE);
        JsonArray unknowns = new JsonArray();
        JsonArray encountered = new JsonArray();
        for (int index = 0; index < 10_001; index++) {
            String id = "workbench-atlas-node-v2:gt-recipe-input-selector:fixture-" + String.format("%05d", index);
            JsonObject unknown = new JsonObject();
            unknown.addProperty("code", "selector-acceptance-incomplete");
            unknown.addProperty("message", "Fixture captured alternatives are incomplete.");
            unknown.addProperty("subject_id", id);
            unknowns.add(unknown);
            encountered.add(id);
        }
        root.add("unknowns", unknowns);
        JsonObject evidence = root.getAsJsonObject("evidence_completeness");
        evidence.addProperty("graph_incomplete_selector_count", 10_001);
        evidence.add("encountered_incomplete_selector_ids", encountered);
        root.getAsJsonObject("context").getAsJsonObject("summary").addProperty("node_count", 20_020);
        root.getAsJsonObject("exploration").addProperty("graph_node_count", 20_020);
        AtlasCompleteRecipeImpact impact = assertDoesNotThrow(() -> parse(root));
        assertEquals(10_001, JsonParser.parseString(impact.rawJson()).getAsJsonObject().getAsJsonArray("unknowns").size());
        assertTrue(impact.statusText().contains("10001 unknown(s)"));
    }

    @Test
    void lazyTreePagesArraysAndObjectsWithoutDroppingRetainedValues() {
        JsonArray array = new JsonArray();
        JsonObject object = new JsonObject();
        for (int index = 0; index < 250; index++) {
            array.add(index);
            object.addProperty("field-" + index, index);
        }
        for (JsonElement value : List.of(array, object)) {
            AtlasCompleteRecipeImpactTree.LazyNode first = new AtlasCompleteRecipeImpactTree.LazyNode("retained", value, 0);
            assertEquals(1, first.getChildCount());
            first.populate();
            assertEquals(101, first.getChildCount());
            first.populate();
            assertEquals(101, first.getChildCount());
            AtlasCompleteRecipeImpactTree.LazyNode second = (AtlasCompleteRecipeImpactTree.LazyNode) first.getChildAt(100);
            assertEquals(1, second.getChildCount());
            second.populate();
            assertEquals(101, second.getChildCount());
            AtlasCompleteRecipeImpactTree.LazyNode third = (AtlasCompleteRecipeImpactTree.LazyNode) second.getChildAt(100);
            third.populate();
            assertEquals(50, third.getChildCount());
            assertTrue(third.getChildAt(49).toString().contains("249"));
        }
        assertEquals(250, array.size());
        assertEquals(250, object.size());
    }
}
