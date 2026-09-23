package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import static org.junit.jupiter.api.Assertions.*;

public class MaterialChecksClientTest {
    @TempDir Path root;
    private static final String SESSION = "work-session-v2-" + "a".repeat(32), ATTEMPT = "material-check-" + "b".repeat(32), REQUEST = "material-check-request:sha256:" + "c".repeat(64);
    static JsonObject result() {
        var result = JsonParser.parseString("""
          {"format":"workbench-material-check-result-v1","state":"completed","id":"result","findings":[],
           "authority":{"source_mutated":false,"minecraft_launched":false,"runtime_image_required":false,"validity_qualified":false,"whole_pack_parity":false},
           "native":{"status":"rejected","result":{"nativeOutcome":"completed-without-observed-error","qualification":"pending-native-program-acceptance",
             "expectations":{"status":"mismatch","checks":[{"id":"plate","material":"supersymmetry:test","status":"mismatch","expected":true,"observed":false}]}}}}
          """).getAsJsonObject();
        result.addProperty("attempt_id", ATTEMPT); return result;
    }
    @Test void completeRecipeScopeRetainsNativePackErrorsWithoutSourceErrorHeadline() {
        var value=result();
        var nativeResult=value.getAsJsonObject("native");nativeResult.addProperty("status","rejected");
        var body=nativeResult.getAsJsonObject("result");body.addProperty("nativeOutcome","native-failed");
        body.add("initialization",JsonParser.parseString("""
          {"schema":"axiom.scoped-initialization.v1","scope":"original-preinit-through-available-recipe-registries",
           "status":"native-failed","nativeScopeQualified":true,"recipeEffectsChecked":true,"nativeErrorObserved":true}
          """));
        body.add("sourceScope",JsonParser.parseString("{\"selectedLoader\":\"preInit\",\"initializationStage\":\"recipes\",\"deferredLoaders\":[],\"deferredSourceFiles\":[]}"));
        String before=value.toString(), report=MaterialChecksClient.report(value);
        assertTrue(report.contains("initialization (original-preinit-through-available-recipe-registries): native-failed"));
        assertTrue(report.contains("Native error observed: yes"));
        assertTrue(report.contains("Recipe registry effects checked: yes"));
        assertTrue(report.contains("Selected native initialization stage: recipes"));
        assertFalse(report.contains("source-error"));assertFalse(report.contains("their recipe effects are not checked"));
        assertEquals(before,value.toString());
    }
    @Test void savedRecipeRemovalAndDeferredScopeDoNotClaimNativeValidity() {
        var value=result();var nativeResult=value.getAsJsonObject("native").deepCopy();
        nativeResult.getAsJsonObject("result").add("sourceScope",JsonParser.parseString("""
          {"selectedLoader":"preInit","deferredLoaders":["postInit"],"deferredSourceFiles":["groovy/postInit/Recipe.groovy"]}
          """));
        var pair=new JsonObject();pair.add("baseline",nativeResult);pair.add("candidate",nativeResult.deepCopy());
        pair.add("comparison",JsonParser.parseString("{\"status\":\"not-comparable\"}"));
        pair.add("sourceComparison",JsonParser.parseString("""
          {"status":"changed","added":[],"modified":[],"removed":["groovy/postInit/Recipe.groovy"]}
          """));
        var envelope=new JsonObject();envelope.add("result",pair);value.add("native",envelope);
        String before=value.toString(), report=MaterialChecksClient.report(value);
        assertTrue(report.contains("Saved file removed: groovy/postInit/Recipe.groovy"));
        assertTrue(report.contains("Saved file comparison: changed (not recipe validity)"));
        assertTrue(report.contains("their recipe effects are not checked"));
        assertTrue(report.contains("Native observation comparison: not-comparable"));
        assertEquals(before,value.toString());
    }
    @Test void explicitProgramHasNoImageAndExecutionRequiresExactConsent() {
        var options = JsonParser.parseString("""
          {"engineHome":"/tools/axiom engine","runtimeHome":"/inputs/runtime","java":"/jdk/bin/java","programRoot":"authoring/example","intent":"checks/materials.json","context":"supersymmetry:material-authoring-gt-base"}
          """).getAsJsonObject();
        options.addProperty("baseline", ATTEMPT);
        var args = MaterialChecksClient.arguments(SESSION, "prepare", null, null, options);
        assertTrue(args.contains("--engine-home=/tools/axiom engine")); assertTrue(args.contains("--baseline=" + ATTEMPT));
        assertFalse(args.contains("--image")); assertFalse(args.contains("--confirm"));
        assertTrue(MaterialChecksClient.arguments(SESSION, "execute", ATTEMPT, REQUEST, null).contains(REQUEST));
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "execute", ATTEMPT, "yes", null));
        options.remove("intent");
        assertTrue(MaterialChecksClient.arguments(SESSION, "prepare", null, null, options).stream().noneMatch(arg -> arg.startsWith("--request")));
        options.addProperty("intent", "");
        assertTrue(MaterialChecksClient.arguments(SESSION, "prepare", null, null, options).stream().noneMatch(arg -> arg.startsWith("--request")));
        options.add("intent", com.google.gson.JsonNull.INSTANCE);
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "prepare", null, null, options));
        options.remove("intent");
        options.addProperty("programRoot", "../escape");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "prepare", null, null, options));
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "source", ATTEMPT, "../../file", null));
    }
    @Test void responseRetainsAuthorityAndExactAttempt() throws Exception {
        var value = result();
        var envelope = JsonParser.parseString("{\"format\":\"workbench-developer-action-v1\",\"exit_code\":0,\"context\":{\"selection\":{}}}").getAsJsonObject();
        envelope.getAsJsonObject("context").getAsJsonObject("selection").addProperty("pack_uri", root.toRealPath().toUri().toString());
        envelope.add("result", value);
        assertSame(value, MaterialChecksClient.validate(envelope, root, "show", ATTEMPT));
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.validate(envelope, root, "show", "other"));
        value.getAsJsonObject("authority").addProperty("validity_qualified", true);
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.validate(envelope, root, "show", ATTEMPT));
    }
    @Test void configuredRerunNeedsOnlyContextAndOverridesRemainAllOrNone() {
        var options = JsonParser.parseString("{\"context\":\"supersymmetry:material-authoring-pack\"}").getAsJsonObject();
        for (String action : java.util.List.of("setup-status", "prepare", "run")) {
            var args = MaterialChecksClient.arguments(SESSION, action, null, null, options);
            assertTrue(args.contains("--context=supersymmetry:material-authoring-pack"));
            assertTrue(args.stream().noneMatch(arg -> arg.startsWith("--engine-home") || arg.startsWith("--runtime-home")
                    || arg.startsWith("--java") || arg.startsWith("--program-root") || arg.startsWith("--request")));
        }
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "setup", null, null, options));
        options.addProperty("java", "/jdk/bin/java");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.arguments(SESSION, "prepare", null, null, options));
        options.addProperty("engineHome", "/native/engine"); options.addProperty("runtimeHome", "/native/runtime");
        options.addProperty("programRoot", "."); options.addProperty("intent", "intent.json"); options.addProperty("baseline", ATTEMPT);
        var setup = MaterialChecksClient.arguments(SESSION, "setup", null, null, options);
        assertTrue(setup.contains("--engine-home=/native/engine"));
        assertTrue(setup.contains("--runtime-home=/native/runtime")); assertTrue(setup.contains("--java=/jdk/bin/java"));
        assertTrue(setup.stream().noneMatch(arg -> arg.startsWith("--request") || arg.startsWith("--baseline")));
    }
    @Test void setupReadinessRequiresExplicitCurrentBindingsWithoutQualification() throws Exception {
        var value = JsonParser.parseString("""
          {"format":"workbench-material-check-setup-status-v1","state":"ready",
           "context_id":"supersymmetry:material-authoring-pack","program_root":".","failure":null,
           "paths":{"engine_home":"/native/engine","runtime_home":"/native/runtime","java":"/jdk/bin/java"},
           "readiness_scope":"saved-input-and-profile-bindings-only"}
          """).getAsJsonObject();
        value.addProperty("selection_id", "developer-selection:sha256:" + "d".repeat(64));
        value.addProperty("setup_id", "material-check-setup:sha256:" + "e".repeat(64));
        var envelope = JsonParser.parseString("{\"format\":\"workbench-developer-action-v1\",\"exit_code\":0,\"context\":{\"selection\":{}}}").getAsJsonObject();
        envelope.getAsJsonObject("context").add("selection_id", value.get("selection_id"));
        envelope.getAsJsonObject("context").getAsJsonObject("selection").addProperty("pack_uri", root.toRealPath().toUri().toString());
        envelope.add("result", value);
        assertSame(value, MaterialChecksClient.validate(envelope, root, "setup-status", null));
        envelope.getAsJsonObject("context").addProperty("selection_id", "developer-selection:sha256:" + "f".repeat(64));
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.validate(envelope, root, "setup-status", null));
        envelope.getAsJsonObject("context").add("selection_id", value.get("selection_id"));
        value.addProperty("readiness_scope", "native-initialization-qualified");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.validate(envelope, root, "setup-status", null));
        value.addProperty("readiness_scope", "saved-input-and-profile-bindings-only");
        value.getAsJsonObject("paths").remove("java");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.validate(envelope, root, "setup-status", null));
        value.addProperty("state", "stale");
        value.add("failure", JsonParser.parseString("{\"message\":\"Java changed\",\"meaning\":\"setup-unavailable-not-source-invalidity\"}"));
        assertSame(value, MaterialChecksClient.validate(envelope, root, "setup-status", null));
    }
    @Test void nativeCauseGraphKeepsMessagesSourceMembershipAndBothSidePointers() {
        var value = result(); var nativeResult = value.getAsJsonObject("native").deepCopy();
        nativeResult.getAsJsonObject("result").add("execution", JsonParser.parseString("""
          {"diagnostics":[{"message":"native wrapper","causality":{"root":0,"exceptions":[
            {"type":"Wrapper","message":"outer","cause":1,"suppressed":[1],"locations":[]},
            {"type":"NativeFailure","message":null,"cause":0,"suppressed":[],"locations":[
              {"path":"groovy/classes/Helper.groovy","line":8,"class":"classes.Helper","method":"fail"}]}]},
            "observationLocations":[{"path":"groovy/classes/Helper.groovy","line":8}],
            "compilerFindings":[{"exceptionIndex":1,"message":"native compiler detail"}]}]}
          """));
        var pair = new JsonObject(); var body = new JsonObject(); pair.add("result", body);
        body.add("baseline", nativeResult); body.add("candidate", nativeResult.deepCopy()); value.add("native", pair);
        var before = value.deepCopy(); String text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Exception 0 (reported throwable): Wrapper: outer"));
        assertTrue(text.contains("Exception 1: NativeFailure: (no message)"));
        assertTrue(text.contains("Observation site (not exception origin): groovy/classes/Helper.groovy:8"));
        assertTrue(text.contains("/result/baseline/result/execution/diagnostics/0/causality/exceptions/1/locations/0"));
        assertTrue(text.contains("/result/candidate/result/execution/diagnostics/0/causality/exceptions/1/locations/0"));
        assertTrue(text.contains("Compiler finding for exception 1: native compiler detail"));
        assertEquals(before, value);
    }
    @Test void earlyLog4jTraceRetainsOriginalTextAndBothSidePointersWithoutSourceAttribution() {
        var value = result(); var nativeResult = value.getAsJsonObject("native").deepCopy();
        var diagnostic = new JsonObject();
        String trace = "NativeFailure\n at groovy.material.A.run(A.groovy:8)";
        diagnostic.addProperty("logger", "FML"); diagnostic.addProperty("severity", "warning");
        diagnostic.addProperty("message", "native warning"); diagnostic.addProperty("trace", trace);
        diagnostic.addProperty("locationStatus", "unlocated");
        var diagnostics = new com.google.gson.JsonArray(); diagnostics.add(diagnostic);
        var execution = new JsonObject(); execution.add("diagnostics", diagnostics);
        nativeResult.getAsJsonObject("result").add("execution", execution);
        var pair = new JsonObject(); var body = new JsonObject(); pair.add("result", body);
        body.add("baseline", nativeResult); body.add("candidate", nativeResult.deepCopy()); value.add("native", pair);
        var before = value.deepCopy(); String text = MaterialChecksClient.report(value);
        assertTrue(text.contains(trace));
        assertTrue(text.contains("Original native trace; no verified source location:"));
        for (String side : new String[]{"baseline", "candidate"}) {
            assertTrue(text.contains(Character.toUpperCase(side.charAt(0)) + side.substring(1) + " native log: FML · warning · native warning"));
            assertTrue(text.contains("/result/" + side + "/result/execution/diagnostics/0/trace"));
        }
        assertEquals(before, value);
    }
    @Test void findingTextRetainsWrapperAndOnlyExceptionsWithNativeSourceMembership() {
        var finding = JsonParser.parseString("""
          {"message":"java.lang.reflect.InvocationTargetException: null","sourceRelationships":[
            {"kind":"exception-frame","type":"java.lang.IllegalArgumentException","message":"Harvest Level must be greater than zero!"},
            {"kind":"exception-frame","type":"NativeFailure","message":null},
            {"kind":"observation-site","message":"not an exception at this source"}]}
          """).getAsJsonObject();
        var before = finding.deepCopy(); String text = MaterialChecksClient.findingMessage(finding);
        assertTrue(text.startsWith(finding.get("message").getAsString()));
        assertTrue(text.contains("Native exception at this source: java.lang.IllegalArgumentException: Harvest Level must be greater than zero!"));
        assertTrue(text.contains("NativeFailure: (no message)"));
        assertFalse(text.contains("not an exception at this source"));
        assertEquals(before, finding);
    }
    @Test void readableReportDoesNotEquateCompletedWithValid() {
        String text = MaterialChecksClient.report(result());
        assertTrue(text.contains("Workflow: completed (not material validity)"));
        assertTrue(text.contains("native status: rejected")); assertTrue(text.contains("Developer intent: mismatch"));
        assertTrue(text.contains("expected true; observed false")); assertTrue(text.contains("Qualification: pending-native-program-acceptance"));
        assertTrue(text.contains("No qualified material validity"));
    }
    @Test void scopedInitializationAndIdentityEffectsStaySeparateAndImmutable() {
        var value = result(); var observed = value.getAsJsonObject("native").deepCopy();
        observed.addProperty("status", "incomplete");
        observed.getAsJsonObject("result").add("initialization", JsonParser.parseString("""
          {"schema":"axiom.scoped-initialization.v1","scope":"preInit","status":"incomplete",
           "nativeErrorObserved":true,"executionCheckpoint":"stopped","coverage":"incomplete","wholePackValidity":false}
          """));
        var pair = new JsonObject(); pair.add("baseline", observed); pair.add("candidate", observed.deepCopy());
        pair.add("effectComparison", JsonParser.parseString("""
          {"schema":"axiom.registration-effect-comparison.v1","status":"incomplete","recipeEffectsChecked":false,
           "domains":{"materials":{"status":"not-comparable","reasons":["Complete inventory unavailable"]},
             "customItems":{"status":"changed","membership":"native-custom-meta-item-variant-definition",
               "added":[],"removed":[],"modified":[{"identity":"susy:items#4",
                 "baselinePointer":"/baseline/result/execution/customMetaItems/items/0/variants/0",
                 "candidatePointer":"/candidate/result/execution/customMetaItems/items/0/variants/0",
                 "candidateOwnerPointer":"/candidate/result/execution/customMetaItems/items/0"}]},
             "fluids":{"status":"changed","membership":"native-fluid-registry","added":[],"modified":[],
               "removed":[{"identity":"susy:example","baselinePointer":"/baseline/result/execution/registrationEffects/fluids/entries/susy:example"}]}}}
          """));
        var nativeResult = new JsonObject(); nativeResult.add("result", pair); value.add("native", nativeResult);
        String before = value.toString(), report = MaterialChecksClient.report(value);
        assertTrue(report.contains("Candidate initialization (preInit): incomplete"));
        assertTrue(report.contains("Native error observed: yes; this is not whole-pack validity."));
        assertTrue(report.contains("Native effect comparison: incomplete (observed membership and selected properties only)"));
        assertTrue(report.contains("materials: not-comparable"));
        assertTrue(report.contains("Not comparable: Complete inventory unavailable"));
        assertTrue(report.contains("modified: susy:items#4"));
        assertTrue(report.contains("Candidate owner registration: /candidate/result/execution/customMetaItems/items/0"));
        assertTrue(report.contains("fluids: changed · native-fluid-registry"));
        assertTrue(report.contains("removed: susy:example"));
        assertTrue(report.contains("owner Forge registration is separate"));
        assertTrue(report.contains("Queued fluids and recipe effects are not inferred"));
        assertEquals(before, value.toString());
    }
    @Test void assessmentExposesMixedIntentAndEvidenceWithoutGrantingValidity() {
        var value = result(); value.getAsJsonObject("native").addProperty("status", "incomplete");
        value.getAsJsonObject("native").getAsJsonObject("result").add("assessment", JsonParser.parseString("""
          {"schema":"axiom.material-program-assessment.v1","execution":"completed","coverage":"no-observed-gaps",
           "intentCounts":{"matched":0,"mismatch":1,"unsupported":1,"not-evaluated":0},"qualifiedValidity":false,
           "reasons":[{"code":"expectation-mismatch","message":"A known mismatch remains visible.","evidencePointer":"/expectations/checks"},
             {"code":"qualification-pending","message":"Qualification is pending.","evidencePointer":"/qualification"}]}
          """));
        var before = value.deepCopy(); var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("native status: incomplete"));
        assertTrue(text.contains("Execution checkpoint: completed; coverage: no-observed-gaps"));
        assertTrue(text.contains("Expectation counts: matched 0; mismatched 1; unsupported 1; not evaluated 0"));
        assertTrue(text.contains("Assessment [expectation-mismatch]: A known mismatch remains visible.\n  Evidence: /expectations/checks"));
        assertTrue(text.contains("No qualified material validity"));
        assertEquals(before, value);
    }
    @Test void pendingNativeWorkIsNotReportedAsExecuted() {
        var value = result();
        value.getAsJsonObject("native").getAsJsonObject("result").add("execution", JsonParser.parseString("""
          {"lifecycle":{"scope":"native-host-checkpoints-not-per-listener-trace","checkpoints":[
            {"checkpoint":"after-material-event","phase":"OPEN","activeOwner":"gregtech"}]},
           "deferredWork":{"schema":"axiom.native-deferred-material-work.v1","phase":"FROZEN",
            "fluidRegistrationExecuted":false,"recipeHandlersExecuted":false,"fluids":[
              {"material":"supersymmetry:test","hasFluidProperty":true,"queued":[{"key":"gregtech:liquid"}],"stored":[]}],
            "prefixScope":"all-native-prefix-queues","prefixProcessing":[{"prefix":"dust","pendingMaterials":["supersymmetry:test"]}]}}
          """));
        var before = value.deepCopy(); var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("after-material-event: OPEN · owner gregtech"));
        assertTrue(text.contains("queued gregtech:liquid; stored none"));
        assertTrue(text.contains("recipe handlers executed: false"));
        assertTrue(text.contains("Pending prefix/material memberships: 1")); assertEquals(before, value);
    }
    @Test void pairedReportsKeepBaselineAndCandidateStatusesSeparate() {
        var value = result(); var baseline = value.get("native").deepCopy();
        var pair = JsonParser.parseString("{\"result\":{\"candidate\":{\"status\":\"incomplete\",\"result\":{\"expectations\":{\"status\":\"matched\"}}},\"comparison\":{\"status\":\"changed\"}}}").getAsJsonObject();
        pair.getAsJsonObject("result").add("baseline", baseline); value.add("native", pair);
        var original = value.deepCopy(); String text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Baseline native status: rejected")); assertTrue(text.contains("Candidate native status: incomplete"));
        assertTrue(text.contains("not source causation")); assertEquals(original, value);
    }
    @Test void unvisitedNativeQueuesAreNotReportedAsEmpty() {
        var value = result();
        var execution = JsonParser.parseString("""
          {"contentProgress":{"phase":"NOT_STARTED","lastCompletedPhase":"NONE","completedCheckpoints":[]},
           "deferredWork":{"schema":"axiom.native-deferred-material-work.v1","phase":"CLOSED",
             "prefixScope":"not-observed-before-content-construction-checkpoint"}}
          """).getAsJsonObject();
        value.getAsJsonObject("native").getAsJsonObject("result").add("execution", execution);
        var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Native content: NOT_STARTED; last completed phase: NONE"));
        assertTrue(text.contains("Pending prefix/material memberships: not observed"));
        execution.getAsJsonObject("deferredWork").add("prefixProcessing", JsonParser.parseString("[]"));
        assertTrue(MaterialChecksClient.report(value).contains("Pending prefix/material memberships: 0"));
    }
    @Test void stoppedContentKeepsCompletedCheckpointsAndPartialCountsSeparate() {
        var value = result();
        value.getAsJsonObject("native").getAsJsonObject("result").add("execution", JsonParser.parseString("""
          {"contentProgress":{"phase":"FAILED","lastCompletedPhase":"CONSTRUCTED","failedPhase":"BLOCK_REGISTERING",
            "completedCheckpoints":[{"phase":"CONSTRUCTED","registeredBlocks":0}],"interruptedState":{"registeredBlocks":12},
            "failure":{"type":"java.lang.IllegalArgumentException","message":"Harvest Level must be greater than zero!"}}}
          """));
        var before = value.deepCopy(); var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Native content: FAILED; last completed phase: CONSTRUCTED"));
        assertTrue(text.contains("Completed CONSTRUCTED: registered material blocks 0"));
        assertTrue(text.contains("Stopped during BLOCK_REGISTERING: java.lang.IllegalArgumentException"));
        assertTrue(text.contains("Interrupted state (partial inventory): registered material blocks 12"));
        assertEquals(before, value);
    }
    @Test void retainedSourceRequiresExactFindingAndDigest() throws Exception {
        String text = "// 🌍\r\nerror\n";
        var finding = JsonParser.parseString("{\"id\":\"diagnostic-2\",\"location\":{}}").getAsJsonObject();
        finding.getAsJsonObject("location").addProperty("sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))));
        var view = new JsonObject(); view.addProperty("format", "workbench-material-source-view-v1"); view.addProperty("text", text); view.addProperty("read_only", true);
        view.addProperty("result_id", "result"); view.addProperty("attempt_id", ATTEMPT); view.add("source", finding);
        assertEquals(text, MaterialChecksClient.retainedSource(view, result(), finding));
        view.addProperty("text", "newer source");
        assertThrows(IllegalArgumentException.class, () -> MaterialChecksClient.retainedSource(view, result(), finding));
    }
    @Test void comparisonReportsOutcomeTransitionAndExactEvidence() {
        var value = result();
        value.add("native", JsonParser.parseString("""
          {"result":{"baseline":{"status":"incomplete","result":{"nativeOutcome":"completed-without-observed-error"}},
            "candidate":{"status":"source-error","result":{"nativeOutcome":"source-error"}},
            "comparison":{"status":"changed","changedSections":[{"field":"nativeOutcome",
              "baselinePointer":"/baseline/result/nativeOutcome","candidatePointer":"/candidate/result/nativeOutcome"}]}}}
          """));
        var before = value.deepCopy(); var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Native observation comparison: changed (not source causation)"));
        assertTrue(text.contains("Material execution outcome: completed-without-observed-error → source-error"));
        assertTrue(text.contains("Changed observation: nativeOutcome"));
        assertTrue(text.contains("Baseline evidence: /baseline/result/nativeOutcome"));
        assertTrue(text.contains("Candidate evidence: /candidate/result/nativeOutcome"));
        assertEquals(before, value);
    }
    @Test void compilerLinkageRefusalNamesRuntimeDependencyWithoutBlamingSource() {
        var value = result();
        value.add("native", JsonParser.parseString("""
          {"status":"incomplete","result":{"nativeOutcome":"not-run","bootstrap":{"compilerLinkage":{
            "status":"incomplete","missing":[{"kind":"mixin","target":"groovy.lang.Closure",
            "required":"com.cleanroommc.groovyscript.core.mixin.groovy.ClosureMixin","reason":"not-observed-on-defined-target"}]}}}}
          """));
        var before = value.deepCopy(); var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Material execution: not-run"));
        assertTrue(text.contains("Candidate native compiler linkage: incomplete (not behavioral qualification)"));
        assertTrue(text.contains("Missing mixin: groovy.lang.Closure · requires com.cleanroommc.groovyscript.core.mixin.groovy.ClosureMixin"));
        assertTrue(text.contains("Evidence: /bootstrap/compilerLinkage/missing/0"));
        assertFalse(text.contains("source-error"));
        assertEquals(before, value);
    }
    @Test void unavailableComparisonShowsReasonNotEquality() {
        var value = result();
        value.add("native", JsonParser.parseString("""
          {"result":{"comparison":{"status":"not-comparable","reasons":["Native execution observation is unavailable"]}}}
          """));
        var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Material execution outcome: not observed → not observed"));
        assertTrue(text.contains("Not comparable: Native execution observation is unavailable"));
    }
    @Test void earlyBootstrapFailureKeepsOriginalCausesAndUnavailableObserverVisible() {
        var value = result();
        value.add("native", JsonParser.parseString("""
          {"status":"incomplete","result":{"nativeOutcome":"not-run",
            "initialization":{"schema":"axiom.scoped-initialization.v1","scope":"preInit","status":"incomplete","nativeErrorObserved":true},
            "bootstrap":{"admitted":false,"compilerInvoked":false,"platformInitialization":{"status":"threw","causes":[
              {"type":"java.lang.ExceptionInInitializerError","message":""},
              {"type":"java.lang.IllegalStateException","message":"Original native early failure"}]}},
            "transformationObservation":{"status":"unavailable","causes":[
              {"type":"java.lang.NoClassDefFoundError","message":"Could not initialize class native.Transformer"}],
              "meaning":"observer-unavailable-not-an-empty-transformer-audit"}}}
          """));
        var before = value.deepCopy(); var text = MaterialChecksClient.report(value);
        assertTrue(text.contains("Candidate initialization (preInit): incomplete"));
        assertTrue(text.contains("Native error observed: yes; this is not whole-pack validity."));
        assertTrue(text.contains("Material execution: not-run"));
        assertTrue(text.contains("Candidate native platform initialization failed; the material context is incomplete, not evidence of an invalid developer edit."));
        assertTrue(text.contains("java.lang.ExceptionInInitializerError: \n  Evidence: /bootstrap/platformInitialization/causes/0"));
        assertTrue(text.contains("java.lang.IllegalStateException: Original native early failure\n  Evidence: /bootstrap/platformInitialization/causes/1"));
        assertTrue(text.contains("Candidate transformation observation unavailable; this is not an empty transformer audit."));
        assertTrue(text.contains("java.lang.NoClassDefFoundError: Could not initialize class native.Transformer\n  Evidence: /transformationObservation/causes/0"));
        assertFalse(text.contains("source-error"));
        assertEquals(before, value);
    }
}
