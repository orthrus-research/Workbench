package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.ComboBox;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.ui.ValidationInfo;
import com.intellij.ui.JBColor;
import com.intellij.ui.components.JBCheckBox;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBPasswordField;
import com.intellij.ui.components.JBRadioButton;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextField;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.AbstractButton;
import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.ButtonGroup;
import javax.swing.JComponent;
import javax.swing.JPanel;
import javax.swing.JTextField;
import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.event.ActionListener;
import java.math.BigInteger;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Bounded typed option form generated only from live catalog metadata. */
final class CommandOptionsDialog extends DialogWrapper {
    private static final Gson JSON = new GsonBuilder().disableHtmlEscaping().create();
    private static final int MAX_INPUT_CHARS = 1024 * 1024;

    private final CommandCatalog.Command command;
    private final @Nullable String workspacePath;
    private final JPanel body = new JPanel();
    private final Map<String, Row> rows = new LinkedHashMap<>();
    private final Map<String, ButtonGroup> mutexGroups = new LinkedHashMap<>();
    private final Map<String, JBRadioButton> emptyGroupChoices = new LinkedHashMap<>();

    CommandOptionsDialog(
            @NotNull Project project,
            @NotNull CommandCatalog.Command command,
            @Nullable String workspacePath
    ) {
        super(project, true);
        this.command = command;
        this.workspacePath = workspacePath;
        setTitle(command.title());
        setOKButtonText("Review Command");
        setCancelButtonText("Cancel");
        buildBody();
        init();
    }

    @NotNull Map<String, JsonElement> values() {
        Map<String, JsonElement> result = new LinkedHashMap<>();
        for (Row row : rows.values()) {
            if (row.selected()) {
                result.put(row.option().key(), row.parse());
            }
        }
        return Collections.unmodifiableMap(new LinkedHashMap<>(result));
    }

    @Override
    protected @Nullable JComponent createCenterPanel() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(10)));
        panel.setBorder(JBUI.Borders.empty(4));
        String limitations = command.limitations().isEmpty()
                ? ""
                : "<br>Known limits: " + html(String.join(" · ", command.limitations()));
        panel.add(new JBLabel(
                "<html><b>" + html(command.summary()) + "</b><br>Authority: "
                        + html(command.authority()) + " · Risk: " + html(command.risk())
                        + limitations + "</html>"
        ), BorderLayout.NORTH);
        JBScrollPane scrollPane = new JBScrollPane(body);
        scrollPane.setBorder(BorderFactory.createLineBorder(JBColor.border()));
        scrollPane.setPreferredSize(new Dimension(JBUI.scale(800), JBUI.scale(560)));
        panel.add(scrollPane, BorderLayout.CENTER);
        return panel;
    }

    @Override
    protected @Nullable ValidationInfo doValidate() {
        for (Map.Entry<String, ButtonGroup> group : mutexGroups.entrySet()) {
            boolean required = command.options().stream()
                    .anyMatch(option -> group.getKey().equals(option.mutexGroup())
                            && option.requiredGroup());
            if (required && group.getValue().getSelection() == null) {
                return new ValidationInfo("Choose one value for " + group.getKey(), body);
            }
        }
        for (Row row : rows.values()) {
            if (!row.selected()) {
                if (row.option().required()) {
                    return new ValidationInfo(
                            row.option().label() + " is required", row.focusTarget()
                    );
                }
                continue;
            }
            try {
                row.parse();
            } catch (IllegalArgumentException error) {
                return new ValidationInfo(error.getMessage(), row.focusTarget());
            }
        }
        return null;
    }

    private void buildBody() {
        body.setLayout(new BoxLayout(body, BoxLayout.Y_AXIS));
        body.setBorder(JBUI.Borders.empty(8));
        Set<String> emittedGroups = new LinkedHashSet<>();
        for (CommandCatalog.Option option : command.options()) {
            if (option.consoleManaged()) {
                continue;
            }
            if (option.mutexGroup() != null && emittedGroups.add(option.mutexGroup())) {
                addGroupHeader(option.mutexGroup());
            }
            Row row = createRow(option);
            rows.put(option.key(), row);
            body.add(row.panel());
            body.add(Box.createVerticalStrut(JBUI.scale(8)));
        }
        if (rows.isEmpty()) {
            body.add(new JBLabel("This action has no developer-supplied fields."));
        }
        initializeSelections();
    }

    private void addGroupHeader(@NotNull String groupId) {
        List<CommandCatalog.Option> members = command.options().stream()
                .filter(option -> groupId.equals(option.mutexGroup()))
                .toList();
        boolean required = members.stream().anyMatch(CommandCatalog.Option::requiredGroup);
        body.add(new JBLabel(
                "<html><b>Choose " + html(groupId) + (required ? " (required)" : " (optional)")
                        + "</b></html>"
        ));
        ButtonGroup group = new ButtonGroup();
        mutexGroups.put(groupId, group);
        if (!required) {
            JBRadioButton none = new JBRadioButton("Do not set this group", true);
            group.add(none);
            emptyGroupChoices.put(groupId, none);
            body.add(none);
            body.add(Box.createVerticalStrut(JBUI.scale(4)));
        }
    }

    private @NotNull Row createRow(@NotNull CommandCatalog.Option option) {
        AbstractButton selector = null;
        if (option.mutexGroup() != null) {
            JBRadioButton radio = new JBRadioButton(option.label());
            mutexGroups.get(option.mutexGroup()).add(radio);
            selector = radio;
        } else if (!option.required()) {
            selector = new JBCheckBox(option.label(), defaultSelected(option));
        }

        JComponent editor = createEditor(option);
        JPanel panel = new JPanel(new BorderLayout(JBUI.scale(8), JBUI.scale(4)));
        panel.setBorder(JBUI.Borders.compound(
                BorderFactory.createLineBorder(JBColor.border()),
                JBUI.Borders.empty(8)
        ));
        if (selector != null) {
            panel.add(selector, BorderLayout.NORTH);
        } else {
            panel.add(new JBLabel(
                    "<html><b>" + html(option.label()) + " (required)</b></html>"
            ), BorderLayout.NORTH);
        }
        if (editor != null) {
            panel.add(editor, BorderLayout.CENTER);
        }
        String detail = option.help() + " · " + option.kind()
                + (option.multiple() ? " · JSON array" : "")
                + (option.metavar() == null ? "" : " · " + option.metavar());
        panel.add(new JBLabel(
                "<html><span style='color:#777777'>" + html(detail) + "</span></html>"
        ), BorderLayout.SOUTH);

        Row row = new Row(option, selector, editor, panel);
        if (selector != null) {
            ActionListener listener = event -> row.updateEnabled();
            selector.addActionListener(listener);
        }
        row.updateEnabled();
        return row;
    }

    private @Nullable JComponent createEditor(@NotNull CommandCatalog.Option option) {
        if (option.kind().equals("boolean")) {
            return null;
        }
        if (option.kind().equals("choice") && !option.multiple()) {
            ComboBox<String> combo = new ComboBox<>(option.choices().toArray(String[]::new));
            if (option.defaultValue() != null
                    && option.defaultValue().isJsonPrimitive()
                    && option.defaultValue().getAsJsonPrimitive().isString()) {
                combo.setSelectedItem(option.defaultValue().getAsString());
            }
            return combo;
        }
        JTextField field = option.sensitive() ? new JBPasswordField() : new JBTextField();
        field.setText(defaultText(option));
        return field;
    }

    private void initializeSelections() {
        for (Map.Entry<String, ButtonGroup> entry : mutexGroups.entrySet()) {
            List<Row> members = rows.values().stream()
                    .filter(row -> entry.getKey().equals(row.option().mutexGroup()))
                    .toList();
            Row selected = members.stream()
                    .filter(row -> defaultSelected(row.option()))
                    .findFirst()
                    .orElse(null);
            boolean required = members.stream().anyMatch(row -> row.option().requiredGroup());
            if (selected == null && required && !members.isEmpty()) {
                selected = members.get(0);
            }
            if (selected != null && selected.selector() != null) {
                selected.selector().setSelected(true);
            } else if (emptyGroupChoices.containsKey(entry.getKey())) {
                emptyGroupChoices.get(entry.getKey()).setSelected(true);
            }
            members.forEach(Row::updateEnabled);
        }
    }

    private @NotNull String defaultText(@NotNull CommandCatalog.Option option) {
        JsonElement value = option.defaultValue();
        if (value == null || value.isJsonNull()) {
            if (option.kind().equals("path") && option.required()
                    && workspacePath != null && !option.multiple()) {
                return workspacePath;
            }
            return "";
        }
        if (option.multiple() || option.kind().equals("json")) {
            return JSON.toJson(value);
        }
        return value.isJsonPrimitive() ? value.getAsString() : JSON.toJson(value);
    }

    private static boolean defaultSelected(@NotNull CommandCatalog.Option option) {
        JsonElement value = option.defaultValue();
        if (value == null || value.isJsonNull()) {
            return false;
        }
        if (option.kind().equals("boolean")) {
            return value.isJsonPrimitive()
                    && value.getAsJsonPrimitive().isBoolean()
                    && value.getAsBoolean();
        }
        return !(value.isJsonPrimitive()
                && value.getAsJsonPrimitive().isString()
                && value.getAsString().isEmpty());
    }

    private static @NotNull String html(@NotNull String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }

    private record Row(
            @NotNull CommandCatalog.Option option,
            @Nullable AbstractButton selector,
            @Nullable JComponent editor,
            @NotNull JPanel panel
    ) {
        boolean selected() {
            return selector == null || selector.isSelected();
        }

        void updateEnabled() {
            setEnabled(editor, selected());
        }

        @NotNull JComponent focusTarget() {
            return editor == null ? panel : editor;
        }

        @NotNull JsonElement parse() {
            if (option.kind().equals("boolean")) {
                return new JsonPrimitive(true);
            }
            if (editor instanceof ComboBox<?> combo) {
                Object selected = combo.getSelectedItem();
                if (!(selected instanceof String value) || !option.choices().contains(value)) {
                    throw new IllegalArgumentException(
                            option.label() + " requires a declared choice"
                    );
                }
                return new JsonPrimitive(value);
            }
            if (!(editor instanceof JTextField field)) {
                throw new IllegalArgumentException(
                        option.label() + " has no supported input editor"
                );
            }
            String raw = field.getText();
            if (raw.length() > MAX_INPUT_CHARS) {
                throw new IllegalArgumentException(
                        option.label() + " exceeds the supported input bound"
                );
            }
            if (raw.isEmpty()) {
                throw new IllegalArgumentException(option.label() + " requires a value");
            }
            if (option.multiple() || option.kind().equals("json")) {
                final JsonElement parsed;
                try {
                    parsed = JsonParser.parseString(raw);
                } catch (RuntimeException error) {
                    throw new IllegalArgumentException(
                            option.label() + " requires valid JSON", error
                    );
                }
                if (option.multiple()) {
                    validateMultiple(parsed);
                }
                if ((option.required() || option.requiredGroup()) && parsed.isJsonNull()) {
                    throw new IllegalArgumentException(option.label() + " cannot be null");
                }
                return parsed;
            }
            if (option.kind().equals("integer")) {
                try {
                    return new JsonPrimitive(new BigInteger(raw));
                } catch (NumberFormatException error) {
                    throw new IllegalArgumentException(
                            option.label() + " requires an integer", error
                    );
                }
            }
            if (option.kind().equals("choice") && !option.choices().contains(raw)) {
                throw new IllegalArgumentException(
                        option.label() + " requires a declared choice"
                );
            }
            return new JsonPrimitive(raw);
        }

        private void validateMultiple(@NotNull JsonElement value) {
            if (!value.isJsonArray()) {
                throw new IllegalArgumentException(option.label() + " requires a JSON array");
            }
            JsonArray values = value.getAsJsonArray();
            if ((option.required() || option.requiredGroup() || option.nargs().equals("one_or_more"))
                    && values.isEmpty()) {
                throw new IllegalArgumentException(
                        option.label() + " requires at least one value"
                );
            }
            final int exact;
            try {
                exact = Integer.parseInt(option.nargs());
            } catch (NumberFormatException ignored) {
                return;
            }
            if (option.repeat() && values.asList().stream().anyMatch(JsonElement::isJsonArray)) {
                boolean valid = values.asList().stream().allMatch(item ->
                        item.isJsonArray() && item.getAsJsonArray().size() == exact
                );
                if (!valid) {
                    throw new IllegalArgumentException(
                            option.label() + " requires repeated groups of " + exact + " values"
                    );
                }
            } else if (values.size() != exact) {
                throw new IllegalArgumentException(
                        option.label() + " requires exactly " + exact + " values"
                );
            }
        }

        private static void setEnabled(@Nullable JComponent component, boolean enabled) {
            if (component == null) {
                return;
            }
            component.setEnabled(enabled);
            for (java.awt.Component child : component.getComponents()) {
                child.setEnabled(enabled);
            }
        }
    }
}
