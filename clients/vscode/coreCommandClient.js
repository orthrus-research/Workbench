"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const { scrubbedEnvironment } = require("./coreClient");
const {
  commandForCoreLaunch,
  resolveCoreLaunch,
} = require("./coreLaunch");

const MAX_CORE_OUTPUT = 48 * 1024 * 1024;
const MAX_CORE_STDERR = 64 * 1024;
const MAX_TIMEOUT_MS = 12 * 60 * 60 * 1000;

function positiveBoundedInteger(value, label, maximum) {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
    throw new Error(`${label} must be between 1 and ${maximum}`);
  }
  return value;
}

function boundedWorkingDirectory(value) {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !value || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 32 * 1024) {
    throw new Error("core working directory is invalid");
  }
  return value;
}

function boundedText(value, maximum, label, allowEmpty = true) {
  if (typeof value !== "string" || (!allowEmpty && !value)
      || (maximum !== null && Buffer.byteLength(value, "utf8") > maximum)) {
    throw new Error(`${label} exceeds its ${maximum} byte boundary`);
  }
  return value;
}

function failureDetail(error, stderr, stdout) {
  const detail = typeof stderr === "string" ? stderr.trim() : "";
  if (detail) return detail.slice(0, 4000);
  // Core's command boundary returns a structured refusal on stdout with a
  // nonzero exit. Preserve its explanation without accepting it as success or
  // replacing cancellation/process failures with an arbitrary output payload.
  if (Number.isSafeInteger(error?.code) && error.code !== 0 && !error.killed && !error.signal
      && typeof stdout === "string" && Buffer.byteLength(stdout, "utf8") <= MAX_CORE_STDERR) {
    try {
      const refusal = JSON.parse(stdout);
      if (refusal?.state === "unavailable" && typeof refusal.reason === "string"
          && refusal.reason.trim() && Object.keys(refusal).length === 2) {
        return refusal.reason.trim().slice(0, 4000);
      }
    } catch (_) { /* Retain the process failure for non-protocol output. */ }
  }
  if (error && typeof error.message === "string") return error.message.slice(0, 4000);
  return "installed core invocation failed";
}

function linuxProcessIdentity(pid) {
  if (process.platform !== "linux" || !Number.isSafeInteger(pid) || pid < 1) return null;
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    const close = stat.lastIndexOf(")");
    const fields = close < 0 ? [] : stat.slice(close + 2).trim().split(/\s+/);
    const groupId = Number(fields[2]);
    const startTimeTicks = Number(fields[19]);
    return Number.isSafeInteger(groupId) && groupId > 0
      && Number.isSafeInteger(startTimeTicks) && startTimeTicks > 0
      ? Object.freeze({ groupId, startTimeTicks })
      : null;
  } catch (_error) {
    return null;
  }
}

/**
 * Invoke one exact Workbench argv without a shell. JSON protocols use
 * rejectStderr=true so a successful response cannot silently carry a second
 * diagnostic channel.
 */
function invokeCoreText(executable, arguments_, options = {}) {
  const maximumOutput = options.maximumOutput === null ? Infinity : positiveBoundedInteger(
    options.maximumOutput === undefined ? MAX_CORE_OUTPUT : options.maximumOutput,
    "core output boundary",
    MAX_CORE_OUTPUT,
  );
  const timeoutMs = options.timeoutMs === null ? 0 : positiveBoundedInteger(
    options.timeoutMs === undefined ? 120_000 : options.timeoutMs,
    "core timeout",
    MAX_TIMEOUT_MS,
  );
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const cwd = launch.host === "native"
    ? boundedWorkingDirectory(options.cwd)
    : undefined;
  if (options.observeInvocation !== undefined
      && typeof options.observeInvocation !== "function") {
    throw new Error("core invocation observer must be callable");
  }
  return new Promise((resolve, reject) => {
    const startedAt = new Date().toISOString();
    const coreArguments = commandForCoreLaunch(launch, arguments_);
    let child;
    const abortGroup = () => {
      if (!child || child.exitCode !== null) return;
      const identity = linuxProcessIdentity(child.pid);
      if (identity && identity.groupId === child.workbenchProcessGroupId
          && identity.startTimeTicks === child.workbenchProcessStartTimeTicks) {
        try { process.kill(-identity.groupId, "SIGTERM"); } catch (_) { /* Already exited. */ }
      }
    };
    child = childProcess.execFile(
      launch.executable,
      coreArguments,
      {
        cwd,
        env: scrubbedEnvironment(options.environment),
        encoding: "utf8",
        timeout: timeoutMs,
        maxBuffer: maximumOutput,
        detached: true,
        windowsHide: true,
        shell: false,
        signal: options.signal,
      },
      (error, stdout, stderr) => {
        options.signal?.removeEventListener("abort", abortGroup);
        const finishedAt = new Date().toISOString();
        if (options.observeInvocation) {
          try {
            options.observeInvocation(Object.freeze({
              argv: Object.freeze([launch.executable, ...coreArguments]),
              cwd: cwd === undefined ? null : cwd,
              exit_code: Number.isSafeInteger(child.exitCode)
                ? child.exitCode
                : (Number.isSafeInteger(error?.code) ? error.code : null),
              finished_at: finishedAt,
              parent_pid: process.pid,
              pgid: child.workbenchProcessGroupId,
              pid: child.pid,
              process_start_time_ticks: child.workbenchProcessStartTimeTicks,
              signal: child.signalCode || error?.signal || null,
              started_at: startedAt,
              stderr,
              stdout,
            }));
          } catch (observationError) {
            reject(observationError);
            return;
          }
        }
        try {
          boundedText(stdout, maximumOutput, "core stdout");
          boundedText(stderr, options.maximumOutput === null ? null : MAX_CORE_STDERR, "core stderr");
        } catch (boundaryError) {
          reject(boundaryError);
          return;
        }
        if (error) {
          reject(new Error(`Workbench core failed: ${failureDetail(error, stderr, stdout)}`, { cause: error }));
          return;
        }
        if (options.rejectStderr !== false && stderr.trim()) {
          reject(new Error(`Workbench core returned unexpected stderr: ${stderr.trim().slice(0, 4000)}`));
          return;
        }
        resolve(stdout);
      },
    );
    const identity = linuxProcessIdentity(child.pid);
    child.workbenchProcessGroupId = identity?.groupId || child.pid;
    child.workbenchProcessStartTimeTicks = identity?.startTimeTicks || null;
    options.signal?.addEventListener("abort", abortGroup, { once: true });
    if (options.signal?.aborted) abortGroup();
  });
}

function parseBoundedJson(text, maximumOutput, label) {
  boundedText(text, maximumOutput, `${label} output`, false);
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`${label} output is invalid JSON`, { cause: error });
  }
}

async function invokeCoreJson(executable, arguments_, options = {}) {
  const maximumOutput = options.maximumOutput === undefined
    ? 8 * 1024 * 1024
    : options.maximumOutput;
  const text = await invokeCoreText(executable, arguments_, {
    ...options,
    maximumOutput,
    rejectStderr: true,
  });
  return parseBoundedJson(text, maximumOutput, options.label || "Workbench JSON");
}

module.exports = {
  MAX_CORE_OUTPUT,
  MAX_TIMEOUT_MS,
  invokeCoreJson,
  invokeCoreText,
  parseBoundedJson,
};
