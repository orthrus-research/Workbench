"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");

const PROFILE_SELECTOR = "supersymmetry";
const PROJECT_ID = "supersymmetry";
const PACK_PROFILE_ID = "workbench-pack:supersymmetry";
const PLAN_FORMAT = "workbench-project-qualification-plan-v1";
const RESULT_FORMAT = "workbench-project-qualification-result-v1";
const MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
const QUALIFICATION_TIMEOUT_MS = 2 * 60 * 1000;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const GIT_OBJECT = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/;
const PLAN_ID = /^workbench-project-qualification-plan:sha256:[0-9a-f]{64}$/;
const INSPECTION_ID = /^workbench-project-inspection:sha256:[0-9a-f]{64}$/;
const BINDING_ID = /^workbench-project-qualification-binding:sha256:[0-9a-f]{64}$/;
const STATE_REVISION = /^workbench-project-qualification-state:sha256:[0-9a-f]{64}$/;
const BINDING_DIRECTORY = "project-qualification-v1";
const CANONICAL_ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const PROFILE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]*$/;
const STALE_REASON = /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$/;
const APPLICABLE_STATES = Object.freeze(["ready", "attention"]);
const QUALIFICATION_STATES = Object.freeze(["ready", "attention", "incompatible"]);
const BINDING_STATES = Object.freeze(["absent", "current", "stale"]);
const ACTION_BY_BINDING_STATE = Object.freeze({
  absent: "atomic-private-record-create",
  current: "reuse-current-binding",
  stale: "atomic-private-record-replace",
});
const OUTCOME_BY_BINDING_STATE = Object.freeze({
  absent: "qualified",
  current: "reused",
  stale: "requalified",
});

function exactObject(value, required, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be one ordinary object`);
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

function boolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
  return value;
}

function member(value, choices, label) {
  if (!choices.includes(value)) throw new Error(`${label} is unsupported`);
  return value;
}

function boundedStrings(value, label, maximum = 256, pattern) {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new Error(`${label} exceeds its list boundary`);
  }
  value.forEach((item, index) => text(item, `${label}[${index}]`, 4096, pattern));
  return value;
}

function absolutePath(value, label) {
  const selected = text(value, label);
  if (!selected.startsWith("/")
      && !/^[A-Za-z]:[\\/]/.test(selected)
      && !/^\\\\[^\\]+\\[^\\]+/.test(selected)) {
    throw new Error(`${label} must be absolute`);
  }
  return selected;
}

function fileUri(value, label) {
  const selected = text(value, label);
  let parsed;
  try {
    parsed = new URL(selected);
  } catch (error) {
    throw new Error(`${label} is invalid`, { cause: error });
  }
  if (parsed.protocol !== "file:") throw new Error(`${label} must use the file scheme`);
  return selected;
}

function canonicalQualificationWorkspace(workspace, resolver = fs.realpathSync.native) {
  const selected = text(workspace, "project qualification workspace");
  if (typeof resolver !== "function") {
    throw new Error("project qualification workspace realpath resolver must be callable");
  }
  let canonical;
  try {
    canonical = resolver(selected);
  } catch (error) {
    throw new Error(
      "project qualification workspace could not be resolved to its filesystem identity",
      { cause: error },
    );
  }
  return absolutePath(canonical, "canonical project qualification workspace");
}

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
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("qualification identity contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const selected = exactObject(value, Object.keys(value), "qualification identity material");
  return `{${Object.keys(selected).sort(unicodeScalarCompare).map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(selected[key])}`,
  ).join(",")}}`;
}

function freeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

function validateProfile(value, label) {
  const profile = exactObject(value, [
    "selector", "pack_profile_id", "pack_variant", "platform_profile_id",
    "selection_digest",
  ], label);
  if (profile.selector !== PROFILE_SELECTOR || profile.pack_profile_id !== PACK_PROFILE_ID) {
    throw new Error(`${label} selects another pack profile`);
  }
  text(profile.pack_variant, `${label} pack variant`, 256, PROFILE_ID);
  text(profile.platform_profile_id, `${label} platform profile ID`, 256, PROFILE_ID);
  text(profile.selection_digest, `${label} selection digest`, 128, DIGEST);
  return profile;
}

function validateWorkspace(value, label, expectedRoot) {
  const workspace = exactObject(value, [
    "root", "root_uri", "revision", "dirty", "dirty_entries", "dirty_fingerprint",
  ], label);
  absolutePath(workspace.root, `${label} root`);
  if (expectedRoot !== undefined && workspace.root !== expectedRoot) {
    throw new Error(`${label} root differs from the requested local workspace`);
  }
  fileUri(workspace.root_uri, `${label} root URI`);
  text(workspace.revision, `${label} revision`, 64, GIT_OBJECT);
  boolean(workspace.dirty, `${label} dirty state`);
  boundedStrings(workspace.dirty_entries, `${label} dirty entries`, 4096);
  text(workspace.dirty_fingerprint, `${label} dirty fingerprint`, 128, DIGEST);
  if (workspace.dirty !== (workspace.dirty_entries.length > 0)) {
    throw new Error(`${label} dirty state and entries are inconsistent`);
  }
  return workspace;
}

function validateChecks(value, state, label) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 64) {
    throw new Error(`${label} exceeds its list boundary`);
  }
  const ids = new Set();
  let attention = false;
  value.forEach((item, index) => {
    const check = exactObject(item, ["id", "label", "state", "detail"], `${label}[${index}]`);
    text(check.id, `${label}[${index}] ID`, 128, CANONICAL_ID);
    if (ids.has(check.id)) throw new Error(`${label} contains duplicate check IDs`);
    ids.add(check.id);
    text(check.label, `${label}[${index}] label`, 1024);
    member(check.state, QUALIFICATION_STATES, `${label}[${index}] state`);
    text(check.detail, `${label}[${index}] detail`, 4096);
    attention ||= check.state === "attention";
  });
  for (const required of ["profile-conformance", "packwiz-index-integrity"]) {
    if (!ids.has(required)) throw new Error(`${label} omits required check ${required}`);
  }
  const incompatible = value.some((check) => check.state === "incompatible");
  const expectedState = incompatible ? "incompatible" : attention ? "attention" : "ready";
  if (state !== expectedState
      || (state === "incompatible" && value.some((check) => check.state !== "incompatible"))) {
    throw new Error(`${label} and qualification state are inconsistent`);
  }
  return value;
}

function validateBinding(value, label, result = false) {
  const binding = exactObject(value, [
    "state", "binding_id", "path", "state_revision", "stale_reasons",
  ], label);
  const state = member(binding.state, BINDING_STATES, `${label} state`);
  if (result && state !== "current") throw new Error(`${label} must be current after apply`);
  text(binding.binding_id, `${label} ID`, 256, BINDING_ID);
  absolutePath(binding.path, `${label} path`);
  if (state === "absent") {
    if (binding.state_revision !== null) throw new Error(`${label} absent revision must be null`);
  } else {
    text(binding.state_revision, `${label} state revision`, 256, STATE_REVISION);
  }
  boundedStrings(binding.stale_reasons, `${label} stale reasons`, 64, STALE_REASON);
  if ((state === "stale") !== (binding.stale_reasons.length > 0)) {
    throw new Error(`${label} stale reasons are inconsistent`);
  }
  return binding;
}

function validateAction(value, binding, label) {
  const action = exactObject(value, ["id", "operation", "destination", "effect"], label);
  if (action.id !== "persist-project-qualification"
      || action.operation !== ACTION_BY_BINDING_STATE[binding.state]) {
    throw new Error(`${label} does not match the current binding state`);
  }
  absolutePath(action.destination, `${label} destination`);
  if (action.destination !== binding.path) {
    throw new Error(`${label} destination differs from the qualification binding`);
  }
  text(action.effect, `${label} effect`, 4096);
  return action;
}

function planIdentityMaterial(plan) {
  return {
    project_id: plan.project_id,
    profile: plan.profile,
    workspace_root: plan.workspace.root,
    inspection_id: plan.inspection_id,
    qualification_state: plan.state,
    binding: {
      state: plan.binding.state,
      binding_id: plan.binding.binding_id,
      path: plan.binding.path,
      state_revision: plan.binding.state_revision,
    },
    action: plan.actions.length ? plan.actions[0] : null,
  };
}

function projectQualificationPlanId(plan) {
  const digest = crypto.createHash("sha256")
    .update(canonicalJson(planIdentityMaterial(plan)), "utf8").digest("hex");
  return `workbench-project-qualification-plan:sha256:${digest}`;
}

function qualificationArguments(workspace, phase, launch, options = {}) {
  const selectedWorkspace = text(workspace, "project qualification workspace");
  const mappedWorkspace = pathForCoreLaunch(
    selectedWorkspace, launch, "project qualification workspace",
  );
  const arguments_ = [
    "project", "qualify", mappedWorkspace, "--profile", PROFILE_SELECTOR,
  ];
  if (options.stateRoot !== undefined) {
    arguments_.push(
      "--state-root",
      pathForCoreLaunch(
        text(options.stateRoot, "project qualification state root"),
        launch,
        "project qualification state root",
      ),
    );
  }
  if (phase === "plan") {
    arguments_.push("--plan");
  } else if (phase !== null && typeof phase === "object" && !Array.isArray(phase)) {
    exactObject(phase, ["apply"], "project qualification apply phase");
    arguments_.push("--apply", text(
      phase.apply, "project qualification plan ID", 256, PLAN_ID,
    ));
  } else {
    throw new Error("project qualification phase is invalid");
  }
  arguments_.push("--json");
  return Object.freeze(arguments_);
}

function validateProjectQualificationPlan(value, workspace, options = {}) {
  const selectedWorkspace = text(workspace, "project qualification workspace");
  const expectedRoot = options.expectedCoreWorkspace === undefined
    ? options.launch
      ? pathForCoreLaunch(selectedWorkspace, options.launch, "project qualification workspace")
      : selectedWorkspace
    : text(options.expectedCoreWorkspace, "expected project qualification workspace");
  const plan = exactObject(value, [
    "format", "schema_version", "operation_class", "plan_id", "state", "can_apply", "project_id",
    "profile", "workspace", "inspection_id", "checks", "limitations", "binding",
    "actions", "consent",
  ], "project qualification plan");
  if (plan.format !== PLAN_FORMAT || plan.schema_version !== 1
      || plan.operation_class !== "review-before-mutation") {
    throw new Error("project qualification plan format changed");
  }
  text(plan.plan_id, "project qualification plan ID", 256, PLAN_ID);
  const state = member(
    plan.state, QUALIFICATION_STATES, "project qualification plan state",
  );
  boolean(plan.can_apply, "project qualification plan applicability");
  if (plan.can_apply !== APPLICABLE_STATES.includes(state)) {
    throw new Error("project qualification plan applicability is inconsistent");
  }
  if (plan.project_id !== PROJECT_ID) {
    throw new Error("project qualification plan identifies another project family");
  }
  validateProfile(plan.profile, "project qualification plan profile");
  validateWorkspace(plan.workspace, "project qualification plan workspace", expectedRoot);
  text(plan.inspection_id, "project qualification inspection ID", 256, INSPECTION_ID);
  validateChecks(plan.checks, state, "project qualification plan checks");
  boundedStrings(plan.limitations, "project qualification plan limitations", 64);
  const binding = validateBinding(plan.binding, "project qualification plan binding");
  if (!Array.isArray(plan.actions)
      || (plan.can_apply ? plan.actions.length !== 1 : plan.actions.length !== 0)) {
    throw new Error("project qualification plan action boundary changed");
  }
  if (plan.can_apply) {
    validateAction(plan.actions[0], binding, "project qualification plan action");
  }
  const consent = exactObject(plan.consent, [
    "required", "prompt", "non_interactive",
  ], "project qualification plan consent");
  const applicableConsent = consent.required === true
    && consent.prompt === "Apply this qualification? [y/N]"
    && consent.non_interactive === "Pass this exact plan_id with --apply.";
  const incompatibleConsent = consent.required === false
    && consent.prompt === null && consent.non_interactive === null;
  if ((plan.can_apply && !applicableConsent) || (!plan.can_apply && !incompatibleConsent)) {
    throw new Error("project qualification consent boundary changed");
  }
  if (plan.plan_id !== projectQualificationPlanId(plan)) {
    throw new Error("project qualification plan identity changed");
  }
  return freeze(plan);
}

function sameJson(left, right) {
  return canonicalJson(left) === canonicalJson(right);
}

function bindingStateRoot(binding) {
  const selected = absolutePath(binding.path, "project qualification binding path");
  const pathApi = /^[A-Za-z]:[\\/]/.test(selected) || selected.startsWith("\\\\")
    ? path.win32
    : path.posix;
  const bindingDirectory = pathApi.dirname(selected);
  const qualificationDirectory = pathApi.dirname(bindingDirectory);
  const digest = binding.binding_id.slice(binding.binding_id.lastIndexOf(":") + 1);
  if (pathApi.basename(bindingDirectory) !== "bindings"
      || pathApi.basename(qualificationDirectory) !== BINDING_DIRECTORY
      || pathApi.basename(selected) !== `${digest}.json`) {
    throw new Error("project qualification binding path changed its private-store layout");
  }
  return absolutePath(
    pathApi.dirname(qualificationDirectory), "project qualification effective state root",
  );
}

function validateNextCommands(value, workspace, binding) {
  if (!Array.isArray(value) || value.length !== 1 || !Array.isArray(value[0])) {
    throw new Error("project qualification result must return one exact status command");
  }
  const effectiveStateRoot = bindingStateRoot(binding);
  const expected = [
    "workbench", "project", "qualify", workspace,
    "--profile", PROFILE_SELECTOR, "--status", "--state-root", effectiveStateRoot,
  ];
  value[0].forEach((argument, index) => text(
    argument, `project qualification next command argument ${index}`, 4096,
  ));
  if (value[0].length !== expected.length
      || value[0].some((argument, index) => argument !== expected[index])) {
    throw new Error(
      "project qualification result did not preserve the exact workspace and effective state root in its status command",
    );
  }
  return value;
}

function validateProjectQualificationResult(value, plan) {
  const result = exactObject(value, [
    "format", "schema_version", "outcome", "applied_plan_id", "binding",
    "qualification", "next_commands",
  ], "project qualification result");
  if (result.format !== RESULT_FORMAT || result.schema_version !== 1) {
    throw new Error("project qualification result format changed");
  }
  const outcome = member(
    result.outcome, ["qualified", "requalified", "reused"],
    "project qualification result outcome",
  );
  text(result.applied_plan_id, "project qualification applied plan ID", 256, PLAN_ID);
  if (result.applied_plan_id !== plan.plan_id) {
    throw new Error("project qualification result differs from the exact applied plan ID");
  }
  if (outcome !== OUTCOME_BY_BINDING_STATE[plan.binding.state]) {
    throw new Error("project qualification result outcome differs from the planned binding state");
  }
  const binding = validateBinding(result.binding, "project qualification result binding", true);
  if (binding.binding_id !== plan.binding.binding_id || binding.path !== plan.binding.path) {
    throw new Error("project qualification result binding differs from the exact applied plan");
  }
  const qualification = exactObject(result.qualification, [
    "qualified", "project_id", "profile", "workspace", "inspection_id", "state", "checks",
    "limitations",
  ], "project qualification result qualification");
  if (qualification.qualified !== true || qualification.project_id !== plan.project_id
      || qualification.inspection_id !== plan.inspection_id
      || qualification.state !== plan.state) {
    throw new Error("project qualification result identity differs from the exact applied plan");
  }
  validateProfile(qualification.profile, "project qualification result profile");
  validateWorkspace(
    qualification.workspace, "project qualification result workspace", plan.workspace.root,
  );
  validateChecks(
    qualification.checks, qualification.state, "project qualification result checks",
  );
  boundedStrings(qualification.limitations, "project qualification result limitations", 64);
  if (!sameJson(qualification.profile, plan.profile)
      || !sameJson(qualification.checks, plan.checks)
      || !sameJson(qualification.limitations, plan.limitations)) {
    throw new Error("project qualification result evidence differs from the exact applied plan");
  }
  validateNextCommands(result.next_commands, plan.workspace.root, binding);
  return freeze(result);
}

function qualificationPlanSummary(plan) {
  const dirty = plan.workspace.dirty
    ? `dirty (${plan.workspace.dirty_entries.length} entr${plan.workspace.dirty_entries.length === 1 ? "y" : "ies"})`
    : "clean";
  const lines = [
    `Qualification state: ${plan.state.toUpperCase()}`,
    `Project family: ${plan.project_id}`,
    `Selected profile: ${plan.profile.selector} (${plan.profile.pack_profile_id})`,
    `Workspace: ${plan.workspace.root}`,
    `Git revision: ${plan.workspace.revision}`,
    `Working tree: ${dirty}`,
    `Inspection: ${plan.inspection_id}`,
    "",
    "Checks:",
    ...plan.checks.map((check) => `- [${check.state.toUpperCase()}] ${check.label}: ${check.detail}`),
    "",
    `Existing binding: ${plan.binding.state}`,
  ];
  if (plan.binding.stale_reasons.length) {
    lines.push(`Stale reasons: ${plan.binding.stale_reasons.join(", ")}`);
  }
  if (plan.can_apply) {
    lines.push(
      "",
      "Planned local record:",
      `- ${plan.actions[0].effect}`,
      `- Destination: ${plan.actions[0].destination}`,
    );
  } else {
    lines.push(
      "",
      "Planned local record: none. This workspace is incompatible and cannot be qualified.",
    );
  }
  if (plan.limitations.length) {
    lines.push("", "Limitations:", ...plan.limitations.map((item) => `- ${item}`));
  }
  lines.push(
    "",
    "When applicable and explicitly applied, this optional project-family qualification records the selected Workbench profile and inspection binding. It does not approve source changes, recipe findings, construction, Packwiz/runtime payload integrity, or release.",
    plan.can_apply
      ? `Only the exact plan ID shown here can be applied: ${plan.plan_id}`
      : "No plan ID can authorize apply while the qualification state is incompatible.",
  );
  return lines.join("\n");
}

async function planProjectQualification(executable, workspace, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const canonicalWorkspace = canonicalQualificationWorkspace(
    workspace, options.workspaceRealpath,
  );
  const expectedCoreWorkspace = pathForCoreLaunch(
    canonicalWorkspace, launch, "canonical project qualification workspace",
  );
  const response = await invokeCoreJson(
    executable, qualificationArguments(canonicalWorkspace, "plan", launch, options), {
      ...options,
      launch,
      label: "project qualification plan",
      maximumOutput: MAX_OUTPUT_BYTES,
      timeoutMs: options.timeoutMs === undefined ? QUALIFICATION_TIMEOUT_MS : options.timeoutMs,
    },
  );
  return validateProjectQualificationPlan(response, canonicalWorkspace, {
    expectedCoreWorkspace,
  });
}

async function applyProjectQualification(executable, workspace, plan, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const canonicalWorkspace = canonicalQualificationWorkspace(
    workspace, options.workspaceRealpath,
  );
  const expectedCoreWorkspace = pathForCoreLaunch(
    canonicalWorkspace, launch, "canonical project qualification workspace",
  );
  const validatedPlan = validateProjectQualificationPlan(plan, canonicalWorkspace, {
    expectedCoreWorkspace,
  });
  if (!validatedPlan.can_apply) {
    throw new Error("incompatible project qualification plans cannot be applied");
  }
  const response = await invokeCoreJson(
    executable, qualificationArguments(
      canonicalWorkspace, { apply: validatedPlan.plan_id }, launch, options,
    ), {
      ...options,
      launch,
      label: "project qualification result",
      maximumOutput: MAX_OUTPUT_BYTES,
      timeoutMs: options.timeoutMs === undefined ? QUALIFICATION_TIMEOUT_MS : options.timeoutMs,
    },
  );
  return validateProjectQualificationResult(response, validatedPlan);
}

module.exports = {
  MAX_OUTPUT_BYTES,
  PACK_PROFILE_ID,
  PLAN_FORMAT,
  PROFILE_SELECTOR,
  RESULT_FORMAT,
  applyProjectQualification,
  canonicalQualificationWorkspace,
  canonicalJson,
  planProjectQualification,
  projectQualificationPlanId,
  qualificationArguments,
  qualificationPlanSummary,
  validateProjectQualificationPlan,
  validateProjectQualificationResult,
};
