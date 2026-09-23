"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { promisify } = require("node:util");

const {
  invokeWorkspaceHomeV2,
  validateWorkspaceHomeV2,
} = require("../workspaceHomeV2Client");
const {
  availableHomeV2Fixture,
  contentId,
  homeV2Fixture,
} = require("./workspaceHomeV2Fixtures");

const SOURCE_ROUTE_TIMEOUT_MS = 15 * 60 * 1000;

test("Home V2 preserves exact capability catalog, session, Home, and job identities", () => {
  const home = validateWorkspaceHomeV2(homeV2Fixture());
  assert.match(home.home_id, /^workspace-home:sha256:/);
  assert.equal(home.session.session_id, `work-session-v2-${"1".repeat(32)}`);
  assert.equal(
    home.capability_catalog.catalog_id,
    `workbench-product-capabilities:sha256:${"2".repeat(64)}`,
  );
  assert.deepEqual(home.jobs.map((job) => job.id), [
    "workspace-health", "run-development-client",
  ]);
  assert.equal(home.new_project.state, "unavailable");
  assert.equal(Object.isFrozen(home.jobs), true);
});

test("Home V2 accepts only the two exact unavailable new-project blockers", () => {
  for (const blocker of [
    "OWNER_ADMITTED_NEW_KIND_ABSENT",
    "NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE",
  ]) {
    const value = homeV2Fixture();
    value.new_project.blockers = [blocker];
    value.home_id = contentId("workspace-home", value, "home_id");
    assert.deepEqual(validateWorkspaceHomeV2(value).new_project.blockers, [blocker]);
  }

  for (const blockers of [
    [],
    ["INVENTED_CONSTRUCTION_AUTHORITY"],
    ["OWNER_ADMITTED_NEW_KIND_ABSENT", "NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE"],
  ]) {
    const value = homeV2Fixture();
    value.new_project.blockers = blockers;
    value.home_id = contentId("workspace-home", value, "home_id");
    assert.throws(
      () => validateWorkspaceHomeV2(value),
      /invented new-project construction authority/i,
    );
  }
});

test("Home V2 accepts only the exact owner-backed Cleanroom new-project availability", () => {
  const home = validateWorkspaceHomeV2(availableHomeV2Fixture());
  assert.deepEqual(home.new_project, {
    state: "available",
    admitted_kinds: ["workbench-new-project-kind:cleanroom-mod"],
    owner_ref_ids: [home.new_project.owner_ref_ids[0]],
    blockers: [],
    reason: "The Cleanroom profile owns one exact fresh-project constructor.",
    next_safe_action: "workbench new cleanroom-mod preview --help",
  });
  const owner = home.owner_records.find((item) => item.id === home.new_project.owner_ref_ids[0]);
  assert.ok(owner);
  assert.equal(owner.kind, "new-project-construction");
  assert.equal(owner.owner_id, "cleanroom-platform-profile");
  assert.equal(owner.record_format, "workbench-cleanroom-mod-construction-owner-v2");
  assert.notEqual(owner.record_id, null);
});

test("Home V2 rejects drift in every Cleanroom new-project authority binding", () => {
  const cases = [
    (value) => { value.new_project.admitted_kinds = ["workbench-new-project-kind:pack"]; },
    (value) => { value.new_project.owner_ref_ids = []; },
    (value) => { value.new_project.blockers = ["INVENTED_BLOCKER"]; },
    (value) => { value.new_project.reason = ""; },
    (value) => { value.new_project.next_safe_action = "workbench new cleanroom-mod apply"; },
    (value) => {
      value.owner_records.find((item) => item.kind === "new-project-construction").record_format =
        "workbench-cleanroom-mod-construction-owner-v3";
    },
    (value) => {
      value.owner_records.find((item) => item.kind === "new-project-construction").record_id = null;
    },
    (value) => {
      value.owner_records.find((item) => item.kind === "new-project-construction").freshness = "stale";
    },
  ];
  for (const mutate of cases) {
    const drifted = availableHomeV2Fixture();
    mutate(drifted);
    drifted.home_id = contentId("workspace-home", drifted, "home_id");
    assert.throws(
      () => validateWorkspaceHomeV2(drifted),
      /new-project|owner|reason|invalid/i,
    );
  }
});

test("Home V2 rejects wrong format, altered catalog binding, and more than five jobs", () => {
  const wrong = homeV2Fixture();
  wrong.format = "workbench-workspace-home-v1";
  assert.throws(() => validateWorkspaceHomeV2(wrong), /format|identity/i);

  const changedCatalog = homeV2Fixture();
  changedCatalog.capability_catalog.catalog_id = `workbench-product-capabilities:sha256:${"3".repeat(64)}`;
  changedCatalog.home_id = contentId("workspace-home", changedCatalog, "home_id");
  assert.throws(() => validateWorkspaceHomeV2(changedCatalog), /catalog|record|identity/i);

  const oversized = homeV2Fixture();
  while (oversized.jobs.length < 6) {
    const copy = structuredClone(oversized.jobs.at(-1));
    copy.id = `extra-${oversized.jobs.length}`;
    copy.rank = 100 + oversized.jobs.length;
    copy.eligibility_digest = contentId("", copy, "eligibility_digest").replace(":sha256:", "sha256:");
    oversized.jobs.push(copy);
  }
  assert.throws(() => validateWorkspaceHomeV2(oversized), /five|jobs/i);
});

test("Home V2 rejects unavailable jobs made executable and stale/corrupt owner dependencies", () => {
  const invented = homeV2Fixture();
  invented.jobs[1].argv = ["workbench", "run", "client"];
  assert.throws(() => validateWorkspaceHomeV2(invented), /unavailable|executable|identity/i);

  for (const freshness of ["stale", "corrupt"]) {
    const drifted = homeV2Fixture();
    const ref = drifted.owner_records.find(
      (item) => item.kind === "product-capability-catalog",
    );
    ref.freshness = freshness;
    ref.integrity = freshness === "corrupt" ? "failed" : "verified";
    drifted.capability_catalog.state = "unavailable";
    drifted.capability_catalog.freshness = freshness;
    drifted.capability_catalog.reason = `The validated owner record is ${freshness}.`;
    drifted.freshness.capability_catalog = freshness;
    drifted.home_id = contentId("workspace-home", drifted, "home_id");
    assert.throws(() => validateWorkspaceHomeV2(drifted), /stale|corrupt|owner|dependent/i);
  }
});

test("Home V2 invocation uses the public open JSON route and WSL path mapping", async () => {
  const original = childProcess.execFile;
  const calls = [];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      callback(null, JSON.stringify(homeV2Fixture()), "");
    };
    await invokeWorkspaceHomeV2(
      "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
      "\\\\wsl.localhost\\Ubuntu\\home\\dev\\mod",
      {
        platform: "win32",
        environment: { SystemRoot: "C:\\Windows" },
        stateRoot: "\\\\wsl.localhost\\Ubuntu\\home\\dev\\workbench-state",
      },
    );
    assert.deepEqual(calls[0].arguments_.slice(-5), [
      "open", "/home/dev/mod", "--state-root", "/home/dev/workbench-state", "--json",
    ]);
    assert.equal(calls[0].options.shell, false);
  } finally {
    childProcess.execFile = original;
  }
});

test("Home V2 parses the live Python core projection instead of only a client fixture", async () => {
  const repository = path.resolve(__dirname, "../../..");
  const workspace = fs.mkdtempSync("/tmp/workbench-home-v2-native-");
  const script = [
    "import json, pathlib, runpy, sys",
    "root = pathlib.Path(sys.argv[1]).resolve()",
    "runpy.run_path(str(root / 'tools/workbench.py'), run_name='workbench_fixture_route')",
    "from workbench_shell.workspace_dashboard import build_workspace_home_v2",
    "print(json.dumps(build_workspace_home_v2(root, pathlib.Path(sys.argv[2])), sort_keys=True))",
  ].join("; ");
  try {
    const { stdout, stderr } = await promisify(childProcess.execFile)(
      "python3", ["-c", script, repository, workspace],
      { cwd: repository, encoding: "utf8", maxBuffer: 16 * 1024 * 1024, timeout: 120_000 },
    );
    assert.equal(stderr, "");
    const home = validateWorkspaceHomeV2(JSON.parse(stdout));
    assert.equal(home.format, "workbench-workspace-home-v2");
    assert.equal(home.jobs.length <= 5, true);
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true });
  }
});

test("Home V2 parses the public route with exact available or unavailable capability rows", async () => {
  const repository = path.resolve(__dirname, "../../..");
  const { stdout, stderr } = await promisify(childProcess.execFile)(
    "python3", [path.join(repository, "tools/workbench.py"), "open", repository, "--json"],
    {
      cwd: repository, encoding: "utf8", maxBuffer: 16 * 1024 * 1024,
      timeout: SOURCE_ROUTE_TIMEOUT_MS,
    },
  );
  assert.equal(stderr, "");
  const home = validateWorkspaceHomeV2(JSON.parse(stdout));
  const bound = home.jobs.filter((job) => job.capability !== null);
  assert.equal(bound.length > 0, true);
  for (const job of bound) {
    assert.equal(job.capability.capability_id, job.capability_id);
    assert.equal(job.capability.capability_key, job.capability_key);
    assert.equal(job.capability.catalog_action.command_id, job.command_id);
    assert.equal(job.capability.catalog_action.action_digest, job.action_digest);
    assert.equal(job.capability.handler.registered, true);
  }
});

test("Home V2 parses the public Cleanroom fixture job's complete execution binding", {
  skip: !(process.env.WORKBENCH_CLEANROOM_FIXTURE_GRADLEW
    && process.env.WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME),
}, async () => {
  const repository = path.resolve(__dirname, "../../..");
  const workspace = path.join(
    repository, "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop",
  );
  const stateRoot = fs.mkdtempSync("/tmp/workbench-home-v2-cleanroom-");
  try {
    const { stdout, stderr } = await promisify(childProcess.execFile)(
      "python3", [
        path.join(repository, "tools/workbench.py"), "adopt", workspace,
        "--state-root", stateRoot, "--json",
      ],
      {
        cwd: workspace, encoding: "utf8", env: process.env,
        maxBuffer: 16 * 1024 * 1024, timeout: SOURCE_ROUTE_TIMEOUT_MS,
      },
    );
    assert.equal(stderr, "");
    const home = validateWorkspaceHomeV2(JSON.parse(stdout));
    const fixture = home.jobs.find((job) => job.id === "cleanroom-fixture-build");
    assert.ok(fixture);
    assert.deepEqual(Object.keys(fixture.arguments).sort(), [
      "expected_input_digest", "gradle_cmd", "java_home", "state_root",
    ]);
    assert.equal(path.isAbsolute(fixture.arguments.state_root), true);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});
