package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowManager;
import org.jetbrains.annotations.NotNull;

/** Opens the current project’s read-only Workbench Home. */
public final class OpenWorkspaceHomeAction extends AnAction {
    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null) {
            return;
        }
        ToolWindow toolWindow = ToolWindowManager.getInstance(project)
                .getToolWindow(WorkspaceHomeToolWindowFactory.TOOL_WINDOW_ID);
        if (toolWindow == null) {
            WorkbenchNotifications.commandFailed(
                    project,
                    "Workbench Home tool window is unavailable",
                    new IllegalStateException("the Community Home tool window was not registered")
            );
            return;
        }
        toolWindow.show();
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
