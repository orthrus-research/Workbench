package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.openapi.command.WriteCommandAction;
import com.intellij.openapi.editor.Document;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.testFramework.fixtures.BasePlatformTestCase;
import com.intellij.testFramework.fixtures.IdeaTestFixtureFactory;
import com.intellij.testFramework.fixtures.TempDirTestFixture;
import com.intellij.ui.JBColor;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Real IntelliJ documents/markup/navigation. Native acceptance is explicitly supplied. */
public final class MaterialChecksPlatformTest extends BasePlatformTestCase {
    public void testInstalledRetainedHistoryReader() throws Exception {
        var fixture = JsonParser.parseString(Files.readString(Path.of(
            System.getenv("WORKBENCH_TEST_MATERIAL_HISTORY_FIXTURE")))).getAsJsonObject();
        assertTrue(fixture.get("retainedReaderOnly").getAsBoolean());
        var launch = CoreLaunch.resolve(fixture.get("executable").getAsString());
        var pack = Path.of(fixture.get("pack").getAsString());
        var session = fixture.get("session").getAsString();
        var evidence = new JsonArray();
        for (var element : fixture.getAsJsonArray("retainedAttempts")) {
            var expected = element.getAsJsonObject();
            var result = MaterialChecksClient.invoke(launch, pack, session, "show", expected.get("attempt").getAsString(), null, null);
            assertEquals(expected.get("snapshot"), result.get("snapshot_id"));
            assertEquals(expected.get("nativeOutcome"), result.get("native_outcome"));
            assertEquals(expected.get("coverage"), result.get("coverage"));
            assertEquals("complete", result.getAsJsonObject("interpretation").get("state").getAsString());
            assertEquals(expected.get("findings"), result.get("findings_count"));
            var text = MaterialChecksClient.summary(result);
            assertTrue(text.contains("Reader interpretation: complete"));
            var shown = MaterialCheckViews.openText(getProject(), "retained-history.txt", text, 1);
            assertEquals(text, shown.getDocument().getText());
            var row = new JsonObject(); row.add("snapshot", result.get("snapshot_id")); row.add("readerBinding", result.get("reader_binding"));
            evidence.add(row);
        }
        var report = new JsonObject(); report.addProperty("state", "passed-reader-only");
        report.addProperty("nativeWorkers", 0); report.addProperty("qualification", false); report.add("evidence", evidence);
        var expiredEvidence = new JsonArray();
        if (fixture.has("expiredAttempts")) {
            var history = MaterialChecksClient.invoke(launch, pack, session, "history", null, null, null);
            for (var element : fixture.getAsJsonArray("expiredAttempts")) {
                var expected = element.getAsJsonObject();
                JsonObject row = null;
                for (var item : history.getAsJsonArray("attempts")) {
                    var candidate = item.getAsJsonObject();
                    if (candidate.get("attempt_id").equals(expected.get("attempt"))) row = candidate;
                }
                assertNotNull("Expired checks remain discoverable in installed history", row);
                assertEquals("expired", row.get("state").getAsString());
                assertEquals("expired", row.get("detail_state").getAsString());
                assertEquals(expected.get("snapshot"), row.get("snapshot_id"));
                assertEquals("completed", row.getAsJsonObject("original_summary").get("state").getAsString());
                var originalNative = row.getAsJsonObject("original_summary").getAsJsonObject("overview").getAsJsonObject("native");
                assertEquals(expected.get("nativeOutcome"), originalNative.getAsJsonObject("result").get("nativeOutcome"));
                try {
                    MaterialChecksClient.invoke(launch, pack, session, "show", expected.get("attempt").getAsString(), null, null);
                    fail("Expired detail must not reopen as an empty successful result");
                } catch (Exception error) {
                    assertTrue(error.toString(), error.toString().contains("details are expired"));
                }
                var text = new GsonBuilder().setPrettyPrinting().create().toJson(row);
                assertEquals(text, MaterialCheckViews.openText(getProject(), "expired-history.json", text, 1).getDocument().getText());
                expiredEvidence.add(row);
            }
        }
        report.add("expiredEvidence", expiredEvidence);
        if (fixture.has("retentionControls") && fixture.get("retentionControls").getAsBoolean()) {
            var operation = new JsonObject(); operation.addProperty("operation", "status");
            var status = MaterialChecksClient.invoke(launch, pack, session, "retention", null, null, operation);
            assertTrue(status.getAsJsonArray("stores").size() > 0);
            var settings = status.getAsJsonObject("policy").getAsJsonObject("settings").deepCopy();
            settings.addProperty("mode", "keep-everything");
            operation.addProperty("operation", "configure"); operation.add("settings", settings);
            var proposed = MaterialChecksClient.invoke(launch, pack, session, "retention", null, null, operation);
            var proposal = proposed.getAsJsonObject("proposal");
            String disclosure = proposal.get("disclosure").getAsString();
            assertTrue(disclosure.contains("permanent expiry"));
            var shown = MaterialCheckViews.openText(getProject(), "retention-disclosure.txt", disclosure, 1);
            assertEquals(disclosure, shown.getDocument().getText());
            var configured = MaterialChecksClient.invoke(launch, pack, session, "retention", null, proposal.get("id").getAsString(), operation);
            assertEquals("configured", configured.get("state").getAsString());
            operation = new JsonObject(); operation.addProperty("operation", "maintain");
            var maintained = MaterialChecksClient.invoke(launch, pack, session, "retention", null, null, operation);
            assertEquals("disabled", maintained.get("state").getAsString());
            var text = new GsonBuilder().setPrettyPrinting().create().toJson(maintained);
            assertEquals(text, MaterialCheckViews.openText(getProject(), "retention-maintenance.json", text, 1).getDocument().getText());
            report.add("retentionPolicy", configured.get("policy")); report.add("retentionMaintenance", maintained);
        }
        Files.writeString(Path.of(System.getenv("WORKBENCH_TEST_MATERIAL_HISTORY_REPORT")), report.toString(),
                          java.nio.file.StandardOpenOption.CREATE_NEW);
    }
    public void testLoadedFindingSelectorPreservesEveryChoicePastCommandCatalogSize() {
        var choices = new java.util.ArrayList<CatalogSelectionDialog.Choice<Integer>>();
        for (int index = 0; index < 7700; index++)
            choices.add(new CatalogSelectionDialog.Choice<>("Finding " + index, "retained", "Original diagnostic " + index, index));
        var dialog = new CatalogSelectionDialog<>(getProject(), "Retained findings", "7700 of 7700 loaded", choices);
        try {
            var list = (javax.swing.JList<?>) dialog.getPreferredFocusedComponent();
            assertEquals(7700, list.getModel().getSize());
            list.setSelectedIndex(7699);
            assertEquals(Integer.valueOf(7699), dialog.selected());
            list.setSelectedIndex(4096);
            assertEquals(Integer.valueOf(4096), dialog.selected());
        } finally { dialog.close(com.intellij.openapi.ui.DialogWrapper.CANCEL_EXIT_CODE); }
    }
    @Override protected TempDirTestFixture createTempDirTestFixture() {
        return IdeaTestFixtureFactory.getFixtureFactory().createTempDirTestFixture();
    }
    @Override protected boolean isWriteActionRequired() { return false; }
    private Path root() { return Path.of(myFixture.getTempDirPath()); }
    private void edit(Document document, String text, boolean save) {
        WriteCommandAction.runWriteCommandAction(getProject(), () -> document.setText(text));
        if (save) FileDocumentManager.getInstance().saveDocument(document);
    }
    private JsonObject finding(String text) throws Exception {
        var location = JsonParser.parseString("""
          {"path":"groovy/Test.groovy","byte_start":9,"byte_end":9,"start":{"line":2,"column":1},"end":{"line":2,"column":1},
           "coordinate_system":"one-based-utf16","interval":"half-open"}
          """).getAsJsonObject();
        location.addProperty("sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))));
        var finding = new JsonObject(); finding.addProperty("id", "diagnostic-0"); finding.addProperty("side", "candidate");
        finding.addProperty("severity", "error"); finding.addProperty("message", "Retained native line"); finding.add("location", location); return finding;
    }
    private JsonObject result(JsonObject finding) {
        var result = MaterialChecksClientTest.result(); result.addProperty("_sourceCurrent", true);
        result.getAsJsonArray("findings").add(finding); return result;
    }
    public void testActualDocumentEditClearsMaterialMarkersAndBlocksDirtyNavigation() throws Exception {
        String text = "// saved\nnativeLine\n";
        var file = myFixture.getTempDirFixture().createFile("groovy/Test.groovy", text);
        myFixture.openFileInEditor(file); var document = FileDocumentManager.getInstance().getDocument(file);
        var state = RunSavedChecksAction.state(getProject()); var finding = finding(text); var result = result(finding);
        try {
            RunSavedChecksAction.materialAnnotations(getProject(), state, root(), result, state.edits);
            assertEquals(1, state.marks.size()); assertEquals(9, state.marks.getFirst().getStartOffset());
            RunSavedChecksAction.openCurrentSource(getProject(), root(), finding.getAsJsonObject("location"));
            assertEquals(1, FileEditorManager.getInstance(getProject()).getSelectedTextEditor().getCaretModel().getLogicalPosition().line);
            long epoch = state.edits;
            edit(document, text + "// unsaved\n", false);
            assertTrue(FileDocumentManager.getInstance().isDocumentUnsaved(document)); assertTrue(state.marks.isEmpty());
            RunSavedChecksAction.materialAnnotations(getProject(), state, root(), result, epoch);
            assertTrue(state.marks.isEmpty());
            RunSavedChecksAction.materialAnnotations(getProject(), state, root(), result, state.edits);
            assertTrue(state.marks.isEmpty());
            assertThrows(IllegalArgumentException.class, () -> RunSavedChecksAction.openCurrentSource(getProject(), root(), finding.getAsJsonObject("location")));
        } finally { state.clear(); }
    }
    public void testBaselineAndStaleFindingsDoNotDecorateAnIdenticalFile() throws Exception {
        String text = "// saved\nnativeLine\n";
        myFixture.openFileInEditor(myFixture.getTempDirFixture().createFile("groovy/Test.groovy", text));
        var finding = finding(text); finding.addProperty("side", "baseline"); var result = result(finding);
        assertEmpty(MaterialCheckViews.annotate(getProject(), root(), result, true));
        finding.addProperty("side", "candidate");
        assertEmpty(MaterialCheckViews.annotate(getProject(), root(), result, false));
        result.addProperty("_sourceCurrent", false);
        assertEmpty(MaterialCheckViews.annotate(getProject(), root(), result, true));
    }
    public void testRetainedEditorIsReadOnlyAtItsActualNativeLine() throws Exception {
        String text = "// saved\nnativeLine\n"; var finding = finding(text); var result = result(finding);
        var view = new JsonObject(); view.addProperty("format", "workbench-material-source-view-v1"); view.addProperty("read_only", true);
        view.add("attempt_id", result.get("attempt_id")); view.add("result_id", result.get("id")); view.add("source", finding); view.addProperty("text", text);
        var editor = MaterialCheckViews.openRetained(getProject(), view, result, finding);
        assertNotNull(editor); assertEquals(text, editor.getDocument().getText());
        assertFalse(editor.getDocument().isWritable()); assertEquals(1, editor.getCaretModel().getLogicalPosition().line);
        view.addProperty("text", "other source");
        assertThrows(IllegalArgumentException.class, () -> MaterialCheckViews.openRetained(getProject(), view, result, finding));
    }

    public void testInstalledNativeProgramThroughRealPlatformViews() throws Exception {
        String fixturePath = System.getenv("WORKBENCH_TEST_MATERIAL_IDEA_FIXTURE");
        assertNotNull("Explicit installed native material fixture required (selected by Gradle opt-in)", fixturePath);
        var fixture = JsonParser.parseString(Files.readString(Path.of(fixturePath))).getAsJsonObject();
        assertEquals("prepared-not-qualified", fixture.get("state").getAsString());
        boolean diagnosticsOnly = fixture.getAsJsonObject("options").get("intent").getAsString().isEmpty();
        boolean singleRerun = MaterialChecksClient.text(fixture, "comparisonMode", "paired").equals("single-rerun");
        if (singleRerun) assertTrue("Single rerun requires measured full-pack comparison overflow", fixture.has("comparisonOverflowEvidence"));
        var savedSource = fixture.getAsJsonObject("savedSource");
        String sourcePath = savedSource.get("path").getAsString(); int sourceLine = savedSource.get("line").getAsInt();
        assertTrue(sourceLine > 0);
        Path output = Path.of(System.getenv("WORKBENCH_TEST_MATERIAL_IDEA_REPORT")); assertFalse(Files.exists(output));
        var report = new JsonObject(); report.addProperty("state", "running"); report.addProperty("host", "real-intellij-platform-fixture");
        report.addProperty("dialogInteraction", "not-exercised"); report.addProperty("qualification", false); report.addProperty("minecraftLaunched", false);
        report.add("calls", new JsonArray()); report.addProperty("workspace", root().toString());
        var sourcePack = Path.of(fixture.get("pack").getAsString());
        for (String directory : List.of("groovy", "config")) try (var inputs = Files.walk(sourcePack.resolve(directory))) {
            for (var input : inputs.filter(Files::isRegularFile).toList()) {
                String relative = sourcePack.relativize(input).toString();
                Path target = root().resolve(relative); Files.createDirectories(target.getParent()); Files.copy(input, target);
                assertTrue(java.util.Arrays.equals(Files.readAllBytes(input), Files.readAllBytes(root().resolve(relative))));
            }
        }
        for (String name : diagnosticsOnly ? List.of("pack.toml", "index.toml") : List.of("intent.json", "pack.toml", "index.toml"))
            myFixture.getTempDirFixture().createFile(name, Files.readString(sourcePack.resolve(name)));
        myFixture.getTempDirFixture().findOrCreateDir("mods"); myFixture.getTempDirFixture().findOrCreateDir("config");
        for (var args : List.of(List.of("git", "init", "-q"), List.of("git", "-c", "core.autocrlf=false", "add", "."), List.of("git", "-c", "user.name=Workbench fixture", "-c", "user.email=fixture@invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "Material platform fixture"))) {
            var process = new ProcessBuilder(args).directory(root().toFile()).start(); assertEquals(0, process.waitFor());
        }
        var launch = CoreLaunch.resolve(fixture.get("executable").getAsString());
        String selection = CommandProcess.capture(launch, List.of("context", "select", root().toString(), "--pack-profile=supersymmetry", "--platform-profile=cleanroom", "--variant=cleanroom-provisional"), 16 * 1024 * 1024, 120, root().toString());
        String session = JsonParser.parseString(selection).getAsJsonObject().get("session_id").getAsString(); report.addProperty("session", session);
        var source = com.intellij.openapi.vfs.LocalFileSystem.getInstance().refreshAndFindFileByNioFile(root().resolve(sourcePath));
        assertNotNull(source); myFixture.openFileInEditor(source);
        var document = FileDocumentManager.getInstance().getDocument(source); String original = document.getText();
        String anchor = savedSource.get("anchor").getAsString();
        assertEquals(original.indexOf(anchor), original.lastIndexOf(anchor));
        String bad = original.replace(anchor, savedSource.get("replacement").getAsString());
        assertFalse(bad.equals(original)); var state = RunSavedChecksAction.state(getProject());
        class Native {
            JsonObject call(String action, String value, String confirmation, JsonObject options) throws Exception {
                var result = MaterialChecksClient.invoke(launch, root(), session, action, value, confirmation, options);
                var entry = new JsonObject(); entry.addProperty("action", action); entry.add("result", result); report.getAsJsonArray("calls").add(entry); return result;
            }
        }
        var nativeClient = new Native();
        try {
            var setup = nativeClient.call("setup", null, null, fixture.getAsJsonObject("options"));
            var selected = new JsonObject(); selected.add("context", fixture.getAsJsonObject("options").get("context"));
            var readiness = nativeClient.call("setup-status", null, null, selected);
            assertEquals("ready", readiness.get("state").getAsString()); assertEquals(setup.get("id"), readiness.get("setup_id"));
            edit(document, bad, true);
            var request = nativeClient.call("prepare", null, null, selected); String attempt = request.get("attempt_id").getAsString();
            assertEquals(fixture.get("savedInputFiles").getAsInt(), request.getAsJsonObject("program").getAsJsonArray("files").size());
            assertEquals(setup.get("id"), request.get("setup_id"));
            long epoch = state.edits; JsonObject result;
            var executor = Executors.newSingleThreadExecutor();
            try {
                var running = executor.submit(() -> nativeClient.call("execute", attempt, request.get("id").getAsString(), null));
                var marker = Path.of(System.getenv("WORKBENCH_STATE_ROOT"), "product-spine/developer-checks/.workbench/check-attempts", attempt, "started.json");
                while (!Files.exists(marker) && !running.isDone()) TimeUnit.MILLISECONDS.sleep(30);
                assertTrue("Prepared native attempt must start before the editor change", Files.exists(marker));
                edit(document, bad + "\n// unsaved during native execution\n", false);
                result = running.get();
            } finally { executor.shutdown(); }
            assertEquals(fixture.get("errorNativeStatus").getAsString(), result.getAsJsonObject("native").get("status").getAsString());
            MaterialNativeAssertions.assertScope(result.getAsJsonObject("native"), "native-failed");
            assertTrue(MaterialNativeAssertions.hasError(result.getAsJsonObject("native"), savedSource.get("message").getAsString()));
            assertTrue(result.get("_sourceCurrent").getAsBoolean());
            RunSavedChecksAction.materialAnnotations(getProject(), state, root(), result, epoch); assertTrue(state.marks.isEmpty());
            JsonObject finding = null;
            for (var value : result.getAsJsonArray("findings")) {
                var row = value.getAsJsonObject();
                if (MaterialChecksClient.text(row, "channel", "").equals(savedSource.get("channel").getAsString()) && row.get("location").isJsonObject()
                        && MaterialChecksClient.text(row.getAsJsonObject("location"), "path", "").equals(sourcePath)
                        && row.getAsJsonObject("location").getAsJsonObject("start").get("line").getAsInt() == sourceLine) { finding = row; break; }
            }
            assertNotNull("Native error source line must be present", finding);
            assertEquals("error", finding.get("severity").getAsString());
            assertTrue(MaterialChecksClient.findingMessage(finding).contains(savedSource.get("message").getAsString()));
            var view = nativeClient.call("source", attempt, finding.get("id").getAsString(), null);
            var retained = MaterialCheckViews.openRetained(getProject(), view, result, finding);
            assertEquals(bad, retained.getDocument().getText()); assertFalse(retained.getDocument().isWritable()); assertEquals(sourceLine - 1, retained.getCaretModel().getLogicalPosition().line);
            edit(document, bad, true); RunSavedChecksAction.materialAnnotations(getProject(), state, root(), result, state.edits);
            assertTrue("Exact native error must have a red highlighter", state.marks.stream().anyMatch(mark ->
                    mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == sourceLine - 1
                            && JBColor.RED.equals(mark.getErrorStripeMarkColor(com.intellij.openapi.editor.colors.EditorColorsManager.getInstance().getGlobalScheme()))
                            && String.valueOf(mark.getErrorStripeTooltip()).contains(savedSource.get("message").getAsString())));
            RunSavedChecksAction.openCurrentSource(getProject(), root(), finding.getAsJsonObject("location"));
            assertEquals(sourceLine - 1, FileEditorManager.getInstance(getProject()).getSelectedTextEditor().getCaretModel().getLogicalPosition().line);
            edit(document, bad + "\n// unsaved\n", false); assertTrue(state.marks.isEmpty());
            var location = finding.getAsJsonObject("location");
            assertThrows(IllegalArgumentException.class, () -> RunSavedChecksAction.openCurrentSource(getProject(), root(), location));
            edit(document, original, true);
            var stale = nativeClient.call("show", attempt, null, null); assertFalse(stale.get("_sourceCurrent").getAsBoolean());
            RunSavedChecksAction.materialAnnotations(getProject(), state, root(), stale, state.edits); assertTrue(state.marks.isEmpty());
            assertEquals(bad, MaterialCheckViews.openRetained(getProject(), view, stale, finding).getDocument().getText());
            var options = selected.deepCopy(); if (!singleRerun) options.addProperty("baseline", attempt);
            var paired = nativeClient.call("prepare", null, null, options); String pairedAttempt = paired.get("attempt_id").getAsString();
            assertEquals(setup.get("id"), paired.get("setup_id"));
            var pair = nativeClient.call("execute", pairedAttempt, paired.get("id").getAsString(), null);
            var body = pair.getAsJsonObject("native").getAsJsonObject("result");
            if (!singleRerun) {
                assertEquals(fixture.get("errorNativeStatus").getAsString(), body.getAsJsonObject("baseline").get("status").getAsString());
                MaterialNativeAssertions.assertScope(body.getAsJsonObject("baseline"), "native-failed");
                assertTrue(MaterialNativeAssertions.hasError(body.getAsJsonObject("baseline"), savedSource.get("message").getAsString()));
            }
            var candidate = singleRerun ? pair.getAsJsonObject("native") : body.getAsJsonObject("candidate");
            assertEquals(fixture.get("correctedNativeStatus").getAsString(), candidate.get("status").getAsString());
            MaterialNativeAssertions.assertScope(candidate, "completed");
            assertFalse(MaterialNativeAssertions.hasError(candidate, savedSource.get("message").getAsString()));
            assertEquals(diagnosticsOnly ? "not-requested" : "matched", candidate.getAsJsonObject("result").getAsJsonObject("expectations").get("status").getAsString());
            int intentChecks = candidate.getAsJsonObject("result").getAsJsonObject("expectations").getAsJsonArray("checks").size();
            assertEquals(diagnosticsOnly ? 0 : 16, intentChecks);
            assertTrue(pair.get("_sourceCurrent").getAsBoolean()); RunSavedChecksAction.materialAnnotations(getProject(), state, root(), pair, state.edits);
            assertTrue("Corrected error line retained a highlighter", state.marks.stream().noneMatch(mark ->
                    mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == sourceLine - 1));
            var rendered = MaterialCheckViews.openText(getProject(), "material-report.txt", MaterialChecksClient.report(pair), 1);
            if (!singleRerun) assertTrue(rendered.getDocument().getText().contains("Baseline native status: " + fixture.get("errorNativeStatus").getAsString()));
            assertTrue(rendered.getDocument().getText().contains((singleRerun ? "Material execution: " : "Candidate native status: ") + fixture.get("correctedNativeStatus").getAsString())); assertFalse(rendered.getDocument().isWritable());
            boolean baselineOpened = false;
            if (!singleRerun) for (var value : pair.getAsJsonArray("findings")) {
                var row = value.getAsJsonObject();
                if (MaterialChecksClient.text(row, "side", "").equals("baseline") && row.get("location").isJsonObject()
                        && MaterialChecksClient.text(row.getAsJsonObject("location"), "path", "").equals(sourcePath)
                        && row.getAsJsonObject("location").getAsJsonObject("start").get("line").getAsInt() == sourceLine) {
                    var baseline = nativeClient.call("source", pairedAttempt, row.get("id").getAsString(), null);
                    var editor = MaterialCheckViews.openRetained(getProject(), baseline, pair, row);
                    assertEquals(bad, editor.getDocument().getText()); assertFalse(editor.getDocument().isWritable());
                    assertEquals(sourceLine - 1, editor.getCaretModel().getLogicalPosition().line); baselineOpened = true; break;
                }
            }
            if (!singleRerun) assertTrue(baselineOpened);
            else assertEquals(bad, MaterialCheckViews.openRetained(getProject(), view, stale, finding).getDocument().getText());
            assertTrue("Corrected error line regained a highlighter", state.marks.stream().noneMatch(mark ->
                    mark.getDocument() == document && document.getLineNumber(mark.getStartOffset()) == sourceLine - 1));
            var history = nativeClient.call("history", null, null, null);
            nativeClient.call("show", pairedAttempt, null, null);
            assertEquals(history, nativeClient.call("history", null, null, null));
            assertEquals(original, Files.readString(root().resolve(sourcePath)));
            report.addProperty("state", "passed-not-qualified"); report.addProperty("nativeWorkers", singleRerun ? 2 : 3);
            report.addProperty("comparisonMode", singleRerun ? "single-rerun" : "paired");
            report.addProperty("nativeLine", sourceLine); report.addProperty("sourcePath", sourcePath); report.addProperty("intentChecks", intentChecks);
            report.addProperty("routineSetupReused", true); report.add("savedInputFiles", fixture.get("savedInputFiles"));
            report.addProperty("baselineRetainedSourceOpened", baselineOpened);
            report.addProperty("badAttempt", attempt); report.addProperty("pairedAttempt", pairedAttempt);
        } catch (Throwable error) { report.addProperty("state", "failed"); report.addProperty("error", error.toString()); throw error; }
        finally {
            state.clear(); Files.writeString(output, new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create().toJson(report) + "\n");
        }
    }
}
