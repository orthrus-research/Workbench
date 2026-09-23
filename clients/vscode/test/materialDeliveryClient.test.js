"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const delivery = require("../materialDeliveryClient");
const { materialArguments } = require("../materialChecksClient");
const { session, attempt, request } = require("./materialCheckFixture");
const revision = "check-diagnostics:sha256:" + "a".repeat(64);
const prepared = { attempt_id: attempt, id: request };
const status = () => ({ format: delivery.STATUS, attempt_id: attempt, request_id: request, diagnostic_id: revision, detail_state: "preparing" });

test("group pages retain global totals and reject crossed group responses", () => {
  const counts = Object.fromEntries(delivery.GROUPS.map(group => [group, 0])); counts["error-unlocated"] = 1; counts["warning-located"] = 900;
  const view = { format: delivery.VIEW, id: revision, diagnostic_id: revision, view_id: revision, group: "error-unlocated", group_count: 1, finding_counts: counts, findings_count: 901, findings: [{}], offset: 0, next_offset: null };
  const options = { revision, group: "error-unlocated" };
  delivery.validate(view, "diagnostics", options);
  assert.deepEqual(delivery.argumentsFor("diagnostics", options).slice(-2), ["--group", "error-unlocated"]);
  assert.throws(() => delivery.validate(view, "diagnostics", { ...options, group: "error-located" }));
  assert.throws(() => delivery.validate({ ...view, finding_counts: undefined }, "diagnostics", options));
  assert.throws(() => delivery.validate({ ...view, findings_count: 1 }, "diagnostics", options));
  assert.throws(() => delivery.argumentsFor("diagnostics", { ...options, group: "invented" }));
});

test("early presentation happens while execution remains pending and is delivered once", async () => {
  let complete, show; const shown = new Promise(resolve => { show = resolve; });
  let presentations = 0, reads = 0;
  const running = delivery.monitor(() => new Promise(resolve => { complete = resolve; }), async () => status(),
    async selected => { reads++; assert.equal(selected, revision); return { request_id: request, diagnostic_id: revision }; },
    value => { presentations++; show(value); }, prepared, message => assert.fail(message), 1);
  await shown;
  assert.equal(presentations, 1); assert.equal(reads, 1);
  complete({ snapshot: "finished" }); assert.deepEqual(await running, { snapshot: "finished" });
  assert.equal(presentations, 1);
});

test("wrong-request status is reported without replacing the retained execution result", async () => {
  let complete, report; const reported = new Promise(resolve => { report = resolve; });
  const running = delivery.monitor(() => new Promise(resolve => { complete = resolve; }),
    async () => ({ ...status(), request_id: "another-request" }), () => assert.fail("must not read"),
    () => assert.fail("must not present"), prepared, report, 1);
  assert.match(await reported, /another request/); complete("done"); assert.equal(await running, "done");
});

test("a late poll cannot reopen a view after the execution result wins", async () => {
  let complete, polled, release; const observed = new Promise(resolve => { polled = resolve; });
  const running = delivery.monitor(() => new Promise(resolve => { complete = resolve; }),
    () => { polled(); return new Promise(resolve => { release = resolve; }); },
    () => assert.fail("late read"), () => assert.fail("late presentation"), prepared, () => {}, 1);
  await observed; complete("final"); await Promise.resolve(); release(status());
  assert.equal(await running, "final");
});

test("commands bind independent diagnostic pages and source navigation to a revision", () => {
  assert.deepEqual(materialArguments(session, "diagnostics", attempt, { revision, offset: 128 }).slice(-5), [attempt, "--revision", revision, "--offset", "128"]);
  assert.deepEqual(materialArguments(session, "source", attempt, { revision, finding: "diagnostic-7" }).slice(-4), ["--revision", revision, "--diagnostic", "diagnostic-7"]);
  assert.throws(() => materialArguments(session, "diagnostics", attempt, { revision, offset: -1 }));
  assert.throws(() => materialArguments(session, "diagnostic", attempt, { revision: "wrong", finding: "diagnostic-7" }));
});

test("page completion, progress and response revision cannot be invented", () => {
  const view = { format: delivery.VIEW, id: revision, diagnostic_id: revision, view_id: revision, findings: [1], findings_count: 2, offset: 0, next_offset: 1 };
  delivery.validate(view, "diagnostics", { revision, offset: 0 });
  assert.throws(() => delivery.validate({ ...view, next_offset: null }, "diagnostics", { revision }));
  assert.throws(() => delivery.validate(view, "diagnostics", { revision: revision.replace(/a/g, "b") }));
  assert.throws(() => delivery.validate({ ...view, findings: [], next_offset: 0 }, "diagnostics", { revision }));
});
