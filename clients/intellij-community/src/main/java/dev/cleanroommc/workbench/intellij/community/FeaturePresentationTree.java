package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.tree.DefaultMutableTreeNode;
import javax.swing.tree.TreePath;
import java.util.Map;

/** Read-only transaction tree built exclusively from the validated Shell projection. */
final class FeaturePresentationTree {
    private FeaturePresentationTree() {
    }

    static @NotNull DefaultMutableTreeNode build(@NotNull FeaturePresentation value) {
        DefaultMutableTreeNode root = node(
                value.family() + " · " + value.collection() + " · "
                        + value.ownerRecord().state()
        );

        DefaultMutableTreeNode record = node("Owner record");
        record.add(node("ID: " + value.ownerRecord().id()));
        record.add(node("Kind: " + value.ownerRecord().kind()));
        record.add(node("State: " + value.ownerRecord().state()));
        record.add(node("URI: " + value.ownerRecord().uri()));
        if (value.ownerRecord().diagnosticCode() != null) {
            record.add(node("Diagnostic: " + value.ownerRecord().diagnosticCode()));
        }
        record.add(node("Plan: " + value.planId()));
        record.add(node("Workspace: " + value.workspaceUri()));
        root.add(record);

        DefaultMutableTreeNode verification = node(
                "Verification: " + value.verification().state()
        );
        verification.add(node("Format: " + value.verification().format()));
        if (value.verification().reason() != null) {
            verification.add(node("Reason: " + value.verification().reason()));
        }
        root.add(verification);

        DefaultMutableTreeNode runtime = node("Runtime: " + value.runtime().state());
        runtime.add(node("Action available: " + value.runtime().actionAvailable()));
        if (value.runtime().outcome() != null) {
            runtime.add(node("Outcome: " + value.runtime().outcome()));
        }
        if (value.runtime().recordId() != null) {
            runtime.add(node("Record: " + value.runtime().recordId()));
        }
        if (value.runtime().requirement() != null) {
            addJson(runtime, "Requirement", value.runtime().requirement(), 0, new int[]{2048});
        }
        if (!value.runtime().sides().isEmpty()) {
            DefaultMutableTreeNode sides = node(
                    "Sides (" + value.runtime().sides().size() + ")"
            );
            for (FeaturePresentation.RuntimeSide side : value.runtime().sides()) {
                sides.add(runtimeSide(side));
            }
            runtime.add(sides);
        }
        root.add(runtime);

        DefaultMutableTreeNode authority = node("Authority boundary");
        addObjectMembers(authority, value.authorityBoundary(), 0, new int[]{2048});
        root.add(authority);

        DefaultMutableTreeNode request = node("Request");
        addObjectMembers(request, value.request(), 0, new int[]{2048});
        root.add(request);

        DefaultMutableTreeNode review = node("Review");
        addObjectMembers(review, value.review(), 0, new int[]{2048});
        root.add(review);

        DefaultMutableTreeNode limitations = node(
                "Limitations (" + value.limitations().size() + ")"
        );
        if (value.limitations().isEmpty()) {
            limitations.add(node("None declared"));
        } else {
            for (String limitation : value.limitations()) {
                limitations.add(node(limitation));
            }
        }
        root.add(limitations);

        DefaultMutableTreeNode actions = node("Actions (" + value.actions().size() + ")");
        for (FeaturePresentation.Action action : value.actions()) {
            DefaultMutableTreeNode actionNode = node(
                    action.action() + " · " + (action.available() ? "available" : "unavailable")
            );
            if (action.consentId() != null) {
                actionNode.add(node("Consent ID: " + action.consentId()));
            }
            if (action.reason() != null) {
                actionNode.add(node("Reason: " + action.reason()));
            }
            actions.add(actionNode);
        }
        root.add(actions);

        DefaultMutableTreeNode operations = node(
                "Operations (" + value.operations().size() + ")"
        );
        for (FeaturePresentation.Operation operation : value.operations()) {
            DefaultMutableTreeNode operationNode = new DefaultMutableTreeNode(
                    new OperationEntry(
                            (operation.ordinal() + 1) + ". " + operation.path()
                                    + " · " + operation.role() + " · " + operation.outcome(),
                            operation
                    )
            );
            operationNode.add(node("Operation: " + operation.operation()));
            operationNode.add(node(
                    "Before: " + operation.beforeSize() + " bytes · "
                            + operation.beforeSha256()
            ));
            operationNode.add(node(
                    "After: " + operation.afterSize() + " bytes · "
                            + operation.afterSha256()
            ));
            operationNode.add(node(
                    "Owner diff: " + operation.diff().getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                            + " UTF-8 bytes"
            ));
            operations.add(operationNode);
        }
        root.add(operations);
        return root;
    }

    static @NotNull DefaultMutableTreeNode buildTransaction(
            @NotNull RetainedFeatureClient.TransactionView transaction,
            @NotNull FeaturePresentation owner
    ) {
        DefaultMutableTreeNode root = node(
                "CURRENT: " + transaction.currentEffectiveState().toUpperCase(java.util.Locale.ROOT)
                        + " · " + transaction.family()
        );

        DefaultMutableTreeNode current = node("Current effective transaction state");
        current.add(node("Effective state: " + transaction.currentEffectiveState()));
        current.add(node("Workspace match: " + transaction.workspaceMatch().state()));
        current.add(node("Plan freshness: " + transaction.planFreshness().state()));
        current.add(node("Plan: " + transaction.planId()));
        current.add(node("Workspace: " + transaction.workspaceUri()));
        root.add(current);

        RetainedFeatureClient.TransactionView.WorkspaceMatch match =
                transaction.workspaceMatch();
        DefaultMutableTreeNode workspace = node("Workspace match: " + match.state());
        if (match.reason() != null) {
            workspace.add(node("Reason: " + match.reason()));
        }
        for (RetainedFeatureClient.TransactionView.WorkspaceOperation operation
                : match.operations()) {
            DefaultMutableTreeNode operationNode = node(
                    (operation.ordinal() + 1) + ". " + operation.path()
                            + " · " + operation.state()
            );
            if (operation.actualSize() != null) {
                operationNode.add(node("Current size: " + operation.actualSize() + " bytes"));
            }
            if (operation.actualSha256() != null) {
                operationNode.add(node("Current SHA-256: " + operation.actualSha256()));
            }
            if (operation.reason() != null) {
                operationNode.add(node("Reason: " + operation.reason()));
            }
            workspace.add(operationNode);
        }
        root.add(workspace);

        RetainedFeatureClient.TransactionView.PlanFreshness freshness =
                transaction.planFreshness();
        DefaultMutableTreeNode freshnessNode = node("Plan freshness: " + freshness.state());
        freshnessNode.add(node("Format: " + freshness.format()));
        freshnessNode.add(node("Plan: " + freshness.planId()));
        if (freshness.reason() != null) {
            freshnessNode.add(node("Reason: " + freshness.reason()));
        }
        root.add(freshnessNode);

        DefaultMutableTreeNode lineage = node(
                "Retained lineage (" + transaction.records().size() + ")"
        );
        for (FeatureRecordCatalog.Record record : transaction.records()) {
            DefaultMutableTreeNode recordNode = node(
                    record.collection() + " · " + record.recordState()
                            + " · " + record.verificationState()
            );
            recordNode.add(node("ID: " + record.recordId()));
            recordNode.add(node("Kind: " + record.recordKind()));
            recordNode.add(node("URI: " + record.uri()));
            if (record.diagnosticCode() != null) {
                recordNode.add(node("Diagnostic: " + record.diagnosticCode()));
            }
            lineage.add(recordNode);
        }
        root.add(lineage);

        long eligibleCount = transaction.actions().stream()
                .filter(RetainedFeatureClient.TransactionView.Action::available)
                .count();
        DefaultMutableTreeNode actions = node(
                "Eligible next actions (" + eligibleCount + " of "
                        + transaction.actions().size() + ")"
        );
        for (RetainedFeatureClient.TransactionView.Action action : transaction.actions()) {
            DefaultMutableTreeNode actionNode = node(
                    action.action() + " · " + (action.available() ? "eligible" : "blocked")
            );
            if (action.recordId() != null) {
                actionNode.add(node("Record: " + action.recordId()));
            }
            if (action.consentId() != null) {
                actionNode.add(node("Consent ID: " + action.consentId()));
            }
            if (action.reason() != null) {
                actionNode.add(node("Reason: " + action.reason()));
            }
            actions.add(actionNode);
        }
        root.add(actions);

        DefaultMutableTreeNode operations = node(
                "Compact reviewed operations (" + transaction.operations().size() + ")"
        );
        for (int index = 0; index < transaction.operations().size(); index++) {
            RetainedFeatureClient.TransactionView.Operation compact =
                    transaction.operations().get(index);
            FeaturePresentation.Operation exact = owner.operations().get(index);
            DefaultMutableTreeNode operationNode = new DefaultMutableTreeNode(
                    new OperationEntry(
                            (compact.ordinal() + 1) + ". " + compact.path()
                                    + " · " + compact.role(),
                            exact
                    )
            );
            operationNode.add(node(
                    "Before: " + compact.beforeSize() + " bytes · "
                            + compact.beforeSha256()
            ));
            operationNode.add(node(
                    "After: " + compact.afterSize() + " bytes · "
                            + compact.afterSha256()
            ));
            operationNode.add(node(
                    "Owner diff: " + compact.diff().getBytes(
                            java.nio.charset.StandardCharsets.UTF_8
                    ).length + " UTF-8 bytes"
            ));
            operationNode.add(node(
                    "Exact before/after byte custody: selected owner presentation"
            ));
            operations.add(operationNode);
        }
        root.add(operations);

        DefaultMutableTreeNode limitations = node(
                "Transaction limitations (" + transaction.limitations().size() + ")"
        );
        for (String limitation : transaction.limitations()) {
            limitations.add(node(limitation));
        }
        root.add(limitations);

        DefaultMutableTreeNode ownerProjection = node(
                "Selected immutable owner projection · " + owner.collection()
                        + " · " + owner.ownerRecord().state()
        );
        ownerProjection.add(build(owner));
        root.add(ownerProjection);
        return root;
    }

    private static @NotNull DefaultMutableTreeNode runtimeSide(
            @NotNull FeaturePresentation.RuntimeSide side
    ) {
        DefaultMutableTreeNode result = node(
                side.role() + " · " + side.state() + " · " + side.outcome()
        );
        result.add(node("Role: " + side.role()));
        result.add(node("State: " + side.state()));
        result.add(node("Outcome: " + side.outcome()));

        FeaturePresentation.AssertionReference assertion = side.assertion();
        if (assertion == null) {
            result.add(node("Assertion: none"));
        } else {
            DefaultMutableTreeNode assertionNode = node("Assertion");
            assertionNode.add(node("ID: " + assertion.id()));
            assertionNode.add(node("State: " + assertion.state()));
            result.add(assertionNode);
        }

        FeaturePresentation.RuntimeError error = side.error();
        if (error == null) {
            result.add(node("Error: none"));
        } else {
            DefaultMutableTreeNode errorNode = node("Error");
            errorNode.add(node("Kind: " + error.kind()));
            errorNode.add(node("Message: " + error.message()));
            errorNode.add(node("Phase: " + error.phase()));
            result.add(errorNode);
        }

        FeaturePresentation.ProbeReference probe = side.probe();
        if (probe == null) {
            result.add(node("Probe: none"));
        } else {
            DefaultMutableTreeNode probeNode = node("Probe reference");
            probeNode.add(node("ID: " + probe.id()));
            probeNode.add(node("Overlay ID: " + probe.overlayId()));
            probeNode.add(node("Overlay URI: " + probe.overlayUri()));
            probeNode.add(node("Script URI: " + probe.scriptUri()));
            result.add(probeNode);
        }

        FeaturePresentation.ReceiptCustody receipt = side.receipt();
        if (receipt == null) {
            result.add(node("Receipt: none"));
        } else {
            DefaultMutableTreeNode receiptNode = node("Retained evidence references");
            receiptNode.add(receiptReference("Final launch", receipt.finalLaunch()));
            FeaturePresentation.GroovyReference log = receipt.groovyLog();
            if (log == null) {
                receiptNode.add(node("Groovy log: none"));
            } else {
                DefaultMutableTreeNode logNode = node("Groovy log");
                logNode.add(node("SHA-256: " + log.sha256()));
                logNode.add(node("Size: " + log.size() + " bytes"));
                logNode.add(node("URI: " + log.uri()));
                receiptNode.add(logNode);
            }
            receiptNode.add(receiptReference("Runtime session", receipt.runtimeSession()));
            result.add(receiptNode);
        }
        return result;
    }

    private static @NotNull DefaultMutableTreeNode receiptReference(
            @NotNull String label,
            @NotNull FeaturePresentation.ReceiptReference reference
    ) {
        DefaultMutableTreeNode result = node(label);
        result.add(node("ID: " + reference.id()));
        result.add(node("SHA-256: " + reference.sha256()));
        result.add(node("Size: " + reference.size() + " bytes"));
        result.add(node("URI: " + reference.uri()));
        return result;
    }

    static @Nullable FeaturePresentation.Operation selectedOperation(@Nullable TreePath path) {
        if (path == null) {
            return null;
        }
        Object[] values = path.getPath();
        for (int index = values.length - 1; index >= 0; index--) {
            if (values[index] instanceof DefaultMutableTreeNode node
                    && node.getUserObject() instanceof OperationEntry entry) {
                return entry.operation();
            }
        }
        return null;
    }

    private static void addObjectMembers(
            @NotNull DefaultMutableTreeNode parent,
            @NotNull JsonObject value,
            int depth,
            int @NotNull [] budget
    ) {
        if (value.isEmpty()) {
            parent.add(node("{}"));
            return;
        }
        for (Map.Entry<String, JsonElement> entry : value.entrySet()) {
            addJson(parent, entry.getKey(), entry.getValue(), depth, budget);
        }
    }

    private static void addJson(
            @NotNull DefaultMutableTreeNode parent,
            @NotNull String label,
            @NotNull JsonElement value,
            int depth,
            int @NotNull [] budget
    ) {
        if (--budget[0] < 0) {
            if (budget[0] == -1) {
                parent.add(node("… remaining exact values are available in owner JSON"));
            }
            return;
        }
        if (depth >= 32) {
            parent.add(node(label + ": (nested owner value)"));
            return;
        }
        if (value.isJsonObject()) {
            DefaultMutableTreeNode child = node(label);
            addObjectMembers(child, value.getAsJsonObject(), depth + 1, budget);
            parent.add(child);
        } else if (value.isJsonArray()) {
            JsonArray array = value.getAsJsonArray();
            DefaultMutableTreeNode child = node(label + " (" + array.size() + ")");
            for (int index = 0; index < array.size(); index++) {
                addJson(child, "[" + index + "]", array.get(index), depth + 1, budget);
            }
            parent.add(child);
        } else if (value.isJsonNull()) {
            parent.add(node(label + ": null"));
        } else {
            String rendered = value.getAsJsonPrimitive().isString()
                    ? value.getAsString()
                    : value.toString();
            if (rendered.length() > 512) {
                rendered = rendered.substring(0, 512) + "… (open owner JSON for the exact value)";
            }
            parent.add(node(label + ": " + rendered));
        }
    }

    private static @NotNull DefaultMutableTreeNode node(@NotNull String value) {
        return new DefaultMutableTreeNode(value);
    }

    record OperationEntry(
            @NotNull String label,
            @NotNull FeaturePresentation.Operation operation
    ) {
        @Override
        public @NotNull String toString() {
            return label;
        }
    }
}
