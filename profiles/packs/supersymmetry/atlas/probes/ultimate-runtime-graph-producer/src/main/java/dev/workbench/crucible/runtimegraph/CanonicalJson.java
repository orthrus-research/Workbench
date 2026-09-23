package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/** Deterministic UTF-8 JSON for raw producer evidence. */
public final class CanonicalJson {
    private CanonicalJson() {}

    public static byte[] bytes(JsonElement value) {
        if (value == null) throw new IllegalArgumentException("canonical JSON value is null");
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        write(value, output);
        return output.toByteArray();
    }

    public static String sha256(JsonElement value) {
        return Hashing.sha256(bytes(value));
    }

    public static JsonArray sortedRecords(List<JsonObject> records) {
        List<JsonObject> copy = new ArrayList<JsonObject>(records);
        Collections.sort(copy, new Comparator<JsonObject>() {
            @Override
            public int compare(JsonObject left, JsonObject right) {
                return compareUnsigned(bytes(left), bytes(right));
            }
        });
        JsonArray result = new JsonArray();
        for (JsonObject record : copy) result.add(record);
        return result;
    }

    public static int compareUnsigned(byte[] left, byte[] right) {
        int common = Math.min(left.length, right.length);
        for (int index = 0; index < common; index++) {
            int a = left[index] & 0xff;
            int b = right[index] & 0xff;
            if (a != b) return a < b ? -1 : 1;
        }
        return Integer.compare(left.length, right.length);
    }

    private static void write(JsonElement value, ByteArrayOutputStream output) {
        if (value == null || value instanceof JsonNull || value.isJsonNull()) {
            ascii(output, "null");
        } else if (value.isJsonObject()) {
            writeObject(value.getAsJsonObject(), output);
        } else if (value.isJsonArray()) {
            writeArray(value.getAsJsonArray(), output);
        } else if (value.isJsonPrimitive()) {
            writePrimitive(value.getAsJsonPrimitive(), output);
        } else {
            throw new IllegalArgumentException("unsupported JSON element " + value.getClass());
        }
    }

    private static void writeObject(JsonObject value, ByteArrayOutputStream output) {
        List<Map.Entry<String, JsonElement>> entries =
            new ArrayList<Map.Entry<String, JsonElement>>(value.entrySet());
        Collections.sort(entries, new Comparator<Map.Entry<String, JsonElement>>() {
            @Override
            public int compare(
                Map.Entry<String, JsonElement> left,
                Map.Entry<String, JsonElement> right
            ) {
                return compareUnsigned(
                    left.getKey().getBytes(StandardCharsets.UTF_8),
                    right.getKey().getBytes(StandardCharsets.UTF_8)
                );
            }
        });
        output.write('{');
        for (int index = 0; index < entries.size(); index++) {
            if (index != 0) output.write(',');
            writeString(entries.get(index).getKey(), output);
            output.write(':');
            write(entries.get(index).getValue(), output);
        }
        output.write('}');
    }

    private static void writeArray(JsonArray value, ByteArrayOutputStream output) {
        output.write('[');
        for (int index = 0; index < value.size(); index++) {
            if (index != 0) output.write(',');
            write(value.get(index), output);
        }
        output.write(']');
    }

    private static void writePrimitive(JsonPrimitive value, ByteArrayOutputStream output) {
        if (value.isBoolean() || value.isNumber()) {
            ascii(output, value.getAsString());
        } else if (value.isString()) {
            writeString(value.getAsString(), output);
        } else {
            throw new IllegalArgumentException("unsupported JSON primitive");
        }
    }

    private static void writeString(String value, ByteArrayOutputStream output) {
        if (value == null) throw new IllegalArgumentException("JSON string is null");
        output.write('"');
        StringBuilder plain = new StringBuilder();
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (Character.isHighSurrogate(character)) {
                if (index + 1 >= value.length()
                    || !Character.isLowSurrogate(value.charAt(index + 1))) {
                    throw new IllegalArgumentException("JSON string has unpaired high surrogate");
                }
                plain.append(character).append(value.charAt(++index));
            } else if (Character.isLowSurrogate(character)) {
                throw new IllegalArgumentException("JSON string has unpaired low surrogate");
            } else if (character == '"' || character == '\\' || character <= 0x1f) {
                flushPlain(plain, output);
                if (character == '"') ascii(output, "\\\"");
                else if (character == '\\') ascii(output, "\\\\");
                else ascii(output, String.format("\\u%04x", Integer.valueOf(character)));
            } else {
                plain.append(character);
            }
        }
        flushPlain(plain, output);
        output.write('"');
    }

    private static void flushPlain(StringBuilder value, ByteArrayOutputStream output) {
        if (value.length() == 0) return;
        byte[] bytes = value.toString().getBytes(StandardCharsets.UTF_8);
        output.write(bytes, 0, bytes.length);
        value.setLength(0);
    }

    private static void ascii(ByteArrayOutputStream output, String value) {
        byte[] bytes = value.getBytes(StandardCharsets.US_ASCII);
        output.write(bytes, 0, bytes.length);
    }
}
