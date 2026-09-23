package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** Exact direct argv for the core-owned provider-bound PR Recipe Review flow. */
public record RecipeReviewRequest(
        int pullRequest,
        @NotNull String source
) {
    private static final Pattern PLAN_ID = Pattern.compile(
            "^workbench-pr-preparation-plan-v2:sha256:[0-9a-f]{64}$"
    );

    public RecipeReviewRequest {
        if (pullRequest < 1) {
            throw new IllegalArgumentException("pull request number must be positive");
        }
        source = bounded(source, "recipe review source");
    }

    public @NotNull List<String> planArguments(@NotNull CoreLaunch launch) {
        List<String> arguments = commonArguments(launch);
        arguments.add("--plan");
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    public @NotNull List<String> applyArguments(
            @NotNull CoreLaunch launch,
            @NotNull String planId
    ) {
        if (!PLAN_ID.matcher(planId).matches()) {
            throw new IllegalArgumentException("PR review plan identity is invalid");
        }
        List<String> arguments = commonArguments(launch);
        arguments.add("--apply");
        arguments.add(planId);
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    private @NotNull List<String> commonArguments(@NotNull CoreLaunch launch) {
        List<String> arguments = new ArrayList<>();
        arguments.add("review");
        arguments.add("pr");
        arguments.add(Integer.toString(pullRequest));
        arguments.add("--profile");
        arguments.add("supersymmetry");
        arguments.add("--source");
        arguments.add(launch.commandPath(source, "recipe review source"));
        return arguments;
    }

    static int parsePullRequest(@NotNull String value) {
        String selected = value.trim();
        if (selected.isEmpty() || !selected.chars().allMatch(Character::isDigit)) {
            throw new IllegalArgumentException("Enter a positive pull request number.");
        }
        try {
            int number = Integer.parseInt(selected);
            if (number < 1) {
                throw new IllegalArgumentException("Enter a positive pull request number.");
            }
            return number;
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException(
                    "Pull request number is outside the supported range.", error
            );
        }
    }

    private static @NotNull String bounded(@NotNull String value, @NotNull String label) {
        String selected = value.trim();
        if (selected.isEmpty() || selected.indexOf('\0') >= 0
                || selected.getBytes(StandardCharsets.UTF_8).length > 32 * 1024) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return selected;
    }
}
