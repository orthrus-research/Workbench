"use strict";

const vscode = require("vscode");
const { discoverExecutable, scrubbedEnvironment } = require("./coreClient");
const { invokeCoreVersion, invokeSetupCheck } = require("./coreStatusClient");
const {
  commandForCoreLaunch,
  pathForCatalogLaunch,
  pathForCoreLaunch,
  resolveCoreLaunch,
} = require("./coreLaunch");
const { invokeRecipeReview } = require("./recipeReviewClient");
const {
  applyPullRequestReview,
  planPullRequestReview,
  pullRequestNumber,
} = require("./prRecipeReviewClient");
const {
  applyProjectQualification,
  planProjectQualification,
  qualificationPlanSummary,
} = require("./projectQualificationClient");
const {
  PrRecipeReviewDocuments,
  PrRecipeReviewTreeProvider,
} = require("./prRecipeReviewTree");
const {
  assignmentArguments,
  composeCommandFlow,
  invokeCatalog,
  invokeCommandOutput,
  invokeCommandReview,
  parseExactIntegerInput,
} = require("./commandCenterClient");
const { invokeExamples } = require("./developerToolsClient");
const { createAtlasRecipeImpactFlow } = require("./atlasRecipeImpactFlow");
const { createAtlasRecipeBrowser } = require("./atlasRecipeBrowser");
const { invokeSourceAction, openSourceLocation } = require("./sourceNavigationClient");
const {
  AtlasImpactDocuments,
  AtlasImpactTreeProvider,
} = require("./atlasRecipeImpactTree");
const {
  invokePresentation,
  invokeRecordCatalog,
  invokeTransaction,
} = require("./featureRecordClient");
const {
  FeatureRecordTreeProvider,
  FeatureVirtualDocuments,
  retainedFileUri,
} = require("./featureRecordTree");
const { invokeResult } = require("./featureServiceClient");
const { invokeRun } = require("./developerFeatureClient");
const {
  invokeCurrentContextFeatureAction,
} = require("./currentContextFeatureClient");
const { invokeWorkspaceHomeV2 } = require("./workspaceHomeV2Client");
const { WorkspaceHomeV2TreeProvider } = require("./workspaceHomeV2Tree");
const {
  invokeWorkSessionArtifactEvents,
  invokeWorkSessionArtifactRange,
  invokeWorkSessionClose,
  invokeWorkSessionRecovery,
  invokeWorkSessionRecoveryApply,
  invokeWorkSessionResume,
  invokeWorkSessionStatus,
  invokeWorkSessionTimeline,
} = require("./workSessionClient");
const diagnoseClient = require("./diagnoseClient");
const { registerLocalReview } = require("./localReview");
const { registerDeveloperChecks } = require("./developerChecks");

function activate(context) {
  registerLocalReview(vscode, context, selectedCore);
  registerDeveloperChecks(vscode, context, selectedCore);
  const prReviewDocuments = new PrRecipeReviewDocuments(vscode);
  const prReview = new PrRecipeReviewTreeProvider(vscode, prReviewDocuments);
  const prReviewView = vscode.window.createTreeView("workbench.prRecipeReview", {
    treeDataProvider: prReview,
    showCollapseAll: true,
  });
  const workspaceHome = new WorkspaceHomeV2TreeProvider(vscode);
  const workspaceHomeView = vscode.window.createTreeView("workbench.workspaceHome", {
    treeDataProvider: workspaceHome,
    showCollapseAll: true,
  });
  const impactDocuments = new AtlasImpactDocuments(vscode);
  const impact = new AtlasImpactTreeProvider(vscode, impactDocuments);
  const impactFlow = createAtlasRecipeImpactFlow(vscode, impact, { selectedCore, currentWorkingDirectory });
  const impactView = vscode.window.createTreeView("workbench.recipeImpact", {
    treeDataProvider: impact,
    showCollapseAll: true,
  });
  const documents = new FeatureVirtualDocuments(vscode);
  const records = new FeatureRecordTreeProvider(vscode, {
    documents,
    loadCatalog: () => loadRetainedRecordCatalog(),
    loadPresentation: (record) => loadRetainedRecordPresentation(record),
    loadTransaction: (record) => vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Window,
        title: `Inspecting CURRENT Workbench transaction ${record.record_id.slice(-12)}`,
        cancellable: false,
      },
      () => loadRetainedRecordTransaction(record),
    ),
    resolveEvidenceUri: (uri) => retainedFileUri(
      vscode, uri, resolveCoreLaunch(selectedCore()), "retained Workbench evidence URI",
    ),
    onError: (message) => vscode.window.showErrorMessage(
      `Could not validate the retained Workbench record: ${message}`,
    ),
  });
  const recordsView = vscode.window.createTreeView("workbench.featureRecords", {
    treeDataProvider: records,
    showCollapseAll: true,
  });
  context.subscriptions.push(
    prReview,
    prReviewView,
    vscode.workspace.registerTextDocumentContentProvider(
      "workbench-pr-review", prReviewDocuments,
    ),
    workspaceHome,
    workspaceHomeView,
    impact,
    impactView,
    vscode.workspace.registerTextDocumentContentProvider("workbench-atlas-impact", impactDocuments),
    records,
    recordsView,
    vscode.workspace.registerTextDocumentContentProvider("workbench-feature", documents),
    vscode.workspace.onDidCloseTextDocument((document) => {
      prReviewDocuments.release(document.uri);
      documents.release(document.uri);
      impactDocuments.release(document.uri);
    }),
    vscode.commands.registerCommand("workbench.core.configureExecutable", () => configureCore()),
    vscode.commands.registerCommand("workbench.core.checkInstallation", () => refreshCoreStatus(true)),
    vscode.commands.registerCommand(
      "workbench.core.openInstallationGuide",
      () => openInstallationGuide(context.extensionUri),
    ),
    vscode.commands.registerCommand("workbench.setup.open", () => openSetup()),
    vscode.commands.registerCommand(
      "workbench.review.pullRequest", () => reviewPullRequest(prReview, prReviewView),
    ),
    vscode.commands.registerCommand("workbench.review.recipes", () => reviewRecipes()),
    vscode.commands.registerCommand("workbench.pr.openReport", () => runPrReviewUi(
      "Could not open the PR Recipe Review report", () => prReview.openReport(), prReview,
    )),
    vscode.commands.registerCommand("workbench.pr.openFindingDiff", (item) => runPrReviewUi(
      "Could not open the recipe-property diff", () => prReview.openFindingDiff(item), prReview,
    )),
    vscode.commands.registerCommand("workbench.pr.openWorkspaceFile", (item) => runPrReviewUi(
      "Could not open the current workspace file",
      () => prReview.openWorkspaceFile(item, localWorkspace().uri), prReview,
    )),
    vscode.commands.registerCommand("workbench.workspaceHome.open", () => openWorkspaceHome(workspaceHome)),
    vscode.commands.registerCommand(
      "workbench.project.qualify", () => qualifyProject(workspaceHome),
    ),
    vscode.commands.registerCommand(
      "workbench.workspaceHome.inspectOwner", (reference) => inspectHomeOwner(reference, workspaceHome),
    ),
    vscode.commands.registerCommand(
      "workbench.workspaceHome.inspectValue",
      (label, value) => inspectHomeValue(label, value, workspaceHome),
    ),
    vscode.commands.registerCommand("workbench.commandCenter.open", () => openCommandCenter()),
    vscode.commands.registerCommand("workbench.feature.browseExamples", () => browseFeatureExamples()),
    vscode.commands.registerCommand("workbench.feature.browseRetainedRecords", () => browseRetainedRecords(records)),
    vscode.commands.registerCommand("workbench.feature.records.refresh", () => refreshRetainedRecords(records)),
    vscode.commands.registerCommand("workbench.feature.records.openRecord", (item) => runRecordUi(
      "Could not open the retained Workbench record", () => records.openRecord(item),
    )),
    vscode.commands.registerCommand("workbench.feature.records.openDiff", (item) => runRecordUi(
      "Could not open the Workbench transaction diff", () => records.openDiff(item),
    )),
    vscode.commands.registerCommand("workbench.feature.records.openEvidence", (item) => runRecordUi(
      "Could not open the retained Workbench evidence", () => records.openEvidence(item),
    )),
    vscode.commands.registerCommand("workbench.atlas.searchRecipes", async () => {
      if (!requireTrustedWorkspace("Atlas requires a trusted local workspace.")) return;
      try {
        return await createAtlasRecipeBrowser(vscode, context, selectedCore, showJson).open(localWorkspace().uri.fsPath);
      } catch (error) { void vscode.window.showErrorMessage(`Could not browse Atlas evidence: ${error.message}`); }
    }),
    vscode.commands.registerCommand("workbench.source.navigate", () => navigateSource()),
    vscode.commands.registerCommand("workbench.atlas.analyzeRecipeImpact", () => impactFlow.analyze()),
    vscode.commands.registerCommand("workbench.atlas.recipeImpact.openReport", () => impactFlow.openReport()),
    vscode.commands.registerCommand("workbench.feature.reopenJob", () => reopenFeatureJob()),
    vscode.commands.registerCommand("workbench.feature.runMaterialFluidRecipe", () => runMaterialFluidRecipe(context)),
    vscode.commands.registerCommand(
      "workbench.feature.currentContextAction",
      (action) => runCurrentContextFeatureAction(action),
    ),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("workbench.coreExecutable")) {
        prReview.reset();
        prReviewView.description = undefined;
        void vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
        workspaceHome.reset();
        void refreshCoreStatus(false);
      }
      if (event.affectsConfiguration("workbench.productSpine.stateRoot")) workspaceHome.reset();
      if (event.affectsConfiguration("workbench.coreExecutable")
          || event.affectsConfiguration("workbench.feature.stateRoot")) records.reset();
    }),
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      prReview.reset();
      prReviewView.description = undefined;
      void vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
      workspaceHome.reset();
    }),
  );
  void vscode.commands.executeCommand("setContext", "workbench.coreChecked", false);
  void vscode.commands.executeCommand("setContext", "workbench.coreAvailable", false);
  void vscode.commands.executeCommand("setContext", "workbench.setupChecked", false);
  void vscode.commands.executeCommand("setContext", "workbench.setupReady", false);
  void vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
  void refreshCoreStatus(false);
}

async function refreshCoreStatus(notify) {
  if (!vscode.workspace.isTrusted) {
    await vscode.commands.executeCommand("setContext", "workbench.coreAvailable", false);
    await vscode.commands.executeCommand("setContext", "workbench.coreChecked", true);
    await vscode.commands.executeCommand("setContext", "workbench.setupChecked", true);
    await vscode.commands.executeCommand("setContext", "workbench.setupReady", false);
    return undefined;
  }
  try {
    const status = await invokeCoreVersion(selectedCore(), { cwd: currentWorkingDirectory() });
    let setup;
    try {
      setup = await invokeSetupCheck(selectedCore(), { cwd: currentWorkingDirectory() });
    } catch (_error) {
      setup = undefined;
    }
    await vscode.commands.executeCommand("setContext", "workbench.coreVersion", status.currentVersion);
    await vscode.commands.executeCommand("setContext", "workbench.coreAvailable", true);
    await vscode.commands.executeCommand("setContext", "workbench.coreChecked", true);
    await vscode.commands.executeCommand("setContext", "workbench.setupChecked", true);
    await vscode.commands.executeCommand("setContext", "workbench.setupReady", setup?.ready === true);
    if (notify) {
      void vscode.window.showInformationMessage(
        setup?.ready
          ? `Workbench ${status.currentVersion} is installed and setup is ready.`
          : `Workbench ${status.currentVersion} is installed; run Workbench Setup to finish configuration.`,
      );
    }
    return Object.freeze({ status, setup });
  } catch (error) {
    await vscode.commands.executeCommand("setContext", "workbench.coreVersion", "");
    await vscode.commands.executeCommand("setContext", "workbench.coreAvailable", false);
    await vscode.commands.executeCommand("setContext", "workbench.coreChecked", true);
    await vscode.commands.executeCommand("setContext", "workbench.setupChecked", true);
    await vscode.commands.executeCommand("setContext", "workbench.setupReady", false);
    if (notify) await showCoreUnavailable(error);
    return undefined;
  }
}

async function showCoreUnavailable(error) {
  const detail = error instanceof Error ? error.message : String(error);
  const selected = await vscode.window.showWarningMessage(
    `Workbench CLI is not available: ${detail}`,
    "Installation Guide",
    "Choose Workbench Executable",
    "Check Again",
  );
  if (selected === "Installation Guide") {
    return vscode.commands.executeCommand("workbench.core.openInstallationGuide");
  }
  if (selected === "Choose Workbench Executable") return configureCore();
  if (selected === "Check Again") return refreshCoreStatus(true);
  return undefined;
}

async function openInstallationGuide(extensionUri) {
  const guide = vscode.Uri.joinPath(extensionUri, "README.md");
  const document = await vscode.workspace.openTextDocument(guide);
  await vscode.window.showTextDocument(document, { preview: true });
  return guide;
}

async function openSetup() {
  if (!requireTrustedWorkspace(
    "Workbench setup will not run an executable from an untrusted workspace.",
  )) return undefined;
  try {
    const launch = resolveCoreLaunch(selectedCore());
    const terminal = vscode.window.createTerminal({
      name: "Workbench Setup",
      shellPath: launch.executable,
      shellArgs: commandForCoreLaunch(launch, ["setup"]),
      cwd: launch.host === "native" ? currentWorkingDirectory() : undefined,
      env: {
        ...scrubbedEnvironment(),
        CONDA_PREFIX: null,
        NODE_OPTIONS: null,
        PYTHONHOME: null,
        PYTHONINSPECT: null,
        PYTHONPATH: null,
        PYTHONSTARTUP: null,
        VIRTUAL_ENV: null,
      },
      strictEnv: true,
      iconPath: new vscode.ThemeIcon("tools"),
      message: "Workbench owns setup and repair decisions and will ask before installing dependencies.",
    });
    terminal.show();
    return terminal;
  } catch (error) {
    await showCoreUnavailable(error);
    return undefined;
  }
}

async function focusPrReview() {
  await vscode.commands.executeCommand("workbench.prRecipeReview.focus");
}

async function showPrReviewPrerequisite(kind) {
  const missing = kind === "core";
  const selected = await vscode.window.showWarningMessage(
    missing
      ? "Install or connect the Workbench CLI before reviewing a pull request."
      : "Workbench setup is incomplete or needs attention before this review.",
    missing ? "Installation Guide" : "Run Workbench Setup",
    "Choose Workbench Executable",
    "Check Again",
  );
  if (selected === "Installation Guide") {
    return vscode.commands.executeCommand("workbench.core.openInstallationGuide");
  }
  if (selected === "Run Workbench Setup") return openSetup();
  if (selected === "Choose Workbench Executable") return configureCore();
  if (selected === "Check Again") return refreshCoreStatus(true);
  return undefined;
}

function prPlanConsentDetail(plan) {
  const merge = plan.provider_merge_oid ? `\nProvider merge: ${plan.provider_merge_oid}` : "";
  return [
    `Plan: ${plan.plan_id}`,
    `Provider-recorded base: ${plan.base_repository}:${plan.base_name} @ ${plan.base_oid}`,
    `Provider-recorded head: ${plan.head_repository}:${plan.head_name} @ ${plan.head_oid}${merge}`,
    "",
    "Core-declared effects:",
    ...plan.effects.map((effect, index) => `${index + 1}. ${effect}`),
    "",
    "The review is read-only after Workbench retains these exact refs. The IDE does not approve findings or construction.",
  ].join("\n");
}

async function reviewPullRequest(prReview, prReviewView) {
  if (!requireTrustedWorkspace(
    "Workbench will not observe or prepare pull-request refs from an untrusted workspace.",
  )) return undefined;
  let workspace;
  try {
    workspace = localWorkspace();
  } catch (error) {
    void vscode.window.showErrorMessage(error.message);
    return undefined;
  }
  const readiness = await refreshCoreStatus(false);
  if (!readiness) {
    prReview.reset();
    prReviewView.description = undefined;
    await vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
    await focusPrReview();
    return showPrReviewPrerequisite("core");
  }
  if (readiness.setup?.ready !== true) {
    prReview.reset();
    prReviewView.description = undefined;
    await vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
    await focusPrReview();
    return showPrReviewPrerequisite("setup");
  }
  const raw = await vscode.window.showInputBox({
    title: "Review a Supersymmetry Pull Request",
    prompt: "Enter the GitHub pull request number. Workbench will first return an exact provider-bound plan; no refs change before you consent.",
    placeHolder: "Pull request number, for example 2002",
    value: "",
    ignoreFocusOut: true,
    validateInput: (value) => {
      if (!/^[1-9][0-9]*$/.test(value.trim())) return "Enter one positive pull request number.";
      try {
        pullRequestNumber(Number(value.trim()));
        return undefined;
      } catch (error) {
        return error instanceof Error ? error.message : String(error);
      }
    },
  });
  if (raw === undefined) return undefined;
  const request = {
    pullRequest: pullRequestNumber(Number(raw.trim())),
    source: workspace.uri.fsPath,
  };
  try {
    prReview.reset();
    prReviewView.description = `#${request.pullRequest} · observing provider`;
    await vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
    const plan = await vscode.window.withProgress({
      location: { viewId: "workbench.prRecipeReview" },
      title: `Observing pull request #${request.pullRequest}`,
      cancellable: false,
    }, () => planPullRequestReview(selectedCore(), request, { cwd: workspace.uri.fsPath }));
    prReview.setPlan(plan);
    prReviewView.description = `#${request.pullRequest} · consent required`;
    await focusPrReview();
    const confirmed = await vscode.window.showWarningMessage(
      `Prepare the exact provider-bound refs for pull request #${request.pullRequest}, then run Recipe Review?`,
      { modal: true, detail: prPlanConsentDetail(plan) },
      "Prepare and Review",
    );
    if (confirmed !== "Prepare and Review") {
      prReviewView.description = `#${request.pullRequest} · plan only; no refs changed`;
      return plan;
    }
    prReviewView.description = `#${request.pullRequest} · preparing and reviewing`;
    const report = await vscode.window.withProgress({
      location: { viewId: "workbench.prRecipeReview" },
      title: `Reviewing pull request #${request.pullRequest}`,
      cancellable: false,
    }, () => applyPullRequestReview(selectedCore(), request, plan, {
      cwd: workspace.uri.fsPath,
    }));
    prReview.setReport(report);
    prReviewView.description = `#${request.pullRequest} · ${report.summary.status}`;
    await vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", true);
    await focusPrReview();
    return report;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    prReview.setFailure(detail);
    prReviewView.description = `#${request.pullRequest} · failed`;
    await vscode.commands.executeCommand("setContext", "workbench.prReviewHasReport", false);
    await focusPrReview();
    const action = await vscode.window.showErrorMessage(
      `PR Recipe Review failed: ${detail}`,
      "Try Again",
      "Run Workbench Setup",
      "Choose Workbench Executable",
    );
    if (action === "Try Again") return reviewPullRequest(prReview, prReviewView);
    if (action === "Run Workbench Setup") return openSetup();
    if (action === "Choose Workbench Executable") return configureCore();
    return undefined;
  }
}

async function runPrReviewUi(prefix, callback, prReview) {
  if (!requireTrustedWorkspace(
    "Workbench PR Recipe Review is unavailable in an untrusted workspace.",
  )) return undefined;
  try {
    return await callback();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    const action = await vscode.window.showErrorMessage(
      `${prefix}: ${detail}`,
      "Run Workbench Setup",
      "Choose Workbench Executable",
    );
    if (action === "Run Workbench Setup") return openSetup();
    if (action === "Choose Workbench Executable") return configureCore();
    if (!prReview.report) prReview.setFailure(detail);
    return undefined;
  }
}

async function reviewRecipes() {
  if (!requireTrustedWorkspace(
    "Workbench will not inspect recipe changes from an untrusted workspace.",
  )) return undefined;
  let workspace;
  try {
    workspace = localWorkspace();
  } catch (error) {
    void vscode.window.showErrorMessage(error.message);
    return undefined;
  }
  const mode = await vscode.window.showQuickPick([
    {
      label: "Compare with a PR target",
      description: "uses an explicit local target ref and merge base",
      detail: "Workbench does not choose or fetch the target; its freshness remains visible in the report.",
      value: "pr-base",
    },
    {
      label: "Compare with an exact Git ref",
      description: "uses one local commit-ish",
      detail: "The selected local ref is materialized without changing the working tree.",
      value: "ref",
    },
    {
      label: "Compare with another folder",
      description: "uses two exact directory trees",
      detail: "Choose a baseline pack or Groovy root on this machine.",
      value: "directory",
    },
  ], {
    title: "Review Recipe Changes",
    placeHolder: "How should Workbench choose the baseline?",
    matchOnDescription: true,
    matchOnDetail: true,
  });
  if (!mode) return undefined;
  let baseline;
  if (mode.value === "directory") {
    const selected = await vscode.window.showOpenDialog({
      title: "Choose Baseline Pack or Groovy Folder",
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      openLabel: "Use as Baseline",
    });
    if (!selected || selected.length !== 1) return undefined;
    if (selected[0].scheme !== "file") {
      void vscode.window.showErrorMessage("Recipe Review requires a local filesystem baseline.");
      return undefined;
    }
    baseline = selected[0].fsPath;
  } else {
    baseline = await vscode.window.showInputBox({
      title: mode.value === "pr-base" ? "PR Target Ref" : "Exact Baseline Ref",
      prompt: mode.value === "pr-base"
        ? "Required exact local target branch or ref; no default is inferred. Workbench will use its unique merge base with HEAD"
        : "Required local commit-ish to materialize as the exact baseline; no default is inferred",
      placeHolder: mode.value === "pr-base"
        ? "Exact local target ref for this PR"
        : "Exact local baseline ref",
      value: "",
      ignoreFocusOut: true,
      validateInput: (value) => value.trim() && !/[\r\n\0]/.test(value)
        ? undefined : mode.value === "pr-base"
          ? "Enter the exact local target ref for this PR."
          : "Enter one exact local baseline ref.",
    });
    if (baseline === undefined) return undefined;
  }
  try {
    const output = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: "Reviewing Supersymmetry recipe changes",
      cancellable: false,
    }, () => invokeRecipeReview(selectedCore(), {
      baseline: baseline.trim(),
      baselineMode: mode.value,
      source: workspace.uri.fsPath,
    }, { cwd: workspace.uri.fsPath }));
    await showOwnerOutput(output);
    return output;
  } catch (error) {
    const action = await vscode.window.showErrorMessage(
      `Recipe Review failed: ${error instanceof Error ? error.message : String(error)}`,
      "Run Workbench Setup",
      "Choose Workbench Executable",
    );
    if (action === "Run Workbench Setup") return openSetup();
    if (action === "Choose Workbench Executable") return configureCore();
    return undefined;
  }
}

async function runCurrentContextFeatureAction(action) {
  if (!vscode.workspace.isTrusted) {
    throw new Error(
      "Workbench will not run a current Work Session action from an untrusted workspace.",
    );
  }
  return invokeCurrentContextFeatureAction(selectedCore(), action, {
    cwd: currentWorkingDirectory(),
    timeoutMs: 12 * 60 * 60 * 1000,
  });
}

function requireTrustedWorkspace(message) {
  if (vscode.workspace.isTrusted) return true;
  void vscode.window.showWarningMessage(message);
  return false;
}

function localWorkspace() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length !== 1 || folders[0].uri.scheme !== "file") {
    throw new Error("Open one local filesystem workspace for this Workbench command.");
  }
  return folders[0];
}

function selectedCore() {
  const configuration = vscode.workspace.getConfiguration("workbench");
  return discoverExecutable(configuration.get("coreExecutable", ""));
}

function selectedProductSpineStateRoot() {
  const configured = vscode.workspace.getConfiguration("workbench")
    .get("productSpine.stateRoot", "");
  return typeof configured === "string" && configured.trim() ? configured : undefined;
}

function currentWorkingDirectory() {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length === 1 && folders[0].uri.scheme === "file"
    ? folders[0].uri.fsPath
    : undefined;
}

async function qualifyProject(workspaceHome) {
  if (!requireTrustedWorkspace(
    "Workbench will not inspect or qualify a project from an untrusted workspace.",
  )) return undefined;
  let workspace;
  try {
    workspace = localWorkspace();
  } catch (error) {
    void vscode.window.showErrorMessage(error.message);
    return undefined;
  }
  const readiness = await refreshCoreStatus(false);
  if (!readiness) {
    return showCoreUnavailable(new Error(
      "Connect the Workbench CLI before reviewing optional project qualification.",
    ));
  }
  try {
    const executable = selectedCore();
    const stateRoot = selectedProductSpineStateRoot();
    const plan = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Window,
        title: "Inspecting Optional Project Qualification",
        cancellable: false,
      },
      () => planProjectQualification(executable, workspace.uri.fsPath, {
        cwd: workspace.uri.fsPath,
        stateRoot,
      }),
    );
    if (!plan.can_apply) {
      await vscode.window.showWarningMessage(
        "This workspace is incompatible with the selected project profile, so no qualification can be applied.",
        { modal: true, detail: qualificationPlanSummary(plan) },
      );
      return plan;
    }
    const question = plan.state === "attention"
      ? "Project qualification has attention items. Apply this exact optional local record?"
      : "Apply this exact optional project qualification record?";
    const confirmed = await vscode.window.showWarningMessage(
      question,
      { modal: true, detail: qualificationPlanSummary(plan) },
      "Apply Exact Qualification",
    );
    if (confirmed !== "Apply Exact Qualification") {
      void vscode.window.showInformationMessage(
        "No project qualification was applied. This optional action does not block other Workbench actions.",
      );
      return plan;
    }
    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Window,
        title: `Applying Exact Project Qualification (${plan.state})`,
        cancellable: false,
      },
      () => applyProjectQualification(executable, workspace.uri.fsPath, plan, {
        cwd: workspace.uri.fsPath,
        stateRoot,
      }),
    );
    void vscode.window.showInformationMessage(
      `Optional project qualification ${result.outcome}; checks are ${result.qualification.state}. `
      + "This records a project-family binding, not source, recipe, construction, runtime, Home, or release approval.",
    );
    return result;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    const action = await vscode.window.showErrorMessage(
      `Optional project qualification could not be completed: ${detail}. Other Workbench actions remain available.`,
      "Choose Workbench Executable",
      "Open Workspace Home",
    );
    if (action === "Choose Workbench Executable") return configureCore();
    if (action === "Open Workspace Home") return openWorkspaceHome(workspaceHome);
    return undefined;
  }
}

async function openWorkspaceHome(workspaceHome) {
  if (!requireTrustedWorkspace(
    "Workbench will not inspect a local workspace from an untrusted workspace.",
  )) return undefined;
  let workspace;
  try {
    workspace = localWorkspace();
  } catch (error) {
    void vscode.window.showErrorMessage(error.message);
    return undefined;
  }
  try {
    const stateRoot = selectedProductSpineStateRoot();
    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Window,
        title: "Opening Workbench Home",
        cancellable: false,
      },
      () => invokeWorkspaceHomeV2(selectedCore(), workspace.uri.fsPath, {
        cwd: workspace.uri.fsPath,
        stateRoot,
      }),
    );
    let workSession = null;
    if (result.session.state === "available" && result.session.session_id) {
      const [status, recovery] = await Promise.all([
        invokeWorkSessionStatus(selectedCore(), result.session.session_id, {
          cwd: workspace.uri.fsPath,
          stateRoot,
        }),
        invokeWorkSessionRecovery(selectedCore(), result.session.session_id, {
          cwd: workspace.uri.fsPath,
          stateRoot,
        }),
      ]);
      const timeline = await invokeWorkSessionTimeline(
        selectedCore(), result.session.session_id, {
          cwd: workspace.uri.fsPath,
          afterSequence: Math.max(-1, status.latest_sequence - 32),
          limit: 32,
          stateRoot,
        },
      );
      workSession = { status, timeline, recovery };
    }
    workspaceHome.setHome(result);
    if (workSession) workspaceHome.setWorkSession(workSession);
    return result;
  } catch (error) {
    void vscode.window.showErrorMessage(
      `Could not open Workbench Home: ${error instanceof Error ? error.message : String(error)}`,
    );
    return undefined;
  }
}

async function inspectHomeValue(label, value, workspaceHome) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Workspace Home V2 selection is not an owner-returned object");
  }
  if (label === "Work Session operation") {
    return runWorkSessionOperation(value, workspaceHome);
  }
  const document = await vscode.workspace.openTextDocument({
    language: "json",
    content: `${JSON.stringify(value, null, 2)}\n`,
  });
  await vscode.window.showTextDocument(document, { preview: true });
  return value;
}

async function runWorkSessionOperation(operation, workspaceHome) {
  const fields = Object.keys(operation).sort();
  if (JSON.stringify(fields) !== JSON.stringify(["available", "operation", "session_id"])
      || typeof operation.operation !== "string"
      || !["resume", "close", "recovery-preview", "recovery-apply"].includes(operation.operation)
      || typeof operation.session_id !== "string" || typeof operation.available !== "boolean") {
    throw new Error("Work Session operation selection is invalid");
  }
  const workspace = localWorkspace();
  const executable = selectedCore();
  const options = {
    cwd: workspace.uri.fsPath,
    stateRoot: selectedProductSpineStateRoot(),
  };
  let result;
  if (operation.operation === "recovery-preview") {
    result = await invokeWorkSessionRecovery(executable, operation.session_id, options);
  } else if (operation.operation === "resume") {
    result = await invokeWorkSessionResume(executable, operation.session_id, {
      ...options, workspace: workspace.uri.fsPath,
    });
  } else if (operation.operation === "close") {
    const confirmed = await vscode.window.showWarningMessage(
      "Close this Work Session navigation? Owner work and retained evidence are unchanged.",
      { modal: true }, "Close session navigation",
    );
    if (confirmed !== "Close session navigation") return undefined;
    result = await invokeWorkSessionClose(executable, operation.session_id, options);
  } else {
    const preview = await invokeWorkSessionRecovery(executable, operation.session_id, options);
    if (!operation.available || !preview.required) {
      throw new Error("Workbench reports that recovery is not required; apply remains unavailable");
    }
    const confirmed = await vscode.window.showWarningMessage(
      "Apply the exact core-owned recovery preview? Workbench will revalidate current owner custody.",
      { modal: true }, "Apply recovery",
    );
    if (confirmed !== "Apply recovery") return undefined;
    result = await invokeWorkSessionRecoveryApply(executable, operation.session_id, options);
  }
  if (operation.operation !== "recovery-preview") {
    const status = result;
    const [home, timeline, recovery] = await Promise.all([
      invokeWorkspaceHomeV2(executable, workspace.uri.fsPath, options),
      invokeWorkSessionTimeline(executable, operation.session_id, {
        ...options, afterSequence: Math.max(-1, status.latest_sequence - 32), limit: 32,
      }),
      invokeWorkSessionRecovery(executable, operation.session_id, options),
    ]);
    if (home.session.state !== "available"
        || home.session.session_id !== operation.session_id
        || home.session.record_id !== status.session_record_id) {
      throw new Error("refreshed Workspace Home differs from the mutated Work Session identity");
    }
    workspaceHome.setHome(home);
    workspaceHome.setWorkSession({ status, timeline, recovery });
  }
  return inspectHomeValue(
    operation.operation === "recovery-preview" ? "Recovery preview" : "Work Session result",
    result,
  );
}

async function inspectHomeOwner(reference, workspaceHome) {
  if (reference === null || typeof reference !== "object" || Array.isArray(reference)
      || typeof reference.uri !== "string") {
    throw new Error("Workspace Home V2 owner selection is invalid");
  }
  if (reference.record_kind === "workbench-live-console-session-v1") {
    const sessionId = workspaceHome.home?.session?.session_id;
    if (typeof sessionId !== "string") {
      throw new Error("Workspace Home V2 has no exact Work Session identity");
    }
    return inspectLiveConsoleOwner(reference, sessionId);
  }
  const uri = vscode.Uri.parse(reference.uri, true);
  if (uri.scheme === "file") {
    try {
      await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri));
      return;
    } catch (_error) {
      // The exact owner reference remains inspectable even when its local file is absent.
    }
  }
  await inspectHomeValue("Owner reference", reference);
  return reference;
}

async function inspectLiveConsoleOwner(reference, sessionId) {
  const options = {
    cwd: currentWorkingDirectory(),
    stateRoot: selectedProductSpineStateRoot(),
  };
  const events = [];
  let afterSequence = -1;
  let selected;
  for (;;) {
    const page = await invokeWorkSessionArtifactEvents(selectedCore(), sessionId, reference, {
      ...options, afterSequence, limit: 1024,
    });
    events.push(...page.events);
    if (!events.length) {
      await vscode.window.showInformationMessage(
        "This exact live-console owner has no retained event ranges yet.",
      );
      return undefined;
    }
    const choices = [
      ...events.map((event) => ({
        label: `#${event.sequence} · ${event.kind}`,
        description: `${event.severity} · ${event.subsystem}`,
        detail: `${event.artifact}:${event.byte_start}-${event.byte_end} · ${event.message}`,
        event,
        loadMore: false,
      })),
      ...(page.has_more ? [{
        label: "Load the next owner-sealed event page…",
        description: `after sequence ${page.next_after_sequence}`,
        detail: "No raw bytes are opened until one exact event is selected.",
        loadMore: true,
      }] : []),
    ];
    selected = await vscode.window.showQuickPick(choices, {
      title: `Live console · ${reference.record_id}`,
      placeHolder: "Select one owner-retained event to inspect its exact raw byte range",
      matchOnDescription: true,
      matchOnDetail: true,
    });
    if (!selected) return undefined;
    if (!selected.loadMore) break;
    afterSequence = page.next_after_sequence;
  }
  if (!selected.event) throw new Error("Live-console event selection changed");
  const range = await invokeWorkSessionArtifactRange(
    selectedCore(), sessionId, reference, selected.event.event_id,
    options,
  );
  return inspectHomeValue("Live-console event and exact raw range", {
    event: selected.event,
    raw_range: range,
  });
}

function retainedStateRoot() {
  const value = vscode.workspace.getConfiguration("workbench").get("feature.stateRoot", "");
  if (typeof value !== "string" || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 32 * 1024) {
    throw new Error("workbench.feature.stateRoot is invalid");
  }
  return value.trim();
}

async function loadRetainedRecordCatalog() {
  if (!vscode.workspace.isTrusted) {
    throw new Error("Retained Workbench records are unavailable in an untrusted workspace");
  }
  const executable = selectedCore();
  return invokeRecordCatalog(executable, { stateRoot: retainedStateRoot() }, {
    cwd: currentWorkingDirectory(),
  });
}

async function loadRetainedRecordPresentation(record) {
  if (!vscode.workspace.isTrusted) {
    throw new Error("Retained Workbench records are unavailable in an untrusted workspace");
  }
  return invokePresentation(selectedCore(), {
    family: record.family,
    collection: record.collection,
    reference: record.reference,
    record,
    allowVerificationRefresh: record.collection === "plans",
    stateRoot: retainedStateRoot(),
  }, { cwd: currentWorkingDirectory() });
}

async function loadRetainedRecordTransaction(record) {
  if (!vscode.workspace.isTrusted) {
    throw new Error("Current Workbench transactions are unavailable in an untrusted workspace");
  }
  if (record.collection !== "plans" || record.record_id !== record.plan_id) {
    throw new Error("Current Workbench transaction inspection requires one retained plan");
  }
  return invokeTransaction(selectedCore(), {
    family: record.family,
    planId: record.plan_id,
    record,
    stateRoot: retainedStateRoot(),
  }, { cwd: currentWorkingDirectory() });
}

async function runRecordUi(prefix, callback) {
  if (!requireTrustedWorkspace(
    "Workbench will not inspect retained owner records from an untrusted workspace.",
  )) return undefined;
  try {
    return await callback();
  } catch (error) {
    void vscode.window.showErrorMessage(
      `${prefix}: ${error instanceof Error ? error.message : String(error)}`,
    );
    return undefined;
  }
}

async function refreshRetainedRecords(records) {
  return runRecordUi("Could not discover retained Workbench records", async () => {
    const catalog = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Window,
        title: "Discovering retained Workbench records",
        cancellable: false,
      },
      () => records.refresh(),
    );
    if (!catalog.records.length) {
      void vscode.window.showInformationMessage("The selected Workbench state root has no retained developer-feature records.");
    }
    return catalog;
  });
}

async function browseRetainedRecords(records) {
  return runRecordUi("Could not browse retained Workbench records", async () => {
    const catalog = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Window,
        title: "Discovering retained Workbench records",
        cancellable: false,
      },
      () => records.refresh(),
    );
    if (!catalog.records.length) {
      void vscode.window.showInformationMessage("The selected Workbench state root has no retained developer-feature records.");
      return undefined;
    }
    const selected = await vscode.window.showQuickPick(
      catalog.records.map((record) => ({
        label: `${record.record_kind} · ${record.record_id.slice(-12)}`,
        description: `${record.family} · ${record.collection} · ${record.record_state}`,
        detail: `Plan ${record.plan_id}`,
        record,
      })),
      {
        title: "Workbench Retained Developer-Feature Records",
        placeHolder: "Choose one owner-validated record presentation",
        matchOnDescription: true,
        matchOnDetail: true,
      },
    );
    if (!selected) return undefined;
    return records.openRecord({
      type: "record",
      id: `record:${selected.record.family}:${selected.record.collection}:${selected.record.record_id}`,
      record: selected.record,
    });
  });
}

async function showJson(value) {
  const document = await vscode.workspace.openTextDocument({
    language: "json",
    content: `${JSON.stringify(value, null, 2)}\n`,
  });
  await vscode.window.showTextDocument(document, { preview: true });
}

async function showOwnerOutput(text) {
  let language = "plaintext";
  try {
    JSON.parse(text);
    language = "json";
  } catch {
    // Arbitrary command output remains owned by its module. Syntax detection
    // does not reinterpret or normalize those bytes.
  }
  const document = await vscode.workspace.openTextDocument({ language, content: text });
  await vscode.window.showTextDocument(document, { preview: true });
}

async function browseFeatureExamples() {
  if (!requireTrustedWorkspace(
    "Workbench will not query installed-core examples from an untrusted workspace.",
  )) return;
  const executable = selectedCore();
  const cwd = currentWorkingDirectory();
  try {
    const catalog = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Loading packaged Workbench Blueprint examples",
        cancellable: false,
      },
      () => invokeExamples(executable, null, { cwd }),
    );
    const selected = await vscode.window.showQuickPick(
      catalog.examples.map((example) => ({
        label: example.example_key,
        description: example.family,
        detail: `Runtime evidence: ${example.runtime_evidence_state}`,
        example,
      })),
      {
        title: "Workbench Blueprint Examples",
        placeHolder: "Choose one profile-owned, non-authorizing example",
        matchOnDescription: true,
        matchOnDetail: true,
      },
    );
    if (!selected) return;
    const exact = await invokeExamples(executable, selected.example.example_key, { cwd });
    await showJson(exact);
  } catch (error) {
    void vscode.window.showErrorMessage(
      `Could not browse Workbench Blueprint examples: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function navigateSource() {
  if (!requireTrustedWorkspace("Source navigation requires a trusted local workspace.")) return;
  try {
    const workspace = localWorkspace().uri.fsPath;
    const session = await vscode.window.showInputBox({ title: "Source Navigation", prompt: "Exact Work Session ID from workbench context select" });
    if (!session) return;
    const query = await vscode.window.showInputBox({ title: "Find Source Declaration", prompt: "Recipe, material, or quest name (source only)" });
    if (query === undefined) return;
    const result = await invokeSourceAction(selectedCore(), session, ["search", "--", query], { cwd: workspace });
    const choices = result.results.filter((row) => row.location).map((row) => ({
      label: row.label, description: row.kind, detail: row.location.path, selectionId: row.selection_id,
    }));
    const selected = await vscode.window.showQuickPick(choices, { title: "Open Source Declaration", placeHolder: result.truncated ? "Results bounded; narrow the query if needed" : "Source declarations, not observed runtime state" });
    if (!selected) return;
    const fresh = await invokeSourceAction(selectedCore(), session, ["location", selected.selectionId], { cwd: workspace });
    if (fresh.selection_id !== selected.selectionId) throw new Error("Source location changed the selected identity.");
    await openSourceLocation(vscode, workspace, fresh.location);
  } catch (error) {
    void vscode.window.showErrorMessage(`Could not open source declaration: ${error.message}`);
  }
}


async function openCommandCenter() {
  if (!requireTrustedWorkspace(
    "Workbench Command Center will not invoke a local core from an untrusted workspace.",
  )) return;
  const executable = selectedCore();
  const cwd = currentWorkingDirectory();
  try {
    const catalog = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Loading the installed Workbench command catalog",
        cancellable: false,
      },
      () => invokeCatalog(executable, { cwd }),
    );
    const selectedSuite = await vscode.window.showQuickPick(
      catalog.suites.map((suite) => ({
        label: suite.title,
        description: `${suite.command_count} actions · ${suite.availability}`,
        detail: `${suite.summary} Authority: ${suite.authority}`,
        suite,
      })),
      {
        title: "Workbench Command Center",
        placeHolder: "Choose a core-owned tool suite",
        matchOnDescription: true,
        matchOnDetail: true,
      },
    );
    if (!selectedSuite) return;
    const selectedCommand = await vscode.window.showQuickPick(
      catalog.commands
        .filter((command) => command.suite_id === selectedSuite.suite.suite_id)
        .map((command) => ({
          label: command.title,
          description: `${command.risk} · ${command.availability}`,
          detail: `${command.summary} Authority: ${command.authority}`,
          command,
        })),
      {
        title: selectedSuite.suite.title,
        placeHolder: "Choose an exact catalog action",
        matchOnDescription: true,
        matchOnDetail: true,
      },
    );
    if (!selectedCommand) return;
    const command = selectedCommand.command;
    if (command.availability === "unavailable") {
      throw new Error(`${command.title} is declared unavailable by the installed core`);
    }
    if (command.document !== null) {
      await showJson({
        catalog_digest: catalog.catalog_digest,
        command,
        note: "This catalog entry is documentation; the installed client did not execute it.",
      });
      return;
    }
    if (["mutating", "destructive"].includes(command.risk)
        && vscode.workspace.textDocuments.some((document) => document.isDirty
          && document.uri.scheme === "file"
          && vscode.workspace.getWorkspaceFolder(document.uri))) {
      throw new Error("Save or revert every dirty workspace editor before running a mutating catalog action");
    }
    const launch = resolveCoreLaunch(executable);
    const values = await collectCommandOptions(command, launch, cwd);
    if (!values) return;
    const flow = composeCommandFlow(
      catalog.catalog_digest,
      command,
      assignmentArguments(values),
    );
    const reviewValue = await invokeCommandReview(executable, flow.commandReviewArguments, {
      launch, cwd,
    });
    const bound = flow.bindReview(reviewValue);
    let ownerPreview = "";
    if (bound.ownerPreviewArguments) {
      ownerPreview = await invokeCommandOutput(executable, bound.ownerPreviewArguments, {
        launch, cwd,
      });
      await showOwnerOutput(ownerPreview);
    }
    const approved = await confirmCatalogCommand(command, bound.review, ownerPreview);
    if (!approved) return;
    if (["mutating", "destructive"].includes(command.risk)
        && vscode.workspace.textDocuments.some((document) => document.isDirty
          && document.uri.scheme === "file"
          && vscode.workspace.getWorkspaceFolder(document.uri))) {
      throw new Error("A workspace editor became dirty after review; save or revert it and review the action again");
    }
    let output;
    try {
      output = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Running ${command.title}`,
          cancellable: false,
        },
        () => invokeCommandOutput(executable, bound.executeArguments, { launch, cwd }),
      );
    } catch (error) {
      if (["mutating", "destructive"].includes(command.risk)) {
        throw new Error(
          `The installed core did not return a complete result, so the mutation outcome may be unknown. Do not repeat the action automatically; use its core-owned presentation or recovery flow. ${error instanceof Error ? error.message : String(error)}`,
          { cause: error },
        );
      }
      throw error;
    }
    await showOwnerOutput(output);
    return output;
  } catch (error) {
    void vscode.window.showErrorMessage(
      `Workbench Command Center failed: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function collectCommandOptions(command, launch, workspacePath) {
  const candidates = command.options.filter((option) => !option.console_managed);
  const selected = new Map();
  const groups = new Map();
  for (const option of candidates) {
    if (option.mutex_group) {
      const group = groups.get(option.mutex_group) || [];
      group.push(option);
      groups.set(option.mutex_group, group);
    } else if (option.required) {
      selected.set(option.key, option);
    }
  }
  for (const [groupId, options] of groups) {
    const required = options.some((option) => option.required_group);
    const choices = options.map((option) => ({
      label: option.label,
      description: option.kind,
      detail: option.help,
      option,
    }));
    if (!required) choices.unshift({ label: "$(circle-slash) Do not set this group" });
    const choice = await vscode.window.showQuickPick(choices, {
      title: `${command.title}: ${groupId}`,
      placeHolder: required ? "Choose one required option" : "Choose at most one option",
      matchOnDetail: true,
    });
    if (!choice) return undefined;
    if (choice.option) selected.set(choice.option.key, choice.option);
  }
  const optional = candidates.filter((option) => !option.mutex_group && !option.required);
  if (optional.length) {
    const choices = await vscode.window.showQuickPick(
      optional.map((option) => ({
        label: option.label,
        description: option.kind,
        detail: option.help,
        picked: false,
        option,
      })),
      {
        title: `${command.title}: optional inputs`,
        placeHolder: "Select optional inputs, or press Enter for none",
        canPickMany: true,
        matchOnDescription: true,
        matchOnDetail: true,
      },
    );
    if (!choices) return undefined;
    choices.forEach((choice) => selected.set(choice.option.key, choice.option));
  }
  const values = new Map();
  for (const option of candidates.filter((candidate) => selected.has(candidate.key))) {
    let value = await promptCommandOption(command, option, workspacePath);
    if (value === undefined) return undefined;
    if (option.kind === "path") value = mapPathValue(value, launch, option.label);
    values.set(option.key, value);
  }
  return values;
}

function mapPathValue(value, launch, label) {
  if (Array.isArray(value)) {
    return value.map((item) => mapPathValue(item, launch, label));
  }
  return pathForCatalogLaunch(value, launch, label);
}

async function promptCommandOption(command, option, workspacePath) {
  if (option.kind === "boolean") return true;
  const multiple = option.repeat || option.nargs !== "one";
  if (option.kind === "choice") {
    const choices = option.choices.map((choice) => ({ label: choice }));
    if (multiple) {
      const selected = await vscode.window.showQuickPick(choices, {
        title: `${command.title}: ${option.label}`,
        placeHolder: option.help,
        canPickMany: true,
      });
      if (selected === undefined) return undefined;
      if ((option.required || option.required_group) && selected.length === 0) {
        void vscode.window.showWarningMessage(`${option.label} requires at least one value.`);
        return promptCommandOption(command, option, workspacePath);
      }
      return selected.map((item) => item.label);
    }
    return (await vscode.window.showQuickPick(choices, {
      title: `${command.title}: ${option.label}`,
      placeHolder: option.help,
    }))?.label;
  }
  const workspaceDefault = option.kind === "path" && workspacePath
    && ["path", "workspace", "source_checkout"].includes(option.key)
    ? workspacePath : "";
  const defaultValue = option.default === undefined
    ? workspaceDefault
    : multiple || option.kind === "json" ? JSON.stringify(option.default) : String(option.default);
  const raw = await vscode.window.showInputBox({
    title: `${command.title}: ${option.label}`,
    prompt: option.help,
    value: defaultValue,
    password: option.sensitive,
    ignoreFocusOut: true,
    validateInput: (value) => validateCommandOption(value, option, multiple),
  });
  if (raw === undefined) return undefined;
  if (option.kind === "integer") return parseExactIntegerInput(raw, multiple);
  if (multiple || option.kind === "json") return JSON.parse(raw);
  return raw;
}

function validateCommandOption(value, option, multiple) {
  if (!value) return option.required || option.required_group
    ? "A value is required."
    : "Enter a value, or cancel and deselect this optional input.";
  if (multiple || option.kind === "json") {
    try {
      const parsed = option.kind === "integer"
        ? parseExactIntegerInput(value, true) : JSON.parse(value);
      if (multiple && !Array.isArray(parsed)) return "Enter a JSON array.";
      if (Array.isArray(parsed) && parsed.length === 0
          && (option.required || option.required_group || option.nargs === "one_or_more")) {
        return "Enter at least one value.";
      }
      if (typeof option.nargs === "number" && Array.isArray(parsed)) {
        if (option.repeat && parsed.some(Array.isArray)) {
          if (!parsed.every((item) => Array.isArray(item) && item.length === option.nargs)) {
            return `Enter groups containing exactly ${option.nargs} values.`;
          }
        } else if (parsed.length !== option.nargs) {
          return `Enter exactly ${option.nargs} values.`;
        }
      }
    } catch {
      return option.kind === "integer"
        ? "Enter a JSON array containing only integers." : "Enter valid JSON.";
    }
  } else if (option.kind === "integer") {
    try {
      parseExactIntegerInput(value, false);
    } catch {
      return "Enter an integer.";
    }
  }
  return undefined;
}

async function confirmCatalogCommand(command, review, ownerPreview) {
  const detail = [
    command.summary,
    `Authority: ${command.authority}`,
    `Risk: ${command.risk}`,
    `Review: ${review.review_digest}`,
    `Preview argv: ${review.preview_command}`,
    `Execution argv: ${review.execute_command}`,
    ...(ownerPreview ? [
      "The complete owner preview is open in an editor. Bounded excerpt:",
      ownerPreview.slice(0, 8000),
    ] : []),
    ...(command.limitations.length ? ["Limitations:", ...command.limitations] : []),
  ].join("\n").slice(0, 12 * 1024);
  if (command.risk === "read-only") {
    return await vscode.window.showInformationMessage(
      `Run reviewed action ${command.title}?`,
      { modal: true, detail },
      "Run Reviewed Action",
    ) === "Run Reviewed Action";
  }
  const consent = command.risk === "destructive"
    ? "Execute Destructive Reviewed Action" : "Execute Reviewed Action";
  return await vscode.window.showWarningMessage(
    `${command.title} is ${command.risk}.`,
    { modal: true, detail },
    consent,
  ) === consent;
}

async function runMaterialFluidRecipe(context) {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("Workbench will not run a local developer feature from an untrusted workspace.");
    return;
  }
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length !== 1 || folders[0].uri.scheme !== "file") {
    void vscode.window.showErrorMessage("Open one local filesystem workspace before running a Workbench developer feature.");
    return;
  }
  const configuration = vscode.workspace.getConfiguration("workbench");
  const plan = await vscode.window.showInputBox({
    title: "Run Reviewed Material/Fluid Recipe",
    prompt: "Exact reviewed workbench-developer-material-fluid-recipe-plan ID",
    value: context.workspaceState.get("workbench.feature.lastPlanId", ""),
    ignoreFocusOut: true,
    validateInput: (value) => /^workbench-developer-material-fluid-recipe-plan:sha256:[0-9a-f]{64}$/.test(value.trim())
      ? undefined : "Enter one exact reviewed material-fluid-recipe plan ID.",
  });
  if (plan === undefined) return;
  const planId = plan.trim();
  const consent = await vscode.window.showWarningMessage(
    `Run exact reviewed plan ${planId} in a disposable Cleanroom client? The source checkout is input-only.`,
    { modal: true },
    "Run Exact Plan",
  );
  if (consent !== "Run Exact Plan") return;
  const launcherExecutable = configuration.get("feature.launcherExecutable", "").trim();
  const launcherRoot = configuration.get("feature.launcherRoot", "").trim();
  if (!launcherExecutable || !launcherRoot) {
    void vscode.window.showErrorMessage(
      "Configure workbench.feature.launcherExecutable and workbench.feature.launcherRoot before running the feature.",
    );
    return;
  }
  await context.workspaceState.update("workbench.feature.lastPlanId", planId);
  const executable = discoverExecutable(configuration.get("coreExecutable", ""));
  try {
    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Running reviewed Workbench feature in disposable Cleanroom",
        cancellable: false,
      },
      () => invokeRun(executable, {
        plan: planId,
        launcher: configuration.get("feature.launcher", "prism"),
        launcherExecutable,
        launcherRoot,
        launcherProfile: configuration.get("feature.launcherProfile", ""),
        launcherJava: configuration.get("feature.launcherJava", ""),
        launcherJavaState: configuration.get("feature.launcherJavaState", ""),
        packwizExecutable: configuration.get("feature.packwizExecutable", ""),
        seedRoots: configuration.get("feature.seedRoots", []),
        stateRoot: configuration.get("feature.stateRoot", ""),
        memoryMiB: configuration.get("feature.memoryMiB", 8192),
        offlineName: configuration.get("feature.offlineName", "Workbench"),
        timeoutSeconds: configuration.get("feature.timeoutSeconds", 600),
        attachTimeoutSeconds: configuration.get("feature.attachTimeoutSeconds", 120),
        sessionTimeoutSeconds: configuration.get("feature.sessionTimeoutSeconds", 21600),
        cwd: folders[0].uri.fsPath,
      }),
    );
    const document = await vscode.workspace.openTextDocument({
      language: "json",
      content: `${JSON.stringify(result.value, null, 2)}\n`,
    });
    await vscode.window.showTextDocument(document, { preview: true });
    if (result.complete) {
      void vscode.window.showInformationMessage(`Workbench feature completed: ${result.id}`);
    } else {
      void vscode.window.showWarningMessage(`Workbench feature is incomplete (${result.outcome}): ${result.id}`);
    }
    return result;
  } catch (error) {
    void vscode.window.showErrorMessage(
      `Could not run the reviewed Workbench feature: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function reopenFeatureJob() {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage("Workbench will not attach to a local job from an untrusted workspace.");
    return;
  }
  const selected = await vscode.window.showOpenDialog({
    title: "Open Workbench Feature Job Attachment",
    canSelectFiles: true,
    canSelectFolders: false,
    canSelectMany: false,
    filters: { JSON: ["json"] },
  });
  if (!selected || selected.length !== 1) {
    return;
  }
  try {
    const bytes = await vscode.workspace.fs.readFile(selected[0]);
    if (bytes.byteLength < 2 || bytes.byteLength > 64 * 1024) {
      throw new Error("job attachment is outside the 64 KiB boundary");
    }
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    const keys = ["context_ref_id", "credential", "endpoint", "input_binding_id", "job_id", "job_submission_id"];
    if (value === null || typeof value !== "object" || Array.isArray(value)
        || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys)) {
      throw new Error("job attachment fields changed");
    }
    const configuration = vscode.workspace.getConfiguration("workbench");
    const executable = discoverExecutable(configuration.get("coreExecutable", ""));
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Reopening Workbench Feature Studio job", cancellable: false },
      () => invokeResult(executable, {
        endpoint: value.endpoint,
        credential: value.credential,
      }, {
        contextRefId: value.context_ref_id,
        inputBindingId: value.input_binding_id,
        jobId: value.job_id,
        jobSubmissionId: value.job_submission_id,
      }, { cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath }),
    );
    const document = await vscode.workspace.openTextDocument({
      language: "json",
      content: `${JSON.stringify(result, null, 2)}\n`,
    });
    await vscode.window.showTextDocument(document, { preview: true });
  } catch (error) {
    void vscode.window.showErrorMessage(`Could not reopen Feature Studio job: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function configureCore() {
  const configuration = vscode.workspace.getConfiguration("workbench");
  const current = configuration.get("coreExecutable", "");
  const selected = await vscode.window.showInputBox({
    title: "Configure Workbench Core",
    prompt: "Exact installed Workbench executable path or command name; on Windows, a \\\\wsl.localhost\\DISTRO\\... path uses the bounded no-shell WSL adapter",
    value: current || discoverExecutable(""),
    ignoreFocusOut: true,
    validateInput: (value) => value.trim() && !value.includes("\0") ? undefined : "Enter one non-empty executable path or command name.",
  });
  if (selected === undefined) {
    return;
  }
  await configuration.update("coreExecutable", selected.trim(), vscode.ConfigurationTarget.Global);
  void vscode.window.showInformationMessage(
    "Workbench core executable configured. Developer commands will invoke it directly without a shell.",
  );
}

function deactivate() {}

module.exports = { activate, deactivate, diagnoseClient };
