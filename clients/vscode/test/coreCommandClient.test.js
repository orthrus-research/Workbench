"use strict";

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const test = require("node:test");

const {
  MAX_TIMEOUT_MS,
  invokeCoreJson,
  invokeCoreText,
  parseBoundedJson,
} = require("../coreCommandClient");

test("suspended targets retain large JSON with no process deadline", async () => {
  const original = childProcess.execFile;
  const value = { diagnostic: "é".repeat(17 * 1024 * 1024) };
  try {
    childProcess.execFile = (_executable, _arguments, options, callback) => {
      assert.equal(options.timeout, 0);
      assert.equal(options.maxBuffer, Infinity);
      callback(null, JSON.stringify(value), "");
      return { pid: -1, exitCode: 0 };
    };
    assert.deepEqual(await invokeCoreJson("/workbench", ["context"], {
      timeoutMs: null, maximumOutput: null,
    }), value);
  } finally {
    childProcess.execFile = original;
  }
});

test("core command transport stays direct, scrubbed, and WSL-safe", async () => {
  const original = childProcess.execFile;
  let observed;
  try {
    childProcess.execFile = (executable, arguments_, options, callback) => {
      observed = { executable, arguments_, options };
      callback(null, '{"ready":true}\n', "");
    };
    const result = await invokeCoreJson(
      "\\\\wsl.localhost\\Ubuntu\\home\\dev\\workbench\\workbench",
      ["console", "catalog", "--json"],
      {
        platform: "win32",
        environment: {
          SystemRoot: "C:\\Windows",
          PATH: "C:\\Windows\\System32",
          PYTHONPATH: "C:\\hostile",
          NODE_OPTIONS: "--require=C:\\hostile.js",
        },
      },
    );
    assert.deepEqual(result, { ready: true });
    assert.equal(observed.executable, "C:\\Windows\\System32\\wsl.exe");
    assert.deepEqual(observed.arguments_, [
      "--distribution", "Ubuntu",
      "--cd", "/home/dev/workbench",
      "--exec", "/home/dev/workbench/workbench",
      "console", "catalog", "--json",
    ]);
    assert.equal(observed.options.shell, false);
    assert.equal(observed.options.env.PYTHONPATH, undefined);
    assert.equal(observed.options.env.NODE_OPTIONS, undefined);
  } finally {
    childProcess.execFile = original;
  }
});

test("JSON protocols require exact zero exit and a clean diagnostic channel", async () => {
  const original = childProcess.execFile;
  try {
    childProcess.execFile = (_executable, _arguments, _options, callback) => {
      callback(null, "{}", "unexpected warning");
    };
    await assert.rejects(
      invokeCoreJson("/workbench", ["feature", "examples", "--json"]),
      /unexpected stderr/,
    );

    childProcess.execFile = (_executable, _arguments, _options, callback) => {
      const error = new Error("exit 2");
      error.code = 2;
      callback(error, "{}", "owner rejected input");
    };
    await assert.rejects(
      invokeCoreText("/workbench", ["atlas", "recipes"]),
      /owner rejected input/,
    );
  } finally {
    childProcess.execFile = original;
  }
});

test("bounded JSON parsing rejects empty, malformed, and oversized output", () => {
  assert.throws(() => parseBoundedJson("", 16, "fixture"), /16 byte boundary/);
  assert.throws(() => parseBoundedJson("not-json", 16, "fixture"), /invalid JSON/);
  assert.throws(() => parseBoundedJson(' {"long":true}', 4, "fixture"), /4 byte boundary/);
});

test("nonzero Core refusal preserves expired detail without becoming success", async () => {
  const original = childProcess.execFile;
  const error = Object.assign(new Error("exit 2"), { code: 2 });
  try {
    childProcess.execFile = (_executable, _arguments, _options, callback) => {
      callback(error, JSON.stringify({ state: "unavailable", reason: "check details are expired" }), "");
    };
    await assert.rejects(invokeCoreJson("/workbench", ["context", "run"]), failure => {
      assert.equal(failure.message, "Workbench core failed: check details are expired");
      assert.equal(failure.cause, error);
      return true;
    });
  } finally { childProcess.execFile = original; }
});

test("Core stderr keeps precedence over a structured stdout refusal", async () => {
  const original = childProcess.execFile;
  try {
    childProcess.execFile = (_executable, _arguments, _options, callback) => {
      callback(Object.assign(new Error("exit 2"), { code: 2 }),
        JSON.stringify({ state: "unavailable", reason: "stdout reason" }), "owner stderr reason");
    };
    await assert.rejects(invokeCoreJson("/workbench", ["context"]), /owner stderr reason/);
  } finally { childProcess.execFile = original; }
});

test("non-protocol output and cancelled processes retain the process failure", async () => {
  const original = childProcess.execFile;
  const refusal = JSON.stringify({ state: "unavailable", reason: "misleading reason" });
  try {
    for (const [stdout, properties] of [
      ["not JSON", { code: 2 }],
      ['{"state":"completed","reason":"misleading reason"}', { code: 2 }],
      ['{"state":"unavailable","reason":"misleading reason","extra":true}', { code: 2 }],
      [refusal, { code: "ABORT_ERR" }],
      [refusal, { code: 2, killed: true }],
      [refusal, { code: 2, signal: "SIGTERM" }],
    ]) {
      childProcess.execFile = (_executable, _arguments, _options, callback) => {
        callback(Object.assign(new Error("original process failure"), properties), stdout, "");
      };
      await assert.rejects(invokeCoreJson("/workbench", ["context"]), error => {
        assert.equal(error.message, "Workbench core failed: original process failure"); return true;
      });
    }
  } finally { childProcess.execFile = original; }
});

test("core command timeout accepts twelve hours and rejects anything longer", async () => {
  const original = childProcess.execFile;
  let observed;
  try {
    childProcess.execFile = (_executable, _arguments, options, callback) => {
      observed = options;
      callback(null, "complete\n", "");
    };
    assert.equal(MAX_TIMEOUT_MS, 12 * 60 * 60 * 1000);
    assert.equal(
      await invokeCoreText("/workbench", ["console", "catalog"], {
        timeoutMs: MAX_TIMEOUT_MS,
      }),
      "complete\n",
    );
    assert.equal(observed.timeout, MAX_TIMEOUT_MS);
    assert.throws(
      () => invokeCoreText("/workbench", ["console", "catalog"], {
        timeoutMs: MAX_TIMEOUT_MS + 1,
      }),
      /core timeout must be between 1 and 43200000/,
    );
  } finally {
    childProcess.execFile = original;
  }
});
