package dev.cleanroommc.workbench.intellij.community;

import com.intellij.ide.BrowserUtil;
import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import org.jetbrains.annotations.NotNull;

/** Actionable first-run recovery that does not duplicate installation authority. */
public final class OpenInstallationHelpAction extends AnAction {
    static final String DOCUMENTATION_URL =
            "https://github.com/orthrus-research/workbench#start-here";

    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project != null) {
            open(project);
        }
    }

    static @NotNull String instructions() {
        return "Obtain a reviewed native Workbench wheelhouse and matching installer for "
                + "your Python version, operating system, and architecture. "
                + "Use the trusted installer with a new environment:\n\n"
                + "python <wheelhouse>/install_workbench.py <wheelhouse> --destination <new-environment>\n\n"
                + "The installer is included in the wheelhouse; a source checkout is not required. "
                + "Include Workbench Shell and its dependencies for Workspace Home; "
                + "profile-specific commands also need their selected profile. "
                + "The source Pixi bootstrap prepares development tools, not an installed launcher.\n\n"
                + "The installer reports the exact Workbench executable. Add its directory to PATH, "
                + "or choose the exact executable with Configure Core Executable. "
                + "Then run Set Up or Repair Environment.";
    }

    static void open(@NotNull Project project) {
        int selected = Messages.showDialog(
                project,
                instructions(),
                "Install the Workbench CLI",
                new String[]{"Open Online Guide", "Close"},
                0,
                Messages.getInformationIcon()
        );
        if (selected == 0) {
            BrowserUtil.browse(DOCUMENTATION_URL);
        }
    }

    static void recover(
            @NotNull Project project,
            @NotNull String title,
            @NotNull Throwable error
    ) {
        String detail = error.getMessage();
        int selected = Messages.showDialog(
                project,
                (detail == null || detail.isBlank()
                        ? error.getClass().getSimpleName()
                        : detail)
                        + "\n\nIf the Workbench CLI is missing, install a reviewed native wheelhouse. "
                        + "If it is already installed outside PATH, choose its exact executable.",
                title,
                new String[]{"Installation Help", "Configure Executable", "Close"},
                0,
                Messages.getErrorIcon()
        );
        if (selected == 0) {
            open(project);
        } else if (selected == 1) {
            ConfigureInstalledCoreAction.configure(project);
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
