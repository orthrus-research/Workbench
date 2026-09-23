package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.ui.JBColor;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextArea;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.BorderFactory;
import javax.swing.JComponent;
import java.awt.Dimension;
import java.awt.Font;

/** Exact, read-only JSON returned by the owner presentation command. */
final class FeaturePresentationJsonDialog extends DialogWrapper {
    private final String json;

    FeaturePresentationJsonDialog(@NotNull Project project, @NotNull String json) {
        super(project, false);
        this.json = json;
        setTitle("Workbench Retained Record JSON");
        setOKButtonText("Close");
        init();
    }

    @Override
    protected @Nullable JComponent createCenterPanel() {
        JBTextArea area = new JBTextArea(json);
        area.setEditable(false);
        area.setLineWrap(false);
        area.setFont(new Font(Font.MONOSPACED, Font.PLAIN, area.getFont().getSize()));
        area.setCaretPosition(0);
        JBScrollPane scroll = new JBScrollPane(area);
        scroll.setBorder(BorderFactory.createLineBorder(JBColor.border()));
        scroll.setPreferredSize(new Dimension(JBUI.scale(960), JBUI.scale(640)));
        return scroll;
    }
}
