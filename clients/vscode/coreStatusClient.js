"use strict";

const childProcess = require("node:child_process");
const {
  commandForCoreLaunch,
  resolveCoreLaunch,
} = require("./coreLaunch");
const { scrubbedEnvironment } = require("./coreClient");

const MAXIMUM_VERSION_BYTES = 1024 * 1024;
const VERSION_TIMEOUT_MS = 10 * 1000;
const SETUP_TIMEOUT_MS = 60 * 1000;
const COMPONENT_VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:(?:a|b|rc)[1-9][0-9]*)?$/;

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function string(value, label) {
  if (typeof value !== "string" || !value || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 16 * 1024) {
    throw new Error(`${label} must be one bounded non-empty string`);
  }
  return value;
}

function exactKeys(value, expected, label) {
  const keys = Object.keys(value);
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) {
    throw new Error(`${label} fields are unsupported`);
  }
}

function validateCoreVersion(value) {
  const root = object(value, "Workbench version response");
  exactKeys(root, new Set(["component_id", "version"]), "Workbench version response");
  if (root.component_id !== "workbench-core") {
    throw new Error("the selected command is not a Workbench core");
  }
  const currentVersion = string(root.version, "Workbench version");
  if (!COMPONENT_VERSION.test(currentVersion) || currentVersion.trim() !== currentVersion) {
    throw new Error("Workbench version must be a native component version");
  }
  return Object.freeze({
    currentVersion,
    value: root,
  });
}

function stringArray(value, label) {
  if (!Array.isArray(value) || value.length > 1024
      || value.some((item) => typeof item !== "string" || !item || item.includes("\0"))) {
    throw new Error(`${label} must be a bounded string array`);
  }
  return Object.freeze([...value]);
}

function validateSetupCheck(value) {
  const root = object(value, "Workbench setup check");
  if (root.format !== "workbench-setup-check-v1" || root.schema_version !== 1) {
    throw new Error("the installed command returned an unsupported Workbench setup check");
  }
  if (typeof root.configured !== "boolean") {
    throw new Error("Workbench setup configured state must be boolean");
  }
  const state = string(root.state, "Workbench setup state");
  if (!new Set(["ready", "installable", "attention"]).has(state)) {
    throw new Error("Workbench setup state is unsupported");
  }
  return Object.freeze({
    blockers: stringArray(root.blockers, "Workbench setup blockers"),
    configured: root.configured,
    managedInstallsAvailable: stringArray(
      root.managed_installs_available,
      "Workbench managed setup installs",
    ),
    ready: root.configured && state === "ready",
    state,
    value: root,
  });
}

function invokeCoreVersion(executable, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable);
  const arguments_ = commandForCoreLaunch(launch, ["version", "--json"]);
  return new Promise((resolve, reject) => {
    childProcess.execFile(launch.executable, arguments_, {
      cwd: launch.host === "native" ? options.cwd : undefined,
      encoding: "utf8",
      env: scrubbedEnvironment(options.environment),
      maxBuffer: MAXIMUM_VERSION_BYTES,
      timeout: options.timeoutMs === undefined ? VERSION_TIMEOUT_MS : options.timeoutMs,
      windowsHide: true,
      shell: false,
    }, (error, stdout, stderr) => {
      const detail = typeof stderr === "string" ? stderr.trim() : "";
      if (error) {
        reject(new Error(detail || `could not run ${executable}: ${error.message}`, { cause: error }));
        return;
      }
      if (detail) {
        reject(new Error(`Workbench version returned unexpected stderr: ${detail}`));
        return;
      }
      try {
        resolve(validateCoreVersion(JSON.parse(stdout)));
      } catch (parseError) {
        reject(new Error(
          `Workbench version could not be validated: ${parseError instanceof Error ? parseError.message : String(parseError)}`,
          { cause: parseError },
        ));
      }
    });
  });
}

function invokeSetupCheck(executable, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable);
  const arguments_ = commandForCoreLaunch(launch, ["setup", "--check", "--json"]);
  return new Promise((resolve, reject) => {
    childProcess.execFile(launch.executable, arguments_, {
      cwd: launch.host === "native" ? options.cwd : undefined,
      encoding: "utf8",
      env: scrubbedEnvironment(options.environment),
      maxBuffer: MAXIMUM_VERSION_BYTES,
      timeout: options.timeoutMs === undefined ? SETUP_TIMEOUT_MS : options.timeoutMs,
      windowsHide: true,
      shell: false,
    }, (error, stdout, stderr) => {
      const exitCode = error && Number.isInteger(error.code) ? error.code : 0;
      const detail = typeof stderr === "string" ? stderr.trim() : "";
      if (error && exitCode !== 1) {
        reject(new Error(detail || `could not check Workbench setup: ${error.message}`, { cause: error }));
        return;
      }
      if (detail) {
        reject(new Error(`Workbench setup check returned unexpected stderr: ${detail}`));
        return;
      }
      try {
        const result = validateSetupCheck(JSON.parse(stdout));
        resolve(Object.freeze({ ...result, exitCode }));
      } catch (parseError) {
        reject(new Error(
          `Workbench setup check could not be validated: ${parseError instanceof Error ? parseError.message : String(parseError)}`,
          { cause: parseError },
        ));
      }
    });
  });
}

module.exports = {
  invokeCoreVersion,
  invokeSetupCheck,
  validateCoreVersion,
  validateSetupCheck,
};
