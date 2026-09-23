package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowManager;
import com.intellij.ui.content.Content;
import org.jetbrains.annotations.NotNull;

/** Opens the native provider-plan → consent → PR Recipe Review journey. */
public final class OpenRecipeReviewAction extends AnAction {
    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null || project.getBasePath() == null) {
            return;
        }
        open(project);
    }

    static void open(@NotNull Project project) {
        if (project.getBasePath() == null
                || !WorkbenchProjectTrust.require(project, "inspect recipe changes")) {
            return;
        }
        ToolWindowManager manager = ToolWindowManager.getInstance(project);
        ToolWindow toolWindow = manager
                .getToolWindow(RecipeReviewToolWindowFactory.TOOL_WINDOW_ID);
        if (toolWindow == null) {
            WorkbenchNotifications.commandFailed(
                    project,
                    "Recipe Review tool window is unavailable",
                    new IllegalStateException("the Community tool window was not registered")
            );
            return;
        }
        toolWindow.show();
        manager.invokeLater(() -> {
            if (project.isDisposed()) {
                return;
            }
            for (Content content : toolWindow.getContentManager().getContents()) {
                if (content.getComponent() instanceof RecipeReviewPanel panel) {
                    panel.promptForPullRequest();
                    return;
                }
            }
        });
    }

    @Override
    public void update(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        event.getPresentation().setEnabledAndVisible(
                project != null && project.getBasePath() != null
        );
        event.getPresentation().setEnabled(
                project != null && project.getBasePath() != null
                        && WorkbenchProjectTrust.isTrusted(project)
        );
    }

    @Override
    public @NotNull ActionUpdateThread getActionUpdateThread() {
        return ActionUpdateThread.BGT;
    }
}
