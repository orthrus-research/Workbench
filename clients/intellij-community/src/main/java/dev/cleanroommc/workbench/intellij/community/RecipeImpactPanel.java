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

import javax.swing.DefaultListCellRenderer;
import javax.swing.DefaultListModel;
import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JComponent;
import javax.swing.JList;
import javax.swing.JPanel;
import javax.swing.JSpinner;
import javax.swing.ListSelectionModel;
import javax.swing.SpinnerNumberModel;
import javax.swing.Timer;
import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.DefaultTreeModel;
import javax.swing.event.TreeExpansionEvent;
import javax.swing.event.TreeWillExpandListener;
import java.awt.BorderLayout;
import java.awt.Component;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.util.concurrent.atomic.AtomicLong;

/** Native explicit-graph recipe search, exact selection, and bounded impact tree. */
final class RecipeImpactPanel extends JPanel implements Disposable {
    static final String GRAPH_PROPERTY = "workbench.atlasRecipeImpactGraph";
    private static final int SEARCH_LIMIT = 100;

    private final Project project;
    private final JBTextField graphPath = new JBTextField();
    private final JBTextField query = new JBTextField();
    private final JButton search = new JButton("Search Exact Recipes");
    private final DefaultListModel<AtlasRecipeSearch.Result> resultModel =
            new DefaultListModel<>();
    private final JBList<AtlasRecipeSearch.Result> results = new JBList<>(resultModel);
    private final JBTextField selectionId = new JBTextField();
    private final JSpinner maxDepth = new JSpinner(new SpinnerNumberModel(4, 1, 12, 1));
    private final JSpinner maxNodes = new JSpinner(
            new SpinnerNumberModel(500, 10, 2_000, 10)
    );
    private final JButton analyze = new JButton("Analyze Candidate Exposure");
    private final JCheckBox completeExploration = new JCheckBox("Explore all captured finite dependencies");
    private final Tree tree = new Tree(new DefaultTreeModel(new DefaultMutableTreeNode(
            "Choose an explicit verified graph and exact observed gt-recipe"
    )));
    private final JButton completeJson = new JButton("Show Complete Atlas JSON");
    private final JBLabel status = new JBLabel(
            "Ready. Search and impact each verify and open the graph independently."
    );
    private final AtomicLong generation = new AtomicLong();

    private AtlasRecipeSearch currentSearch;
    private AtlasRecipeClient.GraphPath currentSearchGraph;
    private AtlasRecipeImpact currentImpact;
    private AtlasCompleteRecipeImpact currentCompleteImpact;
    private Timer elapsedTimer;
    private long progressStartedMillis;
    private String progressLabel = "";
    private boolean busy;
    private boolean disposed;

    RecipeImpactPanel(@NotNull Project project) {
        super(new BorderLayout(0, JBUI.scale(6)));
        this.project = project;
        setBorder(JBUI.Borders.empty(6));
        String configured = PropertiesComponent.getInstance(project).getValue(GRAPH_PROPERTY);
        graphPath.setText(configured == null ? "" : configured);
        graphPath.getEmptyText().setText("Absolute categorical graph directory");
        query.getEmptyText().setText("Recipe map, semantic key, item, or fluid");
        selectionId.getEmptyText().setText(
                "Exact workbench-atlas-node-v2:gt-recipe:… identity"
        );
        add(controls(), BorderLayout.NORTH);
        add(content(), BorderLayout.CENTER);
        add(status, BorderLayout.SOUTH);
        configureInteractions();
    }

    private @NotNull JComponent controls() {
        JPanel panel = new JPanel(new GridBagLayout());
        GridBagConstraints constraints = new GridBagConstraints();
        constraints.gridx = 0;
        constraints.gridy = 0;
        constraints.gridwidth = 4;
        constraints.weightx = 1;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        constraints.anchor = GridBagConstraints.WEST;
        constraints.insets = new Insets(0, 0, JBUI.scale(6), 0);
        panel.add(new JBLabel(
                "<html><b>Observed recipe dependency exposure.</b> "
                        + "The scenario removes only the exact current recipe; a proposed "
                        + "replacement is not modeled. Search and impact are separate graph "
                        + "verification processes and large graphs can take several minutes. "
                        + "Paste an exact selection ID to skip search.</html>"
        ), constraints);

        constraints.gridwidth = 1;
        constraints.weightx = 0;
        constraints.fill = GridBagConstraints.NONE;
        constraints.gridy = 1;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), JBUI.scale(6));
        panel.add(new JBLabel("Verified graph"), constraints);
        constraints.gridx = 1;
        constraints.gridwidth = 3;
        constraints.weightx = 1;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), 0);
        panel.add(graphPath, constraints);

        constraints.gridx = 0;
        constraints.gridy = 2;
        constraints.gridwidth = 1;
        constraints.weightx = 0;
        constraints.fill = GridBagConstraints.NONE;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), JBUI.scale(6));
        panel.add(new JBLabel("Recipe query"), constraints);
        constraints.gridx = 1;
        constraints.gridwidth = 2;
        constraints.weightx = 1;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        panel.add(query, constraints);
        constraints.gridx = 3;
        constraints.gridwidth = 1;
        constraints.weightx = 0;
        constraints.fill = GridBagConstraints.NONE;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), 0);
        panel.add(search, constraints);

        constraints.gridx = 0;
        constraints.gridy = 3;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), JBUI.scale(6));
        panel.add(new JBLabel("Exact selection ID"), constraints);
        constraints.gridx = 1;
        constraints.gridwidth = 3;
        constraints.weightx = 1;
        constraints.fill = GridBagConstraints.HORIZONTAL;
        constraints.insets = new Insets(0, 0, JBUI.scale(4), 0);
        panel.add(selectionId, constraints);

        constraints.gridx = 0;
        constraints.gridy = 4;
        constraints.gridwidth = 1;
        constraints.weightx = 0;
        constraints.fill = GridBagConstraints.NONE;
        constraints.insets = new Insets(0, 0, 0, JBUI.scale(6));
        panel.add(new JBLabel("Max depth"), constraints);
        constraints.gridx = 1;
        panel.add(maxDepth, constraints);
        constraints.gridx = 2;
        panel.add(new JBLabel("Max nodes"), constraints);
        constraints.gridx = 3;
        constraints.insets = new Insets(0, JBUI.scale(6), 0, 0);
        panel.add(maxNodes, constraints);
        constraints.gridx = 4;
        constraints.insets = new Insets(0, JBUI.scale(10), 0, 0);
        panel.add(analyze, constraints);
        constraints.gridx = 0;
        constraints.gridy = 5;
        constraints.gridwidth = 5;
        constraints.insets = new Insets(JBUI.scale(6), 0, 0, 0);
        completeExploration.setToolTipText("Explore the entire admitted finite structure; matching evidence and viability may remain unknown.");
        panel.add(completeExploration, constraints);
        return panel;
    }

    private @NotNull JComponent content() {
        results.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        results.setCellRenderer(new RecipeRenderer());
        results.setVisibleRowCount(12);
        JBScrollPane resultScroll = new JBScrollPane(results);
        resultScroll.setMinimumSize(new Dimension(JBUI.scale(300), JBUI.scale(320)));
        JPanel picker = new JPanel(new BorderLayout(0, JBUI.scale(4)));
        picker.add(new JBLabel("<html><b>Exact observed gt-recipe results</b></html>"),
                BorderLayout.NORTH);
        picker.add(resultScroll, BorderLayout.CENTER);

        tree.setRootVisible(true);
        tree.setShowsRootHandles(true);
        JBScrollPane treeScroll = new JBScrollPane(tree);
        JPanel detail = new JPanel(new BorderLayout(0, JBUI.scale(4)));
        detail.add(new JBLabel("<html><b>Candidate exposure and evidence limits</b></html>"),
                BorderLayout.NORTH);
        detail.add(treeScroll, BorderLayout.CENTER);
        JPanel buttons = new JPanel(new FlowLayout(FlowLayout.LEFT, 0, 0));
        buttons.add(completeJson);
        detail.add(buttons, BorderLayout.SOUTH);

        JBSplitter splitter = new JBSplitter(false, 0.32f);
        splitter.setFirstComponent(picker);
        splitter.setSecondComponent(detail);
        splitter.setHonorComponentsMinimumSize(true);
        return splitter;
    }

    private void configureInteractions() {
        search.addActionListener(event -> startSearch());
        query.addActionListener(event -> startSearch());
        analyze.addActionListener(event -> startImpact());
        completeExploration.addActionListener(event -> updateControls());
        tree.addTreeWillExpandListener(new TreeWillExpandListener() {
            @Override
            public void treeWillExpand(TreeExpansionEvent event) {
                if (event.getPath().getLastPathComponent() instanceof AtlasCompleteRecipeImpactTree.LazyNode node) {
                    node.populate();
                    ((DefaultTreeModel) tree.getModel()).nodeStructureChanged(node);
                }
            }
            @Override public void treeWillCollapse(TreeExpansionEvent event) { }
        });
        selectionId.addActionListener(event -> startImpact());
        results.addListSelectionListener(event -> {
            if (!event.getValueIsAdjusting()) {
                AtlasRecipeSearch.Result selected = results.getSelectedValue();
                if (selected != null) {
                    selectionId.setText(selected.selectionId());
                }
            }
        });
        completeJson.addActionListener(event -> {
            String json = currentCompleteImpact != null ? currentCompleteImpact.rawJson()
                    : currentImpact != null ? currentImpact.rawJson() : null;
            if (json != null) {
                new AtlasRecipeImpactJsonDialog(project, json).show();
            }
        });
        completeJson.setEnabled(false);
        updateControls();
    }

    private void startSearch() {
        if (busy || disposed || project.isDisposed()) {
            return;
        }
        final Prepared prepared;
        final String exactQuery = query.getText();
        try {
            prepared = prepare();
            AtlasRecipeClient.searchArguments(prepared.graph(), exactQuery, SEARCH_LIMIT);
        } catch (RuntimeException error) {
            showPreparationFailure("Atlas recipe search could not start", error);
            return;
        }
        persistGraph();
        long request = generation.incrementAndGet();
        currentSearch = null;
        currentSearchGraph = null;
        currentImpact = null;
        currentCompleteImpact = null;
        resultModel.clear();
        completeJson.setEnabled(false);
        showEmpty("Verifying the explicit graph before recipe search…");
        beginProgress(request, "Recipe search",
                "verifying and opening the graph (first independent process)");
        new Task.Backgroundable(project, "Verifying graph and searching Atlas recipes", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                indicator.setText(
                        "Verifying the categorical graph; this can take several minutes"
                );
                try {
                    String output = CommandProcess.capture(
                            prepared.launch(),
                            AtlasRecipeClient.searchArguments(
                                    prepared.graph(), exactQuery, SEARCH_LIMIT
                            ),
                            AtlasRecipeContract.MAX_OUTPUT_BYTES,
                            1_200,
                            project.getBasePath()
                    );
                    AtlasRecipeSearch parsed = AtlasRecipeSearch.parse(
                            output, exactQuery, SEARCH_LIMIT
                    );
                    AtlasRecipeClient.validateSearch(prepared.graph(), parsed);
                    ApplicationManager.getApplication().invokeLater(
                            () -> acceptSearch(request, prepared.graph(), parsed)
                    );
                } catch (Exception error) {
                    failLater(request, "Atlas recipe search failed", error);
                }
            }
        }.queue();
    }

    private void startImpact() {
        if (busy || disposed || project.isDisposed()) {
            return;
        }
        final Prepared prepared;
        final String exactSelection = selectionId.getText().trim();
        final boolean complete = completeExploration.isSelected();
        final int depth = ((Number) maxDepth.getValue()).intValue();
        final int nodes = ((Number) maxNodes.getValue()).intValue();
        try {
            prepared = prepare();
            if (complete) AtlasRecipeClient.completeImpactArguments(prepared.graph(), exactSelection);
            else AtlasRecipeClient.impactArguments(prepared.graph(), exactSelection, depth, nodes);
        } catch (RuntimeException error) {
            showPreparationFailure("Atlas recipe impact could not start", error);
            return;
        }
        persistGraph();
        AtlasRecipeSearch linkedSearch = null;
        AtlasRecipeSearch.Result linkedSelection = null;
        AtlasRecipeSearch.Result selected = results.getSelectedValue();
        if (currentSearch != null && currentSearchGraph != null && selected != null
                && selected.selectionId().equals(exactSelection)
                && currentSearchGraph.expectedContextRoot().equals(
                        prepared.graph().expectedContextRoot())) {
            linkedSearch = currentSearch;
            linkedSelection = selected;
        }
        AtlasRecipeSearch searchLink = linkedSearch;
        AtlasRecipeSearch.Result selectionLink = linkedSelection;
        long request = generation.incrementAndGet();
        currentImpact = null;
        currentCompleteImpact = null;
        completeJson.setEnabled(false);
        showEmpty("Verifying the explicit graph before candidate analysis…");
        beginProgress(request, "Recipe impact",
                "verifying and opening the graph again (second independent process)");
        new Task.Backgroundable(project, "Verifying graph and analyzing recipe impact", complete) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                indicator.setText(
                        complete ? "Exploring all captured finite dependencies; evidence gaps remain explicit"
                                : "Verifying the graph again and deriving bounded candidate exposure"
                );
                try {
                    if (complete) {
                        String output = CommandProcess.capture(prepared.launch(),
                                AtlasRecipeClient.completeImpactArguments(prepared.graph(), exactSelection),
                                0, 0, project.getBasePath(), () -> indicator.isCanceled() || !current(request));
                        if (indicator.isCanceled()) { cancelCompleteLater(request); return; }
                        if (!current(request)) return;
                        AtlasCompleteRecipeImpact parsed = AtlasCompleteRecipeImpact.parse(output, exactSelection);
                        AtlasRecipeClient.validateCompleteImpact(prepared.graph(), exactSelection, searchLink, selectionLink, parsed);
                        if (indicator.isCanceled()) { cancelCompleteLater(request); return; }
                        if (!current(request)) return;
                        ApplicationManager.getApplication().invokeLater(() -> {
                            if (indicator.isCanceled()) cancelCompleteLater(request);
                            else acceptCompleteImpact(request, parsed);
                        });
                        return;
                    }
                    String output = CommandProcess.capture(
                            prepared.launch(),
                            AtlasRecipeClient.impactArguments(
                                    prepared.graph(), exactSelection, depth, nodes
                            ),
                            AtlasRecipeContract.MAX_OUTPUT_BYTES,
                            1_200,
                            project.getBasePath()
                    );
                    AtlasRecipeImpact parsed = AtlasRecipeImpact.parse(
                            output, exactSelection, depth, nodes
                    );
                    AtlasRecipeClient.validateImpact(
                            prepared.graph(), exactSelection, searchLink, selectionLink, parsed
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> acceptImpact(request, parsed)
                    );
                } catch (Exception error) {
                    if (complete && indicator.isCanceled()) {
                        cancelCompleteLater(request);
                        return;
                    }
                    failLater(request, "Atlas recipe impact failed", error);
                }
            }
        }.queue();
    }

    private void cancelCompleteLater(long request) {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (!current(request)) return;
            endProgress();
            showEmpty("Complete finite analysis cancelled; no completed report was accepted.");
            status.setText("Complete finite analysis cancelled");
            updateControls();
        });
    }

    private @NotNull Prepared prepare() {
        String executable = CoreLocation.discover(project);
        CoreLaunch launch = CoreLaunch.resolve(executable);
        AtlasRecipeClient.GraphPath selected = AtlasRecipeClient.graphPath(
                launch, graphPath.getText()
        );
        return new Prepared(launch, selected);
    }

    private void acceptSearch(
            long request,
            @NotNull AtlasRecipeClient.GraphPath graph,
            @NotNull AtlasRecipeSearch parsed
    ) {
        if (!current(request)) {
            return;
        }
        endProgress();
        currentSearch = parsed;
        currentSearchGraph = graph;
        resultModel.clear();
        parsed.recipes().forEach(resultModel::addElement);
        if (!parsed.recipes().isEmpty()) {
            results.setSelectedIndex(0);
        }
        String suffix = parsed.truncated()
                ? " · source search was truncated at " + SEARCH_LIMIT : "";
        status.setText(
                parsed.recipes().size() + " exact gt-recipe result(s) from verified graph "
                        + parsed.context().graphSetId() + suffix
                        + ". Impact will verify and open the graph again."
        );
        updateControls();
    }

    private void acceptImpact(long request, @NotNull AtlasRecipeImpact parsed) {
        if (!current(request)) {
            return;
        }
        endProgress();
        currentImpact = parsed;
        currentCompleteImpact = null;
        tree.setModel(new DefaultTreeModel(AtlasRecipeImpactTree.build(parsed)));
        tree.expandRow(0);
        for (int row = 1; row < Math.min(tree.getRowCount(), 5); row++) {
            tree.expandRow(row);
        }
        completeJson.setEnabled(true);
        int exposedResources = AtlasRecipeContract.nonnegativeInteger(
                parsed.summary(), "at_risk_resource_candidate_count"
        );
        int exposedRecipes = AtlasRecipeContract.nonnegativeInteger(
                parsed.summary(), "at_risk_recipe_candidate_count"
        );
        status.setText(
                exposedResources + " exposed resource candidate(s) · "
                        + exposedRecipes + " exposed recipe candidate(s)"
                        + " · " + AtlasRecipeImpactTree.statusText(parsed)
                        + " · not gameplay reachability"
        );
        updateControls();
    }

    private void acceptCompleteImpact(long request, @NotNull AtlasCompleteRecipeImpact parsed) {
        if (!current(request)) return;
        endProgress();
        currentImpact = null;
        currentCompleteImpact = parsed;
        tree.setModel(new DefaultTreeModel(AtlasCompleteRecipeImpactTree.build(parsed)));
        tree.expandRow(0);
        status.setText(parsed.statusText());
        updateControls();
    }

    private void beginProgress(
            long request,
            @NotNull String label,
            @NotNull String detail
    ) {
        busy = true;
        updateControls();
        progressStartedMillis = System.currentTimeMillis();
        progressLabel = label + ": " + detail;
        status.setText(progressLabel + " · elapsed 0s · may take several minutes");
        if (elapsedTimer != null) {
            elapsedTimer.stop();
        }
        elapsedTimer = new Timer(1_000, event -> {
            if (!current(request)) {
                ((Timer) event.getSource()).stop();
                return;
            }
            long seconds = Math.max(
                    0, (System.currentTimeMillis() - progressStartedMillis) / 1_000
            );
            status.setText(progressLabel + " · elapsed " + seconds
                    + "s · may take several minutes");
        });
        elapsedTimer.start();
    }

    private void endProgress() {
        if (elapsedTimer != null) {
            elapsedTimer.stop();
            elapsedTimer = null;
        }
        busy = false;
    }

    private void failLater(long request, @NotNull String title, @NotNull Throwable error) {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (!current(request)) {
                return;
            }
            endProgress();
            showEmpty(title);
            status.setText(title + ": " + detail(error));
            updateControls();
            WorkbenchNotifications.commandFailed(project, title, error);
        });
    }

    private void showPreparationFailure(
            @NotNull String title,
            @NotNull RuntimeException error
    ) {
        status.setText(title + ": " + detail(error));
        WorkbenchNotifications.commandFailed(project, title, error);
    }

    private void showEmpty(@NotNull String message) {
        tree.setModel(new DefaultTreeModel(new DefaultMutableTreeNode(message)));
    }

    private void updateControls() {
        graphPath.setEnabled(!busy);
        query.setEnabled(!busy);
        results.setEnabled(!busy);
        selectionId.setEnabled(!busy);
        completeExploration.setEnabled(!busy);
        maxDepth.setEnabled(!busy && !completeExploration.isSelected());
        maxNodes.setEnabled(!busy && !completeExploration.isSelected());
        search.setEnabled(!busy);
        analyze.setEnabled(!busy);
        completeJson.setEnabled(!busy && (currentImpact != null || currentCompleteImpact != null));
    }

    private void persistGraph() {
        String selected = graphPath.getText().trim();
        PropertiesComponent properties = PropertiesComponent.getInstance(project);
        if (selected.isEmpty()) {
            properties.unsetValue(GRAPH_PROPERTY);
        } else {
            properties.setValue(GRAPH_PROPERTY, selected);
        }
    }

    private boolean current(long request) {
        return !disposed && !project.isDisposed() && generation.get() == request;
    }

    private static @NotNull String detail(@NotNull Throwable error) {
        String message = error.getMessage();
        return message == null || message.isBlank()
                ? error.getClass().getSimpleName() : message;
    }

    @Override
    public void dispose() {
        disposed = true;
        generation.incrementAndGet();
        if (elapsedTimer != null) {
            elapsedTimer.stop();
            elapsedTimer = null;
        }
    }

    private record Prepared(
            @NotNull CoreLaunch launch,
            @NotNull AtlasRecipeClient.GraphPath graph
    ) {
    }

    private static final class RecipeRenderer extends DefaultListCellRenderer {
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
            if (value instanceof AtlasRecipeSearch.Result recipe) {
                setText("<html><b>" + html(recipe.semanticKey()) + "</b><br>"
                        + html(shortId(recipe.selectionId())) + "</html>");
                setToolTipText(recipe.selectionId());
                setBorder(JBUI.Borders.empty(5, 6));
            }
            return this;
        }

        private static @NotNull String shortId(@NotNull String value) {
            int marker = value.lastIndexOf(':');
            if (marker < 0 || value.length() - marker <= 24) {
                return value;
            }
            return value.substring(0, marker + 1) + value.substring(marker + 1, marker + 21)
                    + "…";
        }

        private static @NotNull String html(@NotNull String value) {
            return value.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\"", "&quot;");
        }
    }
}
