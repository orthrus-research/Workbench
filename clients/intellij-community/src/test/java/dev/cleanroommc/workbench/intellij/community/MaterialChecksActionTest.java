package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.notification.Notification;
import com.intellij.notification.NotificationType;
import com.intellij.notification.Notifications;
import com.intellij.openapi.actionSystem.ActionManager;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.actionSystem.CommonDataKeys;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.command.WriteCommandAction;
import com.intellij.openapi.editor.Document;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.ui.DialogWrapperPeer;
import com.intellij.openapi.ui.DialogWrapperPeerFactory;
import com.intellij.openapi.ui.impl.DialogWrapperPeerFactoryImpl;
import com.intellij.openapi.ui.impl.DialogWrapperPeerImpl;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.ui.TestDialogManager;
import com.intellij.openapi.vfs.LocalFileSystem;
import com.intellij.testFramework.HeavyPlatformTestCase;
import com.intellij.testFramework.PlatformTestUtil;
import com.intellij.testFramework.ServiceContainerUtil;
import com.intellij.ui.UiInterceptors;
import com.intellij.ui.JBColor;
import com.intellij.ui.awt.RelativePoint;
import javax.swing.JList;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Queue;
import java.util.function.Predicate;
import java.util.regex.Pattern;

/** Registered action, real dialogs and background tasks; scripted selections, no mocks of Core/native execution. */
public final class MaterialChecksActionTest extends HeavyPlatformTestCase {
    @Override protected boolean isCreateDirectoryBasedProject() { return true; }
    private record Step(String title, Predicate<CatalogSelectionDialog.Choice<?>> pick) { }
    private static Step select(String title, String choice) { return new Step(title, row -> row.title().equals(choice)); }
    private static Step nativeFinding(String side, String path, int line, String channel) {
        return new Step("Axiom:", choice -> {
            if (!(choice.value() instanceof JsonObject value) || !value.has("finding")) return false;
            var finding = value.getAsJsonObject("finding"); var location = MaterialChecksClient.object(finding, "location");
            return MaterialChecksClient.text(finding, "side", "").equals(side) && MaterialChecksClient.text(finding, "channel", "").equals(channel)
                    && MaterialChecksClient.text(finding, "severity", "").equals("error")
                    && MaterialChecksClient.text(location, "path", "").equals(path)
                    && location.has("start") && location.getAsJsonObject("start").get("line").getAsInt() == line;
        });
    }
    private void edit(Document document, String text) {
        WriteCommandAction.runWriteCommandAction(getProject(), () -> document.setText(text));
        FileDocumentManager.getInstance().saveDocument(document);
    }
    public void testRegisteredMaterialActionPreparationConsentExecutionAndReopen() throws Exception {
        // The platform otherwise runs Backgroundable tasks synchronously in headless
        // tests. Use its normal asynchronous scheduler to observe in-flight editor UI.
        String taskModeKey = "intellij.progress.task.ignoreHeadless";
        String previousTaskMode = System.getProperty(taskModeKey);
        System.setProperty(taskModeKey, "true");
        com.intellij.openapi.util.Disposer.register(getTestRootDisposable(), () -> {
            if (previousTaskMode == null) System.clearProperty(taskModeKey);
            else System.setProperty(taskModeKey, previousTaskMode);
        });
        assertTrue(com.intellij.openapi.progress.impl.CoreProgressManager.shouldKeepTasksAsynchronous());
        String fixturePath = System.getenv("WORKBENCH_TEST_MATERIAL_ACTION_FIXTURE");
        assertNotNull("Explicit installed native action fixture required (selected by Gradle opt-in)", fixturePath);
        var fixture = JsonParser.parseString(Files.readString(Path.of(fixturePath))).getAsJsonObject();
        assertEquals("prepared-not-qualified", fixture.get("state").getAsString());
        var savedSource = fixture.getAsJsonObject("savedSource");
        String sourcePath = savedSource.get("path").getAsString(); int sourceLine = savedSource.get("line").getAsInt();
        assertTrue(sourceLine > 0);
        boolean diagnosticsOnly = fixture.getAsJsonObject("options").get("intent").getAsString().isEmpty();
        boolean singleRerun = MaterialChecksClient.text(fixture, "comparisonMode", "paired").equals("single-rerun");
        if (singleRerun) assertTrue("Single rerun requires measured full-pack comparison overflow", fixture.has("comparisonOverflowEvidence"));
        Path output = Path.of(System.getenv("WORKBENCH_TEST_MATERIAL_ACTION_REPORT")); assertFalse(Files.exists(output));
        Path root = Path.of(getProject().getBasePath()); Path sourcePack = Path.of(fixture.get("pack").getAsString());
        assertFalse(root.startsWith(Path.of(System.getProperty("user.dir"))));
        for (String directory : List.of("groovy", "config")) try (var inputs = Files.walk(sourcePack.resolve(directory))) {
            for (var input : inputs.filter(Files::isRegularFile).toList()) {
                Path target = root.resolve(sourcePack.relativize(input)); Files.createDirectories(target.getParent()); Files.copy(input, target);
            }
        }
        for (String name : diagnosticsOnly ? List.of("pack.toml", "index.toml") : List.of("intent.json", "pack.toml", "index.toml"))
            Files.copy(sourcePack.resolve(name), root.resolve(name));
        Files.createDirectories(root.resolve("mods")); Files.createDirectories(root.resolve("config"));
        Files.writeString(root.resolve(".gitignore"), "/.idea/\n/*.iml\n/*.ipr\n/*.iws\n");
        for (var args : List.of(List.of("git", "init", "-q"), List.of("git", "-c", "core.autocrlf=false", "add", "."), List.of("git", "-c", "user.name=Workbench fixture", "-c", "user.email=fixture@invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "Material action fixture"))) {
            var child = new ProcessBuilder(args).directory(root.toFile()).start(); assertEquals(0, child.waitFor());
        }
        var file = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(root.resolve(sourcePath)); assertNotNull(file);
        FileEditorManager.getInstance(getProject()).openTextEditor(new OpenFileDescriptor(getProject(), file), true);
        var document = FileDocumentManager.getInstance().getDocument(file); String original = document.getText();
        String anchor = savedSource.get("anchor").getAsString();
        assertEquals(original.indexOf(anchor), original.lastIndexOf(anchor));
        String bad = original.replace(anchor, savedSource.get("replacement").getAsString());
        String corrected = fixture.has("correctedSource") ? original.replace(fixture.getAsJsonObject("correctedSource").get("anchor").getAsString(), fixture.getAsJsonObject("correctedSource").get("replacement").getAsString()) : original;
        var correctedSha = fixture.has("correctedSource") ? fixture.getAsJsonObject("correctedSource").get("sha256") : savedSource.get("sha256");
        assertFalse(bad.equals(original)); edit(document, bad);
        CoreLocation.configure(getProject(), fixture.get("executable").getAsString());
        DeveloperContextSelection.set(getProject(), null); assertTrue(WorkbenchProjectTrust.isTrusted(getProject()));
        var report = new JsonObject(); report.addProperty("state", "running"); report.addProperty("host", "registered-intellij-action");
        report.addProperty("dialogs", "real-models-scripted-platform-interceptors"); report.addProperty("qualification", false);
        report.addProperty("minecraftLaunched", false); report.addProperty("workspace", root.toString());
        report.addProperty("taskScheduling", "platform-asynchronous");
        report.add("dialogsObserved", new JsonArray()); report.add("confirmations", new JsonArray()); report.add("ownerReads", new JsonArray());
        Queue<Step> steps = new ArrayDeque<>(); List<String> errors = new ArrayList<>(); boolean[] allowExecution = {false};
        var action = ActionManager.getInstance().getAction("Workbench.RunSavedChecks"); assertTrue(action instanceof RunSavedChecksAction);
        report.addProperty("actionClassSource", String.valueOf(action.getClass().getResource("RunSavedChecksAction.class")));
        var state = RunSavedChecksAction.state(getProject());
        // The platform's unit-test HeadlessDialog hardcodes isModal=false. Keep
        // its native peer and models, supplying only modality for our intercepted
        // selectors. This is not a visible-window or manual-gesture test.
        ServiceContainerUtil.replaceService(ApplicationManager.getApplication(), DialogWrapperPeerFactory.class,
                new DialogWrapperPeerFactoryImpl() {
                    @Override public DialogWrapperPeer createPeer(DialogWrapper wrapper, com.intellij.openapi.project.Project project,
                            boolean canBeParent, DialogWrapper.IdeModalityType modality) {
                        if (!(wrapper instanceof CatalogSelectionDialog)) return super.createPeer(wrapper, project, canBeParent, modality);
                        return new DialogWrapperPeerImpl(wrapper, project, canBeParent, modality) {
                            @Override public boolean isModal() { return modality != DialogWrapper.IdeModalityType.MODELESS; }
                        };
                    }
                }, getTestRootDisposable());
        report.addProperty("dialogPeer", "platform-headless-with-test-modality-for-intercepted-selectors");
        getProject().getMessageBus().connect(getTestRootDisposable()).subscribe(Notifications.TOPIC, new Notifications() {
            @Override public void notify(Notification notification) { if (notification.getType() == NotificationType.ERROR) errors.add(notification.getContent()); }
        });
        UiInterceptors.registerPersistent(getTestRootDisposable(), new UiInterceptors.PersistentUiInterceptor<DialogWrapper>(DialogWrapper.class) {
            @Override public boolean shouldIntercept(DialogWrapper dialog) { return dialog instanceof CatalogSelectionDialog; }
            @Override protected void doIntercept(DialogWrapper dialog, RelativePoint point) {
                try {
                    assertFalse("Unexpected modal selection: " + dialog.getTitle(), steps.isEmpty());
                    var list = (JList<?>) dialog.getPreferredFocusedComponent(); int selected = -1;
                    boolean load = false;
                    for (int i = 0; i < list.getModel().getSize(); i++) if (((CatalogSelectionDialog.Choice<?>) list.getModel().getElementAt(i)).title().equals("Load remaining findings")) load = true;
                    Step step = load ? select("Axiom:", "Load remaining findings") : steps.remove();
                    assertTrue(dialog.getTitle(), dialog.getTitle().startsWith(step.title()));
                    for (int i = 0; i < list.getModel().getSize(); i++) {
                        if (step.pick().test((CatalogSelectionDialog.Choice<?>) list.getModel().getElementAt(i))) { selected = i; break; }
                    }
                    assertTrue("Missing expected choice in " + dialog.getTitle(), selected >= 0); list.setSelectedIndex(selected);
                    var observed = new JsonObject(); observed.addProperty("title", dialog.getTitle()); observed.addProperty("selection", ((CatalogSelectionDialog.Choice<?>) list.getSelectedValue()).title());
        report.getAsJsonArray("dialogsObserved").add(observed); dialog.close(DialogWrapper.OK_EXIT_CODE);
                } catch (Throwable error) { errors.add(error.toString()); dialog.close(DialogWrapper.CANCEL_EXIT_CODE); }
            }
        });
        var priorDialog = TestDialogManager.setTestDialog(message -> {
            if (message.startsWith("Create a developer context") || message.startsWith("Use the selected Work Session")) return Messages.YES;
            var match = Pattern.compile("Request: (material-check-request:sha256:[0-9a-f]{64})").matcher(message);
            assertTrue("Unexpected confirmation: " + message, match.find());
            assertTrue(message.contains("unsaved edits are excluded")); assertTrue(message.contains("not yet qualified"));
            var consent = new JsonObject(); consent.addProperty("requestId", match.group(1)); consent.addProperty("accepted", allowExecution[0]);
            report.getAsJsonArray("confirmations").add(consent); return allowExecution[0] ? Messages.YES : Messages.NO;
        });
        List<String> materialInputPrompts = new ArrayList<>();
        var priorInput = TestDialogManager.setTestInputDialog(message -> {
            if (message.equals("Pack profile")) return "supersymmetry";
            if (message.equals("Platform profile")) return "cleanroom";
            if (message.equals("Profile variant")) return "cleanroom-provisional";
            String key = message.startsWith("Complete program") ? "programRoot" : message.startsWith("Saved material") ? "intent"
                    : message.startsWith("Explicit installed") ? "engineHome" : message.startsWith("Selected local") ? "runtimeHome"
                    : message.startsWith("Selected Cleanroom") ? "java" : null;
            assertNotNull("Unexpected input: " + message, key); materialInputPrompts.add(key);
            return fixture.getAsJsonObject("options").get(key).getAsString();
        });
        class Journey {
            JsonObject expectedHistorical;
            boolean historicalWhileRunning;
            void run(Step... selections) throws Exception {
                assertFalse(state.active); steps.addAll(List.of(selections));
                action.actionPerformed(AnActionEvent.createFromAnAction(action, null, "material-acceptance", key -> CommonDataKeys.PROJECT.is(key) ? getProject() : null));
                while (errors.isEmpty() && (state.active || state.materialExecution || !steps.isEmpty())) {
                    com.intellij.util.ui.UIUtil.dispatchAllInvocationEvents();
                    if (state.active && expectedHistorical != null && !historicalWhileRunning) {
                        var editor = FileEditorManager.getInstance(getProject()).getSelectedTextEditor();
                        String text = editor == null ? "" : editor.getDocument().getText();
                        if (text.startsWith("Previous completed check — historical while a new check runs")) {
                            for (String field : List.of("attempt_id", "candidate_id", "context_id", "snapshot_id"))
                                assertTrue("Historical view lost " + field, text.contains(expectedHistorical.get(field).getAsString()));
                            assertEmpty(state.marks);
                            historicalWhileRunning = true;
                            report.addProperty("historicalWhileRunning", true);
                            report.addProperty("historicalSummary", text);
                        }
                    }
                    Thread.sleep(20);
                }
                assertEmpty(errors); assertEmpty(steps);
            }
            JsonObject read(String kind, String attempt) throws Exception {
                var result = MaterialChecksClient.invoke(CoreLaunch.resolve(fixture.get("executable").getAsString()), root, DeveloperContextSelection.get(getProject()), kind, attempt, null, null);
                if (MaterialChecksClient.text(result, "format", "").equals(MaterialSnapshotClient.VIEW) && !result.get("snapshot_id").isJsonNull()) {
                    while (!result.getAsJsonObject("finding_page").get("complete").getAsBoolean()) {
                        var query = MaterialSnapshotClient.nextFindingsQuery(result, true);
                        var page = MaterialChecksClient.invoke(CoreLaunch.resolve(fixture.get("executable").getAsString()), root, DeveloperContextSelection.get(getProject()), "query", attempt, null, query);
                        assertEquals("ready", page.get("state").getAsString());
                        result.getAsJsonArray("findings").addAll(MaterialSnapshotClient.findings(page));
                        for (var label : page.getAsJsonObject("_presentation").getAsJsonObject("finding_labels").entrySet()) result.getAsJsonObject("_presentation").getAsJsonObject("finding_labels").add(label.getKey(), label.getValue());
                        result.add("finding_page", page);
                    }
                }
                var row = new JsonObject(); row.addProperty("action", kind); row.add("id", result.get("id")); row.add("snapshot", result.get("snapshot_id"));
                if (result.has("findings")) row.addProperty("loadedFindings", result.getAsJsonArray("findings").size());
                report.getAsJsonArray("ownerReads").add(row); return result;
            }
            JsonObject value(JsonObject view, String section, String key) throws Exception {
                var query = MaterialSnapshotClient.query(view, "record", section, MaterialSnapshotClient.identity("key", new com.google.gson.JsonPrimitive(key)));
                var page = MaterialChecksClient.invoke(CoreLaunch.resolve(fixture.get("executable").getAsString()), root, DeveloperContextSelection.get(getProject()), "query", view.get("attempt_id").getAsString(), null, query);
                assertEquals("ready", page.get("state").getAsString()); return page.getAsJsonObject("payload").getAsJsonObject("value");
            }
            void reopen(String attempt, Step... selections) throws Exception {
                var all = new ArrayList<Step>(); all.add(select("Saved Checks", "Reopen an Axiom material check")); all.add(select("Retained Axiom Checks", attempt)); all.addAll(List.of(selections)); run(all.toArray(Step[]::new));
            }
        }
        var journey = new Journey();
        var initialAttempts = new java.util.HashSet<String>();
        if (fixture.has("existingAttempts")) for (var attempt : fixture.getAsJsonArray("existingAttempts"))
            initialAttempts.add(attempt.getAsString());
        try {
            // Model the documented terminal preparation before opening Saved Checks.
            // The ordinary action must then use Core's selection with no path prompts.
            if (fixture.has("prepareEngineArchive")) {
                var launch = CoreLaunch.resolve(fixture.get("executable").getAsString());
                var selectionArgs = List.of("context", "select", root.toString(), "--pack-profile=supersymmetry",
                        "--platform-profile=cleanroom", "--variant=cleanroom-provisional");
                var selection = JsonParser.parseString(CommandProcess.capture(launch, selectionArgs, 0, 0, root.toString())).getAsJsonObject();
                String selectedSession = selection.get("session_id").getAsString();
                DeveloperContextSelection.set(getProject(), selectedSession);
                var setupArgs = List.of("context", "run", selectedSession, "--", "checks", "materials", "setup",
                        "--prepare", "--engine-archive", fixture.get("prepareEngineArchive").getAsString(),
                        "--context", fixture.getAsJsonObject("options").get("context").getAsString());
                Path setupOutput = output.resolveSibling(output.getFileName() + ".setup.json");
                Path setupError = output.resolveSibling(output.getFileName() + ".setup.stderr");
                var terminalSetup = new ProcessBuilder(launch.command(setupArgs)).directory(root.toFile())
                        .redirectOutput(setupOutput.toFile()).redirectError(setupError.toFile()).start();
                int setupExit = terminalSetup.waitFor();
                assertEquals(Files.readString(setupError), 0, setupExit);
                var envelope = JsonParser.parseString(Files.readString(setupOutput)).getAsJsonObject();
                var prepared = MaterialChecksClient.validate(envelope, root, "setup", null);
                report.add("setupArguments", new com.google.gson.Gson().toJsonTree(launch.command(setupArgs)));
                report.add("preparedSetup", prepared);
                for (var row : journey.read("history", null).getAsJsonArray("attempts"))
                    initialAttempts.add(row.getAsJsonObject().get("attempt_id").getAsString());
            }
            journey.run(select("Saved Checks", "Run Axiom material preflight"), new Step("Axiom Material Context", row -> row.value() instanceof JsonObject value && value.get("id").getAsString().equals(fixture.getAsJsonObject("options").get("context").getAsString())));
            var history = journey.read("history", null); assertEquals(initialAttempts.size() + 1, history.getAsJsonArray("attempts").size());
            String attempt = null;
            for (var row : history.getAsJsonArray("attempts")) {
                String id = row.getAsJsonObject().get("attempt_id").getAsString();
                if (!initialAttempts.contains(id)) attempt = id;
            }
            assertNotNull(attempt);
            var request = journey.read("show", attempt); assertEquals(MaterialChecksClient.REQUEST, request.get("format").getAsString());
            assertEquals(fixture.get("savedInputFiles").getAsInt(), request.getAsJsonObject("program").getAsJsonArray("files").size());
            assertTrue(request.get("setup_id").getAsString().startsWith("material-check-setup:sha256:"));
            var setupOptions = new JsonObject(); setupOptions.add("context", fixture.getAsJsonObject("options").get("context"));
            var setup = MaterialChecksClient.invoke(CoreLaunch.resolve(fixture.get("executable").getAsString()), root,
                    DeveloperContextSelection.get(getProject()), "setup-status", null, null, setupOptions);
            assertEquals("ready", setup.get("state").getAsString());
            assertEquals(request.get("setup_id"), setup.get("setup_id"));
            if (fixture.has("prepareEngineArchive"))
                assertEquals(report.getAsJsonObject("preparedSetup").get("id"), setup.get("setup_id"));
            if (diagnosticsOnly) {
                assertTrue(request.get("intent_path").isJsonNull());
                assertFalse(Files.exists(root.resolve("intent.json")));
            }
            var marker = Path.of(System.getenv("WORKBENCH_STATE_ROOT"), "product-spine/developer-checks/.workbench/check-attempts", attempt, "started.json"); assertFalse(Files.exists(marker));
            allowExecution[0] = true;
            journey.reopen(attempt, select("Axiom:", "Run this exact prepared material check"), select("Axiom:", "Read native outcomes, expectations and limitations"));
            var result = journey.read("show", attempt); assertEquals(fixture.get("errorNativeStatus").getAsString(), result.getAsJsonObject("native").get("status").getAsString());
            MaterialNativeAssertions.assertScope(result.getAsJsonObject("native"), "native-failed");
            assertTrue(result.getAsJsonArray("findings").asList().stream().anyMatch(item -> MaterialChecksClient.findingLabel(result, item.getAsJsonObject()).contains(savedSource.get("message").getAsString())));
            assertEquals("completed", result.get("state").getAsString()); assertTrue(Files.exists(marker));
            assertTrue(FileEditorManager.getInstance(getProject()).getSelectedTextEditor().getDocument().getText().contains("Candidate native status: " + fixture.get("errorNativeStatus").getAsString()));
            journey.reopen(attempt, nativeFinding("candidate", sourcePath, sourceLine, savedSource.get("channel").getAsString()), select("Material Diagnostic", "Read native diagnostic evidence"));
            assertTrue(FileEditorManager.getInstance(getProject()).getSelectedTextEditor().getDocument().getText().contains(savedSource.get("message").getAsString()));
            journey.reopen(attempt, nativeFinding("candidate", sourcePath, sourceLine, savedSource.get("channel").getAsString()), select("Material Diagnostic", "Open identical saved working copy"));
            var editor = FileEditorManager.getInstance(getProject()).getSelectedTextEditor(); assertEquals(document, editor.getDocument()); assertEquals(sourceLine - 1, editor.getCaretModel().getLogicalPosition().line);
            assertTrue("Exact native error must have a red highlighter at its actual source line", state.marks.stream().anyMatch(mark ->
                    mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == sourceLine - 1
                            && JBColor.RED.equals(mark.getErrorStripeMarkColor(com.intellij.openapi.editor.colors.EditorColorsManager.getInstance().getGlobalScheme()))
                            && String.valueOf(mark.getErrorStripeTooltip()).contains(savedSource.get("message").getAsString())));
            report.addProperty("nativeErrorSeverity", "error"); report.addProperty("nativeErrorRedHighlighter", true);
            WriteCommandAction.runWriteCommandAction(getProject(), () -> document.setText(bad + "\n// unsaved editor change\n"));
            assertTrue(FileDocumentManager.getInstance().isDocumentUnsaved(document)); assertTrue(state.marks.isEmpty());
            journey.reopen(attempt, nativeFinding("candidate", sourcePath, sourceLine, savedSource.get("channel").getAsString()), select("Material Diagnostic", "Read exact retained source"));
            editor = FileEditorManager.getInstance(getProject()).getSelectedTextEditor(); assertEquals(bad, editor.getDocument().getText());
            assertFalse(editor.getDocument().isWritable());
            assertTrue("Dirty source received a historical highlighter", state.marks.stream().noneMatch(mark -> mark.getDocument() == document));
            report.addProperty("unchangedOtherSourceMarkersAfterDirtyReopen", state.marks.size());
            edit(document, corrected);
            assertFalse(journey.read("show", attempt).get("_sourceCurrent").getAsBoolean());
            journey.reopen(attempt, nativeFinding("candidate", sourcePath, sourceLine, savedSource.get("channel").getAsString()), select("Material Diagnostic", "Read exact retained source"));
            editor = FileEditorManager.getInstance(getProject()).getSelectedTextEditor(); assertEquals(bad, editor.getDocument().getText()); assertFalse(editor.getDocument().isWritable()); assertEquals(sourceLine - 1, editor.getCaretModel().getLogicalPosition().line);
            assertTrue(state.marks.isEmpty());
            var materialContext = new Step("Axiom Material Context", row -> row.value() instanceof JsonObject value
                    && value.get("id").equals(fixture.getAsJsonObject("options").get("context")));
            journey.expectedHistorical = result;
            if (singleRerun) journey.run(select("Saved Checks", "Run Axiom material preflight"), materialContext, select("Axiom:", "Read native outcomes, expectations and limitations"));
            else journey.reopen(attempt, select("Axiom:", "Compare a new saved edit against this program"), materialContext, select("Axiom:", "Read native outcomes, expectations and limitations"));
            assertTrue("Previous completed snapshot was not shown during the fresh native check", journey.historicalWhileRunning);
            journey.expectedHistorical = null;
            history = journey.read("history", null); assertEquals(initialAttempts.size() + 2, history.getAsJsonArray("attempts").size());
            String pairedAttempt = null;
            for (var row : history.getAsJsonArray("attempts")) {
                String id = row.getAsJsonObject().get("attempt_id").getAsString();
                if (!id.equals(attempt) && !initialAttempts.contains(id)) pairedAttempt = id;
            }
            assertNotNull(pairedAttempt); var pair = journey.read("show", pairedAttempt); var body = pair.getAsJsonObject("native").getAsJsonObject("result");
            if (!singleRerun) {
                assertEquals(fixture.get("errorNativeStatus").getAsString(), body.getAsJsonObject("baseline").get("status").getAsString());
                MaterialNativeAssertions.assertScope(body.getAsJsonObject("baseline"), "native-failed");
                assertTrue(pair.getAsJsonArray("findings").asList().stream().anyMatch(item -> MaterialChecksClient.text(item.getAsJsonObject(), "side", "").equals("baseline") && MaterialChecksClient.findingLabel(pair, item.getAsJsonObject()).contains(savedSource.get("message").getAsString())));
            }
            var candidate = singleRerun ? pair.getAsJsonObject("native") : body.getAsJsonObject("candidate");
            assertEquals(fixture.get("correctedNativeStatus").getAsString(), candidate.get("status").getAsString());
            MaterialNativeAssertions.assertScope(candidate, MaterialChecksClient.text(fixture, "correctedInitializationStatus", "completed"));
            assertFalse(pair.getAsJsonArray("findings").asList().stream().anyMatch(item -> MaterialChecksClient.text(item.getAsJsonObject(), "side", "").equals("candidate") && MaterialChecksClient.findingLabel(pair, item.getAsJsonObject()).contains(savedSource.get("message").getAsString())));
            assertEquals(diagnosticsOnly ? "not-requested" : "matched", candidate.getAsJsonObject("result").getAsJsonObject("expectations").get("status").getAsString());
            if (candidate.getAsJsonObject("result").getAsJsonObject("expectations").has("checks")) assertEquals(diagnosticsOnly ? 0 : 16, candidate.getAsJsonObject("result").getAsJsonObject("expectations").getAsJsonArray("checks").size());
            assertTrue("Corrected error line retained a highlighter", state.marks.stream().noneMatch(mark ->
                    mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == sourceLine - 1));
            if (fixture.has("recipe")) {
                var expected = fixture.getAsJsonObject("recipe");
                var recipe = journey.value(pair, "crafting-recipes", expected.get("key").getAsString());
                String reference = recipe.getAsJsonObject("storedFields").getAsJsonObject("com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipe#output").get("nativeValueRef").getAsString();
                var stack = journey.value(pair, "crafting-values", reference);
                var item = journey.value(pair, "crafting-values", stack.getAsJsonObject("item").get("nativeValueRef").getAsString());
                assertEquals("net.minecraft.item.ItemStack", stack.get("type").getAsString()); assertEquals(expected.get("count"), stack.get("count"));
                assertTrue(item.toString().contains(expected.get("item").getAsString()));
                var observation = new JsonObject(); observation.add("snapshot", pair.get("snapshot_id")); observation.add("recipe", expected); observation.add("output", stack); observation.add("item", item); report.add("recipeObservation", observation);
            }
            if (fixture.has("gtRecipe")) {
                var expected = fixture.getAsJsonObject("gtRecipe");
                String mapName = expected.get("map").getAsString();
                String key = MaterialSnapshotClient.identity("key", expected.get("map"));
                var query = MaterialSnapshotClient.query(pair, "record", "gt-recipes", key);
                var launch = CoreLaunch.resolve(fixture.get("executable").getAsString());
                String session = DeveloperContextSelection.get(getProject());
                var page = MaterialChecksClient.invoke(launch, root, session, "query", pairedAttempt, null, query);
                assertEquals("ready", page.get("state").getAsString());
                var payload = page.getAsJsonObject("payload");
                JsonObject map = payload.getAsJsonObject("value"), exported = null;
                if (map == null) {
                    // File detail is parsed by the test, without loading a map into a UI string.
                    Path destination = output.resolveSibling(pairedAttempt + "-" + mapName + ".json");
                    var options = new JsonObject(); options.add("snapshot_id", pair.get("snapshot_id"));
                    options.addProperty("destination", destination.toString()); options.addProperty("section_id", "gt-recipes");
                    options.addProperty("record_key", key); options.add("sha256", payload.getAsJsonObject("content").get("sha256"));
                    exported = MaterialChecksClient.invoke(launch, root, session, "export", pairedAttempt, null, options);
                    byte[] bytes = Files.readAllBytes(destination);
                    assertEquals(options.get("sha256").getAsString(), java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(bytes)));
                    map = JsonParser.parseString(new String(bytes, java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
                }
                assertTrue(map.get("storedValuesComplete").getAsBoolean()); assertTrue(map.getAsJsonArray("affectingGaps").isEmpty());
                var entries = map.getAsJsonObject("lookup").getAsJsonObject("entries").entrySet().stream()
                        .filter(entry -> entry.getValue().getAsJsonObject().getAsJsonObject("value").get("inputs").toString().contains(expected.get("marker").getAsString())).toList();
                assertEquals(1, entries.size());
                String identity = entries.getFirst().getKey(); var entry = entries.getFirst().getValue().getAsJsonObject(); var recipe = entry.getAsJsonObject("value");
                assertEquals(1, entry.get("multiplicity").getAsInt()); assertTrue(recipe.get("groovyRecipe").getAsBoolean());
                assertTrue(map.getAsJsonArray("categories").asList().stream().anyMatch(category -> category.getAsJsonObject().getAsJsonObject("lookupMembers").has(identity)));
                var inputs = new JsonArray();
                for (var element : recipe.getAsJsonArray("inputs")) {
                    var input = element.getAsJsonObject(); var value = new JsonObject();
                    value.add("item", input.getAsJsonObject("stacks").getAsJsonArray("values").get(0).getAsJsonObject().getAsJsonObject("item").get("value"));
                    value.addProperty("amount", input.getAsJsonObject("amount").get("value").getAsInt()); value.add("consumable", input.get("isConsumable")); inputs.add(value);
                }
                assertEquals(expected.get("inputs"), inputs);
                var fluids = new JsonArray();
                for (var element : recipe.getAsJsonArray("fluidInputs")) {
                    var input = element.getAsJsonObject(); var value = new JsonObject(); value.add("fluid", input.getAsJsonObject("stack").get("fluid"));
                    value.addProperty("amount", input.getAsJsonObject("amount").get("value").getAsInt()); fluids.add(value);
                }
                assertEquals(expected.get("fluidInputs"), fluids);
                var stack = recipe.getAsJsonObject("outputs").getAsJsonArray("values").get(0).getAsJsonObject();
                assertEquals(expected.getAsJsonObject("output").get("item"), stack.getAsJsonObject("item").get("value"));
                assertEquals(expected.getAsJsonObject("output").get("count"), stack.get("count"));
                assertEquals(expected.get("duration").getAsInt(), recipe.getAsJsonObject("duration").get("value").getAsInt());
                assertEquals(expected.get("EUt").getAsInt(), recipe.getAsJsonObject("EUt").get("value").getAsInt());
                assertTrue(recipe.getAsJsonObject("properties").getAsJsonArray("entries").asList().stream().anyMatch(property -> property.getAsJsonObject().get("key").getAsString().equals("research")));
                var observation = new JsonObject(); observation.add("snapshot", pair.get("snapshot_id")); observation.addProperty("map", mapName);
                observation.addProperty("identity", identity); observation.add("recipe", recipe); observation.add("exported", exported); report.add("machineObservation", observation);
            }
            int preservedWarnings = 0;
            for (var item : pair.getAsJsonArray("findings")) {
                var finding = item.getAsJsonObject(); var location = MaterialChecksClient.object(finding, "location");
                if (MaterialChecksClient.text(finding, "side", "").equals("candidate")
                        && MaterialChecksClient.text(finding, "severity", "").equals("warning")
                        && MaterialChecksClient.text(location, "path", "").equals(sourcePath)) {
                    assertEquals(correctedSha, location.get("sha256"));
                    int warningLine = location.getAsJsonObject("start").get("line").getAsInt() - 1;
                    assertTrue("Current native warning lost its highlighter", state.marks.stream().anyMatch(mark ->
                            mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == warningLine
                                    && JBColor.ORANGE.equals(mark.getErrorStripeMarkColor(com.intellij.openapi.editor.colors.EditorColorsManager.getInstance().getGlobalScheme()))
                                    && String.valueOf(mark.getErrorStripeTooltip()).contains(finding.get("message").getAsString())));
                    preservedWarnings++;
                }
            }
            report.addProperty("preservedWarningMarkers", preservedWarnings);
            journey.reopen(singleRerun ? attempt : pairedAttempt, nativeFinding(singleRerun ? "candidate" : "baseline", sourcePath, sourceLine, savedSource.get("channel").getAsString()), select("Material Diagnostic", "Read exact retained source"));
            editor = FileEditorManager.getInstance(getProject()).getSelectedTextEditor(); assertEquals(bad, editor.getDocument().getText()); assertFalse(editor.getDocument().isWritable());
            if (singleRerun) assertTrue(state.marks.isEmpty());
            else assertTrue("Baseline source must not decorate the corrected error line", state.marks.stream().noneMatch(mark ->
                    mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == sourceLine - 1));
            assertEquals(history, journey.read("history", null)); assertEquals(3, report.getAsJsonArray("confirmations").size());
            for (String field : List.of("programRoot", "intent", "engineHome", "runtimeHome", "java"))
                assertEquals("Routine comparison must reuse Core setup without repeated input prompts: " + field,
                        fixture.has("prepareEngineArchive") ? 0L : 1L, materialInputPrompts.stream().filter(field::equals).count());
            report.add("materialInputPrompts", new com.google.gson.Gson().toJsonTree(materialInputPrompts));
            report.addProperty("routineSetupReused", true);
            assertEquals(corrected, Files.readString(root.resolve(sourcePath)));
            assertFalse(Files.exists(root.resolve(".workbench"))); report.addProperty("state", "passed-not-qualified");
            report.addProperty("sourcePath", sourcePath); report.addProperty("nativeLine", sourceLine);
            report.add("savedInputFiles", fixture.get("savedInputFiles"));
            report.addProperty("dirtySuppressed", true); report.addProperty("staleSourceReadable", true);
            report.addProperty("nativeWorkers", singleRerun ? 2 : 3); report.addProperty("comparisonMode", singleRerun ? "single-rerun" : "paired");
            report.addProperty("badAttempt", attempt); report.addProperty("pairedAttempt", pairedAttempt);
            report.addProperty("session", DeveloperContextSelection.get(getProject()));
        } catch (Throwable error) { report.addProperty("state", "failed"); report.addProperty("error", error.toString()); throw error; }
        finally {
            state.clear(); TestDialogManager.setTestDialog(priorDialog); TestDialogManager.setTestInputDialog(priorInput);
            Files.writeString(output, new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create().toJson(report) + "\n");
        }
    }
}
