package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class MaterialSnapshotClientTest {
    @Test void retentionTransportBindsExactProposalAndStructuredSettings() {
        String session = "work-session-v2-" + "a".repeat(32);
        String confirmation = "check-retention-proposal:sha256:" + "b".repeat(64);
        var options = JsonParser.parseString("{\"operation\":\"configure\",\"settings\":{\"mode\":\"finite\",\"known_stores\":[\"/path with spaces\"]}}").getAsJsonObject();
        var args = MaterialChecksClient.arguments(session, "retention", null, confirmation, options);
        assertEquals(List.of("--settings", options.get("settings").toString(), "--confirm", confirmation), args.subList(args.size()-4, args.size()));
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(session, "retention", null, "yes", options));
        options.addProperty("operation", "purge-all");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(session, "retention", null, null, options));
    }
    @Test void readerSupportCannotRewriteHistoricalCaptureOrNativeOutcome() {
        var view = JsonParser.parseString("""
            {"format":"workbench-material-check-view-v1","state":"completed",
             "native_outcome":"native-failed","coverage":"complete","findings_count":7,
             "interpretation":{"state":"unsupported","unsupported_sections":["crafting-values"]}}
            """).getAsJsonObject();
        var text = MaterialChecksClient.summary(view);
        assertTrue(text.contains("Initialization: native-failed; observation coverage: complete"));
        assertTrue(text.contains("Reader interpretation: unsupported"));
        assertTrue(text.contains("crafting-values"));
    }
    private JsonObject fixture(String name) throws Exception {
        return JsonParser.parseString(Files.readString(Path.of("../../core/tests/fixtures/check-snapshot-v1/" + name + ".json"))).getAsJsonObject();
    }
    @Test void coreContractFixtureAndUnicodeIdentityAgree() throws Exception {
        var query = fixture("query"); var response = fixture("response");
        assertSame(response, MaterialSnapshotClient.validatePage(response, query));
        var unicode = JsonParser.parseString("{\"view_id\":\"vue 🌍 é\",\"cursor\":null,\"offset\":2}").getAsJsonObject();
        assertEquals("fixture:sha256:089c2f2c49da75b273674f7755397eacc6525b42c6d5e43be8c0fd248ec88482", MaterialSnapshotClient.identity("fixture", unicode));
    }
    @Test void oldViewAndOutOfRangePageCannotReplaceCurrentEvidence() throws Exception {
        var query = fixture("query"); var response = fixture("response");
        query.addProperty("view_id", "another-view");
        assertThrows(IllegalArgumentException.class, () -> MaterialSnapshotClient.validatePage(response, query));
        var original = fixture("query"); response.getAsJsonObject("payload").addProperty("total", 2);
        assertThrows(IllegalArgumentException.class, () -> MaterialSnapshotClient.validatePage(response, original));
    }
    @Test void unavailableStatesNeverCertifyEmptySuccess() throws Exception {
        var query = fixture("query");
        for (String state : List.of("unavailable", "unsupported", "expired", "cancelled", "incomplete")) {
            var response = fixture("response"); response.addProperty("state", state); response.add("payload", com.google.gson.JsonNull.INSTANCE);
            response.addProperty("reason", "Explicit fixture reason"); response.addProperty("complete", false);
            assertSame(response, MaterialSnapshotClient.validatePage(response, query)); response.addProperty("complete", true);
            assertThrows(IllegalArgumentException.class, () -> MaterialSnapshotClient.validatePage(response, query));
        }
    }
    @Test void diagnosticAddressRemainsBoundToSelectedSide() {
        var view = new JsonObject(); view.addProperty("snapshot_id", "fixture"); view.addProperty("view_id", "fixture-view");
        var finding = JsonParser.parseString("{\"side\":\"candidate\",\"pointer\":\"/result/candidate/result/execution/diagnostics/7\"}").getAsJsonObject();
        var query = MaterialSnapshotClient.diagnosticQuery(view, finding);
        assertEquals("candidate-diagnostics", query.get("section_id").getAsString());
        assertEquals("item:7", query.get("record_key").getAsString());
        finding.addProperty("side", "baseline"); assertThrows(IllegalArgumentException.class, () -> MaterialSnapshotClient.diagnosticQuery(view, finding));
    }
    @Test void bulkPagesChangeQueryIdentityWithoutChangingTheRecordOffset() throws Exception {
        var view = fixture("query");
        view.add("finding_page", JsonParser.parseString("{\"next_cursor\":{\"offset\":73}}"));
        var bulk = MaterialSnapshotClient.nextFindingsQuery(view, true);
        var small = MaterialSnapshotClient.nextFindingsQuery(view, false);
        assertEquals(1048576, bulk.get("preferred_bytes").getAsInt());
        assertEquals(65536, small.get("preferred_bytes").getAsInt());
        assertEquals(73, bulk.getAsJsonObject("cursor").get("offset").getAsInt());
        assertNotEquals(bulk.getAsJsonObject("cursor").get("query_id"), small.getAsJsonObject("cursor").get("query_id"));
        var base = bulk.deepCopy(); base.remove("cursor");
        assertEquals(MaterialSnapshotClient.identity("check-snapshot-query", base), bulk.getAsJsonObject("cursor").get("query_id").getAsString());
    }

}
