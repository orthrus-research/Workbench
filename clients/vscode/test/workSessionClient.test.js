"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const test = require("node:test");

const {
  invokeWorkSessionArtifactEvents,
  invokeWorkSessionArtifactRange,
  invokeWorkSessionClose,
  invokeWorkSessionRecovery,
  invokeWorkSessionRecoveryApply,
  invokeWorkSessionResume,
  invokeWorkSessionStatus,
  invokeWorkSessionTimeline,
  validateRecoveryPreview,
  validateWorkSessionStatus,
  validateWorkSessionTimeline,
} = require("../workSessionClient");
const {
  contentId,
  recoveryFixture,
  summaryFixture,
  timelineFixture,
} = require("./workSessionFixtures");

const SOURCE_ROUTE_TIMEOUT_MS = 15 * 60 * 1000;

test("Work Session clients preserve owner identities and exact timeline order", () => {
  const status = validateWorkSessionStatus(summaryFixture());
  const timeline = validateWorkSessionTimeline(timelineFixture());
  const recovery = validateRecoveryPreview(recoveryFixture());
  assert.equal(status.session_id, timeline.session_id);
  assert.equal(status.session_id, recovery.session_id);
  assert.deepEqual(timeline.events.map((event) => event.sequence), [0, 1]);
  assert.equal(recovery.automatic, false);
  assert.deepEqual(recovery.safe_actions.map((item) => item.action_id), [
    "crucible.service.job-status",
  ]);
  assert.equal(Object.isFrozen(status), true);
});

test("Work Session status accepts an adopted generic workspace without selected profiles", () => {
  const generic = summaryFixture();
  generic.identities.platform_profile_id = null;
  generic.identities.pack_profile_id = null;
  generic.summary_id = contentId("work-session-summary", generic, "summary_id");
  const status = validateWorkSessionStatus(generic);
  assert.equal(status.identities.platform_profile_id, null);
  assert.equal(status.identities.pack_profile_id, null);
});

test("Work Session clients reject wrong formats and altered content/session identities", () => {
  const wrong = summaryFixture();
  wrong.format_version = "workbench-work-session-summary-v0";
  assert.throws(() => validateWorkSessionStatus(wrong), /format/i);

  const alteredSummary = summaryFixture();
  alteredSummary.lifecycle = "complete";
  assert.throws(() => validateWorkSessionStatus(alteredSummary), /identity/i);

  const alteredEvent = timelineFixture();
  alteredEvent.events[1].session_id = `work-session-v2-${"9".repeat(32)}`;
  assert.throws(() => validateWorkSessionTimeline(alteredEvent), /session ID/i);

  const brokenChain = timelineFixture();
  brokenChain.events[1].previous_event_id = `work-session-event:sha256:${"9".repeat(64)}`;
  assert.throws(() => validateWorkSessionTimeline(brokenChain), /chain|identity/i);
});

test("Recovery stays preview-only and fails closed on stale or corrupt owner state", () => {
  const automatic = recoveryFixture();
  automatic.automatic = true;
  assert.throws(() => validateRecoveryPreview(automatic), /automatic/i);

  const corrupt = recoveryFixture();
  corrupt.integrity.journal_state = "corrupt";
  corrupt.required = false;
  assert.throws(() => validateRecoveryPreview(corrupt), /corrupt|recovery/i);
});

test("Work Session invocation uses only public no-shell JSON routes", async () => {
  const original = childProcess.execFile;
  const calls = [];
  const outputs = [
    summaryFixture(), timelineFixture(), recoveryFixture(), summaryFixture(),
    summaryFixture(), summaryFixture(),
  ];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      callback(null, JSON.stringify(outputs.shift()), "");
    };
    const executable = "/opt/workbench/bin/workbench";
    const sessionId = summaryFixture().session_id;
    await invokeWorkSessionStatus(executable, sessionId);
    await invokeWorkSessionTimeline(executable, sessionId, { afterSequence: -1, limit: 64 });
    await invokeWorkSessionRecovery(executable, sessionId);
    await invokeWorkSessionResume(executable, sessionId, {
      workspace: "/workspace", expectedSequence: 7,
    });
    await invokeWorkSessionClose(executable, sessionId);
    await invokeWorkSessionRecoveryApply(executable, sessionId);
    assert.deepEqual(calls.map((call) => call.arguments_), [
      ["session", "status", sessionId, "--frontend", "vscode", "--json"],
      ["session", "timeline", sessionId, "--after-sequence", "-1", "--limit", "64",
        "--frontend", "vscode", "--json"],
      ["session", "recover", sessionId, "--frontend", "vscode", "--json"],
      ["session", "resume", sessionId, "--workspace", "/workspace",
        "--expected-sequence", "7",
        "--frontend", "vscode", "--json"],
      ["session", "close", sessionId, "--frontend", "vscode", "--json"],
      ["session", "recover", sessionId, "--apply", "--frontend", "vscode", "--json"],
    ]);
    assert.equal(calls.every((call) => call.options.shell === false), true);
  } finally {
    childProcess.execFile = original;
  }
});

test("Windows-hosted WSL Work Session routes map every path-bearing core argument", async () => {
  const original = childProcess.execFile;
  const calls = [];
  const sessionId = summaryFixture().session_id;
  const owner = {
    owner_id: "workbench-shell",
    record_id: "live-console-wsl-v1",
    record_kind: "workbench-live-console-session-v1",
    uri: "file:///home/developer/state/live-console-wsl-v1/session-v1.json",
    digest: `sha256:${"a".repeat(64)}`,
    last_verified_state: "complete",
    verified_at: "2026-08-21T12:01:00Z",
  };
  const launch = {
    configured: "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
    coreExecutable: "/opt/workbench/workbench",
    distribution: "Ubuntu",
    executable: "C:\\Windows\\System32\\wsl.exe",
    host: "windows-wsl",
    prefixArguments: [
      "--distribution", "Ubuntu", "--cd", "/opt/workbench", "--exec",
      "/opt/workbench/workbench",
    ],
  };
  const stateRoot = "\\\\wsl.localhost\\Ubuntu\\home\\developer\\state";
  const workspace = "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workspace";
  const artifactPage = {
    format_version: "workbench-owner-artifact-events-v1",
    session_id: sessionId,
    owner_record_id: owner.record_id,
    owner_digest: owner.digest,
    current_owner_digest: owner.digest,
    after_sequence: -1,
    limit: 16,
    events: [],
    has_more: false,
    next_after_sequence: -1,
  };
  const outputs = [summaryFixture(), summaryFixture(), artifactPage];
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      calls.push({ executable, arguments_, options });
      callback(null, JSON.stringify(outputs.shift()), "");
    };
    await invokeWorkSessionStatus(launch.configured, sessionId, { launch, stateRoot });
    await invokeWorkSessionResume(launch.configured, sessionId, {
      launch, stateRoot, workspace,
    });
    await invokeWorkSessionArtifactEvents(
      launch.configured, sessionId, owner,
      { launch, stateRoot, afterSequence: -1, limit: 16 },
    );
    const prefix = launch.prefixArguments;
    assert.deepEqual(calls.map((call) => call.arguments_), [
      [...prefix, "session", "status", sessionId,
        "--state-root", "/home/developer/state", "--frontend", "vscode", "--json"],
      [...prefix, "session", "resume", sessionId,
        "--workspace", "/home/developer/workspace",
        "--state-root", "/home/developer/state", "--frontend", "vscode", "--json"],
      [...prefix, "session", "artifact", sessionId, owner.record_id, owner.digest,
        "--after-sequence", "-1", "--limit", "16",
        "--state-root", "/home/developer/state", "--frontend", "vscode", "--json"],
    ]);
    assert.equal(calls.every((call) => call.executable === launch.executable), true);
    assert.equal(calls.every((call) => !call.arguments_.some(
      (argument) => argument.includes("wsl.localhost"),
    )), true);
    await assert.rejects(
      invokeWorkSessionStatus(launch.configured, sessionId, {
        launch,
        stateRoot: "\\\\wsl.localhost\\Debian\\home\\developer\\state",
      }),
      /distribution/i,
    );
    await assert.rejects(
      invokeWorkSessionResume(launch.configured, sessionId, {
        launch,
        workspace: "C:\\Users\\developer\\workspace",
      }),
      /distribution/i,
    );
  } finally {
    childProcess.execFile = original;
  }
});

test("installed VS Code adapter retains exact frontend events through the public core port", async () => {
  const repository = path.resolve(__dirname, "../../..");
  const temporary = await fs.mkdtemp("/tmp/workbench-vscode-release-adapter-");
  const stateRoot = path.join(temporary, "product-spine");
  const createScript = `
import pathlib, runpy, sys
root = pathlib.Path(sys.argv[1]).resolve()
state = pathlib.Path(sys.argv[2]).resolve()
runpy.run_path(str(root / "tools/workbench.py"), run_name="workbench_vscode_release_adapter_fixture")
from workbench_shell.work_session import WorkSessionStore
created = WorkSessionStore(state).create(
    task={"task_id": "task:vscode-release-adapter", "owner_id": "workbench-shell"},
    workspace={
        "identity_id": "workspace:vscode-release-adapter",
        "canonical_root": str(root),
        "source_revision": "git:vscode-release-adapter",
        "dirty_fingerprint": "sha256:" + "1" * 64,
    },
    identities={
        "core_id": "core:vscode-release-adapter",
        "catalog_id": "sha256:" + "2" * 64,
        "host_adapter_id": "host:native",
        "platform_profile_id": "platform:cleanroom",
        "pack_profile_id": None,
    },
    frontend={"frontend_id": "workbench-cli", "kind": "cli", "version": "0.1.0"},
    lifecycle="discovered",
)
print(created["session_id"])
`;
  try {
    const created = childProcess.execFileSync(
      "python3", ["-c", createScript, repository, stateRoot],
      { cwd: repository, encoding: "utf8", maxBuffer: 8 * 1024 * 1024 },
    );
    const sessionId = created.trim();
    const launch = {
      configured: "python3",
      coreExecutable: path.join(repository, "tools/workbench.py"),
      distribution: null,
      executable: "python3",
      host: "native",
      prefixArguments: [path.join(repository, "tools/workbench.py")],
    };
    const options = {
      cwd: repository,
      launch,
      stateRoot,
      timeoutMs: SOURCE_ROUTE_TIMEOUT_MS,
    };
    await invokeWorkSessionResume("python3", sessionId, options);
    await invokeWorkSessionClose("python3", sessionId, options);
    const timeline = await invokeWorkSessionTimeline("python3", sessionId, {
      ...options, afterSequence: -1, limit: 32,
    });
    assert.deepEqual(timeline.events.slice(-2).map((event) => event.kind), [
      "frontend-reopened", "session-closed",
    ]);
    assert.deepEqual(timeline.events.slice(-2).map((event) => event.frontend.kind), [
      "vscode", "vscode",
    ]);
    assert.deepEqual(timeline.events.slice(-2).map((event) => event.frontend.frontend_id), [
      "workbench-vscode", "workbench-vscode",
    ]);
  } finally {
    await fs.rm(temporary, { recursive: true, force: true });
  }
});

test("installed VS Code opens only the exact owner-retained raw byte range", async () => {
  const repository = path.resolve(__dirname, "../../..");
  const temporary = await fs.mkdtemp("/tmp/workbench-vscode-release-raw-");
  const createScript = `
import json, pathlib, runpy, sys
root = pathlib.Path(sys.argv[1]).resolve()
state = pathlib.Path(sys.argv[2]).resolve()
runpy.run_path(str(root / "tools/workbench.py"), run_name="workbench_vscode_release_raw_fixture")
from workbench_core.sessions import RetainedSession, live_console_owner_reference
from workbench_shell.work_session import WorkSessionStore
session = RetainedSession(root=state, command_id="fixture.raw-range", argv=["fixture"], cwd=root, intent="inspect", session_id="live-console-raw-v1")
historical = live_console_owner_reference(state, session.session_id)
store = WorkSessionStore(state)
created = store.create(
    task={"task_id": "task:vscode-release-artifact", "owner_id": "workbench-shell"},
    workspace={"identity_id": "workspace:vscode-release-artifact", "canonical_root": str(root), "source_revision": "git:vscode-release-artifact", "dirty_fingerprint": "sha256:" + "1" * 64},
    identities={"core_id": "core:vscode-release-artifact", "catalog_id": "sha256:" + "2" * 64, "host_adapter_id": "host:native", "platform_profile_id": "platform:cleanroom", "pack_profile_id": None},
    frontend={"frontend_id": "workbench-test", "kind": "test", "version": "0.1.0"},
)
store._append_event(
    created["session_id"], expected_sequence=0,
    frontend={"frontend_id": "workbench-test", "kind": "test", "version": "0.1.0"},
    kind="owner-execution-bound", lifecycle="running",
    stage={"stage_id": "owner-execution", "state": "running"},
    owner_record_refs=[historical],
    _terminal_owner_authorized=False, _owner_custody_authorized=True,
)
locator = session.write_raw("stdout", b"needle\\n")
session.record_event({
    "format_version": "workbench-live-console-event-v1", "event_id": "event:1",
    "sequence": 1, "ingested_at": "2026-08-21T12:00:00Z", "monotonic_ns": 1,
    "source_timestamp": None, "source": "fixture", "stream": "stdout",
    "raw_locator": {"artifact": locator.path, "byte_start": locator.byte_start, "byte_end": locator.byte_end, "line": 1, "chunk": 1, "boundary": "lf"},
    "kind": "text", "severity": "unknown", "subsystem": "generic", "logger": None,
    "thread": None, "message": "needle", "parse_provenance": "raw",
    "classification_basis": [], "cluster_key": "sha256:" + "0" * 64,
    "signal": True, "outcome_failure": False, "source_locators": [], "limitations": [],
})
session.finish(state="complete", process_exit_code=0, effective_exit_code=0, outcome="complete")
print(json.dumps({"session_id": created["session_id"], "owner": historical}, sort_keys=True))
`;
  try {
    const fixture = JSON.parse(childProcess.execFileSync(
      "python3", ["-c", createScript, repository, temporary],
      { cwd: repository, encoding: "utf8", maxBuffer: 8 * 1024 * 1024 },
    ));
    const launch = {
      configured: "python3",
      coreExecutable: path.join(repository, "tools/workbench.py"),
      distribution: null,
      executable: "python3",
      host: "native",
      prefixArguments: [path.join(repository, "tools/workbench.py")],
    };
    const options = { cwd: repository, launch, stateRoot: temporary };
    const page = await invokeWorkSessionArtifactEvents(
      "python3", fixture.session_id, fixture.owner,
      { ...options, afterSequence: -1, limit: 1024 },
    );
    assert.equal(page.events.length, 1);
    assert.deepEqual(page.events[0], {
      event_id: "event:1", sequence: 1, kind: "text",
      severity: "unknown", subsystem: "generic", message: "needle", stream: "stdout",
      artifact: "stdout.raw", byte_start: 0, byte_end: 7, boundary: "lf",
    });
    assert.notEqual(page.current_owner_digest, fixture.owner.digest);
    const range = await invokeWorkSessionArtifactRange(
      "python3", fixture.session_id, fixture.owner, page.events[0].event_id, options,
    );
    assert.equal(Buffer.from(range.content_base64, "base64").toString("utf8"), "needle\n");
    assert.equal(range.content_sha256, `sha256:${crypto.createHash("sha256").update("needle\n").digest("hex")}`);

    const rawPath = path.join(temporary, ".workbench", "sessions", "live-console",
      "live-console-raw-v1", "stdout.raw");
    const retained = `${rawPath}.retained`;
    await fs.rename(rawPath, retained);
    await fs.symlink(retained, rawPath);
    await assert.rejects(
      invokeWorkSessionArtifactRange(
        "python3", fixture.session_id, fixture.owner, page.events[0].event_id, options,
      ),
      /sealed index|unsafe|artifact/i,
    );
  } finally {
    await fs.rm(temporary, { recursive: true, force: true });
  }
});
