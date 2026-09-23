"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), crypto = require("node:crypto");
const { materialArguments, validateMaterial, materialSummary, materialReport, materialFindingMessage, retainedMaterialSource, REQUEST, RESULT } = require("../materialChecksClient");
const { result, session, attempt, request } = require("./materialCheckFixture");
test("retention transport carries exact proposal consent without shell interpretation", () => {
  const id = "check-retention-proposal:sha256:" + "a".repeat(64);
  const settings = { mode: "finite", known_stores: ["/history with spaces"] };
  const args = materialArguments(session, "retention", { operation: "configure", settings }, id);
  assert.deepEqual(args.slice(-4), ["--settings", JSON.stringify(settings), "--confirm", id]);
  assert.throws(() => materialArguments(session, "retention", { operation: "purge-all" }), /operation/);
  assert.throws(() => materialArguments(session, "retention", { operation: "configure", settings }, "yes"), /exact/);
});
test("saved source removals and deferred recipe scope are distinct from native comparison", () => {
  const value = result(), native = structuredClone(value.native);
  native.result.sourceScope = { selectedLoader: "preInit", deferredLoaders: ["postInit"], deferredSourceFiles: ["groovy/postInit/Recipe.groovy"] };
  value.native = { result: { baseline: native, candidate: structuredClone(native), comparison: { status: "not-comparable" },
    sourceComparison: { status: "changed", added: [], modified: [], removed: ["groovy/postInit/Recipe.groovy"] } } };
  const before = JSON.stringify(value), report = materialReport(value);
  assert.match(report, /Saved file removed: groovy\/postInit\/Recipe.groovy/);
  assert.match(report, /Saved file comparison: changed \(not recipe validity\)/);
  assert.match(report, /Deferred loaders: postInit; their recipe effects are not checked/);
  assert.match(report, /Native observation comparison: not-comparable/);
  assert.equal(JSON.stringify(value), before);
});
test("native exception relationships retain messages, exact side pointers and distinct observation sites", () => {
  const value = result(), native = structuredClone(value.native);
  const location = { path: "groovy/classes/Helper.groovy", line: 8, class: "classes.Helper", method: "fail" };
  native.result.execution = { diagnostics: [{ message: "native wrapper", causality: { root: 0, exceptions: [
    { type: "Wrapper", message: "outer", cause: 1, suppressed: [1], locations: [] },
    { type: "NativeFailure", message: null, cause: 0, suppressed: [], locations: [location] }
  ] }, observationLocations: [location], compilerFindings: [{ exceptionIndex: 1, message: "native compiler detail" }] }] };
  value.native = { result: { baseline: native, candidate: structuredClone(native) } };
  value.findings = [{ side: "candidate", message: "native wrapper", pointer: "/result/candidate/result/execution/diagnostics/0",
    sourceRelationships: [{ kind: "exception-frame", exceptionIndex: 1, locationPointer: "/result/candidate/result/execution/diagnostics/0/causality/exceptions/1/locations/0" }] }];
  const before = JSON.stringify(value), text = materialReport(value);
  assert.match(text, /Exception 0 \(reported throwable\): Wrapper: outer/);
  assert.match(text, /Exception 1: NativeFailure: \(no message\)/);
  assert.match(text, /Observation site \(not exception origin\): groovy\/classes\/Helper.groovy:8/);
  assert.ok(text.includes("/result/baseline/result/execution/diagnostics/0/causality/exceptions/1/locations/0"));
  assert.ok(text.includes("/result/candidate/result/execution/diagnostics/0/causality/exceptions/1/locations/0"));
  assert.match(text, /Source relationship: exception-frame · exception 1/);
  assert.match(text, /Compiler finding for exception 1: native compiler detail/);
  assert.equal(JSON.stringify(value), before);
});
test("early native logs retain original traces and exact side pointers without source attribution", () => {
  const value = result(), native = structuredClone(value.native);
  const trace = "NativeFailure\n at groovy.material.A.run(A.groovy:8)";
  native.result.execution = { diagnostics: [{ logger: "FML", severity: "warning", message: "native warning", trace, locationStatus: "unlocated" }] };
  value.native = { result: { baseline: native, candidate: structuredClone(native) } };
  const before = structuredClone(value), text = materialReport(value);
  assert.ok(text.includes(trace));
  assert.match(text, /Original native trace; no verified source location:/);
  for (const side of ["baseline", "candidate"]) {
    assert.ok(text.includes(`${side[0].toUpperCase() + side.slice(1)} native log: FML · warning · native warning`));
    assert.ok(text.includes(`/result/${side}/result/execution/diagnostics/0/trace`));
  }
  assert.deepEqual(value, before);
});
test("finding text keeps the wrapper and exceptions with verified source membership", () => {
  const finding = { message: "java.lang.reflect.InvocationTargetException: null", sourceRelationships: [
    { kind: "exception-frame", type: "java.lang.IllegalArgumentException", message: "Harvest Level must be greater than zero!" },
    { kind: "exception-frame", type: "NativeFailure", message: null },
    { kind: "observation-site", message: "not an exception at this source" }
  ] };
  const before = structuredClone(finding), text = materialFindingMessage(finding);
  assert.ok(text.startsWith(finding.message));
  assert.ok(text.includes("Native exception at this source: java.lang.IllegalArgumentException: Harvest Level must be greater than zero!"));
  assert.ok(text.includes("NativeFailure: (no message)"));
  assert.ok(!text.includes("not an exception at this source"));
  assert.equal(materialFindingMessage({ message: "native warning" }), "native warning");
  assert.deepEqual(finding, before);
});
test("material reports keep fluid queues and pending handlers distinct from execution", () => {
  const value = result(); value.native.result.execution = {
    lifecycle: { scope: "native-host-checkpoints-not-per-listener-trace", checkpoints: [{ checkpoint: "after-material-event", phase: "OPEN", activeOwner: "gregtech" }] },
    deferredWork: { phase: "FROZEN", fluidRegistrationExecuted: false, recipeHandlersExecuted: false,
      fluids: [{ material: "supersymmetry:test", hasFluidProperty: true, queued: [{ key: "gregtech:liquid" }], stored: [] }],
      prefixScope: "all-native-prefix-queues", prefixProcessing: [{ prefix: "dust", pendingMaterials: ["supersymmetry:test"] }] }
  };
  const before = JSON.stringify(value), text = materialReport(value);
  assert.match(text, /after-material-event: OPEN · owner gregtech/);
  assert.match(text, /queued gregtech:liquid; stored none/);
  assert.match(text, /recipe handlers executed: false/); assert.match(text, /Pending prefix\/material memberships: 1/);
  assert.equal(JSON.stringify(value), before);
});
test("material inputs select a complete saved program with separate exact consent", () => {
  const options = { engineHome: "/tools/axiom engine", runtimeHome: "/inputs/runtime", java: "/jdk/bin/java", programRoot: "authoring/example", intent: "checks/materials.json", context: "supersymmetry:material-authoring-gt-base", baseline: attempt };
  const args = materialArguments(session, "prepare", options);
  assert.ok(args.includes("materials")); assert.ok(args.includes("--engine-home=/tools/axiom engine")); assert.ok(args.includes("--baseline=" + attempt));
  assert.ok(!args.includes("--confirm")); assert.ok(!args.includes("--image"));
  for (const intent of ["", undefined]) {
    const diagnosticArgs = materialArguments(session, "prepare", { ...options, intent });
    assert.ok(!diagnosticArgs.some(arg => arg.startsWith("--request")));
    assert.ok(diagnosticArgs.includes("--baseline=" + attempt));
  }
  for (const intent of [null, 5, "../intent.json", "/intent.json"]) {
    assert.throws(() => materialArguments(session, "prepare", { ...options, intent }));
  }
  assert.deepEqual(materialArguments(session, "execute", attempt, request).slice(-3), [attempt, "--confirm", request]);
  assert.throws(() => materialArguments(session, "execute", attempt, "yes"));
  assert.throws(() => materialArguments(session, "prepare", { ...options, programRoot: "../escape" }));
  assert.throws(() => materialArguments(session, "prepare", { ...options, baseline: "latest" }));
  assert.ok(materialArguments(session, "cancel", attempt).includes(attempt));
  assert.throws(() => materialArguments(session, "source", attempt, "../../file"));
});
test("unvisited native queues are not reported as empty", () => {
  const value = result(); value.native.result.execution = {
    contentProgress: { phase: "NOT_STARTED", lastCompletedPhase: "NONE", completedCheckpoints: [] },
    deferredWork: { phase: "CLOSED", prefixScope: "not-observed-before-content-construction-checkpoint" }
  };
  const text = materialReport(value);
  assert.match(text, /Native content: NOT_STARTED; last completed phase: NONE/);
  assert.match(text, /Pending prefix\/material memberships: not observed/);
  value.native.result.execution.deferredWork.prefixProcessing = [];
  assert.match(materialReport(value), /Pending prefix\/material memberships: 0/);
});
test("Core setup arguments allow resolved reruns but refuse partial path overrides", () => {
  const context = "supersymmetry:material-authoring-pack";
  assert.deepEqual(materialArguments(session, "prepare", { context }).slice(-2), ["prepare", "--context=" + context]);
  assert.deepEqual(materialArguments(session, "setup-status", { context }).slice(-2), ["setup-status", "--context=" + context]);
  assert.throws(() => materialArguments(session, "prepare", { context, java: "/jdk/bin/java" }));
  assert.throws(() => materialArguments(session, "setup", { context }));
  assert.throws(() => materialArguments(session, "setup-status", { context, engineHome: "/engine" }));
  const args = materialArguments(session, "setup", { context, engineHome: "/engine", runtimeHome: "/runtime", java: "/jdk/bin/java", programRoot: "." });
  assert.ok(args.includes("--program-root=.")); assert.ok(!args.some(arg => arg.startsWith("--request")));
});
test("setup readiness remains context-bound and distinct from native execution validity", () => {
  const context = "supersymmetry:material-authoring-pack", value = { format: "workbench-material-check-setup-status-v1", context_id: context,
    state: "ready", readiness_scope: "saved-input-and-profile-bindings-only" };
  const envelope = { format: "workbench-developer-action-v1", exit_code: 0, context: { selection: { pack_uri: "file:///pack" } }, result: value };
  assert.equal(validateMaterial(envelope, "file:///pack", "setup-status", { context }), value);
  assert.throws(() => validateMaterial(envelope, "file:///pack", "setup-status", { context: "different:context" }));
  value.state = "valid"; assert.throws(() => validateMaterial(envelope, "file:///pack", "setup-status", { context }));
  value.state = "ready"; value.readiness_scope = "qualified-pack";
  assert.throws(() => validateMaterial(envelope, "file:///pack", "setup-status", { context }));
});
test("stopped native content keeps completed checkpoints and partial counts separate", () => {
  const value = result(); value.native.result.execution = { contentProgress: {
    phase: "FAILED", lastCompletedPhase: "CONSTRUCTED", failedPhase: "BLOCK_REGISTERING",
    completedCheckpoints: [{ phase: "CONSTRUCTED", registeredBlocks: 0 }],
    interruptedState: { registeredBlocks: 12 }, failure: { type: "java.lang.IllegalArgumentException", message: "Harvest Level must be greater than zero!" }
  } };
  const before = JSON.stringify(value), text = materialReport(value);
  assert.match(text, /Native content: FAILED; last completed phase: CONSTRUCTED/);
  assert.match(text, /Completed CONSTRUCTED: registered material blocks 0/);
  assert.match(text, /Stopped during BLOCK_REGISTERING: java.lang.IllegalArgumentException/);
  assert.match(text, /Interrupted state \(partial inventory\): registered material blocks 12/);
  assert.equal(JSON.stringify(value), before);
});
test("material responses preserve authority boundaries, attempt and format identity", () => {
  const value = result(), envelope = { format: "workbench-developer-action-v1", exit_code: 0, context: { selection: { pack_uri: "file:///pack" } }, result: value };
  assert.equal(validateMaterial(envelope, "file:///pack", "show", attempt), value);
  assert.throws(() => validateMaterial(envelope, "file:///other", "show", attempt));
  assert.throws(() => validateMaterial(envelope, "file:///pack", "show", "different"));
  value.authority.validity_qualified = true;
  assert.throws(() => validateMaterial(envelope, "file:///pack", "show", attempt));
  value.authority.validity_qualified = false; value.format = "workbench-material-check-result-v0";
  assert.throws(() => validateMaterial(envelope, "file:///pack", "show", attempt));
});
test("readable evidence distinguishes completed execution from intent mismatch and unqualified validity", () => {
  const value = result(), text = materialReport(value);
  assert.match(text, /Workflow: completed \(not material validity\)/);
  assert.match(text, /native status: rejected/); assert.match(text, /Material execution: completed-without-observed-error/);
  assert.match(text, /Developer intent: mismatch/); assert.match(text, /expected true; observed false/);
  assert.match(text, /Qualification: pending-native-program-acceptance/);
  value.native.result.expectations.checks[0] = { id: "unknown", material: "test", status: "unsupported", expected: false };
  assert.match(materialReport(value), /observed not observed/);
  value.failure = { message: "cancelled" }; value.state = "incomplete";
  assert.match(materialSummary(value), /Invocation incomplete: cancelled/);
});
test("paired native observations remain separately labeled and unchanged", () => {
  const value = result(), native = structuredClone(value.native);
  value.native = { result: { baseline: native, candidate: { status: "incomplete", result: { expectations: { status: "matched" } } }, comparison: { status: "changed" } } };
  const before = JSON.stringify(value);
  const text = materialReport(value);
  assert.match(text, /Baseline native status: rejected/); assert.match(text, /Candidate native status: incomplete/);
  assert.match(text, /Native observation comparison: changed \(not source causation\)/);
  assert.equal(JSON.stringify(value), before);
});
test("assessment exposes known mismatches beside unresolved checks without granting validity", () => {
  const value = result();
  value.native.status = "incomplete";
  value.native.result.assessment = { schema: "axiom.material-program-assessment.v1", execution: "completed", coverage: "no-observed-gaps",
    intentCounts: { matched: 0, mismatch: 1, unsupported: 1, "not-evaluated": 0 }, qualifiedValidity: false,
    reasons: [{ code: "expectation-mismatch", message: "A known mismatch remains visible.", evidencePointer: "/expectations/checks" },
      { code: "qualification-pending", message: "Qualification is pending.", evidencePointer: "/qualification" }] };
  const before = JSON.stringify(value), text = materialReport(value);
  assert.match(text, /native status: incomplete/);
  assert.match(text, /Execution checkpoint: completed; coverage: no-observed-gaps/);
  assert.match(text, /Expectation counts: matched 0; mismatched 1; unsupported 1; not evaluated 0/);
  assert.match(text, /Assessment \[expectation-mismatch\]: A known mismatch remains visible\.\n  Evidence: \/expectations\/checks/);
  assert.match(text, /No qualified material validity/);
  assert.equal(JSON.stringify(value), before);
});
test("retained source requires the exact finding, result, and file digest", () => {
  const value = result(), text = "// 🌍\r\nerror\n";
  const finding = { id: "diagnostic-2", location: { sha256: crypto.createHash("sha256").update(text).digest("hex") } };
  const view = { format: "workbench-material-source-view-v1", result_id: value.id, attempt_id: attempt, source: finding, text, read_only: true };
  assert.equal(retainedMaterialSource(view, value, finding), text);
  assert.throws(() => retainedMaterialSource({ ...view, text: "newer source" }, value, finding));
  assert.throws(() => retainedMaterialSource({ ...view, result_id: "other" }, value, finding));
});
test("compiler linkage refusal names the runtime dependency without blaming saved source", () => {
  const value = result();
  value.native = { status: "incomplete", result: { nativeOutcome: "not-run", bootstrap: {
    compilerLinkage: { status: "incomplete", missing: [{ kind: "mixin", target: "groovy.lang.Closure",
      required: "com.cleanroommc.groovyscript.core.mixin.groovy.ClosureMixin", reason: "not-observed-on-defined-target" }] } } } };
  const before = JSON.stringify(value), text = materialReport(value);
  assert.match(text, /Material execution: not-run/);
  assert.match(text, /Native compiler linkage: incomplete \(not behavioral qualification\)/);
  assert.match(text, /Missing mixin: groovy.lang.Closure · requires com.cleanroommc.groovyscript.core.mixin.groovy.ClosureMixin/);
  assert.match(text, /Evidence: \/bootstrap\/compilerLinkage\/missing\/0/);
  assert.doesNotMatch(text, /source-error/);
  assert.equal(JSON.stringify(value), before);
});
test("comparison reports native outcome transitions and exact changed evidence", () => {
  const value = result(); value.native = { result: {
    baseline: { status: "incomplete", result: { nativeOutcome: "completed-without-observed-error" } },
    candidate: { status: "source-error", result: { nativeOutcome: "source-error" } },
    comparison: { status: "changed", changedSections: [{ field: "nativeOutcome", baselinePointer: "/baseline/result/nativeOutcome", candidatePointer: "/candidate/result/nativeOutcome" }] }
  } };
  const before = JSON.stringify(value), text = materialReport(value);
  assert.match(text, /Native observation comparison: changed \(not source causation\)/);
  assert.match(text, /Material execution outcome: completed-without-observed-error → source-error/);
  assert.match(text, /Changed observation: nativeOutcome/);
  assert.match(text, /Baseline evidence: \/baseline\/result\/nativeOutcome/);
  assert.match(text, /Candidate evidence: \/candidate\/result\/nativeOutcome/);
  assert.equal(JSON.stringify(value), before);
});
test("unavailable comparison shows the reason instead of implying equality", () => {
  const value = result(); value.native = { result: { comparison: { status: "not-comparable", reasons: ["Native execution observation is unavailable"] } } };
  const text = materialReport(value);
  assert.match(text, /Material execution outcome: not observed → not observed/);
  assert.match(text, /Not comparable: Native execution observation is unavailable/);
});
test("early bootstrap native causes remain visible without blaming saved source", () => {
  const value = result();
  value.native.result = {
    initialization: { schema: "axiom.scoped-initialization.v1", scope: "preInit", status: "incomplete", nativeErrorObserved: true },
    bootstrap: { platformInitialization: { status: "threw", causes: [{ type: "java.lang.LinkageError", message: "original native cause" }] } },
    transformationObservation: { status: "unavailable", causes: [{ type: "java.lang.ClassNotFoundException", message: "NativeTransformAudit" }] }
  };
  const before = JSON.stringify(value), report = materialReport(value);
  assert.match(report, /initialization \(preInit\): incomplete/);
  assert.match(report, /Native error observed: yes/);
  assert.match(report, /java.lang.LinkageError: original native cause/);
  assert.match(report, /not evidence of an invalid developer edit/);
  assert.match(report, /Transformation observation unavailable/);
  assert.match(report, /java.lang.ClassNotFoundException: NativeTransformAudit/);
  assert.equal(JSON.stringify(value), before);
});
test("scoped initialization and same-count native effects stay distinct from whole-pack validity", () => {
  const value = result();
  const native = value.native;
  native.result.initialization = { schema: "axiom.scoped-initialization.v1", scope: "preInit", status: "incomplete", nativeErrorObserved: true };
  value.native = { result: { baseline: native, candidate: structuredClone(native), effectComparison: {
    status: "incomplete", domains: {
      materials: { status: "not-comparable", reasons: ["Complete inventory unavailable"] },
      customItems: { status: "changed", membership: "native-custom-meta-item-variant-definition", added: [], removed: [],
        modified: [{ identity: "susy:items#4", baselinePointer: "/baseline/result/execution/customMetaItems/items/0/variants/0", candidatePointer: "/candidate/result/execution/customMetaItems/items/0/variants/0" }] }
    }
  } } };
  const before = JSON.stringify(value), report = materialReport(value);
  assert.match(report, /Candidate initialization \(preInit\): incomplete/);
  assert.match(report, /Native error observed: yes/);
  assert.match(report, /materials: not-comparable/);
  assert.match(report, /modified: susy:items#4/);
  assert.match(report, /owner Forge registration is separate/);
  assert.ok(report.includes("/candidate/result/execution/customMetaItems/items/0/variants/0"));
  assert.equal(JSON.stringify(value), before);
});

test("complete recipe scope retains native pack errors without a source-error headline", () => {
  const value = result();
  value.native.status = "rejected";
  value.native.result.nativeOutcome = "native-failed";
  value.native.result.initialization = { schema: "axiom.scoped-initialization.v1",
    scope: "original-preinit-through-available-recipe-registries", status: "native-failed",
    nativeScopeQualified: true, recipeEffectsChecked: true, nativeErrorObserved: true };
  value.native.result.sourceScope = { selectedLoader: "preInit", initializationStage: "recipes", deferredLoaders: [], deferredSourceFiles: [] };
  const before = JSON.stringify(value), report = materialReport(value);
  assert.match(report, /initialization \(original-preinit-through-available-recipe-registries\): native-failed/);
  assert.match(report, /Native error observed: yes/);
  assert.match(report, /Recipe registry effects checked: yes/);
  assert.match(report, /Selected native initialization stage: recipes/);
  assert.ok(!report.includes("source-error"));
  assert.ok(!report.includes("their recipe effects are not checked"));
  assert.equal(JSON.stringify(value), before);
});
