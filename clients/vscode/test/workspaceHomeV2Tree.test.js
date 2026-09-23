"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  validateRecoveryPreview,
  validateWorkSessionStatus,
  validateWorkSessionTimeline,
} = require("../workSessionClient");
const { validateWorkspaceHomeV2 } = require("../workspaceHomeV2Client");
const { WorkspaceHomeV2TreeProvider } = require("../workspaceHomeV2Tree");
const {
  contentId: workSessionContentId,
  recoveryFixture,
  summaryFixture,
  timelineFixture,
} = require("./workSessionFixtures");
const {
  contentId: homeContentId,
  homeV2Fixture,
} = require("./workspaceHomeV2Fixtures");

function fakeVscode() {
  class EventEmitter {
    constructor() { this.event = () => ({ dispose() {} }); }
    fire() {}
    dispose() {}
  }
  class TreeItem {
    constructor(label, collapsibleState) {
      this.label = label;
      this.collapsibleState = collapsibleState;
    }
  }
  class ThemeIcon { constructor(id) { this.id = id; } }
  return {
    EventEmitter,
    ThemeIcon,
    TreeItem,
    TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
  };
}

test("Home V2 renders exactly six stable native regions and preserves owner job order", () => {
  const provider = new WorkspaceHomeV2TreeProvider(fakeVscode());
  provider.setHome(validateWorkspaceHomeV2(homeV2Fixture()));
  provider.setWorkSession({
    status: validateWorkSessionStatus(summaryFixture()),
    timeline: validateWorkSessionTimeline(timelineFixture()),
    recovery: validateRecoveryPreview(recoveryFixture()),
  });
  const roots = provider.getChildren();
  assert.deepEqual(roots.map((item) => [item.id, item.label]), [
    ["workspace-home-v2-region:workspace", "Workspace"],
    ["workspace-home-v2-region:exact-environment", "Exact Environment"],
    ["workspace-home-v2-region:support-limitations", "Support and Limitations"],
    ["workspace-home-v2-region:active-work", "Active Work"],
    ["workspace-home-v2-region:recovery-required", "Recovery Required"],
    ["workspace-home-v2-region:recommended-jobs", "Recommended Jobs"],
  ]);
  const jobs = provider.getChildren(roots[5]);
  assert.deepEqual(jobs.map((item) => item.job.id), [
    "workspace-health", "run-development-client",
  ]);
  assert.equal(provider.getTreeItem(jobs[0]).contextValue, "workbenchHomeV2JobAvailable");
  assert.deepEqual(
    provider.getChildren(jobs[0]).filter((item) => item.type === "fact")
      .map((item) => [item.label, item.value]),
    [
      ["Availability basis", "owner-context-resolution/workspace-context"],
      ["Global capability effect", "retained-unmodified"],
      ["Capability ID", `capability:sha256:${"e".repeat(64)}`],
      ["Capability availability", "experimental"],
      ["Capability authority", "Project Intelligence facts composed by Workbench Shell"],
      ["Handler", "process/executable"],
    ],
  );
  const inspect = provider.getChildren(jobs[0]).find((item) => item.type === "job-inspect");
  assert.equal(
    provider.getTreeItem(inspect).command.command,
    "workbench.workspaceHome.inspectValue",
  );
  assert.equal(provider.getTreeItem(jobs[1]).contextValue, "workbenchHomeV2JobUnavailable");
  assert.equal(provider.getTreeItem(jobs[1]).command, undefined);

  const active = provider.getChildren(roots[3]);
  const events = active.filter((item) => item.type === "timeline-event");
  assert.deepEqual(events.map((item) => item.event.sequence), [0, 1]);
  assert.equal(
    provider.getTreeItem(events[0]).command.command,
    "workbench.workspaceHome.inspectValue",
  );
  const owner = active.find((item) => item.type === "owner-reference");
  assert.equal(
    provider.getTreeItem(owner).command.command,
    "workbench.workspaceHome.inspectOwner",
  );
  assert.deepEqual(
    active.filter((item) => item.type === "work-session-operation")
      .map((item) => item.operation),
    ["resume", "close"],
  );
  const recovery = provider.getChildren(roots[4]);
  const recoveryAction = recovery.find((item) => item.type === "recovery-action");
  assert.equal(
    provider.getTreeItem(recoveryAction).command.command,
    "workbench.workspaceHome.inspectValue",
  );
  assert.deepEqual(
    recovery.filter((item) => item.type === "work-session-operation")
      .map((item) => item.operation),
    ["recovery-preview", "recovery-apply"],
  );
});

test("Home V2 regions expose exact Home, capability catalog, and session IDs", () => {
  const provider = new WorkspaceHomeV2TreeProvider(fakeVscode());
  const home = validateWorkspaceHomeV2(homeV2Fixture());
  const status = validateWorkSessionStatus(summaryFixture());
  provider.setHome(home);
  provider.setWorkSession({ status });
  const roots = provider.getChildren();
  const workspace = provider.getChildren(roots[0]);
  const environment = provider.getChildren(roots[1]);
  const active = provider.getChildren(roots[3]);
  assert.equal(workspace.find((item) => item.label === "Home ID").value, home.home_id);
  assert.equal(
    environment.find((item) => item.label === "Capability catalog ID").value,
    home.capability_catalog.catalog_id,
  );
  assert.equal(active.find((item) => item.label === "Session ID").value, status.session_id);
});

test("Home V2 tree rejects a valid session fixture belonging to another surface identity", () => {
  const provider = new WorkspaceHomeV2TreeProvider(fakeVscode());
  provider.setHome(validateWorkspaceHomeV2(homeV2Fixture()));
  const other = summaryFixture();
  other.session_id = `work-session-v2-${"6".repeat(32)}`;
  other.summary_id = workSessionContentId(
    "work-session-summary", other, "summary_id",
  );
  const validOther = validateWorkSessionStatus(other);
  assert.throws(() => provider.setWorkSession({ status: validOther }), /session identity|surface/i);
});

test("Home V2 tree accepts a post-mutation session only after Home refreshes its owner identity", () => {
  const provider = new WorkspaceHomeV2TreeProvider(fakeVscode());
  provider.setHome(validateWorkspaceHomeV2(homeV2Fixture()));
  const recordId = `work-session-record:sha256:${"5".repeat(64)}`;
  const statusFixture = summaryFixture();
  statusFixture.session_record_id = recordId;
  statusFixture.summary_id = workSessionContentId(
    "work-session-summary", statusFixture, "summary_id",
  );
  const status = validateWorkSessionStatus(statusFixture);
  assert.throws(() => provider.setWorkSession({ status }), /record identity/i);

  const refreshedFixture = homeV2Fixture();
  const sessionOwner = refreshedFixture.owner_records.find((row) => row.kind === "work-session");
  sessionOwner.record_id = recordId;
  refreshedFixture.session.record_id = recordId;
  refreshedFixture.home_id = homeContentId("workspace-home", refreshedFixture, "home_id");
  provider.setHome(validateWorkspaceHomeV2(refreshedFixture));
  assert.doesNotThrow(() => provider.setWorkSession({ status }));
  assert.equal(provider.workSession.status.session_record_id, recordId);
});
