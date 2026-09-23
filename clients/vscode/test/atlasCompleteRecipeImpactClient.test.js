"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const childProcess = require("node:child_process");
const test = require("node:test");
const fixture = require("./fixtures/atlas-complete-impact-v2.json");
const { validateCompleteImpactReport: validate, invokeCompleteAtlasImpact, rawCompleteReport } = require("../atlasCompleteRecipeImpactClient");
const { validateImpactReport, validateImpactSearchLink } = require("../atlasRecipeImpactClient");
const { AtlasImpactDocuments, AtlasImpactTreeProvider, impactCautions } = require("../atlasRecipeImpactTree");

function report() { return structuredClone(fixture); }
function expected(value) { return { root: value.context.root, selectionId: value.selection.selection_id }; }
function search(value) { return { format: "workbench-atlas-recipe-health-search-v1", schema_version: 1,
  context: value.context, query: "mixer", results: [value.selection], truncated: false }; }
function fakeApi() {
  return {
    EventEmitter: class { event() {} fire() {} dispose() {} },
    TreeItem: class { constructor(label, state) { this.label = label; this.collapsibleState = state; } },
    ThemeIcon: class { constructor(id) { this.id = id; } },
    TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
    Uri: { from: parts => ({ ...parts, toString: () => parts.path }) },
  };
}

test("owner-generated complete report preserves incomplete evidence and unknown cycle viability", () => {
  const value = report();
  assert.deepEqual(validate(value, expected(value)), value);
  validateImpactSearchLink(value, search(value), value.selection);
  assert.throws(() => validateImpactReport(value, { ...expected(value), maxDepth: 4, maxNodes: 500 }));
  const cautions = impactCautions(value);
  assert.equal(cautions.state, "Observed finite exploration complete");
  assert.equal(cautions.warning, true);
  assert.match(cautions.text, /evidence incomplete.*1 unknown.*1 alternative dependency component.*viability unknown/);
  const provider = new AtlasImpactTreeProvider(fakeApi(), null); provider.setReport(value);
  const section = provider.getChildren().find(item => item.section === "summary");
  assert.deepEqual(provider.getChildren(section).map(item => item.key), ["selection", "scenario", "analysis_model", "exploration", "evidence_completeness", "summary", "context"]);
});

test("complete parser refuses shape, completeness, witness, identity and summary drift", () => {
  const mutations = [
    value => { value.bounds = {}; },
    value => { value.schema_version = 1; },
    value => { value.exploration.status = "cancelled"; },
    value => { value.exploration.graph_node_count++; },
    value => { value.summary.truncated = true; },
    value => { value.frontiers = [{ kind: "node-bound" }]; },
    value => { value.analysis_model.claim_boundary = "proved safe"; },
    value => { value.evidence_completeness.status = "complete-within-declared-model"; },
    value => { value.summary.alternative_dependency_component_count = 0; },
    value => { value.propagation.alternative_dependency_components[0].component_id += "changed"; },
    value => { value.propagation.alternative_dependency_components[0].witness.arcs[0].source_id = "other"; },
    value => { value.propagation.alternative_dependency_components[0].witness.node_ids[0] = "other"; },
    value => { value.propagation.alternative_dependency_components[0].viability_effect = "safe"; },
    value => { value.direct.outputs[0].producer_portfolio.evidence_complete = false; },
    value => { value.direct.outputs[0].downstream_consumers_truncated = true; },
    value => { value.selection.properties.eut = JSON.parse("9007199254740993"); },
  ];
  for (const mutate of mutations) { const value = report(); mutate(value); assert.throws(() => validate(value, expected(value)), /Atlas/); }
  const value = report(); const prior = search(report()); prior.context.graph_set_id += "changed";
  assert.throws(() => validateImpactSearchLink(validate(value, expected(value)), prior, prior.results[0]), /graph context changed/);
});

test("inactive selected recipe with no active alternatives is not a sole active producer", () => {
  const value = report(); value.selection.properties.lookup_active = false;
  const portfolio = value.direct.outputs[0].producer_portfolio; portfolio.alternative_producers = [];
  portfolio.status = "no-observed-active-producer";
  assert.equal(validate(value, expected(value)), value);
  portfolio.status = "sole-observed-finite-producer";
  assert.throws(() => validate(value, expected(value)), /lookup state/);
});

test("large complete reports retain every SCC member and unknown with lazy native children", () => {
  const value = report(); const component = value.propagation.alternative_dependency_components[0];
  component.member_node_ids.push(...Array.from({ length: 20001 }, (_, index) => `synthetic:node:${String(index).padStart(6, "0")}`));
  component.member_node_ids.sort();
  component.component_id = `workbench-atlas-dependency-component-v2:sha256:${crypto.createHash("sha256").update(JSON.stringify(component.member_node_ids)).digest("hex")}`;
  value.exploration.graph_node_count = value.context.summary.node_count = 30000;
  value.exploration.visited_node_count = 25000;
  value.unknowns = Array.from({ length: 12001 }, (_, index) => ({ code: "lookup-state-unavailable", message: "Observed activity is unknown", subject_id: `synthetic:recipe:${index}` }));
  assert.equal(validate(value, expected(value)), value);
  const api = fakeApi(); const docs = new AtlasImpactDocuments(api); const provider = new AtlasImpactTreeProvider(api, docs); provider.setReport(value);
  const members = { type: "json", path: "propagation.components[0].member_node_ids", value: component.member_node_ids };
  function leaves(parent) {
    const children = provider.getChildren(parent); assert.ok(children.length <= 200);
    return children.flatMap(child => child.type === "range" ? leaves(child) : [child.value]);
  }
  assert.deepEqual(leaves(members), component.member_node_ids);
  const unknowns = provider.getChildren().find(row => row.section === "unknowns");
  assert.deepEqual(leaves(unknowns), value.unknowns);
  const uri = docs.report(value); assert.deepEqual(JSON.parse(docs.provideTextDocumentContent(uri)), value);
});

test("complete transport omits bounds, lifts only complete limits and preserves raw bytes", async () => {
  const original = childProcess.execFile; const value = report(); const raw = JSON.stringify(value, null, 1) + "\n";
  const controller = new AbortController(); const requests = [];
  try {
    childProcess.execFile = (executable, args, options, callback) => {
      requests.push({ executable, args, options }); queueMicrotask(() => callback(null, raw, ""));
      return { pid: -1, exitCode: null };
    };
    const actual = await invokeCompleteAtlasImpact("/opt/workbench", "/graph", value.selection.selection_id, {
      signal: controller.signal, priorSearch: search(value), priorSelection: value.selection,
    });
    assert.deepEqual(actual, value); assert.equal(rawCompleteReport(actual), raw);
    assert.deepEqual(requests[0].args, ["atlas", "recipes", "impact", "/graph", value.selection.selection_id, "--exploration", "complete-finite", "--json"]);
    assert.equal(requests[0].options.timeout, 0); assert.equal(requests[0].options.maxBuffer, Infinity);
    assert.equal(requests[0].options.signal, controller.signal);
    const docs = new AtlasImpactDocuments(fakeApi()); assert.equal(docs.provideTextDocumentContent(docs.report(actual)), raw);
    controller.abort();
    await assert.rejects(invokeCompleteAtlasImpact("/opt/workbench", "/graph", value.selection.selection_id, { signal: controller.signal }), /abort/i);
    assert.equal(requests.length, 1);
  } finally { childProcess.execFile = original; }
});

module.exports = { report, search };

test("owner inactive and unknown activity reports preserve the evidence distinction", () => {
  for (const name of ["selected-inactive", "selected-unknown"]) {
    const value = structuredClone(require(`./fixtures/atlas-complete-${name}-v2.json`));
    assert.equal(validate(value, expected(value)), value);
    const portfolio = value.direct.outputs[0].producer_portfolio;
    assert.equal(portfolio.status, name === "selected-inactive" ? "no-observed-active-producer" : "producer-evidence-incomplete");
    assert.equal(portfolio.evidence_complete, name === "selected-inactive");
    assert.equal(value.summary.sole_observed_finite_producer_output_count, 0);
  }
});

test("owner quest component preserves its closed witness and zero-distance return to the root", () => {
  const value = structuredClone(require("./fixtures/atlas-complete-quest-cycle-v2.json"));
  assert.equal(validate(value, expected(value)), value);
  const quest = value.progression_signals.quest_signals;
  assert.equal(quest.structural_prerequisite_dependents[0].depth, 0);
  assert.equal(quest.prerequisite_cycle_components[0].member_node_ids.length, 6);
  assert.equal(quest.prerequisite_cycle_components[0].witness.arcs.length, 6);
  assert.equal(impactCautions(value).questComponents, 1);
  quest.prerequisite_cycle_components[0].witness.arcs.pop();
  assert.throws(() => validate(value, expected(value)), /witness is not closed/);
});
