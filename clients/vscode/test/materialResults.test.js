"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { createMaterialResults, outcome } = require("../materialResults"), { GROUPS } = require("../materialDeliveryClient");
function fixture() {
  let provider, edit; const commands = new Map(), view = { selection: [], dispose() {} };
  const vscode = { EventEmitter: class { event() {} fire() {} dispose() {} }, TreeItem: class { constructor(label) { this.label = label; } }, TreeItemCollapsibleState: { None: 0, Collapsed: 1 }, ThemeIcon: class {},
    window: { createTreeView: (_, options) => { provider = options.treeDataProvider; return view; }, showWarningMessage: async text => { view.warning = text; } },
    commands: { registerCommand: (id, fn) => { commands.set(id, fn); return { dispose() {} }; }, executeCommand: async () => {} }, workspace: { onDidChangeTextDocument: fn => { edit = fn; return { dispose() {} }; } } };
  const subscriptions = [], flow = createMaterialResults(vscode, { subscriptions });
  return { flow, view, provider: () => provider, command: (name, node) => commands.get("workbench.axiomResults." + name)(node), edit: () => edit(), dispose: () => subscriptions.forEach(d => d.dispose()) };
}
const finding = (id, location = null) => ({ id, location, severity: "error", side: "candidate", message: "Original native message" });
function result(id = "revision") { return { diagnostic_id: id, request_id: id, native_outcome: "native-failed", coverage: "complete", detail_state: "preparing", findings_count: 201,
  finding_counts: Object.fromEntries(GROUPS.map(group => [group, group === "error-unlocated" ? 201 : 0])), findings: [], presentation: { finding_labels: {} } }; }
test("unlocated errors remain browsable beside source, with global counts and bounded next pages", async () => {
  const f = fixture(), value = result(); let opened, reads = [], annotated;
  await f.flow.show(value, { fresh: () => true, read: async (group, offset) => { reads.push([group, offset]); return { ...value, findings: [finding("d" + offset)], next_offset: offset ? null : 1, sourceCurrent: true }; }, annotations: (rows, fresh) => { annotated = [rows, fresh]; }, open: (...args) => { opened = args; } });
  assert.match(f.view.message, /201 errors/); assert.match(f.view.message, /Preparing recipe details/);
  const [group] = await f.provider().getChildren(); const [row] = await f.provider().getChildren(group);
  await f.command("open", row); assert.equal(opened[1].id, "d0"); assert.equal((await f.provider().getChildren(group)).length, 1);
  await f.command("next", group); assert.deepEqual(reads, [["error-unlocated", 0], ["error-unlocated", 1]]); assert.equal(annotated[0].length, 2);
  value.detail_state = "ready"; f.flow.update(value); assert.match(f.view.message, /Recipe details ready/);
  f.edit(); await f.command("open", row); assert.equal(opened[2], false); assert.match(f.view.message, /Source changed/);
  f.dispose();
});
test("late group reads cannot replace a new run or decorate stale source", async () => {
  const f = fixture(); let release, marked = 0;
  await f.flow.show(result(), { fresh: () => true, read: () => new Promise(resolve => { release = resolve; }), annotations: () => { marked++; } });
  const [old] = await f.provider().getChildren(); const reading = f.provider().getChildren(old);
  await f.flow.show(result("new"), { fresh: () => true });
  release({ ...result(), findings: [finding("old")], next_offset: null, sourceCurrent: true });
  assert.deepEqual(await reading, []); assert.equal(marked, 0);
  let fresh;
  await f.flow.show(result("stale"), { fresh: () => true, read: async () => ({ ...result("stale"), findings: [finding("stale")], next_offset: null, sourceCurrent: false }), annotations: (_, value) => { fresh = value; } });
  await f.provider().getChildren((await f.provider().getChildren())[0]); assert.equal(fresh, false); assert.match(f.view.message, /Source changed/);
  f.dispose();
});

test("source-error headline uses the original native status separately from initialization outcome", () => {
  assert.equal(outcome({ native_outcome: "native-failed", native_status: "native-failed", native: { status: "source-error" }, coverage: "incomplete" }), "Initialization stopped at a script error");
});
