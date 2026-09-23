"use strict";

const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");
const fs = require("node:fs");
const path = require("node:path");

const EXAMPLES_FORMAT = "workbench-developer-feature-example-catalog-v1";
const ATLAS_SEARCH_FORMAT = "workbench-atlas-recipe-health-search-v1";
const ATLAS_CONTEXT_FORMAT = "workbench-atlas-recipe-health-context-v1";
const SHA256 = /^[0-9a-f]{64}$/;
const EXAMPLE_KEY = /^[a-z0-9][a-z0-9-]{0,255}$/;
const FAMILY = /^[a-z][a-z0-9-]{0,127}$/;

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, required, optional, label) {
  const actual = new Set(Object.keys(value));
  const allowed = new Set([...required, ...optional]);
  const missing = required.filter((key) => !actual.has(key));
  const extra = [...actual].filter((key) => !allowed.has(key));
  if (missing.length || extra.length) {
    throw new Error(`${label} fields changed; missing=${missing.join(",")}; extra=${extra.join(",")}`);
  }
}

function text(value, label, maximum = 32 * 1024) {
  if (typeof value !== "string" || !value || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function safeInteger(value, label, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside its supported bound`);
  }
  return value;
}

function exactBooleanObject(value, keys, label) {
  const selected = object(value, label);
  exactKeys(selected, keys, [], label);
  for (const key of keys) {
    if (typeof selected[key] !== "boolean") throw new Error(`${label}.${key} must be boolean`);
  }
  return selected;
}

function validateExamples(value, expectedSelection = undefined) {
  const catalog = object(value, "developer feature example catalog");
  exactKeys(catalog, [
    "authority_boundary", "count", "examples", "format", "profile",
    "schema_version", "selection", "state",
  ], [], "developer feature example catalog");
  if (catalog.format !== EXAMPLES_FORMAT || catalog.schema_version !== 1
      || catalog.profile !== "supersymmetry" || catalog.state !== "available") {
    throw new Error("developer feature example catalog identity changed");
  }
  const authority = object(catalog.authority_boundary, "developer feature example authority boundary");
  const authorityBooleanKeys = [
    "construction_authority", "profile_action_authorized", "profile_support_claimed",
    "publication_authorized", "read_only", "release_qualified",
  ];
  exactKeys(authority, [...authorityBooleanKeys, "source"], [], "developer feature example authority boundary");
  for (const key of authorityBooleanKeys) {
    if (typeof authority[key] !== "boolean") {
      throw new Error(`developer feature example authority boundary.${key} must be boolean`);
    }
  }
  if (catalog.authority_boundary.source !== "profile-owned-non-identity-bearing-examples") {
    // source is deliberately checked separately because it is the one text field.
    throw new Error("developer feature example authority source changed");
  }
  if (authority.construction_authority || authority.profile_action_authorized
      || authority.profile_support_claimed || authority.publication_authorized
      || authority.release_qualified || !authority.read_only) {
    throw new Error("developer feature example authority boundary became authorizing");
  }
  if (!Array.isArray(catalog.examples) || catalog.examples.length > 256
      || safeInteger(catalog.count, "developer feature example count", 0, 256) !== catalog.examples.length) {
    throw new Error("developer feature example count changed");
  }
  const examples = catalog.examples.map((item) => {
    const example = object(item, "developer feature example");
    exactKeys(example, [
      "example_key", "family", "record", "runtime_evidence_state",
      "source_path", "source_sha256", "source_size",
    ], [], "developer feature example");
    const exampleKey = text(example.example_key, "example key", 256);
    const family = text(example.family, "example family", 128);
    if (!EXAMPLE_KEY.test(exampleKey) || !FAMILY.test(family)
        || !SHA256.test(example.source_sha256)
        || !example.source_path.startsWith("profiles/packs/supersymmetry/blueprints/examples/")
        || example.source_path.includes("..")) {
      throw new Error("developer feature example binding is invalid");
    }
    safeInteger(example.source_size, "developer feature example source size", 1, 8 * 1024 * 1024);
    object(example.record, "developer feature example owner record");
    return Object.freeze({
      example_key: exampleKey,
      family,
      record: example.record,
      runtime_evidence_state: text(example.runtime_evidence_state, "runtime evidence state", 256),
      source_path: text(example.source_path, "example source path"),
      source_sha256: example.source_sha256,
      source_size: example.source_size,
    });
  });
  if (new Set(examples.map((item) => item.example_key)).size !== examples.length) {
    throw new Error("developer feature example keys are duplicated");
  }
  const selection = object(catalog.selection, "developer feature example selection");
  exactKeys(selection, ["kind", "value"], [], "developer feature example selection");
  if (!["all", "family", "example-key"].includes(selection.kind)
      || (selection.kind === "all" ? selection.value !== null : typeof selection.value !== "string")) {
    throw new Error("developer feature example selection changed");
  }
  if (expectedSelection === null
      && (selection.kind !== "all" || selection.value !== null)) {
    throw new Error("developer feature example catalog did not return the requested all selection");
  }
  if (typeof expectedSelection === "string"
      && (selection.kind !== "example-key" || selection.value !== expectedSelection
        || examples.length !== 1 || examples[0].example_key !== expectedSelection)) {
    throw new Error("developer feature example selection does not match the request");
  }
  return Object.freeze({
    authority_boundary: catalog.authority_boundary,
    count: catalog.count,
    examples: Object.freeze(examples),
    format: EXAMPLES_FORMAT,
    profile: "supersymmetry",
    schema_version: 1,
    selection: Object.freeze({ kind: selection.kind, value: selection.value }),
    state: "available",
  });
}

function validateCapabilities(value, contextType) {
  const keys = contextType === "source-only-checkout"
    ? [
      "consumers", "duplicate_signatures", "producers", "reachability", "recipe_search",
      "source_mutations", "source_occurrence_search", "stoichiometry",
    ]
    : [
      "consumers", "duplicate_signatures", "producers", "reachability", "recipe_search",
      "source_mutations", "stoichiometry",
    ];
  const capabilities = exactBooleanObject(value, keys, "Atlas recipe capabilities");
  if (contextType === "source-only-checkout") {
    for (const key of keys) {
      const expected = key === "source_occurrence_search";
      if (capabilities[key] !== expected) {
        throw new Error("Atlas source-only capabilities overclaim runtime evidence");
      }
    }
  }
  return capabilities;
}

function sameAtlasRoot(actual, expected) {
  if (actual === expected) return true;
  // Resolve native filesystem spellings (including Windows directory case).
  // A remote Linux path on a Windows client must remain an exact string match.
  if (!path.isAbsolute(actual) || !path.isAbsolute(expected)
      || (process.platform === "win32" && ![actual, expected].every(value => /^(?:[A-Za-z]:[\\/]|\\\\)/.test(value)))) return false;
  try { return fs.realpathSync.native(actual) === fs.realpathSync.native(expected); }
  catch { return false; }
}

function validateAtlasContext(value, expectedRoot) {
  const context = object(value, "Atlas recipe context");
  const contextType = context.context_type;
  if (contextType === "source-only-checkout") {
    exactKeys(context, [
      "capabilities", "context_type", "format", "root", "schema_version",
      "search", "source_file_count", "source_roots",
    ], [], "Atlas source recipe context");
    safeInteger(context.source_file_count, "Atlas source file count", 1);
    if (!Array.isArray(context.source_roots) || context.source_roots.length < 1
        || context.source_roots.length > 16) {
      throw new Error("Atlas source roots are invalid");
    }
    context.source_roots.forEach((item) => text(item, "Atlas source root"));
    const search = object(context.search, "Atlas source search contract");
    exactKeys(search, ["requires_semantic_key", "selection_is_exact_source_occurrence"], [], "Atlas source search contract");
    if (search.requires_semantic_key !== false || search.selection_is_exact_source_occurrence !== true) {
      throw new Error("Atlas source search contract changed");
    }
  } else if (contextType === "categorical-graph-v2") {
    exactKeys(context, [
      "capabilities", "context_type", "format", "graph_set_id", "recipe_count", "root",
      "schema_version", "scope", "search", "summary",
    ], [], "Atlas graph recipe context");
    text(context.graph_set_id, "Atlas graph set ID");
    object(context.scope, "Atlas graph scope");
    object(context.summary, "Atlas graph summary");
    safeInteger(context.recipe_count, "Atlas recipe count", 0);
    const search = object(context.search, "Atlas graph search contract");
    exactKeys(search, ["requires_semantic_key", "selection_is_exact_node_id"], [], "Atlas graph search contract");
    if (search.requires_semantic_key !== false || search.selection_is_exact_node_id !== true) {
      throw new Error("Atlas graph search contract changed");
    }
  } else {
    throw new Error("Atlas recipe context type is unsupported");
  }
  if (context.format !== ATLAS_CONTEXT_FORMAT || context.schema_version !== 1) {
    throw new Error("Atlas recipe context identity changed");
  }
  const root = text(context.root, "Atlas recipe context root");
  if (!sameAtlasRoot(root, expectedRoot)) {
    throw new Error("Atlas recipe context does not match the requested root");
  }
  validateCapabilities(context.capabilities, contextType);
  return context;
}

function validateEvidenceGaps(value) {
  if (!Array.isArray(value) || value.length > 256) throw new Error("Atlas evidence gaps are invalid");
  value.forEach((item) => {
    const gap = object(item, "Atlas evidence gap");
    exactKeys(gap, ["code", "message"], ["paths"], "Atlas evidence gap");
    text(gap.code, "Atlas evidence gap code");
    text(gap.message, "Atlas evidence gap message");
    if (gap.paths !== undefined) {
      if (!Array.isArray(gap.paths) || gap.paths.length > 4096) throw new Error("Atlas evidence gap paths are invalid");
      gap.paths.forEach((itemPath) => text(itemPath, "Atlas evidence gap path"));
    }
  });
  return value;
}

function validateAtlasResult(value, contextType) {
  const result = object(value, "Atlas recipe search result");
  if (contextType === "source-only-checkout") {
    exactKeys(result, [
      "column", "kind", "line", "selection_id", "snippet", "source_path",
    ], [], "Atlas source recipe result");
    if (result.kind !== "source-occurrence") throw new Error("Atlas source result kind changed");
    safeInteger(result.line, "Atlas source result line", 1);
    safeInteger(result.column, "Atlas source result column", 1);
    text(result.source_path, "Atlas source result path");
    text(result.snippet, "Atlas source result snippet", 1024 * 1024);
  } else {
    exactKeys(result, [
      "evidence", "kind", "properties", "selection_id", "semantic_key",
    ], [], "Atlas graph recipe result");
    if (!Array.isArray(result.evidence) || result.evidence.length > 4096) {
      throw new Error("Atlas graph result evidence is invalid");
    }
    result.evidence.forEach((item) => object(item, "Atlas graph result evidence row"));
    object(result.properties, "Atlas graph result properties");
    text(result.semantic_key, "Atlas graph result semantic key");
  }
  text(result.selection_id, "Atlas result selection ID");
  text(result.kind, "Atlas result kind");
  return result;
}

/** Reject rounded capture numbers before they enter an Atlas picker, tree or report. */
function validateAtlasNumericPrecision(value) {
  const pending = [value];
  const visited = new Set();
  while (pending.length) {
    const current = pending.pop();
    if (typeof current === "number") {
      if (!Number.isFinite(current) || (Number.isInteger(current) && !Number.isSafeInteger(current))) {
        throw new Error("Atlas numeric precision cannot be preserved: an unsafe integral or nonfinite JSON number was returned. Read the original CLI JSON for exact values.");
      }
    } else if (current !== null && typeof current === "object" && !visited.has(current)) {
      visited.add(current);
      for (const child of Object.values(current)) pending.push(child);
    }
  }
}

function validateAtlasSearch(value, expectedQuery, limit, expectedRoot) {
  validateAtlasNumericPrecision(value);
  const search = object(value, "Atlas recipe search");
  exactKeys(search, [
    "context", "format", "query", "results", "schema_version", "truncated",
  ], ["evidence_gaps"], "Atlas recipe search");
  if (search.format !== ATLAS_SEARCH_FORMAT || search.schema_version !== 1
      || search.query !== expectedQuery || typeof search.truncated !== "boolean") {
    throw new Error("Atlas recipe search identity changed");
  }
  const context = validateAtlasContext(search.context, expectedRoot);
  if (!Array.isArray(search.results) || search.results.length > limit) {
    throw new Error("Atlas recipe search result count exceeds the requested limit");
  }
  const results = search.results.map((item) => validateAtlasResult(item, context.context_type));
  if (search.evidence_gaps !== undefined) validateEvidenceGaps(search.evidence_gaps);
  if (context.context_type === "source-only-checkout" && search.evidence_gaps === undefined) {
    throw new Error("Atlas source search omitted its evidence gaps");
  }
  return Object.freeze({
    context,
    format: ATLAS_SEARCH_FORMAT,
    query: search.query,
    results: Object.freeze(results),
    schema_version: 1,
    truncated: search.truncated,
    ...(search.evidence_gaps === undefined ? {} : { evidence_gaps: search.evidence_gaps }),
  });
}

async function invokeExamples(executable, selection = null, options = {}) {
  if (selection !== null && (typeof selection !== "string" || !EXAMPLE_KEY.test(selection))) {
    throw new Error("developer feature example selection is invalid");
  }
  const arguments_ = ["feature", "examples"];
  if (selection !== null) arguments_.push(selection);
  arguments_.push("--json");
  const value = await invokeCoreJson(executable, arguments_, {
    ...options,
    maximumOutput: 16 * 1024 * 1024,
    timeoutMs: 60_000,
    label: "Workbench developer feature examples",
  });
  return validateExamples(value, selection);
}

async function invokeAtlasSearch(executable, root, query, limit = 50, options = {}) {
  const selectedQuery = text(query, "Atlas recipe query", 16 * 1024);
  if (/\r|\n/.test(selectedQuery)) throw new Error("Atlas recipe query must be one line");
  const selectedLimit = safeInteger(limit, "Atlas recipe limit", 1, 10_000);
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const mappedRoot = pathForCoreLaunch(root, launch, "Atlas recipe root");
  const value = await invokeCoreJson(executable, [
    "atlas", "recipes", "search", mappedRoot, selectedQuery,
    "--limit", String(selectedLimit), "--json",
  ], {
    ...options,
    launch,
    maximumOutput: 48 * 1024 * 1024,
    timeoutMs: options.timeoutMs === undefined ? 15 * 60 * 1000 : options.timeoutMs,
    label: "Atlas recipe search",
  });
  return validateAtlasSearch(value, selectedQuery, selectedLimit, mappedRoot);
}

module.exports = {
  ATLAS_SEARCH_FORMAT,
  EXAMPLES_FORMAT,
  invokeAtlasSearch,
  invokeExamples,
  validateAtlasContext,
  validateAtlasNumericPrecision,
  validateAtlasSearch,
  validateExamples,
};
