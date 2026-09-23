package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Strict graph-only Atlas recipe search result and exact selection picker input. */
final class AtlasRecipeSearch {
    static final String FORMAT = "workbench-atlas-recipe-health-search-v1";

    private final String rawJson;
    private final AtlasRecipeContext context;
    private final String query;
    private final boolean truncated;
    private final List<Result> recipes;

    private AtlasRecipeSearch(
            @NotNull String rawJson,
            @NotNull AtlasRecipeContext context,
            @NotNull String query,
            boolean truncated,
            @NotNull List<Result> recipes
    ) {
        this.rawJson = rawJson;
        this.context = context;
        this.query = query;
        this.truncated = truncated;
        this.recipes = List.copyOf(recipes);
    }

    static @NotNull AtlasRecipeSearch parse(
            @NotNull String json,
            @NotNull String expectedQuery,
            int maximumResults
    ) {
        AtlasRecipeContract.require(maximumResults >= 1 && maximumResults <= 10_000,
                "Atlas recipe search result bound is unsupported");
        JsonObject root = AtlasRecipeContract.parseRoot(json, "Atlas recipe search");
        AtlasRecipeContract.exactKeys(root, Set.of(
                "context", "format", "query", "results", "schema_version", "truncated"
        ), "Atlas recipe search");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(root, "format"), FORMAT,
                "Workbench returned an unsupported Atlas recipe search"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.integer(root, "schema_version") == 1,
                "Workbench returned an unsupported Atlas recipe search schema"
        );
        String query = AtlasRecipeContract.string(root, "query");
        AtlasRecipeContract.equal(query, expectedQuery,
                "Workbench Atlas search changed the exact query");
        AtlasRecipeContext context = AtlasRecipeContext.parse(
                AtlasRecipeContract.object(
                        AtlasRecipeContract.required(root, "context"), "Atlas search context"
                )
        );
        boolean truncated = AtlasRecipeContract.bool(root, "truncated");
        JsonArray values = AtlasRecipeContract.array(root, "results");
        AtlasRecipeContract.require(values.size() <= maximumResults,
                "Workbench Atlas search exceeded its requested result bound");
        List<Result> recipes = new ArrayList<>();
        Set<String> identities = new HashSet<>();
        for (var element : values) {
            JsonObject row = AtlasRecipeContract.object(element, "Atlas search result");
            AtlasRecipeContract.exactKeys(row, Set.of(
                    "evidence", "kind", "properties", "selection_id", "semantic_key"
            ), "Atlas graph search result");
            String selectionId = AtlasRecipeContract.string(row, "selection_id");
            String kind = AtlasRecipeContract.string(row, "kind");
            String semanticKey = AtlasRecipeContract.string(row, "semantic_key");
            AtlasRecipeContract.object(
                    AtlasRecipeContract.required(row, "properties"), "Atlas node properties"
            );
            AtlasRecipeContract.require(
                    AtlasRecipeContract.required(row, "evidence").isJsonArray(),
                    "Workbench Atlas node evidence must be an array"
            );
            AtlasRecipeContract.require(identities.add(selectionId),
                    "Workbench Atlas search contains a duplicate selection");
            if (kind.equals("gt-recipe")) {
                AtlasRecipeImpact.requireRecipeId(selectionId);
                recipes.add(new Result(selectionId, semanticKey, row.deepCopy()));
            }
        }
        return new AtlasRecipeSearch(json, context, query, truncated, recipes);
    }

    @NotNull String rawJson() {
        return rawJson;
    }

    @NotNull AtlasRecipeContext context() {
        return context;
    }

    @NotNull String query() {
        return query;
    }

    boolean truncated() {
        return truncated;
    }

    @NotNull List<Result> recipes() {
        return recipes;
    }

    record Result(
            @NotNull String selectionId,
            @NotNull String semanticKey,
            @NotNull JsonObject value
    ) {
        Result {
            value = value.deepCopy();
        }

        @Override
        public @NotNull JsonObject value() {
            return value.deepCopy();
        }
    }
}
