package dev.cleanroommc.workbench.intellij.community;

import com.intellij.ide.util.PropertiesComponent;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

/** Machine-local discovery policy for the independently installed core. */
public final class CoreLocation {
    static final String PROPERTY = "workbench.coreExecutable";
    static final String ENVIRONMENT = "WORKBENCH_EXECUTABLE";

    private CoreLocation() {
    }

    public static @NotNull String discover(@NotNull Project project) {
        String configured = PropertiesComponent.getInstance(project).getValue(PROPERTY);
        if (usable(configured)) {
            return configured.trim();
        }
        String environment = System.getenv(ENVIRONMENT);
        if (usable(environment)) {
            return environment.trim();
        }
        return "workbench";
    }

    public static void configure(@NotNull Project project, @NotNull String executable) {
        String normalized = executable.trim();
        if (!usable(normalized)) {
            throw new IllegalArgumentException("core executable must be non-empty and contain no NUL");
        }
        PropertiesComponent.getInstance(project).setValue(PROPERTY, normalized);
    }

    public static @Nullable String configured(@NotNull Project project) {
        return PropertiesComponent.getInstance(project).getValue(PROPERTY);
    }

    static boolean usable(@Nullable String value) {
        return value != null && !value.trim().isEmpty() && value.indexOf('\0') < 0;
    }
}
