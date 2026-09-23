"use strict";
const { spawn } = require("node:child_process");
const { TextDecoder } = require("node:util");
const { scrubbedEnvironment } = require("./coreClient");
const { commandForCoreLaunch, pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");
const { validateAtlasContext, validateAtlasSearch } = require("./developerToolsClient");
const { validateBrowse } = require("./atlasRecipeBrowseValidation");

async function openRecipeSession(executable, root, options = {}) {
  const cancelled = () => Object.assign(new Error("Atlas browsing cancelled"), { name: "AbortError" });
  if (options.signal?.aborted) throw cancelled();
  const launch = resolveCoreLaunch(executable, options);
  const mapped = pathForCoreLaunch(root, launch, "Atlas graph");
  const child = (options.spawn || spawn)(launch.executable, commandForCoreLaunch(launch, ["atlas", "recipes", "session", mapped]), {
    cwd: launch.host === "native" ? options.cwd : undefined,
    env: scrubbedEnvironment(options.environment), shell: false, windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });
  let pending, failure, buffer = "", sequence = 0, closed = false;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const abort = () => fail(cancelled());
  const detach = () => options.signal?.removeEventListener("abort", abort);
  function fail(error) {
    if (failure) return;
    failure = error;
    closed = true;
    detach();
    if (pending) { const current = pending; pending = null; current.reject(failure); }
    child.stdin.end(); child.kill();
  }
  function next() {
    if (failure) return Promise.reject(failure);
    if (pending) return Promise.reject(new Error("Atlas session already has a pending request"));
    return new Promise((resolve, reject) => { pending = { resolve, reject }; });
  }
  child.stdout.on("data", chunk => {
    try {
      buffer += decoder.decode(chunk, { stream: true });
      if (Buffer.byteLength(buffer, "utf8") > 48 * 1024 * 1024) throw new Error("Atlas page exceeds the client transport boundary");
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        if (!pending) throw new Error("Atlas returned an unsolicited session record");
        const value = JSON.parse(line); const current = pending; pending = null; current.resolve(value);
      }
    } catch (error) { fail(error); }
  });
  child.stderr.on("data", chunk => fail(new Error(`Atlas session failed: ${chunk.toString("utf8").slice(0, 4000)}`)));
  child.on("error", fail);
  child.stdin.on("error", fail);
  child.on("close", code => {
    detach();
    if (!closed || pending || code !== 0) fail(new Error(`Atlas session closed (${code}); reopen and verify the graph`));
  });
  const close = () => {
    if (closed) return;
    closed = true; detach(); child.stdin.end();
    if (pending) fail(cancelled());
  };
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) abort();
  try {
    const ready = await next();
    if (ready.format !== "workbench-atlas-recipe-session-ready-v1" || ready.schema_version !== 1 || ready.state !== "ready") throw new Error("Unsupported Atlas session");
    validateAtlasContext(ready.context, mapped);
    const graph = ready.context.graph_set_id;
    if (!graph || ready.graph_set_id !== graph) throw new Error("Atlas session graph identity changed");
    async function request(operation, arguments_) {
      if (closed) throw new Error("Atlas session is closed");
      const requestId = String(++sequence);
      const response = next();
      child.stdin.write(JSON.stringify({ format: "workbench-atlas-recipe-session-request-v1", schema_version: 1,
        request_id: requestId, graph_set_id: graph, operation, arguments: arguments_ }) + "\n");
      const value = await response;
      if (value.format !== "workbench-atlas-recipe-session-response-v1" || value.schema_version !== 1
          || value.request_id !== requestId || value.graph_set_id !== graph) throw new Error("Atlas session response linkage changed");
      if (value.state !== "complete") throw new Error(typeof value.error === "string" ? value.error : "Atlas request did not complete");
      return value.result;
    }
    return { graph, root: mapped, close,
      search: async query => validateAtlasSearch(await request("search", { query, limit: 50 }), query, 50, mapped),
      browse: async (selection, offset) => {
        return validateBrowse(await request("browse", { selection_id: selection, offset, limit: 50 }), { root: mapped, graph, selection, offset });
      },
    };
  } catch (error) { closed = true; fail(error); throw error; }
}

module.exports = { openRecipeSession };
