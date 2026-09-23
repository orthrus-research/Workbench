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
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict, bounded consumer for the Shell-owned live command catalog. */
public final class CommandCatalog {
    public static final String FORMAT = "workbench-live-console-command-catalog-v2";
    public static final int MAX_BYTES = 16 * 1024 * 1024;
    private static final int MAX_TEXT_BYTES = 16 * 1024;
    private static final Pattern IDENTIFIER = Pattern.compile("^[a-z][a-z0-9.-]*$");
    private static final Pattern OPTION_KEY = Pattern.compile("^[a-z][a-z0-9_]*$");
    private static final Pattern DIGEST = Pattern.compile("^sha256:[0-9a-f]{64}$");
    private static final Set<String> AVAILABILITY = Set.of(
            "available", "experimental", "unavailable"
    );
    private static final Set<String> RISKS = Set.of(
            "read-only", "writes-output", "mutating", "destructive"
    );
    private static final Set<String> PREVIEWS = Set.of(
            "none", "append-show", "plan-then-apply", "inert-only", "confirm-and-show"
    );
    private static final Set<String> OPTION_KINDS = Set.of(
            "text", "path", "integer", "boolean", "choice", "json"
    );

    private final String catalogDigest;
    private final List<Suite> suites;
    private final List<Command> commands;

    private CommandCatalog(
            @NotNull String catalogDigest,
            @NotNull List<Suite> suites,
            @NotNull List<Command> commands
    ) {
        this.catalogDigest = catalogDigest;
        this.suites = List.copyOf(suites);
        this.commands = List.copyOf(commands);
    }

    public static @NotNull CommandCatalog parse(@NotNull String json) {
        int encodedSize = json.getBytes(StandardCharsets.UTF_8).length;
        require(encodedSize >= 1 && encodedSize <= MAX_BYTES,
                "Workbench command catalog size is outside the supported bound");
        final JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (RuntimeException error) {
            throw new IllegalArgumentException("Workbench command catalog is invalid JSON", error);
        }
        require(parsed.isJsonObject(), "Workbench command catalog must be a JSON object");
        JsonObject root = parsed.getAsJsonObject();
        exactKeys(root, Set.of("format_version", "catalog_digest", "suites", "commands"), Set.of(),
                "Workbench command catalog");
        equal(string(root, "format_version"), FORMAT,
                "Workbench returned an unsupported command catalog");

        JsonArray suiteValues = array(root, "suites");
        JsonArray commandValues = array(root, "commands");
        require(suiteValues.size() >= 1 && suiteValues.size() <= 256,
                "Workbench suite catalog size is outside the supported bound");
        require(commandValues.size() >= 1 && commandValues.size() <= 4096,
                "Workbench command catalog size is outside the supported bound");

        List<Suite> suites = new ArrayList<>();
        Set<String> suiteIds = new LinkedHashSet<>();
        for (JsonElement value : suiteValues) {
            Suite suite = suite(value);
            require(suiteIds.add(suite.suiteId()),
                    "Workbench command catalog contains duplicate suite IDs");
            suites.add(suite);
        }

        List<Command> commands = new ArrayList<>();
        Set<String> commandIds = new HashSet<>();
        for (JsonElement value : commandValues) {
            Command command = command(value, suiteIds);
            require(commandIds.add(command.commandId()),
                    "Workbench command catalog contains duplicate command IDs");
            commands.add(command);
        }
        for (Suite suite : suites) {
            long actual = commands.stream()
                    .filter(command -> command.suiteId().equals(suite.suiteId()))
                    .count();
            require(actual == suite.commandCount(),
                    "Workbench suite " + suite.suiteId() + " has a stale command count");
        }
        return new CommandCatalog(digest(root, "catalog_digest"), suites, commands);
    }

    public @NotNull String catalogDigest() {
        return catalogDigest;
    }

    public @NotNull List<Suite> suites() {
        return suites;
    }

    public @NotNull List<Command> commands() {
        return commands;
    }

    public @NotNull List<Command> commandsForSuite(@NotNull String suiteId) {
        require(IDENTIFIER.matcher(suiteId).matches(), "Workbench suite ID is invalid");
        require(suites.stream().anyMatch(suite -> suite.suiteId().equals(suiteId)),
                "Workbench suite is not present in the catalog: " + suiteId);
        return commands.stream()
                .filter(command -> command.suiteId().equals(suiteId))
                .toList();
    }

    private static @NotNull Suite suite(@NotNull JsonElement value) {
        JsonObject suite = object(value, "catalog suite");
        exactKeys(suite, Set.of(
                "suite_id", "title", "summary", "authority", "availability", "command_count"
        ), Set.of(), "catalog suite");
        return new Suite(
                identifier(suite, "suite_id", false, "suite ID"),
                string(suite, "title"),
                string(suite, "summary"),
                string(suite, "authority"),
                member(suite, "availability", AVAILABILITY,
                        "catalog availability is unsupported"),
                nonnegativeInteger(suite, "command_count")
        );
    }

    private static @NotNull Command command(
            @NotNull JsonElement value,
            @NotNull Set<String> suiteIds
    ) {
        JsonObject command = object(value, "catalog command");
        exactKeys(command, Set.of(
                "command_id", "suite_id", "title", "summary", "authority", "risk", "preview",
                "availability", "documentation", "document", "limitations", "command_preview",
                "action_digest", "options"
        ), Set.of(), "catalog command");
        String commandId = identifier(command, "command_id", false, "command ID");
        String suiteId = identifier(command, "suite_id", false, "command suite ID");
        require(suiteIds.contains(suiteId),
                "Workbench command references unknown suite " + suiteId);
        JsonArray limitationsValue = array(command, "limitations");
        JsonArray optionsValue = array(command, "options");
        require(limitationsValue.size() <= 256 && optionsValue.size() <= 256,
                "Workbench command metadata is outside the supported bound");
        List<Option> options = new ArrayList<>();
        Set<String> optionKeys = new HashSet<>();
        for (JsonElement optionValue : optionsValue) {
            Option option = option(optionValue);
            require(optionKeys.add(option.key()),
                    "Workbench command " + commandId + " has duplicate option keys");
            options.add(option);
        }
        return new Command(
                commandId,
                suiteId,
                string(command, "title"),
                string(command, "summary"),
                string(command, "authority"),
                member(command, "risk", RISKS, "catalog risk is unsupported"),
                member(command, "preview", PREVIEWS, "catalog preview strategy is unsupported"),
                member(command, "availability", AVAILABILITY,
                        "catalog availability is unsupported"),
                nullableString(command, "documentation"),
                nullableString(command, "document"),
                strings(limitationsValue, "command limitation"),
                string(command, "command_preview"),
                digest(command, "action_digest"),
                options
        );
    }

    private static @NotNull Option option(@NotNull JsonElement value) {
        JsonObject option = object(value, "catalog option");
        exactKeys(option, Set.of(
                "key", "label", "help", "flags", "kind", "required", "positional", "choices",
                "nargs", "repeat", "placement", "sensitive", "required_group", "console_managed"
        ), Set.of("default", "mutex_group", "metavar"), "catalog option");
        String key = identifier(option, "key", true, "option key");
        List<String> flags = strings(array(option, "flags"), "option flag");
        List<String> choices = strings(array(option, "choices"), "option choice");
        require(flags.size() <= 16 && choices.size() <= 4096,
                "Workbench option metadata is outside the supported bound");
        require(new HashSet<>(flags).size() == flags.size()
                        && new HashSet<>(choices).size() == choices.size(),
                "Workbench option " + key + " contains duplicate metadata");
        String kind = member(option, "kind", OPTION_KINDS,
                "Workbench option has an unsupported kind");
        boolean positional = bool(option, "positional");
        require(positional != !flags.isEmpty(),
                "Workbench option " + key + " must be positional or flagged, but not both");
        require(!kind.equals("choice") || !choices.isEmpty(),
                "Workbench choice option " + key + " has no choices");
        boolean requiredGroup = bool(option, "required_group");
        String mutexGroup = nullableOptionalString(option, "mutex_group");
        require(!requiredGroup || mutexGroup != null,
                "Workbench option " + key + " has a required group without a mutex group");
        JsonElement nargsValue = required(option, "nargs");
        String nargs;
        if (nargsValue.isJsonPrimitive() && nargsValue.getAsJsonPrimitive().isNumber()) {
            nargs = Integer.toString(nonnegativeInteger(nargsValue, "option nargs"));
        } else {
            nargs = boundedString(nargsValue, "option nargs");
        }
        JsonElement defaultValue = option.has("default")
                ? option.get("default").deepCopy()
                : null;
        return new Option(
                key,
                string(option, "label"),
                string(option, "help"),
                flags,
                kind,
                bool(option, "required"),
                positional,
                choices,
                nargs,
                bool(option, "repeat"),
                nonnegativeInteger(option, "placement"),
                bool(option, "sensitive"),
                requiredGroup,
                bool(option, "console_managed"),
                defaultValue,
                mutexGroup,
                nullableOptionalString(option, "metavar")
        );
    }

    private static @NotNull JsonObject object(@NotNull JsonElement value, @NotNull String label) {
        require(value.isJsonObject(), label + " must be an object");
        return value.getAsJsonObject();
    }

    private static @NotNull JsonArray array(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonArray(), key + " must be an array");
        return member.getAsJsonArray();
    }

    private static @NotNull JsonElement required(@NotNull JsonObject value, @NotNull String key) {
        require(value.has(key), "missing required catalog member: " + key);
        return value.get(key);
    }

    private static @NotNull List<String> strings(@NotNull JsonArray values, @NotNull String label) {
        List<String> result = new ArrayList<>();
        for (JsonElement value : values) {
            result.add(boundedString(value, label));
        }
        return List.copyOf(result);
    }

    private static @NotNull String string(@NotNull JsonObject value, @NotNull String key) {
        return boundedString(required(value, key), key);
    }

    private static @NotNull String digest(@NotNull JsonObject value, @NotNull String key) {
        String parsed = string(value, key);
        require(DIGEST.matcher(parsed).matches(), key + " is not a canonical SHA-256 ID");
        return parsed;
    }

    private static @Nullable String nullableString(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        return member.isJsonNull() ? null : boundedString(member, key);
    }

    private static @Nullable String nullableOptionalString(
            @NotNull JsonObject value,
            @NotNull String key
    ) {
        if (!value.has(key) || value.get(key).isJsonNull()) {
            return null;
        }
        return boundedString(value.get(key), key);
    }

    private static @NotNull String boundedString(@NotNull JsonElement value, @NotNull String label) {
        require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString(),
                label + " must be a string");
        String parsed = value.getAsString();
        int byteLength = parsed.getBytes(StandardCharsets.UTF_8).length;
        require(byteLength >= 1 && byteLength <= MAX_TEXT_BYTES,
                label + " must be a bounded nonempty string");
        for (int offset = 0; offset < parsed.length(); ) {
            int codePoint = parsed.codePointAt(offset);
            require(!(codePoint <= 0x08 || (codePoint >= 0x0b && codePoint <= 0x1f)),
                    label + " contains an unsupported control character");
            offset += Character.charCount(codePoint);
        }
        return parsed;
    }

    private static @NotNull String identifier(
            @NotNull JsonObject value,
            @NotNull String key,
            boolean option,
            @NotNull String label
    ) {
        String parsed = string(value, key);
        require((option ? OPTION_KEY : IDENTIFIER).matcher(parsed).matches(),
                label + " is invalid");
        return parsed;
    }

    private static boolean bool(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isBoolean(),
                key + " must be boolean");
        return member.getAsBoolean();
    }

    private static int nonnegativeInteger(@NotNull JsonObject value, @NotNull String key) {
        return nonnegativeInteger(required(value, key), key);
    }

    private static int nonnegativeInteger(@NotNull JsonElement value, @NotNull String label) {
        require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isNumber(),
                label + " must be a nonnegative integer");
        try {
            int parsed = value.getAsBigDecimal().intValueExact();
            require(parsed >= 0, label + " must be a nonnegative integer");
            return parsed;
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalArgumentException(label + " must be a nonnegative integer", error);
        }
    }

    private static @NotNull String member(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull Set<String> choices,
            @NotNull String message
    ) {
        String parsed = string(value, key);
        require(choices.contains(parsed), message + ": " + parsed);
        return parsed;
    }

    private static void exactKeys(
            @NotNull JsonObject value,
            @NotNull Set<String> required,
            @NotNull Set<String> optional,
            @NotNull String label
    ) {
        Set<String> actual = value.keySet();
        Set<String> missing = new LinkedHashSet<>(required);
        missing.removeAll(actual);
        Set<String> extra = new LinkedHashSet<>(actual);
        extra.removeAll(required);
        extra.removeAll(optional);
        require(missing.isEmpty() && extra.isEmpty(),
                label + " keys differ; missing=" + missing + "; extra=" + extra);
    }

    private static void equal(Object actual, Object expected, String message) {
        require(expected.equals(actual), message);
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }

    public record Suite(
            @NotNull String suiteId,
            @NotNull String title,
            @NotNull String summary,
            @NotNull String authority,
            @NotNull String availability,
            int commandCount
    ) {
    }

    public record Command(
            @NotNull String commandId,
            @NotNull String suiteId,
            @NotNull String title,
            @NotNull String summary,
            @NotNull String authority,
            @NotNull String risk,
            @NotNull String preview,
            @NotNull String availability,
            @Nullable String documentation,
            @Nullable String document,
            @NotNull List<String> limitations,
            @NotNull String commandPreview,
            @NotNull String actionDigest,
            @NotNull List<Option> options
    ) {
        public Command {
            limitations = List.copyOf(limitations);
            options = List.copyOf(options);
        }

        public boolean isDocument() {
            return document != null;
        }
    }

    public record Option(
            @NotNull String key,
            @NotNull String label,
            @NotNull String help,
            @NotNull List<String> flags,
            @NotNull String kind,
            boolean required,
            boolean positional,
            @NotNull List<String> choices,
            @NotNull String nargs,
            boolean repeat,
            int placement,
            boolean sensitive,
            boolean requiredGroup,
            boolean consoleManaged,
            @Nullable JsonElement defaultValue,
            @Nullable String mutexGroup,
            @Nullable String metavar
    ) {
        public Option {
            flags = List.copyOf(flags);
            choices = List.copyOf(choices);
            defaultValue = defaultValue == null ? null : defaultValue.deepCopy();
        }

        public boolean multiple() {
            return repeat || !("one".equals(nargs) || "optional".equals(nargs));
        }
    }
}
