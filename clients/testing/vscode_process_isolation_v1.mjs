// Shared process-isolation fixture for the canonical VS Code client.
import childProcess from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

const PROFILE_ENVIRONMENT_KEYS = new Set([
  "appdata",
  "electron_run_as_node",
  "home",
  "homedrive",
  "homepath",
  "localappdata",
  "node_options",
  "node_path",
  "temp",
  "tmp",
  "tmpdir",
  "userprofile",
  "vscode_portable",
  "vscode_ipc_hook_cli",
  "xdg_cache_home",
  "xdg_config_home",
  "xdg_data_home",
]);

function pathApi(platform) {
  return platform === "win32" ? path.win32 : path.posix;
}

function assertAbsolute(selected, label, platform) {
  if (typeof selected !== "string" || !pathApi(platform).isAbsolute(selected)) {
    throw new Error(`${label} must be an absolute ${platform} path`);
  }
}

function assertContained(root, selected, label, platform) {
  const relative = pathApi(platform).relative(root, selected);
  if (relative === "" || (!relative.startsWith("..") && !pathApi(platform).isAbsolute(relative))) {
    return;
  }
  throw new Error(`${label} escapes the VS Code process-isolation root`);
}

export function vscodeProcessProfile(root, {platform = process.platform} = {}) {
  assertAbsolute(root, "VS Code process-isolation root", platform);
  const selectedPath = pathApi(platform);
  const resolvedRoot = selectedPath.resolve(root);
  const profile = {
    root: resolvedRoot,
    osUser: selectedPath.join(resolvedRoot, "os-user"),
    appDataRoaming: selectedPath.join(resolvedRoot, "app-data", "roaming"),
    appDataLocal: selectedPath.join(resolvedRoot, "app-data", "local"),
    temporary: selectedPath.join(resolvedRoot, "temporary"),
    xdgConfig: selectedPath.join(resolvedRoot, "xdg", "config"),
    xdgCache: selectedPath.join(resolvedRoot, "xdg", "cache"),
    xdgData: selectedPath.join(resolvedRoot, "xdg", "data"),
  };
  for (const [label, selected] of Object.entries(profile)) {
    assertContained(resolvedRoot, selected, label, platform);
  }
  return Object.freeze(profile);
}

export async function prepareVSCodeProcessProfile(profile) {
  await Promise.all([
    profile.osUser,
    profile.appDataRoaming,
    profile.appDataLocal,
    profile.temporary,
    profile.xdgConfig,
    profile.xdgCache,
    profile.xdgData,
  ].map((selected) => fs.mkdir(selected, {recursive: true})));
}

export function isolatedVSCodeEnvironment(
  baseEnvironment,
  profile,
  {electronRunAsNode = false, platform = process.platform} = {},
) {
  const environment = {};
  for (const [key, value] of Object.entries(baseEnvironment)) {
    if (!PROFILE_ENVIRONMENT_KEYS.has(key.toLowerCase())) {
      environment[key] = value;
    }
  }
  Object.assign(environment, {
    HOME: profile.osUser,
    USERPROFILE: profile.osUser,
    APPDATA: profile.appDataRoaming,
    LOCALAPPDATA: profile.appDataLocal,
    TEMP: profile.temporary,
    TMP: profile.temporary,
    TMPDIR: profile.temporary,
    XDG_CONFIG_HOME: profile.xdgConfig,
    XDG_CACHE_HOME: profile.xdgCache,
    XDG_DATA_HOME: profile.xdgData,
  });
  if (platform === "win32") {
    const parsed = path.win32.parse(profile.osUser);
    environment.HOMEDRIVE = parsed.root.replace(/[\\/]$/, "");
    environment.HOMEPATH = profile.osUser.slice(environment.HOMEDRIVE.length);
  }
  if (electronRunAsNode) environment.ELECTRON_RUN_AS_NODE = "1";
  return environment;
}

export function activateVSCodeProcessProfile(
  targetEnvironment,
  profile,
  {platform = process.platform} = {},
) {
  const isolated = isolatedVSCodeEnvironment(targetEnvironment, profile, {platform});
  for (const key of Object.keys(targetEnvironment)) {
    if (PROFILE_ENVIRONMENT_KEYS.has(key.toLowerCase())) {
      delete targetEnvironment[key];
    }
  }
  Object.assign(targetEnvironment, isolated);
  return isolated;
}

export function vscodeCliProcess(
  vscodeExecutable,
  arguments_,
  profile,
  {baseEnvironment = process.env, platform = process.platform} = {},
) {
  assertAbsolute(vscodeExecutable, "VS Code executable", platform);
  const selectedPath = pathApi(platform);
  const cliScript = platform === "darwin"
    ? selectedPath.resolve(
      selectedPath.dirname(vscodeExecutable), "..", "Resources", "app", "out", "cli.js",
    )
    : selectedPath.join(
      selectedPath.dirname(vscodeExecutable), "resources", "app", "out", "cli.js",
    );
  return {
    command: vscodeExecutable,
    arguments: [cliScript, ...arguments_],
    options: {
      env: isolatedVSCodeEnvironment(baseEnvironment, profile, {
        electronRunAsNode: true,
        platform,
      }),
      shell: false,
      windowsHide: true,
    },
  };
}

export async function runIsolatedVSCodeCli(
  vscodeExecutable,
  arguments_,
  profile,
  {baseEnvironment = process.env, platform = process.platform, stdio = "inherit"} = {},
) {
  const selected = vscodeCliProcess(vscodeExecutable, arguments_, profile, {
    baseEnvironment,
    platform,
  });
  await new Promise((resolve, reject) => {
    const child = childProcess.spawn(selected.command, selected.arguments, {
      ...selected.options,
      stdio,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`exact VS Code CLI exited ${code ?? `for signal ${signal}`}`));
      }
    });
  });
}
