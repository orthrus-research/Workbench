package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Identity-bearing base projection embedded by the current Workspace Home response. */
public final class WorkspaceHome {
    public static final String FORMAT = "workbench-workspace-home-v1";
    public static final int MAX_BYTES = 2 * 1024 * 1024;
    private static final int MAX_ACTIONS = 5;
    private static final int MAX_ROWS = 256;

    private final String rawJson;
    private final Workspace workspace;
    private final String status;
    private final List<Problem> problems;
    private final List<Action> actions;
    private final List<Gap> gaps;
    private final List<String> limitations;

    private WorkspaceHome(
            @NotNull String rawJson,
            @NotNull Workspace workspace,
            @NotNull String status,
            @NotNull List<Problem> problems,
            @NotNull List<Action> actions,
            @NotNull List<Gap> gaps,
            @NotNull List<String> limitations
    ) {
        this.rawJson = rawJson;
        this.workspace = workspace;
        this.status = status;
        this.problems = List.copyOf(problems);
        this.actions = List.copyOf(actions);
        this.gaps = List.copyOf(gaps);
        this.limitations = List.copyOf(limitations);
    }

    public static @NotNull WorkspaceHome parse(@NotNull String json) {
        int size = json.getBytes(StandardCharsets.UTF_8).length;
        require(size >= 1 && size <= MAX_BYTES,
                "Workbench Home size is outside the supported bound");
        StrictJson.validate(json, "workspace Home", 50_000);
        final JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (RuntimeException error) {
            throw new IllegalArgumentException("Workbench Home is invalid JSON", error);
        }
        JsonObject root = object(parsed, "workspace Home");
        equal(string(root, "format"), FORMAT,
                "Workbench returned an unsupported Home format");
        require(integer(root, "schema_version") == 1,
                "Workbench returned an unsupported Home schema");
        require(bool(root, "read_only"), "Workbench Home is not read-only");

        JsonObject identity = object(required(root, "workspace"), "Home workspace");
        Workspace workspace = new Workspace(
                string(identity, "requested_path"),
                string(identity, "root"),
                string(identity, "display_name"),
                string(identity, "kind"),
                string(identity, "recognition")
        );
        String status = string(object(required(root, "status"), "Home status"), "status");

        JsonArray problemRows = array(root, "problems");
        require(problemRows.size() <= MAX_ROWS, "Workbench Home has too many problems");
        List<Problem> problems = new ArrayList<>();
        for (JsonElement element : problemRows) {
            JsonObject problem = object(element, "Home problem");
            problems.add(new Problem(
                    string(problem, "id"),
                    string(problem, "severity"),
                    string(problem, "title"),
                    string(problem, "detail")
            ));
        }

        JsonArray actionRows = array(root, "actions");
        require(actionRows.size() >= 1 && actionRows.size() <= MAX_ACTIONS,
                "Workbench Home must contain one to five ordered actions");
        List<Action> actions = new ArrayList<>();
        Set<String> actionIds = new HashSet<>();
        for (JsonElement element : actionRows) {
            JsonObject action = object(element, "Home action");
            String id = string(action, "id");
            require(actionIds.add(id), "Workbench Home repeats action " + id);
            boolean available = bool(action, "available");
            List<String> blockers = strings(array(action, "blockers"), "action blocker");
            List<String> argv = nullableStrings(action, "argv", "action argv");
            String reason = nullableString(action, "unavailable_reason");
            if (available) {
                require(argv != null && !argv.isEmpty() && blockers.isEmpty() && reason == null,
                        "Available Workbench Home action is not executable as returned: " + id);
            } else {
                require(argv == null && !blockers.isEmpty() && reason != null,
                        "Blocked Workbench Home action lacks its returned reason: " + id);
            }
            actions.add(new Action(
                    id,
                    string(action, "title"),
                    string(action, "purpose"),
                    available,
                    argv,
                    blockers,
                    reason
            ));
        }

        JsonArray gapRows = array(root, "gaps");
        require(gapRows.size() <= MAX_ROWS, "Workbench Home has too many gaps");
        List<Gap> gaps = new ArrayList<>();
        for (JsonElement element : gapRows) {
            JsonObject gap = object(element, "Home gap");
            gaps.add(new Gap(string(gap, "id"), string(gap, "summary")));
        }
        List<String> limitations = strings(array(root, "limitations"), "Home limitation");
        return new WorkspaceHome(
                json, workspace, status, problems, actions, gaps, limitations
        );
    }

    private static @NotNull JsonElement required(
            @NotNull JsonObject value, @NotNull String key
    ) {
        require(value.has(key), "Workbench Home is missing " + key);
        return value.get(key);
    }

    private static @NotNull JsonObject object(
            @NotNull JsonElement value, @NotNull String label
    ) {
        require(value.isJsonObject(), "Workbench " + label + " must be an object");
        return value.getAsJsonObject();
    }

    private static @NotNull JsonArray array(
            @NotNull JsonObject value, @NotNull String key
    ) {
        JsonElement member = required(value, key);
        require(member.isJsonArray(), "Workbench Home " + key + " must be an array");
        return member.getAsJsonArray();
    }

    private static @NotNull String string(
            @NotNull JsonObject value, @NotNull String key
    ) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isString(),
                "Workbench Home " + key + " must be a string");
        String result = member.getAsString();
        require(!result.isBlank() && result.indexOf('\0') < 0,
                "Workbench Home " + key + " is blank or unsafe");
        return result;
    }

    private static @Nullable String nullableString(
            @NotNull JsonObject value, @NotNull String key
    ) {
        JsonElement member = required(value, key);
        if (member.isJsonNull()) {
            return null;
        }
        return string(value, key);
    }

    private static boolean bool(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isBoolean(),
                "Workbench Home " + key + " must be boolean");
        return member.getAsBoolean();
    }

    private static int integer(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isNumber(),
                "Workbench Home " + key + " must be an integer");
        try {
            return member.getAsInt();
        } catch (RuntimeException error) {
            throw new IllegalArgumentException(
                    "Workbench Home " + key + " must be an integer", error
            );
        }
    }

    private static @NotNull List<String> strings(
            @NotNull JsonArray values, @NotNull String label
    ) {
        require(values.size() <= MAX_ROWS, "Workbench Home has too many " + label + " rows");
        List<String> result = new ArrayList<>();
        for (JsonElement value : values) {
            require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString(),
                    "Workbench Home " + label + " must be a string");
            String text = value.getAsString();
            require(!text.isBlank() && text.indexOf('\0') < 0,
                    "Workbench Home " + label + " is blank or unsafe");
            result.add(text);
        }
        return List.copyOf(result);
    }

    private static @Nullable List<String> nullableStrings(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label
    ) {
        JsonElement member = required(value, key);
        return member.isJsonNull() ? null : strings(array(value, key), label);
    }

    private static void equal(
            @NotNull Object actual, @NotNull Object expected, @NotNull String message
    ) {
        require(actual.equals(expected), message);
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }

    public @NotNull String rawJson() {
        return rawJson;
    }

    public @NotNull Workspace workspace() {
        return workspace;
    }

    public @NotNull String status() {
        return status;
    }

    public @NotNull List<Problem> problems() {
        return problems;
    }

    public @NotNull List<Action> actions() {
        return actions;
    }

    public @NotNull List<Gap> gaps() {
        return gaps;
    }

    public @NotNull List<String> limitations() {
        return limitations;
    }

    public record Workspace(
            @NotNull String requestedPath,
            @NotNull String root,
            @NotNull String displayName,
            @NotNull String kind,
            @NotNull String recognition
    ) {
    }

    public record Problem(
            @NotNull String id,
            @NotNull String severity,
            @NotNull String title,
            @NotNull String detail
    ) {
    }

    public record Action(
            @NotNull String id,
            @NotNull String title,
            @NotNull String purpose,
            boolean available,
            @Nullable List<String> argv,
            @NotNull List<String> blockers,
            @Nullable String unavailableReason
    ) {
        public Action {
            argv = argv == null ? null : List.copyOf(argv);
            blockers = List.copyOf(blockers);
        }
    }

    public record Gap(@NotNull String id, @NotNull String summary) {
    }
}
