"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const test = require("node:test");

const {
  assignmentArguments,
  composeCommandFlow,
  invokeCommandOutput,
  parseExactIntegerInput,
  validateCatalog,
} = require("../commandCenterClient");
const { MAX_TIMEOUT_MS } = require("../coreCommandClient");

const CATALOG_DIGEST = `sha256:${"1".repeat(64)}`;
const ACTION_DIGEST = `sha256:${"2".repeat(64)}`;
const REVIEW_DIGEST = `sha256:${"3".repeat(64)}`;

function fixtureCatalog() {
  return {
    format_version: "workbench-live-console-command-catalog-v2",
    catalog_digest: CATALOG_DIGEST,
    suites: [{
      suite_id: "developer-features",
      title: "Developer Features",
      summary: "Owner-backed construction flows.",
      authority: "Blueprints and pack profiles",
      availability: "experimental",
      command_count: 1,
    }],
    commands: [{
      command_id: "developer-features.recipe-change-plan",
      suite_id: "developer-features",
      title: "Plan recipe change",
      summary: "Plan one exact additive recipe change.",
      authority: "Supersymmetry profile-owned Blueprint",
      risk: "read-only",
      preview: "none",
      availability: "experimental",
      documentation: null,
      document: null,
      limitations: ["Runtime observation remains separate."],
      command_preview: "workbench feature plan recipe-change ...",
      action_digest: ACTION_DIGEST,
      options: [{
        key: "workspace",
        label: "Workspace",
        help: "Exact source checkout.",
        flags: [],
        kind: "path",
        required: true,
        positional: true,
        choices: [],
        nargs: "one",
        repeat: false,
        placement: 0,
        sensitive: false,
        required_group: false,
        console_managed: false,
      }],
    }],
  };
}

test("catalog validation preserves owner authority and typed options", () => {
  const catalog = validateCatalog(fixtureCatalog());
  assert.equal(catalog.catalog_digest, CATALOG_DIGEST);
  assert.equal(catalog.commands[0].authority, "Supersymmetry profile-owned Blueprint");
  assert.equal(catalog.commands[0].options[0].kind, "path");
  assert.equal(Object.isFrozen(catalog.commands[0]), true);

  const changed = fixtureCatalog();
  changed.commands[0].copied_blueprint_policy = true;
  assert.throws(() => validateCatalog(changed), /keys differ/);
});

test("generic command center accepts both runtime-comparison catalog surfaces", () => {
  const runtime = fixtureCatalog();
  runtime.commands[0].command_id = "developer-features.compare-recipe-runtime";
  runtime.commands[0].risk = "mutating";
  runtime.commands[0].preview = "inert-only";
  assert.equal(
    validateCatalog(runtime).commands[0].command_id,
    "developer-features.compare-recipe-runtime",
  );

  const atlas = fixtureCatalog();
  atlas.suites[0].suite_id = "atlas";
  atlas.commands[0].suite_id = "atlas";
  atlas.commands[0].command_id = "atlas.recipes-compare-runtime";
  assert.equal(
    validateCatalog(atlas).commands[0].command_id,
    "atlas.recipes-compare-runtime",
  );
});

test("typed assignments remain exact argv tokens without a command shell", () => {
  assert.deepEqual(assignmentArguments(new Map([
    ["workspace", "/tmp/a workspace"],
    ["request", { mutation: "add", duration: 60 }],
  ])), [
    "--set", "workspace:=\"/tmp/a workspace\"",
    "--set", "request:={\"mutation\":\"add\",\"duration\":60}",
  ]);
  assert.throws(() => assignmentArguments(new Map([["unsafe-key", "x"]])), /unsafe/);
  assert.throws(
    () => assignmentArguments(new Map([["request", "x".repeat(25 * 1024)]])),
    /assignment boundary/,
  );
});

test("integer inputs preserve signed 64-bit values as decimal text", () => {
  assert.equal(parseExactIntegerInput("9223372036854775807", false), "9223372036854775807");
  assert.deepEqual(
    parseExactIntegerInput("[9223372036854775807,-9223372036854775808]", true),
    ["9223372036854775807", "-9223372036854775808"],
  );
  assert.throws(() => parseExactIntegerInput("[1.0]", true), /only JSON integers/);
});

test("command flow binds catalog, action, and review digests before execution", () => {
  const command = validateCatalog(fixtureCatalog()).commands[0];
  const flow = composeCommandFlow(
    CATALOG_DIGEST,
    command,
    ["--set", "workspace:=\"/tmp/source\""],
  );
  assert.equal(flow.commandReviewArguments.at(-1), "--review-json");
  const bound = flow.bindReview({
    format_version: "workbench-live-console-command-review-v2",
    catalog_digest: CATALOG_DIGEST,
    action_digest: ACTION_DIGEST,
    review_digest: REVIEW_DIGEST,
    command_id: command.command_id,
    risk: "read-only",
    preview: "none",
    preview_intent: "execute",
    execute_intent: "execute",
    preview_command: "workbench feature plan recipe-change /tmp/source",
    execute_command: "workbench feature plan recipe-change /tmp/source",
  });
  assert.equal(bound.ownerPreviewArguments, undefined);
  assert.deepEqual(bound.executeArguments.slice(-3), [
    "--expect-review-digest", REVIEW_DIGEST, "--execute",
  ]);

  const mismatched = {
    ...bound.review,
    action_digest: `sha256:${"4".repeat(64)}`,
  };
  assert.throws(() => flow.bindReview(mismatched), /does not match/);
});

test("catalog owner previews and executions can outlive a thirty-minute runtime matrix", async () => {
  const original = childProcess.execFile;
  const observed = [];
  try {
    childProcess.execFile = (_executable, arguments_, options, callback) => {
      observed.push({ arguments_, options });
      callback(null, '{"outcome":"passed"}\n', "");
    };
    const base = ["console", "run", "change.material-fluid-test"];
    for (const arguments_ of [base, [...base, "--execute"]]) {
      assert.equal(
        await invokeCommandOutput("/workbench", arguments_),
        '{"outcome":"passed"}\n',
      );
    }
    assert.deepEqual(
      observed.map(({ arguments_ }) => arguments_),
      [base, [...base, "--execute"]],
    );
    assert.deepEqual(observed.map(({ options }) => options.timeout), [
      MAX_TIMEOUT_MS,
      MAX_TIMEOUT_MS,
    ]);
    assert.ok(observed.every(({ options }) => options.timeout > 30 * 60 * 1000));
  } finally {
    childProcess.execFile = original;
  }
});
