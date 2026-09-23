"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const test = require("node:test");

const {
  invokeAtlasSearch,
  invokeExamples,
  validateAtlasSearch,
  validateAtlasContext,
  validateExamples,
} = require("../developerToolsClient");

test("Atlas root linkage resolves filesystem aliases and still rejects another directory", () => {
  const fs = require("node:fs");
  const os = require("node:os");
  const path = require("node:path");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "Atlas root é "));
  try {
    const graph = path.join(directory, "graph"); fs.mkdirSync(graph);
    const other = path.join(directory, "other"); fs.mkdirSync(other);
    const context = atlasSearch().context;
    context.root = fs.realpathSync.native(graph);
    assert.equal(validateAtlasContext(context, path.join(graph, "..", "graph") + path.sep + "."), context);
    assert.throws(() => validateAtlasContext(context, other), /requested root/);
    if (process.platform === "win32") assert.equal(validateAtlasContext(context, graph.toUpperCase()), context);
    else assert.throws(() => validateAtlasContext(context, graph.toUpperCase()), /requested root/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

function exampleCatalog(selection = { kind: "all", value: null }) {
  return {
    authority_boundary: {
      construction_authority: false,
      profile_action_authorized: false,
      profile_support_claimed: false,
      publication_authorized: false,
      read_only: true,
      release_qualified: false,
      source: "profile-owned-non-identity-bearing-examples",
    },
    count: 1,
    examples: [{
      example_key: "supersymmetry-recipe-change-copper-sulfate-solution",
      family: "recipe-change",
      record: { format: "workbench-blueprints-current-example-v1", request: { mutation: "add" } },
      runtime_evidence_state: "required-not-observed",
      source_path: "profiles/packs/supersymmetry/blueprints/examples/recipe-change-copper-sulfate-solution.json",
      source_sha256: "a".repeat(64),
      source_size: 1234,
    }],
    format: "workbench-developer-feature-example-catalog-v1",
    profile: "supersymmetry",
    schema_version: 1,
    selection,
    state: "available",
  };
}

function atlasSearch(query = "copper") {
  return {
    context: {
      capabilities: {
        consumers: false,
        duplicate_signatures: false,
        producers: false,
        reachability: false,
        recipe_search: false,
        source_mutations: false,
        source_occurrence_search: true,
        stoichiometry: false,
      },
      context_type: "source-only-checkout",
      format: "workbench-atlas-recipe-health-context-v1",
      root: "/home/dev/susy",
      schema_version: 1,
      search: {
        requires_semantic_key: false,
        selection_is_exact_source_occurrence: true,
      },
      source_file_count: 1,
      source_roots: ["groovy"],
    },
    evidence_gaps: [{
      code: "runtime-recipes-unavailable",
      message: "Source text does not establish the loaded recipe registry.",
    }],
    format: "workbench-atlas-recipe-health-search-v1",
    query,
    results: [{
      column: 9,
      kind: "source-occurrence",
      line: 12,
      selection_id: `source-text:${"a".repeat(64)}:groovy%2FProbe.groovy:42:47`,
      snippet: "Copper.setBaseProof(true)",
      source_path: "groovy/Probe.groovy",
    }],
    schema_version: 1,
    truncated: false,
  };
}

test("example catalog keeps its non-authorizing owner boundary and opaque record", () => {
  const result = validateExamples(exampleCatalog(), null);
  assert.equal(result.examples[0].record.request.mutation, "add");
  assert.equal(result.authority_boundary.construction_authority, false);

  const authorizing = exampleCatalog();
  authorizing.authority_boundary.profile_action_authorized = true;
  assert.throws(() => validateExamples(authorizing), /became authorizing/);

  const drift = exampleCatalog();
  drift.examples[0].copied_policy = true;
  assert.throws(() => validateExamples(drift), /fields changed/);

  const wrongSelection = exampleCatalog({ kind: "family", value: "recipe-change" });
  assert.throws(() => validateExamples(wrongSelection, null), /requested all selection/);
});

test("Atlas search preserves source-only gaps and rejects same-version shape drift", () => {
  const result = validateAtlasSearch(atlasSearch(), "copper", 50, "/home/dev/susy");
  assert.equal(result.results[0].kind, "source-occurrence");
  assert.equal(result.evidence_gaps[0].code, "runtime-recipes-unavailable");

  const drift = atlasSearch();
  drift.results[0].runtime_recipe = true;
  assert.throws(() => validateAtlasSearch(drift, "copper", 50, "/home/dev/susy"), /fields changed/);
  assert.throws(() => validateAtlasSearch(atlasSearch(), "tin", 50, "/home/dev/susy"), /identity changed/);

  const wrongRoot = atlasSearch();
  wrongRoot.context.root = "/home/dev/other";
  assert.throws(
    () => validateAtlasSearch(wrongRoot, "copper", 50, "/home/dev/susy"),
    /requested root/,
  );

  const overclaim = atlasSearch();
  overclaim.context.capabilities.recipe_search = true;
  assert.throws(
    () => validateAtlasSearch(overclaim, "copper", 50, "/home/dev/susy"),
    /overclaim/,
  );
});

test("Atlas graph search accepts bounded evidence rows", () => {
  const graph = atlasSearch();
  graph.context = {
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
    root: "/evidence/graph",
    schema_version: 1,
    scope: { physical_side: "client" },
    search: { requires_semantic_key: false, selection_is_exact_node_id: true },
    summary: { node_count: 1 },
  };
  delete graph.evidence_gaps;
  graph.results = [{
    evidence: [{ adapter_id: "forge-fluids", record_ordinal: 1754 }],
    kind: "final-machine-recipe",
    properties: { recipe_map: "mixer" },
    selection_id: "recipe:fixture",
    semantic_key: "mixer|fixture",
  }];
  assert.equal(
    validateAtlasSearch(graph, "copper", 50, "/evidence/graph").results[0].evidence.length,
    1,
  );
  graph.results[0].evidence = { adapter_id: "forged" };
  assert.throws(
    () => validateAtlasSearch(graph, "copper", 50, "/evidence/graph"),
    /evidence is invalid/,
  );
});

test("example and Atlas invocations use exact no-shell routes and map WSL roots", async () => {
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      if (arguments_.includes("examples")) {
        callback(null, JSON.stringify(exampleCatalog({
          kind: "example-key",
          value: "supersymmetry-recipe-change-copper-sulfate-solution",
        })), "");
      } else {
        callback(null, JSON.stringify(atlasSearch()), "");
      }
    };
    const executable = "\\\\wsl.localhost\\Ubuntu\\home\\dev\\workbench\\workbench";
    const options = {
      platform: "win32",
      environment: { SystemRoot: "C:\\Windows", PATH: "C:\\Windows\\System32" },
    };
    await invokeExamples(
      executable,
      "supersymmetry-recipe-change-copper-sulfate-solution",
      options,
    );
    await invokeAtlasSearch(
      executable,
      "\\\\wsl.localhost\\Ubuntu\\home\\dev\\susy",
      "copper",
      50,
      options,
    );
    assert.deepEqual(calls[0].arguments_.slice(-4), [
      "feature", "examples", "supersymmetry-recipe-change-copper-sulfate-solution", "--json",
    ]);
    const atlas = calls[1].arguments_;
    const index = atlas.indexOf("atlas");
    assert.deepEqual(atlas.slice(index, index + 6), [
      "atlas", "recipes", "search", "/home/dev/susy", "copper", "--limit",
    ]);
    assert.equal(calls.every((call) => call.options.shell === false), true);
  } finally {
    childProcess.execFile = original;
  }
});
