package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.TreePath;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class RetainedFeatureClientTransactionTest {
    @Test
    void parsesCurrentStateLinksOwnerBytesAndBuildsNativeTransactionTree() throws Exception {
        JsonObject source = transaction();
        RetainedFeatureClient.TransactionView value =
                RetainedFeatureClient.TransactionView.parse(source.toString());
        FeaturePresentation presentation = FeaturePresentation.parse(
                FeaturePresentationTest.presentation().toString()
        );
        FeatureRecordCatalog.Record selected = FeatureRecordCatalog.parse(
                FeatureRecordCatalogTest.catalog().toString()
        ).records().getFirst();
        RetainedFeatureClient.validateTransactionLink(selected, presentation, value);

        assertEquals("planned", value.currentEffectiveState());
        assertEquals("matches-before", value.workspaceMatch().state());
        assertEquals("ready", value.planFreshness().state());
        assertEquals(List.of("plans"), value.records().stream()
                .map(FeatureRecordCatalog.Record::collection)
                .toList());
        assertEquals(List.of("check", "apply", "rollback", "recover"),
                value.actions().stream()
                        .map(RetainedFeatureClient.TransactionView.Action::action)
                        .toList());
        assertTrue(value.actions().get(1).available());

        DefaultMutableTreeNode tree = FeaturePresentationTree.buildTransaction(
                value, presentation
        );
        List<String> labels = labels(tree);
        assertTrue(labels.contains("CURRENT: PLANNED · recipe-change"));
        assertTrue(labels.contains("Workspace match: matches-before"));
        assertTrue(labels.contains("Plan freshness: ready"));
        assertTrue(labels.contains("Retained lineage (1)"));
        assertTrue(labels.contains("Eligible next actions (2 of 4)"));
        assertTrue(labels.contains("Compact reviewed operations (1)"));
        DefaultMutableTreeNode compactOperation = findEntry(tree);
        assertNotNull(compactOperation);
        FeaturePresentation.Operation selectedOperation =
                FeaturePresentationTree.selectedOperation(
                        new TreePath(compactOperation.getPath())
                );
        assertNotNull(selectedOperation);
        assertEquals("groovy/postInit/example.groovy", selectedOperation.path());
    }

    @Test
    void composesDirectAndWslTransactionArgumentsWithoutAShell() throws Exception {
        FeatureRecordCatalog.Record record = FeatureRecordCatalog.parse(
                FeatureRecordCatalogTest.catalog().toString()
        ).records().getFirst();
        CoreLaunch nativeLaunch = CoreLaunch.resolve(
                "/opt/workbench/bin/workbench", false, null
        );
        assertEquals(List.of(
                        "feature", "transaction", "recipe-change", record.planId(),
                        "--state-root", "/tmp/state", "--json"
                ),
                RetainedFeatureClient.transactionArguments(
                        nativeLaunch, record, "/tmp/state"
                ));

        CoreLaunch wsl = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\bin\\workbench",
                true,
                "C:\\Windows"
        );
        assertEquals(List.of(
                        "feature", "transaction", "recipe-change", record.planId(),
                        "--state-root", "/home/dev/state", "--json"
                ),
                RetainedFeatureClient.transactionArguments(
                        wsl, record,
                        "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state"
                ));
    }

    @Test
    void acceptsRestoredLineageInsteadOfRepeatingImmutableReceiptState() throws Exception {
        JsonObject source = transaction();
        JsonObject plan = source.getAsJsonArray("records").get(0).getAsJsonObject();
        JsonObject rollback = plan.deepCopy();
        String kind = FeatureRecordKinds.expected("recipe-change", "rollbacks");
        String rollbackId = kind + ":sha256:" + "b".repeat(64);
        rollback.addProperty("collection", "rollbacks");
        rollback.addProperty("record_id", rollbackId);
        rollback.addProperty("record_kind", kind);
        rollback.addProperty("record_state", "restored");
        rollback.addProperty("reference", rollbackId);
        rollback.addProperty("uri", "file:///tmp/workbench/rollbacks/b/record.json");
        source.getAsJsonArray("records").add(rollback);
        source.addProperty("current_effective_state", "restored");
        reseal(source);

        RetainedFeatureClient.TransactionView parsed =
                RetainedFeatureClient.TransactionView.parse(source.toString());
        assertEquals("restored", parsed.currentEffectiveState());
        assertEquals(List.of("plans", "rollbacks"), parsed.records().stream()
                .map(FeatureRecordCatalog.Record::collection)
                .toList());
        assertTrue(parsed.actions().get(1).available());
    }

    @Test
    void acceptsInterruptedStateOnlyWhenRecoverySuppressesOtherMutations() throws Exception {
        JsonObject source = transaction();
        source.addProperty("current_effective_state", "interrupted");
        JsonArray actions = source.getAsJsonArray("actions");
        JsonObject apply = actions.get(1).getAsJsonObject();
        apply.addProperty("available", false);
        apply.add("consent_id", JsonNull.INSTANCE);
        apply.addProperty(
                "reason", "an interrupted transaction must be recovered before applying"
        );
        JsonObject rollback = actions.get(2).getAsJsonObject();
        rollback.addProperty(
                "reason", "an interrupted transaction must be recovered before rolling back"
        );
        JsonObject recover = actions.get(3).getAsJsonObject();
        recover.addProperty("available", true);
        recover.add("reason", JsonNull.INSTANCE);
        reseal(source);

        RetainedFeatureClient.TransactionView parsed =
                RetainedFeatureClient.TransactionView.parse(source.toString());
        assertEquals("interrupted", parsed.currentEffectiveState());
        assertTrue(parsed.actions().get(3).available());
        assertFalse(parsed.actions().get(1).available());
        assertFalse(parsed.actions().get(2).available());
    }

    @Test
    void rejectsSealShapeAggregateLineageAndActionMeaningDrift() throws Exception {
        JsonObject brokenSeal = transaction();
        brokenSeal.addProperty("current_effective_state", "applied");
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.TransactionView.parse(brokenSeal.toString()));

        JsonObject unknown = transaction();
        unknown.addProperty("ide_guess", "unsafe");
        reseal(unknown);
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.TransactionView.parse(unknown.toString()));

        JsonObject aggregateLie = transaction();
        aggregateLie.getAsJsonObject("workspace_match")
                .addProperty("state", "matches-after");
        reseal(aggregateLie);
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.TransactionView.parse(aggregateLie.toString()));

        JsonObject effectiveLie = transaction();
        effectiveLie.addProperty("current_effective_state", "applied");
        reseal(effectiveLie);
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.TransactionView.parse(effectiveLie.toString()));

        JsonObject actionOverclaim = transaction();
        JsonObject rollback = actionOverclaim.getAsJsonArray("actions")
                .get(2).getAsJsonObject();
        rollback.addProperty("available", true);
        rollback.add("reason", JsonNull.INSTANCE);
        reseal(actionOverclaim);
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.TransactionView.parse(actionOverclaim.toString()));

        JsonObject crossFamilyKind = transaction();
        JsonObject retained = crossFamilyKind.getAsJsonArray("records")
                .get(0).getAsJsonObject();
        String wrong = "workbench-developer-source-feature-plan:sha256:" + "0".repeat(64);
        retained.addProperty("record_id", wrong);
        retained.addProperty("reference", wrong);
        retained.addProperty("record_kind", "workbench-developer-source-feature-plan");
        reseal(crossFamilyKind);
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.TransactionView.parse(crossFamilyKind.toString()));
    }

    static JsonObject transaction() throws Exception {
        JsonObject presentation = FeaturePresentationTest.presentation();
        JsonObject compact = new JsonObject();
        JsonObject exact = presentation.getAsJsonArray("operations")
                .get(0).getAsJsonObject();
        compact.add("after_sha256", exact.get("after_sha256"));
        compact.add("after_size", exact.get("after_size"));
        compact.add("before_sha256", exact.get("before_sha256"));
        compact.add("before_size", exact.get("before_size"));
        compact.add("diff", exact.get("diff"));
        compact.add("ordinal", exact.get("ordinal"));
        compact.add("path", exact.get("path"));
        compact.add("role", exact.get("role"));

        JsonObject workspaceOperation = new JsonObject();
        workspaceOperation.add("actual_sha256", exact.get("before_sha256"));
        workspaceOperation.add("actual_size", exact.get("before_size"));
        workspaceOperation.addProperty("ordinal", 0);
        workspaceOperation.add("path", exact.get("path"));
        workspaceOperation.add("reason", JsonNull.INSTANCE);
        workspaceOperation.addProperty("state", "matches-before");
        JsonObject workspaceMatch = new JsonObject();
        workspaceMatch.add("operations", array(workspaceOperation));
        workspaceMatch.add("reason", JsonNull.INSTANCE);
        workspaceMatch.addProperty("state", "matches-before");

        String planId = presentation.get("plan_id").getAsString();
        JsonObject check = action("check", true, null, planId, null);
        JsonObject apply = action("apply", true, planId, planId, null);
        JsonObject rollback = action(
                "rollback", false, null, null,
                "workspace does not match reviewed after bytes"
        );
        JsonObject recover = action(
                "recover", false, null, planId,
                "no interrupted transaction is retained"
        );

        JsonObject root = new JsonObject();
        root.add("actions", array(check, apply, rollback, recover));
        root.addProperty("current_effective_state", "planned");
        root.addProperty("family", "recipe-change");
        root.addProperty("format", RetainedFeatureClient.TransactionView.FORMAT);
        root.addProperty("kind", "workbench-developer-feature-transaction-view");
        root.add("limitations", array(
                "Retained records are immutable content identities; current effective state "
                        + "is derived separately from live workspace bytes.",
                "Workspace match and action availability are point-in-time observations."
        ));
        root.add("operations", array(compact));
        root.add("plan_freshness", presentation.get("verification").deepCopy());
        root.addProperty("plan_id", planId);
        root.add("records", FeatureRecordCatalogTest.catalog()
                .getAsJsonArray("records").deepCopy());
        root.addProperty("schema_version", 1);
        root.add("workspace_match", workspaceMatch);
        root.add("workspace_uri", presentation.get("workspace_uri"));
        reseal(root);
        return root;
    }

    private static JsonObject action(
            String name,
            boolean available,
            String consentId,
            String recordId,
            String reason
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("action", name);
        result.addProperty("available", available);
        if (consentId == null) {
            result.add("consent_id", JsonNull.INSTANCE);
        } else {
            result.addProperty("consent_id", consentId);
        }
        if (reason == null) {
            result.add("reason", JsonNull.INSTANCE);
        } else {
            result.addProperty("reason", reason);
        }
        if (recordId == null) {
            result.add("record_id", JsonNull.INSTANCE);
        } else {
            result.addProperty("record_id", recordId);
        }
        return result;
    }

    private static JsonArray array(Object... values) {
        JsonArray result = new JsonArray();
        for (Object value : values) {
            if (value instanceof JsonObject object) {
                result.add(object);
            } else if (value instanceof String string) {
                result.add(string);
            } else {
                throw new IllegalArgumentException("unsupported fixture value");
            }
        }
        return result;
    }

    private static void reseal(JsonObject value) {
        value.remove("id");
        value.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-transaction-view", value
        ));
    }

    private static List<String> labels(DefaultMutableTreeNode root) {
        List<String> result = new ArrayList<>();
        collect(root, result);
        return result;
    }

    private static void collect(DefaultMutableTreeNode node, List<String> output) {
        output.add(node.getUserObject().toString());
        for (int index = 0; index < node.getChildCount(); index++) {
            collect((DefaultMutableTreeNode) node.getChildAt(index), output);
        }
    }

    private static DefaultMutableTreeNode findEntry(DefaultMutableTreeNode node) {
        if (node.getUserObject() instanceof FeaturePresentationTree.OperationEntry) {
            return node;
        }
        for (int index = 0; index < node.getChildCount(); index++) {
            DefaultMutableTreeNode found = findEntry(
                    (DefaultMutableTreeNode) node.getChildAt(index)
            );
            if (found != null) {
                return found;
            }
        }
        return null;
    }
}
