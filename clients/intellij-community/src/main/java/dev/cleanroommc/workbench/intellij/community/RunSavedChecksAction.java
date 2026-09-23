package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.openapi.actionSystem.*;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.editor.EditorFactory;
import com.intellij.openapi.editor.event.DocumentEvent;
import com.intellij.openapi.editor.event.DocumentListener;
import com.intellij.openapi.editor.impl.DocumentMarkupModel;
import com.intellij.openapi.editor.markup.*;
import com.intellij.openapi.fileEditor.*;
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
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;

/** Explicit saved-candidate execution with native diagnostics and owner cancellation. */
public final class RunSavedChecksAction extends AnAction {
    private static final Key<State> STATE = Key.create("workbench.saved.checks");
    static final class State {
        volatile boolean active;
        volatile long generation, edits;
        String lastAttempt;
        String materialView;
        boolean materialExecution;
        MaterialResultsPanel materialResults;
        final List<RangeHighlighter> marks = new ArrayList<>();
        void clear() { for (var mark : marks) if (mark.isValid()) mark.dispose(); marks.clear(); }
    }
    static State state(Project project) {
        var value = project.getUserData(STATE);
        if (value != null) return value;
        var created = new State(); project.putUserData(STATE, created);
        EditorFactory.getInstance().getEventMulticaster().addDocumentListener(new DocumentListener() {
            @Override public void documentChanged(@NotNull DocumentEvent event) { created.edits++; created.clear(); if (created.materialResults != null) created.materialResults.invalidateSource(); }
        }, project);
        project.getMessageBus().connect(project).subscribe(VirtualFileManager.VFS_CHANGES, new BulkFileListener() {
            @Override public void after(@NotNull List<? extends VFileEvent> events) {
                if (events.stream().anyMatch(value -> value.getPath().startsWith(project.getBasePath() + "/"))) {
                    ui(project, () -> { created.edits++; created.clear(); if (created.materialResults != null) created.materialResults.invalidateSource(); });
                }
            }
        });
        return created;
    }
    private static String input(Project project, String title, String value) {
        return input(project, title, value, false);
    }
    private static String input(Project project, String title, String value, boolean allowEmpty) {
        var result = Messages.showInputDialog(project, title, "Saved Developer Checks", null, value, null);
        return result == null || (!allowEmpty && result.isBlank()) ? null : result.trim();
    }
    @Override public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null || project.getBasePath() == null || !WorkbenchProjectTrust.require(project, "execute saved developer checks")) return;
        State state = state(project);
        if (state.active || state.materialExecution) { Messages.showInfoMessage(project, "A check is active. Use its progress cancellation control.", "Saved Checks"); return; }
        try {
            Path root = Path.of(project.getBasePath());
            CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
            if (!launch.host().equals("native")) throw new IllegalArgumentException("Saved checks require a native Linux host.");
            String session = DeveloperContextSelection.get(project);
            int choice = Messages.showYesNoCancelDialog(project, session == null ? "Create a developer context for this workspace? No selects an existing Work Session." : "Use the selected Work Session? No selects another session.", "Saved Checks", null);
            if (choice == Messages.CANCEL) return;
            if (choice == Messages.NO) {
                session = input(project, "Exact Work Session ID", session == null ? "" : session); if (session == null) return;
            } else if (session == null) {
                String pack = input(project, "Pack profile", "supersymmetry"); if (pack == null) return;
                String platform = input(project, "Platform profile", "cleanroom"); if (platform == null) return;
                String variant = input(project, "Profile variant", "cleanroom-provisional"); if (variant == null) return;
                background(project, state, indicator -> {
                    String raw = CommandProcess.capture(launch, List.of("context", "select", root.toString(), "--pack-profile=" + pack, "--platform-profile=" + platform, "--variant=" + variant), 8 * 1024 * 1024, 120, root.toString());
                    String selected = JsonParser.parseString(raw).getAsJsonObject().get("session_id").getAsString();
                    ui(project, () -> { DeveloperContextSelection.set(project, selected); choose(project, state, launch, root, selected); });
                });
                return;
            }
            DeveloperContextSelection.set(project, session);
            state.materialView = null; state.clear();
            choose(project, state, launch, root, session);
        } catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Saved check unavailable", error); }
    }
    private static void choose(Project project, State state, CoreLaunch launch, Path root, String session) {
        var mode = new CatalogSelectionDialog<>(project, "Saved Checks", "Developer-authored saved changes", List.of(
                new CatalogSelectionDialog.Choice<>("Run startup diagnostics", "saved source", "Explicit runtime consent follows", "startup"),
                new CatalogSelectionDialog.Choice<>("Run a recipe-specific check", "expected versus observed", "Select a source-bound recipe expectation", "recipe"),
                new CatalogSelectionDialog.Choice<>("Run Axiom material preflight", "no game image", "Complete saved program; native validity qualification remains pending", "material"),
                new CatalogSelectionDialog.Choice<>("Configure and run Axiom material preflight", "Core-owned saved setup", "Explicitly rebind native inputs or change optional expectations", "material-setup"),
                new CatalogSelectionDialog.Choice<>("Reopen an Axiom material check", "retained source and native evidence", "No automatic rerun", "material-reopen"),
                new CatalogSelectionDialog.Choice<>("Reopen an existing attempt", "retained evidence", "No runtime launch", "reopen")));
        if (!mode.showAndGet()) return;
        if (mode.selected().equals("material")) { materialPrepare(project, state, launch, root, session, null); return; }
        if (mode.selected().equals("material-setup")) { materialPrepare(project, state, launch, root, session, null, true); return; }
        if (mode.selected().equals("material-reopen")) { materialReopen(project, state, launch, root, session); return; }
        if (mode.selected().equals("reopen")) {
            String attempt = input(project, "Exact check attempt ID", state.lastAttempt == null ? "" : state.lastAttempt);
            if (attempt != null) background(project, state, indicator -> {
                long edits = state.edits;
                var result = DeveloperChecksClient.invoke(launch, root, session, "show", attempt, null);
                ui(project, () -> present(project, state, launch, root, session, result, edits));
            });
            return;
        }
        background(project, state, indicator -> {
            var images = DeveloperChecksClient.invoke(launch, root, session, "images", null, null).getAsJsonArray("images");
            var options = new ArrayList<CatalogSelectionDialog.Choice<String>>();
            for (var value : images) {
                var image = value.getAsJsonObject(); String id = image.get("id").getAsString();
                options.add(new CatalogSelectionDialog.Choice<>(id, "installed", image.getAsJsonObject("binding").toString(), id));
            }
            options.add(new CatalogSelectionDialog.Choice<>("Prepare with Packwiz and Prism", "no game execution", "Resolve a private, dependency-bound offline check image", "prepare-environment"));
            options.add(new CatalogSelectionDialog.Choice<>("Reopen environment preparation", "retained", "Inspect, cancel or recover a preparation attempt", "environment-show"));
            options.add(new CatalogSelectionDialog.Choice<>("Import a native runtime image (advanced)", "local copy", "Import a manually installed self-contained client", ""));
            ui(project, () -> {
                var dialog = new CatalogSelectionDialog<>(project, "Runtime Image", "Choose one exact installed runtime image", options);
                if (!dialog.showAndGet()) return;
                if (dialog.selected().isEmpty()) { importImage(project, state, launch, root, session); return; }
                if (dialog.selected().equals("prepare-environment")) { prepareEnvironment(project, state, launch, root, session); return; }
                if (dialog.selected().equals("environment-show")) { reopenEnvironment(project, state, launch, root, session); return; }
                if (mode.selected().equals("recipe")) { selectRecipe(project, state, launch, root, session, dialog.selected()); return; }
                background(project, state, preparation -> {
                    var request = DeveloperChecksClient.invoke(launch, root, session, "prepare", dialog.selected(), null);
                    ui(project, () -> confirm(project, state, launch, root, session, request));
                });
            });
        });
    }
    private static void materialPrepare(Project project, State state, CoreLaunch launch, Path root, String session, String baseline) {
        materialPrepare(project, state, launch, root, session, baseline, false);
    }
    private static void materialPrepare(Project project, State state, CoreLaunch launch, Path root, String session, String baseline, boolean reconfigure) {
        background(project, state, indicator -> {
            var catalog = MaterialChecksClient.invoke(launch, root, session, "contexts", null, null, null);
            var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
            for (var value : catalog.getAsJsonObject("policy").getAsJsonArray("contexts")) {
                var context = value.getAsJsonObject();
                choices.add(new CatalogSelectionDialog.Choice<>(context.get("label").getAsString(), context.get("qualification").getAsString(),
                        "Complete explicit program. Excludes: " + context.get("excludedComposition"), context));
            }
            ui(project, () -> {
                var storage = MaterialChecksClient.object(catalog, "storage");
                if (storage.has("before_work_notice") && !storage.get("before_work_notice").isJsonNull()) {
                    int next = Messages.showDialog(project, storage.get("before_work_notice").getAsString(), "Check Storage Before Capture",
                            new String[]{"Review storage and exports", "Continue with complete capture", "Cancel"}, 0, null);
                    if (next == 0) { materialRetention(project, state, launch, root, session); return; }
                    if (next != 1) return;
                }
                var dialog = new CatalogSelectionDialog<>(project, "Axiom Material Context", "No qualified validity claim", choices);
                if (!dialog.showAndGet()) return;
                var options = new JsonObject();
                options.addProperty("context", dialog.selected().get("id").getAsString());
                if (baseline != null) options.addProperty("baseline", baseline);
                background(project, state, statusCheck -> {
                    var status = MaterialChecksClient.invoke(launch, root, session, "setup-status", null, null, options);
                    ui(project, () -> materialSetup(project, state, launch, root, session, options, status, reconfigure));
                });
            });
        });
    }
    private static void materialSetup(Project project, State state, CoreLaunch launch, Path root, String session,
                                      JsonObject options, JsonObject status, boolean reconfigure) {
        var properties = com.intellij.ide.util.PropertiesComponent.getInstance(project);
        String key = "workbench.material." + options.get("context").getAsString() + ".";
        if (!reconfigure && MaterialChecksClient.text(status, "state", "").equals("ready")) {
            String intent = properties.getValue(key + "intent", "");
            if (!intent.isEmpty()) options.addProperty("intent", intent);
            materialPrepareConfigured(project, state, launch, root, session, options);
            return;
        }
        if (MaterialChecksClient.text(status, "state", "").equals("stale")) {
            Messages.showInfoMessage(project, "Saved Axiom setup is unavailable. Reconfigure the selected inputs before running.\n"
                    + MaterialChecksClient.text(MaterialChecksClient.object(status, "failure"), "message", "Inputs changed."), "Axiom Setup");
        }
        var selected = new JsonObject(); selected.add("context", options.get("context"));
        var paths = MaterialChecksClient.object(status, "paths");
        for (String[] field : List.of(
                new String[]{"programRoot", "Complete program directory relative to checkout ('.' includes all pack Groovy)", MaterialChecksClient.text(status, "program_root", ".")},
                new String[]{"intent", "Saved material expectations JSON (optional; leave blank for native errors)", ""},
                new String[]{"engineHome", "Explicit installed Axiom engine directory", MaterialChecksClient.text(paths, "engine_home", "")},
                new String[]{"runtimeHome", "Selected local native material runtime directory", MaterialChecksClient.text(paths, "runtime_home", "")},
                new String[]{"java", "Selected Cleanroom JDK bin/java (independent installed toolchain)", MaterialChecksClient.text(paths, "java", "")})) {
            // Remembered text is only a prompt default. Core validates selected
            // bytes against current profile/JVM bindings before retaining setup.
            String value = input(project, field[1], properties.getValue(key + field[0], field[2]), field[0].equals("intent"));
            if (value == null) return;
            selected.addProperty(field[0], value);
        }
        background(project, state, configuration -> {
            MaterialChecksClient.invoke(launch, root, session, "setup", null, null, selected);
            ui(project, () -> {
                for (String name : List.of("programRoot", "intent", "engineHome", "runtimeHome", "java"))
                    properties.setValue(key + name, selected.get(name).getAsString());
                if (!selected.get("intent").getAsString().isEmpty()) options.add("intent", selected.get("intent"));
                materialPrepareConfigured(project, state, launch, root, session, options);
            });
        });
    }
    private static void materialPrepareConfigured(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject options) {
        background(project, state, preparation -> {
            var request = MaterialChecksClient.invoke(launch, root, session, "prepare", null, null, options);
            ui(project, () -> materialExecute(project, state, launch, root, session, request));
        });
    }
    private static void materialReopen(Project project, State state, CoreLaunch launch, Path root, String session) {
        background(project, state, indicator -> {
            var history = MaterialChecksClient.invoke(launch, root, session, "history", null, null, null);
            var choices = new ArrayList<CatalogSelectionDialog.Choice<String>>();
            for (var element : history.getAsJsonArray("attempts")) {
                var row = element.getAsJsonObject();
                if (!row.get("state").getAsString().equals("unavailable")) choices.add(new CatalogSelectionDialog.Choice<>(row.get("attempt_id").getAsString(), row.get("state").getAsString(), "Exact retained material evidence", row.get("attempt_id").getAsString()));
            }
            choices.add(new CatalogSelectionDialog.Choice<>("Enter an exact material attempt ID", "manual", "No automatic rerun", ""));
            choices.add(new CatalogSelectionDialog.Choice<>("Storage and retention", "Core history preferences", "Usage, protections and cleanup status", "retention"));
            ui(project, () -> {
                var dialog = new CatalogSelectionDialog<>(project, "Retained Axiom Checks", "No game image or launch", choices);
                if (!dialog.showAndGet()) return;
                if (dialog.selected().equals("retention")) { materialRetention(project, state, launch, root, session); return; }
                String attempt = dialog.selected().isEmpty() ? input(project, "Exact material-check attempt ID", "") : dialog.selected();
                if (attempt == null) return;
                long edits = state.edits;
                background(project, state, opening -> {
                    var result = MaterialChecksClient.invoke(launch, root, session, "show", attempt, null, null);
                    var present = materialDiagnosticView(launch, root, session, result);
                    ui(project, () -> materialPresent(project, state, launch, root, session, present, edits));
                });
            });
        });
    }
    private static void materialRetention(Project project, State state, CoreLaunch launch, Path root, String session) {
        background(project, state, indicator -> {
            var options = new JsonObject(); options.addProperty("operation", "status");
            var status = MaterialChecksClient.invoke(launch, root, session, "retention", null, null, options);
            ui(project, () -> {
                var choices = new ArrayList<CatalogSelectionDialog.Choice<String>>();
                choices.add(new CatalogSelectionDialog.Choice<>("Inspect storage and protections", "Usage across known stores and volumes", status.get("notices").toString(), "status"));
                choices.add(new CatalogSelectionDialog.Choice<>("Edit retention preferences", "Finite, keep everything or disabled", "Review exact scope and permanent effects before applying", "configure"));
                choices.add(new CatalogSelectionDialog.Choice<>("Choose active contexts", "Latest useful results remain protected", "Explicit context keys from the storage report", "contexts"));
                choices.add(new CatalogSelectionDialog.Choice<>("Edit known store accounting", "Explicit native paths", "Accounting only; inaccessible stores are coverage gaps", "stores"));
                choices.add(new CatalogSelectionDialog.Choice<>("Preview cleanup", "Read only", "Current pins, grace periods and active work apply", "preview"));
                choices.add(new CatalogSelectionDialog.Choice<>("Run enabled maintenance", "Existing policy authority", "Disabled policy leaves history unchanged", "maintain"));
                var dialog = new CatalogSelectionDialog<>(project, "Storage and Retention", status.getAsJsonObject("policy").getAsJsonObject("settings").get("mode").getAsString() + " · " + status.get("allocated_bytes") + " allocated bytes", choices);
                if (!dialog.showAndGet()) return;
                String action = dialog.selected();
                if (action.equals("status")) { materialText(project, "workbench-check-storage.json", new GsonBuilder().setPrettyPrinting().create().toJson(status), 1); return; }
                var operation = new JsonObject(); operation.addProperty("operation", action);
                if (List.of("configure", "contexts", "stores").contains(action)) {
                    var settings = status.getAsJsonObject("policy").getAsJsonObject("settings").deepCopy();
                    if (action.equals("configure")) {
                        var modes = List.of(new CatalogSelectionDialog.Choice<>("Finite history", "Automatic expiry after grace", "Exact disclosure follows", "finite"),
                            new CatalogSelectionDialog.Choice<>("Keep everything", "Storage can grow", "Automatic expiry disabled", "keep-everything"),
                            new CatalogSelectionDialog.Choice<>("Disable maintenance", "History unchanged", "Automatic expiry disabled", "disabled"));
                        var mode = new CatalogSelectionDialog<>(project, "Retention Mode", "History preferences do not limit native work", modes);
                        if (!mode.showAndGet()) return;
                        settings.addProperty("mode", mode.selected());
                        for (String[] field : List.of(new String[]{"max_count", "Preferred live check count"}, new String[]{"max_bytes", "Preferred total allocated bytes, including trash and indexes"},
                                new String[]{"min_age_days", "Minimum age in days before quarantine"}, new String[]{"trash_days", "Recoverable trash grace in days"}, new String[]{"metadata_count", "Recent expiry summaries to retain"})) {
                            String value = input(project, field[1], settings.get(field[0]).getAsString());
                            if (value == null) return;
                            try { long number = Long.parseLong(value); if (number < 0 || number == 0 && !List.of("min_age_days", "trash_days").contains(field[0])) throw new NumberFormatException(); settings.addProperty(field[0], number); }
                            catch (NumberFormatException error) { Messages.showErrorDialog(project, "Enter a supported whole-number history preference.", "Retention Preferences"); return; }
                        }
                    } else if (action.equals("contexts")) {
                        var contexts = new ArrayList<CatalogSelectionDialog.Choice<String>>();
                        for (var element : status.getAsJsonArray("contexts")) {
                            var row = element.getAsJsonObject(); var context = row.getAsJsonObject("context");
                            contexts.add(new CatalogSelectionDialog.Choice<>(MaterialChecksClient.text(context, "context_id", "Saved check context"),
                                row.get("active").getAsBoolean() ? "Active — select to deactivate" : "Inactive — select to protect latest useful check",
                                MaterialChecksClient.text(context, "workspace_uri", context.toString()), row.get("key").getAsString()));
                        }
                        var select = new CatalogSelectionDialog<>(project, "Active Check Contexts", "Toggle one context; changes require policy review", contexts);
                        if (!select.showAndGet()) return;
                        var active = settings.getAsJsonArray("active_contexts"); var key = new com.google.gson.JsonPrimitive(select.selected());
                        if (!active.remove(key)) active.add(key);
                    } else {
                        var stores = new ArrayList<CatalogSelectionDialog.Choice<String>>();
                        stores.add(new CatalogSelectionDialog.Choice<>("Add a known store", "Accounting only", "No deletion authority", ""));
                        for (var path : settings.getAsJsonArray("known_stores")) stores.add(new CatalogSelectionDialog.Choice<>("Stop accounting for this store", path.getAsString(), "Stored data is unchanged", path.getAsString()));
                        var select = new CatalogSelectionDialog<>(project, "Known Check Stores", "Other stores remain independently controlled", stores);
                        if (!select.showAndGet()) return;
                        if (select.selected().isEmpty()) {
                            String path = input(project, "Known Core check store (absolute native path; accounting only)", "");
                            if (path == null || path.isBlank()) return;
                            var value = new com.google.gson.JsonPrimitive(path);
                            if (!settings.getAsJsonArray("known_stores").contains(value)) settings.getAsJsonArray("known_stores").add(value);
                        } else settings.getAsJsonArray("known_stores").remove(new com.google.gson.JsonPrimitive(select.selected()));
                    }
                    operation.addProperty("operation", "configure"); operation.add("settings", settings);
                }
                background(project, state, work -> {
                    var result = MaterialChecksClient.invoke(launch, root, session, "retention", null, null, operation);
                    ui(project, () -> {
                        if (!result.has("proposal")) { materialText(project, "workbench-check-maintenance.json", new GsonBuilder().setPrettyPrinting().create().toJson(result), 1); return; }
                        var proposal = result.getAsJsonObject("proposal");
                        if (Messages.showYesNoDialog(project, proposal.get("disclosure").getAsString(), "Apply These Retention Preferences?", null) != Messages.YES) return;
                        background(project, state, applying -> {
                            var configured = MaterialChecksClient.invoke(launch, root, session, "retention", null, proposal.get("id").getAsString(), operation);
                            ui(project, () -> materialText(project, "workbench-check-retention-policy.json", new GsonBuilder().setPrettyPrinting().create().toJson(configured), 1));
                        });
                    });
                });
            });
        });
    }
    private static void materialExecute(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject request) {
        String attempt = request.get("attempt_id").getAsString();
        if (Messages.showYesNoDialog(project, MaterialChecksClient.summary(request) + "\n\nRequest: " + request.get("id").getAsString()
                + "\n\nSaved files only; unsaved edits are excluded. Runs isolated native material logic, not Minecraft. This context is not yet qualified.", "Run This Exact Material Check?", null) != Messages.YES) return;
        long edits = state.edits;
        state.materialView = null; state.clear();
        if (state.materialResults != null) state.materialResults.clearResult();
        state.materialExecution = true;
        background(project, state, indicator -> {
            long ticket = state.generation;
            var properties = com.intellij.ide.util.PropertiesComponent.getInstance(project);
            String prior = properties.getValue("workbench.lastCompletedMaterial");
            if (prior != null) {
                try {
                    var previous = JsonParser.parseString(prior).getAsJsonObject();
                    if (session.equals(previous.get("session").getAsString()) && root.toString().equals(previous.get("root").getAsString())
                            && request.getAsJsonObject("inputs").getAsJsonObject("context").get("id").equals(previous.get("context"))) {
                        var historic = MaterialChecksClient.invoke(launch, root, session, "show", previous.get("attempt").getAsString(), null, null);
                        ui(project, () -> { if (state.generation == ticket) materialText(project, "workbench-material-history.txt",
                                "Previous completed check — historical while a new check runs\n" + MaterialChecksClient.summary(historic), 1); });
                    }
                } catch (Exception error) { indicator.setText("Previous check details unavailable; starting the new check"); }
            }
            var executor = Executors.newSingleThreadExecutor();
            JsonObject early = null;
            try {
                var future = executor.submit(() -> MaterialChecksClient.invoke(launch, root, session, "execute", attempt, request.get("id").getAsString(), null));
                boolean cancelSent = false;
                long nextDelivery = System.nanoTime();
                JsonObject result;
                indicator.setText("Axiom: running native initialization");
                while (true) {
                    try { result = future.get(200, TimeUnit.MILLISECONDS); break; }
                    catch (TimeoutException waiting) {
                        if (early == null && MaterialDeliveryClient.VIEW.equals(MaterialChecksClient.text(request, "diagnostic_delivery_format", "")) && !cancelSent && System.nanoTime() >= nextDelivery) {
                            nextDelivery = System.nanoTime() + TimeUnit.SECONDS.toNanos(1);
                            try {
                                var status = MaterialChecksClient.invoke(launch, root, session, "delivery", attempt, null, null);
                                if (!request.get("id").equals(status.get("request_id"))) throw new IllegalArgumentException("Delivery belongs to another request.");
                                if (!status.get("diagnostic_id").isJsonNull()) {
                                    var options = MaterialDeliveryClient.options(status);
                                    var view = MaterialChecksClient.invoke(launch, root, session, "diagnostics", attempt, null, options);
                                    if (!request.get("id").equals(view.get("request_id"))) throw new IllegalArgumentException("Diagnostics belong to another request.");
                                    early = view;
                                    indicator.setText("Findings available in Axiom Results; preparing recipe details");
                                    ui(project, () -> { if (state.generation == ticket) materialPresent(project, state, launch, root, session, view, edits); });
                                }
                            } catch (Exception error) { indicator.setText("Early diagnostics unavailable; execution continues: " + error.getMessage()); }
                        }
                        if ((indicator.isCanceled() || project.isDisposed()) && !cancelSent) {
                            cancelSent = true;
                            try {
                                MaterialChecksClient.invoke(launch, root, session, "cancel", attempt, null, null);
                                indicator.setText("Cancellation requested; waiting for Core to close native workers");
                            } catch (Exception error) { indicator.setText("Cancellation request failed; waiting for bounded execution. Reopen " + attempt); }
                        }
                    }
                }
                var retained = result;
                if (MaterialSnapshotClient.VIEW.equals(MaterialChecksClient.text(result, "format", "")) && !result.get("snapshot_id").isJsonNull() && MaterialChecksClient.text(result, "state", "").equals("completed")) {
                    var previous = new JsonObject(); previous.addProperty("session", session); previous.addProperty("root", root.toString());
                    previous.add("context", result.get("context_id")); previous.add("attempt", result.get("attempt_id"));
                    properties.setValue("workbench.lastCompletedMaterial", previous.toString());
                }
                if (early == null) {
                    var present = materialDiagnosticView(launch, root, session, retained);
                    ui(project, () -> { if (state.generation == ticket) materialPresent(project, state, launch, root, session, present, edits); });
                }
                else {
                    var delivered = early;
                    ui(project, () -> {
                        delivered.addProperty("detail_state", retained.has("snapshot_id") && !retained.get("snapshot_id").isJsonNull() ? "ready" : "interrupted");
                        if (state.materialResults != null) state.materialResults.update(delivered);
                    });
                }
            } catch (Exception error) {
                if (early != null) {
                    String detail;
                    try { detail = MaterialChecksClient.text(MaterialChecksClient.invoke(launch, root, session, "delivery", attempt, null, null), "detail_state", "unavailable"); }
                    catch (Exception unavailable) { detail = "unavailable"; }
                    var delivered = early; var status = detail;
                    ui(project, () -> { delivered.addProperty("detail_state", status); if (state.materialResults != null) state.materialResults.update(delivered); });
                }
                throw error;
            } finally { executor.shutdown(); ui(project, () -> state.materialExecution = false); }
        });
    }
    private static void materialText(Project project, String name, String text, int line) {
        MaterialCheckViews.openText(project, name, text, line);
    }
    static void materialAnnotations(Project project, State state, Path root, JsonObject result, long edits) {
        state.clear();
        state.marks.addAll(MaterialCheckViews.annotate(project, root, result, state.edits == edits));
    }
    private static JsonObject materialDiagnosticView(CoreLaunch launch, Path root, String session, JsonObject result) {
        if (MaterialSnapshotClient.VIEW.equals(MaterialChecksClient.text(result, "format", ""))) try {
            var status = MaterialChecksClient.invoke(launch, root, session, "delivery", result.get("attempt_id").getAsString(), null, null);
            if (!status.get("diagnostic_id").isJsonNull()) return MaterialChecksClient.invoke(launch, root, session, "diagnostics", result.get("attempt_id").getAsString(), null, MaterialDeliveryClient.options(status));
        } catch (Exception ignored) { /* Historical Core retains its existing snapshot browser. */ }
        return result;
    }
    private static void materialPresent(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, long edits) {
        state.materialView = MaterialChecksClient.text(result, "view_id", "");
        materialAnnotations(project, state, root, result, edits);
        if (MaterialDeliveryClient.isView(result)) {
            if (state.materialResults == null) state.materialResults = new MaterialResultsPanel(project);
            var panel = state.materialResults;
            panel.show(result, () -> state.edits == edits && result.has("_sourceCurrent") && result.get("_sourceCurrent").getAsBoolean(), new MaterialResultsPanel.Owner() {
                @Override public JsonObject read(String action, JsonObject options) throws Exception {
                    return MaterialChecksClient.invoke(launch, root, session, action, result.get("attempt_id").getAsString(), null, options);
                }
                @Override public void annotations(JsonObject view) { if (materialCurrent(state, result)) materialAnnotations(project, state, root, view, edits); }
                @Override public void recipes(JsonObject snapshot) { if (materialCurrent(state, result)) materialSections(project, state, launch, root, session, snapshot); }
                @Override public void open(String action, JsonObject finding, boolean fresh) throws Exception {
                    if (!materialCurrent(state, result)) return;
                    var location = MaterialChecksClient.object(finding, "location");
                    if (action.equals("open") && location.has("path") && MaterialChecksClient.text(finding, "side", "").equals("candidate")) {
                        try {
                            if (!fresh) throw new IllegalArgumentException("Saved source changed or is not verified.");
                            openCurrentSource(project, root, location); return;
                        } catch (Exception error) {
                            if (Messages.showYesNoDialog(project, "Source changed or has unsaved edits. Open the exact captured source?", "Axiom Source", "Open Captured Source", "Cancel", null) != Messages.YES) return;
                            action = "captured";
                        }
                    }
                    if (action.equals("captured") && location.has("path")) {
                        panel.read(() -> MaterialChecksClient.invoke(launch, root, session, "source", result.get("attempt_id").getAsString(), finding.get("id").getAsString(), MaterialDeliveryClient.options(result)),
                            value -> { if (materialCurrent(state, result)) MaterialCheckViews.openRetained(project, value, result, finding); });
                    } else {
                        var options = MaterialDeliveryClient.options(result); options.add("finding", finding.get("id"));
                        panel.read(() -> read("diagnostic", options), value -> { if (materialCurrent(state, result)) details(project, "workbench-original-material-diagnostic.json", value); });
                    }
                }
                @Override public void actions() {
                    var choices = List.of(new CatalogSelectionDialog.Choice<>("Read outcome and scope", "", "", "report"),
                        new CatalogSelectionDialog.Choice<>("Inspect run evidence", "", "", "evidence"),
                        new CatalogSelectionDialog.Choice<>("Export complete result", "", "Available when recipe details are ready", "export"),
                        new CatalogSelectionDialog.Choice<>("Review setup", "", "", "setup"),
                        new CatalogSelectionDialog.Choice<>("Compare a saved edit", "", "", "compare"));
                    var dialog = new CatalogSelectionDialog<>(project, "Axiom Run Actions", "Selected retained run", choices);
                    if (!dialog.showAndGet() || !materialCurrent(state, result)) return;
                    switch (dialog.selected()) {
                        case "report" -> materialText(project, "workbench-material-report.txt", MaterialChecksClient.report(result), 1);
                        case "evidence" -> details(project, "workbench-material-evidence.json", result);
                        case "export" -> {
                            if (MaterialChecksClient.text(result, "detail_state", "").equals("ready")) panel.read(() -> read("show", null), snapshot -> materialExport(project, state, launch, root, session, snapshot, null, null, null));
                            else Messages.showInfoMessage(project, "Recipe details are still preparing. Original diagnostics remain available.", "Axiom");
                        }
                        default -> {
                            if (state.materialExecution) Messages.showInfoMessage(project, "This check is still finishing. Cancel it or wait before running another check.", "Axiom");
                            else materialPrepare(project, state, launch, root, session, dialog.selected().equals("compare") ? result.get("attempt_id").getAsString() : null, dialog.selected().equals("setup"));
                        }
                    }
                }
            });
            return;
        }
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        for (String type : List.of("read", "raw")) {
            var choice = new JsonObject(); choice.addProperty("type", type);
            choices.add(new CatalogSelectionDialog.Choice<>(type.equals("read") ? "Read native outcomes, expectations and limitations" : "Inspect exact retained evidence", "read-only", MaterialChecksClient.summary(result), choice));
        }
        boolean prepared = result.get("format").getAsString().equals(MaterialChecksClient.REQUEST);
        if (MaterialDeliveryClient.isView(result)) {
            for (String type : List.of("snapshot", "early-next", "early-remaining")) {
                if (!type.equals("snapshot") && result.get("next_offset").isJsonNull()) continue;
                var choice = new JsonObject(); choice.addProperty("type", type);
                choices.add(new CatalogSelectionDialog.Choice<>(type.equals("snapshot") ? "Open completed snapshot detail" : type.equals("early-next") ? "Load next findings page" : "Load remaining findings", "retained diagnostics", "Snapshot: " + MaterialChecksClient.text(result, "detail_state", "preparing"), choice));
            }
        }
        if (MaterialSnapshotClient.VIEW.equals(MaterialChecksClient.text(result, "format", "")) && !result.get("snapshot_id").isJsonNull() && MaterialChecksClient.text(result, "state", "").equals("completed")) {
            for (String type : List.of("sections", "export", "next", "remaining")) {
                var page = MaterialChecksClient.object(result, "finding_page");
                if (List.of("next", "remaining").contains(type) && (!MaterialChecksClient.text(page, "state", "").equals("ready") || page.get("complete").getAsBoolean())) continue;
                var choice = new JsonObject(); choice.addProperty("type", type);
                String label = switch (type) { case "sections" -> "Browse retained sections and values"; case "export" -> "Export complete original result";
                    case "next" -> "Load next findings page"; default -> "Load remaining findings"; };
                choices.add(new CatalogSelectionDialog.Choice<>(label, "on demand", MaterialSnapshotClient.progress(result), choice));
            }
        }
        boolean interrupted = MaterialChecksClient.text(MaterialChecksClient.object(result, "_presentation"), "attempt_state", "").equals("started-without-retained-result");
        var operation = new JsonObject(); operation.addProperty("type", prepared ? (interrupted ? "cancel" : "execute") : "compare");
        choices.add(new CatalogSelectionDialog.Choice<>(prepared ? (interrupted ? "Request cancellation of this started attempt" : "Run this exact prepared material check") : "Compare a new saved edit against this program",
                "explicit action", interrupted ? "A start marker does not prove a live worker. Never reruns it." : "Exact saved input confirmation follows", operation));
        if (result.has("findings")) for (var element : result.getAsJsonArray("findings")) {
            var finding = element.getAsJsonObject(); var location = MaterialChecksClient.object(finding, "location");
            var choice = new JsonObject(); choice.addProperty("type", "finding"); choice.add("finding", finding);
            String source = location.has("path") ? location.get("path").getAsString() + ":" + location.getAsJsonObject("start").get("line").getAsInt() : "unlocated";
            choices.add(new CatalogSelectionDialog.Choice<>(finding.get("side").getAsString() + " · " + MaterialChecksClient.findingLabel(result, finding).split("\n", 2)[0], source, MaterialChecksClient.findingLabel(result, finding), choice));
        }
        var dialog = new CatalogSelectionDialog<>(project, "Axiom: " + (interrupted ? "started without retained result" : result.get("state").getAsString()), MaterialChecksClient.summary(result), choices);
        if (!dialog.showAndGet()) return;
        String type = dialog.selected().get("type").getAsString(), attempt = result.get("attempt_id").getAsString();
        switch (type) {
            case "read" -> materialText(project, "workbench-material-report.txt", prepared ? MaterialChecksClient.summary(result) : MaterialChecksClient.report(result), 1);
            case "raw" -> details(project, "workbench-material-evidence.json", result);
            case "snapshot" -> background(project, state, indicator -> {
                var status = MaterialChecksClient.invoke(launch, root, session, "delivery", attempt, null, null);
                if (MaterialChecksClient.text(status, "detail_state", "").equals("ready")) {
                    var complete = MaterialChecksClient.invoke(launch, root, session, "show", attempt, null, null);
                    ui(project, () -> materialPresent(project, state, launch, root, session, complete, edits));
                } else ui(project, () -> materialText(project, "material-detail-status.txt", "Snapshot detail: " + MaterialChecksClient.text(status, "detail_state", "unavailable") + ". Diagnostic evidence remains available.", 1));
            });
            case "early-next", "early-remaining" -> materialDeliveryFindings(project, state, launch, root, session, result, edits, type.equals("early-remaining"));
            case "sections" -> materialSections(project, state, launch, root, session, result);
            case "export" -> materialExport(project, state, launch, root, session, result, null, null, null);
            case "next", "remaining" -> materialFindings(project, state, launch, root, session, result, edits, type.equals("remaining"));
            case "compare" -> materialPrepare(project, state, launch, root, session, attempt);
            case "execute" -> materialExecute(project, state, launch, root, session, result);
            case "cancel" -> background(project, state, indicator -> {
                var cancelled = MaterialChecksClient.invoke(launch, root, session, "cancel", attempt, null, null);
                ui(project, () -> details(project, "workbench-material-cancellation.json", cancelled));
            });
            default -> materialFinding(project, state, launch, root, session, result, dialog.selected().getAsJsonObject("finding"));
        }
    }
    private static void materialFinding(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, JsonObject finding) {
        var choices = new ArrayList<CatalogSelectionDialog.Choice<String>>();
        choices.add(new CatalogSelectionDialog.Choice<>("Read native diagnostic evidence", "retained", "Includes original native exception and evidence pointers", "evidence"));
        var location = MaterialChecksClient.object(finding, "location");
        if (location.has("path")) {
            choices.add(new CatalogSelectionDialog.Choice<>("Read exact retained source", "read-only", "Remains available after developer edits", "retained"));
            choices.add(new CatalogSelectionDialog.Choice<>("Open identical saved working copy", "byte-verified", "Requires matching bytes and no unsaved editor edits", "live"));
        }
        var dialog = new CatalogSelectionDialog<>(project, "Material Diagnostic", MaterialChecksClient.findingLabel(result, finding), choices);
        if (!dialog.showAndGet()) return;
        if (dialog.selected().equals("live")) {
            try { openCurrentSource(project, root, location); }
            catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Source changed; inspect retained material evidence", error); }
        } else if (dialog.selected().equals("retained")) background(project, state, indicator -> {
            var view = MaterialChecksClient.invoke(launch, root, session, "source", result.get("attempt_id").getAsString(), finding.get("id").getAsString(), MaterialDeliveryClient.isView(result) ? MaterialDeliveryClient.options(result) : null);
            ui(project, () -> {
                try { MaterialCheckViews.openRetained(project, view, result, finding); }
                catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Retained material source unavailable", error); }
            });
        });
        else if (MaterialDeliveryClient.isView(result)) background(project, state, indicator -> {
            var options = MaterialDeliveryClient.options(result); options.add("finding", finding.get("id"));
            var evidence = MaterialChecksClient.invoke(launch, root, session, "diagnostic", result.get("attempt_id").getAsString(), null, options);
            ui(project, () -> details(project, "workbench-material-diagnostic.json", evidence));
        });
        else if (MaterialSnapshotClient.VIEW.equals(MaterialChecksClient.text(result, "format", ""))) {
            materialValue(project, state, launch, root, session, result, MaterialSnapshotClient.diagnosticQuery(result, finding));
        } else { var evidence = new JsonObject(); evidence.add("finding", finding); evidence.add("native", result.get("native")); details(project, "workbench-material-diagnostic.json", evidence); }
    }
    private static boolean materialCurrent(State state, JsonObject result) {
        return MaterialChecksClient.text(result, "view_id", "").equals(state.materialView);
    }
    private static void materialDeliveryFindings(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, long edits, boolean all) {
        background(project, state, indicator -> {
            do {
                if (indicator.isCanceled() || !materialCurrent(state, result)) break;
                var options = MaterialDeliveryClient.options(result); options.add("offset", result.get("next_offset"));
                var page = MaterialChecksClient.invoke(launch, root, session, "diagnostics", result.get("attempt_id").getAsString(), null, options);
                if (!materialCurrent(state, result)) return;
                result.getAsJsonArray("findings").addAll(page.getAsJsonArray("findings")); result.add("next_offset", page.get("next_offset"));
                var labels = MaterialChecksClient.object(MaterialChecksClient.object(result, "_presentation"), "finding_labels");
                MaterialChecksClient.object(MaterialChecksClient.object(page, "_presentation"), "finding_labels").entrySet().forEach(row -> labels.add(row.getKey(), row.getValue()));
            } while (all && !result.get("next_offset").isJsonNull());
            ui(project, () -> { if (materialCurrent(state, result)) materialPresent(project, state, launch, root, session, result, edits); });
        });
    }
    private static void materialFindings(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, long edits, boolean all) {
        background(project, state, indicator -> {
            do {
                if (indicator.isCanceled() || !materialCurrent(state, result)) break;
                var query = MaterialSnapshotClient.nextFindingsQuery(result, all);
                var page = MaterialChecksClient.invoke(launch, root, session, "query", result.get("attempt_id").getAsString(), null, query);
                if (!materialCurrent(state, result)) return;
                result.add("finding_page", page);
                if (!MaterialChecksClient.text(page, "state", "").equals("ready")) break;
                result.getAsJsonArray("findings").addAll(MaterialSnapshotClient.findings(page));
                var labels = MaterialChecksClient.object(MaterialChecksClient.object(result, "_presentation"), "finding_labels");
                MaterialChecksClient.object(MaterialChecksClient.object(page, "_presentation"), "finding_labels").entrySet().forEach(row -> labels.add(row.getKey(), row.getValue()));
                indicator.setText(MaterialSnapshotClient.progress(result));
            } while (all && !result.getAsJsonObject("finding_page").get("complete").getAsBoolean());
            ui(project, () -> { if (materialCurrent(state, result)) materialPresent(project, state, launch, root, session, result, edits); });
        });
    }
    private static void materialExport(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, String section, String key, JsonObject content) {
        String destination = input(project, "New absolute JSON export file (existing files are preserved)", root.resolve("axiom-result.json").toString());
        if (destination == null) return;
        var options = new JsonObject(); options.add("snapshot_id", result.get("snapshot_id")); options.addProperty("destination", destination);
        if (section != null) { options.addProperty("section_id", section); options.addProperty("record_key", key); options.add("sha256", content.get("sha256")); }
        background(project, state, indicator -> {
            var receipt = MaterialChecksClient.invoke(launch, root, session, "export", result.get("attempt_id").getAsString(), null, options);
            ui(project, () -> details(project, "workbench-material-export.json", receipt));
        });
    }
    private static void materialValue(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, JsonObject query) {
        background(project, state, indicator -> {
            var page = MaterialChecksClient.invoke(launch, root, session, "query", result.get("attempt_id").getAsString(), null, query);
            ui(project, () -> {
                if (!materialCurrent(state, result)) return;
                if (!MaterialChecksClient.text(page, "state", "").equals("ready")) { details(project, "workbench-material-detail-status.json", page); return; }
                var record = page.getAsJsonObject("payload");
                if (record.has("value")) { details(project, "workbench-material-detail.json", page); return; }
                var content = record.getAsJsonObject("content");
                if (Messages.showYesNoDialog(project, content.get("bytes") + " bytes retained. Export this complete value to a new file?",
                        "Large Retained Value — Not Loaded Into Editor", null) == Messages.YES)
                    materialExport(project, state, launch, root, session, result, query.get("section_id").getAsString(), record.get("key").getAsString(), content);
                else details(project, "workbench-material-content-reference.json", page);
            });
        });
    }
    private static void materialSections(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result) {
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        for (var item : result.getAsJsonArray("sections")) {
            var section = item.getAsJsonObject(); choices.add(new CatalogSelectionDialog.Choice<>(section.get("id").getAsString(), section.get("state").getAsString(),
                    "Records: " + section.get("count") + " · " + MaterialChecksClient.text(section, "reason", section.get("schema").getAsString()), section));
        }
        var dialog = new CatalogSelectionDialog<>(project, "Retained Snapshot Sections", "Details not yet loaded", choices);
        if (dialog.showAndGet()) materialRecords(project, state, launch, root, session, result,
                MaterialSnapshotClient.query(result, "records", dialog.selected().get("id").getAsString(), null));
    }
    private static void materialRecords(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, JsonObject query) {
        background(project, state, indicator -> {
            var page = MaterialChecksClient.invoke(launch, root, session, "query", result.get("attempt_id").getAsString(), null, query);
            ui(project, () -> {
                if (!materialCurrent(state, result)) return;
                if (!MaterialChecksClient.text(page, "state", "").equals("ready")) { details(project, "workbench-material-section-status.json", page); return; }
                var payload = page.getAsJsonObject("payload"); var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
                long ordinal = payload.get("offset").getAsLong();
                for (var value : payload.getAsJsonArray("records")) { var row = value.getAsJsonObject();
                    choices.add(new CatalogSelectionDialog.Choice<>("Record " + (++ordinal), row.get("key").getAsString(), row.has("content") ? row.getAsJsonObject("content").get("bytes") + " bytes · export available" : "Complete value available on selection", row)); }
                if (!page.get("complete").getAsBoolean()) choices.add(new CatalogSelectionDialog.Choice<>("Next records page", "more available", "No records omitted from retention", new JsonObject()));
                var dialog = new CatalogSelectionDialog<>(project, query.get("section_id").getAsString(), ordinal + " of " + payload.get("total"), choices);
                if (!dialog.showAndGet()) return;
                if (!dialog.selected().has("key")) { var next = query.deepCopy(); next.add("cursor", page.get("next_cursor")); materialRecords(project, state, launch, root, session, result, next); }
                else materialValue(project, state, launch, root, session, result, MaterialSnapshotClient.query(result, "record", query.get("section_id").getAsString(), dialog.selected().get("key").getAsString()));
            });
        });
    }
    private static void selectRecipe(Project project, State state, CoreLaunch launch, Path root, String session, String image) {
        var mode = new CatalogSelectionDialog<>(project, "Recipe Expectation", "Observation only; never applies source", List.of(
                new CatalogSelectionDialog.Choice<>("Present: current saved source", "exact candidate", "Inspect what the saved declaration expects", "saved"),
                new CatalogSelectionDialog.Choice<>("Present: retained source", "explicit reference", "Check against a previous declaration", "reference"),
                new CatalogSelectionDialog.Choice<>("Absent: retained source", "explicit reference", "Require complete evidence that this exact recipe is absent", "absent")));
        if (!mode.showAndGet()) return;
        var options = new JsonObject();
        if (!mode.selected().equals("saved")) {
            String reference = input(project, "Exact retained reference attempt ID", ""); if (reference == null) return;
            options.addProperty("reference", reference);
        }
        String path = input(project, "Exact recipe source path (groovy/postInit/...groovy)", "groovy/postInit/"); if (path == null) return;
        options.addProperty("path", path);
        background(project, state, indicator -> {
            var catalog = DeveloperChecksClient.invoke(launch, root, session, "recipes", null, null, options);
            var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
            for (var value : catalog.getAsJsonArray("recipes")) {
                var row = value.getAsJsonObject(); var location = row.getAsJsonObject("location");
                choices.add(new CatalogSelectionDialog.Choice<>(row.get("map").getAsString() + " · " + location.get("path").getAsString() + ":" + location.getAsJsonObject("start").get("line").getAsInt(),
                        row.get("support").getAsString(), row.get("reasons") + " " + row.get("recipe"), row));
            }
            ui(project, () -> {
                var dialog = new CatalogSelectionDialog<>(project, "Saved Recipe", "Static candidate, not execution proof", choices);
                if (!dialog.showAndGet()) return;
                var observation = new CatalogSelectionDialog<>(project, "Observation Scope", "Lifecycle requires the admitted current stack", List.of(
                        new CatalogSelectionDialog.Choice<>("Registration, actual lookup decisions, and final snapshot", "sealed observer", "Real GTCEu lookup and executed source; not machine execution validation", true),
                        new CatalogSelectionDialog.Choice<>("Final snapshot only", "no startup observer", "Capture final recipe membership and lookup evidence", false)));
                if (!observation.showAndGet()) return;
                var selected = new JsonObject(); selected.addProperty("recipe", dialog.selected().get("id").getAsString());
                selected.addProperty("trace", observation.selected());
                if (options.has("reference")) selected.add("reference", options.get("reference"));
                selected.addProperty("absent", mode.selected().equals("absent"));
                background(project, state, preparation -> {
                    var request = DeveloperChecksClient.invoke(launch, root, session, "prepare", image, null, selected);
                    ui(project, () -> confirm(project, state, launch, root, session, request));
                });
            });
        });
    }
    private static void confirm(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject request) {
        if (request.has("expectation") && !request.get("expectation").isJsonNull() && request.getAsJsonObject("expectation").get("support").getAsString().equals("unsupported")) {
            details(project, "workbench-unsupported-recipe.json", request.get("expectation")); return;
        }
        String attempt = request.get("attempt_id").getAsString(); state.lastAttempt = attempt;
        String explanation = request.getAsJsonObject("check").get("label").getAsString() + "\nCandidate: " + request.getAsJsonObject("candidate").get("id").getAsString() + "\n" + DeveloperChecksClient.provenanceSummary(request) + "\n\n" + DeveloperChecksClient.expectationSummary(request) + "\n\n" + request.getAsJsonArray("effects") + "\n\nNo downloads. Unsaved edits are excluded. Trusted executable project, not a sandbox.";
        if (Messages.showYesNoDialog(project, explanation, "Run This Exact Check?", null) != Messages.YES) return;
        long edits = state.edits;
        background(project, state, indicator -> {
            ExecutorService executor = Executors.newSingleThreadExecutor();
            ScheduledExecutorService progressPoller = Executors.newSingleThreadScheduledExecutor();
            boolean cancelSent = false;
            try {
                Future<JsonObject> future = executor.submit(() -> DeveloperChecksClient.invoke(launch, root, session, "execute", attempt, request.get("id").getAsString()));
                progressPoller.scheduleWithFixedDelay(() -> {
                    if (future.isDone() || indicator.isCanceled() || project.isDisposed()) return;
                    try {
                        var progress = DeveloperChecksClient.invoke(launch, root, session, "progress", attempt, null);
                        if (!future.isDone() && !indicator.isCanceled()) indicator.setText(DeveloperChecksClient.progressMessage(progress, attempt, request.get("id").getAsString()));
                    } catch (Exception unavailable) {
                        if (!future.isDone() && !indicator.isCanceled()) indicator.setText("Progress unavailable; execution continues. Reopen the retained attempt if needed.");
                    }
                }, 2, 2, TimeUnit.SECONDS);
                JsonObject result;
                while (true) {
                    try { result = future.get(200, TimeUnit.MILLISECONDS); break; }
                    catch (TimeoutException waiting) {
                        if ((indicator.isCanceled() || project.isDisposed()) && !cancelSent) {
                            DeveloperChecksClient.invoke(launch, root, session, "cancel", attempt, null);
                            cancelSent = true;
                            indicator.setText("Cancellation requested; waiting for owned processes to close");
                        }
                    }
                }
                var completed = result;
                ui(project, () -> present(project, state, launch, root, session, completed, edits));
            } finally { progressPoller.shutdownNow(); executor.shutdown(); }
        });
    }
    private static void present(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result, long edits) {
        state.clear();
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        choices.add(new CatalogSelectionDialog.Choice<>("Inspect evidence and cleanup", result.get("state").getAsString(), "Read-only retained result", new JsonObject()));
        if (result.has("assertions") && !result.get("assertions").isJsonNull()) {
            var detail = new JsonObject(); detail.addProperty("assertions", true);
            choices.add(new CatalogSelectionDialog.Choice<>("Recipe assertion: " + result.getAsJsonObject("assertions").get("state").getAsString(), "expected versus observed", "Does not promote the startup outcome", detail));
            var source = new JsonObject(); source.addProperty("recipeSource", true);
            choices.add(new CatalogSelectionDialog.Choice<>("Open exact expected recipe source", "byte-verified", "Historical bytes never retarget a changed editor", source));
        }
        if (result.has("provenance")) {
            var provenance = new JsonObject(); provenance.addProperty("provenance", true);
            choices.add(new CatalogSelectionDialog.Choice<>("Inspect selected pack, dependencies and environment", "captured", DeveloperChecksClient.provenanceSummary(result), provenance));
            var comparison = new JsonObject(); comparison.addProperty("compare", true);
            choices.add(new CatalogSelectionDialog.Choice<>("Compare with a retained reference run", "explicit selection", "Neither outcome is changed or promoted", comparison));
        }
        if (result.has("diagnostics")) for (var value : result.getAsJsonArray("diagnostics")) {
            var group = value.getAsJsonObject(); var item = new JsonObject(); item.add("group", group);
            choices.add(new CatalogSelectionDialog.Choice<>(group.get("family").getAsString() + " · " + group.get("occurrence_count").getAsInt() + " occurrence(s)", group.get("subject").getAsString(), group.get("guidance").getAsString(), item));
        }
        boolean recovered = result.has("_presentation") && result.getAsJsonObject("_presentation").has("recovery") && !result.getAsJsonObject("_presentation").get("recovery").isJsonNull();
        if (!recovered && (result.get("state").getAsString().equals("needs-attention") || result.has("cleanup") && result.getAsJsonObject("cleanup").get("state").getAsString().equals("blocked"))) {
            var recovery = new JsonObject(); recovery.addProperty("recover", true);
            choices.add(new CatalogSelectionDialog.Choice<>("Recover interrupted process and disposable files", "explicit consent", "Does not rerun or promote the check", recovery));
        }
        if (result.has("evidence")) for (var value : result.getAsJsonArray("evidence")) {
            String name = value.getAsJsonObject().get("path").getAsString();
            var item = new JsonObject(); item.addProperty("log", name);
            choices.add(new CatalogSelectionDialog.Choice<>("Open " + name, "retained", "Exact process or runtime log", item));
        }
        {
            for (var value : DeveloperChecksClient.findings(result)) {
                var finding = value.getAsJsonObject();
                if (!finding.get("location").isJsonObject()) {
                    continue;
                }
                var location = finding.getAsJsonObject("location");
                if (!result.has("_sourceCurrent") || !result.get("_sourceCurrent").getAsBoolean() || state.edits != edits) continue;
                try {
                    var target = SourceNavigationClient.verify(root, location);
                    var file = LocalFileSystem.getInstance().findFileByNioFile(target.path());
                    var document = file == null ? null : FileDocumentManager.getInstance().getDocument(file);
                    if (document == null || FileDocumentManager.getInstance().isDocumentUnsaved(document) || !document.getText().equals(target.text())) continue;
                    int start = document.getLineStartOffset(location.getAsJsonObject("start").get("line").getAsInt() - 1) + location.getAsJsonObject("start").get("column").getAsInt() - 1;
                    int end = document.getLineStartOffset(location.getAsJsonObject("end").get("line").getAsInt() - 1) + location.getAsJsonObject("end").get("column").getAsInt() - 1;
                    var model = DocumentMarkupModel.forDocument(document, project, true);
                    var color = finding.get("severity").getAsString().equals("error") ? JBColor.RED : JBColor.GRAY;
                    var mark = model.addRangeHighlighter(start, end, HighlighterLayer.WARNING, new TextAttributes(null, null, color, EffectType.WAVE_UNDERSCORE, 0), HighlighterTargetArea.EXACT_RANGE);
                    mark.setErrorStripeTooltip(finding.get("message").getAsString()); state.marks.add(mark);
                } catch (Exception ignored) { /* Never attach old evidence to new bytes. */ }
            }
        }
        var dialog = new CatalogSelectionDialog<>(project, "Saved Check", "Historical evidence applies only to its saved candidate", choices);
        if (!dialog.showAndGet()) return;
        try {
            var selected = dialog.selected();
            if (selected.has("compare")) {
                compare(project, state, launch, root, session, result);
            } else if (selected.has("assertions")) {
                presentRecipe(project, state, launch, root, session, result);
            } else if (selected.has("recipeSource")) {
                openCurrentSource(project, root, result.getAsJsonObject("assertions").getAsJsonObject("expectation").getAsJsonObject("subject").getAsJsonObject("location"));
            } else if (selected.has("provenance")) {
                var detail = new JsonObject(); detail.add("source", result.get("candidate")); detail.add("provenance", result.get("provenance"));
                details(project, "workbench-check-provenance.json", detail);
            } else if (selected.has("group")) {
                presentGroup(project, root, result, selected.getAsJsonObject("group"));
            } else if (selected.size() == 0) {
                var file = new LightVirtualFile("workbench-saved-check.json", new GsonBuilder().setPrettyPrinting().create().toJson(result));
                file.setWritable(false); FileEditorManager.getInstance(project).openFile(file, true);
            } else if (selected.has("diagnostic")) {
                var file = new LightVirtualFile("workbench-check-diagnostic.json", new GsonBuilder().setPrettyPrinting().create().toJson(selected.get("diagnostic")));
                file.setWritable(false); FileEditorManager.getInstance(project).openFile(file, true);
            } else if (selected.has("recover")) {
                if (Messages.showYesNoDialog(project, "Close only this attempt's exactly identified processes and move disposable files to recoverable trash?", "Recover This Attempt?", null) != Messages.YES) return;
                String consent = result.has("request_id") ? result.get("request_id").getAsString() : result.getAsJsonObject("request").get("id").getAsString();
                background(project, state, indicator -> {
                    String attempt = result.get("attempt_id").getAsString();
                    DeveloperChecksClient.invoke(launch, root, session, "recover", attempt, consent);
                    var reopened = DeveloperChecksClient.invoke(launch, root, session, "show", attempt, null);
                    ui(project, () -> present(project, state, launch, root, session, reopened, state.edits));
                });
            } else if (selected.has("log")) {
                background(project, state, indicator -> {
                    var log = DeveloperChecksClient.invoke(launch, root, session, "log", result.get("attempt_id").getAsString(), selected.get("log").getAsString());
                    ui(project, () -> {
                        var file = new LightVirtualFile("workbench-check.log", log.get("text").getAsString());
                        file.setWritable(false); FileEditorManager.getInstance(project).openFile(file, true);
                    });
                });
            } else {
                var target = SourceNavigationClient.verify(root, selected);
                var file = LocalFileSystem.getInstance().findFileByNioFile(target.path());
                var document = file == null ? null : FileDocumentManager.getInstance().getDocument(file);
                if (document == null || FileDocumentManager.getInstance().isDocumentUnsaved(document) || !document.getText().equals(target.text())) throw new IllegalArgumentException("Editor bytes changed; rerun checks.");
                FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, selected.getAsJsonObject("start").get("line").getAsInt() - 1, selected.getAsJsonObject("start").get("column").getAsInt() - 1), true);
            }
        } catch (Exception error) { WorkbenchNotifications.commandFailed(project, "Check finding unavailable", error); }
    }

    private static void details(Project project, String name, JsonElement value) {
        var file = new LightVirtualFile(name, new GsonBuilder().setPrettyPrinting().create().toJson(value));
        file.setWritable(false); FileEditorManager.getInstance(project).openFile(file, true);
    }

    private static void recipeText(Project project, JsonObject result, JsonObject section) {
        var file = new LightVirtualFile("workbench-recipe-explanation.txt", DeveloperChecksClient.recipeExplanationText(result, section));
        file.setWritable(false); FileEditorManager.getInstance(project).openFile(file, true);
    }

    private static void presentRecipe(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result) throws Exception {
        var report = DeveloperChecksClient.recipeExplanation(result);
        if (report == null) { recipeText(project, result, null); return; }
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        choices.add(new CatalogSelectionDialog.Choice<>("Read full recipe explanation", "retained", report.get("summary").getAsString(), new JsonObject()));
        for (var value : report.getAsJsonArray("sections")) {
            var section = value.getAsJsonObject();
            choices.add(new CatalogSelectionDialog.Choice<>(section.get("title").getAsString(), "captured evidence", "Property differences, source candidates and log links", section));
        }
        var raw = new JsonObject(); raw.addProperty("raw", true);
        choices.add(new CatalogSelectionDialog.Choice<>("Inspect raw assertion evidence", "retained", "Original structured assertion", raw));
        var dialog = new CatalogSelectionDialog<>(project, "Recipe Explanation", "Recipe assertion: " + result.getAsJsonObject("assertions").get("state").getAsString() + " · startup: " + result.get("state").getAsString(), choices);
        if (!dialog.showAndGet()) return;
        var section = dialog.selected();
        if (section.size() == 0) { recipeText(project, result, null); return; }
        if (section.has("raw")) { details(project, "workbench-recipe-assertions.json", result.get("assertions")); return; }
        var links = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        links.add(new CatalogSelectionDialog.Choice<>("Read explanation and property differences", "retained", section.get("title").getAsString(), new JsonObject()));
        int sourceIndex = 0;
        for (var value : section.getAsJsonArray("sources")) {
            var source = value.getAsJsonObject(); var location = source.getAsJsonObject("location");
            var retained = new JsonObject();
            retained.addProperty("retained_source", section.get("id").getAsString() + ":" + sourceIndex++);
            links.add(new CatalogSelectionDialog.Choice<>("Read retained " + source.get("label").getAsString(), location.get("path").getAsString(), "Exact candidate source, read-only", retained));
            links.add(new CatalogSelectionDialog.Choice<>(source.get("label").getAsString(), location.get("path").getAsString() + ":" + location.getAsJsonObject("start").get("line").getAsInt(), source.get("basis").getAsString() + " · requires identical saved editor bytes", source));
        }
        for (var value : section.getAsJsonArray("evidence")) {
            var ref = value.getAsJsonObject();
            links.add(new CatalogSelectionDialog.Choice<>("Open captured " + ref.get("log").getAsString() + ":" + ref.get("line").getAsInt(), "retained", "Exact observation evidence", ref));
        }
        var detail = new CatalogSelectionDialog<>(project, "Recipe Evidence", section.get("title").getAsString(), links);
        if (!detail.showAndGet()) return;
        var selected = detail.selected();
        if (selected.has("retained_source")) {
            background(project, state, indicator -> {
                var source = DeveloperChecksClient.invoke(launch, root, session, "source", result.get("attempt_id").getAsString(), selected.get("retained_source").getAsString());
                if (!source.get("format").getAsString().equals("workbench-check-source-view-v1") || !source.get("read_only").getAsBoolean() || !source.get("result_id").equals(result.get("id"))) throw new IllegalArgumentException("Retained source differs from the selected check.");
                ui(project, () -> {
                    var file = new LightVirtualFile("workbench-retained-recipe.groovy", source.get("text").getAsString());
                    file.setWritable(false);
                    int line = source.getAsJsonObject("source").getAsJsonObject("location").getAsJsonObject("start").get("line").getAsInt();
                    FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, line - 1, 0), true);
                });
            });
            return;
        }
        if (selected.has("location")) { openCurrentSource(project, root, selected.getAsJsonObject("location")); return; }
        if (selected.has("log")) {
            background(project, state, indicator -> {
                var log = DeveloperChecksClient.invoke(launch, root, session, "log", result.get("attempt_id").getAsString(), selected.get("log").getAsString());
                ui(project, () -> {
                    var file = new LightVirtualFile("workbench-recipe-evidence-line-" + selected.get("line").getAsInt() + ".log", log.get("text").getAsString());
                    file.setWritable(false);
                    FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, selected.get("line").getAsInt() - 1, 0), true);
                });
            });
            return;
        }
        recipeText(project, result, section);
    }

    private static void presentGroup(Project project, Path root, JsonObject result, JsonObject group) throws Exception {
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        var detail = group.deepCopy(); var occurrences = new com.google.gson.JsonArray();
        for (var index : group.getAsJsonArray("finding_indices")) occurrences.add(result.getAsJsonObject("interpretation").getAsJsonArray("findings").get(index.getAsInt()));
        detail.add("findings", occurrences);
        choices.add(new CatalogSelectionDialog.Choice<>("Inspect group and all evidence", "retained", group.get("guidance").getAsString(), new JsonObject()));
        for (var value : occurrences) {
            var finding = value.getAsJsonObject();
            choices.add(new CatalogSelectionDialog.Choice<>(finding.get("message").getAsString().split("\n", 2)[0], finding.get("category").getAsString(), finding.get("evidence").toString(), finding));
        }
        var dialog = new CatalogSelectionDialog<>(project, "Diagnostic Group", group.get("guidance").getAsString(), choices);
        if (!dialog.showAndGet()) return;
        var selected = dialog.selected();
        if (selected.size() == 0) { details(project, "workbench-diagnostic-group.json", detail); return; }
        if (!selected.get("location").isJsonObject()) { details(project, "workbench-diagnostic-event.json", selected); return; }
        var location = selected.getAsJsonObject("location");
        openCurrentSource(project, root, location);
    }

    static void openCurrentSource(Project project, Path root, JsonObject location) throws Exception {
        var target = SourceNavigationClient.verify(root, location);
        var file = LocalFileSystem.getInstance().findFileByNioFile(target.path());
        var document = file == null ? null : FileDocumentManager.getInstance().getDocument(file);
        if (document == null || FileDocumentManager.getInstance().isDocumentUnsaved(document) || !document.getText().equals(target.text())) throw new IllegalArgumentException("Editor bytes changed; inspect retained evidence or rerun checks.");
        FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, location.getAsJsonObject("start").get("line").getAsInt() - 1, location.getAsJsonObject("start").get("column").getAsInt() - 1), true);
    }

    private static void compare(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject candidate) {
        background(project, state, indicator -> {
            var history = DeveloperChecksClient.invoke(launch, root, session, "history", null, null);
            var choices = new ArrayList<CatalogSelectionDialog.Choice<String>>();
            String attempt = candidate.get("attempt_id").getAsString();
            for (var value : history.getAsJsonArray("runs")) {
                var run = value.getAsJsonObject(); String id = run.get("attempt_id").getAsString();
                if (id.equals(attempt)) continue;
                choices.add(new CatalogSelectionDialog.Choice<>(run.getAsJsonObject("pack").get("version").getAsString() + " · " + run.get("state").getAsString(), id, run.getAsJsonObject("source").get("revision").getAsString(), id));
            }
            choices.add(new CatalogSelectionDialog.Choice<>("Enter an exact reference attempt ID", "retained", "No implicit reference selection", ""));
            ui(project, () -> {
                var dialog = new CatalogSelectionDialog<>(project, "Reference Run", "Comparison is not acceptance; historical formats excluded: " + history.get("unsupported_records"), choices);
                if (!dialog.showAndGet()) return;
                String reference = dialog.selected().isEmpty() ? input(project, "Exact reference check attempt ID", "") : dialog.selected();
                if (reference == null) return;
                background(project, state, progress -> {
                    var comparison = DeveloperChecksClient.invoke(launch, root, session, "compare", attempt, reference);
                    ui(project, () -> presentComparison(project, state, launch, root, session, comparison));
                });
            });
        });
    }

    private static void presentComparison(Project project, State state, CoreLaunch launch, Path root, String session, JsonObject result) {
        var choices = new ArrayList<CatalogSelectionDialog.Choice<JsonObject>>();
        choices.add(new CatalogSelectionDialog.Choice<>("Inspect comparison and provenance", result.get("state").getAsString(), "Original outcomes remain unchanged", new JsonObject()));
        for (String side : List.of("reference", "candidate")) {
            var item = new JsonObject(); item.addProperty("attempt", result.getAsJsonObject(side).get("attempt_id").getAsString());
            choices.add(new CatalogSelectionDialog.Choice<>("Open " + side + " check", result.getAsJsonObject(side).get("state").getAsString(), "Navigate its verified source and retained logs", item));
        }
        for (var value : result.getAsJsonArray("groups")) {
            var row = value.getAsJsonObject(); var group = row.get(row.get("candidate").isJsonNull() ? "reference" : "candidate").getAsJsonObject();
            choices.add(new CatalogSelectionDialog.Choice<>(row.get("status").getAsString() + " · " + group.get("family").getAsString(), group.get("subject").getAsString(), group.get("guidance").getAsString(), row));
        }
        var dialog = new CatalogSelectionDialog<>(project, "Saved Check Comparison", DeveloperChecksClient.comparisonSummary(result), choices);
        if (!dialog.showAndGet()) return;
        var selected = dialog.selected();
        if (selected.has("attempt")) {
            background(project, state, indicator -> {
                var run = DeveloperChecksClient.invoke(launch, root, session, "show", selected.get("attempt").getAsString(), null);
                ui(project, () -> present(project, state, launch, root, session, run, state.edits));
            });
        } else details(project, "workbench-check-comparison.json", selected.size() == 0 ? result : selected);
    }
    @FunctionalInterface private interface Work { void run(ProgressIndicator indicator) throws Exception; }
    private static void background(Project project, State state, Work work) {
        state.active = true;
        long generation = ++state.generation;
        new Task.Backgroundable(project, "Workbench saved-candidate check", true) {
            @Override public void run(@NotNull ProgressIndicator indicator) {
                try { work.run(indicator); }
                catch (Exception error) { ui(project, () -> WorkbenchNotifications.commandFailed(project, "Saved check unavailable; reopen the retained attempt", error)); }
                finally { ui(project, () -> { if (state.generation == generation) state.active = false; }); }
            }
        }.queue();
    }
    private static void reopenEnvironment(Project project, State state, CoreLaunch launch, Path root, String session) {
        String attempt = input(project, "Exact environment preparation ID", ""); if (attempt == null) return;
        background(project, state, indicator -> {
            var result = DeveloperChecksClient.invoke(launch, root, session, "environment-show", attempt, null);
            ui(project, () -> {
                String status = result.get("state").getAsString();
                if (status.equals("running")) {
                    if (Messages.showYesNoDialog(project, "Request cancellation and wait for Core process closure?", "Cancel Preparation?", null) == Messages.YES)
                        background(project, state, progress -> DeveloperChecksClient.invoke(launch, root, session, "environment-cancel", attempt, null));
                } else if (status.equals("needs-attention") || result.has("cleanup") && result.getAsJsonObject("cleanup").get("state").getAsString().equals("blocked")) {
                    if (Messages.showYesNoDialog(project, "Recover this preparation's exact process custody and private files?", "Recover Preparation?", null) == Messages.YES) {
                        String consent = result.has("request_id") ? result.get("request_id").getAsString() : result.getAsJsonObject("request").get("id").getAsString();
                        background(project, state, progress -> DeveloperChecksClient.invoke(launch, root, session, "environment-recover", attempt, consent));
                    }
                } else {
                    var file = new LightVirtualFile("workbench-environment.json", new GsonBuilder().setPrettyPrinting().create().toJson(result));
                    file.setWritable(false); FileEditorManager.getInstance(project).openFile(file, true);
                }
            });
        });
    }
    private static void prepareEnvironment(Project project, State state, CoreLaunch launch, Path root, String session) {
        var args = new ArrayList<>(List.of("context", "run", session, "--", "checks", "plan-environment"));
        for (String name : List.of("prism", "packwiz", "java", "accounts")) {
            String path = input(project, name.equals("accounts") ? "Prism accounts.json path to copy privately — never paste credentials" : "Exact native " + name + " executable path", "");
            if (path == null) return;
            args.add("--" + name + "=" + path);
        }
        String seed = input(project, "Optional existing mod seed directory (Cancel skips). Only matching hashes are copied.", "");
        if (seed != null) args.add("--seed=" + seed);
        background(project, state, indicator -> {
            String raw = CommandProcess.capture(launch, args, 32 * 1024 * 1024, 300, root.toString());
            var request = DeveloperChecksClient.validate(JsonParser.parseString(raw).getAsJsonObject(), root);
            ui(project, () -> {
                String attempt = request.get("attempt_id").getAsString();
                if (Messages.showYesNoDialog(project, "Candidate: " + request.get("candidate_id").getAsString() + "\n\n" + request.getAsJsonArray("effects") + "\n\nNo Minecraft execution. Trusted local tools, not a sandbox.\nReopen: " + attempt, "Prepare This Environment?", null) != Messages.YES) return;
                background(project, state, progress -> {
                    ExecutorService executor = Executors.newSingleThreadExecutor();
                    try {
                        Future<JsonObject> future = executor.submit(() -> DeveloperChecksClient.invoke(launch, root, session, "prepare-environment", attempt, request.get("id").getAsString()));
                        boolean cancelled = false;
                        JsonObject result;
                        while (true) {
                            try { result = future.get(200, TimeUnit.MILLISECONDS); break; }
                            catch (TimeoutException waiting) {
                                if ((progress.isCanceled() || project.isDisposed()) && !cancelled) {
                                    DeveloperChecksClient.invoke(launch, root, session, "environment-cancel", attempt, null);
                                    cancelled = true; progress.setText("Waiting for Core process closure and cleanup");
                                }
                            }
                        }
                        if (!result.get("state").getAsString().equals("ready")) throw new IllegalStateException("Environment " + result.get("state").getAsString() + "; reopen " + attempt);
                        ui(project, () -> choose(project, state, launch, root, session));
                    } finally { executor.shutdown(); }
                });
            });
        });
    }
    private static void importImage(Project project, State state, CoreLaunch launch, Path root, String session) {
        if (Messages.showYesNoDialog(project, "Import a self-contained native client and its Java toolchain? This copies local files; no launch or download.", "Import Native Runtime", null) != Messages.YES) return;
        String runtime = input(project, "Installed native client game directory", ""); if (runtime == null) return;
        String java = input(project, "Exact native JDK bin/java path", ""); if (java == null) return;
        String arguments = input(project, "Direct Java arguments as JSON. Use image-relative paths, --gameDir . and offline token 0; never paste account tokens.", ""); if (arguments == null) return;
        background(project, state, indicator -> {
            CommandProcess.capture(launch, List.of("context", "run", session, "--", "checks", "import-image", "--runtime-root=" + runtime, "--java=" + java, "--arguments-json=" + arguments), 32 * 1024 * 1024, 300, root.toString());
            ui(project, () -> choose(project, state, launch, root, session));
        });
    }
    private static void ui(Project project, Runnable work) { ApplicationManager.getApplication().invokeLater(() -> { if (!project.isDisposed()) work.run(); }); }
    @Override public void update(@NotNull AnActionEvent event) { event.getPresentation().setEnabledAndVisible(event.getProject() != null && WorkbenchProjectTrust.isTrusted(event.getProject())); }
    @Override public @NotNull ActionUpdateThread getActionUpdateThread() { return ActionUpdateThread.BGT; }
}
