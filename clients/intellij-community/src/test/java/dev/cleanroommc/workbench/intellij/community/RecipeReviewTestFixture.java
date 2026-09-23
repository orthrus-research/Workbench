package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

final class RecipeReviewTestFixture {
    static final int PULL_REQUEST = 2002;
    static final String BASE_OID = "1".repeat(40);
    static final String HEAD_OID = "2".repeat(40);
    private static final Gson GSON = new GsonBuilder()
            .disableHtmlEscaping()
            .serializeNulls()
            .create();

    private RecipeReviewTestFixture() {
    }

    static JsonObject planObject() {
        JsonObject identity = new JsonObject();
        identity.addProperty(
                "acquisition_profile_id",
                "workbench-pack:supersymmetry:acquisition-v1"
        );
        identity.addProperty(
                "acquisition_profile_digest",
                "workbench-project-acquisition-profile:sha256:" + "3".repeat(64)
        );
        identity.addProperty(
                "provider_profile_id",
                "workbench-pack:supersymmetry:github-pull-requests-v1"
        );
        identity.addProperty(
                "provider_profile_digest",
                "workbench-pull-request-provider-profile:sha256:" + "4".repeat(64)
        );
        identity.addProperty("provider_kind", "github");
        identity.addProperty("project_id", "supersymmetry");
        identity.addProperty("pull_request", PULL_REQUEST);
        identity.addProperty("provider_observation_mode", "github-api");
        identity.addProperty(
                "pull_request_url",
                "https://github.com/SymmetricDevs/Supersymmetry/pull/2002"
        );
        identity.addProperty("pull_request_state", "open");
        identity.addProperty("pull_request_merged", false);
        identity.addProperty("base_repository", "SymmetricDevs/Supersymmetry");
        identity.addProperty("base_name", "master-ceu");
        identity.addProperty("base_oid", BASE_OID);
        identity.addProperty("head_repository", "contributor/Supersymmetry");
        identity.addProperty("head_name", "recipe-fix");
        identity.addProperty("head_oid", HEAD_OID);
        identity.add("provider_merge_oid", JsonNull.INSTANCE);
        identity.addProperty("channel_id", "github");
        identity.addProperty(
                "remote_url", "https://github.com/SymmetricDevs/Supersymmetry.git"
        );
        identity.addProperty("base_remote_ref", "refs/heads/master-ceu");
        identity.addProperty("head_remote_ref", "refs/pull/2002/head");
        identity.addProperty("provider_merge_remote_ref", "refs/pull/2002/merge");
        identity.addProperty("delta_kind", "provider-base-to-head");
        identity.addProperty("repository_root", "/work/Supersymmetry");
        identity.addProperty("repository_head_before_prepare", HEAD_OID);
        identity.addProperty("state_root", "/work/state");
        identity.addProperty("git_executable", "/usr/bin/git");

        JsonObject plan = new JsonObject();
        plan.addProperty("format", "workbench-pr-preparation-plan-v2");
        plan.addProperty("schema_version", 2);
        plan.addProperty(
                "operation_class", "review-provider-state-before-network-write"
        );
        identity.entrySet().forEach(entry -> plan.add(
                entry.getKey(), entry.getValue().isJsonNull()
                        ? JsonNull.INSTANCE : entry.getValue().deepCopy()
        ));
        plan.addProperty(
                "plan_id",
                CanonicalJson.contentId("workbench-pr-preparation-plan-v2", identity)
        );
        JsonArray effects = new JsonArray();
        effects.add("Revalidate the exact GitHub pull-request response before fetching.");
        effects.add("Fetch provider-bound base, head, and declared merge objects into temporary refs.");
        effects.add("Retain immutable Workbench-only refs and a provider-bound V2 receipt.");
        plan.add("effects", effects);
        return plan;
    }

    static String planJson() {
        return GSON.toJson(planObject());
    }

    static JsonObject reviewObject() {
        JsonObject plan = planObject();
        JsonObject root = new JsonObject();
        root.addProperty("format", "workbench-recipe-review-v2");
        root.addProperty("schema_version", 2);
        root.addProperty("report_id", "");
        root.addProperty("operation_class", "read-only");

        JsonObject authority = new JsonObject();
        authority.addProperty("analysis_owner", "Pack Program Studio");
        authority.addProperty("git_identity_owner", "Project Intelligence");
        authority.addProperty("construction_authority", "none");
        root.add("authority", authority);

        JsonObject source = new JsonObject();
        source.addProperty("format", "workbench-groovy-pack-program-report-v1");
        source.addProperty("schema_version", 1);
        source.addProperty("report_id", "workbench-pack-program:sha256:" + "5".repeat(64));
        source.addProperty("availability", "rerun with --full-json-v1");
        root.add("source_report", source);

        JsonObject profile = new JsonObject();
        profile.addProperty(
                "profile_id",
                "workbench-pack:supersymmetry:groovy-program:master-ceu-v1"
        );
        profile.addProperty("profile_sha256", "6".repeat(64));
        profile.addProperty("pack_profile_id", "workbench-pack:supersymmetry");
        profile.addProperty(
                "platform_profile_id",
                "workbench-platform:cleanroom:groovyscript:1.4.3"
        );
        profile.addProperty("platform_profile_sha256", "7".repeat(64));
        root.add("profile", profile);

        root.add("selection", selection(plan));
        root.add("summary", summary());

        JsonObject files = new JsonObject();
        files.add("added", fileGroup());
        files.add("modified", fileGroup("postInit/Recipes.groovy"));
        files.add("removed", fileGroup());
        root.add("files", files);

        JsonObject recipes = new JsonObject();
        JsonArray modified = new JsonArray();
        modified.add(modification());
        recipes.add("modified", boundedRows(modified));
        JsonArray added = new JsonArray();
        added.add(recipe());
        recipes.add("added", boundedRows(added));
        recipes.add("removed", boundedRows(new JsonArray()));
        recipes.addProperty(
                "pairing_boundary",
                "Only unique one-to-one rows with exact non-property structure are paired."
        );
        root.add("machine_recipes", recipes);

        JsonObject direct = new JsonObject();
        direct.add("added", boundedRows(new JsonArray()));
        direct.add("removed", boundedRows(new JsonArray()));
        direct.addProperty("boundary", "Rows are static source statements.");
        root.add("direct_removal_calls", direct);

        JsonObject attention = new JsonObject();
        attention.add("strict_reasons", strings("Introduced recipe signal"));
        attention.addProperty("strict_reasons_truncated", false);
        attention.add("configuration_warnings", strings("Preexisting candidate warning"));
        attention.addProperty("configuration_warnings_truncated", false);
        attention.addProperty("configuration_warnings_drive_strict", false);
        root.add("attention", attention);

        JsonObject guidance = new JsonObject();
        guidance.addProperty("change_state", "bounded-static-review");
        guidance.addProperty("recommendation", "Review the modified mixer duration.");
        guidance.addProperty("save_risk_count", 0);
        root.add("review_guidance", guidance);

        JsonObject evidence = new JsonObject();
        evidence.addProperty("candidate_program_id", "candidate-program");
        evidence.addProperty("baseline_program_id", "baseline-program");
        JsonObject git = new JsonObject();
        git.addProperty("repository_root", "/work/Supersymmetry");
        git.addProperty("revision", HEAD_OID);
        git.addProperty("dirty", false);
        evidence.add("candidate_git", git);
        evidence.addProperty("runtime_state", "not-supplied");
        evidence.addProperty("static_effect_rows_truncated", false);
        root.add("evidence", evidence);

        root.add("limitations", strings("Static source candidates are not observed registry effects."));
        JsonArray next = new JsonArray();
        JsonObject action = new JsonObject();
        action.addProperty("id", "full-owner-report");
        action.addProperty("description", "Emit the complete owner report.");
        action.addProperty("command_hint", "rerun this review with --full-json-v1");
        next.add(action);
        root.add("next_actions", next);
        reidentify(root);
        return root;
    }

    static String reviewJson() {
        return GSON.toJson(reviewObject());
    }

    static void reidentify(JsonObject root) {
        JsonObject body = root.deepCopy();
        body.remove("report_id");
        root.addProperty(
                "report_id", CanonicalJson.contentId("workbench-recipe-review", body)
        );
    }

    private static JsonObject selection(JsonObject plan) {
        JsonObject selection = new JsonObject();
        selection.addProperty("kind", "prepared-provider-pull-request");
        selection.addProperty("project_id", "supersymmetry");
        selection.addProperty("pull_request", PULL_REQUEST);
        selection.addProperty("pull_request_url", plan.get("pull_request_url").getAsString());
        selection.addProperty("pull_request_state", "open");
        selection.addProperty("pull_request_merged", false);
        selection.addProperty("provider_kind", "github");
        selection.addProperty(
                "provider_profile_id",
                "workbench-pack:supersymmetry:github-pull-requests-v1"
        );
        selection.addProperty("remote_url", plan.get("remote_url").getAsString());
        selection.addProperty("channel_id", "github");
        selection.addProperty("repository_root", "/work/Supersymmetry");
        selection.addProperty("delta_kind", "provider-base-to-head");
        selection.add("base", gitSide(
                "SymmetricDevs/Supersymmetry", "master-ceu", BASE_OID,
                "refs/heads/master-ceu", "refs/workbench/review-v2/base"
        ));
        selection.add("head", gitSide(
                "contributor/Supersymmetry", "recipe-fix", HEAD_OID,
                "refs/pull/2002/head", "refs/workbench/review-v2/head"
        ));
        JsonObject merge = new JsonObject();
        merge.addProperty("remote_ref", "refs/pull/2002/merge");
        merge.add("immutable_ref", JsonNull.INSTANCE);
        merge.add("oid", JsonNull.INSTANCE);
        selection.add("provider_merge", merge);
        selection.addProperty("receipt_id", "workbench-pr-preparation-v2:sha256:" + "8".repeat(64));
        selection.addProperty("prepared_at", "2026-09-01T00:00:00Z");
        JsonObject committed = new JsonObject();
        committed.add("repository", fileGroup("groovy/postInit/Recipes.groovy", "README.md"));
        committed.add("selected", fileGroup("groovy/postInit/Recipes.groovy"));
        committed.add("excluded", fileGroup("README.md"));
        selection.add("committed_scope", committed);
        JsonObject attention = new JsonObject();
        attention.addProperty("introduced_static_signals", 1);
        attention.addProperty("preexisting_static_signals", 2);
        attention.addProperty("candidate_static_signal_total", 3);
        attention.addProperty("supplied_runtime_attention", false);
        attention.addProperty(
                "pr_strict", "introduced static signals and supplied runtime attention"
        );
        attention.addProperty(
                "strict_all",
                "candidate-wide static signals, supplied runtime attention, "
                        + "source configuration warnings, and Git hygiene attention"
        );
        selection.add("attention_scope", attention);
        JsonObject hygiene = new JsonObject();
        hygiene.addProperty("state", "clean");
        hygiene.addProperty("detail", "Candidate checkout was clean when prepared.");
        selection.add("git_hygiene", hygiene);
        return selection;
    }

    private static JsonObject gitSide(
            String repository,
            String name,
            String oid,
            String remoteRef,
            String immutableRef
    ) {
        JsonObject side = new JsonObject();
        side.addProperty("repository", repository);
        side.addProperty("name", name);
        side.addProperty("remote_ref", remoteRef);
        side.addProperty("immutable_ref", immutableRef);
        side.addProperty("oid", oid);
        return side;
    }

    private static JsonObject summary() {
        JsonObject summary = new JsonObject();
        summary.addProperty("status", "attention");
        summary.addProperty("strict_exit_code", 1);
        summary.addProperty("analysis_state", "bounded-static");
        summary.addProperty("comparison_state", "available");
        summary.addProperty("runtime_state", "not-supplied");
        summary.addProperty("changed_source_files", 1);
        JsonObject recipes = new JsonObject();
        recipes.addProperty("modified", 1);
        recipes.addProperty("added", 1);
        recipes.addProperty("removed", 0);
        summary.add("machine_recipes", recipes);
        JsonObject direct = new JsonObject();
        direct.addProperty("added", 0);
        direct.addProperty("removed", 0);
        direct.addProperty("counts_incomplete", false);
        direct.addProperty("runtime_invocation_counts", "unknown");
        summary.add("direct_removal_source_statements", direct);
        return summary;
    }

    private static JsonObject modification() {
        JsonObject row = new JsonObject();
        row.addProperty("recipe_map", "MIXER");
        row.addProperty("before_semantic_key", "mixer-before");
        row.addProperty("after_semantic_key", "mixer-after");
        row.add("before_source", source(6));
        row.add("after_source", source(8));
        JsonObject changes = new JsonObject();
        JsonObject duration = new JsonObject();
        duration.add("before", strings("100"));
        duration.add("after", strings("120"));
        changes.add("duration", duration);
        row.add("property_changes", changes);
        row.addProperty("properties_truncated", false);
        row.addProperty("complete", true);
        row.add("lifecycle", lifecycle());
        row.addProperty("reload_state", "restart-required");
        row.addProperty("pairing_basis", "unique-exact-non-property-structure");
        return row;
    }

    private static JsonObject recipe() {
        JsonObject row = new JsonObject();
        row.addProperty("semantic_key", "mixer-added");
        row.addProperty("count", 1);
        row.addProperty("recipe_map", "MIXER");
        row.addProperty("complete", true);
        JsonObject properties = new JsonObject();
        properties.add("duration", strings("80"));
        row.add("properties", properties);
        row.addProperty("properties_truncated", false);
        row.add("source", source(20));
        row.add("lifecycle", lifecycle());
        row.addProperty("reload_state", "restart-required");
        row.addProperty("evidence_state", "static-source-candidate");
        return row;
    }

    private static JsonObject lifecycle() {
        JsonObject lifecycle = new JsonObject();
        lifecycle.addProperty("stage", "postInit");
        lifecycle.addProperty("execution_state", "included");
        lifecycle.addProperty("runtime_invocation_count", "unknown");
        return lifecycle;
    }

    private static JsonObject source(int line) {
        JsonObject source = new JsonObject();
        source.addProperty("path", "postInit/Recipes.groovy");
        source.addProperty("line", line);
        source.addProperty("column", 1);
        return source;
    }

    private static JsonObject fileGroup(String... paths) {
        JsonObject group = new JsonObject();
        group.add("paths", strings(paths));
        group.addProperty("path_count", paths.length);
        group.addProperty("truncated", false);
        return group;
    }

    private static JsonObject boundedRows(JsonArray rows) {
        JsonObject group = new JsonObject();
        group.add("rows", rows);
        group.addProperty("row_count", rows.size());
        group.addProperty("truncated", false);
        return group;
    }

    private static JsonArray strings(String... values) {
        JsonArray result = new JsonArray();
        for (String value : values) {
            result.add(value);
        }
        return result;
    }
}
