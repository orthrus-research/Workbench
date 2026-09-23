package dev.cleanroommc.workbench.intellij.community;

import com.intellij.diff.DiffContentFactory;
import com.intellij.diff.DiffManager;
import com.intellij.diff.contents.DiffContent;
import com.intellij.diff.requests.SimpleDiffRequest;
import com.intellij.openapi.fileTypes.FileType;
import com.intellij.openapi.fileTypes.FileTypeManager;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.io.IOException;
import java.util.List;

/** Opens owner-projected recipe property changes in IntelliJ's native diff viewer. */
final class RecipeReviewDiffOpener {
    private RecipeReviewDiffOpener() {
    }

    static void open(
            @NotNull Project project,
            @NotNull RecipeReview.Modification modification
    ) throws IOException {
        FileType type = FileTypeManager.getInstance().getFileTypeByFileName(
                "recipe-properties.txt"
        );
        DiffContentFactory factory = DiffContentFactory.getInstance();
        DiffContent before = factory.createFromBytes(
                project,
                render(modification, true).getBytes(StandardCharsets.UTF_8),
                type,
                "recipe-properties-before.txt"
        );
        DiffContent after = factory.createFromBytes(
                project,
                render(modification, false).getBytes(StandardCharsets.UTF_8),
                type,
                "recipe-properties-after.txt"
        );
        DiffManager.getInstance().showDiff(
                project,
                new SimpleDiffRequest(
                        "Workbench · " + modification.recipeMap() + " recipe properties",
                        before,
                        after,
                        "Before · " + source(modification.beforeSource()),
                        "After · " + source(modification.afterSource())
                )
        );
    }

    static @NotNull String render(
            @NotNull RecipeReview.Modification modification,
            boolean before
    ) {
        StringBuilder output = new StringBuilder();
        RecipeReview.Source source = before
                ? modification.beforeSource() : modification.afterSource();
        output.append("Recipe map: ").append(modification.recipeMap()).append('\n');
        output.append("Source: ").append(source(source)).append('\n');
        output.append("Semantic key: ").append(
                before ? modification.beforeSemanticKey() : modification.afterSemanticKey()
        ).append("\n\n");
        for (RecipeReview.PropertyChange change : modification.propertyChanges()) {
            output.append(change.name()).append(":\n");
            List<String> values = before ? change.before() : change.after();
            if (values == null) {
                output.append("  <not set>\n");
            } else if (values.isEmpty()) {
                output.append("  <empty>\n");
            } else {
                values.forEach(value -> output.append("  ").append(value).append('\n'));
            }
            output.append('\n');
        }
        if (modification.propertiesTruncated()) {
            output.append("Property projection is bounded; use the complete owner report.\n");
        }
        return output.toString();
    }

    private static @NotNull String source(@NotNull RecipeReview.Source source) {
        return source.path() + (source.line() == null ? "" : ":" + source.line());
    }
}
