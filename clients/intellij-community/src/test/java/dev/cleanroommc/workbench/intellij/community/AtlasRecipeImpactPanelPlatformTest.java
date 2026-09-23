package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.ide.util.PropertiesComponent;
import com.intellij.notification.Notification;
import com.intellij.notification.NotificationType;
import com.intellij.notification.Notifications;
import com.intellij.openapi.progress.impl.CoreProgressManager;
import com.intellij.openapi.util.Disposer;
import com.intellij.testFramework.HeavyPlatformTestCase;
import com.intellij.ui.components.JBLabel;
import com.intellij.util.ui.UIUtil;

import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JList;
import javax.swing.JSpinner;
import javax.swing.JTextField;
import javax.swing.JTree;
import javax.swing.SwingUtilities;
import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.TreePath;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicLong;

/** Real headless IntelliJ panel and asynchronous tasks with explicitly installed Core inputs. */
public final class AtlasRecipeImpactPanelPlatformTest extends HeavyPlatformTestCase {
    @Override protected boolean isCreateDirectoryBasedProject() { return true; }

    public void testSixInstalledHighlightsThroughNativePanelAndDisposalCancellation() throws Exception {
        assertTrue("The real Swing controls must be driven on the EDT", SwingUtilities.isEventDispatchThread());
        Path corpusPath = requiredPath("WORKBENCH_TEST_ATLAS_NATIVE_CORPUS");
        Path reportPath = requiredPath("WORKBENCH_TEST_ATLAS_NATIVE_REPORT");
        byte[] corpusBytes = Files.readAllBytes(corpusPath);
        JsonObject corpus = JsonParser.parseString(new String(corpusBytes, StandardCharsets.UTF_8)).getAsJsonObject();
        assertEquals("workbench-atlas-client-corpus-v1", corpus.get("format").getAsString());
        List<JsonObject> highlights = new ArrayList<>();
        for (var value : corpus.getAsJsonArray("cases")) {
            var row = value.getAsJsonObject();
            if (row.has("highlight") && row.get("highlight").getAsBoolean()) highlights.add(row);
        }
        assertEquals("Use the six independently selected installed CLI highlights", 6, highlights.size());
        String executable = corpus.get("executable").getAsString();
        assertTrue(Path.of(executable).isAbsolute());
        assertTrue(Files.isExecutable(Path.of(executable)));
        for (var row : highlights) {
            assertEquals("complete-finite", row.get("exploration").getAsString());
            assertEquals("The native panel lane requires actual installed CLI reference bytes",
                    "installed-cli", row.get("record_origin").getAsString());
        }

        // Existing platform fixture convention: preserve real asynchronous Backgroundable tasks.
        String modeKey = "intellij.progress.task.ignoreHeadless";
        String priorMode = System.getProperty(modeKey);
        System.setProperty(modeKey, "true");
        Disposer.register(getTestRootDisposable(), () -> {
            if (priorMode == null) System.clearProperty(modeKey);
            else System.setProperty(modeKey, priorMode);
        });
        assertTrue(CoreProgressManager.shouldKeepTasksAsynchronous());
        // A directory-based HeavyPlatform project can have only an in-memory
        // store path. Native ProcessBuilder needs its physical working directory.
        Path workspace = Path.of(getProject().getBasePath());
        assertTrue(workspace.isAbsolute());
        boolean workspaceExisted = Files.isDirectory(workspace);
        Files.createDirectories(workspace);
        assertTrue("The fixture must provide a physical native-process cwd", Files.isDirectory(workspace));
        CoreLocation.configure(getProject(), executable);
        PropertiesComponent.getInstance(getProject()).setValue(RecipeImpactPanel.GRAPH_PROPERTY,
                highlights.getFirst().get("graph").getAsString());

        JsonObject report = new JsonObject();
        report.addProperty("format", "workbench-atlas-intellij-native-panel-acceptance-v1");
        report.addProperty("state", "running");
        report.addProperty("host", "real-intellij-heavy-platform-fixture");
        report.addProperty("presentation", "headless-real-swing-panel");
        report.addProperty("task_scheduling", "platform-asynchronous");
        report.addProperty("visible_window_test", false);
        report.addProperty("installed_plugin_test", false);
        report.addProperty("json_dialog_exercised", false);
        report.addProperty("progress_cancel_button_exercised", false);
        report.addProperty("qualification", false);
        report.addProperty("corpus", corpusPath.toString());
        report.addProperty("corpus_sha256", sha(corpusBytes));
        report.addProperty("executable", executable);
        report.addProperty("executable_sha256", sha(Files.readAllBytes(Path.of(executable))));
        report.addProperty("product_class_source", String.valueOf(RecipeImpactPanel.class.getResource("RecipeImpactPanel.class")));
        report.addProperty("workspace", workspace.toString());
        report.addProperty("workspace_existed_before_fixture_setup", workspaceExisted);
        report.addProperty("workspace_is_physical_directory", Files.isDirectory(workspace));
        report.add("cases", new JsonArray());
        // Refuse to overwrite an earlier accepted or failed receipt.
        Files.writeString(reportPath, report + "\n", StandardOpenOption.CREATE_NEW);

        List<String> errors = new CopyOnWriteArrayList<>();
        getProject().getMessageBus().connect(getTestRootDisposable()).subscribe(Notifications.TOPIC,
                new Notifications() {
                    @Override public void notify(Notification notification) {
                        if (notification.getType() == NotificationType.ERROR) errors.add(notification.getContent());
                    }
                });
        RecipeImpactPanel panel = new RecipeImpactPanel(getProject());
        Disposer.register(getTestRootDisposable(), panel);
        boolean disposed = false;
        long began = System.nanoTime();
        try {
            JTextField graph = field(panel, "graphPath", JTextField.class);
            JTextField query = field(panel, "query", JTextField.class);
            JTextField selection = field(panel, "selectionId", JTextField.class);
            JList<?> results = field(panel, "results", JList.class);
            JButton search = field(panel, "search", JButton.class);
            JButton analyze = field(panel, "analyze", JButton.class);
            JCheckBox complete = field(panel, "completeExploration", JCheckBox.class);
            JSpinner depth = field(panel, "maxDepth", JSpinner.class);
            JSpinner nodes = field(panel, "maxNodes", JSpinner.class);
            JButton json = field(panel, "completeJson", JButton.class);
            JTree tree = field(panel, "tree", JTree.class);
            assertFalse(complete.isSelected());
            assertTrue(depth.isEnabled());
            assertTrue(nodes.isEnabled());
            assertFalse(json.isEnabled());
            complete.doClick();
            assertTrue(complete.isSelected());
            assertFalse(depth.isEnabled());
            assertFalse(nodes.isEnabled());
            Set<String> selectedIds = new HashSet<>();

            for (JsonObject row : highlights) {
                long started = System.nanoTime();
                String expectedSelection = row.get("selection_id").getAsString();
                assertTrue(selectedIds.add(expectedSelection));
                graph.setText(row.get("graph").getAsString());
                query.setText(row.get("query").getAsString());
                search.doClick();
                assertBusy(panel, search, analyze, complete, json);
                assertNull(field(panel, "currentCompleteImpact", AtlasCompleteRecipeImpact.class));
                awaitIdle(panel, errors);
                AtlasRecipeSearch searched = field(panel, "currentSearch", AtlasRecipeSearch.class);
                assertNotNull(status(panel), searched);
                assertEquals(row.get("graph").getAsString(), searched.context().root());
                int selectedIndex = -1;
                for (int index = 0; index < results.getModel().getSize(); index++) {
                    if (((AtlasRecipeSearch.Result) results.getModel().getElementAt(index)).selectionId()
                            .equals(expectedSelection)) {
                        assertEquals("Exact selection must appear only once", -1, selectedIndex);
                        selectedIndex = index;
                    }
                }
                assertTrue("Expected highlight missing from the actual 100-result panel search", selectedIndex >= 0);
                results.setSelectedIndex(selectedIndex);
                assertEquals(expectedSelection, selection.getText());
                analyze.doClick();
                assertBusy(panel, search, analyze, complete, json);
                awaitIdle(panel, errors);
                AtlasCompleteRecipeImpact impact = field(panel, "currentCompleteImpact", AtlasCompleteRecipeImpact.class);
                assertNotNull(status(panel), impact);
                assertNull(field(panel, "currentImpact", AtlasRecipeImpact.class));
                assertEquals(expectedSelection, impact.selectionId());
                assertEquals(searched.context().value(), impact.context().value());
                assertEquals(((AtlasRecipeSearch.Result) results.getSelectedValue()).value(), impact.selection());
                assertEquals("complete", impact.value().getAsJsonObject("exploration").get("status").getAsString());
                assertEquals("incomplete", impact.value().getAsJsonObject("evidence_completeness").get("status").getAsString());
                assertEquals(0, impact.value().getAsJsonArray("frontiers").size());
                assertFalse(impact.value().getAsJsonObject("summary").get("truncated").getAsBoolean());
                assertEquals(impact.statusText(), status(panel));
                assertTrue(status(panel).contains("viability unknown"));
                assertTrue(json.isEnabled());
                assertTrue(search.isEnabled());
                assertTrue(analyze.isEnabled());
                assertTrue(complete.isEnabled());
                assertFalse(depth.isEnabled());
                assertFalse(nodes.isEnabled());
                assertNull("Completed tasks must release the elapsed timer", field(panel, "elapsedTimer", Object.class));

                Path expectedPath = Path.of(row.get("impact_record").getAsString());
                byte[] expectedBytes = Files.readAllBytes(expectedPath);
                byte[] actualBytes = impact.rawJson().getBytes(StandardCharsets.UTF_8);
                assertEquals("Complete report bytes changed from the installed CLI reference", sha(expectedBytes), sha(actualBytes));
                assertTrue("The retained native report must preserve every original UTF-8 byte", Arrays.equals(expectedBytes, actualBytes));
                JsonArray expanded = new JsonArray();
                DefaultMutableTreeNode root = (DefaultMutableTreeNode) tree.getModel().getRoot();
                assertTrue(root.toString().contains(expectedSelection));
                assertEquals(impact.statusText(), root.getChildAt(0).toString());
                assertEquals(AtlasRecipeImpact.CLAIM_BOUNDARY, root.getChildAt(1).toString());
                for (String key : List.of("exploration", "evidence_completeness", "direct", "propagation", "unknowns")) {
                    DefaultMutableTreeNode branch = child(root, key + " · ");
                    int before = branch.getChildCount();
                    tree.expandPath(new TreePath(branch.getPath()));
                    UIUtil.dispatchAllInvocationEvents();
                    assertFalse("Expansion must call the production lazy-tree listener", containsPlaceholder(branch));
                    var expansion = new JsonObject();
                    expansion.addProperty("section", key);
                    expansion.addProperty("child_count_before", before);
                    expansion.addProperty("child_count_after", branch.getChildCount());
                    for (int index = 0; index < branch.getChildCount(); index++) {
                        var next = (DefaultMutableTreeNode) branch.getChildAt(index);
                        if (next.toString().startsWith("Remaining ")) {
                            tree.expandPath(new TreePath(next.getPath()));
                            UIUtil.dispatchAllInvocationEvents();
                            assertFalse(containsPlaceholder(next));
                            assertTrue(next.getChildCount() > 0);
                            expansion.addProperty("next_page_child_count", next.getChildCount());
                            break;
                        }
                    }
                    expanded.add(expansion);
                }
                JsonObject observed = new JsonObject();
                observed.add("id", row.get("id"));
                observed.addProperty("selection_id", expectedSelection);
                observed.addProperty("graph_set_id", impact.context().graphSetId());
                observed.addProperty("panel_search_limit", 100);
                observed.addProperty("selected_result_index", selectedIndex);
                observed.addProperty("status", status(panel));
                observed.addProperty("raw_report_reference", expectedPath.toString());
                observed.addProperty("raw_report_sha256", sha(actualBytes));
                observed.addProperty("raw_report_bytes", actualBytes.length);
                observed.addProperty("raw_report_bytes_equal", true);
                observed.addProperty("elapsed_seconds", (System.nanoTime() - started) / 1_000_000_000.0);
                observed.add("tree_expansions", expanded);
                report.getAsJsonArray("cases").add(observed);
                writeReport(reportPath, report);
                System.out.println("Atlas native panel completed: " + row.get("id").getAsString());
            }

            // Disposal is a real production cancellation boundary; no task/indicator replacement.
            Set<Long> existing = new HashSet<>();
            ProcessHandle.current().descendants().forEach(process -> existing.add(process.pid()));
            String cancelSelection = selection.getText();
            analyze.doClick();
            assertBusy(panel, search, analyze, complete, json);
            List<ProcessHandle> running = new ArrayList<>();
            while (running.isEmpty() && field(panel, "busy", Boolean.class)) {
                UIUtil.dispatchAllInvocationEvents();
                assertEmpty(errors);
                ProcessHandle.current().descendants().filter(process -> !existing.contains(process.pid()))
                        .filter(process -> Arrays.asList(process.info().arguments().orElse(new String[0]))
                                .contains(cancelSelection))
                        .forEach(running::add);
                if (running.isEmpty()) Thread.sleep(25);
            }
            assertFalse("A real installed impact process must be observed before disposal", running.isEmpty());
            Set<ProcessHandle> cancelledProcesses = new HashSet<>(running);
            for (ProcessHandle process : running) process.descendants().forEach(cancelledProcesses::add);
            long generation = field(panel, "generation", AtomicLong.class).get();
            Disposer.dispose(panel);
            disposed = true;
            assertTrue(field(panel, "disposed", Boolean.class));
            assertEquals(generation + 1, field(panel, "generation", AtomicLong.class).get());
            while (cancelledProcesses.stream().anyMatch(ProcessHandle::isAlive)) {
                UIUtil.dispatchAllInvocationEvents();
                Thread.sleep(25);
            }
            UIUtil.dispatchAllInvocationEvents();
            assertNull(field(panel, "currentCompleteImpact", AtlasCompleteRecipeImpact.class));
            assertNull(field(panel, "currentImpact", AtlasRecipeImpact.class));
            assertNull(field(panel, "elapsedTimer", Object.class));
            assertEmpty(errors);
            JsonObject cancellation = new JsonObject();
            cancellation.addProperty("kind", "panel-disposal");
            cancellation.addProperty("completed_report_published", false);
            cancellation.addProperty("observed_processes_terminated", true);
            var pids = new JsonArray();
            cancelledProcesses.stream().map(ProcessHandle::pid).sorted().forEach(pids::add);
            cancellation.add("observed_process_ids", pids);
            report.add("cancellation", cancellation);
            report.addProperty("state", "passed");
        } catch (Throwable error) {
            report.addProperty("state", "failed");
            report.addProperty("failure", error.toString());
            throw error;
        } finally {
            if (!disposed) Disposer.dispose(panel);
            report.addProperty("elapsed_seconds", (System.nanoTime() - began) / 1_000_000_000.0);
            writeReport(reportPath, report);
        }
    }

    private static Path requiredPath(String name) {
        String value = System.getenv(name);
        assertNotNull("Explicit installed Atlas panel input required: " + name, value);
        assertFalse(value.isBlank());
        Path result = Path.of(value);
        assertTrue(name + " must be absolute", result.isAbsolute());
        return result;
    }

    private static <T> T field(Object target, String name, Class<T> type) throws Exception {
        Field field = target.getClass().getDeclaredField(name);
        field.setAccessible(true);
        return type.cast(field.get(target));
    }

    private static String status(RecipeImpactPanel panel) throws Exception {
        return field(panel, "status", JBLabel.class).getText();
    }

    private static void assertBusy(RecipeImpactPanel panel, JButton search, JButton analyze,
                                   JCheckBox complete, JButton json) throws Exception {
        assertTrue("Real asynchronous task must enter the busy state", field(panel, "busy", Boolean.class));
        assertFalse(search.isEnabled());
        assertFalse(analyze.isEnabled());
        assertFalse(complete.isEnabled());
        assertFalse(json.isEnabled());
    }

    private static void awaitIdle(RecipeImpactPanel panel, List<String> errors) throws Exception {
        // Complete mode has no arbitrary journey timeout or semantic work budget.
        while (field(panel, "busy", Boolean.class)) {
            UIUtil.dispatchAllInvocationEvents();
            assertEmpty(errors);
            Thread.sleep(25);
        }
        UIUtil.dispatchAllInvocationEvents();
        assertEmpty(errors);
    }

    private static DefaultMutableTreeNode child(DefaultMutableTreeNode parent, String prefix) {
        for (int index = 0; index < parent.getChildCount(); index++) {
            var candidate = (DefaultMutableTreeNode) parent.getChildAt(index);
            if (candidate.toString().startsWith(prefix)) return candidate;
        }
        throw new AssertionError("Missing actual tree branch: " + prefix);
    }

    private static boolean containsPlaceholder(DefaultMutableTreeNode node) {
        for (int index = 0; index < node.getChildCount(); index++) {
            if (node.getChildAt(index).toString().equals("Expand to inspect retained values")) return true;
        }
        return false;
    }

    private static String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static void writeReport(Path path, JsonObject report) throws Exception {
        Files.writeString(path, new GsonBuilder().disableHtmlEscaping().setPrettyPrinting().create().toJson(report) + "\n",
                StandardOpenOption.TRUNCATE_EXISTING);
    }
}
