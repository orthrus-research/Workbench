"use strict";

const { invokeLocalReview, ReviewEpoch } = require("./localReviewClient");
const { invokeCoreJson } = require("./coreCommandClient");
const { resolveCoreLaunch } = require("./coreLaunch");
const { openSourceLocation, verifiedSourceTarget } = require("./sourceNavigationClient");
const { rememberContext } = require("./developerContext");

function registerLocalReview(vscode, context, executable) {
  const epoch = new ReviewEpoch();
  const diagnostics = vscode.languages.createDiagnosticCollection("workbench-local-review");
  const documents = new Map();
  let selection;
  let configuring = false;
  let serial = 0;
  const invalidate = () => { epoch.invalidate(); diagnostics.clear(); };
  const watch = vscode.workspace.createFileSystemWatcher("**/*");
  const provider = vscode.workspace.registerTextDocumentContentProvider("workbench-local-review", {
    provideTextDocumentContent: (uri) => documents.get(uri.toString()) || "Review document is no longer retained in this IDE session.",
  });
  function virtual(text, name) {
    const uri = vscode.Uri.parse(`workbench-local-review:/snapshot-${++serial}/${encodeURIComponent(name)}`);
    documents.set(uri.toString(), text);
    while (documents.size > 100) documents.delete(documents.keys().next().value);
    return uri;
  }
  async function configure(root) {
    const mode = await vscode.window.showQuickPick(["Create context for this workspace", "Use existing Work Session"], { title: "Local review context" });
    if (!mode) return;
    let session;
    if (mode.startsWith("Use")) {
      session = await vscode.window.showInputBox({ title: "Existing Work Session ID" });
    } else {
      const pack = await vscode.window.showInputBox({ title: "Pack profile", value: "supersymmetry" });
      if (!pack) return;
      const platform = await vscode.window.showInputBox({ title: "Platform profile", value: "cleanroom" });
      if (!platform) return;
      const variant = await vscode.window.showInputBox({ title: "Profile variant", value: "cleanroom-provisional" });
      if (!variant) return;
      const launch = resolveCoreLaunch(executable());
      if (launch.host !== "native") throw new Error("Local review requires a native Linux Workbench host.");
      const value = await invokeCoreJson(executable(), ["context", "select", root,
        `--pack-profile=${pack}`, `--platform-profile=${platform}`, `--variant=${variant}`], { cwd: root, launch });
      session = value.session_id;
    }
    if (!session) return;
    rememberContext(root, session);
    const baseline = await vscode.window.showInputBox({ title: "Local Git baseline", prompt: "Resolved once to an exact commit; no fetch or checkout.", value: "HEAD" });
    if (!baseline) return;
    selection = { root, session, baseline };
    return selection;
  }
  async function run() {
    if (!vscode.workspace.isTrusted) throw new Error("Trust the workspace before invoking the installed Workbench executable.");
    const folders = vscode.workspace.workspaceFolders || [];
    if (folders.length !== 1 || folders[0].uri.scheme !== "file") throw new Error("Select one local workspace for review.");
    const root = folders[0].uri.fsPath;
    if (selection?.root !== root) selection = undefined;
    if (selection) {
      const mode = await vscode.window.showQuickPick(["Review saved changes", "Choose another context or baseline"], { title: "Local source review" });
      if (!mode) return;
      if (mode.startsWith("Choose")) selection = undefined;
    }
    if (configuring) return;
    let chosen = selection;
    if (!chosen) {
      configuring = true;
      try { chosen = await configure(root); }
      finally { configuring = false; }
    }
    if (!chosen) return;
    const ticket = epoch.begin();
    diagnostics.clear();
    const result = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Reviewing saved source changes", cancellable: true }, async (_progress, cancellation) => {
      const disposable = cancellation.onCancellationRequested(invalidate);
      try { return await invokeLocalReview(executable(), chosen.session, chosen.baseline, { cwd: root, signal: ticket.signal }); }
      finally { disposable.dispose(); }
    });
    if (!epoch.current(ticket)) return;
    chosen.baseline = result.baseline.revision;
    const grouped = new Map();
    for (const finding of result.findings) {
      if (finding.state === "resolved" || !finding.location) continue;
      try {
        const target = verifiedSourceTarget(root, finding.location);
        const dirty = vscode.workspace.textDocuments.some((doc) => doc.uri.fsPath === target.path && doc.isDirty);
        if (dirty) continue;
        const loc = finding.location;
        const diagnostic = new vscode.Diagnostic(new vscode.Range(loc.start.line - 1, loc.start.column - 1, loc.end.line - 1, loc.end.column - 1),
          `${finding.state}: ${finding.message}`, finding.severity === "warning" ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Information);
        diagnostic.source = "Workbench saved-source review";
        diagnostic.code = finding.code;
        if (!grouped.has(target.path)) grouped.set(target.path, []);
        grouped.get(target.path).push(diagnostic);
      } catch (_) { /* Never decorate a different or unsaved source version. */ }
    }
    diagnostics.set([...grouped].map(([path, rows]) => [vscode.Uri.file(path), rows]));
    const choices = [
      { label: "Inspect review details and checks", description: "Source interpretation, not compilation or runtime proof", type: "report" },
      ...result.files.map((file) => ({ label: `${file.state}: ${file.path}`, description: "Open exact saved before/after diff", type: "file", file })),
      ...result.findings.filter((finding) => finding.location && finding.state !== "resolved").map((finding) => ({ label: finding.message,
        description: `${finding.state} · ${finding.location.path}`, type: "finding", finding })),
      ...result.changes.filter((change) => change.after).map((change) => ({ label: `${change.state}: ${change.after.kind} · ${change.after.label}`,
        description: change.after.location.path, type: "finding", finding: change.after })),
    ];
    const related = new Map();
    for (const graph of result.relationships) {
      for (const node of graph.nodes) if (node.location) related.set(node.selection_id, node);
    }
    choices.push(...[...related.values()].map((node) => ({ label: `Related source: ${node.label}`, description: `${node.kind} · ${node.location.path}`,
      type: "finding", finding: node })));
    while (epoch.current(ticket)) {
      const selected = await vscode.window.showQuickPick(choices, { title: `Saved changes: ${result.counts.files} files · ${result.counts.changes} declaration changes`,
        placeHolder: "Review is invalidated by further edits. Escape to continue coding.", matchOnDescription: true });
      if (!selected || !epoch.current(ticket)) return;
      if (selected.type === "report") {
        await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(virtual(JSON.stringify(result, null, 2), "review.json")), { preview: true });
      } else if (selected.type === "file") {
        const file = selected.file;
        if (["before", "after"].some((side) => !["included", "absent"].includes(file[side + "_text_state"]))) {
          await vscode.window.showInformationMessage("File bytes are bound to this review, but a text diff is unavailable (binary or size limit). See review details.");
          continue;
        }
        await vscode.commands.executeCommand("vscode.diff", virtual(file.before_text || "", "baseline/" + file.path),
          virtual(file.after_text || "", "saved/" + file.path), `${file.path} — captured saved changes (read-only)`);
      } else {
        await invokeLocalReview(executable(), chosen.session, chosen.baseline, { cwd: root, expected: result.review_id, signal: ticket.signal });
        if (epoch.current(ticket)) await openSourceLocation(vscode, root, selected.finding.location);
      }
    }
  }
  context.subscriptions.push(diagnostics, provider, watch,
    watch.onDidChange(invalidate), watch.onDidCreate(invalidate), watch.onDidDelete(invalidate),
    vscode.workspace.onDidChangeTextDocument((event) => { if (event.document.uri.scheme === "file" && event.contentChanges.length) invalidate(); }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => { invalidate(); selection = undefined; }),
    vscode.workspace.onDidChangeConfiguration((event) => { if (event.affectsConfiguration("workbench.coreExecutable")) { invalidate(); selection = undefined; } }),
    vscode.commands.registerCommand("workbench.review.local", () => run().catch((error) => {
      if (error.cause?.name !== "AbortError" && error.name !== "AbortError") void vscode.window.showErrorMessage(`Local review unavailable: ${error.message}`);
    })), { dispose: invalidate });
}

module.exports = { registerLocalReview };
