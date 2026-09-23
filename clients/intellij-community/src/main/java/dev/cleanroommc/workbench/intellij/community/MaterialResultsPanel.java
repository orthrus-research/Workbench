package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.application.WriteIntentReadAction;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindowManager;
import com.intellij.ui.JBSplitter;
import com.intellij.ui.ListSpeedSearch;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBList;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;
import javax.swing.*;
import java.awt.BorderLayout;
import java.awt.FlowLayout;
import java.awt.event.*;
import java.util.*;
import java.util.List;
import java.util.function.BooleanSupplier;
import static dev.cleanroommc.workbench.intellij.community.MaterialChecksClient.text;
import static dev.cleanroommc.workbench.intellij.community.MaterialChecksClient.object;

/** A persistent native result browser; page reads never replace the execution task. */
final class MaterialResultsPanel extends JPanel implements Disposable {
    interface Owner {
        JsonObject read(String action, JsonObject options) throws Exception;
        void open(String action, JsonObject finding, boolean fresh) throws Exception;
        void annotations(JsonObject view);
        void recipes(JsonObject snapshot);
        void actions();
    }
    private final Project project;
    private final JBLabel headline = new JBLabel(), summary = new JBLabel(), status = new JBLabel();
    private final JComboBox<String> groups = new JComboBox<>();
    private final DefaultListModel<JsonObject> rows = new DefaultListModel<>();
    private final JBList<JsonObject> findings = new JBList<>(rows);
    private final JTextArea detail = new JTextArea();
    private final JButton open = new JButton("Open Source"), captured = new JButton("Captured Source"), evidence = new JButton("Original Diagnostic"),
            next = new JButton("Load More"), recipes = new JButton("Recipe Details"), actions = new JButton("Run Actions");
    private final Map<String, JsonObject> pages = new LinkedHashMap<>();
    private final Set<String> pending = new HashSet<>();
    private Content content;
    private JsonObject result;
    private Owner owner;
    private BooleanSupplier currentSource;
    private boolean stale, disposed, selecting;
    private long generation;

    MaterialResultsPanel(Project project) {
        super(new BorderLayout(0, JBUI.scale(6))); this.project = project;
        setName("Axiom Results"); setBorder(JBUI.Borders.empty(6));
        var top = new JPanel(); top.setLayout(new BoxLayout(top, BoxLayout.Y_AXIS));
        headline.setAlignmentX(LEFT_ALIGNMENT); summary.setAlignmentX(LEFT_ALIGNMENT); groups.setAlignmentX(LEFT_ALIGNMENT);
        top.add(headline); top.add(summary); top.add(groups); add(top, BorderLayout.NORTH);
        groups.getAccessibleContext().setAccessibleName("Finding severity and source location");
        groups.setRenderer(new DefaultListCellRenderer() {
            @Override public java.awt.Component getListCellRendererComponent(JList<?> list, Object value, int index, boolean selected, boolean focus) {
                super.getListCellRendererComponent(list, value, index, selected, focus);
                if (value != null && result != null) setText(groupTitle(value.toString()) + " · " + groupCount(value.toString()) + " findings"); return this;
            }
        });
        findings.setName("Axiom findings"); findings.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        findings.getAccessibleContext().setAccessibleName("Axiom findings in selected group");
        findings.setCellRenderer(new DefaultListCellRenderer() {
            @Override public java.awt.Component getListCellRendererComponent(JList<?> list, Object element, int index, boolean selected, boolean focus) {
                super.getListCellRendererComponent(list, element, index, selected, focus);
                if (!(element instanceof JsonObject value)) return this;
                String label = label(value).split("\n", 2)[0].replaceAll("\\b(?:[a-z_]\\w*\\.)+([A-Z][\\w$]*)", "$1");
                if (label.length() > 100) label = label.substring(0, 99) + "…";
                String location = MaterialResultsPanel.this.location(value); String shortLocation = location.substring(location.lastIndexOf('/') + 1);
                setText("<html>" + escape(label) + "<br><small>" + escape(shortLocation) + "</small></html>");
                setBorder(JBUI.Borders.empty(3, 6));
                getAccessibleContext().setAccessibleName(label(value) + ". " + location);
                return this;
            }
        });
        ListSpeedSearch.installOn(findings, this::label);
        findings.getEmptyText().setText("No findings in this group");
        detail.setEditable(false); detail.setLineWrap(true); detail.setWrapStyleWord(true);
        detail.getAccessibleContext().setAccessibleName("Selected finding and source anchor");
        var splitter = new JBSplitter(true, 0.60f); splitter.setFirstComponent(new JBScrollPane(findings)); splitter.setSecondComponent(new JBScrollPane(detail));
        add(splitter, BorderLayout.CENTER);
        var bottom = new JPanel(); bottom.setLayout(new BoxLayout(bottom, BoxLayout.Y_AXIS));
        for (var buttons : List.of(List.of(open, captured, evidence), List.of(next, recipes, actions))) {
            var bar = new JPanel(new FlowLayout(FlowLayout.LEADING, JBUI.scale(4), 0)); buttons.forEach(bar::add); bottom.add(bar);
        }
        bottom.add(status); add(bottom, BorderLayout.SOUTH);
        findings.addListSelectionListener(event -> { if (!event.getValueIsAdjusting()) selection(); });
        findings.addMouseListener(new MouseAdapter() { @Override public void mouseClicked(MouseEvent event) { if (event.getClickCount() == 2) activate("open"); } });
        findings.getInputMap().put(KeyStroke.getKeyStroke(KeyEvent.VK_ENTER, 0), "openFinding");
        findings.getActionMap().put("openFinding", new AbstractAction() { @Override public void actionPerformed(ActionEvent e) { activate("open"); } });
        open.addActionListener(e -> activate("open")); captured.addActionListener(e -> activate("captured")); evidence.addActionListener(e -> activate("evidence"));
        next.addActionListener(e -> load()); groups.addActionListener(e -> { if (!selecting) selectGroup(); });
        recipes.addActionListener(e -> {
            var model = result; var bound = owner;
            read(() -> bound.read("show", null), snapshot -> { if (model == result) bound.recipes(snapshot); });
        });
        actions.addActionListener(e -> WriteIntentReadAction.run(() -> owner.actions()));
        controls();
    }
    static String outcome(JsonObject result) {
        if ((text(result, "native_status", "").equals("source-error") || text(object(result, "native"), "status", "").equals("source-error"))) return "Initialization stopped at a script error";
        if (text(result, "native_outcome", "").equals("native-failed")) return "Native initialization reported errors";
        if (!text(result, "coverage", "").equals("complete")) return "Initialization observations are incomplete";
        return "Selected initialization scope completed";
    }
    static String groupTitle(String group) {
        if (group.equals("all")) return "All findings · legacy counts unavailable";
        return (group.startsWith("error") ? "Errors" : group.startsWith("warning") ? "Warnings" : "Information")
                + (group.endsWith("-unlocated") ? " · No source location" : " · With source locations");
    }
    static String group(JsonObject finding) {
        String severity = text(finding, "severity", "").toLowerCase(Locale.ROOT);
        return (List.of("error", "warning").contains(severity) ? severity : "information")
                + (object(finding, "location").has("path") ? "-located" : "-unlocated");
    }
    private static String escape(String value) { return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"); }
    private String label(JsonObject finding) { return MaterialChecksClient.findingLabel(result, finding); }
    private String location(JsonObject finding) {
        var loc = object(finding, "location");
        return loc.has("path") ? text(loc, "path", "") + ":" + object(loc, "start").get("line").getAsString() : "No source location";
    }
    private long groupCount(String group) { return group.equals("all") ? result.get("findings_count").getAsLong() : result.getAsJsonObject("finding_counts").get(group).getAsLong(); }
    private boolean fresh() { return !stale && currentSource != null && currentSource.getAsBoolean(); }
    void show(JsonObject value, BooleanSupplier currentSource, Owner owner) {
        generation++; result = value; this.currentSource = currentSource; this.owner = owner; stale = false; pages.clear(); pending.clear();
        selecting = true; groups.removeAllItems();
        var keys = value.has("finding_counts") ? MaterialDeliveryClient.GROUPS : List.of("all");
        for (String key : keys) if (groupCount(key) > 0) {
            groups.addItem(key); var initial = new JsonArray();
            value.getAsJsonArray("findings").forEach(row -> { if (key.equals("all") || group(row.getAsJsonObject()).equals(key)) initial.add(row); });
            if (!initial.isEmpty()) {
                var page = new JsonObject(); page.add("findings", initial);
                if (initial.size() == groupCount(key)) page.add("next_offset", com.google.gson.JsonNull.INSTANCE); else page.addProperty("next_offset", initial.size());
                pages.put(key, page);
            }
        }
        selecting = false; selectGroup(); refresh();
        var window = ToolWindowManager.getInstance(project).getToolWindow(WorkbenchRecordsToolWindowFactory.TOOL_WINDOW_ID);
        if (window != null) {
            var manager = window.getContentManager();
            if (content == null) { content = ContentFactory.getInstance().createContent(this, "Axiom Results", false); content.setDisposer(this); content.setPreferredFocusableComponent(findings); manager.addContent(content); }
            manager.setSelectedContent(content); window.show();
        }
    }
    void update(JsonObject value) { if (result == value) refresh(); }
    void invalidateSource() { stale = true; refresh(); }
    void clearResult() { generation++; result = null; pages.clear(); pending.clear(); rows.clear(); detail.setText(""); headline.setText("Running a new saved check…"); summary.setText(""); status.setText(""); controls(); }
    private void refresh() {
        if (result == null || disposed) return;
        headline.setText("<html><b>" + escape(outcome(result)) + "</b></html>");
        var counts = object(result, "finding_counts");
        String totals = result.get("findings_count").getAsString() + " findings · legacy counts unavailable";
        if (counts.size() > 0) {
            var totalsBySeverity = new ArrayList<String>();
            for (String severity : List.of("error", "warning", "information")) totalsBySeverity.add((counts.get(severity + "-located").getAsLong() + counts.get(severity + "-unlocated").getAsLong()) + " " + (severity.equals("information") ? severity : severity + "s"));
            totals = String.join(" · ", totalsBySeverity);
        }
        String state = switch (text(result, "detail_state", "")) { case "ready" -> "Recipe details ready"; case "preparing" -> "Preparing recipe details"; case "interrupted" -> "Recipe details interrupted"; case "not-started" -> "Recipe details not started"; default -> "Recipe details unavailable"; };
        summary.setText("<html>" + escape(totals) + "<br>" + (fresh() ? "Saved source matches" : "Source changed or not verified · captured source available")
                + "<br>" + (text(result, "coverage", "").equals("complete") ? "Selected scope observed" : "Observations incomplete") + " · " + state + "</html>");
        String key = (String) groups.getSelectedItem();
        status.setText(key == null ? "No findings" : pending.contains(key) ? "Loading selected findings…" : rows.size() + " of " + groupCount(key) + " findings loaded in this group");
        controls();
    }
    private void controls() {
        var finding = findings.getSelectedValue(); boolean located = finding != null && object(finding, "location").has("path");
        open.setEnabled(located); captured.setEnabled(located); evidence.setEnabled(finding != null);
        String key = (String) groups.getSelectedItem(); var page = pages.get(key);
        next.setEnabled(result != null && key != null && !pending.contains(key) && (page == null || !page.get("next_offset").isJsonNull()));
        recipes.setEnabled(result != null && text(result, "detail_state", "").equals("ready")); actions.setEnabled(result != null);
    }
    private void selection() {
        var value = findings.getSelectedValue();
        detail.setText(value == null ? "Select a finding to read it here. Enter opens its verified source or original evidence." : label(value) + "\n\n" + MaterialResultsPanel.this.location(value)
                + (object(value, "location").has("path") ? "\nNative call-site anchor; the expression may be on a following line." : "\nThis native observation has no source location.")
                + "\n\n" + text(value, "side", "candidate") + " · " + text(value, "severity", "information") + "\nOriginal Diagnostic opens complete retained evidence.");
        detail.setCaretPosition(0); controls();
    }
    private void selectGroup() {
        rows.clear(); String key = (String) groups.getSelectedItem(); var page = pages.get(key);
        if (page != null) page.getAsJsonArray("findings").forEach(row -> rows.addElement(row.getAsJsonObject()));
        if (!rows.isEmpty()) findings.setSelectedIndex(0); else selection();
        refresh(); if (key != null && page == null) load();
    }
    private void load() {
        String key = (String) groups.getSelectedItem();
        if (key == null || pending.contains(key) || result == null) return;
        var previous = pages.get(key); if (previous != null && previous.get("next_offset").isJsonNull()) return;
        var options = MaterialDeliveryClient.options(result); if (!key.equals("all")) options.addProperty("group", key);
        options.addProperty("offset", previous == null ? 0 : previous.get("next_offset").getAsInt());
        pending.add(key); refresh(); var bound = owner;
        read(() -> bound.read("diagnostics", options), page -> {
            pending.remove(key);
            if (!result.get("diagnostic_id").equals(page.get("diagnostic_id")) || !result.get("request_id").equals(page.get("request_id"))) throw new IllegalArgumentException("Findings belong to another run.");
            if (previous != null) { var all = previous.getAsJsonArray("findings").deepCopy(); all.addAll(page.getAsJsonArray("findings")); page.add("findings", all); }
            pages.put(key, page);
            var labels = object(object(result, "_presentation"), "finding_labels"); object(object(page, "_presentation"), "finding_labels").entrySet().forEach(row -> labels.add(row.getKey(), row.getValue()));
            if (!page.has("_sourceCurrent") || !page.get("_sourceCurrent").getAsBoolean()) stale = true;
            var annotations = result.deepCopy(); var all = new JsonArray(); pages.values().forEach(p -> all.addAll(p.getAsJsonArray("findings"))); annotations.add("findings", all); annotations.addProperty("_sourceCurrent", fresh()); bound.annotations(annotations);
            if (key.equals(groups.getSelectedItem())) {
                if (previous == null) selectGroup();
                else { for (int i = rows.size(); i < page.getAsJsonArray("findings").size(); i++) rows.addElement(page.getAsJsonArray("findings").get(i).getAsJsonObject()); refresh(); }
            } else refresh();
        }, () -> { pending.remove(key); controls(); });
    }
    private void activate(String action) {
        var finding = findings.getSelectedValue(); if (finding == null || result == null) return;
        WriteIntentReadAction.run(() -> {
            try { owner.open(action, finding, fresh()); }
            catch (Exception error) { status.setText(error.getMessage()); }
        });
    }
    @FunctionalInterface interface Read { JsonObject run() throws Exception; }
    @FunctionalInterface interface Present { void run(JsonObject value) throws Exception; }
    void read(Read operation, Present present) { read(operation, present, () -> {}); }
    private void read(Read operation, Present present, Runnable failed) {
        long ticket = generation;
        new Task.Backgroundable(project, "Reading Axiom evidence", false) {
            @Override public void run(@NotNull ProgressIndicator indicator) {
                try { var value = operation.run(); ui(() -> { if (ticket == generation) { try { present.run(value); } catch (Exception error) { failed.run(); status.setText(error.getMessage()); } } }); }
                catch (Exception error) { ui(() -> { if (ticket == generation) { failed.run(); status.setText("Evidence unavailable: " + error.getMessage()); } }); }
            }
        }.queue();
    }
    private void ui(Runnable run) { ApplicationManager.getApplication().invokeLater(() -> { if (!disposed && !project.isDisposed()) run.run(); }); }
    @Override public void dispose() { disposed = true; generation++; result = null; }
}
