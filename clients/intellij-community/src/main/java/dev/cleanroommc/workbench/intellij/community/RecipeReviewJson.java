package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Small fail-closed JSON helpers shared by the provider plan and review consumers. */
final class RecipeReviewJson {
    private static final int MAX_TEXT_BYTES = 64 * 1024;

    private RecipeReviewJson() {
    }

    static @NotNull JsonObject root(
            @NotNull String json,
            @NotNull String label,
            int maximumNodes
    ) {
        StrictJson.validate(json, label, maximumNodes);
        JsonElement value = JsonParser.parseString(json);
        if (!value.isJsonObject()) {
            throw invalid(label, "must be one JSON object");
        }
        return value.getAsJsonObject();
    }

    static void exactKeys(
            @NotNull JsonObject value,
            @NotNull Set<String> expected,
            @NotNull String label
    ) {
        Set<String> found = new HashSet<>(value.keySet());
        if (!found.equals(expected)) {
            Set<String> missing = new HashSet<>(expected);
            missing.removeAll(found);
            Set<String> extra = new HashSet<>(found);
            extra.removeAll(expected);
            throw invalid(
                    label,
                    "has unsupported or missing fields (missing=" + missing
                            + ", extra=" + extra + ")"
            );
        }
    }

    static @NotNull JsonObject object(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null || !selected.isJsonObject()) {
            throw invalid(label, "field " + field + " must be an object");
        }
        return selected.getAsJsonObject();
    }

    static @NotNull JsonArray array(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null || !selected.isJsonArray()) {
            throw invalid(label, "field " + field + " must be an array");
        }
        return selected.getAsJsonArray();
    }

    static @NotNull String text(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null || !selected.isJsonPrimitive()
                || !selected.getAsJsonPrimitive().isString()) {
            throw invalid(label, "field " + field + " must be text");
        }
        String result = selected.getAsString();
        if (result.getBytes(StandardCharsets.UTF_8).length > MAX_TEXT_BYTES) {
            throw invalid(label, "field " + field + " exceeds its text bound");
        }
        return result;
    }

    static @Nullable String nullableText(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null) {
            throw invalid(label, "field " + field + " is missing");
        }
        if (selected.isJsonNull()) {
            return null;
        }
        return text(value, field, label);
    }

    static int nonnegativeInteger(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null || !selected.isJsonPrimitive()) {
            throw invalid(label, "field " + field + " must be a nonnegative integer");
        }
        JsonPrimitive primitive = selected.getAsJsonPrimitive();
        String rendered = primitive.getAsString();
        if (!primitive.isNumber() || !rendered.matches("0|[1-9][0-9]*")) {
            throw invalid(label, "field " + field + " must be a nonnegative integer");
        }
        try {
            return Integer.parseInt(rendered);
        } catch (NumberFormatException error) {
            throw invalid(label, "field " + field + " exceeds the supported integer range");
        }
    }

    static int positiveInteger(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        int selected = nonnegativeInteger(value, field, label);
        if (selected < 1) {
            throw invalid(label, "field " + field + " must be positive");
        }
        return selected;
    }

    static boolean bool(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null || !selected.isJsonPrimitive()
                || !selected.getAsJsonPrimitive().isBoolean()) {
            throw invalid(label, "field " + field + " must be boolean");
        }
        return selected.getAsBoolean();
    }

    static @NotNull List<String> textList(
            @NotNull JsonArray values,
            int maximum,
            @NotNull String label
    ) {
        if (values.size() > maximum) {
            throw invalid(label, "exceeds its row bound");
        }
        List<String> result = new ArrayList<>(values.size());
        for (JsonElement value : values) {
            if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
                throw invalid(label, "contains a non-text row");
            }
            String selected = value.getAsString();
            if (selected.getBytes(StandardCharsets.UTF_8).length > MAX_TEXT_BYTES) {
                throw invalid(label, "contains an oversized text row");
            }
            result.add(selected);
        }
        return List.copyOf(result);
    }

    static @NotNull IllegalArgumentException invalid(
            @NotNull String label,
            @NotNull String detail
    ) {
        return new IllegalArgumentException("Workbench " + label + " " + detail);
    }
}
