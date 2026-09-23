package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.fileChooser.FileChooser;
import com.intellij.openapi.fileChooser.FileChooserDescriptorFactory;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.vfs.VirtualFile;
import org.jetbrains.annotations.NotNull;

import java.nio.charset.StandardCharsets;
import java.util.Set;

/** Native Community action that reopens one exact durable Feature Studio job. */
public final class ReopenFeatureStudioJobAction extends AnAction {
    private static final Set<String> ATTACHMENT_FIELDS = Set.of(
            "context_ref_id", "credential", "endpoint", "input_binding_id",
            "job_id", "job_submission_id"
    );

    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null) {
            return;
        }
        VirtualFile selected = FileChooser.chooseFile(
                FileChooserDescriptorFactory.createSingleFileDescriptor("json")
                        .withTitle("Open Workbench Feature Job Attachment"),
                project,
                null
        );
        if (selected == null) {
            return;
        }
        final FeatureServiceClient.Connection connection;
        final FeatureServiceClient.Job job;
        try {
            if (selected.getLength() < 2 || selected.getLength() > 64 * 1024) {
                throw new IllegalArgumentException("job attachment is outside the 64 KiB boundary");
            }
            String text = new String(selected.contentsToByteArray(), StandardCharsets.UTF_8);
            JsonObject value = JsonParser.parseString(text).getAsJsonObject();
            if (!value.keySet().equals(ATTACHMENT_FIELDS)) {
                throw new IllegalArgumentException("job attachment fields changed");
            }
            connection = new FeatureServiceClient.Connection(
                    value.get("endpoint").getAsString(),
                    value.get("credential").getAsString()
            );
            job = new FeatureServiceClient.Job(
                    value.get("context_ref_id").getAsString(),
                    value.get("input_binding_id").getAsString(),
                    value.get("job_id").getAsString(),
                    value.get("job_submission_id").getAsString()
            );
        } catch (Exception error) {
            WorkbenchNotifications.featureJobFailed(project, error);
            return;
        }
        String executable = CoreLocation.discover(project);
        new Task.Backgroundable(project, "Reopening Workbench Feature Studio job", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    FeatureServiceClient.Result result = FeatureServiceClient.readResult(
                            executable, connection, job
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> WorkbenchNotifications.featureJobReady(project, result)
                    );
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(
                            () -> WorkbenchNotifications.featureJobFailed(project, error)
                    );
                }
            }
        }.queue();
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
