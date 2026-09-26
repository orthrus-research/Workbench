"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const { createHash } = require("node:crypto");
const test = require("node:test");

const {
  invokeStateRootPolicy,
  legacyFeatureDecision,
  legacyProductSpineDecision,
  selectStateRootPolicy,
  validateStateRootPolicy,
} = require("../stateRootPolicyClient");
const { resolveCoreLaunch } = require("../coreLaunch");

const RECORD_ID = `workbench-state-root-selections:sha256:${"b".repeat(64)}`;

function fixture(workspace = "/home/dev/project", stateRoot = "/home/dev/retained", role = "product-spine") {
  const body = {
    format: "workbench-state-root-policy-v1",
    schema_version: 1,
    configuration_home: "/home/dev/.workbench",
    workspace,
    workspace_id: null,
    role,
    state_root: stateRoot,
    source: "user-selection",
    selections_record_id: RECORD_ID,
  };
  const canonical = Object.fromEntries(Object.keys(body).sort().map((key) => [key, body[key]]));
  return {
    ...body,
    policy_id: `workbench-state-root-policy:sha256:${createHash("sha256")
      .update(JSON.stringify(canonical), "utf8").digest("hex")}`,
  };
}

const POLICY_ID = fixture().policy_id;

test("installed-like Windows/WSL route maps the Core policy into the same host path", async () => {
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      callback(null, JSON.stringify(fixture()), "");
      return { pid: 999999, exitCode: 0 };
    };
    const executable = "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench";
    const workspace = "\\\\wsl.localhost\\Ubuntu\\home\\dev\\project";
    const options = { platform: "win32", environment: { SystemRoot: "C:\\Windows" } };
    const selected = await invokeStateRootPolicy(executable, workspace, "product-spine", options);
    assert.equal(selected.stateRoot, "\\\\wsl.localhost\\Ubuntu\\home\\dev\\retained");
    assert.deepEqual(calls[0].arguments_.slice(-6), [
      "settings", "state-root", "resolve", "/home/dev/project", "product-spine", "--json",
    ]);
    assert.equal(calls[0].options.shell, false);
    assert.equal(calls[0].options.cwd, undefined);
    await invokeStateRootPolicy(executable, workspace, "product-spine", {
      ...options, expectedPolicyId: selected.policyId,
    });
    assert.deepEqual(calls[1].arguments_.slice(-8), [
      "settings", "state-root", "resolve", "/home/dev/project", "product-spine",
      "--expected-policy-id", POLICY_ID, "--json",
    ]);
    const saved = await selectStateRootPolicy(
      executable, workspace, "product-spine", selected.stateRoot, selected.policyId, options,
    );
    assert.equal(saved.policyId, POLICY_ID);
    assert.deepEqual(calls[2].arguments_.slice(-9), [
      "settings", "state-root", "select", "/home/dev/project", "product-spine",
      "/home/dev/retained", "--expected-policy-id", POLICY_ID, "--json",
    ]);
  } finally {
    childProcess.execFile = original;
  }
});

test("Feature policy selection uses the configured WSL distribution", async () => {
  const feature = fixture("/home/dev/project", "/home/dev/feature-state", "feature");
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      callback(null, JSON.stringify(feature), "");
      return { pid: 999999, exitCode: 0 };
    };
    const executable = "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench";
    const workspace = "\\\\wsl.localhost\\Ubuntu\\home\\dev\\project";
    const options = { platform: "win32", environment: { SystemRoot: "C:\\Windows" } };
    const selected = await invokeStateRootPolicy(executable, workspace, "feature", options);
    assert.equal(selected.stateRoot, "\\\\wsl.localhost\\Ubuntu\\home\\dev\\feature-state");
    assert.deepEqual(calls[0].arguments_.slice(-6), [
      "settings", "state-root", "resolve", "/home/dev/project", "feature", "--json",
    ]);
    await selectStateRootPolicy(
      executable, workspace, "feature", selected.stateRoot, selected.policyId, options,
    );
    assert.deepEqual(calls[1].arguments_.slice(-9), [
      "settings", "state-root", "select", "/home/dev/project", "feature",
      "/home/dev/feature-state", "--expected-policy-id", selected.policyId, "--json",
    ]);
    await assert.rejects(
      selectStateRootPolicy(
        executable, workspace, "feature",
        "\\\\wsl.localhost\\Other\\home\\dev\\feature-state", selected.policyId, options,
      ),
      /configured WSL distribution/,
    );
    assert.equal(calls.length, 2);
  } finally {
    childProcess.execFile = original;
  }
});

test("policy read refuses a missing installed Core and a changed workspace scope", async () => {
  const original = childProcess.execFile;
  try {
    childProcess.execFile = (_executable, _arguments, _options, callback) => {
      callback(Object.assign(new Error("missing Core"), { code: "ENOENT" }), "", "");
      return { pid: 999999, exitCode: 1 };
    };
    await assert.rejects(
      invokeStateRootPolicy("/missing/workbench", "/project", "product-spine"),
      /missing Core/,
    );
    await assert.rejects(
      invokeStateRootPolicy("/missing/workbench", "/project", "feature"),
      /missing Core/,
    );
  } finally {
    childProcess.execFile = original;
  }
  const launch = resolveCoreLaunch("/opt/workbench");
  assert.throws(
    () => validateStateRootPolicy(fixture("/other"), "/project", "product-spine", launch),
    /identity or scope/,
  );
  assert.throws(
    () => validateStateRootPolicy({ ...fixture("/project"), policy_id: "stale" },
      "/project", "product-spine", launch),
    /identity or scope/,
  );
});

test("earlier VS Code setting requires an explicit Core migration choice", () => {
  const earlier = "\\\\wsl.localhost\\Ubuntu\\home\\dev\\prior-state";
  assert.equal(
    legacyProductSpineDecision({ source: "platform-default" }, earlier),
    "migration-required",
  );
  assert.equal(
    legacyProductSpineDecision({ source: "user-selection" }, earlier),
    "historical-hint",
  );
  assert.equal(legacyProductSpineDecision({ source: "platform-default" }, ""), "ready");
  assert.throws(
    () => legacyProductSpineDecision({ source: "platform-default" }, "bad\0path"),
    /invalid/,
  );
  assert.equal(
    legacyFeatureDecision({ source: "platform-default" }, earlier),
    "migration-required",
  );
  assert.equal(
    legacyFeatureDecision({ source: "user-selection" }, earlier),
    "historical-hint",
  );
  assert.equal(legacyFeatureDecision({ source: "platform-default" }, ""), "ready");
  assert.throws(
    () => legacyFeatureDecision({ source: "platform-default" }, "bad\0path"),
    /workbench\.feature\.stateRoot is invalid/,
  );
});
