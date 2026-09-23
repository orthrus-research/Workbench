package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import java.util.HashSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import static dev.cleanroommc.workbench.intellij.community.AtlasRecipeContract.*;

/** Exact graph and edge linkage for a single neighborhood page. */
final class AtlasRecipeBrowse {
    private AtlasRecipeBrowse() { }

    static String nodeLabel(JsonObject row) {
        var properties = row.getAsJsonObject("properties");
        String kind = string(row, "kind"), semantic = string(row, "semantic_key");
        if (kind.equals("item-variant") && properties.has("observed_item_names") && properties.get("observed_item_names").isJsonArray()) {
            var names = new TreeSet<String>();
            for (var value : properties.getAsJsonArray("observed_item_names")) {
                if (value.isJsonObject()) {
                    var name = value.getAsJsonObject().get("name");
                    if (name != null && name.isJsonPrimitive() && name.getAsJsonPrimitive().isString() && !name.getAsString().isEmpty()) names.add(name.getAsString());
                }
            }
            if (!names.isEmpty()) return names.first() + (names.size() > 1 ? " (+" + (names.size() - 1) + " names)" : "");
        }
        var parts = new ArrayList<String>();
        if (kind.equals("gt-recipe")) {
            var map = properties.get("recipe_map");
            parts.add(map != null && map.isJsonPrimitive() && map.getAsJsonPrimitive().isString() ? map.getAsString() : semantic);
            numberLabel(parts, properties, "duration", "", " ticks");
            numberLabel(parts, properties, "eut", "", " EU/t");
            return String.join(" · ", parts);
        }
        if (kind.equals("gt-recipe-input-selector")) {
            var ordinal = properties.get("ordinal");
            parts.add("Input" + (ordinal != null && ordinal.isJsonPrimitive() && ordinal.getAsJsonPrimitive().isNumber()
                    ? " " + (ordinal.getAsLong() + 1) : ""));
            numberLabel(parts, properties, "amount", "amount ", "");
            if (isBoolean(properties, "non_consumable", true)) parts.add("reusable");
            if (isBoolean(properties, "acceptance_complete", false)) parts.add("matching incomplete");
            return String.join(" · ", parts);
        }
        return semantic;
    }

    private static void numberLabel(List<String> parts, JsonObject properties, String key, String prefix, String suffix) {
        var value = properties.get(key);
        if (value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isNumber()) parts.add(prefix + value.getAsString() + suffix);
    }

    private static boolean isBoolean(JsonObject properties, String key, boolean expected) {
        var value = properties.get(key);
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isBoolean() && value.getAsBoolean() == expected;
    }

    static String relationshipLabel(JsonObject row) {
        var edge = row.getAsJsonObject("relationship");
        boolean outgoing = string(row, "direction").equals("outgoing");
        String label = switch (string(edge, "relation")) {
            case "produces-gt-item" -> outgoing ? "Produces item" : "Produced by recipe";
            case "produces-gt-fluid" -> outgoing ? "Produces fluid" : "Produced by recipe";
            case "has-item-input-selector" -> outgoing ? "Item input" : "Input to recipe";
            case "has-fluid-input-selector" -> outgoing ? "Fluid input" : "Input to recipe";
            case "accepts-gt-item-alternative" -> outgoing ? "Accepted item alternative" : "Accepted by input";
            case "accepts-gt-fluid-input" -> outgoing ? "Accepted fluid" : "Accepted by input";
            case "contained-in-recipe-map" -> outgoing ? "Recipe map" : "Contains recipe";
            default -> string(row, "direction") + " · " + string(edge, "relation");
        };
        var parts = new ArrayList<>(List.of(label));
        var properties = edge.getAsJsonObject("properties");
        numberLabel(parts, properties, "amount", "amount ", "");
        if (isBoolean(properties, "non_consumable", true)) parts.add("reusable");
        if (isBoolean(properties, "chanced", true)) parts.add("chance output; inspect values");
        return String.join(" · ", parts);
    }

    record Choice(String label, String action, JsonObject node) { }

    static List<Choice> choices(JsonObject page, Set<String> path, boolean canGoBack) {
        var choices = new ArrayList<Choice>();
        choices.add(new Choice("Inspect captured values and evidence", "values", null));
        if (canGoBack) choices.add(new Choice("Back to previous selection", "back", null));
        var paging = page.getAsJsonObject("page");
        if (integer(paging, "offset") > 0) choices.add(new Choice("Previous relationship page", "previous", null));
        if (!paging.get("next_offset").isJsonNull()) choices.add(new Choice("Next relationship page", "next", null));
        for (var element : array(page, "links")) {
            var row = element.getAsJsonObject(); var peer = row.getAsJsonObject("node");
            choices.add(new Choice(nodeLabel(peer) + " [" + relationshipLabel(row) + "] · " + string(peer, "semantic_key")
                    + (path.contains(string(peer, "selection_id")) ? " · already on path (cycle/reference)" : ""), "node", peer));
        }
        return choices;
    }

    static JsonObject node(JsonObject value) {
        exactKeys(value, Set.of("selection_id", "kind", "semantic_key", "properties", "evidence"), "browse node");
        string(value, "selection_id"); string(value, "kind"); string(value, "semantic_key");
        object(required(value, "properties"), "node properties"); array(value, "evidence");
        return value;
    }

    static JsonObject parse(String json, String root, String graph, String selection, int offset) {
        JsonObject value = parseRoot(json, "recipe browse");
        exactKeys(value, Set.of("format", "schema_version", "context", "selection", "page", "links", "evidence_gaps", "scope"), "browse page");
        equal(string(value, "format"), "workbench-atlas-recipe-browse-v1", "Unsupported browse format");
        require(integer(value, "schema_version") == 1, "Unsupported browse schema");
        AtlasRecipeContext context = AtlasRecipeContext.parse(object(required(value, "context"), "context"));
        require(AtlasRecipeContext.sameRoot(context.root(), root), "Atlas changed the graph root");
        equal(context.graphSetId(), graph, "Atlas graph changed; search again");
        equal(string(node(object(required(value, "selection"), "selection")), "selection_id"), selection, "Atlas changed selection");
        JsonObject page = object(required(value, "page"), "page");
        exactKeys(page, Set.of("offset", "limit", "total", "next_offset"), "page");
        int total = nonnegativeInteger(page, "total");
        require(integer(page, "offset") == offset && integer(page, "limit") == 50, "Atlas changed paging");
        var links = array(value, "links");
        require(links.size() == Math.max(0, Math.min(50, total - offset)), "Atlas relationship count changed");
        int end = offset + links.size();
        require(end < total ? integer(page, "next_offset") == end : required(page, "next_offset").isJsonNull(), "Atlas next page changed");
        var seen = new HashSet<String>();
        for (var element : links) {
            JsonObject row = object(element, "link");
            exactKeys(row, Set.of("direction", "relationship", "node"), "link");
            String direction = string(row, "direction");
            require(Set.of("incoming", "outgoing").contains(direction), "Atlas direction changed");
            JsonObject peer = node(object(required(row, "node"), "linked node"));
            JsonObject edge = object(required(row, "relationship"), "relationship");
            string(edge, "relation"); object(required(edge, "properties"), "edge properties"); array(edge, "evidence");
            equal(string(edge, direction.equals("outgoing") ? "source" : "target"), selection, "Atlas relationship source changed");
            equal(string(edge, direction.equals("outgoing") ? "target" : "source"), string(peer, "selection_id"), "Atlas linked node changed");
            require(seen.add(direction + ":" + string(edge, "id")), "Duplicate Atlas relationship");
        }
        string(value, "scope"); array(value, "evidence_gaps");
        return value;
    }
}
