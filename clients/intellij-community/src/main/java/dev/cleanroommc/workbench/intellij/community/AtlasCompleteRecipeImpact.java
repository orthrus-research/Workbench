package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;

import static dev.cleanroommc.workbench.intellij.community.AtlasRecipeContract.*;

/** Exact V2 complete finite exploration; completion never upgrades incomplete evidence. */
final class AtlasCompleteRecipeImpact {
    static final String FORMAT = "workbench-atlas-recipe-impact-report-v2";
    private final String rawJson;
    private final JsonObject record;
    private final AtlasRecipeContext context;

    private AtlasCompleteRecipeImpact(String rawJson, JsonObject record, AtlasRecipeContext context) {
        this.rawJson = rawJson;
        this.record = record;
        this.context = context;
    }

    static AtlasCompleteRecipeImpact parse(String json, String expectedSelection) {
        AtlasRecipeImpact.requireRecipeId(expectedSelection);
        StrictJson.validateComplete(json, "complete Atlas impact");
        JsonObject root = object(JsonParser.parseString(json), "complete Atlas impact");
        keys(root, "format", "schema_version", "context", "selection", "scenario", "analysis_model",
                "exploration", "evidence_completeness", "direct", "propagation", "progression_signals",
                "frontiers", "unknowns", "evidence_gaps", "summary");
        is(root, "format", FORMAT);
        require(integer(root, "schema_version") == 2, "Unsupported complete Atlas schema");
        AtlasRecipeContext context = AtlasRecipeContext.parse(obj(root, "context"));
        JsonObject selected = node(root, "selection");
        is(selected, "selection_id", expectedSelection);
        is(selected, "kind", "gt-recipe");
        JsonObject scenario = obj(root, "scenario");
        keys(scenario, "kind", "selected_recipe_assumed_unavailable", "change_interpretation");
        is(scenario, "kind", "remove-exact-observed-recipe");
        require(bool(scenario, "selected_recipe_assumed_unavailable"), "Selected recipe must be unavailable");
        is(scenario, "change_interpretation", AtlasRecipeImpact.CHANGE_INTERPRETATION);
        JsonObject model = obj(root, "analysis_model");
        keys(model, "kind", "resource_at_risk_rule", "recipe_at_risk_rule", "claim_boundary");
        is(model, "kind", "complete-observed-finite-recipe-dependency-exposure");
        is(model, "resource_at_risk_rule", "every observed finite recipe producer is an unavailable candidate");
        is(model, "recipe_at_risk_rule", "at least one exact input selector accepts only resources already classified at risk");
        is(model, "claim_boundary", AtlasRecipeImpact.CLAIM_BOUNDARY);
        JsonObject exploration = obj(root, "exploration");
        keys(exploration, "mode", "status", "graph_node_count", "graph_edge_count", "visited_node_count",
                "traversed_edge_count", "worklist_event_count", "cycle_algorithm");
        is(exploration, "mode", "complete-finite");
        is(exploration, "status", "complete");
        is(exploration, "cycle_algorithm", "iterative-scc");
        int nodes = nonnegativeInteger(exploration, "graph_node_count");
        int edges = nonnegativeInteger(exploration, "graph_edge_count");
        JsonObject graphSummary = obj(context.value(), "summary");
        require(nodes == nonnegativeInteger(graphSummary, "node_count")
                && edges == nonnegativeInteger(graphSummary, "edge_count"), "Complete exploration changed graph counts");
        int visited = nonnegativeInteger(exploration, "visited_node_count");
        require(visited >= 1 && visited <= nodes, "Complete exploration node count is invalid");
        require(nonnegativeInteger(exploration, "traversed_edge_count") <= edges, "Complete exploration edge count is invalid");
        nonnegativeInteger(exploration, "worklist_event_count");
        JsonObject evidence = obj(root, "evidence_completeness");
        keys(evidence, "status", "graph_incomplete_selector_count", "graph_unknown_lookup_recipe_count",
                "encountered_incomplete_selector_ids", "encountered_unknown_lookup_recipe_ids");
        int incomplete = nonnegativeInteger(evidence, "graph_incomplete_selector_count");
        int unknown = nonnegativeInteger(evidence, "graph_unknown_lookup_recipe_count");
        require(incomplete <= nodes && unknown <= nodes, "Evidence counts exceed the graph");
        List<String> selectors = orderedIds(array(evidence, "encountered_incomplete_selector_ids"));
        List<String> recipes = orderedIds(array(evidence, "encountered_unknown_lookup_recipe_ids"));
        require(selectors.size() <= incomplete && recipes.size() <= unknown, "Encountered evidence exceeds graph counts");
        boolean evidenceComplete = incomplete == 0 && unknown == 0;
        is(evidence, "status", evidenceComplete ? "complete-within-declared-model" : "incomplete");
        require(array(root, "frontiers").isEmpty(), "Complete exploration cannot contain omitted-work frontiers");
        AtlasRecipeImpact.validateUnknowns(array(root, "unknowns"));
        AtlasRecipeImpact.validateEvidenceGaps(array(root, "evidence_gaps"));
        JsonObject direct = obj(root, "direct");
        keys(direct, "inputs", "outputs");
        AtlasRecipeImpact.validateDirectInputs(array(direct, "inputs"));
        int sole = outputs(array(direct, "outputs"), selected);
        Set<String> outputIds = new HashSet<>();
        for (JsonElement value : array(direct, "outputs")) {
            outputIds.add(string(node(obj(object(value, "output"), "output"), "node"), "selection_id"));
        }
        JsonObject propagation = obj(root, "propagation");
        keys(propagation, "status", "at_risk_resources", "at_risk_recipes", "alternative_dependency_components");
        is(propagation, "status", evidenceComplete ? "complete-within-model" : "evidence-incomplete");
        candidates(array(propagation, "at_risk_resources"), false, expectedSelection);
        candidates(array(propagation, "at_risk_recipes"), true, expectedSelection);
        components(array(propagation, "alternative_dependency_components"), outputIds, expectedSelection, false);
        JsonObject progression = obj(root, "progression_signals");
        keys(progression, "energy_and_machine_signals", "quest_signals");
        energy(obj(progression, "energy_and_machine_signals"));
        JsonObject quests = obj(progression, "quest_signals");
        keys(quests, "status", "direct_resource_requirements", "structural_prerequisite_dependents",
                "prerequisite_cycle_components", "interpretation");
        require(Set.of("observed-definition-references", "no-observed-reference", "evidence-unavailable",
                "evidence-incomplete").contains(string(quests, "status")), "Unknown complete quest status");
        is(quests, "interpretation", "these are definition references and prerequisite structure, not observed player blockage or task execution");
        AtlasRecipeImpact.validateQuestRequirements(array(quests, "direct_resource_requirements"), Integer.MAX_VALUE);
        AtlasRecipeImpact.validateQuestDependents(array(quests, "structural_prerequisite_dependents"), Integer.MAX_VALUE);
        components(array(quests, "prerequisite_cycle_components"), Set.of(), expectedSelection, true);
        JsonObject summary = obj(root, "summary");
        keys(summary, "selected_output_count", "sole_observed_finite_producer_output_count",
                "at_risk_resource_candidate_count", "at_risk_recipe_candidate_count", "quest_requirement_exposure_count",
                "structural_quest_dependent_count", "alternative_dependency_component_count",
                "quest_prerequisite_component_count", "truncated");
        count(summary, "selected_output_count", array(direct, "outputs").size());
        count(summary, "sole_observed_finite_producer_output_count", sole);
        count(summary, "at_risk_resource_candidate_count", array(propagation, "at_risk_resources").size());
        count(summary, "at_risk_recipe_candidate_count", array(propagation, "at_risk_recipes").size());
        count(summary, "quest_requirement_exposure_count", array(quests, "direct_resource_requirements").size());
        count(summary, "structural_quest_dependent_count", array(quests, "structural_prerequisite_dependents").size());
        count(summary, "alternative_dependency_component_count", array(propagation, "alternative_dependency_components").size());
        count(summary, "quest_prerequisite_component_count", array(quests, "prerequisite_cycle_components").size());
        require(!bool(summary, "truncated"), "Complete exploration cannot be resource truncated");
        return new AtlasCompleteRecipeImpact(json, root, context);
    }

    private static int outputs(JsonArray values, JsonObject selection) {
        String selected = string(selection, "selection_id");
        JsonElement activity = obj(selection, "properties").get("lookup_active");
        Boolean selectedActive = activity != null && activity.isJsonPrimitive()
                && activity.getAsJsonPrimitive().isBoolean() ? activity.getAsBoolean() : null;
        int sole = 0;
        for (JsonElement element : values) {
            JsonObject row = object(element, "direct output");
            keys(row, "output", "producer_portfolio", "downstream_consumers", "downstream_consumers_truncated",
                    "downstream_consumers_evidence_complete");
            AtlasRecipeImpact.validateEdgeSummary(required(row, "output"), "output edge", true);
            require(!bool(row, "downstream_consumers_truncated"), "Complete consumer list cannot be truncated");
            bool(row, "downstream_consumers_evidence_complete");
            for (JsonElement value : array(row, "downstream_consumers")) {
                JsonObject consumer = object(value, "consumer");
                keys(consumer, "recipe", "selector", "acceptance_edge");
                activeRecipe(node(consumer, "recipe"));
                JsonObject selector = node(consumer, "selector");
                JsonObject selectorProperties = obj(selector, "properties");
                require(!selectorProperties.has("acceptance_complete") || bool(selectorProperties, "acceptance_complete"),
                        "Incomplete matching cannot establish an exact consumer");
                AtlasRecipeImpact.validateEdgeSummary(required(consumer, "acceptance_edge"), "acceptance edge", false);
            }
            JsonObject portfolio = obj(row, "producer_portfolio");
            keys(portfolio, "status", "alternative_producers", "truncated", "evidence_complete", "viability");
            require(!bool(portfolio, "truncated"), "Complete producer list cannot be truncated");
            is(portfolio, "viability", "not-assessed");
            JsonArray alternatives = array(portfolio, "alternative_producers");
            require(selectedActive != null || !bool(portfolio, "evidence_complete"),
                    "Unknown selected producer activity cannot establish complete supply evidence");
            String status = bool(portfolio, "evidence_complete")
                    ? (alternatives.isEmpty() ? (Boolean.TRUE.equals(selectedActive)
                        ? "sole-observed-finite-producer" : "no-observed-active-producer") : "observed-alternatives-present")
                    : "producer-evidence-incomplete";
            is(portfolio, "status", status);
            if (status.equals("sole-observed-finite-producer")) sole++;
            for (JsonElement value : alternatives) {
                JsonObject alternative = object(value, "alternative");
                keys(alternative, "recipe", "output_edge");
                JsonObject recipe = node(alternative, "recipe");
                activeRecipe(recipe);
                require(!string(recipe, "selection_id").equals(selected), "Removed recipe reappeared as an alternative");
                AtlasRecipeImpact.validateEdgeSummary(required(alternative, "output_edge"), "alternative edge", false);
            }
        }
        return sole;
    }

    private static void candidates(JsonArray rows, boolean recipe, String selected) {
        Set<String> identities = new HashSet<>();
        for (JsonElement value : rows) {
            JsonObject row = object(value, "candidate");
            keys(row, recipe ? new String[]{"recipe", "depth", "reason", "blocked_selectors"}
                    : new String[]{"resource", "depth", "reason", "all_observed_finite_producers"});
            JsonObject node = node(row, recipe ? "recipe" : "resource");
            require(identities.add(string(node, "selection_id")), "Duplicate exposed candidate");
            nonnegativeInteger(row, "depth");
            is(row, "reason", recipe ? "at-least-one-selector-has-only-at-risk-observed-alternatives"
                    : "all-observed-finite-producers-are-unavailable-candidates");
            if (recipe) {
                activeRecipe(node);
                require(!string(node, "selection_id").equals(selected), "Removed recipe is not downstream exposure");
                JsonArray blocked = array(row, "blocked_selectors");
                require(!blocked.isEmpty(), "An exposed recipe needs a blocked selector");
                for (JsonElement block : blocked) {
                    JsonObject item = object(block, "blocked selector");
                    keys(item, "selector", "accepted_resources");
                    JsonObject selector = node(item, "selector");
                    JsonObject props = obj(selector, "properties");
                    require(!props.has("acceptance_complete") || bool(props, "acceptance_complete"), "Incomplete matching cannot establish blockage");
                    JsonArray resources = array(item, "accepted_resources");
                    require(!resources.isEmpty(), "An empty selector cannot establish blockage");
                    for (JsonElement resource : resources) AtlasRecipeImpact.validateNodeSummary(resource, "accepted resource");
                }
            } else {
                List<String> producers = orderedIds(array(row, "all_observed_finite_producers"));
                require(!producers.isEmpty(), "A resource candidate needs observed producers");
                producers.forEach(AtlasRecipeImpact::requireRecipeId);
            }
        }
    }

    private static void energy(JsonObject energy) {
        keys(energy, "observed_recipe_properties", "recipe_map", "machines_observed_using_recipe_map",
                "machine_numeric_tiers_observed", "truncated", "evidence_complete", "interpretation");
        keys(obj(energy, "observed_recipe_properties"), "duration", "eut", "hidden", "lookup_active", "recipe_map");
        if (!required(energy, "recipe_map").isJsonNull()) node(energy, "recipe_map");
        for (JsonElement machine : array(energy, "machines_observed_using_recipe_map")) {
            JsonObject row = object(machine, "machine binding");
            keys(row, "machine", "map_edge");
            node(row, "machine");
            AtlasRecipeImpact.validateEdgeSummary(required(row, "map_edge"), "map edge", false);
        }
        for (JsonElement tier : array(energy, "machine_numeric_tiers_observed")) {
            require(tier.isJsonPrimitive() && tier.getAsJsonPrimitive().isNumber(), "Machine tier must be numeric");
            try { tier.getAsBigDecimal().intValueExact(); }
            catch (ArithmeticException error) { throw new IllegalArgumentException("Machine tier must be an integer", error); }
        }
        require(!bool(energy, "truncated"), "Complete machine bindings cannot be truncated");
        bool(energy, "evidence_complete");
        is(energy, "interpretation", "numeric EUT and machine tier properties are observed signals; no pack progression tier is inferred");
    }

    private static void components(JsonArray rows, Set<String> roots, String selected, boolean quest) {
        Set<String> componentIds = new HashSet<>();
        Set<String> allMembers = new HashSet<>();
        for (JsonElement value : rows) {
            JsonObject component = object(value, "dependency component");
            keys(component, "component_id", "member_node_ids", "root_resource_ids", "witness", "interpretation", "viability_effect");
            JsonArray memberArray = array(component, "member_node_ids");
            List<String> members = orderedIds(memberArray);
            require(!members.isEmpty() && !members.contains(selected), "Invalid cyclic component membership");
            for (String member : members) require(allMembers.add(member), "Cyclic components overlap");
            String id = string(component, "component_id");
            require(componentIds.add(id), "Duplicate component");
            equal(id, componentId(memberArray), "Component identity differs from its members");
            List<String> componentRoots = orderedIds(array(component, "root_resource_ids"));
            require(roots.containsAll(componentRoots) && (quest ? componentRoots.isEmpty() : !componentRoots.isEmpty()), "Invalid component root resources");
            is(component, "viability_effect", "unknown");
            string(component, "interpretation");
            JsonObject witness = obj(component, "witness");
            keys(witness, "node_ids", "arcs");
            JsonArray path = array(witness, "node_ids");
            JsonArray arcs = array(witness, "arcs");
            require(path.size() >= 2 && arcs.size() == path.size() - 1, "Cycle witness has invalid length");
            require(path.get(0).equals(path.get(path.size() - 1)), "Cycle witness is not closed");
            Set<String> memberSet = new HashSet<>(members);
            for (int index = 0; index < path.size(); index++) {
                String node = string(path.get(index), "witness node", false, MAX_SMALL_TEXT_BYTES);
                require(memberSet.contains(node), "Cycle witness leaves its component");
                if (index == arcs.size()) continue;
                JsonObject arc = object(arcs.get(index), "witness arc");
                keys(arc, "source_id", "target_id", "edge_id", "relation", "direction");
                is(arc, "source_id", node);
                is(arc, "target_id", path.get(index + 1).getAsString());
                require(string(arc, "edge_id").matches("workbench-atlas-edge-v2:sha256:[0-9a-f]{64}"), "Noncanonical witness edge identity");
                String relation = string(arc, "relation");
                String direction = string(arc, "direction");
                require(Set.of("forward", "reverse").contains(direction), "Invalid edge direction");
                if (quest) {
                    require(Set.of("targets-progression-prerequisite", "owns-progression-prerequisite").contains(relation),
                            "Unsupported quest dependency relation");
                    equal(direction, "reverse", "Quest dependency edge direction changed");
                } else {
                    require(Set.of("produces-gt-item", "produces-gt-fluid", "has-item-input-selector",
                            "has-fluid-input-selector", "accepts-gt-item-alternative", "accepts-ore-dictionary-class",
                            "accepts-gt-fluid-input").contains(relation), "Unsupported finite dependency relation");
                    equal(direction, relation.startsWith("produces-") ? "reverse" : "forward", "Dependency edge direction changed");
                }
            }
        }
    }

    static String componentId(JsonArray members) {
        try {
            return "workbench-atlas-dependency-component-v2:sha256:" + HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(members.toString().getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException error) { throw new IllegalStateException(error); }
    }
    private static void activeRecipe(JsonObject node) {
        is(node, "kind", "gt-recipe");
        AtlasRecipeImpact.requireRecipeId(string(node, "selection_id"));
        require(bool(obj(node, "properties"), "lookup_active"), "Only lookup-active recipes may be claimed here");
    }
    private static List<String> orderedIds(JsonArray values) {
        List<String> ids = new ArrayList<>();
        String previous = null;
        for (JsonElement value : values) {
            String id = string(value, "node identity", false, MAX_SMALL_TEXT_BYTES);
            require(id.startsWith("workbench-atlas-node-v2:"), "Noncanonical node identity");
            require(previous == null || previous.compareTo(id) < 0, "Identities must be unique and sorted");
            ids.add(id); previous = id;
        }
        return ids;
    }
    private static JsonObject obj(JsonObject value, String key) { return object(required(value, key), key); }
    private static JsonObject node(JsonObject value, String key) {
        JsonObject node = obj(value, key);
        AtlasRecipeImpact.validateNodeSummary(node, key);
        return node;
    }
    private static void keys(JsonObject value, String... keys) { exactKeys(value, Set.of(keys), "complete Atlas record"); }
    private static void is(JsonObject value, String key, String expected) { equal(string(value, key), expected, "Complete Atlas " + key + " changed"); }
    private static void count(JsonObject value, String key, int expected) { require(nonnegativeInteger(value, key) == expected, "Complete Atlas " + key + " count differs"); }
    @NotNull String rawJson() { return rawJson; }
    @NotNull AtlasRecipeContext context() { return context; }
    @NotNull JsonObject selection() { return obj(record, "selection").deepCopy(); }
    @NotNull String selectionId() { return string(obj(record, "selection"), "selection_id"); }
    @NotNull JsonObject value() { return record; }
    @NotNull String statusText() {
        return "Observed finite exploration complete · evidence " + string(obj(record, "evidence_completeness"), "status")
                + " · " + array(record, "unknowns").size() + " unknown(s) · "
                + nonnegativeInteger(obj(record, "summary"), "alternative_dependency_component_count")
                + " dependency component(s) · viability unknown";
    }
}
