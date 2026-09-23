package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.ide.util.PropertiesComponent;
import com.intellij.openapi.actionSystem.ActionManager;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.actionSystem.CommonDataKeys;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.project.DumbService;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.startup.StartupActivity;
import java.awt.Component;
import java.awt.Container;
import java.awt.Dialog;
import java.awt.Frame;
import java.awt.Window;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import javax.imageio.ImageIO;
import javax.swing.AbstractButton;
import javax.swing.JList;
import javax.swing.SwingUtilities;
import javax.swing.Timer;
import org.jetbrains.annotations.NotNull;

/** Opt-in companion test plugin: actual IDE windows, registered action and native Core. */
public final class MaterialDeliveryWindowProbe implements StartupActivity.DumbAware {
    private JsonObject fixture, target;
    private Path output, attempts, attempt;
    private Project project;
    private final Set<Path> existing = new HashSet<>();
    private final Set<Window> handled = new HashSet<>();
    private final Set<String> milestones = new HashSet<>();
    private boolean capturing, navigated, firstPresented;
    private Timer timer;
    private int resultStage;

    @Override public void runActivity(@NotNull Project opened) {
        String selected = System.getProperty("workbench.delivery.fixture");
        if (selected == null) return;
        try {
            fixture = JsonParser.parseString(Files.readString(Path.of(selected))).getAsJsonObject();
            if (!Path.of(opened.getBasePath()).equals(Path.of(fixture.get("pack").getAsString()))) return;
            project = opened;
            com.intellij.ide.GeneralSettings.getInstance().setConfirmExit(false);
            output = Path.of(fixture.getAsJsonObject("deliveryMeasurement").get("output").getAsString()); Files.createDirectories(output);
            attempts = Path.of(fixture.get("coreState").getAsString(), "product-spine/developer-checks/.workbench/check-attempts");
            if (Files.exists(attempts)) try (var paths = Files.list(attempts)) { paths.forEach(existing::add); }
            CoreLocation.configure(project, fixture.get("executable").getAsString());
            var selectedContext = Class.forName("dev.cleanroommc.workbench.intellij.community.DeveloperContextSelection");
            var setter = selectedContext.getDeclaredMethod("set", Project.class, String.class); setter.setAccessible(true);
            setter.invoke(null, project, fixture.get("session").getAsString());
            // Project import can enter dumb mode after postStartupActivity and
            // dismiss dialogs it opened too early. Startup is outside the Run
            // action measurement; wait for the disposable project to settle.
            ApplicationManager.getApplication().invokeLater(() -> {
                var ready = new Timer(15000, ignored -> DumbService.getInstance(project).runWhenSmart(() -> {
                    var editors = FileEditorManager.getInstance(project);
                    for (var file : editors.getOpenFiles()) editors.closeFile(file);
                    start();
                }));
                ready.setRepeats(false); ready.start();
            });
        } catch (Throwable error) { fail(error); }
    }
    private void event(String phase, JsonObject fields) throws Exception {
        var value = fields.deepCopy(); value.addProperty("phase", phase); value.addProperty("ns", Long.toString(System.nanoTime()));
        Files.writeString(output.resolve("ide-events.jsonl"), value + "\n", StandardOpenOption.CREATE, StandardOpenOption.APPEND);
    }
    private void start() {
        try {
            timer = new Timer(100, ignored -> tick()); timer.start(); event("run-action", new JsonObject());
            var action = ActionManager.getInstance().getAction("Workbench.RunSavedChecks");
            action.actionPerformed(AnActionEvent.createFromAnAction(action, null, "windowed-material-delivery",
                    key -> CommonDataKeys.PROJECT.is(key) ? project : null));
        } catch (Throwable error) { fail(error); }
    }
    private static List<Component> descendants(Component component) {
        var result = new ArrayList<Component>(); result.add(component);
        if (component instanceof Container container) for (var child : container.getComponents()) result.addAll(descendants(child));
        return result;
    }
    private static Object field(Object value, String method) throws Exception {
        var accessor = value.getClass().getDeclaredMethod(method); accessor.setAccessible(true); return accessor.invoke(value);
    }
    private void click(Window window) {
        for (var component : descendants(window)) if (component instanceof AbstractButton button && button.isShowing()
                && List.of("Continue", "Yes", "Run this exact material check", "Continue with complete capture").contains(button.getText())) {
            try { var row = new JsonObject(); row.addProperty("button", button.getText()); event("dialog-accept", row); }
            catch (Exception error) { throw new IllegalStateException(error); }
            button.doClick(); return;
        }
        throw new IllegalStateException("No explicit acceptance button in " + window);
    }
    private void capture(String phase, Window window, Runnable after) throws Exception {
        capturing = true;
        String title = window instanceof Dialog dialog ? dialog.getTitle() : ((Frame) window).getTitle();
        var settling = new Timer(250, ignored -> {
            new Thread(() -> {
                try {
                    long before = System.nanoTime();
                    Path destination = output.resolve(phase + ".png");
                    if (Files.exists(destination)) throw new IllegalStateException("capture already exists");
                    String observer = fixture.getAsJsonObject("deliveryMeasurement").get("observer").getAsString();
                    var capture = new ProcessBuilder("python3", observer, "--title", title, "--capture", destination.toString())
                            .redirectError(output.resolve(phase + "-capture.log").toFile()).start();
                    String captureReceipt = new String(capture.getInputStream().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
                    if (capture.waitFor() != 0) throw new IllegalStateException("window capture failed: " + captureReceipt);
                    long observed = System.nanoTime();
                    var pixels = ImageIO.read(destination.toFile());
                    int first = pixels.getRGB(0, 0); boolean painted = false;
                    for (int y = 0; y < pixels.getHeight() && !painted; y += 5)
                        for (int x = 0; x < pixels.getWidth(); x += 5) if (pixels.getRGB(x, y) != first) { painted = true; break; }
                    if (!painted) throw new IllegalStateException("window capture contains no visible IDE content");
                    SwingUtilities.invokeLater(() -> {
                        try {
                            var receipt = new JsonObject(); receipt.addProperty("capture", destination.toString());
                            receipt.addProperty("before_ns", Long.toString(before)); receipt.addProperty("after_ns", Long.toString(observed));
                            if (target != null) receipt.add("finding", target); event(phase, receipt);
                            capturing = false; after.run();
                        } catch (Throwable error) { fail(error); }
                    });
                } catch (Throwable error) { SwingUtilities.invokeLater(() -> fail(error)); }
            }, "workbench-delivery-window-observer").start();
        });
        settling.setRepeats(false); settling.start();
    }
    private void tick() {
        try {
            if (attempt == null && Files.exists(attempts)) try (var paths = Files.list(attempts)) {
                var added = paths.filter(path -> !existing.contains(path) && path.getFileName().toString().startsWith("material-check-")).toList();
                if (added.size() > 1) throw new IllegalStateException("ambiguous new check attempt");
                if (!added.isEmpty()) attempt = added.getFirst();
            }
            if (attempt != null) for (String file : List.of("native-process/capture.json", "diagnostic-delivery/publication.json", "snapshot/publication.json")) {
                if (Files.exists(attempt.resolve(file)) && milestones.add(file)) { var row = new JsonObject(); row.addProperty("attempt", attempt.getFileName().toString()); event(file, row); }
            }
            if (capturing) return;
            for (var window : Window.getWindows()) {
                if (!window.isShowing() || handled.contains(window) || !(window instanceof Dialog dialog)) continue;
                JList<?> list = null;
                for (var component : descendants(window)) if (component instanceof JList<?> candidate && candidate.getModel().getSize() > 0
                        && candidate.getModel().getElementAt(0).getClass().getName().contains("CatalogSelectionDialog$Choice")) { list = candidate; break; }
                if (list == null) {
                    if (dialog.getTitle().equals("Check Storage Before Capture")) {
                        handled.add(window); capture("storage-notice", window, () -> click(window)); return;
                    }
                    if (dialog.getTitle().equals("Saved Checks") || dialog.getTitle().equals("Run This Exact Material Check?")) {
                        handled.add(window); if (dialog.getTitle().startsWith("Run This")) event("confirmation", new JsonObject()); click(window);
                    }
                    continue;
                }
                int selected = -1, remaining = -1;
                for (int index = 0; index < list.getModel().getSize(); index++) {
                    Object choice = list.getModel().getElementAt(index); String title = (String) field(choice, "title"); Object value = field(choice, "value");
                    if (title.equals("Load remaining findings")) remaining = index;
                    if (dialog.getTitle().equals("Saved Checks") && title.equals("Run Axiom material preflight")) selected = index;
                    if (dialog.getTitle().equals("Axiom Material Context") && value instanceof JsonObject row
                            && row.has("id") && row.get("id").equals(fixture.getAsJsonObject("options").get("context"))) selected = index;
                    if (dialog.getTitle().equals("Material Diagnostic") && title.equals("Open identical saved working copy")) selected = index;
                    if (dialog.getTitle().startsWith("Axiom:") && value instanceof JsonObject row && row.has("finding")) {
                        var finding = row.getAsJsonObject("finding");
                        if (selected < 0 && finding.get("side").getAsString().equals("candidate") && finding.has("severity")
                                && finding.get("severity").getAsString().equalsIgnoreCase(fixture.getAsJsonObject("deliveryMeasurement").get("severity").getAsString())
                                && finding.get("location").isJsonObject()
                                && finding.getAsJsonObject("location").get("path").equals(fixture.getAsJsonObject("deliveryMeasurement").get("sourcePath"))
                                && finding.getAsJsonObject("location").getAsJsonObject("start").get("line").equals(fixture.getAsJsonObject("deliveryMeasurement").get("sourceLine"))) {
                            selected = index; target = finding;
                        }
                    }
                }
                if (selected < 0) selected = remaining;
                if (selected < 0) throw new IllegalStateException("Missing expected selection in " + dialog.getTitle());
                list.setSelectedIndex(selected); list.ensureIndexIsVisible(selected); handled.add(window);
                var row = new JsonObject(); row.addProperty("title", dialog.getTitle()); row.addProperty("selection", String.valueOf(field(list.getSelectedValue(), "title"))); event("dialog-model", row);
                Runnable accept = () -> {
                    try {
                        if (dialog.getTitle().startsWith("Axiom:") && target != null) capture("visible-presentation", window, () -> click(window));
                        else click(window);
                    } catch (Throwable error) { fail(error); }
                };
                if (dialog.getTitle().startsWith("Axiom:") && !firstPresented) {
                    firstPresented = true; capture("visible-first-result", window, accept);
                } else accept.run();
                return;
            }
            if (target != null && !navigated) {
                var editor = FileEditorManager.getInstance(project).getSelectedTextEditor();
                var location = target.getAsJsonObject("location");
                Path selectedPath = Path.of(fixture.get("pack").getAsString()).resolve(location.get("path").getAsString());
                var selectedFile = editor == null ? null : com.intellij.openapi.fileEditor.FileDocumentManager.getInstance().getFile(editor.getDocument());
                if (selectedFile != null && selectedFile.toNioPath().equals(selectedPath)
                        && editor.getCaretModel().getLogicalPosition().line == location.getAsJsonObject("start").get("line").getAsInt() - 1) {
                    String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(selectedPath)));
                    if (!digest.equals(location.get("sha256").getAsString())) throw new IllegalStateException("navigation source changed");
                    navigated = true; event("source-open", target);
                    capture("visible-source", SwingUtilities.getWindowAncestor(editor.getComponent()), () -> { }); return;
                }
            }
            if (!navigated) for (var window : Window.getWindows()) {
                if (!(window instanceof Frame) || !window.isShowing()) continue;
                var components = descendants(window);
                var panel = components.stream().filter(c -> "Axiom Results".equals(c.getName()) && c.isShowing()).findFirst().orElse(null);
                if (panel == null) continue;
                var contents = descendants(panel);
                JList<?> list = null; javax.swing.JComboBox<?> groups = null;
                for (var component : contents) {
                    if (component instanceof JList<?> candidate && "Axiom findings".equals(candidate.getName())) list = candidate;
                    if (component instanceof javax.swing.JComboBox<?> candidate) groups = candidate;
                }
                if (list == null || groups == null) throw new IllegalStateException("Native findings list and group selector must exist");
                if (resultStage == 0) {
                    firstPresented = true; resultStage = 1;
                    var model = new JsonObject(); var labels = new com.google.gson.JsonArray();
                    for (var component : contents) if (component instanceof javax.swing.JLabel label) labels.add(label.getText());
                    model.add("labels", labels); event("result-summary", model);
                    capture("visible-first-result", window, () -> {}); return;
                }
                if (resultStage == 1) {
                    boolean unlocated = false;
                    for (int i = 0; i < groups.getItemCount(); i++) if ("error-unlocated".equals(groups.getItemAt(i))) unlocated = true;
                    if (unlocated) {
                        if (!"error-unlocated".equals(groups.getSelectedItem())) { groups.setSelectedItem("error-unlocated"); return; }
                        if (list.getModel().getSize() == 0) return;
                        var row = (JsonObject) list.getModel().getElementAt(0);
                        if (row.get("location").isJsonObject()) throw new IllegalStateException("Unlocated group contained a source location");
                        resultStage = 2; event("unlocated-errors-accessible", row); capture("visible-unlocated-errors", window, () -> {}); return;
                    }
                    resultStage = 2;
                }
                if (resultStage == 2) {
                    String key = fixture.getAsJsonObject("deliveryMeasurement").get("severity").getAsString() + "-located";
                    if (!key.equals(groups.getSelectedItem())) { groups.setSelectedItem(key); return; }
                    for (int i = 0; i < list.getModel().getSize(); i++) {
                        var finding = (JsonObject) list.getModel().getElementAt(i);
                        if (!finding.get("location").isJsonObject()) continue;
                        var loc = finding.getAsJsonObject("location");
                        if (loc.get("path").equals(fixture.getAsJsonObject("deliveryMeasurement").get("sourcePath")) && loc.getAsJsonObject("start").get("line").equals(fixture.getAsJsonObject("deliveryMeasurement").get("sourceLine"))) {
                            list.setSelectedIndex(i); list.ensureIndexIsVisible(i); target = finding; resultStage = 3; event("diagnostic-selected", finding);
                            capture("visible-presentation", window, () -> {
                                for (var component : contents) if (component instanceof AbstractButton button && "Open Source".equals(button.getText())) { button.doClick(); return; }
                                throw new IllegalStateException("Missing direct source action");
                            }); return;
                        }
                    }
                    for (var component : contents) if (component instanceof AbstractButton button && "Load More".equals(button.getText()) && button.isEnabled()) { button.doClick(); return; }
                }
            }
            if (navigated && milestones.contains("snapshot/publication.json") && !capturing) {
                String latest = PropertiesComponent.getInstance(project).getValue("workbench.lastCompletedMaterial");
                if (latest != null && JsonParser.parseString(latest).getAsJsonObject().get("attempt").getAsString().equals(attempt.getFileName().toString())) {
                    if (milestones.add("visible-ready")) {
                        var window = SwingUtilities.getWindowAncestor(FileEditorManager.getInstance(project).getSelectedTextEditor().getComponent());
                        capture("visible-ready", window, () -> {}); return;
                    }
                    timer.stop(); var report = new JsonObject(); report.addProperty("format", "workbench-ide-delivery-measurement-v1");
                    report.addProperty("ide", "intellij"); report.addProperty("attempt", attempt.getFileName().toString()); report.add("target", target);
                    report.addProperty("navigationVerified", true); report.addProperty("visibleEvidenceRequiresInspection", true); report.addProperty("qualification", false);
                    event("snapshot-finalized", report); Files.writeString(output.resolve("ide-report.json"), report + "\n", StandardOpenOption.CREATE_NEW);
                    ApplicationManager.getApplication().exit();
                }
            }
        } catch (Throwable error) { fail(error); }
    }
    private void fail(Throwable error) {
        if (timer != null) timer.stop();
        error.printStackTrace();
        try { if (output != null) { var row = new JsonObject(); row.addProperty("message", error.toString()); event("error", row); } } catch (Exception ignored) { }
    }
}
