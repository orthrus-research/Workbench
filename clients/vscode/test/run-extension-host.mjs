import childProcess from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import {
  downloadAndUnzipVSCode,
  runTests,
} from "@vscode/test-electron";
import {
  activateVSCodeProcessProfile,
  prepareVSCodeProcessProfile,
  runIsolatedVSCodeCli,
  vscodeProcessProfile,
} from "../../testing/vscode_process_isolation_v1.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const vscodePackage = path.resolve(process.argv[2] ?? "");
if (!process.argv[2] || path.extname(vscodePackage).toLowerCase() !== ".vsix") {
  throw new Error("usage: node test/run-extension-host.mjs <client.vsix> [installed-core]");
}
await fs.access(vscodePackage);
const installedCore = process.argv[3] ? path.resolve(process.argv[3]) : null;
if (installedCore) await fs.access(installedCore);
// Material contexts are selected through Shell, independently of Home adoption.
// This explicit lane proves Saved Checks, not the separate Home/C01 contract.
const materialFixture = process.env.WORKBENCH_TEST_MATERIAL_FIXTURE
  ? JSON.parse(await fs.readFile(path.resolve(process.env.WORKBENCH_TEST_MATERIAL_FIXTURE), "utf8")) : null;
if (materialFixture) {
  if (installedCore || process.argv[4] || materialFixture.state !== "prepared-not-qualified"
      || !path.isAbsolute(materialFixture.pack) || !path.isAbsolute(materialFixture.executable)
      || process.env.WORKBENCH_STATE_ROOT !== materialFixture.coreState) {
    throw new Error("material host lane requires its explicit installed fixture/state and no Home adoption arguments");
  }
  await Promise.all([fs.access(materialFixture.pack), fs.access(materialFixture.executable)]);
}
// Atlas uses explicit retained graphs and installed CLI records, independent of Home adoption.
const atlasCorpus = process.env.WORKBENCH_TEST_ATLAS_CORPUS
  ? JSON.parse(await fs.readFile(path.resolve(process.env.WORKBENCH_TEST_ATLAS_CORPUS), "utf8")) : null;
if (atlasCorpus) {
  if (installedCore || process.argv[4] || materialFixture
      || atlasCorpus.format !== "workbench-atlas-client-corpus-v1"
      || !path.isAbsolute(atlasCorpus.workspace) || !path.isAbsolute(atlasCorpus.executable)
      || !Array.isArray(atlasCorpus.cases) || !atlasCorpus.cases.length
      || atlasCorpus.cases.filter(row => row.highlight === true).length < 1
      || atlasCorpus.cases.filter(row => row.highlight === true).length > 6) {
    throw new Error("Atlas host lane requires an explicit installed corpus and one through six highlights, with no other journey arguments");
  }
  await Promise.all([fs.access(atlasCorpus.workspace), fs.access(atlasCorpus.executable)]);
}
const sharedWorkspace = process.argv[4] ? path.resolve(process.argv[4]) : null;
const sharedStateRoot = process.argv[5] ? path.resolve(process.argv[5]) : null;
if ((sharedWorkspace === null) !== (sharedStateRoot === null)) {
  throw new Error("shared Workspace Home qualification requires both workspace and state root");
}
if (sharedWorkspace && !installedCore) {
  throw new Error("shared Workspace Home qualification requires an installed core");
}
if (sharedWorkspace) {
  await Promise.all([fs.access(sharedWorkspace), fs.access(sharedStateRoot)]);
}
const execFile = promisify(childProcess.execFile);
const retainTemporary = process.env.WORKBENCH_TEST_RETAIN_TEMPORARY === "1";
// The downloaded Linux CLI otherwise prompts forever when this matrix is run from WSL.
process.env.DONT_PROMPT_WSL_INSTALL = "1";

const repositoryRoot = path.resolve(root, "..", "..");
const clientManifest = JSON.parse(await fs.readFile(
  path.join(root, "package.json"),
  "utf8",
));
const expectedReleaseVersion = clientManifest.version;
if (clientManifest.name !== "workbench-vscode" || typeof expectedReleaseVersion !== "string"
    || !/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(expectedReleaseVersion)) {
  throw new Error("the native VS Code manifest has an invalid package identity");
}
const expectedRequiredCommands = new Set(
  clientManifest.contributes?.commands?.map((row) => row.command) ?? [],
);
if (expectedRequiredCommands.size === 0
    || [...expectedRequiredCommands].some((command) => typeof command !== "string")) {
  throw new Error("the VS Code package manifest has an invalid command contract");
}
const toolchainLock = JSON.parse(await fs.readFile(
  path.join(repositoryRoot, "validation", "ide-toolchains-v1.json"),
  "utf8",
));
const versions = [
  toolchainLock.vscode_extension_host.minimum,
  toolchainLock.vscode_extension_host.current,
];
if (versions.some((value) => typeof value !== "string" || !/^1\.[0-9]+\.0$/.test(value))
    || new Set(versions).size !== versions.length) {
  throw new Error("the exact VS Code Extension Host matrix is invalid");
}
// A single material host is an explicit development probe. Qualification keeps
// the default complete locked matrix and records every executed version.
const selectedMaterialHost = process.env.WORKBENCH_TEST_MATERIAL_HOST_VERSION;
if (selectedMaterialHost && (!materialFixture || !versions.includes(selectedMaterialHost))) {
  throw new Error("a single material host must select one exact locked VS Code version with its explicit fixture");
}
const executedVersions = selectedMaterialHost ? [selectedMaterialHost] : versions;
// macOS limits Unix-domain socket paths to 103 bytes. Keep Extension Host
// user data in the host temporary directory instead of underneath a deep
// checkout path so VS Code's instance socket remains representable.
const testRunsRoot = process.platform === "win32" ? os.tmpdir() : "/tmp";
const extensionHostResults = [];
let sharedObservedSessionId = null;
for (const version of executedVersions) {
  const temporary = await fs.mkdtemp(path.join(testRunsRoot, `wb-vsc-${version}-`));
  const extensionsDirectory = path.join(temporary, "extensions");
  const userDataDirectory = path.join(temporary, "user-data");
  const workspace = atlasCorpus?.workspace ?? materialFixture?.pack ?? sharedWorkspace ?? path.join(temporary, "workspace");
  const productSpineStateRoot = sharedStateRoot ?? path.join(temporary, "product-spine-state");
  const resultPath = path.join(temporary, "extension-host-result.json");
  const processProfile = vscodeProcessProfile(path.join(temporary, "process-profile"));
  await prepareVSCodeProcessProfile(processProfile);
  const hostEnvironment = activateVSCodeProcessProfile(process.env, processProfile);
  let adoptedBindingId = null;
  let adoptedSessionId = null;
  let observedWorkspaceKind = null;
  await Promise.all([
    fs.mkdir(extensionsDirectory),
    fs.mkdir(userDataDirectory),
    ...(sharedWorkspace || materialFixture || atlasCorpus ? [] : [fs.mkdir(workspace)]),
  ]);
  const userSettingsDirectory = path.join(userDataDirectory, "User");
  await fs.mkdir(userSettingsDirectory);
  const settings = {
    "editor.accessibilitySupport": "on",
    "workbench.colorTheme": "Default High Contrast",
    "workbench.reduceMotion": "on",
  };
  if (installedCore) {
    settings["workbench.coreExecutable"] = installedCore;
    settings["workbench.productSpine.stateRoot"] = productSpineStateRoot;
  }
  if (materialFixture) settings["workbench.coreExecutable"] = materialFixture.executable;
  if (atlasCorpus) settings["workbench.coreExecutable"] = atlasCorpus.executable;
  await fs.writeFile(path.join(userSettingsDirectory, "settings.json"), `${JSON.stringify(
    settings, null, 2,
  )}\n`);
  try {
    if (installedCore && !sharedWorkspace) {
      const adoptionProcess = await execFile(installedCore, [
        "adopt", workspace, "--state-root", productSpineStateRoot, "--json",
      ], {
        cwd: workspace,
        encoding: "utf8",
        maxBuffer: 16 * 1024 * 1024,
        timeout: 120_000,
        windowsHide: true,
        env: hostEnvironment,
      });
      if (adoptionProcess.stderr.trim()) {
        throw new Error(`installed core adoption returned stderr: ${adoptionProcess.stderr.trim()}`);
      }
      const adoption = JSON.parse(adoptionProcess.stdout);
      if (adoption.format !== "workbench-workspace-home-v2"
          || adoption.operation !== "adopt"
          || adoption.workspace?.root !== workspace
          || adoption.adoption?.state !== "adopted"
          || adoption.adoption?.freshness !== "current"
          || adoption.session?.state !== "available"
          || adoption.session?.freshness !== "current") {
        throw new Error("installed core did not adopt the disposable Extension Host workspace");
      }
      adoptedBindingId = adoption.adoption.binding_id;
      adoptedSessionId = adoption.session.session_id;
      observedWorkspaceKind = adoption.workspace.kind;
    } else if (installedCore) {
      const openProcess = await execFile(installedCore, [
        "open", workspace, "--state-root", productSpineStateRoot, "--json",
      ], {
        cwd: workspace,
        encoding: "utf8",
        maxBuffer: 16 * 1024 * 1024,
        timeout: 120_000,
        windowsHide: true,
        env: hostEnvironment,
      });
      if (openProcess.stderr.trim()) {
        throw new Error(`installed core open returned stderr: ${openProcess.stderr.trim()}`);
      }
      const opened = JSON.parse(openProcess.stdout);
      if (opened.format !== "workbench-workspace-home-v2"
          || opened.operation !== "open"
          || opened.session?.state !== "available"
          || opened.session?.freshness !== "current") {
        throw new Error("installed core did not discover the shared Work Session");
      }
      adoptedSessionId = opened.session.session_id;
      observedWorkspaceKind = opened.workspace.kind;
      if (sharedObservedSessionId !== null && sharedObservedSessionId !== adoptedSessionId) {
        throw new Error("VS Code host matrix discovered different shared Work Sessions");
      }
      sharedObservedSessionId = adoptedSessionId;
    }
    const vscodeExecutablePath = await downloadAndUnzipVSCode({ version });
    await runIsolatedVSCodeCli(vscodeExecutablePath, [
      "--install-extension", vscodePackage,
      "--force",
      `--extensions-dir=${extensionsDirectory}`,
      `--user-data-dir=${userDataDirectory}`,
    ], processProfile);
    await runTests({
      vscodeExecutablePath,
      extensionDevelopmentPath: path.join(root, "test", "extension-host-harness"),
      extensionTestsPath: path.join(root, "test", "extension-host-integration.js"),
      launchArgs: [
        workspace,
        ...(materialFixture?.deliveryMeasurement ? ["--ozone-platform=x11"] : []),
        "--force-renderer-accessibility",
        `--extensions-dir=${extensionsDirectory}`,
        `--user-data-dir=${userDataDirectory}`,
      ],
      extensionTestsEnv: {
        ...hostEnvironment,
        WORKBENCH_TEST_EXTENSIONS_DIR: extensionsDirectory,
        WORKBENCH_TEST_RELEASE_VERSION: expectedReleaseVersion,
        WORKBENCH_TEST_RESULT: resultPath,
        ...(installedCore ? { WORKBENCH_TEST_CORE: installedCore } : {}),
        ...(installedCore ? { WORKBENCH_TEST_STATE_ROOT: productSpineStateRoot } : {}),
        ...(sharedWorkspace ? { WORKBENCH_TEST_SHARED_SESSION: "1" } : {}),
      },
    });
    const result = JSON.parse(await fs.readFile(resultPath, "utf8"));
    if (installedCore && !sharedWorkspace) {
      const reopenProcess = await execFile(installedCore, [
        "reopen", adoptedBindingId, "--state-root", productSpineStateRoot, "--json",
      ], {
        cwd: workspace,
        encoding: "utf8",
        maxBuffer: 16 * 1024 * 1024,
        timeout: 120_000,
        windowsHide: true,
        env: hostEnvironment,
      });
      if (reopenProcess.stderr.trim()) {
        throw new Error(`installed core reopen returned stderr: ${reopenProcess.stderr.trim()}`);
      }
      const reopened = JSON.parse(reopenProcess.stdout);
      if (reopened.format !== "workbench-workspace-home-v2"
          || reopened.operation !== "reopen"
          || reopened.adoption?.state !== "adopted"
          || reopened.adoption?.freshness !== "current"
          || reopened.session?.session_id !== adoptedSessionId
          || reopened.session?.freshness !== "current") {
        throw new Error("adopted binding was not current after the installed client journey");
      }
      result.adopted_binding = { freshness: "current", reopened: true };
    } else if (installedCore) {
      const statusProcess = await execFile(installedCore, [
        "session", "status", adoptedSessionId,
        "--state-root", productSpineStateRoot,
        "--frontend", "cli", "--json",
      ], {
        cwd: workspace,
        encoding: "utf8",
        maxBuffer: 16 * 1024 * 1024,
        timeout: 120_000,
        windowsHide: true,
        env: hostEnvironment,
      });
      if (statusProcess.stderr.trim()) {
        throw new Error(`installed core status returned stderr: ${statusProcess.stderr.trim()}`);
      }
      const status = JSON.parse(statusProcess.stdout);
      if (status.format_version !== "workbench-work-session-summary-v1"
          || status.session_id !== adoptedSessionId
          || status.integrity_state !== "verified"
          || !status.frontend_ids?.includes("workbench-vscode")) {
        throw new Error("shared Work Session did not retain the installed VS Code handoff");
      }
      result.adopted_binding = { freshness: "current", reopened: true };
    }
    const returnedRequiredCommands = Array.isArray(result.required_commands)
      ? new Set(result.required_commands)
      : null;
    if (result.extension === null || typeof result.extension !== "object"
        || result.extension.id !== "cleanroom-workbench.workbench-vscode"
        || result.extension.version !== expectedReleaseVersion
        || returnedRequiredCommands === null
        || returnedRequiredCommands.size !== result.required_commands.length
        || returnedRequiredCommands.size !== expectedRequiredCommands.size
        || [...expectedRequiredCommands].some(
          (command) => !returnedRequiredCommands.has(command)
        )
        || result.required_commands.some((command) => command.startsWith("workbench.release."))
        || (installedCore && (
          result.workspace_home?.format !== "workbench-workspace-home-v2"
          || result.workspace_home?.operation !== "open"
          || result.workspace_home?.workspace_kind !== observedWorkspaceKind
          || result.workspace_home?.session_freshness !== "current"
          || !/^workbench-product-capabilities:sha256:[0-9a-f]{64}$/.test(
            result.workspace_home?.capability_catalog_id ?? "",
          )
          || result.work_session?.format !== "workbench-work-session-summary-v1"
          || result.work_session?.session_id !== adoptedSessionId
          || result.work_session?.resumed !== true
          || result.work_session?.frontend !== "vscode"
          || result.work_session?.closed !== false
          || result.work_session?.home_remained_current !== true
          || result.adopted_binding?.freshness !== "current"
          || result.adopted_binding?.reopened !== true
        ))) {
      throw new Error(`VS Code ${version} returned an invalid Extension Host result`);
    }
    if (await fs.access(path.join(workspace, ".workbench")).then(() => true, () => false)) {
      throw new Error(`VS Code ${version} wrote mutable Workbench state inside the workspace`);
    }
    extensionHostResults.push(result);
    if (materialFixture && (!result.material_check || result.material_check.qualification !== false
        || result.material_check.editorHost !== "real-vscode"
        || result.material_check.nativeWorkers !== (materialFixture.deliveryMeasurement ? 1 : materialFixture.retainedReaderOnly === true ? 0 : materialFixture.comparisonMode === "single-rerun" ? 2 : 3)
        || (materialFixture.deliveryMeasurement && (result.material_check.format !== "workbench-ide-delivery-measurement-v1"
          || result.material_check.navigationVerified !== true || result.material_check.visibleEvidenceRequiresInspection !== true))
        || (materialFixture.retainedReaderOnly === true && result.material_check.readerOnly !== true))) {
      throw new Error(`VS Code ${version} did not complete the explicit native material/editor acceptance`);
    }
    if (atlasCorpus && (result.atlas_recipe_impact?.format !== "workbench-atlas-installed-native-journey-v1"
        || result.atlas_recipe_impact.editor_host !== "real-vscode"
        || result.atlas_recipe_impact.installed_core_transport !== true
        || result.atlas_recipe_impact.contract_case_count !== atlasCorpus.cases.length
        || result.atlas_recipe_impact.highlight_count !== atlasCorpus.cases.filter(row => row.highlight === true).length)) {
      throw new Error(`VS Code ${version} did not complete the installed Atlas native journey`);
    }
    process.stdout.write(`Installed VSIX passed VS Code ${version} ${atlasCorpus ? "Atlas recipe impact acceptance" : materialFixture ? "material editor acceptance" : "Extension Host qualification"}.\n`);
  } finally {
    if (retainTemporary) {
      process.stderr.write(`Retained Extension Host diagnostics at ${temporary}\n`);
    } else {
      await fs.rm(temporary, { recursive: true, force: true });
    }
  }
}

function invariantInstalledSurface(result) {
  const projected = structuredClone(result);
  if (!sharedWorkspace) {
    // Each ordinary matrix row intentionally adopts a different disposable
    // workspace and Work Session. Compare the installed client surface, not
    // those per-row identities.
    delete projected.work_session?.session_id;
  }
  // Material runs are intentionally fresh per host. Compare the editor behavior,
  // not the distinct retained attempts; each full result keeps its evidence IDs.
  if (projected.material_check) delete projected.material_check.evidence;
  // Each host explicitly applies its own policy proposal, whose receipt binds
  // the previous policy. Keep exact IDs in the full evidence, not the surface
  // equality projection; mode, controls and consent counts must still agree.
  if (projected.material_check?.retentionControls) delete projected.material_check.retentionControls.policy;
  return projected;
}

if (extensionHostResults.length === 2 && JSON.stringify(invariantInstalledSurface(extensionHostResults[0]))
      !== JSON.stringify(invariantInstalledSurface(extensionHostResults[1]))) {
  throw new Error("minimum and current VS Code hosts observed different installed surfaces");
}
if (process.env.WORKBENCH_TEST_MATRIX_RESULT) {
  await fs.writeFile(
    path.resolve(process.env.WORKBENCH_TEST_MATRIX_RESULT),
    `${JSON.stringify({
      format: atlasCorpus ? "workbench-vscode-atlas-impact-host-matrix-v1" : materialFixture ? "workbench-vscode-material-check-host-matrix-v1" : "workbench-vscode-product-spine-host-matrix-v1",
      schema_version: 1,
      versions: executedVersions,
      session_id: sharedWorkspace ? sharedObservedSessionId : null,
      results: extensionHostResults,
    }, null, 2)}\n`,
    { flag: "wx" },
  );
}

// VS Code 1.133 may leave its optional Agent Host IPC handle open after the
// Extension Host and test runner have both exited successfully. All child
// results and retained files have been awaited above, so end the harness at
// this explicit success boundary instead of hanging a CI worker indefinitely.
process.exit(0);
