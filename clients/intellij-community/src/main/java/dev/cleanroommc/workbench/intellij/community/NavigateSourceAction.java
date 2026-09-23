package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.vfs.LocalFileSystem;
import org.jetbrains.annotations.NotNull;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Minimal shared-context search picker and native editor reveal. */
public final class NavigateSourceAction extends AnAction {
    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null || project.getBasePath() == null) return;
        String session = Messages.showInputDialog(project, "Exact Work Session ID from workbench context select", "Source Navigation", null);
        if (session == null || session.isBlank()) return;
        String query = Messages.showInputDialog(project, "Recipe, material, or quest name (source only)", "Find Source Declaration", null);
        if (query == null) return;
        Path workspace = Path.of(project.getBasePath());
        background(project, () -> {
            CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
            JsonObject result = SourceNavigationClient.invoke(launch, workspace, session, List.of("search", "--", query));
            List<CatalogSelectionDialog.Choice<JsonObject>> choices = new ArrayList<>();
            for (var value : result.getAsJsonArray("results")) {
                JsonObject row = value.getAsJsonObject();
                if (row.get("location").isJsonNull()) continue;
                choices.add(new CatalogSelectionDialog.Choice<>(row.get("label").getAsString(), row.get("kind").getAsString(), row.getAsJsonObject("location").get("path").getAsString(), row));
            }
            ApplicationManager.getApplication().invokeLater(() -> {
                if (project.isDisposed()) return;
                var picker = new CatalogSelectionDialog<>(project, "Open Source Declaration",
                        result.get("truncated").getAsBoolean() ? "Results bounded; narrow your query if needed." : "Source declarations, not observed runtime state.", choices);
                if (!picker.showAndGet()) return;
                String selected = picker.selected().get("selection_id").getAsString();
                background(project, () -> {
                    JsonObject fresh = SourceNavigationClient.invoke(launch, workspace, session, List.of("location", selected));
                    if (!fresh.get("selection_id").getAsString().equals(selected)) throw new IllegalArgumentException("Source location changed selection.");
                    JsonObject location = fresh.getAsJsonObject("location");
                    SourceNavigationClient.verify(workspace, location);
                    ApplicationManager.getApplication().invokeLater(() -> reveal(project, workspace, location));
                });
            });
        });
    }

    private static void reveal(Project project, Path workspace, JsonObject location) {
        if (project.isDisposed()) return;
        try {
            var target = SourceNavigationClient.verify(workspace, location);
            var file = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(target.path());
            if (file == null) throw new IllegalArgumentException("Selected source file disappeared.");
            var manager = FileDocumentManager.getInstance();
            var document = manager.getDocument(file);
            if (document == null || manager.isDocumentUnsaved(document) || !document.getText().equals(target.text())) {
                throw new IllegalArgumentException("Editor buffer differs from selected source; save and search again.");
            }
            JsonObject start = location.getAsJsonObject("start");
            JsonObject end = location.getAsJsonObject("end");
            int first = document.getLineStartOffset(start.get("line").getAsInt() - 1) + start.get("column").getAsInt() - 1;
            int last = document.getLineStartOffset(end.get("line").getAsInt() - 1) + end.get("column").getAsInt() - 1;
            var editor = FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, first), true);
            if (editor != null) editor.getSelectionModel().setSelection(first, last);
        } catch (Exception error) {
            WorkbenchNotifications.commandFailed(project, "Could not open source declaration", error);
        }
    }

    @FunctionalInterface private interface Checked { void run() throws Exception; }
    private static void background(Project project, Checked work) {
        new Task.Backgroundable(project, "Workbench source navigation", false) {
            @Override public void run(@NotNull ProgressIndicator indicator) {
                try { work.run(); }
                catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (!project.isDisposed()) WorkbenchNotifications.commandFailed(project, "Source navigation unavailable", error);
                    });
                }
            }
        }.queue();
    }

    @Override public void update(@NotNull AnActionEvent event) {
        event.getPresentation().setEnabledAndVisible(event.getProject() != null);
    }
    @Override public @NotNull ActionUpdateThread getActionUpdateThread() { return ActionUpdateThread.BGT; }
}
