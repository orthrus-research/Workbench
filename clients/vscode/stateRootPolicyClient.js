"use strict";

const path = require("node:path");
const { createHash } = require("node:crypto");
const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");

const POLICY_ID = /^workbench-state-root-policy:sha256:[0-9a-f]{64}$/;
const RECORD_ID = /^workbench-state-root-selections:sha256:[0-9a-f]{64}$/;
const WORKSPACE_ID = /^workbench-workspace-v1:[0-9a-f]{32}$/;
const SOURCES = new Set(["user-selection", "environment", "platform-default"]);
const ROLES = new Set(["product-spine", "feature"]);

function exactFields(value, fields, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).length !== fields.size
      || Object.keys(value).some((key) => !fields.has(key))) {
    throw new Error(`${label} has unsupported fields`);
  }
}

function bounded(value, label) {
  if (typeof value !== "string" || !value || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 32 * 1024) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function hostStateRoot(corePath, launch) {
  const selected = bounded(corePath, "Core-selected state root");
  if (launch.host !== "windows-wsl") return selected;
  if (!path.posix.isAbsolute(selected) || selected.startsWith("//")
      || selected.endsWith("/")
      || selected.split("/").slice(1).some((part) => !part || part === "." || part === "..")) {
    throw new Error("Core-selected WSL state root is not one normalized absolute path");
  }
  const hostPath = `\\\\wsl.localhost\\${launch.distribution}${selected.replaceAll("/", "\\")}`;
  if (pathForCoreLaunch(hostPath, launch, "Core-selected WSL state root") !== selected) {
    throw new Error("Core-selected WSL state root changed during host mapping");
  }
  return hostPath;
}

function legacyStateRootDecision(policy, configured, setting) {
  if (typeof configured !== "string" || configured.includes("\0")
      || Buffer.byteLength(configured, "utf8") > 32 * 1024) {
    throw new Error(`${setting} is invalid`);
  }
  if (!configured.trim()) return "ready";
  return policy.source === "platform-default" ? "migration-required" : "historical-hint";
}

function legacyProductSpineDecision(policy, configured) {
  return legacyStateRootDecision(policy, configured, "workbench.productSpine.stateRoot");
}

function legacyFeatureDecision(policy, configured) {
  return legacyStateRootDecision(policy, configured, "workbench.feature.stateRoot");
}

function validateStateRootPolicy(value, expectedWorkspace, role, launch) {
  const fields = new Set([
    "format", "schema_version", "configuration_home", "workspace", "workspace_id",
    "role", "state_root", "source", "selections_record_id", "policy_id",
  ]);
  exactFields(value, fields, "Workbench state-root policy");
  if (value.format !== "workbench-state-root-policy-v1" || value.schema_version !== 1
      || value.role !== role || !ROLES.has(role)
      || value.workspace !== expectedWorkspace
      || (value.workspace_id !== null && !WORKSPACE_ID.test(value.workspace_id))
      || !SOURCES.has(value.source)
      || !RECORD_ID.test(value.selections_record_id)
      || !POLICY_ID.test(value.policy_id)) {
    throw new Error("Workbench state-root policy identity or scope is invalid");
  }
  const body = Object.fromEntries(
    Object.keys(value).filter((key) => key !== "policy_id").sort().map((key) => [key, value[key]]),
  );
  const expectedId = `workbench-state-root-policy:sha256:${createHash("sha256")
    .update(JSON.stringify(body), "utf8").digest("hex")}`;
  if (value.policy_id !== expectedId) {
    throw new Error("Workbench state-root policy digest does not match its fields");
  }
  bounded(value.configuration_home, "Workbench configuration home");
  const stateRoot = hostStateRoot(value.state_root, launch);
  return Object.freeze({
    stateRoot,
    policyId: value.policy_id,
    source: value.source,
    value: Object.freeze(value),
  });
}

async function invokeStateRootPolicy(executable, workspace, role, options = {}) {
  if (!ROLES.has(role)) throw new Error("unsupported Workbench state-root role");
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const mappedWorkspace = pathForCoreLaunch(workspace, launch, "state-root workspace");
  const arguments_ = ["settings", "state-root", "resolve", mappedWorkspace, role];
  if (options.expectedPolicyId !== undefined) {
    if (!POLICY_ID.test(options.expectedPolicyId)) throw new Error("expected state-root policy identity is invalid");
    arguments_.push("--expected-policy-id", options.expectedPolicyId);
  }
  const value = await invokeCoreJson(executable, [...arguments_, "--json"], {
    ...options, launch, label: "Workbench state-root policy", maximumOutput: 1024 * 1024,
  });
  return validateStateRootPolicy(value, mappedWorkspace, role, launch);
}

async function selectStateRootPolicy(executable, workspace, role, selectedRoot, expectedPolicyId, options = {}) {
  if (!ROLES.has(role)) throw new Error("unsupported Workbench state-root role");
  if (!POLICY_ID.test(expectedPolicyId)) throw new Error("expected state-root policy identity is invalid");
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const mappedWorkspace = pathForCoreLaunch(workspace, launch, "state-root workspace");
  const clear = selectedRoot === null;
  const arguments_ = [
    "settings", "state-root", clear ? "clear" : "select", mappedWorkspace, role,
  ];
  if (!clear) {
    arguments_.push(pathForCoreLaunch(selectedRoot, launch, "selected state root"));
  }
  arguments_.push("--expected-policy-id", expectedPolicyId, "--json");
  const value = await invokeCoreJson(executable, arguments_, {
    ...options, launch, label: "Workbench selected state-root policy", maximumOutput: 1024 * 1024,
  });
  return validateStateRootPolicy(value, mappedWorkspace, role, launch);
}

module.exports = {
  hostStateRoot,
  invokeStateRootPolicy,
  legacyFeatureDecision,
  legacyProductSpineDecision,
  selectStateRootPolicy,
  validateStateRootPolicy,
};
