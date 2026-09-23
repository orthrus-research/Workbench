"use strict";

const crypto = require("node:crypto");
const path = require("node:path");
const { invokeCoreText, parseBoundedJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");
const { validateAtlasContext, validateAtlasNumericPrecision } = require("./developerToolsClient");
const { validateImpactSearchLink } = require("./atlasRecipeImpactClient");

const FORMAT = "workbench-atlas-recipe-impact-report-v2";
const rawReports = new WeakMap();
const GAP_CODES = ["counterfactual-runtime-not-observed", "non-recipe-acquisition-not-assessed",
  "dynamic-recipes-not-executed", "stoichiometry-and-chance-not-assessed", "progression-reachability-not-proven",
  "task-execution-not-invoked", "pack-tier-policy-not-applied"];

function requireThat(condition, message) {
  if (!condition) throw new Error(`Atlas complete impact ${message}`);
}
function object(value, label) {
  requireThat(value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype, `${label} must be an ordinary object`);
  return value;
}
function keys(value, expected, label) {
  object(value, label);
  requireThat(Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key)), `${label} fields changed`);
  return value;
}
function text(value, label) {
  requireThat(typeof value === "string" && value.length > 0 && !value.includes("\0"), `${label} must be nonempty text`);
  return value;
}
function integer(value, label, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  requireThat(Number.isSafeInteger(value) && value >= minimum && value <= maximum, `${label} is invalid`);
  return value;
}
function bool(value, label) {
  requireThat(typeof value === "boolean", `${label} must be boolean`);
}
function list(value, label) {
  requireThat(Array.isArray(value), `${label} must be an array`);
  return value;
}
function strings(value, label, sorted = false) {
  list(value, label).forEach(item => text(item, label));
  if (sorted) requireThat(value.every((item, index) => index === 0 || value[index - 1] < item), `${label} must be sorted and unique`);
  return value;
}
function node(value, label, kind = undefined) {
  keys(value, ["evidence", "kind", "properties", "selection_id", "semantic_key"], label);
  for (const key of ["kind", "selection_id", "semantic_key"]) text(value[key], `${label}.${key}`);
  if (kind !== undefined) requireThat(value.kind === kind, `${label} kind changed`);
  object(value.properties, `${label} properties`);
  list(value.evidence, `${label} evidence`).forEach(row => object(row, `${label} evidence row`));
}
function edge(value, label, withNode = false) {
  keys(value, ["evidence", "properties", "relation", "semantic_key", ...(withNode ? ["node"] : [])], label);
  object(value.properties, `${label} properties`);
  list(value.evidence, `${label} evidence`).forEach(row => object(row, `${label} evidence row`));
  text(value.relation, `${label} relation`); text(value.semantic_key, `${label} semantic key`);
  if (withNode && value.node !== null) node(value.node, `${label} node`);
}
function directInput(value) {
  keys(value, ["evidence", "node", "properties", "relation", "selector", "selector_edge", "semantic_key"], "direct input");
  const { selector, selector_edge: selectorEdge, ...nodeEdge } = value;
  edge(nodeEdge, "direct input edge", true);
  if (selector !== null) node(selector, "direct input selector");
  keys(selectorEdge, ["evidence", "properties", "relation"], "selector edge");
  list(selectorEdge.evidence, "selector edge evidence"); object(selectorEdge.properties, "selector edge properties");
  text(selectorEdge.relation, "selector edge relation");
}
function direct(value, selectedActive) {
  keys(value, ["inputs", "outputs"], "direct section");
  list(value.inputs, "direct inputs").forEach(directInput);
  list(value.outputs, "direct outputs").forEach(output => {
    keys(output, ["downstream_consumers", "downstream_consumers_truncated", "downstream_consumers_evidence_complete", "output", "producer_portfolio"], "direct output");
    edge(output.output, "output edge", true);
    requireThat(output.downstream_consumers_truncated === false, "complete consumers cannot be truncated");
    bool(output.downstream_consumers_evidence_complete, "consumer evidence completeness");
    list(output.downstream_consumers, "consumers").forEach(consumer => {
      keys(consumer, ["acceptance_edge", "recipe", "selector"], "consumer");
      node(consumer.recipe, "consumer recipe", "gt-recipe"); node(consumer.selector, "consumer selector");
      edge(consumer.acceptance_edge, "consumer acceptance edge");
    });
    const portfolio = keys(output.producer_portfolio, ["alternative_producers", "status", "truncated", "viability", "evidence_complete"], "producer portfolio");
    bool(portfolio.evidence_complete, "producer evidence completeness");
    requireThat(portfolio.truncated === false && portfolio.viability === "not-assessed", "producer viability or truncation changed");
    requireThat(["sole-observed-finite-producer", "no-observed-active-producer", "observed-alternatives-present", "producer-evidence-incomplete"].includes(portfolio.status), "producer status changed");
    requireThat((portfolio.status === "producer-evidence-incomplete") === !portfolio.evidence_complete, "producer status disagrees with evidence completeness");
    list(portfolio.alternative_producers, "producer alternatives").forEach(alternative => {
      keys(alternative, ["output_edge", "recipe"], "alternative producer");
      edge(alternative.output_edge, "alternative output edge"); node(alternative.recipe, "alternative recipe", "gt-recipe");
      requireThat(alternative.recipe.properties.lookup_active === true, "alternative producer is not lookup-active");
    });
    if (portfolio.evidence_complete) {
      const status = portfolio.alternative_producers.length ? "observed-alternatives-present"
        : selectedActive === true ? "sole-observed-finite-producer" : "no-observed-active-producer";
      requireThat(typeof selectedActive === "boolean" && portfolio.status === status, "producer status disagrees with lookup state or alternatives");
    }
  });
}

function components(value, graphNodes, { quest = false } = {}) {
  const componentIds = new Set();
  const allMembers = new Set();
  list(value, "dependency components").forEach(component => {
    keys(component, ["component_id", "member_node_ids", "root_resource_ids", "witness", "interpretation", "viability_effect"], "dependency component");
    text(component.component_id, "component identity");
    requireThat(!componentIds.has(component.component_id), "component identity repeats"); componentIds.add(component.component_id);
    strings(component.member_node_ids, "component members", true);
    requireThat(component.member_node_ids.length > 0 && component.member_node_ids.length <= graphNodes, "component member count is invalid");
    const digest = crypto.createHash("sha256").update(JSON.stringify(component.member_node_ids), "utf8").digest("hex");
    requireThat(component.component_id === `workbench-atlas-dependency-component-v2:sha256:${digest}`, "component identity differs from its members");
    const members = new Set(component.member_node_ids);
    for (const member of members) { requireThat(!allMembers.has(member), "SCC membership overlaps"); allMembers.add(member); }
    strings(component.root_resource_ids, "component roots", true);
    if (quest) requireThat(component.root_resource_ids.length === 0, "quest component cannot have output roots");
    requireThat(component.viability_effect === "unknown", "cycle viability must remain unknown");
    text(component.interpretation, "component interpretation");
    const witness = keys(component.witness, ["node_ids", "arcs"], "component witness");
    strings(witness.node_ids, "witness nodes"); list(witness.arcs, "witness arcs");
    requireThat(witness.node_ids.length >= 2 && witness.node_ids[0] === witness.node_ids.at(-1)
      && witness.arcs.length === witness.node_ids.length - 1, "cycle witness is not closed");
    requireThat(witness.node_ids.every(id => members.has(id)), "witness leaves its component");
    witness.arcs.forEach((arc, index) => {
      keys(arc, ["source_id", "target_id", "edge_id", "relation", "direction"], "witness arc");
      for (const key of ["source_id", "target_id", "edge_id", "relation"]) text(arc[key], `witness ${key}`);
      requireThat(["forward", "reverse"].includes(arc.direction), "witness direction changed");
      requireThat(arc.source_id === witness.node_ids[index] && arc.target_id === witness.node_ids[index + 1], "witness arc endpoints disagree with its path");
    });
  });
}

function propagation(value, graphNodes) {
  keys(value, ["status", "at_risk_resources", "at_risk_recipes", "alternative_dependency_components"], "propagation");
  requireThat(["complete-within-model", "evidence-incomplete"].includes(value.status), "propagation status changed");
  list(value.at_risk_resources, "candidate resources").forEach(candidate => {
    keys(candidate, ["all_observed_finite_producers", "depth", "reason", "resource"], "candidate resource");
    node(candidate.resource, "candidate resource node"); integer(candidate.depth, "elimination round");
    requireThat(candidate.reason === "all-observed-finite-producers-are-unavailable-candidates", "resource candidate reason changed");
    strings(candidate.all_observed_finite_producers, "candidate producers");
  });
  list(value.at_risk_recipes, "candidate recipes").forEach(candidate => {
    keys(candidate, ["blocked_selectors", "depth", "reason", "recipe"], "candidate recipe");
    node(candidate.recipe, "candidate recipe node", "gt-recipe"); integer(candidate.depth, "recipe elimination round", 1);
    requireThat(candidate.reason === "at-least-one-selector-has-only-at-risk-observed-alternatives", "recipe candidate reason changed");
    list(candidate.blocked_selectors, "blocked selectors").forEach(blocked => {
      keys(blocked, ["accepted_resources", "selector"], "blocked selector"); node(blocked.selector, "blocked selector node");
      list(blocked.accepted_resources, "blocked alternatives").forEach(resource => node(resource, "blocked alternative"));
    });
  });
  components(value.alternative_dependency_components, graphNodes);
}
function questRequirement(value) {
  keys(value, ["accepted_ore_dictionary_keys", "owner_edge", "quest", "requirement_occurrence", "requirement_semantics", "resource", "resource_edge", "resource_risk_status", "task", "task_edge"], "quest requirement");
  node(value.quest, "quest", "betterquesting-quest"); node(value.task, "task", "betterquesting-task-occurrence");
  node(value.requirement_occurrence, "requirement occurrence"); node(value.resource, "quest resource");
  requireThat(["ore-dictionary-selector-with-observed-item-representative", "exact-observed-resource-reference"].includes(value.requirement_semantics), "quest requirement semantics changed");
  requireThat(["producer-portfolio-changed", "observed-all-finite-producers-at-risk"].includes(value.resource_risk_status), "quest resource status changed");
  for (const field of ["resource_edge", "task_edge", "owner_edge"]) edge(value[field], field);
  list(value.accepted_ore_dictionary_keys, "accepted ore keys").forEach(ore => {
    keys(ore, ["acceptance_edge", "ore_dictionary_key"], "accepted ore key");
    edge(ore.acceptance_edge, "ore acceptance edge"); node(ore.ore_dictionary_key, "ore key");
  });
}
function progression(value, graphNodes) {
  keys(value, ["energy_and_machine_signals", "quest_signals"], "progression");
  const energy = keys(value.energy_and_machine_signals, ["interpretation", "machine_numeric_tiers_observed", "machines_observed_using_recipe_map", "observed_recipe_properties", "recipe_map", "truncated", "evidence_complete"], "machine signals");
  requireThat(energy.truncated === false, "complete machine signals cannot be truncated"); bool(energy.evidence_complete, "machine evidence completeness");
  text(energy.interpretation, "machine signal interpretation");
  keys(energy.observed_recipe_properties, ["duration", "eut", "hidden", "lookup_active", "recipe_map"], "observed properties");
  if (energy.recipe_map !== null) node(energy.recipe_map, "recipe map", "gt-recipe-map");
  list(energy.machines_observed_using_recipe_map, "machines").forEach(machine => {
    keys(machine, ["machine", "map_edge"], "machine binding"); node(machine.machine, "machine", "gt-machine"); edge(machine.map_edge, "machine map edge");
  });
  list(energy.machine_numeric_tiers_observed, "machine tiers").forEach(tier => integer(tier, "machine tier", Number.MIN_SAFE_INTEGER));
  const quest = keys(value.quest_signals, ["direct_resource_requirements", "interpretation", "prerequisite_cycle_components", "status", "structural_prerequisite_dependents"], "quest signals");
  requireThat(["observed-definition-references", "no-observed-reference", "evidence-unavailable", "evidence-incomplete"].includes(quest.status), "quest status changed");
  text(quest.interpretation, "quest interpretation");
  list(quest.direct_resource_requirements, "quest requirements").forEach(questRequirement);
  list(quest.structural_prerequisite_dependents, "quest dependents").forEach(dependent => {
    keys(dependent, ["depends_on", "depth", "owner_edge", "prerequisite_occurrence", "quest", "target_edge"], "quest dependent");
    node(dependent.quest, "dependent quest", "betterquesting-quest"); node(dependent.depends_on, "prerequisite quest", "betterquesting-quest");
    node(dependent.prerequisite_occurrence, "prerequisite occurrence", "betterquesting-prerequisite-occurrence");
    integer(dependent.depth, "quest BFS distance"); edge(dependent.target_edge, "prerequisite target"); edge(dependent.owner_edge, "prerequisite owner");
  });
  components(quest.prerequisite_cycle_components, graphNodes, { quest: true });
}

function validateCompleteImpactReport(value, expected) {
  validateAtlasNumericPrecision(value);
  keys(value, ["format", "schema_version", "context", "selection", "scenario", "analysis_model", "exploration", "evidence_completeness", "direct", "propagation", "progression_signals", "frontiers", "unknowns", "evidence_gaps", "summary"], "report");
  requireThat(value.format === FORMAT && value.schema_version === 2, "format or schema changed");
  const context = validateAtlasContext(value.context, expected.root);
  requireThat(context.context_type === "categorical-graph-v2" && context.capabilities.recipe_search && !context.capabilities.reachability, "requires a verified finite graph");
  node(value.selection, "selected recipe", "gt-recipe");
  requireThat(value.selection.selection_id === expected.selectionId, "selection differs from the request");
  const scenario = keys(value.scenario, ["change_interpretation", "kind", "selected_recipe_assumed_unavailable"], "scenario");
  requireThat(scenario.kind === "remove-exact-observed-recipe" && scenario.selected_recipe_assumed_unavailable === true, "scenario changed");
  text(scenario.change_interpretation, "removal interpretation");
  const model = keys(value.analysis_model, ["claim_boundary", "kind", "recipe_at_risk_rule", "resource_at_risk_rule"], "analysis model");
  requireThat(model.kind === "complete-observed-finite-recipe-dependency-exposure" && model.claim_boundary === "candidate dead paths inside observed finite recipe structure; not gameplay reachability", "claim boundary changed");
  requireThat(model.recipe_at_risk_rule === "at least one exact input selector accepts only resources already classified at risk"
    && model.resource_at_risk_rule === "every observed finite recipe producer is an unavailable candidate", "candidate rules changed");
  const exploration = keys(value.exploration, ["mode", "status", "graph_node_count", "graph_edge_count", "visited_node_count", "traversed_edge_count", "worklist_event_count", "cycle_algorithm"], "exploration");
  requireThat(exploration.mode === "complete-finite" && exploration.status === "complete" && exploration.cycle_algorithm === "iterative-scc", "exploration completeness changed");
  for (const field of ["graph_node_count", "graph_edge_count", "visited_node_count", "traversed_edge_count", "worklist_event_count"]) integer(exploration[field], field);
  requireThat(exploration.graph_node_count >= 1 && exploration.visited_node_count >= 1 && exploration.visited_node_count <= exploration.graph_node_count, "visited graph count changed");
  requireThat(exploration.traversed_edge_count <= exploration.graph_edge_count, "traversed edge count exceeds the finite graph");
  requireThat(exploration.graph_node_count === context.summary.node_count && exploration.graph_edge_count === context.summary.edge_count, "exploration counts differ from graph context");
  const evidence = keys(value.evidence_completeness, ["status", "graph_incomplete_selector_count", "graph_unknown_lookup_recipe_count", "encountered_incomplete_selector_ids", "encountered_unknown_lookup_recipe_ids"], "evidence completeness");
  integer(evidence.graph_incomplete_selector_count, "incomplete selector count", 0, exploration.graph_node_count);
  integer(evidence.graph_unknown_lookup_recipe_count, "unknown recipe lookup count", 0, context.recipe_count);
  strings(evidence.encountered_incomplete_selector_ids, "encountered incomplete selectors", true);
  strings(evidence.encountered_unknown_lookup_recipe_ids, "encountered unknown recipes", true);
  requireThat(evidence.encountered_incomplete_selector_ids.length <= evidence.graph_incomplete_selector_count
    && evidence.encountered_unknown_lookup_recipe_ids.length <= evidence.graph_unknown_lookup_recipe_count, "encountered evidence exceeds graph counts");
  requireThat(evidence.status === ((evidence.graph_incomplete_selector_count || evidence.graph_unknown_lookup_recipe_count) ? "incomplete" : "complete-within-declared-model"), "evidence completeness disagrees with graph gaps");
  direct(value.direct, value.selection.properties.lookup_active); propagation(value.propagation, exploration.graph_node_count); progression(value.progression_signals, exploration.graph_node_count);
  requireThat(list(value.frontiers, "frontiers").length === 0, "complete exploration cannot have traversal frontiers");
  const unknownIds = new Set();
  list(value.unknowns, "unknowns").forEach(unknown => {
    keys(unknown, ["code", "message", ...(Object.hasOwn(unknown, "subject_id") ? ["subject_id"] : [])], "unknown");
    text(unknown.code, "unknown code"); text(unknown.message, "unknown message");
    if (Object.hasOwn(unknown, "subject_id")) text(unknown.subject_id, "unknown subject");
    const id = JSON.stringify([unknown.code, unknown.subject_id ?? null]);
    requireThat(!unknownIds.has(id), "unknown identity repeats"); unknownIds.add(id);
  });
  const gaps = list(value.evidence_gaps, "evidence gaps");
  requireThat(gaps.length === GAP_CODES.length || gaps.length === GAP_CODES.length + 1, "evidence gap set changed");
  gaps.forEach((gap, index) => { keys(gap, ["code", "message"], "evidence gap"); text(gap.message, "gap message"); requireThat(gap.code === (GAP_CODES[index] || "graph-projection-limitations"), "evidence gap identity changed"); });
  const counts = {
    selected_output_count: value.direct.outputs.length,
    sole_observed_finite_producer_output_count: value.direct.outputs.filter(row => row.producer_portfolio.status === "sole-observed-finite-producer").length,
    at_risk_resource_candidate_count: value.propagation.at_risk_resources.length,
    at_risk_recipe_candidate_count: value.propagation.at_risk_recipes.length,
    quest_requirement_exposure_count: value.progression_signals.quest_signals.direct_resource_requirements.length,
    structural_quest_dependent_count: value.progression_signals.quest_signals.structural_prerequisite_dependents.length,
    alternative_dependency_component_count: value.propagation.alternative_dependency_components.length,
    quest_prerequisite_component_count: value.progression_signals.quest_signals.prerequisite_cycle_components.length,
  };
  keys(value.summary, [...Object.keys(counts), "truncated"], "summary");
  for (const [key, count] of Object.entries(counts)) requireThat(value.summary[key] === count, `summary ${key} disagrees with the report`);
  requireThat(value.summary.truncated === false, "complete summary cannot be truncated");
  return Object.freeze(value);
}

async function invokeCompleteAtlasImpact(executable, root, selectionId, options = {}) {
  text(selectionId, "selection ID");
  options.signal?.throwIfAborted();
  const launch = options.launch || resolveCoreLaunch(executable, { platform: options.platform, environment: options.environment });
  const transported = pathForCoreLaunch(root, launch, "Atlas recipe graph root");
  const mappedRoot = launch.host === "native" ? path.resolve(options.cwd || process.cwd(), transported) : transported;
  const raw = await invokeCoreText(executable, ["atlas", "recipes", "impact", mappedRoot, selectionId,
    "--exploration", "complete-finite", "--json"], { ...options, launch, maximumOutput: null, timeoutMs: null, rejectStderr: true, label: "Atlas complete recipe impact" });
  options.signal?.throwIfAborted();
  const report = validateCompleteImpactReport(parseBoundedJson(raw, null, "Atlas complete recipe impact"), { root: mappedRoot, selectionId });
  validateImpactSearchLink(report, options.priorSearch, options.priorSelection);
  rawReports.set(report, raw);
  return report;
}

module.exports = { FORMAT, validateCompleteImpactReport, invokeCompleteAtlasImpact,
  rawCompleteReport: report => rawReports.get(report) };
