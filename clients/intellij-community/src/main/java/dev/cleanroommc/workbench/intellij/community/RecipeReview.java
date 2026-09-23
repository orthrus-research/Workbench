package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict compact Recipe Review V2 consumer; all decisions remain core-owned. */
record RecipeReview(
        @NotNull String rawJson,
        @NotNull String reportId,
        @NotNull Scope scope,
        @NotNull Summary summary,
        @NotNull FileGroup addedFiles,
        @NotNull FileGroup modifiedFiles,
        @NotNull FileGroup removedFiles,
        @NotNull FindingGroup<Modification> modifiedRecipes,
        @NotNull FindingGroup<Finding> addedRecipes,
        @NotNull FindingGroup<Finding> removedRecipes,
        @NotNull FindingGroup<Finding> addedDirectRemovals,
        @NotNull FindingGroup<Finding> removedDirectRemovals,
        @NotNull Attention attention,
        @NotNull String guidanceState,
        @NotNull String recommendation,
        @NotNull List<String> limitations
) {
    static final int MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
    private static final Pattern REPORT_ID = Pattern.compile(
            "^workbench-recipe-review:sha256:[0-9a-f]{64}$"
    );
    private static final String SUPERSYMMETRY_PACK_PROFILE_ID =
            "workbench-pack:supersymmetry";
    private static final Set<String> ROOT_FIELDS = Set.of(
            "format",
            "schema_version",
            "report_id",
            "operation_class",
            "authority",
            "source_report",
            "profile",
            "selection",
            "summary",
            "files",
            "machine_recipes",
            "direct_removal_calls",
            "attention",
            "review_guidance",
            "evidence",
            "limitations",
            "next_actions"
    );

    record Scope(
            int pullRequest,
            @NotNull String pullRequestUrl,
            @NotNull String state,
            boolean merged,
            @NotNull GitSide base,
            @NotNull GitSide head,
            @NotNull FileGroup selectedFiles,
            @NotNull FileGroup excludedFiles,
            @NotNull AttentionScope attentionScope,
            @NotNull GitHygiene gitHygiene
    ) {
    }

    record AttentionScope(
            int introducedStaticSignals,
            int preexistingStaticSignals,
            int candidateStaticSignalTotal,
            boolean suppliedRuntimeAttention,
            @NotNull String prStrict,
            @NotNull String strictAll
    ) {
    }

    record GitHygiene(
            @NotNull String state,
            @NotNull String detail
    ) {
    }

    record GitSide(
            @NotNull String repository,
            @NotNull String name,
            @NotNull String oid
    ) {
    }

    record Summary(
            @NotNull String status,
            int strictExitCode,
            @NotNull String analysisState,
            @NotNull String comparisonState,
            @NotNull String runtimeState,
            int changedSourceFiles,
            int modifiedRecipes,
            int addedRecipes,
            int removedRecipes,
            int addedDirectRemovalStatements,
            int removedDirectRemovalStatements,
            boolean directRemovalCountsIncomplete
    ) {
    }

    record FileGroup(
            @NotNull List<String> paths,
            int count,
            boolean truncated
    ) {
        FileGroup {
            paths = List.copyOf(paths);
        }
    }

    record FindingGroup<T>(
            @NotNull List<T> rows,
            int count,
            boolean truncated
    ) {
        FindingGroup {
            rows = List.copyOf(rows);
        }
    }

    enum FindingKind {
        RECIPE_ADDED,
        RECIPE_REMOVED,
        DIRECT_REMOVAL_ADDED,
        DIRECT_REMOVAL_REMOVED
    }

    record Source(
            @NotNull String path,
            @Nullable Integer line,
            @Nullable Integer column
    ) {
    }

    record Finding(
            @NotNull FindingKind kind,
            @NotNull String label,
            int count,
            @NotNull Source source
    ) {
    }

    record PropertyChange(
            @NotNull String name,
            @Nullable List<String> before,
            @Nullable List<String> after
    ) {
        PropertyChange {
            before = before == null ? null : List.copyOf(before);
            after = after == null ? null : List.copyOf(after);
        }
    }

    record Modification(
            @NotNull String recipeMap,
            @NotNull String beforeSemanticKey,
            @NotNull String afterSemanticKey,
            @NotNull Source beforeSource,
            @NotNull Source afterSource,
            @NotNull List<PropertyChange> propertyChanges,
            boolean propertiesTruncated,
            @NotNull String pairingBasis
    ) {
        Modification {
            propertyChanges = List.copyOf(propertyChanges);
        }
    }

    record Attention(
            @NotNull List<String> strictReasons,
            boolean strictReasonsTruncated,
            @NotNull List<String> configurationWarnings,
            boolean configurationWarningsTruncated
    ) {
        Attention {
            strictReasons = List.copyOf(strictReasons);
            configurationWarnings = List.copyOf(configurationWarnings);
        }
    }

    static @NotNull RecipeReview parse(
            @NotNull String json,
            @NotNull RecipeReviewPlan plan
    ) {
        if (json.getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT_BYTES) {
            throw RecipeReviewJson.invalid("Recipe Review V2", "exceeds its byte bound");
        }
        JsonObject root = RecipeReviewJson.root(json, "Recipe Review V2", 2_000_000);
        RecipeReviewJson.exactKeys(root, ROOT_FIELDS, "Recipe Review V2");
        if (!RecipeReviewJson.text(root, "format", "Recipe Review V2")
                .equals("workbench-recipe-review-v2")
                || RecipeReviewJson.nonnegativeInteger(
                root, "schema_version", "Recipe Review V2"
        ) != 2
                || !RecipeReviewJson.text(root, "operation_class", "Recipe Review V2")
                .equals("read-only")) {
            throw RecipeReviewJson.invalid("Recipe Review V2", "format is unsupported");
        }
        String reportId = RecipeReviewJson.text(root, "report_id", "Recipe Review V2");
        if (!REPORT_ID.matcher(reportId).matches()) {
            throw RecipeReviewJson.invalid("Recipe Review V2", "identity is malformed");
        }
        CanonicalJson.verifyObjectIdentity(
                root, "report_id", "workbench-recipe-review", false
        );
        JsonObject authority = RecipeReviewJson.object(root, "authority", "Recipe Review V2");
        RecipeReviewJson.exactKeys(
                authority,
                Set.of("analysis_owner", "git_identity_owner", "construction_authority"),
                "Recipe Review V2 authority"
        );
        if (!RecipeReviewJson.text(authority, "analysis_owner", "Recipe Review V2 authority")
                .equals("Pack Program Studio")
                || !RecipeReviewJson.text(
                authority, "git_identity_owner", "Recipe Review V2 authority"
        ).equals("Project Intelligence")
                || !RecipeReviewJson.text(
                authority, "construction_authority", "Recipe Review V2 authority"
        ).equals("none")) {
            throw RecipeReviewJson.invalid("Recipe Review V2", "authority is unsupported");
        }
        JsonObject source = RecipeReviewJson.object(
                root, "source_report", "Recipe Review V2"
        );
        RecipeReviewJson.exactKeys(
                source,
                Set.of("format", "schema_version", "report_id", "availability"),
                "Recipe Review V2 source"
        );
        if (!RecipeReviewJson.text(source, "format", "Recipe Review V2 source")
                .equals("workbench-groovy-pack-program-report-v1")
                || RecipeReviewJson.nonnegativeInteger(
                source, "schema_version", "Recipe Review V2 source"
        ) != 1
                || RecipeReviewJson.text(source, "report_id", "Recipe Review V2 source").isBlank()
                || !RecipeReviewJson.text(
                source, "availability", "Recipe Review V2 source"
        ).equals("rerun with --full-json-v1")) {
            throw RecipeReviewJson.invalid("Recipe Review V2", "source authority is unsupported");
        }

        validateProfile(RecipeReviewJson.object(root, "profile", "Recipe Review V2"));

        Scope scope = scope(RecipeReviewJson.object(root, "selection", "Recipe Review V2"), plan);
        Summary summary = summary(RecipeReviewJson.object(root, "summary", "Recipe Review V2"));
        JsonObject files = RecipeReviewJson.object(root, "files", "Recipe Review V2");
        RecipeReviewJson.exactKeys(
                files, Set.of("added", "modified", "removed"), "Recipe Review V2 files"
        );
        JsonObject recipes = RecipeReviewJson.object(
                root, "machine_recipes", "Recipe Review V2"
        );
        RecipeReviewJson.exactKeys(
                recipes,
                Set.of("modified", "added", "removed", "pairing_boundary"),
                "Recipe Review V2 recipes"
        );
        if (RecipeReviewJson.text(
                recipes, "pairing_boundary", "Recipe Review V2 recipes"
        ).isBlank()) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 recipes", "pairing boundary is empty"
            );
        }
        JsonObject direct = RecipeReviewJson.object(
                root, "direct_removal_calls", "Recipe Review V2"
        );
        RecipeReviewJson.exactKeys(
                direct,
                Set.of("added", "removed", "boundary"),
                "Recipe Review V2 direct removals"
        );
        if (RecipeReviewJson.text(
                direct, "boundary", "Recipe Review V2 direct removals"
        ).isBlank()) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 direct removals", "boundary is empty"
            );
        }
        JsonObject attention = RecipeReviewJson.object(
                root, "attention", "Recipe Review V2"
        );
        RecipeReviewJson.exactKeys(
                attention,
                Set.of(
                        "strict_reasons",
                        "strict_reasons_truncated",
                        "configuration_warnings",
                        "configuration_warnings_truncated",
                        "configuration_warnings_drive_strict"
                ),
                "Recipe Review V2 attention"
        );
        if (RecipeReviewJson.bool(
                attention,
                "configuration_warnings_drive_strict",
                "Recipe Review V2 attention"
        )) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 attention", "warning strictness is unsupported"
            );
        }
        JsonObject guidance = RecipeReviewJson.object(
                root, "review_guidance", "Recipe Review V2"
        );
        RecipeReviewJson.exactKeys(
                guidance,
                Set.of("change_state", "recommendation", "save_risk_count"),
                "Recipe Review V2 guidance"
        );
        RecipeReviewJson.nonnegativeInteger(
                guidance, "save_risk_count", "Recipe Review V2 guidance"
        );
        validateEvidence(RecipeReviewJson.object(root, "evidence", "Recipe Review V2"));
        validateNextActions(RecipeReviewJson.array(root, "next_actions", "Recipe Review V2"));
        return new RecipeReview(
                json,
                reportId,
                scope,
                summary,
                fileGroup(RecipeReviewJson.object(files, "added", "Recipe Review V2 files")),
                fileGroup(RecipeReviewJson.object(files, "modified", "Recipe Review V2 files")),
                fileGroup(RecipeReviewJson.object(files, "removed", "Recipe Review V2 files")),
                modificationGroup(RecipeReviewJson.object(
                        recipes, "modified", "Recipe Review V2 recipes"
                )),
                recipeGroup(RecipeReviewJson.object(
                        recipes, "added", "Recipe Review V2 recipes"
                ), FindingKind.RECIPE_ADDED),
                recipeGroup(RecipeReviewJson.object(
                        recipes, "removed", "Recipe Review V2 recipes"
                ), FindingKind.RECIPE_REMOVED),
                directGroup(RecipeReviewJson.object(
                        direct, "added", "Recipe Review V2 direct removals"
                ), FindingKind.DIRECT_REMOVAL_ADDED),
                directGroup(RecipeReviewJson.object(
                        direct, "removed", "Recipe Review V2 direct removals"
                ), FindingKind.DIRECT_REMOVAL_REMOVED),
                new Attention(
                        RecipeReviewJson.textList(
                                RecipeReviewJson.array(
                                        attention,
                                        "strict_reasons",
                                        "Recipe Review V2 attention"
                                ),
                                200,
                                "Recipe Review V2 strict reasons"
                        ),
                        RecipeReviewJson.bool(
                                attention,
                                "strict_reasons_truncated",
                                "Recipe Review V2 attention"
                        ),
                        RecipeReviewJson.textList(
                                RecipeReviewJson.array(
                                        attention,
                                        "configuration_warnings",
                                        "Recipe Review V2 attention"
                                ),
                                200,
                                "Recipe Review V2 configuration warnings"
                        ),
                        RecipeReviewJson.bool(
                                attention,
                                "configuration_warnings_truncated",
                                "Recipe Review V2 attention"
                        )
                ),
                RecipeReviewJson.text(
                        guidance, "change_state", "Recipe Review V2 guidance"
                ),
                RecipeReviewJson.text(
                        guidance, "recommendation", "Recipe Review V2 guidance"
                ),
                RecipeReviewJson.textList(
                        RecipeReviewJson.array(root, "limitations", "Recipe Review V2"),
                        64,
                        "Recipe Review V2 limitations"
                )
        );
    }

    private static @NotNull Scope scope(
            @NotNull JsonObject value,
            @NotNull RecipeReviewPlan plan
    ) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of(
                        "kind",
                        "project_id",
                        "pull_request",
                        "pull_request_url",
                        "pull_request_state",
                        "pull_request_merged",
                        "provider_kind",
                        "provider_profile_id",
                        "remote_url",
                        "channel_id",
                        "repository_root",
                        "delta_kind",
                        "base",
                        "head",
                        "provider_merge",
                        "receipt_id",
                        "prepared_at",
                        "committed_scope",
                        "attention_scope",
                        "git_hygiene"
                ),
                "Recipe Review V2 selection"
        );
        if (!RecipeReviewJson.text(value, "kind", "Recipe Review V2 selection")
                .equals("prepared-provider-pull-request")
                || !RecipeReviewJson.text(value, "project_id", "Recipe Review V2 selection")
                .equals("supersymmetry")
                || !RecipeReviewJson.text(value, "provider_kind", "Recipe Review V2 selection")
                .equals("github")
                || !RecipeReviewJson.text(value, "delta_kind", "Recipe Review V2 selection")
                .equals("provider-base-to-head")) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 selection", "is not a provider-bound PR review"
            );
        }
        int pullRequest = RecipeReviewJson.positiveInteger(
                value, "pull_request", "Recipe Review V2 selection"
        );
        String url = RecipeReviewJson.text(
                value, "pull_request_url", "Recipe Review V2 selection"
        );
        String state = RecipeReviewJson.text(
                value, "pull_request_state", "Recipe Review V2 selection"
        );
        boolean merged = RecipeReviewJson.bool(
                value, "pull_request_merged", "Recipe Review V2 selection"
        );
        JsonObject base = RecipeReviewJson.object(value, "base", "Recipe Review V2 selection");
        JsonObject head = RecipeReviewJson.object(value, "head", "Recipe Review V2 selection");
        validateProviderMerge(RecipeReviewJson.object(
                value, "provider_merge", "Recipe Review V2 selection"
        ), plan);
        for (String field : List.of(
                "provider_profile_id",
                "remote_url",
                "channel_id",
                "repository_root",
                "receipt_id",
                "prepared_at"
        )) {
            if (RecipeReviewJson.text(value, field, "Recipe Review V2 selection").isBlank()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 selection", "field " + field + " is empty"
                );
            }
        }
        GitSide baseSide = gitSide(base, "base");
        GitSide headSide = gitSide(head, "head");
        if (pullRequest != plan.pullRequest()
                || !url.equals(plan.pullRequestUrl())
                || !state.equals(plan.state())
                || merged != plan.merged()
                || !baseSide.repository().equals(plan.baseRepository())
                || !baseSide.name().equals(plan.baseName())
                || !baseSide.oid().equals(plan.baseOid())
                || !headSide.repository().equals(plan.headRepository())
                || !headSide.name().equals(plan.headName())
                || !headSide.oid().equals(plan.headOid())) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 selection", "does not match the consented provider plan"
            );
        }
        JsonObject committed = RecipeReviewJson.object(
                value, "committed_scope", "Recipe Review V2 selection"
        );
        RecipeReviewJson.exactKeys(
                committed,
                Set.of("repository", "selected", "excluded"),
                "Recipe Review V2 committed scope"
        );
        fileGroup(RecipeReviewJson.object(
                committed, "repository", "Recipe Review V2 committed scope"
        ));
        AttentionScope attentionScope = attentionScope(RecipeReviewJson.object(
                value, "attention_scope", "Recipe Review V2 selection"
        ));
        GitHygiene gitHygiene = gitHygiene(RecipeReviewJson.object(
                value, "git_hygiene", "Recipe Review V2 selection"
        ));
        return new Scope(
                pullRequest,
                url,
                state,
                merged,
                baseSide,
                headSide,
                fileGroup(RecipeReviewJson.object(
                        committed, "selected", "Recipe Review V2 committed scope"
                )),
                fileGroup(RecipeReviewJson.object(
                        committed, "excluded", "Recipe Review V2 committed scope"
                )),
                attentionScope,
                gitHygiene
        );
    }

    private static @NotNull AttentionScope attentionScope(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of(
                        "introduced_static_signals",
                        "preexisting_static_signals",
                        "candidate_static_signal_total",
                        "supplied_runtime_attention",
                        "pr_strict",
                        "strict_all"
                ),
                "Recipe Review V2 attention scope"
        );
        int introduced = RecipeReviewJson.nonnegativeInteger(
                value, "introduced_static_signals", "Recipe Review V2 attention scope"
        );
        int preexisting = RecipeReviewJson.nonnegativeInteger(
                value, "preexisting_static_signals", "Recipe Review V2 attention scope"
        );
        int total = RecipeReviewJson.nonnegativeInteger(
                value, "candidate_static_signal_total", "Recipe Review V2 attention scope"
        );
        if (introduced + preexisting != total) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 attention scope", "static-signal counts are inconsistent"
            );
        }
        String prStrict = RecipeReviewJson.text(
                value, "pr_strict", "Recipe Review V2 attention scope"
        );
        String strictAll = RecipeReviewJson.text(
                value, "strict_all", "Recipe Review V2 attention scope"
        );
        if (!prStrict.equals("introduced static signals and supplied runtime attention")
                || !strictAll.equals(
                "candidate-wide static signals, supplied runtime attention, "
                        + "source configuration warnings, and Git hygiene attention"
        )) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 attention scope", "strict scope is unsupported"
            );
        }
        return new AttentionScope(
                introduced,
                preexisting,
                total,
                RecipeReviewJson.bool(
                        value,
                        "supplied_runtime_attention",
                        "Recipe Review V2 attention scope"
                ),
                prStrict,
                strictAll
        );
    }

    private static @NotNull GitHygiene gitHygiene(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("state", "detail"),
                "Recipe Review V2 Git hygiene"
        );
        String state = RecipeReviewJson.text(
                value, "state", "Recipe Review V2 Git hygiene"
        );
        if (!(state.equals("clean") || state.equals("attention")
                || state.equals("unavailable"))) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 Git hygiene", "state is unsupported"
            );
        }
        return new GitHygiene(
                state,
                RecipeReviewJson.text(value, "detail", "Recipe Review V2 Git hygiene")
        );
    }

    private static @NotNull GitSide gitSide(
            @NotNull JsonObject value,
            @NotNull String label
    ) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("repository", "name", "remote_ref", "immutable_ref", "oid"),
                "Recipe Review V2 " + label
        );
        RecipeReviewJson.text(value, "remote_ref", "Recipe Review V2 " + label);
        RecipeReviewJson.text(value, "immutable_ref", "Recipe Review V2 " + label);
        return new GitSide(
                RecipeReviewJson.text(value, "repository", "Recipe Review V2 " + label),
                RecipeReviewJson.text(value, "name", "Recipe Review V2 " + label),
                RecipeReviewJson.text(value, "oid", "Recipe Review V2 " + label)
        );
    }

    private static @NotNull Summary summary(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of(
                        "status",
                        "strict_exit_code",
                        "analysis_state",
                        "comparison_state",
                        "runtime_state",
                        "changed_source_files",
                        "machine_recipes",
                        "direct_removal_source_statements"
                ),
                "Recipe Review V2 summary"
        );
        String status = RecipeReviewJson.text(value, "status", "Recipe Review V2 summary");
        int strict = RecipeReviewJson.nonnegativeInteger(
                value, "strict_exit_code", "Recipe Review V2 summary"
        );
        int expectedStrict = switch (status) {
            case "ready" -> 0;
            case "attention" -> 1;
            case "blocked" -> 2;
            default -> throw RecipeReviewJson.invalid(
                    "Recipe Review V2 summary", "status is unsupported"
            );
        };
        if (strict != expectedStrict) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 summary", "strict result is inconsistent"
            );
        }
        JsonObject recipes = RecipeReviewJson.object(
                value, "machine_recipes", "Recipe Review V2 summary"
        );
        RecipeReviewJson.exactKeys(
                recipes,
                Set.of("modified", "added", "removed"),
                "Recipe Review V2 summary recipes"
        );
        JsonObject direct = RecipeReviewJson.object(
                value, "direct_removal_source_statements", "Recipe Review V2 summary"
        );
        RecipeReviewJson.exactKeys(
                direct,
                Set.of(
                        "added",
                        "removed",
                        "counts_incomplete",
                        "runtime_invocation_counts"
                ),
                "Recipe Review V2 direct-removal summary"
        );
        if (!RecipeReviewJson.text(
                direct,
                "runtime_invocation_counts",
                "Recipe Review V2 direct-removal summary"
        ).equals("unknown")) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 direct-removal summary",
                    "runtime invocation state is unsupported"
            );
        }
        return new Summary(
                status,
                strict,
                RecipeReviewJson.text(value, "analysis_state", "Recipe Review V2 summary"),
                RecipeReviewJson.text(value, "comparison_state", "Recipe Review V2 summary"),
                RecipeReviewJson.text(value, "runtime_state", "Recipe Review V2 summary"),
                RecipeReviewJson.nonnegativeInteger(
                        value, "changed_source_files", "Recipe Review V2 summary"
                ),
                RecipeReviewJson.nonnegativeInteger(
                        recipes, "modified", "Recipe Review V2 summary"
                ),
                RecipeReviewJson.nonnegativeInteger(
                        recipes, "added", "Recipe Review V2 summary"
                ),
                RecipeReviewJson.nonnegativeInteger(
                        recipes, "removed", "Recipe Review V2 summary"
                ),
                RecipeReviewJson.nonnegativeInteger(
                        direct, "added", "Recipe Review V2 summary"
                ),
                RecipeReviewJson.nonnegativeInteger(
                        direct, "removed", "Recipe Review V2 summary"
                ),
                RecipeReviewJson.bool(
                        direct, "counts_incomplete", "Recipe Review V2 summary"
                )
        );
    }

    private static @NotNull FileGroup fileGroup(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("paths", "path_count", "truncated"),
                "Recipe Review V2 file group"
        );
        List<String> paths = RecipeReviewJson.textList(
                RecipeReviewJson.array(value, "paths", "Recipe Review V2 file group"),
                2_000,
                "Recipe Review V2 file paths"
        );
        int count = RecipeReviewJson.nonnegativeInteger(
                value, "path_count", "Recipe Review V2 file group"
        );
        boolean truncated = RecipeReviewJson.bool(
                value, "truncated", "Recipe Review V2 file group"
        );
        if (paths.size() > count || (!truncated && paths.size() != count)) {
            throw RecipeReviewJson.invalid("Recipe Review V2 file group", "bounds are inconsistent");
        }
        return new FileGroup(paths, count, truncated);
    }

    private static @NotNull FindingGroup<Modification> modificationGroup(
            @NotNull JsonObject value
    ) {
        JsonArray rows = RecipeReviewJson.array(value, "rows", "Recipe Review V2 modifications");
        List<Modification> findings = new ArrayList<>(rows.size());
        for (JsonElement element : rows) {
            if (!element.isJsonObject()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 modifications", "contains a non-object row"
                );
            }
            JsonObject row = element.getAsJsonObject();
            RecipeReviewJson.exactKeys(
                    row,
                    Set.of(
                            "recipe_map",
                            "before_semantic_key",
                            "after_semantic_key",
                            "before_source",
                            "after_source",
                            "property_changes",
                            "properties_truncated",
                            "complete",
                            "lifecycle",
                            "reload_state",
                            "pairing_basis"
                    ),
                    "Recipe Review V2 modification"
            );
            nullableBoolean(row, "complete", "Recipe Review V2 modification");
            validateLifecycle(RecipeReviewJson.object(
                    row, "lifecycle", "Recipe Review V2 modification"
            ));
            RecipeReviewJson.text(row, "reload_state", "Recipe Review V2 modification");
            JsonObject changes = RecipeReviewJson.object(
                    row, "property_changes", "Recipe Review V2 modification"
            );
            List<PropertyChange> properties = new ArrayList<>(changes.size());
            for (Map.Entry<String, JsonElement> entry : changes.entrySet()) {
                if (!entry.getValue().isJsonObject()) {
                    throw RecipeReviewJson.invalid(
                            "Recipe Review V2 modification", "has an invalid property change"
                    );
                }
                JsonObject change = entry.getValue().getAsJsonObject();
                RecipeReviewJson.exactKeys(
                        change,
                        Set.of("before", "after"),
                        "Recipe Review V2 property change"
                );
                properties.add(new PropertyChange(
                        entry.getKey(),
                        nullableTextList(change, "before", "Recipe Review V2 property change"),
                        nullableTextList(change, "after", "Recipe Review V2 property change")
                ));
            }
            findings.add(new Modification(
                    RecipeReviewJson.text(row, "recipe_map", "Recipe Review V2 modification"),
                    RecipeReviewJson.text(
                            row, "before_semantic_key", "Recipe Review V2 modification"
                    ),
                    RecipeReviewJson.text(
                            row, "after_semantic_key", "Recipe Review V2 modification"
                    ),
                    source(RecipeReviewJson.object(
                            row, "before_source", "Recipe Review V2 modification"
                    )),
                    source(RecipeReviewJson.object(
                            row, "after_source", "Recipe Review V2 modification"
                    )),
                    properties,
                    RecipeReviewJson.bool(
                            row, "properties_truncated", "Recipe Review V2 modification"
                    ),
                    RecipeReviewJson.text(
                            row, "pairing_basis", "Recipe Review V2 modification"
                    )
            ));
        }
        return boundedGroup(value, findings, "Recipe Review V2 modifications");
    }

    private static @NotNull FindingGroup<Finding> recipeGroup(
            @NotNull JsonObject value,
            @NotNull FindingKind kind
    ) {
        JsonArray rows = RecipeReviewJson.array(value, "rows", "Recipe Review V2 recipes");
        List<Finding> findings = new ArrayList<>(rows.size());
        for (JsonElement element : rows) {
            if (!element.isJsonObject()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 recipes", "contains a non-object row"
                );
            }
            JsonObject row = element.getAsJsonObject();
            RecipeReviewJson.exactKeys(
                    row,
                    Set.of(
                            "semantic_key",
                            "count",
                            "recipe_map",
                            "complete",
                            "properties",
                            "properties_truncated",
                            "source",
                            "lifecycle",
                            "reload_state",
                            "evidence_state"
                    ),
                    "Recipe Review V2 recipe"
            );
            nullableBoolean(row, "complete", "Recipe Review V2 recipe");
            validateProperties(RecipeReviewJson.object(
                    row, "properties", "Recipe Review V2 recipe"
            ));
            RecipeReviewJson.bool(
                    row, "properties_truncated", "Recipe Review V2 recipe"
            );
            validateLifecycle(RecipeReviewJson.object(
                    row, "lifecycle", "Recipe Review V2 recipe"
            ));
            RecipeReviewJson.text(row, "reload_state", "Recipe Review V2 recipe");
            RecipeReviewJson.text(row, "evidence_state", "Recipe Review V2 recipe");
            String map = RecipeReviewJson.text(row, "recipe_map", "Recipe Review V2 recipe");
            String key = RecipeReviewJson.text(row, "semantic_key", "Recipe Review V2 recipe");
            findings.add(new Finding(
                    kind,
                    map + " · " + key,
                    RecipeReviewJson.positiveInteger(row, "count", "Recipe Review V2 recipe"),
                    source(RecipeReviewJson.object(row, "source", "Recipe Review V2 recipe"))
            ));
        }
        return boundedGroup(value, findings, "Recipe Review V2 recipes");
    }

    private static @NotNull FindingGroup<Finding> directGroup(
            @NotNull JsonObject value,
            @NotNull FindingKind kind
    ) {
        JsonArray rows = RecipeReviewJson.array(
                value, "rows", "Recipe Review V2 direct removals"
        );
        List<Finding> findings = new ArrayList<>(rows.size());
        for (JsonElement element : rows) {
            if (!element.isJsonObject()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 direct removals", "contains a non-object row"
                );
            }
            JsonObject row = element.getAsJsonObject();
            RecipeReviewJson.exactKeys(
                    row,
                    Set.of(
                            "semantic_key",
                            "expression",
                            "adapter_path",
                            "method",
                            "source_statement_count",
                            "source",
                            "stage",
                            "execution_state",
                            "loop_expansion",
                            "runtime_invocation_count"
                    ),
                    "Recipe Review V2 direct removal"
            );
            RecipeReviewJson.text(row, "semantic_key", "Recipe Review V2 direct removal");
            RecipeReviewJson.text(row, "adapter_path", "Recipe Review V2 direct removal");
            RecipeReviewJson.text(row, "stage", "Recipe Review V2 direct removal");
            RecipeReviewJson.text(row, "execution_state", "Recipe Review V2 direct removal");
            RecipeReviewJson.text(row, "loop_expansion", "Recipe Review V2 direct removal");
            RecipeReviewJson.text(
                    row, "runtime_invocation_count", "Recipe Review V2 direct removal"
            );
            String expression = RecipeReviewJson.text(
                    row, "expression", "Recipe Review V2 direct removal"
            );
            String method = RecipeReviewJson.text(
                    row, "method", "Recipe Review V2 direct removal"
            );
            findings.add(new Finding(
                    kind,
                    method + " · " + expression,
                    RecipeReviewJson.positiveInteger(
                            row, "source_statement_count", "Recipe Review V2 direct removal"
                    ),
                    source(RecipeReviewJson.object(
                            row, "source", "Recipe Review V2 direct removal"
                    ))
            ));
        }
        return boundedGroup(value, findings, "Recipe Review V2 direct removals");
    }

    private static <T> @NotNull FindingGroup<T> boundedGroup(
            @NotNull JsonObject value,
            @NotNull List<T> rows,
            @NotNull String label
    ) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("rows", "row_count", "truncated"),
                label
        );
        if (rows.size() > 200) {
            throw RecipeReviewJson.invalid(label, "exceeds its row bound");
        }
        int count = RecipeReviewJson.nonnegativeInteger(value, "row_count", label);
        boolean truncated = RecipeReviewJson.bool(value, "truncated", label);
        if (rows.size() > count || (!truncated && rows.size() != count)) {
            throw RecipeReviewJson.invalid(label, "bounds are inconsistent");
        }
        return new FindingGroup<>(rows, count, truncated);
    }

    private static @NotNull Source source(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("path", "line", "column"),
                "Recipe Review V2 source"
        );
        return new Source(
                RecipeReviewJson.text(value, "path", "Recipe Review V2 source"),
                nullableInteger(value, "line", "Recipe Review V2 source"),
                nullableInteger(value, "column", "Recipe Review V2 source")
        );
    }

    private static @Nullable Integer nullableInteger(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null) {
            throw RecipeReviewJson.invalid(label, "field " + field + " is missing");
        }
        if (selected.isJsonNull()) {
            return null;
        }
        return RecipeReviewJson.positiveInteger(value, field, label);
    }

    private static @Nullable List<String> nullableTextList(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null) {
            throw RecipeReviewJson.invalid(label, "field " + field + " is missing");
        }
        if (selected.isJsonNull()) {
            return null;
        }
        if (!selected.isJsonArray()) {
            throw RecipeReviewJson.invalid(label, "field " + field + " must be an array or null");
        }
        return RecipeReviewJson.textList(selected.getAsJsonArray(), 24, label + " " + field);
    }

    private static void validateProfile(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of(
                        "profile_id",
                        "profile_sha256",
                        "pack_profile_id",
                        "platform_profile_id",
                        "platform_profile_sha256"
                ),
                "Recipe Review V2 profile"
        );
        for (String field : value.keySet()) {
            RecipeReviewJson.nullableText(value, field, "Recipe Review V2 profile");
        }
        String pack = RecipeReviewJson.nullableText(
                value, "pack_profile_id", "Recipe Review V2 profile"
        );
        if (!SUPERSYMMETRY_PACK_PROFILE_ID.equals(pack)) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 profile",
                    "pack_profile_id must be " + SUPERSYMMETRY_PACK_PROFILE_ID
            );
        }
    }

    private static void validateProviderMerge(
            @NotNull JsonObject value,
            @NotNull RecipeReviewPlan plan
    ) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("remote_ref", "immutable_ref", "oid"),
                "Recipe Review V2 provider merge"
        );
        RecipeReviewJson.nullableText(
                value, "remote_ref", "Recipe Review V2 provider merge"
        );
        RecipeReviewJson.nullableText(
                value, "immutable_ref", "Recipe Review V2 provider merge"
        );
        String oid = RecipeReviewJson.nullableText(
                value, "oid", "Recipe Review V2 provider merge"
        );
        if (!(oid == null ? plan.providerMergeOid() == null : oid.equals(plan.providerMergeOid()))) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 provider merge", "does not match the consented plan"
            );
        }
    }

    private static void validateLifecycle(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of("stage", "execution_state", "runtime_invocation_count"),
                "Recipe Review V2 lifecycle"
        );
        RecipeReviewJson.text(value, "stage", "Recipe Review V2 lifecycle");
        RecipeReviewJson.text(value, "execution_state", "Recipe Review V2 lifecycle");
        RecipeReviewJson.text(
                value, "runtime_invocation_count", "Recipe Review V2 lifecycle"
        );
    }

    private static void validateProperties(@NotNull JsonObject value) {
        if (value.size() > 32) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 recipe properties", "exceeds its field bound"
            );
        }
        for (Map.Entry<String, JsonElement> entry : value.entrySet()) {
            if (entry.getKey().isBlank() || !entry.getValue().isJsonArray()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 recipe properties", "contains an invalid field"
                );
            }
            RecipeReviewJson.textList(
                    entry.getValue().getAsJsonArray(),
                    24,
                    "Recipe Review V2 recipe property " + entry.getKey()
            );
        }
    }

    private static void validateEvidence(@NotNull JsonObject value) {
        RecipeReviewJson.exactKeys(
                value,
                Set.of(
                        "candidate_program_id",
                        "baseline_program_id",
                        "candidate_git",
                        "runtime_state",
                        "static_effect_rows_truncated"
                ),
                "Recipe Review V2 evidence"
        );
        RecipeReviewJson.nullableText(
                value, "candidate_program_id", "Recipe Review V2 evidence"
        );
        RecipeReviewJson.nullableText(
                value, "baseline_program_id", "Recipe Review V2 evidence"
        );
        JsonElement git = value.get("candidate_git");
        if (git == null) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 evidence", "candidate Git binding is missing"
            );
        }
        if (!git.isJsonNull()) {
            if (!git.isJsonObject()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 evidence", "candidate Git binding is invalid"
                );
            }
            JsonObject binding = git.getAsJsonObject();
            RecipeReviewJson.exactKeys(
                    binding,
                    Set.of("repository_root", "revision", "dirty"),
                    "Recipe Review V2 candidate Git binding"
            );
            RecipeReviewJson.text(
                    binding, "repository_root", "Recipe Review V2 candidate Git binding"
            );
            RecipeReviewJson.text(
                    binding, "revision", "Recipe Review V2 candidate Git binding"
            );
            RecipeReviewJson.bool(
                    binding, "dirty", "Recipe Review V2 candidate Git binding"
            );
        }
        RecipeReviewJson.text(value, "runtime_state", "Recipe Review V2 evidence");
        RecipeReviewJson.bool(
                value, "static_effect_rows_truncated", "Recipe Review V2 evidence"
        );
    }

    private static void validateNextActions(@NotNull JsonArray values) {
        if (values.size() > 16) {
            throw RecipeReviewJson.invalid(
                    "Recipe Review V2 next actions", "exceeds its row bound"
            );
        }
        for (JsonElement element : values) {
            if (!element.isJsonObject()) {
                throw RecipeReviewJson.invalid(
                        "Recipe Review V2 next actions", "contains a non-object row"
                );
            }
            JsonObject value = element.getAsJsonObject();
            RecipeReviewJson.exactKeys(
                    value,
                    Set.of("id", "description", "command_hint"),
                    "Recipe Review V2 next action"
            );
            for (String field : value.keySet()) {
                if (RecipeReviewJson.text(
                        value, field, "Recipe Review V2 next action"
                ).isBlank()) {
                    throw RecipeReviewJson.invalid(
                            "Recipe Review V2 next action", "field " + field + " is empty"
                    );
                }
            }
        }
    }

    private static @Nullable Boolean nullableBoolean(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        JsonElement selected = value.get(field);
        if (selected == null) {
            throw RecipeReviewJson.invalid(label, "field " + field + " is missing");
        }
        if (selected.isJsonNull()) {
            return null;
        }
        return RecipeReviewJson.bool(value, field, label);
    }
}
