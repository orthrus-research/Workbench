"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  AtlasImpactDocuments,
  AtlasImpactTreeProvider,
  childrenForJson,
  impactCautions,
} = require("../atlasRecipeImpactTree");

function fakeVscode() {
  const calls = [];
  class EventEmitter {
    constructor() { this.event = () => ({ dispose() {} }); }
    fire() {}
    dispose() {}
  }
  class TreeItem {
    constructor(label, state) { this.label = label; this.collapsibleState = state; }
  }
  class ThemeIcon { constructor(id) { this.id = id; } }
  const api = {
    EventEmitter,
    ThemeIcon,
    TreeItem,
    TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
    Uri: {
      from(parts) {
        return { ...parts, toString() { return `${parts.scheme}:${parts.path}`; } };
      },
    },
    workspace: { async openTextDocument(uri) { return { uri }; } },
    window: { async showTextDocument(document, options) { calls.push({ document, options }); } },
  };
  return { api, calls };
}

function report() {
  return {
    analysis_model: { claim_boundary: "candidates are not gameplay reachability" },
    bounds: { max_depth: 4, max_nodes: 500, visited_node_count: 8 },
    context: { context_type: "categorical-graph-v2", root: "/graph" },
    direct: { inputs: [], outputs: [{ output: { relation: "produces-gt-fluid" } }] },
    evidence_gaps: [{ code: "counterfactual-runtime-not-observed", message: "Runtime not invoked." }],
    format: "workbench-atlas-recipe-impact-report-v1",
    frontiers: [{ kind: "depth-bound", phase: "risk-propagation" }],
    progression_signals: { quest_signals: { direct_resource_requirements: [] } },
    propagation: { at_risk_recipes: [], at_risk_resources: [{ resource: { semantic_key: "radon" } }] },
    scenario: { kind: "remove-exact-observed-recipe" },
    schema_version: 1,
    selection: { kind: "gt-recipe", selection_id: "gt-recipe:fixture", semantic_key: "mixer|fixture" },
    summary: {
      at_risk_recipe_candidate_count: 0,
      at_risk_resource_candidate_count: 1,
      quest_requirement_exposure_count: 0,
      truncated: true,
    },
    unknowns: [],
  };
}

test("impact tree remains inert until a validated report is explicitly supplied", () => {
  const { api } = fakeVscode();
  const documents = new AtlasImpactDocuments(api);
  const provider = new AtlasImpactTreeProvider(api, documents);
  const initial = provider.getChildren();
  assert.equal(initial.length, 1);
  assert.equal(provider.getTreeItem(initial[0]).command.command, "workbench.atlas.analyzeRecipeImpact");

  provider.setReport(report());
  const sections = provider.getChildren();
  assert.equal(sections[0].type, "cautions");
  assert.deepEqual(sections.slice(1).map((section) => section.section), [
    "summary", "direct", "propagation", "progression_signals",
    "frontiers", "unknowns", "evidence_gaps",
  ]);
  const visible = sections.map((section) => {
    const item = provider.getTreeItem(section);
    return `${item.label} ${item.description || ""}`;
  }).join("\n");
  assert.match(visible, /Propagation candidates/);
  assert.doesNotMatch(visible, /broken|unreachable/i);
});

test("summary keeps the scenario, claim boundary, bounds, and graph context visible", () => {
  const { api } = fakeVscode();
  const provider = new AtlasImpactTreeProvider(api, new AtlasImpactDocuments(api));
  provider.setReport(report());
  const summary = provider.getChildren().find(row => row.section === "summary");
  assert.deepEqual(provider.getChildren(summary).map((item) => item.key), [
    "selection", "scenario", "analysis_model", "bounds", "summary", "context",
  ]);
  assert.equal(childrenForJson({ status: "candidate" }, "fixture")[0].path, "fixture.status");
});

test("cycles, unknowns and inactive alternatives cannot appear as completed analysis", () => {
  const value = report();
  value.frontiers = [];
  value.summary.truncated = false;
  value.unknowns = [{ code: "unresolved-producer", message: "Producer viability unknown" }];
  value.propagation.alternative_dependency_cycle_signals = [{ viability_effect: "unknown" }];
  const alternative = { recipe: { selection_id: "recipe:inactive", properties: { lookup_active: false } } };
  value.direct.outputs[0].producer_portfolio = { alternative_producers: [alternative, alternative] };
  const cautions = impactCautions(value);
  assert.equal(cautions.state, "Unresolved within bounds");
  assert.equal(cautions.warning, true);
  assert.equal(cautions.cycles, 1);
  assert.equal(cautions.unknowns, 1);
  assert.equal(cautions.inactiveAlternatives, 1);
  const { api } = fakeVscode();
  const provider = new AtlasImpactTreeProvider(api, new AtlasImpactDocuments(api));
  provider.setReport(value);
  const visible = provider.getTreeItem(provider.getChildren()[0]);
  assert.match(visible.description, /1 unknown.*1 alternative dependency cycle.*1 lookup-inactive/);
  assert.doesNotMatch(visible.label, /complete/i);
  assert.match(visible.tooltip, /do not establish viability/);
  value.frontiers.push({ kind: "node-bound" });
  assert.equal(impactCautions(value).state, "Bounded frontiers remain");
});

test("complete owner JSON opens through a read-only virtual document", async () => {
  const { api, calls } = fakeVscode();
  const documents = new AtlasImpactDocuments(api);
  const provider = new AtlasImpactTreeProvider(api, documents);
  provider.setReport(report());
  await provider.openReport();
  assert.equal(calls.length, 1);
  const content = documents.provideTextDocumentContent(calls[0].document.uri);
  assert.equal(JSON.parse(content).scenario.kind, "remove-exact-observed-recipe");
  documents.release(calls[0].document.uri);
  assert.throws(() => documents.provideTextDocumentContent(calls[0].document.uri), /no longer retained/);
});
