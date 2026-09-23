package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.tree.DefaultMutableTreeNode;
import java.util.List;

/** Native decision-first tree over the exact core-owned PR plan and review. */
final class RecipeReviewTree {
    sealed interface Target permits SourceTarget, ModificationTarget {
    }

    record SourceTarget(@NotNull RecipeReview.Source source) implements Target {
    }

    record ModificationTarget(@NotNull RecipeReview.Modification modification)
            implements Target {
    }

    record Entry(@NotNull String text, @Nullable Target target) {
        @Override
        public @NotNull String toString() {
            return text;
        }
    }

    private RecipeReviewTree() {
    }

    static @NotNull DefaultMutableTreeNode empty(@NotNull String message) {
        return node(message);
    }

    static @NotNull DefaultMutableTreeNode plan(@NotNull RecipeReviewPlan plan) {
        DefaultMutableTreeNode root = node(
                "PR #" + plan.pullRequest() + " · review plan ready for consent"
        );
        root.add(node("Decision: no refs changed; review the exact plan below"));
        DefaultMutableTreeNode scope = node("Provider-recorded PR scope");
        scope.add(node("Pull request: " + plan.pullRequestUrl()));
        scope.add(node("Provider state: " + providerState(plan.state(), plan.merged())));
        scope.add(node(
                "Base: " + plan.baseRepository() + ":" + plan.baseName()
                        + " at " + shortOid(plan.baseOid())
        ));
        scope.add(node(
                "Head: " + plan.headRepository() + ":" + plan.headName()
                        + " at " + shortOid(plan.headOid())
        ));
        scope.add(node(
                "Provider merge: " + (plan.providerMergeOid() == null
                        ? "not supplied" : shortOid(plan.providerMergeOid()))
        ));
        scope.add(node("Local repository: " + plan.repositoryRoot()));
        root.add(scope);

        DefaultMutableTreeNode effects = node("Exact effects requiring consent");
        plan.effects().forEach(effect -> effects.add(node(effect)));
        root.add(effects);
        root.add(node("Plan identity: " + plan.planId()));
        return root;
    }

    static @NotNull DefaultMutableTreeNode review(@NotNull RecipeReview review) {
        RecipeReview.Summary summary = review.summary();
        RecipeReview.Scope scopeValue = review.scope();
        DefaultMutableTreeNode root = node(
                "PR #" + scopeValue.pullRequest() + " · decision: "
                        + summary.status().toUpperCase()
        );
        root.add(node(
                "Decision summary: " + summary.modifiedRecipes() + " modified, "
                        + summary.addedRecipes() + " added, "
                        + summary.removedRecipes() + " removed machine recipe(s)"
        ));
        root.add(node(
                "Source scope: " + summary.changedSourceFiles()
                        + " changed recipe source file(s)"
        ));
        root.add(node("Core recommendation: " + review.recommendation()));

        DefaultMutableTreeNode scope = node("PR-scoped Git identity");
        scope.add(node("Pull request: " + scopeValue.pullRequestUrl()));
        scope.add(node(
                "Provider state: " + providerState(scopeValue.state(), scopeValue.merged())
        ));
        scope.add(node(
                "Historical delta: " + scopeValue.base().repository() + ":"
                        + scopeValue.base().name() + " at "
                        + shortOid(scopeValue.base().oid()) + " → "
                        + scopeValue.head().repository() + ":" + scopeValue.head().name()
                        + " at " + shortOid(scopeValue.head().oid())
        ));
        scope.add(fileGroup("Selected committed files", scopeValue.selectedFiles()));
        scope.add(fileGroup("Excluded committed files", scopeValue.excludedFiles()));
        root.add(scope);

        DefaultMutableTreeNode findings = node("PR-scoped recipe findings");
        findings.add(modifications(review.modifiedRecipes()));
        findings.add(findings("Added machine recipes", review.addedRecipes()));
        findings.add(findings("Removed machine recipes", review.removedRecipes()));
        findings.add(findings(
                "Added direct-removal statements", review.addedDirectRemovals()
        ));
        findings.add(findings(
                "Removed direct-removal statements", review.removedDirectRemovals()
        ));
        root.add(findings);

        DefaultMutableTreeNode files = node("Changed recipe source files");
        files.add(fileGroup("Added", review.addedFiles()));
        files.add(fileGroup("Modified", review.modifiedFiles()));
        files.add(fileGroup("Removed", review.removedFiles()));
        root.add(files);

        DefaultMutableTreeNode attention = node("Attention");
        RecipeReview.AttentionScope attentionScope = scopeValue.attentionScope();
        DefaultMutableTreeNode prAttention = node("PR-introduced decision scope");
        prAttention.add(node(
                "Introduced static signals: " + attentionScope.introducedStaticSignals()
        ));
        prAttention.add(node(
                "Supplied runtime attention: "
                        + (attentionScope.suppliedRuntimeAttention() ? "yes" : "no")
        ));
        prAttention.add(node("Default strict scope: " + attentionScope.prStrict()));
        if (review.attention().strictReasons().isEmpty()) {
            prAttention.add(node("No PR-scoped strict attention reasons reported"));
        } else {
            DefaultMutableTreeNode strict = node(
                    "PR decision-driving reasons"
                            + truncated(review.attention().strictReasonsTruncated())
            );
            review.attention().strictReasons().forEach(reason -> strict.add(node(reason)));
            prAttention.add(strict);
        }
        attention.add(prAttention);

        DefaultMutableTreeNode candidate = node(
                "Preexisting and candidate-wide context (not PR-introduced)"
        );
        candidate.add(node(
                "Preexisting static signals: " + attentionScope.preexistingStaticSignals()
        ));
        candidate.add(node(
                "Candidate static-signal total: "
                        + attentionScope.candidateStaticSignalTotal()
        ));
        candidate.add(node("Strict-all scope: " + attentionScope.strictAll()));
        if (!review.attention().configurationWarnings().isEmpty()) {
            DefaultMutableTreeNode warnings = node(
                    "Candidate source configuration warnings"
                            + truncated(review.attention().configurationWarningsTruncated())
            );
            review.attention().configurationWarnings().forEach(
                    warning -> warnings.add(node(warning))
            );
            candidate.add(warnings);
        }
        candidate.add(node(
                "Git hygiene: " + scopeValue.gitHygiene().state() + " · "
                        + scopeValue.gitHygiene().detail()
        ));
        attention.add(candidate);
        root.add(attention);

        DefaultMutableTreeNode evidence = node("Evidence boundary");
        evidence.add(node("Analysis: " + summary.analysisState()));
        evidence.add(node("Comparison: " + summary.comparisonState()));
        evidence.add(node("Runtime evidence: " + summary.runtimeState()));
        evidence.add(node("Guidance state: " + review.guidanceState()));
        if (summary.addedDirectRemovalStatements() > 0
                || summary.removedDirectRemovalStatements() > 0) {
            evidence.add(node(
                    "Direct-removal source statements: "
                            + summary.addedDirectRemovalStatements() + " added, "
                            + summary.removedDirectRemovalStatements() + " removed"
                            + (summary.directRemovalCountsIncomplete()
                            ? " · counts are incomplete" : "")
            ));
        }
        review.limitations().forEach(limit -> evidence.add(node(limit)));
        root.add(evidence);
        root.add(node("Report identity: " + review.reportId()));
        return root;
    }

    static @Nullable Target target(@Nullable Object value) {
        if (!(value instanceof DefaultMutableTreeNode node)
                || !(node.getUserObject() instanceof Entry entry)) {
            return null;
        }
        return entry.target();
    }

    private static @NotNull DefaultMutableTreeNode modifications(
            @NotNull RecipeReview.FindingGroup<RecipeReview.Modification> group
    ) {
        DefaultMutableTreeNode node = node(
                "Modified machine recipes · " + group.count() + truncated(group.truncated())
        );
        for (RecipeReview.Modification modification : group.rows()) {
            String properties = modification.propertyChanges().isEmpty()
                    ? "no projected property rows"
                    : modification.propertyChanges().stream()
                    .map(RecipeReview.PropertyChange::name)
                    .limit(4)
                    .reduce((left, right) -> left + ", " + right)
                    .orElse("");
            DefaultMutableTreeNode row = node(
                    modification.recipeMap() + " · " + properties,
                    new ModificationTarget(modification)
            );
            row.add(sourceNode("Before", modification.beforeSource()));
            row.add(sourceNode("After", modification.afterSource()));
            modification.propertyChanges().forEach(change -> row.add(node(
                    change.name() + ": " + values(change.before()) + " → "
                            + values(change.after())
            )));
            if (modification.propertiesTruncated()) {
                row.add(node("Property projection is bounded; use the complete owner report"));
            }
            node.add(row);
        }
        return node;
    }

    private static @NotNull DefaultMutableTreeNode findings(
            @NotNull String label,
            @NotNull RecipeReview.FindingGroup<RecipeReview.Finding> group
    ) {
        DefaultMutableTreeNode node = node(
                label + " · " + group.count() + truncated(group.truncated())
        );
        for (RecipeReview.Finding finding : group.rows()) {
            node.add(new DefaultMutableTreeNode(new Entry(
                    finding.label() + (finding.count() == 1 ? "" : " ×" + finding.count())
                            + " · " + sourceLabel(finding.source()),
                    new SourceTarget(finding.source())
            )));
        }
        return node;
    }

    private static @NotNull DefaultMutableTreeNode fileGroup(
            @NotNull String label,
            @NotNull RecipeReview.FileGroup group
    ) {
        DefaultMutableTreeNode node = node(
                label + " · " + group.count() + truncated(group.truncated())
        );
        for (String path : group.paths()) {
            RecipeReview.Source source = new RecipeReview.Source(path, null, null);
            node.add(new DefaultMutableTreeNode(new Entry(
                    path,
                    new SourceTarget(source)
            )));
        }
        return node;
    }

    private static @NotNull DefaultMutableTreeNode sourceNode(
            @NotNull String label,
            @NotNull RecipeReview.Source source
    ) {
        return new DefaultMutableTreeNode(new Entry(
                label + ": " + sourceLabel(source),
                new SourceTarget(source)
        ));
    }

    private static @NotNull DefaultMutableTreeNode node(@NotNull String text) {
        return node(text, null);
    }

    private static @NotNull DefaultMutableTreeNode node(
            @NotNull String text,
            @Nullable Target target
    ) {
        return new DefaultMutableTreeNode(new Entry(text, target));
    }

    private static @NotNull String sourceLabel(@NotNull RecipeReview.Source source) {
        return source.path() + (source.line() == null ? "" : ":" + source.line());
    }

    private static @NotNull String values(@Nullable List<String> values) {
        if (values == null) {
            return "not set";
        }
        if (values.isEmpty()) {
            return "empty";
        }
        return String.join(", ", values);
    }

    private static @NotNull String providerState(@NotNull String state, boolean merged) {
        return merged ? "merged" : state;
    }

    private static @NotNull String truncated(boolean value) {
        return value ? " · bounded list" : "";
    }

    private static @NotNull String shortOid(@NotNull String value) {
        return value.length() <= 12 ? value : value.substring(0, 12) + "…";
    }
}
