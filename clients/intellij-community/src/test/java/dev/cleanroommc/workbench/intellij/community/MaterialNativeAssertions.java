package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;

/** Assertions over the original native diagnostic result used by installed IDE cases. */
final class MaterialNativeAssertions {
    private MaterialNativeAssertions() { }

    static boolean hasError(JsonObject nativeResult, String message) {
        for (var value : nativeResult.getAsJsonObject("result").getAsJsonObject("execution").getAsJsonArray("diagnostics")) {
            var diagnostic = value.getAsJsonObject();
            if (!MaterialChecksClient.text(diagnostic, "severity", "").equals("error")) continue;
            if (MaterialChecksClient.text(diagnostic, "message", "").contains(message)) return true;
            if (diagnostic.has("causality")) for (var cause : diagnostic.getAsJsonObject("causality").getAsJsonArray("exceptions")) {
                if (MaterialChecksClient.text(cause.getAsJsonObject(), "message", "").contains(message)) return true;
            }
        }
        return false;
    }

    static void assertScope(JsonObject nativeResult, String status) {
        org.junit.Assert.assertEquals(status, nativeResult.getAsJsonObject("result").getAsJsonObject("initialization").get("status").getAsString());
    }
}
