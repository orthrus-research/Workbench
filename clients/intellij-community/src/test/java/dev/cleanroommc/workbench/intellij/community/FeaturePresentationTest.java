package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.TreePath;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class FeaturePresentationTest {
    @Test
    void parsesExactOwnerBytesAndBuildsANavigableOperationTree() throws Exception {
        FeaturePresentation value = FeaturePresentation.parse(presentation().toString());

        assertEquals("recipe-change", value.family());
        assertEquals("plans", value.collection());
        assertEquals(1, value.schemaVersion());
        assertEquals(List.of(), value.runtime().sides());
        assertTrue(value.runtime().actionAvailable());
        assertTrue(value.actions().stream()
                .anyMatch(action -> action.action().equals("run") && action.available()));
        assertEquals(1, value.operations().size());
        assertArrayEquals("before\n".getBytes(StandardCharsets.UTF_8),
                value.operations().getFirst().beforeBytes());
        assertArrayEquals("after\n".getBytes(StandardCharsets.UTF_8),
                value.operations().getFirst().afterBytes());

        DefaultMutableTreeNode root = FeaturePresentationTree.build(value);
        DefaultMutableTreeNode operations = (DefaultMutableTreeNode) root.getLastChild();
        DefaultMutableTreeNode operation = (DefaultMutableTreeNode) operations.getFirstChild();
        FeaturePresentation.Operation selected = FeaturePresentationTree.selectedOperation(
                new TreePath(operation.getPath())
        );
        assertNotNull(selected);
        assertEquals("groovy/postInit/example.groovy", selected.path());
        assertEquals("example.groovy", FeatureDiffOpener.displayName(selected.path()));
    }

    @Test
    void rejectsTamperedBytesUnknownFieldsAndCrossFamilyKinds() throws Exception {
        JsonObject digestTamper = presentation();
        digestTamper.getAsJsonArray("operations").get(0).getAsJsonObject()
                .addProperty("after_sha256", "0".repeat(64));
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(digestTamper.toString()));

        JsonObject unknown = presentation();
        unknown.addProperty("ide_guess", true);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(unknown.toString()));

        JsonObject wrongKind = presentation();
        wrongKind.getAsJsonObject("owner_record").addProperty(
                "kind", "workbench-developer-source-feature-plan"
        );
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(wrongKind.toString()));

        String duplicate = presentation().toString().replaceFirst(
                "\\{", "{\\\"format\\\":\\\"duplicate\\\","
        );
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(duplicate));
    }

    @Test
    void returnsClonesInsteadOfMutableOwnerByteArrays() throws Exception {
        FeaturePresentation value = FeaturePresentation.parse(presentation().toString());
        byte[] first = value.operations().getFirst().beforeBytes();
        first[0] = 0;
        assertEquals('b', value.operations().getFirst().beforeBytes()[0]);
    }

    @Test
    void acceptsRecipeRuntimeComparisonAndRejectsQuestRuntimeOverclaim() throws Exception {
        FeaturePresentation runtime = FeaturePresentation.parse(
                runtimePresentation().toString()
        );
        assertEquals("recipe-change", runtime.family());
        assertEquals("runs", runtime.collection());
        assertEquals("workbench-developer-recipe-change-runtime-comparison",
                runtime.ownerRecord().kind());
        assertEquals("complete", runtime.runtime().state());

        JsonObject quest = presentation();
        quest.remove("id");
        String planKind = FeatureRecordKinds.expected("quest-for-process", "plans");
        String planId = id(planKind, 'd');
        quest.addProperty("family", "quest-for-process");
        quest.addProperty("plan_id", planId);
        quest.getAsJsonObject("owner_record").addProperty("id", planId);
        quest.getAsJsonObject("owner_record").addProperty("kind", planKind);
        quest.getAsJsonObject("verification").addProperty("plan_id", planId);
        quest.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-presentation", quest
        ));
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(quest.toString()));
    }

    @Test
    void parsesV2RuntimeSidesAndBuildsNativeRetainedEvidenceNodes() throws Exception {
        FeaturePresentation value = FeaturePresentation.parse(
                runtimePresentationV2().toString()
        );

        assertEquals(2, value.schemaVersion());
        assertEquals(FeaturePresentation.FORMAT_V2,
                runtimePresentationV2().get("format").getAsString());
        assertEquals(2, value.runtime().sides().size());
        FeaturePresentation.RuntimeSide baseline = value.runtime().sides().getFirst();
        assertEquals("baseline", baseline.role());
        assertEquals("failed", baseline.outcome());
        assertNotNull(baseline.assertion());
        assertEquals("assertion", baseline.error().phase());
        assertEquals("probe-baseline", baseline.probe().id());
        assertEquals("sha256:" + "e".repeat(64),
                baseline.receipt().finalLaunch().id());
        assertEquals("file:///tmp/runtime-launch-v3.json",
                baseline.receipt().finalLaunch().uri());
        assertEquals(42, baseline.receipt().groovyLog().size());
        assertEquals("candidate", value.runtime().sides().get(1).role());
        assertEquals(null, value.runtime().sides().get(1).receipt());

        List<String> labels = treeLabels(FeaturePresentationTree.build(value));
        assertTrue(labels.contains("Sides (2)"));
        assertTrue(labels.contains("Role: baseline"));
        assertTrue(labels.contains("Phase: assertion"));
        assertTrue(labels.contains("Retained evidence references"));
        assertTrue(labels.contains("URI: file:///tmp/runtime-launch-v3.json"));
        assertTrue(labels.contains("URI: file:///tmp/runtime-observation-v1.json"));
        assertTrue(labels.contains("Receipt: none"));
        assertTrue(labels.contains("Probe: none"));
        assertTrue(labels.contains("Assertion: none"));
    }

    @Test
    void v2FailsClosedOnUnknownInvalidAndUnsealedRuntimeEvidence() throws Exception {
        JsonObject unknown = runtimePresentationV2();
        unknown.getAsJsonObject("runtime").getAsJsonArray("sides")
                .get(0).getAsJsonObject().addProperty("gameplay_meaning", "guessed");
        reseal(unknown);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(unknown.toString()));

        JsonObject invalidPhase = runtimePresentationV2();
        invalidPhase.getAsJsonObject("runtime").getAsJsonArray("sides")
                .get(0).getAsJsonObject().getAsJsonObject("error")
                .addProperty("phase", "gameplay");
        reseal(invalidPhase);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(invalidPhase.toString()));

        JsonObject duplicateRole = runtimePresentationV2();
        duplicateRole.getAsJsonObject("runtime").getAsJsonArray("sides")
                .get(1).getAsJsonObject().addProperty("role", "baseline");
        reseal(duplicateRole);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(duplicateRole.toString()));

        JsonObject invalidReceipt = runtimePresentationV2();
        invalidReceipt.getAsJsonObject("runtime").getAsJsonArray("sides")
                .get(0).getAsJsonObject().getAsJsonObject("receipt")
                .getAsJsonObject("final_launch").addProperty("size", 0);
        reseal(invalidReceipt);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(invalidReceipt.toString()));

        JsonObject invalidProbeUri = runtimePresentationV2();
        invalidProbeUri.getAsJsonObject("runtime").getAsJsonArray("sides")
                .get(0).getAsJsonObject().getAsJsonObject("probe")
                .addProperty("overlay_uri", "https://example.invalid/overlay.json");
        reseal(invalidProbeUri);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(invalidProbeUri.toString()));

        JsonObject brokenSeal = runtimePresentationV2();
        brokenSeal.getAsJsonObject("runtime").getAsJsonArray("sides")
                .get(0).getAsJsonObject().addProperty("state", "tampered");
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(brokenSeal.toString()));

        JsonObject v1WithSides = runtimePresentation();
        v1WithSides.getAsJsonObject("runtime").add(
                "sides",
                runtimePresentationV2().getAsJsonObject("runtime").get("sides").deepCopy()
        );
        reseal(v1WithSides);
        assertThrows(IllegalArgumentException.class,
                () -> FeaturePresentation.parse(v1WithSides.toString()));
    }

    static JsonObject presentation() throws Exception {
        String family = "recipe-change";
        String collection = "plans";
        String planKind = FeatureRecordKinds.expected(family, "plans");
        String planId = id(planKind, 'a');
        byte[] before = "before\n".getBytes(StandardCharsets.UTF_8);
        byte[] after = "after\n".getBytes(StandardCharsets.UTF_8);

        JsonObject operation = new JsonObject();
        operation.addProperty("after_base64", Base64.getEncoder().encodeToString(after));
        operation.addProperty("after_sha256", sha256(after));
        operation.addProperty("after_size", after.length);
        operation.addProperty("before_base64", Base64.getEncoder().encodeToString(before));
        operation.addProperty("before_sha256", sha256(before));
        operation.addProperty("before_size", before.length);
        operation.addProperty("diff", "--- before\n+++ after\n");
        operation.addProperty("operation", "update");
        operation.addProperty("ordinal", 0);
        operation.addProperty("outcome", "changed");
        operation.addProperty("path", "groovy/postInit/example.groovy");
        operation.addProperty("role", "recipe-script");

        JsonObject action = new JsonObject();
        action.addProperty("action", "check");
        action.addProperty("available", true);
        action.add("consent_id", JsonNull.INSTANCE);
        action.add("reason", JsonNull.INSTANCE);

        JsonObject runAction = new JsonObject();
        runAction.addProperty("action", "run");
        runAction.addProperty("available", true);
        runAction.addProperty("consent_id", planId);
        runAction.add("reason", JsonNull.INSTANCE);

        JsonObject owner = new JsonObject();
        owner.add("diagnostic_code", JsonNull.INSTANCE);
        owner.addProperty("id", planId);
        owner.addProperty("kind", planKind);
        owner.addProperty("state", "planned");
        owner.addProperty("uri", "file:///tmp/workbench/plans/a/record.json");

        JsonObject runtime = new JsonObject();
        runtime.addProperty("action_available", true);
        runtime.add("outcome", JsonNull.INSTANCE);
        runtime.add("record_id", JsonNull.INSTANCE);
        runtime.add("requirement", JsonNull.INSTANCE);
        runtime.addProperty("state", "available-not-observed");

        JsonObject verification = new JsonObject();
        verification.addProperty("format", "workbench-feature-verification-v1");
        verification.addProperty("plan_id", planId);
        verification.add("reason", JsonNull.INSTANCE);
        verification.addProperty("schema_version", 1);
        verification.addProperty("state", "ready");

        JsonObject root = new JsonObject();
        root.add("actions", array(action, runAction));
        JsonObject authority = new JsonObject();
        authority.addProperty("owner", "Blueprints");
        root.add("authority_boundary", authority);
        root.addProperty("collection", collection);
        root.addProperty("family", family);
        root.addProperty("format", FeaturePresentation.FORMAT);
        root.addProperty("kind", "workbench-developer-feature-presentation");
        root.add("limitations", array("Runtime comparison remains a separate action."));
        root.add("operations", array(operation));
        root.add("owner_record", owner);
        root.addProperty("plan_id", planId);
        JsonObject request = new JsonObject();
        request.addProperty("duration", 40);
        root.add("request", request);
        JsonObject review = new JsonObject();
        review.addProperty("changed_files", 1);
        root.add("review", review);
        root.add("runtime", runtime);
        root.addProperty("schema_version", 1);
        root.add("verification", verification);
        root.addProperty("workspace_uri", "file:///tmp/workspace");
        root.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-presentation", root
        ));
        return root;
    }

    static JsonObject runtimePresentation() throws Exception {
        JsonObject root = presentation();
        root.remove("id");
        String runKind = FeatureRecordKinds.expected("recipe-change", "runs");
        String runId = id(runKind, 'c');
        root.addProperty("collection", "runs");
        root.add("actions", new JsonArray());
        JsonObject owner = root.getAsJsonObject("owner_record");
        owner.addProperty("id", runId);
        owner.addProperty("kind", runKind);
        owner.addProperty("state", "complete");
        owner.addProperty("uri", "file:///tmp/workbench/runs/c/record.json");
        JsonObject runtime = root.getAsJsonObject("runtime");
        runtime.addProperty("outcome", "runtime-comparison-completed");
        runtime.addProperty("record_id", runId);
        runtime.add("requirement", JsonNull.INSTANCE);
        runtime.addProperty("state", "complete");
        root.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-presentation", root
        ));
        return root;
    }

    static JsonObject runtimePresentationV2() throws Exception {
        JsonObject root = runtimePresentation();
        root.remove("id");
        root.addProperty("format", FeaturePresentation.FORMAT_V2);
        root.addProperty("schema_version", 2);
        root.getAsJsonObject("owner_record").addProperty("state", "incomplete");
        JsonObject runtime = root.getAsJsonObject("runtime");
        runtime.addProperty("outcome", "runtime-comparison-incomplete");
        runtime.addProperty("state", "incomplete");

        String digest = "e".repeat(64);
        JsonObject assertion = new JsonObject();
        assertion.addProperty("id", "assessment-baseline");
        assertion.addProperty("state", "failed");
        JsonObject error = new JsonObject();
        error.addProperty("kind", "MarkerError");
        error.addProperty("message", "marker missing");
        error.addProperty("phase", "assertion");
        JsonObject probe = new JsonObject();
        probe.addProperty("id", "probe-baseline");
        probe.addProperty("overlay_id", "overlay-baseline");
        probe.addProperty("overlay_uri", "file:///tmp/overlay-v1.json");
        probe.addProperty("script_uri", "file:///tmp/Probe.groovy");

        JsonObject finalLaunch = receiptReference(
                "sha256:" + digest,
                digest,
                12,
                "file:///tmp/runtime-launch-v3.json"
        );
        JsonObject groovyLog = new JsonObject();
        groovyLog.addProperty("sha256", digest);
        groovyLog.addProperty("size", 42);
        groovyLog.addProperty("uri", "file:///tmp/minecraft-groovy.log");
        JsonObject runtimeSession = receiptReference(
                "sha256:" + digest,
                digest,
                24,
                "file:///tmp/runtime-observation-v1.json"
        );
        JsonObject receipt = new JsonObject();
        receipt.add("final_launch", finalLaunch);
        receipt.add("groovy_log", groovyLog);
        receipt.add("runtime_session", runtimeSession);

        JsonObject baseline = new JsonObject();
        baseline.add("assertion", assertion);
        baseline.add("error", error);
        baseline.addProperty("outcome", "failed");
        baseline.add("probe", probe);
        baseline.add("receipt", receipt);
        baseline.addProperty("role", "baseline");
        baseline.addProperty("state", "incomplete");

        JsonObject blocked = new JsonObject();
        blocked.addProperty("kind", "PriorSideFailed");
        blocked.addProperty("message", "not run");
        blocked.addProperty("phase", "blocked");
        JsonObject candidate = new JsonObject();
        candidate.add("assertion", JsonNull.INSTANCE);
        candidate.add("error", blocked);
        candidate.addProperty("outcome", "not-run");
        candidate.add("probe", JsonNull.INSTANCE);
        candidate.add("receipt", JsonNull.INSTANCE);
        candidate.addProperty("role", "candidate");
        candidate.addProperty("state", "incomplete");
        runtime.add("sides", array(baseline, candidate));
        reseal(root);
        return root;
    }

    private static JsonObject receiptReference(
            String id,
            String digest,
            int size,
            String uri
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("id", id);
        result.addProperty("sha256", digest);
        result.addProperty("size", size);
        result.addProperty("uri", uri);
        return result;
    }

    private static void reseal(JsonObject value) throws Exception {
        value.remove("id");
        value.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-presentation", value
        ));
    }

    private static List<String> treeLabels(DefaultMutableTreeNode root) {
        List<String> result = new ArrayList<>();
        collectLabels(root, result);
        return result;
    }

    private static void collectLabels(
            DefaultMutableTreeNode node,
            List<String> result
    ) {
        result.add(node.getUserObject().toString());
        for (int index = 0; index < node.getChildCount(); index++) {
            collectLabels((DefaultMutableTreeNode) node.getChildAt(index), result);
        }
    }

    static String id(String kind, char digest) {
        return kind + ":sha256:" + Character.toString(digest).repeat(64);
    }

    private static JsonArray array(Object... values) {
        JsonArray result = new JsonArray();
        for (Object value : values) {
            if (value instanceof JsonObject object) {
                result.add(object);
            } else if (value instanceof String text) {
                result.add(text);
            } else {
                throw new IllegalArgumentException("unsupported fixture value");
            }
        }
        return result;
    }

    private static String sha256(byte[] bytes) throws Exception {
        StringBuilder result = new StringBuilder();
        for (byte value : MessageDigest.getInstance("SHA-256").digest(bytes)) {
            result.append(String.format("%02x", value & 0xff));
        }
        return result.toString();
    }
}
