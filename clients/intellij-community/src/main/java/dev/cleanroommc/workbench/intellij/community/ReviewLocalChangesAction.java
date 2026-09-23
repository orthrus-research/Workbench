package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.diff.DiffContentFactory;
import com.intellij.diff.DiffManager;
import com.intellij.diff.requests.SimpleDiffRequest;
import com.intellij.diff.util.DiffUserDataKeys;
import com.intellij.openapi.actionSystem.*;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.editor.EditorFactory;
import com.intellij.openapi.editor.event.DocumentEvent;
import com.intellij.openapi.editor.event.DocumentListener;
import com.intellij.openapi.editor.impl.DocumentMarkupModel;
import com.intellij.openapi.editor.markup.*;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.fileTypes.FileTypeManager;
import com.intellij.openapi.progress.*;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.util.Key;
import com.intellij.openapi.vfs.*;
import com.intellij.openapi.vfs.newvfs.BulkFileListener;
import com.intellij.openapi.vfs.newvfs.events.VFileEvent;
import com.intellij.testFramework.LightVirtualFile;
import com.intellij.ui.JBColor;
import org.jetbrains.annotations.NotNull;
import java.awt.Font;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** IDE-first local review, exact native diffs, and expiring saved-source diagnostics. */
public final class ReviewLocalChangesAction extends AnAction {
    private static final Key<State> STATE = Key.create("workbench.local.source.review");

    private static final class State {
        final LocalReviewEpoch epoch = new LocalReviewEpoch();
        final List<RangeHighlighter> highlights = new ArrayList<>();
        String session;
        String baseline;
        void invalidate() {
            epoch.invalidate();
            Runnable clear = () -> { for (var mark : highlights) if (mark.isValid()) mark.dispose(); highlights.clear(); };
            if (ApplicationManager.getApplication().isDispatchThread()) clear.run();
            else ApplicationManager.getApplication().invokeLater(clear);
        }
    }

    private static State state(Project project) {
        State existing = project.getUserData(STATE);
        if (existing != null) return existing;
        State state = new State();
        project.putUserData(STATE, state);
        EditorFactory.getInstance().getEventMulticaster().addDocumentListener(new DocumentListener() {
            @Override public void documentChanged(@NotNull DocumentEvent event) {
                var file = FileDocumentManager.getInstance().getFile(event.getDocument());
                if (file != null && file.isInLocalFileSystem()) state.invalidate();
            }
        }, project);
        project.getMessageBus().connect(project).subscribe(VirtualFileManager.VFS_CHANGES, new BulkFileListener() {
            @Override public void after(@NotNull List<? extends VFileEvent> events) {
                if (events.stream().anyMatch(event -> event.getPath().startsWith(project.getBasePath() + "/"))) state.invalidate();
            }
        });
        return state;
    }

    @Override public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null || project.getBasePath() == null || !WorkbenchProjectTrust.require(project, "review saved changes")) return;
        State state = state(project);
        Path workspace = Path.of(project.getBasePath());
        try {
            CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
            if (!launch.host().equals("native")) throw new IllegalArgumentException("Local review requires a native Linux Workbench host.");
            if (state.session != null) {
                int choice = Messages.showYesNoCancelDialog(project, "Review saved changes against the selected exact baseline? Choose No to select another context or baseline.", "Local Source Review", null);
                if (choice == Messages.CANCEL) return;
                if (choice == Messages.NO) { state.session = null; state.baseline = null; }
            }
            if (state.session == null) {
                int choice = Messages.showYesNoCancelDialog(project, "Create a developer context for this workspace? Choose No to use an existing Work Session.", "Local Source Review", null);
                if (choice == Messages.CANCEL) return;
                if (choice == Messages.YES) {
                    String pack = input(project, "Pack profile", "supersymmetry"); if (pack == null) return;
                    String platform = input(project, "Platform profile", "cleanroom"); if (platform == null) return;
                    String variant = input(project, "Profile variant", "cleanroom-provisional"); if (variant == null) return;
                    String baseline = input(project, "Local Git baseline (resolved once; never fetched)", "HEAD"); if (baseline == null) return;
                    long setupTicket = state.epoch.invalidate();
                    background(project, state, indicator -> {
                        String raw = CommandProcess.capture(launch, List.of("context", "select", workspace.toString(), "--pack-profile=" + pack,
                                "--platform-profile=" + platform, "--variant=" + variant), 8 * 1024 * 1024, 120, workspace.toString(), indicator::isCanceled);
                        String session = JsonParser.parseString(raw).getAsJsonObject().get("session_id").getAsString();
                        ApplicationManager.getApplication().invokeLater(() -> {
                            if (!project.isDisposed() && !indicator.isCanceled() && state.epoch.current(setupTicket)) { state.session = session; state.baseline = baseline; review(project, state, launch, workspace); }
                        });
                    });
                    return;
                }
                state.session = input(project, "Exact Work Session ID", "");
                if (state.session == null) return;
                state.baseline = input(project, "Local Git baseline (resolved once; never fetched)", "HEAD");
                if (state.baseline == null) { state.session = null; return; }
            }
            review(project, state, launch, workspace);
        } catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Local review unavailable", error); }
    }

    private static String input(Project project, String title, String initial) {
        String value = Messages.showInputDialog(project, title, "Local Source Review", null, initial, null);
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static void review(Project project, State state, CoreLaunch launch, Path workspace) {
        DeveloperContextSelection.set(project, state.session);
        state.invalidate();
        long ticket = state.epoch.invalidate();
        String session = state.session, baseline = state.baseline;
        background(project, state, indicator -> {
            JsonObject result = LocalReviewClient.invoke(launch, workspace, session, baseline, null,
                    () -> indicator.isCanceled() || !state.epoch.current(ticket));
            ApplicationManager.getApplication().invokeLater(() -> {
                if (project.isDisposed() || indicator.isCanceled() || !state.epoch.current(ticket)) return;
                state.baseline = result.getAsJsonObject("baseline").get("revision").getAsString();
                present(project, state, launch, workspace, result, ticket);
            });
        });
    }

    private static void present(Project project, State state, CoreLaunch launch, Path workspace, JsonObject result, long ticket) {
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        JsonObject report = new JsonObject(); report.addProperty("type", "report");
        choices.add(new CatalogSelectionDialog.Choice<>("Inspect review details and checks", "Saved source only", "Includes recognized changes, declared relationships, and limitations", report));
        for (var value : result.getAsJsonArray("files")) {
            JsonObject row = value.getAsJsonObject().deepCopy(); row.addProperty("type", "file");
            choices.add(new CatalogSelectionDialog.Choice<>(row.get("path").getAsString(), row.get("state").getAsString(), "Exact captured before/after diff", row));
        }
        var relatedLocations = new java.util.LinkedHashMap<String, JsonObject>();
        for (var change : result.getAsJsonArray("changes")) {
            var row = change.getAsJsonObject().get("after");
            if (!row.isJsonNull()) relatedLocations.put(row.getAsJsonObject().get("selection_id").getAsString(), row.getAsJsonObject());
        }
        for (var graph : result.getAsJsonArray("relationships")) {
            for (var value : graph.getAsJsonObject().getAsJsonArray("nodes")) {
                var row = value.getAsJsonObject();
                if (!row.get("location").isJsonNull()) relatedLocations.put(row.get("selection_id").getAsString(), row);
            }
        }
        for (var row : relatedLocations.values()) {
            var choice = row.deepCopy(); choice.addProperty("type", "location");
            choices.add(new CatalogSelectionDialog.Choice<>(row.get("label").getAsString(), "Changed or related " + row.get("kind").getAsString(), row.getAsJsonObject("location").get("path").getAsString(), choice));
        }
        for (var value : result.getAsJsonArray("findings")) {
            JsonObject row = value.getAsJsonObject();
            if (row.get("state").getAsString().equals("resolved") || row.get("location").isJsonNull()) continue;
            JsonObject choice = row.deepCopy(); choice.addProperty("type", "finding");
            choices.add(new CatalogSelectionDialog.Choice<>(row.get("message").getAsString(), row.get("state").getAsString(), row.getAsJsonObject("location").get("path").getAsString(), choice));
            try {
                var target = SourceNavigationClient.verify(workspace, row.getAsJsonObject("location"));
                var file = LocalFileSystem.getInstance().findFileByNioFile(target.path());
                var document = file == null ? null : FileDocumentManager.getInstance().getDocument(file);
                if (document == null || FileDocumentManager.getInstance().isDocumentUnsaved(document) || !document.getText().equals(target.text())) continue;
                JsonObject first = row.getAsJsonObject("location").getAsJsonObject("start"), last = row.getAsJsonObject("location").getAsJsonObject("end");
                int start = document.getLineStartOffset(first.get("line").getAsInt() - 1) + first.get("column").getAsInt() - 1;
                int end = document.getLineStartOffset(last.get("line").getAsInt() - 1) + last.get("column").getAsInt() - 1;
                var mark = DocumentMarkupModel.forDocument(document, project, true).addRangeHighlighter(start, end, HighlighterLayer.WARNING,
                        new TextAttributes(null, null, JBColor.ORANGE, EffectType.WAVE_UNDERSCORE, Font.PLAIN), HighlighterTargetArea.EXACT_RANGE);
                mark.setErrorStripeTooltip("Workbench saved-source review: " + row.get("message").getAsString());
                state.highlights.add(mark);
            } catch (Exception ignored) { /* Never annotate different or unsaved bytes. */ }
        }
        var picker = new CatalogSelectionDialog<>(project, "Review Saved Local Changes",
                result.getAsJsonObject("counts").get("files").getAsInt() + " changed files. Source interpretation is not compilation or runtime proof. Further edits invalidate this review.", choices);
        if (!picker.showAndGet() || !state.epoch.current(ticket)) return;
        JsonObject selected = picker.selected();
        try {
            switch (selected.get("type").getAsString()) {
                case "report" -> {
                    var file = new LightVirtualFile("Workbench saved-source review.json", new GsonBuilder().setPrettyPrinting().create().toJson(result));
                    file.setWritable(false);
                    FileEditorManager.getInstance(project).openFile(file, true);
                }
                case "file" -> {
                    for (String side : List.of("before", "after")) {
                        if (!List.of("included", "absent").contains(selected.get(side + "_text_state").getAsString())) {
                            Messages.showInfoMessage(project, "A text diff is unavailable for this binary or over-bound file. Exact hashes remain in review details.", "Local Source Review");
                            return;
                        }
                    }
                    String path = selected.get("path").getAsString();
                    var type = FileTypeManager.getInstance().getFileTypeByFileName(path);
                    var factory = DiffContentFactory.getInstance();
                    var before = factory.create(project, selected.get("before_text").isJsonNull() ? "" : selected.get("before_text").getAsString(), type);
                    var after = factory.create(project, selected.get("after_text").isJsonNull() ? "" : selected.get("after_text").getAsString(), type);
                    var request = new SimpleDiffRequest(path + " — captured saved changes (read-only)", before, after, "Exact baseline", "Saved candidate");
                    request.putUserData(DiffUserDataKeys.FORCE_READ_ONLY, true);
                    DiffManager.getInstance().showDiff(project, request);
                }
                default -> background(project, state, indicator -> {
                    LocalReviewClient.invoke(launch, workspace, state.session, state.baseline, result.get("review_id").getAsString(), () -> indicator.isCanceled() || !state.epoch.current(ticket));
                    var location = selected.getAsJsonObject("location");
                    SourceNavigationClient.verify(workspace, location);
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (project.isDisposed() || indicator.isCanceled() || !state.epoch.current(ticket)) return;
                        try {
                            var target = SourceNavigationClient.verify(workspace, location);
                            var file = LocalFileSystem.getInstance().findFileByNioFile(target.path());
                            var document = file == null ? null : FileDocumentManager.getInstance().getDocument(file);
                            if (document == null || FileDocumentManager.getInstance().isDocumentUnsaved(document) || !document.getText().equals(target.text())) throw new IllegalArgumentException("Save and review again; editor bytes changed.");
                            var point = location.getAsJsonObject("start");
                            FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, point.get("line").getAsInt() - 1, point.get("column").getAsInt() - 1), true);
                        } catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Review location unavailable", error); }
                    });
                });
            }
        } catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Review view unavailable", error); }
    }

    @FunctionalInterface private interface Work { void run(ProgressIndicator indicator) throws Exception; }
    private static void background(Project project, State state, Work work) {
        new Task.Backgroundable(project, "Workbench saved-source review", true) {
            @Override public void run(@NotNull ProgressIndicator indicator) {
                try { work.run(indicator); }
                catch (ProcessCanceledException ignored) { }
                catch (Exception error) { ApplicationManager.getApplication().invokeLater(() -> {
                    if (!project.isDisposed() && !indicator.isCanceled()) WorkbenchNotifications.commandFailed(project, "Local review unavailable", error);
                }); }
            }
        }.queue();
    }
    @Override public void update(@NotNull AnActionEvent event) {
        event.getPresentation().setEnabledAndVisible(event.getProject() != null && WorkbenchProjectTrust.isTrusted(event.getProject()));
    }
    @Override public @NotNull ActionUpdateThread getActionUpdateThread() { return ActionUpdateThread.BGT; }
}
