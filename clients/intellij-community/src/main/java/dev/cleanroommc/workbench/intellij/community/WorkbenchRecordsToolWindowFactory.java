package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowFactory;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;
import com.intellij.ui.content.ContentManagerEvent;
import com.intellij.ui.content.ContentManagerListener;
import javax.swing.JPanel;
import org.jetbrains.annotations.NotNull;

/** Lazy Community-compatible native record explorer registration. */
public final class WorkbenchRecordsToolWindowFactory implements ToolWindowFactory, DumbAware {
    static final String TOOL_WINDOW_ID = "Workbench Records";

    @Override
    public void createToolWindowContent(
            @NotNull Project project,
            @NotNull ToolWindow toolWindow
    ) {
        var manager = toolWindow.getContentManager();
        Content content = ContentFactory.getInstance().createContent(new JPanel(), "Retained Records", false);
        Runnable load = () -> ApplicationManager.getApplication().invokeLater(() -> {
            if (project.isDisposed() || manager.isDisposed() || manager.getSelectedContent() != content || content.getComponent() instanceof RetainedRecordsPanel) return;
            var panel = new RetainedRecordsPanel(project);
            content.setComponent(panel); content.setPreferredFocusableComponent(panel); content.setDisposer(panel);
        });
        var listener = new ContentManagerListener() {
            @Override public void selectionChanged(@NotNull ContentManagerEvent event) { if (event.getContent() == content) load.run(); }
        };
        manager.addContentManagerListener(listener);
        com.intellij.openapi.util.Disposer.register(project, () -> manager.removeContentManagerListener(listener));
        manager.addContent(content);
        load.run();
    }
}
