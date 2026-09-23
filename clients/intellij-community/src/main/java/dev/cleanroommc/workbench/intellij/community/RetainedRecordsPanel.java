package dev.cleanroommc.workbench.intellij.community;

import com.intellij.ide.util.PropertiesComponent;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.ui.JBSplitter;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBList;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextField;
import com.intellij.ui.treeStructure.Tree;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.DefaultListCellRenderer;
import javax.swing.DefaultListModel;
import javax.swing.JButton;
import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JList;
import javax.swing.JPanel;
import javax.swing.ListSelectionModel;
import javax.swing.SwingUtilities;
import javax.swing.event.TreeSelectionEvent;
import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.DefaultTreeModel;
import java.awt.BorderLayout;
import java.awt.Component;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.event.KeyAdapter;
import java.awt.event.KeyEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicLong;

/** Persistent native retained-record explorer and transaction tree. */
final class RetainedRecordsPanel extends JPanel implements Disposable {
    static final String STATE_ROOT_PROPERTY = "workbench.developerFeatureStateRoot";
    private static final String ALL = "All";
    private static final String[] FAMILIES = {
            ALL, "material-fluid-recipe", "recipe-change", "quest-for-process"
    };
    private static final String[] COLLECTIONS = {
            ALL, "plans", "receipts", "rollbacks", "recoveries", "runs"
    };

    private final Project project;
    private final JBTextField stateRoot = new JBTextField();
    private final JComboBox<String> family = new JComboBox<>(FAMILIES);
    private final JComboBox<String> collection = new JComboBox<>(COLLECTIONS);
    private final JButton refresh = new JButton("Refresh");
    private final DefaultListModel<FeatureRecordCatalog.Record> recordModel =
            new DefaultListModel<>();
    private final JBList<FeatureRecordCatalog.Record> recordList = new JBList<>(recordModel);
    private final Tree tree = new Tree(
            new DefaultTreeModel(new DefaultMutableTreeNode("Select a retained record"))
    );
    private final JButton openDiff = new JButton("Open Native Diff");
    private final JButton transactionJson = new JButton("Show Transaction JSON");
    private final JButton rawJson = new JButton("Show Owner Projection JSON");
    private final JBLabel status = new JBLabel("Open the tool window to discover retained records.");
    private final JBLabel limitations = new JBLabel();
    private final AtomicLong generation = new AtomicLong();

    private List<FeatureRecordCatalog.Record> allRecords = List.of();
    private FeaturePresentation presentation;
    private RetainedFeatureClient.TransactionView transaction;
    private boolean disposed;

    RetainedRecordsPanel(@NotNull Project project) {
        super(new BorderLayout(0, JBUI.scale(6)));
        this.project = project;
        setBorder(JBUI.Borders.empty(6));
        String configuredState = PropertiesComponent.getInstance(project)
                .getValue(STATE_ROOT_PROPERTY);
        stateRoot.setText(configuredState == null ? "" : configuredState);
        stateRoot.getEmptyText().setText("Core default retained state");
        add(filters(), BorderLayout.NORTH);
        add(content(), BorderLayout.CENTER);
        add(statusArea(), BorderLayout.SOUTH);
        configureInteractions();
        refreshRecords();
    }

    void refreshRecords() {
        if (disposed || project.isDisposed()) {
            return;
        }
        String selectedStateRoot = stateRoot.getText().trim();
        PropertiesComponent properties = PropertiesComponent.getInstance(project);
        if (selectedStateRoot.isEmpty()) {
            properties.unsetValue(STATE_ROOT_PROPERTY);
        } else {
            properties.setValue(STATE_ROOT_PROPERTY, selectedStateRoot);
        }
        long request = generation.incrementAndGet();
        setBusy(true, "Discovering retained owner records…");
        allRecords = List.of();
        recordModel.clear();
        showEmpty("Discovering retained owner records…");
        String executable = CoreLocation.discover(project);
        String workingDirectory = project.getBasePath();
        new Task.Backgroundable(project, "Discovering Workbench retained records", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    CoreLaunch launch = CoreLaunch.resolve(executable);
                    String output = CommandProcess.capture(
                            launch,
                            RetainedFeatureClient.recordsArguments(launch, selectedStateRoot),
                            FeatureRecordCatalog.MAX_BYTES,
                            900,
                            workingDirectory
                    );
                    FeatureRecordCatalog catalog = FeatureRecordCatalog.parse(output);
                    ApplicationManager.getApplication().invokeLater(
                            () -> acceptCatalog(request, catalog)
                    );
                } catch (Exception error) {
                    failLater(request, "Retained record discovery failed", error);
                }
            }
        }.queue();
    }

    private @NotNull JComponent filters() {
        JPanel panel = new JPanel(new GridBagLayout());
        GridBagConstraints constraints = new GridBagConstraints();
        constraints.gridy = 0;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), JBUI.scale(6));
        constraints.anchor = GridBagConstraints.WEST;
        panel.add(new JBLabel("Family"), constraints);
        constraints.gridx = 1;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        constraints.weightx = 0.4;
        panel.add(family, constraints);
        constraints.gridx = 2;
        constraints.fill = GridBagConstraints.NONE;
        constraints.weightx = 0;
        panel.add(new JBLabel("Collection"), constraints);
        constraints.gridx = 3;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        constraints.weightx = 0.3;
        panel.add(collection, constraints);
        constraints.gridx = 4;
        constraints.fill = GridBagConstraints.NONE;
        constraints.weightx = 0;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), 0);
        panel.add(refresh, constraints);

        constraints.gridx = 0;
        constraints.gridy = 1;
        constraints.fill = GridBagConstraints.NONE;
        constraints.weightx = 0;
        constraints.insets = new Insets(0, 0, 0, JBUI.scale(6));
        panel.add(new JBLabel("State root"), constraints);
        constraints.gridx = 1;
        constraints.gridwidth = 4;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        constraints.weightx = 1;
        constraints.insets = new Insets(0, 0, 0, 0);
        panel.add(stateRoot, constraints);
        return panel;
    }

    private @NotNull JComponent content() {
        recordList.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        recordList.setCellRenderer(new RecordRenderer());
        recordList.setVisibleRowCount(12);
        JBScrollPane recordScroll = new JBScrollPane(recordList);
        recordScroll.setMinimumSize(new Dimension(JBUI.scale(260), JBUI.scale(300)));

        tree.setRootVisible(true);
        tree.setShowsRootHandles(true);
        JBScrollPane treeScroll = new JBScrollPane(tree);
        JPanel details = new JPanel(new BorderLayout(0, JBUI.scale(4)));
        details.add(treeScroll, BorderLayout.CENTER);
        JPanel buttons = new JPanel(new FlowLayout(FlowLayout.LEFT, JBUI.scale(6), 0));
        buttons.add(openDiff);
        buttons.add(transactionJson);
        buttons.add(rawJson);
        details.add(buttons, BorderLayout.SOUTH);

        JBSplitter splitter = new JBSplitter(false, 0.34f);
        splitter.setFirstComponent(recordScroll);
        splitter.setSecondComponent(details);
        splitter.setHonorComponentsMinimumSize(true);
        return splitter;
    }

    private @NotNull JComponent statusArea() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(2)));
        limitations.setVisible(false);
        panel.add(status, BorderLayout.NORTH);
        panel.add(limitations, BorderLayout.SOUTH);
        return panel;
    }

    private void configureInteractions() {
        refresh.addActionListener(event -> refreshRecords());
        stateRoot.addActionListener(event -> refreshRecords());
        family.addActionListener(event -> applyFilters());
        collection.addActionListener(event -> applyFilters());
        recordList.addListSelectionListener(event -> {
            if (!event.getValueIsAdjusting()) {
                FeatureRecordCatalog.Record selected = recordList.getSelectedValue();
                if (selected != null) {
                    loadPresentation(selected);
                } else {
                    showEmpty("No retained record matches the current filters.");
                }
            }
        });
        tree.addTreeSelectionListener(this::treeSelectionChanged);
        tree.addMouseListener(new MouseAdapter() {
            @Override
            public void mouseClicked(MouseEvent event) {
                if (event.getClickCount() == 2 && SwingUtilities.isLeftMouseButton(event)) {
                    openSelectedDiff();
                }
            }
        });
        tree.addKeyListener(new KeyAdapter() {
            @Override
            public void keyPressed(KeyEvent event) {
                if (event.getKeyCode() == KeyEvent.VK_ENTER && openDiff.isEnabled()) {
                    openSelectedDiff();
                    event.consume();
                }
            }
        });
        openDiff.addActionListener(event -> openSelectedDiff());
        transactionJson.addActionListener(event -> {
            RetainedFeatureClient.TransactionView selected = transaction;
            if (selected != null) {
                new FeaturePresentationJsonDialog(project, selected.rawJson()).show();
            }
        });
        rawJson.addActionListener(event -> {
            FeaturePresentation selected = presentation;
            if (selected != null) {
                new FeaturePresentationJsonDialog(project, selected.rawJson()).show();
            }
        });
        openDiff.setEnabled(false);
        transactionJson.setEnabled(false);
        rawJson.setEnabled(false);
    }

    private void acceptCatalog(long request, @NotNull FeatureRecordCatalog catalog) {
        if (!current(request)) {
            return;
        }
        allRecords = catalog.records();
        limitations.setText(
                catalog.limitations().isEmpty()
                        ? ""
                        : "<html>Boundary: " + html(String.join(" · ", catalog.limitations()))
                                + "</html>"
        );
        limitations.setToolTipText(String.join("\n", catalog.limitations()));
        limitations.setVisible(!catalog.limitations().isEmpty());
        setBusy(false,
                catalog.records().isEmpty()
                        ? "No retained records are present in " + catalog.stateRootUri()
                        : catalog.records().size() + " retained record(s) in "
                                + catalog.stateRootUri()
        );
        applyFilters();
    }

    private void applyFilters() {
        if (disposed) {
            return;
        }
        FeatureRecordCatalog.Record previous = recordList.getSelectedValue();
        String selectedFamily = Objects.toString(family.getSelectedItem(), ALL);
        String selectedCollection = Objects.toString(collection.getSelectedItem(), ALL);
        List<FeatureRecordCatalog.Record> filtered = new ArrayList<>();
        for (FeatureRecordCatalog.Record record : allRecords) {
            if ((selectedFamily.equals(ALL) || record.family().equals(selectedFamily))
                    && (selectedCollection.equals(ALL)
                    || record.collection().equals(selectedCollection))) {
                filtered.add(record);
            }
        }
        recordModel.clear();
        filtered.forEach(recordModel::addElement);
        int selectedIndex = -1;
        if (previous != null) {
            for (int index = 0; index < recordModel.size(); index++) {
                FeatureRecordCatalog.Record candidate = recordModel.get(index);
                if (candidate.collection().equals(previous.collection())
                        && candidate.recordId().equals(previous.recordId())) {
                    selectedIndex = index;
                    break;
                }
            }
        }
        if (selectedIndex < 0 && !filtered.isEmpty()) {
            selectedIndex = 0;
        }
        if (selectedIndex >= 0) {
            recordList.setSelectedIndex(selectedIndex);
        } else {
            showEmpty("No retained record matches the current filters.");
        }
    }

    private void loadPresentation(@NotNull FeatureRecordCatalog.Record record) {
        String selectedStateRoot = stateRoot.getText().trim();
        long request = generation.incrementAndGet();
        presentation = null;
        transaction = null;
        rawJson.setEnabled(false);
        transactionJson.setEnabled(false);
        openDiff.setEnabled(false);
        tree.setModel(new DefaultTreeModel(new DefaultMutableTreeNode(
                "Loading " + record.recordId() + "…"
        )));
        status.setText("Deriving current transaction state and reopening owner bytes…");
        String executable = CoreLocation.discover(project);
        String workingDirectory = project.getBasePath();
        new Task.Backgroundable(project, "Opening Workbench retained record", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    CoreLaunch launch = CoreLaunch.resolve(executable);
                    String transactionOutput = CommandProcess.capture(
                            launch,
                            RetainedFeatureClient.transactionArguments(
                                    launch, record, selectedStateRoot
                            ),
                            RetainedFeatureClient.TransactionView.MAX_BYTES,
                            900,
                            workingDirectory
                    );
                    RetainedFeatureClient.TransactionView transaction =
                            RetainedFeatureClient.TransactionView.parse(transactionOutput);
                    String output = CommandProcess.capture(
                            launch,
                            RetainedFeatureClient.presentationArguments(
                                    launch, record, selectedStateRoot
                            ),
                            FeaturePresentation.MAX_BYTES,
                            900,
                            workingDirectory
                    );
                    FeaturePresentation parsed = FeaturePresentation.parse(output);
                    RetainedFeatureClient.validateTransactionLink(
                            record, parsed, transaction
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> acceptPresentation(request, parsed, transaction)
                    );
                } catch (Exception error) {
                    failLater(request, "Retained record could not be reopened", error);
                }
            }
        }.queue();
    }

    private void acceptPresentation(
            long request,
            @NotNull FeaturePresentation parsed,
            @NotNull RetainedFeatureClient.TransactionView transaction
    ) {
        if (!current(request)) {
            return;
        }
        presentation = parsed;
        this.transaction = transaction;
        tree.setModel(new DefaultTreeModel(
                FeaturePresentationTree.buildTransaction(transaction, parsed)
        ));
        tree.expandRow(0);
        for (int row = 1; row < Math.min(tree.getRowCount(), 9); row++) {
            tree.expandRow(row);
        }
        rawJson.setEnabled(true);
        transactionJson.setEnabled(true);
        openDiff.setEnabled(false);
        status.setText(
                "CURRENT: " + transaction.currentEffectiveState().toUpperCase(java.util.Locale.ROOT)
                        + " · workspace " + transaction.workspaceMatch().state()
                        + " · plan " + transaction.planFreshness().state()
                        + " · selected immutable " + parsed.collection() + " record "
                        + parsed.ownerRecord().state()
        );
    }

    private void treeSelectionChanged(@NotNull TreeSelectionEvent event) {
        openDiff.setEnabled(
                FeaturePresentationTree.selectedOperation(event.getPath()) != null
        );
    }

    private void openSelectedDiff() {
        FeaturePresentation.Operation operation = FeaturePresentationTree.selectedOperation(
                tree.getSelectionPath()
        );
        if (operation == null) {
            return;
        }
        try {
            FeatureDiffOpener.open(project, operation);
        } catch (IOException | RuntimeException error) {
            WorkbenchNotifications.commandFailed(
                    project, "Workbench native diff could not be opened", error
            );
        }
    }

    private void showEmpty(@NotNull String message) {
        presentation = null;
        transaction = null;
        rawJson.setEnabled(false);
        transactionJson.setEnabled(false);
        openDiff.setEnabled(false);
        tree.setModel(new DefaultTreeModel(new DefaultMutableTreeNode(message)));
    }

    private void setBusy(boolean busy, @NotNull String message) {
        refresh.setEnabled(!busy);
        stateRoot.setEnabled(!busy);
        family.setEnabled(!busy);
        collection.setEnabled(!busy);
        recordList.setEnabled(!busy);
        status.setText(message);
    }

    private void failLater(long request, @NotNull String title, @NotNull Throwable error) {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (!current(request)) {
                return;
            }
            setBusy(false, title + ": " + detail(error));
            showEmpty(title);
            WorkbenchNotifications.commandFailed(project, title, error);
        });
    }

    private boolean current(long request) {
        return !disposed && !project.isDisposed() && generation.get() == request;
    }

    private static @NotNull String detail(@NotNull Throwable error) {
        String message = error.getMessage();
        return message == null || message.isBlank()
                ? error.getClass().getSimpleName()
                : message;
    }

    private static @NotNull String html(@NotNull String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }

    @Override
    public void dispose() {
        disposed = true;
        generation.incrementAndGet();
    }

    private static final class RecordRenderer extends DefaultListCellRenderer {
        @Override
        public Component getListCellRendererComponent(
                JList<?> list,
                Object value,
                int index,
                boolean isSelected,
                boolean cellHasFocus
        ) {
            super.getListCellRendererComponent(
                    list, value, index, isSelected, cellHasFocus
            );
            if (value instanceof FeatureRecordCatalog.Record record) {
                setText(
                        "<html><b>" + html(record.family()) + "</b> · "
                                + html(record.collection()) + " · "
                                + html(record.recordState())
                                + "<br>" + html(shortId(record.recordId()))
                                + " · " + record.operationCount() + " operation(s) · "
                                + html(record.verificationState()) + "</html>"
                );
                setToolTipText(record.recordId() + "\n" + record.workspaceUri());
                setBorder(JBUI.Borders.empty(5, 6));
            }
            return this;
        }

        private static @NotNull String shortId(@NotNull String value) {
            int marker = value.lastIndexOf(':');
            if (marker < 0 || value.length() - marker <= 15) {
                return value;
            }
            return value.substring(0, marker + 1) + value.substring(marker + 1, marker + 13)
                    + "…";
        }
    }
}
