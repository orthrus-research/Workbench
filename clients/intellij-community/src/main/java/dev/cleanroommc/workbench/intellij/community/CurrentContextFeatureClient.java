package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.IOException;
import java.util.List;
import java.util.Set;

/** Product-owned path/identity-free action port for the selected Work Session. */
public final class CurrentContextFeatureClient {
    private static final Set<String> ACTIONS = Set.of(
            "open", "test", "apply", "verify", "rollback", "recover"
    );
    private static final int MAXIMUM_OUTPUT_BYTES = 48 * 1024 * 1024;

    private CurrentContextFeatureClient() {
    }

    public static @NotNull List<String> arguments(@NotNull String action) {
        if (!ACTIONS.contains(action)) {
            throw new IllegalArgumentException(
                    "current Work Session feature action is unsupported"
            );
        }
        return List.of("change", "material-fluid-recipe", action, "--json");
    }

    public static @NotNull CommandProcess.Observation invoke(
            @NotNull CoreLaunch launch,
            @NotNull String action,
            long timeoutSeconds,
            @Nullable String ambientWorkingDirectory
    ) throws IOException {
        CommandProcess.Observation observation = CommandProcess.captureObserved(
                launch,
                arguments(action),
                MAXIMUM_OUTPUT_BYTES,
                timeoutSeconds,
                ambientWorkingDirectory
        );
        String stderr = observation.stderr().trim();
        if (observation.exitCode() != 0 || !stderr.isEmpty()) {
            String detail = stderr.isEmpty()
                    ? observation.stdout().trim()
                    : stderr;
            throw new IOException(
                    "current Work Session action exited " + observation.exitCode()
                            + (detail.isEmpty()
                            ? ""
                            : ":\n" + detail.substring(
                                    0, Math.min(16 * 1024, detail.length())
                            ))
            );
        }
        return observation;
    }
}
