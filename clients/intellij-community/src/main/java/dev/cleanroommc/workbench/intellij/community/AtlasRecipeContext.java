package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;

import java.util.Set;
import java.io.IOException;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.nio.file.Files;

/** Exact verified categorical-graph context embedded in search and impact V1. */
final class AtlasRecipeContext {
    static final String FORMAT = "workbench-atlas-recipe-health-context-v1";

    static boolean sameRoot(String actual, String expected) {
        if (actual.equals(expected)) return true;
        try {
            Path left = Path.of(actual), right = Path.of(expected);
            // Remote Linux roots are not absolute native Windows paths.
            // Require existing directories and compare canonical names exactly.
            // toRealPath may need ancestor enumeration unavailable on Windows.
            return left.isAbsolute() && right.isAbsolute()
                    && Files.isDirectory(left) && Files.isDirectory(right)
                    && left.toFile().getCanonicalPath().equals(right.toFile().getCanonicalPath());
        } catch (IOException | InvalidPathException error) { return false; }
    }

    private final JsonObject value;
    private final String root;
    private final String graphSetId;
    private final int recipeCount;

    private AtlasRecipeContext(
            @NotNull JsonObject value,
            @NotNull String root,
            @NotNull String graphSetId,
            int recipeCount
    ) {
        this.value = value.deepCopy();
        this.root = root;
        this.graphSetId = graphSetId;
        this.recipeCount = recipeCount;
    }

    static @NotNull AtlasRecipeContext parse(@NotNull JsonObject value) {
        AtlasRecipeContract.exactKeys(value, Set.of(
                "capabilities", "context_type", "format", "graph_set_id", "recipe_count",
                "root", "schema_version", "scope", "search", "summary"
        ), "Atlas recipe context");
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(value, "format"), FORMAT,
                "Workbench returned an unsupported Atlas recipe context"
        );
        AtlasRecipeContract.require(
                AtlasRecipeContract.integer(value, "schema_version") == 1,
                "Workbench returned an unsupported Atlas recipe context schema"
        );
        AtlasRecipeContract.equal(
                AtlasRecipeContract.string(value, "context_type"), "categorical-graph-v2",
                "Recipe impact requires one explicit verified categorical graph"
        );
        String root = AtlasRecipeContract.string(value, "root");
        String graphSetId = AtlasRecipeContract.graphSetId(value, "graph_set_id");
        int recipeCount = AtlasRecipeContract.nonnegativeInteger(value, "recipe_count");
        AtlasRecipeContract.object(
                AtlasRecipeContract.required(value, "scope"), "Atlas graph scope"
        );
        AtlasRecipeContract.object(
                AtlasRecipeContract.required(value, "summary"), "Atlas graph summary"
        );

        JsonObject capabilities = AtlasRecipeContract.object(
                AtlasRecipeContract.required(value, "capabilities"), "Atlas capabilities"
        );
        AtlasRecipeContract.exactKeys(capabilities, Set.of(
                "consumers", "duplicate_signatures", "producers", "reachability",
                "recipe_search", "source_mutations", "stoichiometry"
        ), "Atlas recipe capabilities");
        AtlasRecipeContract.require(
                AtlasRecipeContract.bool(capabilities, "recipe_search") && recipeCount > 0,
                "The selected graph publishes no finite recipe search capability"
        );
        AtlasRecipeContract.bool(capabilities, "duplicate_signatures");
        AtlasRecipeContract.bool(capabilities, "producers");
        AtlasRecipeContract.bool(capabilities, "consumers");
        AtlasRecipeContract.bool(capabilities, "source_mutations");
        AtlasRecipeContract.require(
                !AtlasRecipeContract.bool(capabilities, "stoichiometry")
                        && !AtlasRecipeContract.bool(capabilities, "reachability"),
                "Atlas recipe V1 capability semantics changed"
        );

        JsonObject search = AtlasRecipeContract.object(
                AtlasRecipeContract.required(value, "search"), "Atlas search contract"
        );
        AtlasRecipeContract.exactKeys(search, Set.of(
                "requires_semantic_key", "selection_is_exact_node_id"
        ), "Atlas search contract");
        AtlasRecipeContract.require(
                !AtlasRecipeContract.bool(search, "requires_semantic_key")
                        && AtlasRecipeContract.bool(search, "selection_is_exact_node_id"),
                "Atlas recipe search selection semantics changed"
        );
        return new AtlasRecipeContext(value, root, graphSetId, recipeCount);
    }

    @NotNull JsonObject value() {
        return value.deepCopy();
    }

    @NotNull String root() {
        return root;
    }

    @NotNull String graphSetId() {
        return graphSetId;
    }

    int recipeCount() {
        return recipeCount;
    }
}
