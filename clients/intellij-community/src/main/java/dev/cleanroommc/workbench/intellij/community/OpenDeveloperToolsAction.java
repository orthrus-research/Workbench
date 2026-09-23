package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.List;

/** Complete live-catalog Command Center for IntelliJ Community. */
public final class OpenDeveloperToolsAction extends AnAction {
    private static final int MAX_COMMAND_BYTES = 16 * 1024 * 1024;
    private static final int MAX_PREVIEW_BYTES = 2 * 1024 * 1024;

    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null) {
            return;
        }
        String executable = CoreLocation.discover(project);
        String workingDirectory = project.getBasePath();
        new Task.Backgroundable(project, "Loading Workbench developer tools", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    CoreLaunch launch = CoreLaunch.resolve(executable);
                    String json = CommandProcess.capture(
                            launch,
                            List.of("console", "catalog", "--json"),
                            CommandCatalog.MAX_BYTES,
                            30,
                            workingDirectory
                    );
                    CommandCatalog catalog = CommandCatalog.parse(json);
                    if (catalog.suites().isEmpty()) {
                        throw new IllegalArgumentException(
                                "The installed core catalog has no command suites"
                        );
                    }
                    ApplicationManager.getApplication().invokeLater(
                            () -> chooseSuite(project, workingDirectory, launch, catalog)
                    );
                } catch (Exception error) {
                    failLater(project, "Workbench developer tools unavailable", error);
                }
            }
        }.queue();
    }

    private static void chooseSuite(
            @NotNull Project project,
            @Nullable String workingDirectory,
            @NotNull CoreLaunch launch,
            @NotNull CommandCatalog catalog
    ) {
        if (project.isDisposed()) {
            return;
        }
        List<CatalogSelectionDialog.Choice<CommandCatalog.Suite>> choices = catalog.suites().stream()
                .map(suite -> new CatalogSelectionDialog.Choice<>(
                        suite.title(),
                        suite.commandCount() + " actions · " + suite.availability(),
                        suite.summary() + " Authority: " + suite.authority()
                                + " [" + suite.suiteId() + "]",
                        suite
                ))
                .toList();
        CatalogSelectionDialog<CommandCatalog.Suite> picker = new CatalogSelectionDialog<>(
                project,
                "Workbench Command Center",
                "Choose one exact core-owned tool suite from the live command catalog.",
                choices
        );
        if (!picker.showAndGet()) {
            return;
        }
        CommandCatalog.Suite suite = picker.selected();
        List<CommandCatalog.Command> commands = catalog.commandsForSuite(suite.suiteId());
        if (commands.isEmpty()) {
            WorkbenchNotifications.commandFailed(
                    project,
                    suite.title() + " has no catalog actions",
                    new IllegalArgumentException(
                            suite.summary() + " Availability: " + suite.availability()
                    )
            );
            return;
        }
        chooseCommand(project, workingDirectory, launch, catalog, suite, commands);
    }

    private static void chooseCommand(
            @NotNull Project project,
            @Nullable String workingDirectory,
            @NotNull CoreLaunch launch,
            @NotNull CommandCatalog catalog,
            @NotNull CommandCatalog.Suite suite,
            @NotNull List<CommandCatalog.Command> commands
    ) {
        if (project.isDisposed()) {
            return;
        }
        List<CatalogSelectionDialog.Choice<CommandCatalog.Command>> choices = commands.stream()
                .map(command -> new CatalogSelectionDialog.Choice<>(
                        command.title(),
                        command.risk() + " · " + command.availability(),
                        command.summary() + " Authority: " + command.authority()
                                + " [" + command.commandId() + "]",
                        command
                ))
                .toList();
        CatalogSelectionDialog<CommandCatalog.Command> picker = new CatalogSelectionDialog<>(
                project,
                suite.title(),
                "Choose one exact catalog action. Its authority, risk, options, preview policy, "
                        + "and review binding remain owned by the installed core.",
                choices
        );
        if (!picker.showAndGet()) {
            return;
        }
        CommandCatalog.Command command = picker.selected();
        if (command.availability().equals("unavailable")) {
            WorkbenchNotifications.commandFailed(
                    project,
                    command.title() + " is unavailable",
                    new IllegalArgumentException(command.summary())
            );
            return;
        }
        if (command.isDocument()) {
            new CommandOutputDialog(project, command, documentationSummary(command)).show();
            return;
        }
        CommandOptionsDialog options = new CommandOptionsDialog(
                project, command, workingDirectory
        );
        if (!options.showAndGet()) {
            return;
        }
        final CommandFlow flow;
        try {
            flow = CommandFlow.compose(
                    launch, catalog.catalogDigest(), command, options.values()
            );
        } catch (Exception error) {
            WorkbenchNotifications.commandFailed(project, "Invalid Workbench inputs", error);
            return;
        }
        prepare(project, workingDirectory, launch, command, flow);
    }

    static @NotNull String documentationSummary(@NotNull CommandCatalog.Command command) {
        if (!command.isDocument()) {
            throw new IllegalArgumentException("Workbench catalog entry is not documentation-only");
        }
        StringBuilder output = new StringBuilder()
                .append("Documentation-only catalog entry\n")
                .append("================================\n")
                .append("Path: ").append(command.document()).append('\n');
        if (command.documentation() != null) {
            output.append("Reference: ").append(command.documentation()).append('\n');
        }
        output.append("\nNo command was executed. Open the documented path from a Workbench checkout.");
        return output.toString();
    }

    private static void prepare(
            @NotNull Project project,
            @Nullable String workingDirectory,
            @NotNull CoreLaunch launch,
            @NotNull CommandCatalog.Command command,
            @NotNull CommandFlow flow
    ) {
        new Task.Backgroundable(project, "Preparing " + command.title(), false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    String reviewJson = CommandProcess.capture(
                            launch,
                            flow.commandReviewArguments(),
                            MAX_PREVIEW_BYTES,
                            30,
                            workingDirectory
                    );
                    CommandFlow.Bound bound = flow.bindReview(reviewJson);
                    String ownerPreview = null;
                    if (bound.ownerPreviewArgumentsOptional().isPresent()) {
                        ownerPreview = CommandProcess.capture(
                                launch,
                                bound.ownerPreviewArgumentsOptional().orElseThrow(),
                                MAX_PREVIEW_BYTES,
                                600,
                                workingDirectory
                        );
                    }
                    String review = "Preview argv:\n" + bound.review().previewCommand()
                            + "\n\nExecution argv:\n" + bound.review().executeCommand()
                            + "\n\nReview binding: " + bound.review().reviewDigest();
                    String finalOwnerPreview = ownerPreview;
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (project.isDisposed()) {
                            return;
                        }
                        CommandReviewDialog dialog = new CommandReviewDialog(
                                project, command, review, finalOwnerPreview
                        );
                        if (dialog.showAndGet()) {
                            if (!command.risk().equals("read-only")
                                    && FileDocumentManager.getInstance()
                                    .getUnsavedDocuments().length > 0) {
                                WorkbenchNotifications.commandFailed(
                                        project,
                                        "Save files before " + command.title(),
                                        new IllegalStateException(
                                                "Workbench will not run a write-capable reviewed "
                                                        + "action while IntelliJ has unsaved documents"
                                        )
                                );
                                return;
                            }
                            execute(
                                    project,
                                    workingDirectory,
                                    launch,
                                    command,
                                    bound.executeArguments()
                            );
                        }
                    });
                } catch (Exception error) {
                    failLater(project, "Workbench command review failed", error);
                }
            }
        }.queue();
    }

    private static void execute(
            @NotNull Project project,
            @Nullable String workingDirectory,
            @NotNull CoreLaunch launch,
            @NotNull CommandCatalog.Command command,
            @NotNull List<String> arguments
    ) {
        new Task.Backgroundable(project, "Running " + command.title(), false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    long timeout = command.risk().equals("read-only") ? 900 : 12 * 60 * 60;
                    String output = CommandProcess.capture(
                            launch,
                            arguments,
                            MAX_COMMAND_BYTES,
                            timeout,
                            workingDirectory
                    );
                    ApplicationManager.getApplication().invokeLater(() -> {
                        if (!project.isDisposed()) {
                            new CommandOutputDialog(project, command, output).show();
                        }
                    });
                } catch (Exception error) {
                    boolean transactionRisk = command.risk().equals("mutating")
                            || command.risk().equals("destructive");
                    String title = transactionRisk
                            ? "Workbench transaction did not complete"
                            : "Workbench command failed";
                    Exception reported = transactionRisk
                            ? new IllegalStateException(
                                    "Inspect retained receipts and use Recover before retrying. "
                                            + (error.getMessage() == null
                                            ? error.getClass().getSimpleName()
                                            : error.getMessage()),
                                    error
                            )
                            : error;
                    failLater(project, title, reported);
                }
            }
        }.queue();
    }

    private static void failLater(
            @NotNull Project project,
            @NotNull String title,
            @NotNull Throwable error
    ) {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (!project.isDisposed()) {
                WorkbenchNotifications.commandFailed(project, title, error);
            }
        });
    }

    @Override
    public void update(@NotNull AnActionEvent event) {
        event.getPresentation().setEnabledAndVisible(event.getProject() != null);
    }

    @Override
    public @NotNull ActionUpdateThread getActionUpdateThread() {
        return ActionUpdateThread.BGT;
    }
}
