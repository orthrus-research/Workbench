package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import org.jetbrains.annotations.NotNull;

/** Explicit machine-local executable configuration; package and source identities stay core-owned. */
public final class ConfigureInstalledCoreAction extends AnAction {
    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null) {
            return;
        }
        configure(project);
    }

    static void configure(@NotNull Project project) {
        String current = CoreLocation.configured(project);
        String selected = Messages.showInputDialog(
                project,
                "Enter the installed Workbench executable path or command name. "
                        + "On Windows, a \\\\wsl.localhost\\DISTRO\\... path uses the bounded WSL adapter. "
                        + "It is always invoked directly, without a shell.",
                "Configure Workbench Core",
                Messages.getQuestionIcon(),
                current == null ? CoreLocation.discover(project) : current,
                null
        );
        if (selected == null) {
            return;
        }
        try {
            CoreLocation.configure(project, selected);
            Messages.showInfoMessage(
                    project,
                    "Configured Workbench core executable:\n" + selected.trim()
                            + "\n\nDeveloper commands will invoke it directly without a shell.",
                    "Workbench Core Configured"
            );
        } catch (IllegalArgumentException error) {
            Messages.showErrorDialog(project, error.getMessage(), "Invalid Workbench Core");
        }
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
