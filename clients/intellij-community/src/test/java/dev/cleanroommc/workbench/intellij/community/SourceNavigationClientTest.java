package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import static org.junit.jupiter.api.Assertions.*;

final class SourceNavigationClientTest {
    @TempDir Path root;

    JsonObject fixture() throws Exception {
        byte[] raw = "/* 😀 */ water\r\n".getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("recipe.groovy"), raw);
        JsonObject location = JsonParser.parseString("""
            {"path":"recipe.groovy","byte_start":11,"byte_end":16,
             "start":{"line":1,"column":10},"end":{"line":1,"column":15},
             "coordinate_system":"one-based-utf16","interval":"half-open"}
            """).getAsJsonObject();
        location.addProperty("sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw)));
        return location;
    }

    @Test void exactUtf16LocationRejectsChangedBytesAndCoordinates() throws Exception {
        JsonObject location = fixture();
        assertEquals("/* 😀 */ water\n", SourceNavigationClient.verify(root, location).text());
        var changed = location.deepCopy();
        changed.getAsJsonObject("start").addProperty("column", 9);
        assertThrows(IllegalArgumentException.class, () -> SourceNavigationClient.verify(root, changed));
        Files.writeString(root.resolve("recipe.groovy"), "different statement");
        assertThrows(IllegalArgumentException.class, () -> SourceNavigationClient.verify(root, location));
    }

    @Test void eofAndEmptyFilePointsRemainExactlyNavigable() throws Exception {
        JsonObject location = fixture();
        int end = (int) Files.size(root.resolve("recipe.groovy"));
        location.addProperty("byte_start", end);
        location.addProperty("byte_end", end);
        for (String key : java.util.List.of("start", "end")) {
            location.getAsJsonObject(key).addProperty("line", 2);
            location.getAsJsonObject(key).addProperty("column", 1);
        }
        assertEquals(root.resolve("recipe.groovy"), SourceNavigationClient.verify(root, location).path());
        Files.writeString(root.resolve("recipe.groovy"), "");
        location.addProperty("sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(new byte[0])));
        location.addProperty("byte_start", 0);
        location.addProperty("byte_end", 0);
        for (String key : java.util.List.of("start", "end")) location.getAsJsonObject(key).addProperty("line", 1);
        assertEquals("", SourceNavigationClient.verify(root, location).text());
    }

    @Test void traversalAndSymlinkLocationsFailClosed() throws Exception {
        JsonObject location = fixture();
        var changed = location.deepCopy();
        changed.addProperty("path", "../recipe.groovy");
        assertThrows(IllegalArgumentException.class, () -> SourceNavigationClient.verify(root, changed));
        Files.move(root.resolve("recipe.groovy"), root.resolve("moved.groovy"));
        Files.createSymbolicLink(root.resolve("recipe.groovy"), Path.of("moved.groovy"));
        assertThrows(IllegalArgumentException.class, () -> SourceNavigationClient.verify(root, location));
    }
}
