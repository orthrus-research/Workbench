package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.vfs.VirtualFile;
import com.intellij.openapi.vfs.VirtualFileManager;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextArea;
import com.intellij.util.ui.JBUI;
import org.jetbrains.annotations.NotNull;

import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import java.awt.BorderLayout;
import java.awt.Font;
import java.awt.FlowLayout;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/** Read-only Community Home V2 rendered solely from public Shell owner projections. */
final class WorkspaceHomePanel extends JPanel implements Disposable {
    private static final Gson GSON = new Gson();

    private final Project project;
    private final JButton configureCore = new JButton("Choose Workbench");
    private final JButton installHelp = new JButton("Installation Help");
    private final JButton setup = new JButton("Set Up or Repair");
    private final JButton qualifyProject = new JButton("Qualify This Pack");
    private final JButton reviewRecipes = new JButton("Review Recipe Changes");
    private final JButton refresh = new JButton("Refresh Home");
    private final JButton resumeSession = new JButton("Resume Session");
    private final JButton closeSession = new JButton("Close Session");
    private final JButton previewRecovery = new JButton("Preview Recovery");
    private final JButton applyRecovery = new JButton("Apply Recovery");
    private final JPanel rows = new JPanel();
    private final JBLabel state = new JBLabel("Loading workbench open …");
    private final AtomicLong generation = new AtomicLong();
    private LoadedHomeV2 loadedHome;
    private boolean disposed;

    WorkspaceHomePanel(@NotNull Project project) {
        super(new BorderLayout(0, JBUI.scale(6)));
        this.project = project;
        setBorder(JBUI.Borders.empty(8));
        rows.setLayout(new BoxLayout(rows, BoxLayout.Y_AXIS));
        JPanel controls = new JPanel(new BorderLayout());
        controls.add(new JBLabel("Workbench Home"), BorderLayout.WEST);
        JPanel actions = new JPanel(new FlowLayout(FlowLayout.RIGHT, JBUI.scale(4), 0));
        actions.add(installHelp);
        actions.add(configureCore);
        actions.add(setup);
        actions.add(qualifyProject);
        actions.add(reviewRecipes);
        actions.add(resumeSession);
        actions.add(closeSession);
        actions.add(previewRecovery);
        actions.add(applyRecovery);
        actions.add(refresh);
        controls.add(actions, BorderLayout.EAST);
        add(controls, BorderLayout.NORTH);
        add(new JBScrollPane(rows), BorderLayout.CENTER);
        add(state, BorderLayout.SOUTH);
        refresh.addActionListener(event -> refresh());
        installHelp.addActionListener(event -> OpenInstallationHelpAction.open(project));
        configureCore.addActionListener(event -> ConfigureInstalledCoreAction.configure(project));
        setup.addActionListener(event -> OpenWorkbenchSetupAction.open(project));
        qualifyProject.addActionListener(event -> OpenProjectQualificationAction.qualify(project));
        reviewRecipes.addActionListener(event -> OpenRecipeReviewAction.open(project));
        resumeSession.addActionListener(event -> runSessionOperation(SessionOperation.RESUME));
        closeSession.addActionListener(event -> runSessionOperation(SessionOperation.CLOSE));
        previewRecovery.addActionListener(
                event -> runSessionOperation(SessionOperation.RECOVERY_PREVIEW)
        );
        applyRecovery.addActionListener(
                event -> runSessionOperation(SessionOperation.RECOVERY_APPLY)
        );
        setSessionButtons(false, false);
        qualifyProject.setEnabled(WorkbenchProjectTrust.isTrusted(project)
                && project.getBasePath() != null && !project.getBasePath().isBlank());
        reviewRecipes.setEnabled(false);
        refresh();
    }

    private void refresh() {
        if (disposed || project.isDisposed()) {
            return;
        }
        if (!WorkbenchProjectTrust.isTrusted(project)) {
            showFailure(new IllegalStateException(
                    "Trust this IntelliJ project before Workbench can inspect it."
            ));
            setup.setEnabled(false);
            qualifyProject.setEnabled(false);
            reviewRecipes.setEnabled(false);
            return;
        }
        setup.setEnabled(true);
        String workspace = project.getBasePath();
        if (workspace == null || workspace.isBlank()) {
            qualifyProject.setEnabled(false);
            showFailure(new IllegalStateException("the IntelliJ project has no workspace root"));
            return;
        }
        qualifyProject.setEnabled(true);
        long request = generation.incrementAndGet();
        refresh.setEnabled(false);
        setSessionButtons(false, false);
        state.setText("Running workbench open for the current workspace …");
        new Task.Backgroundable(project, "Loading Workbench Home", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                try {
                    CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
                    CoreStatus coreStatus = CoreStatus.probe(launch, workspace);
                    if (!coreStatus.setup().ready()) {
                        ApplicationManager.getApplication().invokeLater(
                                () -> setupRequired(request, coreStatus)
                        );
                        return;
                    }
                    WorkspaceHomeV2 home = WorkspaceHomeV2Client.load(launch, workspace, workspace);
                    WorkSessionV2.Status status = null;
                    WorkSessionV2.Timeline timeline = null;
                    WorkSessionV2.RecoveryPreview recovery = null;
                    if (home.session().sessionId() != null
                            && "available".equals(home.session().state())) {
                        String sessionId = home.session().sessionId();
                        status = WorkSessionV2Client.status(launch, sessionId, workspace);
                        timeline = WorkSessionV2Client.timeline(
                                launch, sessionId,
                                Math.max(-1, status.latestSequence() - 32), 32, workspace
                        );
                        recovery = WorkSessionV2Client.recovery(launch, sessionId, workspace);
                    }
                    LoadedHomeV2 loaded = new LoadedHomeV2(home, status, timeline, recovery);
                    ApplicationManager.getApplication().invokeLater(
                            () -> accept(request, loaded)
                    );
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(
                            () -> fail(request, error)
                    );
                }
            }
        }.queue();
    }

    private void accept(long request, @NotNull LoadedHomeV2 loaded) {
        if (!current(request)) {
            return;
        }
        rows.removeAll();
        WorkspaceHomeV2 home = loaded.home();
        loadedHome = loaded;
        rows.add(heading(home.workspace().displayName() + " — " + home.workspace().kind()));
        for (WorkspaceHomeV2Regions.Region region : WorkspaceHomeV2Regions.compose(
                home, loaded.status(), loaded.timeline(), loaded.recovery())) {
            rows.add(section(region.definition().label()));
            for (WorkspaceHomeV2Regions.Row projected : region.rows()) {
                rows.add(projected.ownerValue() == null
                        ? line(projected.label() + ": " + projected.value())
                        : selectable(projected));
                if (projected.ownerValue() instanceof WorkspaceHomeV2.Job job) {
                    rows.add(wrapped(job.purpose()));
                    if (job.available()) {
                        rows.add(monospace("Exact argv: " + GSON.toJson(job.argv())));
                    } else {
                        rows.add(wrapped("Blocked by: " + String.join(", ", job.blockers())));
                        rows.add(wrapped("Reason: " + job.unavailableReason()));
                    }
                    if (job.capabilityId() != null) {
                        rows.add(monospace("Capability: " + job.capabilityId()
                                + " (" + job.capabilityKey() + ")"));
                    }
                }
            }
        }
        rows.add(Box.createVerticalGlue());
        rows.revalidate();
        rows.repaint();
        refresh.setEnabled(true);
        reviewRecipes.setEnabled(true);
        boolean hasSession = home.session().sessionId() != null && loaded.status() != null;
        setSessionButtons(hasSession,
                hasSession && loaded.recovery() != null && loaded.recovery().required());
        state.setText("Read-only Home V2 loaded. Select owner, event, recovery, or job rows for exact details.");
    }

    private void setupRequired(long request, @NotNull CoreStatus coreStatus) {
        if (!current(request)) {
            return;
        }
        loadedHome = null;
        rows.removeAll();
        rows.add(heading("Workbench setup required"));
        rows.add(wrapped("Workbench " + coreStatus.currentVersion() + " is installed, but setup is "
                + coreStatus.setup().state() + "."));
        if (!coreStatus.setup().managedInstallsAvailable().isEmpty()) {
            rows.add(wrapped("Workbench can install after confirmation: "
                    + String.join(", ", coreStatus.setup().managedInstallsAvailable())));
        }
        if (!coreStatus.setup().blockers().isEmpty()) {
            rows.add(wrapped("Needs attention: "
                    + String.join(", ", coreStatus.setup().blockers())));
        }
        rows.add(wrapped("Set Up or Repair to choose a workspace, inspect dependencies, and review "
                + "the exact plan before anything is installed."));
        rows.revalidate();
        rows.repaint();
        refresh.setEnabled(true);
        reviewRecipes.setEnabled(false);
        setSessionButtons(false, false);
        state.setText("Setup is required before Workbench Home is opened.");
    }

    private void fail(long request, @NotNull Exception error) {
        if (!current(request)) {
            return;
        }
        showFailure(error);
    }

    private void showFailure(@NotNull Exception error) {
        loadedHome = null;
        rows.removeAll();
        rows.add(heading("Workbench Home unavailable"));
        String message = error.getMessage();
        rows.add(wrapped(message == null || message.isBlank()
                ? error.getClass().getSimpleName()
                : message));
        rows.add(wrapped("If the Workbench CLI is missing, use Installation Help. If it is "
                + "installed outside PATH, choose its exact executable, then Refresh Home."));
        rows.revalidate();
        rows.repaint();
        refresh.setEnabled(true);
        reviewRecipes.setEnabled(false);
        setSessionButtons(false, false);
        state.setText("No Home actions were inferred by the IntelliJ client.");
    }

    private void setSessionButtons(boolean enabled, boolean recoveryApplyEnabled) {
        resumeSession.setEnabled(enabled);
        closeSession.setEnabled(enabled);
        previewRecovery.setEnabled(enabled);
        applyRecovery.setEnabled(recoveryApplyEnabled);
    }

    private void runSessionOperation(@NotNull SessionOperation operation) {
        LoadedHomeV2 selected = loadedHome;
        if (disposed || project.isDisposed() || selected == null
                || selected.home().session().sessionId() == null) {
            return;
        }
        if (operation == SessionOperation.RECOVERY_APPLY
                && (selected.recovery() == null || !selected.recovery().required())) {
            Messages.showErrorDialog(
                    project,
                    "Workbench reports that recovery is not required; apply remains unavailable.",
                    "Workbench Recovery"
            );
            return;
        }
        if (operation == SessionOperation.CLOSE && Messages.showYesNoDialog(
                project,
                "Close this Work Session navigation? Owner work and retained evidence are unchanged.",
                "Close Work Session Navigation",
                "Close Navigation", "Cancel", null
        ) != Messages.YES) {
            return;
        }
        if (operation == SessionOperation.RECOVERY_APPLY && Messages.showYesNoDialog(
                project,
                "Apply the exact core-owned recovery preview? Workbench will revalidate current owner custody.",
                "Apply Workbench Recovery",
                "Apply Recovery", "Cancel", null
        ) != Messages.YES) {
            return;
        }
        String workspace = project.getBasePath();
        if (workspace == null || workspace.isBlank()) {
            Messages.showErrorDialog(project, "The IntelliJ project has no workspace root.",
                    "Workbench Session");
            return;
        }
        String sessionId = selected.home().session().sessionId();
        long request = generation.incrementAndGet();
        refresh.setEnabled(false);
        setSessionButtons(false, false);
        state.setText(operation.label + " through the public Workbench session port …");
        new Task.Backgroundable(project, operation.label, false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                try {
                    CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
                    Object result = switch (operation) {
                        case RESUME -> WorkSessionV2Client.resume(
                                launch, sessionId, workspace, workspace
                        );
                        case CLOSE -> WorkSessionV2Client.close(launch, sessionId, workspace);
                        case RECOVERY_PREVIEW -> WorkSessionV2Client.recovery(
                                launch, sessionId, workspace
                        );
                        case RECOVERY_APPLY -> WorkSessionV2Client.recoveryApply(
                                launch, sessionId, workspace
                        );
                    };
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (!current(request)) return;
                        new ProductSpineDetailDialog(
                                project, "Workbench · " + operation.label, GSON.toJson(result)
                        ).show();
                        if (operation == SessionOperation.RECOVERY_PREVIEW) {
                            refresh.setEnabled(true);
                            setSessionButtons(true, selected.recovery() != null
                                    && selected.recovery().required());
                            state.setText("Core-owned recovery preview inspected; no state was changed.");
                        } else {
                            refresh();
                        }
                    });
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (!current(request)) return;
                        refresh.setEnabled(true);
                        setSessionButtons(true, selected.recovery() != null
                                && selected.recovery().required());
                        state.setText(operation.label + " failed closed.");
                        Messages.showErrorDialog(
                                project,
                                error.getMessage() == null ? error.getClass().getSimpleName()
                                        : error.getMessage(),
                                "Workbench Session"
                        );
                    });
                }
            }
        }.queue();
    }

    private boolean current(long request) {
        return !disposed && !project.isDisposed() && generation.get() == request;
    }

    private static @NotNull JComponent heading(@NotNull String text) {
        JBLabel label = new JBLabel(" " + text);
        label.setFont(label.getFont().deriveFont(Font.BOLD, label.getFont().getSize2D() + 2));
        label.setAlignmentX(LEFT_ALIGNMENT);
        return label;
    }

    private static @NotNull JComponent section(@NotNull String text) {
        JBLabel label = new JBLabel(" " + text);
        label.setFont(label.getFont().deriveFont(Font.BOLD));
        label.setBorder(JBUI.Borders.emptyTop(10));
        label.setAlignmentX(LEFT_ALIGNMENT);
        return label;
    }

    private static @NotNull JComponent line(@NotNull String text) {
        JBLabel label = new JBLabel(" " + text);
        label.setAlignmentX(LEFT_ALIGNMENT);
        return label;
    }

    private @NotNull JComponent selectable(@NotNull WorkspaceHomeV2Regions.Row row) {
        JButton button = new JButton(row.label() + ": " + row.value());
        button.setToolTipText("Open this exact owner-returned value");
        button.setAlignmentX(LEFT_ALIGNMENT);
        button.addActionListener(event -> inspect(row));
        return button;
    }

    private void inspect(@NotNull WorkspaceHomeV2Regions.Row row) {
        Object value = row.ownerValue();
        if (value instanceof WorkSessionV2.OwnerReference reference) {
            if ("workbench-live-console-session-v1".equals(reference.recordKind())) {
                inspectLiveConsoleOwner(reference);
                return;
            }
            VirtualFile file = VirtualFileManager.getInstance()
                    .refreshAndFindFileByUrl(reference.uri());
            if (file != null && !file.isDirectory()) {
                FileEditorManager.getInstance(project).openFile(file, true);
                return;
            }
        }
        new ProductSpineDetailDialog(
                project,
                "Workbench · " + row.label(),
                GSON.toJson(value)
        ).show();
    }

    private void inspectLiveConsoleOwner(@NotNull WorkSessionV2.OwnerReference reference) {
        LoadedHomeV2 selected = loadedHome;
        String workspace = project.getBasePath();
        if (selected == null || selected.status() == null || workspace == null
                || workspace.isBlank()) {
            Messages.showErrorDialog(project,
                    "Workspace Home V2 has no exact open Work Session identity.",
                    "Workbench Owner Artifact");
            return;
        }
        String sessionId = selected.status().sessionId();
        long request = generation.incrementAndGet();
        refresh.setEnabled(false);
        state.setText("Loading owner-sealed live-console event ranges through Workbench …");
        new Task.Backgroundable(project, "Loading Workbench Owner Artifact Events", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                try {
                    CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
                    List<WorkSessionV2Client.OwnerArtifactEvent> events = new ArrayList<>();
                    int afterSequence = -1;
                    for (;;) {
                        WorkSessionV2Client.OwnerArtifactEvents page =
                                WorkSessionV2Client.artifactEvents(
                                        launch, sessionId, reference,
                                        afterSequence, 1024, workspace
                                );
                        events.addAll(page.events());
                        if (!page.hasMore()) break;
                        ProductSpineJson.require(
                                page.nextAfterSequence() > afterSequence
                                        && events.size() <= 100_000,
                                "Work Session owner artifact pagination did not advance"
                        );
                        afterSequence = page.nextAfterSequence();
                    }
                    ApplicationManager.getApplication().invokeLater(() -> selectArtifactEvent(
                            request, launch, workspace, sessionId, reference,
                            List.copyOf(events), selected
                    ));
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(
                            () -> failArtifactInspection(request, selected, error)
                    );
                }
            }
        }.queue();
    }

    private void selectArtifactEvent(
            long request,
            @NotNull CoreLaunch launch,
            @NotNull String workspace,
            @NotNull String sessionId,
            @NotNull WorkSessionV2.OwnerReference reference,
            @NotNull List<WorkSessionV2Client.OwnerArtifactEvent> events,
            @NotNull LoadedHomeV2 selectedHome
    ) {
        if (!current(request)) return;
        if (events.isEmpty()) {
            refresh.setEnabled(true);
            setSessionButtons(true, selectedHome.recovery() != null
                    && selectedHome.recovery().required());
            state.setText("This owner has no retained live-console event ranges.");
            return;
        }
        String[] choices = events.stream().map(event ->
                "#" + event.sequence() + " · " + event.kind() + " · "
                        + event.stream() + ":" + event.byteStart() + "-" + event.byteEnd()
                        + " · " + event.message()
        ).toArray(String[]::new);
        Object chosen = JOptionPane.showInputDialog(
                this,
                "Select one owner-retained event to inspect its exact raw byte range.",
                "Workbench · Live Console " + reference.recordId(),
                JOptionPane.PLAIN_MESSAGE,
                null,
                choices,
                choices[0]
        );
        if (!(chosen instanceof String selectedChoice)) {
            refresh.setEnabled(true);
            setSessionButtons(true, selectedHome.recovery() != null
                    && selectedHome.recovery().required());
            state.setText("Owner artifact inspection cancelled.");
            return;
        }
        int index = java.util.Arrays.asList(choices).indexOf(selectedChoice);
        ProductSpineJson.require(index >= 0, "Work Session owner artifact selection changed");
        WorkSessionV2Client.OwnerArtifactEvent event = events.get(index);
        state.setText("Opening one exact owner-sealed raw byte range through Workbench …");
        new Task.Backgroundable(project, "Opening Workbench Owner Artifact Range", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                try {
                    WorkSessionV2Client.OwnerArtifactRange range =
                            WorkSessionV2Client.artifactRange(
                                    launch, sessionId, reference, event.eventId(), workspace
                            );
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (!current(request)) return;
                        JsonObject value = new JsonObject();
                        value.add("event", GSON.toJsonTree(event));
                        value.add("raw_range", GSON.toJsonTree(range));
                        new ProductSpineDetailDialog(
                                project,
                                "Workbench · Live-console event and exact raw range",
                                GSON.toJson(value)
                        ).show();
                        refresh.setEnabled(true);
                        setSessionButtons(true, selectedHome.recovery() != null
                                && selectedHome.recovery().required());
                        state.setText("Exact owner-sealed raw byte range inspected.");
                    });
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(
                            () -> failArtifactInspection(request, selectedHome, error)
                    );
                }
            }
        }.queue();
    }

    private void failArtifactInspection(
            long request, @NotNull LoadedHomeV2 selectedHome, @NotNull Exception error
    ) {
        if (!current(request)) return;
        refresh.setEnabled(true);
        setSessionButtons(true, selectedHome.recovery() != null
                && selectedHome.recovery().required());
        state.setText("Owner artifact inspection failed closed.");
        Messages.showErrorDialog(
                project,
                error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage(),
                "Workbench Owner Artifact"
        );
    }

    private static @NotNull JComponent wrapped(@NotNull String text) {
        JBTextArea area = new JBTextArea(text);
        area.setEditable(false);
        area.setOpaque(false);
        area.setLineWrap(true);
        area.setWrapStyleWord(true);
        area.setBorder(JBUI.Borders.emptyLeft(8));
        area.setAlignmentX(LEFT_ALIGNMENT);
        return area;
    }

    private static @NotNull JComponent monospace(@NotNull String text) {
        JBTextArea area = (JBTextArea) wrapped(text);
        area.setFont(new Font(Font.MONOSPACED, Font.PLAIN, area.getFont().getSize()));
        return area;
    }

    @Override
    public void dispose() {
        disposed = true;
        generation.incrementAndGet();
    }

    private record LoadedHomeV2(
            @NotNull WorkspaceHomeV2 home,
            WorkSessionV2.Status status,
            WorkSessionV2.Timeline timeline,
            WorkSessionV2.RecoveryPreview recovery
    ) { }

    private enum SessionOperation {
        RESUME("Resuming Work Session"),
        CLOSE("Closing Work Session navigation"),
        RECOVERY_PREVIEW("Previewing Work Session recovery"),
        RECOVERY_APPLY("Applying Work Session recovery");

        private final String label;

        SessionOperation(@NotNull String label) {
            this.label = label;
        }
    }
}
