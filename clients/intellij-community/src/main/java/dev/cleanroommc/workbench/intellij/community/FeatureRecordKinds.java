package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;

/** V1 owner-kind mapping needed only to reject cross-family retained identities. */
final class FeatureRecordKinds {
    private FeatureRecordKinds() {
    }

    static @NotNull String expected(@NotNull String family, @NotNull String collection) {
        String suffix = switch (collection) {
            case "plans" -> "plan";
            case "receipts" -> "receipt";
            case "rollbacks" -> "rollback";
            case "recoveries" -> "recovery";
            case "runs" -> "run";
            default -> throw new IllegalArgumentException(
                    "Workbench feature collection is unsupported: " + collection
            );
        };
        return switch (family) {
            case "material-fluid-recipe" ->
                    "workbench-developer-material-fluid-recipe-" + suffix;
            case "recipe-change" -> collection.equals("runs")
                    ? "workbench-developer-recipe-change-runtime-comparison"
                    : "workbench-supersymmetry-recipe-change-" + suffix;
            case "quest-for-process" -> {
                require(!collection.equals("runs"),
                        "quest-for-process has no retained runtime record");
                yield "workbench-developer-source-feature-" + suffix;
            }
            default -> throw new IllegalArgumentException(
                    "Workbench feature family is unsupported: " + family
            );
        };
    }

    static void requireIdKind(@NotNull String id, @NotNull String kind, @NotNull String label) {
        require(id.startsWith(kind + ":sha256:"),
                "Workbench " + label + " does not match its owner kind");
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }
}
