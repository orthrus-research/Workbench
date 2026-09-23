"use strict";

const path = require("node:path");
const { isDeepStrictEqual } = require("node:util");

const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");
const { validateAtlasContext, validateAtlasNumericPrecision } = require("./developerToolsClient");

const FORMAT = "workbench-atlas-recipe-impact-report-v1";
const MAX_LIST = 10_000;
// Omitted edge/phase observations can outnumber admitted traversal nodes.
const MAX_UNCERTAINTY_ROWS = 10_000;
const GAP_CODES = Object.freeze([
  "counterfactual-runtime-not-observed",
  "non-recipe-acquisition-not-assessed",
  "dynamic-recipes-not-executed",
  "stoichiometry-and-chance-not-assessed",
  "progression-reachability-not-proven",
  "task-execution-not-invoked",
  "pack-tier-policy-not-applied",
]);

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, keys, label) {
  const actual = Object.keys(object(value, label)).sort();
  const expected = [...keys].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} fields changed`);
  }
}

function text(value, label, maximum = 256 * 1024, allowEmpty = false) {
  if (typeof value !== "string" || (!allowEmpty && !value) || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function integer(value, label, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside its supported boundary`);
  }
  return value;
}

function list(value, label, maximum = MAX_LIST) {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new Error(`${label} exceeds its supported boundary`);
  }
  return value;
}

function jsonTree(value, label, depth = 0, budget = { count: 0 }) {
  budget.count += 1;
  if (budget.count > 250_000 || depth > 64) throw new Error(`${label} exceeds its JSON boundary`);
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return text(value, label, 2 * 1024 * 1024, true);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${label} contains a non-finite number`);
    return value;
  }
  if (Array.isArray(value)) {
    list(value, label, MAX_LIST);
    value.forEach((item) => jsonTree(item, label, depth + 1, budget));
    return value;
  }
  const selected = object(value, label);
  if (Object.keys(selected).length > MAX_LIST) throw new Error(`${label} object exceeds its boundary`);
  for (const [key, item] of Object.entries(selected)) {
    text(key, `${label} key`, 16 * 1024, true);
    jsonTree(item, label, depth + 1, budget);
  }
  return value;
}

function stringList(value, label, maximum = MAX_LIST) {
  list(value, label, maximum).forEach((item, index) => text(item, `${label}[${index}]`));
  return value;
}

function nodeSummary(value, label, expectedKind = undefined) {
  const node = object(value, label);
  exactKeys(node, ["evidence", "kind", "properties", "selection_id", "semantic_key"], label);
  text(node.selection_id, `${label} selection ID`);
  text(node.kind, `${label} kind`, 1024);
  text(node.semantic_key, `${label} semantic key`, 2 * 1024 * 1024);
  if (expectedKind !== undefined && node.kind !== expectedKind) {
    throw new Error(`${label} kind changed`);
  }
  jsonTree(object(node.properties, `${label} properties`), `${label} properties`);
  list(node.evidence, `${label} evidence`, 4096)
    .forEach((item) => jsonTree(item, `${label} evidence row`));
  return node;
}

function edgeSummary(value, label) {
  const edge = object(value, label);
  exactKeys(edge, ["evidence", "properties", "relation", "semantic_key"], label);
  text(edge.relation, `${label} relation`, 1024);
  text(edge.semantic_key, `${label} semantic key`, 2 * 1024 * 1024);
  jsonTree(object(edge.properties, `${label} properties`), `${label} properties`);
  list(edge.evidence, `${label} evidence`, 4096)
    .forEach((item) => jsonTree(item, `${label} evidence row`));
  return edge;
}

function nodeEdge(value, label) {
  const edge = object(value, label);
  exactKeys(edge, ["evidence", "node", "properties", "relation", "semantic_key"], label);
  edgeSummary({
    evidence: edge.evidence,
    properties: edge.properties,
    relation: edge.relation,
    semantic_key: edge.semantic_key,
  }, label);
  if (edge.node !== null) nodeSummary(edge.node, `${label} node`);
  return edge;
}

function directInput(value, index) {
  const label = `Atlas direct input ${index}`;
  const input = object(value, label);
  exactKeys(input, [
    "evidence", "node", "properties", "relation", "selector", "selector_edge", "semantic_key",
  ], label);
  nodeEdge({
    evidence: input.evidence,
    node: input.node,
    properties: input.properties,
    relation: input.relation,
    semantic_key: input.semantic_key,
  }, label);
  if (input.selector !== null) nodeSummary(input.selector, `${label} selector`);
  const selectorEdge = object(input.selector_edge, `${label} selector edge`);
  exactKeys(selectorEdge, ["evidence", "properties", "relation"], `${label} selector edge`);
  text(selectorEdge.relation, `${label} selector edge relation`, 1024);
  jsonTree(object(selectorEdge.properties, `${label} selector edge properties`), `${label} selector edge properties`);
  list(selectorEdge.evidence, `${label} selector edge evidence`, 4096)
    .forEach((item) => jsonTree(item, `${label} selector edge evidence row`));
  return input;
}

function directOutput(value, index, maxNodes) {
  const label = `Atlas direct output ${index}`;
  const output = object(value, label);
  exactKeys(output, [
    "downstream_consumers", "downstream_consumers_truncated", "output", "producer_portfolio",
  ], label);
  nodeEdge(output.output, `${label} output edge`);
  if (typeof output.downstream_consumers_truncated !== "boolean") {
    throw new Error(`${label} truncation state changed`);
  }
  list(output.downstream_consumers, `${label} downstream consumers`, maxNodes)
    .forEach((row, rowIndex) => {
      const consumer = object(row, `${label} consumer ${rowIndex}`);
      exactKeys(consumer, ["acceptance_edge", "recipe", "selector"], `${label} consumer ${rowIndex}`);
      nodeSummary(consumer.recipe, `${label} consumer recipe`, "gt-recipe");
      nodeSummary(consumer.selector, `${label} consumer selector`);
      edgeSummary(consumer.acceptance_edge, `${label} consumer acceptance edge`);
    });
  const portfolio = object(output.producer_portfolio, `${label} producer portfolio`);
  exactKeys(portfolio, ["alternative_producers", "status", "truncated", "viability"], `${label} producer portfolio`);
  if (!["producer-set-truncated", "observed-alternatives-present", "sole-observed-finite-producer", "no-observed-active-producer"].includes(portfolio.status)
      || portfolio.viability !== "not-assessed" || typeof portfolio.truncated !== "boolean"
      || (portfolio.status === "producer-set-truncated") !== portfolio.truncated) {
    throw new Error(`${label} producer portfolio semantics changed`);
  }
  list(portfolio.alternative_producers, `${label} alternative producers`, maxNodes)
    .forEach((row, rowIndex) => {
      const alternative = object(row, `${label} alternative producer ${rowIndex}`);
      exactKeys(alternative, ["output_edge", "recipe"], `${label} alternative producer ${rowIndex}`);
      nodeSummary(alternative.recipe, `${label} alternative recipe`, "gt-recipe");
      edgeSummary(alternative.output_edge, `${label} alternative output edge`);
    });
  return output;
}

function validateDirect(value, maxNodes) {
  const direct = object(value, "Atlas direct impact");
  exactKeys(direct, ["inputs", "outputs"], "Atlas direct impact");
  list(direct.inputs, "Atlas direct inputs", MAX_LIST).forEach(directInput);
  list(direct.outputs, "Atlas direct outputs", maxNodes)
    .forEach((row, index) => directOutput(row, index, maxNodes));
  return direct;
}

function validatePropagation(value, maxDepth, maxNodes) {
  const propagation = object(value, "Atlas impact propagation");
  exactKeys(propagation, [
    "alternative_dependency_cycle_signals", "at_risk_recipes", "at_risk_resources", "status",
  ], "Atlas impact propagation");
  if (!["complete-within-model", "truncated"].includes(propagation.status)) {
    throw new Error("Atlas impact propagation state changed");
  }
  list(propagation.at_risk_resources, "Atlas at-risk resource candidates", maxNodes)
    .forEach((row, index) => {
      const candidate = object(row, `Atlas at-risk resource candidate ${index}`);
      exactKeys(candidate, ["all_observed_finite_producers", "depth", "reason", "resource"], `Atlas at-risk resource candidate ${index}`);
      nodeSummary(candidate.resource, `Atlas at-risk resource candidate ${index} resource`);
      integer(candidate.depth, `Atlas at-risk resource candidate ${index} depth`, 0, maxDepth);
      if (candidate.reason !== "all-observed-finite-producers-are-unavailable-candidates") {
        throw new Error("Atlas at-risk resource candidate reason changed");
      }
      stringList(candidate.all_observed_finite_producers, `Atlas at-risk resource candidate ${index} producers`, maxNodes);
    });
  list(propagation.at_risk_recipes, "Atlas at-risk recipe candidates", maxNodes)
    .forEach((row, index) => {
      const candidate = object(row, `Atlas at-risk recipe candidate ${index}`);
      exactKeys(candidate, ["blocked_selectors", "depth", "reason", "recipe"], `Atlas at-risk recipe candidate ${index}`);
      nodeSummary(candidate.recipe, `Atlas at-risk recipe candidate ${index} recipe`, "gt-recipe");
      integer(candidate.depth, `Atlas at-risk recipe candidate ${index} depth`, 1, maxDepth);
      if (candidate.reason !== "at-least-one-selector-has-only-at-risk-observed-alternatives") {
        throw new Error("Atlas at-risk recipe candidate reason changed");
      }
      list(candidate.blocked_selectors, `Atlas at-risk recipe candidate ${index} selectors`, maxNodes)
        .forEach((rowValue, selectorIndex) => {
          const blocked = object(rowValue, `Atlas blocked selector ${selectorIndex}`);
          exactKeys(blocked, ["accepted_resources", "selector"], `Atlas blocked selector ${selectorIndex}`);
          nodeSummary(blocked.selector, `Atlas blocked selector ${selectorIndex} selector`);
          list(blocked.accepted_resources, `Atlas blocked selector ${selectorIndex} resources`, maxNodes)
            .forEach((resource, resourceIndex) => nodeSummary(resource, `Atlas blocked selector resource ${resourceIndex}`));
        });
    });
  list(propagation.alternative_dependency_cycle_signals, "Atlas alternative dependency cycle signals", maxNodes)
    .forEach((row, index) => {
      const signal = object(row, `Atlas alternative dependency cycle signal ${index}`);
      exactKeys(signal, ["interpretation", "path", "root_resource_id", "viability_effect"], `Atlas alternative dependency cycle signal ${index}`);
      text(signal.root_resource_id, `Atlas alternative dependency cycle signal ${index} root`);
      stringList(signal.path, `Atlas alternative dependency cycle signal ${index} path`, maxNodes);
      text(signal.interpretation, `Atlas alternative dependency cycle signal ${index} interpretation`);
      if (signal.viability_effect !== "unknown") throw new Error("Atlas cycle viability claim changed");
    });
  return propagation;
}

function validateQuestRequirement(value, index, maxNodes) {
  const label = `Atlas quest requirement exposure ${index}`;
  const row = object(value, label);
  exactKeys(row, [
    "accepted_ore_dictionary_keys", "owner_edge", "quest", "requirement_occurrence",
    "requirement_semantics", "resource", "resource_edge", "resource_risk_status",
    "task", "task_edge",
  ], label);
  nodeSummary(row.quest, `${label} quest`, "betterquesting-quest");
  nodeSummary(row.task, `${label} task`, "betterquesting-task-occurrence");
  nodeSummary(row.requirement_occurrence, `${label} occurrence`);
  nodeSummary(row.resource, `${label} resource`);
  if (!["ore-dictionary-selector-with-observed-item-representative", "exact-observed-resource-reference"].includes(row.requirement_semantics)
      || !["producer-portfolio-changed", "observed-all-finite-producers-at-risk"].includes(row.resource_risk_status)) {
    throw new Error(`${label} semantics changed`);
  }
  edgeSummary(row.resource_edge, `${label} resource edge`);
  edgeSummary(row.task_edge, `${label} task edge`);
  edgeSummary(row.owner_edge, `${label} owner edge`);
  list(row.accepted_ore_dictionary_keys, `${label} ore dictionary keys`, maxNodes)
    .forEach((item, oreIndex) => {
      const ore = object(item, `${label} ore dictionary key ${oreIndex}`);
      exactKeys(ore, ["acceptance_edge", "ore_dictionary_key"], `${label} ore dictionary key ${oreIndex}`);
      nodeSummary(ore.ore_dictionary_key, `${label} ore dictionary key ${oreIndex} node`);
      edgeSummary(ore.acceptance_edge, `${label} ore dictionary key ${oreIndex} edge`);
    });
  return row;
}

function validateProgression(value, maxDepth, maxNodes) {
  const progression = object(value, "Atlas progression signals");
  exactKeys(progression, ["energy_and_machine_signals", "quest_signals"], "Atlas progression signals");
  const energy = object(progression.energy_and_machine_signals, "Atlas energy and machine signals");
  exactKeys(energy, [
    "interpretation", "machine_numeric_tiers_observed", "machines_observed_using_recipe_map",
    "observed_recipe_properties", "recipe_map", "truncated",
  ], "Atlas energy and machine signals");
  text(energy.interpretation, "Atlas energy and machine interpretation");
  if (typeof energy.truncated !== "boolean") throw new Error("Atlas machine signal truncation changed");
  const properties = object(energy.observed_recipe_properties, "Atlas observed recipe properties");
  exactKeys(properties, ["duration", "eut", "hidden", "lookup_active", "recipe_map"], "Atlas observed recipe properties");
  jsonTree(properties, "Atlas observed recipe properties");
  if (energy.recipe_map !== null) nodeSummary(energy.recipe_map, "Atlas recipe map", "gt-recipe-map");
  list(energy.machines_observed_using_recipe_map, "Atlas observed machines", maxNodes)
    .forEach((row, index) => {
      const machine = object(row, `Atlas observed machine ${index}`);
      exactKeys(machine, ["machine", "map_edge"], `Atlas observed machine ${index}`);
      nodeSummary(machine.machine, `Atlas observed machine ${index} node`, "gt-machine");
      edgeSummary(machine.map_edge, `Atlas observed machine ${index} edge`);
    });
  list(energy.machine_numeric_tiers_observed, "Atlas observed machine numeric tiers", maxNodes)
    .forEach((tier, index) => integer(tier, `Atlas observed machine numeric tier ${index}`, -1_000_000, 1_000_000));

  const quest = object(progression.quest_signals, "Atlas quest signals");
  exactKeys(quest, [
    "direct_resource_requirements", "interpretation", "prerequisite_cycles", "status",
    "structural_prerequisite_dependents",
  ], "Atlas quest signals");
  if (!["truncated", "observed-definition-references", "no-observed-reference", "evidence-unavailable"].includes(quest.status)) {
    throw new Error("Atlas quest signal status changed");
  }
  text(quest.interpretation, "Atlas quest signal interpretation");
  list(quest.direct_resource_requirements, "Atlas quest requirement exposures", maxNodes)
    .forEach((row, index) => validateQuestRequirement(row, index, maxNodes));
  list(quest.structural_prerequisite_dependents, "Atlas structural quest dependents", maxNodes)
    .forEach((row, index) => {
      const dependent = object(row, `Atlas structural quest dependent ${index}`);
      exactKeys(dependent, [
        "depends_on", "depth", "owner_edge", "prerequisite_occurrence", "quest", "target_edge",
      ], `Atlas structural quest dependent ${index}`);
      nodeSummary(dependent.quest, `Atlas structural quest dependent ${index} quest`, "betterquesting-quest");
      nodeSummary(dependent.depends_on, `Atlas structural quest dependent ${index} dependency`, "betterquesting-quest");
      nodeSummary(dependent.prerequisite_occurrence, `Atlas structural quest dependent ${index} occurrence`, "betterquesting-prerequisite-occurrence");
      integer(dependent.depth, `Atlas structural quest dependent ${index} depth`, 1, maxDepth);
      edgeSummary(dependent.target_edge, `Atlas structural quest dependent ${index} target edge`);
      edgeSummary(dependent.owner_edge, `Atlas structural quest dependent ${index} owner edge`);
    });
  list(quest.prerequisite_cycles, "Atlas prerequisite cycle signals", maxNodes)
    .forEach((row, index) => {
      const cycle = object(row, `Atlas prerequisite cycle signal ${index}`);
      exactKeys(cycle, ["interpretation", "path"], `Atlas prerequisite cycle signal ${index}`);
      text(cycle.interpretation, `Atlas prerequisite cycle signal ${index} interpretation`);
      stringList(cycle.path, `Atlas prerequisite cycle signal ${index} path`, maxNodes);
    });
  return progression;
}

function validateFrontiers(value, maxDepth, maxNodes) {
  const allowed = new Set([
    "depth", "from_node_id", "kind", "omitted_at_least", "phase", "relation", "subject_ids",
  ]);
  list(value, "Atlas impact frontiers", MAX_UNCERTAINTY_ROWS).forEach((row, index) => {
    const frontier = object(row, `Atlas impact frontier ${index}`);
    if (!Object.keys(frontier).includes("kind")
        || Object.keys(frontier).some((key) => !allowed.has(key))) {
      throw new Error(`Atlas impact frontier ${index} fields changed`);
    }
    if (!["depth-bound", "node-bound", "relation-bound"].includes(frontier.kind)) {
      throw new Error(`Atlas impact frontier ${index} kind changed`);
    }
    for (const key of ["from_node_id", "phase", "relation"]) {
      if (frontier[key] !== undefined) text(frontier[key], `Atlas impact frontier ${index} ${key}`);
    }
    if (frontier.depth !== undefined) integer(frontier.depth, `Atlas impact frontier ${index} depth`, 1, maxDepth);
    if (frontier.omitted_at_least !== undefined) integer(frontier.omitted_at_least, `Atlas impact frontier ${index} omitted count`, 1);
    if (frontier.subject_ids !== undefined) stringList(frontier.subject_ids, `Atlas impact frontier ${index} subjects`, maxNodes);
  });
  return value;
}

function validateUnknowns(value) {
  list(value, "Atlas impact unknowns", MAX_UNCERTAINTY_ROWS).forEach((row, index) => {
    const unknown = object(row, `Atlas impact unknown ${index}`);
    const keys = Object.keys(unknown).sort();
    if (JSON.stringify(keys) !== JSON.stringify(["code", "message"])
        && JSON.stringify(keys) !== JSON.stringify(["code", "message", "subject_id"])) {
      throw new Error(`Atlas impact unknown ${index} fields changed`);
    }
    text(unknown.code, `Atlas impact unknown ${index} code`, 1024);
    text(unknown.message, `Atlas impact unknown ${index} message`);
    if (unknown.subject_id !== undefined) text(unknown.subject_id, `Atlas impact unknown ${index} subject`);
  });
  return value;
}

function validateEvidenceGaps(value) {
  const gaps = list(value, "Atlas impact evidence gaps", GAP_CODES.length + 1);
  if (gaps.length < GAP_CODES.length) throw new Error("Atlas impact evidence gap set changed");
  gaps.forEach((row, index) => {
    const gap = object(row, `Atlas impact evidence gap ${index}`);
    exactKeys(gap, ["code", "message"], `Atlas impact evidence gap ${index}`);
    if (gap.code !== (GAP_CODES[index] || "graph-projection-limitations")) throw new Error("Atlas impact evidence gap order changed");
    text(gap.message, `Atlas impact evidence gap ${index} message`);
  });
  return gaps;
}

function validateSummary(value, direct, propagation, progression, expectedTruncated) {
  const summary = object(value, "Atlas impact summary");
  exactKeys(summary, [
    "alternative_dependency_cycle_signal_count", "at_risk_recipe_candidate_count",
    "at_risk_resource_candidate_count", "quest_requirement_exposure_count",
    "selected_output_count", "sole_observed_finite_producer_output_count",
    "structural_quest_dependent_count", "truncated",
  ], "Atlas impact summary");
  const exactCounts = {
    selected_output_count: direct.outputs.length,
    sole_observed_finite_producer_output_count: direct.outputs.filter(
      (row) => row.producer_portfolio.status === "sole-observed-finite-producer",
    ).length,
    at_risk_resource_candidate_count: propagation.at_risk_resources.length,
    at_risk_recipe_candidate_count: propagation.at_risk_recipes.length,
    quest_requirement_exposure_count: progression.quest_signals.direct_resource_requirements.length,
    structural_quest_dependent_count: progression.quest_signals.structural_prerequisite_dependents.length,
    alternative_dependency_cycle_signal_count: propagation.alternative_dependency_cycle_signals.length,
  };
  for (const [key, count] of Object.entries(exactCounts)) {
    if (summary[key] !== count) throw new Error(`Atlas impact summary ${key} changed`);
  }
  if (summary.truncated !== expectedTruncated) throw new Error("Atlas impact truncation summary changed");
  return summary;
}

function validateImpactReport(value, expected) {
  validateAtlasNumericPrecision(value);
  const report = object(value, "Atlas recipe impact report");
  exactKeys(report, [
    "analysis_model", "bounds", "context", "direct", "evidence_gaps", "format",
    "frontiers", "progression_signals", "propagation", "scenario", "schema_version",
    "selection", "summary", "unknowns",
  ], "Atlas recipe impact report");
  if (report.format !== FORMAT || report.schema_version !== 1) {
    throw new Error("Atlas recipe impact report identity changed");
  }
  const context = validateAtlasContext(report.context, expected.root);
  if (context.context_type !== "categorical-graph-v2"
      || context.capabilities.recipe_search !== true
      || context.capabilities.reachability !== false) {
    throw new Error("Atlas recipe impact requires one explicit finite-recipe graph without reachability claims");
  }
  const selection = nodeSummary(report.selection, "Atlas recipe impact selection", "gt-recipe");
  if (selection.selection_id !== expected.selectionId) {
    throw new Error("Atlas recipe impact selection does not match the requested recipe");
  }
  const scenario = object(report.scenario, "Atlas recipe impact scenario");
  exactKeys(scenario, ["change_interpretation", "kind", "selected_recipe_assumed_unavailable"], "Atlas recipe impact scenario");
  if (scenario.kind !== "remove-exact-observed-recipe"
      || scenario.selected_recipe_assumed_unavailable !== true) {
    throw new Error("Atlas recipe impact scenario changed");
  }
  text(scenario.change_interpretation, "Atlas recipe impact change interpretation");
  const model = object(report.analysis_model, "Atlas recipe impact analysis model");
  exactKeys(model, ["claim_boundary", "kind", "recipe_at_risk_rule", "resource_at_risk_rule"], "Atlas recipe impact analysis model");
  if (model.kind !== "bounded-observed-finite-recipe-dependency-exposure"
      || model.claim_boundary !== "candidate dead paths inside observed finite recipe structure; not gameplay reachability") {
    throw new Error("Atlas recipe impact claim boundary changed");
  }
  text(model.recipe_at_risk_rule, "Atlas recipe at-risk rule");
  text(model.resource_at_risk_rule, "Atlas resource at-risk rule");
  const bounds = object(report.bounds, "Atlas recipe impact bounds");
  exactKeys(bounds, ["max_depth", "max_nodes", "visited_node_count"], "Atlas recipe impact bounds");
  if (bounds.max_depth !== expected.maxDepth || bounds.max_nodes !== expected.maxNodes) {
    throw new Error("Atlas recipe impact bounds do not match the request");
  }
  integer(bounds.visited_node_count, "Atlas visited node count", 1, bounds.max_nodes);
  const direct = validateDirect(report.direct, bounds.max_nodes);
  const propagation = validatePropagation(report.propagation, bounds.max_depth, bounds.max_nodes);
  const progression = validateProgression(report.progression_signals, bounds.max_depth, bounds.max_nodes);
  const frontiers = validateFrontiers(report.frontiers, bounds.max_depth, bounds.max_nodes);
  if (propagation.status === "truncated" && frontiers.length === 0) {
    throw new Error("Atlas propagation frontier state changed");
  }
  const unknowns = validateUnknowns(report.unknowns);
  const gaps = validateEvidenceGaps(report.evidence_gaps);
  const summary = validateSummary(report.summary, direct, propagation, progression, frontiers.length > 0);
  return Object.freeze({
    ...report,
    analysis_model: Object.freeze({ ...model }),
    bounds: Object.freeze({ ...bounds }),
    context,
    direct: Object.freeze({ ...direct }),
    evidence_gaps: Object.freeze([...gaps]),
    frontiers: Object.freeze([...frontiers]),
    progression_signals: Object.freeze({ ...progression }),
    propagation: Object.freeze({ ...propagation }),
    scenario: Object.freeze({ ...scenario }),
    selection: Object.freeze({ ...selection }),
    summary: Object.freeze({ ...summary }),
    unknowns: Object.freeze([...unknowns]),
  });
}

function validateImpactSearchLink(report, priorSearch, priorSelection) {
  if (priorSearch == null && priorSelection == null) return report;
  if (!priorSearch || !priorSelection || priorSelection.selection_id !== report.selection.selection_id) {
    throw new Error("Atlas impact search linkage is incomplete or changed");
  }
  if (!isDeepStrictEqual(priorSearch.context, report.context)) {
    throw new Error("Atlas impact graph context changed after exact recipe search");
  }
  if (!isDeepStrictEqual(priorSelection, report.selection)) {
    throw new Error("Atlas impact recipe changed after exact recipe search");
  }
  return report;
}

async function invokeAtlasImpact(executable, root, selectionId, maxDepth, maxNodes, options = {}) {
  const selectedId = text(selectionId, "Atlas recipe impact selection ID");
  const depth = integer(maxDepth, "Atlas recipe impact maximum depth", 1, 12);
  const nodes = integer(maxNodes, "Atlas recipe impact maximum nodes", 10, 2000);
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const transportedRoot = pathForCoreLaunch(root, launch, "Atlas recipe graph root");
  const mappedRoot = launch.host === "native"
    ? path.resolve(options.cwd || process.cwd(), transportedRoot)
    : transportedRoot;
  const value = await invokeCoreJson(executable, [
    "atlas", "recipes", "impact", mappedRoot, selectedId,
    "--max-depth", String(depth), "--max-nodes", String(nodes), "--json",
  ], {
    ...options,
    launch,
    maximumOutput: 48 * 1024 * 1024,
    timeoutMs: options.timeoutMs === undefined ? 20 * 60 * 1000 : options.timeoutMs,
    label: "Atlas recipe impact",
  });
  const report = validateImpactReport(value, {
    maxDepth: depth,
    maxNodes: nodes,
    root: mappedRoot,
    selectionId: selectedId,
  });
  return validateImpactSearchLink(report, options.priorSearch, options.priorSelection);
}

module.exports = {
  FORMAT,
  invokeAtlasImpact,
  validateImpactReport,
  validateImpactSearchLink,
};
