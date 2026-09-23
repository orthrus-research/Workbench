"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const test = require("node:test");
const { createAtlasRecipeImpactFlow } = require("../atlasRecipeImpactFlow");
const { report } = require("./atlasRecipeImpactClient.test");

test("native search journey rejects changed graph evidence before replacing its displayed report", async () => {
  const original = childProcess.execFile;
  const expected = report();
  const search = { format: "workbench-atlas-recipe-health-search-v1", schema_version: 1,
    context: expected.context, query: "selected", results: [expected.selection], truncated: false };
  const requests = [], notices = [], errors = [];
  let changed = false;
  const shown = { report: { prior: true }, setReport(value) { this.report = value; } };
  const api = {
    workspace: { isTrusted: true, workspaceFolders: [] },
    ProgressLocation: { Notification: 1 },
    commands: { executeCommand: async () => undefined },
    window: {
      showOpenDialog: async () => [{ scheme: "file", fsPath: "/graph" }],
      showQuickPick: async choices => choices.find(row => row.mode === "search") || choices[0],
      showInputBox: async options => options.title === "Find One Observed GT Recipe" ? "selected" : options.value,
      withProgress: async (_options, callback) => callback(),
      showErrorMessage: async message => { errors.push(message); },
      showWarningMessage: async message => { notices.push(message); },
      showInformationMessage: async message => { notices.push(message); },
    },
  };
  try {
    childProcess.execFile = (_executable, args, _options, callback) => {
      requests.push(args);
      const value = args.includes("search") ? search : structuredClone(expected);
      if (changed && args.includes("impact")) value.context.graph_set_id += ":changed";
      callback(null, JSON.stringify(value), "");
    };
    const flow = createAtlasRecipeImpactFlow(api, shown, {
      selectedCore: () => "/opt/workbench", currentWorkingDirectory: () => "/workspace",
    });
    assert.deepEqual(await flow.analyze(), expected);
    assert.deepEqual(errors, []);
    assert.equal(requests.length, 2);
    assert.ok(requests[0].includes("200"));
    assert.match(notices[0], /Alternative viability is not established/);
    const prior = shown.report;
    changed = true;
    assert.equal(await flow.analyze(), undefined);
    assert.equal(shown.report, prior);
    assert.match(errors[0], /graph context changed after exact recipe search/);
    assert.equal(notices.length, 1);
    api.workspace.isTrusted = false;
    const count = requests.length;
    assert.equal(await flow.analyze(), undefined);
    assert.equal(requests.length, count);
    assert.match(notices.at(-1), /untrusted workspace/);
  } finally {
    childProcess.execFile = original;
  }
});

test("explicit complete journey skips bounds and cancellation never replaces the prior report", async () => {
  const original = childProcess.execFile;
  const expected = structuredClone(require("./fixtures/atlas-complete-impact-v2.json"));
  const search = { format: "workbench-atlas-recipe-health-search-v1", schema_version: 1,
    context: expected.context, query: "selected", results: [expected.selection], truncated: false };
  const requests = [], notices = [], errors = [], prompts = [], progressModes = [];
  const shown = { report: { prior: true }, setReport(value) { this.report = value; } };
  let cancel = false, cancelCallback;
  const api = {
    workspace: { isTrusted: true, workspaceFolders: [] }, ProgressLocation: { Notification: 1 },
    commands: { executeCommand: async () => undefined },
    window: {
      showOpenDialog: async () => [{ scheme: "file", fsPath: "/graph" }],
      showQuickPick: async choices => choices.find(row => row.mode === "search")
        || choices.find(row => row.exploration === "complete-finite") || choices[0],
      showInputBox: async options => { prompts.push(options.title); assert.equal(options.title, "Find One Observed GT Recipe"); return "selected"; },
      withProgress: async (options, callback) => {
        progressModes.push(options.cancellable);
        return callback({}, { isCancellationRequested: false, onCancellationRequested(listener) {
          cancelCallback = listener; return { dispose() { cancelCallback = undefined; } };
        } });
      },
      showErrorMessage: async message => { errors.push(message); },
      showWarningMessage: async message => { notices.push(message); },
      showInformationMessage: async message => { notices.push(message); },
    },
  };
  try {
    childProcess.execFile = (_executable, args, options, callback) => {
      requests.push({ args, options });
      queueMicrotask(() => {
        if (cancel && args.includes("impact")) {
          assert.ok(options.signal); cancelCallback();
          callback(new Error("aborted"), "", "");
        } else callback(null, JSON.stringify(args.includes("search") ? search : expected), "");
      });
      return { pid: -1, exitCode: null };
    };
    const flow = createAtlasRecipeImpactFlow(api, shown, {
      selectedCore: () => "/opt/workbench", currentWorkingDirectory: () => "/workspace",
    });
    assert.deepEqual(await flow.analyze(), expected);
    assert.deepEqual(progressModes, [false, true]);
    assert.deepEqual(prompts, ["Find One Observed GT Recipe"]);
    assert.ok(requests[1].args.includes("complete-finite"));
    assert.equal(requests[1].options.timeout, 0); assert.equal(requests[1].options.maxBuffer, Infinity);
    assert.match(notices[0], /exploration complete.*evidence incomplete.*viability/);
    const prior = shown.report; cancel = true;
    assert.equal(await flow.analyze(), undefined); assert.equal(shown.report, prior);
    assert.match(notices.at(-1), /cancelled.*previous report remains/);
    assert.equal(cancelCallback, undefined); assert.deepEqual(errors, []);
  } finally { childProcess.execFile = original; }
});
