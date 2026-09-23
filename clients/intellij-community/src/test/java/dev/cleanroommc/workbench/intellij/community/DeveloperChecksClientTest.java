package dev.cleanroommc.workbench.intellij.community;

import org.junit.Test;
import static org.junit.Assert.*;

public class DeveloperChecksClientTest {
    @Test public void explanationsUseRetainedTextAndNeverAnnotateOldExpectedSource() {
        var result = com.google.gson.JsonParser.parseString("""
            {"candidate_id":"current","state":"inconclusive","assertions":{"state":"mismatched","reasons":[],
              "expectation":{"source":{"candidate_id":"current"},"subject":{"location":{"path":"Probe.groovy"}}},
              "observation":{"details":{"explanation":{"format":"workbench-check-explanation-v1",
                "text":"Duration (ticks): expected 200; observed 240","limitations":["Not source-causation proof."],
                "sections":[{"id":"lookup-q0","text":"Observed winner r0","properties":[{"label":"Duration (ticks)","expected":"200","observed":"240"}]}]}}}}}
            """).getAsJsonObject();
        assertTrue(DeveloperChecksClient.recipeExplanationText(result, null).contains("Startup: inconclusive"));
        assertTrue(DeveloperChecksClient.recipeExplanationText(result, null).contains("expected 200; observed 240"));
        var report = DeveloperChecksClient.recipeExplanation(result);
        assertTrue(DeveloperChecksClient.recipeExplanationText(result, report.getAsJsonArray("sections").get(0).getAsJsonObject()).contains("Not source-causation proof"));
        assertEquals(1, DeveloperChecksClient.findings(result).size());
        result.getAsJsonObject("assertions").getAsJsonObject("expectation").getAsJsonObject("source").addProperty("candidate_id", "old");
        assertEquals(0, DeveloperChecksClient.findings(result).size());
        result.getAsJsonObject("assertions").getAsJsonObject("expectation").getAsJsonObject("source").addProperty("candidate_id", "current");
        result.getAsJsonObject("assertions").addProperty("state", "inconclusive");
        assertEquals(0, DeveloperChecksClient.findings(result).size());
        report.addProperty("format", "future");
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.recipeExplanation(result));
    }
    @Test public void olderAssertionsAreNeverReinterpretedIntoExplanations() {
        var result = com.google.gson.JsonParser.parseString("""
            {"state":"failed","assertions":{"state":"inconclusive","reasons":[],"observation":{"details":{}}}}
            """).getAsJsonObject();
        assertNull(DeveloperChecksClient.recipeExplanation(result));
        assertTrue(DeveloperChecksClient.recipeExplanationText(result, null).contains("fresh check is required"));
    }
    @Test public void actualLookupDecisionTextDoesNotCertifyMachineExecution() {
        var result = com.google.gson.JsonParser.parseString("""
            {"state":"inconclusive","assertions":{"state":"matched","reasons":[],
              "observation":{"details":{"explanation":{"format":"workbench-check-explanation-v1",
                "text":"Original findRecipe invocations: 1; selected recipe: o0. Not machine execution validation.",
                "limitations":["Scenario-bound lookup only."],
                "sections":[{"id":"decision-q0","text":"Accepting recipe o1 was not evaluated.","properties":[],"sources":[]}]}}}}}
            """).getAsJsonObject();
        var report = DeveloperChecksClient.recipeExplanation(result);
        assertTrue(DeveloperChecksClient.recipeExplanationText(result, null).contains("Not machine execution validation"));
        assertTrue(DeveloperChecksClient.recipeExplanationText(result, report.getAsJsonArray("sections").get(0).getAsJsonObject()).contains("o1 was not evaluated"));
        assertEquals("inconclusive", result.get("state").getAsString());
    }
    @Test public void recipeSelectionUsesExactIdentitiesAndSeparateConsent() {
        String session = "work-session-v2-" + "a".repeat(32), image = "runtime-image:sha256:" + "b".repeat(64);
        var options = new com.google.gson.JsonObject();
        options.addProperty("recipe", "saved-recipe:sha256:" + "c".repeat(64));
        options.addProperty("reference", "check-" + "d".repeat(32)); options.addProperty("absent", true);
        var args = DeveloperChecksClient.arguments(session, "prepare", image, null, options);
        assertTrue(args.contains("--recipe-reference")); assertTrue(args.contains("--absent")); assertFalse(args.contains("--confirm"));
        options.addProperty("trace", false);
        assertTrue(DeveloperChecksClient.arguments(session, "prepare", image, null, options).contains("--no-trace"));
        assertTrue(DeveloperChecksClient.arguments(session, "source", "check-" + "d".repeat(32), "lifecycle-e10:0").contains("--source"));
        assertTrue(DeveloperChecksClient.arguments(session, "source", "check-" + "d".repeat(32), "decision-q0:0").contains("--source"));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "source", "check-" + "d".repeat(32), "../secret"));
        options.remove("reference");
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "prepare", image, null, options));
        var source = new com.google.gson.JsonObject(); source.addProperty("path", "groovy/postInit/Some File.groovy");
        assertTrue(DeveloperChecksClient.recipeArguments(source, true).contains("--path=groovy/postInit/Some File.groovy"));
        source.addProperty("path", "groovy/postInit/../secret.groovy");
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.recipeArguments(source, true));
    }
    @Test public void comparisonRequiresDistinctRunsWithoutExecutionConsent() {
        String session = "work-session-v2-" + "a".repeat(32), candidate = "check-" + "b".repeat(32), reference = "check-" + "c".repeat(32);
        var arguments = DeveloperChecksClient.arguments(session, "compare", candidate, reference);
        assertEquals(reference, arguments.get(arguments.size() - 1));
        assertTrue(arguments.contains("--reference"));
        assertFalse(arguments.contains("--confirm"));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "compare", candidate, candidate));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "compare", candidate, "latest"));
        assertTrue(DeveloperChecksClient.arguments(session, "history", null, null).contains("history"));
    }
    @Test public void provenanceAndComparisonDoNotClaimLatestOrPromoteFailedRuns() {
        var value = com.google.gson.JsonParser.parseString("""
            {"candidate":{"source":{"revision":"exact","dirty":true}},"provenance":{
              "runtime":{"pack":{"name":"Supersymmetry","version":"0.1.16.15"},"platform":{"id":"cleanroom","version":"0.6.12-alpha","java":{"JAVA_RUNTIME_VERSION":"25.0.4+7"}}},
              "image":{"id":"image"},"source_labels":{"local_tags":["latest"],"release_verified":false}}}
            """).getAsJsonObject();
        assertTrue(DeveloperChecksClient.provenanceSummary(value).contains("saved working-tree changes"));
        assertTrue(DeveloperChecksClient.provenanceSummary(value).contains("release/latest not verified"));
        var comparison = com.google.gson.JsonParser.parseString("""
            {"state":"partial","reference":{"state":"inconclusive"},"candidate":{"state":"failed"},"counts":{"newly-observed":1},"reasons":[]}
            """).getAsJsonObject();
        assertTrue(DeveloperChecksClient.comparisonSummary(comparison).contains("candidate failed"));
        assertTrue(DeveloperChecksClient.comparisonSummary(comparison).contains("outcomes are unchanged"));
    }
    @Test public void progressAndReportSelectionStayBoundToExactRequest() {
        String session = "work-session-v2-" + "a".repeat(32), attempt = "check-" + "b".repeat(32);
        var result = com.google.gson.JsonParser.parseString("""
            {"format":"workbench-check-live-status-v1","attempt_id":"ATTEMPT","request_id":"request","state":"running",
             "progress":{"attempt_id":"ATTEMPT","request_id":"request","observation":{"format":"workbench-check-observation-v1","summary":"Compiling saved source"}}}
            """.replace("ATTEMPT", attempt)).getAsJsonObject();
        assertTrue(DeveloperChecksClient.progressMessage(result, attempt, "request").contains("Compiling saved source"));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.progressMessage(result, attempt, "other"));
        assertTrue(DeveloperChecksClient.arguments(session, "progress", attempt, null).contains("progress"));
        assertTrue(DeveloperChecksClient.arguments(session, "log", attempt, "crash-reports/crash-client.txt").contains("crash-reports/crash-client.txt"));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "log", attempt, "crash-reports/../secret"));
    }
    @Test public void environmentPreparationRequiresItsOwnExactConsent() {
        String session = "work-session-v2-" + "a".repeat(32), attempt = "environment-" + "b".repeat(32);
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "prepare-environment", attempt, null));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "prepare-environment", attempt, "saved-check-request:sha256:" + "c".repeat(64)));
        var argv = DeveloperChecksClient.arguments(session, "prepare-environment", attempt, "environment-request:sha256:" + "c".repeat(64));
        assertEquals("--confirm", argv.get(argv.size() - 2));
        assertFalse(DeveloperChecksClient.arguments(session, "environment-cancel", attempt, null).contains("--confirm"));
    }
    @Test public void executionRequiresExactConsent() {
        String session = "work-session-v2-" + "a".repeat(32), attempt = "check-" + "b".repeat(32);
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments(session, "execute", attempt, null));
        var argv = DeveloperChecksClient.arguments(session, "execute", attempt, "saved-check-request:sha256:" + "c".repeat(64));
        assertEquals("--confirm", argv.get(argv.size() - 2));
        assertFalse(DeveloperChecksClient.arguments(session, "cancel", attempt, null).contains("--confirm"));
    }
    @Test public void neverAcceptsAmbiguousOrPathAttemptSelectors() {
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments("latest", "show", "../outside", null));
        assertThrows(IllegalArgumentException.class, () -> DeveloperChecksClient.arguments("work-session-v2-" + "a".repeat(32), "show", "../outside", null));
    }
}
