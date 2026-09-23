"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const isolationModule = import(
  "../../testing/vscode_process_isolation_v1.mjs"
);

test("VS Code UI and CLI processes use one disposable host profile", async (context) => {
  const {
    isolatedVSCodeEnvironment,
    prepareVSCodeProcessProfile,
    vscodeCliProcess,
    vscodeProcessProfile,
  } = await isolationModule;
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-vscode-isolation-test-"));
  context.after(() => fs.rmSync(root, {recursive: true, force: true}));

  const profile = vscodeProcessProfile(root);
  await prepareVSCodeProcessProfile(profile);
  for (const selected of Object.values(profile)) {
    assert.equal(path.relative(root, selected).startsWith(".."), false);
  }
  for (const selected of [
    profile.osUser,
    profile.appDataRoaming,
    profile.appDataLocal,
    profile.temporary,
    profile.xdgConfig,
    profile.xdgCache,
    profile.xdgData,
  ]) {
    assert.equal(fs.statSync(selected).isDirectory(), true);
  }

  const inherited = {
    HOME: "/global/home",
    UserProfile: "/global/profile",
    APPDATA: "/global/roaming",
    LOCALAPPDATA: "/global/local",
    TEMP: "/global/temp",
    XDG_CONFIG_HOME: "/global/config",
    ELECTRON_RUN_AS_NODE: "unexpected",
    Node_Options: "--require=/global/injection.cjs",
    NODE_PATH: "/global/node-modules",
    vscode_ipc_hook_cli: "/global/vscode-ipc.sock",
    VSCODE_PORTABLE: "/global/portable",
    RETAINED: "yes",
  };
  const uiEnvironment = isolatedVSCodeEnvironment(inherited, profile);
  assert.equal(uiEnvironment.HOME, profile.osUser);
  assert.equal(uiEnvironment.USERPROFILE, profile.osUser);
  assert.equal(uiEnvironment.APPDATA, profile.appDataRoaming);
  assert.equal(uiEnvironment.LOCALAPPDATA, profile.appDataLocal);
  assert.equal(uiEnvironment.TEMP, profile.temporary);
  assert.equal(uiEnvironment.XDG_CONFIG_HOME, profile.xdgConfig);
  assert.equal(uiEnvironment.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(uiEnvironment.Node_Options, undefined);
  assert.equal(uiEnvironment.NODE_PATH, undefined);
  assert.equal(uiEnvironment.vscode_ipc_hook_cli, undefined);
  assert.equal(uiEnvironment.VSCODE_PORTABLE, undefined);
  assert.equal(uiEnvironment.RETAINED, "yes");

  const windowsProfile = vscodeProcessProfile("C:\\scenario\\profile", {platform: "win32"});
  const invocation = vscodeCliProcess(
    "C:\\scenario\\VS Code\\Code.exe",
    ["--install-extension", "C:\\scenario\\client.vsix"],
    windowsProfile,
    {baseEnvironment: inherited, platform: "win32"},
  );
  assert.equal(invocation.command, "C:\\scenario\\VS Code\\Code.exe");
  assert.deepEqual(invocation.arguments, [
    "C:\\scenario\\VS Code\\resources\\app\\out\\cli.js",
    "--install-extension",
    "C:\\scenario\\client.vsix",
  ]);
  assert.equal(invocation.options.shell, false);
  assert.equal(invocation.options.env.ELECTRON_RUN_AS_NODE, "1");
  assert.equal(invocation.options.env.Node_Options, undefined);
  assert.equal(invocation.options.env.NODE_PATH, undefined);
  assert.equal(invocation.options.env.vscode_ipc_hook_cli, undefined);
  assert.equal(invocation.options.env.HOME, "C:\\scenario\\profile\\os-user");
  assert.equal(invocation.options.env.HOMEDRIVE, "C:");
  assert.equal(invocation.options.env.HOMEPATH, "\\scenario\\profile\\os-user");
});
