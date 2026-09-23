"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const { result, session, attempt, request: requestId } = require("./materialCheckFixture");
const client = require("../materialChecksClient");
function harness({ consent = true, execute = async () => result(), dirty = false, intent = "", setupState = "missing", pick, query, show, retention, early, deliveryState = () => "preparing", results, initialState = [] } = {}) {
  const calls = [], shown = [], markers = [], prompts = [], selections = [], state = new Map(initialState); let generation = 0, cancel, stopped = false;
  const request = { format: client.REQUEST, id: requestId, attempt_id: attempt, state: "prepared-not-run", program: { files: [1, 2] }, candidate: { id: "exact-saved" }, inputs: { context: { id: "supersymmetry:material-authoring-gt-base", qualification: "pending" } } };
  if (early) request.diagnostic_delivery_format = require("../materialDeliveryClient").VIEW;
  const disposable = { dispose() {} }, module = { exports: {} };
  const vscode = { workspace: { textDocuments: dirty ? [{ uri: { fsPath: "/pack/groovy/Test.groovy" }, isDirty: true }] : [] },
    window: { showQuickPick: async (choices, options) => { selections.push(options); return pick ? pick(choices, options) : choices[0]; }, showInputBox: async ({ title, value }) => { prompts.push(title); return title.startsWith("Saved material") ? (intent === null ? undefined : intent) : value || "/tools/input"; },
      showWarningMessage: async (_message, _options, accept) => consent ? (accept === "Apply these retention preferences" ? accept : "Run this exact material check") : undefined,
      showErrorMessage: message => { throw new Error(message); },
      withProgress: async (_options, work) => work({ report() {} }, { get isCancellationRequested() { return stopped; }, onCancellationRequested: callback => { cancel = callback; return disposable; } }) },
    ProgressLocation: { Notification: 1 }, DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2 }, Range: class {}, Diagnostic: class {}, Uri: { file: value => value } };
  const context = { workspaceState: { get: key => state.get(key), update: async (key, value) => state.set(key, value) } };
  const mocks = { "./materialDeliveryClient": { ...require("../materialDeliveryClient"), monitor: (...args) => require("../materialDeliveryClient").monitor(...args, 5) }, "./materialSnapshotClient": require("../materialSnapshotClient"), "./sourceNavigationClient": { verifiedSourceTarget: () => ({ path: "/pack/groovy/Test.groovy", text: "source" }) },
    "./materialChecksClient": { ...client, invokeMaterialCheck: async (_exe, _root, _session, action, value, confirmation) => {
      calls.push({ action, value, confirmation });
      if (action === "contexts") return { policy: { contexts: [{ id: "supersymmetry:material-authoring-gt-base", label: "GT base", qualification: "pending", excludedComposition: ["full pack"] }] } };
      if (action === "setup-status") return { state: setupState, failure: setupState === "stale" ? { message: "Runtime changed" } : null };
      if (action === "setup") { setupState = "ready"; return { state: "configured-not-run" }; }
      if (action === "prepare") return request;
      if (action === "execute") return execute();
      if (action === "delivery") return { format: require("../materialDeliveryClient").STATUS, attempt_id: attempt, request_id: requestId, diagnostic_id: early.id, detail_state: deliveryState() };
      if (action === "diagnostics") return early;
      if (action === "query") return query(value, confirmation);
      if (action === "show") return show(value);
      if (action === "retention") return retention(value, confirmation);
      return {};
    } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../materialChecks.js"), "utf8"), { module, require: name => mocks[name] });
  const flow = module.exports.createMaterialChecks(vscode, context, () => "/workbench", { diagnostics: { clear() {}, set: rows => markers.push(rows) }, details: async value => shown.push(value), generation: () => generation, results });
  return { storage: () => flow.storageSettings("/pack", session), run: baseline => flow.run("/pack", session, baseline), present: value => flow.present("/pack", session, value, generation), calls, shown, markers, prompts, selections, edit: () => { generation++; }, cancel: () => cancel(), stopReading: () => { stopped = true; } };
}
test("storage UI shows protections and requires exact disclosed policy consent", async () => {
  const status = { policy: { settings: { mode: "disabled", max_count: 10, max_bytes: 8589934592, min_age_days: 1, trash_days: 7, metadata_count: 128, active_contexts: [], known_stores: [] } }, allocated_bytes: 1024, notices: ["History can grow."] };
  for (const consent of [false, true]) {
    const h = harness({ consent, pick: choices => choices.find(row => row.action === "configure") || choices[0],
      retention: (value, confirmation) => value.operation === "status" ? status : confirmation ? { state: "configured" } : { proposal: { id: "exact-policy", disclosure: "Permanent expiry after seven days of recoverable trash." } } });
    await h.storage();
    assert.equal(h.calls.filter(row => row.confirmation === "exact-policy").length, consent ? 1 : 0);
    assert.ok(h.calls.every(row => row.action === "retention"));
  }
});
test("storage maintenance uses already enabled policy authority without another prompt", async () => {
  const h = harness({ consent: false, pick: choices => choices.find(row => row.action === "maintain"), retention: value => value.operation === "status"
    ? { policy: { settings: { mode: "finite" } }, allocated_bytes: 1024, notices: [] }
    : { state: "complete", allocated_bytes_unlinked: 4096 } });
  await h.storage(); assert.equal(h.shown[0].allocated_bytes_unlinked, 4096);
  assert.equal(h.calls[1].value.operation, "maintain"); assert.equal(h.prompts.length, 0);
});
test("material UI chooses context and exact saved input without any image call", async () => {
  const h = harness(); await h.run();
  assert.deepEqual(h.calls.map(row => row.action), ["contexts", "setup-status", "setup", "prepare", "execute"]);
  assert.equal(h.calls[2].value.programRoot, "."); assert.equal(h.calls[4].confirmation, requestId);
  assert.equal(h.calls[3].value.intent, "");
  assert.equal(h.calls[3].value.engineHome, undefined);
  assert.match(h.shown[0], /Developer intent: mismatch/); assert.match(h.shown[0], /No qualified material validity/);
});
test("saved material expectations are optional; selecting a file or cancelling stays explicit", async () => {
  const selected = harness({ intent: "checks/my-intent.json" }); await selected.run();
  assert.equal(selected.calls[3].value.intent, "checks/my-intent.json");
  const cancelled = harness({ intent: null }); await cancelled.run();
  assert.deepEqual(cancelled.calls.map(row => row.action), ["contexts", "setup-status"]);
});
test("declining material consent never executes", async () => {
  const h = harness({ consent: false }); await h.run();
  assert.deepEqual(h.calls.map(row => row.action), ["contexts", "setup-status", "setup", "prepare"]);
});
test("ordinary reruns reuse Core setup without five prompts or replaying path overrides", async () => {
  const h = harness(); await h.run(); const prompts = h.prompts.length; await h.run();
  assert.equal(prompts, 5); assert.equal(h.prompts.length, prompts);
  assert.equal(h.calls.filter(row => row.action === "setup").length, 1);
  assert.equal(h.calls.filter(row => row.action === "setup-status").length, 2);
  for (const row of h.calls.filter(row => row.action === "prepare")) {
    assert.equal(row.value.engineHome, undefined); assert.equal(row.value.programRoot, undefined);
  }
});
test("ready setup can be reused from another client, and stale setup never silently executes", async () => {
  const ready = harness({ setupState: "ready" }); await ready.run();
  assert.equal(ready.prompts.length, 0);
  assert.deepEqual(ready.calls.map(row => row.action), ["contexts", "setup-status", "prepare", "execute"]);
  const stale = harness({ setupState: "stale" }); await stale.run();
  assert.deepEqual(stale.calls.map(row => row.action), ["contexts", "setup-status"]);
});
test("retained comparisons select their context instead of silently taking the last run context", async () => {
  const h = harness(); await h.run(); await h.run(attempt);
  assert.equal(h.selections.filter(row => row.title.startsWith("Axiom material context")).length, 2);
  assert.equal(h.calls.filter(row => row.action === "prepare")[1].value.baseline, attempt);
});
test("editing during a native run suppresses markers without cancelling; cancellation uses the retained owner", async () => {
  let start, finish; const started = new Promise(resolve => { start = resolve; });
  const h = harness({ execute: () => { start(); return new Promise(resolve => { finish = resolve; }); } });
  const running = h.run(); await started; h.edit();
  assert.ok(!h.calls.some(row => row.action === "cancel")); h.cancel();
  const value = result(); value.sourceCurrent = true; value.findings = [{ side: "candidate", location: { start: { line: 1, column: 1 }, end: { line: 1, column: 1 }, path: "groovy/Test.groovy" }, severity: "error", message: "native exception" }];
  finish(value); await running;
  assert.equal(h.calls.filter(row => row.action === "cancel").length, 1);
  assert.ok(h.markers.every(rows => rows.length === 0));
});
test("only byte-current candidate findings decorate; dirty buffers and baseline findings do not", async () => {
  const value = result(); value.sourceCurrent = true;
  const finding = { id: "diagnostic-1", side: "candidate", severity: "error", message: "native error", location: { path: "groovy/Test.groovy", start: { line: 1, column: 1 }, end: { line: 1, column: 1 } } };
  value.findings = [finding, { ...finding, side: "baseline" }];
  const clean = harness(); await clean.present(value); assert.equal(clean.markers[0][0][1].length, 1);
  const dirty = harness({ dirty: true }); await dirty.present(value); assert.equal(dirty.markers[0].length, 0);
});

function snapshotView(name = "one") {
  const value = result(); Object.assign(value, { format: "workbench-material-check-view-v1", snapshot_id: `snapshot-${name}`, view_id: `view-${name}`,
    context_id: "supersymmetry:material-authoring-gt-base", findings_count: 3, native_outcome: "native-failed", coverage: "complete", detail_state: "not-loaded",
    sourceCurrent: true, findings: [], presentation: { finding_labels: {} },
    finding_query: { snapshot_id: `snapshot-${name}`, view_id: `view-${name}`, operation: "records", section_id: "findings" },
    finding_page: { state: "ready", complete: false, next_cursor: { offset: 1 }, payload: { records: [], offset: 0, total: 3 } } });
  return value;
}
function nextPage(offset) {
  return { state: "ready", complete: offset === 2, next_cursor: offset === 2 ? null : { offset: 2 },
    payload: { records: [{ value: { id: `finding-${offset}`, side: "candidate", severity: "error", message: "Throwing", pointer: "/original" } }], offset, total: 3 },
    presentation: { finding_labels: { [`finding-${offset}`]: "Original compiler message" } } };
}
test("snapshot UI progressively reads every requested page and uses original labels", async () => {
  const h = harness({ pick: rows => rows.find(row => row.type === "remaining") || rows[0], query: async (_attempt, q) => nextPage(q.cursor.offset) });
  const value = snapshotView(); await h.present(value);
  assert.deepEqual(h.calls.filter(row => row.action === "query").map(row => row.confirmation.cursor.offset), [1, 2]);
  assert.equal(value.findings.length, 2); assert.match(h.shown[0], /Original compiler message/);
  assert.equal(value.findings[0].message, "Throwing");
});
test("a late findings page cannot replace a newly selected view", async () => {
  let release, started; const waiting = new Promise(resolve => { started = resolve; });
  const h = harness({ pick: (rows, options) => rows.find(row => row.type === "remaining") || rows[0],
    query: () => { started(); return new Promise(resolve => { release = resolve; }); } });
  const old = snapshotView(); const pending = h.present(old); await waiting;
  const current = snapshotView("two"); current.finding_page.complete = true; await h.present(current);
  const count = h.shown.length; release(nextPage(1)); await pending;
  assert.equal(old.findings.length, 0); assert.equal(h.shown.length, count);
});
test("cancelling progressive reads keeps loaded evidence and does not start another page", async () => {
  let picked = false;
  const h = harness({ pick: rows => { if (!picked) { picked = true; return rows.find(row => row.type === "remaining"); } return rows[0]; },
    query: async () => { h.stopReading(); return nextPage(1); } });
  const value = snapshotView(); await h.present(value);
  assert.equal(h.calls.filter(row => row.action === "query").length, 1); assert.equal(value.findings.length, 1);
  assert.equal(value.finding_page.complete, false); assert.match(h.shown[0], /more/);
});
test("a fresh native run shows the previous completed snapshot as historical", async () => {
  const old = snapshotView(); old.finding_page.complete = true;
  const h = harness({ initialState: [["workbench.lastCompletedMaterial", { root: "/pack", session, context: old.context_id, attempt: "previous" }]],
    show: async () => old, execute: async () => { assert.match(h.shown[0], /Previous completed check.*historical/); return result(); } });
  await h.run(); assert.equal(h.calls.filter(row => row.action === "show")[0].value, "previous");
});


test("early diagnostics retain edit and cancellation guards while native finalization continues", async () => {
  for (const edited of [false, true]) {
    let finish, started, delivered;
    const nativeStarted = new Promise(resolve => { started = resolve; });
    const visible = new Promise(resolve => { delivered = resolve; });
    const early = snapshotView("early");
    Object.assign(early, { format: require("../materialDeliveryClient").VIEW, id: "check-diagnostics:sha256:" + "a".repeat(64),
      request_id: requestId, diagnostic_id: "check-diagnostics:sha256:" + "a".repeat(64), snapshot_id: null, next_offset: null,
      findings: [{ id: "diagnostic-1", side: "candidate", severity: "error", message: "original exception",
        location: { path: "groovy/Test.groovy", start: { line: 1, column: 1 }, end: { line: 1, column: 1 } } }] });
    const h = harness({ early, execute: () => { started(); return new Promise(resolve => { finish = resolve; }); },
      pick: (choices, options) => {
        if (options.title.startsWith("Axiom:")) { delivered(); return choices.find(row => row.type === "read"); }
        return choices[0];
      } });
    const running = h.run(); await nativeStarted; if (edited) h.edit(); await visible;
    assert.equal(h.calls.filter(row => row.action === "execute").length, 1);
    assert.equal(h.calls.filter(row => row.action === "diagnostics").length, 1);
    assert.equal(h.markers.at(-1).length, edited ? 0 : 1);
    h.cancel(); assert.equal(h.calls.filter(row => row.action === "cancel").length, 1);
    const marked = h.markers.length; finish(result()); await running;
    assert.equal(h.markers.length, marked, "final snapshot must not clear or replace the delivered view");
  }
});

test("a failed finalizer refreshes readiness while retaining delivered findings", async () => {
  let fail, visible, state = "preparing", updated;
  const shown = new Promise(resolve => { visible = resolve; });
  const early = snapshotView("early"); Object.assign(early, { format: require("../materialDeliveryClient").VIEW, id: "revision", request_id: requestId, diagnostic_id: "revision", next_offset: null });
  const h = harness({ early, deliveryState: () => state, results: { clear() {}, update: value => { updated = value; } },
    execute: () => new Promise((_, reject) => { fail = reject; }), pick: (rows, options) => { if (options.title.startsWith("Axiom:")) { visible(); return undefined; } return rows[0]; } });
  const running = h.run(); await shown; state = "interrupted"; fail(new Error("finalizer interrupted"));
  await assert.rejects(running, /finalizer interrupted/); assert.equal(updated, early); assert.equal(early.detail_state, "interrupted");
});
