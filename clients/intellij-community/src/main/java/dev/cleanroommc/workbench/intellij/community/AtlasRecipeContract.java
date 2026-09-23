package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Shared strict JSON helpers for the Atlas recipe-health V1 IDE boundary. */
final class AtlasRecipeContract {
    static final int MAX_OUTPUT_BYTES = 64 * 1024 * 1024;
    static final int MAX_TEXT_BYTES = 4 * 1024 * 1024;
    static final int MAX_SMALL_TEXT_BYTES = 256 * 1024;
    private static final Pattern GRAPH_SET_ID = Pattern.compile(
            "^workbench-atlas-graph-set-v2:sha256:[0-9a-f]{64}$"
    );

    private AtlasRecipeContract() {
    }

    static @NotNull JsonObject parseRoot(@NotNull String json, @NotNull String label) {
        int size = json.getBytes(StandardCharsets.UTF_8).length;
        require(size >= 1 && size <= MAX_OUTPUT_BYTES,
                "Workbench " + label + " size is outside the supported bound");
        StrictJson.validate(json, label, 500_000);
        final JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (RuntimeException error) {
            throw new IllegalArgumentException("Workbench " + label + " is invalid JSON", error);
        }
        return object(parsed, label);
    }

    static @NotNull JsonObject object(@NotNull JsonElement value, @NotNull String label) {
        require(value.isJsonObject(), "Workbench " + label + " must be an object");
        return value.getAsJsonObject();
    }

    static @NotNull JsonElement required(@NotNull JsonObject value, @NotNull String key) {
        require(value.has(key), "Workbench Atlas record is missing " + key);
        return value.get(key);
    }

    static @NotNull JsonArray array(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonArray(), "Workbench " + key + " must be an array");
        return member.getAsJsonArray();
    }

    static @NotNull List<JsonObject> objectArray(
            @NotNull JsonObject value,
            @NotNull String key,
            int maximum
    ) {
        JsonArray array = array(value, key);
        require(array.size() <= maximum,
                "Workbench " + key + " exceeds the selected impact bound");
        List<JsonObject> result = new ArrayList<>();
        for (JsonElement element : array) {
            result.add(object(element, key + " entry").deepCopy());
        }
        return List.copyOf(result);
    }

    static @NotNull String string(@NotNull JsonObject value, @NotNull String key) {
        return string(required(value, key), key, false, MAX_SMALL_TEXT_BYTES);
    }

    static @NotNull String string(
            @NotNull JsonElement value,
            @NotNull String label,
            boolean allowEmpty,
            int maximumBytes
    ) {
        require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString(),
                "Workbench " + label + " must be a string");
        String parsed = value.getAsString();
        int bytes = parsed.getBytes(StandardCharsets.UTF_8).length;
        require((allowEmpty || bytes >= 1) && bytes <= maximumBytes,
                "Workbench " + label + " is outside the supported bound");
        for (int offset = 0; offset < parsed.length(); ) {
            int codePoint = parsed.codePointAt(offset);
            require(codePoint != 0 && !(codePoint >= 1 && codePoint <= 0x08)
                            && !(codePoint >= 0x0b && codePoint <= 0x1f),
                    "Workbench " + label + " contains an unsupported control character");
            offset += Character.charCount(codePoint);
        }
        return parsed;
    }

    static int integer(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isNumber(),
                "Workbench " + key + " must be an integer");
        try {
            return member.getAsBigDecimal().intValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalArgumentException("Workbench " + key + " must be an integer", error);
        }
    }

    static int nonnegativeInteger(@NotNull JsonObject value, @NotNull String key) {
        int parsed = integer(value, key);
        require(parsed >= 0, "Workbench " + key + " must be nonnegative");
        return parsed;
    }

    static boolean bool(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isBoolean(),
                "Workbench " + key + " must be boolean");
        return member.getAsBoolean();
    }

    static @Nullable String nullableString(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        return member.isJsonNull() ? null : string(member, key, false, MAX_SMALL_TEXT_BYTES);
    }

    static void exactKeys(
            @NotNull JsonObject value,
            @NotNull Set<String> required,
            @NotNull String label
    ) {
        Set<String> actual = value.keySet();
        Set<String> missing = new LinkedHashSet<>(required);
        missing.removeAll(actual);
        Set<String> extra = new LinkedHashSet<>(actual);
        extra.removeAll(required);
        require(missing.isEmpty() && extra.isEmpty(),
                "Workbench " + label + " keys differ; missing=" + missing + "; extra=" + extra);
    }

    static void allowedKeys(
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
                "Workbench " + label + " keys differ; missing=" + missing + "; extra=" + extra);
    }

    static void equal(Object actual, Object expected, @NotNull String message) {
        require(expected.equals(actual), message);
    }

    static @NotNull String graphSetId(@NotNull JsonObject value, @NotNull String key) {
        String parsed = string(value, key);
        require(GRAPH_SET_ID.matcher(parsed).matches(),
                "Workbench graph-set identity is not canonical V2");
        return parsed;
    }

    static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }
}
