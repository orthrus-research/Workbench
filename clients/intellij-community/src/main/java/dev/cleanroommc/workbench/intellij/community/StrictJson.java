package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.Strictness;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import org.jetbrains.annotations.NotNull;

import java.io.IOException;
import java.io.StringReader;
import java.util.HashSet;
import java.util.Set;
import java.util.ArrayDeque;

/** Rejects ambiguous duplicate-key or excessively complex JSON before Gson materializes it. */
final class StrictJson {
    private static final int MAX_DEPTH = 128;
    private static final int MAX_NODES = 100_000;
    private static final int MAX_EXPLICIT_NODES = 2 * 1024 * 1024;

    private StrictJson() {
    }

    /** Explicit complete Atlas reports: validate every token without a value-count budget. */
    static void validateComplete(@NotNull String json, @NotNull String label) {
        try (JsonReader reader = new JsonReader(new StringReader(json))) {
            reader.setStrictness(Strictness.STRICT);
            reader.setNestingLimit(Integer.MAX_VALUE);
            ArrayDeque<Set<String>> objects = new ArrayDeque<>();
            boolean consumed = false;
            while (reader.peek() != JsonToken.END_DOCUMENT) {
                if (consumed && objects.isEmpty()) {
                    throw new IllegalArgumentException("Workbench " + label + " has trailing JSON");
                }
                consumed = true;
                switch (reader.peek()) {
                    case BEGIN_OBJECT -> { reader.beginObject(); objects.push(new HashSet<>()); }
                    case BEGIN_ARRAY -> { reader.beginArray(); objects.push(Set.of()); }
                    case END_OBJECT -> { reader.endObject(); objects.pop(); }
                    case END_ARRAY -> { reader.endArray(); objects.pop(); }
                    case NAME -> {
                        String key = reader.nextName();
                        if (!objects.peek().add(key)) {
                            throw new IllegalArgumentException("Workbench " + label + " contains duplicate object key " + key);
                        }
                    }
                    case STRING, NUMBER -> reader.nextString();
                    case BOOLEAN -> reader.nextBoolean();
                    case NULL -> reader.nextNull();
                    default -> throw new IllegalArgumentException("Workbench " + label + " contains an unsupported token");
                }
            }
            if (!consumed || !objects.isEmpty()) {
                throw new IllegalArgumentException("Workbench " + label + " is incomplete JSON");
            }
        } catch (IOException | IllegalStateException error) {
            throw new IllegalArgumentException("Workbench " + label + " is invalid JSON", error);
        }
    }

    static void validate(@NotNull String json, @NotNull String label) {
        validate(json, label, MAX_NODES);
    }

    static void validate(
            @NotNull String json,
            @NotNull String label,
            int maximumNodes
    ) {
        if (maximumNodes < 1 || maximumNodes > MAX_EXPLICIT_NODES) {
            throw new IllegalArgumentException("Workbench JSON node bound is unsupported");
        }
        try (JsonReader reader = new JsonReader(new StringReader(json))) {
            reader.setStrictness(Strictness.STRICT);
            reader.setNestingLimit(MAX_DEPTH + 1);
            int[] remaining = {maximumNodes};
            value(reader, remaining, 0, label);
            if (reader.peek() != JsonToken.END_DOCUMENT) {
                throw new IllegalArgumentException("Workbench " + label + " has trailing JSON");
            }
        } catch (IOException | IllegalStateException error) {
            throw new IllegalArgumentException("Workbench " + label + " is invalid JSON", error);
        }
    }

    private static void value(
            @NotNull JsonReader reader,
            int @NotNull [] remaining,
            int depth,
            @NotNull String label
    ) throws IOException {
        if (depth > MAX_DEPTH || --remaining[0] < 0) {
            throw new IllegalArgumentException(
                    "Workbench " + label + " exceeds the supported JSON complexity"
            );
        }
        switch (reader.peek()) {
            case BEGIN_OBJECT -> {
                reader.beginObject();
                Set<String> names = new HashSet<>();
                while (reader.hasNext()) {
                    String name = reader.nextName();
                    if (!names.add(name)) {
                        throw new IllegalArgumentException(
                                "Workbench " + label + " contains duplicate object key " + name
                        );
                    }
                    value(reader, remaining, depth + 1, label);
                }
                reader.endObject();
            }
            case BEGIN_ARRAY -> {
                reader.beginArray();
                while (reader.hasNext()) {
                    value(reader, remaining, depth + 1, label);
                }
                reader.endArray();
            }
            case STRING, NUMBER -> reader.nextString();
            case BOOLEAN -> reader.nextBoolean();
            case NULL -> reader.nextNull();
            default -> throw new IllegalArgumentException(
                    "Workbench " + label + " contains an unsupported JSON token"
            );
        }
    }
}
