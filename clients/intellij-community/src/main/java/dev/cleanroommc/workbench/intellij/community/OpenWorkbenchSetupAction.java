package dev.cleanroommc.workbench.intellij.community;

import com.intellij.execution.ExecutionException;
import com.intellij.execution.RunContentExecutor;
import com.intellij.execution.configurations.GeneralCommandLine;
import com.intellij.execution.filters.TextConsoleBuilderFactory;
import com.intellij.execution.process.KillableProcessHandler;
import com.intellij.execution.process.ProcessTerminatedListener;
import com.intellij.execution.ui.ConsoleView;
import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.NotNull;

import java.nio.charset.StandardCharsets;
import java.util.List;

/** Opens the core-owned interactive setup wizard in an IntelliJ run console. */
public final class OpenWorkbenchSetupAction extends AnAction {
    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null) {
            return;
        }
        open(project);
    }

    static void open(@NotNull Project project) {
        if (!WorkbenchProjectTrust.require(project, "run setup")) {
            return;
        }
        try {
            CoreLaunch launch = CoreLaunch.resolve(CoreLocation.discover(project));
            List<String> command = launch.command(List.of("setup"));
            GeneralCommandLine commandLine = new GeneralCommandLine(command)
                    .withCharset(StandardCharsets.UTF_8)
                    .withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.NONE)
                    .withEnvironment(CommandProcess.scrubbedEnvironment(System.getenv()));
            if (launch.host().equals("native") && project.getBasePath() != null) {
                commandLine.withWorkDirectory(project.getBasePath());
            }
            KillableProcessHandler handler = new KillableProcessHandler(commandLine);
            handler.setShouldKillProcessSoftly(true);
            handler.setShouldDestroyProcessRecursively(true);
            ProcessTerminatedListener.attach(handler);
            ConsoleView console = TextConsoleBuilderFactory.getInstance()
                    .createBuilder(project)
                    .getConsole();
            console.attachToProcess(handler);
            new RunContentExecutor(project, handler)
                    .withConsole(console)
                    .withTitle("Workbench Setup")
                    .withActivateToolWindow(true)
                    .run();
        } catch (ExecutionException | IllegalArgumentException error) {
            OpenInstallationHelpAction.recover(
                    project,
                    "Workbench Setup Could Not Start",
                    error
            );
        }
    }

    @Override
    public void update(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        event.getPresentation().setEnabledAndVisible(project != null);
        event.getPresentation().setEnabled(project != null && WorkbenchProjectTrust.isTrusted(project));
    }

    @Override
    public @NotNull ActionUpdateThread getActionUpdateThread() {
        return ActionUpdateThread.BGT;
    }
}
