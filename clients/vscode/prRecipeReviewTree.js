"use strict";

const crypto = require("node:crypto");

const SECTION_ORDER = Object.freeze([
  "decision", "scope", "recipes", "files", "attention", "next",
]);
const SECTION_LABELS = Object.freeze({
  decision: "Decision summary",
  scope: "Pull request scope",
  recipes: "Recipe findings",
  files: "Changed files",
  attention: "Attention and limits",
  next: "Next steps",
});
const SECTION_ICONS = Object.freeze({
  decision: "checklist",
  scope: "git-pull-request",
  recipes: "beaker",
  files: "files",
  attention: "warning",
  next: "arrow-right",
});

function node(type, properties = {}) {
  return Object.freeze({ type, ...properties });
}

function compact(value, maximum = 160) {
  const rendered = value === null || value === undefined ? "not supplied" : String(value);
  return rendered.length <= maximum ? rendered : `${rendered.slice(0, maximum - 1)}…`;
}

function plural(value, word) {
  return `${value} ${word}${value === 1 ? "" : "s"}`;
}

function recipeLabel(row, kind, index) {
  if (kind === "modified") {
    return compact(row.after_semantic_key || row.before_semantic_key || `Modified recipe ${index + 1}`);
  }
  return compact(row.semantic_key || `${kind === "added" ? "Added" : "Removed"} recipe ${index + 1}`);
}

function recipeSource(row, kind) {
  if (kind === "modified") {
    return row.after_source?.path && row.after_source.path !== "unknown"
      ? row.after_source.path : row.before_source?.path;
  }
  return row.source?.path;
}

function currentWorkspacePath(report, logicalPath) {
  if (!logicalPath || logicalPath === "unknown") return null;
  const selected = report.selection.committed_scope.selected.paths;
  if (selected.includes(logicalPath)) return logicalPath;
  const matches = selected.filter((path) => path.endsWith(`/${logicalPath}`));
  return matches.length === 1 ? matches[0] : null;
}

function decisionChildren(report) {
  const summary = report.summary;
  const recipes = summary.machine_recipes;
  const removals = summary.direct_removal_source_statements;
  const attention = report.selection.attention_scope;
  return [
    node("value", { id: "pr-review:decision:status", label: "Compact report decision", value: summary.status,
      icon: summary.status === "ready" ? "pass-filled" : summary.status === "blocked" ? "error" : "warning" }),
    node("value", { id: "pr-review:decision:introduced", label: "PR-introduced static signals",
      value: attention.introduced_static_signals,
      icon: attention.introduced_static_signals ? "warning" : "pass-filled" }),
    node("value", { id: "pr-review:decision:preexisting", label: "Pre-existing static signals",
      value: attention.preexisting_static_signals, icon: "history" }),
    node("value", { id: "pr-review:decision:runtime-attention", label: "Supplied-runtime attention",
      value: attention.supplied_runtime_attention ? "present" : "not present",
      icon: attention.supplied_runtime_attention ? "warning" : "pass-filled" }),
    node("value", { id: "pr-review:decision:analysis", label: "Static analysis", value: summary.analysis_state }),
    node("value", { id: "pr-review:decision:comparison", label: "Comparison", value: summary.comparison_state }),
    node("value", { id: "pr-review:decision:runtime", label: "Runtime evidence", value: summary.runtime_state }),
    node("value", { id: "pr-review:decision:files", label: "Changed source files",
      value: summary.changed_source_files }),
    node("value", { id: "pr-review:decision:recipes", label: "Machine recipes",
      value: `${recipes.modified} modified · ${recipes.added} added · ${recipes.removed} removed` }),
    node("value", { id: "pr-review:decision:removals", label: "Direct removal statements",
      value: `${removals.added} added · ${removals.removed} removed${removals.counts_incomplete ? " · incomplete" : ""}` }),
  ];
}

function scopeChildren(report) {
  const selection = report.selection;
  return [
    node("value", { id: "pr-review:scope:pr", label: `Pull request #${selection.pull_request}`,
      value: `${selection.pull_request_state}${selection.pull_request_merged ? " · merged" : ""}`,
      tooltip: selection.pull_request_url, icon: "git-pull-request" }),
    node("value", { id: "pr-review:scope:delta", label: "Reviewed delta", value: selection.delta_kind }),
    node("value", { id: "pr-review:scope:base", label: "Provider-recorded base",
      value: `${selection.base.name} · ${selection.base.oid}`,
      tooltip: `${selection.base.repository}\n${selection.base.remote_ref}\n${selection.base.immutable_ref}`,
      icon: "git-commit" }),
    node("value", { id: "pr-review:scope:head", label: "Provider-recorded head",
      value: `${selection.head.name} · ${selection.head.oid}`,
      tooltip: `${selection.head.repository}\n${selection.head.remote_ref}\n${selection.head.immutable_ref}`,
      icon: "git-commit" }),
    node("value", { id: "pr-review:scope:repository", label: "Repository changes",
      value: plural(selection.committed_scope.repository.path_count, "file") }),
    node("value", { id: "pr-review:scope:selected", label: "Recipe-review scope",
      value: plural(selection.committed_scope.selected.path_count, "file") }),
    node("value", { id: "pr-review:scope:excluded", label: "Outside recipe-review scope",
      value: plural(selection.committed_scope.excluded.path_count, "file") }),
    node("value", { id: "pr-review:scope:hygiene", label: "Git hygiene",
      value: selection.git_hygiene.state, tooltip: selection.git_hygiene.detail,
      icon: selection.git_hygiene.state === "clean" ? "pass-filled"
        : selection.git_hygiene.state === "attention" ? "warning" : "question" }),
  ];
}

function findingGroups(report) {
  const groups = [];
  for (const kind of ["modified", "added", "removed"]) {
    const rows = report.machine_recipes[kind];
    groups.push(node("recipe-group", {
      id: `pr-review:recipes:${kind}`,
      kind,
      label: `${kind[0].toUpperCase()}${kind.slice(1)} machine recipes`,
      count: rows.row_count,
      truncated: rows.truncated,
      rows: rows.rows,
    }));
  }
  for (const kind of ["added", "removed"]) {
    const rows = report.direct_removal_calls[kind];
    groups.push(node("direct-group", {
      id: `pr-review:direct:${kind}`,
      kind,
      label: `${kind[0].toUpperCase()}${kind.slice(1)} direct removal calls`,
      count: rows.row_count,
      truncated: rows.truncated,
      rows: rows.rows,
    }));
  }
  return groups;
}

function fileGroups(report) {
  return ["modified", "added", "removed"].map((kind) => node("file-group", {
    id: `pr-review:files:${kind}`,
    kind,
    label: `${kind[0].toUpperCase()}${kind.slice(1)} files`,
    count: report.files[kind].path_count,
    truncated: report.files[kind].truncated,
    paths: report.files[kind].paths,
  }));
}

function attentionChildren(report) {
  const scope = report.selection.attention_scope;
  return [
    node("attention-group", {
      id: "pr-review:attention:pr",
      group: "pr",
      label: "PR-introduced and supplied-runtime attention",
      count: scope.introduced_static_signals + (scope.supplied_runtime_attention ? 1 : 0),
    }),
    node("attention-group", {
      id: "pr-review:attention:candidate",
      group: "candidate",
      label: "Pre-existing and candidate-wide attention",
      count: scope.preexisting_static_signals + report.attention.strict_reasons.length
        + report.attention.configuration_warnings.length
        + (report.selection.git_hygiene.state === "attention" ? 1 : 0),
    }),
    node("attention-group", {
      id: "pr-review:attention:limits",
      group: "limits",
      label: "Evidence limits",
      count: report.limitations.length,
    }),
  ];
}

function attentionGroupChildren(report, group) {
  const scope = report.selection.attention_scope;
  if (group === "pr") return [
    node("attention", {
      id: "pr-review:attention:pr:introduced",
      label: "PR-introduced static signals",
      value: scope.introduced_static_signals,
      icon: scope.introduced_static_signals ? "warning" : "pass-filled",
    }),
    node("attention", {
      id: "pr-review:attention:pr:runtime",
      label: "Supplied-runtime attention",
      value: scope.supplied_runtime_attention ? "present" : "not present",
      icon: scope.supplied_runtime_attention ? "warning" : "pass-filled",
    }),
    node("attention", {
      id: "pr-review:attention:pr:boundary",
      label: "PR strict scope",
      value: scope.pr_strict,
      icon: "shield",
    }),
  ];
  if (group === "candidate") {
    const rows = [
      node("attention", {
        id: "pr-review:attention:candidate:preexisting",
        label: "Pre-existing static signals",
        value: scope.preexisting_static_signals,
        icon: "history",
      }),
      node("attention", {
        id: "pr-review:attention:candidate:total",
        label: "Candidate-wide static signal total",
        value: scope.candidate_static_signal_total,
        icon: "list-ordered",
      }),
      node("attention", {
        id: "pr-review:attention:candidate:hygiene",
        label: "Git hygiene",
        value: `${report.selection.git_hygiene.state} · ${report.selection.git_hygiene.detail}`,
        icon: report.selection.git_hygiene.state === "clean" ? "pass-filled"
          : report.selection.git_hygiene.state === "attention" ? "warning" : "question",
      }),
    ];
    report.attention.strict_reasons.forEach((value, index) => rows.push(node("attention", {
      id: `pr-review:attention:candidate:strict:${index}`,
      label: "Candidate-wide decision reason", value, icon: "warning",
    })));
    report.attention.configuration_warnings.forEach((value, index) => rows.push(node("attention", {
      id: `pr-review:attention:candidate:configuration:${index}`,
      label: "Source configuration warning", value,
      tooltip: "This warning belongs to candidate-wide strict-all context, not PR-introduced static signals.",
      icon: "info",
    })));
    rows.push(node("attention", {
      id: "pr-review:attention:candidate:boundary",
      label: "Candidate-wide strict-all scope", value: scope.strict_all, icon: "shield",
    }));
    return rows;
  }
  return report.limitations.map((value, index) => node("attention", {
    id: `pr-review:attention:limit:${index}`, label: "Evidence limit", value,
    icon: "circle-slash",
  }));
}

function nextChildren(report) {
  return report.next_actions.map((action, index) => node("next", {
    id: `pr-review:next:${index}`,
    label: action.description,
    value: action.command_hint,
    action,
  }));
}

class PrRecipeReviewDocuments {
  constructor(vscode) {
    this.vscode = vscode;
    this.contents = new Map();
  }

  provideTextDocumentContent(uri) {
    const content = this.contents.get(uri.toString());
    if (content === undefined) throw new Error("PR Recipe Review document is no longer retained");
    return content;
  }

  release(uri) {
    if (uri?.scheme === "workbench-pr-review") this.contents.delete(uri.toString());
  }

  retain(path, value) {
    const content = `${JSON.stringify(value, null, 2)}\n`;
    if (Buffer.byteLength(content, "utf8") > 16 * 1024 * 1024) {
      throw new Error("PR Recipe Review document exceeds its byte boundary");
    }
    const digest = crypto.createHash("sha256").update(content, "utf8").digest("hex");
    const uri = this.vscode.Uri.from({
      scheme: "workbench-pr-review",
      path: `/${digest}/${path}`,
    });
    if (!this.contents.has(uri.toString()) && this.contents.size >= 256) {
      throw new Error("PR Recipe Review document registry exceeds its boundary");
    }
    this.contents.set(uri.toString(), content);
    return uri;
  }

  report(report) {
    return this.retain("recipe-review.json", report);
  }

  recipeSide(report, item, side) {
    if (item.type !== "recipe" || item.kind !== "modified") {
      throw new Error("A modified recipe finding is required for native diff");
    }
    const row = item.row;
    const changes = row.property_changes || {};
    const properties = {};
    for (const [key, change] of Object.entries(changes)) {
      properties[key] = change?.[side] === undefined ? null : change[side];
    }
    return this.retain(`modified-recipe-${item.index}-${side}.json`, {
      format: "workbench-recipe-property-projection-v1",
      report_id: report.report_id,
      projection_boundary: "bounded changed properties from the exact compact owner report",
      side,
      semantic_key: side === "before" ? row.before_semantic_key : row.after_semantic_key,
      source: side === "before" ? row.before_source : row.after_source,
      recipe_map: row.recipe_map,
      properties,
      properties_truncated: row.properties_truncated,
      pairing_basis: row.pairing_basis,
    });
  }
}

class PrRecipeReviewTreeProvider {
  constructor(vscode, documents) {
    this.vscode = vscode;
    this.documents = documents;
    this.plan = null;
    this.report = null;
    this.failure = null;
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }

  dispose() {
    this.emitter.dispose();
  }

  reset() {
    this.plan = null;
    this.report = null;
    this.failure = null;
    this.emitter.fire(undefined);
  }

  setPlan(plan) {
    this.plan = plan;
    this.report = null;
    this.failure = null;
    this.emitter.fire(undefined);
  }

  setReport(report) {
    this.report = report;
    this.failure = null;
    this.emitter.fire(undefined);
  }

  setFailure(message) {
    this.report = null;
    this.failure = compact(message, 4000);
    this.emitter.fire(undefined);
  }

  getChildren(element) {
    if (!element) {
      if (this.failure) return [node("failure", { id: "pr-review:failure", message: this.failure })];
      if (this.report) return SECTION_ORDER.map((section) => node("section", {
        id: `pr-review:section:${section}`, section,
      }));
      if (this.plan) return [
        node("plan-section", { id: "pr-review:plan:identity", section: "identity" }),
        node("plan-section", { id: "pr-review:plan:effects", section: "effects" }),
      ];
      // An empty provider lets VS Code display the native Welcome View.
      return [];
    }
    if (element.type === "plan-section") {
      if (element.section === "effects") return this.plan.effects.map((effect, index) => node("effect", {
        id: `pr-review:plan:effect:${index}`, label: `Effect ${index + 1}`, value: effect,
      }));
      return [
        node("value", { id: "pr-review:plan:pr", label: `Pull request #${this.plan.pull_request}`,
          value: `${this.plan.pull_request_state}${this.plan.pull_request_merged ? " · merged" : ""}`,
          tooltip: this.plan.pull_request_url, icon: "git-pull-request" }),
        node("value", { id: "pr-review:plan:delta", label: "Provider-recorded base → head",
          value: `${this.plan.base_oid} → ${this.plan.head_oid}`, icon: "git-compare" }),
        node("value", { id: "pr-review:plan:id", label: "Core-owned plan", value: this.plan.plan_id,
          tooltip: "This exact identity is supplied back to Workbench only after explicit consent.",
          icon: "verified-filled" }),
      ];
    }
    if (element.type === "section") {
      if (element.section === "decision") return decisionChildren(this.report);
      if (element.section === "scope") return scopeChildren(this.report);
      if (element.section === "recipes") return findingGroups(this.report);
      if (element.section === "files") return fileGroups(this.report);
      if (element.section === "attention") return attentionChildren(this.report);
      if (element.section === "next") return nextChildren(this.report);
    }
    if (element.type === "attention-group") {
      return attentionGroupChildren(this.report, element.group);
    }
    if (element.type === "recipe-group") return element.rows.map((row, index) => node("recipe", {
      id: `${element.id}:${index}`,
      kind: element.kind,
      index,
      label: recipeLabel(row, element.kind, index),
      path: recipeSource(row, element.kind),
      workspacePath: currentWorkspacePath(this.report, recipeSource(row, element.kind)),
      row,
      reportId: this.report.report_id,
    }));
    if (element.type === "direct-group") return element.rows.map((row, index) => node("direct", {
      id: `${element.id}:${index}`,
      kind: element.kind,
      index,
      label: compact(row.expression || row.semantic_key || `Removal call ${index + 1}`),
      path: row.source?.path,
      workspacePath: currentWorkspacePath(this.report, row.source?.path),
      row,
      reportId: this.report.report_id,
    }));
    if (element.type === "file-group") return element.paths.map((path, index) => node("file", {
      id: `${element.id}:${index}`,
      kind: element.kind,
      index,
      label: path,
      path,
      workspacePath: currentWorkspacePath(this.report, path),
      reportId: this.report.report_id,
    }));
    return [];
  }

  getTreeItem(element) {
    const api = this.vscode;
    const none = api.TreeItemCollapsibleState.None;
    const collapsed = api.TreeItemCollapsibleState.Collapsed;
    let item;
    if (element.type === "failure") {
      item = new api.TreeItem("Review could not be completed", none);
      item.description = compact(element.message, 200);
      item.tooltip = `${element.message}\nUse Workbench Setup or choose the installed CLI, then try again.`;
      item.iconPath = new api.ThemeIcon("error");
      item.contextValue = "workbenchPrReviewFailure";
    } else if (element.type === "plan-section") {
      item = new api.TreeItem(
        element.section === "identity" ? "Reviewed plan" : "Core-declared effects",
        collapsed,
      );
      item.iconPath = new api.ThemeIcon(element.section === "identity" ? "verified" : "list-ordered");
      if (element.section === "effects") item.description = `${this.plan.effects.length}`;
    } else if (element.type === "section") {
      item = new api.TreeItem(SECTION_LABELS[element.section], collapsed);
      item.iconPath = new api.ThemeIcon(SECTION_ICONS[element.section]);
      if (element.section === "decision") item.description = this.report.summary.status;
      if (element.section === "files") item.description = plural(this.report.summary.changed_source_files, "file");
      if (element.section === "attention") {
        item.description = `${this.report.attention.strict_reasons.length} decision · ${this.report.attention.configuration_warnings.length} configuration`;
      }
    } else if (["recipe-group", "direct-group", "file-group", "attention-group"].includes(element.type)) {
      item = new api.TreeItem(element.label, element.rows?.length || element.paths?.length ? collapsed : none);
      if (element.type === "attention-group") item.collapsibleState = collapsed;
      item.description = `${element.count}${element.truncated ? " · truncated" : ""}`;
      item.iconPath = new api.ThemeIcon(element.type === "file-group" ? "files"
        : element.type === "attention-group" ? "list-tree" : "symbol-method");
    } else {
      item = new api.TreeItem(element.label, none);
      item.description = compact(element.value);
      item.tooltip = element.tooltip || (element.workspacePath
        ? `${element.path}\nOpens current workspace file: ${element.workspacePath}\nHistorical PR bytes remain owned by Workbench.`
        : compact(element.value, 4000));
      if (element.icon) item.iconPath = new api.ThemeIcon(element.icon);
      if (element.type === "effect") item.iconPath = new api.ThemeIcon("arrow-right");
      if (element.type === "attention") item.iconPath = new api.ThemeIcon(element.icon);
      if (element.type === "next") item.iconPath = new api.ThemeIcon("terminal");
      if (element.type === "file") {
        item.iconPath = new api.ThemeIcon(element.kind === "added" ? "diff-added"
          : element.kind === "removed" ? "diff-removed" : "diff-modified");
        if (element.workspacePath) item.contextValue = "workbenchPrReviewFile";
      }
      if (element.type === "recipe") {
        item.description = compact(element.row.recipe_map || element.kind);
        item.iconPath = new api.ThemeIcon("beaker");
        item.contextValue = element.kind === "modified"
          ? element.workspacePath
            ? "workbenchPrReviewModifiedRecipe"
            : "workbenchPrReviewModifiedRecipeNoFile"
          : element.workspacePath ? "workbenchPrReviewRecipe" : undefined;
      }
      if (element.type === "direct") {
        item.description = compact(element.row.method || element.kind);
        item.iconPath = new api.ThemeIcon("remove");
        if (element.workspacePath) item.contextValue = "workbenchPrReviewRecipe";
      }
    }
    item.id = element.id;
    return item;
  }

  requireReport(item) {
    if (!this.report || item?.reportId !== this.report.report_id) {
      throw new Error("PR Recipe Review selection is stale; run the review again");
    }
    return this.report;
  }

  async openReport() {
    if (!this.report) throw new Error("Complete one PR Recipe Review first");
    const uri = this.documents.report(this.report);
    const document = await this.vscode.workspace.openTextDocument(uri);
    await this.vscode.window.showTextDocument(document, { preview: true });
    return this.report;
  }

  async openFindingDiff(item) {
    const report = this.requireReport(item);
    const before = this.documents.recipeSide(report, item, "before");
    const after = this.documents.recipeSide(report, item, "after");
    await this.vscode.commands.executeCommand(
      "vscode.diff", before, after, `Recipe properties · ${recipeLabel(item.row, item.kind, item.index)}`,
      { preview: true },
    );
    return item.row;
  }

  async openWorkspaceFile(item, workspaceUri) {
    this.requireReport(item);
    if (!item?.workspacePath || workspaceUri?.scheme !== "file") {
      throw new Error("A current local workspace file is required");
    }
    const uri = this.vscode.Uri.joinPath(workspaceUri, ...item.workspacePath.split("/"));
    await this.vscode.commands.executeCommand("vscode.open", uri, { preview: true });
    return uri;
  }
}

module.exports = {
  PrRecipeReviewDocuments,
  PrRecipeReviewTreeProvider,
  SECTION_ORDER,
  attentionChildren,
  attentionGroupChildren,
  currentWorkspacePath,
  decisionChildren,
  fileGroups,
  findingGroups,
  scopeChildren,
};
