"use strict";

const crypto = require("node:crypto");

const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");

const SUMMARY_FORMAT = "workbench-work-session-summary-v1";
const EVENT_FORMAT = "workbench-work-session-event-v1";
const TIMELINE_FORMAT = "workbench-work-session-timeline-v1";
const RECOVERY_FORMAT = "workbench-work-session-recovery-preview-v1";
const SESSION_ID = /^work-session-v2-[0-9a-f]{32}$/;
const RECORD_ID = /^work-session-record:sha256:[0-9a-f]{64}$/;
const SUMMARY_ID = /^work-session-summary:sha256:[0-9a-f]{64}$/;
const EVENT_ID = /^work-session-event:sha256:[0-9a-f]{64}$/;
const PLAIN_ID = /^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;
const LIFECYCLES = new Set([
  "discovered", "ready", "attention", "blocked", "running", "complete",
  "failed", "cancelled", "incomplete", "recoverable",
]);
const FRONTENDS = new Set(["cli", "vscode", "intellij-community", "service", "test"]);
const AVAILABILITIES = new Set([
  "available", "unavailable", "experimental", "blocked", "stale", "unknown",
]);
const MUTATION_BUDGETS = new Set(["read-only", "writes-output", "mutating", "destructive"]);
const PROBLEM_SEVERITIES = new Set(["info", "warning", "error", "fatal"]);
const MAX_ROWS = 4096;
const MAX_TEXT_BYTES = 256 * 1024;
const ADAPTER_FRONTEND = "vscode";
const LIVE_CONSOLE_FORMAT = "workbench-live-console-session-v1";
const LIVE_CONSOLE_RECORD_ID = /^[A-Za-z0-9._-]{8,120}$/;
const LIVE_CONSOLE_EVENT_ID = PLAIN_ID;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const LIVE_CONSOLE_STREAM = /^[a-z][a-z0-9-]{0,47}$/;
const LIVE_CONSOLE_BOUNDARIES = new Set(["lf", "crlf", "cr", "limit", "eof"]);
const MAX_LIVE_CONSOLE_EVENT_PAGE = 1024;
const MAX_LIVE_CONSOLE_RAW_RANGE_BYTES = 8 * 1024 * 1024;

function ordinaryObject(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, required, label) {
  const actual = new Set(Object.keys(value));
  const missing = required.filter((key) => !actual.has(key));
  const extra = [...actual].filter((key) => !required.includes(key));
  if (missing.length || extra.length) {
    throw new Error(`${label} fields changed; missing=${missing.join(",")}; extra=${extra.join(",")}`);
  }
}

function boundedText(value, label, { nullable = false, singleLine = true } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !value || value.includes("\0")
      || (singleLine && /[\r\n]/.test(value))
      || Buffer.byteLength(value, "utf8") > MAX_TEXT_BYTES) {
    throw new Error(`${label} must be bounded nonempty text`);
  }
  return value;
}

function identifier(value, label, { nullable = false, pattern = PLAIN_ID } = {}) {
  const selected = boundedText(value, label, { nullable });
  if (selected !== null && !pattern.test(selected)) throw new Error(`${label} is invalid`);
  return selected;
}

function timestamp(value, label, { nullable = false } = {}) {
  const selected = boundedText(value, label, { nullable });
  if (selected === null) return null;
  if (!TIMESTAMP.test(selected) || Number.isNaN(Date.parse(selected))) {
    throw new Error(`${label} is not an RFC 3339 UTC timestamp`);
  }
  return selected;
}

function boolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
  return value;
}

function integer(value, label, { minimum = 0, maximum = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside its supported bound`);
  }
  return value;
}

function member(value, choices, label) {
  if (!choices.has(value)) throw new Error(`${label} is unsupported`);
  return value;
}

function rows(value, label, maximum = MAX_ROWS) {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new Error(`${label} is outside its supported bound`);
  }
  return value;
}

function stringRows(value, label, maximum = 256) {
  return rows(value, label, maximum).map((item, index) => (
    boundedText(item, `${label}[${index}]`)
  ));
}

function canonicalJson(value) {
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Work Session identity contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const selected = ordinaryObject(value, "Work Session identity material");
  return `{${Object.keys(selected).sort().map((key) => (
    `${JSON.stringify(key)}:${canonicalJson(selected[key])}`
  )).join(",")}}`;
}

function verifyContentIdentity(value, field, prefix, pattern, label) {
  const supplied = identifier(value[field], `${label} identity`, { pattern });
  const body = { ...value };
  delete body[field];
  const digest = crypto.createHash("sha256").update(canonicalJson(body)).digest("hex");
  if (supplied !== `${prefix}:sha256:${digest}`) {
    throw new Error(`${label} content identity changed`);
  }
}

function freeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

function validateOwnerReference(value, label = "owner record reference") {
  const ref = ordinaryObject(value, label);
  exactKeys(ref, [
    "owner_id", "record_id", "record_kind", "uri", "digest",
    "last_verified_state", "verified_at",
  ], label);
  const uri = boundedText(ref.uri, `${label} URI`);
  if (!uri.includes(":")) throw new Error(`${label} URI has no scheme`);
  return {
    owner_id: identifier(ref.owner_id, `${label} owner ID`),
    record_id: identifier(ref.record_id, `${label} record ID`),
    record_kind: identifier(ref.record_kind, `${label} kind`),
    uri,
    digest: identifier(ref.digest, `${label} digest`, { nullable: true }),
    last_verified_state: identifier(
      ref.last_verified_state, `${label} verified state`, { nullable: true },
    ),
    verified_at: timestamp(ref.verified_at, `${label} verified time`, { nullable: true }),
  };
}

function validateOwnerReferences(value, label) {
  const result = rows(value, label, 256).map((item, index) => (
    validateOwnerReference(item, `${label}[${index}]`)
  ));
  if (new Set(result.map((item) => item.record_id)).size !== result.length) {
    throw new Error(`${label} contains duplicate owner identities`);
  }
  return result;
}

function validateTask(value) {
  const task = ordinaryObject(value, "Work Session task");
  exactKeys(task, ["task_id", "owner_id", "label", "owner_record_ref"], "Work Session task");
  return {
    task_id: identifier(task.task_id, "Work Session task ID"),
    owner_id: identifier(task.owner_id, "Work Session task owner ID"),
    label: boundedText(task.label, "Work Session task label", { nullable: true }),
    owner_record_ref: task.owner_record_ref === null
      ? null : validateOwnerReference(task.owner_record_ref, "Work Session task owner reference"),
  };
}

function validateWorkspace(value) {
  const workspace = ordinaryObject(value, "Work Session workspace");
  exactKeys(workspace, [
    "identity_id", "canonical_root", "root_uri", "source_revision", "dirty_fingerprint",
  ], "Work Session workspace");
  const root = boundedText(workspace.canonical_root, "Work Session workspace root");
  if (!root.startsWith("/")) throw new Error("Work Session workspace root must be absolute");
  return {
    identity_id: identifier(workspace.identity_id, "Work Session workspace ID"),
    canonical_root: root,
    root_uri: boundedText(workspace.root_uri, "Work Session workspace URI"),
    source_revision: identifier(
      workspace.source_revision, "Work Session source revision", { nullable: true },
    ),
    dirty_fingerprint: identifier(
      workspace.dirty_fingerprint, "Work Session dirty fingerprint", { nullable: true },
    ),
  };
}

function validateIdentities(value) {
  const identities = ordinaryObject(value, "Work Session identities");
  exactKeys(identities, [
    "core_id", "catalog_id", "host_adapter_id", "platform_profile_id", "pack_profile_id",
  ], "Work Session identities");
  return {
    core_id: identifier(identities.core_id, "Work Session core ID"),
    catalog_id: identifier(identities.catalog_id, "Work Session catalog ID"),
    host_adapter_id: identifier(identities.host_adapter_id, "Work Session host adapter ID"),
    platform_profile_id: identifier(
      identities.platform_profile_id, "Work Session platform profile ID", { nullable: true },
    ),
    pack_profile_id: identifier(
      identities.pack_profile_id, "Work Session pack profile ID", { nullable: true },
    ),
  };
}

function validateAction(value, label = "Work Session action") {
  const action = ordinaryObject(value, label);
  exactKeys(action, [
    "action_id", "action_digest", "owner_id", "availability", "mutation_budget", "arguments",
  ], label);
  ordinaryObject(action.arguments, `${label} arguments`);
  return {
    action_id: identifier(action.action_id, `${label} ID`),
    action_digest: identifier(action.action_digest, `${label} digest`),
    owner_id: identifier(action.owner_id, `${label} owner ID`),
    availability: member(action.availability, AVAILABILITIES, `${label} availability`),
    mutation_budget: member(action.mutation_budget, MUTATION_BUDGETS, `${label} mutation budget`),
    arguments: structuredClone(action.arguments),
  };
}

function validateActions(value, label) {
  const result = rows(value, label, 128).map((item, index) => (
    validateAction(item, `${label}[${index}]`)
  ));
  if (new Set(result.map((item) => item.action_id)).size !== result.length) {
    throw new Error(`${label} contains duplicate action identities`);
  }
  return result;
}

function validateResultReferences(value) {
  const result = rows(value, "Work Session result references", 256).map((item, index) => {
    const row = ordinaryObject(item, `Work Session result reference ${index}`);
    exactKeys(row, ["result_id", "owner_record_ref"], `Work Session result reference ${index}`);
    return {
      result_id: identifier(row.result_id, `Work Session result reference ${index} ID`),
      owner_record_ref: validateOwnerReference(
        row.owner_record_ref, `Work Session result reference ${index} owner record`,
      ),
    };
  });
  if (new Set(result.map((item) => item.result_id)).size !== result.length) {
    throw new Error("Work Session result references contain duplicate identities");
  }
  return result;
}

function validateProblems(value) {
  const result = rows(value, "Work Session problems", 256).map((item, index) => {
    const problem = ordinaryObject(item, `Work Session problem ${index}`);
    exactKeys(problem, [
      "problem_id", "code", "severity", "message", "affected_identity",
      "evidence_refs", "next_action_id",
    ], `Work Session problem ${index}`);
    return {
      problem_id: identifier(problem.problem_id, `Work Session problem ${index} ID`),
      code: identifier(problem.code, `Work Session problem ${index} code`),
      severity: member(
        problem.severity, PROBLEM_SEVERITIES, `Work Session problem ${index} severity`,
      ),
      message: boundedText(problem.message, `Work Session problem ${index} message`),
      affected_identity: identifier(
        problem.affected_identity, `Work Session problem ${index} affected identity`, { nullable: true },
      ),
      evidence_refs: validateOwnerReferences(
        problem.evidence_refs, `Work Session problem ${index} evidence references`,
      ),
      next_action_id: identifier(
        problem.next_action_id, `Work Session problem ${index} next action ID`, { nullable: true },
      ),
    };
  });
  if (new Set(result.map((item) => item.problem_id)).size !== result.length) {
    throw new Error("Work Session problems contain duplicate identities");
  }
  return result;
}

function validateRecovery(value, label = "Work Session recovery") {
  if (value === null) return null;
  const recovery = ordinaryObject(value, label);
  exactKeys(recovery, ["state", "reason", "owner_record_refs", "safe_action_ids"], label);
  const state = member(recovery.state, new Set(["required", "recovering", "resolved"]), `${label} state`);
  return {
    state,
    reason: boundedText(recovery.reason, `${label} reason`),
    owner_record_refs: validateOwnerReferences(recovery.owner_record_refs, `${label} owner references`),
    safe_action_ids: stringRows(recovery.safe_action_ids, `${label} safe action IDs`, 128)
      .map((item, index) => identifier(item, `${label} safe action ID ${index}`)),
  };
}

function validateIntegrity(value) {
  const integrity = ordinaryObject(value, "Work Session integrity");
  exactKeys(integrity, ["state", "journal_state", "summary_state", "problems"], "Work Session integrity");
  const journalState = member(
    integrity.journal_state, new Set(["verified", "recoverable", "corrupt"]),
    "Work Session journal integrity",
  );
  const summaryState = member(
    integrity.summary_state, new Set(["verified", "summary-regenerated"]),
    "Work Session summary integrity",
  );
  const state = member(
    integrity.state, new Set(["verified", "summary-regenerated", "corrupt"]),
    "Work Session aggregate integrity",
  );
  if ((journalState === "corrupt") !== (state === "corrupt")) {
    throw new Error("Work Session corrupt integrity is internally inconsistent");
  }
  if (journalState !== "corrupt" && state !== summaryState) {
    throw new Error("Work Session summary integrity is internally inconsistent");
  }
  return {
    state,
    journal_state: journalState,
    summary_state: summaryState,
    problems: validateProblems(integrity.problems),
  };
}

function validateSummary(value) {
  const summary = ordinaryObject(value, "Work Session status");
  exactKeys(summary, [
    "format_version", "summary_id", "session_id", "session_record_id", "task",
    "workspace", "identities", "lifecycle", "created_at", "updated_at",
    "latest_sequence", "latest_event_id", "event_count", "closed", "frontend_ids",
    "owner_record_refs", "result_refs", "problems", "next_actions", "recovery",
    "limitations", "unknowns", "integrity_state", "integrity_problem_ids",
  ], "Work Session status");
  if (summary.format_version !== SUMMARY_FORMAT) throw new Error("Work Session status format is unsupported");
  const eventCount = integer(summary.event_count, "Work Session event count", { maximum: 100_000 });
  const latestSequence = integer(summary.latest_sequence, "Work Session latest sequence", { minimum: -1 });
  if ((eventCount === 0 && latestSequence !== -1)
      || (eventCount > 0 && latestSequence !== eventCount - 1)) {
    throw new Error("Work Session event counters are inconsistent");
  }
  const latestEventId = identifier(
    summary.latest_event_id, "Work Session latest event ID", { nullable: true, pattern: EVENT_ID },
  );
  if ((eventCount === 0) !== (latestEventId === null)) {
    throw new Error("Work Session latest event identity is inconsistent");
  }
  const nextActions = validateActions(summary.next_actions, "Work Session next actions");
  const recovery = validateRecovery(summary.recovery);
  if (summary.lifecycle === "recoverable") {
    if (!recovery || recovery.state !== "required" || recovery.safe_action_ids.length < 1) {
      throw new Error("recoverable Work Session lacks exact recovery actions");
    }
  }
  if (recovery) {
    const available = new Set(nextActions.map((item) => item.action_id));
    if (recovery.safe_action_ids.some((item) => !available.has(item))) {
      throw new Error("Work Session recovery references an absent safe action");
    }
  }
  verifyContentIdentity(summary, "summary_id", "work-session-summary", SUMMARY_ID, "Work Session summary");
  return freeze({
    format_version: SUMMARY_FORMAT,
    summary_id: summary.summary_id,
    session_id: identifier(summary.session_id, "Work Session ID", { pattern: SESSION_ID }),
    session_record_id: identifier(summary.session_record_id, "Work Session record ID", { pattern: RECORD_ID }),
    task: validateTask(summary.task),
    workspace: validateWorkspace(summary.workspace),
    identities: validateIdentities(summary.identities),
    lifecycle: member(summary.lifecycle, LIFECYCLES, "Work Session lifecycle"),
    created_at: timestamp(summary.created_at, "Work Session creation time"),
    updated_at: timestamp(summary.updated_at, "Work Session update time"),
    latest_sequence: latestSequence,
    latest_event_id: latestEventId,
    event_count: eventCount,
    closed: boolean(summary.closed, "Work Session closed state"),
    frontend_ids: stringRows(summary.frontend_ids, "Work Session frontend IDs")
      .map((item, index) => identifier(item, `Work Session frontend ID ${index}`)),
    owner_record_refs: validateOwnerReferences(summary.owner_record_refs, "Work Session owner references"),
    result_refs: validateResultReferences(summary.result_refs),
    problems: validateProblems(summary.problems),
    next_actions: nextActions,
    recovery,
    limitations: stringRows(summary.limitations, "Work Session limitations", 128),
    unknowns: stringRows(summary.unknowns, "Work Session unknowns", 128),
    integrity_state: member(
      summary.integrity_state, new Set(["verified", "recoverable", "corrupt"]),
      "Work Session integrity state",
    ),
    integrity_problem_ids: stringRows(
      summary.integrity_problem_ids, "Work Session integrity problem IDs", 256,
    ).map((item, index) => identifier(item, `Work Session integrity problem ID ${index}`)),
  });
}

function validateEvent(value, expected = {}) {
  const event = ordinaryObject(value, "Work Session event");
  exactKeys(event, [
    "format_version", "event_id", "session_id", "session_record_id", "sequence",
    "previous_event_id", "occurred_at", "frontend", "kind", "task_id", "lifecycle",
    "stage", "action", "result_refs", "problems", "next_actions", "owner_record_refs",
    "recovery", "workspace_observation", "closed", "message", "limitations", "unknowns",
  ], "Work Session event");
  if (event.format_version !== EVENT_FORMAT) throw new Error("Work Session event format is unsupported");
  const sessionId = identifier(event.session_id, "Work Session event session ID", { pattern: SESSION_ID });
  if (expected.sessionId && sessionId !== expected.sessionId) {
    throw new Error("Work Session event session ID changed");
  }
  const sequence = integer(event.sequence, "Work Session event sequence", { maximum: 99_999 });
  if (expected.sequence !== undefined && sequence !== expected.sequence) {
    throw new Error("Work Session event sequence changed");
  }
  const previous = identifier(
    event.previous_event_id, "Work Session previous event ID", { nullable: true, pattern: EVENT_ID },
  );
  if (expected.previousEventId !== undefined && previous !== expected.previousEventId) {
    throw new Error("Work Session event chain changed");
  }
  const frontend = ordinaryObject(event.frontend, "Work Session frontend");
  exactKeys(frontend, ["frontend_id", "kind", "version", "instance_id", "process_id"], "Work Session frontend");
  if (frontend.process_id !== null) integer(frontend.process_id, "Work Session frontend process ID", { minimum: 1 });
  const nextActions = validateActions(event.next_actions, "Work Session event next actions");
  const recovery = validateRecovery(event.recovery, "Work Session event recovery");
  if (event.lifecycle === "recoverable" && (!recovery || recovery.state !== "required")) {
    throw new Error("recoverable Work Session event lacks required recovery");
  }
  if (recovery) {
    const available = new Set(nextActions.map((item) => item.action_id));
    if (recovery.safe_action_ids.some((item) => !available.has(item))) {
      throw new Error("Work Session event recovery references an absent safe action");
    }
  }
  let stage = null;
  if (event.stage !== null) {
    const row = ordinaryObject(event.stage, "Work Session stage");
    exactKeys(row, ["stage_id", "state"], "Work Session stage");
    stage = {
      stage_id: identifier(row.stage_id, "Work Session stage ID"),
      state: member(row.state, new Set([
        "pending", "running", "complete", "failed", "cancelled", "incomplete", "blocked",
      ]), "Work Session stage state"),
    };
  }
  verifyContentIdentity(event, "event_id", "work-session-event", EVENT_ID, "Work Session event");
  return freeze({
    format_version: EVENT_FORMAT,
    event_id: event.event_id,
    session_id: sessionId,
    session_record_id: identifier(event.session_record_id, "Work Session event record ID", { pattern: RECORD_ID }),
    sequence,
    previous_event_id: previous,
    occurred_at: timestamp(event.occurred_at, "Work Session event time"),
    frontend: {
      frontend_id: identifier(frontend.frontend_id, "Work Session frontend ID"),
      kind: member(frontend.kind, FRONTENDS, "Work Session frontend kind"),
      version: boundedText(frontend.version, "Work Session frontend version"),
      instance_id: identifier(frontend.instance_id, "Work Session frontend instance ID", { nullable: true }),
      process_id: frontend.process_id,
    },
    kind: identifier(event.kind, "Work Session event kind"),
    task_id: identifier(event.task_id, "Work Session event task ID"),
    lifecycle: member(event.lifecycle, LIFECYCLES, "Work Session event lifecycle"),
    stage,
    action: event.action === null ? null : validateAction(event.action, "Work Session event action"),
    result_refs: validateResultReferences(event.result_refs),
    problems: validateProblems(event.problems),
    next_actions: nextActions,
    owner_record_refs: validateOwnerReferences(event.owner_record_refs, "Work Session event owner references"),
    recovery,
    workspace_observation: event.workspace_observation === null
      ? null : validateWorkspace(event.workspace_observation),
    closed: boolean(event.closed, "Work Session event closed state"),
    message: boundedText(event.message, "Work Session event message", { nullable: true }),
    limitations: stringRows(event.limitations, "Work Session event limitations", 128),
    unknowns: stringRows(event.unknowns, "Work Session event unknowns", 128),
  });
}

function validateWorkSessionStatus(value) {
  return validateSummary(value);
}

function validateWorkSessionTimeline(value) {
  const timeline = ordinaryObject(value, "Work Session timeline");
  exactKeys(timeline, [
    "format_version", "session_id", "after_sequence", "events", "next_sequence",
    "has_more", "integrity",
  ], "Work Session timeline");
  if (timeline.format_version !== TIMELINE_FORMAT) throw new Error("Work Session timeline format is unsupported");
  const sessionId = identifier(timeline.session_id, "Work Session timeline session ID", { pattern: SESSION_ID });
  const afterSequence = integer(timeline.after_sequence, "Work Session timeline cursor", { minimum: -1 });
  let previousEventId;
  const events = rows(timeline.events, "Work Session timeline events", 4096).map((item, index) => {
    const sequence = afterSequence + index + 1;
    const expected = { sessionId, sequence };
    if (index > 0 || afterSequence === -1) expected.previousEventId = previousEventId ?? null;
    const parsed = validateEvent(item, expected);
    previousEventId = parsed.event_id;
    return parsed;
  });
  const expectedNext = events.length ? events.at(-1).sequence : afterSequence;
  const nextSequence = integer(timeline.next_sequence, "Work Session timeline next cursor", { minimum: -1 });
  if (nextSequence !== expectedNext) throw new Error("Work Session timeline cursor is inconsistent");
  return freeze({
    format_version: TIMELINE_FORMAT,
    session_id: sessionId,
    after_sequence: afterSequence,
    events,
    next_sequence: nextSequence,
    has_more: boolean(timeline.has_more, "Work Session timeline continuation state"),
    integrity: validateIntegrity(timeline.integrity),
  });
}

function validateRecoveryPreview(value) {
  const preview = ordinaryObject(value, "Work Session recovery preview");
  exactKeys(preview, [
    "format_version", "session_id", "session_record_id", "latest_sequence", "required",
    "automatic", "reason", "owner_record_refs", "owner_resolution",
    "owner_resolution_problems", "safe_actions", "integrity",
  ], "Work Session recovery preview");
  if (preview.format_version !== RECOVERY_FORMAT) throw new Error("Work Session recovery format is unsupported");
  if (preview.automatic !== false) throw new Error("Work Session recovery must never be automatic");
  const integrity = validateIntegrity(preview.integrity);
  const required = boolean(preview.required, "Work Session recovery required state");
  if (integrity.journal_state === "corrupt" && !required) {
    throw new Error("corrupt Work Session state must remain visible as recovery work");
  }
  const ownerResolutionProblems = rows(
    preview.owner_resolution_problems, "Work Session owner resolution problems", 256,
  ).map((item, index) => {
    const row = ordinaryObject(item, `Work Session owner resolution problem ${index}`);
    exactKeys(row, ["record_id", "message"], `Work Session owner resolution problem ${index}`);
    return {
      record_id: identifier(row.record_id, `Work Session owner resolution problem ${index} record ID`),
      message: boundedText(row.message, `Work Session owner resolution problem ${index} message`, { singleLine: false }),
    };
  });
  return freeze({
    format_version: RECOVERY_FORMAT,
    session_id: identifier(preview.session_id, "Work Session recovery session ID", { pattern: SESSION_ID }),
    session_record_id: identifier(preview.session_record_id, "Work Session recovery record ID", { pattern: RECORD_ID }),
    latest_sequence: integer(preview.latest_sequence, "Work Session recovery sequence", { minimum: -1 }),
    required,
    automatic: false,
    reason: boundedText(preview.reason, "Work Session recovery reason", { nullable: true }),
    owner_record_refs: validateOwnerReferences(preview.owner_record_refs, "Work Session recovery owner references"),
    owner_resolution: member(
      preview.owner_resolution, new Set(["not-requested", "partial", "verified"]),
      "Work Session owner resolution",
    ),
    owner_resolution_problems: ownerResolutionProblems,
    safe_actions: validateActions(preview.safe_actions, "Work Session recovery safe actions"),
    integrity,
  });
}

function selectorArguments(sessionId, { required = false } = {}) {
  if (sessionId === undefined || sessionId === null) {
    if (required) throw new Error("Work Session ID is required");
    return [];
  }
  return [identifier(sessionId, "Work Session selector", { pattern: SESSION_ID })];
}

function stateRootArguments(stateRoot) {
  if (stateRoot === undefined) return [];
  return ["--state-root", boundedText(stateRoot, "Work Session state root")];
}

function invocationRoute(executable, options) {
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const stateRoot = options.stateRoot === undefined
    ? undefined
    : pathForCoreLaunch(
      boundedText(options.stateRoot, "Work Session state root"),
      launch,
      "Work Session state root",
    );
  return { launch, stateRoot };
}

function workSessionStatusArguments(sessionId, { stateRoot } = {}) {
  return [
    "session", "status", ...selectorArguments(sessionId),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

function workSessionTimelineArguments(
  sessionId, { afterSequence = -1, limit = 1024, stateRoot } = {},
) {
  const selectedAfter = integer(afterSequence, "Work Session timeline cursor", { minimum: -1 });
  const selectedLimit = integer(limit, "Work Session timeline limit", { minimum: 1, maximum: 4096 });
  return [
    "session", "timeline", ...selectorArguments(sessionId, { required: true }),
    "--after-sequence", String(selectedAfter), "--limit", String(selectedLimit),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

function workSessionRecoveryArguments(sessionId, { apply = false, stateRoot } = {}) {
  return [
    "session", "recover", ...selectorArguments(sessionId), ...(apply ? ["--apply"] : []),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

function workSessionResumeArguments(
  sessionId, { workspace, stateRoot, expectedSequence } = {},
) {
  if (workspace !== undefined) {
    boundedText(workspace, "Work Session workspace");
  }
  const selectedExpected = expectedSequence === undefined
    ? undefined
    : integer(expectedSequence, "Work Session expected sequence", { minimum: 0 });
  return [
    "session", "resume", ...selectorArguments(sessionId, { required: true }),
    ...(workspace === undefined ? [] : ["--workspace", workspace]),
    ...(selectedExpected === undefined
      ? [] : ["--expected-sequence", String(selectedExpected)]),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

function workSessionCloseArguments(sessionId, { stateRoot } = {}) {
  return [
    "session", "close", ...selectorArguments(sessionId, { required: true }),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

async function invokeWorkSessionStatus(executable, sessionId, options = {}) {
  const route = invocationRoute(executable, options);
  const value = await invokeCoreJson(
    executable, workSessionStatusArguments(sessionId, { stateRoot: route.stateRoot }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session status", maximumOutput: 8 * 1024 * 1024,
    },
  );
  return validateWorkSessionStatus(value);
}

async function invokeWorkSessionTimeline(executable, sessionId, options = {}) {
  const route = invocationRoute(executable, options);
  const afterSequence = options.afterSequence === undefined ? -1
    : integer(options.afterSequence, "Work Session timeline cursor", { minimum: -1 });
  const limit = options.limit === undefined ? 1024
    : integer(options.limit, "Work Session timeline limit", { minimum: 1, maximum: 4096 });
  const value = await invokeCoreJson(
    executable, workSessionTimelineArguments(sessionId, {
      afterSequence, limit, stateRoot: route.stateRoot,
    }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session timeline", maximumOutput: 48 * 1024 * 1024,
    },
  );
  return validateWorkSessionTimeline(value);
}

async function invokeWorkSessionRecovery(executable, sessionId, options = {}) {
  const route = invocationRoute(executable, options);
  const value = await invokeCoreJson(
    executable, workSessionRecoveryArguments(sessionId, { stateRoot: route.stateRoot }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session recovery", maximumOutput: 8 * 1024 * 1024,
    },
  );
  return validateRecoveryPreview(value);
}

async function invokeWorkSessionRecoveryApply(executable, sessionId, options = {}) {
  const route = invocationRoute(executable, options);
  const value = await invokeCoreJson(
    executable, workSessionRecoveryArguments(sessionId, {
      apply: true, stateRoot: route.stateRoot,
    }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session recovery apply", maximumOutput: 8 * 1024 * 1024,
    },
  );
  return validateWorkSessionStatus(value);
}

async function invokeWorkSessionResume(executable, sessionId, options = {}) {
  const route = invocationRoute(executable, options);
  const workspace = options.workspace === undefined
    ? undefined
    : pathForCoreLaunch(
      boundedText(options.workspace, "Work Session workspace"),
      route.launch,
      "Work Session workspace",
    );
  const expectedSequence = options.expectedSequence === undefined
    ? undefined
    : integer(options.expectedSequence, "Work Session expected sequence", { minimum: 0 });
  const value = await invokeCoreJson(
    executable, workSessionResumeArguments(sessionId, {
      workspace, stateRoot: route.stateRoot, expectedSequence,
    }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session resume", maximumOutput: 8 * 1024 * 1024,
    },
  );
  return validateWorkSessionStatus(value);
}

async function invokeWorkSessionClose(executable, sessionId, options = {}) {
  const route = invocationRoute(executable, options);
  const value = await invokeCoreJson(
    executable, workSessionCloseArguments(sessionId, { stateRoot: route.stateRoot }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session close", maximumOutput: 8 * 1024 * 1024,
    },
  );
  return validateWorkSessionStatus(value);
}

function liveConsoleOwnerSelector(reference) {
  const selected = validateOwnerReference(reference, "Work Session live-console owner reference");
  if (selected.owner_id !== "workbench-shell"
      || selected.record_kind !== LIVE_CONSOLE_FORMAT
      || !LIVE_CONSOLE_RECORD_ID.test(selected.record_id)
      || selected.digest === null || !DIGEST.test(selected.digest)) {
    throw new Error("Work Session owner is not one exact live-console V1 record");
  }
  return selected;
}

function workSessionArtifactEventsArguments(
  sessionId, reference, { afterSequence = -1, limit = 200, stateRoot } = {},
) {
  const owner = liveConsoleOwnerSelector(reference);
  const selectedAfter = integer(afterSequence, "live-console event cursor", { minimum: -1 });
  const selectedLimit = integer(limit, "live-console event page size", {
    minimum: 1, maximum: MAX_LIVE_CONSOLE_EVENT_PAGE,
  });
  return [
    "session", "artifact", ...selectorArguments(sessionId, { required: true }),
    owner.record_id, owner.digest,
    "--after-sequence", String(selectedAfter), "--limit", String(selectedLimit),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

function workSessionArtifactRangeArguments(sessionId, reference, eventId, { stateRoot } = {}) {
  const owner = liveConsoleOwnerSelector(reference);
  return [
    "session", "artifact", ...selectorArguments(sessionId, { required: true }),
    owner.record_id, owner.digest,
    identifier(eventId, "live-console event selector", { pattern: LIVE_CONSOLE_EVENT_ID }),
    ...stateRootArguments(stateRoot), "--frontend", ADAPTER_FRONTEND, "--json",
  ];
}

function validateOwnerArtifactEvents(value) {
  const page = ordinaryObject(value, "Work Session owner artifact events");
  exactKeys(page, [
    "format_version", "session_id", "owner_record_id", "owner_digest",
    "current_owner_digest", "after_sequence", "limit", "events", "has_more",
    "next_after_sequence",
  ], "Work Session owner artifact events");
  if (page.format_version !== "workbench-owner-artifact-events-v1") {
    throw new Error("Work Session owner artifact events format is unsupported");
  }
  const sessionId = identifier(
    page.session_id, "Work Session owner artifact session ID", { pattern: SESSION_ID },
  );
  const ownerRecordId = identifier(
    page.owner_record_id, "Work Session owner artifact record ID",
    { pattern: LIVE_CONSOLE_RECORD_ID },
  );
  const ownerDigest = identifier(
    page.owner_digest, "Work Session owner artifact digest", { pattern: DIGEST },
  );
  const currentOwnerDigest = identifier(
    page.current_owner_digest, "Work Session current owner artifact digest", { pattern: DIGEST },
  );
  const afterSequence = integer(page.after_sequence, "Work Session owner artifact event cursor", {
    minimum: -1,
  });
  const limit = integer(page.limit, "Work Session owner artifact event page size", {
    minimum: 1, maximum: MAX_LIVE_CONSOLE_EVENT_PAGE,
  });
  let previousSequence = afterSequence;
  const eventIds = new Set();
  const events = rows(page.events, "Work Session owner artifact events", limit)
    .map((value_, index) => {
      const event = ordinaryObject(value_, `Work Session owner artifact event ${index}`);
      exactKeys(event, [
        "event_id", "sequence", "kind", "severity", "subsystem", "message",
        "stream", "artifact", "byte_start", "byte_end", "boundary",
      ], `Work Session owner artifact event ${index}`);
      const sequence = integer(event.sequence, "Work Session owner artifact event sequence", {
        minimum: 1,
      });
      if (sequence <= previousSequence) {
        throw new Error("Work Session owner artifact event order changed");
      }
      previousSequence = sequence;
      const eventId = identifier(event.event_id, "Work Session owner artifact event ID", {
        pattern: LIVE_CONSOLE_EVENT_ID,
      });
      if (eventIds.has(eventId)) {
        throw new Error("Work Session owner artifact event identity was repeated");
      }
      eventIds.add(eventId);
      const stream = identifier(event.stream, "Work Session owner artifact stream", {
        pattern: LIVE_CONSOLE_STREAM,
      });
      const artifact = boundedText(event.artifact, "Work Session owner artifact name");
      if (artifact !== `${stream}.raw`) {
        throw new Error("Work Session owner artifact stream binding changed");
      }
      const byteStart = integer(event.byte_start, "Work Session owner artifact byte start");
      const byteEnd = integer(event.byte_end, "Work Session owner artifact byte end", {
        minimum: byteStart,
      });
      if (byteEnd - byteStart > MAX_LIVE_CONSOLE_RAW_RANGE_BYTES) {
        throw new Error("Work Session owner artifact event range exceeds its bound");
      }
      return {
        event_id: eventId,
        sequence,
        kind: boundedText(event.kind, "Work Session owner artifact event kind"),
        severity: boundedText(event.severity, "Work Session owner artifact event severity"),
        subsystem: boundedText(event.subsystem, "Work Session owner artifact event subsystem"),
        message: boundedText(event.message, "Work Session owner artifact event message", {
          singleLine: false,
        }),
        stream,
        artifact,
        byte_start: byteStart,
        byte_end: byteEnd,
        boundary: member(
          event.boundary, LIVE_CONSOLE_BOUNDARIES,
          "Work Session owner artifact event boundary",
        ),
      };
    });
  const hasMore = boolean(page.has_more, "Work Session owner artifact continuation state");
  const nextAfterSequence = integer(
    page.next_after_sequence, "Work Session owner artifact next cursor", { minimum: -1 },
  );
  const expectedNext = events.length ? events[events.length - 1].sequence : afterSequence;
  if (nextAfterSequence !== expectedNext || (hasMore && events.length !== limit)) {
    throw new Error("Work Session owner artifact event cursor is inconsistent");
  }
  return freeze({
    format_version: "workbench-owner-artifact-events-v1",
    session_id: sessionId,
    owner_record_id: ownerRecordId,
    owner_digest: ownerDigest,
    current_owner_digest: currentOwnerDigest,
    after_sequence: afterSequence,
    limit,
    events,
    has_more: hasMore,
    next_after_sequence: nextAfterSequence,
  });
}

function validateOwnerArtifactRange(value) {
  const range = ordinaryObject(value, "Work Session owner artifact range");
  exactKeys(range, [
    "format_version", "session_id", "owner_record_id", "owner_digest",
    "current_owner_digest", "event_id", "sequence", "stream", "artifact",
    "byte_start", "byte_end", "byte_count", "content_sha256", "encoding",
    "content_base64", "utf8",
  ], "Work Session owner artifact range");
  if (range.format_version !== "workbench-owner-artifact-range-v1") {
    throw new Error("Work Session owner artifact range format is unsupported");
  }
  const sessionId = identifier(
    range.session_id, "Work Session owner artifact session ID", { pattern: SESSION_ID },
  );
  const ownerRecordId = identifier(
    range.owner_record_id, "Work Session owner artifact record ID",
    { pattern: LIVE_CONSOLE_RECORD_ID },
  );
  const ownerDigest = identifier(
    range.owner_digest, "Work Session owner artifact digest", { pattern: DIGEST },
  );
  const currentOwnerDigest = identifier(
    range.current_owner_digest, "Work Session current owner artifact digest", { pattern: DIGEST },
  );
  const eventId = identifier(
    range.event_id, "Work Session owner artifact event ID", { pattern: LIVE_CONSOLE_EVENT_ID },
  );
  const sequence = integer(range.sequence, "Work Session owner artifact event sequence", {
    minimum: 1,
  });
  const stream = identifier(
    range.stream, "Work Session owner artifact stream",
    { pattern: LIVE_CONSOLE_STREAM },
  );
  const artifact = boundedText(range.artifact, "Work Session owner artifact name");
  if (artifact !== `${stream}.raw`) {
    throw new Error("Work Session owner artifact stream binding changed");
  }
  const byteStart = integer(range.byte_start, "Work Session owner artifact byte start");
  const byteEnd = integer(range.byte_end, "Work Session owner artifact byte end", {
    minimum: byteStart,
  });
  const byteCount = integer(range.byte_count, "Work Session owner artifact byte count", {
    maximum: MAX_LIVE_CONSOLE_RAW_RANGE_BYTES,
  });
  if (byteEnd - byteStart !== byteCount) {
    throw new Error("Work Session owner artifact byte range is inconsistent");
  }
  const contentSha256 = identifier(
    range.content_sha256, "Work Session owner artifact content digest", { pattern: DIGEST },
  );
  if (range.encoding !== "base64" || typeof range.content_base64 !== "string"
      || range.content_base64.length > Math.ceil(MAX_LIVE_CONSOLE_RAW_RANGE_BYTES / 3) * 4
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
        range.content_base64,
      )) {
    throw new Error("Work Session owner artifact content is not bounded canonical base64");
  }
  const bytes = Buffer.from(range.content_base64, "base64");
  if (bytes.length !== byteCount || bytes.toString("base64") !== range.content_base64
      || `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}` !== contentSha256) {
    throw new Error("Work Session owner artifact content binding changed");
  }
  let exactUtf8 = null;
  try {
    exactUtf8 = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (_error) {
    // Null is the only valid projection for a byte range that is not exact UTF-8.
  }
  if ((range.utf8 !== null && typeof range.utf8 !== "string") || range.utf8 !== exactUtf8) {
    throw new Error("Work Session owner artifact UTF-8 projection changed");
  }
  return freeze({
    format_version: "workbench-owner-artifact-range-v1",
    session_id: sessionId,
    owner_record_id: ownerRecordId,
    owner_digest: ownerDigest,
    current_owner_digest: currentOwnerDigest,
    event_id: eventId,
    sequence,
    stream,
    artifact,
    byte_start: byteStart,
    byte_end: byteEnd,
    byte_count: byteCount,
    content_sha256: contentSha256,
    encoding: "base64",
    content_base64: range.content_base64,
    utf8: exactUtf8,
  });
}

async function invokeWorkSessionArtifactEvents(executable, sessionId, reference, options = {}) {
  const route = invocationRoute(executable, options);
  const owner = liveConsoleOwnerSelector(reference);
  const afterSequence = options.afterSequence === undefined ? -1 : options.afterSequence;
  const limit = options.limit === undefined ? 200 : options.limit;
  const value = await invokeCoreJson(
    executable,
    workSessionArtifactEventsArguments(sessionId, owner, {
      afterSequence, limit, stateRoot: route.stateRoot,
    }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session owner artifact events", maximumOutput: 8 * 1024 * 1024,
    },
  );
  const result = validateOwnerArtifactEvents(value);
  if (result.session_id !== sessionId || result.owner_record_id !== owner.record_id
      || result.owner_digest !== owner.digest || result.after_sequence !== afterSequence
      || result.limit !== limit) {
    throw new Error("Work Session owner artifact events changed their selected identity");
  }
  return result;
}

async function invokeWorkSessionArtifactRange(
  executable, sessionId, reference, eventId, options = {},
) {
  const route = invocationRoute(executable, options);
  const owner = liveConsoleOwnerSelector(reference);
  const value = await invokeCoreJson(
    executable,
    workSessionArtifactRangeArguments(sessionId, owner, eventId, {
      stateRoot: route.stateRoot,
    }),
    {
      ...options, launch: route.launch,
      label: "Workbench Work Session owner artifact", maximumOutput: 16 * 1024 * 1024,
    },
  );
  const result = validateOwnerArtifactRange(value);
  if (result.session_id !== sessionId || result.owner_record_id !== owner.record_id
      || result.owner_digest !== owner.digest || result.event_id !== eventId) {
    throw new Error("Work Session owner artifact response changed its selected identity");
  }
  return result;
}

module.exports = {
  EVENT_FORMAT,
  RECOVERY_FORMAT,
  SUMMARY_FORMAT,
  TIMELINE_FORMAT,
  invokeWorkSessionClose,
  invokeWorkSessionArtifactEvents,
  invokeWorkSessionRecovery,
  invokeWorkSessionRecoveryApply,
  invokeWorkSessionResume,
  invokeWorkSessionStatus,
  invokeWorkSessionTimeline,
  invokeWorkSessionArtifactRange,
  workSessionArtifactEventsArguments,
  workSessionArtifactRangeArguments,
  workSessionCloseArguments,
  workSessionRecoveryArguments,
  workSessionResumeArguments,
  workSessionStatusArguments,
  workSessionTimelineArguments,
  validateOwnerArtifactEvents,
  validateOwnerArtifactRange,
  validateRecoveryPreview,
  validateWorkSessionStatus,
  validateWorkSessionTimeline,
};
