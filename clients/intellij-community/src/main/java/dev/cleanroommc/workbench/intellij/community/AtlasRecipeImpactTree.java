package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;

import javax.swing.tree.DefaultMutableTreeNode;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Native candidate/exposure tree with the Atlas V1 claim boundary kept visible. */
final class AtlasRecipeImpactTree {
    private static final int MAX_VISIBLE_VALUES = 8_000;

    private AtlasRecipeImpactTree() {
    }

    static @NotNull DefaultMutableTreeNode build(@NotNull AtlasRecipeImpact impact) {
        DefaultMutableTreeNode root = node(
                "Recipe impact candidates · " + impact.semanticKey()
        );
        DefaultMutableTreeNode boundary = node("Claim boundary (always visible)");
        boundary.add(node(statusText(impact)));
        boundary.add(node("Observed alternatives and cycle signals do not establish viability"));
        boundary.add(node(AtlasRecipeImpact.CLAIM_BOUNDARY));
        boundary.add(node("Scenario removes only this exact observed recipe: "
                + impact.selectionId()));
        boundary.add(node(
                "Proposed replacement inputs, outputs, quantities, and runtime are not modeled"
        ));
        root.add(boundary);

        DefaultMutableTreeNode context = node("Verified graph and exact selection");
        context.add(node("Graph: " + impact.context().root()));
        context.add(node("Graph set: " + impact.context().graphSetId()));
        context.add(node("Observed recipes: " + impact.context().recipeCount()));
        addJson(context, "Selection", impact.selection(), 0, new int[]{MAX_VISIBLE_VALUES});
        root.add(context);

        addSection(root, "Summary", impact.summary());
        addSection(root, "Direct", impact.direct());
        addSection(root, "Propagation candidates", impact.propagation());
        addSection(root, "Progression signals", impact.progressionSignals());
        addSection(root, "Frontiers", impact.frontiers());
        addSection(root, "Unknowns", impact.unknowns());
        addEvidenceGaps(root, impact.evidenceGaps());
        return root;
    }

    static @NotNull String statusText(@NotNull AtlasRecipeImpact impact) {
        int frontiers = impact.frontiers().size();
        int unknowns = impact.unknowns().size();
        int cycles = impact.propagation().getAsJsonArray("alternative_dependency_cycle_signals").size();
        Set<String> inactive = new HashSet<>();
        for (JsonElement output : impact.direct().getAsJsonArray("outputs")) {
            for (JsonElement alternative : output.getAsJsonObject().getAsJsonObject("producer_portfolio").getAsJsonArray("alternative_producers")) {
                JsonObject recipe = alternative.getAsJsonObject().getAsJsonObject("recipe");
                JsonElement active = recipe.getAsJsonObject("properties").get("lookup_active");
                if (active != null && active.isJsonPrimitive()
                        && active.getAsJsonPrimitive().isBoolean() && !active.getAsBoolean()) {
                    inactive.add(recipe.get("selection_id").getAsString());
                }
            }
        }
        String state = frontiers > 0 ? "Bounded frontiers remain"
                : unknowns > 0 || cycles > 0 || !inactive.isEmpty()
                ? "Unresolved within bounds" : "Bounded analysis complete";
        return state + " · " + frontiers + " frontier(s) · " + unknowns + " unknown(s) · "
                + cycles + " alternative dependency cycle signal(s) · " + inactive.size()
                + " lookup-inactive alternative recipe(s)";
    }

    private static void addEvidenceGaps(
            @NotNull DefaultMutableTreeNode root,
            @NotNull JsonArray gaps
    ) {
        DefaultMutableTreeNode section = node("Evidence gaps (" + gaps.size() + ")");
        for (JsonElement element : gaps) {
            JsonObject gap = element.getAsJsonObject();
            String code = gap.get("code").getAsString();
            String message = code.equals("progression-reachability-not-proven")
                    ? "Observed definitions, finite recipes, and machine signals do not "
                            + "establish gameplay reachability."
                    : gap.get("message").getAsString();
            section.add(node(code + ": " + message));
        }
        root.add(section);
    }

    private static void addSection(
            @NotNull DefaultMutableTreeNode root,
            @NotNull String label,
            @NotNull JsonElement value
    ) {
        DefaultMutableTreeNode section = node(
                value.isJsonArray() ? label + " (" + value.getAsJsonArray().size() + ")" : label
        );
        if (value.isJsonObject()) {
            addObjectMembers(section, value.getAsJsonObject(), 0,
                    new int[]{MAX_VISIBLE_VALUES});
        } else if (value.isJsonArray()) {
            JsonArray array = value.getAsJsonArray();
            if (array.isEmpty()) {
                section.add(node("None observed within the selected bounds"));
            } else {
                for (int index = 0; index < array.size(); index++) {
                    addJson(section, "[" + index + "]", array.get(index), 0,
                            new int[]{MAX_VISIBLE_VALUES});
                }
            }
        }
        root.add(section);
    }

    private static void addObjectMembers(
            @NotNull DefaultMutableTreeNode parent,
            @NotNull JsonObject object,
            int depth,
            int @NotNull [] budget
    ) {
        if (object.isEmpty()) {
            parent.add(node("{}"));
            return;
        }
        for (Map.Entry<String, JsonElement> entry : object.entrySet()) {
            String label = switch (entry.getKey()) {
                case "at_risk_resources" -> "exposed resource candidates";
                case "at_risk_recipes" -> "exposed recipe candidates";
                case "at_risk_resource_candidate_count" -> "exposed resource candidate count";
                case "at_risk_recipe_candidate_count" -> "exposed recipe candidate count";
                default -> entry.getKey().replace('_', ' ');
            };
            addJson(parent, label, entry.getValue(), depth, budget);
        }
    }

    private static void addJson(
            @NotNull DefaultMutableTreeNode parent,
            @NotNull String label,
            @NotNull JsonElement value,
            int depth,
            int @NotNull [] budget
    ) {
        if (--budget[0] < 0) {
            if (budget[0] == -1) {
                parent.add(node(
                        "… remaining exact values are available in Complete Atlas JSON"
                ));
            }
            return;
        }
        if (depth >= 40) {
            parent.add(node(label + ": (nested value; open Complete Atlas JSON)"));
            return;
        }
        if (value.isJsonObject()) {
            DefaultMutableTreeNode child = node(label);
            addObjectMembers(child, value.getAsJsonObject(), depth + 1, budget);
            parent.add(child);
            return;
        }
        if (value.isJsonArray()) {
            JsonArray array = value.getAsJsonArray();
            DefaultMutableTreeNode child = node(label + " (" + array.size() + ")");
            if (array.isEmpty()) {
                child.add(node("None observed within the selected bounds"));
            } else {
                for (int index = 0; index < array.size(); index++) {
                    addJson(child, "[" + index + "]", array.get(index), depth + 1, budget);
                }
            }
            parent.add(child);
            return;
        }
        if (value.isJsonNull()) {
            parent.add(node(label + ": null"));
            return;
        }
        String rendered = value.getAsJsonPrimitive().isString()
                ? value.getAsString() : value.toString();
        if (rendered.length() > 700) {
            rendered = rendered.substring(0, 700)
                    + "… (open Complete Atlas JSON for the exact value)";
        }
        parent.add(node(label + ": " + rendered));
    }

    private static @NotNull DefaultMutableTreeNode node(@NotNull String value) {
        return new DefaultMutableTreeNode(value);
    }
}
