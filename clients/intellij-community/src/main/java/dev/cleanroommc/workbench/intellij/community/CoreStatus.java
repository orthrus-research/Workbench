package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Read-only installed-core and setup readiness shown by first-run IDE surfaces. */
public record CoreStatus(
        @NotNull String currentVersion,
        @NotNull Setup setup
) {
    private static final int MAXIMUM_STATUS_BYTES = 1024 * 1024;
    private static final Pattern COMPONENT_VERSION = Pattern.compile(
            "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:(?:a|b|rc)[1-9][0-9]*)?$"
    );

    public record Setup(
            @NotNull String state,
            boolean configured,
            boolean ready,
            @NotNull List<String> blockers,
            @NotNull List<String> managedInstallsAvailable
    ) {
        public Setup {
            blockers = List.copyOf(blockers);
            managedInstallsAvailable = List.copyOf(managedInstallsAvailable);
        }

        static @NotNull Setup required() {
            return new Setup("attention", false, false, List.of(), List.of());
        }
    }

    public static @NotNull CoreStatus probe(
            @NotNull CoreLaunch launch,
            String workingDirectory
    ) throws IOException {
        String versionJson = CommandProcess.capture(
                launch, List.of("version", "--json"), MAXIMUM_STATUS_BYTES, 10,
                workingDirectory
        );
        Version version = parseVersion(versionJson);
        Setup setup;
        CommandProcess.Observation setupObservation = CommandProcess.captureObserved(
                launch, List.of("setup", "--check", "--json"), MAXIMUM_STATUS_BYTES, 60,
                workingDirectory
        );
        if (setupObservation.exitCode() != 0 && setupObservation.exitCode() != 1) {
            setup = Setup.required();
        } else if (!setupObservation.stderr().isBlank()) {
            setup = Setup.required();
        } else {
            try {
                setup = parseSetup(setupObservation.stdout());
            } catch (IllegalArgumentException error) {
                setup = Setup.required();
            }
        }
        return new CoreStatus(
                version.currentVersion(), setup
        );
    }

    static @NotNull Version parseVersion(@NotNull String json) {
        JsonObject root = parseObject(json, "Workbench version response");
        exactKeys(root, Set.of("component_id", "version"), "Workbench version response");
        equal(text(root, "component_id"), "workbench-core",
                "selected command is not Workbench core");
        String currentVersion = text(root, "version");
        require(COMPONENT_VERSION.matcher(currentVersion).matches(),
                "Workbench version must be a native component version");
        return new Version(currentVersion);
    }

    static @NotNull Setup parseSetup(@NotNull String json) {
        JsonObject root = parseObject(json, "Workbench setup check");
        equal(text(root, "format"), "workbench-setup-check-v1",
                "unsupported Workbench setup check");
        equal(integer(root, "schema_version"), 1, "unsupported Workbench setup schema");
        String state = text(root, "state");
        require(Set.of("ready", "installable", "attention").contains(state),
                "unsupported Workbench setup state");
        JsonElement configuredValue = root.get("configured");
        require(configuredValue != null && configuredValue.isJsonPrimitive()
                        && configuredValue.getAsJsonPrimitive().isBoolean(),
                "Workbench setup configured state must be boolean");
        boolean configured = configuredValue.getAsBoolean();
        return new Setup(
                state,
                configured,
                configured && state.equals("ready"),
                strings(root, "blockers"),
                strings(root, "managed_installs_available")
        );
    }

    record Version(
            @NotNull String currentVersion
    ) {
    }

    private static @NotNull JsonObject parseObject(@NotNull String json, @NotNull String label) {
        int bytes = json.getBytes(StandardCharsets.UTF_8).length;
        require(bytes >= 1 && bytes <= MAXIMUM_STATUS_BYTES,
                label + " size is outside the supported bound");
        try {
            JsonElement value = JsonParser.parseString(json);
            require(value.isJsonObject(), label + " must be an object");
            return value.getAsJsonObject();
        } catch (RuntimeException error) {
            throw new IllegalArgumentException(label + " is invalid JSON", error);
        }
    }

    private static void exactKeys(
            @NotNull JsonObject value, @NotNull Set<String> expected, @NotNull String label
    ) {
        require(value.keySet().equals(expected), label + " fields are unsupported");
    }

    private static @NotNull String text(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = value.get(key);
        require(member != null && member.isJsonPrimitive()
                        && member.getAsJsonPrimitive().isString(),
                key + " must be a string");
        String parsed = member.getAsString();
        require(!parsed.isEmpty() && parsed.indexOf('\0') < 0
                        && parsed.getBytes(StandardCharsets.UTF_8).length <= 16 * 1024,
                key + " must be a bounded nonempty string");
        return parsed;
    }

    private static int integer(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = value.get(key);
        require(member != null && member.isJsonPrimitive()
                        && member.getAsJsonPrimitive().isNumber(),
                key + " must be an integer");
        try {
            return member.getAsInt();
        } catch (RuntimeException error) {
            throw new IllegalArgumentException(key + " must be an integer", error);
        }
    }

    private static @NotNull List<String> strings(
            @NotNull JsonObject value,
            @NotNull String key
    ) {
        JsonElement member = value.get(key);
        require(member != null && member.isJsonArray(), key + " must be an array");
        JsonArray array = member.getAsJsonArray();
        require(array.size() <= 1024, key + " exceeds the supported bound");
        List<String> result = new ArrayList<>();
        for (JsonElement item : array) {
            require(item.isJsonPrimitive() && item.getAsJsonPrimitive().isString(),
                    key + " must contain only strings");
            String parsed = item.getAsString();
            require(!parsed.isEmpty() && parsed.indexOf('\0') < 0,
                    key + " contains an invalid value");
            result.add(parsed);
        }
        return List.copyOf(result);
    }

    private static void equal(Object actual, Object expected, String message) {
        require(expected.equals(actual), message);
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }
}
