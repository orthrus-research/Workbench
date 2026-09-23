"use strict";

const childProcess = require("node:child_process");
const {
  commandForCoreLaunch,
  pathForCoreLaunch,
  resolveCoreLaunch,
} = require("./coreLaunch");
const { scrubbedEnvironment } = require("./coreClient");

const BASELINE_OPTIONS = Object.freeze({
  directory: "--baseline",
  ref: "--baseline-ref",
  "pr-base": "--pr-base",
});
const SIDES = new Set(["dedicated-server", "integrated-server", "client"]);
const MAXIMUM_REVIEW_BYTES = 48 * 1024 * 1024;
const REVIEW_TIMEOUT_MS = 30 * 60 * 1000;

function bounded(value, label) {
  if (typeof value !== "string" || !value.trim() || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 32 * 1024) {
    throw new Error(`${label} is invalid`);
  }
  return value.trim();
}

function recipeReviewArguments(request, launch) {
  if (request === null || typeof request !== "object" || Array.isArray(request)) {
    throw new Error("recipe review request is invalid");
  }
  const baselineFlag = BASELINE_OPTIONS[request.baselineMode];
  if (!baselineFlag) throw new Error("recipe review baseline mode is invalid");
  const side = request.side === undefined ? "dedicated-server" : request.side;
  if (!SIDES.has(side)) throw new Error("recipe review side is invalid");
  const source = pathForCoreLaunch(bounded(request.source, "recipe review source"), launch,
    "recipe review source");
  const baselineRaw = bounded(request.baseline, "recipe review baseline");
  const baseline = request.baselineMode === "directory"
    ? pathForCoreLaunch(baselineRaw, launch, "recipe review baseline")
    : baselineRaw;
  return Object.freeze([
    "review", "recipes",
    "--profile", "supersymmetry",
    baselineFlag, baseline,
    "--source", source,
    "--side", side,
  ]);
}

function invokeRecipeReview(executable, request, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable);
  const arguments_ = commandForCoreLaunch(launch, recipeReviewArguments(request, launch));
  return new Promise((resolve, reject) => {
    childProcess.execFile(launch.executable, arguments_, {
      cwd: launch.host === "native" ? options.cwd : undefined,
      encoding: "utf8",
      env: scrubbedEnvironment(options.environment),
      maxBuffer: MAXIMUM_REVIEW_BYTES,
      timeout: options.timeoutMs === undefined ? REVIEW_TIMEOUT_MS : options.timeoutMs,
      windowsHide: true,
      shell: false,
    }, (error, stdout, stderr) => {
      const detail = typeof stderr === "string" ? stderr.trim() : "";
      if (error) {
        reject(new Error(detail || `Workbench recipe review failed: ${error.message}`, { cause: error }));
        return;
      }
      if (detail) {
        reject(new Error(`Workbench recipe review returned unexpected stderr: ${detail}`));
        return;
      }
      if (typeof stdout !== "string" || !stdout.trim()) {
        reject(new Error("Workbench recipe review returned no report"));
        return;
      }
      resolve(stdout);
    });
  });
}

module.exports = {
  invokeRecipeReview,
  recipeReviewArguments,
};
