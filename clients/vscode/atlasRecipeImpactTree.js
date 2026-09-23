"use strict";

const crypto = require("node:crypto");
const { FORMAT: COMPLETE_FORMAT, rawCompleteReport } = require("./atlasCompleteRecipeImpactClient");

const SECTION_TITLES = Object.freeze({
  summary: "Candidate summary and claim boundary",
  direct: "Direct observed recipe I/O",
  propagation: "Propagation candidates",
  progression_signals: "Progression signals",
  frontiers: "Traversal frontiers",
  unknowns: "Unknowns",
  evidence_gaps: "Evidence gaps",
});

function node(type, properties = {}) {
  return Object.freeze({ type, ...properties });
}

function impactCautions(report) {
  const frontiers = report.frontiers.length;
  const unknowns = report.unknowns.length;
  const cycles = report.propagation.alternative_dependency_cycle_signals?.length || 0;
  const inactive = new Set();
  for (const output of report.direct.outputs) {
    for (const alternative of output.producer_portfolio?.alternative_producers || []) {
      if (alternative.recipe.properties.lookup_active === false) inactive.add(alternative.recipe.selection_id);
    }
  }
  if (report.format === COMPLETE_FORMAT) {
    const components = report.propagation.alternative_dependency_components.length;
    const questComponents = report.progression_signals.quest_signals.prerequisite_cycle_components.length;
    const incomplete = report.evidence_completeness.status === "incomplete";
    const state = "Observed finite exploration complete";
    const text = `${state} · evidence ${incomplete ? "incomplete" : "complete within declared model"} · ${unknowns} unknown(s) · ${components} alternative dependency component(s) · ${questComponents} quest cycle component(s) · viability unknown`;
    return Object.freeze({ state, text, warning: incomplete || unknowns > 0 || components > 0 || questComponents > 0,
      frontiers, unknowns, cycles: components, questComponents, inactiveAlternatives: inactive.size });
  }
  const warning = frontiers > 0 || unknowns > 0 || cycles > 0 || inactive.size > 0;
  const state = frontiers ? "Bounded frontiers remain"
    : warning ? "Unresolved within bounds" : "Bounded analysis complete";
  const text = `${state} · ${frontiers} frontier(s) · ${unknowns} unknown(s) · ${cycles} alternative dependency cycle signal(s) · ${inactive.size} lookup-inactive alternative recipe(s)`;
  return Object.freeze({ state, text, warning, frontiers, unknowns, cycles, inactiveAlternatives: inactive.size });
}

function valueDescription(value) {
  if (typeof value === "string") return value;
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (typeof value.code === "string") return value.message || value.code;
  if (typeof value.selection_id === "string") return value.semantic_key || value.selection_id;
  if (typeof value.component_id === "string") return `${value.member_node_ids.length} members · ${value.root_resource_ids.length} affected resources · one cycle witness`;
  if (value.resource?.semantic_key) return value.resource.semantic_key;
  if (value.recipe?.semantic_key) return value.recipe.semantic_key;
  if (typeof value.kind === "string") return value.kind;
  return Array.isArray(value) ? `${value.length} entries` : `${Object.keys(value).length} fields`;
}

function arrayChildren(value, path, start = 0, end = value.length) {
  // Group by reference: no members are dropped or copied into an eager tree.
  const span = end - start;
  const width = span > 200 ? Math.ceil(span / 200) : 1;
  const result = [];
  for (let index = start; index < end; index += width) {
    const limit = Math.min(end, index + width);
    result.push(width > 1 ? node("range", { id: `${path}[${index}:${limit}]`, path, value, start: index, end: limit })
      : node("json", { id: `${path}[${index}]`, key: `#${index + 1}`, path: `${path}[${index}]`, value: value[index] }));
  }
  return result;
}

function childrenForJson(value, path, lazy = false) {
  if (Array.isArray(value)) {
    if (lazy) return arrayChildren(value, path);
    return value.map((child, index) => node("json", {
      id: `${path}[${index}]`,
      key: `#${index + 1}`,
      path: `${path}[${index}]`,
      value: child,
    }));
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).map(([key, child]) => node("json", {
      id: `${path}.${key}`,
      key,
      path: `${path}.${key}`,
      value: child,
    }));
  }
  return [];
}

class AtlasImpactDocuments {
  constructor(vscode) {
    this.vscode = vscode;
    this.contents = new Map();
  }

  provideTextDocumentContent(uri) {
    const value = this.contents.get(uri.toString());
    if (value === undefined) throw new Error("Atlas impact report is no longer retained");
    return value;
  }

  release(uri) {
    if (uri?.scheme === "workbench-atlas-impact") this.contents.delete(uri.toString());
  }

  report(value) {
    const complete = value.format === COMPLETE_FORMAT;
    const content = (complete && rawCompleteReport(value)) || `${JSON.stringify(value, null, 2)}\n`;
    if (!complete && Buffer.byteLength(content, "utf8") > 48 * 1024 * 1024) {
      throw new Error("Atlas impact report exceeds the native document boundary");
    }
    const digest = crypto.createHash("sha256").update(content, "utf8").digest("hex");
    const uri = this.vscode.Uri.from({
      scheme: "workbench-atlas-impact",
      path: `/reports/${digest}/recipe-impact.json`,
    });
    if (!this.contents.has(uri.toString()) && this.contents.size >= 128) {
      throw new Error("Atlas impact report document registry exceeds its boundary");
    }
    this.contents.set(uri.toString(), content);
    return uri;
  }
}

class AtlasImpactTreeProvider {
  constructor(vscode, documents) {
    this.vscode = vscode;
    this.documents = documents;
    this.report = null;
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }

  dispose() {
    this.emitter.dispose();
  }

  setReport(report) {
    this.report = report;
    this.emitter.fire(undefined);
  }

  getChildren(element) {
    const complete = this.report?.format === COMPLETE_FORMAT;
    if (!element) {
      if (!this.report) return [node("load", { id: "atlas-impact-load" })];
      return [node("cautions", { id: "atlas-impact-cautions" }), ...Object.keys(SECTION_TITLES).map((section) => node("section", {
        id: `atlas-impact-section:${section}`,
        section,
      }))];
    }
    if (element.type === "section") {
      if (element.section === "summary") {
        return ["selection", "scenario", "analysis_model", ...(complete ? ["exploration", "evidence_completeness"] : ["bounds"]), "summary", "context"]
          .map((key) => node("json", {
            id: `atlas-impact-summary.${key}`,
            key,
            path: key,
            value: this.report[key],
          }));
      }
      return childrenForJson(this.report[element.section], element.section, complete);
    }
    if (element.type === "range") return arrayChildren(element.value, element.path, element.start, element.end);
    if (element.type === "json") return childrenForJson(element.value, element.path, complete);
    return [];
  }

  getTreeItem(element) {
    const api = this.vscode;
    const none = api.TreeItemCollapsibleState.None;
    const collapsed = api.TreeItemCollapsibleState.Collapsed;
    let item;
    if (element.type === "load") {
      item = new api.TreeItem("Analyze one observed recipe", none);
      item.command = {
        command: "workbench.atlas.analyzeRecipeImpact",
        title: "Analyze one observed recipe",
      };
      item.iconPath = new api.ThemeIcon("pulse");
      item.tooltip = "Choose an explicit verified Atlas graph and one exact observed GT recipe.";
    } else if (element.type === "cautions") {
      const cautions = impactCautions(this.report);
      item = new api.TreeItem(cautions.state, none);
      item.description = cautions.text.slice(cautions.state.length + 3);
      item.tooltip = `${cautions.text}\nObserved alternatives and cycle signals do not establish viability. Review the exact paths, unknowns, and evidence gaps.`;
      item.iconPath = new api.ThemeIcon(cautions.warning ? "warning" : "info");
    } else if (element.type === "section") {
      item = new api.TreeItem(SECTION_TITLES[element.section], collapsed);
      const icons = {
        summary: "info",
        direct: "references",
        propagation: "type-hierarchy-sub",
        progression_signals: "graph-line",
        frontiers: "debug-disconnect",
        unknowns: "question",
        evidence_gaps: "warning",
      };
      item.iconPath = new api.ThemeIcon(icons[element.section]);
      if (element.section === "propagation") {
        item.description = `${this.report.summary.at_risk_resource_candidate_count} resource · ${this.report.summary.at_risk_recipe_candidate_count} recipe candidates`;
      } else if (["frontiers", "unknowns", "evidence_gaps"].includes(element.section)) {
        item.description = `${this.report[element.section].length}`;
      } else if (element.section === "progression_signals") {
        item.description = `${this.report.summary.quest_requirement_exposure_count} quest requirement signals`;
      }
    } else if (element.type === "range") {
      item = new api.TreeItem(`Entries ${element.start + 1}–${element.end}`, collapsed);
      item.description = `${element.end - element.start} entries`;
      item.tooltip = "Expand to inspect every member. The full report retains all entries.";
    } else {
      const expandable = element.value !== null && typeof element.value === "object";
      item = new api.TreeItem(element.key, expandable ? collapsed : none);
      item.description = valueDescription(element.value);
      item.tooltip = expandable
        ? `${element.path}\n${valueDescription(element.value)}`
        : `${element.path}: ${valueDescription(element.value)}`;
      if (!expandable && typeof element.value === "boolean") {
        item.iconPath = new api.ThemeIcon(element.value ? "pass-filled" : "circle-slash");
      } else if (element.value?.code) {
        item.iconPath = new api.ThemeIcon("warning");
      }
    }
    item.id = element.id;
    return item;
  }

  async openReport() {
    if (!this.report) throw new Error("Run one Atlas recipe impact analysis first");
    const uri = this.documents.report(this.report);
    const document = await this.vscode.workspace.openTextDocument(uri);
    await this.vscode.window.showTextDocument(document, { preview: true });
    return this.report;
  }
}

module.exports = {
  AtlasImpactDocuments,
  AtlasImpactTreeProvider,
  childrenForJson,
  impactCautions,
};
