package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.project.Project;
import com.intellij.openapi.util.Key;

/** An ephemeral pointer to the existing Work Session, shared by IDE actions. */
final class DeveloperContextSelection {
    private static final Key<String> SESSION = Key.create("workbench.developer.context.session");
    static String get(Project project) { return project.getUserData(SESSION); }
    static void set(Project project, String value) { project.putUserData(SESSION, value); }
}
