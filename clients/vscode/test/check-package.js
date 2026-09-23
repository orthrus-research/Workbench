"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
for (const relative of [
  "package.json", "extension.js", "coreLaunch.js", "coreClient.js",
  "coreStatusClient.js", "recipeReviewClient.js",
  "prRecipeReviewClient.js", "prRecipeReviewTree.js",
  "projectQualificationClient.js",
  "coreCommandClient.js", "commandCenterClient.js", "developerToolsClient.js",
  "currentContextFeatureClient.js",
  "workspaceHomeClient.js",
  "workspaceHomeV2Client.js", "workspaceHomeV2Tree.js", "workSessionClient.js",
  "diagnoseClient.js",
  "atlasCompleteRecipeImpactClient.js", "atlasRecipeImpactClient.js", "atlasRecipeImpactTree.js", "atlasRecipeImpactFlow.js",
  "atlasRecipeBrowser.js", "atlasRecipeBrowseValidation.js", "atlasRecipeSession.js",
  "sourceNavigationClient.js",
  "localReviewClient.js",
  "localReview.js",
  "developerContext.js",
  "developerChecksClient.js",
  "developerChecks.js",
  "materialSnapshotClient.js",
    "materialDeliveryClient.js",
    "materialResults.js",
  "materialChecksClient.js",
  "materialChecks.js",
  "featureRecordClient.js", "featureRecordTree.js", "featureServiceClient.js",
  "developerFeatureClient.js", "README.md", "CHANGELOG.md", "SUPPORT.md",
  "media/workbench-icon.png",
  "media/axiom-results.svg",
]) {
  assert.ok(fs.statSync(path.join(root, relative)).isFile(), `${relative} is missing`);
}
const descriptor = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const packageLock = JSON.parse(fs.readFileSync(path.join(root, "package-lock.json"), "utf8"));
const extensionHostIntegrationSource = fs.readFileSync(
  path.join(root, "test", "extension-host-integration.js"),
  "utf8",
);
const extensionHostRequiredCommandsMatch = extensionHostIntegrationSource.match(
  /const REQUIRED_COMMANDS = (\[[\s\S]*?\]);/,
);
assert.ok(extensionHostRequiredCommandsMatch, "Extension Host command contract is absent");
const extensionHostRequiredCommands = [
  ...extensionHostRequiredCommandsMatch[1].matchAll(/"([^"]+)"/g),
].map((match) => match[1]);
assert.deepEqual(
  [...extensionHostRequiredCommands].sort(),
  descriptor.contributes.commands.map((command) => command.command).sort(),
  "Extension Host must exercise every contributed command",
);
const extensionHostRunnerSource = fs.readFileSync(
  path.join(root, "test", "run-extension-host.mjs"),
  "utf8",
);
assert.match(extensionHostRunnerSource, /expectedRequiredCommands/);
assert.doesNotMatch(extensionHostRunnerSource, /required_commands\.length !== \d+/);
assert.equal(descriptor.name, "workbench-vscode");
assert.equal(descriptor.displayName, "Workbench for Minecraft Development");
assert.match(descriptor.version, /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/);
assert.equal(packageLock.version, descriptor.version);
assert.equal(packageLock.packages[""].version, descriptor.version);
assert.equal(descriptor.publisher, "cleanroom-workbench");
assert.equal(descriptor.main, "./extension.js");
assert.equal(descriptor.icon, "media/workbench-icon.png");
assert.equal(descriptor.preview, true);
assert.equal(descriptor.repository.url, "https://github.com/orthrus-research/workbench.git");
assert.equal(descriptor.homepage, "https://github.com/orthrus-research/workbench#readme");
assert.equal(descriptor.bugs.url, "https://github.com/orthrus-research/workbench/issues");
assert.deepEqual(descriptor.extensionKind, ["workspace"]);
assert.equal(descriptor.capabilities.untrustedWorkspaces.supported, false);
assert.equal(descriptor.capabilities.virtualWorkspaces.supported, false);
assert.equal(descriptor.private, undefined);
assert.equal(descriptor.scripts.package, undefined);
assert.match(descriptor.description, /Review Minecraft modpack recipe changes/);
assert.match(descriptor.capabilities.virtualWorkspaces.description, /local filesystem/);
const icon = fs.readFileSync(path.join(root, descriptor.icon));
assert.deepEqual([...icon.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
assert.equal(icon.readUInt32BE(16), 256);
assert.equal(icon.readUInt32BE(20), 256);
const configuration = descriptor.contributes.configuration.properties;
for (const setting of [
  "workbench.productSpine.stateRoot",
  "workbench.feature.launcherExecutable",
  "workbench.feature.launcherRoot",
  "workbench.feature.packwizExecutable",
  "workbench.feature.stateRoot",
]) {
  assert.equal(configuration[setting]?.type, "string", `${setting} is missing`);
}
const source = fs.readFileSync(path.join(root, "coreClient.js"), "utf8");
assert.match(source, /discoverExecutable/);
assert.match(source, /scrubbedEnvironment/);
assert.doesNotMatch(source, /core-compatibility|invokeCore|execFile\(/);
const launchSource = fs.readFileSync(path.join(root, "coreLaunch.js"), "utf8");
assert.match(launchSource, /installedWindowsWslMappingV1/);
const featureSource = fs.readFileSync(path.join(root, "featureServiceClient.js"), "utf8");
assert.match(featureSource, /execFile\(/);
assert.match(featureSource, /shell: false/);
assert.doesNotMatch(featureSource, /execSync|spawnSync|shell: true/);
const developerFeatureSource = fs.readFileSync(path.join(root, "developerFeatureClient.js"), "utf8");
assert.match(developerFeatureSource, /execFile\(/);
assert.match(developerFeatureSource, /shell: false/);
assert.match(developerFeatureSource, /"feature", "run", "material-fluid-recipe"/);
assert.match(developerFeatureSource, /"--packwiz-executable"/);
assert.doesNotMatch(developerFeatureSource, /execSync|spawnSync|shell: true/);
const commandSource = fs.readFileSync(path.join(root, "coreCommandClient.js"), "utf8");
assert.match(commandSource, /execFile\(/);
assert.match(commandSource, /shell: false/);
assert.match(commandSource, /commandForCoreLaunch/);
assert.match(commandSource, /scrubbedEnvironment/);
assert.match(commandSource, /const MAX_TIMEOUT_MS = 12 \* 60 \* 60 \* 1000;/);
assert.doesNotMatch(commandSource, /execSync|spawnSync|shell: true/);
const currentContextSource = fs.readFileSync(
  path.join(root, "currentContextFeatureClient.js"), "utf8",
);
assert.match(currentContextSource, /"change", "material-fluid-recipe", action, "--json"/);
assert.doesNotMatch(
  currentContextSource,
  /change_id|plan_id|state_root|runtime_config|consent_plan_id|workspace_uri/,
);
const catalogSource = fs.readFileSync(path.join(root, "commandCenterClient.js"), "utf8");
assert.match(catalogSource, /"console", "catalog", "--json"/);
assert.match(catalogSource, /expect-review-digest/);
assert.match(catalogSource, /workbench-live-console-command-catalog-v2/);
assert.match(
  catalogSource,
  /timeoutMs: options\.timeoutMs === undefined \? MAX_TIMEOUT_MS : options\.timeoutMs/,
);
const toolsSource = fs.readFileSync(path.join(root, "developerToolsClient.js"), "utf8");
assert.match(toolsSource, /"feature", "examples"/);
assert.match(toolsSource, /"atlas", "recipes", "search"/);
assert.match(toolsSource, /workbench-atlas-recipe-health-search-v1/);
const homeClientSource = fs.readFileSync(path.join(root, "workspaceHomeClient.js"), "utf8");
assert.match(homeClientSource, /workbench-workspace-home-v1/);
assert.match(homeClientSource, /validateWorkspaceHome/);
assert.doesNotMatch(
  homeClientSource,
  /invokeCoreJson|pathForCoreLaunch|resolveCoreLaunch|invokeWorkspaceHome/,
);
const homeV2ClientSource = fs.readFileSync(path.join(root, "workspaceHomeV2Client.js"), "utf8");
assert.match(homeV2ClientSource, /workbench-workspace-home-v2/);
assert.match(homeV2ClientSource, /capability_id/);
assert.match(homeV2ClientSource, /capability_key/);
assert.doesNotMatch(homeV2ClientSource, /execSync|spawnSync|shell: true/);
const homeV2TreeSource = fs.readFileSync(path.join(root, "workspaceHomeV2Tree.js"), "utf8");
assert.match(homeV2TreeSource, /Recommended Jobs/);
assert.match(homeV2TreeSource, /Recovery Required/);
assert.match(homeV2TreeSource, /timeline-event/);
assert.match(homeV2TreeSource, /workbench\.workspaceHome\.inspectOwner/);
assert.match(homeV2TreeSource, /Home argv is a preview/);
assert.doesNotMatch(homeV2TreeSource, /workspaceHome\.runJob|ProcessExecution/);
assert.doesNotMatch(homeV2TreeSource, /createWebviewPanel|registerWebviewPanelSerializer/);
const sessionV2Source = fs.readFileSync(path.join(root, "workSessionClient.js"), "utf8");
assert.match(sessionV2Source, /workbench-work-session-summary-v1/);
assert.match(sessionV2Source, /recovery must never be automatic/i);
assert.doesNotMatch(sessionV2Source, /execSync|spawnSync|shell: true/);
const diagnosisSource = fs.readFileSync(path.join(root, "diagnoseClient.js"), "utf8");
assert.match(diagnosisSource, /workbench-diagnosis-v1/);
assert.match(diagnosisSource, /workbench-reproduction-capsule-inspection-v1/);
assert.match(diagnosisSource, /pathForCoreLaunch/);
assert.match(diagnosisSource, /invokeCoreJson/);
assert.doesNotMatch(diagnosisSource, /execSync|spawnSync|ProcessExecution|shell: true/);
const impactSource = fs.readFileSync(path.join(root, "atlasRecipeImpactClient.js"), "utf8");
assert.match(impactSource, /"atlas", "recipes", "impact"/);
assert.match(impactSource, /"--max-depth"/);
assert.match(impactSource, /"--max-nodes"/);
assert.match(impactSource, /workbench-atlas-recipe-impact-report-v1/);
assert.match(impactSource, /pathForCoreLaunch/);
assert.doesNotMatch(impactSource, /execSync|spawnSync|shell: true/);
const completeImpactSource = fs.readFileSync(path.join(root, "atlasCompleteRecipeImpactClient.js"), "utf8");
assert.match(completeImpactSource, /workbench-atlas-recipe-impact-report-v2/);
assert.match(completeImpactSource, /"--exploration", "complete-finite"/);
assert.match(completeImpactSource, /validateImpactSearchLink/);
assert.match(completeImpactSource, /validateAtlasNumericPrecision/);
assert.doesNotMatch(completeImpactSource, /execSync|spawnSync|shell: true/);
const impactTreeSource = fs.readFileSync(path.join(root, "atlasRecipeImpactTree.js"), "utf8");
assert.match(impactTreeSource, /provideTextDocumentContent/);
assert.match(impactTreeSource, /Propagation candidates/);
assert.doesNotMatch(impactTreeSource, /createWebviewPanel|registerWebviewPanelSerializer/);
const prReviewClientSource = fs.readFileSync(path.join(root, "prRecipeReviewClient.js"), "utf8");
assert.match(prReviewClientSource, /"review", "pr"/);
assert.match(prReviewClientSource, /"--plan"/);
assert.match(prReviewClientSource, /"--apply"/);
assert.match(prReviewClientSource, /workbench-pr-preparation-plan-v2/);
assert.match(prReviewClientSource, /workbench-recipe-review-v2/);
assert.match(prReviewClientSource, /pathForCoreLaunch/);
assert.match(prReviewClientSource, /attention_scope/);
assert.match(prReviewClientSource, /git_hygiene/);
assert.doesNotMatch(prReviewClientSource, /--yes|execSync|spawnSync|shell: true/);
const prReviewTreeSource = fs.readFileSync(path.join(root, "prRecipeReviewTree.js"), "utf8");
assert.match(prReviewTreeSource, /Decision summary/);
assert.match(prReviewTreeSource, /PR-introduced and supplied-runtime attention/);
assert.match(prReviewTreeSource, /Pre-existing and candidate-wide attention/);
assert.match(prReviewTreeSource, /new api\.ThemeIcon/);
assert.match(prReviewTreeSource, /"vscode\.diff"/);
assert.match(prReviewTreeSource, /"vscode\.open"/);
assert.doesNotMatch(prReviewTreeSource, /createWebviewPanel|registerWebviewPanelSerializer/);
const qualificationSource = fs.readFileSync(
  path.join(root, "projectQualificationClient.js"), "utf8",
);
assert.match(qualificationSource, /"project", "qualify"/);
assert.match(qualificationSource, /"--plan"/);
assert.match(qualificationSource, /"--apply"/);
assert.match(qualificationSource, /"--state-root"/);
assert.match(qualificationSource, /workbench-project-qualification-plan-v1/);
assert.match(qualificationSource, /workbench-project-qualification-result-v1/);
assert.match(qualificationSource, /can_apply/);
assert.match(qualificationSource, /pathForCoreLaunch/);
assert.match(qualificationSource, /Qualification state:/);
assert.match(qualificationSource, /does not approve source changes/);
assert.doesNotMatch(qualificationSource, /--yes|execSync|spawnSync|shell: true/);
const recordSource = fs.readFileSync(path.join(root, "featureRecordClient.js"), "utf8");
assert.match(recordSource, /"feature", "records"/);
assert.match(recordSource, /"feature", "present"/);
assert.match(recordSource, /"feature", "transaction"/);
assert.match(recordSource, /workbench-developer-feature-record-catalog-v1/);
assert.match(recordSource, /workbench-developer-feature-presentation-v1/);
assert.match(recordSource, /workbench-developer-feature-presentation-v2/);
assert.match(recordSource, /workbench-developer-feature-transaction-view-v1/);
assert.match(recordSource, /pathForCoreLaunch/);
assert.doesNotMatch(recordSource, /execSync|spawnSync|shell: true/);
const recordTreeSource = fs.readFileSync(path.join(root, "featureRecordTree.js"), "utf8");
assert.match(recordTreeSource, /provideTextDocumentContent/);
assert.match(recordTreeSource, /"vscode\.diff"/);
assert.match(recordTreeSource, /workbench\.feature\.records\.openEvidence/);
assert.match(recordTreeSource, /sealed size and digest/);
assert.doesNotMatch(recordTreeSource, /command: "vscode\.open"/);
assert.match(recordTreeSource, /Runtime evidence/);
assert.match(recordTreeSource, /CURRENT transaction state/);
assert.match(recordTreeSource, /exact owner plan presentation/);
assert.doesNotMatch(recordTreeSource, /createWebviewPanel|registerWebviewPanelSerializer/);
const extensionSource = fs.readFileSync(path.join(root, "extension.js"), "utf8");
assert.match(extensionSource, /WorkspaceHomeV2TreeProvider/);
assert.match(extensionSource, /invokeWorkspaceHomeV2/);
assert.match(extensionSource, /invokeWorkSessionTimeline/);
assert.match(extensionSource, /require\("\.\/diagnoseClient"\)/);
assert.match(extensionSource, /workbench\.workspaceHome\.inspectValue/);
assert.match(extensionSource, /registerCommand\(\s*"workbench\.review\.pullRequest"/);
assert.match(extensionSource, /registerCommand\(\s*"workbench\.project\.qualify"/);
assert.match(extensionSource, /location: \{ viewId: "workbench\.prRecipeReview" \}/);
assert.match(extensionSource, /vscode\.workspace\.isTrusted/);
assert.doesNotMatch(extensionSource, /createWebviewPanel|registerWebviewPanelSerializer/);
const qualificationActionStart = extensionSource.indexOf("async function qualifyProject");
const qualificationActionEnd = extensionSource.indexOf(
  "\nasync function openWorkspaceHome", qualificationActionStart,
);
assert.ok(qualificationActionStart >= 0 && qualificationActionEnd > qualificationActionStart);
const qualificationActionSource = extensionSource.slice(
  qualificationActionStart, qualificationActionEnd,
);
assert.match(qualificationActionSource, /if \(!plan\.can_apply\)/);
assert.match(
  qualificationActionSource,
  /showWarningMessage\([\s\S]*"Apply Exact Qualification"[\s\S]*confirmed !== "Apply Exact Qualification"[\s\S]*return plan;[\s\S]*applyProjectQualification/,
);
assert.match(qualificationActionSource, /stateRoot: selectedProductSpineStateRoot|const stateRoot = selectedProductSpineStateRoot/);
assert.doesNotMatch(
  qualificationActionSource,
  /workspaceHome\.reset\(\);[\s\S]*await openWorkspaceHome\(workspaceHome\)/,
);
assert.match(qualificationActionSource, /not source, recipe, construction, runtime, Home, or release approval/);
const liveConsoleStart = extensionSource.indexOf("async function inspectLiveConsoleOwner");
const liveConsoleEnd = extensionSource.indexOf("\nfunction retainedStateRoot", liveConsoleStart);
assert.ok(liveConsoleStart >= 0 && liveConsoleEnd > liveConsoleStart);
const liveConsoleSource = extensionSource.slice(liveConsoleStart, liveConsoleEnd);
assert.match(liveConsoleSource, /stateRoot: selectedProductSpineStateRoot\(\)/);
assert.match(liveConsoleSource, /invokeWorkSessionArtifactEvents\([\s\S]*\.\.\.options/);
assert.match(liveConsoleSource, /invokeWorkSessionArtifactRange\([\s\S]*\n    options,/);
assert.match(
  extensionSource,
  /invokeWorkspaceHomeV2\(executable, workspace\.uri\.fsPath, options\)[\s\S]*workspaceHome\.setHome\(home\)[\s\S]*workspaceHome\.setWorkSession/,
);
assert.doesNotMatch(extensionSource, /workbench\.workspaceHome\.runJob/);
assert.match(extensionSource, /registerCommand\("workbench\.feature\.records\.openEvidence"/);
assert.match(extensionSource, /workbench\.feature\.currentContextAction/);
assert.match(extensionSource, /invokeTransaction/);
assert.match(extensionSource, /Inspecting CURRENT Workbench transaction/);
assert.doesNotMatch(extensionSource, /release-feature-application|FeatureApplication/);
assert.doesNotMatch(extensionSource, /workbench\.release\./);
for (const command of descriptor.contributes.commands) {
  assert.doesNotMatch(command.command, /checkCore|applicationStatus|applyDisposableFeature|rollbackDisposableFeature|applyDirectFeature|rollbackDirectFeature/);
  assert.doesNotMatch(command.command, /^workbench\.release\./);
  assert.doesNotMatch(command.command, /diagnos|foundation[.]?assist/i);
}
assert.deepEqual(
  descriptor.contributes.commands.map((command) => command.command).sort(),
  [
    "workbench.atlas.analyzeRecipeImpact",
    "workbench.atlas.recipeImpact.openReport",
    "workbench.atlas.searchRecipes",
    "workbench.axiomResults.actions",
    "workbench.axiomResults.captured",
    "workbench.axiomResults.evidence",
    "workbench.axiomResults.next",
    "workbench.axiomResults.open",
    "workbench.axiomResults.snapshot",
    "workbench.checks.saved",
    "workbench.commandCenter.open",
    "workbench.core.checkInstallation",
    "workbench.core.configureExecutable",
    "workbench.core.openInstallationGuide",
    "workbench.feature.browseExamples",
    "workbench.feature.browseRetainedRecords",
    "workbench.feature.records.openDiff",
    "workbench.feature.records.openRecord",
    "workbench.feature.records.refresh",
    "workbench.feature.reopenJob",
    "workbench.feature.runMaterialFluidRecipe",
    "workbench.pr.openFindingDiff",
    "workbench.pr.openReport",
    "workbench.pr.openWorkspaceFile",
    "workbench.project.qualify",
    "workbench.review.local",
    "workbench.review.pullRequest",
    "workbench.review.recipes",
    "workbench.setup.open",
    "workbench.source.navigate",
    "workbench.workspaceHome.open",
  ],
);
assert.equal(descriptor.contributes.walkthroughs[0].steps.length, 4);
assert.match(
  descriptor.contributes.walkthroughs[0].steps[0].description,
  /workbench\.core\.openInstallationGuide/,
);
assert.match(extensionSource, /registerCommand\(\s*"workbench\.core\.openInstallationGuide"/);
assert.match(extensionSource, /vscode\.Uri\.joinPath\(extensionUri, "README\.md"\)/);
assert.doesNotMatch(extensionSource, /origin\/main/);
assert.match(extensionSource, /no default is inferred/);
assert.deepEqual(
  descriptor.contributes.viewsWelcome
    .filter((entry) => entry.view === "workbench.workspaceHome")
    .map((entry) => entry.when),
  [
    "!workbench.coreChecked",
    "workbench.coreChecked && !workbench.coreAvailable",
    "workbench.coreChecked && workbench.coreAvailable && workbench.setupChecked && !workbench.setupReady",
    "workbench.coreChecked && workbench.coreAvailable && workbench.setupChecked && workbench.setupReady",
  ],
);
const prWelcome = descriptor.contributes.viewsWelcome
  .filter((entry) => entry.view === "workbench.prRecipeReview");
assert.deepEqual(prWelcome.map((entry) => entry.when), [
  "!workbench.coreChecked",
  "workbench.coreChecked && !workbench.coreAvailable",
  "workbench.coreChecked && workbench.coreAvailable && workbench.setupChecked && !workbench.setupReady",
  "workbench.coreChecked && workbench.coreAvailable && workbench.setupChecked && workbench.setupReady",
]);
for (const entry of prWelcome.slice(1)) {
  assert.equal(
    entry.contents.split("\n").filter((line) => /^\[[^\]]+\]\(command:[^)]+\)$/.test(line)).length,
    1,
    "each actionable PR Welcome View state must expose one primary button",
  );
}
assert.deepEqual(descriptor.contributes.views.explorer, [{
  id: "workbench.workspaceHome",
  name: "Workbench Home",
  when: "isWorkspaceTrusted",
  visibility: "visible",
}, {
  id: "workbench.prRecipeReview",
  name: "PR Recipe Review",
  when: "isWorkspaceTrusted",
  visibility: "visible",
}, {
  id: "workbench.featureRecords",
  name: "Workbench Retained Records",
  when: "isWorkspaceTrusted",
  visibility: "collapsed",
}, {
  id: "workbench.recipeImpact",
  name: "Atlas Recipe Impact Candidates",
  when: "isWorkspaceTrusted",
  visibility: "collapsed",
}]);
const prCommands = Object.fromEntries(descriptor.contributes.commands
  .filter((command) => command.command.startsWith("workbench.pr.")
    || command.command === "workbench.review.pullRequest")
  .map((command) => [command.command, command]));
assert.equal(prCommands["workbench.review.pullRequest"].icon, "$(git-pull-request)");
assert.equal(prCommands["workbench.pr.openReport"].icon, "$(json)");
assert.equal(prCommands["workbench.pr.openFindingDiff"].icon, "$(diff)");
assert.equal(prCommands["workbench.pr.openWorkspaceFile"].icon, "$(go-to-file)");
const qualificationCommand = descriptor.contributes.commands.find(
  (command) => command.command === "workbench.project.qualify",
);
assert.equal(qualificationCommand.title, "Qualify This Pack");
assert.equal(qualificationCommand.icon, "$(checklist)");
assert.match(
  descriptor.contributes.viewsWelcome.find((entry) => (
    entry.view === "workbench.workspaceHome"
    && entry.when === "workbench.coreChecked && workbench.coreAvailable && workbench.setupChecked && workbench.setupReady"
  )).contents,
  /qualify this pack[\s\S]*matches the Supersymmetry profile[\s\S]*optionally save that association[\s\S]*not approval[\s\S]*does not block other actions/i,
);
assert.ok(descriptor.contributes.menus["view/title"].some((item) => (
  item.command === "workbench.project.qualify"
  && item.when === "view == workbench.workspaceHome && isWorkspaceTrusted"
)));
console.log("VS Code developer-client package inputs passed.");

assert.deepEqual(descriptor.contributes.viewsContainers, { panel: [{ id: "workbench-axiom-results", title: "Axiom Results", icon: "media/axiom-results.svg" }] });
assert.deepEqual(descriptor.contributes.views["workbench-axiom-results"], [{ id: "workbench.axiomResults", name: "Axiom Results", when: "isWorkspaceTrusted && workbench.axiomHasResults", visibility: "visible", icon: "media/axiom-results.svg" }]);

for (const container of Object.values(descriptor.contributes.viewsContainers).flat()) assert.match(container.id, /^[a-zA-Z0-9_-]+$/, "native view container id must satisfy VS Code's contribution schema");
