package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class MaterialDeliveryClientTest {
    private static final String REVISION = "check-diagnostics:sha256:" + "a".repeat(64);
    private static final String ATTEMPT = "material-check-" + "b".repeat(32);
    private static final String SESSION = "work-session-v2-" + "c".repeat(32);
    @Test void groupPagesRetainGlobalTotalsAndRejectOtherGroups() {
        var result = JsonParser.parseString("{\"findings\":[{}],\"findings_count\":901,\"offset\":0,\"next_offset\":null,\"group\":\"error-unlocated\",\"group_count\":1}").getAsJsonObject();
        result.addProperty("format", MaterialDeliveryClient.VIEW); result.addProperty("id", REVISION);
        result.addProperty("diagnostic_id", REVISION); result.addProperty("view_id", REVISION);
        var counts = new JsonObject(); MaterialDeliveryClient.GROUPS.forEach(key -> counts.addProperty(key, 0));
        counts.addProperty("error-unlocated", 1); counts.addProperty("warning-located", 900); result.add("finding_counts", counts);
        var options = MaterialDeliveryClient.options(result); options.addProperty("group", "error-unlocated");
        MaterialDeliveryClient.validate(result, "diagnostics", options);
        var args = MaterialChecksClient.arguments(SESSION, "diagnostics", ATTEMPT, null, options);
        assertEquals(List.of("--group", "error-unlocated"), args.subList(args.size() - 2, args.size()));
        options.addProperty("group", "error-located"); assertThrows(IllegalArgumentException.class, () -> MaterialDeliveryClient.validate(result, "diagnostics", options));
        options.addProperty("group", "error-unlocated"); result.remove("finding_counts"); assertThrows(IllegalArgumentException.class, () -> MaterialDeliveryClient.validate(result, "diagnostics", options));
        options.addProperty("group", "invented"); assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "diagnostics", ATTEMPT, null, options));
    }
    @Test void diagnosticAndSourceCommandsRetainTheirExactRevision() {
        var options = new JsonObject(); options.addProperty("revision", REVISION); options.addProperty("offset", 128);
        var args = MaterialChecksClient.arguments(SESSION, "diagnostics", ATTEMPT, null, options);
        assertEquals(List.of(ATTEMPT, "--revision", REVISION, "--offset", "128"), args.subList(args.size() - 5, args.size()));
        var source = MaterialChecksClient.arguments(SESSION, "source", ATTEMPT, "diagnostic-7", options);
        assertEquals(List.of("--revision", REVISION, "--diagnostic", "diagnostic-7"), source.subList(source.size() - 4, source.size()));
        options.addProperty("revision", "wrong");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "diagnostics", ATTEMPT, null, options));
    }
    @Test void pagesRefuseFalseCompletionAndWrongRevision() {
        var result = JsonParser.parseString("{\"findings\":[{}],\"findings_count\":2,\"offset\":0,\"next_offset\":1}").getAsJsonObject();
        result.addProperty("format", MaterialDeliveryClient.VIEW); result.addProperty("id", REVISION);
        result.addProperty("diagnostic_id", REVISION); result.addProperty("view_id", REVISION);
        var options = MaterialDeliveryClient.options(result);
        MaterialDeliveryClient.validate(result, "diagnostics", options);
        result.add("next_offset", com.google.gson.JsonNull.INSTANCE);
        assertThrows(IllegalArgumentException.class, () -> MaterialDeliveryClient.validate(result, "diagnostics", options));
        result.addProperty("next_offset", 1); options.addProperty("revision", "another");
        assertThrows(IllegalArgumentException.class, () -> MaterialDeliveryClient.validate(result, "diagnostics", options));
    }
}
