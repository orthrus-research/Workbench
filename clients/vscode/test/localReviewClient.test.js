"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { reviewArguments, validateReview, ReviewEpoch } = require("../localReviewClient");
const session = "work-session-v2-" + "a".repeat(32);

test("local review uses explicit session and baseline with no apply or launch command", () => {
  assert.deepEqual(reviewArguments(session, "HEAD"), ["context", "run", session, "--", "review", "local", "--baseline-ref=HEAD"]);
  assert.throws(() => reviewArguments("latest", "HEAD"), /exact/);
  assert.throws(() => reviewArguments(session, "HEAD\nmain"), /baseline/);
  assert.throws(() => reviewArguments(session, "HEAD", "latest"), /identity/);
});

test("saved edits and cancelled or superseded requests cannot publish old findings", () => {
  const epoch = new ReviewEpoch();
  const first = epoch.begin();
  const second = epoch.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(epoch.current(first), false);
  assert.equal(epoch.current(second), true);
  epoch.invalidate();
  assert.equal(epoch.current(second), false);
});

test("client rejects wrong workspace, unsafe paths, and promoted authority", () => {
  const root = "file:///tmp/pack";
  const result = { format: "workbench-local-source-review-v1", review_id: "source-review:sha256:" + "a".repeat(64),
    baseline: { root_uri: root, revision: "a".repeat(40) }, candidate: { root_uri: root },
    authority: { runtime_authority: "none", source_mutated: false, construction_authorized: false },
    files: [], changes: [], findings: [], checks: [] };
  const envelope = { format: "workbench-developer-action-v1", exit_code: 0, context: { selection: { pack_uri: root } }, result };
  assert.equal(validateReview(envelope, root), result);
  assert.throws(() => validateReview(envelope, "file:///elsewhere"), /workspace/);
  result.files.push({ path: "../escape" });
  assert.throws(() => validateReview(envelope, root), /Unsafe/);
  result.files = [];
  result.authority.source_mutated = true;
  assert.throws(() => validateReview(envelope, root), /contract/);
});
