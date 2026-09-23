package dev.cleanroommc.workbench.intellij.community;

import com.intellij.ide.BrowserUtil;
import com.intellij.ide.util.PropertiesComponent;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.progress.ProcessCanceledException;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import com.intellij.ui.TreeSpeedSearch;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.treeStructure.Tree;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;

import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JPanel;
import javax.swing.KeyStroke;
import javax.swing.SwingUtilities;
import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.DefaultTreeModel;
import java.awt.BorderLayout;
import java.awt.FlowLayout;
import java.awt.event.KeyEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.util.concurrent.atomic.AtomicLong;

/** Native plan → consent → PR Recipe Review journey over the independently installed core. */
final class RecipeReviewPanel extends JPanel implements Disposable {
    static final String LAST_PULL_REQUEST_PROPERTY = "workbench.recipeReview.lastPullRequest";
    private static final int PLAN_TIMEOUT_SECONDS = 10 * 60;
    private static final int REVIEW_TIMEOUT_SECONDS = 30 * 60;

    private final Project project;
    private final Tree tree = new Tree(new DefaultTreeModel(RecipeReviewTree.empty(
            "Choose Review Pull Request to inspect one provider-bound recipe delta"
    )));
    private final JButton reviewPullRequest = new JButton("Review Pull Request…");
    private final JButton applyPlan = new JButton("Fetch Exact Objects and Review");
    private final JButton openSelected = new JButton("Open Source");
    private final JButton compareSelected = new JButton("Compare Recipe Properties");
    private final JButton openPullRequest = new JButton("Open PR in Browser");
    private final JButton showJson = new JButton("Show Core JSON");
    private final JButton setup = new JButton("Set Up or Repair…");
    private final JButton configure = new JButton("Configure CLI…");
    private final JBLabel status = new JBLabel(
            "Ready. The open IntelliJ project is the candidate checkout."
    );
    private final AtomicLong generation = new AtomicLong();

    private RecipeReviewRequest request;
    private CoreLaunch launch;
    private RecipeReviewPlan plan;
    private RecipeReview review;
    private String visibleJson;
    private boolean busy;
    private boolean disposed;

    RecipeReviewPanel(@NotNull Project project) {
        super(new BorderLayout(0, JBUI.scale(6)));
        this.project = project;
        setBorder(JBUI.Borders.empty(6));
        add(header(), BorderLayout.NORTH);
        tree.setRootVisible(true);
        tree.setShowsRootHandles(true);
        TreeSpeedSearch.installOn(tree);
        add(new JBScrollPane(tree), BorderLayout.CENTER);
        add(footer(), BorderLayout.SOUTH);
        configureInteractions();
        updateControls();
    }

    private @NotNull JComponent header() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(6)));
        panel.add(new JBLabel(
                "<html><b>Review one pull request without switching branches.</b> "
                        + "Workbench first observes the provider-recorded base and head. "
                        + "You approve that exact plan before it fetches Workbench-only refs; "
                        + "the installed core owns every decision shown here.</html>"
        ), BorderLayout.NORTH);
        JPanel actions = new JPanel(new FlowLayout(FlowLayout.LEFT, JBUI.scale(6), 0));
        actions.add(reviewPullRequest);
        actions.add(applyPlan);
        actions.add(openPullRequest);
        actions.add(showJson);
        panel.add(actions, BorderLayout.SOUTH);
        return panel;
    }

    private @NotNull JComponent footer() {
        JPanel panel = new JPanel(new BorderLayout(0, JBUI.scale(4)));
        JPanel navigation = new JPanel(new FlowLayout(FlowLayout.LEFT, JBUI.scale(6), 0));
        navigation.add(openSelected);
        navigation.add(compareSelected);
        navigation.add(setup);
        navigation.add(configure);
        panel.add(navigation, BorderLayout.NORTH);
        panel.add(status, BorderLayout.SOUTH);
        return panel;
    }

    private void configureInteractions() {
        reviewPullRequest.addActionListener(event -> promptForPullRequest());
        applyPlan.addActionListener(event -> confirmAndApply());
        openPullRequest.addActionListener(event -> {
            String url = review != null
                    ? review.scope().pullRequestUrl()
                    : plan == null ? null : plan.pullRequestUrl();
            if (url != null) {
                BrowserUtil.browse(url);
            }
        });
        showJson.addActionListener(event -> {
            if (visibleJson != null) {
                new ProductSpineDetailDialog(
                        project,
                        review == null
                                ? "Workbench · PR Review Plan JSON"
                                : "Workbench · Recipe Review V2 JSON",
                        visibleJson
                ).show();
            }
        });
        openSelected.addActionListener(event -> openSelectedSource());
        compareSelected.addActionListener(event -> compareSelectedModification());
        setup.addActionListener(event -> OpenWorkbenchSetupAction.open(project));
        configure.addActionListener(event -> ConfigureInstalledCoreAction.configure(project));
        tree.addTreeSelectionListener(event -> updateControls());
        tree.addMouseListener(new MouseAdapter() {
            @Override
            public void mouseClicked(MouseEvent event) {
                if (event.getClickCount() == 2 && SwingUtilities.isLeftMouseButton(event)) {
                    activateSelectedTarget();
                }
            }
        });
        tree.registerKeyboardAction(
                event -> activateSelectedTarget(),
                KeyStroke.getKeyStroke(KeyEvent.VK_ENTER, 0),
                JComponent.WHEN_FOCUSED
        );
    }

    void promptForPullRequest() {
        if (busy || disposed || project.isDisposed()
                || !WorkbenchProjectTrust.require(project, "review pull-request recipes")) {
            return;
        }
        String previous = PropertiesComponent.getInstance(project).getValue(
                LAST_PULL_REQUEST_PROPERTY, ""
        );
        String value = Messages.showInputDialog(
                project,
                "Enter the GitHub pull request number. The open project is used as the "
                        + "candidate checkout. Workbench will observe a read-only provider plan first.",
                "Review Pull Request Recipes",
                Messages.getQuestionIcon(),
                previous,
                null
        );
        if (value == null) {
            return;
        }
        final int pullRequest;
        try {
            pullRequest = RecipeReviewRequest.parsePullRequest(value);
        } catch (IllegalArgumentException error) {
            status.setText(error.getMessage());
            return;
        }
        PropertiesComponent.getInstance(project).setValue(
                LAST_PULL_REQUEST_PROPERTY, Integer.toString(pullRequest)
        );
        startPlan(new RecipeReviewRequest(pullRequest, project.getBasePath()));
    }

    private void startPlan(@NotNull RecipeReviewRequest selectedRequest) {
        final CoreLaunch selectedLaunch;
        try {
            selectedLaunch = CoreLaunch.resolve(CoreLocation.discover(project));
            selectedRequest.planArguments(selectedLaunch);
        } catch (RuntimeException error) {
            failPreparation("PR review could not start", error, selectedRequest);
            return;
        }
        long operation = generation.incrementAndGet();
        request = selectedRequest;
        launch = selectedLaunch;
        plan = null;
        review = null;
        visibleJson = null;
        begin(
                "Observing PR #" + selectedRequest.pullRequest()
                        + " and building an exact provider plan…",
                true
        );
        new Task.Backgroundable(project, "Planning Workbench PR Recipe Review", true) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                indicator.setText("Observing provider-recorded base and head identities");
                indicator.setText2("No Git refs are changed during this phase");
                try {
                    String output = CommandProcess.capture(
                            selectedLaunch,
                            selectedRequest.planArguments(selectedLaunch),
                            RecipeReviewPlan.MAX_OUTPUT_BYTES,
                            PLAN_TIMEOUT_SECONDS,
                            project.getBasePath(),
                            indicator::isCanceled
                    );
                    RecipeReviewPlan parsed = RecipeReviewPlan.parse(
                            output, selectedRequest.pullRequest()
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> acceptPlan(operation, selectedRequest, selectedLaunch, parsed)
                    );
                } catch (ProcessCanceledException error) {
                    throw error;
                } catch (Exception error) {
                    failLater(operation, "PR review plan failed", error, selectedRequest);
                }
            }

            @Override
            public void onCancel() {
                cancelLater(operation, "PR review planning cancelled; no refs were changed.");
            }
        }.queue();
    }

    private void confirmAndApply() {
        RecipeReviewPlan selectedPlan = plan;
        RecipeReviewRequest selectedRequest = request;
        CoreLaunch selectedLaunch = launch;
        if (busy || selectedPlan == null || selectedRequest == null || selectedLaunch == null) {
            return;
        }
        StringBuilder effects = new StringBuilder(
                "Apply the exact provider plan shown in this tool window?\n\n"
        );
        selectedPlan.effects().forEach(effect -> effects.append("• ").append(effect).append('\n'));
        effects.append("\nPlan: ").append(selectedPlan.planId())
                .append("\n\nAfter preparation, the installed core will review the recorded base → head recipe delta.");
        int selected = Messages.showYesNoDialog(
                project,
                effects.toString(),
                "Fetch Exact PR Objects and Review Recipes",
                "Fetch and Review",
                "Cancel",
                Messages.getQuestionIcon()
        );
        if (selected == Messages.YES) {
            startApply(selectedRequest, selectedLaunch, selectedPlan);
        }
    }

    private void startApply(
            @NotNull RecipeReviewRequest selectedRequest,
            @NotNull CoreLaunch selectedLaunch,
            @NotNull RecipeReviewPlan selectedPlan
    ) {
        long operation = generation.incrementAndGet();
        begin(
                "Preparing exact refs and reviewing PR #" + selectedRequest.pullRequest() + "…",
                false
        );
        // Applying the consented plan can update Workbench-owned refs and its
        // retained receipt. Let the core finish that bounded transaction once
        // it starts instead of killing it between those custody steps.
        new Task.Backgroundable(project, "Reviewing Workbench PR Recipes", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                indicator.setText("Revalidating the consented provider plan and fetching exact objects");
                indicator.setText2("Then comparing the provider-recorded base → head recipe delta");
                try {
                    String output = CommandProcess.capture(
                            selectedLaunch,
                            selectedRequest.applyArguments(
                                    selectedLaunch, selectedPlan.planId()
                            ),
                            RecipeReview.MAX_OUTPUT_BYTES,
                            REVIEW_TIMEOUT_SECONDS,
                            project.getBasePath()
                    );
                    RecipeReview parsed = RecipeReview.parse(output, selectedPlan);
                    ApplicationManager.getApplication().invokeLater(
                            () -> acceptReview(operation, parsed)
                    );
                } catch (ProcessCanceledException error) {
                    throw error;
                } catch (Exception error) {
                    failLater(operation, "PR Recipe Review failed", error, selectedRequest);
                }
            }

        }.queue();
    }

    private void acceptPlan(
            long operation,
            @NotNull RecipeReviewRequest selectedRequest,
            @NotNull CoreLaunch selectedLaunch,
            @NotNull RecipeReviewPlan selectedPlan
    ) {
        if (!current(operation)) {
            return;
        }
        busy = false;
        request = selectedRequest;
        launch = selectedLaunch;
        plan = selectedPlan;
        review = null;
        visibleJson = selectedPlan.rawJson();
        tree.setModel(new DefaultTreeModel(RecipeReviewTree.plan(selectedPlan)));
        expandTopRows();
        status.setText(
                "Plan ready. Review the exact provider identities and effects, then consent "
                        + "to fetch and analyze. No refs have changed."
        );
        updateControls();
    }

    private void acceptReview(long operation, @NotNull RecipeReview parsed) {
        if (!current(operation)) {
            return;
        }
        busy = false;
        review = parsed;
        visibleJson = parsed.rawJson();
        tree.setModel(new DefaultTreeModel(RecipeReviewTree.review(parsed)));
        expandTopRows();
        status.setText(
                "Core decision: " + parsed.summary().status().toUpperCase()
                        + " · double-click a file to open it or a modified recipe to compare properties."
        );
        updateControls();
    }

    private void begin(@NotNull String message, boolean cancellable) {
        busy = true;
        visibleJson = null;
        tree.setModel(new DefaultTreeModel(RecipeReviewTree.empty(message)));
        status.setText(
                message + (cancellable
                        ? " Use IntelliJ's background-task control to cancel."
                        : " The consented core transaction will finish before this view updates.")
        );
        updateControls();
    }

    private void failPreparation(
            @NotNull String title,
            @NotNull Throwable error,
            @NotNull RecipeReviewRequest selectedRequest
    ) {
        request = selectedRequest;
        busy = false;
        showFailure(title, error);
    }

    private void failLater(
            long operation,
            @NotNull String title,
            @NotNull Throwable error,
            @NotNull RecipeReviewRequest selectedRequest
    ) {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (!current(operation)) {
                return;
            }
            request = selectedRequest;
            busy = false;
            plan = null;
            review = null;
            visibleJson = null;
            showFailure(title, error);
        });
    }

    private void showFailure(@NotNull String title, @NotNull Throwable error) {
        String detail = detail(error);
        DefaultMutableTreeNode root = RecipeReviewTree.empty(title);
        root.add(RecipeReviewTree.empty(detail));
        root.add(RecipeReviewTree.empty(
                "Repair choices are available below: Set Up or Repair, Configure CLI, or retry."
        ));
        tree.setModel(new DefaultTreeModel(root));
        status.setText(title + ": " + detail);
        updateControls();
        RecipeReviewRequest retryRequest = request;
        WorkbenchNotifications.recipeReviewFailed(
                project,
                error,
                () -> {
                    if (retryRequest != null && !busy && !disposed) {
                        startPlan(retryRequest);
                    }
                }
        );
    }

    private void cancelLater(long operation, @NotNull String message) {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (!current(operation)) {
                return;
            }
            busy = false;
            plan = null;
            review = null;
            visibleJson = null;
            tree.setModel(new DefaultTreeModel(RecipeReviewTree.empty(message)));
            status.setText(message);
            updateControls();
        });
    }

    private void activateSelectedTarget() {
        RecipeReviewTree.Target target = selectedTarget();
        if (target instanceof RecipeReviewTree.ModificationTarget modification) {
            compare(modification.modification());
        } else if (target instanceof RecipeReviewTree.SourceTarget source) {
            open(source.source());
        }
    }

    private void openSelectedSource() {
        RecipeReviewTree.Target target = selectedTarget();
        if (target instanceof RecipeReviewTree.SourceTarget source) {
            open(source.source());
        } else if (target instanceof RecipeReviewTree.ModificationTarget modification) {
            open(modification.modification().afterSource());
        }
    }

    private void compareSelectedModification() {
        RecipeReviewTree.Target target = selectedTarget();
        if (target instanceof RecipeReviewTree.ModificationTarget modification) {
            compare(modification.modification());
        }
    }

    private void compare(@NotNull RecipeReview.Modification modification) {
        try {
            RecipeReviewDiffOpener.open(project, modification);
        } catch (Exception error) {
            status.setText("Recipe property diff failed: " + detail(error));
        }
    }

    private void open(@NotNull RecipeReview.Source source) {
        try {
            RecipeReviewNavigation.open(project, source);
        } catch (Exception error) {
            status.setText("Source navigation failed: " + detail(error));
        }
    }

    private RecipeReviewTree.Target selectedTarget() {
        return RecipeReviewTree.target(tree.getLastSelectedPathComponent());
    }

    private void updateControls() {
        RecipeReviewTree.Target selected = selectedTarget();
        reviewPullRequest.setEnabled(!busy);
        applyPlan.setEnabled(!busy && plan != null && review == null);
        openPullRequest.setEnabled(!busy && (plan != null || review != null));
        showJson.setEnabled(!busy && visibleJson != null);
        openSelected.setEnabled(!busy && (selected instanceof RecipeReviewTree.SourceTarget
                || selected instanceof RecipeReviewTree.ModificationTarget));
        compareSelected.setEnabled(
                !busy && selected instanceof RecipeReviewTree.ModificationTarget
        );
        setup.setEnabled(!busy);
        configure.setEnabled(!busy);
    }

    private void expandTopRows() {
        tree.expandRow(0);
        for (int row = 1; row < Math.min(tree.getRowCount(), 8); row++) {
            tree.expandRow(row);
        }
    }

    private boolean current(long operation) {
        return !disposed && !project.isDisposed() && generation.get() == operation;
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
    }
}
