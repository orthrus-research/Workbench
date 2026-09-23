"use strict";
const { invokeCheck, monitorCheck, provenanceSummary, comparisonSummary, expectationSummary, recipeExplanation, recipeExplanationText, recipeAssertionFindings } = require("./developerChecksClient");
const { selectContext } = require("./developerContext");
const { openSourceLocation, verifiedSourceTarget } = require("./sourceNavigationClient");

function registerDeveloperChecks(vscode, context, executable) {
  const diagnostics = vscode.languages.createDiagnosticCollection("workbench-saved-checks");
  const documents = new Map();
  let active = false, lastAttempt, serial = 0, generation = 0, materials;
  const materialResults = vscode.window.createTreeView ? require("./materialResults").createMaterialResults(vscode, context) : undefined;
  let lastEnvironment = context.workspaceState?.get("workbench.lastEnvironment");
  const provider = vscode.workspace.registerTextDocumentContentProvider("workbench-saved-check", { provideTextDocumentContent: (uri) => documents.get(uri.toString()) || "Check details are no longer held by this IDE session." });
  const watch = vscode.workspace.createFileSystemWatcher("**/*");
  const clear = () => { generation += 1; diagnostics.clear(); materials?.invalidate(); }; // Editing does not cancel an immutable run.
  async function details(value, title = "result", extension = "json", line) {
    const uri = vscode.Uri.parse(`workbench-saved-check:/${title}-${++serial}.${extension}`);
    documents.set(uri.toString(), typeof value === "string" ? value : JSON.stringify(value, null, 2));
    while (documents.size > 30) documents.delete(documents.keys().next().value);
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri), { preview: true, ...(line ? { selection: new vscode.Range(line - 1, 0, line - 1, 0) } : {}) });
  }
  async function presentRecipe(root, session, result) {
    const report = recipeExplanation(result);
    if (!report) return details(recipeExplanationText(result), "recipe-explanation", "txt");
    const selected = await vscode.window.showQuickPick([
      { label: "Read full recipe explanation", report: true, detail: report.summary },
      ...report.sections.map(section => ({ label: section.title, detail: section.notes.join(" · "), section })),
      { label: "Inspect raw assertion evidence", raw: true },
    ], { title: `Recipe assertion: ${result.assertions.state} · startup: ${result.state}` });
    if (!selected) return;
    if (selected.raw) return details(result.assertions, "recipe-assertions");
    if (selected.report) return details(recipeExplanationText(result), "recipe-explanation", "txt");
    const section = selected.section;
    const choice = await vscode.window.showQuickPick([
      { label: "Read explanation and property differences", read: true },
      ...section.sources.flatMap((source, index) => [
        { label: `Read retained ${source.label}`, description: `${source.location.path}:${source.location.start.line}`, detail: `${source.basis} · exact candidate, read-only`, source, retained: `${section.id}:${index}` },
        { label: `Open working copy: ${source.label}`, description: `${source.location.path}:${source.location.start.line}`, detail: `${source.basis} · requires identical saved editor bytes`, source },
      ]),
      ...section.evidence.map(ref => ({ label: `Open captured ${ref.log}:${ref.line}`, ref })),
    ], { title: section.title });
    if (!choice) return;
    if (choice.retained) {
      const retained = await invokeCheck(executable(), root, session, "source", result.attempt_id, choice.retained);
      if (retained.format !== "workbench-check-source-view-v1" || retained.read_only !== true || retained.result_id !== result.id) throw new Error("Retained source differs from the selected check.");
      return details(retained.text, "retained-recipe-source", "groovy", retained.source.location.start.line);
    }
    if (choice.source) return openSourceLocation(vscode, root, choice.source.location);
    if (choice.ref) {
      const log = await invokeCheck(executable(), root, session, "log", result.attempt_id, choice.ref.log);
      return details(log.text, `recipe-evidence-line-${choice.ref.line}`, "log", choice.ref.line);
    }
    return details(recipeExplanationText(result, section), "recipe-explanation", "txt");
  }
  async function compare(root, session, candidate) {
    const history = await invokeCheck(executable(), root, session, "history");
    const choices = history.runs.filter((run) => run.attempt_id !== candidate.attempt_id).map((run) => ({
      label: `${run.pack.name} ${run.pack.version} · ${run.state}`,
      description: run.attempt_id, detail: `${run.source.revision}${run.source.dirty ? " · saved edits" : ""} · ${run.image_id}`, run,
    }));
    choices.push({ label: "Enter an exact reference attempt ID", manual: true });
    const selected = await vscode.window.showQuickPick(choices, { title: `Choose reference (not an acceptance baseline) · ${history.unsupported_records} historical-format records excluded` });
    if (!selected) return;
    const reference = selected.manual ? await vscode.window.showInputBox({ title: "Exact reference check attempt ID" }) : selected.run.attempt_id;
    if (!reference) return;
    const result = await invokeCheck(executable(), root, session, "compare", candidate.attempt_id, reference);
    const item = await vscode.window.showQuickPick([
      { label: "Inspect comparison, provenance and original outcomes", report: true },
      { label: "Open reference check, source findings and logs", attempt: result.reference.attempt_id },
      { label: "Open candidate check, source findings and logs", attempt: result.candidate.attempt_id },
      ...result.groups.map((group) => ({ label: `${group.status} · ${(group.candidate || group.reference).message.split("\n")[0]}`, description: `${group.reference?.occurrence_count || 0} → ${group.candidate?.occurrence_count || 0}`, detail: group.reason || (group.candidate || group.reference).guidance, group })),
    ], { title: comparisonSummary(result) });
    if (item?.attempt) return present(root, session, await invokeCheck(executable(), root, session, "show", item.attempt), generation);
    if (item) await details(item.report ? result : { reference_attempt: reference, candidate_attempt: candidate.attempt_id, ...item.group }, "comparison");
  }
  async function present(root, session, result, observedGeneration) {
    diagnostics.clear();
    const rows = new Map();
    for (const finding of [...(result.interpretation?.findings || []), ...recipeAssertionFindings(result)]) {
      if (!result.sourceCurrent || observedGeneration !== generation) break;
      if (!finding.location) continue;
      try {
        const target = verifiedSourceTarget(root, finding.location);
        if (vscode.workspace.textDocuments.some((doc) => doc.uri.fsPath === target.path && doc.isDirty)) continue;
        const loc = finding.location;
        const severity = { error: vscode.DiagnosticSeverity.Error, warning: vscode.DiagnosticSeverity.Warning, information: vscode.DiagnosticSeverity.Information }[finding.severity];
        const item = new vscode.Diagnostic(new vscode.Range(loc.start.line - 1, loc.start.column - 1, loc.end.line - 1, loc.end.column - 1), finding.message, severity);
        item.source = "Workbench saved check"; item.code = finding.code;
        if (!rows.has(target.path)) rows.set(target.path, []);
        rows.get(target.path).push(item);
      } catch (_) { /* Historical evidence must not decorate newer bytes. */ }
    }
    diagnostics.set([...rows].map(([path, items]) => [vscode.Uri.file(path), items]));
    const choices = [{ label: "Inspect check evidence and cleanup", type: "report" },
      ...(result.assertions ? [{ label: `Recipe assertion: ${result.assertions.state} — explain expected and observed`, type: "assertions" }, { label: "Open exact expected recipe source", type: "recipe-source" }] : []),
      ...(result.provenance ? [{ label: "Inspect selected pack, dependencies and environment", type: "provenance", detail: provenanceSummary(result) }, { label: "Compare with a retained reference run", type: "compare" }] : []),
      ...((result.state === "needs-attention" || result.cleanup?.state === "blocked") && !result.presentation?.recovery ? [{ label: "Recover interrupted process and disposable files", type: "recover" }] : []),
      ...(result.evidence || []).map((log) => ({ label: `Open ${log.path}`, type: "log", log })),
      ...(result.diagnostics || []).map((group) => ({ label: `${group.family} · ${group.occurrence_count} occurrence(s)`, description: group.subject, detail: group.guidance, type: "group", group }))];
    const chosen = await vscode.window.showQuickPick(choices, { title: `Saved check: ${result.state} · cleanup ${result.cleanup?.state || "not-started"}`, placeHolder: result.provenance ? provenanceSummary(result) : "Retained execution evidence" });
    if (!chosen) return;
    if (chosen.type === "compare") return compare(root, session, result);
    if (chosen.type === "provenance") return details({ source: result.candidate, ...result.provenance }, "provenance");
    if (chosen.type === "assertions") return presentRecipe(root, session, result);
    if (chosen.type === "recipe-source") return openSourceLocation(vscode, root, result.assertions.expectation.subject.location);
    if (chosen.type === "group") {
      const findings = chosen.group.finding_indices.map((index) => result.interpretation.findings[index]);
      const item = await vscode.window.showQuickPick([{ label: "Inspect group and all evidence", report: true }, ...findings.map((finding) => ({ label: finding.message.split("\n")[0], description: finding.location?.path || finding.evidence[0]?.log, finding }))], { title: chosen.group.guidance });
      if (!item) return;
      if (item.finding?.location) return openSourceLocation(vscode, root, item.finding.location);
      return details(item.report ? { ...chosen.group, findings } : item.finding, "diagnostic");
    }
    if (chosen.type === "recover") {
      const consent = await vscode.window.showWarningMessage("Close only this attempt's exactly identified processes and move its disposable files to recoverable trash? This does not rerun or promote the check.", { modal: true }, "Recover this attempt");
      if (consent !== "Recover this attempt") return;
      const requestId = result.request_id || result.request?.id || result.presentation?.request_id;
      await invokeCheck(executable(), root, session, "recover", result.attempt_id, requestId);
      return present(root, session, await invokeCheck(executable(), root, session, "show", result.attempt_id), generation);
    }
    if (chosen.type === "finding" && chosen.finding.location) await openSourceLocation(vscode, root, chosen.finding.location);
    else {
      const contents = chosen.type === "log" ? (await invokeCheck(executable(), root, session, "log", result.attempt_id, chosen.log.path)).text : JSON.stringify(chosen.type === "finding" ? chosen.finding : result, null, 2);
      await details(contents);
    }
  }
  async function run() {
    if (active) { await vscode.window.showInformationMessage("A saved check is already active in this IDE. Cancel it through its progress notification."); return; }
    if (!vscode.workspace.isTrusted) throw new Error("Trust the project before executing developer checks.");
    const folders = vscode.workspace.workspaceFolders || [];
    if (folders.length !== 1 || folders[0].uri.scheme !== "file") throw new Error("Select one local checkout.");
    const root = folders[0].uri.fsPath;
    active = true;
    try {
      const session = await selectContext(vscode, executable(), root); if (!session) return;
      const mode = await vscode.window.showQuickPick(["Run a new saved-candidate check", "Run a recipe check on saved changes", "Run Axiom material preflight", "Reopen an Axiom material check", "Reopen an existing attempt", "Reopen environment preparation"], { title: "Saved developer checks" });
      if (!mode) return;
      if (mode === "Run Axiom material preflight" || mode === "Reopen an Axiom material check") {
        const { createMaterialChecks } = require("./materialChecks");
        materials ||= createMaterialChecks(vscode, context, executable, { diagnostics, details, generation: () => generation, results: materialResults });
        return mode === "Run Axiom material preflight" ? await materials.run(root, session) : await materials.reopen(root, session);
      }
      if (mode === "Reopen environment preparation") {
        const attempt = await vscode.window.showInputBox({ title: "Exact environment preparation ID", value: lastEnvironment || "" });
        if (!attempt) return;
        const result = await invokeCheck(executable(), root, session, "environment-show", attempt);
        const request = result.request || result;
        if (result.state === "running") {
          const choice = await vscode.window.showWarningMessage("Preparation is running. Request cancellation and wait for Core to close its processes?", { modal: true }, "Cancel preparation");
          if (choice) await invokeCheck(executable(), root, session, "environment-cancel", attempt);
        } else if (result.state === "needs-attention" || result.cleanup?.state === "blocked") {
          const choice = await vscode.window.showWarningMessage("Recover this preparation's exact process custody and private files? Copied account files are removed only after closure.", { modal: true }, "Recover preparation");
          if (choice) await invokeCheck(executable(), root, session, "environment-recover", attempt, result.request_id || request.id);
        } else await vscode.window.showInformationMessage(`Environment ${result.state}${result.image_id ? ` · ${result.image_id}` : ""}`);
        return;
      }
      if (mode.startsWith("Reopen")) {
        const attempt = await vscode.window.showInputBox({ title: "Exact check attempt ID", value: lastAttempt || "" });
        const observedGeneration = generation;
        if (attempt) await present(root, session, await invokeCheck(executable(), root, session, "show", attempt), observedGeneration);
        return;
      }
      let available = await invokeCheck(executable(), root, session, "images");
      let image = await vscode.window.showQuickPick([...available.images.map((row) => ({ label: row.id, description: `${row.binding.pack} · ${row.binding.side}` })), { label: "Prepare with Packwiz and Prism", prepare: true }, { label: "Import a native runtime image (advanced)", import: true }], { title: "Installed runtime image" });
      if (!image) return;
      if (image.prepare) {
        const values = {};
        for (const [name, title] of [["prism", "Native Prism Launcher executable"], ["packwiz", "Native Packwiz executable"], ["java", "Platform-selected JDK bin/java"], ["accounts", "Prism accounts.json to copy privately — never paste credentials"]]) {
          const paths = await vscode.window.showOpenDialog({ title, canSelectFiles: true, canSelectFolders: false, canSelectMany: false });
          if (!paths?.length) return;
          values[name] = paths[0].fsPath;
        }
        const seedChoice = await vscode.window.showInformationMessage("Use an existing downloaded mod directory as a seed? Only files matching this pack's exact hashes will be copied.", "Select seed directory", "Continue without seed");
        if (!seedChoice) return;
        if (seedChoice === "Select seed directory") {
          const paths = await vscode.window.showOpenDialog({ title: "Hash-verified mod seed directory", canSelectFiles: false, canSelectFolders: true, canSelectMany: false });
          if (!paths?.length) return;
          values.seeds = [paths[0].fsPath];
        }
        const plan = await invokeCheck(executable(), root, session, "plan-environment", values);
        lastEnvironment = plan.attempt_id;
        await context.workspaceState?.update("workbench.lastEnvironment", lastEnvironment);
        const consent = await vscode.window.showWarningMessage(`Prepare saved candidate ${plan.candidate_id}?\n\n${plan.effects.join("\n")}\n\nNo Minecraft execution. Trusted local tools, not a sandbox.`, { modal: true }, "Prepare this environment");
        if (consent !== "Prepare this environment") return;
        const prepared = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Preparing environment · ${plan.attempt_id}`, cancellable: true }, async (progress, cancellation) => {
          const subscription = cancellation.onCancellationRequested(() => {
            progress.report({ message: "Cancellation requested; waiting for Core cleanup" });
            void invokeCheck(executable(), root, session, "environment-cancel", plan.attempt_id).catch((error) => vscode.window.showErrorMessage(`Cancellation failed: ${error.message}. Reopen ${plan.attempt_id}.`));
          });
          try { return await invokeCheck(executable(), root, session, "prepare-environment", plan.attempt_id, plan.id); }
          finally { subscription.dispose(); }
        });
        if (prepared.state !== "ready") throw new Error(`Environment ${prepared.state}; reopen ${plan.attempt_id}. No game was executed.`);
        image = { label: prepared.image_id };
      }
      if (image.import) {
        const choice = await vscode.window.showInformationMessage("Import a self-contained native client and its Java toolchain? This copies local files; it does not launch or download anything.", "Import installed runtime");
        if (!choice) return;
        const roots = await vscode.window.showOpenDialog({ title: "Installed native client game directory", canSelectFiles: false, canSelectFolders: true, canSelectMany: false });
        if (!roots?.length) return;
        const java = await vscode.window.showOpenDialog({ title: "Exact native JDK bin/java", canSelectFiles: true, canSelectFolders: false, canSelectMany: false });
        if (!java?.length) return;
        const arguments_ = await vscode.window.showInputBox({ title: "Direct Java arguments as a JSON array", prompt: "Use the current Cleanroom entry point, image-relative resources, --gameDir . and offline token 0. Do not paste account tokens." });
        if (!arguments_) return;
        await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Importing and verifying native runtime image", cancellable: false }, () => invokeCheck(executable(), root, session, "import-image", { root: roots[0].fsPath, java: java[0].fsPath, arguments: arguments_ }));
        available = await invokeCheck(executable(), root, session, "images");
        image = await vscode.window.showQuickPick(available.images.map((row) => ({ label: row.id, description: `${row.binding.pack} · ${row.binding.side}` })), { title: "Installed runtime image" });
      }
      if (!image) return;
      let recipeOptions;
      if (mode === "Run a recipe check on saved changes") {
        const expectation = await vscode.window.showQuickPick(["Present: current saved source", "Present: retained source", "Absent: retained source"], { title: "Recipe expectation — observation only, no source application" });
        if (!expectation) return;
        const reference = expectation.includes("retained") ? await vscode.window.showInputBox({ title: "Exact retained reference attempt ID" }) : undefined;
        if (expectation.includes("retained") && !reference) return;
        const path = await vscode.window.showInputBox({ title: "Recipe source path relative to checkout", prompt: "groovy/postInit/...groovy; leave empty to inspect the bounded catalog" });
        if (path === undefined) return;
        const catalog = await invokeCheck(executable(), root, session, "recipes", { path, reference });
        const recipe = await vscode.window.showQuickPick(catalog.recipes.map(row => ({ label: `${row.map} · ${row.location.path}:${row.location.start.line}`, description: row.support, detail: row.reasons.join("; ") || JSON.stringify(row.recipe), row })), { title: "Select exact source recipe — static candidate, not execution proof" });
        if (!recipe) return;
        const observation = await vscode.window.showQuickPick(["Registration, actual lookup decisions, and final snapshot", "Final snapshot only"], { title: "Observation scope — real GTCEu lookup, not machine execution validation" });
        if (!observation) return;
        recipeOptions = { recipe: recipe.row.id, reference, absent: expectation.startsWith("Absent"), trace: observation !== "Final snapshot only" };
      }
      const request = await invokeCheck(executable(), root, session, "prepare", image.label, recipeOptions);
      lastAttempt = request.attempt_id;
      if (request.expectation?.support === "unsupported") return details(request.expectation, "unsupported-recipe-expectation");
      const consent = await vscode.window.showWarningMessage(`Run ${request.check.label} for saved candidate ${request.candidate.id}?\n\n${provenanceSummary(request)}\n\n${expectationSummary(request)}\n\n${request.effects.join("\n")}\n\nNo downloads. Unsaved edits are excluded. Executable projects are trusted code, not sandboxed.`, { modal: true }, "Run this exact check");
      if (consent !== "Run this exact check") return;
      const observedGeneration = generation;
      const result = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Checking saved candidate · ${request.attempt_id}`, cancellable: true }, async (progress, cancellation) => {
        const subscription = cancellation.onCancellationRequested(() => {
          progress.report({ message: "Requesting cancellation; waiting for owned processes to close" });
          void invokeCheck(executable(), root, session, "cancel", request.attempt_id).catch((error) => vscode.window.showErrorMessage(`Cancellation request failed: ${error.message}. Reopen ${request.attempt_id}.`));
        });
        try { return await monitorCheck(
          () => invokeCheck(executable(), root, session, "execute", request.attempt_id, request.id),
          () => invokeCheck(executable(), root, session, "progress", request.attempt_id),
          (message) => { if (!cancellation.isCancellationRequested) progress.report({ message }); },
          request.attempt_id, request.id,
        ); }
        finally { subscription.dispose(); }
      });
      await present(root, session, result, observedGeneration);
    } finally { active = false; }
  }
  context.subscriptions.push(diagnostics, provider, watch, watch.onDidChange(clear), watch.onDidCreate(clear), watch.onDidDelete(clear),
    vscode.workspace.onDidChangeTextDocument((event) => { if (event.document.uri.scheme === "file" && event.contentChanges.length) clear(); }),
    vscode.workspace.onDidChangeWorkspaceFolders(clear),
    vscode.commands.registerCommand("workbench.checks.saved", () => run().catch((error) => vscode.window.showErrorMessage(`Saved check unavailable: ${error.message}${lastAttempt ? ` · Reopen ${lastAttempt}` : ""}`))));
}
module.exports = { registerDeveloperChecks };
