package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import org.jetbrains.annotations.NotNull;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/** Python-compatible canonical JSON used by the Shell V1 sealed envelopes. */
final class CanonicalJson {
    private CanonicalJson() {
    }

    static void verifySealedObject(
            @NotNull JsonObject value,
            @NotNull String kind,
            @NotNull String suppliedId
    ) {
        JsonObject body = value.deepCopy();
        body.remove("id");
        String expected = contentId(kind, body);
        if (!expected.equals(suppliedId)) {
            throw new IllegalArgumentException(
                    "Workbench " + kind + " content identity does not match its exact JSON body"
            );
        }
    }

    static void verifyObjectIdentity(
            @NotNull JsonObject value,
            @NotNull String identityField,
            @NotNull String kind,
            boolean trailingNewline
    ) {
        JsonElement supplied = value.get(identityField);
        if (supplied == null || !supplied.isJsonPrimitive()
                || !supplied.getAsJsonPrimitive().isString()) {
            throw new IllegalArgumentException(
                    "Workbench " + kind + " has no exact content identity"
            );
        }
        JsonObject body = value.deepCopy();
        body.remove(identityField);
        String expected = contentId(kind, body, trailingNewline);
        if (!expected.equals(supplied.getAsString())) {
            throw new IllegalArgumentException(
                    "Workbench " + kind + " content identity does not match its exact JSON body"
            );
        }
    }

    static @NotNull String contentId(@NotNull String kind, @NotNull JsonObject body) {
        return contentId(kind, body, false);
    }

    static @NotNull String contentId(
            @NotNull String kind,
            @NotNull JsonObject body,
            boolean trailingNewline
    ) {
        String canonical = canonical(body) + (trailingNewline ? "\n" : "");
        byte[] bytes = canonical.getBytes(StandardCharsets.UTF_8);
        final byte[] digest;
        try {
            digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("this Java runtime has no SHA-256 provider", error);
        }
        StringBuilder hex = new StringBuilder(64);
        for (byte octet : digest) {
            hex.append(String.format("%02x", octet & 0xff));
        }
        return (kind.isEmpty() ? "sha256:" : kind + ":sha256:") + hex;
    }

    static @NotNull String canonical(@NotNull JsonElement value) {
        StringBuilder output = new StringBuilder();
        append(value, output, 0);
        return output.toString();
    }

    private static void append(
            @NotNull JsonElement value,
            @NotNull StringBuilder output,
            int depth
    ) {
        if (depth > 128) {
            throw new IllegalArgumentException("Workbench JSON nesting exceeds the supported bound");
        }
        if (value.isJsonNull()) {
            output.append("null");
            return;
        }
        if (value.isJsonArray()) {
            output.append('[');
            for (int index = 0; index < value.getAsJsonArray().size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                append(value.getAsJsonArray().get(index), output, depth + 1);
            }
            output.append(']');
            return;
        }
        if (value.isJsonObject()) {
            output.append('{');
            List<Map.Entry<String, JsonElement>> entries = new ArrayList<>(
                    value.getAsJsonObject().entrySet()
            );
            entries.sort(Comparator.comparing(Map.Entry::getKey));
            for (int index = 0; index < entries.size(); index++) {
                if (index > 0) {
                    output.append(',');
                }
                appendString(entries.get(index).getKey(), output);
                output.append(':');
                append(entries.get(index).getValue(), output, depth + 1);
            }
            output.append('}');
            return;
        }
        JsonPrimitive primitive = value.getAsJsonPrimitive();
        if (primitive.isString()) {
            appendString(primitive.getAsString(), output);
        } else if (primitive.isBoolean()) {
            output.append(primitive.getAsBoolean() ? "true" : "false");
        } else if (primitive.isNumber()) {
            String number = primitive.getAsString();
            if (number.equals("NaN") || number.equals("Infinity") || number.equals("-Infinity")) {
                throw new IllegalArgumentException("Workbench JSON contains a non-finite number");
            }
            output.append(number);
        } else {
            throw new IllegalArgumentException("Workbench JSON primitive is unsupported");
        }
    }

    private static void appendString(@NotNull String value, @NotNull StringBuilder output) {
        output.append('"');
        for (int offset = 0; offset < value.length(); ) {
            int codePoint = value.codePointAt(offset);
            int width = Character.charCount(codePoint);
            if (width == 1 && Character.isSurrogate(value.charAt(offset))) {
                throw new IllegalArgumentException("Workbench JSON contains an unpaired surrogate");
            }
            switch (codePoint) {
                case '"' -> output.append("\\\"");
                case '\\' -> output.append("\\\\");
                case '\b' -> output.append("\\b");
                case '\f' -> output.append("\\f");
                case '\n' -> output.append("\\n");
                case '\r' -> output.append("\\r");
                case '\t' -> output.append("\\t");
                default -> {
                    if (codePoint < 0x20) {
                        output.append(String.format("\\u%04x", codePoint));
                    } else {
                        output.appendCodePoint(codePoint);
                    }
                }
            }
            offset += width;
        }
        output.append('"');
    }
}
