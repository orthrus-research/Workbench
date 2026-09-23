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
import java.util.regex.Pattern;

/** Shared strict JSON boundary for the additive Product Spine V2 native clients. */
final class ProductSpineJson {
    private static final int MAX_TEXT_BYTES = 256 * 1024;

    private ProductSpineJson() {
    }

    static @NotNull JsonObject parse(
            @NotNull String json,
            @NotNull String label,
            int maximumBytes,
            int maximumNodes
    ) {
        int bytes = json.getBytes(StandardCharsets.UTF_8).length;
        require(bytes >= 2 && bytes <= maximumBytes,
                label + " size is outside the supported bound");
        StrictJson.validate(json, label, maximumNodes);
        final JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (RuntimeException error) {
            throw new IllegalArgumentException("Workbench " + label + " is invalid JSON", error);
        }
        return object(parsed, label);
    }

    static @NotNull JsonElement required(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        require(value.has(key), "Workbench " + label + " is missing " + key);
        return value.get(key);
    }

    static @NotNull JsonObject object(
            @NotNull JsonElement value, @NotNull String label
    ) {
        require(value.isJsonObject(), "Workbench " + label + " must be an object");
        return value.getAsJsonObject();
    }

    static @NotNull JsonObject object(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        return object(required(value, key, label), label + "." + key);
    }

    static @NotNull JsonArray array(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label,
            int maximum
    ) {
        JsonElement member = required(value, key, label);
        require(member.isJsonArray(), "Workbench " + label + "." + key + " must be an array");
        JsonArray result = member.getAsJsonArray();
        require(result.size() <= maximum,
                "Workbench " + label + "." + key + " exceeds its row bound");
        return result;
    }

    static void exact(
            @NotNull JsonObject value, @NotNull String label, @NotNull String... names
    ) {
        Set<String> expected = Set.of(names);
        Set<String> actual = new HashSet<>(value.keySet());
        if (!actual.equals(expected)) {
            Set<String> missing = new HashSet<>(expected);
            missing.removeAll(actual);
            Set<String> extra = new HashSet<>(actual);
            extra.removeAll(expected);
            throw new IllegalArgumentException(
                    "Workbench " + label + " fields changed; missing=" + missing + "; extra=" + extra
            );
        }
    }

    static @NotNull String string(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        JsonElement member = required(value, key, label);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isString(),
                "Workbench " + label + "." + key + " must be a string");
        String selected = member.getAsString();
        require(!selected.isEmpty() && selected.indexOf('\0') < 0
                        && selected.getBytes(StandardCharsets.UTF_8).length <= MAX_TEXT_BYTES,
                "Workbench " + label + "." + key + " is invalid");
        return selected;
    }

    static @Nullable String nullableString(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        JsonElement member = required(value, key, label);
        return member.isJsonNull() ? null : string(value, key, label);
    }

    static @NotNull String patterned(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label,
            @NotNull Pattern pattern
    ) {
        String selected = string(value, key, label);
        require(pattern.matcher(selected).matches(),
                "Workbench " + label + "." + key + " identity is invalid");
        return selected;
    }

    static @Nullable String nullablePatterned(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label,
            @NotNull Pattern pattern
    ) {
        String selected = nullableString(value, key, label);
        require(selected == null || pattern.matcher(selected).matches(),
                "Workbench " + label + "." + key + " identity is invalid");
        return selected;
    }

    static boolean bool(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        JsonElement member = required(value, key, label);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isBoolean(),
                "Workbench " + label + "." + key + " must be boolean");
        return member.getAsBoolean();
    }

    static int integer(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label,
            int minimum,
            int maximum
    ) {
        JsonElement member = required(value, key, label);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isNumber(),
                "Workbench " + label + "." + key + " must be an integer");
        try {
            int selected = member.getAsInt();
            require(member.getAsString().matches("-?(?:0|[1-9][0-9]*)")
                            && selected >= minimum && selected <= maximum,
                    "Workbench " + label + "." + key + " is outside its integer bound");
            return selected;
        } catch (RuntimeException error) {
            throw new IllegalArgumentException(
                    "Workbench " + label + "." + key + " must be a bounded integer", error
            );
        }
    }

    static @NotNull String member(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label,
            @NotNull Set<String> choices
    ) {
        String selected = string(value, key, label);
        require(choices.contains(selected),
                "Workbench " + label + "." + key + " is unsupported");
        return selected;
    }

    static @NotNull List<String> strings(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull String label,
            int maximum
    ) {
        JsonArray rows = array(value, key, label, maximum);
        List<String> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            JsonElement member = rows.get(index);
            require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isString(),
                    "Workbench " + label + "." + key + " row must be a string");
            String selected = member.getAsString();
            require(!selected.isEmpty() && selected.indexOf('\0') < 0
                            && selected.getBytes(StandardCharsets.UTF_8).length <= MAX_TEXT_BYTES,
                    "Workbench " + label + "." + key + " row is invalid");
            result.add(selected);
        }
        return List.copyOf(result);
    }

    static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }
}
