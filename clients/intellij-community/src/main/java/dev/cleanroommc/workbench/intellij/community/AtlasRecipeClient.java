package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.util.List;

/** Direct no-shell client and exact context linker for native Atlas recipe impact. */
final class AtlasRecipeClient {
    private AtlasRecipeClient() {
    }

    static @NotNull GraphPath graphPath(
            @NotNull CoreLaunch launch,
            @NotNull String enteredPath
    ) {
        String selected = enteredPath.trim();
        AtlasRecipeContract.require(!selected.isEmpty(),
                "Choose one explicit categorical graph directory");
        if (!launch.host().equals("native")) {
            String command = launch.commandPath(selected, "Atlas categorical graph");
            AtlasRecipeContract.require(command.startsWith("/"),
                    "Atlas categorical graph must be an absolute path");
            return new GraphPath(selected, command, command);
        }
        final Path parsed;
        try {
            parsed = Path.of(selected);
        } catch (InvalidPathException error) {
            throw new IllegalArgumentException("Atlas categorical graph path is invalid", error);
        }
        AtlasRecipeContract.require(parsed.isAbsolute(),
                "Atlas categorical graph must be an absolute path");
        String normalized = parsed.normalize().toString();
        AtlasRecipeContract.require(!normalized.isEmpty(),
                "Atlas categorical graph must be an absolute path");
        return new GraphPath(normalized, normalized, normalized);
    }

    static @NotNull List<String> searchArguments(
            @NotNull GraphPath graph,
            @NotNull String query,
            int limit
    ) {
        AtlasRecipeContract.require(!query.isBlank(),
                "Enter a recipe query or paste an exact selection ID");
        AtlasRecipeContract.require(limit >= 1 && limit <= 10_000,
                "Atlas recipe search limit is outside 1..10000");
        return List.of(
                "atlas", "recipes", "search", graph.commandPath(), query,
                "--limit", Integer.toString(limit), "--json"
        );
    }

    static @NotNull List<String> impactArguments(
            @NotNull GraphPath graph,
            @NotNull String selectionId,
            int maxDepth,
            int maxNodes
    ) {
        AtlasRecipeImpact.requireRecipeId(selectionId);
        AtlasRecipeContract.require(maxDepth >= 1 && maxDepth <= 12,
                "Atlas impact depth is outside 1..12");
        AtlasRecipeContract.require(maxNodes >= 10 && maxNodes <= 2_000,
                "Atlas impact node bound is outside 10..2000");
        return List.of(
                "atlas", "recipes", "impact", graph.commandPath(), selectionId,
                "--max-depth", Integer.toString(maxDepth),
                "--max-nodes", Integer.toString(maxNodes), "--json"
        );
    }

    static void validateSearch(
            @NotNull GraphPath graph,
            @NotNull AtlasRecipeSearch search
    ) {
        AtlasRecipeContract.require(
                AtlasRecipeContext.sameRoot(search.context().root(), graph.expectedContextRoot()),
                "Workbench Atlas search changed the explicit graph root"
        );
    }

    static @NotNull List<String> completeImpactArguments(
            @NotNull GraphPath graph, @NotNull String selectionId
    ) {
        AtlasRecipeImpact.requireRecipeId(selectionId);
        return List.of("atlas", "recipes", "impact", graph.commandPath(), selectionId,
                "--exploration", "complete-finite", "--json");
    }

    static void validateCompleteImpact(
            @NotNull GraphPath graph, @NotNull String selectionId,
            @Nullable AtlasRecipeSearch priorSearch, @Nullable AtlasRecipeSearch.Result priorSelection,
            @NotNull AtlasCompleteRecipeImpact impact
    ) {
        AtlasRecipeContract.require(AtlasRecipeContext.sameRoot(impact.context().root(), graph.expectedContextRoot()),
                "Complete Atlas impact changed the explicit graph root");
        AtlasRecipeContract.equal(impact.selectionId(), selectionId,
                "Complete Atlas impact changed the selected recipe");
        if (priorSearch == null && priorSelection == null) return;
        AtlasRecipeContract.require(priorSearch != null && priorSelection != null,
                "Complete Atlas search linkage is incomplete");
        AtlasRecipeContract.equal(priorSelection.selectionId(), selectionId,
                "Complete Atlas selection changed after search");
        AtlasRecipeContract.equal(priorSearch.context().value(), impact.context().value(),
                "Complete Atlas graph context changed after search");
        AtlasRecipeContract.equal(priorSelection.value(), impact.selection(),
                "Complete Atlas recipe changed after search");
    }

    static void validateImpact(
            @NotNull GraphPath graph,
            @NotNull String selectionId,
            @Nullable AtlasRecipeSearch priorSearch,
            @Nullable AtlasRecipeSearch.Result priorSelection,
            @NotNull AtlasRecipeImpact impact
    ) {
        AtlasRecipeContract.require(
                AtlasRecipeContext.sameRoot(impact.context().root(), graph.expectedContextRoot()),
                "Workbench Atlas impact changed the explicit graph root"
        );
        AtlasRecipeContract.equal(
                impact.selectionId(), selectionId,
                "Workbench Atlas impact changed the exact recipe selection"
        );
        if (priorSearch == null && priorSelection == null) {
            return;
        }
        AtlasRecipeContract.require(priorSearch != null && priorSelection != null,
                "Atlas impact search linkage is incomplete");
        AtlasRecipeContract.require(
                priorSelection.selectionId().equals(selectionId),
                "Atlas impact no longer matches the selected search result"
        );
        AtlasRecipeContract.require(
                priorSearch.context().value().equals(impact.context().value()),
                "Atlas impact graph context changed after exact recipe search"
        );
        AtlasRecipeContract.require(
                priorSelection.value().equals(impact.selection()),
                "Atlas impact recipe changed after exact recipe search"
        );
    }

    record GraphPath(
            @NotNull String displayPath,
            @NotNull String commandPath,
            @NotNull String expectedContextRoot
    ) {
    }
}
