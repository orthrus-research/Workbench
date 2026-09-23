package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.vfs.LocalFileSystem;
import com.intellij.openapi.vfs.VirtualFile;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Safe project-bounded navigation for source paths emitted by Recipe Review V2. */
final class RecipeReviewNavigation {
    private RecipeReviewNavigation() {
    }

    static void open(
            @NotNull Project project,
            @NotNull RecipeReview.Source source
    ) throws IOException {
        Path path = resolve(project, source.path());
        VirtualFile file = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(path);
        if (file == null || file.isDirectory()) {
            throw new IOException("Recipe source is not available in this project: " + source.path());
        }
        int line = source.line() == null ? 0 : Math.max(0, source.line() - 1);
        int column = source.column() == null ? 0 : Math.max(0, source.column() - 1);
        FileEditorManager.getInstance(project).openTextEditor(
                new OpenFileDescriptor(project, file, line, column),
                true
        );
    }

    static @NotNull Path resolve(
            @NotNull Project project,
            @NotNull String value
    ) throws IOException {
        String baseValue = project.getBasePath();
        if (baseValue == null) {
            throw new IOException("The IntelliJ project has no local workspace path");
        }
        return resolve(Path.of(baseValue), value);
    }

    static @NotNull Path resolve(
            @NotNull Path baseValue,
            @NotNull String value
    ) throws IOException {
        String normalizedValue = value.replace('\\', '/');
        if (normalizedValue.isBlank() || normalizedValue.startsWith("/")
                || normalizedValue.indexOf('\0') >= 0) {
            throw new IOException("Recipe source path is not project-relative: " + value);
        }
        for (String part : normalizedValue.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                throw new IOException("Recipe source path is not normalized: " + value);
            }
        }
        Path base = baseValue.toAbsolutePath().normalize();
        List<Path> candidates = new ArrayList<>();
        candidates.add(base.resolve(normalizedValue).normalize());
        if (!normalizedValue.startsWith("groovy/")) {
            candidates.add(base.resolve("groovy").resolve(normalizedValue).normalize());
        }
        for (Path candidate : candidates) {
            if (candidate.startsWith(base) && Files.isRegularFile(candidate)) {
                return candidate;
            }
        }
        throw new IOException("Recipe source is not available in this project: " + value);
    }
}
