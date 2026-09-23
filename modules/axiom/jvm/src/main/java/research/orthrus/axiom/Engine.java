package research.orthrus.axiom;

import java.util.*;
import static research.orthrus.axiom.Domain.*;

/** Public independent Java library entry point, also used by the isolated application worker. */
public final class Engine {
    public Map<String, Object> inspectTarget(java.nio.file.Path path, Object request) throws java.io.IOException {
        return inspectTarget(path, request, null);
    }
    public Map<String, Object> inspectTarget(java.nio.file.Path path, Object request, java.nio.file.Path artifacts) throws java.io.IOException {
        return inspectTarget(path, request, artifacts, null);
    }
    public Map<String, Object> inspectPlatform(java.nio.file.Path path, Object request) throws java.io.IOException {
        try { NativeRuntime.require(); return envelope("platform", "accepted", PlatformBundle.inspect(path, request)); }
        catch (Failure failure) { return failure("platform", failure); }
    }
    public Map<String, Object> inspectTarget(java.nio.file.Path path, Object request, java.nio.file.Path artifacts, java.nio.file.Path platform) throws java.io.IOException {
        try { NativeRuntime.require(); } catch (Failure failure) { return failure("target", failure); }
        try (SourceTarget target = new SourceTarget(path)) {
            Object platformRequest = null;
            if (request != null) {
                Map<String, Object> copy = new LinkedHashMap<>(Json.object(Json.parse(Json.write(request))));
                if (copy.containsKey("platform")) {
                    if (platform == null) throw Failure.request("Platform detail request requires an explicit platform archive");
                    platformRequest = copy.remove("platform");
                    if (platformRequest == null) throw Failure.request("Platform detail request must be an object");
                }
                request = copy;
            }
            Map<String, Object> body = target.inspect(request, artifacts);
            if (platform != null) {
                Map<String, Object> inspected = PlatformBundle.inspect(platform, platformRequest);
                if (!Objects.equals(Json.object(body.get("composition")).get("side"),
                        Json.object(Json.object(inspected.get("metadata")).get("selection")).get("side")))
                    throw Failure.request("Source composition and explicit platform must select the same physical side");
                body.put("platformInspection", PlatformBundle.summary(inspected, platformRequest));
                body.put("platformRelation", "explicit-platform-selection; no equivalence to the pack's declared Forge platform is asserted");
                Map<String, Object> identity = new LinkedHashMap<>();
                identity.put("candidateId", body.get("candidateId")); identity.put("compositionId", Json.object(body.get("composition")).get("compositionId"));
                identity.put("platformId", inspected.get("platformId")); identity.put("engineId", Target.ENGINE_ID);
                identity.put("artifactBundleId", artifacts == null ? null : Json.object(body.get("artifactInspection")).get("artifactBundleId"));
                body.put("inputCompositionId", "axiom-input-composition:sha256:" + Json.digest(identity));
            }
            return envelope("target", "accepted", body);
        } catch (Failure failure) { return failure("target", failure); }
    }
    public Map<String, Object> evaluate(String operation, Object raw) {
        NativeRuntime.require();
        if (operation.equals("coverage")) return envelope(operation, "accepted", Target.coverage());
        if (!Set.of("check", "query").contains(operation)) throw Failure.request("Unknown operation");
        raw = Json.parse(Json.write(raw)); // isolate caller-owned mutable inputs
        Map<String, Object> request = Json.object(raw);
        Json.keys(request, "schema", "targetId", "registry", "files", "machine", "loads", "queryKind");
        if (!"axiom.request.v1".equals(request.get("schema"))) throw Failure.request("Expected axiom.request.v1");
        if (!Target.ID.equals(request.get("targetId"))) throw Failure.request("Target identity differs from the installed source/rule lock; inspect coverage");
        List<Object> files = Json.array(request.get("files"));
        if (files.isEmpty() || files.size() > 64) throw Failure.request("A definition program needs 1..64 explicit source files");
        Registry registry = new Registry(request.get("registry"));
        Construction construction = new Construction(registry);
        GroovyProgram compiler = new GroovyProgram(construction);
        Set<String> paths = new HashSet<>();
        for (Object row : files) {
            Map<String, Object> file = Json.object(row); Json.keys(file, "path", "text");
            String path = Json.string(file.get("path"));
            if (!paths.add(path)) throw Failure.request("Duplicate source path: " + path);
            compiler.read(path, Json.string(file.get("text")));
        }
        String programId = "axiom-program:sha256:" + Json.digest(Map.of("engineId", Target.ENGINE_ID,
                "target", Target.ID, "runtimeId", NativeRuntime.require().get("runtimeId"), "registry", request.get("registry"), "files", files));
        List<Map<String, Object>> definitions = new ArrayList<>();
        for (Recipe recipe : construction.definitions) {
            Map<String, Object> entry = recipe.json();
            entry.put("registered", construction.registered.get(recipe.id()));
            entry.put("treeReachable", construction.trees.get(recipe.map()).contains(recipe));
            definitions.add(entry);
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("programId", programId); body.put("targetId", Target.ID); body.put("definitions", definitions);
        body.put("runtime", NativeRuntime.require());
        body.put("diagnostics", construction.diagnostics);
        body.put("scope", "explicit-definition-program/ordinary-mixer-idle-start");
        body.put("wholePackParity", false); body.put("installedCompositionQualified", false);
        body.put("assumptions", List.of("The supplied registry, membership and ordered ore lookup facts are explicit context, not a recovered pack registry",
                "This file sequence is the entire selected program; it excludes all other pack and mod recipe producers",
                "Source-bound MIXER/BLENDER bindings use the locked map-shape and callback projections; active installed mixins are not qualified"));
        String status = construction.diagnostics.stream().anyMatch(diag -> diag.get("status").equals("rejected")) ? "rejected" : "accepted";
        if (operation.equals("query")) {
            if (!"select-and-start".equals(request.get("queryKind"))) throw Failure.unsupported("query.kind", "Only tree/cache select-and-start is implemented; this is not a named-recipe validity claim");
            Mixer machine = new Mixer(request.get("machine"), registry, programId);
            machine.load(request.get("loads"));
            Map<String, Object> result = machine.start(construction, programId);
            body.put("machine", result); status = (String)result.get("status");
        } else if (request.containsKey("machine") || request.containsKey("loads") || request.containsKey("queryKind")) {
            throw Failure.request("Machine fields are only accepted by query");
        }
        return envelope(operation, status, body);
    }
    public Map<String, Object> run(String operation, Object raw) {
        try { return evaluate(operation, raw); }
        catch (Failure failure) { return failure(operation, failure); }
    }
    static Map<String, Object> envelope(String operation, String status, Map<String, Object> body) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("schema", "axiom.result.v1"); out.put("engineVersion", Target.VERSION); out.put("engineId", Target.ENGINE_ID);
        out.put("operation", operation); out.put("status", status); out.put("completion", "completed"); out.put("result", body);
        return out;
    }
    static Map<String, Object> failure(String operation, Failure failure) {
        Map<String, Object> result = envelope(operation, failure.kind, Map.of("rule", failure.rule, "message", failure.getMessage(),
                "wholePackParity", false, "installedCompositionQualified", false));
        result.put("completion", failure.kind.equals("incomplete") ? "incomplete" : "not-evaluated");
        return result;
    }
}
