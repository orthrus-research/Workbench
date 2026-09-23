"use strict";

const crypto = require("node:crypto");

const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");

const CATALOG_FORMAT = "workbench-developer-feature-record-catalog-v1";
const CATALOG_KIND = "workbench-developer-feature-record-catalog";
const PRESENTATION_FORMAT = "workbench-developer-feature-presentation-v1";
const PRESENTATION_FORMAT_V2 = "workbench-developer-feature-presentation-v2";
const PRESENTATION_KIND = "workbench-developer-feature-presentation";
const TRANSACTION_FORMAT = "workbench-developer-feature-transaction-view-v1";
const TRANSACTION_KIND = "workbench-developer-feature-transaction-view";
const FAMILIES = Object.freeze([
  "material-fluid-recipe",
  "recipe-change",
  "quest-for-process",
]);
const COLLECTIONS = Object.freeze(["plans", "receipts", "rollbacks", "recoveries", "runs"]);
const ACTIONS = Object.freeze(["apply", "check", "recover", "rollback", "run"]);
const RUNTIME_FAMILIES = Object.freeze(["material-fluid-recipe", "recipe-change"]);
const CONTENT_ID = /^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_RECORDS = 4096;
const MAX_OPERATIONS = 4096;
const MAX_TRANSACTION_OPERATIONS = 64;
const KINDS = Object.freeze({
  "material-fluid-recipe": Object.freeze({
    plans: "workbench-developer-material-fluid-recipe-plan",
    receipts: "workbench-developer-material-fluid-recipe-receipt",
    rollbacks: "workbench-developer-material-fluid-recipe-rollback",
    recoveries: "workbench-developer-material-fluid-recipe-recovery",
    runs: "workbench-developer-material-fluid-recipe-run",
  }),
  "recipe-change": Object.freeze({
    plans: "workbench-supersymmetry-recipe-change-plan",
    receipts: "workbench-supersymmetry-recipe-change-receipt",
    rollbacks: "workbench-supersymmetry-recipe-change-rollback",
    recoveries: "workbench-supersymmetry-recipe-change-recovery",
    runs: "workbench-developer-recipe-change-runtime-comparison",
  }),
  "quest-for-process": Object.freeze({
    plans: "workbench-developer-source-feature-plan",
    receipts: "workbench-developer-source-feature-receipt",
    rollbacks: "workbench-developer-source-feature-rollback",
    recoveries: "workbench-developer-source-feature-recovery",
  }),
});
const TRANSACTION_RECORD_STATES = Object.freeze({
  plans: Object.freeze({
    "material-fluid-recipe": Object.freeze(["experimental-ready"]),
    "recipe-change": Object.freeze(["experimental-ready"]),
    "quest-for-process": Object.freeze(["experimental-ready-runtime-unverified"]),
  }),
  receipts: Object.freeze(Object.fromEntries(FAMILIES.map((family) => [
    family, Object.freeze(["applied", "rejected"]),
  ]))),
  rollbacks: Object.freeze(Object.fromEntries(FAMILIES.map((family) => [
    family, Object.freeze(["rejected", "restored"]),
  ]))),
  recoveries: Object.freeze(Object.fromEntries(FAMILIES.map((family) => [
    family, Object.freeze(["applied", "restored", "review-required"]),
  ]))),
  runs: Object.freeze({
    "material-fluid-recipe": Object.freeze(["complete", "incomplete"]),
    "recipe-change": Object.freeze(["complete", "incomplete"]),
  }),
});

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, keys, label) {
  const actual = Object.keys(object(value, label)).sort();
  const expected = [...keys].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${label} fields changed`);
  }
}

function text(value, label, maximum = 64 * 1024, allowEmpty = false) {
  if (typeof value !== "string" || (!allowEmpty && !value) || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > maximum) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function nullableText(value, label, maximum = 64 * 1024) {
  return value === null ? null : text(value, label, maximum);
}

function safeInteger(value, label, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside its supported boundary`);
  }
  return value;
}

function contentId(value, label) {
  if (typeof value !== "string" || !CONTENT_ID.test(value)) {
    throw new Error(`${label} is not one content identity`);
  }
  return value;
}

function fileUri(value, label) {
  const selected = text(value, label, 128 * 1024);
  let parsed;
  try {
    parsed = new URL(selected);
  } catch (error) {
    throw new Error(`${label} is invalid`, { cause: error });
  }
  if (parsed.protocol !== "file:") throw new Error(`${label} must be a file URI`);
  return selected;
}

function containedRecordUri(rootValue, recordValue, collection) {
  const root = new URL(rootValue);
  const record = new URL(recordValue);
  const rootPath = decodeURIComponent(root.pathname).replace(/\/$/, "");
  const recordPath = decodeURIComponent(record.pathname);
  return root.protocol === record.protocol && root.host === record.host
    && recordPath.startsWith(`${rootPath}/${collection}/`)
    && recordPath.endsWith("/record.json");
}

function validateJsonTree(value, label, depth = 0, count = { value: 0 }) {
  count.value += 1;
  if (count.value > 100_000 || depth > 64) throw new Error(`${label} exceeds its JSON boundary`);
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${label} contains a non-finite number`);
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length > 16 * 1024) throw new Error(`${label} array exceeds its boundary`);
    value.forEach((item) => validateJsonTree(item, label, depth + 1, count));
    return value;
  }
  const selected = object(value, label);
  if (Object.keys(selected).length > 16 * 1024) {
    throw new Error(`${label} object exceeds its boundary`);
  }
  for (const [key, item] of Object.entries(selected)) {
    text(key, `${label} key`, 16 * 1024, true);
    validateJsonTree(item, label, depth + 1, count);
  }
  return value;
}

function canonicalJson(value) {
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("content identity contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const selected = object(value, "content identity value");
  return `{${Object.keys(selected).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalJson(selected[key])}`,
  ).join(",")}}`;
}

function verifySealedId(value, kind, label) {
  const selected = object(value, label);
  const supplied = selected.id;
  if (typeof supplied !== "string" || !supplied.startsWith(`${kind}:sha256:`)) {
    throw new Error(`${label} identity changed`);
  }
  const body = { ...selected };
  delete body.id;
  const digest = crypto.createHash("sha256").update(canonicalJson(body), "utf8").digest("hex");
  if (supplied !== `${kind}:sha256:${digest}`) throw new Error(`${label} content identity does not verify`);
}

function validateStringList(value, label, maximumItems = 4096) {
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw new Error(`${label} is outside its supported boundary`);
  }
  value.forEach((item, index) => text(item, `${label}[${index}]`, 256 * 1024, true));
  return value;
}

function validateRecordSummary(value, index) {
  const label = `developer feature record summary ${index}`;
  const record = object(value, label);
  exactKeys(record, [
    "collection", "diagnostic_code", "family", "plan_id", "record_id",
    "operation_count", "record_kind", "record_state", "reference", "uri",
    "verification_state", "workspace_uri",
  ], label);
  if (!FAMILIES.includes(record.family) || !COLLECTIONS.includes(record.collection)
      || (record.collection === "runs" && !RUNTIME_FAMILIES.includes(record.family))) {
    throw new Error(`${label} selection is unsupported`);
  }
  contentId(record.record_id, `${label} record ID`);
  contentId(record.plan_id, `${label} plan ID`);
  const expectedKind = KINDS[record.family][record.collection];
  const expectedPlanKind = KINDS[record.family].plans;
  if (record.record_kind !== expectedKind
      || !record.record_id.startsWith(`${expectedKind}:sha256:`)
      || !record.plan_id.startsWith(`${expectedPlanKind}:sha256:`)) {
    throw new Error(`${label} owner kind binding changed`);
  }
  if (record.reference !== record.record_id) throw new Error(`${label} reference changed`);
  text(record.record_kind, `${label} kind`, 256);
  text(record.record_state, `${label} state`, 256);
  if (record.diagnostic_code !== null) {
    text(record.diagnostic_code, `${label} diagnostic code`, 1024, true);
  }
  fileUri(record.uri, `${label} URI`);
  fileUri(record.workspace_uri, `${label} workspace URI`);
  safeInteger(record.operation_count, `${label} operation count`, 1, MAX_OPERATIONS);
  if (!["ready", "stale"].includes(record.verification_state)) {
    throw new Error(`${label} verification state changed`);
  }
  return Object.freeze({ ...record });
}

/** Validate the exact Shell-owned retained-record discovery envelope. */
function validateRecordCatalog(value, expected = {}) {
  const catalog = object(value, "developer feature record catalog");
  exactKeys(catalog, [
    "filters", "format", "id", "kind", "limitations", "records",
    "schema_version", "state_root_uri",
  ], "developer feature record catalog");
  if (catalog.format !== CATALOG_FORMAT || catalog.kind !== CATALOG_KIND
      || catalog.schema_version !== 1) {
    throw new Error("developer feature record catalog identity changed");
  }
  verifySealedId(catalog, CATALOG_KIND, "developer feature record catalog");
  fileUri(catalog.state_root_uri, "developer feature record state root URI");
  validateStringList(catalog.limitations, "developer feature record catalog limitations", 256);
  if (catalog.limitations.some((item) => item.length === 0)) {
    throw new Error("developer feature record catalog limitation is empty");
  }
  const filters = object(catalog.filters, "developer feature record filters");
  exactKeys(filters, ["collection", "family"], "developer feature record filters");
  if (filters.family !== null && !FAMILIES.includes(filters.family)) {
    throw new Error("developer feature record family filter changed");
  }
  if (filters.collection !== null && !COLLECTIONS.includes(filters.collection)) {
    throw new Error("developer feature record collection filter changed");
  }
  if ((expected.family ?? null) !== filters.family
      || (expected.collection ?? null) !== filters.collection) {
    throw new Error("developer feature record catalog does not match the requested filters");
  }
  if (!Array.isArray(catalog.records) || catalog.records.length > MAX_RECORDS) {
    throw new Error("developer feature record catalog exceeds its record boundary");
  }
  const records = catalog.records.map(validateRecordSummary);
  for (const record of records) {
    if ((filters.family !== null && record.family !== filters.family)
        || (filters.collection !== null && record.collection !== filters.collection)) {
      throw new Error("developer feature record escaped its catalog filter");
    }
    if (!containedRecordUri(catalog.state_root_uri, record.uri, record.collection)) {
      throw new Error("developer feature record escaped its state root");
    }
  }
  const keys = records.map((record) => `${record.collection}\0${record.record_id}`);
  if (new Set(keys).size !== keys.length) throw new Error("developer feature record catalog is duplicated");
  const expectedOrder = [...records].sort((left, right) => {
    const family = left.family < right.family ? -1 : left.family > right.family ? 1 : 0;
    if (family !== 0) return family;
    const collection = COLLECTIONS.indexOf(left.collection) - COLLECTIONS.indexOf(right.collection);
    return collection || (left.record_id < right.record_id ? -1 : left.record_id > right.record_id ? 1 : 0);
  });
  if (records.some((record, index) => record !== expectedOrder[index])) {
    throw new Error("developer feature record catalog order changed");
  }
  return Object.freeze({
    ...catalog,
    filters: Object.freeze({ ...filters }),
    limitations: Object.freeze([...catalog.limitations]),
    records: Object.freeze(records),
  });
}

function decodeExactBase64(value, size, digest, label) {
  text(value, `${label} base64`, 48 * 1024 * 1024, true);
  if (value.length % 4 !== 0 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error(`${label} is not canonical base64`);
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.toString("base64") !== value || bytes.byteLength !== size
      || crypto.createHash("sha256").update(bytes).digest("hex") !== digest) {
    throw new Error(`${label} bytes do not match their owner digest`);
  }
  return bytes;
}

function validateOperation(value, index) {
  const label = `developer feature operation ${index}`;
  const operation = object(value, label);
  exactKeys(operation, [
    "after_base64", "after_sha256", "after_size", "before_base64", "before_sha256",
    "before_size", "diff", "operation", "ordinal", "outcome", "path", "role",
  ], label);
  if (operation.operation !== "update" || operation.ordinal !== index) {
    throw new Error(`${label} identity changed`);
  }
  safeInteger(operation.before_size, `${label} before size`, 0, 32 * 1024 * 1024);
  safeInteger(operation.after_size, `${label} after size`, 0, 32 * 1024 * 1024);
  if (!SHA256.test(operation.before_sha256) || !SHA256.test(operation.after_sha256)) {
    throw new Error(`${label} digest changed`);
  }
  const path = text(operation.path, `${label} path`, 32 * 1024);
  if (path.startsWith("/") || path.includes("\\")
      || path.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error(`${label} path is not one normalized workspace-relative path`);
  }
  text(operation.role, `${label} role`, 1024);
  text(operation.outcome, `${label} outcome`, 1024);
  text(operation.diff, `${label} diff`, 16 * 1024 * 1024, true);
  decodeExactBase64(
    operation.before_base64, operation.before_size, operation.before_sha256, `${label} before`,
  );
  decodeExactBase64(
    operation.after_base64, operation.after_size, operation.after_sha256, `${label} after`,
  );
  return Object.freeze({ ...operation });
}

function validateVerification(value, expectedPlanId) {
  const verification = object(value, "developer feature verification");
  exactKeys(verification, ["format", "plan_id", "reason", "schema_version", "state"], "developer feature verification");
  text(verification.format, "developer feature verification format", 256);
  if (verification.schema_version !== 1 || verification.plan_id !== expectedPlanId
      || !["ready", "stale"].includes(verification.state)) {
    throw new Error("developer feature verification identity changed");
  }
  nullableText(verification.reason, "developer feature verification reason", 256 * 1024);
  return verification;
}

function validateOwnerRecord(value, expected) {
  const record = object(value, "developer feature owner record");
  exactKeys(record, ["diagnostic_code", "id", "kind", "state", "uri"], "developer feature owner record");
  contentId(record.id, "developer feature owner record ID");
  text(record.kind, "developer feature owner record kind", 256);
  text(record.state, "developer feature owner record state", 256);
  if (record.diagnostic_code !== null) {
    text(record.diagnostic_code, "developer feature owner diagnostic code", 1024, true);
  }
  fileUri(record.uri, "developer feature owner record URI");
  if (expected && (record.id !== expected.record_id || record.kind !== expected.record_kind
      || record.state !== expected.record_state || record.uri !== expected.uri
      || record.diagnostic_code !== expected.diagnostic_code)) {
    throw new Error("developer feature owner record does not match its discovery summary");
  }
  return record;
}

function validateRuntime(value, family) {
  const runtime = object(value, "developer feature runtime boundary");
  exactKeys(runtime, ["action_available", "outcome", "record_id", "requirement", "state"], "developer feature runtime boundary");
  if (typeof runtime.action_available !== "boolean"
      || !["available-not-observed", "required-not-available", "complete", "incomplete"].includes(runtime.state)
      || (runtime.action_available !== RUNTIME_FAMILIES.includes(family))) {
    throw new Error("developer feature runtime boundary changed");
  }
  nullableText(runtime.outcome, "developer feature runtime outcome", 1024);
  if (runtime.record_id !== null) contentId(runtime.record_id, "developer feature runtime record ID");
  if (runtime.requirement !== null) {
    object(runtime.requirement, "developer feature runtime requirement");
    validateJsonTree(runtime.requirement, "developer feature runtime requirement");
  }
  return runtime;
}

function validateRuntimeAssertion(value, label) {
  if (value === null) return null;
  const assertion = object(value, label);
  exactKeys(assertion, ["id", "state"], label);
  text(assertion.id, `${label} ID`, 128 * 1024);
  text(assertion.state, `${label} state`, 1024);
  return Object.freeze({ ...assertion });
}

function validateRuntimeError(value, label) {
  if (value === null) return null;
  const error = object(value, label);
  exactKeys(error, ["kind", "message", "phase"], label);
  text(error.kind, `${label} kind`, 1024);
  text(error.message, `${label} message`, 256 * 1024);
  if (!["assertion", "blocked", "capture", "execution"].includes(error.phase)) {
    throw new Error(`${label} phase changed`);
  }
  return Object.freeze({ ...error });
}

function validateRuntimeProbe(value, label) {
  if (value === null) return null;
  const probe = object(value, label);
  exactKeys(probe, ["id", "overlay_id", "overlay_uri", "script_uri"], label);
  text(probe.id, `${label} ID`, 128 * 1024);
  text(probe.overlay_id, `${label} overlay ID`, 128 * 1024);
  fileUri(probe.overlay_uri, `${label} overlay URI`);
  fileUri(probe.script_uri, `${label} script URI`);
  return Object.freeze({ ...probe });
}

function validateRuntimeReceiptReference(value, label) {
  const reference = object(value, label);
  exactKeys(reference, ["id", "sha256", "size", "uri"], label);
  text(reference.id, `${label} ID`, 128 * 1024);
  if (typeof reference.sha256 !== "string" || !SHA256.test(reference.sha256)) {
    throw new Error(`${label} digest changed`);
  }
  safeInteger(reference.size, `${label} size`, 1);
  fileUri(reference.uri, `${label} URI`);
  return Object.freeze({ ...reference });
}

function validateRuntimeGroovyReference(value, label) {
  if (value === null) return null;
  const reference = object(value, label);
  exactKeys(reference, ["sha256", "size", "uri"], label);
  if (typeof reference.sha256 !== "string" || !SHA256.test(reference.sha256)) {
    throw new Error(`${label} digest changed`);
  }
  safeInteger(reference.size, `${label} size`, 0);
  fileUri(reference.uri, `${label} URI`);
  return Object.freeze({ ...reference });
}

function validateRuntimeReceipt(value, label) {
  if (value === null) return null;
  const receipt = object(value, label);
  exactKeys(receipt, ["final_launch", "groovy_log", "runtime_session"], label);
  return Object.freeze({
    final_launch: validateRuntimeReceiptReference(
      receipt.final_launch, `${label} final launch`,
    ),
    groovy_log: validateRuntimeGroovyReference(receipt.groovy_log, `${label} Groovy log`),
    runtime_session: validateRuntimeReceiptReference(
      receipt.runtime_session, `${label} runtime session`,
    ),
  });
}

function validateRuntimeV2(value, family) {
  const runtime = object(value, "developer feature V2 runtime boundary");
  exactKeys(runtime, [
    "action_available", "outcome", "record_id", "requirement", "sides", "state",
  ], "developer feature V2 runtime boundary");
  if (typeof runtime.action_available !== "boolean"
      || runtime.action_available !== RUNTIME_FAMILIES.includes(family)
      || !["available-not-observed", "required-not-available", "complete", "incomplete"].includes(runtime.state)) {
    throw new Error("developer feature V2 runtime boundary changed");
  }
  nullableText(runtime.outcome, "developer feature V2 runtime outcome", 1024);
  if (runtime.record_id !== null) contentId(runtime.record_id, "developer feature V2 runtime record ID");
  if (runtime.requirement !== null) {
    object(runtime.requirement, "developer feature V2 runtime requirement");
    validateJsonTree(runtime.requirement, "developer feature V2 runtime requirement");
  }
  if (!Array.isArray(runtime.sides) || runtime.sides.length < 1 || runtime.sides.length > 16) {
    throw new Error("developer feature V2 runtime sides changed");
  }
  const roles = new Set();
  const sides = runtime.sides.map((value_, index) => {
    const label = `developer feature V2 runtime side ${index}`;
    const side = object(value_, label);
    exactKeys(side, [
      "assertion", "error", "outcome", "probe", "receipt", "role", "state",
    ], label);
    text(side.role, `${label} role`, 128);
    if (roles.has(side.role)) throw new Error("developer feature V2 runtime side role is duplicated");
    roles.add(side.role);
    text(side.state, `${label} state`, 64 * 1024);
    text(side.outcome, `${label} outcome`, 64 * 1024);
    return Object.freeze({
      assertion: validateRuntimeAssertion(side.assertion, `${label} assertion`),
      error: validateRuntimeError(side.error, `${label} error`),
      outcome: side.outcome,
      probe: validateRuntimeProbe(side.probe, `${label} probe`),
      receipt: validateRuntimeReceipt(side.receipt, `${label} receipt`),
      role: side.role,
      state: side.state,
    });
  });
  return Object.freeze({
    ...runtime,
    sides: Object.freeze(sides),
  });
}

function validateActions(value, family, verificationState) {
  if (!Array.isArray(value) || value.length > ACTIONS.length) {
    throw new Error("developer feature actions changed");
  }
  const seen = new Set();
  for (const [index, item] of value.entries()) {
    const action = object(item, `developer feature action ${index}`);
    exactKeys(action, ["action", "available", "consent_id", "reason"], `developer feature action ${index}`);
    if (!ACTIONS.includes(action.action) || seen.has(action.action)
        || typeof action.available !== "boolean") {
      throw new Error("developer feature action identity changed");
    }
    seen.add(action.action);
    if (action.consent_id !== null) contentId(action.consent_id, `developer feature ${action.action} consent ID`);
    nullableText(action.reason, `developer feature ${action.action} reason`, 256 * 1024);
    if ((action.consent_id !== null) !== action.available && action.action !== "recover"
        && action.action !== "rollback" && action.action !== "check") {
      throw new Error("developer feature consent availability changed");
    }
    if (action.action === "run" && action.available
        && (!RUNTIME_FAMILIES.includes(family) || verificationState !== "ready")) {
      throw new Error("developer feature runtime action overclaims availability");
    }
  }
  return value;
}

/** Validate and content-verify the Shell-owned native IDE projection. */
function validatePresentation(value, expected = {}) {
  const presentation = object(value, "developer feature presentation");
  exactKeys(presentation, [
    "actions", "authority_boundary", "collection", "family", "format", "id", "kind",
    "limitations", "operations", "owner_record", "plan_id", "request", "review", "runtime",
    "schema_version", "verification", "workspace_uri",
  ], "developer feature presentation");
  const expectedFormat = presentation.schema_version === 1
    ? PRESENTATION_FORMAT
    : presentation.schema_version === 2 ? PRESENTATION_FORMAT_V2 : null;
  if (presentation.format !== expectedFormat || presentation.kind !== PRESENTATION_KIND
      || !FAMILIES.includes(presentation.family)
      || !COLLECTIONS.includes(presentation.collection)
      || (presentation.collection === "runs"
        && !RUNTIME_FAMILIES.includes(presentation.family))
      || (presentation.schema_version === 2 && presentation.collection !== "runs")) {
    throw new Error("developer feature presentation identity changed");
  }
  if ((expected.family !== undefined && presentation.family !== expected.family)
      || (expected.collection !== undefined && presentation.collection !== expected.collection)) {
    throw new Error("developer feature presentation does not match the requested selection");
  }
  verifySealedId(presentation, PRESENTATION_KIND, "developer feature presentation");
  contentId(presentation.plan_id, "developer feature presentation plan ID");
  fileUri(presentation.workspace_uri, "developer feature presentation workspace URI");
  object(presentation.authority_boundary, "developer feature authority boundary");
  object(presentation.request, "developer feature request");
  object(presentation.review, "developer feature review");
  validateJsonTree(presentation.authority_boundary, "developer feature authority boundary");
  validateJsonTree(presentation.request, "developer feature request");
  validateJsonTree(presentation.review, "developer feature review");
  validateStringList(presentation.limitations, "developer feature limitations", 4096);
  if (!Array.isArray(presentation.operations) || presentation.operations.length < 1
      || presentation.operations.length > MAX_OPERATIONS) {
    throw new Error("developer feature operations exceed their supported boundary");
  }
  const operations = presentation.operations.map(validateOperation);
  const verification = validateVerification(presentation.verification, presentation.plan_id);
  const ownerRecord = validateOwnerRecord(presentation.owner_record, expected.record);
  const expectedOwnerKind = KINDS[presentation.family][presentation.collection];
  const expectedPlanKind = KINDS[presentation.family].plans;
  if (ownerRecord.kind !== expectedOwnerKind
      || !ownerRecord.id.startsWith(`${expectedOwnerKind}:sha256:`)
      || !presentation.plan_id.startsWith(`${expectedPlanKind}:sha256:`)) {
    throw new Error("developer feature presentation owner kind binding changed");
  }
  if (expected.reference !== undefined && ownerRecord.id !== expected.reference) {
    throw new Error("developer feature presentation does not match the requested record");
  }
  const runtime = presentation.schema_version === 2
    ? validateRuntimeV2(presentation.runtime, presentation.family)
    : validateRuntime(presentation.runtime, presentation.family);
  if (presentation.schema_version === 2
      && (runtime.record_id !== ownerRecord.id || runtime.state !== ownerRecord.state)) {
    throw new Error("developer feature V2 runtime record identity changed");
  }
  const actions = validateActions(
    presentation.actions, presentation.family, verification.state,
  );
  if (expected.record && (presentation.plan_id !== expected.record.plan_id
      || presentation.workspace_uri !== expected.record.workspace_uri
      || operations.length !== expected.record.operation_count
      || (!expected.allowVerificationRefresh
        && verification.state !== expected.record.verification_state))) {
    throw new Error("developer feature presentation does not match its discovery summary");
  }
  return Object.freeze({
    ...presentation,
    actions: Object.freeze([...actions]),
    limitations: Object.freeze([...presentation.limitations]),
    operations: Object.freeze(operations),
    owner_record: Object.freeze({ ...ownerRecord }),
    runtime: presentation.schema_version === 2 ? runtime : Object.freeze({ ...runtime }),
    verification: Object.freeze({ ...verification }),
  });
}

function validateTransactionPath(value, label) {
  const selected = text(value, label, 32 * 1024);
  if (selected.startsWith("/") || selected.includes("\\")
      || selected.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error(`${label} is not one normalized workspace-relative path`);
  }
  return selected;
}

function validateTransactionOperation(value, index) {
  const label = `developer feature transaction operation ${index}`;
  const operation = object(value, label);
  exactKeys(operation, [
    "after_sha256", "after_size", "before_sha256", "before_size", "diff",
    "ordinal", "path", "role",
  ], label);
  if (operation.ordinal !== index) throw new Error(`${label} ordinal changed`);
  validateTransactionPath(operation.path, `${label} path`);
  text(operation.role, `${label} role`, 1024);
  text(operation.diff, `${label} diff`, 16 * 1024 * 1024, true);
  if (typeof operation.before_sha256 !== "string" || !SHA256.test(operation.before_sha256)
      || typeof operation.after_sha256 !== "string" || !SHA256.test(operation.after_sha256)) {
    throw new Error(`${label} digest changed`);
  }
  safeInteger(operation.before_size, `${label} before size`, 0, 32 * 1024 * 1024);
  safeInteger(operation.after_size, `${label} after size`, 0, 32 * 1024 * 1024);
  return Object.freeze({ ...operation });
}

function validateTransactionWorkspaceMatch(value, operations) {
  const match = object(value, "developer feature transaction workspace match");
  exactKeys(match, ["operations", "reason", "state"], "developer feature transaction workspace match");
  if (!["matches-before", "matches-after", "mixed", "drifted", "unavailable"].includes(match.state)
      || !Array.isArray(match.operations)
      || (match.reason !== null && (typeof match.reason !== "string"
        || Buffer.byteLength(match.reason, "utf8") > 4096 || match.reason.includes("\0")))) {
    throw new Error("developer feature transaction workspace match changed");
  }
  if (match.state === "unavailable") {
    if (match.operations.length !== 0 || !match.reason) {
      throw new Error("developer feature unavailable workspace match changed");
    }
  } else if (match.operations.length !== operations.length) {
    throw new Error("developer feature transaction workspace operation set changed");
  }
  const rows = match.operations.map((value_, index) => {
    const label = `developer feature transaction workspace operation ${index}`;
    const row = object(value_, label);
    exactKeys(row, [
      "actual_sha256", "actual_size", "ordinal", "path", "reason", "state",
    ], label);
    const compact = operations[index];
    if (row.ordinal !== index || row.path !== compact.path
        || !["matches-before", "matches-after", "drifted"].includes(row.state)
        || (row.actual_sha256 !== null
          && (typeof row.actual_sha256 !== "string" || !SHA256.test(row.actual_sha256)))
        || (row.actual_size !== null
          && (!Number.isSafeInteger(row.actual_size) || row.actual_size < 0))
        || (row.reason !== null && (typeof row.reason !== "string" || !row.reason
          || row.reason.includes("\0") || Buffer.byteLength(row.reason, "utf8") > 4096))) {
      throw new Error(`${label} changed`);
    }
    if (row.state === "matches-before"
        && (row.actual_sha256 !== compact.before_sha256
          || row.actual_size !== compact.before_size)) {
      throw new Error("developer feature transaction before-byte match changed");
    }
    if (row.state === "matches-after"
        && (row.actual_sha256 !== compact.after_sha256
          || row.actual_size !== compact.after_size)) {
      throw new Error("developer feature transaction after-byte match changed");
    }
    if (row.state !== "drifted" && row.reason !== null) {
      throw new Error("developer feature transaction matched-byte reason changed");
    }
    return Object.freeze({ ...row });
  });
  if (match.state !== "unavailable") {
    const observed = new Set(rows.map((row) => row.state));
    const derived = observed.size === 1 && observed.has("matches-before")
      ? "matches-before"
      : observed.size === 1 && observed.has("matches-after")
        ? "matches-after"
        : [...observed].every((state) => ["matches-before", "matches-after"].includes(state))
          ? "mixed" : "drifted";
    if (match.state !== derived || match.reason !== null) {
      throw new Error("developer feature transaction aggregate workspace match changed");
    }
  }
  return Object.freeze({
    operations: Object.freeze(rows),
    reason: match.reason,
    state: match.state,
  });
}

function validateTransactionRecord(value, index, transaction, operationCount) {
  const label = `developer feature transaction retained record ${index}`;
  const record = object(value, label);
  exactKeys(record, [
    "collection", "diagnostic_code", "family", "operation_count", "plan_id",
    "record_id", "record_kind", "record_state", "reference", "uri",
    "verification_state", "workspace_uri",
  ], label);
  const states = TRANSACTION_RECORD_STATES[record.collection]?.[transaction.family];
  const expectedKind = KINDS[transaction.family]?.[record.collection];
  if (record.family !== transaction.family || !COLLECTIONS.includes(record.collection)
      || !states?.includes(record.record_state) || record.record_kind !== expectedKind
      || typeof record.record_id !== "string" || !CONTENT_ID.test(record.record_id)
      || !record.record_id.startsWith(`${record.record_kind}:sha256:`)
      || record.reference !== record.record_id || record.plan_id !== transaction.plan_id
      || record.operation_count !== operationCount
      || !["ready", "stale"].includes(record.verification_state)
      || record.workspace_uri !== transaction.workspace_uri
      || (record.diagnostic_code !== null
        && (typeof record.diagnostic_code !== "string"
          || record.diagnostic_code.includes("\0")
          || Buffer.byteLength(record.diagnostic_code, "utf8") > 1024))) {
    throw new Error(`${label} changed`);
  }
  fileUri(record.uri, `${label} URI`);
  fileUri(record.workspace_uri, `${label} workspace URI`);
  return Object.freeze({ ...record });
}

function deriveTransactionState(match, records, recoverAvailable) {
  if (recoverAvailable) return "interrupted";
  const hasReceipt = records.some((record) => record.collection === "receipts"
    && record.record_state === "applied");
  const hasRestoration = records.some((record) => (
    ["rollbacks", "recoveries"].includes(record.collection)
      && ["restored", "rolled-back"].includes(record.record_state)
  ) || (
    record.collection === "receipts" && record.record_state === "rejected"
      && record.diagnostic_code === "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
  ));
  if (match.state === "matches-before") return hasRestoration ? "restored" : "planned";
  if (match.state === "matches-after") return hasReceipt ? "applied" : "matches-after-without-receipt";
  return match.state;
}

function expectedTransactionActions(transaction, recoverAvailable) {
  const match = transaction.workspace_match.state;
  const ready = transaction.plan_freshness.state === "ready";
  const receipts = transaction.records
    .filter((record) => record.collection === "receipts" && record.record_state === "applied")
    .map((record) => record.record_id)
    .sort();
  const applyAvailable = match === "matches-before" && ready && !recoverAvailable;
  const rollbackAvailable = match === "matches-after" && receipts.length === 1 && !recoverAvailable;
  return [
    {
      action: "check", available: true, consent_id: null,
      reason: null, record_id: transaction.plan_id,
    },
    {
      action: "apply",
      available: applyAvailable,
      consent_id: applyAvailable ? transaction.plan_id : null,
      reason: applyAvailable ? null
        : recoverAvailable ? "an interrupted transaction must be recovered before applying"
          : !ready ? "plan is stale" : "workspace does not match reviewed before bytes",
      record_id: transaction.plan_id,
    },
    {
      action: "rollback",
      available: rollbackAvailable,
      consent_id: null,
      reason: rollbackAvailable ? null
        : recoverAvailable ? "an interrupted transaction must be recovered before rolling back"
          : match !== "matches-after" ? "workspace does not match reviewed after bytes"
            : "rollback requires one unambiguous retained applied receipt",
      record_id: rollbackAvailable ? receipts[0] : null,
    },
    {
      action: "recover",
      available: recoverAvailable,
      consent_id: null,
      reason: recoverAvailable ? null : "no interrupted transaction is retained",
      record_id: transaction.plan_id,
    },
  ];
}

/** Validate the exact sealed current-state/lineage projection before native use. */
function validateTransaction(value, expected = {}) {
  const transaction = object(value, "developer feature transaction view");
  exactKeys(transaction, [
    "actions", "current_effective_state", "family", "format", "id", "kind",
    "limitations", "operations", "plan_freshness", "plan_id", "records",
    "schema_version", "workspace_match", "workspace_uri",
  ], "developer feature transaction view");
  if (transaction.format !== TRANSACTION_FORMAT || transaction.kind !== TRANSACTION_KIND
      || transaction.schema_version !== 1 || !FAMILIES.includes(transaction.family)
      || (expected.family !== undefined && transaction.family !== expected.family)) {
    throw new Error("developer feature transaction view identity changed");
  }
  verifySealedId(transaction, TRANSACTION_KIND, "developer feature transaction view");
  contentId(transaction.plan_id, "developer feature transaction plan ID");
  if (!transaction.plan_id.startsWith(`${KINDS[transaction.family].plans}:sha256:`)
      || (expected.planId !== undefined && transaction.plan_id !== expected.planId)) {
    throw new Error("developer feature transaction plan kind binding changed");
  }
  fileUri(transaction.workspace_uri, "developer feature transaction workspace URI");
  if (!["applied", "drifted", "interrupted", "matches-after-without-receipt",
    "mixed", "planned", "restored", "unavailable"].includes(transaction.current_effective_state)) {
    throw new Error("developer feature transaction effective state changed");
  }
  validateStringList(transaction.limitations, "developer feature transaction limitations", 4096);
  if (transaction.limitations.some((limitation) => !limitation)) {
    throw new Error("developer feature transaction limitation is empty");
  }
  if (!Array.isArray(transaction.operations) || transaction.operations.length < 1
      || transaction.operations.length > MAX_TRANSACTION_OPERATIONS) {
    throw new Error("developer feature transaction operation set changed");
  }
  const operations = transaction.operations.map(validateTransactionOperation);
  const workspaceMatch = validateTransactionWorkspaceMatch(transaction.workspace_match, operations);
  if (!Array.isArray(transaction.records) || transaction.records.length > MAX_RECORDS) {
    throw new Error("developer feature transaction retained record set changed");
  }
  const binding = {
    family: transaction.family,
    plan_id: transaction.plan_id,
    workspace_uri: transaction.workspace_uri,
  };
  const records = transaction.records.map((record, index) => validateTransactionRecord(
    record, index, binding, operations.length,
  ));
  const keys = records.map((record) => `${record.collection}\0${record.record_id}`);
  if (new Set(keys).size !== keys.length) {
    throw new Error("developer feature transaction contains a duplicate record");
  }
  const sorted = [...records].sort((left, right) => {
    const collection = COLLECTIONS.indexOf(left.collection) - COLLECTIONS.indexOf(right.collection);
    return collection || (left.record_id < right.record_id ? -1 : left.record_id > right.record_id ? 1 : 0);
  });
  const planRecord = records.find((record) => record.collection === "plans"
    && record.record_id === transaction.plan_id);
  if (!planRecord || records.some((record, index) => record !== sorted[index])) {
    throw new Error("developer feature transaction retained record set changed");
  }
  if (expected.record) {
    const selected = expected.record;
    for (const key of [
      "collection", "diagnostic_code", "family", "operation_count", "plan_id",
      "record_id", "record_kind", "record_state", "reference", "uri", "workspace_uri",
    ]) {
      if (planRecord[key] !== selected[key]) {
        throw new Error("developer feature transaction does not match its selected plan");
      }
    }
  }
  const freshness = object(transaction.plan_freshness, "developer feature transaction plan freshness");
  exactKeys(freshness, ["format", "plan_id", "reason", "schema_version", "state"],
    "developer feature transaction plan freshness");
  text(freshness.format, "developer feature transaction plan freshness format", 256);
  if (freshness.schema_version !== 1 || freshness.plan_id !== transaction.plan_id
      || !["ready", "stale"].includes(freshness.state)
      || (freshness.reason !== null && (typeof freshness.reason !== "string"
        || freshness.reason.includes("\0")
        || Buffer.byteLength(freshness.reason, "utf8") > 256 * 1024))) {
    throw new Error("developer feature transaction plan freshness changed");
  }
  if (!Array.isArray(transaction.actions) || transaction.actions.length !== 4) {
    throw new Error("developer feature transaction actions changed");
  }
  const actionNames = ["check", "apply", "rollback", "recover"];
  const actions = transaction.actions.map((value_, index) => {
    const label = `developer feature transaction action ${index}`;
    const action = object(value_, label);
    exactKeys(action, ["action", "available", "consent_id", "reason", "record_id"], label);
    if (action.action !== actionNames[index] || typeof action.available !== "boolean"
        || (action.consent_id !== null
          && (typeof action.consent_id !== "string" || !CONTENT_ID.test(action.consent_id)))
        || (action.record_id !== null
          && (typeof action.record_id !== "string" || !CONTENT_ID.test(action.record_id)))
        || (action.reason !== null && (typeof action.reason !== "string"
          || action.reason.includes("\0")
          || Buffer.byteLength(action.reason, "utf8") > 256 * 1024))) {
      throw new Error(`${label} changed`);
    }
    return Object.freeze({ ...action });
  });
  const recoverAvailable = actions[3].available;
  const validated = {
    ...transaction,
    actions,
    limitations: [...transaction.limitations],
    operations,
    plan_freshness: { ...freshness },
    records,
    workspace_match: workspaceMatch,
  };
  const derivedState = deriveTransactionState(workspaceMatch, records, recoverAvailable);
  if (transaction.current_effective_state !== derivedState) {
    throw new Error("developer feature transaction retained lineage changed");
  }
  const expectedActions = expectedTransactionActions(validated, recoverAvailable);
  if (canonicalJson(actions) !== canonicalJson(expectedActions)) {
    throw new Error("developer feature transaction action meaning changed");
  }
  return Object.freeze({
    ...validated,
    actions: Object.freeze(actions),
    limitations: Object.freeze(validated.limitations),
    operations: Object.freeze(operations),
    plan_freshness: Object.freeze(validated.plan_freshness),
    records: Object.freeze(records),
  });
}

function optionalSelection(value, allowed, label) {
  if (value === undefined || value === null) return null;
  if (!allowed.includes(value)) throw new Error(`${label} is unsupported`);
  return value;
}

function launchOptions(executable, stateRoot, options) {
  const launch = options.launch || resolveCoreLaunch(executable, options);
  const arguments_ = [];
  if (stateRoot) {
    arguments_.push("--state-root", pathForCoreLaunch(stateRoot, launch, "developer feature state root"));
  }
  return { arguments_, launch };
}

async function invokeRecordCatalog(executable, selection = {}, options = {}) {
  const family = optionalSelection(selection.family, FAMILIES, "developer feature family");
  const collection = optionalSelection(selection.collection, COLLECTIONS, "developer feature collection");
  if (collection !== null && family === null) {
    throw new Error("developer feature collection filtering requires a family");
  }
  const arguments_ = ["feature", "records"];
  if (family !== null) arguments_.push(family);
  if (collection !== null) arguments_.push(collection);
  const state = launchOptions(executable, selection.stateRoot || "", options);
  arguments_.push(...state.arguments_, "--json");
  const value = await invokeCoreJson(executable, arguments_, {
    ...options,
    launch: state.launch,
    maximumOutput: 32 * 1024 * 1024,
    timeoutMs: 60_000,
    label: "Workbench developer feature retained records",
  });
  return validateRecordCatalog(value, { family, collection });
}

async function invokePresentation(executable, selection, options = {}) {
  const family = optionalSelection(selection.family, FAMILIES, "developer feature family");
  const collection = optionalSelection(selection.collection, COLLECTIONS, "developer feature collection");
  if (family === null || collection === null) {
    throw new Error("developer feature presentation requires one family and collection");
  }
  const reference = contentId(selection.reference, "developer feature record reference");
  const arguments_ = ["feature", "present", family, collection, reference];
  const state = launchOptions(executable, selection.stateRoot || "", options);
  arguments_.push(...state.arguments_, "--json");
  const value = await invokeCoreJson(executable, arguments_, {
    ...options,
    launch: state.launch,
    maximumOutput: 48 * 1024 * 1024,
    timeoutMs: 120_000,
    label: "Workbench developer feature presentation",
  });
  return validatePresentation(value, {
    family,
    collection,
    reference,
    record: selection.record,
    allowVerificationRefresh: selection.allowVerificationRefresh === true,
  });
}

async function invokeTransaction(executable, selection, options = {}) {
  const family = optionalSelection(selection.family, FAMILIES, "developer feature family");
  if (family === null) throw new Error("developer feature transaction requires one family");
  const planId = contentId(selection.planId, "developer feature transaction plan reference");
  if (!planId.startsWith(`${KINDS[family].plans}:sha256:`)) {
    throw new Error("developer feature transaction plan reference has the wrong family kind");
  }
  if (selection.record && (selection.record.collection !== "plans"
      || selection.record.family !== family || selection.record.plan_id !== planId
      || selection.record.record_id !== planId)) {
    throw new Error("developer feature transaction selection is not one retained plan");
  }
  const arguments_ = ["feature", "transaction", family, planId];
  const state = launchOptions(executable, selection.stateRoot || "", options);
  arguments_.push(...state.arguments_, "--json");
  const value = await invokeCoreJson(executable, arguments_, {
    ...options,
    launch: state.launch,
    maximumOutput: 48 * 1024 * 1024,
    timeoutMs: 120_000,
    label: "Workbench developer feature transaction",
  });
  return validateTransaction(value, { family, planId, record: selection.record });
}

module.exports = {
  COLLECTIONS,
  FAMILIES,
  canonicalJson,
  invokePresentation,
  invokeRecordCatalog,
  invokeTransaction,
  validatePresentation,
  validateRecordCatalog,
  validateTransaction,
};
