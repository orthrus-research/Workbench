"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { ReviewEpoch } = require("../localReviewClient");

function harness(invoke) {
  let command, change, provider;
  const calls = [], cleared = [], diagnosticSets = [];
  const inputs = ["supersymmetry", "cleanroom", "cleanroom-provisional", "HEAD"];
  let pick = 0;
  const disposable = { dispose() {} };
  const vscode = {
    languages: { createDiagnosticCollection: () => ({ ...disposable, clear: () => cleared.push(true), set: (rows) => diagnosticSets.push(rows) }) },
    workspace: { isTrusted: true, workspaceFolders: [{ uri: { scheme: "file", fsPath: "/pack" } }], textDocuments: [],
      createFileSystemWatcher: () => ({ ...disposable, onDidChange: () => disposable, onDidCreate: () => disposable, onDidDelete: () => disposable }),
      registerTextDocumentContentProvider: (_scheme, value) => { provider = value; return disposable; },
      onDidChangeTextDocument: (callback) => { change = callback; return disposable; },
      onDidChangeWorkspaceFolders: () => disposable, onDidChangeConfiguration: () => disposable,
    },
    Uri: { parse: (value) => ({ toString: () => value }) },
    window: { showQuickPick: async (choices) => pick++ === 0 ? choices[0] : pick === 2 ? choices.find((choice) => choice.type === "file") : undefined,
      showInputBox: async () => inputs.shift(),
      withProgress: async (_options, work) => work({}, { onCancellationRequested: () => disposable }),
      showErrorMessage: (message) => { throw new Error(message); },
    },
    ProgressLocation: { Notification: 1 },
    commands: { registerCommand: (_id, callback) => { command = callback; return disposable; },
      executeCommand: async (...args) => calls.push(args) },
  };
  const module = { exports: {} };
  const contextCalls = [];
  const mocks = {
    "./developerContext": { rememberContext() {} },
    "./localReviewClient": { ReviewEpoch, invokeLocalReview: invoke },
    "./coreCommandClient": { invokeCoreJson: async (...args) => { contextCalls.push(args); return { session_id: "work-session-v2-" + "a".repeat(32) }; } },
    "./coreLaunch": { resolveCoreLaunch: () => ({ host: "native" }) },
    "./sourceNavigationClient": {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../localReview.js"), "utf8"), { module, require: (name) => mocks[name] });
  module.exports.registerLocalReview(vscode, { subscriptions: [] }, () => "/workbench");
  return { run: () => command(), edit: () => change({ document: { uri: { scheme: "file" } }, contentChanges: [{}] }),
    calls, cleared, diagnosticSets, contextCalls, text: (uri) => provider.provideTextDocumentContent(uri) };
}

function report() {
  return { baseline: { revision: "b".repeat(40) }, review_id: "source-review:sha256:" + "c".repeat(64),
    findings: [], changes: [], relationships: [], counts: { files: 1, changes: 0 },
    files: [{ path: "recipe.groovy", state: "modified", before_text: "before", after_text: "after",
      before_text_state: "included", after_text_state: "included" }] };
}

test("IDE context selection opens exact read-only diff without any source write command", async () => {
  const harnessed = harness(async () => report());
  await harnessed.run();
  assert.equal(harnessed.contextCalls[0][1][1], "select");
  assert.equal(harnessed.calls.length, 1);
  const [command, before, after, title] = harnessed.calls[0];
  assert.equal(command, "vscode.diff");
  assert.match(title, /read-only/);
  assert.equal(harnessed.text(before), "before");
  assert.equal(harnessed.text(after), "after");
  harnessed.edit();
  assert.ok(harnessed.cleared.length >= 2);
});

test("editing while analysis is pending suppresses the obsolete result", async () => {
  let resolve, started;
  const ready = new Promise((done) => { started = done; });
  const harnessed = harness(async () => { started(); return new Promise((done) => { resolve = done; }); });
  const work = harnessed.run();
  await ready;
  harnessed.edit();
  resolve(report());
  await work;
  assert.equal(harnessed.calls.length, 0);
  assert.equal(harnessed.diagnosticSets.length, 0);
});
