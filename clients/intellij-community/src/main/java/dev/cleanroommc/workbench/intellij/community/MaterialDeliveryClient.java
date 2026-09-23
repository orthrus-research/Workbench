package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import java.util.List;
import static dev.cleanroommc.workbench.intellij.community.MaterialChecksClient.text;

/** Identity and paging guards for diagnostics retained before snapshot finalization. */
final class MaterialDeliveryClient {
    static final String VIEW = "workbench-material-diagnostic-view-v1";
    static final String STATUS = "workbench-material-delivery-status-v1";
    static final List<String> GROUPS = List.of("error-located", "error-unlocated", "warning-located", "warning-unlocated", "information-located", "information-unlocated");
    private MaterialDeliveryClient() { }
    static boolean isView(JsonObject value) { return VIEW.equals(text(value, "format", "")); }
    static boolean revision(String value) { return value.matches("check-diagnostics:sha256:[0-9a-f]{64}"); }
    static JsonObject options(JsonObject view) {
        var options = new JsonObject(); options.add("revision", view.get("diagnostic_id")); return options;
    }
    static void arguments(List<String> args, String action, JsonObject options) {
        if (options != null && options.has("revision")) {
            if (!revision(text(options, "revision", ""))) throw new IllegalArgumentException("Select an exact diagnostic revision.");
            args.addAll(List.of("--revision", options.get("revision").getAsString()));
        }
        if (action.equals("diagnostics")) {
            long offset = options != null && options.has("offset") ? options.get("offset").getAsLong() : 0;
            if (offset < 0) throw new IllegalArgumentException("Select an exact diagnostic offset.");
            args.addAll(List.of("--offset", Long.toString(offset)));
            if (options != null && options.has("group")) {
                if (!GROUPS.contains(text(options, "group", ""))) throw new IllegalArgumentException("Select an exact diagnostic group.");
                args.addAll(List.of("--group", options.get("group").getAsString()));
            }
        }
        if (action.equals("diagnostic")) {
            if (options == null || !revision(text(options, "revision", "")) || !text(options, "finding", "").matches("diagnostic-(0|[1-9][0-9]*)"))
                throw new IllegalArgumentException("Select an exact retained diagnostic.");
            args.addAll(List.of("--diagnostic", options.get("finding").getAsString()));
        }
    }
    static void validate(JsonObject result, String action, JsonObject options) {
        if (STATUS.equals(text(result, "format", ""))) {
            if (!result.has("diagnostic_id") || !result.get("diagnostic_id").isJsonNull() && !revision(text(result, "diagnostic_id", ""))
                    || !List.of("ready", "preparing", "interrupted", "not-started").contains(text(result, "detail_state", "")))
                throw new IllegalArgumentException("Invalid diagnostic delivery status.");
        }
        if (isView(result)) {
            long offset = options != null && options.has("offset") ? options.get("offset").getAsLong() : 0;
            String group = text(result, "group", ""), selected = text(options, "group", "");
            if (!group.equals(selected)) throw new IllegalArgumentException("Diagnostic response belongs to another group.");
            long global = result.get("findings_count").getAsLong();
            long total = result.has("group_count") ? result.get("group_count").getAsLong() : global;
            if (!group.isEmpty() && (!GROUPS.contains(group) || !result.has("finding_counts"))) throw new IllegalArgumentException("Diagnostic group counts are unavailable.");
            if (result.has("finding_counts")) {
                var counts = result.getAsJsonObject("finding_counts");
                long sum = 0;
                if (counts.size() != GROUPS.size()) throw new IllegalArgumentException("Diagnostic groups differ.");
                for (String key : GROUPS) {
                    if (!counts.has(key) || !counts.get(key).toString().matches("0|[1-9][0-9]*")) throw new IllegalArgumentException("Invalid diagnostic group count.");
                    sum = Math.addExact(sum, counts.get(key).getAsLong());
                }
                if (sum != global || total != (group.isEmpty() ? global : counts.get(group).getAsLong())) throw new IllegalArgumentException("Diagnostic group counts differ from total findings.");
            }
            long count = result.getAsJsonArray("findings").size(), end = offset + count;
            if (!revision(text(result, "id", "")) || !result.get("id").equals(result.get("diagnostic_id"))
                    || !result.get("id").equals(result.get("view_id")) || result.get("offset").getAsLong() != offset || offset < 0 || total < end || total > global
                    || (result.get("next_offset").isJsonNull() ? end != total : count == 0 || end >= total || result.get("next_offset").getAsLong() != end))
                throw new IllegalArgumentException("Diagnostic page differs from its count or offset.");
        }
        if (options != null && options.has("revision") && List.of("diagnostics", "diagnostic").contains(action)
                && !options.get("revision").equals(result.get("diagnostic_id"))) throw new IllegalArgumentException("Diagnostic response belongs to another revision.");
        if (action.equals("diagnostic") && !options.get("finding").equals(result.getAsJsonObject("finding").get("id")))
            throw new IllegalArgumentException("Diagnostic response belongs to another finding.");
    }
}
