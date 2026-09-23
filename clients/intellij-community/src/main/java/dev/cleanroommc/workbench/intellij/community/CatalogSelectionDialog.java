package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.ui.ValidationInfo;
import com.intellij.ui.JBColor;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBList;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.DefaultListCellRenderer;
import javax.swing.DefaultListModel;
import javax.swing.JComponent;
import javax.swing.JList;
import javax.swing.JPanel;
import javax.swing.ListSelectionModel;
import javax.swing.SwingUtilities;
import java.awt.BorderLayout;
import java.awt.Component;
import java.awt.Dimension;
import java.util.List;

/** Native selector for validated commands and progressively loaded retained findings. */
final class CatalogSelectionDialog<T> extends DialogWrapper {
    private final String prompt;
    private final JBList<Choice<T>> list;

    CatalogSelectionDialog(
            @NotNull Project project,
            @NotNull String title,
            @NotNull String prompt,
            @NotNull List<Choice<T>> choices
    ) {
        super(project, true);
        if (choices.isEmpty()) {
            throw new IllegalArgumentException("selection requires at least one choice");
        }
        this.prompt = prompt;
        DefaultListModel<Choice<T>> model = new DefaultListModel<>();
        choices.forEach(model::addElement);
        list = new JBList<>(model);
        list.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        list.setCellRenderer(new ChoiceRenderer());
        list.setVisibleRowCount(Math.min(12, choices.size()));
        list.setSelectedIndex(0);
        list.addMouseListener(new java.awt.event.MouseAdapter() {
            @Override
            public void mouseClicked(java.awt.event.MouseEvent event) {
                if (event.getClickCount() == 2 && SwingUtilities.isLeftMouseButton(event)) {
                    doOKAction();
                }
            }
        });
        setTitle(title);
        setOKButtonText("Continue");
        init();
    }

    @NotNull T selected() {
        Choice<T> selected = list.getSelectedValue();
        if (selected == null) {
            throw new IllegalStateException("catalog selection has no value");
        }
        return selected.value();
    }

    @Override
    protected @Nullable JComponent createCenterPanel() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(8)));
        panel.setBorder(JBUI.Borders.empty(4));
        panel.add(new JBLabel("<html>" + html(prompt) + "</html>"), BorderLayout.NORTH);
        JBScrollPane scroll = new JBScrollPane(list);
        scroll.setPreferredSize(new Dimension(JBUI.scale(820), JBUI.scale(480)));
        panel.add(scroll, BorderLayout.CENTER);
        return panel;
    }

    @Override
    protected @Nullable ValidationInfo doValidate() {
        return list.getSelectedValue() == null
                ? new ValidationInfo("Choose one catalog entry", list)
                : null;
    }

    @Override
    public @Nullable JComponent getPreferredFocusedComponent() {
        return list;
    }

    record Choice<T>(
            @NotNull String title,
            @NotNull String badges,
            @NotNull String detail,
            @NotNull T value
    ) {
    }

    private static final class ChoiceRenderer extends DefaultListCellRenderer {
        @Override
        public Component getListCellRendererComponent(
                JList<?> list,
                Object value,
                int index,
                boolean isSelected,
                boolean cellHasFocus
        ) {
            super.getListCellRendererComponent(list, value, index, isSelected, cellHasFocus);
            if (value instanceof Choice<?> choice) {
                String detailColor = isSelected
                        ? colorHex(list.getSelectionForeground())
                        : colorHex(JBColor.GRAY);
                setText(
                        "<html><b>" + html(choice.title()) + "</b> · " + html(choice.badges())
                                + "<br><span style='color:" + detailColor + "'>"
                                + html(choice.detail()) + "</span></html>"
                );
                setBorder(JBUI.Borders.empty(6, 8));
            }
            return this;
        }
    }

    private static @NotNull String colorHex(@NotNull java.awt.Color color) {
        return String.format("#%02x%02x%02x", color.getRed(), color.getGreen(), color.getBlue());
    }

    private static @NotNull String html(@NotNull String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;")
                .replace("\n", "<br>");
    }
}
