package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.ui.JBColor;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextArea;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.BorderFactory;
import javax.swing.JComponent;
import javax.swing.JPanel;
import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.Font;

/** Read-only native result surface for Atlas findings and Blueprint receipts. */
final class CommandOutputDialog extends DialogWrapper {
    private final CommandCatalog.Command command;
    private final String output;

    CommandOutputDialog(
            @NotNull Project project,
            @NotNull CommandCatalog.Command command,
            @NotNull String output
    ) {
        super(project, false);
        this.command = command;
        this.output = output;
        setTitle("Workbench · " + command.title());
        setOKButtonText("Close");
        init();
    }

    @Override
    protected @Nullable JComponent createCenterPanel() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(10)));
        panel.setBorder(JBUI.Borders.empty(4));
        panel.add(new JBLabel(
                "<html><b>" + html(command.title()) + "</b><br>"
                        + html(command.authority()) + " · " + html(command.risk()) + "</html>"
        ), BorderLayout.NORTH);
        JBTextArea area = new JBTextArea(output.isBlank()
                ? "(Workbench completed without output)"
                : output.stripTrailing());
        area.setEditable(false);
        area.setLineWrap(false);
        area.setFont(new Font(Font.MONOSPACED, Font.PLAIN, area.getFont().getSize()));
        area.setCaretPosition(0);
        JBScrollPane scroll = new JBScrollPane(area);
        scroll.setBorder(BorderFactory.createLineBorder(JBColor.border()));
        scroll.setPreferredSize(new Dimension(JBUI.scale(920), JBUI.scale(600)));
        panel.add(scroll, BorderLayout.CENTER);
        return panel;
    }

    private static @NotNull String html(@NotNull String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }
}
