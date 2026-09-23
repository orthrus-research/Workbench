"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function harness({ consent = true, execute = async () => ({ state: "completed" }), pick = (choices, index) => index < 2 ? choices[0] : undefined, history, comparison, recipes } = {}) {
  let command, edit, cancel, picks = 0;
  const calls = [], decorations = [], shown = [];
  let provider;
  const disposable = { dispose() {} };
  const request = { attempt_id: "check-" + "b".repeat(32), id: "saved-check-request:sha256:" + "c".repeat(64), check: { label: "Client check" }, candidate: { id: "exact-candidate" }, effects: ["Execute disposable runtime"] };
  const vscode = {
    languages: { createDiagnosticCollection: () => ({ ...disposable, clear() {}, set: (rows) => decorations.push(rows) }) },
    workspace: { isTrusted: true, workspaceFolders: [{ uri: { scheme: "file", fsPath: "/pack" } }], textDocuments: [],
      registerTextDocumentContentProvider: (_name, value) => { provider = value; return disposable; },
      openTextDocument: async (uri) => provider.provideTextDocumentContent(uri),
      createFileSystemWatcher: () => ({ ...disposable, onDidChange: () => disposable, onDidCreate: () => disposable, onDidDelete: () => disposable }),
      onDidChangeTextDocument: (callback) => { edit = callback; return disposable; }, onDidChangeWorkspaceFolders: () => disposable,
    },
    window: {
      showQuickPick: async (choices) => pick(choices, picks++),
      showTextDocument: async (document) => shown.push(document),
      showWarningMessage: async () => consent ? "Run this exact check" : undefined,
      showErrorMessage: (message) => { throw new Error(message); },
      showInputBox: async () => "groovy/postInit/Probe.groovy",
      withProgress: async (_options, work) => work({ report() {} }, { onCancellationRequested: (callback) => { cancel = callback; return disposable; } }),
    },
    ProgressLocation: { Notification: 1 },
    commands: { registerCommand: (_id, callback) => { command = callback; return disposable; } },
    Uri: { file: (value) => value, parse: (value) => ({ toString: () => value }) }, Diagnostic: class {}, Range: class {}, DiagnosticSeverity: { Error: 0 },
  };
  const module = { exports: {} };
  const mocks = {
    "./developerContext": { selectContext: async () => "work-session-v2-" + "a".repeat(32) },
    "./sourceNavigationClient": { verifiedSourceTarget: () => ({ path: "/pack/test.groovy" }) },
    "./materialChecks": { createMaterialChecks: () => ({ run: async (root, session) => calls.push({ action: "material-ui", root, session }), reopen: async (root, session) => calls.push({ action: "material-reopen", root, session }) }) },
    "./developerChecksClient": { ...require("../developerChecksClient"), invokeCheck: async (_exe, _root, _session, action, value, confirmation) => {
      calls.push({ action, value, confirmation });
      if (action === "images") return { images: [{ id: "runtime-image:sha256:" + "d".repeat(64), binding: { pack: "supersymmetry", side: "client" } }] };
      if (action === "prepare") return request;
      if (action === "execute") return execute();
      if (action === "history") return history;
      if (action === "recipes") return recipes;
      if (action === "compare") return comparison;
      return {};
    } },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../developerChecks.js"), "utf8"), { module, require: (name) => mocks[name] });
  module.exports.registerDeveloperChecks(vscode, { subscriptions: [] }, () => "/workbench");
  return { run: () => command(), cancel: () => cancel(), edit: () => edit({ document: { uri: { scheme: "file" } }, contentChanges: [{}] }), calls, decorations, request, shown };
}

test("IDE recipe flow selects source then requires exact runtime consent", async () => {
  const recipe = { id: "saved-recipe:sha256:" + "e".repeat(64), map: "MIXER", location: { path: "groovy/postInit/Probe.groovy", start: { line: 1 } }, support: "supported", reasons: [], recipe: {} };
  const h = harness({ consent: false, recipes: { recipes: [recipe] }, pick: (choices, index) => index === 0 ? "Run a recipe check on saved changes" : choices[0] });
  await h.run();
  assert.equal(h.calls.find(row => row.action === "prepare").confirmation.recipe, recipe.id);
  assert.ok(h.calls.some(row => row.action === "recipes"));
  assert.ok(!h.calls.some(row => row.action === "execute"));
});

test("Saved Checks dispatches material choices before the runtime-image workflow", async () => {
  for (const mode of ["Run Axiom material preflight", "Reopen an Axiom material check"]) {
    const h = harness({ pick: () => mode }); await h.run();
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].root, "/pack");
    assert.equal(h.calls[0].action, mode.startsWith("Run") ? "material-ui" : "material-reopen");
  }
});

test("IDE comparison uses explicit history selection and displays an unchanged inconclusive outcome", async () => {
  const candidate = "check-" + "b".repeat(32), reference = "check-" + "e".repeat(32);
  const provenance = { runtime: { pack: { name: "Supersymmetry", version: "test" }, platform: { id: "fixture", version: "1" } }, image: { id: "image" }, source_labels: { local_tags: [] } };
  const comparison = { state: "compared", reference: { attempt_id: reference, state: "inconclusive" }, candidate: { attempt_id: candidate, state: "inconclusive" }, counts: { persistent: 1 }, reasons: [], groups: [] };
  const h = harness({ execute: async () => ({ attempt_id: candidate, state: "inconclusive", candidate: { revision: "revision", dirty: false }, provenance, diagnostics: [] }),
    history: { runs: [{ attempt_id: reference, pack: { name: "Supersymmetry", version: "test" }, source: { revision: "revision" }, state: "inconclusive", image_id: "image" }], unsupported_records: 0 }, comparison,
    pick: (choices, index) => index === 2 ? choices.find((row) => row.type === "compare") : choices[0],
  });
  await h.run();
  assert.equal(h.calls.find((row) => row.action === "compare").confirmation, reference);
  assert.equal(h.calls.filter((row) => row.action === "execute").length, 1);
  assert.equal(JSON.parse(h.shown[0]).candidate.state, "inconclusive");
});

test("declining exact IDE consent prepares but never executes", async () => {
  const h = harness({ consent: false });
  await h.run();
  assert.deepEqual(h.calls.map((row) => row.action), ["images", "prepare"]);
});

test("recipe result opens readable retained explanation without another runtime run", async () => {
  const report = { format: "workbench-check-explanation-v1", summary: "Captured lookup", text: "Duration (ticks): expected 200; observed 240", sections: [], limitations: [] };
  const h = harness({ execute: async () => ({ state: "inconclusive", candidate_id: "current", assertions: { state: "mismatched", reasons: [], expectation: { source: { candidate_id: "old" } }, observation: { details: { explanation: report } } } }),
    pick: (choices, index) => index === 2 ? choices.find(row => row.type === "assertions") : choices[0] });
  await h.run();
  assert.match(h.shown[0], /Recipe assertion: mismatched/);
  assert.match(h.shown[0], /Startup: inconclusive/);
  assert.match(h.shown[0], /Duration \(ticks\): expected 200; observed 240/);
  assert.equal(h.calls.filter(row => row.action === "execute").length, 1);
  assert.ok(h.decorations.every(rows => rows.length === 0));
});

test("edits do not retarget or cancel a running check; cancellation uses the owner", async () => {
  let ready, finish;
  const started = new Promise((resolve) => { ready = resolve; });
  const h = harness({ execute: () => { ready(); return new Promise((resolve) => { finish = resolve; }); } });
  const work = h.run();
  await started;
  h.edit();
  assert.equal(h.calls.filter((row) => row.action === "cancel").length, 0);
  h.cancel();
  finish({ state: "cancelled", sourceCurrent: true, interpretation: { findings: [{ message: "Compiler error", category: "compiler", severity: "error", reason: "Captured compiler failure", evidence: [], location: { start: { line: 1, column: 1 }, end: { line: 1, column: 2 } } }] } });
  await work;
  assert.equal(h.calls.find((row) => row.action === "execute").confirmation, h.request.id);
  assert.equal(h.calls.filter((row) => row.action === "cancel").length, 1);
  assert.ok(h.decorations.every((rows) => rows.length === 0));
});
