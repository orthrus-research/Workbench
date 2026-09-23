"use strict";

const { validateAtlasContext, validateAtlasNumericPrecision } = require("./developerToolsClient");

function object(value) { return value && typeof value === "object" && !Array.isArray(value); }

function node(value) {
  if (!object(value) || typeof value.selection_id !== "string" || !value.selection_id
      || typeof value.kind !== "string" || typeof value.semantic_key !== "string"
      || !object(value.properties) || !Array.isArray(value.evidence)) throw new Error("Atlas returned an invalid node");
}

function validateBrowse(value, { root, graph, selection, offset, limit = 50 }) {
  validateAtlasNumericPrecision(value);
  if (!object(value) || value.format !== "workbench-atlas-recipe-browse-v1" || value.schema_version !== 1) throw new Error("Unsupported Atlas browse record");
  validateAtlasContext(value.context, root);
  if (value.context.graph_set_id !== graph) throw new Error("Atlas graph changed; search again");
  node(value.selection);
  if (value.selection.selection_id !== selection) throw new Error("Atlas changed the selected node");
  const page = value.page;
  if (!object(page) || page.offset !== offset || page.limit !== limit || !Number.isSafeInteger(page.total) || page.total < 0
      || !Array.isArray(value.links) || value.links.length !== Math.max(0, Math.min(limit, page.total - offset))
      || page.next_offset !== (offset + value.links.length < page.total ? offset + value.links.length : null)) throw new Error("Atlas relationship page is inconsistent");
  const seen = new Set();
  for (const row of value.links) {
    node(row.node);
    const edge = row.relationship;
    if (!["incoming", "outgoing"].includes(row.direction) || !object(edge)
        || typeof edge.id !== "string" || typeof edge.relation !== "string"
        || !object(edge.properties) || !Array.isArray(edge.evidence)
        || edge[row.direction === "outgoing" ? "source" : "target"] !== selection
        || edge[row.direction === "outgoing" ? "target" : "source"] !== row.node.selection_id) throw new Error("Atlas relationship linkage changed");
    const key = `${row.direction}:${edge.id}`;
    if (seen.has(key)) throw new Error("Atlas returned a duplicate relationship");
    seen.add(key);
  }
  if (typeof value.scope !== "string" || !Array.isArray(value.evidence_gaps)) throw new Error("Atlas omitted the evidence scope");
  return value;
}

module.exports = { validateBrowse };
