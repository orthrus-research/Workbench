package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class RecipeReviewNavigationTest {
    @TempDir
    Path root;

    @Test
    void resolvesOwnerSourcePathsWithoutLeavingTheProject() throws IOException {
        Path recipe = root.resolve("groovy/postInit/Recipes.groovy");
        Files.createDirectories(recipe.getParent());
        Files.writeString(recipe, "MIXER.recipeBuilder()");
        assertEquals(
                recipe,
                RecipeReviewNavigation.resolve(root, "postInit/Recipes.groovy")
        );
        assertThrows(
                IOException.class,
                () -> RecipeReviewNavigation.resolve(root, "../outside.groovy")
        );
        assertThrows(
                IOException.class,
                () -> RecipeReviewNavigation.resolve(root, recipe.toString())
        );
    }
}
