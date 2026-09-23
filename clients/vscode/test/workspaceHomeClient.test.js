"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { validateWorkspaceHome } = require("../workspaceHomeClient");

function homeFixture() {
  return {
    format: "workbench-workspace-home-v1",
    schema_version: 1,
    read_only: true,
    workspace: {
      requested_path: "/home/dev/example",
      root: "/home/dev/example",
      display_name: "Example Mod",
      kind: "cleanroom-mod",
      recognition: "bounded",
    },
    status: {
      status: "attention",
      blockers: 0,
      warnings: 1,
      information: 0,
    },
    repository: {
      state: "observed",
      root: "/home/dev/example",
      branch: "main",
      head: "a".repeat(40),
      dirty: false,
      changes: { unstaged: 0, untracked: 0 },
    },
    context: {
      platform: {
        state: "observed",
        kind: "cleanroom",
        minecraft_version: "1.12.2",
        cleanroom_version: "0.3.0-alpha",
      },
      profile: {
        state: "unresolved",
        profile_family_id: null,
        selected_profile: null,
        platform_profile_id: null,
        maturity: null,
        stable_release: false,
        support_claimed: false,
      },
      build: { state: "observed", provider: "gradle", wrapper: { version: "8.7" } },
      mod_ids: ["example"],
      source_surfaces: { source: ["src/main/java"], resources: [], groovy: [], configuration: [] },
    },
    problems: [{
      id: "PROFILE_UNRESOLVED",
      severity: "warning",
      title: "No exact profile was selected",
      detail: "The project was recognized without a supported pack profile.",
      repair: { action: "Select an exact profile.", command: null, mutates: false },
    }],
    actions: [{
      id: "workspace-health",
      title: "Check workspace health",
      purpose: "Inspect project declarations.",
      available: true,
      argv: ["workbench", "doctor", "/home/dev/example"],
      blockers: [],
      unavailable_reason: null,
    }, {
      id: "run-development-client",
      title: "Run a development client",
      purpose: "Start the declared development client.",
      available: false,
      argv: null,
      blockers: ["EXACT_RUN_PROFILE_REQUIRED"],
      unavailable_reason: "No exact development run profile was found.",
    }],
    gaps: [{
      id: "generic-development-loop",
      summary: "Generic Cleanroom build and run is not available yet.",
    }],
    owner_records: {
      workspace_doctor_format: "workbench-project-intelligence-workspace-doctor-report-v1",
      exact_pack_context_format: null,
    },
    limitations: ["Home is read-only."],
  };
}

test("workspace Home preserves owner action order, availability, and blockers", () => {
  const result = validateWorkspaceHome(homeFixture());
  assert.deepEqual(result.actions.map((action) => action.id), [
    "workspace-health",
    "run-development-client",
  ]);
  assert.equal(result.actions[0].available, true);
  assert.equal(result.actions[1].available, false);
  assert.deepEqual(result.actions[1].blockers, ["EXACT_RUN_PROFILE_REQUIRED"]);
  assert.equal(
    result.actions[1].unavailable_reason,
    "No exact development run profile was found.",
  );
  assert.equal(Object.isFrozen(result.actions), true);
});
test("workspace Home rejects shape drift and contradictory action claims", () => {
  const drift = homeFixture();
  drift.actions[0].client_rank = 1;
  assert.throws(() => validateWorkspaceHome(drift), /fields changed/);

  const inventedAvailable = homeFixture();
  inventedAvailable.actions[1].available = true;
  assert.throws(() => validateWorkspaceHome(inventedAvailable), /lacks exact argv/);

  const erasedBlocker = homeFixture();
  erasedBlocker.actions[1].blockers = [];
  assert.throws(() => validateWorkspaceHome(erasedBlocker), /must carry owner blockers/);

  const wrongExecutable = homeFixture();
  wrongExecutable.actions[0].argv[0] = "./surprise";
  assert.throws(() => validateWorkspaceHome(wrongExecutable), /internally inconsistent/);
});
