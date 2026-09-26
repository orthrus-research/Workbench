package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Reads and saves the workspace's durable Feature state-root choice through Core. */
final class FeatureStateRootPolicy {
    private static final int MAX_OUTPUT = 1024 * 1024;
    private static final int MAX_PATH = 32 * 1024;
    private static final Pattern POLICY_ID = Pattern.compile(
            "workbench-state-root-policy:sha256:[0-9a-f]{64}"
    );
    private static final Pattern RECORD_ID = Pattern.compile(
            "workbench-state-root-selections:sha256:[0-9a-f]{64}"
    );
    private static final Pattern WORKSPACE_ID = Pattern.compile(
            "workbench-workspace-v1:[0-9a-f]{32}"
    );
    private static final Set<String> FIELDS = Set.of(
            "format", "schema_version", "configuration_home", "workspace", "workspace_id",
            "role", "state_root", "source", "selections_record_id", "policy_id"
    );

    record Choice(@NotNull String stateRoot, @NotNull String policyId,
                  @NotNull String source) {
    }

    private FeatureStateRootPolicy() {
    }

    static @NotNull Choice resolve(@NotNull CoreLaunch launch,
                                   @NotNull String workspace,
                                   @Nullable String expectedPolicyId) throws IOException {
        String selected = launch.commandPath(workspace, "Feature workspace");
        List<String> arguments = new ArrayList<>(List.of(
                "settings", "state-root", "resolve", selected, "feature"
        ));
        if (expectedPolicyId != null) {
            requireId(expectedPolicyId);
            arguments.addAll(List.of("--expected-policy-id", expectedPolicyId));
        }
        arguments.add("--json");
        return parse(CommandProcess.capture(launch, arguments, MAX_OUTPUT, 30, workspace),
                launch, selected);
    }

    static @NotNull Choice save(@NotNull CoreLaunch launch, @NotNull String workspace,
                                @Nullable String selectedRoot,
                                @NotNull String expectedPolicyId) throws IOException {
        requireId(expectedPolicyId);
        String selected = launch.commandPath(workspace, "Feature workspace");
        boolean clear = selectedRoot == null || selectedRoot.isBlank();
        List<String> arguments = new ArrayList<>(List.of(
                "settings", "state-root", clear ? "clear" : "select", selected, "feature"
        ));
        if (!clear) {
            arguments.add(launch.commandPath(selectedRoot.trim(), "Feature state root"));
        }
        arguments.addAll(List.of("--expected-policy-id", expectedPolicyId, "--json"));
        return parse(CommandProcess.capture(launch, arguments, MAX_OUTPUT, 30, workspace),
                launch, selected);
    }

    static @NotNull Choice parse(@NotNull String output, @NotNull CoreLaunch launch,
                                 @NotNull String selectedWorkspace) {
        JsonElement parsed = JsonParser.parseString(output);
        if (!parsed.isJsonObject()) {
            throw new IllegalArgumentException("Core Feature state-root policy is not an object");
        }
        JsonObject value = parsed.getAsJsonObject();
        if (!value.keySet().equals(FIELDS)
                || !"workbench-state-root-policy-v1".equals(text(value, "format"))
                || value.get("schema_version").getAsInt() != 1
                || !"feature".equals(text(value, "role"))
                || !selectedWorkspace.equals(text(value, "workspace"))
                || !Set.of("platform-default", "environment", "user-selection")
                .contains(text(value, "source"))) {
            throw new IllegalArgumentException("Core Feature state-root policy has another scope");
        }
        JsonElement workspaceId = value.get("workspace_id");
        if (!workspaceId.isJsonNull()
                && !WORKSPACE_ID.matcher(text(value, "workspace_id")).matches()) {
            throw new IllegalArgumentException("Core Feature workspace identity is invalid");
        }
        if (!RECORD_ID.matcher(text(value, "selections_record_id")).matches()) {
            throw new IllegalArgumentException("Core Feature selections identity is invalid");
        }
        String policyId = text(value, "policy_id");
        requireId(policyId);
        bounded(text(value, "configuration_home"));
        String coreRoot = bounded(text(value, "state_root"));
        String hostRoot = coreRoot;
        if (launch.host().equals("windows-wsl")) {
            if (!coreRoot.startsWith("/") || coreRoot.startsWith("//")
                    || coreRoot.endsWith("/") || coreRoot.contains("\\")) {
                throw new IllegalArgumentException("Core selected an invalid WSL Feature state root");
            }
            for (String part : coreRoot.substring(1).split("/", -1)) {
                if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                    throw new IllegalArgumentException("Core selected an invalid WSL Feature state root");
                }
            }
            hostRoot = "\\\\wsl.localhost\\" + launch.distribution()
                    + coreRoot.replace('/', '\\');
            if (!launch.commandPath(hostRoot, "Core selected Feature state root").equals(coreRoot)) {
                throw new IllegalArgumentException("Core Feature state-root mapping changed");
            }
        }
        return new Choice(hostRoot, policyId, text(value, "source"));
    }

    private static @NotNull String text(JsonObject value, String key) {
        JsonElement member = value.get(key);
        if (member == null || !member.isJsonPrimitive()
                || !member.getAsJsonPrimitive().isString()) {
            throw new IllegalArgumentException("Core Feature state-root field is invalid: " + key);
        }
        return member.getAsString();
    }

    private static @NotNull String bounded(String value) {
        if (value.isEmpty() || value.indexOf('\0') >= 0
                || value.getBytes(StandardCharsets.UTF_8).length > MAX_PATH) {
            throw new IllegalArgumentException("Core Feature state-root path is invalid");
        }
        return value;
    }

    private static void requireId(String value) {
        if (!POLICY_ID.matcher(value).matches()) {
            throw new IllegalArgumentException("Core Feature state-root policy ID is invalid");
        }
    }
}
