package dev.cleanroommc.workbench.intellij.community;

import com.intellij.notification.Notification;
import com.intellij.notification.NotificationGroupManager;
import com.intellij.notification.NotificationAction;
import com.intellij.notification.NotificationType;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.util.text.StringUtil;
import org.jetbrains.annotations.NotNull;

public final class WorkbenchNotifications {
    private WorkbenchNotifications() {
    }

    public static void featureJobReady(
            @NotNull Project project,
            @NotNull FeatureServiceClient.Result result
    ) {
        notify(
                project,
                "Workbench Feature Studio job reopened",
                "Job: " + result.jobId()
                        + "<br/>Owner result: " + result.ownerResultId()
                        + "<br/>Owner bytes: " + result.ownerResultCanonicalSha256(),
                NotificationType.INFORMATION
        );
    }

    public static void featureJobFailed(
            @NotNull Project project,
            @NotNull Throwable error
    ) {
        notify(
                project,
                "Workbench Feature Studio job unavailable",
                "The exact durable job could not be reopened: " + error.getMessage(),
                NotificationType.ERROR
        );
    }

    public static void developerFeatureFinished(
            @NotNull Project project,
            @NotNull DeveloperFeatureClient.Result result
    ) {
        notify(
                project,
                result.complete()
                        ? "Workbench developer feature completed"
                        : "Workbench developer feature is incomplete",
                "Run: " + result.id()
                        + "<br/>Outcome: " + result.outcome()
                        + "<br/>Assertions: " + result.assertionStates(),
                result.complete() ? NotificationType.INFORMATION : NotificationType.WARNING
        );
    }

    public static void developerFeatureFailed(
            @NotNull Project project,
            @NotNull Throwable error
    ) {
        notify(
                project,
                "Workbench developer feature failed",
                "The disposable Cleanroom run did not produce a valid receipt: " + error.getMessage(),
                NotificationType.ERROR
        );
    }

    public static void commandFailed(
            @NotNull Project project,
            @NotNull String title,
            @NotNull Throwable error
    ) {
        String detail = error.getMessage();
        notify(
                project,
                title,
                detail == null || detail.isBlank() ? error.getClass().getSimpleName() : detail,
                NotificationType.ERROR
        );
    }

    public static void recipeReviewFailed(
            @NotNull Project project,
            @NotNull Throwable error,
            @NotNull Runnable retry
    ) {
        String detail = error.getMessage();
        Notification notification = create(
                "Recipe Review needs attention",
                detail == null || detail.isBlank()
                        ? error.getClass().getSimpleName() : detail,
                NotificationType.ERROR
        );
        notification.addAction(new NotificationAction("Retry") {
            @Override
            public void actionPerformed(
                    @NotNull AnActionEvent event,
                    @NotNull Notification current
            ) {
                current.expire();
                retry.run();
            }
        });
        notification.addAction(new NotificationAction("Set Up or Repair") {
            @Override
            public void actionPerformed(
                    @NotNull AnActionEvent event,
                    @NotNull Notification current
            ) {
                current.expire();
                OpenWorkbenchSetupAction.open(project);
            }
        });
        notification.addAction(new NotificationAction("Configure Executable") {
            @Override
            public void actionPerformed(
                    @NotNull AnActionEvent event,
                    @NotNull Notification current
            ) {
                current.expire();
                ConfigureInstalledCoreAction.configure(project);
            }
        });
        notification.notify(project);
    }

    private static void notify(
            Project project,
            String title,
            String content,
            NotificationType type
    ) {
        create(title, content, type).notify(project);
    }

    private static @NotNull Notification create(
            @NotNull String title,
            @NotNull String content,
            @NotNull NotificationType type
    ) {
        return NotificationGroupManager.getInstance()
                .getNotificationGroup("Workbench")
                .createNotification(
                        StringUtil.escapeXmlEntities(title),
                        StringUtil.escapeXmlEntities(content).replace("&lt;br/&gt;", "<br/>"),
                        type
                );
    }
}
