"use strict";
const VIEW = "workbench-material-diagnostic-view-v1";
const STATUS = "workbench-material-delivery-status-v1";
const revisionPattern = /^check-diagnostics:sha256:[0-9a-f]{64}$/;
const GROUPS = ["error-located", "error-unlocated", "warning-located", "warning-unlocated", "information-located", "information-unlocated"];
function argumentsFor(action, value) {
  const args = [];
  if (value?.revision !== undefined) {
    if (!revisionPattern.test(value.revision)) throw new Error("Select an exact diagnostic revision.");
    args.push("--revision", value.revision);
  }
  if (action === "diagnostics") {
    if (!Number.isSafeInteger(value?.offset ?? 0) || (value?.offset ?? 0) < 0) throw new Error("Select an exact diagnostic offset.");
    args.push("--offset", String(value?.offset ?? 0));
    if (value?.group !== undefined) {
      if (!GROUPS.includes(value.group)) throw new Error("Select an exact diagnostic group.");
      args.push("--group", value.group);
    }
  }
  if (action === "diagnostic" || action === "source") {
    if (!revisionPattern.test(value?.revision || "") || !/^diagnostic-(0|[1-9][0-9]*)$/.test(value?.finding || "")) throw new Error("Select an exact retained diagnostic.");
    args.push("--diagnostic", value.finding);
  }
  return args;
}
function validate(result, action, options) {
  if (result.format === STATUS) {
    if (result.diagnostic_id !== null && !revisionPattern.test(result.diagnostic_id || "")) throw new Error("Invalid diagnostic revision.");
    if (!["ready", "preparing", "interrupted", "not-started"].includes(result.detail_state)) throw new Error("Invalid delivery state.");
  }
  if (result.format === VIEW) {
    const total = result.group_count ?? result.findings_count;
    if ((result.group ?? null) !== (options?.group ?? null)) throw new Error("Diagnostic response belongs to another group.");
    if (result.group && (!GROUPS.includes(result.group) || !result.finding_counts)) throw new Error("Diagnostic group counts are unavailable.");
    if (result.finding_counts) {
      if (Object.keys(result.finding_counts).length !== GROUPS.length
          || GROUPS.some(group => !Number.isSafeInteger(result.finding_counts[group]) || result.finding_counts[group] < 0)
          || GROUPS.reduce((n, group) => n + result.finding_counts[group], 0) !== result.findings_count
          || total !== (result.group ? result.finding_counts[result.group] : result.findings_count)) throw new Error("Diagnostic group counts differ from total findings.");
    }
    if (!revisionPattern.test(result.id || "") || result.diagnostic_id !== result.id || result.view_id !== result.id
        || !Array.isArray(result.findings) || !Number.isSafeInteger(result.findings_count) || result.findings_count < 0
        || !Number.isSafeInteger(total) || total < 0 || total > result.findings_count
        || result.offset !== (options?.offset ?? 0) || result.offset + result.findings.length > total
        || result.next_offset !== (result.offset + result.findings.length === total ? null : result.offset + result.findings.length)
        || (result.next_offset !== null && result.findings.length === 0)) throw new Error("Diagnostic page differs from its count or offset.");
  }
  if (options?.revision && ["diagnostics", "diagnostic"].includes(action) && result.diagnostic_id !== options.revision) throw new Error("Diagnostic response belongs to another revision.");
  if (action === "diagnostic" && result.finding?.id !== options?.finding) throw new Error("Diagnostic response belongs to another finding.");
}
async function monitor(execute, poll, read, present, request, report, interval = 1000) {
  let finished = false, delivered = false, pending;
  const timer = setInterval(() => {
    if (finished || pending || delivered) return;
    pending = (async () => {
      try {
        const status = await poll();
        if (status.format !== STATUS || status.attempt_id !== request.attempt_id || status.request_id !== request.id) throw new Error("Delivery belongs to another request.");
        if (status.diagnostic_id && !finished) {
          const view = await read(status.diagnostic_id);
          if (view.request_id !== request.id || view.diagnostic_id !== status.diagnostic_id) throw new Error("Diagnostic revision belongs to another request.");
          if (!finished) { delivered = true; present(view); }
        }
      } catch (error) { if (!finished) report(`Early diagnostics unavailable: ${error.message}. Execution continues.`); }
      finally { pending = undefined; }
    })();
  }, interval);
  try { return await execute(); }
  finally { finished = true; clearInterval(timer); if (pending) await pending; }
}
module.exports = { VIEW, STATUS, GROUPS, argumentsFor, validate, monitor };
