"use strict";

const { isDeepStrictEqual } = require("node:util");
const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");
const { invokeAtlasSearch } = require("./developerToolsClient");
const { validateBrowse } = require("./atlasRecipeBrowseValidation");

function nodeLabel(row) {
  const properties = row.properties || {};
  if (row.kind === "item-variant") {
    const names = [...new Set((Array.isArray(properties.observed_item_names) ? properties.observed_item_names : [])
      .map(name => name?.name).filter(name => typeof name === "string" && name))].sort();
    if (names.length) return names[0] + (names.length > 1 ? ` (+${names.length - 1} names)` : "");
  }
  if (row.kind === "gt-recipe") {
    return [properties.recipe_map || row.semantic_key,
      ...(typeof properties.duration === "number" ? [`${properties.duration} ticks`] : []),
      ...(typeof properties.eut === "number" ? [`${properties.eut} EU/t`] : [])].join(" · ");
  }
  if (row.kind === "gt-recipe-input-selector") {
    return ["Input" + (Number.isSafeInteger(properties.ordinal) ? ` ${properties.ordinal + 1}` : ""),
      ...(typeof properties.amount === "number" ? [`amount ${properties.amount}`] : []),
      ...(properties.non_consumable === true ? ["reusable"] : []),
      ...(properties.acceptance_complete === false ? ["matching incomplete"] : [])].join(" · ");
  }
  return row.semantic_key || `${row.source_path}:${row.line}`;
}
function relationshipLabel(row) {
  const labels = {
    "produces-gt-item": ["Produces item", "Produced by recipe"],
    "produces-gt-fluid": ["Produces fluid", "Produced by recipe"],
    "has-item-input-selector": ["Item input", "Input to recipe"],
    "has-fluid-input-selector": ["Fluid input", "Input to recipe"],
    "accepts-gt-item-alternative": ["Accepted item alternative", "Accepted by input"],
    "accepts-gt-fluid-input": ["Accepted fluid", "Accepted by input"],
    "contained-in-recipe-map": ["Recipe map", "Contains recipe"],
  };
  const edge = row.relationship, properties = edge.properties;
  return [labels[edge.relation]?.[row.direction === "outgoing" ? 0 : 1] || `${row.direction} · ${edge.relation}`,
    ...(typeof properties.amount === "number" ? [`amount ${properties.amount}`] : []),
    ...(properties.non_consumable === true ? ["reusable"] : []),
    ...(properties.chanced === true ? ["chance output; inspect values"] : [])].join(" · ");
}
async function invokeBrowse(executable, root, graph, selection, offset = 0, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const mappedRoot = pathForCoreLaunch(root, launch, "Atlas graph");
  const value = await invokeCoreJson(executable, ["atlas", "recipes", "browse", mappedRoot, selection,
    "--expect-graph", graph, "--offset", String(offset), "--limit", "50", "--json"], {
    ...options, launch, maximumOutput: 48 * 1024 * 1024, timeoutMs: 15 * 60 * 1000, label: "Atlas relationships",
  });
  return validateBrowse(value, { root: mappedRoot, graph, selection, offset });
}

function createAtlasRecipeBrowser(vscode, context, executable, showJson, options = {}) {
  const { search = invokeAtlasSearch, browse = invokeBrowse } = options;
  const openSession = options.openSession || (!options.search && require("./atlasRecipeSession").openRecipeSession);
  const key = "workbench.atlas.lastBrowse";
  async function open(workspace) {
    let session;
    const cancellation = new AbortController();
    const progress = (title, call) => vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title, cancellable: true }, async (_progress, token) => {
      const subscription = token?.onCancellationRequested(() => cancellation.abort());
      if (token?.isCancellationRequested) cancellation.abort();
      try {
        cancellation.signal.throwIfAborted();
        const result = await call(cancellation.signal);
        if (cancellation.signal.aborted) {
          if (typeof result?.close === "function") result.close();
          cancellation.signal.throwIfAborted();
        }
        return result;
      } finally { subscription?.dispose(); }
    });
    try {
    const saved = context.workspaceState.get(key);
    const mode = await vscode.window.showQuickPick([
      { label: "Explore a captured recipe graph", kind: "graph" },
      { label: "Search this project's source", kind: "source", description: "Source occurrences; runtime relationships unavailable" },
      ...(saved ? [{ label: "Reopen last Atlas selection", kind: "reopen", description: saved.root }] : []),
      ...(saved ? [{ label: "Clear saved Atlas selection", kind: "forget", description: "Choose a graph again next time" }] : []),
    ], { title: "Atlas recipe evidence" });
    if (!mode) return;
    if (mode.kind === "forget") { await context.workspaceState.update(key, undefined); return; }
    let root = workspace, selected, graph, offset = 0;
    if (mode.kind === "reopen") {
      ({ root, graph, selected } = saved);
    } else {
      if (mode.kind === "graph") {
        const folders = await vscode.window.showOpenDialog({ title: "Select a captured Atlas graph directory", canSelectFiles: false, canSelectFolders: true, canSelectMany: false });
        if (!folders?.length) return;
        root = folders[0].fsPath;
      }
      const query = await vscode.window.showInputBox({ title: "Search Atlas Recipes", prompt: "Item, fluid, recipe, machine, or source text", ignoreFocusOut: true });
      if (query === undefined || !query.trim()) return;
      if (mode.kind === "graph" && openSession) session = await progress("Verifying the captured Atlas graph", signal => openSession(executable(), root, { cwd: workspace, signal }));
      const found = await progress("Searching Atlas evidence", signal => session ? session.search(query.trim()) : search(executable(), root, query.trim(), 50, { cwd: workspace, signal }));
      if (!found.results.length) { await vscode.window.showInformationMessage("No matching evidence. Try a different name or identifier."); return found; }
      const choice = await vscode.window.showQuickPick(found.results.map(row => ({
        label: nodeLabel(row), description: row.kind, detail: row.semantic_key || row.selection_id, row,
      })), { title: found.truncated ? "First 50 matches — refine your search for other results" : "Select recipe evidence", matchOnDescription: true, matchOnDetail: true });
      if (!choice) return found;
      if (found.context.context_type !== "categorical-graph-v2") { await showJson({ context: found.context, selection: choice.row, evidence_gaps: found.evidence_gaps }); return found; }
      selected = choice.row.selection_id;
      graph = found.context.graph_set_id;
      // Bind the first page to the exact result selected in the picker.
      selected = { id: selected, expected: choice.row };
    }
    const history = [];
    let expected = typeof selected === "object" ? selected.expected : null;
    selected = typeof selected === "object" ? selected.id : selected;
    if (!session && openSession) session = await progress("Reopening and verifying the Atlas graph", signal => openSession(executable(), root, { cwd: workspace, signal }));
    if (session && session.graph !== graph) throw new Error("Atlas graph changed; reopen the graph and search again");
    for (;;) {
      const page = await progress("Reading captured relationships", signal => session ? session.browse(selected, offset) : browse(executable(), root, graph, selected, offset, { cwd: workspace, signal }));
      if (expected && !isDeepStrictEqual(expected, page.selection)) throw new Error("Atlas selection changed since search");
      expected = null;
      await context.workspaceState.update(key, { root, graph, selected });
      const choices = [
        { label: "Inspect captured values and evidence", kind: "values", description: page.selection.kind },
        ...(history.length ? [{ label: "Back to previous selection", kind: "back" }] : []),
        ...(offset ? [{ label: "Previous relationship page", kind: "previous" }] : []),
        ...(page.page.next_offset !== null ? [{ label: "Next relationship page", kind: "next" }] : []),
        ...page.links.map(row => ({ label: nodeLabel(row.node), description: relationshipLabel(row),
          detail: `${row.node.kind} · ${row.node.semantic_key}${history.some(previous => previous.id === row.node.selection_id) || selected === row.node.selection_id ? " · already on this path (cycle/reference)" : ""}`,
          kind: "node", row })),
      ];
      const choice = await vscode.window.showQuickPick(choices, {
        title: `${nodeLabel(page.selection)} — ${offset + page.links.length}/${page.page.total} relationships`,
        placeHolder: "Observed links only; craftability unknown. Select values to read quantities and scope.",
        matchOnDescription: true, matchOnDetail: true,
      });
      if (!choice) return page;
      if (choice.kind === "values") { await showJson(page); continue; }
      if (choice.kind === "next") { offset = page.page.next_offset; continue; }
      if (choice.kind === "previous") { offset = Math.max(0, offset - 50); continue; }
      if (choice.kind === "back") { const prior = history.pop(); selected = prior.id; offset = prior.offset; continue; }
      history.push({ id: selected, offset });
      selected = choice.row.node.selection_id;
      expected = choice.row.node;
      offset = 0;
    }
    } catch (error) {
      if (!cancellation.signal.aborted) throw error;
    } finally { session?.close(); }
  }
  return { open };
}

module.exports = { createAtlasRecipeBrowser, invokeBrowse, validateBrowse, nodeLabel, relationshipLabel };
