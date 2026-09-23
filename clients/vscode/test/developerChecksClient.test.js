"use strict";
const { recipeExplanation, recipeExplanationText, recipeAssertionFindings } = require("../developerChecksClient");
const test = require("node:test");
const assert = require("node:assert/strict");
const { checkArguments, validateCheck, progressMessage, monitorCheck, provenanceSummary, comparisonSummary } = require("../developerChecksClient");
const session = "work-session-v2-" + "a".repeat(32);
const attempt = "check-" + "b".repeat(32);
test("recipe selection requires exact source and reference identities without execution consent", () => {
  const recipe = "saved-recipe:sha256:" + "e".repeat(64), image = "runtime-image:sha256:" + "f".repeat(64);
  const args = checkArguments(session, "prepare", image, { recipe, reference: attempt, absent: true });
  assert.deepEqual(args.slice(-5), ["--recipe", recipe, "--recipe-reference", attempt, "--absent"]);
  assert.ok(!args.includes("--confirm"));
  assert.ok(checkArguments(session, "prepare", image, { recipe, trace: false }).includes("--no-trace"));
  assert.deepEqual(checkArguments(session, "source", attempt, "lifecycle-e10:0").slice(-3), [attempt, "--source", "lifecycle-e10:0"]);
  assert.deepEqual(checkArguments(session, "source", attempt, "decision-q0:0").slice(-3), [attempt, "--source", "decision-q0:0"]);
  assert.throws(() => checkArguments(session, "source", attempt, "../secret"), /source/);
  assert.deepEqual(checkArguments(session, "recipes", { path: "groovy/postInit/Some File.groovy", reference: attempt }).slice(-3), ["--path=groovy/postInit/Some File.groovy", "--reference", attempt]);
  assert.throws(() => checkArguments(session, "prepare", image, { recipe, absent: true }), /retained/);
  assert.throws(() => checkArguments(session, "recipes", { path: "groovy/postInit/../../secret.groovy" }), /source path/);
  assert.throws(() => checkArguments(session, "prepare", image, { recipe: "latest" }), /exact saved recipe/);
});
test("comparison requires two exact runs without granting runtime consent", () => {
  const reference = "check-" + "d".repeat(32);
  assert.deepEqual(checkArguments(session, "compare", attempt, reference).slice(-4), ["compare", attempt, "--reference", reference]);
  assert.throws(() => checkArguments(session, "compare", attempt, attempt), /distinct/);
  assert.throws(() => checkArguments(session, "compare", attempt, "latest"), /exact/);
  assert.ok(!checkArguments(session, "compare", attempt, reference).includes("--confirm"));
  assert.equal(checkArguments(session, "history").at(-1), "history");
});
test("provenance and comparison summaries preserve uncertainty and original failures", () => {
  const value = { candidate: { source: { revision: "exact", dirty: true } }, provenance: {
    runtime: { pack: { name: "Supersymmetry", version: "0.1.16.15" }, platform: { id: "cleanroom", version: "0.6.12-alpha", java: { JAVA_RUNTIME_VERSION: "25.0.4+7" } } },
    image: { id: "exact-image" }, source_labels: { local_tags: ["latest"], release_verified: false },
  } };
  assert.match(provenanceSummary(value), /saved working-tree changes/);
  assert.match(provenanceSummary(value), /release\/latest not verified/);
  assert.match(provenanceSummary(value), /25\.0\.4\+7/);
  const comparison = { state: "partial", reference: { state: "inconclusive" }, candidate: { state: "failed" }, counts: { "newly-observed": 1 }, reasons: [] };
  assert.match(comparisonSummary(comparison), /inconclusive → candidate failed/);
  assert.match(comparisonSummary(comparison), /outcomes are unchanged/);
});
test("comparison response cannot promote outcomes or reuse historical result formats", () => {
  const value = { format: "workbench-developer-action-v1", exit_code: 0, context: { selection: { pack_uri: "file:///pack" } }, result: { format: "workbench-check-comparison-v2", authority: { runtime_launched: false, source_mutated: false, qualification_granted: false, outcomes_promoted: false } } };
  assert.equal(validateCheck(value, "file:///pack"), value.result);
  value.result.authority.outcomes_promoted = true;
  assert.throws(() => validateCheck(value, "file:///pack"), /authority/);
  value.result.format = "workbench-saved-check-result-v2";
  assert.throws(() => validateCheck(value, "file:///pack"), /historical/);
});
test("progress is tied to one request and arbitrary retained report paths are safe", () => {
  const result = { format: "workbench-check-live-status-v1", attempt_id: attempt, request_id: "request", state: "running", progress: { attempt_id: attempt, request_id: "request", observation: { format: "workbench-check-observation-v1", summary: "Compiling saved source" } } };
  assert.match(progressMessage(result, attempt, "request"), /Compiling saved source/);
  assert.throws(() => progressMessage(result, attempt, "other"), /another check/);
  assert.equal(checkArguments(session, "progress", attempt).at(-2), "progress");
  assert.equal(checkArguments(session, "log", attempt, "crash-reports/crash-client.txt").at(-1), "crash-reports/crash-client.txt");
  assert.throws(() => checkArguments(session, "log", attempt, "crash-reports/../secret"), /retained log/);
});
test("progress errors neither cancel execution nor outlive its result", async () => {
  let complete, polls = 0;
  const messages = [];
  const execution = new Promise((resolve) => { complete = resolve; });
  const running = monitorCheck(() => execution, async () => { polls += 1; throw new Error("temporarily unavailable"); }, (message) => { messages.push(message); complete("retained result"); }, attempt, "request", 1);
  assert.equal(await running, "retained result");
  assert.equal(polls, 1);
  assert.match(messages[0], /execution continues/);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(polls, 1);
});
test("environment preparation requires distinct exact consent and preserves path tokens", () => {
  const environment = "environment-" + "d".repeat(32);
  const request = "environment-request:sha256:" + "e".repeat(64);
  assert.throws(() => checkArguments(session, "prepare-environment", environment), /Confirm/);
  assert.throws(() => checkArguments(session, "prepare-environment", environment, "saved-check-request:sha256:" + "e".repeat(64)), /Confirm/);
  assert.deepEqual(checkArguments(session, "prepare-environment", environment, request).slice(-3), [environment, "--confirm", request]);
  const args = checkArguments(session, "plan-environment", { prism: "/tool path/PrismLauncher", packwiz: "/tools/packwiz", java: "/jdk/bin/java", accounts: "/private/accounts.json", seeds: ["/seed path/mods"] });
  assert.ok(args.includes("--prism=/tool path/PrismLauncher"));
  assert.ok(args.includes("--seed=/seed path/mods"));
  assert.deepEqual(checkArguments(session, "environment-cancel", environment).slice(-2), ["environment-cancel", environment]);
});
test("check execution requires exact explicit consent, cancellation is separate", () => {
  assert.throws(() => checkArguments(session, "execute", attempt), /consent/);
  const id = "saved-check-request:sha256:" + "c".repeat(64);
  assert.deepEqual(checkArguments(session, "execute", attempt, id), ["context", "run", session, "--", "checks", "execute", attempt, "--confirm", id]);
  assert.deepEqual(checkArguments(session, "cancel", attempt).slice(-3), ["checks", "cancel", attempt]);
  assert.throws(() => checkArguments(session, "show", "../escape"), /exact/);
});
test("check result rejects workspace retargeting and source mutation claims", () => {
  const value = { format: "workbench-developer-action-v1", exit_code: 0, context: { selection: { pack_uri: "file:///pack" } }, result: { format: "workbench-saved-check-result-v4", workspace_uri: "file:///pack", authority: { source_mutated: false, construction_authorized: false, qualification_granted: false } } };
  assert.equal(validateCheck(value, "file:///pack"), value.result);
  assert.throws(() => validateCheck(value, "file:///other"), /workspace/);
  value.result.authority.source_mutated = true;
  assert.throws(() => validateCheck(value, "file:///pack"), /authority/);
});
test("recipe explanations retain owner text and never promote inconclusive startup", () => {
  const report = { format: "workbench-check-explanation-v1", text: "Duration (ticks): expected 200; observed 240", limitations: ["Not source-causation proof."], sections: [{ id: "lookup-q0", text: "Observed winner r0", properties: [{ label: "Duration (ticks)", expected: "200", observed: "240" }] }] };
  const result = { candidate_id: "current", state: "inconclusive", assertions: { state: "mismatched", reasons: [], expectation: { source: { candidate_id: "current" }, subject: { location: { path: "Probe.groovy" } } }, observation: { details: { explanation: report } } } };
  assert.equal(recipeExplanation(result), report);
  assert.match(recipeExplanationText(result), /Startup: inconclusive/);
  assert.match(recipeExplanationText(result), /expected 200; observed 240/);
  assert.match(recipeExplanationText(result, report.sections[0]), /Not source-causation proof/);
  assert.equal(recipeAssertionFindings(result).length, 1);
  result.assertions.expectation.source.candidate_id = "retained-old-source";
  assert.deepEqual(recipeAssertionFindings(result), []);
  result.assertions.expectation.source.candidate_id = "current";
  result.assertions.state = "inconclusive";
  assert.deepEqual(recipeAssertionFindings(result), []);
  report.format = "future";
  assert.throws(() => recipeExplanation(result), /Unsupported/);
});

test("actual lookup explanations stay owner-produced and do not certify machine execution", () => {
  const text = "Original findRecipe invocations: 1; selected recipe: o0. Accepting recipe o1 was not evaluated. Not machine execution validation.";
  const section = { id: "decision-q0", text, properties: [], sources: [] };
  const report = { format: "workbench-check-explanation-v1", text, sections: [section], limitations: ["Scenario-bound lookup only."] };
  const result = { state: "inconclusive", assertions: { state: "matched", reasons: [], observation: { details: { explanation: report } } } };
  assert.equal(recipeExplanation(result), report);
  assert.ok(recipeExplanationText(result, section).includes(text));
  assert.match(recipeExplanationText(result, section), /Startup: inconclusive/);
  assert.equal(result.state, "inconclusive");
});

test("older assertions are not silently reinterpreted into new explanations", () => {
  const result = { state: "failed", assertions: { state: "inconclusive", reasons: [], observation: { details: {} } } };
  assert.equal(recipeExplanation(result), null);
  assert.match(recipeExplanationText(result), /fresh check is required/);
});
