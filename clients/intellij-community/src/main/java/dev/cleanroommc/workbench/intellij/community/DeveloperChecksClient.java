package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

final class DeveloperChecksClient {
    static List<String> arguments(String session, String action, String value, String consent) {
        return arguments(session, action, value, consent, null);
    }
    static List<String> arguments(String session, String action, String value, String consent, JsonObject recipe) {
        if (!session.matches("work-session-v2-[0-9a-f]{32}")) throw new IllegalArgumentException("Select an exact Work Session.");
        if (!List.of("images", "catalog", "recipes", "history", "compare", "prepare", "execute", "show", "source", "progress", "cancel", "log", "recover", "prepare-environment", "environment-show", "environment-cancel", "environment-recover").contains(action)) throw new IllegalArgumentException("Unknown check action.");
        var args = new ArrayList<>(List.of("context", "run", session, "--", "checks", action));
        if (action.contains("environment")) {
            if (value == null || !value.matches("environment-[0-9a-f]{32}")) throw new IllegalArgumentException("Select an exact environment attempt.");
            args.add(value);
            if (List.of("prepare-environment", "environment-recover").contains(action)) {
                if (consent == null || !consent.matches("environment-request:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Confirm the exact environment plan.");
                args.addAll(List.of("--confirm", consent));
            }
        }
        if (action.equals("prepare")) {
            if (value == null || !value.matches("runtime-image:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Select an exact runtime image.");
            args.addAll(List.of("--image", value));
            if (recipe != null) args.addAll(recipeArguments(recipe, false));
        } else if (action.equals("recipes")) {
            args.addAll(recipeArguments(recipe == null ? new JsonObject() : recipe, true));
        } else if (List.of("execute", "show", "source", "progress", "cancel", "log", "recover", "compare").contains(action)) {
            if (value == null || !value.matches("check-[0-9a-f]{32}")) throw new IllegalArgumentException("Select an exact check attempt.");
            args.add(value);
        }
        if (List.of("execute", "recover").contains(action)) {
            if (consent == null || !consent.matches("saved-check-request:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Confirm the exact check request.");
            args.addAll(List.of("--confirm", consent));
        } else if (action.equals("compare")) {
            if (consent == null || !consent.matches("check-[0-9a-f]{32}") || consent.equals(value)) throw new IllegalArgumentException("Select a distinct exact reference run.");
            args.addAll(List.of("--reference", consent));
        } else if (action.equals("source")) {
            if (consent == null || !consent.matches("[a-z0-9-]{1,64}:[0-9]{1,2}")) throw new IllegalArgumentException("Select one retained explanation source.");
            args.addAll(List.of("--source", consent));
        } else if (action.equals("log")) {
            if (consent == null || !consent.matches("(?:[a-zA-Z0-9._-]+/)+[a-zA-Z0-9._-]+") || java.util.Arrays.stream(consent.split("/")).anyMatch(part -> part.equals(".") || part.equals(".."))) throw new IllegalArgumentException("Select one retained log.");
            args.addAll(List.of("--path", consent));
        }
        return List.copyOf(args);
    }
    static JsonObject validate(JsonObject envelope, Path workspace) throws Exception {
        JsonObject result = envelope.getAsJsonObject("result");
        Path root = workspace.toRealPath();
        if (!envelope.get("format").getAsString().equals("workbench-developer-action-v1") || envelope.get("exit_code").getAsInt() != 0
                || !Path.of(URI.create(envelope.getAsJsonObject("context").getAsJsonObject("selection").get("pack_uri").getAsString())).equals(root)
                || result == null || (result.has("workspace_uri") && !Path.of(URI.create(result.get("workspace_uri").getAsString())).equals(root))) {
            throw new IllegalArgumentException("Check response changed the selected workspace.");
        }
        String format = result.get("format").getAsString();
        if (format.startsWith("workbench-saved-check-result-") && !format.equals("workbench-saved-check-result-v4")) throw new IllegalArgumentException("Prepare a new check with current provenance; historical formats are not migrated.");
        if (format.equals("workbench-saved-check-result-v4")) {
            var authority = result.getAsJsonObject("authority");
            for (String key : List.of("source_mutated", "construction_authorized", "qualification_granted")) {
                if (authority.get(key).getAsBoolean()) throw new IllegalArgumentException("Check result claims unexpected authority.");
            }
        }
        if (format.equals("workbench-check-comparison-v2")) {
            var authority = result.getAsJsonObject("authority");
            for (String key : List.of("runtime_launched", "source_mutated", "qualification_granted", "outcomes_promoted")) {
                if (authority == null || !authority.has(key) || authority.get(key).getAsBoolean()) throw new IllegalArgumentException("Comparison claims unexpected authority.");
            }
        }
        if (format.equals("workbench-saved-check-result-v4") && result.has("assertions") && !result.get("assertions").isJsonNull()) {
            var authority = result.getAsJsonObject("assertions").getAsJsonObject("authority");
            for (String key : List.of("source_mutated", "outcomes_promoted", "qualification_granted")) {
                if (authority == null || !authority.has(key) || authority.get(key).getAsBoolean()) throw new IllegalArgumentException("Recipe assertion claims unexpected authority.");
            }
        }
        if (List.of("workbench-environment-request-v1", "workbench-environment-result-v1").contains(result.get("format").getAsString())) {
            var authority = result.getAsJsonObject("authority");
            for (String key : List.of("runtime_launched", "source_mutated", "qualification_granted")) {
                if (authority == null || !authority.has(key) || authority.get(key).getAsBoolean()) throw new IllegalArgumentException("Preparation claims unexpected runtime authority.");
            }
        }
        return result;
    }
    static JsonObject invoke(CoreLaunch launch, Path workspace, String session, String action, String value, String consent) throws Exception {
        return invoke(launch, workspace, session, action, value, consent, null);
    }
    static JsonObject invoke(CoreLaunch launch, Path workspace, String session, String action, String value, String consent, JsonObject recipe) throws Exception {
        if (!launch.host().equals("native")) throw new IllegalArgumentException("Saved checks require a native Linux host.");
        // Cancellation is an explicit owner request, not destruction of this
        // transport while Core is supervising an independent runtime process.
        String raw = CommandProcess.capture(launch, arguments(session, action, value, consent, recipe), 32 * 1024 * 1024, List.of("execute", "prepare-environment").contains(action) ? 3700 : action.equals("progress") ? 5 : 300, workspace.toString());
        var envelope = JsonParser.parseString(raw).getAsJsonObject();
        var result = validate(envelope, workspace);
        result.addProperty("_sourceCurrent", envelope.has("presentation") && envelope.getAsJsonObject("presentation").has("source_current") && envelope.getAsJsonObject("presentation").get("source_current").getAsBoolean());
        if (envelope.has("presentation")) result.add("_presentation", envelope.get("presentation"));
        return result;
    }

    static String progressMessage(JsonObject result, String attempt, String request) {
        if (!result.get("format").getAsString().equals("workbench-check-live-status-v1") || !result.get("attempt_id").getAsString().equals(attempt) || !result.get("request_id").getAsString().equals(request)) throw new IllegalArgumentException("Progress belongs to another check.");
        if (!result.has("progress") || result.get("progress").isJsonNull()) return result.get("state").getAsString() + " · waiting for startup observations";
        var progress = result.getAsJsonObject("progress");
        if (!progress.get("attempt_id").getAsString().equals(attempt) || !progress.get("request_id").getAsString().equals(request) || !progress.getAsJsonObject("observation").get("format").getAsString().equals("workbench-check-observation-v1")) throw new IllegalArgumentException("Progress observation changed its request.");
        return progress.getAsJsonObject("observation").get("summary").getAsString() + " · " + result.get("state").getAsString();
    }

    static String provenanceSummary(JsonObject value) {
        if (!value.has("provenance")) return "Environment provenance unavailable";
        var provenance = value.getAsJsonObject("provenance");
        var runtime = provenance.getAsJsonObject("runtime");
        var pack = runtime.getAsJsonObject("pack");
        var platform = runtime.getAsJsonObject("platform");
        var source = value.has("source") ? value.getAsJsonObject("source") : value.getAsJsonObject("candidate");
        if (source.has("source")) source = source.getAsJsonObject("source");
        return pack.get("name").getAsString() + " " + pack.get("version").getAsString() + " · " + source.get("revision").getAsString()
                + (source.get("dirty").getAsBoolean() ? " · saved working-tree changes" : "")
                + "\n" + platform.get("id").getAsString() + " " + platform.get("version").getAsString() + " · Java " + platform.getAsJsonObject("java").get("JAVA_RUNTIME_VERSION").getAsString()
                + "\nImage " + provenance.getAsJsonObject("image").get("id").getAsString()
                + "\nLocal tags: " + provenance.getAsJsonObject("source_labels").get("local_tags") + "; upstream release/latest not verified";
    }

    static String comparisonSummary(JsonObject value) {
        return "Comparison " + value.get("state").getAsString() + " · reference " + value.getAsJsonObject("reference").get("state").getAsString()
                + " → candidate " + value.getAsJsonObject("candidate").get("state").getAsString()
                + "\n" + value.get("counts") + "\n" + value.get("reasons")
                + (value.has("assertions") && !value.get("assertions").isJsonNull() ? "\nRecipe observations: " + value.getAsJsonObject("assertions").get("state").getAsString() : "")
                + "\nObserved differences only; original outcomes are unchanged.";
    }
    static JsonObject recipeExplanation(JsonObject result) {
        if (!result.has("assertions") || result.get("assertions").isJsonNull()) return null;
        var assertion = result.getAsJsonObject("assertions");
        if (!assertion.has("observation")) return null;
        var detail = assertion.getAsJsonObject("observation").getAsJsonObject("details");
        if (!detail.has("explanation")) return null;
        var report = detail.getAsJsonObject("explanation");
        if (!report.get("format").getAsString().equals("workbench-check-explanation-v1") || !report.get("sections").isJsonArray() || !report.get("text").isJsonPrimitive()) throw new IllegalArgumentException("Unsupported retained recipe explanation.");
        return report;
    }
    static String recipeExplanationText(JsonObject result, JsonObject section) {
        var report = recipeExplanation(result);
        var assertion = result.getAsJsonObject("assertions");
        var text = new StringBuilder("Recipe assertion: " + assertion.get("state").getAsString() + "\nStartup: " + result.get("state").getAsString() + "\n");
        if (assertion.has("reasons")) for (var reason : assertion.getAsJsonArray("reasons")) text.append(reason.getAsString()).append('\n');
        text.append('\n');
        if (report == null) return text.append("No explanation was retained. Inspect the original assertion evidence; a fresh check is required for current diagnostics.").toString();
        if (section == null) return text.append(report.get("text").getAsString()).toString();
        text.append(section.get("text").getAsString()).append("\n\nLimits\n");
        for (var limit : report.getAsJsonArray("limitations")) text.append(limit.getAsString()).append('\n');
        return text.toString();
    }
    static com.google.gson.JsonArray findings(JsonObject result) {
        var rows = new com.google.gson.JsonArray();
        if (result.has("interpretation") && result.get("interpretation").isJsonObject()) rows.addAll(result.getAsJsonObject("interpretation").getAsJsonArray("findings"));
        var report = recipeExplanation(result);
        if (report == null) return rows;
        var assertion = result.getAsJsonObject("assertions");
        var expectation = assertion.getAsJsonObject("expectation");
        if (!assertion.get("state").getAsString().equals("mismatched") || !expectation.getAsJsonObject("source").get("candidate_id").equals(result.get("candidate_id"))) return rows;
        var message = new StringBuilder("Selected recipe expectation was not met in the captured runtime.");
        for (var element : report.getAsJsonArray("sections")) {
            var section = element.getAsJsonObject();
            if (!section.get("id").getAsString().startsWith("lookup-") || section.getAsJsonArray("properties").isEmpty()) continue;
            int count = 0;
            for (var value : section.getAsJsonArray("properties")) {
                if (++count > 4) break;
                var field = value.getAsJsonObject();
                message.append('\n').append(field.get("label").getAsString()).append(": expected ").append(field.get("expected").getAsString()).append("; observed ").append(field.get("observed").getAsString());
            }
            break;
        }
        message.append("\nOpen the recipe explanation for bounded lookup evidence; this is not a source-causation claim.");
        var finding = new JsonObject();
        finding.addProperty("code", "recipe-expectation-mismatch"); finding.addProperty("severity", "warning"); finding.addProperty("message", message.toString());
        finding.add("location", expectation.getAsJsonObject("subject").get("location")); rows.add(finding);
        return rows;
    }
    static List<String> recipeArguments(JsonObject value, boolean catalog) {
        var args = new ArrayList<String>();
        if (catalog && value.has("path")) {
            String path = value.get("path").getAsString();
            if (!path.matches("groovy/postInit/[^\\\\\\r\\n\\x00]+\\.groovy") || java.util.Arrays.stream(path.split("/", -1)).anyMatch(part -> part.isEmpty() || part.equals(".") || part.equals(".."))) throw new IllegalArgumentException("Select an exact postInit source path.");
            args.add("--path=" + path);
        }
        if (!catalog) {
            if (!value.has("recipe") || !value.get("recipe").getAsString().matches("saved-recipe:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Select one exact saved recipe.");
            args.addAll(List.of("--recipe", value.get("recipe").getAsString()));
            if (value.has("trace") && !value.get("trace").getAsBoolean()) args.add("--no-trace");
        }
        if (value.has("reference")) {
            String reference = value.get("reference").getAsString();
            if (!reference.matches("check-[0-9a-f]{32}")) throw new IllegalArgumentException("Select one exact reference attempt.");
            args.addAll(List.of(catalog ? "--reference" : "--recipe-reference", reference));
        }
        if (!catalog && value.has("absent") && value.get("absent").getAsBoolean()) {
            if (!value.has("reference")) throw new IllegalArgumentException("Absence requires retained source.");
            args.add("--absent");
        }
        return List.copyOf(args);
    }
    static String expectationSummary(JsonObject value) {
        if (!value.has("expectation") || value.get("expectation").isJsonNull()) return "Startup diagnostics only; no recipe expectation selected.";
        var expectation = value.getAsJsonObject("expectation"); var subject = expectation.getAsJsonObject("subject");
        return "Recipe expectation: " + expectation.get("mode").getAsString() + " · " + expectation.get("support").getAsString()
                + "\n" + subject.getAsJsonObject("location").get("path").getAsString() + ":" + subject.getAsJsonObject("location").getAsJsonObject("start").get("line").getAsInt()
                + "\n" + subject.get("recipe") + "\nExact registration and bounded lookup only; not gameplay or source-causation proof.";
    }
}
