package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.progress.ProcessCanceledException;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import org.jetbrains.annotations.NotNull;

import java.util.List;

/** Optional plan → consent → apply action for core-owned project qualification. */
public final class OpenProjectQualificationAction extends AnAction {
    private static final int PLAN_TIMEOUT_SECONDS = 10 * 60;
    private static final int APPLY_TIMEOUT_SECONDS = 10 * 60;

    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project != null) {
            qualify(project);
        }
    }

    static void qualify(@NotNull Project project) {
        if (project.isDisposed()
                || !WorkbenchProjectTrust.require(project, "qualify this pack")) {
            return;
        }
        String workspace = project.getBasePath();
        if (workspace == null || workspace.isBlank()) {
            Messages.showErrorDialog(
                    project,
                    "The IntelliJ project has no workspace root to qualify.",
                    "Qualify This Pack"
            );
            return;
        }
        final CoreLaunch launch;
        final ProjectQualificationRequest request;
        try {
            launch = CoreLaunch.resolve(CoreLocation.discover(project));
            request = ProjectQualificationRequest.fromProjectWorkspace(workspace);
            request.planArguments(launch);
        } catch (RuntimeException error) {
            failed(project, "Project qualification could not start", error);
            return;
        }
        new Task.Backgroundable(project, "Checking Pack Qualification", true) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                indicator.setText("Inspecting the workspace against the Supersymmetry profile");
                indicator.setText2("This read-only phase does not record qualification");
                try {
                    String output = CommandProcess.capture(
                            launch,
                            request.planArguments(launch),
                            ProjectQualification.MAX_OUTPUT_BYTES,
                            PLAN_TIMEOUT_SECONDS,
                            workspace,
                            indicator::isCanceled
                    );
                    ProjectQualification.Plan plan = ProjectQualification.parsePlan(
                            output, request.coreWorkspace(launch)
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> inspectOrConfirm(project, launch, request, plan)
                    );
                } catch (ProcessCanceledException error) {
                    throw error;
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(() ->
                            failed(project, "Pack qualification check failed", error));
                }
            }
        }.queue();
    }

    private static void inspectOrConfirm(
            @NotNull Project project,
            @NotNull CoreLaunch launch,
            @NotNull ProjectQualificationRequest request,
            @NotNull ProjectQualification.Plan plan
    ) {
        if (project.isDisposed()) {
            return;
        }
        if (!plan.canApply()) {
            Messages.showWarningDialog(
                    project,
                    confirmationText(plan),
                    "This Pack Is Incompatible"
            );
            return;
        }
        String verb = "attention".equals(plan.state())
                ? "Record With Attention"
                : "Record Qualification";
        int selection = Messages.showYesNoDialog(
                project,
                confirmationText(plan),
                "Qualify This Pack",
                verb,
                "Cancel",
                "attention".equals(plan.state())
                        ? Messages.getWarningIcon()
                        : Messages.getQuestionIcon()
        );
        if (selection == Messages.YES) {
            apply(project, launch, request, plan);
        }
    }

    private static void apply(
            @NotNull Project project,
            @NotNull CoreLaunch launch,
            @NotNull ProjectQualificationRequest request,
            @NotNull ProjectQualification.Plan plan
    ) {
        String workspace = request.workspace();
        // Once explicitly consented, let the core finish its bounded private-record
        // transaction instead of cancelling it between validation and atomic replace.
        new Task.Backgroundable(project, "Saving Pack Qualification", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                indicator.setText("Revalidating and applying the exact qualification plan");
                indicator.setText2(plan.planId());
                try {
                    String output = CommandProcess.capture(
                            launch,
                            request.applyArguments(launch, plan.planId()),
                            ProjectQualification.MAX_OUTPUT_BYTES,
                            APPLY_TIMEOUT_SECONDS,
                            workspace
                    );
                    ProjectQualification.Result result = ProjectQualification.parseResult(
                            output, plan
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> succeeded(project, result)
                    );
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(
                            () -> failed(project, "Pack qualification was not saved", error)
                    );
                }
            }
        }.queue();
    }

    static @NotNull String confirmationText(@NotNull ProjectQualification.Plan plan) {
        StringBuilder text = new StringBuilder();
        text.append("Qualification state: ")
                .append(plan.state().toUpperCase())
                .append("\n\nCheck summary:\n");
        appendChecks(text, plan.checks());
        appendLimitations(text, plan.limitations());
        text.append("\nProfile: ").append(plan.profile().packProfileId())
                .append("\nVariant: ").append(plan.profile().packVariant())
                .append("\nPlatform: ").append(plan.profile().platformProfileId())
                .append("\nWorkspace: ").append(plan.workspace().root())
                .append("\nRevision: ").append(plan.workspace().revision())
                .append("\nWorking tree: ")
                .append(plan.workspace().dirty()
                        ? "dirty (" + plan.workspace().dirtyEntries().size() + " entries)"
                        : "clean")
                .append("\nCurrent binding: ").append(plan.binding().state().toUpperCase());
        if (!plan.binding().staleReasons().isEmpty()) {
            text.append(" — ").append(String.join(", ", plan.binding().staleReasons()));
        }
        if (plan.canApply() && plan.action() != null) {
            text.append("\n\nPlanned effect: ").append(plan.action().effect());
        } else {
            text.append("\n\nNo qualification action is available. This incompatible "
                    + "workspace cannot be qualified.");
        }
        text
                .append("\nExact plan: ").append(plan.planId())
                .append("\n\nRecording qualification establishes only the profile family "
                        + "identified by these checks. It is not release, publication, "
                        + "runtime, recipe, or code approval.");
        return text.toString();
    }

    static @NotNull String resultText(@NotNull ProjectQualification.Result result) {
        ProjectQualification.Qualification qualification = result.qualification();
        StringBuilder text = new StringBuilder();
        text.append("Outcome: ").append(result.outcome().toUpperCase())
                .append("\nQualification state: ")
                .append(qualification.state().toUpperCase())
                .append("\nBinding state: ").append(result.binding().state().toUpperCase())
                .append("\nBinding: ").append(result.binding().bindingId())
                .append("\nApplied plan: ").append(result.appliedPlanId())
                .append("\n\nCheck summary:\n");
        appendChecks(text, qualification.checks());
        appendLimitations(text, qualification.limitations());
        text.append("\nWorkspace: ").append(qualification.workspace().root())
                .append("\nInspected revision: ").append(qualification.workspace().revision())
                .append("\n\nThis qualification records profile-family fit only. It is not "
                        + "release, publication, runtime, recipe, or code approval.");
        return text.toString();
    }

    private static void appendChecks(
            @NotNull StringBuilder text,
            @NotNull List<ProjectQualification.Check> checks
    ) {
        for (ProjectQualification.Check check : checks) {
            text.append("• ").append(check.state().toUpperCase())
                    .append(" — ").append(check.label())
                    .append(": ").append(check.detail()).append('\n');
        }
    }

    private static void appendLimitations(
            @NotNull StringBuilder text,
            @NotNull List<String> limitations
    ) {
        if (limitations.isEmpty()) {
            return;
        }
        text.append("\nLimitations:\n");
        for (String limitation : limitations) {
            text.append("• ").append(limitation).append('\n');
        }
    }

    private static void succeeded(
            @NotNull Project project,
            @NotNull ProjectQualification.Result result
    ) {
        if (project.isDisposed()) {
            return;
        }
        Messages.showInfoMessage(
                project,
                resultText(result),
                "Pack Qualification Saved"
        );
    }

    private static void failed(
            @NotNull Project project,
            @NotNull String title,
            @NotNull Throwable error
    ) {
        if (!project.isDisposed()) {
            WorkbenchNotifications.commandFailed(project, title, error);
        }
    }

    @Override
    public void update(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        boolean hasWorkspace = project != null
                && project.getBasePath() != null
                && !project.getBasePath().isBlank();
        event.getPresentation().setEnabledAndVisible(hasWorkspace);
        event.getPresentation().setEnabled(
                hasWorkspace && WorkbenchProjectTrust.isTrusted(project)
        );
    }

    @Override
    public @NotNull ActionUpdateThread getActionUpdateThread() {
        return ActionUpdateThread.BGT;
    }
}
