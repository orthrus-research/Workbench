"use strict";

const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const { scrubbedEnvironment } = require("./coreClient");
const {
  commandForCoreLaunch,
  pathForCoreLaunch,
  resolveCoreLaunch,
} = require("./coreLaunch");

const MAX_OUTPUT = 4 * 1024 * 1024;
const CONTENT_ID = /^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$/;
const JOB_ID = /^job-v2:[0-9a-f]{32}$/;
const SERVICE_INSTANCE_ID = /^service-instance-v2:[0-9a-f]{32}$/;
const SHA256 = /^[0-9a-f]{64}$/;

function contentId(value, label, kind) {
  if (typeof value !== "string" || !CONTENT_ID.test(value) || !value.startsWith(`${kind}:sha256:`)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function boundedPath(value, label) {
  if (typeof value !== "string" || !value || value.includes("\0") || Buffer.byteLength(value, "utf8") > 32 * 1024) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function jobId(value, label) {
  if (typeof value !== "string" || !JOB_ID.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function serviceInstanceId(value) {
  if (typeof value !== "string" || !SERVICE_INSTANCE_ID.test(value)) {
    throw new Error("service instance ID is invalid");
  }
  return value;
}

function resultArguments(connection, job, launch = null) {
  const mapped = (value, label) => launch
    ? pathForCoreLaunch(value, launch, label)
    : boundedPath(value, label);
  return [
    "feature-service", "result",
    "--endpoint", mapped(connection.endpoint, "service endpoint"),
    "--credential", mapped(connection.credential, "service credential"),
    "--context-ref-id", contentId(job.contextRefId, "context ref ID", "context-ref"),
    "--input-binding-id", contentId(job.inputBindingId, "input binding ID", "input-binding"),
    "--job-id", jobId(job.jobId, "job ID"),
    "--json",
  ];
}

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function validateResult(candidate, expectedJobId) {
  const root = object(candidate, "Feature Studio service result");
  const expectedKeys = [
    "action", "format", "registration", "registry_id", "result",
    "schema_version", "service_instance_id",
  ];
  if (JSON.stringify(Object.keys(root).sort()) !== JSON.stringify(expectedKeys)) {
    throw new Error("Feature Studio service result fields changed");
  }
  if (root.format !== "workbench-feature-studio-service-cli-result-v1"
      || root.schema_version !== 1 || root.action !== "result"
      || root.registration !== null) {
    throw new Error("Feature Studio service result boundary changed");
  }
  const registryId = contentId(root.registry_id, "registry ID", "component-capability-registry");
  const serviceInstance = serviceInstanceId(root.service_instance_id);
  const outcome = object(object(root.result, "service result").outcome, "terminal outcome");
  if (outcome.state !== "succeeded") {
    throw new Error("Feature Studio job is not terminal-complete");
  }
  const wrapper = object(outcome.value, "owner result wrapper");
  const serviceResultId = contentId(wrapper.result_id, "service result ID", "feature-studio-service-result");
  const ownerRequestId = contentId(wrapper.owner_request_id, "owner request ID", "feature-studio-owner-request");
  const operationPlanId = wrapper.operation_plan_id === null
    ? null
    : contentId(wrapper.operation_plan_id, "operation plan ID", "operation-plan");
  const ownerResultId = contentId(wrapper.owner_result_id, "owner result ID", "feature-studio-result");
  const ownerText = wrapper.owner_result_canonical_json;
  const ownerSize = wrapper.owner_result_canonical_size;
  const ownerSha256 = wrapper.owner_result_canonical_sha256;
  if (typeof ownerText !== "string" || Buffer.byteLength(ownerText, "utf8") > MAX_OUTPUT
      || !Number.isSafeInteger(ownerSize) || ownerSize < 1 || ownerSize > MAX_OUTPUT
      || typeof ownerSha256 !== "string" || !SHA256.test(ownerSha256)) {
    throw new Error("Feature Studio owner custody fields are invalid");
  }
  const ownerBytes = Buffer.from(ownerText, "utf8");
  if (ownerBytes.length !== ownerSize
      || crypto.createHash("sha256").update(ownerBytes).digest("hex") !== ownerSha256) {
    throw new Error("Feature Studio owner result bytes changed");
  }
  const ownerResult = object(JSON.parse(ownerText), "canonical owner result");
  if (ownerResult.result_id !== ownerResultId) {
    throw new Error("Feature Studio owner result identity changed");
  }
  return Object.freeze({
    registryId,
    serviceInstanceId: serviceInstance,
    jobId: jobId(expectedJobId, "job ID"),
    serviceResultId,
    ownerRequestId,
    operationPlanId,
    ownerResultId,
    ownerResultCanonicalSha256: ownerSha256,
    ownerResultCanonicalSize: ownerSize,
    ownerResult: JSON.parse(JSON.stringify(ownerResult)),
  });
}

function invokeResult(executable, connection, job, options = {}) {
  const launch = resolveCoreLaunch(executable, options);
  return new Promise((resolve, reject) => {
    childProcess.execFile(
      launch.executable,
      commandForCoreLaunch(launch, resultArguments(connection, job, launch)),
      {
        cwd: launch.host === "native" ? options.cwd : undefined,
        env: scrubbedEnvironment(options.environment),
        encoding: "utf8",
        timeout: 60_000,
        maxBuffer: MAX_OUTPUT,
        windowsHide: true,
        shell: false,
      },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`Feature Studio service call failed: ${String(stderr).slice(0, 2000)}`, { cause: error }));
          return;
        }
        try {
          resolve(validateResult(JSON.parse(stdout), job.jobId));
        } catch (parseError) {
          reject(parseError);
        }
      },
    );
  });
}

module.exports = { invokeResult, resultArguments, validateResult };
