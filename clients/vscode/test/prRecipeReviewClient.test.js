"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { resolveCoreLaunch } = require("../coreLaunch");
const {
  canonicalJson,
  pullRequestNumber,
  reviewPrArguments,
  validatePrReviewPlan,
  validatePrReviewReport,
} = require("../prRecipeReviewClient");
const { plan, report } = require("./prRecipeReviewFixtures");

test("builds the exact two-phase reviewer command without an automation bypass", () => {
  const launch = resolveCoreLaunch("/opt/workbench/bin/workbench", { platform: "linux" });
  const request = { pullRequest: 2002, source: "/work/Supersymmetry" };
  assert.deepEqual(reviewPrArguments(request, "plan", launch), [
    "review", "pr", "2002",
    "--profile", "supersymmetry",
    "--source", "/work/Supersymmetry",
    "--plan", "--json",
  ]);
  assert.deepEqual(reviewPrArguments(request, { apply: plan().plan_id }, launch), [
    "review", "pr", "2002",
    "--profile", "supersymmetry",
    "--source", "/work/Supersymmetry",
    "--apply", plan().plan_id, "--json",
  ]);
  assert.doesNotMatch(reviewPrArguments(request, "plan", launch).join(" "), /--yes/);
});

test("maps only the checkout path through the bounded Windows-to-WSL adapter", () => {
  const launch = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
    { platform: "win32", environment: { SystemRoot: "C:\\Windows" } },
  );
  assert.deepEqual(reviewPrArguments({
    pullRequest: 2002,
    source: "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry",
  }, "plan", launch), [
    "review", "pr", "2002",
    "--profile", "supersymmetry",
    "--source", "/work/Supersymmetry",
    "--plan", "--json",
  ]);
  const selected = plan({ repository_root: "/work/Supersymmetry" });
  assert.equal(validatePrReviewPlan(selected, {
    pullRequest: 2002,
    source: "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry",
  }, { launch }), selected);
});

test("validates the provider-bound plan identity, request, effects, and network authority", () => {
  const selected = plan();
  assert.equal(validatePrReviewPlan(selected, {
    pullRequest: 2002, source: "/work/Supersymmetry",
  }), selected);
  assert.throws(() => validatePrReviewPlan({ ...selected, head_oid: "e".repeat(40) }, {
    pullRequest: 2002, source: "/work/Supersymmetry",
  }), /identity changed/);
  assert.throws(() => validatePrReviewPlan(plan({ provider_observation_mode: "local-json" }), {
    pullRequest: 2002, source: "/work/Supersymmetry",
  }), /authority changed/);
  assert.throws(() => validatePrReviewPlan({
    ...selected, effects: [...selected.effects, "Undeclared effect"],
  }, { pullRequest: 2002, source: "/work/Supersymmetry" }), /effects changed/);
  assert.throws(() => validatePrReviewPlan(selected, {
    pullRequest: 2003, source: "/work/Supersymmetry",
  }), /another pull request/);
});

test("validates the compact report identity and exact consented PR/Git binding", () => {
  const selectedPlan = plan();
  const selectedReport = report(selectedPlan);
  assert.equal(validatePrReviewReport(selectedReport, selectedPlan), selectedReport);
  assert.throws(() => validatePrReviewReport({
    ...selectedReport,
    summary: { ...selectedReport.summary, status: "ready" },
  }, selectedPlan), /strict decision is inconsistent/);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    selection: { ...selectedReport.selection, pull_request: 2003 },
  }), selectedPlan), /differs from the consented plan/);
  assert.throws(() => validatePrReviewReport({ ...selectedReport, inventedApproval: true }, selectedPlan),
    /fields changed/);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    profile: { ...selectedReport.profile, pack_profile_id: "supersymmetry" },
  }), selectedPlan), /pack_profile_id must be workbench-pack:supersymmetry/);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    profile: { ...selectedReport.profile, pack_profile_id: "workbench-pack:other" },
  }), selectedPlan), /pack_profile_id must be workbench-pack:supersymmetry/);
  assert.equal(validatePrReviewReport(report(selectedPlan, {
    profile: {
      ...selectedReport.profile,
      profile_id: "workbench-pack:supersymmetry:groovy-program:future-version",
    },
  }), selectedPlan).summary.status, "attention");
});

test("rejects unsafe current-workspace paths and bounded-list inconsistencies", () => {
  const selectedPlan = plan();
  const selectedReport = report(selectedPlan);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    files: {
      ...selectedReport.files,
      modified: { paths: ["../outside.groovy"], path_count: 1, truncated: false },
    },
  }), selectedPlan), /safe repository-relative path/);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    files: {
      ...selectedReport.files,
      modified: { paths: [], path_count: 1, truncated: false },
    },
  }), selectedPlan), /count is inconsistent/);
});

test("fails closed on every interpreted recipe and direct-removal row shape", () => {
  const selectedPlan = plan();
  const selectedReport = report(selectedPlan);
  const modifiedRows = selectedReport.machine_recipes.modified;
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    machine_recipes: {
      ...selectedReport.machine_recipes,
      modified: {
        ...modifiedRows,
        rows: [{ ...modifiedRows.rows[0], inventedMeaning: "approved" }],
      },
    },
  }), selectedPlan), /fields changed/);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    machine_recipes: {
      ...selectedReport.machine_recipes,
      modified: {
        ...modifiedRows,
        rows: [{
          ...modifiedRows.rows[0],
          property_changes: { duration: { before: "20", after: ["40"] } },
        }],
      },
    },
  }), selectedPlan), /values exceed their boundary/);
  const directRows = selectedReport.direct_removal_calls.added;
  const { method: _method, ...withoutMethod } = directRows.rows[0];
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    direct_removal_calls: {
      ...selectedReport.direct_removal_calls,
      added: { ...directRows, rows: [withoutMethod] },
    },
  }), selectedPlan), /fields changed/);

  const added = {
    semantic_key: "mixer|added",
    count: 1,
    recipe_map: "mixer",
    complete: true,
    properties: { duration: ["40"] },
    properties_truncated: false,
    source: { path: "groovy/recipes/mixer.groovy", line: 12, column: 2 },
    lifecycle: { stage: "static", execution_state: "candidate", runtime_invocation_count: "unknown" },
    reload_state: "unknown",
    evidence_state: "source-only",
  };
  const addedReport = report(selectedPlan, {
    machine_recipes: {
      ...selectedReport.machine_recipes,
      added: { rows: [added], row_count: 1, truncated: false },
    },
  });
  assert.equal(validatePrReviewReport(addedReport, selectedPlan), addedReport);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    machine_recipes: {
      ...selectedReport.machine_recipes,
      added: { rows: [{ ...added, source: { ...added.source, path: "/outside.groovy" } }],
        row_count: 1, truncated: false },
    },
  }), selectedPlan), /safe repository-relative path/);
});

test("preserves PR-strict versus candidate-wide attention and Git-hygiene boundaries", () => {
  const selectedPlan = plan();
  const selectedReport = report(selectedPlan);
  assert.equal(
    selectedReport.selection.attention_scope.pr_strict,
    "introduced static signals and supplied runtime attention",
  );
  assert.equal(selectedReport.selection.git_hygiene.state, "clean");
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    selection: {
      ...selectedReport.selection,
      attention_scope: {
        ...selectedReport.selection.attention_scope,
        candidate_static_signal_total: 4,
      },
    },
  }), selectedPlan), /counts are inconsistent/);
  assert.throws(() => validatePrReviewReport(report(selectedPlan, {
    selection: {
      ...selectedReport.selection,
      git_hygiene: { state: "approved", detail: "Invented approval" },
    },
  }), selectedPlan), /Git hygiene state/);
});

test("pull request numbers are explicit bounded integers", () => {
  assert.equal(pullRequestNumber(2002), 2002);
  for (const value of [0, -1, 2_147_483_648, 1.5, "2002", Number.NaN]) {
    assert.throws(() => pullRequestNumber(value), /pull request number/);
  }
});

test("canonical identity ordering matches the Python core for Unicode keys", () => {
  assert.equal(canonicalJson({ "😀": 2, "": 1 }), "{\"\":1,\"😀\":2}");
});
