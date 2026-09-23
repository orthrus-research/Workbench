package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;

/** Saved-source transport; no domain interpretation, mutation, or construction. */
final class LocalReviewClient {
    static List<String> arguments(String session, String baseline, String expected) {
        if (!session.matches("work-session-v2-[0-9a-f]{32}")) throw new IllegalArgumentException("Select one exact Work Session ID.");
        if (baseline.isEmpty() || baseline.length() > 1024 || baseline.indexOf('\0') >= 0
                || baseline.contains("\n") || baseline.contains("\r")) throw new IllegalArgumentException("Select an explicit local Git baseline.");
        var result = new ArrayList<>(List.of("context", "run", session, "--", "review", "local", "--baseline-ref=" + baseline));
        if (expected != null) {
            if (!expected.matches("source-review:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Invalid review identity.");
            result.addAll(List.of("--expect-review", expected));
        }
        return List.copyOf(result);
    }

    static JsonObject validate(JsonObject envelope, Path workspace) throws Exception {
        JsonObject result = envelope.getAsJsonObject("result");
        Path root = workspace.toRealPath();
        if (!envelope.get("format").getAsString().equals("workbench-developer-action-v1")
                || envelope.get("exit_code").getAsInt() != 0
                || !Path.of(URI.create(envelope.getAsJsonObject("context").getAsJsonObject("selection").get("pack_uri").getAsString())).equals(root)
                || !result.get("format").getAsString().equals("workbench-local-source-review-v1")
                || !result.get("review_id").getAsString().matches("source-review:sha256:[0-9a-f]{64}")
                || !result.getAsJsonObject("baseline").get("revision").getAsString().matches("[0-9a-f]{40}([0-9a-f]{24})?")
                || !Path.of(URI.create(result.getAsJsonObject("baseline").get("root_uri").getAsString())).equals(root)
                || !Path.of(URI.create(result.getAsJsonObject("candidate").get("root_uri").getAsString())).equals(root)
                || !result.getAsJsonObject("authority").get("runtime_authority").getAsString().equals("none")
                || result.getAsJsonObject("authority").get("source_mutated").getAsBoolean()
                || result.getAsJsonObject("authority").get("construction_authorized").getAsBoolean()) {
            throw new IllegalArgumentException("Local review changed the selected workspace or source-only contract.");
        }
        for (String field : List.of("files", "changes", "findings", "checks", "relationships")) {
            if (!result.get(field).isJsonArray()) throw new IllegalArgumentException("Invalid local review collection.");
        }
        for (var value : result.getAsJsonArray("files")) {
            String path = value.getAsJsonObject().get("path").getAsString();
            if (path.isEmpty() || path.contains("\\") || path.contains(":") || path.indexOf('\0') >= 0 || path.contains("\n") || path.contains("\r")) {
                throw new IllegalArgumentException("Unsafe reviewed path.");
            }
            for (String part : path.split("/", -1)) if (part.isEmpty() || part.equals(".") || part.equals("..")) throw new IllegalArgumentException("Unsafe reviewed path.");
        }
        return result;
    }

    static JsonObject invoke(CoreLaunch launch, Path workspace, String session, String baseline, String expected, BooleanSupplier cancelled) throws Exception {
        if (!launch.host().equals("native")) throw new IllegalArgumentException("Local review currently requires a native Linux Workbench host.");
        String raw = CommandProcess.capture(launch, arguments(session, baseline, expected), 16 * 1024 * 1024, 120, workspace.toString(), cancelled);
        return validate(JsonParser.parseString(raw).getAsJsonObject(), workspace);
    }
}
