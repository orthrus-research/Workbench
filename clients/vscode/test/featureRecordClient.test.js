"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const test = require("node:test");

const {
  canonicalJson,
  invokePresentation,
  invokeRecordCatalog,
  invokeTransaction,
  validatePresentation,
  validateRecordCatalog,
  validateTransaction,
} = require("../featureRecordClient");

const PLAN_ID = `workbench-developer-material-fluid-recipe-plan:sha256:${"1".repeat(64)}`;
const RECIPE_PLAN_ID = `workbench-supersymmetry-recipe-change-plan:sha256:${"2".repeat(64)}`;
const RECIPE_RUN_ID = `workbench-developer-recipe-change-runtime-comparison:sha256:${"3".repeat(64)}`;
const PRESENTATION_KIND = "workbench-developer-feature-presentation";
const CATALOG_KIND = "workbench-developer-feature-record-catalog";
const TRANSACTION_KIND = "workbench-developer-feature-transaction-view";

function seal(body, kind) {
  return {
    ...body,
    id: `${kind}:sha256:${crypto.createHash("sha256").update(canonicalJson(body)).digest("hex")}`,
  };
}

function operation() {
  const before = Buffer.from("old line\n", "utf8");
  const after = Buffer.from("new line\n", "utf8");
  return {
    after_base64: after.toString("base64"),
    after_sha256: crypto.createHash("sha256").update(after).digest("hex"),
    after_size: after.byteLength,
    before_base64: before.toString("base64"),
    before_sha256: crypto.createHash("sha256").update(before).digest("hex"),
    before_size: before.byteLength,
    diff: "-old line\n+new line\n",
    operation: "update",
    ordinal: 0,
    outcome: "replace-exact-file",
    path: "groovy/postInit/recipes/Probe.groovy",
    role: "recipe-script",
  };
}

function presentation() {
  return seal({
    actions: [
      { action: "check", available: true, consent_id: null, reason: null },
      { action: "apply", available: true, consent_id: PLAN_ID, reason: null },
      { action: "run", available: true, consent_id: PLAN_ID, reason: null },
      { action: "recover", available: false, consent_id: null, reason: "no interrupted transaction is retained" },
    ],
    authority_boundary: { source: "material-authority" },
    collection: "plans",
    family: "material-fluid-recipe",
    format: "workbench-developer-feature-presentation-v1",
    kind: PRESENTATION_KIND,
    limitations: ["Runtime evidence remains a separate action."],
    operations: [operation()],
    owner_record: {
      diagnostic_code: null,
      id: PLAN_ID,
      kind: "workbench-developer-material-fluid-recipe-plan",
      state: "experimental-ready",
      uri: `file:///state/plans/${"1".repeat(64)}/record.json`,
    },
    plan_id: PLAN_ID,
    request: { name: "Radon" },
    review: { summary: "one source update" },
    runtime: {
      action_available: true,
      outcome: null,
      record_id: null,
      requirement: { mode: "cold-start" },
      state: "available-not-observed",
    },
    schema_version: 1,
    verification: {
      format: "workbench-developer-material-fluid-recipe-plan-verification-v1",
      plan_id: PLAN_ID,
      reason: null,
      schema_version: 1,
      state: "ready",
    },
    workspace_uri: "file:///home/dev/susy",
  }, PRESENTATION_KIND);
}

function recordSummary() {
  return {
    collection: "plans",
    diagnostic_code: null,
    family: "material-fluid-recipe",
    operation_count: 1,
    plan_id: PLAN_ID,
    record_id: PLAN_ID,
    record_kind: "workbench-developer-material-fluid-recipe-plan",
    record_state: "experimental-ready",
    reference: PLAN_ID,
    uri: `file:///state/plans/${"1".repeat(64)}/record.json`,
    verification_state: "ready",
    workspace_uri: "file:///home/dev/susy",
  };
}

function transaction() {
  const ownerOperation = operation();
  const record = recordSummary();
  return seal({
    actions: [
      { action: "check", available: true, consent_id: null, reason: null, record_id: PLAN_ID },
      { action: "apply", available: true, consent_id: PLAN_ID, reason: null, record_id: PLAN_ID },
      {
        action: "rollback",
        available: false,
        consent_id: null,
        reason: "workspace does not match reviewed after bytes",
        record_id: null,
      },
      {
        action: "recover",
        available: false,
        consent_id: null,
        reason: "no interrupted transaction is retained",
        record_id: PLAN_ID,
      },
    ],
    current_effective_state: "planned",
    family: "material-fluid-recipe",
    format: "workbench-developer-feature-transaction-view-v1",
    kind: TRANSACTION_KIND,
    limitations: [
      "Retained records are immutable; current state is derived from workspace bytes.",
      "This compact view omits operation Base64; reopen the owner presentation for exact byte custody.",
    ],
    operations: [{
      after_sha256: ownerOperation.after_sha256,
      after_size: ownerOperation.after_size,
      before_sha256: ownerOperation.before_sha256,
      before_size: ownerOperation.before_size,
      diff: ownerOperation.diff,
      ordinal: ownerOperation.ordinal,
      path: ownerOperation.path,
      role: ownerOperation.role,
    }],
    plan_freshness: {
      format: "workbench-developer-material-fluid-recipe-plan-verification-v1",
      plan_id: PLAN_ID,
      reason: null,
      schema_version: 1,
      state: "ready",
    },
    plan_id: PLAN_ID,
    records: [record],
    schema_version: 1,
    workspace_match: {
      operations: [{
        actual_sha256: ownerOperation.before_sha256,
        actual_size: ownerOperation.before_size,
        ordinal: 0,
        path: ownerOperation.path,
        reason: null,
        state: "matches-before",
      }],
      reason: null,
      state: "matches-before",
    },
    workspace_uri: "file:///home/dev/susy",
  }, TRANSACTION_KIND);
}

function catalog() {
  return seal({
    filters: { collection: null, family: null },
    format: "workbench-developer-feature-record-catalog-v1",
    kind: CATALOG_KIND,
    limitations: ["Discovery validates owner records and omits invalid entries."],
    records: [recordSummary()],
    schema_version: 1,
    state_root_uri: "file:///state",
  }, CATALOG_KIND);
}

function recipePlanPresentation() {
  const value = presentation();
  delete value.id;
  value.family = "recipe-change";
  value.plan_id = RECIPE_PLAN_ID;
  value.owner_record.id = RECIPE_PLAN_ID;
  value.owner_record.kind = "workbench-supersymmetry-recipe-change-plan";
  value.verification.plan_id = RECIPE_PLAN_ID;
  value.actions.find((action) => action.action === "apply").consent_id = RECIPE_PLAN_ID;
  value.actions.find((action) => action.action === "run").consent_id = RECIPE_PLAN_ID;
  return seal(value, PRESENTATION_KIND);
}

function recipeRunPresentation() {
  const value = recipePlanPresentation();
  delete value.id;
  value.actions = [];
  value.collection = "runs";
  value.format = "workbench-developer-feature-presentation-v2";
  value.owner_record.id = RECIPE_RUN_ID;
  value.owner_record.kind = "workbench-developer-recipe-change-runtime-comparison";
  value.owner_record.state = "complete";
  value.owner_record.uri = `file:///state/runs/${"3".repeat(64)}/record.json`;
  value.runtime = {
    action_available: true,
    outcome: "runtime-comparison-completed",
    record_id: RECIPE_RUN_ID,
    requirement: null,
    sides: [
      runtimeSide("baseline", "a", true),
      runtimeSide("candidate", "b", false),
    ],
    state: "complete",
  };
  value.schema_version = 2;
  return seal(value, PRESENTATION_KIND);
}

function runtimeSide(role, digit, includeGroovy) {
  return {
    assertion: { id: `assessment-${role}`, state: "observed" },
    error: null,
    outcome: "observed",
    probe: {
      id: `probe-${role}`,
      overlay_id: `overlay-${role}`,
      overlay_uri: `file:///state/probes/${role}/overlay.json`,
      script_uri: `file:///state/probes/${role}/Probe.groovy`,
    },
    receipt: {
      final_launch: {
        id: `receipt-${role}`,
        sha256: digit.repeat(64),
        size: 42,
        uri: `file:///state/runtime/${role}/final-launch.json`,
      },
      groovy_log: includeGroovy ? {
        sha256: "c".repeat(64),
        size: 0,
        uri: `file:///state/runtime/${role}/groovy.log`,
      } : null,
      runtime_session: {
        id: `session-${role}`,
        sha256: digit.repeat(64),
        size: 84,
        uri: `file:///state/runtime/${role}/session.json`,
      },
    },
    role,
    state: "complete",
  };
}

function recipeRunCatalog() {
  const record = {
    ...recordSummary(),
    collection: "runs",
    family: "recipe-change",
    plan_id: RECIPE_PLAN_ID,
    record_id: RECIPE_RUN_ID,
    record_kind: "workbench-developer-recipe-change-runtime-comparison",
    record_state: "complete",
    reference: RECIPE_RUN_ID,
    uri: `file:///state/runs/${"3".repeat(64)}/record.json`,
  };
  return seal({
    filters: { collection: "runs", family: "recipe-change" },
    format: "workbench-developer-feature-record-catalog-v1",
    kind: CATALOG_KIND,
    limitations: ["Discovery validates owner records and omits invalid entries."],
    records: [record],
    schema_version: 1,
    state_root_uri: "file:///state",
  }, CATALOG_KIND);
}

test("retained record discovery and presentation verify exact sealed owner envelopes", () => {
  const selected = validateRecordCatalog(catalog());
  const result = validatePresentation(presentation(), {
    family: "material-fluid-recipe",
    collection: "plans",
    reference: PLAN_ID,
    record: selected.records[0],
  });
  assert.equal(result.owner_record.id, PLAN_ID);
  assert.equal(result.operations[0].path, "groovy/postInit/recipes/Probe.groovy");
  assert.equal(Object.isFrozen(result.operations), true);
});

test("a current plan presentation may refresh freshness without escaping its immutable summary", () => {
  const selected = validateRecordCatalog(catalog()).records[0];
  const changed = presentation();
  delete changed.id;
  changed.verification.state = "stale";
  changed.verification.reason = "workspace changed after discovery";
  for (const action of changed.actions.filter((item) => ["apply", "run"].includes(item.action))) {
    action.available = false;
    action.consent_id = null;
    action.reason = "plan is stale";
  }
  const refreshed = seal(changed, PRESENTATION_KIND);
  assert.throws(
    () => validatePresentation(refreshed, { record: selected }),
    /does not match its discovery summary/,
  );
  assert.equal(validatePresentation(refreshed, {
    record: selected,
    allowVerificationRefresh: true,
  }).verification.state, "stale");
  changed.workspace_uri = "file:///different";
  assert.throws(
    () => validatePresentation(seal(changed, PRESENTATION_KIND), {
      record: selected,
      allowVerificationRefresh: true,
    }),
    /does not match its discovery summary/,
  );
});

test("transaction V1 validates current bytes, retained lineage, actions, and its seal", () => {
  const selected = validateRecordCatalog(catalog()).records[0];
  const result = validateTransaction(transaction(), {
    family: "material-fluid-recipe",
    planId: PLAN_ID,
    record: selected,
  });
  assert.equal(result.current_effective_state, "planned");
  assert.equal(result.workspace_match.state, "matches-before");
  assert.equal(result.actions.find((action) => action.action === "apply").available, true);
  assert.equal(result.records[0].record_state, "experimental-ready");
  assert.equal(Object.isFrozen(result.operations), true);
  assert.equal(Object.isFrozen(result.workspace_match.operations), true);
});

test("transaction V1 fails closed on derived state, workspace, record, and action lies", () => {
  const reseal = (mutate) => {
    const value = transaction();
    delete value.id;
    mutate(value);
    return seal(value, TRANSACTION_KIND);
  };
  const unsealed = transaction();
  unsealed.current_effective_state = "restored";
  assert.throws(() => validateTransaction(unsealed), /content identity/);
  assert.throws(
    () => validateTransaction(reseal((value) => { value.current_effective_state = "restored"; })),
    /retained lineage changed/,
  );
  assert.throws(
    () => validateTransaction(reseal((value) => { value.workspace_match.state = "matches-after"; })),
    /aggregate workspace match changed/,
  );
  assert.throws(
    () => validateTransaction(reseal((value) => {
      value.workspace_match.operations[0].actual_sha256 = "f".repeat(64);
    })),
    /before-byte match changed/,
  );
  assert.throws(
    () => validateTransaction(reseal((value) => { value.records[0].record_state = "complete"; })),
    /retained record 0 changed/,
  );
  assert.throws(
    () => validateTransaction(reseal((value) => { value.actions[1].consent_id = null; })),
    /action meaning changed/,
  );
  assert.throws(
    () => validateTransaction(reseal((value) => { value.operations[0].copied_policy = true; })),
    /fields changed/,
  );
});

test("transaction V1 represents interrupted recovery without enabling competing mutations", () => {
  const value = transaction();
  delete value.id;
  value.current_effective_state = "interrupted";
  value.actions[1] = {
    ...value.actions[1],
    available: false,
    consent_id: null,
    reason: "an interrupted transaction must be recovered before applying",
  };
  value.actions[2] = {
    ...value.actions[2],
    reason: "an interrupted transaction must be recovered before rolling back",
  };
  value.actions[3] = { ...value.actions[3], available: true, reason: null };
  const result = validateTransaction(seal(value, TRANSACTION_KIND));
  assert.equal(result.current_effective_state, "interrupted");
  assert.deepEqual(result.actions.map((action) => action.available), [true, false, false, true]);

  delete value.id;
  value.actions[1] = {
    ...value.actions[1], available: true, consent_id: PLAN_ID, reason: null,
  };
  assert.throws(
    () => validateTransaction(seal(value, TRANSACTION_KIND)),
    /action meaning changed/,
  );
});

test("V1 recipe-change plans and V2 retained runtime comparisons use the shared boundary", () => {
  const plan = validatePresentation(recipePlanPresentation());
  assert.equal(plan.runtime.action_available, true);
  assert.equal(plan.actions.find((action) => action.action === "run").available, true);

  const selected = validateRecordCatalog(recipeRunCatalog(), {
    family: "recipe-change",
    collection: "runs",
  });
  const run = validatePresentation(recipeRunPresentation(), {
    family: "recipe-change",
    collection: "runs",
    reference: RECIPE_RUN_ID,
    record: selected.records[0],
  });
  assert.equal(run.owner_record.kind, "workbench-developer-recipe-change-runtime-comparison");
  assert.equal(run.runtime.state, "complete");
  assert.equal(run.schema_version, 2);
  assert.deepEqual(run.runtime.sides.map((side) => side.role), ["baseline", "candidate"]);
  assert.equal(run.runtime.sides[0].receipt.final_launch.id, "receipt-baseline");
  assert.equal(run.runtime.sides[0].receipt.groovy_log.size, 0);
  assert.equal(Object.isFrozen(run.runtime.sides), true);
  assert.equal(Object.isFrozen(run.runtime.sides[0]), true);
  assert.equal(Object.isFrozen(run.runtime.sides[0].receipt.final_launch), true);
});

test("V2 runtime projections fail closed on version, shape, identity, and custody drift", () => {
  const reseal = (mutate) => {
    const value = recipeRunPresentation();
    delete value.id;
    mutate(value);
    return seal(value, PRESENTATION_KIND);
  };

  assert.throws(
    () => validatePresentation(reseal((value) => { value.format = "workbench-developer-feature-presentation-v1"; })),
    /presentation identity changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.collection = "plans"; })),
    /presentation identity changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.authority_boundary = []; })),
    /authority boundary must be an ordinary object/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.sides[0].copied_meaning = true; })),
    /runtime side 0 fields changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.sides[1].role = "baseline"; })),
    /role is duplicated/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.sides[0].role = "é".repeat(65); })),
    /role is invalid/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => {
      value.runtime.sides[0].error = { kind: "MarkerError", message: "marker missing", phase: "gameplay" };
    })),
    /phase changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => {
      value.runtime.sides[0].probe.overlay_uri = "https://example.invalid/overlay.json";
    })),
    /must be a file URI/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => {
      value.runtime.sides[0].receipt.final_launch.sha256 = "A".repeat(64);
    })),
    /digest changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => {
      value.runtime.sides[0].receipt.runtime_session.size = 0;
    })),
    /outside its supported boundary/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.record_id = PLAN_ID; })),
    /runtime record identity changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.state = "incomplete"; })),
    /runtime record identity changed/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.requirement = []; })),
    /requirement must be an ordinary object/,
  );
  assert.throws(
    () => validatePresentation(reseal((value) => { value.runtime.sides = []; })),
    /runtime sides changed/,
  );
});

test("quest presentations cannot overclaim the shared runtime boundary", () => {
  const value = recipePlanPresentation();
  delete value.id;
  const questPlan = `workbench-developer-source-feature-plan:sha256:${"4".repeat(64)}`;
  value.family = "quest-for-process";
  value.plan_id = questPlan;
  value.owner_record.id = questPlan;
  value.owner_record.kind = "workbench-developer-source-feature-plan";
  value.verification.plan_id = questPlan;
  value.actions.find((action) => action.action === "apply").consent_id = questPlan;
  const run = value.actions.find((action) => action.action === "run");
  run.available = false;
  run.consent_id = null;
  run.reason = "runtime execution is unavailable for this family";
  value.runtime.action_available = false;
  value.runtime.state = "required-not-available";
  const quest = seal(value, PRESENTATION_KIND);
  assert.equal(validatePresentation(quest).runtime.action_available, false);

  delete quest.id;
  quest.runtime.action_available = true;
  assert.throws(
    () => validatePresentation(seal(quest, PRESENTATION_KIND)),
    /runtime boundary changed/,
  );
});

test("native record envelopes fail closed on shape, seal, and exact byte drift", () => {
  const extra = catalog();
  extra.copied_policy = true;
  assert.throws(() => validateRecordCatalog(extra), /fields changed/);

  const kindDrift = catalog();
  kindDrift.records[0].family = "recipe-change";
  const kindBody = { ...kindDrift };
  delete kindBody.id;
  kindDrift.id = seal(kindBody, CATALOG_KIND).id;
  assert.throws(() => validateRecordCatalog(kindDrift), /owner kind binding/);

  const escaped = catalog();
  escaped.records[0].uri = "file:///other/plans/record.json";
  const escapedBody = { ...escaped };
  delete escapedBody.id;
  escaped.id = seal(escapedBody, CATALOG_KIND).id;
  assert.throws(() => validateRecordCatalog(escaped), /escaped its state root/);

  const unsealed = presentation();
  unsealed.limitations = ["silently changed"];
  assert.throws(() => validatePresentation(unsealed), /content identity/);

  const bytes = presentation();
  bytes.operations[0].after_base64 = Buffer.from("other\n").toString("base64");
  const body = { ...bytes };
  delete body.id;
  bytes.id = seal(body, PRESENTATION_KIND).id;
  assert.throws(() => validatePresentation(bytes), /owner digest/);

  const summary = catalog();
  summary.records[0].record_state = "complete";
  const summaryBody = { ...summary };
  delete summaryBody.id;
  summary.id = seal(summaryBody, CATALOG_KIND).id;
  const validated = validateRecordCatalog(summary);
  assert.throws(
    () => validatePresentation(presentation(), { record: validated.records[0] }),
    /does not match its discovery summary/,
  );
});

test("record discovery and presentation use exact no-shell WSL-safe routes", async () => {
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      const value = arguments_.includes("records")
        ? catalog() : arguments_.includes("transaction") ? transaction() : presentation();
      callback(null, JSON.stringify(value), "");
    };
    const executable = "\\\\wsl.localhost\\Ubuntu\\home\\dev\\workbench\\workbench";
    const options = {
      platform: "win32",
      environment: { SystemRoot: "C:\\Windows", PATH: "C:\\Windows\\System32" },
    };
    const discovered = await invokeRecordCatalog(executable, {
      stateRoot: "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state",
    }, options);
    await invokePresentation(executable, {
      family: "material-fluid-recipe",
      collection: "plans",
      reference: PLAN_ID,
      record: discovered.records[0],
      stateRoot: "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state",
    }, options);
    await invokeTransaction(executable, {
      family: "material-fluid-recipe",
      planId: PLAN_ID,
      record: discovered.records[0],
      stateRoot: "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state",
    }, options);
    assert.deepEqual(calls[0].arguments_.slice(-5), [
      "feature", "records", "--state-root", "/home/dev/state", "--json",
    ]);
    const presentIndex = calls[1].arguments_.indexOf("feature");
    assert.deepEqual(calls[1].arguments_.slice(presentIndex, presentIndex + 6), [
      "feature", "present", "material-fluid-recipe", "plans", PLAN_ID, "--state-root",
    ]);
    const transactionIndex = calls[2].arguments_.indexOf("feature");
    assert.deepEqual(calls[2].arguments_.slice(transactionIndex), [
      "feature", "transaction", "material-fluid-recipe", PLAN_ID,
      "--state-root", "/home/dev/state", "--json",
    ]);
    assert.equal(calls.every((call) => call.options.shell === false), true);
  } finally {
    childProcess.execFile = original;
  }
});

module.exports = { catalog, operation, presentation, recordSummary, transaction };
