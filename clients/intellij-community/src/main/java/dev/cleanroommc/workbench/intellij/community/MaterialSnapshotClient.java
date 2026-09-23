package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.regex.Pattern;
import static dev.cleanroommc.workbench.intellij.community.MaterialChecksClient.object;
import static dev.cleanroommc.workbench.intellij.community.MaterialChecksClient.text;

/** Snapshot/page identity guards; Core remains the content and custody owner. */
final class MaterialSnapshotClient {
    static final String VIEW = "workbench-material-check-view-v1";
    private MaterialSnapshotClient() { }

    static String identity(String kind, com.google.gson.JsonElement value) throws Exception {
        var ascii = new StringBuilder();
        for (char c : CanonicalJson.canonical(value).toCharArray()) {
            if (c >= 127) ascii.append(String.format("\\u%04x", (int)c)); else ascii.append(c);
        }
        return kind + ":sha256:" + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(ascii.toString().getBytes(StandardCharsets.UTF_8)));
    }
    static JsonObject query(JsonObject view, String operation, String section, String key) {
        var query = new JsonObject();
        query.addProperty("format", "workbench-check-snapshot-query-v1");
        query.add("snapshot_id", view.get("snapshot_id")); query.add("view_id", view.get("view_id"));
        query.addProperty("operation", operation); query.addProperty("section_id", section);
        query.addProperty("record_key", key); query.add("blob_sha256", JsonNull.INSTANCE);
        query.addProperty("preferred_bytes", 65536); query.add("cursor", JsonNull.INSTANCE);
        return query;
    }
    static JsonObject validatePage(JsonObject page, JsonObject query) throws Exception {
        var base = query.deepCopy(); base.remove("cursor");
        String queryId = identity("check-snapshot-query", base);
        if (!text(page, "format", "").equals("workbench-check-snapshot-response-v1")
                || !query.get("snapshot_id").equals(page.get("snapshot_id")) || !query.get("view_id").equals(page.get("view_id"))
                || !queryId.equals(text(page, "query_id", "")) || !identity("check-snapshot-read", query).equals(text(page, "request_id", ""))
                || !List.of("ready", "unavailable", "unsupported", "expired", "incomplete", "cancelled").contains(text(page, "state", "")))
            throw new IllegalArgumentException("Snapshot response differs from its selected view or page.");
        boolean complete = page.get("complete").getAsBoolean();
        if (!text(page, "state", "").equals("ready")) {
            if (complete || !page.get("payload").isJsonNull() || !page.get("next_cursor").isJsonNull() || text(page, "reason", "").isEmpty())
                throw new IllegalArgumentException("Unavailable snapshot claims complete data.");
            return page;
        }
        var payload = object(page, "payload"); var cursor = object(query, "cursor");
        long offset = cursor.has("offset") ? cursor.get("offset").getAsLong() : 0, next = offset;
        String operation = text(query, "operation", "");
        if (List.of("records", "sections").contains(operation)) {
            var values = payload.getAsJsonArray(operation);
            next += values.size(); long total = payload.get("total").getAsLong();
            if (payload.get("offset").getAsLong() != offset || total < next || complete != (next == total) || (!complete && values.isEmpty()))
                throw new IllegalArgumentException("Snapshot count, order or progress differs.");
        } else if (operation.equals("blob")) {
            byte[] bytes = Base64.getDecoder().decode(text(payload, "data", "")); next += bytes.length;
            String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            long total = payload.get("total_bytes").getAsLong();
            if (!Base64.getEncoder().encodeToString(bytes).equals(text(payload, "data", ""))
                    || payload.get("offset").getAsLong() != offset || !query.get("blob_sha256").equals(payload.get("sha256"))
                    || !digest.equals(text(payload, "chunk_sha256", "")) || total < next || complete != (next == total) || (!complete && bytes.length == 0))
                throw new IllegalArgumentException("Snapshot chunk content or range differs.");
        } else if (!complete) throw new IllegalArgumentException("Snapshot record must be complete or referenced.");
        if (complete) {
            if (!page.get("next_cursor").isJsonNull()) throw new IllegalArgumentException("Terminal page has a cursor.");
        } else {
            var following = object(page, "next_cursor");
            if (following.get("offset").getAsLong() != next || !query.get("snapshot_id").equals(following.get("snapshot_id"))
                    || !queryId.equals(text(following, "query_id", ""))) throw new IllegalArgumentException("Next snapshot page differs.");
        }
        return page;
    }
    static JsonObject nextFindingsQuery(JsonObject result, boolean bulk) throws Exception {
        var next = query(result, "records", "findings", null);
        next.addProperty("preferred_bytes", bulk ? 1048576 : 65536);
        var base = next.deepCopy(); base.remove("cursor");
        var cursor = new JsonObject(); cursor.add("snapshot_id", result.get("snapshot_id"));
        cursor.addProperty("query_id", identity("check-snapshot-query", base));
        cursor.add("offset", result.getAsJsonObject("finding_page").getAsJsonObject("next_cursor").get("offset"));
        next.add("cursor", cursor); return next;
    }
    static JsonArray findings(JsonObject page) {
        var values = new JsonArray(); var payload = object(page, "payload");
        if (payload.has("records")) for (var item : payload.getAsJsonArray("records")) {
            var row = item.getAsJsonObject(); if (row.has("value")) values.add(row.get("value"));
        }
        return values;
    }
    static void attach(JsonObject result) throws Exception {
        if (!text(result, "format", "").equals(VIEW)) return;
        if (result.get("snapshot_id").isJsonNull() && text(result, "detail_state", "").equals("cancelled-before-snapshot-publication")
                && result.get("finding_page").isJsonNull() && result.get("finding_query").isJsonNull() && text(result, "coverage", "").equals("incomplete")) {
            result.add("findings", new JsonArray()); return;
        }
        var query = object(result, "finding_query");
        if (!text(result, "snapshot_id", "").matches("check-snapshot:sha256:[0-9a-f]{64}")
                || !result.get("snapshot_id").equals(query.get("snapshot_id")) || !result.get("view_id").equals(query.get("view_id"))
                || !text(query, "operation", "").equals("records") || !text(query, "section_id", "").equals("findings")
                || !result.has("sections") || !result.get("sections").isJsonArray()) throw new IllegalArgumentException("Unsupported material snapshot view.");
        var page = validatePage(object(result, "finding_page"), query);
        if (text(page, "state", "").equals("ready") && object(page, "payload").get("total").getAsLong() != result.get("findings_count").getAsLong())
            throw new IllegalArgumentException("Finding total differs from summary.");
        result.add("findings", findings(page));
    }
    static String progress(JsonObject result) {
        var page = object(result, "finding_page");
        if (!text(page, "state", "").equals("ready")) return "Findings: " + text(page, "state", "not loaded") + " · " + text(page, "reason", "");
        var payload = object(page, "payload"); long large = payload.get("offset").getAsLong() + payload.getAsJsonArray("records").size() - result.getAsJsonArray("findings").size();
        return "Findings displayed: " + result.getAsJsonArray("findings").size() + "; page through "
                + (payload.get("offset").getAsLong() + payload.getAsJsonArray("records").size()) + " of " + payload.get("total")
                + (page.get("complete").getAsBoolean() ? " · final page" : " · more available")
                + (large > 0 ? " · " + large + " large values require detail export" : "");
    }
    static JsonObject diagnosticQuery(JsonObject result, JsonObject finding) {
        var match = Pattern.compile("^(?:/result/(baseline|candidate))?/result/(execution/diagnostics|sourceAdmission/findings)/([0-9]+)$")
                .matcher(text(finding, "pointer", ""));
        if (!match.matches() || match.group(1) != null && !match.group(1).equals(text(finding, "side", "")))
            throw new IllegalArgumentException("Unsupported original diagnostic pointer.");
        return query(result, "record", (match.group(1) == null ? "" : match.group(1) + "-")
                + (match.group(2).startsWith("execution") ? "diagnostics" : "admission-findings"), "item:" + Long.parseLong(match.group(3)));
    }
}
