package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Strict, bounded V1 Atlas projection for one exact observed recipe removal. */
final class AtlasRecipeImpact {
    static final String FORMAT = "workbench-atlas-recipe-impact-report-v1";
    static final String CLAIM_BOUNDARY =
            "candidate dead paths inside observed finite recipe structure; not gameplay reachability";
    static final String CHANGE_INTERPRETATION =
            "a recipe change is assessed only as loss of the selected current recipe; "
                    + "replacement inputs, outputs, and runtime behavior require a new observed "
                    + "graph or a separately validated proposed-recipe model";

    // Omitted edge/phase observations can outnumber admitted traversal nodes.
    private static final int MAX_UNCERTAINTY_ROWS = 10_000;

    private static final List<Gap> REQUIRED_GAPS = List.of(
            new Gap(
                    "counterfactual-runtime-not-observed",
                    "Atlas did not execute a runtime with the selected recipe removed or changed."
            ),
            new Gap(
                    "non-recipe-acquisition-not-assessed",
                    "World generation, loot, trade, inventory, commands, and other acquisition "
                            + "domains are not treated as alternative producers."
            ),
            new Gap(
                    "dynamic-recipes-not-executed",
                    "Procedural and contextual recipe rules were not invoked for this counterfactual."
            ),
            new Gap(
                    "stoichiometry-and-chance-not-assessed",
                    "Amounts, reusable inputs, probabilities, throughput, and inventory balance "
                            + "do not establish or refute availability here."
            ),
            new Gap(
                    "progression-reachability-not-proven",
                    "Quest definitions, finite recipes, and machine signals do not prove player "
                            + "reachability or a broken progression path."
            ),
            new Gap(
                    "task-execution-not-invoked",
                    "BetterQuesting task matching, completion, and reward execution were not invoked."
            ),
            new Gap(
                    "pack-tier-policy-not-applied",
                    "Numeric EUT and observed machine tier fields are reported without deriving a "
                            + "pack progression tier."
            )
    );

    private final String rawJson;
    private final AtlasRecipeContext context;
    private final JsonObject selection;
    private final String selectionId;
    private final String semanticKey;
    private final JsonObject scenario;
    private final JsonObject analysisModel;
    private final JsonObject bounds;
    private final JsonObject direct;
    private final JsonObject propagation;
    private final JsonObject progressionSignals;
    private final JsonArray frontiers;
    private final JsonArray unknowns;
    private final JsonArray evidenceGaps;
    private final JsonObject summary;

    private AtlasRecipeImpact(
            @NotNull String rawJson,
            @NotNull AtlasRecipeContext context,
            @NotNull JsonObject selection,
            @NotNull String selectionId,
            @NotNull String semanticKey,
            @NotNull JsonObject scenario,
            @NotNull JsonObject analysisModel,
            @NotNull JsonObject bounds,
            @NotNull JsonObject direct,
            @NotNull JsonObject propagation,
            @NotNull JsonObject progressionSignals,
            @NotNull JsonArray frontiers,
            @NotNull JsonArray unknowns,
            @NotNull JsonArray evidenceGaps,
            @NotNull JsonObject summary
    ) {
        this.rawJson = rawJson;
        this.context = context;
        this.selection = selection.deepCopy();
        this.selectionId = selectionId;
        this.semanticKey = semanticKey;
        this.scenario = scenario.deepCopy();
        this.analysisModel = analysisModel.deepCopy();
        this.bounds = bounds.deepCopy();
        this.direct = direct.deepCopy();
        this.propagation = propagation.deepCopy();
        this.progressionSignals = progressionSignals.deepCopy();
        this.frontiers = frontiers.deepCopy();
        this.unknowns = unknowns.deepCopy();
        this.evidenceGaps = evidenceGaps.deepCopy();
        this.summary = summary.deepCopy();
    }

    static @NotNull AtlasRecipeImpact parse(
            @NotNull String json,
            @NotNull String expectedSelectionId,
            int expectedMaxDepth,
            int expectedMaxNodes
    ) {
        AtlasRecipeContract.require(expectedMaxDepth >= 1 && expectedMaxDepth <= 12,
                "Atlas impact depth is outside 1..12");
        AtlasRecipeContract.require(expectedMaxNodes >= 10 && expectedMaxNodes <= 2_000,
                "Atlas impact node bound is outside 10..2000");
        requireRecipeId(expectedSelectionId);

        JsonObject root = AtlasRecipeContract.parseRoot(json, "Atlas recipe impact");
        AtlasRecipeContract.exactKeys(root, Set.of(
                "analysis_model", "bounds", "context", "direct", "evidence_gaps",
                "format", "frontiers", "progression_signals", "propagation", "scenario",
                "schema_version", "selection", "summary", "unknowns"
        ), "Atlas recipe impact");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(root, "format"), FORMAT,
                "Workbench returned an unsupported Atlas recipe impact report"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.integer(root, "schema_version") == 1,
                "Workbench returned an unsupported Atlas recipe impact schema"
        );

        AtlasRecipeContext context = AtlasRecipeContext.parse(object(root, "context"));
        JsonObject selection = object(root, "selection");
        AtlasRecipeContract.exactKeys(selection, Set.of(
                "evidence", "kind", "properties", "selection_id", "semantic_key"
        ), "Atlas impact selection");
        String selectionId = AtlasRecipeContract.string(selection, "selection_id");
        requireRecipeId(selectionId);
        AtlasRecipeContract.equal(selectionId, expectedSelectionId,
                "Workbench Atlas impact changed the exact recipe selection");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(selection, "kind"), "gt-recipe",
                "Atlas impact selection is not one exact observed gt-recipe"
        );
        String semanticKey = AtlasRecipeContract.string(selection, "semantic_key");
        AtlasRecipeContract.object(
                AtlasRecipeContract.required(selection, "properties"),
                "Atlas impact selection properties"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.required(selection, "evidence").isJsonArray(),
                "Atlas impact selection evidence must be an array"
        );

        JsonObject scenario = object(root, "scenario");
        AtlasRecipeContract.exactKeys(scenario, Set.of(
                "change_interpretation", "kind", "selected_recipe_assumed_unavailable"
        ), "Atlas impact scenario");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(scenario, "kind"),
                "remove-exact-observed-recipe",
                "Atlas impact scenario is not the supported exact-recipe removal"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.bool(scenario, "selected_recipe_assumed_unavailable"),
                "Atlas impact scenario did not mark the exact selected recipe unavailable"
        );
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(scenario, "change_interpretation"),
                CHANGE_INTERPRETATION,
                "Atlas impact replacement-model boundary changed"
        );

        JsonObject model = object(root, "analysis_model");
        AtlasRecipeContract.exactKeys(model, Set.of(
                "claim_boundary", "kind", "recipe_at_risk_rule", "resource_at_risk_rule"
        ), "Atlas impact analysis model");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(model, "kind"),
                "bounded-observed-finite-recipe-dependency-exposure",
                "Atlas impact analysis model changed"
        );
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(model, "resource_at_risk_rule"),
                "every observed finite recipe producer is an unavailable candidate",
                "Atlas impact resource candidate rule changed"
        );
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(model, "recipe_at_risk_rule"),
                "at least one exact input selector accepts only resources already classified at risk",
                "Atlas impact recipe candidate rule changed"
        );
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(model, "claim_boundary"), CLAIM_BOUNDARY,
                "Atlas impact claim boundary changed"
        );

        JsonObject bounds = object(root, "bounds");
        AtlasRecipeContract.exactKeys(bounds, Set.of(
                "max_depth", "max_nodes", "visited_node_count"
        ), "Atlas impact bounds");
        AtlasRecipeContract.require(
                AtlasRecipeContract.integer(bounds, "max_depth") == expectedMaxDepth,
                "Workbench Atlas impact changed the requested depth bound"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.integer(bounds, "max_nodes") == expectedMaxNodes,
                "Workbench Atlas impact changed the requested node bound"
        );
        int visited = AtlasRecipeContract.nonnegativeInteger(bounds, "visited_node_count");
        AtlasRecipeContract.require(visited >= 1 && visited <= expectedMaxNodes,
                "Workbench Atlas impact exceeded its traversal node bound");

        JsonObject direct = object(root, "direct");
        AtlasRecipeContract.exactKeys(direct, Set.of("inputs", "outputs"),
                "Atlas impact direct section");
        JsonArray inputs = AtlasRecipeContract.array(direct, "inputs");
        JsonArray outputs = AtlasRecipeContract.array(direct, "outputs");
        AtlasRecipeContract.require(inputs.size() <= 10_000 && outputs.size() <= expectedMaxNodes,
                "Atlas impact direct section exceeds its supported bounds");
        validateDirectInputs(inputs);
        int soleProducerOutputs = validateDirectOutputs(outputs, expectedMaxNodes);

        JsonObject propagation = object(root, "propagation");
        AtlasRecipeContract.exactKeys(propagation, Set.of(
                "alternative_dependency_cycle_signals", "at_risk_recipes",
                "at_risk_resources", "status"
        ), "Atlas impact propagation section");
        String propagationStatus = AtlasRecipeContract.string(propagation, "status");
        AtlasRecipeContract.require(
                propagationStatus.equals("complete-within-model")
                        || propagationStatus.equals("truncated"),
                "Atlas impact propagation status changed"
        );
        JsonArray candidateResources = AtlasRecipeContract.array(
                propagation, "at_risk_resources"
        );
        JsonArray candidateRecipes = AtlasRecipeContract.array(
                propagation, "at_risk_recipes"
        );
        JsonArray cycles = AtlasRecipeContract.array(
                propagation, "alternative_dependency_cycle_signals"
        );
        AtlasRecipeContract.require(
                candidateResources.size() <= expectedMaxNodes
                        && candidateRecipes.size() <= expectedMaxNodes
                        && cycles.size() <= expectedMaxNodes,
                "Atlas impact propagation arrays exceed the requested node bound"
        );
        validateCandidateRows(candidateResources, "resource", expectedMaxDepth);
        validateCandidateRows(candidateRecipes, "recipe", expectedMaxDepth);
        validateAlternativeCycles(cycles, expectedMaxNodes);

        JsonObject progression = object(root, "progression_signals");
        validateProgression(progression, expectedMaxDepth, expectedMaxNodes);

        JsonArray frontiers = AtlasRecipeContract.array(root, "frontiers");
        JsonArray unknowns = AtlasRecipeContract.array(root, "unknowns");
        AtlasRecipeContract.require(
                frontiers.size() <= MAX_UNCERTAINTY_ROWS && unknowns.size() <= MAX_UNCERTAINTY_ROWS,
                "Atlas impact uncertainty arrays exceed the supported report-row bound"
        );
        validateFrontiers(frontiers);
        validateUnknowns(unknowns);

        JsonArray gaps = AtlasRecipeContract.array(root, "evidence_gaps");
        validateEvidenceGaps(gaps);

        JsonObject summary = object(root, "summary");
        AtlasRecipeContract.exactKeys(summary, Set.of(
                "alternative_dependency_cycle_signal_count",
                "at_risk_recipe_candidate_count", "at_risk_resource_candidate_count",
                "quest_requirement_exposure_count", "selected_output_count",
                "sole_observed_finite_producer_output_count",
                "structural_quest_dependent_count", "truncated"
        ), "Atlas impact summary");
        exactCount(summary, "selected_output_count", outputs.size());
        exactCount(summary, "sole_observed_finite_producer_output_count", soleProducerOutputs);
        exactCount(summary, "at_risk_resource_candidate_count", candidateResources.size());
        exactCount(summary, "at_risk_recipe_candidate_count", candidateRecipes.size());
        exactCount(summary, "alternative_dependency_cycle_signal_count", cycles.size());
        JsonObject quest = object(progression, "quest_signals");
        exactCount(
                summary,
                "quest_requirement_exposure_count",
                AtlasRecipeContract.array(quest, "direct_resource_requirements").size()
        );
        exactCount(
                summary,
                "structural_quest_dependent_count",
                AtlasRecipeContract.array(quest, "structural_prerequisite_dependents").size()
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.bool(summary, "truncated") == !frontiers.isEmpty(),
                "Atlas impact truncation summary does not match its frontiers"
        );

        return new AtlasRecipeImpact(
                json, context, selection, selectionId, semanticKey, scenario, model, bounds,
                direct, propagation, progression, frontiers, unknowns, gaps, summary
        );
    }

    private static int validateDirectOutputs(@NotNull JsonArray outputs, int maximumNodes) {
        int sole = 0;
        for (JsonElement element : outputs) {
            JsonObject row = AtlasRecipeContract.object(element, "Atlas direct output");
            AtlasRecipeContract.exactKeys(row, Set.of(
                    "downstream_consumers", "downstream_consumers_truncated", "output",
                    "producer_portfolio"
            ), "Atlas direct output");
            validateEdgeSummary(
                    AtlasRecipeContract.required(row, "output"),
                    "Atlas output edge",
                    true
            );
            JsonArray consumers = AtlasRecipeContract.array(row, "downstream_consumers");
            AtlasRecipeContract.require(
                    consumers.size() <= maximumNodes,
                    "Atlas downstream consumers exceed the requested node bound"
            );
            for (JsonElement consumerElement : consumers) {
                JsonObject consumer = AtlasRecipeContract.object(
                        consumerElement, "Atlas downstream consumer"
                );
                AtlasRecipeContract.exactKeys(consumer, Set.of(
                        "acceptance_edge", "recipe", "selector"
                ), "Atlas downstream consumer");
                validateEdgeSummary(
                        AtlasRecipeContract.required(consumer, "acceptance_edge"),
                        "Atlas downstream acceptance edge",
                        false
                );
                JsonObject recipe = nodeSummary(consumer, "recipe", false);
                AtlasRecipeContract.equal(
                        AtlasRecipeContract.string(recipe, "kind"), "gt-recipe",
                        "Atlas downstream consumer is not an observed gt-recipe"
                );
                requireRecipeId(AtlasRecipeContract.string(recipe, "selection_id"));
                nodeSummary(consumer, "selector", false);
            }
            AtlasRecipeContract.bool(row, "downstream_consumers_truncated");
            JsonObject portfolio = AtlasRecipeContract.object(
                    AtlasRecipeContract.required(row, "producer_portfolio"),
                    "Atlas producer portfolio"
            );
            AtlasRecipeContract.exactKeys(portfolio, Set.of(
                    "alternative_producers", "status", "truncated", "viability"
            ), "Atlas producer portfolio");
            String status = AtlasRecipeContract.string(portfolio, "status");
            AtlasRecipeContract.require(Set.of(
                    "producer-set-truncated", "observed-alternatives-present",
                    "sole-observed-finite-producer", "no-observed-active-producer"
            ).contains(status), "Atlas producer portfolio status changed");
            JsonArray alternatives = AtlasRecipeContract.array(
                    portfolio, "alternative_producers"
            );
            AtlasRecipeContract.require(
                    alternatives.size() <= maximumNodes,
                    "Atlas alternative producers exceed the requested node bound"
            );
            for (JsonElement alternativeElement : alternatives) {
                JsonObject alternative = AtlasRecipeContract.object(
                        alternativeElement, "Atlas alternative producer"
                );
                AtlasRecipeContract.exactKeys(alternative, Set.of(
                        "output_edge", "recipe"
                ), "Atlas alternative producer");
                validateEdgeSummary(
                        AtlasRecipeContract.required(alternative, "output_edge"),
                        "Atlas alternative output edge",
                        false
                );
                JsonObject recipe = nodeSummary(alternative, "recipe", false);
                AtlasRecipeContract.equal(
                        AtlasRecipeContract.string(recipe, "kind"), "gt-recipe",
                        "Atlas alternative producer is not an observed gt-recipe"
                );
                requireRecipeId(AtlasRecipeContract.string(recipe, "selection_id"));
            }
            AtlasRecipeContract.bool(portfolio, "truncated");
            AtlasRecipeContract.equal(
                    AtlasRecipeContract.string(portfolio, "viability"), "not-assessed",
                    "Atlas alternative producer viability boundary changed"
            );
            if (status.equals("sole-observed-finite-producer")) {
                sole++;
            }
        }
        return sole;
    }

    static void validateDirectInputs(@NotNull JsonArray inputs) {
        for (JsonElement element : inputs) {
            JsonObject row = AtlasRecipeContract.object(element, "Atlas direct input");
            AtlasRecipeContract.exactKeys(row, Set.of(
                    "evidence", "node", "properties", "relation", "selector",
                    "selector_edge", "semantic_key"
            ), "Atlas direct input");
            AtlasRecipeContract.require(
                    AtlasRecipeContract.required(row, "evidence").isJsonArray(),
                    "Atlas direct input evidence must be an array"
            );
            AtlasRecipeContract.object(
                    AtlasRecipeContract.required(row, "properties"),
                    "Atlas direct input properties"
            );
            AtlasRecipeContract.string(row, "relation");
            AtlasRecipeContract.string(row, "semantic_key");
            nodeSummary(row, "node", true);
            nodeSummary(row, "selector", true);
            JsonObject selectorEdge = AtlasRecipeContract.object(
                    AtlasRecipeContract.required(row, "selector_edge"),
                    "Atlas selector edge"
            );
            AtlasRecipeContract.exactKeys(selectorEdge, Set.of(
                    "evidence", "properties", "relation"
            ), "Atlas selector edge");
            AtlasRecipeContract.require(
                    AtlasRecipeContract.required(selectorEdge, "evidence").isJsonArray(),
                    "Atlas selector-edge evidence must be an array"
            );
            AtlasRecipeContract.object(
                    AtlasRecipeContract.required(selectorEdge, "properties"),
                    "Atlas selector-edge properties"
            );
            AtlasRecipeContract.string(selectorEdge, "relation");
        }
    }

    private static void validateCandidateRows(
            @NotNull JsonArray rows,
            @NotNull String nodeKey,
            int maximumDepth
    ) {
        Set<String> identities = new HashSet<>();
        for (JsonElement element : rows) {
            JsonObject row = AtlasRecipeContract.object(element, "Atlas candidate row");
            boolean recipe = nodeKey.equals("recipe");
            AtlasRecipeContract.exactKeys(
                    row,
                    recipe
                            ? Set.of("blocked_selectors", "depth", "reason", "recipe")
                            : Set.of(
                                    "all_observed_finite_producers", "depth", "reason",
                                    "resource"
                            ),
                    "Atlas candidate row"
            );
            JsonObject node = AtlasRecipeContract.object(
                    AtlasRecipeContract.required(row, nodeKey), "Atlas candidate node"
            );
            AtlasRecipeContract.exactKeys(node, Set.of(
                    "evidence", "kind", "properties", "selection_id", "semantic_key"
            ), "Atlas candidate node");
            String id = AtlasRecipeContract.string(node, "selection_id");
            AtlasRecipeContract.require(identities.add(id),
                    "Atlas impact repeats one candidate identity");
            AtlasRecipeContract.string(node, "kind");
            if (recipe) {
                AtlasRecipeContract.equal(
                        AtlasRecipeContract.string(node, "kind"), "gt-recipe",
                        "Atlas exposed recipe candidate is not one observed gt-recipe"
                );
                AtlasRecipeImpact.requireRecipeId(id);
            }
            AtlasRecipeContract.string(node, "semantic_key");
            AtlasRecipeContract.object(
                    AtlasRecipeContract.required(node, "properties"),
                    "Atlas candidate properties"
            );
            AtlasRecipeContract.require(
                    AtlasRecipeContract.required(node, "evidence").isJsonArray(),
                    "Atlas candidate evidence must be an array"
            );
            int depth = AtlasRecipeContract.nonnegativeInteger(row, "depth");
            AtlasRecipeContract.require(depth <= maximumDepth,
                    "Atlas candidate depth exceeds the requested bound");
            AtlasRecipeContract.equal(
                    AtlasRecipeContract.string(row, "reason"),
                    recipe
                            ? "at-least-one-selector-has-only-at-risk-observed-alternatives"
                            : "all-observed-finite-producers-are-unavailable-candidates",
                    "Atlas candidate classification rule changed"
            );
            JsonArray detail = AtlasRecipeContract.array(
                    row,
                    recipe ? "blocked_selectors" : "all_observed_finite_producers"
            );
            AtlasRecipeContract.require(detail.size() <= 2_000,
                    "Atlas candidate detail exceeds the supported node bound");
            if (!recipe) {
                for (JsonElement producer : detail) {
                    AtlasRecipeContract.string(
                            producer,
                            "candidate producer identity",
                            false,
                            AtlasRecipeContract.MAX_SMALL_TEXT_BYTES
                    );
                }
            } else {
                for (JsonElement blockedElement : detail) {
                    JsonObject blocked = AtlasRecipeContract.object(
                            blockedElement, "Atlas blocked selector candidate"
                    );
                    AtlasRecipeContract.exactKeys(blocked, Set.of(
                            "accepted_resources", "selector"
                    ), "Atlas blocked selector candidate");
                    nodeSummary(blocked, "selector", false);
                    JsonArray resources = AtlasRecipeContract.array(
                            blocked, "accepted_resources"
                    );
                    AtlasRecipeContract.require(resources.size() <= 2_000,
                            "Atlas accepted candidate resources exceed the supported bound");
                    for (JsonElement resource : resources) {
                        validateNodeSummary(resource, "Atlas accepted candidate resource");
                    }
                }
            }
        }
    }

    private static void validateProgression(
            @NotNull JsonObject progression,
            int maximumDepth,
            int maximumNodes
    ) {
        AtlasRecipeContract.exactKeys(progression, Set.of(
                "energy_and_machine_signals", "quest_signals"
        ), "Atlas progression signals");
        JsonObject energy = object(progression, "energy_and_machine_signals");
        AtlasRecipeContract.exactKeys(energy, Set.of(
                "interpretation", "machine_numeric_tiers_observed",
                "machines_observed_using_recipe_map", "observed_recipe_properties",
                "recipe_map", "truncated"
        ), "Atlas energy and machine signals");
        JsonObject observedProperties = AtlasRecipeContract.object(
                AtlasRecipeContract.required(energy, "observed_recipe_properties"),
                "Atlas observed recipe properties"
        );
        AtlasRecipeContract.exactKeys(observedProperties, Set.of(
                "duration", "eut", "hidden", "lookup_active", "recipe_map"
        ), "Atlas observed recipe properties");
        JsonElement recipeMap = AtlasRecipeContract.required(energy, "recipe_map");
        AtlasRecipeContract.require(recipeMap.isJsonNull() || recipeMap.isJsonObject(),
                "Atlas recipe map signal must be null or an object");
        if (recipeMap.isJsonObject()) {
            validateNodeSummary(recipeMap, "Atlas recipe map signal");
        }
        JsonArray machines = AtlasRecipeContract.array(
                energy, "machines_observed_using_recipe_map"
        );
        AtlasRecipeContract.require(
                machines.size() <= maximumNodes,
                "Atlas machine signals exceed the requested node bound"
        );
        for (JsonElement machineElement : machines) {
            JsonObject machine = AtlasRecipeContract.object(
                    machineElement, "Atlas machine signal"
            );
            AtlasRecipeContract.exactKeys(machine, Set.of("machine", "map_edge"),
                    "Atlas machine signal");
            nodeSummary(machine, "machine", false);
            validateEdgeSummary(
                    AtlasRecipeContract.required(machine, "map_edge"),
                    "Atlas machine map edge",
                    false
            );
        }
        JsonArray tiers = AtlasRecipeContract.array(energy, "machine_numeric_tiers_observed");
        AtlasRecipeContract.require(tiers.size() <= maximumNodes,
                "Atlas machine tier signals exceed the requested node bound");
        for (JsonElement tier : tiers) {
            AtlasRecipeContract.require(
                    tier.isJsonPrimitive() && tier.getAsJsonPrimitive().isNumber(),
                    "Atlas machine tier signal must be an integer"
            );
            try {
                tier.getAsBigDecimal().intValueExact();
            } catch (ArithmeticException | NumberFormatException error) {
                throw new IllegalArgumentException(
                        "Atlas machine tier signal must be an integer", error
                );
            }
        }
        AtlasRecipeContract.bool(energy, "truncated");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(energy, "interpretation"),
                "numeric EUT and machine tier properties are observed signals; no pack progression tier is inferred",
                "Atlas energy and machine interpretation changed"
        );

        JsonObject quest = object(progression, "quest_signals");
        AtlasRecipeContract.exactKeys(quest, Set.of(
                "direct_resource_requirements", "interpretation", "prerequisite_cycles",
                "status", "structural_prerequisite_dependents"
        ), "Atlas quest signals");
        String status = AtlasRecipeContract.string(quest, "status");
        AtlasRecipeContract.require(Set.of(
                "truncated", "observed-definition-references", "no-observed-reference",
                "evidence-unavailable"
        ).contains(status), "Atlas quest signal status changed");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(quest, "interpretation"),
                "these are definition references and prerequisite structure, not observed player blockage or task execution",
                "Atlas quest interpretation changed"
        );
        JsonArray requirements = AtlasRecipeContract.array(
                quest, "direct_resource_requirements"
        );
        JsonArray dependents = AtlasRecipeContract.array(
                quest, "structural_prerequisite_dependents"
        );
        JsonArray prerequisiteCycles = AtlasRecipeContract.array(
                quest, "prerequisite_cycles"
        );
        AtlasRecipeContract.require(
                requirements.size() <= maximumNodes
                        && dependents.size() <= maximumNodes
                        && prerequisiteCycles.size() <= maximumNodes,
                "Atlas quest signal arrays exceed the requested node bound"
        );
        validateQuestRequirements(requirements, maximumNodes);
        validateQuestDependents(dependents, maximumDepth);
        validateQuestCycles(prerequisiteCycles, maximumNodes);
    }

    private static void validateAlternativeCycles(
            @NotNull JsonArray cycles,
            int maximumNodes
    ) {
        for (JsonElement cycleElement : cycles) {
            JsonObject cycle = AtlasRecipeContract.object(
                    cycleElement, "Atlas alternative dependency cycle signal"
            );
            AtlasRecipeContract.exactKeys(cycle, Set.of(
                    "interpretation", "path", "root_resource_id", "viability_effect"
            ), "Atlas alternative dependency cycle signal");
            String interpretation = AtlasRecipeContract.string(cycle, "interpretation");
            AtlasRecipeContract.require(Set.of(
                    "an observed alternative producer has an input-dependency path back to the resource it produces",
                    "the bounded alternative-producer dependency neighborhood contains a cycle"
            ).contains(interpretation), "Atlas alternative cycle interpretation changed");
            AtlasRecipeContract.string(cycle, "root_resource_id");
            AtlasRecipeContract.equal(
                    AtlasRecipeContract.string(cycle, "viability_effect"), "unknown",
                    "Atlas alternative cycle viability boundary changed"
            );
            validateStringArray(
                    AtlasRecipeContract.array(cycle, "path"),
                    "alternative dependency path",
                    maximumNodes
            );
        }
    }

    static void validateQuestRequirements(
            @NotNull JsonArray requirements,
            int maximumNodes
    ) {
        for (JsonElement requirementElement : requirements) {
            JsonObject requirement = AtlasRecipeContract.object(
                    requirementElement, "Atlas quest resource requirement"
            );
            AtlasRecipeContract.exactKeys(requirement, Set.of(
                    "accepted_ore_dictionary_keys", "owner_edge", "quest",
                    "requirement_occurrence", "requirement_semantics", "resource",
                    "resource_edge", "resource_risk_status", "task", "task_edge"
            ), "Atlas quest resource requirement");
            nodeSummary(requirement, "quest", false);
            nodeSummary(requirement, "task", false);
            nodeSummary(requirement, "requirement_occurrence", false);
            nodeSummary(requirement, "resource", false);
            validateEdgeSummary(
                    AtlasRecipeContract.required(requirement, "resource_edge"),
                    "Atlas quest resource edge",
                    false
            );
            validateEdgeSummary(
                    AtlasRecipeContract.required(requirement, "task_edge"),
                    "Atlas quest task edge",
                    false
            );
            validateEdgeSummary(
                    AtlasRecipeContract.required(requirement, "owner_edge"),
                    "Atlas quest owner edge",
                    false
            );
            AtlasRecipeContract.require(Set.of(
                    "producer-portfolio-changed",
                    "observed-all-finite-producers-at-risk"
            ).contains(AtlasRecipeContract.string(requirement, "resource_risk_status")),
                    "Atlas quest resource exposure status changed");
            AtlasRecipeContract.require(Set.of(
                    "ore-dictionary-selector-with-observed-item-representative",
                    "exact-observed-resource-reference"
            ).contains(AtlasRecipeContract.string(requirement, "requirement_semantics")),
                    "Atlas quest requirement semantics changed");
            JsonArray oreKeys = AtlasRecipeContract.array(
                    requirement, "accepted_ore_dictionary_keys"
            );
            AtlasRecipeContract.require(oreKeys.size() <= maximumNodes,
                    "Atlas ore-dictionary signals exceed the requested node bound");
            for (JsonElement oreElement : oreKeys) {
                JsonObject ore = AtlasRecipeContract.object(
                        oreElement, "Atlas ore-dictionary signal"
                );
                AtlasRecipeContract.exactKeys(ore, Set.of(
                        "acceptance_edge", "ore_dictionary_key"
                ), "Atlas ore-dictionary signal");
                nodeSummary(ore, "ore_dictionary_key", false);
                validateEdgeSummary(
                        AtlasRecipeContract.required(ore, "acceptance_edge"),
                        "Atlas ore-dictionary acceptance edge",
                        false
                );
            }
        }
    }

    static void validateQuestDependents(
            @NotNull JsonArray dependents,
            int maximumDepth
    ) {
        for (JsonElement dependentElement : dependents) {
            JsonObject dependent = AtlasRecipeContract.object(
                    dependentElement, "Atlas structural quest dependent"
            );
            AtlasRecipeContract.exactKeys(dependent, Set.of(
                    "depends_on", "depth", "owner_edge", "prerequisite_occurrence",
                    "quest", "target_edge"
            ), "Atlas structural quest dependent");
            nodeSummary(dependent, "quest", false);
            nodeSummary(dependent, "depends_on", false);
            nodeSummary(dependent, "prerequisite_occurrence", false);
            validateEdgeSummary(
                    AtlasRecipeContract.required(dependent, "target_edge"),
                    "Atlas prerequisite target edge",
                    false
            );
            validateEdgeSummary(
                    AtlasRecipeContract.required(dependent, "owner_edge"),
                    "Atlas prerequisite owner edge",
                    false
            );
            int depth = AtlasRecipeContract.nonnegativeInteger(dependent, "depth");
            AtlasRecipeContract.require(depth <= maximumDepth,
                    "Atlas quest dependent depth exceeds the supported bound");
        }
    }

    private static void validateQuestCycles(
            @NotNull JsonArray cycles,
            int maximumNodes
    ) {
        for (JsonElement cycleElement : cycles) {
            JsonObject cycle = AtlasRecipeContract.object(
                    cycleElement, "Atlas prerequisite cycle"
            );
            AtlasRecipeContract.exactKeys(cycle, Set.of("interpretation", "path"),
                    "Atlas prerequisite cycle");
            AtlasRecipeContract.equal(
                    AtlasRecipeContract.string(cycle, "interpretation"),
                    "observed BetterQuesting prerequisite cycle",
                    "Atlas prerequisite cycle interpretation changed"
            );
            validateStringArray(
                    AtlasRecipeContract.array(cycle, "path"),
                    "quest prerequisite cycle path",
                    maximumNodes
            );
        }
    }

    private static void validateFrontiers(@NotNull JsonArray frontiers) {
        for (JsonElement element : frontiers) {
            JsonObject row = AtlasRecipeContract.object(element, "Atlas frontier");
            AtlasRecipeContract.allowedKeys(row, Set.of("kind"), Set.of(
                    "depth", "from_node_id", "omitted_at_least", "phase", "relation",
                    "subject_ids"
            ), "Atlas frontier");
            AtlasRecipeContract.string(row, "kind");
            optionalString(row, "from_node_id");
            optionalString(row, "phase");
            optionalString(row, "relation");
            optionalNonnegative(row, "depth");
            optionalNonnegative(row, "omitted_at_least");
            if (row.has("subject_ids")) {
                JsonElement values = row.get("subject_ids");
                AtlasRecipeContract.require(values.isJsonArray(),
                        "Atlas frontier subject IDs must be an array");
                for (JsonElement value : values.getAsJsonArray()) {
                    AtlasRecipeContract.string(
                            value, "frontier subject identity", false,
                            AtlasRecipeContract.MAX_SMALL_TEXT_BYTES
                    );
                }
            }
        }
    }

    static void validateUnknowns(@NotNull JsonArray unknowns) {
        Set<String> identities = new HashSet<>();
        for (JsonElement element : unknowns) {
            JsonObject row = AtlasRecipeContract.object(element, "Atlas unknown");
            AtlasRecipeContract.allowedKeys(
                    row, Set.of("code", "message"), Set.of("subject_id"), "Atlas unknown"
            );
            String code = AtlasRecipeContract.string(row, "code");
            String subject = row.has("subject_id")
                    ? AtlasRecipeContract.string(row, "subject_id") : "";
            AtlasRecipeContract.require(identities.add(code + "\u0000" + subject),
                    "Atlas impact repeats one unknown classification");
            AtlasRecipeContract.string(row, "message");
        }
    }

    static void validateEvidenceGaps(@NotNull JsonArray gaps) {
        AtlasRecipeContract.require((gaps.size() == REQUIRED_GAPS.size() || gaps.size() == REQUIRED_GAPS.size() + 1),
                "Atlas impact evidence-gap boundary changed");
        for (int index = 0; index < REQUIRED_GAPS.size(); index++) {
            JsonObject row = AtlasRecipeContract.object(
                    gaps.get(index), "Atlas evidence gap"
            );
            AtlasRecipeContract.exactKeys(row, Set.of("code", "message"),
                    "Atlas evidence gap");
            Gap expected = REQUIRED_GAPS.get(index);
            AtlasRecipeContract.equal(
                    AtlasRecipeContract.string(row, "code"), expected.code(),
                    "Atlas impact evidence-gap identity changed"
            );
            AtlasRecipeContract.equal(
                    AtlasRecipeContract.string(row, "message"), expected.message(),
                    "Atlas impact evidence-gap meaning changed"
            );
        }
        if (gaps.size() > REQUIRED_GAPS.size()) {
            JsonObject row = AtlasRecipeContract.object(gaps.get(REQUIRED_GAPS.size()), "Atlas projection limitation");
            AtlasRecipeContract.exactKeys(row, Set.of("code", "message"), "Atlas projection limitation");
            AtlasRecipeContract.equal(AtlasRecipeContract.string(row, "code"), "graph-projection-limitations",
                    "Atlas impact optional evidence-gap identity changed");
            AtlasRecipeContract.string(row, "message");
        }
    }

    private static void exactCount(
            @NotNull JsonObject summary,
            @NotNull String key,
            int expected
    ) {
        AtlasRecipeContract.require(
                AtlasRecipeContract.nonnegativeInteger(summary, key) == expected,
                "Atlas impact summary count does not match " + key
        );
    }

    private static @NotNull JsonObject nodeSummary(
            @NotNull JsonObject parent,
            @NotNull String key,
            boolean nullable
    ) {
        JsonElement element = AtlasRecipeContract.required(parent, key);
        if (nullable && element.isJsonNull()) {
            return new JsonObject();
        }
        validateNodeSummary(element, "Atlas " + key);
        return element.getAsJsonObject();
    }

    static void validateNodeSummary(
            @NotNull JsonElement element,
            @NotNull String label
    ) {
        JsonObject node = AtlasRecipeContract.object(element, label);
        AtlasRecipeContract.exactKeys(node, Set.of(
                "evidence", "kind", "properties", "selection_id", "semantic_key"
        ), label);
        AtlasRecipeContract.string(node, "selection_id");
        AtlasRecipeContract.string(node, "kind");
        AtlasRecipeContract.string(node, "semantic_key");
        AtlasRecipeContract.object(
                AtlasRecipeContract.required(node, "properties"), label + " properties"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.required(node, "evidence").isJsonArray(),
                label + " evidence must be an array"
        );
    }

    static void validateEdgeSummary(
            @NotNull JsonElement element,
            @NotNull String label,
            boolean includesNode
    ) {
        JsonObject edge = AtlasRecipeContract.object(element, label);
        AtlasRecipeContract.exactKeys(
                edge,
                includesNode
                        ? Set.of("evidence", "node", "properties", "relation", "semantic_key")
                        : Set.of("evidence", "properties", "relation", "semantic_key"),
                label
        );
        AtlasRecipeContract.string(edge, "relation");
        AtlasRecipeContract.string(edge, "semantic_key");
        AtlasRecipeContract.object(
                AtlasRecipeContract.required(edge, "properties"), label + " properties"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.required(edge, "evidence").isJsonArray(),
                label + " evidence must be an array"
        );
        if (includesNode) {
            JsonElement node = AtlasRecipeContract.required(edge, "node");
            if (!node.isJsonNull()) {
                validateNodeSummary(node, label + " node");
            }
        }
    }

    private static void validateStringArray(
            @NotNull JsonArray array,
            @NotNull String label,
            int maximum
    ) {
        AtlasRecipeContract.require(array.size() <= maximum,
                "Atlas " + label + " exceeds the supported bound");
        for (JsonElement element : array) {
            AtlasRecipeContract.string(
                    element, label, false, AtlasRecipeContract.MAX_SMALL_TEXT_BYTES
            );
        }
    }

    private static void optionalString(@NotNull JsonObject row, @NotNull String key) {
        if (row.has(key)) {
            AtlasRecipeContract.string(row, key);
        }
    }

    private static void optionalNonnegative(@NotNull JsonObject row, @NotNull String key) {
        if (row.has(key)) {
            AtlasRecipeContract.nonnegativeInteger(row, key);
        }
    }

    private static @NotNull JsonObject object(
            @NotNull JsonObject parent,
            @NotNull String key
    ) {
        return AtlasRecipeContract.object(
                AtlasRecipeContract.required(parent, key), "Atlas " + key
        );
    }

    static void requireRecipeId(@NotNull String value) {
        AtlasRecipeContract.require(
                value.startsWith("workbench-atlas-node-v2:gt-recipe:")
                        && value.length() > "workbench-atlas-node-v2:gt-recipe:".length()
                        && value.length() <= AtlasRecipeContract.MAX_SMALL_TEXT_BYTES,
                "Atlas recipe selection must be one exact gt-recipe node identity"
        );
    }

    @NotNull String rawJson() {
        return rawJson;
    }

    @NotNull AtlasRecipeContext context() {
        return context;
    }

    @NotNull JsonObject selection() {
        return selection.deepCopy();
    }

    @NotNull String selectionId() {
        return selectionId;
    }

    @NotNull String semanticKey() {
        return semanticKey;
    }

    @NotNull JsonObject scenario() {
        return scenario.deepCopy();
    }

    @NotNull JsonObject analysisModel() {
        return analysisModel.deepCopy();
    }

    @NotNull JsonObject bounds() {
        return bounds.deepCopy();
    }

    @NotNull JsonObject direct() {
        return direct.deepCopy();
    }

    @NotNull JsonObject propagation() {
        return propagation.deepCopy();
    }

    @NotNull JsonObject progressionSignals() {
        return progressionSignals.deepCopy();
    }

    @NotNull JsonArray frontiers() {
        return frontiers.deepCopy();
    }

    @NotNull JsonArray unknowns() {
        return unknowns.deepCopy();
    }

    @NotNull JsonArray evidenceGaps() {
        return evidenceGaps.deepCopy();
    }

    @NotNull JsonObject summary() {
        return summary.deepCopy();
    }

    private record Gap(@NotNull String code, @NotNull String message) {
    }
}
