"use strict";
const { REQUEST, invokeMaterialCheck, materialSummary, materialReport, materialFindingMessage, findingLabel, retainedMaterialSource } = require("./materialChecksClient");
const snapshot = require("./materialSnapshotClient");
const delivery = require("./materialDeliveryClient");
const { verifiedSourceTarget, openSourceLocation } = require("./sourceNavigationClient");

function createMaterialChecks(vscode, context, executable, { diagnostics, details, generation, trace = () => {}, results }) {
  let lastAttempt = context.workspaceState?.get("workbench.lastMaterialAttempt");
  let activeView;
  let executing = false;
  const call = (root, session, ...args) => invokeMaterialCheck(executable(), root, session, ...args);
  async function remember(attempt) {
    lastAttempt = attempt;
    await context.workspaceState?.update("workbench.lastMaterialAttempt", attempt);
  }
  async function run(root, session, baseline, configure = false) {
    trace("run-action");
    const catalog = await call(root, session, "contexts");
    if (catalog.storage?.before_work_notice) {
      const choice = await vscode.window.showWarningMessage(catalog.storage.before_work_notice, { modal: true }, "Review storage and exports", "Continue with complete capture");
      if (choice === "Review storage and exports") return storageSettings(root, session);
      if (choice !== "Continue with complete capture") return;
    }
    const remembered = context.workspaceState?.get("workbench.materialContext");
    const current = !configure && !baseline && remembered?.session === session && catalog.policy.contexts.find(row => row.id === remembered.context);
    const selected = current ? { row: current } : await vscode.window.showQuickPick(catalog.policy.contexts.map(row => ({ label: row.label, description: row.qualification,
      detail: `Captures saved workspace; only the selected native phase runs. Excludes: ${row.excludedComposition.join("; ")}`, row })), { title: "Axiom material context — no qualified validity claim" });
    if (!selected) return;
    const values = { context: selected.row.id, ...(baseline ? { baseline } : {}) };
    const setup = await call(root, session, "setup-status", values);
    if (setup.state === "stale" && !configure) {
      const choice = await vscode.window.showWarningMessage(`Axiom setup needs review: ${setup.failure?.message || "Saved input bindings changed."} No source was checked.`, { modal: true }, "Review setup");
      if (choice !== "Review setup") return;
    }
    const previous = context.workspaceState?.get("workbench.materialInputs") || {};
    if (configure || setup.state !== "ready") {
      for (const [name, title, fallback] of [
      ["programRoot", "Complete saved program directory relative to this checkout ('.' includes all pack Groovy)", "."],
      ["intent", "Saved material expectations JSON (optional; leave blank for native errors)", ""],
      ["engineHome", "Installed Axiom engine directory (explicit native Linux path)", ""],
      ["runtimeHome", "Selected local native material runtime directory", ""],
      ["java", "Selected Cleanroom JDK bin/java (independent installed toolchain)", ""],
      ]) {
        const saved = { programRoot: setup.program_root, engineHome: setup.paths?.engine_home, runtimeHome: setup.paths?.runtime_home, java: setup.paths?.java };
        const value = await vscode.window.showInputBox({ title, value: saved[name] ?? previous[name] ?? fallback, ignoreFocusOut: true });
        if (value === undefined || (!value.trim() && name !== "intent")) return;
        values[name] = value.trim();
      }
      await call(root, session, "setup", values);
      await context.workspaceState?.update("workbench.materialInputs", values);
    } else if (previous.context === values.context && remembered?.session === session) {
      values.intent = previous.intent;
    }
    // Core owns the bound paths/default program root and revalidates them for
    // every preparation. A workspace cache is never native input authority.
    const request = await call(root, session, "prepare", { context: values.context, baseline: values.baseline, intent: values.intent });
    await context.workspaceState?.update("workbench.materialContext", { session, context: values.context });
    await remember(request.attempt_id);
    return execute(root, session, request);
  }
  async function execute(root, session, request) {
    const consent = await vscode.window.showWarningMessage(`${materialSummary(request)}\n\nRequest: ${request.id}\nUses saved files; unsaved edits are excluded. Runs isolated native material logic, not Minecraft. This context is not yet qualified.`, { modal: true }, "Run this exact material check");
    if (consent !== "Run this exact material check") return;
    trace("confirmation", request);
    diagnostics.clear();
    activeView = undefined;
    results?.clear();
    const previous = context.workspaceState?.get("workbench.lastCompletedMaterial");
    if (previous?.root === root && previous.session === session && previous.context === request.inputs.context.id) {
      try {
        const historic = await call(root, session, "show", previous.attempt);
        await details(`Previous completed check — historical while a new check runs\n${materialSummary(historic)}`, "material-history", "txt");
      } catch (error) { await vscode.window.showWarningMessage(`Previous check details unavailable: ${error.message}`); }
    }
    const observedGeneration = generation();
    executing = true;
    let early, presentation;
    try {
    const result = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Axiom: checking saved source", cancellable: true }, async (progress, cancellation) => {
      progress.report({ message: "Running native initialization" });
      const cancel = () => {
        progress.report({ message: "Requesting cancellation; waiting for Core to close the native workers" });
        void call(root, session, "cancel", request.attempt_id).catch(error => vscode.window.showErrorMessage(`Material cancellation failed: ${error.message}. Reopen ${request.attempt_id}.`));
      };
      const subscription = cancellation.onCancellationRequested(cancel);
      if (cancellation.isCancellationRequested) cancel();
      try {
        if (request.diagnostic_delivery_format !== delivery.VIEW) return await call(root, session, "execute", request.attempt_id, request.id);
        return await delivery.monitor(
        () => call(root, session, "execute", request.attempt_id, request.id),
        () => call(root, session, "delivery", request.attempt_id),
        revision => call(root, session, "diagnostics", request.attempt_id, { revision, offset: 0 }),
        view => {
          early = view; trace("diagnostics-received", view);
          progress.report({ message: "Findings available in Axiom Results; preparing recipe details" });
          presentation = present(root, session, view, observedGeneration);
          // Observe rejection immediately even if finalization takes longer.
          presentation.catch(error => vscode.window.showErrorMessage(error.message));
        }, request, message => progress.report({ message })); }
      finally { subscription.dispose(); }
    });
    if (result.format === snapshot.VIEW && result.snapshot_id && result.state === "completed") await context.workspaceState?.update("workbench.lastCompletedMaterial", {
      root, session, context: result.context_id, attempt: result.attempt_id });
    trace("snapshot-finished", result);
    if (early) {
      early.detail_state = result.snapshot_id ? "ready" : "interrupted";
      results?.update(early);
      await presentation;
    } else await present(root, session, result, observedGeneration);
    } catch (error) {
      if (early) {
        try { early.detail_state = (await call(root, session, "delivery", request.attempt_id)).detail_state; }
        catch (_) { early.detail_state = "unavailable"; }
        results?.update(early);
      }
      throw error;
    } finally { executing = false; }
  }
  async function reopen(root, session) {
    const history = await call(root, session, "history");
    const selected = await vscode.window.showQuickPick([
      ...history.attempts.filter(row => row.state !== "unavailable").map(row => ({ label: row.attempt_id, description: row.state, attempt: row.attempt_id })),
      { label: "Enter an exact retained material attempt ID", manual: true },
      { label: "Storage and retention", retention: true, detail: "Usage, protections, recoverable trash and finite history preferences" },
    ], { title: "Retained Axiom checks — no automatic rerun" });
    if (!selected) return;
    if (selected.retention) return storageSettings(root, session);
    const attempt = selected.manual ? await vscode.window.showInputBox({ title: "Exact material-check attempt ID", value: lastAttempt || "" }) : selected.attempt;
    if (!attempt) return;
    await remember(attempt);
    const observedGeneration = generation();
    await present(root, session, await call(root, session, "show", attempt), observedGeneration);
  }
  async function storageSettings(root, session) {
    const status = await call(root, session, "retention", { operation: "status" });
    const choice = await vscode.window.showQuickPick([
      { label: "Inspect storage and protections", action: "status" },
      { label: "Edit retention preferences", action: "configure" },
      { label: "Choose active contexts", action: "contexts" },
      { label: "Account for another known store", action: "stores" },
      { label: "Preview cleanup", action: "preview" },
      { label: "Run enabled maintenance", action: "maintain" },
    ], { title: `Check storage · ${status.policy.settings.mode} · ${status.allocated_bytes ?? "unknown"} allocated bytes`,
      placeHolder: status.notices.join(" ") || "Core preserves pins, required evidence and active readers." });
    if (!choice) return;
    if (choice.action === "status") return details(status, "material-storage");
    if (["preview", "maintain"].includes(choice.action)) return details(await call(root, session, "retention", { operation: choice.action }), "material-storage");
    const settings = { ...status.policy.settings };
    if (choice.action === "configure") {
      const mode = await vscode.window.showQuickPick([
        { label: "Finite history", value: "finite" }, { label: "Keep everything — storage can grow", value: "keep-everything" },
        { label: "Disable automatic maintenance", value: "disabled" },
      ], { title: "Retention mode — review permanent effects before applying" });
      if (!mode) return;
      settings.mode = mode.value;
      for (const [key, title] of [["max_count", "Preferred live check count"], ["max_bytes", "Preferred total allocated bytes (including trash and indexes)"],
        ["min_age_days", "Minimum age in days before quarantine"], ["trash_days", "Recoverable trash grace in days"], ["metadata_count", "Number of recent expiry summaries to retain"]]) {
        const value = await vscode.window.showInputBox({ title, value: String(settings[key]),
          validateInput: text => /^\d+$/.test(text) && Number.isSafeInteger(Number(text)) && (Number(text) > 0 || ["min_age_days", "trash_days"].includes(key)) ? undefined : "Enter a supported whole-number history preference." });
        if (value === undefined) return;
        settings[key] = Number(value);
      }
    } else if (choice.action === "contexts") {
      const selected = await vscode.window.showQuickPick(status.contexts.map(row => ({ label: JSON.stringify(row.context), description: row.key, picked: row.active, key: row.key })),
        { title: "Protect the latest useful check for these active contexts", canPickMany: true });
      if (!selected) return;
      settings.active_contexts = selected.map(row => row.key);
    } else {
      const store = await vscode.window.showQuickPick([{ label: "Add a known store", add: true },
        ...settings.known_stores.map(path => ({ label: `Stop accounting for ${path}`, path }))], { title: "Known stores — changing this list never deletes their data" });
      if (!store) return;
      if (store.add) {
        const path = await vscode.window.showInputBox({ title: "Known Core check store (absolute native path; accounting only)", value: "" });
        if (!path) return;
        settings.known_stores = [...new Set([...settings.known_stores, path])];
      } else settings.known_stores = settings.known_stores.filter(path => path !== store.path);
    }
    const proposal = await call(root, session, "retention", { operation: "configure", settings });
    const accept = await vscode.window.showWarningMessage(proposal.proposal.disclosure, { modal: true }, "Apply these retention preferences");
    if (accept !== "Apply these retention preferences") return;
    const result = await call(root, session, "retention", { operation: "configure", settings }, proposal.proposal.id);
    return details(result, "material-retention-policy");
  }
  function annotate(root, result, observedGeneration) {
    diagnostics.clear();
    const rows = new Map();
    for (const finding of result.findings || []) {
      if (!result.sourceCurrent || observedGeneration !== generation()) break;
      // Baseline observations belong in the retained report, not candidate markers.
      if (finding.side !== "candidate" || !finding.location) continue;
      try {
        const target = verifiedSourceTarget(root, finding.location);
        if (vscode.workspace.textDocuments.some(doc => doc.uri.fsPath === target.path && (doc.isDirty || doc.getText().replace(/\r\n/g, "\n") !== target.text))) continue;
        const loc = finding.location;
        const severity = { error: vscode.DiagnosticSeverity.Error, warning: vscode.DiagnosticSeverity.Warning }[finding.severity?.toLowerCase()] ?? vscode.DiagnosticSeverity.Information;
        const item = new vscode.Diagnostic(new vscode.Range(loc.start.line - 1, loc.start.column - 1, loc.end.line - 1, loc.end.column - 1), findingLabel(result, finding), severity);
        item.source = "Axiom saved material check"; item.code = finding.code || finding.id;
        if (!rows.has(target.path)) rows.set(target.path, []);
        rows.get(target.path).push(item);
      } catch (_) { /* Retained observations never retarget changed live source. */ }
    }
    diagnostics.set([...rows].map(([path, items]) => [vscode.Uri.file(path), items]));
    trace("markers-updated", result);
  }
  async function present(root, session, result, observedGeneration) {
    if (vscode.window.createTreeView && result.format === snapshot.VIEW) {
      try {
        const status = await call(root, session, "delivery", result.attempt_id);
        if (status.diagnostic_id) result = await call(root, session, "diagnostics", result.attempt_id, { revision: status.diagnostic_id });
      } catch (_) { /* Earlier Core versions retain their existing snapshot browser. */ }
    }
    activeView = result.view_id;
    annotate(root, result, observedGeneration);
    if (vscode.window.createTreeView && result.format === delivery.VIEW) {
      results ||= require("./materialResults").createMaterialResults(vscode, context);
      async function captured(finding) {
        const value = await call(root, session, "source", result.attempt_id, { revision: result.diagnostic_id, finding: finding.id });
        if (activeView === result.view_id) return details(retainedMaterialSource(value, result, finding), "captured-material-source", "groovy", finding.location.start.line);
      }
      await results.show(result, {
        fresh: () => result.sourceCurrent && observedGeneration === generation(),
        read: (group, offset) => call(root, session, "diagnostics", result.attempt_id, { revision: result.diagnostic_id, offset, ...(group ? { group } : {}) }),
        annotations: (findings, fresh) => { if (activeView === result.view_id) annotate(root, { ...result, findings, sourceCurrent: result.sourceCurrent && fresh }, observedGeneration); },
        open: async (action, finding, fresh) => {
          if (activeView !== result.view_id) return;
          if (action === "captured" && finding.location) return captured(finding);
          if (action === "open" && finding.location && finding.side === "candidate") {
            try { if (!fresh) throw new Error("Source changed or is not verified."); return await openSourceLocation(vscode, root, finding.location); }
            catch (_) {
              const choice = await vscode.window.showWarningMessage("Source changed or has unsaved edits. The captured source remains available.", {}, "Open captured source");
              if (choice === "Open captured source" && activeView === result.view_id) return captured(finding);
              return;
            }
          }
          const evidence = await call(root, session, "diagnostic", result.attempt_id, { revision: result.diagnostic_id, finding: finding.id });
          if (activeView === result.view_id) return details(evidence, "original-material-diagnostic");
        },
        snapshot: async () => {
          const status = await call(root, session, "delivery", result.attempt_id);
          result.detail_state = status.detail_state; results.update(result);
          if (status.detail_state === "ready" && activeView === result.view_id) return sections(root, session, await call(root, session, "show", result.attempt_id));
        },
        actions: async () => {
          const choice = await vscode.window.showQuickPick(["Read outcome and scope", "Inspect run evidence", ...(result.detail_state === "ready" ? ["Export complete result"] : []), "Review setup", "Compare a saved edit"], { title: "Axiom run actions" });
          if (activeView !== result.view_id) return;
          if (choice === "Read outcome and scope") return details(materialReport(result), "material-report", "txt");
          if (choice === "Inspect run evidence") return details(result, "material-evidence");
          if (choice === "Export complete result" && result.detail_state === "ready") return exportValue(root, session, await call(root, session, "show", result.attempt_id));
          if (["Review setup", "Compare a saved edit"].includes(choice)) {
            if (executing) return vscode.window.showInformationMessage("This check is still finishing. Cancel it or wait before running another check.");
            return run(root, session, choice === "Compare a saved edit" ? result.attempt_id : undefined, choice === "Review setup");
          }
        },
      });
      trace("results-visible", result);
      return;
    }
    const prepared = result.format === REQUEST;
    const interrupted = result.presentation?.attempt_state === "started-without-retained-result";
    const choice = await vscode.window.showQuickPick([
      ...(result.format === delivery.VIEW ? [
        { label: "Open completed snapshot detail", type: "snapshot", detail: `Snapshot: ${result.detail_state}` },
        ...(result.next_offset !== null ? [{ label: "Load next findings page", type: "early-next" },
          { label: "Load remaining findings", type: "early-remaining" }] : []),
      ] : []),
      { label: "Read native outcomes, expectations and limitations", type: "read" },
      { label: "Inspect exact retained evidence", type: "raw" },
      ...(result.format === snapshot.VIEW && result.snapshot_id ? [
        { label: "Browse retained sections and values", type: "sections", detail: "Load selected records; large values export to a file." },
        { label: "Export complete original result", type: "export" },
        ...(result.finding_page?.state === "ready" && !result.finding_page.complete ? [
          { label: "Load next findings page", type: "next", detail: snapshot.progress(result) },
          { label: "Load remaining findings", type: "remaining", detail: "Progressive reads; cancellation keeps already loaded findings." },
        ] : []),
      ] : []),
      ...(!prepared ? [{ label: "Compare a new saved edit against this program", type: "compare" }] : []),
      { label: "Review Axiom setup or optional expectations", type: "configure" },
      ...(prepared && !interrupted ? [{ label: "Run this exact prepared material check", type: "execute" }] : []),
      ...(interrupted ? [{ label: "Request cancellation of this started attempt", type: "cancel", detail: "A start marker does not prove a live worker; this never reruns it." }] : []),
      ...(result.findings || []).map(finding => ({ label: `${finding.side} · ${findingLabel(result, finding).split("\n")[0]}`, description: finding.location ? `${finding.location.path}:${finding.location.start.line}` : "unlocated", detail: findingLabel(result, finding), type: "finding", finding })),
    ], { title: `Axiom: ${interrupted ? "started without retained result" : result.state}`, placeHolder: materialSummary(result) });
    if (!choice) return;
    if (choice.type === "snapshot") {
      const status = await call(root, session, "delivery", result.attempt_id);
      if (status.detail_state !== "ready") return details(`Snapshot detail: ${status.detail_state}. Diagnostic evidence remains available.`, "material-detail-status", "txt");
      return present(root, session, await call(root, session, "show", result.attempt_id), observedGeneration);
    }
    if (choice.type === "early-next" || choice.type === "early-remaining") {
      const view = activeView;
      await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Loading retained findings", cancellable: true }, async (_, token) => {
        do {
          if (token.isCancellationRequested || activeView !== view) break;
          const page = await call(root, session, "diagnostics", result.attempt_id, { revision: result.diagnostic_id, offset: result.next_offset });
          if (activeView !== view) return;
          result.findings.push(...page.findings); result.next_offset = page.next_offset;
          Object.assign(result.presentation.finding_labels, page.presentation.finding_labels);
        } while (choice.type === "early-remaining" && result.next_offset !== null);
      });
      if (activeView === view) return present(root, session, result, observedGeneration);
      return;
    }
    if (choice.type === "read") return details(prepared ? materialSummary(result) : materialReport(result), "material-report", "txt");
    if (choice.type === "raw") return details(result, "material-evidence");
    if (choice.type === "next" || choice.type === "remaining") return loadFindings(root, session, result, observedGeneration, choice.type === "remaining");
    if (choice.type === "sections") return sections(root, session, result);
    if (choice.type === "export") return exportValue(root, session, result);
    if (choice.type === "execute") return execute(root, session, result);
    if (choice.type === "compare") return run(root, session, result.attempt_id);
    if (choice.type === "configure") return run(root, session, undefined, true);
    if (choice.type === "cancel") return details(await call(root, session, "cancel", result.attempt_id), "material-cancellation");
    const finding = choice.finding;
    const source = await vscode.window.showQuickPick([
      { label: "Read native diagnostic evidence", type: "evidence" },
      ...(finding.location ? [{ label: "Read exact retained source", type: "retained" }, { label: "Open identical saved working copy", type: "live", detail: "Requires matching bytes and no unsaved editor edits." }] : []),
    ], { title: finding.location ? `${finding.location.path}:${finding.location.start.line} · native line anchor` : "Unlocated native diagnostic" });
    if (source?.type === "live") return openSourceLocation(vscode, root, finding.location);
    if (source?.type === "retained") {
      const view = await call(root, session, "source", result.attempt_id, result.format === delivery.VIEW ? { revision: result.diagnostic_id, finding: finding.id } : finding.id);
      return details(retainedMaterialSource(view, result, finding), "retained-material-source", "groovy", finding.location.start.line);
    }
    if (source && result.format === delivery.VIEW) return details(await call(root, session, "diagnostic", result.attempt_id, { revision: result.diagnostic_id, finding: finding.id }), "material-diagnostic");
    if (source && result.format === snapshot.VIEW) return readValue(root, session, result, snapshot.diagnosticQuery(result, finding));
    if (source) return details({ finding, native: result.native }, "material-diagnostic");
  }
  async function loadFindings(root, session, result, observedGeneration, all) {
    const view = activeView;
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Loading retained findings", cancellable: true }, async (progress, token) => {
      do {
        if (token.isCancellationRequested || activeView !== view) break;
        const query = snapshot.nextFindingsQuery(result, all);
        const page = await call(root, session, "query", result.attempt_id, query);
        if (activeView !== view) return;
        result.finding_page = page;
        if (page.state !== "ready") break;
        result.findings.push(...snapshot.findings(page));
        Object.assign(result.presentation.finding_labels, page.presentation.finding_labels);
        progress.report({ message: snapshot.progress(result) });
      } while (all && !result.finding_page.complete);
    });
    if (activeView === view) return present(root, session, result, observedGeneration);
  }
  async function exportValue(root, session, result, selected) {
    const uri = await vscode.window.showSaveDialog({ title: selected ? "Export complete selected value" : "Export complete original result", filters: { JSON: ["json"] } });
    if (!uri) return;
    const options = { snapshot_id: result.snapshot_id, destination: uri.fsPath,
      ...(selected ? { section_id: selected.section, record_key: selected.key, sha256: selected.content.sha256 } : {}) };
    const receipt = await call(root, session, "export", result.attempt_id, options);
    return details(receipt, "material-export");
  }
  async function readValue(root, session, result, query) {
    const view = activeView;
    const response = await call(root, session, "query", result.attempt_id, query);
    if (activeView !== view) return;
    if (response.state !== "ready") return details(response, "material-detail-status");
    const record = response.payload;
    if (Object.hasOwn(record, "value")) return details({ snapshot_id: result.snapshot_id, section_id: query.section_id, ...record }, "material-detail");
    const choice = await vscode.window.showQuickPick([{ label: "Export complete selected value", detail: `${record.content.bytes} bytes; full content remains retained`, export: true },
      { label: "Inspect content reference", export: false }], { title: "Large retained value — not loaded into the editor" });
    if (choice?.export) return exportValue(root, session, result, { section: query.section_id, key: record.key, content: record.content });
    if (choice) return details(record, "material-content-reference");
  }
  async function sections(root, session, result) {
    const chosen = await vscode.window.showQuickPick(result.sections.map(row => ({ label: row.id, description: `${row.state}${row.count === null ? "" : ` · ${row.count} records`}`, detail: row.reason || row.schema, row })), { title: "Retained snapshot sections — details not yet loaded" });
    if (!chosen) return;
    const view = activeView;
    let query = snapshot.query(result, "records", chosen.row.id);
    while (activeView === view) {
      const page = await call(root, session, "query", result.attempt_id, query);
      if (activeView !== view) return;
      if (page.state !== "ready") return details(page, "material-section-status");
      const item = await vscode.window.showQuickPick([
        ...page.payload.records.map((row, offset) => ({ label: `Record ${page.payload.offset + offset + 1}`, description: row.key, detail: row.content ? `${row.content.bytes} bytes · export available` : JSON.stringify(row.value).slice(0, 160), row })),
        ...(!page.complete ? [{ label: "Next records page", next: true }] : []),
      ], { title: `${chosen.row.id} · ${page.payload.offset + page.payload.records.length} of ${page.payload.total}` });
      if (!item) return;
      if (item.next) { query = { ...query, cursor: page.next_cursor }; continue; }
      return readValue(root, session, result, snapshot.query(result, "record", chosen.row.id, { record_key: item.row.key }));
    }
  }
  return { run, reopen, present, storageSettings, invalidate: () => results?.invalidate() };
}
module.exports = { createMaterialChecks };
