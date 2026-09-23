"use strict";

const crypto = require("node:crypto");

const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");

const DIAGNOSIS_FORMAT = "workbench-diagnosis-v1";
const CAPSULE_INSPECTION_FORMAT = "workbench-reproduction-capsule-inspection-v1";
const DIAGNOSIS_ID = /^workbench-diagnosis:sha256:[0-9a-f]{64}$/;
const CAPSULE_ID = /^workbench-reproduction-capsule:sha256:[0-9a-f]{64}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}$/;
const SESSION_ID = /^work-session-v2-[0-9a-f]{32}$/;
const ACTION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{1,255}$/;
const EVENT_SEVERITIES = new Set(["trace", "debug", "info", "warning", "error", "fatal", "unknown"]);
const RAW_BOUNDARIES = new Set(["lf", "crlf", "cr", "limit", "eof"]);
const MUTATIONS = new Set(["read-only", "isolated-target-only"]);
const REQUIRED_EXCLUSIONS = new Set(["credentials", "personal-worlds", "protected-binaries"]);

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function exact(value, names, label) {
  const row = object(value, label);
  const actual = Object.keys(row).sort();
  const expected = [...names].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    const missing = expected.filter((name) => !actual.includes(name));
    const extra = actual.filter((name) => !expected.includes(name));
    throw new Error(`${label} fields changed; missing=${missing}; extra=${extra}`);
  }
  return row;
}

function text(value, label, maximumBytes = 256 * 1024, allowEmpty = false) {
  if (typeof value !== "string" || (!allowEmpty && !value) || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > maximumBytes) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function patterned(value, pattern, label) {
  const selected = text(value, label);
  if (!pattern.test(selected)) throw new Error(`${label} identity is invalid`);
  return selected;
}

function integer(value, label, minimum, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside its integer bound`);
  }
  return value;
}

function rows(value, label, minimum, maximum) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new Error(`${label} is outside its row bound`);
  }
  return value;
}

function strings(value, label, minimum, maximum, maximumBytes = 8192) {
  return rows(value, label, minimum, maximum).map((item, index) => (
    text(item, `${label} ${index}`, maximumBytes)
  ));
}

function schemaText(value, label, maximumCharacters, allowEmpty = false) {
  const selected = text(value, label, maximumCharacters * 4, allowEmpty);
  if (Array.from(selected).length > maximumCharacters) throw new Error(`${label} exceeds its character bound`);
  return selected;
}

function jsonValue(value, label, state = { nodes: 0 }, depth = 0) {
  state.nodes += 1;
  if (state.nodes > 200_000 || depth > 64) throw new Error(`${label} exceeds its JSON bound`);
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    if (typeof value === "string") text(value, label, 1024 * 1024, true);
    return;
  }
  if (typeof value === "number") {
    integer(value, label, Number.MIN_SAFE_INTEGER);
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > 100_000) throw new Error(`${label} exceeds its array bound`);
    value.forEach((item, index) => jsonValue(item, `${label} ${index}`, state, depth + 1));
    return;
  }
  const row = object(value, label);
  if (Object.keys(row).length > 256) throw new Error(`${label} exceeds its object bound`);
  for (const [key, item] of Object.entries(row)) {
    text(key, `${label} key`, 8192);
    jsonValue(item, `${label}.${key}`, state, depth + 1);
  }
}

function unicodeCompare(left, right) {
  const a = Array.from(left, (item) => item.codePointAt(0));
  const b = Array.from(right, (item) => item.codePointAt(0));
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return a.length - b.length;
}

function pythonString(value) {
  let result = '"';
  for (const character of value) {
    const code = character.codePointAt(0);
    if (character === '"') result += '\\"';
    else if (character === "\\") result += "\\\\";
    else if (character === "\b") result += "\\b";
    else if (character === "\f") result += "\\f";
    else if (character === "\n") result += "\\n";
    else if (character === "\r") result += "\\r";
    else if (character === "\t") result += "\\t";
    else if (code >= 0x20 && code <= 0x7e) result += character;
    else if (code <= 0xffff) result += `\\u${code.toString(16).padStart(4, "0")}`;
    else {
      const selected = code - 0x10000;
      result += `\\u${(0xd800 + (selected >> 10)).toString(16)}`;
      result += `\\u${(0xdc00 + (selected & 0x3ff)).toString(16)}`;
    }
  }
  return `${result}"`;
}

function pythonCanonical(value) {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return pythonString(value);
  if (Array.isArray(value)) return `[${value.map(pythonCanonical).join(",")}]`;
  return `{${Object.keys(value).sort(unicodeCompare).map((key) => (
    `${pythonString(key)}:${pythonCanonical(value[key])}`
  )).join(",")}}`;
}

function verifyDiagnosisIdentity(row) {
  const body = structuredClone(row);
  delete body.diagnosis_id;
  const digest = crypto.createHash("sha256")
    .update(`${pythonCanonical(body)}\n`, "utf8")
    .digest("hex");
  if (row.diagnosis_id !== `workbench-diagnosis:sha256:${digest}`) {
    throw new Error("Workbench diagnosis content identity differs from its exact body");
  }
}

function rawRange(value, label) {
  const row = exact(value, ["stream", "artifact", "byte_start", "byte_end", "boundary"], label);
  const stream = schemaText(row.stream, `${label} stream`, 48);
  if (!/^[a-z][a-z0-9-]*$/.test(stream)) throw new Error(`${label} stream is unsupported`);
  const artifact = schemaText(row.artifact, `${label} artifact`, 256);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(artifact)) throw new Error(`${label} artifact is invalid`);
  const start = integer(row.byte_start, `${label} byte start`, 0);
  const end = integer(row.byte_end, `${label} byte end`, 1);
  if (end <= start) throw new Error(`${label} is not one non-empty raw byte range`);
  if (!RAW_BOUNDARIES.has(row.boundary)) throw new Error(`${label} boundary is unsupported`);
  return row;
}

function event(value, label, claim = false) {
  const fields = ["event_id", "sequence", "kind", "severity", "subsystem", "message", "raw_range"];
  if (claim) fields.push("claim_state", "owner_id");
  const row = exact(value, fields, label);
  patterned(row.event_id, IDENTITY, `${label} event ID`);
  integer(row.sequence, `${label} sequence`, 1);
  patterned(row.kind, IDENTITY, `${label} kind`);
  if (!EVENT_SEVERITIES.has(row.severity)) throw new Error(`${label} severity is unsupported`);
  patterned(row.subsystem, IDENTITY, `${label} subsystem`);
  text(row.message, `${label} message`, 1024 * 1024, true);
  rawRange(row.raw_range, `${label} raw range`);
  if (claim) {
    if (row.claim_state !== "observed") throw new Error(`${label} claim is not owner-returned observation`);
    patterned(row.owner_id, IDENTITY, `${label} owner ID`);
  }
  return row;
}

function classification(value, label) {
  const row = exact(value, [
    "classification_id", "claim_state", "owner_id", "owner_record_id",
    "owner_record_kind", "owner_record_uri", "owner_record_digest", "stage",
    "state", "required_markers", "observed_markers", "effective_exit_code",
    "cleanup_contained", "artifact_digest", "detail",
  ], label);
  if (row.classification_id !== "cleanroom-dev-loop-stage"
      || row.claim_state !== "observed" || row.owner_id !== "workbench-shell"
      || row.owner_record_kind !== "workbench-cleanroom-dev-loop-receipt") {
    throw new Error(`${label} authority changed`);
  }
  patterned(row.owner_record_id, IDENTITY, `${label} owner record ID`);
  const uri = schemaText(row.owner_record_uri, `${label} owner record URI`, 8192);
  if (Array.from(uri).length < 3) throw new Error(`${label} owner record URI is invalid`);
  try {
    const parsed = new URL(uri);
    if (!parsed.protocol) throw new Error("URI is not absolute");
  } catch (error) {
    throw new Error(`${label} owner record URI is invalid`, { cause: error });
  }
  patterned(row.owner_record_digest, DIGEST, `${label} owner record digest`);
  if (!new Set(["build", "client", "server"]).has(row.stage)) throw new Error(`${label} stage is unsupported`);
  if (!new Set(["not-run", "passed", "failed"]).has(row.state)) throw new Error(`${label} state is unsupported`);
  for (const field of ["required_markers", "observed_markers"]) {
    const markers = rows(row[field], `${label} ${field}`, 0, 32).map((item, index) => (
      schemaText(item, `${label} ${field} ${index}`, 256)
    ));
    if (new Set(markers).size !== markers.length) throw new Error(`${label} ${field} repeats a marker`);
  }
  if (row.effective_exit_code !== null) integer(row.effective_exit_code, `${label} effective exit code`, 0, 255);
  if (row.cleanup_contained !== null && typeof row.cleanup_contained !== "boolean") {
    throw new Error(`${label} cleanup state is invalid`);
  }
  if (row.artifact_digest !== null) patterned(row.artifact_digest, DIGEST, `${label} artifact digest`);
  schemaText(row.detail, `${label} detail`, 8192);
  return row;
}

function ownerReference(value, label) {
  const row = exact(value, ["owner_id", "record_id", "record_kind", "uri", "digest", "last_verified_state"], label);
  patterned(row.owner_id, IDENTITY, `${label} owner ID`);
  patterned(row.record_id, IDENTITY, `${label} record ID`);
  patterned(row.record_kind, IDENTITY, `${label} record kind`);
  const uri = text(row.uri, `${label} URI`, 8192);
  try { new URL(uri); } catch (error) { throw new Error(`${label} URI is invalid`, { cause: error }); }
  patterned(row.digest, DIGEST, `${label} digest`);
  patterned(row.last_verified_state, IDENTITY, `${label} state`);
  return row;
}

function validateDiagnosisV1(value) {
  const row = exact(value, [
    "format", "schema_version", "diagnosis_id", "work_session_id", "outcome",
    "target_owner_ref", "current_owner_ref", "command", "timeline",
    "observed_failures", "wrappers", "secondary_failures", "shutdown_noise",
    "contributing_conditions", "classifications", "unknowns", "next_experiments",
    "fingerprint", "limitations",
  ], "Workbench diagnosis");
  if (row.format !== DIAGNOSIS_FORMAT || row.schema_version !== 1) {
    throw new Error("Workbench diagnosis format is unsupported");
  }
  patterned(row.diagnosis_id, DIAGNOSIS_ID, "Workbench diagnosis ID");
  if (row.work_session_id !== null) patterned(row.work_session_id, SESSION_ID, "Workbench diagnosis Work Session ID");
  if (!new Set(["failed", "inconclusive"]).has(row.outcome)) throw new Error("Workbench diagnosis outcome is unsupported");
  const target = ownerReference(row.target_owner_ref, "Workbench diagnosis target owner");
  const current = ownerReference(row.current_owner_ref, "Workbench diagnosis current owner");
  for (const field of ["owner_id", "record_id", "record_kind", "uri"]) {
    if (target[field] !== current[field]) throw new Error("Workbench diagnosis owner identity changed during resolution");
  }
  const command = exact(row.command, ["command_id", "intent", "shell"], "Workbench diagnosis command");
  patterned(command.command_id, IDENTITY, "Workbench diagnosis command ID");
  if (!new Set(["inspect", "execute"]).has(command.intent) || typeof command.shell !== "boolean") {
    throw new Error("Workbench diagnosis command authority is unsupported");
  }
  const timeline = rows(row.timeline, "Workbench diagnosis timeline", 1, 100_000).map((item, index) => (
    event(item, `Workbench diagnosis timeline event ${index}`)
  ));
  for (let index = 1; index < timeline.length; index += 1) {
    if (timeline[index].sequence <= timeline[index - 1].sequence) {
      throw new Error("Workbench diagnosis timeline order changed");
    }
  }
  const timelineById = new Map(timeline.map((item) => [item.event_id, item]));
  for (const group of ["observed_failures", "wrappers", "secondary_failures", "shutdown_noise", "contributing_conditions"]) {
    rows(row[group], `Workbench diagnosis ${group}`, 0, 100_000).forEach((item, index) => {
      const selected = event(item, `Workbench diagnosis ${group} ${index}`, true);
      const original = timelineById.get(selected.event_id);
      if (!original || pythonCanonical(original) !== pythonCanonical(Object.fromEntries(
        Object.entries(selected).filter(([key]) => !new Set(["claim_state", "owner_id"]).has(key)),
      ))) throw new Error(`Workbench diagnosis ${group} changed its retained event`);
    });
  }
  rows(row.classifications, "Workbench diagnosis classifications", 0, 256).forEach((item, index) => (
    classification(item, `Workbench diagnosis classification ${index}`)
  ));
  rows(row.unknowns, "Workbench diagnosis unknowns", 1, 256).forEach((item, index) => {
    const unknown = exact(item, ["id", "claim_state", "owner_id", "detail"], `Workbench diagnosis unknown ${index}`);
    patterned(unknown.id, IDENTITY, `Workbench diagnosis unknown ${index} ID`);
    if (unknown.claim_state !== "unknown") throw new Error(`Workbench diagnosis unknown ${index} changed claim state`);
    patterned(unknown.owner_id, IDENTITY, `Workbench diagnosis unknown ${index} owner`);
    text(unknown.detail, `Workbench diagnosis unknown ${index} detail`, 8192);
  });
  rows(row.next_experiments, "Workbench diagnosis next actions", 1, 128).forEach((item, index) => {
    const action = exact(item, ["action_id", "arguments", "context_digest", "mutation"], `Workbench diagnosis next action ${index}`);
    patterned(action.action_id, IDENTITY, `Workbench diagnosis next action ${index} ID`);
    const arguments_ = object(action.arguments, `Workbench diagnosis next action ${index} arguments`);
    if (Object.keys(arguments_).length > 64) throw new Error(`Workbench diagnosis next action ${index} arguments exceed their bound`);
    jsonValue(arguments_, `Workbench diagnosis next action ${index} arguments`);
    patterned(action.context_digest, DIGEST, `Workbench diagnosis next action ${index} context digest`);
    if (!MUTATIONS.has(action.mutation)) throw new Error(`Workbench diagnosis next action ${index} mutation is unsupported`);
  });
  patterned(row.fingerprint, DIGEST, "Workbench diagnosis fingerprint");
  strings(row.limitations, "Workbench diagnosis limitations", 1, 128);
  jsonValue(row, "Workbench diagnosis");
  verifyDiagnosisIdentity(row);
  return deepFreeze(structuredClone(row));
}

function validateCapsuleInspectionV1(value) {
  const row = exact(value, [
    "format", "capsule_id", "diagnosis_id", "fingerprint", "member_count",
    "replay_action", "privacy_review", "limitations",
  ], "Workbench reproduction capsule inspection");
  if (row.format !== CAPSULE_INSPECTION_FORMAT) throw new Error("Workbench capsule inspection format is unsupported");
  patterned(row.capsule_id, CAPSULE_ID, "Workbench capsule ID");
  patterned(row.diagnosis_id, DIAGNOSIS_ID, "Workbench capsule diagnosis ID");
  patterned(row.fingerprint, DIGEST, "Workbench capsule fingerprint");
  if (row.member_count !== 2) throw new Error("Workbench capsule membership changed");
  const action = exact(row.replay_action, ["action_id", "arguments", "mutation"], "Workbench capsule replay action");
  patterned(action.action_id, ACTION_ID, "Workbench capsule replay action ID");
  const arguments_ = object(action.arguments, "Workbench capsule replay arguments");
  if (Object.keys(arguments_).length > 256) throw new Error("Workbench capsule replay arguments exceed their bound");
  jsonValue(arguments_, "Workbench capsule replay arguments");
  if (!MUTATIONS.has(action.mutation)) throw new Error("Workbench capsule replay mutation is unsupported");
  const privacy = exact(row.privacy_review, ["approved", "excluded"], "Workbench capsule privacy review");
  if (privacy.approved !== true) throw new Error("Workbench capsule privacy review is not approved");
  const excluded = strings(privacy.excluded, "Workbench capsule privacy exclusions", 3, 64, 128);
  if (new Set(excluded).size !== excluded.length
      || [...REQUIRED_EXCLUSIONS].some((item) => !excluded.includes(item))) {
    throw new Error("Workbench capsule privacy exclusions are incomplete or duplicated");
  }
  strings(row.limitations, "Workbench capsule limitations", 1, 128);
  jsonValue(row, "Workbench reproduction capsule inspection");
  return deepFreeze(structuredClone(row));
}

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

function diagnosisArguments(launch, target, stateRoot) {
  text(target, "Workbench diagnosis target", 160);
  if (target !== "latest" && !SESSION_ID.test(target)) {
    throw new Error("Workbench diagnosis target is not latest or one exact Work Session ID");
  }
  const arguments_ = ["diagnose", target];
  if (stateRoot !== undefined && stateRoot !== null) {
    arguments_.push("--state-root", pathForCoreLaunch(stateRoot, launch, "Workbench diagnosis state root"));
  }
  arguments_.push("--json");
  return Object.freeze(arguments_);
}

function capsuleInspectionArguments(launch, capsule, operation = "inspect") {
  if (!new Set(["inspect", "verify"]).has(operation)) {
    throw new Error("Workbench capsule read operation is unsupported");
  }
  return Object.freeze([
    "diagnose", "reproduce", operation,
    pathForCoreLaunch(capsule, launch, "Workbench reproduction capsule"), "--json",
  ]);
}

async function invokeDiagnosis(executable, target = "latest", options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const value = await invokeCoreJson(executable, diagnosisArguments(launch, target, options.stateRoot), {
    ...options,
    launch,
    label: "Workbench diagnosis",
    maximumOutput: 32 * 1024 * 1024,
  });
  const parsed = validateDiagnosisV1(value);
  if (parsed.work_session_id === null || (target !== "latest" && parsed.work_session_id !== target)) {
    throw new Error("Workbench diagnosis changed its selected Work Session identity");
  }
  return parsed;
}

async function invokeCapsuleInspection(executable, capsule, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const value = await invokeCoreJson(
    executable,
    capsuleInspectionArguments(launch, capsule, options.operation || "inspect"),
    {
      ...options,
      launch,
      label: "Workbench reproduction capsule inspection",
      maximumOutput: 2 * 1024 * 1024,
    },
  );
  return validateCapsuleInspectionV1(value);
}

module.exports = {
  CAPSULE_INSPECTION_FORMAT,
  DIAGNOSIS_FORMAT,
  capsuleInspectionArguments,
  diagnosisArguments,
  invokeCapsuleInspection,
  invokeDiagnosis,
  validateCapsuleInspectionV1,
  validateDiagnosisV1,
};
