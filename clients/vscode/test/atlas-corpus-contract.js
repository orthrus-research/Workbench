"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");

/** Read exact installed-CLI records; no graph inference or substitute transport. */
async function loadCorpus(manifestPath, clientRoot = path.resolve(__dirname, "..")) {
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  assert.equal(manifest.format, "workbench-atlas-client-corpus-v1");
  for (const key of ["executable", "workspace"]) {
    assert.ok(path.isAbsolute(manifest[key]), `corpus ${key} must be absolute`);
    await fs.access(manifest[key]);
  }
  assert.ok(Array.isArray(manifest.cases) && manifest.cases.length > 0);
  const { validateAtlasSearch } = require(path.join(clientRoot, "developerToolsClient"));
  const { validateImpactReport, validateImpactSearchLink } = require(path.join(clientRoot, "atlasRecipeImpactClient"));
  const { validateCompleteImpactReport } = require(path.join(clientRoot, "atlasCompleteRecipeImpactClient"));
  const { impactCautions } = require(path.join(clientRoot, "atlasRecipeImpactTree"));
  const identities = new Set();
  const cases = [];
  const highlights = [];
  for (const row of manifest.cases) {
    assert.ok(typeof row.id === "string" && row.id && !identities.has(row.id), "unique corpus case IDs required");
    identities.add(row.id);
    for (const key of ["graph", "search_record", "impact_record"]) {
      assert.ok(path.isAbsolute(row[key]), `${row.id}: ${key} must be absolute`);
    }
    const searchRaw = JSON.parse(await fs.readFile(row.search_record, "utf8"));
    const impactText = await fs.readFile(row.impact_record, "utf8");
    const impactRaw = JSON.parse(impactText);
    const search = validateAtlasSearch(searchRaw, row.query, 200, row.graph);
    const selection = search.results.find(item => item.selection_id === row.selection_id && item.kind === "gt-recipe");
    assert.ok(selection, `${row.id}: exact recipe is absent from the CLI search`);
    assert.ok([undefined, "bounded", "complete-finite"].includes(row.exploration), "unknown corpus exploration mode");
    const validate = row.exploration === "complete-finite" ? validateCompleteImpactReport : validateImpactReport;
    const impact = validate(impactRaw, {
      root: row.graph, selectionId: row.selection_id, maxDepth: row.max_depth, maxNodes: row.max_nodes,
    });
    validateImpactSearchLink(impact, search, selection);
    assert.deepEqual(impact, impactRaw, `${row.id}: client changed the CLI report`);
    const receipt = { ...row, graph_set_id: impact.context.graph_set_id, cautions: impactCautions(impact),
      impact_sha256: crypto.createHash("sha256").update(impactText).digest("hex") };
    cases.push(receipt);
    // Every report has passed the full parser and exact search linkage above.
    // Only the native journeys need full reports after this iteration.
    if (row.highlight === true) highlights.push({ ...receipt, search, selection, impact });
  }
  assert.ok(highlights.length > 0 && highlights.length <= 6, "select one through six explicit native journey highlights");
  return { manifest, cases, highlights };
}

if (require.main === module) {
  loadCorpus(process.argv[2]).then(({ cases, highlights }) => {
    process.stdout.write(`${JSON.stringify({ format: "workbench-atlas-client-contract-result-v1",
      cases: cases.map(row => ({ id: row.id, selection_id: row.selection_id,
        graph_set_id: row.graph_set_id, impact_sha256: row.impact_sha256, cautions: row.cautions })),
      highlight_count: highlights.length }, null, 2)}\n`);
  }).catch(error => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
}

module.exports = { loadCorpus };
