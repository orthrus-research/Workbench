"use strict";

const childProcess = require("node:child_process");
const {
  commandForCoreLaunch,
  pathForCoreLaunch,
  resolveCoreLaunch,
} = require("./coreLaunch");
const { scrubbedEnvironment } = require("./coreClient");

const MAX_OUTPUT = 48 * 1024 * 1024;
const PLAN_ID = /^workbench-developer-material-fluid-recipe-plan:sha256:[0-9a-f]{64}$/;
const RUN_ID = /^workbench-developer-material-fluid-recipe-run:sha256:[0-9a-f]{64}$/;
const ATTEMPT_ID = /^uuid:[0-9a-f]{32}$/;
const ASSERTIONS = Object.freeze([
  "fluid_registration",
  "fml_client_load",
  "groovy_compilation",
  "localization",
  "material_registration",
  "recipe_registration",
]);
const ASSERTION_MEANINGS = Object.freeze({
  fluid_registration: "the material-backed Forge fluid identity is registered",
  fml_client_load: "the disposable client reaches the exact FML loaded marker",
  groovy_compilation: "the changed Groovy program compiles in the projected client",
  localization: "the requested client translation resolves to its intended label",
  material_registration: "the requested GregTech material identity is registered",
  recipe_registration: "the exact reviewed machine recipe is registered once in its selected map",
});
const OUTCOMES = Object.freeze([
  "runtime-completed",
  "runtime-assertion-failed",
  "runtime-incomplete",
  "failed",
]);

function plainObject(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, expected, label) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
    throw new Error(`${label} fields changed`);
  }
}

function bounded(value, label, optional = false) {
  if (typeof value !== "string" || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 32 * 1024
      || (!optional && !value.trim())) {
    throw new Error(`${label} is invalid`);
  }
  return value.trim();
}

function positiveNumber(value, label, fallback, maximum = Number.MAX_VALUE) {
  const selected = value === undefined ? fallback : value;
  if (typeof selected !== "number" || !Number.isFinite(selected)
      || selected <= 0 || selected > maximum) {
    throw new Error(`${label} must be between 0 and ${maximum}`);
  }
  return selected;
}

function positiveInteger(value, label, fallback) {
  const selected = value === undefined ? fallback : value;
  if (!Number.isSafeInteger(selected) || selected <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return selected;
}

function corePath(value, launch, label, optional = false) {
  const selected = bounded(value || "", label, optional);
  if (!selected) return null;
  return pathForCoreLaunch(selected, launch, label);
}

function argumentsForRun(launch, options) {
  const plan = bounded(options.plan, "reviewed plan ID");
  if (!PLAN_ID.test(plan)) {
    throw new Error("reviewed plan ID is invalid");
  }
  const launcher = options.launcher || "prism";
  if (launcher !== "prism" && launcher !== "multimc") {
    throw new Error("launcher must be prism or multimc");
  }
  const arguments_ = [
    "feature", "run", "material-fluid-recipe", plan,
    "--consent", plan,
    "--launcher", launcher,
    "--launcher-executable", corePath(options.launcherExecutable, launch, "launcher executable"),
    "--launcher-root", corePath(options.launcherRoot, launch, "launcher root"),
  ];
  for (const [flag, value, label] of [
    ["--launcher-java", options.launcherJava, "launcher Java"],
    ["--launcher-java-state", options.launcherJavaState, "launcher Java state"],
    ["--packwiz-executable", options.packwizExecutable, "Packwiz executable"],
    ["--state-root", options.stateRoot, "feature state root"],
  ]) {
    const selected = corePath(value, launch, label, true);
    if (selected) arguments_.push(flag, selected);
  }
  if (options.seedRoots !== undefined && !Array.isArray(options.seedRoots)) {
    throw new Error("seed roots must be an array");
  }
  for (const value of options.seedRoots || []) {
    arguments_.push("--seed", corePath(value, launch, "seed root"));
  }
  const launcherProfile = bounded(options.launcherProfile || "", "launcher profile", true);
  if (launcherProfile && ([...launcherProfile].length > 128 || /[\x00-\x1f]/.test(launcherProfile))) {
    throw new Error("launcher profile must be printable and at most 128 characters");
  }
  if (launcherProfile) arguments_.push("--launcher-profile", launcherProfile);
  const memoryMiB = positiveInteger(options.memoryMiB, "memory", 8192);
  if (memoryMiB < 1024 || memoryMiB > 131072) {
    throw new Error("memory must be between 1024 and 131072 MiB");
  }
  const offlineName = bounded(
    options.offlineName === undefined ? "Workbench" : options.offlineName,
    "offline name",
  );
  if (!launcherProfile && !/^[A-Za-z0-9_]{3,16}$/.test(offlineName)) {
    throw new Error("offline name must be 3-16 letters, digits, or underscores");
  }
  arguments_.push(
    "--memory-mib", String(memoryMiB),
    "--offline-name", offlineName,
    "--timeout", String(positiveNumber(options.timeoutSeconds, "launch timeout", 600, 86400)),
    "--attach-timeout", String(positiveNumber(options.attachTimeoutSeconds, "attach timeout", 120, 600)),
    "--session-timeout", String(positiveNumber(options.sessionTimeoutSeconds, "session timeout", 21600, 86400)),
    "--json",
  );
  return arguments_;
}

function parseResult(text, expectedPlan) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") > MAX_OUTPUT) {
    throw new Error("developer feature output exceeds 48 MiB");
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new Error("developer feature output is invalid JSON", { cause: error });
  }
  const receipt = plainObject(value, "developer feature receipt");
  exactKeys(receipt, [
    "assertions", "attempt_id", "format", "id", "kind", "limitations",
    "observation_probe", "operation_class", "outcome", "plan_id",
    "profile_observation", "runtime", "schema_version", "source", "stage",
    "state", "target",
  ], "developer feature receipt");
  if (!PLAN_ID.test(expectedPlan || "")
      || receipt.format !== "workbench-developer-material-fluid-recipe-run-v1"
      || receipt.schema_version !== 1
      || receipt.kind !== "workbench-developer-material-fluid-recipe-run"
      || receipt.operation_class !== "local-disposable-runtime"
      || receipt.plan_id !== expectedPlan
      || !RUN_ID.test(receipt.id || "")
      || !ATTEMPT_ID.test(receipt.attempt_id || "")
      || !["complete", "incomplete"].includes(receipt.state)
      || !OUTCOMES.includes(receipt.outcome)) {
    throw new Error("developer feature receipt identity changed");
  }
  const assertions = plainObject(receipt.assertions, "developer feature assertions");
  exactKeys(assertions, ASSERTIONS, "developer feature assertions");
  const states = {};
  for (const name of ASSERTIONS) {
    const assertion = plainObject(assertions[name], `developer feature assertion ${name}`);
    exactKeys(assertion, ["meaning", "state"], `developer feature assertion ${name}`);
    if (assertion.meaning !== ASSERTION_MEANINGS[name]
        || !["pending", "observed", "failed", "not-observed"].includes(assertion.state)) {
      throw new Error(`developer feature assertion ${name} is invalid`);
    }
    states[name] = assertion.state;
  }
  const allObserved = ASSERTIONS.every((name) => states[name] === "observed");
  const complete = receipt.state === "complete";
  if (complete !== (receipt.outcome === "runtime-completed" && allObserved)) {
    throw new Error("developer feature completion contradicts its assertions");
  }
  const target = plainObject(receipt.target, "developer feature target");
  exactKeys(target, ["attempt_root_uri", "receipt_uri"], "developer feature target");
  if (!Object.values(target).every((item) => typeof item === "string" && item.startsWith("file:"))) {
    throw new Error("developer feature target binding changed");
  }
  const source = plainObject(receipt.source, "developer feature source");
  if (source.plan_id !== expectedPlan || typeof source.workspace_uri !== "string"
      || !source.workspace_uri.startsWith("file:")) {
    throw new Error("developer feature source binding changed");
  }
  if (!Array.isArray(receipt.limitations) || receipt.limitations.length === 0
      || receipt.limitations.some((item) => typeof item !== "string" || !item)) {
    throw new Error("developer feature limitations changed");
  }
  return Object.freeze({
    complete,
    id: receipt.id,
    outcome: receipt.outcome,
    planId: receipt.plan_id,
    states: Object.freeze(states),
    value: receipt,
  });
}

function invokeRun(executable, options = {}) {
  const launch = resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const arguments_ = argumentsForRun(launch, options);
  const expectedPlan = bounded(options.plan, "reviewed plan ID");
  const sessionSeconds = positiveNumber(options.sessionTimeoutSeconds, "session timeout", 21600);
  return new Promise((resolve, reject) => {
    childProcess.execFile(
      launch.executable,
      commandForCoreLaunch(launch, arguments_),
      {
        cwd: launch.host === "native" ? options.cwd : undefined,
        env: scrubbedEnvironment(options.environment),
        encoding: "utf8",
        timeout: options.timeoutMs || Math.ceil((sessionSeconds + 900) * 1000),
        maxBuffer: MAX_OUTPUT,
        windowsHide: true,
        shell: false,
      },
      (error, stdout, stderr) => {
        let result;
        try {
          result = parseResult(stdout, expectedPlan);
        } catch (parseError) {
          const detail = typeof stderr === "string" ? stderr.trim() : "";
          reject(new Error(detail || parseError.message, { cause: error || parseError }));
          return;
        }
        const expectedExit = result.complete
          ? error === null
          : error !== null && error.code === 1 && !error.killed && !error.signal;
        if (!expectedExit) {
          reject(new Error("developer feature exit status conflicts with its receipt", { cause: error || undefined }));
          return;
        }
        resolve(result);
      },
    );
  });
}

module.exports = {
  ASSERTIONS,
  argumentsForRun,
  invokeRun,
  parseResult,
};
