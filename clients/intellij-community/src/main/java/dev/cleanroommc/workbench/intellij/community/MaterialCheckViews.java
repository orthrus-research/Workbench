package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.intellij.openapi.editor.Editor;
import com.intellij.openapi.editor.impl.DocumentMarkupModel;
import com.intellij.openapi.editor.markup.EffectType;
import com.intellij.openapi.editor.markup.HighlighterLayer;
import com.intellij.openapi.editor.markup.RangeHighlighter;
import com.intellij.openapi.editor.markup.TextAttributes;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.vfs.LocalFileSystem;
import com.intellij.testFramework.LightVirtualFile;
import com.intellij.ui.JBColor;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Native editor views for retained material observations; never a domain evaluator. */
final class MaterialCheckViews {
    private MaterialCheckViews() { }

    static Editor openText(Project project, String name, String text, int line) {
        var file = new LightVirtualFile(name, text);
        file.setWritable(false);
        return FileEditorManager.getInstance(project).openTextEditor(new OpenFileDescriptor(project, file, Math.max(0, line - 1), 0), true);
    }

    static Editor openRetained(Project project, JsonObject view, JsonObject result, JsonObject finding) throws Exception {
        String text = MaterialChecksClient.retainedSource(view, result, finding);
        int line = finding.getAsJsonObject("location").getAsJsonObject("start").get("line").getAsInt();
        return openText(project, "workbench-retained-material.groovy", text, line);
    }

    static List<RangeHighlighter> annotate(Project project, Path root, JsonObject result, boolean editorEpochCurrent) {
        var marks = new ArrayList<RangeHighlighter>();
        if (!editorEpochCurrent || !result.has("_sourceCurrent") || !result.get("_sourceCurrent").getAsBoolean() || !result.has("findings")) return marks;
        for (var element : result.getAsJsonArray("findings")) {
            var finding = element.getAsJsonObject(); var location = MaterialChecksClient.object(finding, "location");
            if (!MaterialChecksClient.text(finding, "side", "").equals("candidate") || !location.has("path")) continue;
            try {
                var target = SourceNavigationClient.verify(root, location);
                var file = LocalFileSystem.getInstance().findFileByNioFile(target.path());
                var document = file == null ? null : FileDocumentManager.getInstance().getDocument(file);
                if (document == null || FileDocumentManager.getInstance().isDocumentUnsaved(document) || !document.getText().equals(target.text())) continue;
                int line = location.getAsJsonObject("start").get("line").getAsInt() - 1;
                var attributes = new TextAttributes();
                String severity = MaterialChecksClient.text(finding, "severity", "information").toLowerCase(java.util.Locale.ROOT);
                attributes.setEffectType(EffectType.WAVE_UNDERSCORE);
                attributes.setEffectColor(severity.equals("error") ? JBColor.RED : severity.equals("warning") ? JBColor.ORANGE : JBColor.GRAY);
                var mark = DocumentMarkupModel.forDocument(document, project, true).addLineHighlighter(line, HighlighterLayer.WARNING, attributes);
                mark.setErrorStripeMarkColor(attributes.getEffectColor());
                mark.setErrorStripeTooltip("Axiom saved material check: " + MaterialChecksClient.findingLabel(result, finding)); marks.add(mark);
            } catch (Exception ignored) { /* Historical source never decorates changed live bytes. */ }
        }
        return marks;
    }
}
