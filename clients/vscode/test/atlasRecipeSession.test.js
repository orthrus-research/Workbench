"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter, getEventListeners } = require("node:events");
const { PassThrough } = require("node:stream");
const { openRecipeSession } = require("../atlasRecipeSession");
const fixture = require("../../testing/fixtures/atlas-recipe-browse-v1.json");

function child() {
  const value = new EventEmitter();
  value.stdin = new PassThrough(); value.stdout = new PassThrough(); value.stderr = new PassThrough();
  value.kills = 0;
  value.kill = () => { value.kills++; queueMicrotask(() => value.emit("close", null)); };
  return value;
}
function ready(process) {
  process.stdout.write(JSON.stringify({ format: "workbench-atlas-recipe-session-ready-v1", schema_version: 1,
    state: "ready", graph_set_id: fixture.context.graph_set_id, context: fixture.context }) + "\n");
}

test("pre-cancelled graph load starts no Core process", async () => {
  const controller = new AbortController(); controller.abort();
  let started = false;
  await assert.rejects(openRecipeSession("workbench", "/graph", {
    signal: controller.signal, spawn: () => { started = true; return child(); },
  }), { name: "AbortError" });
  assert.equal(started, false);
});

test("cancelling graph verification rejects the wait, stops Core and detaches cancellation", async () => {
  const controller = new AbortController(), process = child();
  const pending = openRecipeSession("workbench", "/graph", { signal: controller.signal, spawn: () => process });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(process.kills, 1);
  assert.equal(process.stdin.writableEnded, true);
  assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});

test("cancellation also stops an active request; later close is idempotent", async () => {
  const controller = new AbortController(), process = child();
  const opening = openRecipeSession("workbench", "/graph", { signal: controller.signal, spawn: () => process });
  ready(process);
  const session = await opening, request = session.search("final");
  controller.abort();
  await assert.rejects(request, { name: "AbortError" });
  session.close(); session.close();
  assert.equal(process.kills, 1);
  assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});

test("closing an idle session releases its abort listener without killing unrelated later work", async () => {
  const controller = new AbortController(), process = child();
  const opening = openRecipeSession("workbench", "/graph", { signal: controller.signal, spawn: () => process });
  ready(process);
  const session = await opening;
  session.close(); controller.abort();
  assert.equal(process.kills, 0);
  assert.equal(process.stdin.writableEnded, true);
  assert.equal(getEventListeners(controller.signal, "abort").length, 0);
});
