"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const vscode = require("vscode");
const { loadCorpus } = require("./atlas-corpus-contract");

/** Actual installed controller, Core, native tree and virtual editor; scripted dialogs only. */
async function run(extension) {
  const { manifest, cases, highlights } = await loadCorpus(process.env.WORKBENCH_TEST_ATLAS_CORPUS, extension.extensionPath);
  assert.equal(vscode.workspace.workspaceFolders[0].uri.fsPath, manifest.workspace);
  assert.equal(vscode.workspace.getConfiguration("workbench").get("coreExecutable"), manifest.executable);
  const { createAtlasRecipeImpactFlow } = require(path.join(extension.extensionPath, "atlasRecipeImpactFlow"));
  const { AtlasImpactDocuments, AtlasImpactTreeProvider } = require(path.join(extension.extensionPath, "atlasRecipeImpactTree"));
  const override = (api, fields) => Object.defineProperties(Object.create(api), Object.fromEntries(
    Object.entries(fields).map(([key, value]) => [key, { value, enumerable: true }])));
  let current;
  const errors = [], notices = [], prompts = [];
  const facade = override(vscode, {
    Uri: override(vscode.Uri, { from: value => vscode.Uri.from({ ...value, scheme: "workbench-atlas-acceptance" }) }),
    commands: override(vscode.commands, { executeCommand: (command, ...args) => vscode.commands.executeCommand(
      command === "workbench.recipeImpact.focus" ? "workbench.test.atlasImpact.focus" : command, ...args,
    ) }),
    window: override(vscode.window, {
      showOpenDialog: async () => { prompts.push("graph"); return [vscode.Uri.file(current.graph)]; },
      showQuickPick: async choices => {
        const rows = await choices;
        const selected = rows.find(row => row.mode === "search") || rows.find(row => row.recipe?.selection_id === current.selection_id)
          || rows.find(row => row.exploration === (current.exploration || "bounded"));
        assert.ok(selected, "the native picker must offer the exact CLI recipe");
        prompts.push(selected.exploration ? `exploration:${selected.exploration}` : selected.mode === "search" ? "search" : "recipe");
        return selected;
      },
      showInputBox: async options => {
        const value = options.title === "Find One Observed GT Recipe" ? current.query
          : options.title === "Recipe Impact Propagation Bound" ? String(current.max_depth)
            : options.title === "Recipe Impact Node Bound" ? String(current.max_nodes) : undefined;
        assert.notEqual(value, undefined, `unexpected Atlas input: ${options.title}`);
        assert.equal(options.validateInput(value), undefined);
        prompts.push(options.title); return value;
      },
      showErrorMessage: async message => { errors.push(message); },
      showWarningMessage: async message => { notices.push({ severity: "warning", message }); },
      showInformationMessage: async message => { notices.push({ severity: "information", message }); },
    }),
  });
  const documents = new AtlasImpactDocuments(facade);
  const provider = new AtlasImpactTreeProvider(facade, documents);
  const subscriptions = [provider, vscode.workspace.registerTextDocumentContentProvider("workbench-atlas-acceptance", documents)];
  const tree = vscode.window.createTreeView("workbench.test.atlasImpact", { treeDataProvider: provider });
  subscriptions.push(tree);
  const flow = createAtlasRecipeImpactFlow(facade, provider, {
    selectedCore: () => manifest.executable, currentWorkingDirectory: () => manifest.workspace,
  });
  subscriptions.push(vscode.commands.registerCommand("workbench.test.atlas.analyze", () => flow.analyze()));
  subscriptions.push(vscode.commands.registerCommand("workbench.test.atlas.openReport", () => flow.openReport()));
  const evidence = [];
  try {
    for (const row of highlights) {
      current = row; notices.length = 0; prompts.length = 0;
      const report = await vscode.commands.executeCommand("workbench.test.atlas.analyze");
      assert.deepEqual(errors, []);
      assert.deepEqual(report, row.impact, `${row.id}: native journey differs from the installed CLI report`);
      assert.deepEqual(prompts, ["graph", "search", "Find One Observed GT Recipe", "recipe",
        `exploration:${row.exploration || "bounded"}`,
        ...(row.exploration === "complete-finite" ? [] : ["Recipe Impact Propagation Bound", "Recipe Impact Node Bound"])]);
      assert.equal(notices.length, 1);
      assert.equal(notices[0].severity, row.cautions.warning ? "warning" : "information");
      assert.ok(notices[0].message.includes(row.cautions.text));
      assert.match(notices[0].message, /viability is not established/);
      await vscode.commands.executeCommand("workbench.test.atlasImpact.focus");
      const roots = provider.getChildren();
      assert.equal(tree.visible, true, "the real native Atlas acceptance tree was not shown");
      const caution = provider.getTreeItem(roots[0]);
      assert.equal(caution.label, row.cautions.state);
      assert.equal(`${caution.label} · ${caution.description}`, row.cautions.text);
      assert.match(caution.tooltip, /do not establish viability/);
      for (const section of ["frontiers", "unknowns", "evidence_gaps"]) {
        const entry = roots.find(item => item.section === section);
        assert.ok(entry);
        assert.equal(provider.getTreeItem(entry).description, String(report[section].length));
        const values = children => children.flatMap(item => item.type === "range" ? values(provider.getChildren(item)) : [item.value]);
        assert.deepEqual(values(provider.getChildren(entry)), report[section]);
      }
      await vscode.commands.executeCommand("workbench.test.atlas.openReport");
      const document = vscode.window.activeTextEditor.document;
      assert.equal(document.uri.scheme, "workbench-atlas-acceptance");
      assert.deepEqual(JSON.parse(document.getText()), row.impact);
      assert.equal(document.isDirty, false);
      evidence.push({ id: row.id, graph_set_id: report.context.graph_set_id, selection_id: report.selection.selection_id,
        exploration: row.exploration || "bounded",
        impact_sha256: row.impact_sha256, cautions: row.cautions, exact_cli_report: true, native_tree: true, virtual_report: true });
    }
    return { format: "workbench-atlas-installed-native-journey-v1", editor_host: "real-vscode",
      scripted_dialogs: true, installed_core_transport: true, contract_case_count: cases.length,
      highlight_count: evidence.length, evidence };
  } finally {
    for (const subscription of subscriptions.reverse()) subscription.dispose();
  }
}

module.exports = { run };
