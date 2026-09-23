"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const client = require("../materialSnapshotClient");
const query = require("../../../core/tests/fixtures/check-snapshot-v1/query.json");
const response = require("../../../core/tests/fixtures/check-snapshot-v1/response.json");

test("historical capture outcome and current reader support remain separate in the summary", () => {
  const { materialSummary } = require("../materialChecksClient");
  const text = materialSummary({ format: client.VIEW, state: "completed", native_outcome: "native-failed", coverage: "complete",
    interpretation: { state: "unsupported", unsupported_sections: ["crafting-values"] },
    overview_state: "loaded", detail_state: "not-loaded", findings: [], findings_count: 7 });
  assert.match(text, /Initialization: native-failed; observation coverage: complete/);
  assert.match(text, /Reader interpretation: unsupported; unsupported sections: crafting-values/);
});

test("snapshot pages agree with the independent Core contract fixture", () => {
  assert.equal(client.validatePage(response, query), response);
  assert.equal(client.identity("fixture", { view_id: "vue 🌍 é", cursor: null, offset: 2 }),
    "fixture:sha256:089c2f2c49da75b273674f7755397eacc6525b42c6d5e43be8c0fd248ec88482");
});
test("snapshot page identity refuses another view, snapshot and page", () => {
  for (const patch of [{ snapshot_id: "check-snapshot:sha256:" + "1".repeat(64) }, { view_id: "new-view" },
    { cursor: { snapshot_id: query.snapshot_id, query_id: response.query_id, offset: 1 } }])
    assert.throws(() => client.validatePage(response, { ...query, ...patch }), /selected view or page/);
  for (const mutate of [v => { v.payload.offset = 1; }, v => { v.payload.total = 2; },
    v => { v.complete = false; }, v => { v.next_cursor = { offset: 2 }; }]) {
    const altered = structuredClone(response); mutate(altered);
    assert.throws(() => client.validatePage(altered, query), /count|progress|cursor/);
  }
});
test("unavailable and unsupported evidence never appear as an empty completed page", () => {
  for (const state of ["unavailable", "unsupported", "expired", "cancelled", "incomplete"]) {
    const value = { ...response, state, payload: null, complete: false, reason: "Explicit fixture reason" };
    assert.equal(client.validatePage(value, query), value);
    assert.throws(() => client.validatePage({ ...value, complete: true }, query), /complete data/);
  }
});
test("native diagnostic reads bind their side and original record address", () => {
  const view = { snapshot_id: query.snapshot_id, view_id: query.view_id };
  const read = client.diagnosticQuery(view, { side: "candidate", pointer: "/result/candidate/result/execution/diagnostics/7" });
  assert.equal(read.section_id, "candidate-diagnostics"); assert.equal(read.record_key, "item:7");
  assert.throws(() => client.diagnosticQuery(view, { side: "baseline", pointer: "/result/candidate/result/execution/diagnostics/7" }), /pointer/);
});

test("bulk paging changes query identity while retaining the exact next record offset", () => {
  const view = { snapshot_id: query.snapshot_id, view_id: query.view_id, finding_page: { next_cursor: { offset: 73 } } };
  const bulk = client.nextFindingsQuery(view, true), small = client.nextFindingsQuery(view, false);
  assert.equal(bulk.preferred_bytes, 1048576); assert.equal(small.preferred_bytes, 65536);
  assert.equal(bulk.cursor.offset, 73); assert.notEqual(bulk.cursor.query_id, small.cursor.query_id);
  const { cursor, ...base } = bulk; assert.equal(cursor.query_id, client.identity("check-snapshot-query", base));
});
