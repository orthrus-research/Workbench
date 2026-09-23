"use strict";

const crypto = require("node:crypto");
const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");

const PLAN_FORMAT = "workbench-pr-preparation-plan-v2";
const REPORT_FORMAT = "workbench-recipe-review-v2";
const PROFILE = "supersymmetry";
const PACK_PROFILE_ID = "workbench-pack:supersymmetry";
const MAX_PULL_REQUEST = 2_147_483_647;
const MAX_PLAN_BYTES = 2 * 1024 * 1024;
const MAX_REPORT_BYTES = 16 * 1024 * 1024;
const REVIEW_TIMEOUT_MS = 30 * 60 * 1000;
const OBJECT_ID = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/;
const PLAN_ID = /^workbench-pr-preparation-plan-v2:sha256:[0-9a-f]{64}$/;
const REPORT_ID = /^workbench-recipe-review:sha256:[0-9a-f]{64}$/;
const PROFILE_ID = /^[A-Za-z0-9_.:-]+$/;
const FULL_REF = /^refs\/[A-Za-z0-9][A-Za-z0-9._/-]*$/;
const REPOSITORY_NAME = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const PLAN_EFFECTS = Object.freeze([
  "Revalidate the exact GitHub pull-request response before fetching.",
  "Fetch provider-bound base, head, and declared merge objects into temporary refs.",
  "Retain immutable Workbench-only refs and a provider-bound V2 receipt.",
]);
const PLAN_IDENTITY_FIELDS = Object.freeze([
  "acquisition_profile_id", "acquisition_profile_digest",
  "provider_profile_id", "provider_profile_digest", "provider_kind", "project_id",
  "pull_request", "provider_observation_mode", "pull_request_url",
  "pull_request_state", "pull_request_merged", "base_repository", "base_name",
  "base_oid", "head_repository", "head_name", "head_oid", "provider_merge_oid",
  "channel_id", "remote_url", "base_remote_ref", "head_remote_ref",
  "provider_merge_remote_ref", "delta_kind", "repository_root",
  "repository_head_before_prepare", "state_root", "git_executable",
]);

function unicodeScalarCompare(left, right) {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0));
  const rightPoints = Array.from(right, (character) => character.codePointAt(0));
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    // Python's core identities sort Unicode keys by scalar value. JavaScript's
    // default UTF-16 sort differs for supplementary characters.
    return `{${Object.keys(value).sort(unicodeScalarCompare).map(
      (key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`,
    ).join(",")}}`;
  }
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new Error("Recipe Review identity contains a non-finite number");
  }
  return JSON.stringify(value);
}

function exactObject(value, required, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be one object`);
  }
  const actual = Object.keys(value).sort();
  const expected = [...required].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} fields changed`);
  }
  return value;
}

function text(value, label, maximum = 32 * 1024, pattern) {
  if (typeof value !== "string" || !value || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > maximum
      || (pattern && !pattern.test(value))) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function displayText(value, label, maximum = 1024) {
  if (typeof value !== "string" || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function nullableText(value, label, maximum, pattern) {
  return value === null ? null : text(value, label, maximum, pattern);
}

function integer(value, label, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < 0 || value > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function bool(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} is invalid`);
  return value;
}

function member(value, values, label) {
  if (!values.includes(value)) throw new Error(`${label} is invalid`);
  return value;
}

function pullRequestNumber(value) {
  if (!Number.isSafeInteger(value) || value < 1 || value > MAX_PULL_REQUEST) {
    throw new Error(`pull request number must be between 1 and ${MAX_PULL_REQUEST}`);
  }
  return value;
}

function boundedRequest(request) {
  exactObject(request, ["pullRequest", "source"], "PR Recipe Review request");
  return {
    pullRequest: pullRequestNumber(request.pullRequest),
    source: text(request.source, "PR Recipe Review source"),
  };
}

function reviewPrArguments(request, phase, launch) {
  const selected = boundedRequest(request);
  const arguments_ = [
    "review", "pr", String(selected.pullRequest),
    "--profile", PROFILE,
    "--source", pathForCoreLaunch(selected.source, launch, "PR Recipe Review source"),
  ];
  if (phase === "plan") arguments_.push("--plan");
  else if (phase && typeof phase === "object" && !Array.isArray(phase)) {
    exactObject(phase, ["apply"], "PR Recipe Review apply phase");
    arguments_.push("--apply", text(phase.apply, "PR Recipe Review plan ID", 256, PLAN_ID));
  } else {
    throw new Error("PR Recipe Review phase is invalid");
  }
  arguments_.push("--json");
  return Object.freeze(arguments_);
}

function validatePlanIdentity(plan) {
  const identity = {};
  for (const field of PLAN_IDENTITY_FIELDS) identity[field] = plan[field];
  const expected = `workbench-pr-preparation-plan-v2:sha256:${crypto
    .createHash("sha256").update(canonicalJson(identity), "utf8").digest("hex")}`;
  if (plan.plan_id !== expected) throw new Error("PR Recipe Review plan identity changed");
}

function validatePrReviewPlan(value, request, options = {}) {
  const selected = boundedRequest(request);
  const expectedCoreSource = options.expectedCoreSource === undefined
    ? options.launch
      ? pathForCoreLaunch(selected.source, options.launch, "PR Recipe Review source")
      : selected.source
    : text(options.expectedCoreSource, "PR Recipe Review expected core source");
  const plan = exactObject(value, [
    "format", "schema_version", "operation_class", "plan_id",
    ...PLAN_IDENTITY_FIELDS, "effects",
  ], "PR Recipe Review plan");
  if (plan.format !== PLAN_FORMAT || plan.schema_version !== 2
      || plan.operation_class !== "review-provider-state-before-network-write") {
    throw new Error("PR Recipe Review plan format changed");
  }
  text(plan.plan_id, "PR Recipe Review plan ID", 256, PLAN_ID);
  for (const field of ["acquisition_profile_id", "provider_profile_id", "project_id", "channel_id"]) {
    text(plan[field], `PR Recipe Review plan ${field}`, 256, PROFILE_ID);
  }
  text(plan.acquisition_profile_digest, "PR Recipe Review acquisition profile digest", 256,
    /^workbench-project-acquisition-profile:sha256:[0-9a-f]{64}$/);
  text(plan.provider_profile_digest, "PR Recipe Review provider profile digest", 256,
    /^workbench-pull-request-provider-profile:sha256:[0-9a-f]{64}$/);
  if (plan.provider_kind !== "github" || plan.project_id !== PROFILE
      || plan.provider_observation_mode !== "github-api"
      || plan.delta_kind !== "provider-base-to-head") {
    throw new Error("PR Recipe Review plan authority changed");
  }
  if (pullRequestNumber(plan.pull_request) !== selected.pullRequest) {
    throw new Error("PR Recipe Review plan identifies another pull request");
  }
  text(plan.pull_request_url, "PR Recipe Review URL", 4096,
    /^https:\/\/github\.com\/SymmetricDevs\/Supersymmetry\/pull\/[1-9][0-9]*$/);
  if (!plan.pull_request_url.endsWith(`/pull/${selected.pullRequest}`)) {
    throw new Error("PR Recipe Review URL identifies another pull request");
  }
  member(plan.pull_request_state, ["open", "closed"], "PR Recipe Review PR state");
  bool(plan.pull_request_merged, "PR Recipe Review merged state");
  if (plan.pull_request_merged && plan.pull_request_state !== "closed") {
    throw new Error("PR Recipe Review merged state is inconsistent");
  }
  for (const field of ["base_repository", "head_repository"]) {
    text(plan[field], `PR Recipe Review ${field}`, 512, REPOSITORY_NAME);
  }
  for (const field of ["base_name", "head_name"]) {
    text(plan[field], `PR Recipe Review ${field}`, 1024);
  }
  for (const field of ["base_oid", "head_oid", "repository_head_before_prepare"]) {
    text(plan[field], `PR Recipe Review ${field}`, 64, OBJECT_ID);
  }
  nullableText(plan.provider_merge_oid, "PR Recipe Review provider merge object", 64, OBJECT_ID);
  for (const field of ["base_remote_ref", "head_remote_ref", "provider_merge_remote_ref"]) {
    text(plan[field], `PR Recipe Review ${field}`, 4096, FULL_REF);
  }
  for (const field of ["remote_url", "repository_root", "state_root", "git_executable"]) {
    text(plan[field], `PR Recipe Review ${field}`);
  }
  if (plan.repository_root !== expectedCoreSource) {
    throw new Error("PR Recipe Review plan repository differs from the requested checkout");
  }
  if (!Array.isArray(plan.effects) || JSON.stringify(plan.effects) !== JSON.stringify(PLAN_EFFECTS)) {
    throw new Error("PR Recipe Review plan effects changed");
  }
  validatePlanIdentity(plan);
  return plan;
}

function relativePath(value, label) {
  const selected = text(value, label, 4096);
  if (selected.startsWith("/") || selected.startsWith("\\") || selected.includes("\\")
      || /^[A-Za-z]:/.test(selected)
      || selected.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error(`${label} is not a safe repository-relative path`);
  }
  return selected;
}

function boundedPaths(value, label) {
  const selected = exactObject(value, ["paths", "path_count", "truncated"], label);
  if (!Array.isArray(selected.paths) || selected.paths.length > 2000) {
    throw new Error(`${label} paths exceed their boundary`);
  }
  selected.paths.forEach((path, index) => relativePath(path, `${label} path ${index}`));
  integer(selected.path_count, `${label} count`);
  bool(selected.truncated, `${label} truncation`);
  if (selected.path_count < selected.paths.length
      || (!selected.truncated && selected.path_count !== selected.paths.length)) {
    throw new Error(`${label} count is inconsistent`);
  }
  return selected;
}

function jsonBounded(value, label, depth = 0) {
  if (depth > 16) throw new Error(`${label} exceeds its nesting boundary`);
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (value.includes("\0") || Buffer.byteLength(value, "utf8") > 16 * 1024) {
      throw new Error(`${label} text exceeds its boundary`);
    }
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${label} number is invalid`);
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > 2000) throw new Error(`${label} list exceeds its boundary`);
    value.forEach((item, index) => jsonBounded(item, `${label}[${index}]`, depth + 1));
    return;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length > 128) throw new Error(`${label} object exceeds its boundary`);
    for (const key of keys) {
      text(key, `${label} key`, 1024);
      jsonBounded(value[key], `${label}.${key}`, depth + 1);
    }
    return;
  }
  throw new Error(`${label} is not JSON-compatible`);
}

function nullableInteger(value, label) {
  return value === null ? null : integer(value, label);
}

function sourceRow(value, label) {
  const selected = exactObject(value, ["path", "line", "column"], label);
  relativePath(selected.path, `${label} path`);
  nullableInteger(selected.line, `${label} line`);
  nullableInteger(selected.column, `${label} column`);
  return selected;
}

function lifecycleRow(value, label) {
  const selected = exactObject(
    value, ["stage", "execution_state", "runtime_invocation_count"], label,
  );
  text(selected.stage, `${label} stage`, 128);
  text(selected.execution_state, `${label} execution state`, 128);
  if (selected.runtime_invocation_count !== "unknown") {
    throw new Error(`${label} runtime invocation boundary changed`);
  }
  return selected;
}

function optionalBoolean(value, label) {
  if (value !== null) bool(value, label);
  return value;
}

function propertyValues(value, label) {
  if (value === null) return null;
  if (!Array.isArray(value) || value.length > 24) {
    throw new Error(`${label} values exceed their boundary`);
  }
  value.forEach((item, index) => displayText(item, `${label} value ${index}`, 1024));
  return value;
}

function propertiesObject(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).length > 32) {
    throw new Error(`${label} is invalid`);
  }
  for (const [key, values] of Object.entries(value)) {
    text(key, `${label} key`, 128);
    if (values === null) throw new Error(`${label}.${key} is unexpectedly absent`);
    propertyValues(values, `${label}.${key}`);
  }
  return value;
}

function modifiedRecipeRow(value, label) {
  const row = exactObject(value, [
    "recipe_map", "before_semantic_key", "after_semantic_key", "before_source",
    "after_source", "property_changes", "properties_truncated", "complete", "lifecycle",
    "reload_state", "pairing_basis",
  ], label);
  text(row.recipe_map, `${label} recipe map`, 1024);
  displayText(row.before_semantic_key, `${label} before semantic key`, 256);
  displayText(row.after_semantic_key, `${label} after semantic key`, 256);
  sourceRow(row.before_source, `${label} before source`);
  sourceRow(row.after_source, `${label} after source`);
  if (row.property_changes === null || typeof row.property_changes !== "object"
      || Array.isArray(row.property_changes) || Object.keys(row.property_changes).length > 32) {
    throw new Error(`${label} property changes are invalid`);
  }
  for (const [key, change] of Object.entries(row.property_changes)) {
    text(key, `${label} property-change key`, 128);
    const selected = exactObject(change, ["before", "after"], `${label} property change ${key}`);
    propertyValues(selected.before, `${label} property change ${key} before`);
    propertyValues(selected.after, `${label} property change ${key} after`);
  }
  bool(row.properties_truncated, `${label} properties truncation`);
  optionalBoolean(row.complete, `${label} completeness`);
  lifecycleRow(row.lifecycle, `${label} lifecycle`);
  text(row.reload_state, `${label} reload state`, 128);
  if (row.pairing_basis !== "unique-exact-non-property-structure") {
    throw new Error(`${label} pairing basis changed`);
  }
  return row;
}

function recipeRow(value, label) {
  const row = exactObject(value, [
    "semantic_key", "count", "recipe_map", "complete", "properties",
    "properties_truncated", "source", "lifecycle", "reload_state", "evidence_state",
  ], label);
  displayText(row.semantic_key, `${label} semantic key`, 256);
  if (integer(row.count, `${label} count`) < 1) throw new Error(`${label} count is invalid`);
  text(row.recipe_map, `${label} recipe map`, 1024);
  optionalBoolean(row.complete, `${label} completeness`);
  propertiesObject(row.properties, `${label} properties`);
  bool(row.properties_truncated, `${label} properties truncation`);
  sourceRow(row.source, `${label} source`);
  lifecycleRow(row.lifecycle, `${label} lifecycle`);
  text(row.reload_state, `${label} reload state`, 128);
  text(row.evidence_state, `${label} evidence state`, 128);
  return row;
}

function directRemovalRow(value, label) {
  const row = exactObject(value, [
    "semantic_key", "expression", "adapter_path", "method", "source_statement_count",
    "source", "stage", "execution_state", "loop_expansion", "runtime_invocation_count",
  ], label);
  displayText(row.semantic_key, `${label} semantic key`, 256);
  text(row.expression, `${label} expression`, 1024);
  text(row.adapter_path, `${label} adapter path`, 256);
  text(row.method, `${label} method`, 128);
  if (integer(row.source_statement_count, `${label} source statement count`) < 1) {
    throw new Error(`${label} source statement count is invalid`);
  }
  sourceRow(row.source, `${label} source`);
  text(row.stage, `${label} stage`, 128);
  text(row.execution_state, `${label} execution state`, 128);
  if (row.loop_expansion !== "not-performed" || row.runtime_invocation_count !== "unknown") {
    throw new Error(`${label} static/runtime boundary changed`);
  }
  return row;
}

function boundedRows(value, label, validateRow) {
  const selected = exactObject(value, ["rows", "row_count", "truncated"], label);
  if (!Array.isArray(selected.rows) || selected.rows.length > 200) {
    throw new Error(`${label} rows exceed their boundary`);
  }
  selected.rows.forEach((row, index) => validateRow(row, `${label} row ${index}`));
  integer(selected.row_count, `${label} count`);
  bool(selected.truncated, `${label} truncation`);
  if (selected.row_count < selected.rows.length
      || (!selected.truncated && selected.row_count !== selected.rows.length)) {
    throw new Error(`${label} count is inconsistent`);
  }
  return selected;
}

function validateSelection(selection, plan) {
  exactObject(selection, [
    "kind", "project_id", "pull_request", "pull_request_url", "pull_request_state",
    "pull_request_merged", "provider_kind", "provider_profile_id", "remote_url",
    "channel_id", "repository_root", "delta_kind", "base", "head", "provider_merge",
    "receipt_id", "prepared_at", "committed_scope", "attention_scope", "git_hygiene",
  ], "PR Recipe Review selection");
  if (selection.kind !== "prepared-provider-pull-request"
      || selection.project_id !== plan.project_id
      || selection.pull_request !== plan.pull_request
      || selection.pull_request_url !== plan.pull_request_url
      || selection.pull_request_state !== plan.pull_request_state
      || selection.pull_request_merged !== plan.pull_request_merged
      || selection.provider_kind !== plan.provider_kind
      || selection.provider_profile_id !== plan.provider_profile_id
      || selection.remote_url !== plan.remote_url
      || selection.channel_id !== plan.channel_id
      || selection.repository_root !== plan.repository_root
      || selection.delta_kind !== plan.delta_kind) {
    throw new Error("PR Recipe Review result differs from the consented plan");
  }
  const base = exactObject(selection.base,
    ["repository", "name", "remote_ref", "immutable_ref", "oid"],
    "PR Recipe Review base");
  const head = exactObject(selection.head,
    ["repository", "name", "remote_ref", "immutable_ref", "oid"],
    "PR Recipe Review head");
  const merge = exactObject(selection.provider_merge,
    ["remote_ref", "immutable_ref", "oid"], "PR Recipe Review provider merge");
  if (base.repository !== plan.base_repository || base.name !== plan.base_name
      || base.remote_ref !== plan.base_remote_ref || base.oid !== plan.base_oid
      || head.repository !== plan.head_repository || head.name !== plan.head_name
      || head.remote_ref !== plan.head_remote_ref || head.oid !== plan.head_oid
      || merge.remote_ref !== plan.provider_merge_remote_ref || merge.oid !== plan.provider_merge_oid) {
    throw new Error("PR Recipe Review result Git identity differs from the consented plan");
  }
  for (const [label, side] of [["base", base], ["head", head]]) {
    text(side.repository, `PR Recipe Review ${label} repository`, 512, REPOSITORY_NAME);
    text(side.name, `PR Recipe Review ${label} name`, 1024);
    text(side.remote_ref, `PR Recipe Review ${label} remote ref`, 4096, FULL_REF);
    text(side.immutable_ref, `PR Recipe Review ${label} immutable ref`, 4096, FULL_REF);
    text(side.oid, `PR Recipe Review ${label} object`, 64, OBJECT_ID);
  }
  text(merge.remote_ref, "PR Recipe Review merge remote ref", 4096, FULL_REF);
  nullableText(merge.immutable_ref, "PR Recipe Review merge immutable ref", 4096, FULL_REF);
  nullableText(merge.oid, "PR Recipe Review merge object", 64, OBJECT_ID);
  text(selection.receipt_id, "PR Recipe Review receipt ID", 256,
    /^workbench-pr-preparation-v2:sha256:[0-9a-f]{64}$/);
  text(selection.prepared_at, "PR Recipe Review preparation time", 128);
  const scope = exactObject(selection.committed_scope,
    ["repository", "selected", "excluded"], "PR Recipe Review committed scope");
  boundedPaths(scope.repository, "PR Recipe Review repository scope");
  boundedPaths(scope.selected, "PR Recipe Review selected scope");
  boundedPaths(scope.excluded, "PR Recipe Review excluded scope");
  const attentionScope = exactObject(selection.attention_scope, [
    "introduced_static_signals", "preexisting_static_signals", "candidate_static_signal_total",
    "supplied_runtime_attention", "pr_strict", "strict_all",
  ], "PR Recipe Review attention scope");
  integer(attentionScope.introduced_static_signals,
    "PR Recipe Review introduced static-signal count");
  integer(attentionScope.preexisting_static_signals,
    "PR Recipe Review pre-existing static-signal count");
  integer(attentionScope.candidate_static_signal_total,
    "PR Recipe Review candidate static-signal count");
  bool(attentionScope.supplied_runtime_attention,
    "PR Recipe Review supplied-runtime attention");
  if (attentionScope.pr_strict
      !== "introduced static signals and supplied runtime attention"
      || attentionScope.strict_all
      !== "candidate-wide static signals, supplied runtime attention, source configuration warnings, and Git hygiene attention") {
    throw new Error("PR Recipe Review attention strictness boundary changed");
  }
  if (attentionScope.introduced_static_signals + attentionScope.preexisting_static_signals
      !== attentionScope.candidate_static_signal_total) {
    throw new Error("PR Recipe Review attention-scope counts are inconsistent");
  }
  const hygiene = exactObject(
    selection.git_hygiene, ["state", "detail"], "PR Recipe Review Git hygiene",
  );
  member(hygiene.state, ["clean", "attention", "unavailable"],
    "PR Recipe Review Git hygiene state");
  text(hygiene.detail, "PR Recipe Review Git hygiene detail", 4096);
}

function verifyReportIdentity(report) {
  const body = {};
  for (const [key, value] of Object.entries(report)) if (key !== "report_id") body[key] = value;
  const expected = `workbench-recipe-review:sha256:${crypto
    .createHash("sha256").update(canonicalJson(body), "utf8").digest("hex")}`;
  if (report.report_id !== expected) throw new Error("PR Recipe Review report identity changed");
}

function validatePrReviewReport(value, plan) {
  const report = exactObject(value, [
    "format", "schema_version", "report_id", "operation_class", "authority",
    "source_report", "profile", "selection", "summary", "files", "machine_recipes",
    "direct_removal_calls", "attention", "review_guidance", "evidence", "limitations",
    "next_actions",
  ], "PR Recipe Review report");
  if (report.format !== REPORT_FORMAT || report.schema_version !== 2
      || report.operation_class !== "read-only") {
    throw new Error("PR Recipe Review report format changed");
  }
  text(report.report_id, "PR Recipe Review report ID", 256, REPORT_ID);
  const authority = exactObject(report.authority,
    ["analysis_owner", "git_identity_owner", "construction_authority"],
    "PR Recipe Review authority");
  if (authority.analysis_owner !== "Pack Program Studio"
      || authority.git_identity_owner !== "Project Intelligence"
      || authority.construction_authority !== "none") {
    throw new Error("PR Recipe Review authority changed");
  }
  const source = exactObject(report.source_report,
    ["format", "schema_version", "report_id", "availability"],
    "PR Recipe Review source report");
  if (source.format !== "workbench-groovy-pack-program-report-v1"
      || source.schema_version !== 1 || source.availability !== "rerun with --full-json-v1") {
    throw new Error("PR Recipe Review source-report boundary changed");
  }
  text(source.report_id, "PR Recipe Review source report ID", 256);
  const profile = exactObject(report.profile, [
    "profile_id", "profile_sha256", "pack_profile_id", "platform_profile_id",
    "platform_profile_sha256",
  ], "PR Recipe Review profile");
  jsonBounded(profile, "PR Recipe Review profile");
  if (profile.pack_profile_id !== PACK_PROFILE_ID) {
    throw new Error(`PR Recipe Review pack_profile_id must be ${PACK_PROFILE_ID}`);
  }
  validateSelection(report.selection, plan);
  const summary = exactObject(report.summary, [
    "status", "strict_exit_code", "analysis_state", "comparison_state", "runtime_state",
    "changed_source_files", "machine_recipes", "direct_removal_source_statements",
  ], "PR Recipe Review summary");
  const status = member(summary.status, ["ready", "attention", "blocked"],
    "PR Recipe Review decision status");
  const expectedExit = status === "blocked" ? 2 : status === "attention" ? 1 : 0;
  if (summary.strict_exit_code !== expectedExit) {
    throw new Error("PR Recipe Review strict decision is inconsistent");
  }
  for (const field of ["analysis_state", "comparison_state", "runtime_state"]) {
    text(summary[field], `PR Recipe Review summary ${field}`, 1024);
  }
  integer(summary.changed_source_files, "PR Recipe Review changed-file count");
  const counts = exactObject(summary.machine_recipes,
    ["modified", "added", "removed"], "PR Recipe Review recipe counts");
  for (const field of ["modified", "added", "removed"]) {
    integer(counts[field], `PR Recipe Review ${field} recipe count`);
  }
  const removals = exactObject(summary.direct_removal_source_statements,
    ["added", "removed", "counts_incomplete", "runtime_invocation_counts"],
    "PR Recipe Review removal counts");
  integer(removals.added, "PR Recipe Review added removal count");
  integer(removals.removed, "PR Recipe Review removed removal count");
  bool(removals.counts_incomplete, "PR Recipe Review removal truncation");
  if (removals.runtime_invocation_counts !== "unknown") {
    throw new Error("PR Recipe Review removal runtime boundary changed");
  }
  const files = exactObject(report.files, ["added", "modified", "removed"],
    "PR Recipe Review files");
  for (const field of ["added", "modified", "removed"]) {
    boundedPaths(files[field], `PR Recipe Review ${field} files`);
  }
  const recipes = exactObject(report.machine_recipes,
    ["modified", "added", "removed", "pairing_boundary"], "PR Recipe Review recipes");
  for (const field of ["modified", "added", "removed"]) {
    boundedRows(
      recipes[field], `PR Recipe Review ${field} recipes`,
      field === "modified" ? modifiedRecipeRow : recipeRow,
    );
  }
  text(recipes.pairing_boundary, "PR Recipe Review pairing boundary", 4096);
  const direct = exactObject(report.direct_removal_calls,
    ["added", "removed", "boundary"], "PR Recipe Review direct removals");
  boundedRows(direct.added, "PR Recipe Review added direct removals", directRemovalRow);
  boundedRows(direct.removed, "PR Recipe Review removed direct removals", directRemovalRow);
  text(direct.boundary, "PR Recipe Review direct-removal boundary", 4096);
  const attention = exactObject(report.attention, [
    "strict_reasons", "strict_reasons_truncated", "configuration_warnings",
    "configuration_warnings_truncated", "configuration_warnings_drive_strict",
  ], "PR Recipe Review attention");
  for (const field of ["strict_reasons", "configuration_warnings"]) {
    if (!Array.isArray(attention[field]) || attention[field].length > 200) {
      throw new Error(`PR Recipe Review ${field} exceeds its boundary`);
    }
    attention[field].forEach((row, index) => text(row, `PR Recipe Review ${field} ${index}`, 1024));
  }
  bool(attention.strict_reasons_truncated, "PR Recipe Review attention truncation");
  bool(attention.configuration_warnings_truncated, "PR Recipe Review warning truncation");
  if (attention.configuration_warnings_drive_strict !== false) {
    throw new Error("PR Recipe Review warning strictness changed");
  }
  exactObject(report.review_guidance,
    ["change_state", "recommendation", "save_risk_count"], "PR Recipe Review guidance");
  exactObject(report.evidence, [
    "candidate_program_id", "baseline_program_id", "candidate_git", "runtime_state",
    "static_effect_rows_truncated",
  ], "PR Recipe Review evidence");
  jsonBounded(report.review_guidance, "PR Recipe Review guidance");
  jsonBounded(report.evidence, "PR Recipe Review evidence");
  if (!Array.isArray(report.limitations) || report.limitations.length > 53) {
    throw new Error("PR Recipe Review limitations exceed their boundary");
  }
  report.limitations.forEach((row, index) => text(row, `PR Recipe Review limitation ${index}`, 1024));
  if (!Array.isArray(report.next_actions) || report.next_actions.length > 16) {
    throw new Error("PR Recipe Review next actions exceed their boundary");
  }
  report.next_actions.forEach((row, index) => {
    exactObject(row, ["id", "description", "command_hint"], `PR Recipe Review next action ${index}`);
    text(row.id, `PR Recipe Review next action ${index} ID`, 256, /^[a-z][a-z0-9-]*$/);
    text(row.description, `PR Recipe Review next action ${index} description`, 4096);
    text(row.command_hint, `PR Recipe Review next action ${index} command`, 4096);
  });
  jsonBounded(report, "PR Recipe Review report");
  verifyReportIdentity(report);
  return report;
}

async function planPullRequestReview(executable, request, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const result = await invokeCoreJson(executable, reviewPrArguments(request, "plan", launch), {
    ...options,
    launch,
    label: "PR Recipe Review plan",
    maximumOutput: MAX_PLAN_BYTES,
    timeoutMs: options.timeoutMs === undefined ? REVIEW_TIMEOUT_MS : options.timeoutMs,
  });
  return validatePrReviewPlan(result, request, { launch });
}

async function applyPullRequestReview(executable, request, plan, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, options);
  validatePrReviewPlan(plan, request, { launch });
  const result = await invokeCoreJson(
    executable,
    reviewPrArguments(request, { apply: plan.plan_id }, launch),
    {
      ...options,
      launch,
      label: "PR Recipe Review result",
      maximumOutput: MAX_REPORT_BYTES,
      timeoutMs: options.timeoutMs === undefined ? REVIEW_TIMEOUT_MS : options.timeoutMs,
    },
  );
  return validatePrReviewReport(result, plan);
}

module.exports = {
  MAX_PULL_REQUEST,
  PLAN_EFFECTS,
  applyPullRequestReview,
  canonicalJson,
  planPullRequestReview,
  pullRequestNumber,
  reviewPrArguments,
  validatePrReviewPlan,
  validatePrReviewReport,
};
