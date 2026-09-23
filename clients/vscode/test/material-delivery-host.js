"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs/promises"), path = require("node:path");
const { promisify } = require("node:util"), execFile = promisify(require("node:child_process").execFile);
const vscode = require("vscode");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Windowed companion to material-check-host; uses the installed production flow. */
async function run(extension, fixture) {
  const out = fixture.deliveryMeasurement.output;
  await fs.mkdir(out, { recursive: true });
  const events = [];
  let writing = Promise.resolve();
  const clock = () => process.hrtime.bigint().toString();
  async function event(phase, value = {}) {
    const row = { phase, ns: clock(), ...value }; events.push(row);
    writing = writing.then(() => fs.appendFile(path.join(out, "ide-events.jsonl"), JSON.stringify(row) + "\n"));
    await writing;
  }
  async function observer(...args) {
    const result = await execFile("python3", [fixture.deliveryMeasurement.observer, "--title", fixture.deliveryMeasurement.windowTitle, ...args]);
    return JSON.parse(result.stdout);
  }
  async function capture(name) {
    // Capture is an observed upper bound, not an inferred render-complete event.
    await delay(250);
    const receipt = await observer("--capture", path.join(out, name + ".png"));
    await event(name, receipt);
  }
  const client = require(path.join(extension.extensionPath, "materialChecksClient"));
  const materialModule = require(path.join(extension.extensionPath, "materialChecks"));
  const originalFactory = materialModule.createMaterialChecks;
  materialModule.createMaterialChecks = (api, saved, executable, options) => originalFactory(api, saved, executable, { ...options,
    trace: (phase, value) => { void event("client-" + phase, { attempt: value?.attempt_id, revision: value?.diagnostic_id }); if (phase === "results-visible" && !presentation) presentation = browseResults(value); } });
  const values = new Map([["workbench.materialContext", { session: fixture.session, context: fixture.options.context }]]);
  const context = { subscriptions: [], workspaceState: { get: key => values.get(key), update: async (key, value) => { values.set(key, value); } } };
  let target, selectedNode, reopening = false, capturedOffered = false, navigated = false, navigation, firstPresented = false, tree, treeView, presentation;
  const observedFiles = new Set();
  let observing = false;
  const fileObserver = setInterval(async () => {
    const attempt = values.get("workbench.lastMaterialAttempt");
    if (!attempt || observing) return;
    observing = true;
    try {
      for (const file of ["native-process/capture.json", "diagnostic-delivery/publication.json", "snapshot/publication.json"]) {
        if (observedFiles.has(file)) continue;
        try {
          await fs.stat(path.join(fixture.coreState, "product-spine/developer-checks/.workbench/check-attempts", attempt, file));
          observedFiles.add(file); await event(file, { attempt });
        } catch (error) { if (error.code !== "ENOENT") throw error; }
      }
    } finally { observing = false; }
  }, 250);
  const errors = [];
  const facade = Object.create(vscode), window = Object.create(vscode.window);
  Object.defineProperty(facade, "window", { value: window });
  const commands = Object.create(vscode.commands);
  Object.defineProperty(commands, "registerCommand", { value: (name, handler) => vscode.commands.registerCommand("workbench.delivery-test." + name, handler) });
  Object.defineProperty(facade, "commands", { value: commands });
  async function browseResults(value) {
    assert.ok(tree && treeView, "Native Axiom Results tree must exist");
    firstPresented = true; await capture("visible-first-result");
    const groups = await tree.getChildren();
    await event("result-groups", { counts: value.finding_counts, groups: groups.map(row => row.group), detail: value.detail_state, message: treeView.message });
    // Inspect an unlocated error directly before navigating a located finding.
    const unlocated = groups.find(row => row.group === "error-unlocated");
    if (unlocated) {
      await treeView.reveal(unlocated, { expand: true, select: true, focus: true });
      const rows = await tree.getChildren(unlocated); assert.ok(rows.length);
      await event("unlocated-errors-accessible", { loaded: rows.length, first: rows[0].finding.id });
      await capture("visible-unlocated-errors");
    }
    const group = groups.find(row => row.group === (fixture.deliveryMeasurement.severity || "warning") + "-located");
    assert.ok(group, "Located diagnostic group must be available");
    await treeView.reveal(group, { expand: true, select: true, focus: true });
    let chosen;
    while (!chosen) {
      const rows = await tree.getChildren(group);
      chosen = rows.find(row => row.finding.location?.path === fixture.deliveryMeasurement.sourcePath && row.finding.location.start.line === fixture.deliveryMeasurement.sourceLine);
      if (!chosen) {
        const before = rows.length;
        await vscode.commands.executeCommand("workbench.delivery-test.workbench.axiomResults.next", group);
        assert.ok((await tree.getChildren(group)).length > before, "Expected source anchor must be reachable");
      }
    }
    target = chosen.finding; selectedNode = chosen;
    await treeView.reveal(chosen, { select: true, focus: true });
    await event("diagnostic-selected", { finding: target }); await capture("visible-presentation");
    await observer("--key", "Return");
    await event("keyboard-open-source", { finding: target.id });
  }
  Object.defineProperties(window, {
    createTreeView: { value: (id, options) => {
      tree = options.treeDataProvider;
      const provider = Object.create(tree);
      provider.getTreeItem = node => { const item = tree.getTreeItem(node); if (item.command) item.command = { ...item.command, command: "workbench.delivery-test." + item.command.command }; return item; };
      treeView = vscode.window.createTreeView(id, { ...options, treeDataProvider: provider }); return treeView;
    } },
    showQuickPick: { value: async (items, options = {}) => {
      items = await items;
      const strings = typeof items[0] === "string";
      if (strings) items = items.map(label => ({ label }));
      let selected;
      if (options.title === "Developer context") selected = items.find(row => row.label === "Use selected Work Session");
      else if (options.title === "Saved developer checks") selected = items.find(row => row.label === (reopening ? "Reopen an Axiom material check" : "Run Axiom material preflight"));
      else if (options.title?.startsWith("Retained Axiom checks")) selected = items.find(row => row.attempt === values.get("workbench.lastMaterialAttempt"));
      else if (items.some(row => row.row?.id === fixture.options.context)) selected = items.find(row => row.row?.id === fixture.options.context);
      else if (items.some(row => row.type === "finding")) {
        selected = items.find(row => row.type === "finding" && row.finding.side === "candidate" && row.finding.location && row.finding.severity?.toLowerCase() === (fixture.deliveryMeasurement.severity || "warning")
          && (!fixture.deliveryMeasurement.sourcePath || row.finding.location.path === fixture.deliveryMeasurement.sourcePath)
          && (!fixture.deliveryMeasurement.sourceLine || row.finding.location.start.line === fixture.deliveryMeasurement.sourceLine));
        if (!selected) selected = items.find(row => row.label === "Load remaining findings");
      } else selected = items.find(row => row.type === "live") || items.find(row => row.label === "Load remaining findings");
      assert.ok(selected, `Missing measurement selection in ${options.title}`);
      const picker = vscode.window.createQuickPick(); picker.items = items; picker.title = options.title; picker.placeholder = options.placeHolder;
      picker.activeItems = [selected];
      const accepted = new Promise(resolve => { picker.onDidAccept(() => { resolve(picker.activeItems[0]); picker.hide(); }); });
      picker.show(); picker.activeItems = [selected];
      await event("dialog-model", { title: options.title, selection: selected.label });
      if (options.title?.startsWith("Axiom:") && !firstPresented) {
        firstPresented = true; await capture("visible-first-result");
      }
      if (selected.finding) {
        target = selected.finding;
        await event("diagnostic-selected", { finding: target });
        await capture("visible-presentation");
      } else await delay(100);
      await observer("--key", "Return");
      const choice = await accepted; picker.dispose();
      return strings ? choice.label : choice;
    } },
    showWarningMessage: { value: async (message, options, ...buttons) => {
      if (buttons.includes("Open captured source")) { capturedOffered = true; await event("stale-source-offer", { message }); return "Open captured source"; }
      // Extension-test mode refuses modal dialogs. The authorized confirmation
      // is scripted identically for both products; results remain windowed.
      if (buttons.includes("Continue with complete capture")) {
        await event("storage-confirmation", { message, scripted: true });
        return "Continue with complete capture";
      }
      const choice = "Run this exact material check";
      assert.ok(buttons.includes(choice));
      await event("confirmation", { request: /Request: (\S+)/.exec(message)?.[1], scripted: true });
      return choice;
    } },
    showErrorMessage: { value: async message => { errors.push(message); await event("error", { message }); } },
    showInputBox: { value: async options => { throw new Error(`Prepared measurement unexpectedly requested setup: ${options.title}`); } },
  });
  const observeNavigation = change => {
    if (!change.textEditor || !target || navigated || change.textEditor.document.uri.fsPath !== path.join(fixture.pack, target.location.path)
        || change.textEditor.selection.active.line !== target.location.start.line - 1) return;
    navigated = true;
    navigation = (async () => {
      const crypto = require("node:crypto");
      const raw = await fs.readFile(change.textEditor.document.uri.fsPath);
      assert.equal(crypto.createHash("sha256").update(raw).digest("hex"), target.location.sha256);
      await event("source-open", { finding: target.id, path: target.location.path, line: target.location.start.line, sha256: target.location.sha256 });
      await capture("visible-source");
    })();
  };
  const subscription = vscode.window.onDidChangeTextEditorSelection(observeNavigation);
  try {
    const readyDeadline = Date.now() + 30000;
    while (true) {
      try { await observer("--ready"); break; }
      catch (error) { if (Date.now() >= readyDeadline) throw error; await delay(250); }
    }
    await capture("ide-ready");
    require(path.join(extension.extensionPath, "developerContext")).rememberContext(fixture.pack, fixture.session);
    require(path.join(extension.extensionPath, "developerChecks")).registerDeveloperChecks(facade, context, () => fixture.executable);
    await event("run-action");
    await vscode.commands.executeCommand("workbench.delivery-test.workbench.checks.saved");
    assert.equal(errors.length, 0);
    if (presentation) await presentation;
    assert.ok(treeView.visible, "Results must remain visible after source opens");
    const navigationDeadline = Date.now() + 10000;
    while (!navigated && Date.now() < navigationDeadline) {
      observeNavigation({ textEditor: vscode.window.activeTextEditor });
      if (!navigated) await delay(50);
    }
    assert.ok(navigated, "Exact working-copy source must open"); await navigation;
    const attempt = values.get("workbench.lastMaterialAttempt");
    const result = await client.invokeMaterialCheck(fixture.executable, fixture.pack, fixture.session, "show", attempt);
    await event("snapshot-finalized", { attempt, snapshot: result.snapshot_id, count: result.findings_count, coverage: result.coverage, outcome: result.native_outcome });
    assert.match(treeView.message, /Recipe details ready/);
    await capture("visible-ready");
    let sourceGuards;
    if (fixture.deliveryMeasurement.scenario === "ordinary") {
      const editor = vscode.window.activeTextEditor, document = editor.document, savedText = document.getText();
      try {
        assert.ok(await editor.edit(edit => edit.insert(document.positionAt(savedText.length), "\n// Unsaved Axiom presentation acceptance edit\n")));
        assert.ok(document.isDirty); await delay(100);
        assert.match(treeView.message, /Source changed/);
        assert.equal(vscode.languages.getDiagnostics(document.uri).filter(d => d.source === "Axiom saved material check").length, 0);
        await capture("visible-unsaved");
        await vscode.commands.executeCommand("workbench.delivery-test.workbench.axiomResults.open", selectedNode);
        assert.ok(capturedOffered); assert.equal(vscode.window.activeTextEditor.document.uri.scheme, "workbench-saved-check");
        assert.equal(vscode.window.activeTextEditor.document.getText(), savedText); await capture("visible-captured-source");
      } finally {
        await vscode.window.showTextDocument(document); await vscode.commands.executeCommand("workbench.action.files.revert");
        assert.equal(document.getText(), savedText); assert.equal(document.isDirty, false);
      }
      const executions = events.filter(e => e.phase === "confirmation").length;
      reopening = true; await vscode.commands.executeCommand("workbench.delivery-test.workbench.checks.saved");
      assert.match(treeView.message, /Saved source matches/);
      assert.equal(events.filter(e => e.phase === "confirmation").length, executions, "Reopen must not run native initialization");
      sourceGuards = { unsavedMarkersSuppressed: true, capturedBytesEqual: true, liveEditorRestored: true, reopenedWithoutExecution: true };
      await event("source-guards-and-reopen", sourceGuards); await capture("visible-reopened");
    }
    const report = { format: "workbench-ide-delivery-measurement-v1", ide: "vscode", editorHost: "real-vscode", entrypoint: "registered-saved-checks-command-private-alias", nativeWorkers: 1, version: vscode.version,
      attempt, target, events, sourceGuards, navigationVerified: true, visibleEvidenceRequiresInspection: true,
      observation: "250 ms render settling plus capture duration; visible timestamps are observed upper bounds", qualification: false };
    await fs.writeFile(path.join(out, "ide-report.json"), JSON.stringify(report, null, 2) + "\n");
    return report;
  } finally { clearInterval(fileObserver); subscription.dispose(); for (const item of context.subscriptions) item.dispose(); materialModule.createMaterialChecks = originalFactory; }
}
module.exports = { run };
