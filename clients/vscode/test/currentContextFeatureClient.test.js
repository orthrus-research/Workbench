"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  currentContextFeatureArguments,
  invokeCurrentContextFeatureAction,
} = require("../currentContextFeatureClient");

test("current-context actions contain no workspace, path, or owner identity", () => {
  for (const action of ["open", "test", "apply", "verify", "rollback", "recover"]) {
    assert.deepEqual(
      [...currentContextFeatureArguments(action)],
      ["change", "material-fluid-recipe", action, "--json"],
    );
  }
  for (const rejected of ["start", "", "work-session-v2-" + "1".repeat(32), null]) {
    assert.throws(
      () => currentContextFeatureArguments(rejected),
      /unsupported/,
    );
  }
});

test("installed entrypoint returns exact child process custody", async () => {
  const script = "process.stdout.write(JSON.stringify({state:'retained',argv:process.argv.slice(1)}))";
  const result = await invokeCurrentContextFeatureAction(process.execPath, "open", {
    launch: {
      configured: process.execPath,
      coreExecutable: process.execPath,
      distribution: null,
      executable: process.execPath,
      host: "native",
      prefixArguments: ["-e", script, "--"],
    },
    timeoutMs: 10_000,
  });
  assert.equal(result.format, "workbench-current-context-feature-action-v1");
  assert.deepEqual(
    result.outcome.argv,
    ["change", "material-fluid-recipe", "open", "--json"],
  );
  assert.deepEqual(
    [...result.arguments],
    ["change", "material-fluid-recipe", "open", "--json"],
  );
  assert.ok(Number.isSafeInteger(result.invocation.pid) && result.invocation.pid > 0);
  if (process.platform === "linux") {
    assert.ok(
      Number.isSafeInteger(result.invocation.process_start_time_ticks)
        && result.invocation.process_start_time_ticks > 0,
    );
  }
  assert.equal(result.invocation.exit_code, 0);
  assert.equal(result.invocation.signal, null);
  assert.equal(result.invocation.stderr, "");
  assert.match(result.invocation.started_at, /Z$/);
  assert.match(result.invocation.finished_at, /Z$/);
});
