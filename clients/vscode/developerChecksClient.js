"use strict";
const fs = require("node:fs");
const { pathToFileURL } = require("node:url");
const { invokeCoreJson } = require("./coreCommandClient");
const { resolveCoreLaunch } = require("./coreLaunch");

function checkArguments(session, action, value, confirmation) {
  if (!/^work-session-v2-[0-9a-f]{32}$/.test(session || "")) throw new Error("Select an exact Work Session.");
  if (!["images", "catalog", "recipes", "history", "compare", "prepare", "execute", "show", "source", "progress", "cancel", "log", "import-image", "recover", "plan-environment", "prepare-environment", "environment-show", "environment-cancel", "environment-recover"].includes(action)) throw new Error("Unknown check action.");
  const args = ["context", "run", session, "--", "checks", action];
  if (action === "plan-environment") {
    for (const name of ["prism", "packwiz", "java", "accounts"]) {
      if (typeof value?.[name] !== "string" || !value[name].startsWith("/") || /[\0\r\n]/.test(value[name])) throw new Error("Select exact native tools and one Prism account store.");
      args.push(`--${name}=${value[name]}`);
    }
    for (const seed of value.seeds || []) args.push(`--seed=${seed}`);
  } else if (action.includes("environment")) {
    if (!/^environment-[0-9a-f]{32}$/.test(value || "")) throw new Error("Select an exact environment attempt.");
    args.push(value);
    if (["prepare-environment", "environment-recover"].includes(action)) {
      if (!/^environment-request:sha256:[0-9a-f]{64}$/.test(confirmation || "")) throw new Error("Confirm the exact environment plan.");
      args.push("--confirm", confirmation);
    }
  }
  if (action === "prepare") {
    if (!/^runtime-image:sha256:[0-9a-f]{64}$/.test(value || "")) throw new Error("Select an exact runtime image.");
    args.push("--image", value);
    if (confirmation) args.push(...recipeArguments(confirmation, false));
  } else if (action === "recipes") {
    args.push(...recipeArguments(value || {}, true));
  } else if (["execute", "show", "source", "progress", "cancel", "log", "recover", "compare"].includes(action)) {
    if (!/^check-[0-9a-f]{32}$/.test(value || "")) throw new Error("Select an exact check attempt.");
    args.push(value);
  }
  if (["execute", "recover"].includes(action)) {
    if (!/^saved-check-request:sha256:[0-9a-f]{64}$/.test(confirmation || "")) throw new Error("Execution requires exact request consent.");
    args.push("--confirm", confirmation);
  } else if (action === "compare") {
    if (!/^check-[0-9a-f]{32}$/.test(confirmation || "") || confirmation === value) throw new Error("Select a distinct exact reference run.");
    args.push("--reference", confirmation);
  } else if (action === "source") {
    if (typeof confirmation !== "string" || !/^[a-z0-9-]{1,64}:[0-9]{1,2}$/.test(confirmation)) throw new Error("Select one retained explanation source.");
    args.push("--source", confirmation);
  } else if (action === "log") {
    if (typeof confirmation !== "string" || !/^(?:[a-zA-Z0-9._-]+\/)+[a-zA-Z0-9._-]+$/.test(confirmation) || confirmation.split("/").some((part) => [".", ".."].includes(part))) throw new Error("Select one retained log.");
    args.push("--path", confirmation);
  } else if (action === "import-image") {
    if (!value || typeof value.root !== "string" || typeof value.java !== "string" || typeof value.arguments !== "string") throw new Error("Select a runtime directory, native Java and exact arguments.");
    args.push(`--runtime-root=${value.root}`, `--java=${value.java}`, `--arguments-json=${value.arguments}`);
  }
  return args;
}
function validateCheck(value, root) {
  if (value?.format !== "workbench-developer-action-v1" || value.exit_code !== 0 || value.context?.selection?.pack_uri !== root || !value.result) throw new Error("Check response changed the selected workspace.");
  const result = value.result;
  if (result.workspace_uri && result.workspace_uri !== root) throw new Error("Check belongs to another workspace.");
  if (result.format?.startsWith("workbench-saved-check-result-") && result.format !== "workbench-saved-check-result-v4") throw new Error("Prepare a new check with current provenance; historical formats are not migrated.");
  if (result.format === "workbench-saved-check-result-v4" && (result.authority?.source_mutated !== false || result.authority?.construction_authorized !== false || result.authority?.qualification_granted !== false)) throw new Error("Check result claims unexpected authority.");
  if (result.format === "workbench-saved-check-result-v4" && result.assertions && (result.assertions.authority?.source_mutated !== false || result.assertions.authority?.outcomes_promoted !== false || result.assertions.authority?.qualification_granted !== false)) throw new Error("Recipe assertion claims unexpected authority.");
  if (result.format === "workbench-check-comparison-v2" && (result.authority?.runtime_launched !== false || result.authority?.source_mutated !== false || result.authority?.qualification_granted !== false || result.authority?.outcomes_promoted !== false)) throw new Error("Comparison claims unexpected authority.");
  if (["workbench-environment-request-v1", "workbench-environment-result-v1"].includes(result.format) && (result.authority?.runtime_launched !== false || result.authority?.source_mutated !== false || result.authority?.qualification_granted !== false)) throw new Error("Preparation claims unexpected runtime authority.");
  return result;
}
async function invokeCheck(executable, root, session, action, value, confirmation) {
  const launch = resolveCoreLaunch(executable);
  if (launch.host !== "native") throw new Error("Saved checks require a native Linux host.");
  // Execution cancellation uses the owner's explicit cancellation request. Do
  // not abort this transport and strand an independently supervised game.
  const envelope = await invokeCoreJson(executable, checkArguments(session, action, value, confirmation), {
    cwd: root, launch, maximumOutput: 32 * 1024 * 1024, timeoutMs: ["execute", "prepare-environment"].includes(action) ? 3700000 : action === "progress" ? 5000 : 300000,
  });
  const result = validateCheck(envelope, pathToFileURL(fs.realpathSync(root)).href);
  // Presentation freshness is not part of the sealed historical evidence.
  Object.defineProperty(result, "sourceCurrent", { value: envelope.presentation?.source_current === true });
  Object.defineProperty(result, "presentation", { value: envelope.presentation || {}, enumerable: true });
  return result;
}
function progressMessage(result, attempt, request) {
  if (result?.format !== "workbench-check-live-status-v1" || result.attempt_id !== attempt || result.request_id !== request) throw new Error("Progress belongs to another check.");
  if (!result.progress) return `${result.state} · waiting for startup observations`;
  const progress = result.progress;
  if (progress.attempt_id !== attempt || progress.request_id !== request || progress.observation?.format !== "workbench-check-observation-v1") throw new Error("Progress observation changed its request.");
  return `${progress.observation.summary} · ${result.state}`;
}
async function monitorCheck(execute, poll, report, attempt, request, interval = 2000) {
  let finished = false, polling = false;
  const timer = setInterval(async () => {
    if (polling || finished) return;
    polling = true;
    try { const value = await poll(); if (!finished) report(progressMessage(value, attempt, request)); }
    catch (_) { if (!finished) report("Progress unavailable; execution continues. Reopen the retained attempt if needed."); }
    finally { polling = false; }
  }, interval);
  try { return await execute(); }
  finally { finished = true; clearInterval(timer); }
}
function provenanceSummary(value) {
  const provenance = value.provenance;
  if (!provenance) return "Environment provenance unavailable";
  const runtime = provenance.runtime, source = value.source || value.candidate?.source || value.candidate;
  return `${runtime.pack.name} ${runtime.pack.version} · ${source?.revision || "unknown revision"}${source?.dirty ? " · saved working-tree changes" : ""}\n${runtime.platform.id} ${runtime.platform.version} · Java ${runtime.platform.java?.JAVA_RUNTIME_VERSION || "unknown"}\nImage ${provenance.image.id}\nLocal tags: ${(provenance.source_labels.local_tags || []).join(", ") || "none"}; upstream release/latest not verified`;
}
function comparisonSummary(result) {
  return `Comparison ${result.state} · reference ${result.reference.state} → candidate ${result.candidate.state}\n${Object.entries(result.counts).map(([key, count]) => `${key}: ${count}`).join(" · ")}\n${result.reasons.join("; ")}${result.assertions ? `\nRecipe observations: ${result.assertions.state}` : ""}\nObserved differences only; original outcomes are unchanged.`;
}
function recipeArguments(value, catalog) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Select exact recipe options.");
  const args = [];
  if (catalog && value.path) {
    if (typeof value.path !== "string" || !/^groovy\/postInit\/.+\.groovy$/.test(value.path) || /[\\\0\r\n]/.test(value.path) || value.path.split("/").some(part => [".", "..", ""].includes(part))) throw new Error("Select an exact postInit source path.");
    args.push(`--path=${value.path}`);
  }
  if (!catalog) {
    if (!/^saved-recipe:sha256:[0-9a-f]{64}$/.test(value.recipe || "")) throw new Error("Select one exact saved recipe.");
    args.push("--recipe", value.recipe);
    if (value.trace === false) args.push("--no-trace");
  }
  if (value.reference) {
    if (!/^check-[0-9a-f]{32}$/.test(value.reference)) throw new Error("Select one exact reference attempt.");
    args.push(catalog ? "--reference" : "--recipe-reference", value.reference);
  }
  if (!catalog && value.absent) {
    if (!value.reference) throw new Error("Absence requires retained source.");
    args.push("--absent");
  }
  return args;
}
function expectationSummary(value) {
  const expectation = value.expectation || value.assertions?.expectation;
  if (!expectation) return "Startup diagnostics only; no recipe expectation selected.";
  const recipe = expectation.subject.recipe, location = expectation.subject.location;
  return `Recipe expectation: ${expectation.mode} · ${expectation.support}\n${location.path}:${location.start.line}\n${recipe ? `${recipe.map} · duration ${recipe.duration} ticks · EU/t ${recipe.eut}\n${JSON.stringify(recipe)}` : expectation.reasons.join("; ")}\nExact registration and bounded lookup only; not gameplay or source-causation proof.`;
}
function recipeExplanation(result) {
  const report = result.assertions?.observation?.details?.explanation;
  if (!report) return null; // Retained evidence without an explanation is never upgraded here.
  if (report.format !== "workbench-check-explanation-v1" || !Array.isArray(report.sections) || typeof report.text !== "string") throw new Error("Unsupported retained recipe explanation.");
  return report;
}
function recipeExplanationText(result, section) {
  const report = recipeExplanation(result);
  return `Recipe assertion: ${result.assertions.state}\nStartup: ${result.state}\n${(result.assertions.reasons || []).join("\n")}\n\n${section ? section.text + "\n\nLimits\n" + report.limitations.join("\n") : report?.text || "No explanation was retained. Inspect the original assertion evidence; a fresh check is required for current diagnostics."}`;
}
function recipeAssertionFindings(result) {
  const assertion = result.assertions, report = recipeExplanation(result);
  if (!report || assertion.state !== "mismatched" || assertion.expectation.source.candidate_id !== result.candidate_id) return [];
  const differences = report.sections.find(section => section.id.startsWith("lookup-") && section.properties.length)?.properties || [];
  const message = ["Selected recipe expectation was not met in the captured runtime.", ...differences.slice(0, 4).map(row => `${row.label}: expected ${row.expected}; observed ${row.observed}`), "Open the recipe explanation for bounded lookup evidence; this is not a source-causation claim."].join("\n");
  return [{ code: "recipe-expectation-mismatch", severity: "warning", message, location: assertion.expectation.subject.location }];
}
module.exports = { checkArguments, validateCheck, invokeCheck, progressMessage, monitorCheck, provenanceSummary, comparisonSummary, recipeArguments, expectationSummary, recipeExplanation, recipeExplanationText, recipeAssertionFindings };
