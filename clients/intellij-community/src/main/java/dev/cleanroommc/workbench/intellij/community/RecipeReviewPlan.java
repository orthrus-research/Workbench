package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Authenticated provider-bound plan shown before the IDE authorizes PR preparation. */
record RecipeReviewPlan(
        @NotNull String rawJson,
        @NotNull String planId,
        int pullRequest,
        @NotNull String pullRequestUrl,
        @NotNull String state,
        boolean merged,
        @NotNull String baseRepository,
        @NotNull String baseName,
        @NotNull String baseOid,
        @NotNull String headRepository,
        @NotNull String headName,
        @NotNull String headOid,
        @Nullable String providerMergeOid,
        @NotNull String repositoryRoot,
        @NotNull List<String> effects
) {
    static final int MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
    private static final Pattern OBJECT_ID = Pattern.compile("^[0-9a-f]{40}(?:[0-9a-f]{24})?$");
    private static final Pattern PLAN_ID = Pattern.compile(
            "^workbench-pr-preparation-plan-v2:sha256:[0-9a-f]{64}$"
    );
    private static final Set<String> IDENTITY_FIELDS = Set.of(
            "acquisition_profile_id",
            "acquisition_profile_digest",
            "provider_profile_id",
            "provider_profile_digest",
            "provider_kind",
            "project_id",
            "pull_request",
            "provider_observation_mode",
            "pull_request_url",
            "pull_request_state",
            "pull_request_merged",
            "base_repository",
            "base_name",
            "base_oid",
            "head_repository",
            "head_name",
            "head_oid",
            "provider_merge_oid",
            "channel_id",
            "remote_url",
            "base_remote_ref",
            "head_remote_ref",
            "provider_merge_remote_ref",
            "delta_kind",
            "repository_root",
            "repository_head_before_prepare",
            "state_root",
            "git_executable"
    );
    private static final Set<String> ROOT_FIELDS;

    static {
        Set<String> fields = new LinkedHashSet<>(IDENTITY_FIELDS);
        fields.addAll(Set.of(
                "format", "schema_version", "operation_class", "plan_id", "effects"
        ));
        ROOT_FIELDS = Set.copyOf(fields);
    }

    static @NotNull RecipeReviewPlan parse(
            @NotNull String json,
            int expectedPullRequest
    ) {
        if (json.getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT_BYTES) {
            throw RecipeReviewJson.invalid("PR review plan", "exceeds its byte bound");
        }
        JsonObject root = RecipeReviewJson.root(json, "PR review plan", 100_000);
        RecipeReviewJson.exactKeys(root, ROOT_FIELDS, "PR review plan");
        if (!RecipeReviewJson.text(root, "format", "PR review plan")
                .equals("workbench-pr-preparation-plan-v2")
                || RecipeReviewJson.nonnegativeInteger(
                root, "schema_version", "PR review plan"
        ) != 2
                || !RecipeReviewJson.text(root, "operation_class", "PR review plan")
                .equals("review-provider-state-before-network-write")) {
            throw RecipeReviewJson.invalid("PR review plan", "format is unsupported");
        }
        int pullRequest = RecipeReviewJson.positiveInteger(
                root, "pull_request", "PR review plan"
        );
        if (pullRequest != expectedPullRequest) {
            throw RecipeReviewJson.invalid(
                    "PR review plan", "does not identify the requested pull request"
            );
        }
        if (!RecipeReviewJson.text(root, "project_id", "PR review plan")
                .equals("supersymmetry")
                || !RecipeReviewJson.text(root, "provider_kind", "PR review plan")
                .equals("github")
                || !RecipeReviewJson.text(root, "delta_kind", "PR review plan")
                .equals("provider-base-to-head")) {
            throw RecipeReviewJson.invalid("PR review plan", "authority is unsupported");
        }
        String planId = RecipeReviewJson.text(root, "plan_id", "PR review plan");
        if (!PLAN_ID.matcher(planId).matches()) {
            throw RecipeReviewJson.invalid("PR review plan", "identity is malformed");
        }
        JsonObject identity = new JsonObject();
        for (String field : IDENTITY_FIELDS) {
            JsonElement value = root.get(field);
            if (value == null) {
                throw RecipeReviewJson.invalid("PR review plan", "identity is incomplete");
            }
            identity.add(field, value.deepCopy());
        }
        String expectedId = CanonicalJson.contentId(
                "workbench-pr-preparation-plan-v2", identity
        );
        if (!planId.equals(expectedId)) {
            throw RecipeReviewJson.invalid(
                    "PR review plan", "identity does not match its exact provider body"
            );
        }
        String state = RecipeReviewJson.text(root, "pull_request_state", "PR review plan");
        boolean merged = RecipeReviewJson.bool(root, "pull_request_merged", "PR review plan");
        if (!(state.equals("open") || state.equals("closed"))
                || (merged && !state.equals("closed"))) {
            throw RecipeReviewJson.invalid("PR review plan", "provider state is inconsistent");
        }
        String baseOid = objectId(root, "base_oid");
        String headOid = objectId(root, "head_oid");
        String mergeOid = RecipeReviewJson.nullableText(
                root, "provider_merge_oid", "PR review plan"
        );
        if (mergeOid != null && !OBJECT_ID.matcher(mergeOid).matches()) {
            throw RecipeReviewJson.invalid("PR review plan", "provider merge identity is invalid");
        }
        List<String> effects = RecipeReviewJson.textList(
                RecipeReviewJson.array(root, "effects", "PR review plan"),
                16,
                "PR review plan effects"
        );
        if (effects.isEmpty()) {
            throw RecipeReviewJson.invalid("PR review plan", "has no declared effects");
        }
        return new RecipeReviewPlan(
                json,
                planId,
                pullRequest,
                RecipeReviewJson.text(root, "pull_request_url", "PR review plan"),
                state,
                merged,
                RecipeReviewJson.text(root, "base_repository", "PR review plan"),
                RecipeReviewJson.text(root, "base_name", "PR review plan"),
                baseOid,
                RecipeReviewJson.text(root, "head_repository", "PR review plan"),
                RecipeReviewJson.text(root, "head_name", "PR review plan"),
                headOid,
                mergeOid,
                RecipeReviewJson.text(root, "repository_root", "PR review plan"),
                effects
        );
    }

    private static @NotNull String objectId(
            @NotNull JsonObject root,
            @NotNull String field
    ) {
        String value = RecipeReviewJson.text(root, field, "PR review plan");
        if (!OBJECT_ID.matcher(value).matches()) {
            throw RecipeReviewJson.invalid("PR review plan", "field " + field + " is invalid");
        }
        return value;
    }

    @Override
    public @NotNull List<String> effects() {
        return List.copyOf(effects);
    }
}
