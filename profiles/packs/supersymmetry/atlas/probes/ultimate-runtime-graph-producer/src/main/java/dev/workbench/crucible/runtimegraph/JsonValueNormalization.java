package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import java.util.Map;

/** Canonicalizes parsed JSON decimals without transport floating-point tokens. */
public final class JsonValueNormalization {
    private JsonValueNormalization() {}

    public static JsonElement typedDecimals(JsonElement value) {
        if (value == null || value instanceof JsonNull || value.isJsonNull()) {
            return JsonNull.INSTANCE;
        }
        if (value.isJsonObject()) {
            JsonObject result = new JsonObject();
            for (Map.Entry<String, JsonElement> entry : value.getAsJsonObject().entrySet()) {
                result.add(entry.getKey(), typedDecimals(entry.getValue()));
            }
            return result;
        }
        if (value.isJsonArray()) {
            JsonArray result = new JsonArray();
            for (JsonElement item : value.getAsJsonArray()) result.add(typedDecimals(item));
            return result;
        }
        JsonPrimitive primitive = value.getAsJsonPrimitive();
        if (!primitive.isNumber()) return primitive.deepCopy();
        String token = primitive.getAsString();
        if (token.indexOf('.') < 0 && token.indexOf('e') < 0 && token.indexOf('E') < 0) {
            return primitive.deepCopy();
        }
        JsonObject result = new JsonObject();
        result.addProperty("decimal", token);
        result.addProperty("value_kind", "parsed-json-decimal");
        return result;
    }
}
