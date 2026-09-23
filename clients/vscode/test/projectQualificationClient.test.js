"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { promisify } = require("node:util");
const { pathToFileURL } = require("node:url");

const { resolveCoreLaunch } = require("../coreLaunch");
const {
  applyProjectQualification,
  canonicalQualificationWorkspace,
  planProjectQualification,
  projectQualificationPlanId,
  qualificationArguments,
  qualificationPlanSummary,
  validateProjectQualificationPlan,
  validateProjectQualificationResult,
} = require("../projectQualificationClient");

function checks(state = "ready") {
  return [{
    id: "profile-conformance",
    label: "Supersymmetry profile conformance",
    state: state === "incompatible" ? "incompatible" : "ready",
    detail: state === "incompatible"
      ? "The checkout does not match the selected pack-family profile."
      : "The checkout matches the selected pack-family profile.",
  }, {
    id: "packwiz-index-integrity",
    label: "Packwiz index integrity",
    state,
    detail: state === "attention"
      ? "The declared Packwiz index digest differs from the current index."
      : state === "incompatible"
        ? "Unavailable until profile conformance succeeds."
        : "The declared Packwiz index digest matches the current index.",
  }];
}

function plan(overrides = {}) {
  const state = overrides.state || "ready";
  const canApply = state !== "incompatible";
  const value = {
    format: "workbench-project-qualification-plan-v1",
    schema_version: 1,
    operation_class: "review-before-mutation",
    plan_id: "",
    state,
    can_apply: canApply,
    project_id: "supersymmetry",
    profile: {
      selector: "supersymmetry",
      pack_profile_id: "workbench-pack:supersymmetry",
      pack_variant: "main",
      platform_profile_id: "cleanroommc:0.3.0-alpha",
      selection_digest: `sha256:${"1".repeat(64)}`,
    },
    workspace: {
      root: "/work/Supersymmetry",
      root_uri: "file:///work/Supersymmetry",
      revision: "2".repeat(40),
      dirty: true,
      dirty_entries: [" M pack.toml"],
      dirty_fingerprint: `sha256:${"3".repeat(64)}`,
    },
    inspection_id: `workbench-project-inspection:sha256:${"4".repeat(64)}`,
    checks: checks(state),
    limitations: state === "attention" ? [
      "Family qualification does not establish Packwiz or runtime payload integrity.",
    ] : state === "incompatible" ? [
      "No pack-family qualification is granted while exact profile inspection is incompatible.",
    ] : [
      "Qualification is not a stable support or runtime-success claim.",
    ],
    binding: {
      state: "absent",
      binding_id: `workbench-project-qualification-binding:sha256:${"5".repeat(64)}`,
      path: `/workbench-state/project-qualification-v1/bindings/${"5".repeat(64)}.json`,
      state_revision: null,
      stale_reasons: [],
    },
    actions: canApply ? [{
      id: "persist-project-qualification",
      operation: "atomic-private-record-create",
      destination: `/workbench-state/project-qualification-v1/bindings/${"5".repeat(64)}.json`,
      effect: "Create one private project-family qualification record.",
    }] : [],
    consent: canApply ? {
      required: true,
      prompt: "Apply this qualification? [y/N]",
      non_interactive: "Pass this exact plan_id with --apply.",
    } : { required: false, prompt: null, non_interactive: null },
    ...overrides,
  };
  value.plan_id = projectQualificationPlanId(value);
  return value;
}

function result(selectedPlan, overrides = {}) {
  return {
    format: "workbench-project-qualification-result-v1",
    schema_version: 1,
    outcome: selectedPlan.binding.state === "absent"
      ? "qualified"
      : selectedPlan.binding.state === "stale" ? "requalified" : "reused",
    applied_plan_id: selectedPlan.plan_id,
    binding: {
      state: "current",
      binding_id: selectedPlan.binding.binding_id,
      path: selectedPlan.binding.path,
      state_revision: `workbench-project-qualification-state:sha256:${"6".repeat(64)}`,
      stale_reasons: [],
    },
    qualification: {
      qualified: true,
      project_id: selectedPlan.project_id,
      profile: structuredClone(selectedPlan.profile),
      workspace: structuredClone(selectedPlan.workspace),
      inspection_id: selectedPlan.inspection_id,
      state: selectedPlan.state,
      checks: structuredClone(selectedPlan.checks),
      limitations: structuredClone(selectedPlan.limitations),
    },
    next_commands: [
      [
        "workbench", "project", "qualify", selectedPlan.workspace.root,
        "--profile", "supersymmetry", "--status", "--state-root", "/workbench-state",
      ],
    ],
    ...overrides,
  };
}

test("builds exact plan/apply argv without an approval bypass and maps core-side paths", () => {
  const native = resolveCoreLaunch("/opt/workbench/bin/workbench", { platform: "linux" });
  const selected = plan();
  assert.deepEqual(qualificationArguments("/work/Supersymmetry", "plan", native), [
    "project", "qualify", "/work/Supersymmetry",
    "--profile", "supersymmetry", "--plan", "--json",
  ]);
  assert.deepEqual(qualificationArguments(
    "/work/Supersymmetry", { apply: selected.plan_id }, native,
  ), [
    "project", "qualify", "/work/Supersymmetry",
    "--profile", "supersymmetry", "--apply", selected.plan_id, "--json",
  ]);
  assert.doesNotMatch(qualificationArguments(
    "/work/Supersymmetry", "plan", native,
  ).join(" "), /--yes|approve/i);

  const wsl = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
    { platform: "win32", environment: { SystemRoot: "C:\\Windows" } },
  );
  assert.deepEqual(qualificationArguments(
    "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry", "plan", wsl,
  ).slice(0, 3), ["project", "qualify", "/work/Supersymmetry"]);
  assert.deepEqual(qualificationArguments(
    "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry", "plan", wsl, {
      stateRoot: "\\\\wsl.localhost\\Ubuntu\\workbench-state",
    },
  ), [
    "project", "qualify", "/work/Supersymmetry", "--profile", "supersymmetry",
    "--state-root", "/workbench-state", "--plan", "--json",
  ]);
});

test("validates exact bounded ready, attention, and incompatible plans and their identity", () => {
  const ready = plan();
  const attention = plan({ state: "attention" });
  const incompatible = plan({ state: "incompatible" });
  assert.equal(validateProjectQualificationPlan(ready, "/work/Supersymmetry"), ready);
  assert.equal(validateProjectQualificationPlan(attention, "/work/Supersymmetry"), attention);
  assert.equal(
    validateProjectQualificationPlan(incompatible, "/work/Supersymmetry"), incompatible,
  );
  assert.equal(incompatible.can_apply, false);
  assert.deepEqual(incompatible.actions, []);

  assert.throws(() => validateProjectQualificationPlan({
    ...plan(), inventedApproval: true,
  }, "/work/Supersymmetry"), /fields changed/);
  assert.throws(() => validateProjectQualificationPlan({
    ...plan(), inspection_id: `workbench-project-inspection:sha256:${"9".repeat(64)}`,
  }, "/work/Supersymmetry"), /identity changed/);
  assert.throws(() => validateProjectQualificationPlan(plan({
    state: "incompatible", can_apply: true,
  }), "/work/Supersymmetry"), /applicability is inconsistent/);
  assert.throws(() => validateProjectQualificationPlan(plan({
    checks: checks("attention"),
  }), "/work/Supersymmetry"), /checks and qualification state are inconsistent/);
  assert.throws(() => validateProjectQualificationPlan(plan({
    checks: checks("ready").slice(0, 1),
  }), "/work/Supersymmetry"), /omits required check/);
  assert.throws(() => validateProjectQualificationPlan(plan({
    profile: { ...plan().profile, pack_profile_id: "workbench-pack:other" },
  }), "/work/Supersymmetry"), /another pack profile/);
});

test("plan identity deliberately excludes Git provenance but binds the workspace root", () => {
  const first = plan();
  const anotherRevision = plan({
    workspace: {
      ...first.workspace,
      revision: "a".repeat(40),
      dirty_entries: [" M pack.toml", "?? notes.txt"],
      dirty_fingerprint: `sha256:${"b".repeat(64)}`,
    },
  });
  assert.equal(anotherRevision.plan_id, first.plan_id);
  assert.notEqual(plan({
    workspace: { ...first.workspace, root: "/work/another" },
  }).plan_id, first.plan_id);
});

test("binds the result to the exact plan, evidence, binding, and outcome", () => {
  const selected = plan({ state: "attention" });
  const applied = result(selected);
  assert.equal(validateProjectQualificationResult(applied, selected), applied);
  assert.throws(() => validateProjectQualificationResult({
    ...applied,
    applied_plan_id: `workbench-project-qualification-plan:sha256:${"8".repeat(64)}`,
  }, selected), /exact applied plan ID/);
  assert.throws(() => validateProjectQualificationResult(result(selected, {
    outcome: "reused",
  }), selected), /planned binding state/);
  assert.throws(() => validateProjectQualificationResult(result(selected, {
    binding: { ...applied.binding, binding_id: `workbench-project-qualification-binding:sha256:${"9".repeat(64)}` },
  }), selected), /binding differs/);
  assert.throws(() => validateProjectQualificationResult(result(selected, {
    qualification: {
      ...applied.qualification,
      checks: checks("ready"),
      state: "ready",
    },
  }), selected), /identity differs|evidence differs/);
  assert.throws(() => validateProjectQualificationResult(result(selected, {
    next_commands: [[
      "workbench", "project", "qualify", selected.workspace.root,
      "--profile", "supersymmetry", "--status",
    ]],
  }), selected), /effective state root/);
  assert.throws(() => validateProjectQualificationResult(result(selected, {
    next_commands: [[
      "workbench", "project", "qualify", selected.workspace.root,
      "--profile", "supersymmetry", "--status", "--state-root", "/another-state",
    ]],
  }), selected), /effective state root/);
  assert.throws(() => validateProjectQualificationResult(result(selected, {
    next_commands: [],
  }), selected), /one exact status command/);
});

test("accepts fresh Git provenance after apply while retaining the exact workspace root", () => {
  const selected = plan();
  const applied = result(selected);
  applied.qualification.workspace = {
    ...applied.qualification.workspace,
    revision: "a".repeat(40),
    dirty: false,
    dirty_entries: [],
    dirty_fingerprint: `sha256:${"b".repeat(64)}`,
  };
  assert.equal(validateProjectQualificationResult(applied, selected), applied);

  const wrongRoot = result(selected);
  wrongRoot.qualification.workspace = {
    ...wrongRoot.qualification.workspace,
    root: "/work/AnotherCheckout",
  };
  assert.throws(
    () => validateProjectQualificationResult(wrongRoot, selected),
    /root differs from the requested local workspace/,
  );
});

test("renders state, checks, attention, local effect, and non-approval boundary", () => {
  const summary = qualificationPlanSummary(plan({ state: "attention" }));
  assert.match(summary, /Qualification state: ATTENTION/);
  assert.match(summary, /\[READY\] Supersymmetry profile conformance/);
  assert.match(summary, /\[ATTENTION\] Packwiz index integrity/);
  assert.match(summary, /Working tree: dirty \(1 entry\)/);
  assert.match(summary, /Planned local record:/);
  assert.match(summary, /does not approve source changes/);
  assert.match(summary, /Packwiz\/runtime payload integrity, or release/);
  assert.match(summary, /Only the exact plan ID shown here can be applied/);

  const incompatible = qualificationPlanSummary(plan({ state: "incompatible" }));
  assert.match(incompatible, /Qualification state: INCOMPATIBLE/);
  assert.match(incompatible, /Planned local record: none/);
  assert.match(incompatible, /No plan ID can authorize apply/);
});

test("invokes the two core phases directly and applies only the returned exact plan ID", async () => {
  const original = childProcess.execFile;
  const selected = plan();
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      const output = arguments_.includes("--plan") ? selected : result(selected);
      setImmediate(() => callback(null, `${JSON.stringify(output)}\n`, ""));
      return { pid: 999_999, exitCode: 0, signalCode: null };
    };
    const planned = await planProjectQualification(
      "/opt/workbench/bin/workbench", "/work/Supersymmetry",
      {
        platform: "linux",
        cwd: "/work/Supersymmetry",
        stateRoot: "/workbench-state",
        workspaceRealpath: (value) => value,
      },
    );
    const applied = await applyProjectQualification(
      "/opt/workbench/bin/workbench", "/work/Supersymmetry", planned,
      {
        platform: "linux",
        cwd: "/work/Supersymmetry",
        stateRoot: "/workbench-state",
        workspaceRealpath: (value) => value,
      },
    );
    assert.equal(applied.applied_plan_id, selected.plan_id);
    assert.deepEqual(calls.map((call) => call.arguments_), [
      [
        "project", "qualify", "/work/Supersymmetry", "--profile", "supersymmetry",
        "--state-root", "/workbench-state", "--plan", "--json",
      ],
      [
        "project", "qualify", "/work/Supersymmetry", "--profile", "supersymmetry",
        "--state-root", "/workbench-state", "--apply", selected.plan_id, "--json",
      ],
    ]);
    for (const call of calls) {
      assert.equal(call.options.shell, false);
      assert.equal(call.options.maxBuffer, 2 * 1024 * 1024);
    }
  } finally {
    childProcess.execFile = original;
  }
});

test("resolves a symlink-parent workspace before both core phases and exact validation", async () => {
  const temporary = fs.mkdtempSync(path.join(
    os.tmpdir(), "workbench-vscode-project-qualification-realpath-",
  ));
  const realParent = path.join(temporary, "real");
  const realWorkspace = path.join(realParent, "Supersymmetry");
  const aliasParent = path.join(temporary, "alias");
  fs.mkdirSync(realWorkspace, { recursive: true });
  fs.symlinkSync(realParent, aliasParent, process.platform === "win32" ? "junction" : "dir");
  const aliasWorkspace = path.join(aliasParent, "Supersymmetry");
  const canonicalWorkspace = fs.realpathSync.native(aliasWorkspace);
  const base = plan();
  const selected = plan({
    workspace: {
      ...base.workspace,
      root: canonicalWorkspace,
      root_uri: pathToFileURL(canonicalWorkspace).href,
    },
  });
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      const output = arguments_.includes("--plan") ? selected : result(selected);
      setImmediate(() => callback(null, `${JSON.stringify(output)}\n`, ""));
      return { pid: 999_999, exitCode: 0, signalCode: null };
    };
    const launch = resolveCoreLaunch("/opt/workbench/bin/workbench", { platform: "linux" });
    const planned = await planProjectQualification(
      "/opt/workbench/bin/workbench", aliasWorkspace,
      { launch, cwd: aliasWorkspace },
    );
    await applyProjectQualification(
      "/opt/workbench/bin/workbench", aliasWorkspace, planned,
      { launch, cwd: aliasWorkspace },
    );
    assert.equal(planned.workspace.root, canonicalWorkspace);
    assert.deepEqual(calls.map((call) => call.arguments_.slice(0, 3)), [
      ["project", "qualify", canonicalWorkspace],
      ["project", "qualify", canonicalWorkspace],
    ]);
  } finally {
    childProcess.execFile = original;
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});

test("uses native Windows realpath spelling before transport and exact validation", async () => {
  const requested = "c:/dev/supersymmetry";
  const canonical = "C:\\Dev\\Supersymmetry";
  assert.equal(canonicalQualificationWorkspace(requested, (value) => {
    assert.equal(value, requested);
    return canonical;
  }), canonical);

  const base = plan();
  const selected = plan({
    workspace: {
      ...base.workspace,
      root: canonical,
      root_uri: "file:///C:/Dev/Supersymmetry",
    },
  });
  const launch = resolveCoreLaunch("C:\\Workbench\\workbench.exe", { platform: "win32" });
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      setImmediate(() => callback(null, `${JSON.stringify(selected)}\n`, ""));
      return { pid: 999_999, exitCode: 0, signalCode: null };
    };
    const planned = await planProjectQualification(
      "C:\\Workbench\\workbench.exe", requested, {
        launch,
        cwd: requested,
        workspaceRealpath: () => canonical,
      },
    );
    assert.equal(planned.workspace.root, canonical);
    assert.deepEqual(calls[0].arguments_.slice(0, 3), [
      "project", "qualify", canonical,
    ]);
    assert.equal(calls[0].options.shell, false);
  } finally {
    childProcess.execFile = original;
  }
});

test("maps a canonical WSL UNC workspace without adding a shell transport", async () => {
  const launch = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
    { platform: "win32", environment: { SystemRoot: "C:\\Windows" } },
  );
  const requested = "\\\\wsl.localhost\\Ubuntu\\work\\alias\\Supersymmetry";
  const canonical = "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry";
  const selected = plan();
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      setImmediate(() => callback(null, `${JSON.stringify(selected)}\n`, ""));
      return { pid: 999_999, exitCode: 0, signalCode: null };
    };
    const planned = await planProjectQualification(
      "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench", requested, {
        launch,
        cwd: requested,
        workspaceRealpath: () => canonical,
      },
    );
    assert.equal(planned.workspace.root, "/work/Supersymmetry");
    assert.deepEqual(calls[0].arguments_, [
      "--distribution", "Ubuntu",
      "--cd", "/opt/workbench",
      "--exec", "/opt/workbench/workbench",
      "project", "qualify", "/work/Supersymmetry",
      "--profile", "supersymmetry", "--plan", "--json",
    ]);
    assert.equal(calls[0].options.shell, false);
    assert.equal(calls[0].options.cwd, undefined);
  } finally {
    childProcess.execFile = original;
  }
});

test("parses the current public core's complete incompatible plan as a read result", async () => {
  const repository = path.resolve(__dirname, "../../..");
  const scratchRoot = fs.mkdtempSync(path.join(
    os.tmpdir(), "workbench-vscode-project-qualification-",
  ));
  const workspace = path.join(scratchRoot, "workspace");
  const stateRoot = path.join(scratchRoot, "state");
  fs.mkdirSync(workspace);
  fs.mkdirSync(stateRoot);
  const git = (arguments_) => childProcess.execFileSync(
    "git", ["-C", workspace, ...arguments_], { encoding: "utf8" },
  );
  git(["init", "--quiet"]);
  git(["config", "user.name", "Workbench VS Code Test"]);
  git(["config", "user.email", "workbench-vscode-test@invalid.example"]);
  git(["commit", "--quiet", "--allow-empty", "-m", "fixture"]);
  try {
    const { stdout, stderr } = await promisify(childProcess.execFile)(
      "python3", [
        path.join(repository, "tools/workbench.py"),
        "project", "qualify", workspace,
        "--profile", "supersymmetry",
        "--state-root", stateRoot,
        "--plan", "--json",
      ], {
        cwd: repository,
        encoding: "utf8",
        maxBuffer: 2 * 1024 * 1024,
        timeout: 120_000,
      },
    );
    assert.equal(stderr, "");
    const live = validateProjectQualificationPlan(JSON.parse(stdout), workspace);
    assert.equal(live.state, "incompatible");
    assert.equal(live.can_apply, false);
    assert.deepEqual(live.actions, []);
  } finally {
    fs.rmSync(scratchRoot, { recursive: true, force: true });
  }
});
