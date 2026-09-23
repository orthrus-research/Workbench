"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { validateCoreVersion, validateSetupCheck } = require("../coreStatusClient");

function versionFixture() {
  return { component_id: "workbench-core", version: "0.1.3" };
}

test("projects only the native Workbench version for first-run status", () => {
  const result = validateCoreVersion(versionFixture());
  assert.equal(result.currentVersion, "0.1.3");
  assert.deepEqual(Object.keys(result).sort(), ["currentVersion", "value"]);
});

test("accepts native pre-release versions declared by package authority", () => {
  for (const version of ["1.0.0a1", "1.0.0b2", "1.0.0rc3"]) {
    assert.equal(validateCoreVersion({ ...versionFixture(), version }).currentVersion, version);
  }
});

test("rejects non-Core identities and retired response families", () => {
  assert.throws(() => validateCoreVersion({
    ...versionFixture(), component_id: "workbench-vscode",
  }), /not a Workbench core/);
  assert.throws(() => validateCoreVersion({
    component_id: "workbench-core", current_version: "0.1.3",
    format: "workbench-component-version-v1", schema_version: 1,
    distribution: {}, release_metadata: {}, claims: {},
  }), /fields/);
});

test("rejects additive claims and malformed native versions", () => {
  assert.throws(() => validateCoreVersion({
    ...versionFixture(), old_release_channel: "stable",
  }), /fields/);
  assert.throws(() => validateCoreVersion({
    ...versionFixture(), claims: { release_qualified: true },
  }), /fields/);
  for (const version of [null, 3, "", "01.2.3", "1.2", "1.2.3-legacy", "1.2.3rc0", "1.2.3\n"]) {
    assert.throws(() => validateCoreVersion({ ...versionFixture(), version }), /version/);
  }
});

test("projects only the setup readiness fields owned by setup check V1", () => {
  const result = validateSetupCheck({
    format: "workbench-setup-check-v1",
    schema_version: 1,
    operation_class: "read-only",
    state: "installable",
    configured: false,
    record_path: "/home/me/.config/workbench/setup.json",
    selection: {},
    dependencies: [],
    blockers: [],
    managed_installs_available: ["java"],
    workspace_observation: {},
    environment: {},
    profile_policy: null,
    additive_future_field: true,
  });
  assert.equal(result.configured, false);
  assert.equal(result.state, "installable");
  assert.equal(result.ready, false);
  assert.deepEqual(result.managedInstallsAvailable, ["java"]);
});

test("setup check fails closed on malformed readiness", () => {
  assert.throws(() => validateSetupCheck({
    format: "workbench-setup-check-v1",
    schema_version: 1,
    state: "ready",
    configured: "yes",
    blockers: [],
    managed_installs_available: [],
  }), /boolean/);
});
