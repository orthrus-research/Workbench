"use strict";
const { GROUPS } = require("./materialDeliveryClient");
const VIEW = "workbench.axiomResults";
const PREFIX = "workbench.axiomResults.";

function outcome(result) {
  if ((result.native_status === "source-error" || result.native?.status === "source-error")) return "Initialization stopped at a script error";
  if (result.native_outcome === "native-failed") return "Native initialization reported errors";
  if (result.coverage !== "complete") return "Initialization observations are incomplete";
  return "Selected initialization scope completed";
}
function groupTitle(group) {
  const [severity, location] = group.split("-");
  return `${severity === "information" ? "Information" : severity === "error" ? "Errors" : "Warnings"} · ${location === "located" ? "With source locations" : "No source location"}`;
}
function summary(result, fresh, loaded) {
  const counts = result.finding_counts;
  const count = severity => GROUPS.filter(group => group.startsWith(severity)).reduce((n, group) => n + counts[group], 0);
  const totals = counts ? `${count("error")} errors · ${count("warning")} warnings · ${count("information")} information` : `${result.findings_count} findings · legacy counts unavailable`;
  const source = fresh ? "Saved source matches" : "Source changed or not verified · captured source available";
  const details = { preparing: "Preparing recipe details", ready: "Recipe details ready", interrupted: "Recipe details interrupted", "not-started": "Recipe details not started" }[result.detail_state] || "Recipe details unavailable";
  return `${totals}. ${loaded} findings loaded. ${source}. ${result.coverage === "complete" ? "Selected scope observed" : "Observations incomplete"}. ${details}.`;
}

/** Persistent native tree; original evidence stays owned by Core. */
function createMaterialResults(vscode, context) {
  const change = new vscode.EventEmitter();
  let current, view, disposed = false;
  const provider = {
    onDidChangeTreeData: change.event,
    getParent: node => node.group ? undefined : ({ model: node.model, group: node.groupKey, label: node.groupKey === "all" ? "All findings · legacy revision" : groupTitle(node.groupKey) }),
    getTreeItem: node => {
      const item = new vscode.TreeItem(node.label, node.group ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
      item.id = `${node.model.result.diagnostic_id}:${node.group || node.finding?.id}`;
      item.description = node.group ? `${node.model.result.finding_counts?.[node.group] ?? node.model.result.findings_count} findings` : node.description;
      item.tooltip = node.tooltip || node.label;
      item.contextValue = node.group ? "axiomGroup" : node.finding.location ? "axiomLocated" : "axiomUnlocated";
      item.iconPath = new vscode.ThemeIcon(node.group ? (node.group.startsWith("error") ? "error" : node.group.startsWith("warning") ? "warning" : "info") : node.finding.location ? "file-code" : "output");
      item.accessibilityInformation = { label: `${node.label}. ${item.description || ""}` };
      if (!node.group) item.command = { command: PREFIX + "open", title: "Open finding", arguments: [node] };
      return item;
    },
    getChildren: async node => {
      const model = node?.model || current;
      if (!model || model !== current) return [];
      if (!node) return model.groups.map(group => ({ group, model, label: group === "all" ? "All findings · legacy revision" : groupTitle(group) }));
      if (!node.group) return [];
      if (!model.pages.has(node.group)) await load(model, node.group);
      if (model !== current) return [];
      return (model.pages.get(node.group)?.findings || []).map(finding => {
        const label = model.labels[finding.id] || finding.message;
        const location = finding.location;
        const abbreviated = label.split("\n")[0].replace(/\b(?:[a-z_]\w*\.)+([A-Z][\w$]*)/g, "$1");
        return { model, finding, groupKey: node.group, label: `${finding.side === "baseline" ? "Baseline · " : ""}${abbreviated.length > 100 ? abbreviated.slice(0, 99) + "…" : abbreviated}`,
          description: location ? `${location.path.split("/").pop()}:${location.start.line}` : "No source location",
          tooltip: `${location ? location.path + ":" + location.start.line + " · native call-site anchor" : "No source location"}\n${label.slice(0, 2000)}\nOpen original diagnostic for complete evidence.` };
      });
    },
  };
  function refresh() {
    if (!current || !view || disposed) return;
    view.title = "Axiom Results";
    view.description = current.result.native_outcome === "native-failed" ? "Errors observed" : "Native observations";
    view.message = outcome(current.result) + ". " + summary(current.result, current.fresh(), [...current.pages.values()].reduce((n, page) => n + page.findings.length, 0));
    void vscode.commands.executeCommand("setContext", "workbench.axiomDetailReady", current.result.detail_state === "ready");
  }
  async function load(model, group) {
    if (model !== current || model.pending.has(group)) return;
    const previous = model.pages.get(group);
    if (previous?.next_offset === null) return;
    model.pending.add(group); view.message = "Loading selected findings…";
    try {
      const page = await model.read(group === "all" ? undefined : group, previous?.next_offset ?? 0);
      if (model !== current || disposed) return;
      if (page.diagnostic_id !== model.result.diagnostic_id || page.request_id !== model.result.request_id) throw new Error("Findings belong to another run.");
      model.pages.set(group, { findings: [...(previous?.findings || []), ...page.findings], next_offset: page.next_offset });
      Object.assign(model.labels, page.presentation?.finding_labels || {});
      if (!page.sourceCurrent) model.stale = true;
      model.annotations([...model.pages.values()].flatMap(value => value.findings), model.fresh());
      refresh();
    } catch (error) {
      if (model === current) view.message = `Findings unavailable: ${error.message}. Previously loaded evidence is retained.`;
    } finally { model.pending.delete(group); }
  }
  async function command(action, node) {
    const model = node?.model || current;
    if (!model || model !== current || disposed) return;
    try {
      if (action === "next") {
        const group = node?.group || view.selection[0]?.group;
        if (group) { await load(model, group); if (model === current) change.fire(node); }
      } else if (action === "snapshot") {
        if (model.result.detail_state === "ready") await model.snapshot();
      } else if (action === "actions") await model.actions();
      else if (node?.finding) await model.open(action, node.finding, model.fresh());
    } catch (error) { if (model === current) await vscode.window.showWarningMessage(error.message); }
  }
  for (const action of ["open", "captured", "evidence", "next", "snapshot", "actions"])
    context.subscriptions.push(vscode.commands.registerCommand(PREFIX + action, node => command(action, node)));
  const invalidate = () => { if (current) { current.stale = true; refresh(); } };
  if (vscode.workspace.onDidChangeTextDocument) context.subscriptions.push(vscode.workspace.onDidChangeTextDocument(invalidate));
  context.subscriptions.push(change, { dispose() { disposed = true; current = undefined; } });
  return {
    async show(result, callbacks) {
      if (!view) { view = vscode.window.createTreeView(VIEW, { treeDataProvider: provider, showCollapseAll: true }); context.subscriptions.push(view); }
      current = { result, ...callbacks, pages: new Map(), labels: { ...result.presentation?.finding_labels }, pending: new Set(), stale: false };
      const model = current; model.fresh = () => !model.stale && callbacks.fresh();
      model.groups = result.finding_counts ? GROUPS.filter(group => result.finding_counts[group] > 0) : ["all"];
      // Reuse the already delivered page when it is a complete group prefix.
      for (const group of model.groups) {
        const rows = result.findings.filter(f => group === "all" || `${["error", "warning"].includes(f.severity) ? f.severity : "information"}-${f.location ? "located" : "unlocated"}` === group);
        if (rows.length) model.pages.set(group, { findings: rows, next_offset: rows.length === (result.finding_counts?.[group] ?? result.findings_count) ? null : rows.length });
      }
      await vscode.commands.executeCommand("setContext", "workbench.axiomHasResults", true);
      refresh(); change.fire();
      await vscode.commands.executeCommand(VIEW + ".focus");
    },
    update(result) { if (current?.result === result) refresh(); },
    invalidate,
    clear() { current = undefined; change.fire(); if (view) view.message = "Running a new saved check…"; },
  };
}
module.exports = { createMaterialResults, outcome, summary, groupTitle };
