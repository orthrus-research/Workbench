"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { verifiedSourceTarget, openSourceLocation } = require("../sourceNavigationClient");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-source-location-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const raw = Buffer.from('/* 😀 */ water\r\n');
  fs.writeFileSync(path.join(root, "recipe.groovy"), raw);
  return { root, raw, location: { path: "recipe.groovy", sha256: createHash("sha256").update(raw).digest("hex"),
    byte_start: 11, byte_end: 16, start: { line: 1, column: 10 }, end: { line: 1, column: 15 },
    coordinate_system: "one-based-utf16", interval: "half-open" } };
}

test("EOF and empty-file insertion points remain exactly navigable", (t) => {
  const { root, raw, location } = fixture(t);
  const eof = { ...location, byte_start: raw.length, byte_end: raw.length, start: { line: 2, column: 1 }, end: { line: 2, column: 1 } };
  assert.equal(verifiedSourceTarget(root, eof).path, path.join(root, location.path));
  fs.writeFileSync(path.join(root, location.path), "");
  const empty = { ...eof, sha256: createHash("sha256").update("").digest("hex"), byte_start: 0, byte_end: 0, start: { line: 1, column: 1 }, end: { line: 1, column: 1 } };
  assert.equal(verifiedSourceTarget(root, empty).text, "");
  assert.throws(() => verifiedSourceTarget(root, { ...empty, end: { line: 2, column: 1 } }), /coordinates/);
});

test("source location validates UTF-8 bytes and UTF-16 coordinates", (t) => {
  const { root, location } = fixture(t);
  assert.equal(verifiedSourceTarget(root, location).text, '/* 😀 */ water\n');
  assert.throws(() => verifiedSourceTarget(root, { ...location, path: "../escape" }), /Invalid/);
  assert.throws(() => verifiedSourceTarget(root, { ...location, start: { line: 1, column: 9 } }), /coordinates/);
  fs.writeFileSync(path.join(root, location.path), "same place, different bytes");
  assert.throws(() => verifiedSourceTarget(root, location), /stale/);
});

test("native reveal rejects an unsaved buffer and opens the verified range", async (t) => {
  const { root, location } = fixture(t);
  const document = { isDirty: true, getText: () => '/* 😀 */ water\n' };
  let revealed;
  const editor = { revealRange: (range) => { revealed = range; } };
  const vscode = { Uri: { file: (file) => file }, workspace: { openTextDocument: async () => document },
    window: { showTextDocument: async () => editor },
    Range: class { constructor(a, b, c, d) { this.start = [a, b]; this.end = [c, d]; } },
    Selection: class { constructor(start, end) { this.start = start; this.end = end; } } };
  await assert.rejects(openSourceLocation(vscode, root, location), /buffer differs/);
  assert.equal(revealed, undefined);
  document.isDirty = false;
  await openSourceLocation(vscode, root, location);
  assert.deepEqual(editor.selection.start, [0, 9]);
  assert.deepEqual(editor.selection.end, [0, 14]);
});

test("source location does not follow symlink replacements", (t) => {
  const { root, location } = fixture(t);
  fs.renameSync(path.join(root, location.path), path.join(root, "moved.groovy"));
  fs.symlinkSync("moved.groovy", path.join(root, location.path));
  assert.throws(() => verifiedSourceTarget(root, location), /symbolic link/);
});
