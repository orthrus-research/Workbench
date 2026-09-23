"use strict";
const crypto = require("node:crypto");
const VIEW = "workbench-material-check-view-v1";
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}
function identity(kind, value) { return `${kind}:sha256:${crypto.createHash("sha256").update(canonical(value)).digest("hex")}`; }
function query(result, operation, section = null, fields = {}) {
  return { format: "workbench-check-snapshot-query-v1", snapshot_id: result.snapshot_id, view_id: result.view_id,
    operation, section_id: section, record_key: null, blob_sha256: null, preferred_bytes: 65536, cursor: null, ...fields };
}
function validatePage(value, request) {
  const { cursor, ...base } = request;
  const queryId = identity("check-snapshot-query", base);
  if (value?.format !== "workbench-check-snapshot-response-v1" || value.snapshot_id !== request.snapshot_id
      || value.view_id !== request.view_id || value.query_id !== queryId || value.request_id !== identity("check-snapshot-read", request)
      || !["ready", "unavailable", "unsupported", "expired", "incomplete", "cancelled"].includes(value.state)) throw new Error("Snapshot response differs from its selected view or page.");
  if (value.state !== "ready") {
    if (value.complete !== false || value.payload !== null || value.next_cursor !== null || typeof value.reason !== "string") throw new Error("Unavailable snapshot claims complete data.");
    return value;
  }
  const payload = value.payload, offset = cursor?.offset || 0;
  let next;
  if (["records", "sections"].includes(request.operation)) {
    const items = payload?.[request.operation];
    if (!Array.isArray(items) || payload.offset !== offset || !Number.isSafeInteger(payload.total) || payload.total < offset + items.length) throw new Error("Snapshot page count or order differs.");
    next = offset + items.length;
    if (value.complete !== (next === payload.total) || (!value.complete && !items.length)) throw new Error("Snapshot page makes no complete progress.");
  } else if (request.operation === "blob") {
    const raw = Buffer.from(payload?.data || "", "base64");
    if (raw.toString("base64") !== payload.data || payload.offset !== offset || payload.sha256 !== request.blob_sha256
        || crypto.createHash("sha256").update(raw).digest("hex") !== payload.chunk_sha256
        || !Number.isSafeInteger(payload.total_bytes) || payload.total_bytes < offset + raw.length) throw new Error("Snapshot chunk content or range differs.");
    next = offset + raw.length;
    if (value.complete !== (next === payload.total_bytes) || (!value.complete && !raw.length)) throw new Error("Snapshot chunk makes no complete progress.");
  } else if (value.complete !== true || value.next_cursor !== null) throw new Error("Snapshot record must be complete or referenced.");
  if (value.complete) {
    if (value.next_cursor !== null) throw new Error("Terminal snapshot page has a cursor.");
  } else if (value.next_cursor?.offset !== next || value.next_cursor.snapshot_id !== request.snapshot_id || value.next_cursor.query_id !== queryId) throw new Error("Next snapshot page differs.");
  return value;
}
function nextFindingsQuery(result, bulk = false) {
  const next = query(result, "records", "findings", { preferred_bytes: bulk ? 1048576 : 65536 });
  const { cursor, ...base } = next;
  // Changing the transport preference creates a new query identity. Keep the
  // immutable record offset, never reuse the earlier query's cursor identity.
  next.cursor = { snapshot_id: result.snapshot_id, query_id: identity("check-snapshot-query", base), offset: result.finding_page.next_cursor.offset };
  return next;
}
function findings(page) { return (page?.payload?.records || []).filter(row => Object.hasOwn(row, "value")).map(row => row.value); }
function attach(result) {
  if (result.format !== VIEW) return;
  if (result.snapshot_id === null && result.detail_state === "cancelled-before-snapshot-publication"
      && result.finding_page === null && result.finding_query === null && result.coverage === "incomplete") {
    Object.defineProperty(result, "findings", { value: [], writable: true }); return;
  }
  if (!/^check-snapshot:sha256:[0-9a-f]{64}$/.test(result.snapshot_id || "") || typeof result.view_id !== "string"
      || result.finding_query?.operation !== "records" || result.finding_query?.section_id !== "findings"
      || result.finding_query.snapshot_id !== result.snapshot_id || result.finding_query.view_id !== result.view_id
      || !Array.isArray(result.sections) || !Number.isSafeInteger(result.findings_count)) throw new Error("Unsupported material snapshot view.");
  validatePage(result.finding_page, result.finding_query);
  if (result.finding_page.state === "ready" && result.finding_page.payload.total !== result.findings_count) throw new Error("Finding total differs from summary.");
  Object.defineProperty(result, "findings", { value: findings(result.finding_page), writable: true });
}
function progress(result) {
  const page = result.finding_page;
  if (page?.state !== "ready") return `Findings: ${page?.state || "not loaded"}${page?.reason ? ` · ${page.reason}` : ""}`;
  const end = page.payload.offset + page.payload.records.length;
  const large = end - (result.findings?.length || 0);
  return `Findings displayed: ${result.findings?.length || 0}; page through ${end} of ${page.payload.total}${page.complete ? " · final page" : " · more available"}${large ? ` · ${large} large values require detail export` : ""}`;
}
function diagnosticQuery(result, finding) {
  const match = /^(?:\/result\/(baseline|candidate))?\/result\/(execution\/diagnostics|sourceAdmission\/findings)\/([0-9]+)$/.exec(finding.pointer || "");
  if (!match || (match[1] && match[1] !== finding.side)) throw new Error("Unsupported original diagnostic pointer.");
  return query(result, "record", (match[1] ? `${match[1]}-` : "") + (match[2].startsWith("execution") ? "diagnostics" : "admission-findings"), { record_key: `item:${Number(match[3])}` });
}
module.exports = { nextFindingsQuery, VIEW, canonical, identity, query, validatePage, findings, attach, progress, diagnosticQuery };
