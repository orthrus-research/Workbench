"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fixture = require("../../testing/fixtures/atlas-recipe-browse-v1.json");
const { validateBrowse, createAtlasRecipeBrowser, nodeLabel, relationshipLabel } = require("../atlasRecipeBrowser");

const request = { root: "/graph", graph: fixture.context.graph_set_id, selection: fixture.selection.selection_id, offset: 0 };
test("browse preserves quantities and rejects drift, broken links, rounding and hidden pages", () => {
  assert.equal(validateBrowse(structuredClone(fixture), request).selection.properties.duration, 200);
  for (const change of [
    row => { row.context.graph_set_id += "changed"; },
    row => { row.selection.selection_id = "other"; },
    row => { row.links[0].node.selection_id = "other"; },
    row => { row.page.total += 1; },
    row => { row.page.next_offset = 50; },
    row => { row.selection.properties.duration = Number.MAX_SAFE_INTEGER + 1; },
  ]) {
    const value = structuredClone(fixture); change(value);
    assert.throws(() => validateBrowse(value, request));
  }
});

test("search carries the exact selection, reads values and reopens with graph identity", async () => {
  let saved, step = 0;
  const displayed = [], requests = [];
  const context = { workspaceState: { get: () => saved, update: async (_key, value) => { saved = value; } } };
  const ui = {
    ProgressLocation: { Notification: 1 },
    window: {
      showQuickPick: async choices => [choices[0], choices[0], choices[0], undefined][step++],
      showOpenDialog: async () => [{ fsPath: "/graph" }], showInputBox: async () => "final",
      withProgress: async (_options, call) => call(), showInformationMessage: async () => {},
    },
  };
  const api = {
    search: async () => ({ context: fixture.context, results: [fixture.selection], truncated: false }),
    browse: async (...args) => { requests.push(args); return structuredClone(fixture); },
  };
  const browser = createAtlasRecipeBrowser(ui, context, () => "workbench", async value => displayed.push(value), api);
  await browser.open("/workspace");
  assert.equal(displayed.length, 1);
  assert.equal(saved.selected, fixture.selection.selection_id);
  assert.equal(saved.graph, fixture.context.graph_set_id);
  assert.equal(requests[0][3], fixture.selection.selection_id);
  let reopening = true;
  ui.window.showQuickPick = async choices => { if (reopening) { reopening = false; return choices.find(row => row.kind === "reopen"); } return undefined; };
  await browser.open("/workspace");
  assert.equal(requests.at(-1)[2], saved.graph);
});

test("search drift never overwrites the previous saved location", async () => {
  const prior = { root: "/previous", graph: "old", selected: "old" };
  let saved = prior;
  const context = { workspaceState: { get: () => saved, update: async (_key, value) => { saved = value; } } };
  const ui = { ProgressLocation: { Notification: 1 }, window: {
    showQuickPick: async choices => choices[0], showOpenDialog: async () => [{ fsPath: "/graph" }],
    showInputBox: async () => "final", withProgress: async (_options, call) => call(),
  } };
  const changed = structuredClone(fixture); changed.selection.properties.duration++;
  const browser = createAtlasRecipeBrowser(ui, context, () => "workbench", async () => {}, {
    search: async () => ({ context: fixture.context, results: [fixture.selection] }), browse: async () => changed,
  });
  await assert.rejects(browser.open("/workspace"), /changed since search/);
  assert.equal(saved, prior);
});

test("labels expose observed names and values without changing evidence or claiming consumption", () => {
  const item = { kind: "item-variant", semantic_key: "gregtech:meta_item_1|627|627", properties: {
    observed_item_names: [{ name: "circuit.microprocessor" }, { name: "circuit.microprocessor" }],
  } };
  const before = structuredClone(item);
  assert.equal(nodeLabel(item), "circuit.microprocessor");
  assert.deepEqual(item, before);
  assert.equal(nodeLabel(fixture.selection), "final · 200 ticks · 120 EU/t");
  assert.equal(nodeLabel({ kind: "gt-recipe-input-selector", properties: {
    ordinal: 0, amount: 1, non_consumable: true, acceptance_complete: false,
  } }), "Input 1 · amount 1 · reusable · matching incomplete");
  assert.equal(relationshipLabel({ direction: "incoming", relationship: { relation: "accepts-gt-item-alternative", properties: { amount: 1, non_consumable: true } } }),
    "Accepted by input · amount 1 · reusable");
  assert.equal(relationshipLabel({ direction: "incoming", relationship: { relation: "produces-gt-item", properties: { amount: 8, chanced: true } } }),
    "Produced by recipe · amount 8 · chance output; inspect values");
});

test("cancel arriving with a verified session closes it and preserves the saved selection", async () => {
  const prior = { root: "/old", graph: "old", selected: "old" };
  let saved = prior, cancel, closed = 0, disposed = 0;
  const ui = { ProgressLocation: { Notification: 1 }, window: {
    showQuickPick: async choices => choices[0], showOpenDialog: async () => [{ fsPath: "/graph" }], showInputBox: async () => "final",
    withProgress: async (options, call) => {
      assert.equal(options.cancellable, true);
      return call({}, { onCancellationRequested: listener => { cancel = listener; return { dispose: () => disposed++ }; } });
    },
  } };
  const browser = createAtlasRecipeBrowser(ui, { workspaceState: { get: () => saved, update: async (_key, value) => { saved = value; } } },
    () => "workbench", async () => {}, { openSession: async (_exe, _root, options) => {
      cancel(); assert.equal(options.signal.aborted, true);
      return { close: () => closed++ };
    } });
  assert.equal(await browser.open("/workspace"), undefined);
  assert.equal(closed, 1); assert.equal(disposed, 1); assert.equal(saved, prior);
});

test("clearing an obsolete saved location launches no process", async () => {
  let saved = { root: "/missing", graph: "changed", selected: "old" };
  const ui = { window: { showQuickPick: async choices => choices.find(row => row.kind === "forget") } };
  const browser = createAtlasRecipeBrowser(ui, { workspaceState: { get: () => saved, update: async (_key, value) => { saved = value; } } },
    () => { throw new Error("must not launch Core"); }, async () => {});
  await browser.open("/workspace");
  assert.equal(saved, undefined);
});
