"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const test = require("node:test");

const {
  invokeAtlasImpact,
  validateImpactReport,
  validateImpactSearchLink,
} = require("../atlasRecipeImpactClient");

const RECIPE_ID = `workbench-categorical-node:sha256:${"1".repeat(64)}`;
const RESOURCE_ID = `workbench-categorical-node:sha256:${"2".repeat(64)}`;

function node(selectionId, kind, semanticKey, properties = {}) {
  return { evidence: [], kind, properties, selection_id: selectionId, semantic_key: semanticKey };
}

function edge(relation, target = undefined) {
  return {
    evidence: [],
    ...(target === undefined ? {} : { node: target }),
    properties: {},
    relation,
    semantic_key: `${relation}|fixture`,
  };
}

function graphContext(root = "/graph") {
  return {
    capabilities: {
      consumers: true,
      duplicate_signatures: true,
      producers: true,
      reachability: false,
      recipe_search: true,
      source_mutations: true,
      stoichiometry: false,
    },
    context_type: "categorical-graph-v2",
    format: "workbench-atlas-recipe-health-context-v1",
    graph_set_id: "workbench-categorical-graph-set-v2:fixture",
    recipe_count: 1,
    root,
    schema_version: 1,
    scope: { physical_side: "client" },
    search: { requires_semantic_key: false, selection_is_exact_node_id: true },
    summary: { node_count: 2 },
  };
}

function report(root = "/graph") {
  const recipe = node(RECIPE_ID, "gt-recipe", "mixer|selected|0", {
    duration: 40,
    eut: 30,
    hidden: false,
    lookup_active: true,
    recipe_map: "mixer",
  });
  const resource = node(RESOURCE_ID, "forge-fluid", "sole_output", { name: "sole_output" });
  return {
    analysis_model: {
      claim_boundary: "candidate dead paths inside observed finite recipe structure; not gameplay reachability",
      kind: "bounded-observed-finite-recipe-dependency-exposure",
      recipe_at_risk_rule: "at least one exact input selector accepts only resources already classified at risk",
      resource_at_risk_rule: "every observed finite recipe producer is an unavailable candidate",
    },
    bounds: { max_depth: 4, max_nodes: 500, visited_node_count: 2 },
    context: graphContext(root),
    direct: {
      inputs: [],
      outputs: [{
        downstream_consumers: [],
        downstream_consumers_truncated: false,
        output: edge("produces-gt-fluid", resource),
        producer_portfolio: {
          alternative_producers: [],
          status: "sole-observed-finite-producer",
          truncated: false,
          viability: "not-assessed",
        },
      }],
    },
    evidence_gaps: [
      "counterfactual-runtime-not-observed",
      "non-recipe-acquisition-not-assessed",
      "dynamic-recipes-not-executed",
      "stoichiometry-and-chance-not-assessed",
      "progression-reachability-not-proven",
      "task-execution-not-invoked",
      "pack-tier-policy-not-applied",
    ].map((code) => ({ code, message: `${code} evidence boundary` })),
    format: "workbench-atlas-recipe-impact-report-v1",
    frontiers: [],
    progression_signals: {
      energy_and_machine_signals: {
        interpretation: "numeric signals only; no pack tier inferred",
        machine_numeric_tiers_observed: [],
        machines_observed_using_recipe_map: [],
        observed_recipe_properties: {
          duration: 40,
          eut: 30,
          hidden: false,
          lookup_active: true,
          recipe_map: "mixer",
        },
        recipe_map: null,
        truncated: false,
      },
      quest_signals: {
        direct_resource_requirements: [],
        interpretation: "definition references, not player blockage",
        prerequisite_cycles: [],
        status: "no-observed-reference",
        structural_prerequisite_dependents: [],
      },
    },
    propagation: {
      alternative_dependency_cycle_signals: [],
      at_risk_recipes: [],
      at_risk_resources: [{
        all_observed_finite_producers: [RECIPE_ID],
        depth: 0,
        reason: "all-observed-finite-producers-are-unavailable-candidates",
        resource,
      }],
      status: "complete-within-model",
    },
    scenario: {
      change_interpretation: "replacement inputs, outputs, and runtime behavior require separate evidence",
      kind: "remove-exact-observed-recipe",
      selected_recipe_assumed_unavailable: true,
    },
    schema_version: 1,
    selection: recipe,
    summary: {
      alternative_dependency_cycle_signal_count: 0,
      at_risk_recipe_candidate_count: 0,
      at_risk_resource_candidate_count: 1,
      quest_requirement_exposure_count: 0,
      selected_output_count: 1,
      sole_observed_finite_producer_output_count: 1,
      structural_quest_dependent_count: 0,
      truncated: false,
    },
    unknowns: [],
  };
}

test("impact validation preserves candidates, removal scenario, and exact count bindings", () => {
  const value = validateImpactReport(report(), {
    maxDepth: 4,
    maxNodes: 500,
    root: "/graph",
    selectionId: RECIPE_ID,
  });
  assert.equal(value.scenario.kind, "remove-exact-observed-recipe");
  assert.equal(value.propagation.at_risk_resources.length, 1);
  assert.equal(value.analysis_model.claim_boundary.includes("not gameplay reachability"), true);
});

test("search linkage binds complete context and recipe properties, including evidence", () => {
  const value = report();
  const search = { context: structuredClone(value.context) };
  const selection = structuredClone(value.selection);
  assert.equal(validateImpactSearchLink(value, search, selection), value);
  assert.equal(validateImpactSearchLink(value, undefined, undefined), value);
  assert.throws(() => validateImpactSearchLink(value, search, undefined), /linkage/);
  const changedContext = structuredClone(search);
  changedContext.context.graph_set_id = `workbench-atlas-graph-set-v2:sha256:${"9".repeat(64)}`;
  assert.throws(() => validateImpactSearchLink(value, changedContext, selection), /context changed/);
  const changedRecipe = structuredClone(selection);
  changedRecipe.properties.lookup_active = false;
  assert.throws(() => validateImpactSearchLink(value, search, changedRecipe), /recipe changed/);
  changedRecipe.properties = selection.properties;
  changedRecipe.evidence = [{ adapter_id: "other" }];
  assert.throws(() => validateImpactSearchLink(value, search, changedRecipe), /recipe changed/);
});

test("impact validation fails closed on shape, bounds, summary, and claim drift", () => {
  const shape = report();
  shape.proven_broken = true;
  assert.throws(
    () => validateImpactReport(shape, { maxDepth: 4, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID }),
    /fields changed/,
  );
  const count = report();
  count.summary.at_risk_resource_candidate_count = 2;
  assert.throws(
    () => validateImpactReport(count, { maxDepth: 4, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID }),
    /summary/,
  );
  const claim = report();
  claim.analysis_model.claim_boundary = "proven progression failure";
  assert.throws(
    () => validateImpactReport(claim, { maxDepth: 4, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID }),
    /claim boundary/,
  );
  assert.throws(
    () => validateImpactReport(report(), { maxDepth: 5, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID }),
    /bounds do not match/,
  );
});

test("impact invocation uses the exact bounded no-shell WSL route", async () => {
  const original = childProcess.execFile;
  let observed;
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      observed = { executable, arguments_, options };
      callback(null, JSON.stringify(report("/graphs/atlas")), "");
    };
    const result = await invokeAtlasImpact(
      "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
      "\\\\wsl.localhost\\Ubuntu\\graphs\\atlas",
      RECIPE_ID,
      4,
      500,
      {
        platform: "win32",
        environment: { SystemRoot: "C:\\Windows", PATH: "C:\\Windows\\System32" },
      },
    );
    assert.equal(result.selection.selection_id, RECIPE_ID);
    const index = observed.arguments_.indexOf("atlas");
    assert.deepEqual(observed.arguments_.slice(index), [
      "atlas", "recipes", "impact", "/graphs/atlas", RECIPE_ID,
      "--max-depth", "4", "--max-nodes", "500", "--json",
    ]);
    assert.equal(observed.options.shell, false);
    assert.equal(observed.options.timeout, 20 * 60 * 1000);
  } finally {
    childProcess.execFile = original;
  }
});

test("native impact normalizes a relative graph before binding owner context", async () => {
  const original = childProcess.execFile;
  let observed;
  try {
    childProcess.execFile = (_executable, arguments_, _options, callback) => {
      observed = arguments_;
      callback(null, JSON.stringify(report("/workspace/graphs/atlas")), "");
    };
    await invokeAtlasImpact(
      "/opt/workbench",
      "graphs/atlas",
      RECIPE_ID,
      4,
      500,
      { cwd: "/workspace", platform: "linux", environment: { PATH: "/usr/bin" } },
    );
    assert.equal(observed[3], "/workspace/graphs/atlas");
  } finally {
    childProcess.execFile = original;
  }
});

module.exports = { RECIPE_ID, report };

test("one explicit graph projection limitation is preserved after the fixed evidence gaps", () => {
  const expected = { maxDepth: 4, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID };
  const value = report();
  value.evidence_gaps.push({ code: "graph-projection-limitations", message: "Chance and dynamic matching are not modeled." });
  assert.deepEqual(validateImpactReport(value, expected).evidence_gaps, value.evidence_gaps);
  const duplicate = structuredClone(value);
  duplicate.evidence_gaps.push(value.evidence_gaps.at(-1));
  assert.throws(() => validateImpactReport(duplicate, expected), /evidence gaps/);
  value.evidence_gaps.at(-1).code = "undocumented-gap";
  assert.throws(() => validateImpactReport(value, expected), /evidence gap order/);
  value.evidence_gaps.at(-1).code = "graph-projection-limitations";
  value.evidence_gaps.at(-1).message = "";
  assert.throws(() => validateImpactReport(value, expected), /message/);
});

test("Atlas search and impact reject parsed unsafe integers before presentation", () => {
  const { validateAtlasSearch } = require("../developerToolsClient");
  const expected = { maxDepth: 4, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID };
  for (const token of ["9007199254740993", "-9007199254740993", "1e309"]) {
    const value = report();
    value.selection.properties.tag = { type: 4, value: "UNSAFE_NUMBER" };
    const parsed = JSON.parse(JSON.stringify(value).replace('"UNSAFE_NUMBER"', token));
    const search = { format: "workbench-atlas-recipe-health-search-v1", schema_version: 1,
      context: parsed.context, query: "selected", results: [parsed.selection], truncated: false };
    assert.throws(() => validateImpactReport(parsed, expected), /Atlas numeric precision cannot be preserved/);
    assert.throws(() => validateAtlasSearch(search, "selected", 200, "/graph"), /Atlas numeric precision cannot be preserved/);
  }
  const precise = report();
  precise.selection.properties.tag = { value: Number.MAX_SAFE_INTEGER, fraction: 0.125 };
  assert.equal(validateImpactReport(precise, expected).selection.properties.tag.value, Number.MAX_SAFE_INTEGER);
  const nested = report();
  nested.direct.outputs[0].output.evidence = [{ nested: [Number.MAX_SAFE_INTEGER + 1] }];
  assert.throws(() => validateImpactReport(nested, expected), /numeric precision/);
});

test("frontier and unknown rows have an independent report bound, not the visited-node bound", () => {
  const expected = { maxDepth: 4, maxNodes: 500, root: "/graph", selectionId: RECIPE_ID };
  const value = report();
  value.frontiers = Array.from({ length: 501 }, (_, i) => ({ kind: "node-bound", phase: `fixture-${i}` }));
  value.unknowns = Array.from({ length: 501 }, (_, i) => ({ code: `fixture-${i}`, message: "An omitted edge or phase remains unknown." }));
  value.summary.truncated = true;
  const accepted = validateImpactReport(value, expected);
  assert.equal(accepted.frontiers.length, 501);
  assert.equal(accepted.unknowns.length, 501);
  for (const section of ["frontiers", "unknowns"]) {
    const excessive = structuredClone(value);
    excessive[section] = Array.from({ length: 10_001 }, () => value[section][0]);
    assert.throws(() => validateImpactReport(excessive, expected), /supported boundary/);
  }
});

test("bounded portfolio preserves absence of an active producer for an inactive selection", () => {
  const value = report(); value.selection.properties.lookup_active = false;
  value.direct.outputs[0].producer_portfolio.status = "no-observed-active-producer";
  value.summary.sole_observed_finite_producer_output_count = 0;
  assert.deepEqual(validateImpactReport(value, { root: "/graph", selectionId: RECIPE_ID, maxDepth: 4, maxNodes: 500 }), value);
});
