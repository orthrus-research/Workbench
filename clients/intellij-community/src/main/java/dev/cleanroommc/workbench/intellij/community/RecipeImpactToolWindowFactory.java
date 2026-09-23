package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowFactory;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;
import org.jetbrains.annotations.NotNull;

/** Lazy Community-compatible native Atlas recipe-impact registration. */
public final class RecipeImpactToolWindowFactory implements ToolWindowFactory, DumbAware {
    static final String TOOL_WINDOW_ID = "Recipe Impact";

    @Override
    public void createToolWindowContent(
            @NotNull Project project,
            @NotNull ToolWindow toolWindow
    ) {
        RecipeImpactPanel panel = new RecipeImpactPanel(project);
        Content content = ContentFactory.getInstance().createContent(panel, "", false);
        content.setPreferredFocusableComponent(panel);
        content.setDisposer(panel);
        toolWindow.getContentManager().addContent(content);
    }
}
