package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowFactory;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;
import org.jetbrains.annotations.NotNull;

/** Lazy Community registration for the public workspace Home. */
public final class WorkspaceHomeToolWindowFactory implements ToolWindowFactory, DumbAware {
    static final String TOOL_WINDOW_ID = "Workbench Home";

    @Override
    public void createToolWindowContent(
            @NotNull Project project,
            @NotNull ToolWindow toolWindow
    ) {
        WorkspaceHomePanel panel = new WorkspaceHomePanel(project);
        Content content = ContentFactory.getInstance().createContent(panel, "", false);
        content.setPreferredFocusableComponent(panel);
        content.setDisposer(panel);
        toolWindow.getContentManager().addContent(content);
    }
}
