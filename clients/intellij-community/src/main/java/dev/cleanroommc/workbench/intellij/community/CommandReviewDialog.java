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

/** Explicit consent surface for exact owner-rendered argv and limitations. */
final class CommandReviewDialog extends DialogWrapper {
    private static final int MAX_REVIEW_CHARS = 2 * 1024 * 1024;

    private final CommandCatalog.Command command;
    private final String review;
    private final @Nullable String ownerPreview;

    CommandReviewDialog(
            @NotNull Project project,
            @NotNull CommandCatalog.Command command,
            @NotNull String review,
            @Nullable String ownerPreview
    ) {
        super(project, true);
        if (review.length() > MAX_REVIEW_CHARS
                || (ownerPreview != null && ownerPreview.length() > MAX_REVIEW_CHARS)) {
            throw new IllegalArgumentException("Workbench owner preview exceeds the IDE review bound");
        }
        this.command = command;
        this.review = review;
        this.ownerPreview = ownerPreview;
        setTitle("Review " + command.title());
        setOKButtonText(command.risk().equals("read-only")
                ? "Run"
                : "Execute " + command.risk() + " Action");
        setCancelButtonText("Cancel");
        init();
    }

    @Override
    protected @Nullable JComponent createCenterPanel() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(10)));
        panel.setBorder(JBUI.Borders.empty(4));
        String color = command.risk().equals("read-only") ? "#4f6b45" : "#9a5d00";
        String limitations = command.limitations().isEmpty()
                ? ""
                : "<br>Limitations: " + html(String.join(" · ", command.limitations()));
        panel.add(new JBLabel(
                "<html><b>" + html(command.title()) + "</b> · "
                        + "<span style='color:" + color + "'>" + html(command.risk())
                        + "</span><br>" + html(command.summary()) + "<br>Authority: "
                        + html(command.authority()) + limitations + "</html>"
        ), BorderLayout.NORTH);

        StringBuilder text = new StringBuilder();
        text.append("Owner-rendered command\n")
                .append("======================\n")
                .append(review.stripTrailing());
        if (ownerPreview != null) {
            text.append("\n\nOwner preview\n")
                    .append("=============\n")
                    .append(ownerPreview.isBlank()
                            ? "(owner returned no preview text)"
                            : ownerPreview.stripTrailing());
        }
        JBTextArea area = textArea(text.toString());
        JBScrollPane scroll = new JBScrollPane(area);
        scroll.setBorder(BorderFactory.createLineBorder(JBColor.border()));
        scroll.setPreferredSize(new Dimension(JBUI.scale(880), JBUI.scale(540)));
        panel.add(scroll, BorderLayout.CENTER);
        panel.add(new JBLabel(command.risk().equals("read-only")
                ? "Run this exact digest-bound argv through the installed Workbench core?"
                : "The Shell owner will revalidate the digest-bound review before changing state."),
                BorderLayout.SOUTH);
        return panel;
    }

    private static @NotNull JBTextArea textArea(@NotNull String text) {
        JBTextArea area = new JBTextArea(text);
        area.setEditable(false);
        area.setLineWrap(false);
        area.setFont(new Font(Font.MONOSPACED, Font.PLAIN, area.getFont().getSize()));
        area.setCaretPosition(0);
        return area;
    }

    private static @NotNull String html(@NotNull String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }
}
