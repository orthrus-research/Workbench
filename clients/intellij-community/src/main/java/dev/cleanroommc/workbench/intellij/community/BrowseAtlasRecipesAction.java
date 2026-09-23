package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.intellij.ide.util.PropertiesComponent;
import com.intellij.openapi.actionSystem.*;
import com.intellij.openapi.progress.ProgressManager;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import org.jetbrains.annotations.NotNull;
import java.util.ArrayList;
import java.util.List;
import java.util.HashSet;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.Callable;
import static dev.cleanroommc.workbench.intellij.community.AtlasRecipeContract.*;

/** Native navigation of exact observed nodes; all relationship meaning is Atlas-owned. */
public final class BrowseAtlasRecipesAction extends AnAction {
    private static final String SAVED = "workbench.atlas.lastBrowse";
    private record Position(String id, int offset) { }

    private static <T> T call(Project project, Callable<T> operation) {
        var result = new AtomicReference<T>();
        var failure = new AtomicReference<Exception>();
        boolean finished = ProgressManager.getInstance().runProcessWithProgressSynchronously(() -> {
            try {
                result.set(operation.call());
            } catch (Exception error) { failure.set(error); }
        }, "Reading Atlas evidence", true, project);
        if (!finished) {
            if (result.get() instanceof AutoCloseable resource) {
                try { resource.close(); } catch (Exception ignored) { }
            }
            return null;
        }
        if (failure.get() != null) throw new IllegalArgumentException(failure.get().getMessage(), failure.get());
        return result.get();
    }

    @Override public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null || !WorkbenchProjectTrust.require(project, "browse recipe evidence")) return;
        try { browse(project); }
        catch (IllegalArgumentException error) { Messages.showErrorDialog(project, error.getMessage(), "Atlas Evidence Unavailable"); }
    }

    private void browse(Project project) {
        AtlasRecipeSession session = null;
        try {
        CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
        var state = PropertiesComponent.getInstance(project);
        String saved = state.getValue(SAVED);
        int mode = Messages.showChooseDialog(project, "Choose evidence to explore", "Atlas Recipe Evidence", null,
                saved == null ? new String[]{"Open captured recipe graph"} : new String[]{"Open captured recipe graph", "Reopen last selection", "Clear saved Atlas selection"},
                "Open captured recipe graph");
        if (mode < 0) return;
        if (mode == 2) { state.unsetValue(SAVED); return; }
        String root, graph, selection;
        JsonObject expected = null;
        if (mode == 1) {
            JsonObject previous = parseRoot(saved, "saved Atlas location");
            root = string(previous, "root"); graph = string(previous, "graph"); selection = string(previous, "selected");
        } else {
            root = Messages.showInputDialog(project, "Captured Atlas graph directory", "Atlas Recipe Evidence", null);
            if (root == null) return;
            var path = AtlasRecipeClient.graphPath(launch, root);
            root = path.displayPath();
            String query = Messages.showInputDialog(project, "Item, fluid, recipe or machine name", "Search Atlas Recipes", null);
            if (query == null || query.isBlank()) return;
            session = call(project, () -> new AtlasRecipeSession(launch, path, project.getBasePath(), BrowseAtlasRecipesAction::cancelled));
            if (session == null) return;
            var active = session;
            JsonObject arguments = new JsonObject(); arguments.addProperty("query", query); arguments.addProperty("limit", 50);
            String raw = call(project, () -> active.request("search", arguments, BrowseAtlasRecipesAction::cancelled));
            if (raw == null) return;
            var search = AtlasRecipeSearch.parse(raw, query, 50);
            AtlasRecipeClient.validateSearch(path, search);
            var found = parseRoot(raw, "Atlas search");
            var results = array(found, "results");
            if (results.isEmpty()) { Messages.showInfoMessage(project, "No matching evidence. Try another name or identifier.", "Atlas"); return; }
            List<String> labels = new ArrayList<>();
            for (var value : results) {
                var row = AtlasRecipeBrowse.node(value.getAsJsonObject());
                labels.add(AtlasRecipeBrowse.nodeLabel(row) + " [" + string(row, "kind") + "] · " + string(row, "semantic_key"));
            }
            int choice = choose(project, "Select evidence" + (search.truncated() ? " — first 50 matches; refine search for others" : ""), labels);
            if (choice < 0) return;
            expected = results.get(choice).getAsJsonObject();
            graph = search.context().graphSetId(); selection = string(expected, "selection_id");
        }
        var path = AtlasRecipeClient.graphPath(launch, root);
        if (session == null) session = call(project, () -> new AtlasRecipeSession(launch, path, project.getBasePath(), BrowseAtlasRecipesAction::cancelled));
        if (session == null) return;
        equal(session.graph(), graph, "Atlas graph changed; reopen and search again");
        var active = session;
        List<Position> history = new ArrayList<>();
        int offset = 0;
        while (!project.isDisposed()) {
            JsonObject arguments = new JsonObject(); arguments.addProperty("selection_id", selection);
            arguments.addProperty("offset", offset); arguments.addProperty("limit", 50);
            String raw = call(project, () -> active.request("browse", arguments, BrowseAtlasRecipesAction::cancelled));
            if (raw == null) return;
            JsonObject page = AtlasRecipeBrowse.parse(raw, path.expectedContextRoot(), graph, selection, offset);
            if (expected != null) require(expected.equals(page.getAsJsonObject("selection")), "Atlas selection changed since search");
            expected = null;
            JsonObject remembered = new JsonObject();
            remembered.addProperty("root", root); remembered.addProperty("graph", graph); remembered.addProperty("selected", selection);
            state.setValue(SAVED, remembered.toString());
            var paging = page.getAsJsonObject("page");
            var links = array(page, "links");
            var visited = new HashSet<String>(); visited.add(selection);
            for (var position : history) visited.add(position.id());
            var choices = AtlasRecipeBrowse.choices(page, visited, !history.isEmpty());
            String title = AtlasRecipeBrowse.nodeLabel(page.getAsJsonObject("selection")) + " — " + (offset + links.size()) + "/" + integer(paging, "total") + " relationships";
            int choice = choose(project, title, choices.stream().map(AtlasRecipeBrowse.Choice::label).toList());
            if (choice < 0) return;
            var chosen = choices.get(choice);
            if (chosen.action().equals("values")) { new AtlasRecipeImpactJsonDialog(project, new GsonBuilder().setPrettyPrinting().create().toJson(page), "Atlas Captured Values and Relationship Page").show(); continue; }
            if (chosen.action().equals("back")) { var previous = history.removeLast(); selection = previous.id(); offset = previous.offset(); continue; }
            if (chosen.action().equals("previous")) { offset = Math.max(0, offset - 50); continue; }
            if (chosen.action().equals("next")) { offset = integer(paging, "next_offset"); continue; }
            history.add(new Position(selection, offset));
            expected = chosen.node();
            selection = string(expected, "selection_id"); offset = 0;
        }
        } finally { if (session != null) session.close(); }
    }

    private static boolean cancelled() { return ProgressManager.getInstance().getProgressIndicator().isCanceled(); }

    private static int choose(Project project, String title, List<String> choices) {
        return Messages.showChooseDialog(project, "Observed relationships only. Craftability and complete route remain unknown.", title, null,
                choices.toArray(String[]::new), choices.getFirst());
    }

    @Override public void update(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        event.getPresentation().setEnabled(project != null && WorkbenchProjectTrust.isTrusted(project));
    }
    @Override public @NotNull ActionUpdateThread getActionUpdateThread() { return ActionUpdateThread.BGT; }
}
