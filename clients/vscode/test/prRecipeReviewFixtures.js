"use strict";

const crypto = require("node:crypto");
const { canonicalJson, PLAN_EFFECTS } = require("../prRecipeReviewClient");

function withIdentity(prefix, body, field) {
  const identity = {};
  for (const [key, value] of Object.entries(body)) if (key !== field) identity[key] = value;
  return `${prefix}:sha256:${crypto.createHash("sha256")
    .update(canonicalJson(identity), "utf8").digest("hex")}`;
}

function plan(overrides = {}) {
  const body = {
    acquisition_profile_id: "workbench-pack:supersymmetry:acquisition-v1",
    acquisition_profile_digest: `workbench-project-acquisition-profile:sha256:${"1".repeat(64)}`,
    provider_profile_id: "workbench-pack:supersymmetry:github-pull-requests-v1",
    provider_profile_digest: `workbench-pull-request-provider-profile:sha256:${"2".repeat(64)}`,
    provider_kind: "github",
    project_id: "supersymmetry",
    pull_request: 2002,
    provider_observation_mode: "github-api",
    pull_request_url: "https://github.com/SymmetricDevs/Supersymmetry/pull/2002",
    pull_request_state: "closed",
    pull_request_merged: true,
    base_repository: "SymmetricDevs/Supersymmetry",
    base_name: "master-ceu",
    base_oid: "a".repeat(40),
    head_repository: "SymmetricDevs/Supersymmetry",
    head_name: "recipe-review",
    head_oid: "b".repeat(40),
    provider_merge_oid: "c".repeat(40),
    channel_id: "current",
    remote_url: "https://github.com/SymmetricDevs/Supersymmetry.git",
    base_remote_ref: "refs/heads/master-ceu",
    head_remote_ref: "refs/pull/2002/head",
    provider_merge_remote_ref: "refs/pull/2002/merge",
    delta_kind: "provider-base-to-head",
    repository_root: "/work/Supersymmetry",
    repository_head_before_prepare: "d".repeat(40),
    state_root: "/state/workbench",
    git_executable: "/usr/bin/git",
    ...overrides,
  };
  const planId = withIdentity("workbench-pr-preparation-plan-v2", body, "plan_id");
  return {
    format: "workbench-pr-preparation-plan-v2",
    schema_version: 2,
    operation_class: "review-provider-state-before-network-write",
    plan_id: planId,
    ...body,
    effects: [...PLAN_EFFECTS],
  };
}

function report(selectedPlan = plan(), overrides = {}) {
  const body = {
    format: "workbench-recipe-review-v2",
    schema_version: 2,
    operation_class: "read-only",
    authority: {
      analysis_owner: "Pack Program Studio",
      git_identity_owner: "Project Intelligence",
      construction_authority: "none",
    },
    source_report: {
      format: "workbench-groovy-pack-program-report-v1",
      schema_version: 1,
      report_id: `workbench-groovy-pack-program-report:sha256:${"3".repeat(64)}`,
      availability: "rerun with --full-json-v1",
    },
    profile: {
      profile_id: "workbench-pack:supersymmetry:groovy-program:master-ceu-v1",
      profile_sha256: "4".repeat(64),
      pack_profile_id: "workbench-pack:supersymmetry",
      platform_profile_id: "workbench-platform:cleanroom:groovyscript:1.4.3",
      platform_profile_sha256: "5".repeat(64),
    },
    selection: {
      kind: "prepared-provider-pull-request",
      project_id: selectedPlan.project_id,
      pull_request: selectedPlan.pull_request,
      pull_request_url: selectedPlan.pull_request_url,
      pull_request_state: selectedPlan.pull_request_state,
      pull_request_merged: selectedPlan.pull_request_merged,
      provider_kind: selectedPlan.provider_kind,
      provider_profile_id: selectedPlan.provider_profile_id,
      remote_url: selectedPlan.remote_url,
      channel_id: selectedPlan.channel_id,
      repository_root: selectedPlan.repository_root,
      delta_kind: selectedPlan.delta_kind,
      base: {
        repository: selectedPlan.base_repository,
        name: selectedPlan.base_name,
        remote_ref: selectedPlan.base_remote_ref,
        immutable_ref: "refs/workbench/pr-preparation-v2/base/a",
        oid: selectedPlan.base_oid,
      },
      head: {
        repository: selectedPlan.head_repository,
        name: selectedPlan.head_name,
        remote_ref: selectedPlan.head_remote_ref,
        immutable_ref: "refs/workbench/pr-preparation-v2/head/b",
        oid: selectedPlan.head_oid,
      },
      provider_merge: {
        remote_ref: selectedPlan.provider_merge_remote_ref,
        immutable_ref: "refs/workbench/pr-preparation-v2/provider-merge/c",
        oid: selectedPlan.provider_merge_oid,
      },
      receipt_id: `workbench-pr-preparation-v2:sha256:${"6".repeat(64)}`,
      prepared_at: "2026-09-01T12:00:00+00:00",
      committed_scope: {
        repository: {
          paths: ["groovy/recipes/mixer.groovy", "betterquesting/quest-change.json"],
          path_count: 2,
          truncated: false,
        },
        selected: {
          paths: ["groovy/recipes/mixer.groovy"],
          path_count: 1,
          truncated: false,
        },
        excluded: {
          paths: ["betterquesting/quest-change.json"],
          path_count: 1,
          truncated: false,
        },
      },
      attention_scope: {
        introduced_static_signals: 1,
        preexisting_static_signals: 2,
        candidate_static_signal_total: 3,
        supplied_runtime_attention: false,
        pr_strict: "introduced static signals and supplied runtime attention",
        strict_all: "candidate-wide static signals, supplied runtime attention, source configuration warnings, and Git hygiene attention",
      },
      git_hygiene: {
        state: "clean",
        detail: "Exact provider-bound base and head pass git diff --check.",
      },
    },
    summary: {
      status: "attention",
      strict_exit_code: 1,
      analysis_state: "complete",
      comparison_state: "complete",
      runtime_state: "not-supplied",
      changed_source_files: 1,
      machine_recipes: { modified: 1, added: 0, removed: 0 },
      direct_removal_source_statements: {
        added: 1,
        removed: 0,
        counts_incomplete: false,
        runtime_invocation_counts: "unknown",
      },
    },
    files: {
      added: { paths: [], path_count: 0, truncated: false },
      modified: { paths: ["recipes/mixer.groovy"], path_count: 1, truncated: false },
      removed: { paths: [], path_count: 0, truncated: false },
    },
    machine_recipes: {
      modified: {
        rows: [{
          recipe_map: "mixer",
          before_semantic_key: "mixer|old",
          after_semantic_key: "mixer|new",
          before_source: { path: "recipes/mixer.groovy", line: 10, column: 2 },
          after_source: { path: "recipes/mixer.groovy", line: 11, column: 2 },
          property_changes: { duration: { before: ["20"], after: ["40"] } },
          properties_truncated: false,
          complete: true,
          lifecycle: { stage: "static", execution_state: "candidate", runtime_invocation_count: "unknown" },
          reload_state: "unknown",
          pairing_basis: "unique-exact-non-property-structure",
        }],
        row_count: 1,
        truncated: false,
      },
      added: { rows: [], row_count: 0, truncated: false },
      removed: { rows: [], row_count: 0, truncated: false },
      pairing_boundary: "Only unique exact rows are paired.",
    },
    direct_removal_calls: {
      added: {
        rows: [{
          semantic_key: "remove|mixer",
          expression: "mods.gregtech.mixer.removeByOutput(...)" ,
          adapter_path: "mods.gregtech.mixer",
          method: "removeByOutput",
          source_statement_count: 1,
          source: { path: "recipes/mixer.groovy", line: 20, column: 1 },
          stage: "static",
          execution_state: "candidate",
          loop_expansion: "not-performed",
          runtime_invocation_count: "unknown",
        }],
        row_count: 1,
        truncated: false,
      },
      removed: { rows: [], row_count: 0, truncated: false },
      boundary: "Rows are source statements, not runtime invocations.",
    },
    attention: {
      strict_reasons: ["Runtime evidence was not supplied."],
      strict_reasons_truncated: false,
      configuration_warnings: ["Language server was not configured."],
      configuration_warnings_truncated: false,
      configuration_warnings_drive_strict: false,
    },
    review_guidance: {
      change_state: "review",
      recommendation: "Inspect the changed duration and obtain runtime evidence.",
      save_risk_count: 0,
    },
    evidence: {
      candidate_program_id: "candidate:fixture",
      baseline_program_id: "baseline:fixture",
      candidate_git: { revision: selectedPlan.head_oid, dirty: false },
      runtime_state: "not-supplied",
      static_effect_rows_truncated: false,
    },
    limitations: [
      "Static source candidates are not observed registry effects.",
      "The compact record is bounded; use --full-json-v1 for complete owner evidence.",
    ],
    next_actions: [{
      id: "full-owner-report",
      description: "Emit the complete source-linked owner report when needed.",
      command_hint: "rerun this review with --full-json-v1",
    }],
    ...overrides,
  };
  const reportId = withIdentity("workbench-recipe-review", body, "report_id");
  return { ...body, report_id: reportId };
}

module.exports = { plan, report };
