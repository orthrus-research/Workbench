package dev.cleanroommc.workbench.intellij.community;

import com.intellij.ide.trustedProjects.TrustedProjects;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.Messages;
import org.jetbrains.annotations.NotNull;

/** Shared guard for new first-run commands that launch the local Workbench CLI. */
final class WorkbenchProjectTrust {
    private WorkbenchProjectTrust() {
    }

    static boolean isTrusted(@NotNull Project project) {
        return TrustedProjects.isProjectTrusted(project);
    }

    static boolean require(@NotNull Project project, @NotNull String action) {
        if (isTrusted(project)) {
            return true;
        }
        Messages.showWarningDialog(
                project,
                "Trust this IntelliJ project before Workbench can " + action + ".",
                "Workbench Requires a Trusted Project"
        );
        return false;
    }
}
