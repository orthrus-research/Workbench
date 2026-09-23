"use strict";

const fs = require("node:fs");
const { pathToFileURL } = require("node:url");
const { invokeCoreJson } = require("./coreCommandClient");
const { resolveCoreLaunch } = require("./coreLaunch");

function reviewArguments(session, baseline, expected) {
  if (typeof session !== "string" || !/^work-session-v2-[0-9a-f]{32}$/.test(session)) {
    throw new Error("Select one exact Work Session ID.");
  }
  if (typeof baseline !== "string" || !baseline || baseline.length > 1024 || /[\0\r\n]/.test(baseline)) {
    throw new Error("Select an explicit local Git baseline.");
  }
  const args = ["context", "run", session, "--", "review", "local", `--baseline-ref=${baseline}`];
  if (expected !== undefined) {
    if (!/^source-review:sha256:[0-9a-f]{64}$/.test(expected)) throw new Error("Invalid review identity.");
    args.push("--expect-review", expected);
  }
  return args;
}

function validateReview(value, root) {
  const result = value?.result;
  if (value?.format !== "workbench-developer-action-v1" || value.exit_code !== 0
      || value.context?.selection?.pack_uri !== root || result?.candidate?.root_uri !== root
      || result?.baseline?.root_uri !== root || result?.format !== "workbench-local-source-review-v1"
      || !/^source-review:sha256:[0-9a-f]{64}$/.test(result.review_id)
      || !/^[0-9a-f]{40}([0-9a-f]{24})?$/.test(result.baseline.revision)
      || result.authority?.runtime_authority !== "none" || result.authority?.source_mutated !== false
      || result.authority?.construction_authorized !== false || !Array.isArray(result.files)
      || !Array.isArray(result.changes) || !Array.isArray(result.findings) || !Array.isArray(result.checks)) {
    throw new Error("Local review changed the selected workspace or source-only contract.");
  }
  for (const row of result.files) {
    if (typeof row.path !== "string" || /[\\:\0\r\n]/.test(row.path)
        || row.path.split("/").some((part) => !part || part === "." || part === "..")) throw new Error("Unsafe reviewed path.");
  }
  return result;
}

async function invokeLocalReview(executable, session, baseline, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable);
  if (launch.host !== "native") throw new Error("Local review currently requires a native Linux Workbench host.");
  const value = await invokeCoreJson(executable, reviewArguments(session, baseline, options.expected), {
    ...options, launch, maximumOutput: 16 * 1024 * 1024, timeoutMs: 120000, label: "Local source review",
  });
  return validateReview(value, pathToFileURL(fs.realpathSync(options.cwd)).href);
}

class ReviewEpoch {
  constructor() { this.sequence = 0; this.controller = undefined; }
  invalidate() { this.sequence += 1; this.controller?.abort(); this.controller = undefined; }
  begin() { this.invalidate(); this.controller = new AbortController(); return { sequence: this.sequence, signal: this.controller.signal }; }
  current(ticket) { return ticket.sequence === this.sequence && !ticket.signal.aborted; }
}

module.exports = { reviewArguments, validateReview, invokeLocalReview, ReviewEpoch };
