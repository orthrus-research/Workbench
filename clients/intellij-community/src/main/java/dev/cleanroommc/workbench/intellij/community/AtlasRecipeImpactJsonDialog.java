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

/** Complete read-only Atlas impact bytes returned by the installed core. */
final class AtlasRecipeImpactJsonDialog extends DialogWrapper {
    private final String json;

    AtlasRecipeImpactJsonDialog(@NotNull Project project, @NotNull String json) {
        this(project, json, "Complete Atlas Recipe Impact JSON");
    }

    AtlasRecipeImpactJsonDialog(@NotNull Project project, @NotNull String json, @NotNull String title) {
        super(project, false);
        this.json = json;
        setTitle(title);
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
        scroll.setPreferredSize(new Dimension(JBUI.scale(980), JBUI.scale(680)));
        return scroll;
    }
}
