"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const test = require("node:test");
const util = require("node:util");

const {
  capsuleInspectionArguments,
  diagnosisArguments,
  invokeCapsuleInspection,
  invokeDiagnosis,
  validateCapsuleInspectionV1,
  validateDiagnosisV1,
} = require("../diagnoseClient");
const { resolveCoreLaunch } = require("../coreLaunch");

const execFile = util.promisify(childProcess.execFile);
const REPOSITORY = path.resolve(__dirname, "../../..");

async function liveFixture() {
  const root = await fs.mkdtemp("/tmp/workbench-release-diagnose-");
  const result = await execFile(
    "python3",
    [
      path.join(REPOSITORY, "clients/testing/diagnose_live_fixture.py"),
      REPOSITORY,
      root,
    ],
    {
      cwd: REPOSITORY,
      encoding: "utf8",
      timeout: 120_000,
      maxBuffer: 8 * 1024 * 1024,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    },
  );
  assert.equal(result.stderr, "");
  return { root, value: JSON.parse(result.stdout) };
}

function nativeLaunch() {
  return Object.freeze({
    configured: "python3",
    coreExecutable: path.join(REPOSITORY, "tools/workbench.py"),
    distribution: null,
    executable: "python3",
    host: "native",
    prefixArguments: Object.freeze([path.join(REPOSITORY, "tools/workbench.py")]),
  });
}

test("installed release client consumes live diagnosis plus capsule inspect/verify without a second executor", async () => {
  const fixture = await liveFixture();
  try {
    const options = { launch: nativeLaunch(), cwd: REPOSITORY };
    const diagnosis = await invokeDiagnosis("python3", fixture.value.session_id, {
      ...options,
      stateRoot: fixture.value.state_root,
    });
    assert.equal(diagnosis.diagnosis_id, fixture.value.diagnosis_id);
    assert.equal(diagnosis.work_session_id, fixture.value.session_id);
    assert.equal(diagnosis.observed_failures[0].claim_state, "observed");
    assert.equal(diagnosis.unknowns[0].claim_state, "unknown");
    assert.equal(diagnosis.next_experiments[0].mutation, "read-only");
    assert.deepEqual(diagnosis.classifications, []);

    const classified = validateDiagnosisV1(fixture.value.classified_diagnosis);
    assert.equal(classified.diagnosis_id, fixture.value.classified_diagnosis.diagnosis_id);
    assert.equal(classified.work_session_id, fixture.value.session_id);
    assert.equal(classified.classifications.length, 1);
    assert.deepEqual(classified.classifications[0], {
      artifact_digest: `sha256:${"e".repeat(64)}`,
      claim_state: "observed",
      classification_id: "cleanroom-dev-loop-stage",
      cleanup_contained: true,
      detail: "The required dedicated-server markers were not observed.",
      effective_exit_code: 0,
      observed_markers: [],
      owner_id: "workbench-shell",
      owner_record_digest: `sha256:${"d".repeat(64)}`,
      owner_record_id: `workbench-cleanroom-dev-loop-receipt:sha256:${"d".repeat(64)}`,
      owner_record_kind: "workbench-cleanroom-dev-loop-receipt",
      owner_record_uri: path.join(fixture.root, "dev-loop/receipt.json").replace(/^/, "file://"),
      required_markers: ["dedicated-server-ready", "common-registry-ready"],
      stage: "server",
      state: "failed",
    });

    const inspected = await invokeCapsuleInspection("python3", fixture.value.capsule_path, options);
    const verified = await invokeCapsuleInspection("python3", fixture.value.capsule_path, {
      ...options,
      operation: "verify",
    });
    assert.equal(inspected.capsule_id, fixture.value.capsule_id);
    assert.equal(inspected.diagnosis_id, diagnosis.diagnosis_id);
    assert.deepEqual(verified, inspected);
    assert.equal(Object.isFrozen(verified.replay_action.arguments), true);

    const changedSession = structuredClone(diagnosis);
    changedSession.work_session_id = `work-session-v2-${"f".repeat(32)}`;
    assert.throws(() => validateDiagnosisV1(changedSession), /content identity|differs/);
    const changedFormat = structuredClone(diagnosis);
    changedFormat.format = "workbench-diagnosis-v2";
    assert.throws(() => validateDiagnosisV1(changedFormat), /format/);
    const elevatedClaim = structuredClone(diagnosis);
    elevatedClaim.observed_failures[0].claim_state = "derived";
    assert.throws(() => validateDiagnosisV1(elevatedClaim), /observation|claim/);
    const extraClassificationField = structuredClone(classified);
    extraClassificationField.classifications[0].approval = true;
    assert.throws(() => validateDiagnosisV1(extraClassificationField), /fields changed/);
    const duplicateMarker = structuredClone(classified);
    duplicateMarker.classifications[0].required_markers.push("dedicated-server-ready");
    assert.throws(() => validateDiagnosisV1(duplicateMarker), /repeats a marker/);
    const changedReceiptDigest = structuredClone(classified);
    changedReceiptDigest.classifications[0].owner_record_digest = `sha256:${"c".repeat(64)}`;
    assert.throws(() => validateDiagnosisV1(changedReceiptDigest), /content identity|differs/);
    const capsuleDrift = structuredClone(inspected);
    capsuleDrift.schema_version = 1;
    assert.throws(() => validateCapsuleInspectionV1(capsuleDrift), /fields changed/);
  } finally {
    await fs.rm(fixture.root, { recursive: true, force: true });
  }
});

test("diagnosis argv maps only path-bearing inputs through the existing Windows/WSL launch", () => {
  const launch = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\Workbench\\workbench",
    {
      platform: "win32",
      environment: { SystemRoot: "C:\\Windows" },
    },
  );
  assert.deepEqual(diagnosisArguments(
    launch,
    "work-session-v2-11111111111111111111111111111111",
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\state",
  ), [
    "diagnose", "work-session-v2-11111111111111111111111111111111",
    "--state-root", "/home/developer/state", "--json",
  ]);
  assert.deepEqual(capsuleInspectionArguments(
    launch,
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\failure.wb-repro",
    "verify",
  ), [
    "diagnose", "reproduce", "verify", "/home/developer/failure.wb-repro", "--json",
  ]);
  assert.throws(() => capsuleInspectionArguments(
    launch,
    "\\\\wsl.localhost\\Debian\\home\\developer\\failure.wb-repro",
  ), /distribution/);
  assert.throws(() => capsuleInspectionArguments(
    launch,
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\failure.wb-repro",
    "create",
  ), /unsupported/);
});
