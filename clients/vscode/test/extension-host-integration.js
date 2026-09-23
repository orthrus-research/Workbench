"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

const vscode = require("vscode");

const EXTENSION_ID = "cleanroom-workbench.workbench-vscode";
const REQUIRED_COMMANDS = [
  "workbench.axiomResults.open", "workbench.axiomResults.captured", "workbench.axiomResults.evidence",
  "workbench.axiomResults.next", "workbench.axiomResults.snapshot", "workbench.axiomResults.actions",
  "workbench.atlas.analyzeRecipeImpact",
  "workbench.atlas.recipeImpact.openReport",
  "workbench.atlas.searchRecipes",
  "workbench.source.navigate",
  "workbench.review.local",
  "workbench.checks.saved",
  "workbench.commandCenter.open",
  "workbench.core.configureExecutable",
  "workbench.core.openInstallationGuide",
  "workbench.core.checkInstallation",
  "workbench.setup.open",
  "workbench.review.pullRequest",
  "workbench.review.recipes",
  "workbench.pr.openReport",
  "workbench.pr.openFindingDiff",
  "workbench.pr.openWorkspaceFile",
  "workbench.project.qualify",
  "workbench.workspaceHome.open",
  "workbench.feature.browseExamples",
  "workbench.feature.browseRetainedRecords",
  "workbench.feature.records.openDiff",
  "workbench.feature.records.openRecord",
  "workbench.feature.records.refresh",
  "workbench.feature.reopenJob",
  "workbench.feature.runMaterialFluidRecipe",
];

async function containedBy(parent, candidate) {
  const [realParent, realCandidate] = await Promise.all([
    fs.realpath(parent),
    fs.realpath(candidate),
  ]);
  const relative = path.relative(realParent, realCandidate);
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative);
}

/** Run inside VS Code's genuine Extension Host against the installed client VSIX. */
async function run() {
  assert.equal(vscode.workspace.isTrusted, true, "the client probe workspace is not trusted");
  assert.equal(vscode.workspace.workspaceFolders?.length, 1, "the filesystem workspace was not opened");
  assert.equal(vscode.workspace.workspaceFolders[0].uri.scheme, "file");
  assert.equal(vscode.workspace.getConfiguration("editor").get("accessibilitySupport"), "on");
  assert.equal(vscode.workspace.getConfiguration("workbench").get("reduceMotion"), "on");
  assert.ok(
    [vscode.ColorThemeKind.HighContrast, vscode.ColorThemeKind.HighContrastLight]
      .includes(vscode.window.activeColorTheme.kind),
    "the accessibility matrix did not load a native high-contrast theme",
  );

  const extension = vscode.extensions.getExtension(EXTENSION_ID);
  assert.ok(extension, `installed extension ${EXTENSION_ID} was not discovered`);
  assert.match(process.env.WORKBENCH_TEST_RELEASE_VERSION ?? "", /^[0-9]+\.[0-9]+\.[0-9]+$/);
  assert.equal(extension.packageJSON.version, process.env.WORKBENCH_TEST_RELEASE_VERSION);
  assert.equal(extension.packageJSON.publisher, "cleanroom-workbench");
  assert.deepEqual(extension.packageJSON.contributes.views.explorer, [
    {
      id: "workbench.workspaceHome",
      name: "Workbench Home",
      when: "isWorkspaceTrusted",
      visibility: "visible",
    },
    {
      id: "workbench.prRecipeReview",
      name: "PR Recipe Review",
      when: "isWorkspaceTrusted",
      visibility: "visible",
    },
    {
      id: "workbench.featureRecords",
      name: "Workbench Retained Records",
      when: "isWorkspaceTrusted",
      visibility: "collapsed",
    },
    {
      id: "workbench.recipeImpact",
      name: "Atlas Recipe Impact Candidates",
      when: "isWorkspaceTrusted",
      visibility: "collapsed",
    },
  ]);
  assert.ok(
    await containedBy(process.env.WORKBENCH_TEST_EXTENSIONS_DIR, extension.extensionPath),
    `client extension was not loaded from the isolated installed-extension directory: ${extension.extensionPath}`,
  );

  await extension.activate();
  assert.equal(extension.isActive, true, "the installed client extension did not activate");
  const registered = new Set(await vscode.commands.getCommands(true));
  assert.equal(
    [...registered].some((command) => command.startsWith("workbench.release.")),
    false,
    "installed extension retained a release-process command namespace",
  );
  for (const command of REQUIRED_COMMANDS) {
    assert.ok(registered.has(command), `installed client command was not registered: ${command}`);
  }
  assert.ok(
    registered.has("workbench.feature.records.openEvidence"),
    "installed client retained-evidence bridge was not registered",
  );

  let workspaceHome = null;
  let workSession = null;
  if (process.env.WORKBENCH_TEST_CORE) {
    assert.equal(
      vscode.workspace.getConfiguration("workbench").get("coreExecutable"),
      process.env.WORKBENCH_TEST_CORE,
      "the installed core was not configured in the Extension Host",
    );
    assert.equal(
      vscode.workspace.getConfiguration("workbench").get("productSpine.stateRoot"),
      process.env.WORKBENCH_TEST_STATE_ROOT,
      "the external product-spine state root was not configured in the Extension Host",
    );
    const home = await vscode.commands.executeCommand("workbench.workspaceHome.open");
    assert.ok(home, "the installed client did not return Workspace Home V2");
    assert.equal(home.format, "workbench-workspace-home-v2");
    assert.equal(home.operation, "open");
    assert.equal(home.workspace.root, vscode.workspace.workspaceFolders[0].uri.fsPath);
    if (process.env.WORKBENCH_TEST_SHARED_SESSION === "1") {
      assert.ok(
        ["adopted", "unadopted"].includes(home.adoption.state),
        "the shared workspace returned an unsupported adoption state",
      );
      assert.ok(
        ["current", "not-applicable"].includes(home.adoption.freshness),
        "the shared workspace returned an unsupported adoption freshness",
      );
    } else {
      assert.equal(home.adoption.state, "unadopted");
      assert.equal(home.adoption.freshness, "not-applicable");
    }
    assert.equal(home.session.state, "available");
    assert.equal(home.session.freshness, "current");
    assert.equal(home.capability_catalog.state, "available");
    assert.match(
      home.capability_catalog.catalog_id,
      /^workbench-product-capabilities:sha256:[0-9a-f]{64}$/,
    );
    const sessionId = home.session.session_id;
    const resumed = await vscode.commands.executeCommand(
      "workbench.workspaceHome.inspectValue",
      "Work Session operation",
      { available: true, operation: "resume", session_id: sessionId },
    );
    assert.ok(resumed, "the installed client did not return resumed Work Session status");
    assert.equal(resumed.format_version, "workbench-work-session-summary-v1");
    assert.equal(resumed.session_id, sessionId);
    assert.equal(resumed.closed, false);
    assert.ok(resumed.frontend_ids.includes("workbench-vscode"));
    assert.ok(resumed.latest_sequence >= 2, "resume did not append navigation provenance");
    const reopened = await vscode.commands.executeCommand("workbench.workspaceHome.open");
    assert.ok(reopened, "the installed client could not reopen Home after session resume");
    assert.equal(reopened.session.session_id, sessionId);
    assert.equal(reopened.session.freshness, "current");
    assert.equal(reopened.adoption.state, home.adoption.state);
    assert.equal(reopened.adoption.freshness, home.adoption.freshness);
    workspaceHome = {
      format: home.format,
      operation: home.operation,
      workspace_kind: home.workspace.kind,
      session_freshness: reopened.session.freshness,
      capability_catalog_id: reopened.capability_catalog.catalog_id,
    };
    workSession = {
      format: resumed.format_version,
      session_id: sessionId,
      resumed: resumed.latest_sequence >= 2,
      frontend: resumed.frontend_ids.includes("workbench-vscode") ? "vscode" : null,
      closed: resumed.closed,
      home_remained_current: reopened.session.session_id === sessionId
        && reopened.session.freshness === "current",
    };
  }

  const materialCheck = process.env.WORKBENCH_TEST_MATERIAL_FIXTURE
    ? await require("./material-check-host").run(extension) : null;
  const atlasImpact = process.env.WORKBENCH_TEST_ATLAS_CORPUS
    ? await require("./atlas-impact-host").run(extension) : null;
  const resultPath = process.env.WORKBENCH_TEST_RESULT;
  assert.ok(resultPath, "the Extension Host result path was not configured");
  await fs.writeFile(resultPath, `${JSON.stringify({
    extension: {
      id: EXTENSION_ID,
      version: extension.packageJSON.version,
    },
    required_commands: REQUIRED_COMMANDS,
    workspace_home: workspaceHome,
    work_session: workSession,
    ...(materialCheck ? { material_check: materialCheck } : {}),
    ...(atlasImpact ? { atlas_recipe_impact: atlasImpact } : {}),
  })}\n`, { flag: "wx" });
}

module.exports = { run };
