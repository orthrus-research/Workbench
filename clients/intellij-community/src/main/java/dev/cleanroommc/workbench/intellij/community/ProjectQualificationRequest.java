package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** Exact direct argv for the optional core-owned project-qualification flow. */
final class ProjectQualificationRequest {
    private static final Pattern PLAN_ID = Pattern.compile(
            "^workbench-project-qualification-plan:sha256:[0-9a-f]{64}$"
    );
    private final String workspace;

    private ProjectQualificationRequest(@NotNull String resolvedWorkspace) {
        workspace = bounded(resolvedWorkspace, "resolved qualification workspace");
    }

    /** Resolve aliases once so plan, confirmation, and apply share one filesystem identity. */
    static @NotNull ProjectQualificationRequest fromProjectWorkspace(
            @NotNull String workspace
    ) {
        String selected = bounded(workspace, "qualification workspace");
        try {
            return fromResolvedWorkspace(Path.of(selected).toRealPath().toString());
        } catch (InvalidPathException | IOException | SecurityException error) {
            throw new IllegalArgumentException(
                    "qualification workspace cannot be resolved to one real path",
                    error
            );
        }
    }

    /**
     * Build from a path already resolved by the current host filesystem.
     * Kept separate so platform transport fixtures can exercise Windows/WSL mapping on Linux.
     */
    static @NotNull ProjectQualificationRequest fromResolvedWorkspace(
            @NotNull String resolvedWorkspace
    ) {
        return new ProjectQualificationRequest(resolvedWorkspace);
    }

    @NotNull String workspace() {
        return workspace;
    }

    @NotNull List<String> planArguments(@NotNull CoreLaunch launch) {
        List<String> arguments = commonArguments(launch);
        arguments.add("--plan");
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    @NotNull String coreWorkspace(@NotNull CoreLaunch launch) {
        return launch.commandPath(workspace, "qualification workspace");
    }

    @NotNull List<String> applyArguments(
            @NotNull CoreLaunch launch,
            @NotNull String planId
    ) {
        if (!PLAN_ID.matcher(planId).matches()) {
            throw new IllegalArgumentException("project qualification plan identity is invalid");
        }
        List<String> arguments = commonArguments(launch);
        arguments.add("--apply");
        arguments.add(planId);
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    private @NotNull List<String> commonArguments(@NotNull CoreLaunch launch) {
        List<String> arguments = new ArrayList<>();
        arguments.add("project");
        arguments.add("qualify");
        arguments.add(coreWorkspace(launch));
        arguments.add("--profile");
        arguments.add("supersymmetry");
        return arguments;
    }

    private static @NotNull String bounded(
            @NotNull String value,
            @NotNull String label
    ) {
        String selected = value;
        if (selected.isBlank() || selected.indexOf('\0') >= 0
                || selected.getBytes(StandardCharsets.UTF_8).length > 32 * 1024) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return selected;
    }
}
