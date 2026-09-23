package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;

/** Saved material transport and evidence presentation; no native rule simulation. */
final class MaterialChecksClient {
    static final String REQUEST = "workbench-material-check-request-v1";
    static final String RESULT = "workbench-material-check-result-v1";
    static final String SETUP = "workbench-material-check-setup-v1";
    static final String SETUP_STATUS = "workbench-material-check-setup-status-v1";
    private MaterialChecksClient() { }

    static String findingMessage(JsonObject finding) {
        var message = new StringBuilder(text(finding, "message", ""));
        if (finding.has("sourceRelationships")) for (var value : finding.getAsJsonArray("sourceRelationships")) {
            var relation = value.getAsJsonObject();
            if (text(relation, "kind", "").equals("exception-frame")) message
                    .append("\nNative exception at this source: ").append(text(relation, "type", ""))
                    .append(": ").append(text(relation, "message", "(no message)"));
        }
        return message.toString();
    }
    static String findingLabel(JsonObject result, JsonObject finding) {
        return text(object(object(result, "_presentation"), "finding_labels"), text(finding, "id", ""), findingMessage(finding));
    }

    static String attempt(String value) {
        if (value == null || !value.matches("material-check-[0-9a-f]{32}")) throw new IllegalArgumentException("Select an exact material-check attempt.");
        return value;
    }
    private static String relative(String value, boolean root) {
        if (root && value.equals(".")) return value;
        if (value.contains("\\") || value.contains(":") || value.matches("(?s).*[\\r\\n\\x00].*")
                || java.util.Arrays.stream(value.split("/", -1)).anyMatch(p -> List.of("", ".", "..", ".git").contains(p))) throw new IllegalArgumentException("Select a saved checkout-relative program or intent path.");
        return value;
    }
    static List<String> arguments(String session, String action, String value, String confirmation, JsonObject options) {
        if (session == null || !session.matches("work-session-v2-[0-9a-f]{32}")) throw new IllegalArgumentException("Select an exact Work Session.");
        if (!List.of("contexts", "setup", "setup-status", "prepare", "run", "execute", "show", "history", "source", "cancel", "query", "export", "retention", "delivery", "diagnostics", "diagnostic").contains(action)) throw new IllegalArgumentException("Unknown material-check action.");
        var args = new ArrayList<>(List.of("context", "run", session, "--", "checks", "materials", action));
        if (action.equals("retention")) {
            if (options == null || !List.of("status", "preview", "configure", "maintain").contains(text(options, "operation", ""))) throw new IllegalArgumentException("Select a Core retention operation.");
            args.add(options.get("operation").getAsString());
            if (options.has("settings")) args.addAll(List.of("--settings", options.get("settings").toString()));
            if (confirmation != null) {
                if (!confirmation.matches("check-retention-proposal:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Confirm an exact retention proposal.");
                args.addAll(List.of("--confirm", confirmation));
            }
        }
        if (List.of("setup", "setup-status", "prepare", "run").contains(action)) {
            if (options == null) throw new IllegalArgumentException("Select an installed profile-owned material context.");
            String context = text(options, "context", "");
            if (!context.matches("[a-z0-9.-]+:[a-z0-9.-]+")) throw new IllegalArgumentException("Select an installed profile-owned material context.");
            args.add("--context=" + context);
        }
        if (List.of("setup", "prepare", "run").contains(action)) {
            long supplied = List.of("engineHome", "runtimeHome", "java").stream().filter(options::has).count();
            if ((supplied != 0 && supplied != 3) || (action.equals("setup") && supplied != 3))
                throw new IllegalArgumentException("Provide all three native input paths or use the saved Core setup.");
            for (String[] field : List.of(new String[]{"engineHome", "engine-home"}, new String[]{"runtimeHome", "runtime-home"}, new String[]{"java", "java"})) {
                if (supplied == 0) break;
                String path = text(options, field[0], "");
                if (!path.startsWith("/") || path.matches("(?s).*[\\r\\n\\x00].*")) throw new IllegalArgumentException("Select explicit native Axiom inputs and the selected JDK bin/java.");
                args.add("--" + field[1] + "=" + path);
            }
            if (options.has("programRoot")) args.add("--program-root=" + relative(text(options, "programRoot", ""), true));
            if (!action.equals("setup") && options.has("intent")) {
                if (!options.get("intent").isJsonPrimitive() || !options.getAsJsonPrimitive("intent").isString())
                    throw new IllegalArgumentException("Saved expectations must be a checkout-relative path or an empty string.");
                String intent = options.get("intent").getAsString();
                if (!intent.isEmpty()) args.add("--request=" + relative(intent, false));
            }
            if (!action.equals("setup") && options.has("baseline")) args.add("--baseline=" + attempt(options.get("baseline").getAsString()));
        } else if (List.of("execute", "show", "source", "cancel", "query", "export", "delivery", "diagnostics", "diagnostic").contains(action)) args.add(attempt(value));
        MaterialDeliveryClient.arguments(args, action, options);
        if (action.equals("execute")) {
            if (confirmation == null || !confirmation.matches("material-check-request:sha256:[0-9a-f]{64}")) throw new IllegalArgumentException("Confirm the exact saved material request.");
            args.addAll(List.of("--confirm", confirmation));
        } else if (action.equals("source")) {
            if (confirmation == null || !confirmation.matches("diagnostic-(?:0|[1-9][0-9]*)")) throw new IllegalArgumentException("Select one retained material diagnostic.");
            args.addAll(List.of("--diagnostic", confirmation));
        } else if (action.equals("query")) {
            args.addAll(List.of("--query", options.toString()));
        } else if (action.equals("export")) {
            String destination = text(options, "destination", "");
            if (!destination.startsWith("/") || destination.matches("(?s).*[\\r\\n\\x00].*")) throw new IllegalArgumentException("Select a new absolute export file.");
            args.addAll(List.of("--snapshot", text(options, "snapshot_id", ""), "--destination", destination));
            if (options.has("section_id")) args.addAll(List.of("--section", text(options, "section_id", ""), "--key", text(options, "record_key", ""), "--sha256", text(options, "sha256", "")));
        }
        return List.copyOf(args);
    }
    static JsonObject validate(JsonObject envelope, Path root, String action, String attempt) throws Exception {
        var result = DeveloperChecksClient.validate(envelope, root);
        List<String> formats = switch (action) {
            case "contexts" -> List.of("workbench-material-contexts-v1");
            case "setup" -> List.of(SETUP);
            case "setup-status" -> List.of(SETUP_STATUS);
            case "prepare" -> List.of(REQUEST);
            case "execute", "run" -> List.of(RESULT, MaterialSnapshotClient.VIEW);
            case "show" -> List.of(REQUEST, RESULT, MaterialSnapshotClient.VIEW, MaterialDeliveryClient.VIEW);
            case "history" -> List.of("workbench-material-check-history-v1");
            case "source" -> List.of("workbench-material-source-view-v1");
            case "cancel" -> List.of("workbench-material-check-cancellation-v1");
            case "delivery" -> List.of(MaterialDeliveryClient.STATUS);
            case "diagnostics" -> List.of(MaterialDeliveryClient.VIEW);
            case "diagnostic" -> List.of("workbench-material-diagnostic-evidence-v1");
            case "query" -> List.of("workbench-check-snapshot-response-v1");
            case "export" -> List.of("workbench-material-export-v1");
            case "retention" -> List.of("workbench-check-retention-response-v1");
            default -> List.of();
        };
        String format = text(result, "format", "");
        if (!formats.contains(format)) throw new IllegalArgumentException("Unsupported material-check response.");
        if (List.of("execute", "show", "source", "cancel", "export", "delivery", "diagnostics", "diagnostic").contains(action) && !text(result, "attempt_id", "").equals(attempt)) throw new IllegalArgumentException("Material response belongs to another attempt.");
        if (List.of(REQUEST, RESULT, MaterialSnapshotClient.VIEW, MaterialDeliveryClient.VIEW, "workbench-material-contexts-v1").contains(format)) {
            var authority = object(result, "authority");
            for (String key : List.of("source_mutated", "minecraft_launched", "runtime_image_required", "validity_qualified", "whole_pack_parity")) {
                if (!authority.has(key) || !authority.get(key).isJsonPrimitive() || !authority.get(key).getAsJsonPrimitive().isBoolean() || authority.get(key).getAsBoolean()) throw new IllegalArgumentException("Material check claims unexpected authority.");
            }
        }
        if (format.equals(RESULT) && (!result.has("findings") || !result.get("findings").isJsonArray() || !List.of("completed", "incomplete").contains(text(result, "state", "")))) throw new IllegalArgumentException("Invalid retained material result.");
        MaterialSnapshotClient.attach(result);
        if (format.equals("workbench-material-source-view-v1") && (!result.has("read_only") || !result.get("read_only").getAsBoolean() || !result.has("text") || !result.get("text").getAsJsonPrimitive().isString())) throw new IllegalArgumentException("Material source must be retained read-only text.");
        if (List.of(SETUP, SETUP_STATUS).contains(format)) {
            validateSetup(result);
            if (!text(result, "selection_id", "").equals(text(object(envelope, "context"), "selection_id", "")))
                throw new IllegalArgumentException("Material setup belongs to another selected developer context.");
        }
        return result;
    }
    private static void validateSetup(JsonObject result) {
        if (!text(result, "selection_id", "").matches("developer-selection:sha256:[0-9a-f]{64}")
                || !text(result, "context_id", "").matches("[a-z0-9.-]+:[a-z0-9.-]+"))
            throw new IllegalArgumentException("Material setup lacks its selected context identity.");
        String state = text(result, "state", "");
        boolean configured = text(result, "format", "").equals(SETUP);
        if (configured ? !state.equals("configured-not-run") : !List.of("missing", "ready", "stale").contains(state))
            throw new IllegalArgumentException("Invalid material setup readiness.");
        if (!configured && !text(result, "readiness_scope", "").equals("saved-input-and-profile-bindings-only"))
            throw new IllegalArgumentException("Setup readiness must not claim native initialization validity.");
        if (configured || state.equals("ready")) {
            if (!text(result, configured ? "id" : "setup_id", "").matches("material-check-setup:sha256:[0-9a-f]{64}"))
                throw new IllegalArgumentException("Material setup identity is missing.");
            var paths = object(result, "paths");
            for (String key : List.of("engine_home", "runtime_home", "java")) {
                String path = text(paths, key, "");
                if (!path.startsWith("/") || path.matches("(?s).*[\\r\\n\\x00].*"))
                    throw new IllegalArgumentException("Material setup paths are missing or invalid.");
            }
            relative(text(result, "program_root", ""), true);
            if (!configured && (!result.has("failure") || !result.get("failure").isJsonNull()))
                throw new IllegalArgumentException("Ready material setup contains a failure.");
        }
    }
    static JsonObject invoke(CoreLaunch launch, Path root, String session, String action, String value, String confirmation, JsonObject options) throws Exception {
        if (!launch.host().equals("native")) throw new IllegalArgumentException("Material preflight requires native Linux Workbench.");
        // The Core owner request cancels native execution; never cancel this transport.
        // MVP resource targets are suspended; cancellation belongs to the retained attempt.
        String raw = CommandProcess.capture(launch, arguments(session, action, value, confirmation, options), 0, 0, root.toString());
        var envelope = JsonParser.parseString(raw).getAsJsonObject();
        var result = validate(envelope, root, action, value);
        if (action.equals("retention") && !text(result, "operation", "").equals(text(options, "operation", ""))) throw new IllegalArgumentException("Retention response belongs to another operation.");
        MaterialDeliveryClient.validate(result, action, options);
        if (action.equals("query")) MaterialSnapshotClient.validatePage(result, options);
        if (action.equals("export") && (!text(result, "state", "").equals("verified")
                || !options.get("destination").equals(result.get("destination"))
                || !options.get("snapshot_id").equals(object(result, "content").get("snapshot_id"))
                || options.has("sha256") && !options.get("sha256").equals(object(result, "content").get("sha256"))))
            throw new IllegalArgumentException("Export differs from selected content or destination.");
        if (List.of("setup", "setup-status").contains(action)
                && !text(result, "context_id", "").equals(text(options, "context", "")))
            throw new IllegalArgumentException("Material setup belongs to another requested context.");
        var presentation = object(envelope, "presentation");
        result.addProperty("_sourceCurrent", presentation.has("source_current") && presentation.get("source_current").getAsBoolean());
        result.add("_presentation", presentation);
        return result;
    }
    static JsonObject object(JsonObject value, String name) {
        return value != null && value.has(name) && value.get(name).isJsonObject() ? value.getAsJsonObject(name) : new JsonObject();
    }
    static String text(JsonObject value, String name, String fallback) {
        return value != null && value.has(name) && !value.get(name).isJsonNull() ? value.get(name).getAsString() : fallback;
    }
    private record Observation(String side, JsonObject nativeResult, String pointer) { }
    private static List<Observation> observations(JsonObject result) {
        if (!result.has("native") || !result.get("native").isJsonObject()) return List.of();
        var nativeResult = result.getAsJsonObject("native"); var body = object(nativeResult, "result");
        if (body.has("baseline") && body.has("candidate")) return List.of(new Observation("Baseline", body.getAsJsonObject("baseline"), "/result/baseline/result"), new Observation("Candidate", body.getAsJsonObject("candidate"), "/result/candidate/result"));
        return List.of(new Observation("Candidate", nativeResult, "/result"));
    }
    static String summary(JsonObject result) {
        if (text(result, "format", "").equals(REQUEST)) {
            var context = object(object(result, "inputs"), "context");
            return "Not run · " + object(result, "program").getAsJsonArray("files").size() + " complete program files\nContext: " + text(context, "id", "unknown")
                    + "\nQualification: " + text(context, "qualification", "not established") + "\nCandidate: " + object(result, "candidate").get("id").getAsString()
                    + (result.has("baseline") && !result.get("baseline").isJsonNull() ? "\nBoth programs run fresh with the current saved intent." : "");
        }
        var text = new StringBuilder("Workflow: " + text(object(result, "_presentation"), "attempt_state", text(result, "state", "unknown")) + " (not material validity)\n");
        if (text(result, "format", "").equals(MaterialSnapshotClient.VIEW)) text
                .append("Initialization: ").append(text(result, "native_outcome", "unavailable"))
                .append("; observation coverage: ").append(text(result, "coverage", "unavailable"))
                .append("\nRun: ").append(text(result, "attempt_id", "unavailable"))
                .append("\nSource: ").append(text(result, "candidate_id", "unavailable"))
                .append("\nContext: ").append(text(result, "context_id", "unavailable"))
                .append("\nSnapshot: ").append(text(result, "snapshot_id", "unavailable"))
                .append('\n').append(MaterialSnapshotClient.progress(result))
                .append("\nDetailed evidence: ").append(text(result, "detail_state", "not-loaded"))
                .append("; native overview: ").append(text(result, "overview_state", "not-loaded"))
                .append("\nReader interpretation: ").append(text(object(result, "interpretation"), "state", "not established"))
                .append("; unsupported sections: ").append(object(result, "interpretation").get("unsupported_sections")).append('\n');
        if (MaterialDeliveryClient.isView(result)) text.append("Initialization: ").append(text(result, "native_outcome", "unavailable"))
                .append("; observation coverage: ").append(text(result, "coverage", "unavailable"))
                .append("\nRun: ").append(text(result, "attempt_id", "unavailable"))
                .append("\nSource: ").append(text(result, "candidate_id", "unavailable"))
                .append("\nDiagnostics displayed: ").append(result.getAsJsonArray("findings").size()).append(" of ").append(result.get("findings_count"))
                .append("; complete diagnostic evidence is available\nSnapshot detail: ").append(text(result, "detail_state", "unavailable")).append('\n');
        if (result.has("failure") && result.get("failure").isJsonObject()) text.append("Invocation incomplete: ").append(text(result.getAsJsonObject("failure"), "message", "unknown")).append('\n');
        for (var observation : observations(result)) {
            var body = object(observation.nativeResult(), "result");
            var initialization = object(body, "initialization");
            if (text(initialization, "schema", "").equals("axiom.scoped-initialization.v1")) {
                text.append(observation.side()).append(" initialization (").append(text(initialization, "scope", "not established"))
                        .append("): ").append(text(initialization, "status", "incomplete"))
                        .append("\nNative error observed: ")
                        .append(initialization.has("nativeErrorObserved") && initialization.get("nativeErrorObserved").getAsBoolean() ? "yes" : "no")
                        .append("; this is not whole-pack validity.\n");
                if (initialization.has("recipeEffectsChecked")) text.append("Recipe registry effects checked: ")
                        .append(initialization.get("recipeEffectsChecked").getAsBoolean() ? "yes" : "no").append('\n');
            }
            text.append(observation.side()).append(" native status: ").append(text(observation.nativeResult(), "status", "unknown"))
                    .append("\nMaterial execution: ").append(text(body, "nativeOutcome", "not observed"))
                    .append("\nDeveloper intent: ").append(text(object(body, "expectations"), "status", "not evaluated"))
                    .append("\nQualification: ").append(text(body, "qualification", "not established")).append('\n');
            var assessment = object(body, "assessment");
            if (assessment.has("schema")) {
                var counts = object(assessment, "intentCounts");
                text.append("Execution checkpoint: ").append(text(assessment, "execution", "unavailable"))
                        .append("; coverage: ").append(text(assessment, "coverage", "unavailable"))
                        .append("\nExpectation counts: matched ").append(text(counts, "matched", "unavailable"))
                        .append("; mismatched ").append(text(counts, "mismatch", "unavailable"))
                        .append("; unsupported ").append(text(counts, "unsupported", "unavailable"))
                        .append("; not evaluated ").append(text(counts, "not-evaluated", "unavailable")).append('\n');
                if (assessment.has("reasons")) for (var value : assessment.getAsJsonArray("reasons")) {
                    var reason = value.getAsJsonObject();
                    text.append("Assessment [").append(text(reason, "code", "unknown")).append("]: ")
                            .append(text(reason, "message", "unavailable"))
                            .append("\n  Evidence: ").append(text(reason, "evidencePointer", "unavailable")).append('\n');
                }
            }
        }
        var comparison = object(object(object(result, "native"), "result"), "comparison");
        if (comparison.has("status")) text.append("Native observation comparison: ").append(comparison.get("status").getAsString()).append(" (not source causation)\n");
        var source = object(object(object(result, "native"), "result"), "sourceComparison");
        if (source.has("status")) text.append("Saved file comparison: ").append(source.get("status").getAsString()).append(" (not recipe validity)\n");
        var effects = object(object(object(result, "native"), "result"), "effectComparison");
        if (effects.has("status")) text.append("Native effect comparison: ").append(text(effects, "status", "unavailable"))
                .append(" (observed membership and selected properties only)\n");
        if (!result.has("_sourceCurrent") || !result.get("_sourceCurrent").getAsBoolean()) text.append("Current source differs or freshness is unavailable. Retained evidence is unchanged.\n");
        return text.append("No qualified material validity or whole-pack parity. No Minecraft launch.").toString();
    }
    static String report(JsonObject result) {
        var text = new StringBuilder(summary(result));
        var pair = object(object(result, "native"), "result"); var comparison = object(pair, "comparison");
        var source = object(pair, "sourceComparison");
        for (String kind : List.of("added", "removed", "modified")) if (source.has(kind))
            for (var path : source.getAsJsonArray(kind)) text.append("\nSaved file ").append(kind).append(": ").append(path.getAsString());
        if (comparison.has("status")) {
            text.append("\n\nComparison details\nMaterial execution outcome: ")
                    .append(text(object(object(pair, "baseline"), "result"), "nativeOutcome", "not observed"))
                    .append(" → ").append(text(object(object(pair, "candidate"), "result"), "nativeOutcome", "not observed")).append('\n');
            if (comparison.has("reasons")) for (var reason : comparison.getAsJsonArray("reasons")) text.append("Not comparable: ").append(reason.getAsString()).append('\n');
            if (comparison.has("changedSections")) for (var value : comparison.getAsJsonArray("changedSections")) {
                var section = value.getAsJsonObject();
                text.append("Changed observation: ").append(text(section, "field", "unknown"))
                        .append("\n  Baseline evidence: ").append(text(section, "baselinePointer", "unavailable"))
                        .append("\n  Candidate evidence: ").append(text(section, "candidatePointer", "unavailable")).append('\n');
            }
        }
        var effects = object(pair, "effectComparison");
        if (effects.has("status")) {
            text.append("\n\nNative registration effects — not proof of completed initialization\n");
            if (effects.has("reasons")) for (var reason : effects.getAsJsonArray("reasons"))
                text.append("Not comparable: ").append(reason.getAsString()).append('\n');
            for (var domain : object(effects, "domains").entrySet()) {
                var effect = domain.getValue().getAsJsonObject();
                text.append(domain.getKey()).append(": ").append(text(effect, "status", "not-comparable"));
                if (effect.has("membership")) text.append(" · ").append(text(effect, "membership", "unavailable"));
                text.append('\n');
                if (effect.has("reasons")) for (var reason : effect.getAsJsonArray("reasons"))
                    text.append("  Not comparable: ").append(reason.getAsString()).append('\n');
                for (String kind : List.of("added", "removed", "modified")) if (effect.has(kind))
                    for (var value : effect.getAsJsonArray(kind)) {
                        var entry = value.getAsJsonObject();
                        text.append("  ").append(kind).append(": ").append(text(entry, "identity", "unknown")).append('\n');
                        for (String[] field : List.of(new String[]{"baselinePointer", "Baseline evidence"},
                                new String[]{"candidatePointer", "Candidate evidence"},
                                new String[]{"baselineOwnerPointer", "Baseline owner registration"},
                                new String[]{"candidateOwnerPointer", "Candidate owner registration"}))
                            if (entry.has(field[0])) text.append("    ").append(field[1]).append(": ")
                                    .append(text(entry, field[0], "unavailable")).append('\n');
                    }
            }
            text.append("Custom variants are native definitions; owner Forge registration is separate. Queued fluids and recipe effects are not inferred.\n");
        }
        for (var observation : observations(result)) {
            var body = object(observation.nativeResult(), "result");
            var scope = object(body, "sourceScope");
            if (scope.has("selectedLoader")) {
                var deferred=scope.getAsJsonArray("deferredLoaders");
                int files=scope.getAsJsonArray("deferredSourceFiles").size();
                text.append("\n\n").append(observation.side()).append(" source scope\n")
                        .append(scope.has("initializationStage") ? "Selected native initialization stage: " : "Selected native loader: ")
                        .append(text(scope,scope.has("initializationStage") ? "initializationStage" : "selectedLoader","not established"))
                        .append(" (see execution checkpoint)\nDeferred loaders: ");
                if (deferred.isEmpty()) text.append("none declared");
                else text.append(deferred).append("; their recipe effects are not checked");
                text.append("\nDeferred source files: ").append(files)
                        .append(files==0 ? "\n" : ". Available for imports is not executed.\n");
            }
            var linkage = object(object(body, "bootstrap"), "compilerLinkage");
            if (linkage.has("status")) {
                text.append("\n\n").append(observation.side()).append(" native compiler linkage: ")
                        .append(text(linkage, "status", "unavailable")).append(" (not behavioral qualification)\n");
                if (linkage.has("missing")) {
                    int index = 0;
                    for (var element : linkage.getAsJsonArray("missing")) {
                        var missing = element.getAsJsonObject();
                        text.append("Missing ").append(text(missing, "kind", "dependency")).append(": ")
                                .append(text(missing, "target", "unavailable"));
                        if (missing.has("required")) text.append(" · requires ").append(text(missing, "required", "unavailable"));
                        text.append("\n  ").append(text(missing, "reason", "unavailable"))
                                .append("\n  Evidence: /bootstrap/compilerLinkage/missing/").append(index++).append('\n');
                    }
                }
            }
            var platform = object(object(body, "bootstrap"), "platformInitialization");
            if (text(platform, "status", "").equals("threw")) {
                text.append("\n\n").append(observation.side()).append(" native platform initialization failed; the material context is incomplete, not evidence of an invalid developer edit.\n");
                if (platform.has("causes")) {
                    int index = 0;
                    for (var element : platform.getAsJsonArray("causes")) {
                        var cause = element.getAsJsonObject();
                        text.append(text(cause, "type", "unknown")).append(": ").append(text(cause, "message", ""))
                                .append("\n  Evidence: /bootstrap/platformInitialization/causes/").append(index++).append('\n');
                    }
                }
            }
            var observer = object(body, "transformationObservation");
            if (text(observer, "status", "").equals("unavailable")) {
                text.append("\n\n").append(observation.side()).append(" transformation observation unavailable; this is not an empty transformer audit.\n");
                if (observer.has("causes")) {
                    int index = 0;
                    for (var element : observer.getAsJsonArray("causes")) {
                        var cause = element.getAsJsonObject();
                        text.append(text(cause, "type", "unknown")).append(": ").append(text(cause, "message", ""))
                                .append("\n  Evidence: /transformationObservation/causes/").append(index++).append('\n');
                    }
                }
            }
            text.append("\n\n").append(observation.side()).append(" expectations\n");
            var intent = object(body, "expectations");
            if (intent.has("checks")) for (var element : intent.getAsJsonArray("checks")) {
                var check = element.getAsJsonObject();
                text.append(text(check, "id", "unknown")).append(": ").append(text(check, "status", "unknown")).append(" · ").append(text(check, "material", "unknown"))
                        .append("\n  expected ").append(check.get("expected")).append("; observed ").append(check.has("observed") ? check.get("observed") : "not observed").append('\n');
            }
            var context = object(body, "context");
            text.append("Context: ").append(text(context, "id", "unavailable")).append("\nExcluded composition: ").append(context.get("excludedComposition"));
            text.append("\nCoverage gaps: ").append(object(body, "execution").get("coverageGaps")).append('\n');
            var execution = object(body, "execution"); var lifecycle = object(execution, "lifecycle");
            if (execution.has("diagnostics")) {
                int diagnosticIndex = 0;
                for (var value : execution.getAsJsonArray("diagnostics")) {
                    var diagnostic = value.getAsJsonObject();
                    String pointer = observation.pointer() + "/execution/diagnostics/" + diagnosticIndex++;
                    String trace = text(diagnostic, "trace", "");
                    if (!trace.isEmpty()) text.append('\n').append(observation.side()).append(" native log: ")
                            .append(text(diagnostic, "logger", "")).append(" · ").append(text(diagnostic, "severity", ""))
                            .append(" · ").append(text(diagnostic, "message", ""))
                            .append("\nOriginal native trace; no verified source location:\n").append(trace)
                            .append("\n  Evidence: ").append(pointer).append("/trace\n");
                    if (!diagnostic.has("causality")) continue;
                    var graph = object(diagnostic, "causality");
                    text.append('\n').append(observation.side()).append(" native diagnostic: ").append(text(diagnostic, "message", ""))
                            .append("\nException relationships are native evidence, not inferred source blame.\n");
                    int id = 0;
                    for (var node : graph.getAsJsonArray("exceptions")) {
                        var exception = node.getAsJsonObject();
                        text.append("Exception ").append(id);
                        if (graph.has("root") && !graph.get("root").isJsonNull() && graph.get("root").getAsInt() == id) text.append(" (reported throwable)");
                        text.append(": ").append(text(exception, "type", "unknown")).append(": ").append(text(exception, "message", "(no message)"))
                                .append("\n  Cause: ").append(text(exception, "cause", "none")).append("; suppressed: ").append(exception.get("suppressed"))
                                .append("\n  Evidence: ").append(pointer).append("/causality/exceptions/").append(id).append('\n');
                        int frame = 0;
                        for (var location : exception.getAsJsonArray("locations")) {
                            var at = location.getAsJsonObject();
                            text.append("  Exception frame: ").append(text(at, "path", "unknown")).append(':').append(text(at, "line", "unknown"))
                                    .append(" · ").append(text(at, "class", "unknown")).append('#').append(text(at, "method", "unknown"))
                                    .append("\n  Evidence: ").append(pointer).append("/causality/exceptions/").append(id).append("/locations/").append(frame++).append('\n');
                        }
                        id++;
                    }
                    int frame = 0;
                    for (var location : diagnostic.getAsJsonArray("observationLocations")) {
                        var at = location.getAsJsonObject();
                        text.append("Observation site (not exception origin): ").append(text(at, "path", "unknown")).append(':').append(text(at, "line", "unknown"))
                                .append("\n  Evidence: ").append(pointer).append("/observationLocations/").append(frame++).append('\n');
                    }
                    if (diagnostic.has("compilerFindings")) {
                        int finding = 0;
                        for (var compiler : diagnostic.getAsJsonArray("compilerFindings")) {
                            var item = compiler.getAsJsonObject();
                            text.append("Compiler finding for exception ").append(text(item, "exceptionIndex", "unknown")).append(": ").append(text(item, "message", ""))
                                    .append("\n  Evidence: ").append(pointer).append("/compilerFindings/").append(finding++).append('\n');
                        }
                    }
                }
            }
            if (lifecycle.has("checkpoints")) {
                text.append("\nLifecycle (").append(text(lifecycle, "scope", "unavailable")).append(")\n");
                for (var value : lifecycle.getAsJsonArray("checkpoints")) {
                    var point = value.getAsJsonObject();
                    text.append(text(point, "checkpoint", "unknown")).append(": ").append(text(point, "phase", "unknown"))
                            .append(" · owner ").append(text(point, "activeOwner", "none")).append('\n');
                }
            }
            var progress = object(execution, "contentProgress");
            if (progress.has("phase")) {
                text.append("\nNative content: ").append(text(progress, "phase", "unknown"))
                        .append("; last completed phase: ").append(text(progress, "lastCompletedPhase", "unknown")).append('\n');
                if (progress.has("completedCheckpoints")) for (var point : progress.getAsJsonArray("completedCheckpoints")) {
                    text.append("Completed ").append(text(point.getAsJsonObject(), "phase", "unknown"))
                            .append(": ").append(contentCounts(point.getAsJsonObject())).append('\n');
                }
                if (progress.has("failedPhase")) text.append("Stopped during ").append(text(progress, "failedPhase", "unknown"))
                        .append(": ").append(text(object(progress, "failure"), "type", "unknown failure"))
                        .append(" · ").append(text(object(progress, "failure"), "message", "")).append('\n');
                if (progress.has("interruptedState")) text.append("Interrupted state (partial inventory): ")
                        .append(contentCounts(object(progress, "interruptedState"))).append('\n');
            }
            var work = object(execution, "deferredWork");
            if (work.has("schema")) {
                text.append("\nDeferred native work · ").append(text(work, "phase", "unknown"))
                        .append("\nFluid registration executed: ").append(work.get("fluidRegistrationExecuted"))
                        .append("; recipe handlers executed: ").append(work.get("recipeHandlersExecuted")).append('\n');
                if (work.has("fluids")) for (var value : work.getAsJsonArray("fluids")) {
                    var fluid = value.getAsJsonObject(); text.append(text(fluid, "material", "unknown")).append(": ");
                    if (fluid.get("hasFluidProperty").getAsBoolean()) {
                        var queued = new ArrayList<String>(); var stored = new ArrayList<String>();
                        for (var entry : fluid.getAsJsonArray("queued")) queued.add(text(entry.getAsJsonObject(), "key", "unknown"));
                        for (var entry : fluid.getAsJsonArray("stored")) stored.add(text(entry.getAsJsonObject(), "key", "unknown") + "=" + text(entry.getAsJsonObject(), "fluid", "unknown"));
                        text.append("queued ").append(queued.isEmpty() ? "none" : String.join(", ", queued))
                                .append("; stored ").append(stored.isEmpty() ? "none" : String.join(", ", stored));
                    } else text.append("no native fluid property");
                    text.append('\n');
                }
                int pending = 0;
                if (work.has("prefixProcessing")) for (var prefix : work.getAsJsonArray("prefixProcessing")) pending += prefix.getAsJsonObject().getAsJsonArray("pendingMaterials").size();
                text.append("Pending prefix/material memberships: ").append(work.has("prefixProcessing") ? Integer.toString(pending) : "not observed").append(" (").append(text(work, "prefixScope", "unknown"))
                        .append(")\nQueue membership is not processing order or proof that handlers ran.\n");
            }
        }
        if (result.has("findings")) for (var element : result.getAsJsonArray("findings")) {
            var finding = element.getAsJsonObject(); var location = object(finding, "location");
            text.append('\n').append(text(finding, "side", "unknown")).append(" · ").append(text(finding, "severity", text(finding, "channel", "unknown")))
                    .append(" · ").append(findingLabel(result, finding)).append('\n');
            if (location.has("path")) text.append(location.get("path").getAsString()).append(':').append(object(location, "start").get("line")).append(" (native line anchor)\n");
            else text.append("No verified source location\n");
            text.append("Native evidence: ").append(text(finding, "pointer", "unknown")).append('\n');
            if (finding.has("sourceRelationships")) for (var item : finding.getAsJsonArray("sourceRelationships")) {
                var relation = item.getAsJsonObject();
                text.append("  Source relationship: ").append(text(relation, "kind", "unknown"));
                if (relation.has("exceptionIndex")) text.append(" · exception ").append(text(relation, "exceptionIndex", "unknown"));
                text.append("\n  Evidence: ").append(text(relation, "locationPointer", "unknown")).append('\n');
            }
        }
        return text.toString();
    }
    private static String contentCounts(JsonObject point) {
        var values = new ArrayList<String>();
        String[][] fields = {{"variants", "item variants"}, {"registeredBlocks", "registered material blocks"},
                {"registeredBlockItems", "registered material block items"}, {"registeredOreBlocks", "registered ore blocks"},
                {"registeredOreItems", "registered ore items"}};
        for (var field : fields) if (point.has(field[0])) values.add(field[1] + " " + point.get(field[0]));
        return values.isEmpty() ? "counts not observed" : String.join("; ", values);
    }
    static String retainedSource(JsonObject view, JsonObject result, JsonObject finding) throws Exception {
        String text = text(view, "text", "");
        String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
        if (!text(view, "format", "").equals("workbench-material-source-view-v1") || !view.has("read_only") || !view.get("read_only").getAsBoolean()
                || !view.get("result_id").equals(result.get("id")) || !view.get("attempt_id").equals(result.get("attempt_id"))
                || !finding.equals(view.get("source")) || !digest.equals(object(finding, "location").get("sha256").getAsString())) throw new IllegalArgumentException("Retained source differs from the selected material diagnostic.");
        return text;
    }
}
