"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { loadCorpus } = require("./atlas-corpus-contract");

test("installed corpus loader binds an explicit complete record without legacy bounds", async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "workbench-atlas-corpus-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const impact = structuredClone(require("./fixtures/atlas-complete-impact-v2.json"));
  const search = { format: "workbench-atlas-recipe-health-search-v1", schema_version: 1,
    context: impact.context, query: "mixer", results: [impact.selection], truncated: false };
  const searchPath = path.join(root, "search.json"), impactPath = path.join(root, "impact.json"), manifestPath = path.join(root, "manifest.json");
  const otherImpactPath = path.join(root, "other-impact.json");
  await fs.writeFile(searchPath, JSON.stringify(search)); await fs.writeFile(impactPath, JSON.stringify(impact));
  await fs.writeFile(otherImpactPath, JSON.stringify(impact));
  const manifest = { format: "workbench-atlas-client-corpus-v1", executable: process.execPath, workspace: root,
    cases: [{ id: "complete", graph: "/graph", query: "mixer", selection_id: impact.selection.selection_id,
      search_record: searchPath, impact_record: impactPath, exploration: "complete-finite", highlight: true }] };
  manifest.cases.push({ ...manifest.cases[0], id: "nonhighlight", impact_record: otherImpactPath, highlight: false });
  await fs.writeFile(manifestPath, JSON.stringify(manifest));
  const result = await loadCorpus(manifestPath);
  assert.equal(result.cases.length, 2); assert.equal(result.highlights.length, 1);
  assert.deepEqual(result.highlights[0].impact, impact);
  for (const row of result.cases) {
    assert.equal(row.graph_set_id, impact.context.graph_set_id);
    for (const field of ["impact", "selection", "search"]) assert.equal(Object.hasOwn(row, field), false, `compact receipt retained ${field}`);
  }
  assert.equal(result.cases[0].cautions.state, "Observed finite exploration complete");
  const invalid = structuredClone(impact); invalid.exploration.status = "failed";
  await fs.writeFile(otherImpactPath, JSON.stringify(invalid));
  await assert.rejects(loadCorpus(manifestPath), /exploration completeness changed/);
  await fs.writeFile(otherImpactPath, JSON.stringify(impact));
  manifest.cases[0].exploration = "automatic"; await fs.writeFile(manifestPath, JSON.stringify(manifest));
  await assert.rejects(loadCorpus(manifestPath), /unknown corpus exploration mode/);
  delete manifest.cases[0].exploration; await fs.writeFile(manifestPath, JSON.stringify(manifest));
  await assert.rejects(loadCorpus(manifestPath), /Atlas/);
});
