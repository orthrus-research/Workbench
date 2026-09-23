"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const test = require("node:test");
const client = require("../featureServiceClient");

const job = {
  contextRefId: `context-ref:sha256:${"1".repeat(64)}`,
  inputBindingId: `input-binding:sha256:${"2".repeat(64)}`,
  jobId: `job-v2:${"3".repeat(32)}`,
  jobSubmissionId: `job-submission:sha256:${"4".repeat(64)}`,
};
const connection = { endpoint: "/tmp/service.sock", credential: "/tmp/credential" };

test("Feature Studio result uses one exact no-shell argv", () => {
  assert.deepEqual(client.resultArguments(connection, job), [
    "feature-service", "result", "--endpoint", connection.endpoint,
    "--credential", connection.credential, "--context-ref-id", job.contextRefId,
    "--input-binding-id", job.inputBindingId, "--job-id", job.jobId, "--json",
  ]);
});

test("Feature Studio result preserves terminal owner bytes and IDs", () => {
  const owner = { result_id: `feature-studio-result:sha256:${"5".repeat(64)}`, state: "complete" };
  const ownerText = JSON.stringify(owner);
  const value = {
    action: "result",
    format: "workbench-feature-studio-service-cli-result-v1",
    registration: null,
    registry_id: `component-capability-registry:sha256:${"6".repeat(64)}`,
    result: { outcome: { state: "succeeded", value: {
      result_id: `feature-studio-service-result:sha256:${"8".repeat(64)}`,
      owner_request_id: `feature-studio-owner-request:sha256:${"9".repeat(64)}`,
      operation_plan_id: `operation-plan:sha256:${"a".repeat(64)}`,
      owner_result_id: owner.result_id,
      owner_result_canonical_json: ownerText,
      owner_result_canonical_sha256: crypto.createHash("sha256").update(ownerText).digest("hex"),
      owner_result_canonical_size: Buffer.byteLength(ownerText),
    } } },
    schema_version: 1,
    service_instance_id: `service-instance-v2:${"7".repeat(32)}`,
  };
  assert.equal(client.validateResult(value, job.jobId).ownerResultId, owner.result_id);
  value.result.outcome.value.owner_result_canonical_size += 1;
  assert.throws(() => client.validateResult(value, job.jobId), /bytes changed/);
});
