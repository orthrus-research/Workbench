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

/** Exact read-only detail for an owner-returned Home or Work Session row. */
final class ProductSpineDetailDialog extends DialogWrapper {
    private final String content;

    ProductSpineDetailDialog(
            @NotNull Project project, @NotNull String title, @NotNull String content
    ) {
        super(project, false);
        this.content = content;
        setTitle(title);
        setOKButtonText("Close");
        init();
    }

    @Override
    protected @Nullable JComponent createCenterPanel() {
        JBTextArea area = new JBTextArea(content);
        area.setEditable(false);
        area.setLineWrap(false);
        area.setFont(new Font(Font.MONOSPACED, Font.PLAIN, area.getFont().getSize()));
        area.setCaretPosition(0);
        JBScrollPane scroll = new JBScrollPane(area);
        scroll.setBorder(BorderFactory.createLineBorder(JBColor.border()));
        scroll.setPreferredSize(new Dimension(JBUI.scale(900), JBUI.scale(560)));
        return scroll;
    }
}
