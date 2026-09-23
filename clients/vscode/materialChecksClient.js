"use strict";
const fs = require("node:fs");
const crypto = require("node:crypto");
const { pathToFileURL } = require("node:url");
const { invokeCoreJson } = require("./coreCommandClient");
const { resolveCoreLaunch } = require("./coreLaunch");
const { validateCheck } = require("./developerChecksClient");
const snapshot = require("./materialSnapshotClient");
const delivery = require("./materialDeliveryClient");

const REQUEST = "workbench-material-check-request-v1", RESULT = "workbench-material-check-result-v1";
const attemptPattern = /^material-check-[0-9a-f]{32}$/;
function exactAttempt(value) {
  if (!attemptPattern.test(value || "")) throw new Error("Select an exact material-check attempt.");
  return value;
}
function relative(value, allowRoot = false) {
  if (allowRoot && value === ".") return value;
  if (typeof value !== "string" || /[\\:\0\r\n]/.test(value) || value.split("/").some(p => ["", ".", "..", ".git"].includes(p))) throw new Error("Select a saved checkout-relative program or intent path.");
  return value;
}
function materialArguments(session, action, value, confirmation) {
  if (!/^work-session-v2-[0-9a-f]{32}$/.test(session || "")) throw new Error("Select an exact Work Session.");
  if (!["contexts", "setup", "setup-status", "prepare", "execute", "show", "history", "source", "cancel", "query", "export", "retention", "delivery", "diagnostics", "diagnostic"].includes(action)) throw new Error("Unknown material-check action.");
  const args = ["context", "run", session, "--", "checks", "materials", action];
  if (action === "retention") {
    if (!["status", "preview", "configure", "maintain"].includes(value?.operation)) throw new Error("Select a Core retention operation.");
    args.push(value.operation);
    if (value.settings) args.push("--settings", JSON.stringify(value.settings));
    if (confirmation) {
      if (!/^check-retention-proposal:sha256:[0-9a-f]{64}$/.test(confirmation)) throw new Error("Confirm an exact retention proposal.");
      args.push("--confirm", confirmation);
    }
  }
  if (["prepare", "setup", "setup-status"].includes(action)) {
    const paths = [["engineHome", "engine-home"], ["runtimeHome", "runtime-home"], ["java", "java"]];
    const hasPaths = paths.some(([key]) => value?.[key] !== undefined);
    if (action === "setup-status" && hasPaths) throw new Error("Setup status uses the selected context, not replacement paths.");
    for (const [key, option] of (action === "setup" || hasPaths ? paths : [])) {
      if (typeof value?.[key] !== "string" || !value[key].startsWith("/") || /[\0\r\n]/.test(value[key])) throw new Error("Select explicit native Axiom inputs and the selected JDK bin/java.");
      args.push(`--${option}=${value[key]}`);
    }
    if (typeof value?.context !== "string" || !/^[a-z0-9.-]+:[a-z0-9.-]+$/.test(value.context)) throw new Error("Select an installed profile-owned material context.");
    args.push(`--context=${value.context}`);
    if (action !== "setup-status" && value.programRoot !== undefined) args.push(`--program-root=${relative(value.programRoot, true)}`);
    if (action === "prepare") {
      if (value.intent !== undefined && value.intent !== "") args.push(`--request=${relative(value.intent)}`);
      if (value.baseline) args.push(`--baseline=${exactAttempt(value.baseline)}`);
    }
  } else if (["execute", "show", "source", "cancel", "query", "export", "delivery", "diagnostics", "diagnostic"].includes(action)) args.push(exactAttempt(value));
  if (action === "execute") {
    if (!/^material-check-request:sha256:[0-9a-f]{64}$/.test(confirmation || "")) throw new Error("Confirm the exact saved material request.");
    args.push("--confirm", confirmation);
  } else if (["diagnostics", "diagnostic"].includes(action) || action === "source" && typeof confirmation === "object") {
    args.push(...delivery.argumentsFor(action, confirmation));
  } else if (action === "source") {
    if (!/^diagnostic-(?:0|[1-9][0-9]*)$/.test(confirmation || "")) throw new Error("Select one retained material diagnostic.");
    args.push("--diagnostic", confirmation);
  } else if (action === "query") {
    args.push("--query", JSON.stringify(confirmation));
  } else if (action === "export") {
    if (typeof confirmation?.destination !== "string" || !confirmation.destination.startsWith("/") || /[\0\r\n]/.test(confirmation.destination)) throw new Error("Select a new absolute export file.");
    args.push("--snapshot", confirmation.snapshot_id, "--destination", confirmation.destination);
    if (confirmation.section_id) args.push("--section", confirmation.section_id, "--key", confirmation.record_key, "--sha256", confirmation.sha256);
  }
  return args;
}
function validateMaterial(envelope, root, action, attempt) {
  const result = validateCheck(envelope, root);
  const formats = { delivery: [delivery.STATUS], diagnostics: [delivery.VIEW], diagnostic: ["workbench-material-diagnostic-evidence-v1"], contexts: ["workbench-material-contexts-v1"], prepare: [REQUEST], execute: [RESULT, snapshot.VIEW], show: [REQUEST, RESULT, snapshot.VIEW, delivery.VIEW],
    history: ["workbench-material-check-history-v1"], source: ["workbench-material-source-view-v1"], cancel: ["workbench-material-check-cancellation-v1"],
    setup: ["workbench-material-check-setup-v1"], "setup-status": ["workbench-material-check-setup-status-v1"],
    query: ["workbench-check-snapshot-response-v1"], export: ["workbench-material-export-v1"], retention: ["workbench-check-retention-response-v1"] };
  if (!formats[action]?.includes(result.format)) throw new Error("Unsupported material-check response.");
  if (action === "retention" && result.operation !== attempt?.operation) throw new Error("Retention response belongs to another operation.");
  if (["execute", "show", "source", "cancel", "export", "delivery", "diagnostics", "diagnostic"].includes(action) && result.attempt_id !== attempt) throw new Error("Material response belongs to another attempt.");
  if (["setup", "setup-status"].includes(action)) {
    if (result.context_id !== attempt?.context || (action === "setup" ? result.state !== "configured-not-run" : !["missing", "ready", "stale"].includes(result.state))) throw new Error("Material setup belongs to another context or has an unknown state.");
    if (action === "setup-status" && result.readiness_scope !== "saved-input-and-profile-bindings-only") throw new Error("Unexpected material setup readiness claim.");
  }
  if ([REQUEST, RESULT, snapshot.VIEW, delivery.VIEW, "workbench-material-contexts-v1"].includes(result.format)) {
    for (const key of ["source_mutated", "minecraft_launched", "runtime_image_required", "validity_qualified", "whole_pack_parity"]) {
      if (result.authority?.[key] !== false) throw new Error("Material check claims unexpected authority.");
    }
  }
  if (result.format === RESULT && (!Array.isArray(result.findings) || !["completed", "incomplete"].includes(result.state))) throw new Error("Invalid retained material result.");
  snapshot.attach(result);
  if (result.format === "workbench-material-source-view-v1" && (result.read_only !== true || typeof result.text !== "string")) throw new Error("Material source must be retained read-only text.");
  return result;
}
async function invokeMaterialCheck(executable, root, session, action, value, confirmation) {
  const launch = resolveCoreLaunch(executable);
  if (launch.host !== "native") throw new Error("Material preflight requires native Linux Workbench.");
  // Cancellation goes to the retained owner attempt, never aborts this transport.
  const envelope = await invokeCoreJson(executable, materialArguments(session, action, value, confirmation), {
    // MVP resource targets are suspended; cancellation belongs to the retained attempt.
    cwd: root, launch, maximumOutput: null, timeoutMs: null,
  });
  const result = validateMaterial(envelope, pathToFileURL(fs.realpathSync(root)).href, action, value);
  delivery.validate(result, action, confirmation);
  if (action === "query") snapshot.validatePage(result, confirmation);
  if (action === "export" && (result.state !== "verified" || result.destination !== confirmation.destination
      || result.content?.snapshot_id !== confirmation.snapshot_id || (confirmation.sha256 && result.content.sha256 !== confirmation.sha256))) throw new Error("Export differs from selected content or destination.");
  Object.defineProperty(result, "sourceCurrent", { value: envelope.presentation?.source_current === true });
  Object.defineProperty(result, "presentation", { value: envelope.presentation || {} });
  return result;
}
function observations(result) {
  if (!result.native) return [];
  const body = result.native.result || {};
  return body.baseline && body.candidate ? [{ side: "Baseline", native: body.baseline, pointer: "/result/baseline/result" }, { side: "Candidate", native: body.candidate, pointer: "/result/candidate/result" }] : [{ side: "Candidate", native: result.native, pointer: "/result" }];
}
function materialSummary(result) {
  if (result.format === REQUEST) return `Not run · ${result.program.files.length} complete program files\nContext: ${result.inputs.context.id}\nQualification: ${result.inputs.context.qualification}\nCandidate: ${result.candidate.id}${result.baseline ? "\nBaseline and candidate will both run fresh with the current saved intent." : ""}`;
  const rows = [`Workflow: ${result.presentation?.attempt_state || result.state} (not material validity)`];
  if (result.format === snapshot.VIEW) rows.push(`Initialization: ${result.native_outcome}; observation coverage: ${result.coverage}`,
    `Run: ${result.attempt_id}`, `Source: ${result.candidate_id}`, `Context: ${result.context_id}`,
    `Snapshot: ${result.snapshot_id}`, snapshot.progress(result),
    `Detailed evidence: ${result.detail_state}; native overview: ${result.overview_state}`,
    `Reader interpretation: ${result.interpretation?.state || "not established"}; unsupported sections: ${(result.interpretation?.unsupported_sections || []).join(", ") || "none reported"}`);
  if (result.format === delivery.VIEW) rows.push(`Initialization: ${result.native_outcome}; observation coverage: ${result.coverage}`,
    `Run: ${result.attempt_id}`, `Source: ${result.candidate_id}`, `Context: ${result.context_id}`,
    `Diagnostics displayed: ${result.findings.length} of ${result.findings_count}; complete diagnostic evidence is available`,
    `Snapshot detail: ${result.detail_state}`);
  if (result.failure) rows.push(`Invocation incomplete: ${result.failure.message}`);
  for (const row of observations(result)) {
    const body = row.native.result || {};
    const initialization = body.initialization;
    if (initialization?.schema === "axiom.scoped-initialization.v1") {
      rows.push(`${row.side} initialization (${initialization.scope}): ${initialization.status}`,
        `Native error observed: ${initialization.nativeErrorObserved === true ? "yes" : "no"}; this is not whole-pack validity.`);
      if (typeof initialization.recipeEffectsChecked === "boolean")
        rows.push(`Recipe registry effects checked: ${initialization.recipeEffectsChecked ? "yes" : "no"}`);
    }
    rows.push(`${row.side} native status: ${row.native.status}`, `Material execution: ${body.nativeOutcome || "not observed"}`,
      `Developer intent: ${body.expectations?.status || "not evaluated"}`, `Qualification: ${body.qualification || "not established"}`);
    if (body.assessment) {
      const assessment = body.assessment, counts = assessment.intentCounts || {};
      rows.push(`Execution checkpoint: ${assessment.execution}; coverage: ${assessment.coverage}`,
        `Expectation counts: matched ${counts.matched}; mismatched ${counts.mismatch}; unsupported ${counts.unsupported}; not evaluated ${counts["not-evaluated"]}`);
      for (const reason of assessment.reasons || []) rows.push(`Assessment [${reason.code}]: ${reason.message}\n  Evidence: ${reason.evidencePointer}`);
    }
  }
  const comparison = result.native?.result?.comparison;
  if (comparison) rows.push(`Native observation comparison: ${comparison.status} (not source causation)`);
  const source = result.native?.result?.sourceComparison;
  if (source) rows.push(`Saved file comparison: ${source.status} (not recipe validity)`);
  const effects = result.native?.result?.effectComparison;
  if (effects) rows.push(`Native effect comparison: ${effects.status} (observed membership and selected properties only)`);
  if (!result.sourceCurrent) rows.push("Current source differs or freshness is unavailable. Retained evidence is unchanged.");
  rows.push("No qualified material validity or whole-pack parity. No Minecraft launch.");
  return rows.join("\n");
}
function materialReport(result) {
  const rows = [materialSummary(result)];
  const pair = result.native?.result, comparison = pair?.comparison;
  for (const kind of ["added", "removed", "modified"]) for (const path of pair?.sourceComparison?.[kind] || []) rows.push(`Saved file ${kind}: ${path}`);
  if (comparison) {
    rows.push("\nComparison details");
    const before = pair.baseline?.result?.nativeOutcome, after = pair.candidate?.result?.nativeOutcome;
    rows.push(`Material execution outcome: ${before || "not observed"} → ${after || "not observed"}`);
    for (const reason of comparison.reasons || []) rows.push(`Not comparable: ${reason}`);
    for (const section of comparison.changedSections || []) rows.push(`Changed observation: ${section.field}\n  Baseline evidence: ${section.baselinePointer}\n  Candidate evidence: ${section.candidatePointer}`);
  }
  const effects = pair?.effectComparison;
  if (effects) {
    rows.push("\nNative registration effects — not proof of completed initialization");
    for (const reason of effects.reasons || []) rows.push(`Not comparable: ${reason}`);
    for (const [domain, effect] of Object.entries(effects.domains || {})) {
      rows.push(`${domain}: ${effect.status}${effect.membership ? ` · ${effect.membership}` : ""}`);
      for (const reason of effect.reasons || []) rows.push(`  Not comparable: ${reason}`);
      for (const kind of ["added", "removed", "modified"]) for (const entry of effect[kind] || []) {
        rows.push(`  ${kind}: ${entry.identity}`);
        if (entry.baselinePointer) rows.push(`    Baseline evidence: ${entry.baselinePointer}`);
        if (entry.candidatePointer) rows.push(`    Candidate evidence: ${entry.candidatePointer}`);
        if (entry.baselineOwnerPointer) rows.push(`    Baseline owner registration: ${entry.baselineOwnerPointer}`);
        if (entry.candidateOwnerPointer) rows.push(`    Candidate owner registration: ${entry.candidateOwnerPointer}`);
      }
    }
    rows.push("Custom variants are native definitions; owner Forge registration is separate. Queued fluids and recipe effects are not inferred.");
  }
  for (const row of observations(result)) {
    const body = row.native.result || {};
    if (body.sourceScope) {
      const scope = body.sourceScope, deferred = scope.deferredLoaders || [], files = scope.deferredSourceFiles || [];
      rows.push(`\n${row.side} source scope`, scope.initializationStage
        ? `Selected native initialization stage: ${scope.initializationStage} (see execution checkpoint)`
        : `Selected native loader: ${scope.selectedLoader} (see execution checkpoint)`,
        deferred.length ? `Deferred loaders: ${deferred.join(", ")}; their recipe effects are not checked` : "Deferred loaders: none declared",
        `Deferred source files: ${files.length}${files.length ? ". Available for imports is not executed." : ""}`);
    }
    rows.push(`\n${row.side} expectations`);
    for (const check of body.expectations?.checks || []) rows.push(`${check.id}: ${check.status} · ${check.material}\n  expected ${JSON.stringify(check.expected)}; observed ${Object.hasOwn(check, "observed") ? JSON.stringify(check.observed) : "not observed"}${check.reason ? `\n  ${check.reason}` : ""}`);
    rows.push(`\nContext: ${body.context?.id || "unavailable"}`, `Excluded composition: ${JSON.stringify(body.context?.excludedComposition || [])}`);
    const linkage = body.bootstrap?.compilerLinkage;
    if (linkage) {
      rows.push(`\nNative compiler linkage: ${linkage.status} (not behavioral qualification)`);
      for (const [index, missing] of (linkage.missing || []).entries()) rows.push(
        `Missing ${missing.kind}: ${missing.target}${missing.required ? ` · requires ${missing.required}` : ""}\n  ${missing.reason}\n  Evidence: /bootstrap/compilerLinkage/missing/${index}`);
    }
    const platform = body.bootstrap?.platformInitialization;
    if (platform?.status === "threw") {
      rows.push("\nNative platform initialization failed; the material context is incomplete, not evidence of an invalid developer edit.");
      for (const [index, cause] of (platform.causes || []).entries()) rows.push(
        `${cause.type}: ${cause.message || ""}\n  Evidence: /bootstrap/platformInitialization/causes/${index}`);
    }
    const observer = body.transformationObservation;
    if (observer?.status === "unavailable") {
      rows.push("\nTransformation observation unavailable; this is not an empty transformer audit.");
      for (const [index, cause] of (observer.causes || []).entries()) rows.push(
        `${cause.type}: ${cause.message || ""}\n  Evidence: /transformationObservation/causes/${index}`);
    }
    const lifecycle = body.execution?.lifecycle;
    if (lifecycle) {
      rows.push(`\nLifecycle (${lifecycle.scope})`);
      for (const point of lifecycle.checkpoints || []) rows.push(`${point.checkpoint}: ${point.phase} · owner ${point.activeOwner || "none"}`);
    }
    const progress = body.execution?.contentProgress;
    if (progress) {
      rows.push(`\nNative content: ${progress.phase}; last completed phase: ${progress.lastCompletedPhase}`);
      for (const point of progress.completedCheckpoints || []) rows.push(`Completed ${point.phase}: ${contentCounts(point)}`);
      if (progress.failedPhase) rows.push(`Stopped during ${progress.failedPhase}: ${progress.failure?.type || "unknown failure"} · ${progress.failure?.message || ""}`);
      if (progress.interruptedState) rows.push(`Interrupted state (partial inventory): ${contentCounts(progress.interruptedState)}`);
    }
    const work = body.execution?.deferredWork;
    if (work) {
      rows.push(`\nDeferred native work · ${work.phase}`,
        `Fluid registration executed: ${JSON.stringify(work.fluidRegistrationExecuted)}; recipe handlers executed: ${JSON.stringify(work.recipeHandlersExecuted)}`);
      for (const fluid of work.fluids || []) rows.push(fluid.hasFluidProperty
        ? `${fluid.material}: queued ${(fluid.queued || []).map(value => value.key).join(", ") || "none"}; stored ${(fluid.stored || []).map(value => `${value.key}=${value.fluid}`).join(", ") || "none"}`
        : `${fluid.material}: no native fluid property`);
      const pending = Array.isArray(work.prefixProcessing) ? work.prefixProcessing.reduce((sum, prefix) => sum + prefix.pendingMaterials.length, 0) : "not observed";
      rows.push(`Pending prefix/material memberships: ${pending} (${work.prefixScope})`,
        "Queue membership is not processing order or proof that handlers ran.");
    }
    if (body.execution?.coverageGaps?.length) rows.push(`Coverage gaps: ${JSON.stringify(body.execution.coverageGaps)}`);
    for (const [index, diagnostic] of (body.execution?.diagnostics || []).entries()) {
      if (typeof diagnostic.trace === "string" && diagnostic.trace) rows.push(
        `\n${row.side} native log: ${diagnostic.logger} · ${diagnostic.severity} · ${diagnostic.message}`,
        "Original native trace; no verified source location:", diagnostic.trace,
        `  Evidence: ${row.pointer}/execution/diagnostics/${index}/trace`);
      if (!diagnostic.causality) continue;
      const pointer = `${row.pointer}/execution/diagnostics/${index}`, graph = diagnostic.causality;
      rows.push(`\n${row.side} native diagnostic: ${diagnostic.message}`,
        "Exception relationships are native evidence, not inferred source blame.");
      for (const [id, exception] of (graph.exceptions || []).entries()) {
        rows.push(`Exception ${id}${graph.root === id ? " (reported throwable)" : ""}: ${exception.type}: ${exception.message ?? "(no message)"}`,
          `  Cause: ${exception.cause ?? "none"}; suppressed: ${(exception.suppressed || []).join(", ") || "none"}`,
          `  Evidence: ${pointer}/causality/exceptions/${id}`);
        for (const [frame, location] of (exception.locations || []).entries()) rows.push(
          `  Exception frame: ${location.path}:${location.line} · ${location.class}#${location.method}\n  Evidence: ${pointer}/causality/exceptions/${id}/locations/${frame}`);
      }
      for (const [frame, location] of (diagnostic.observationLocations || []).entries()) rows.push(
        `Observation site (not exception origin): ${location.path}:${location.line}\n  Evidence: ${pointer}/observationLocations/${frame}`);
      for (const [finding, compiler] of (diagnostic.compilerFindings || []).entries()) rows.push(
        `Compiler finding for exception ${compiler.exceptionIndex}: ${compiler.message}\n  Evidence: ${pointer}/compilerFindings/${finding}`);
    }
  }
  for (const finding of result.findings || []) {
    rows.push(`\n${finding.side} · ${finding.severity || finding.channel} · ${findingLabel(result, finding)}\n  ${finding.location ? `${finding.location.path}:${finding.location.start.line} (${finding.location_precision})` : "No verified source location"}\n  Native evidence: ${finding.pointer}`);
    for (const relation of finding.sourceRelationships || []) rows.push(
      `  Source relationship: ${relation.kind}${relation.exceptionIndex === undefined ? "" : ` · exception ${relation.exceptionIndex}`}\n  Evidence: ${relation.locationPointer}`);
  }
  return rows.join("\n");
}
function contentCounts(point) {
  const fields = { variants: "item variants", registeredBlocks: "registered material blocks", registeredBlockItems: "registered material block items", registeredOreBlocks: "registered ore blocks", registeredOreItems: "registered ore items" };
  const values = Object.entries(fields).filter(([key]) => Object.hasOwn(point, key)).map(([key, label]) => `${label} ${point[key]}`);
  return values.join("; ") || "counts not observed";
}
function materialFindingMessage(finding) {
  return [finding.message, ...(finding.sourceRelationships || [])
    .filter(relation => relation.kind === "exception-frame")
    .map(relation => `Native exception at this source: ${relation.type}: ${relation.message ?? "(no message)"}`)].join("\n");
}
function findingLabel(result, finding) {
  return result.presentation?.finding_labels?.[finding.id] || materialFindingMessage(finding);
}
function retainedMaterialSource(view, result, finding) {
  if (view.format !== "workbench-material-source-view-v1" || view.read_only !== true || view.result_id !== result.id || view.attempt_id !== result.attempt_id
      || JSON.stringify(view.source) !== JSON.stringify(finding) || crypto.createHash("sha256").update(view.text, "utf8").digest("hex") !== finding.location.sha256) throw new Error("Retained source differs from the selected material diagnostic.");
  return view.text;
}
module.exports = { REQUEST, RESULT, materialArguments, validateMaterial, invokeMaterialCheck, materialSummary, materialReport, materialFindingMessage, findingLabel, retainedMaterialSource };
