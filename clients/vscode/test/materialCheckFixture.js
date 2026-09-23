"use strict";
const session = "work-session-v2-" + "a".repeat(32), attempt = "material-check-" + "b".repeat(32), request = "material-check-request:sha256:" + "c".repeat(64);
function result() {
  return { format: "workbench-material-check-result-v1", id: "result", attempt_id: attempt, state: "completed", findings: [],
    authority: { source_mutated: false, minecraft_launched: false, runtime_image_required: false, validity_qualified: false, whole_pack_parity: false },
    native: { status: "rejected", result: { nativeOutcome: "completed-without-observed-error", qualification: "pending-native-program-acceptance",
      expectations: { status: "mismatch", checks: [{ id: "plate", material: "supersymmetry:test", status: "mismatch", expected: true, observed: false }] } } } };
}
module.exports = { result, session, attempt, request };
