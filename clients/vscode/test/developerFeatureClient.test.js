"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const test = require("node:test");

const {
  argumentsForRun,
  invokeRun,
  parseResult,
} = require("../developerFeatureClient");

const PLAN = `workbench-developer-material-fluid-recipe-plan:sha256:${"1".repeat(64)}`;
const RUN = `workbench-developer-material-fluid-recipe-run:sha256:${"2".repeat(64)}`;
const MEANINGS = Object.freeze({
  fluid_registration: "the material-backed Forge fluid identity is registered",
  fml_client_load: "the disposable client reaches the exact FML loaded marker",
  groovy_compilation: "the changed Groovy program compiles in the projected client",
  localization: "the requested client translation resolves to its intended label",
  material_registration: "the requested GregTech material identity is registered",
  recipe_registration: "the exact reviewed machine recipe is registered once in its selected map",
});
const launch = {
  host: "native",
  distribution: null,
};

function receipt({
  outcome = "runtime-completed",
  state = "complete",
  assertionState = () => "observed",
} = {}) {
  return {
    assertions: Object.fromEntries(Object.entries(MEANINGS).map(([name, meaning]) => [
      name,
      { meaning, state: assertionState(name) },
    ])),
    attempt_id: `uuid:${"3".repeat(32)}`,
    format: "workbench-developer-material-fluid-recipe-run-v1",
    id: RUN,
    kind: "workbench-developer-material-fluid-recipe-run",
    limitations: ["This is one exact disposable runtime observation."],
    observation_probe: null,
    operation_class: "local-disposable-runtime",
    outcome,
    plan_id: PLAN,
    profile_observation: null,
    runtime: { state: "observed" },
    schema_version: 1,
    source: {
      final_verification: { state: "ready" },
      initial_verification: { state: "ready" },
      plan_id: PLAN,
      workspace_uri: "file:///source",
    },
    stage: null,
    state,
    target: {
      attempt_root_uri: "file:///state/attempt",
      receipt_uri: "file:///state/attempt/receipt.json",
    },
  };
}

function runOptions(overrides = {}) {
  return {
    plan: PLAN,
    launcherExecutable: "/opt/prism",
    launcherRoot: "/tmp/prism",
    ...overrides,
  };
}

test("builds the complete exact-consent disposable run command", () => {
  const args = argumentsForRun(launch, runOptions({
    launcherProfile: "Cleanroom",
    launcherJava: "/opt/java/bin/java",
    launcherJavaState: "/tmp/java-state",
    packwizExecutable: "/opt/packwiz",
    seedRoots: ["/tmp/seed"],
    stateRoot: "/tmp/workbench-state",
    memoryMiB: 6144,
    offlineName: "WBFeature",
  }));
  assert.deepEqual(args.slice(0, 8), [
    "feature", "run", "material-fluid-recipe", PLAN,
    "--consent", PLAN, "--launcher", "prism",
  ]);
  assert.equal(args.at(-1), "--json");
  assert.equal(args[args.indexOf("--consent") + 1], PLAN);
  for (const [flag, expected] of [
    ["--launcher-profile", "Cleanroom"],
    ["--launcher-java", "/opt/java/bin/java"],
    ["--launcher-java-state", "/tmp/java-state"],
    ["--packwiz-executable", "/opt/packwiz"],
    ["--seed", "/tmp/seed"],
    ["--state-root", "/tmp/workbench-state"],
    ["--memory-mib", "6144"],
    ["--offline-name", "WBFeature"],
  ]) {
    assert.equal(args[args.indexOf(flag) + 1], expected);
  }
});

test("maps every core-side path through the configured WSL distribution", () => {
  const args = argumentsForRun({
    host: "windows-wsl",
    distribution: "Ubuntu",
  }, runOptions({
    launcherExecutable: "\\\\wsl.localhost\\Ubuntu\\mnt\\c\\Prism\\prismlauncher.exe",
    launcherRoot: "\\\\wsl.localhost\\Ubuntu\\mnt\\c\\PrismData",
    packwizExecutable: "\\\\wsl.localhost\\Ubuntu\\opt\\packwiz",
    seedRoots: ["\\\\wsl.localhost\\Ubuntu\\tmp\\seed"],
  }));
  assert.equal(args[args.indexOf("--launcher-executable") + 1], "/mnt/c/Prism/prismlauncher.exe");
  assert.equal(args[args.indexOf("--launcher-root") + 1], "/mnt/c/PrismData");
  assert.equal(args[args.indexOf("--packwiz-executable") + 1], "/opt/packwiz");
  assert.equal(args[args.indexOf("--seed") + 1], "/tmp/seed");
});

test("rejects launcher values outside the runtime's actual bounds", () => {
  assert.throws(
    () => argumentsForRun(launch, runOptions({ memoryMiB: 512 })),
    /memory must be between 1024 and 131072/,
  );
  assert.throws(
    () => argumentsForRun(launch, runOptions({ offlineName: "x" })),
    /offline name must be 3-16/,
  );
  assert.throws(
    () => argumentsForRun(launch, runOptions({ attachTimeoutSeconds: 601 })),
    /attach timeout must be between 0 and 600/,
  );
  assert.throws(
    () => argumentsForRun(launch, runOptions({ launcherProfile: "bad\nprofile" })),
    /launcher profile must be printable/,
  );
});

test("accepts a complete exact six-assertion receipt", () => {
  const result = parseResult(JSON.stringify(receipt()), PLAN);
  assert.equal(result.complete, true);
  assert.equal(result.id, RUN);
});

test("accepts the CLI's incomplete receipt and preserves assertion states", () => {
  const result = parseResult(JSON.stringify(receipt({
    outcome: "runtime-assertion-failed",
    state: "incomplete",
    assertionState: (name) => name === "localization" ? "failed" : "observed",
  })), PLAN);
  assert.equal(result.complete, false);
  assert.equal(result.states.localization, "failed");
});

test("rejects receipt shape, meaning, and completion drift", () => {
  const extra = receipt();
  extra.unreviewed = true;
  assert.throws(() => parseResult(JSON.stringify(extra), PLAN), /fields changed/);

  const changedMeaning = receipt();
  changedMeaning.assertions.localization.meaning = "some localization was present";
  assert.throws(() => parseResult(JSON.stringify(changedMeaning), PLAN), /localization is invalid/);

  const contradictory = receipt({ state: "incomplete" });
  assert.throws(() => parseResult(JSON.stringify(contradictory), PLAN), /completion contradicts/);
});

test("requires exit 0 for complete and exact exit 1 for incomplete receipts", async () => {
  const original = childProcess.execFile;
  try {
    childProcess.execFile = (_executable, _args, _options, callback) => {
      callback(null, JSON.stringify(receipt()), "");
    };
    assert.equal((await invokeRun("/workbench", runOptions())).complete, true);

    childProcess.execFile = (_executable, _args, _options, callback) => {
      const error = new Error("incomplete");
      error.code = 1;
      callback(error, JSON.stringify(receipt({
        outcome: "failed",
        state: "incomplete",
        assertionState: () => "not-observed",
      })), "");
    };
    assert.equal((await invokeRun("/workbench", runOptions())).complete, false);

    childProcess.execFile = (_executable, _args, _options, callback) => {
      const error = new Error("unexpected core failure");
      error.code = 2;
      callback(error, JSON.stringify(receipt({
        outcome: "failed",
        state: "incomplete",
        assertionState: () => "not-observed",
      })), "");
    };
    await assert.rejects(
      invokeRun("/workbench", runOptions()),
      /exit status conflicts/,
    );
  } finally {
    childProcess.execFile = original;
  }
});
