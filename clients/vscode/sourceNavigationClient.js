"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { pathToFileURL } = require("node:url");
const { invokeCoreJson } = require("./coreCommandClient");
const { resolveCoreLaunch } = require("./coreLaunch");

async function invokeSourceAction(executable, session, args, options = {}) {
  if (typeof session !== "string" || !session || session.startsWith("-") || session.length > 256 || /[\s\0]/.test(session)) {
    throw new Error("Enter one exact Work Session ID from workbench context select.");
  }
  const launch = options.launch || resolveCoreLaunch(executable, options);
  if (launch.host !== "native") throw new Error("Source navigation currently requires a native Linux Workbench host.");
  const value = await invokeCoreJson(executable, ["context", "run", session, "--", "source", ...args], {
    ...options, launch, maximumOutput: 16 * 1024 * 1024, timeoutMs: 120000, label: "Source navigation",
  });
  const root = pathToFileURL(fs.realpathSync(options.cwd)).href;
  if (value.format !== "workbench-developer-action-v1" || value.exit_code !== 0
      || value.context?.selection?.pack_uri !== root
      || value.result?.context?.binding?.source_observation?.root_uri !== root
      || value.result.context.authority?.runtime_authority !== "none") {
    throw new Error("Source navigation changed the selected workspace or source-only authority.");
  }
  return value.result;
}

function verifiedSourceTarget(workspace, location) {
  if (!location || location.coordinate_system !== "one-based-utf16" || location.interval !== "half-open"
      || typeof location.path !== "string" || !location.path || /[\\:\0]/.test(location.path)
      || location.path.split("/").some((part) => !part || part === "." || part === "..")
      || !/^[0-9a-f]{64}$/.test(location.sha256)) throw new Error("Invalid source location.");
  let target = fs.realpathSync(workspace);
  for (const part of location.path.split("/")) {
    target = path.join(target, part);
    if (fs.lstatSync(target).isSymbolicLink()) throw new Error("Source location traverses a symbolic link.");
  }
  const metadata = fs.statSync(target);
  if (!metadata.isFile() || metadata.size > 64 * 1024 * 1024) throw new Error("Source file exceeds its bound.");
  const raw = fs.readFileSync(target);
  if (createHash("sha256").update(raw).digest("hex") !== location.sha256) throw new Error("Source selection is stale; search again.");
  const { byte_start: start, byte_end: end } = location;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start > end || end > raw.length) {
    throw new Error("Source interval is invalid.");
  }
  const decode = (bytes) => new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  const point = (bytes) => {
    const lines = decode(bytes).split("\n");
    return { line: lines.length, column: lines.at(-1).length + 1 };
  };
  for (const [key, offset] of [["start", start], ["end", end]]) {
    const expected = point(raw.subarray(0, offset));
    if (location[key]?.line !== expected.line || location[key]?.column !== expected.column) {
      throw new Error("Source editor coordinates differ from the exact bytes.");
    }
  }
  return { path: target, location, text: decode(raw).replace(/\r\n/g, "\n") };
}

async function openSourceLocation(vscode, workspace, location) {
  const target = verifiedSourceTarget(workspace, location);
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(target.path));
  const editor = await vscode.window.showTextDocument(document, { preview: true });
  const current = verifiedSourceTarget(workspace, location);
  if (document.isDirty || document.getText().replace(/\r\n/g, "\n") !== current.text) {
    throw new Error("The editor buffer differs from the selected source; save and search again.");
  }
  const range = new vscode.Range(location.start.line - 1, location.start.column - 1,
    location.end.line - 1, location.end.column - 1);
  editor.selection = new vscode.Selection(range.start, range.end);
  editor.revealRange(range);
}

module.exports = { invokeSourceAction, verifiedSourceTarget, openSourceLocation };
