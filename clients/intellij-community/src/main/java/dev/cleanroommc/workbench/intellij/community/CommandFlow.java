package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

/** Digest-bound bridge from native catalog inputs to the Shell owner. */
public final class CommandFlow {
    private static final Pattern OPTION_KEY = Pattern.compile("^[a-z][a-z0-9_]*$");
    private static final Pattern DIGEST = Pattern.compile("^sha256:[0-9a-f]{64}$");
    private static final Set<String> OWNER_PREVIEW_STRATEGIES = Set.of(
            "append-show", "plan-then-apply", "confirm-and-show"
    );
    private static final Set<String> RISKS = Set.of(
            "read-only", "writes-output", "mutating", "destructive"
    );
    private static final Set<String> PREVIEWS = Set.of(
            "none", "append-show", "plan-then-apply", "inert-only", "confirm-and-show"
    );
    private static final Set<String> PREVIEW_INTENTS = Set.of("preview", "inert", "execute");
    private static final Gson JSON = new GsonBuilder().disableHtmlEscaping().create();
    private static final int MAX_REVIEW_BYTES = 2 * 1024 * 1024;
    private static final int MAX_REVIEW_TEXT_BYTES = 64 * 1024;

    private final String catalogDigest;
    private final CommandCatalog.Command command;
    private final List<String> boundBase;

    private CommandFlow(
            @NotNull String catalogDigest,
            @NotNull CommandCatalog.Command command,
            @NotNull List<String> boundBase
    ) {
        this.catalogDigest = catalogDigest;
        this.command = command;
        this.boundBase = List.copyOf(boundBase);
    }

    public static @NotNull CommandFlow compose(
            @NotNull CoreLaunch launch,
            @NotNull String catalogDigest,
            @NotNull CommandCatalog.Command command,
            @NotNull Map<String, JsonElement> values
    ) {
        requireDigest(catalogDigest, "catalog digest");
        requireDigest(command.actionDigest(), "action digest");
        Map<String, CommandCatalog.Option> declared = new LinkedHashMap<>();
        for (CommandCatalog.Option option : command.options()) {
            if (!option.consoleManaged()) {
                declared.put(option.key(), option);
            }
        }
        List<String> base = new ArrayList<>(List.of("console", "run", command.commandId()));
        for (Map.Entry<String, JsonElement> value : new LinkedHashMap<>(values).entrySet()) {
            if (!OPTION_KEY.matcher(value.getKey()).matches()) {
                throw new IllegalArgumentException("unsafe Workbench option key: " + value.getKey());
            }
            CommandCatalog.Option option = declared.get(value.getKey());
            if (option == null) {
                throw new IllegalArgumentException("Workbench option is not declared: " + value.getKey());
            }
            if (value.getValue() == null || value.getValue().isJsonNull()) {
                throw new IllegalArgumentException(
                        "Workbench option value cannot be null: " + value.getKey()
                );
            }
            JsonElement normalized = option.kind().equals("path")
                    ? normalizePath(launch, value.getValue(), option.label())
                    : value.getValue().deepCopy();
            base.add("--set");
            base.add(value.getKey() + ":=" + JSON.toJson(normalized));
        }
        base.add("--expect-catalog-digest");
        base.add(catalogDigest);
        base.add("--expect-action-digest");
        base.add(command.actionDigest());
        return new CommandFlow(catalogDigest, command, base);
    }

    public @NotNull List<String> commandReviewArguments() {
        return appended(boundBase, "--review-json");
    }

    public @NotNull Bound bindReview(@NotNull String json) {
        CommandReview review = CommandReview.parse(json, catalogDigest, command);
        List<String> base = new ArrayList<>(boundBase);
        base.add("--expect-review-digest");
        base.add(review.reviewDigest());
        return new Bound(
                review,
                OWNER_PREVIEW_STRATEGIES.contains(command.preview()) ? base : null,
                appended(base, "--execute")
        );
    }

    public static boolean hasOwnerPreview(@NotNull String preview) {
        return OWNER_PREVIEW_STRATEGIES.contains(preview);
    }

    private static @NotNull JsonElement normalizePath(
            @NotNull CoreLaunch launch,
            @NotNull JsonElement value,
            @NotNull String label
    ) {
        if (value.isJsonPrimitive() && value.getAsJsonPrimitive().isString()) {
            return new JsonPrimitive(launch.commandPath(value.getAsString(), label));
        }
        if (value.isJsonArray()) {
            JsonArray result = new JsonArray();
            for (JsonElement item : value.getAsJsonArray()) {
                result.add(normalizePath(launch, item, label));
            }
            return result;
        }
        throw new IllegalArgumentException(label + " must contain only path strings");
    }

    private static @NotNull List<String> appended(
            @NotNull List<String> source,
            @NotNull String value
    ) {
        List<String> result = new ArrayList<>(source);
        result.add(value);
        return List.copyOf(result);
    }

    private static void requireDigest(@NotNull String value, @NotNull String label) {
        if (!DIGEST.matcher(value).matches()) {
            throw new IllegalArgumentException(label + " is not a canonical SHA-256 ID");
        }
    }

    public record Bound(
            @NotNull CommandReview review,
            @Nullable List<String> ownerPreviewArguments,
            @NotNull List<String> executeArguments
    ) {
        public Bound {
            ownerPreviewArguments = ownerPreviewArguments == null
                    ? null
                    : List.copyOf(ownerPreviewArguments);
            executeArguments = List.copyOf(executeArguments);
        }

        public @NotNull Optional<List<String>> ownerPreviewArgumentsOptional() {
            return Optional.ofNullable(ownerPreviewArguments);
        }
    }

    /** Exact Shell-produced review binding for one selected catalog action. */
    public record CommandReview(
            @NotNull String catalogDigest,
            @NotNull String actionDigest,
            @NotNull String reviewDigest,
            @NotNull String commandId,
            @NotNull String risk,
            @NotNull String preview,
            @NotNull String previewIntent,
            @NotNull String executeIntent,
            @NotNull String previewCommand,
            @NotNull String executeCommand
    ) {
        private static final String FORMAT = "workbench-live-console-command-review-v2";

        static @NotNull CommandReview parse(
                @NotNull String json,
                @NotNull String expectedCatalogDigest,
                @NotNull CommandCatalog.Command expectedCommand
        ) {
            int bytes = json.getBytes(StandardCharsets.UTF_8).length;
            require(bytes >= 1 && bytes <= MAX_REVIEW_BYTES,
                    "Workbench command review size is outside the supported bound");
            final JsonElement value;
            try {
                value = JsonParser.parseString(json);
            } catch (RuntimeException error) {
                throw new IllegalArgumentException("Workbench command review is invalid JSON", error);
            }
            require(value.isJsonObject(), "Workbench command review must be an object");
            JsonObject review = value.getAsJsonObject();
            exactKeys(review, Set.of(
                    "format_version", "catalog_digest", "action_digest", "review_digest",
                    "command_id", "risk", "preview", "preview_intent", "execute_intent",
                    "preview_command", "execute_command"
            ));
            equal(string(review, "format_version"), FORMAT,
                    "Workbench returned an unsupported command review");
            CommandReview parsed = new CommandReview(
                    digest(review, "catalog_digest"),
                    digest(review, "action_digest"),
                    digest(review, "review_digest"),
                    string(review, "command_id"),
                    member(review, "risk", RISKS),
                    member(review, "preview", PREVIEWS),
                    member(review, "preview_intent", PREVIEW_INTENTS),
                    member(review, "execute_intent", Set.of("execute")),
                    string(review, "preview_command"),
                    string(review, "execute_command")
            );
            require(parsed.catalogDigest().equals(expectedCatalogDigest)
                            && parsed.actionDigest().equals(expectedCommand.actionDigest())
                            && parsed.commandId().equals(expectedCommand.commandId())
                            && parsed.risk().equals(expectedCommand.risk())
                            && parsed.preview().equals(expectedCommand.preview()),
                    "Workbench command review does not match the selected catalog action");
            String expectedPreviewIntent = switch (expectedCommand.preview()) {
                case "none" -> "execute";
                case "inert-only" -> "inert";
                default -> "preview";
            };
            require(parsed.previewIntent().equals(expectedPreviewIntent),
                    "Workbench command review intent contradicts its preview strategy");
            return parsed;
        }

        private static @NotNull String member(
                @NotNull JsonObject value,
                @NotNull String key,
                @NotNull Set<String> allowed
        ) {
            String parsed = string(value, key);
            require(allowed.contains(parsed), key + " is unsupported");
            return parsed;
        }

        private static @NotNull String digest(@NotNull JsonObject value, @NotNull String key) {
            String parsed = string(value, key);
            require(DIGEST.matcher(parsed).matches(), key + " is not a canonical SHA-256 ID");
            return parsed;
        }

        private static @NotNull String string(@NotNull JsonObject value, @NotNull String key) {
            require(value.has(key), "Workbench command review is missing " + key);
            JsonElement member = value.get(key);
            require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isString(),
                    key + " must be a string");
            String parsed = member.getAsString();
            int bytes = parsed.getBytes(StandardCharsets.UTF_8).length;
            require(bytes >= 1 && bytes <= MAX_REVIEW_TEXT_BYTES,
                    key + " must be a bounded nonempty string");
            for (int offset = 0; offset < parsed.length(); ) {
                int codePoint = parsed.codePointAt(offset);
                require(!(codePoint <= 0x08 || (codePoint >= 0x0b && codePoint <= 0x1f)),
                        key + " contains an unsupported control character");
                offset += Character.charCount(codePoint);
            }
            return parsed;
        }

        private static void exactKeys(@NotNull JsonObject value, @NotNull Set<String> required) {
            Set<String> missing = new LinkedHashSet<>(required);
            missing.removeAll(value.keySet());
            Set<String> extra = new LinkedHashSet<>(value.keySet());
            extra.removeAll(required);
            require(missing.isEmpty() && extra.isEmpty(),
                    "Workbench command review keys differ; missing=" + missing + "; extra=" + extra);
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
}
