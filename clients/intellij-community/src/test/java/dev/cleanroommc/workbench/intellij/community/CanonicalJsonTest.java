package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

final class CanonicalJsonTest {
    @Test
    void matchesThePythonEnsureAsciiFalseSortedCompactEncoding() {
        var value = JsonParser.parseString(
                "{\"z\":\"é\\u2028\",\"a\":[true,null,1,1.0,{\"line\":\"a\\nb\"}]}"
        );
        assertEquals(
                "{\"a\":[true,null,1,1.0,{\"line\":\"a\\nb\"}],\"z\":\"é\u2028\"}",
                CanonicalJson.canonical(value)
        );
        assertEquals(
                "fixture:sha256:d10bb6faa06e2aa6443168b4545805641e83537c7e59b712b942c4def3418d34",
                CanonicalJson.contentId("fixture", value.getAsJsonObject())
        );
    }
}
