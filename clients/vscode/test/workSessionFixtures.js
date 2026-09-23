"use strict";

const crypto = require("node:crypto");

function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${canonical(value[key])}`
  )).join(",")}}`;
}

function contentId(prefix, value, excluded) {
  const material = structuredClone(value);
  delete material[excluded];
  return `${prefix}:sha256:${crypto.createHash("sha256").update(canonical(material)).digest("hex")}`;
}

function ownerRef() {
  return {
    owner_id: "crucible",
    record_id: "job-v2:0123456789abcdef0123456789abcdef",
    record_kind: "durable-job",
    uri: "workbench://crucible/jobs/job-v2:0123456789abcdef0123456789abcdef",
    digest: `sha256:${"a".repeat(64)}`,
    last_verified_state: "running",
    verified_at: "2026-08-21T12:01:00Z",
  };
}

function action() {
  return {
    action_id: "crucible.service.job-status",
    action_digest: `sha256:${"b".repeat(64)}`,
    owner_id: "crucible",
    availability: "available",
    mutation_budget: "read-only",
    arguments: { job_id: ownerRef().record_id },
  };
}

function summaryFixture() {
  const ref = ownerRef();
  const next = action();
  const value = {
    format_version: "workbench-work-session-summary-v1",
    summary_id: "pending",
    session_id: `work-session-v2-${"1".repeat(32)}`,
    session_record_id: `work-session-record:sha256:${"2".repeat(64)}`,
    task: {
      task_id: "task:fixture",
      owner_id: "workbench-shell",
      label: "Fixture task",
      owner_record_ref: null,
    },
    workspace: {
      identity_id: "workspace:fixture",
      canonical_root: "/workspace",
      root_uri: "file:///workspace",
      source_revision: "git:fixture",
      dirty_fingerprint: `sha256:${"3".repeat(64)}`,
    },
    identities: {
      core_id: "core:fixture",
      catalog_id: `sha256:${"4".repeat(64)}`,
      host_adapter_id: "host:native",
      platform_profile_id: "platform:cleanroom",
      pack_profile_id: null,
    },
    lifecycle: "recoverable",
    created_at: "2026-08-21T12:00:00Z",
    updated_at: "2026-08-21T12:01:00Z",
    latest_sequence: 1,
    latest_event_id: `work-session-event:sha256:${"5".repeat(64)}`,
    event_count: 2,
    closed: false,
    frontend_ids: ["frontend:cli", "frontend:vscode"],
    owner_record_refs: [ref],
    result_refs: [{ result_id: "result:fixture", owner_record_ref: ref }],
    problems: [{
      problem_id: "problem:fixture",
      code: "owner.interrupted",
      severity: "warning",
      message: "The owner job requires inspection.",
      affected_identity: ref.record_id,
      evidence_refs: [ref],
      next_action_id: next.action_id,
    }],
    next_actions: [next],
    recovery: {
      state: "required",
      reason: "The frontend exited while owner work was active.",
      owner_record_refs: [ref],
      safe_action_ids: [next.action_id],
    },
    limitations: ["Recovery is explicit."],
    unknowns: [],
    integrity_state: "verified",
    integrity_problem_ids: [],
  };
  value.summary_id = contentId("work-session-summary", value, "summary_id");
  return value;
}

function eventFixture(summary, sequence, previousEventId) {
  const value = {
    format_version: "workbench-work-session-event-v1",
    event_id: "pending",
    session_id: summary.session_id,
    session_record_id: summary.session_record_id,
    sequence,
    previous_event_id: previousEventId,
    occurred_at: sequence === 0 ? summary.created_at : summary.updated_at,
    frontend: {
      frontend_id: sequence === 0 ? "frontend:cli" : "frontend:vscode",
      kind: sequence === 0 ? "cli" : "vscode",
      version: "0.1.0",
      instance_id: null,
      process_id: null,
    },
    kind: sequence === 0 ? "session-created" : "recovery-required",
    task_id: summary.task.task_id,
    lifecycle: sequence === 0 ? "discovered" : "recoverable",
    stage: null,
    action: null,
    result_refs: [],
    problems: sequence === 0 ? [] : summary.problems,
    next_actions: sequence === 0 ? [] : summary.next_actions,
    owner_record_refs: sequence === 0 ? [] : summary.owner_record_refs,
    recovery: sequence === 0 ? null : summary.recovery,
    workspace_observation: sequence === 0 ? summary.workspace : null,
    closed: false,
    message: null,
    limitations: sequence === 0 ? summary.limitations : [],
    unknowns: [],
  };
  value.event_id = contentId("work-session-event", value, "event_id");
  return value;
}

function timelineFixture() {
  const summary = summaryFixture();
  const first = eventFixture(summary, 0, null);
  const second = eventFixture(summary, 1, first.event_id);
  return {
    format_version: "workbench-work-session-timeline-v1",
    session_id: summary.session_id,
    after_sequence: -1,
    events: [first, second],
    next_sequence: 1,
    has_more: false,
    integrity: {
      state: "verified",
      journal_state: "verified",
      summary_state: "verified",
      problems: [],
    },
  };
}

function recoveryFixture() {
  const summary = summaryFixture();
  return {
    format_version: "workbench-work-session-recovery-preview-v1",
    session_id: summary.session_id,
    session_record_id: summary.session_record_id,
    latest_sequence: summary.latest_sequence,
    required: true,
    automatic: false,
    reason: summary.recovery.reason,
    owner_record_refs: summary.owner_record_refs,
    owner_resolution: "verified",
    owner_resolution_problems: [],
    safe_actions: summary.next_actions,
    integrity: {
      state: "verified",
      journal_state: "verified",
      summary_state: "verified",
      problems: [],
    },
  };
}

module.exports = {
  contentId,
  recoveryFixture,
  summaryFixture,
  timelineFixture,
};
