package dev.cleanroommc.workbench.intellij.community;

import com.intellij.diff.DiffContentFactory;
import com.intellij.diff.DiffManager;
import com.intellij.diff.contents.DiffContent;
import com.intellij.diff.requests.SimpleDiffRequest;
import com.intellij.openapi.fileTypes.FileType;
import com.intellij.openapi.fileTypes.FileTypeManager;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;

/** Opens verified before/after owner bytes through the Community Platform diff API. */
final class FeatureDiffOpener {
    private FeatureDiffOpener() {
    }

    static void open(
            @NotNull Project project,
            @NotNull FeaturePresentation.Operation operation
    ) throws IOException {
        String fileName = displayName(operation.path());
        FileType fileType = FileTypeManager.getInstance().getFileTypeByFileName(fileName);
        DiffContentFactory factory = DiffContentFactory.getInstance();
        DiffContent before = factory.createFromBytes(
                project, operation.beforeBytes(), fileType, fileName
        );
        DiffContent after = factory.createFromBytes(
                project, operation.afterBytes(), fileType, fileName
        );
        DiffManager.getInstance().showDiff(
                project,
                new SimpleDiffRequest(
                        "Workbench · " + operation.path(),
                        before,
                        after,
                        "Before · " + operation.beforeSha256(),
                        "After · " + operation.afterSha256()
                )
        );
    }

    static @NotNull String displayName(@NotNull String path) {
        String normalized = path.replace('\\', '/');
        int separator = normalized.lastIndexOf('/');
        String name = separator < 0 ? normalized : normalized.substring(separator + 1);
        return name.isBlank() ? "workbench-record" : name;
    }
}
