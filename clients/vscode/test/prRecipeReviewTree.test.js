"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  PrRecipeReviewDocuments,
  PrRecipeReviewTreeProvider,
  SECTION_ORDER,
} = require("../prRecipeReviewTree");
const { plan, report } = require("./prRecipeReviewFixtures");

function fakeVscode() {
  const commandCalls = [];
  const shown = [];
  class EventEmitter {
    constructor() { this.event = () => ({ dispose() {} }); }
    fire() {}
    dispose() {}
  }
  class TreeItem {
    constructor(label, state) { this.label = label; this.collapsibleState = state; }
  }
  class ThemeIcon { constructor(id) { this.id = id; } }
  const uri = (parts) => ({
    ...parts,
    toString() { return `${parts.scheme}:${parts.path}`; },
  });
  const api = {
    EventEmitter,
    ThemeIcon,
    TreeItem,
    TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
    Uri: {
      from: uri,
      joinPath(base, ...parts) {
        return uri({ scheme: base.scheme, path: `${base.path}/${parts.join("/")}` });
      },
    },
    workspace: {
      async openTextDocument(documentUri) { return { uri: documentUri }; },
    },
    window: {
      async showTextDocument(document, options) { shown.push({ document, options }); },
    },
    commands: {
      async executeCommand(...arguments_) { commandCalls.push(arguments_); },
    },
  };
  return { api, commandCalls, shown };
}

test("empty native tree yields to the Welcome View and plan renders identities before consent", () => {
  const { api } = fakeVscode();
  const provider = new PrRecipeReviewTreeProvider(api, new PrRecipeReviewDocuments(api));
  assert.deepEqual(provider.getChildren(), []);
  const selectedPlan = plan();
  provider.setPlan(selectedPlan);
  const sections = provider.getChildren();
  assert.deepEqual(sections.map((section) => section.section), ["identity", "effects"]);
  const identities = provider.getChildren(sections[0]);
  assert.match(identities[0].label, /#2002/);
  assert.match(identities[1].value, new RegExp(`${"a".repeat(40)}.*${"b".repeat(40)}`));
  assert.equal(identities[2].value, selectedPlan.plan_id);
  assert.deepEqual(
    provider.getChildren(sections[1]).map((item) => item.value),
    selectedPlan.effects,
  );
});

test("compact result renders decision, PR scope, findings, files, attention, and next steps", () => {
  const { api } = fakeVscode();
  const provider = new PrRecipeReviewTreeProvider(api, new PrRecipeReviewDocuments(api));
  const selectedReport = report();
  provider.setReport(selectedReport);
  const sections = provider.getChildren();
  assert.deepEqual(sections.map((section) => section.section), SECTION_ORDER);
  const items = sections.map((section) => provider.getTreeItem(section));
  assert.deepEqual(items.map((item) => item.iconPath.id), [
    "checklist", "git-pull-request", "beaker", "files", "warning", "arrow-right",
  ]);
  const decision = provider.getChildren(sections[0]);
  assert.equal(decision[0].value, "attention");
  assert.match(decision.find((item) => item.label === "Machine recipes").value, /1 modified/);
  const scope = provider.getChildren(sections[1]);
  assert.match(scope[0].label, /#2002/);
  assert.match(scope[2].value, /master-ceu/);
  const groups = provider.getChildren(sections[2]);
  assert.equal(groups.length, 5);
  const modified = provider.getChildren(groups[0]);
  assert.equal(provider.getTreeItem(modified[0]).contextValue, "workbenchPrReviewModifiedRecipe");
  const files = provider.getChildren(sections[3]);
  const modifiedFiles = provider.getChildren(files[0]);
  assert.equal(provider.getTreeItem(modifiedFiles[0]).contextValue, "workbenchPrReviewFile");
  const attentionGroups = provider.getChildren(sections[4]);
  assert.deepEqual(attentionGroups.map((item) => item.label), [
    "PR-introduced and supplied-runtime attention",
    "Pre-existing and candidate-wide attention",
    "Evidence limits",
  ]);
  const prAttention = provider.getChildren(attentionGroups[0]);
  assert.equal(prAttention.find((item) => item.label === "PR-introduced static signals").value, 1);
  const candidateAttention = provider.getChildren(attentionGroups[1]);
  assert.equal(candidateAttention.find((item) => item.label === "Pre-existing static signals").value, 2);
  assert.ok(candidateAttention.some((item) => item.label === "Source configuration warning"));
  assert.ok(provider.getChildren(attentionGroups[2]).some((item) => item.label === "Evidence limit"));
});

test("complete report and modified-property projections use retained native documents", async () => {
  const { api, commandCalls, shown } = fakeVscode();
  const documents = new PrRecipeReviewDocuments(api);
  const provider = new PrRecipeReviewTreeProvider(api, documents);
  const selectedReport = report();
  provider.setReport(selectedReport);
  await provider.openReport();
  assert.equal(shown.length, 1);
  assert.deepEqual(JSON.parse(documents.provideTextDocumentContent(shown[0].document.uri)), selectedReport);

  const recipeSection = provider.getChildren().find((entry) => entry.section === "recipes");
  const modifiedGroup = provider.getChildren(recipeSection)[0];
  const modified = provider.getChildren(modifiedGroup)[0];
  await provider.openFindingDiff(modified);
  assert.equal(commandCalls[0][0], "vscode.diff");
  const before = JSON.parse(documents.provideTextDocumentContent(commandCalls[0][1]));
  const after = JSON.parse(documents.provideTextDocumentContent(commandCalls[0][2]));
  assert.deepEqual(before.properties.duration, ["20"]);
  assert.deepEqual(after.properties.duration, ["40"]);
  assert.match(before.projection_boundary, /bounded changed properties/);
});

test("file action opens only the safe current-workspace path and never fabricates PR bytes", async () => {
  const { api, commandCalls } = fakeVscode();
  const provider = new PrRecipeReviewTreeProvider(api, new PrRecipeReviewDocuments(api));
  provider.setReport(report());
  const filesSection = provider.getChildren().find((entry) => entry.section === "files");
  const modifiedGroup = provider.getChildren(filesSection)[0];
  const file = provider.getChildren(modifiedGroup)[0];
  await provider.openWorkspaceFile(file, {
    scheme: "file", path: "/work/Supersymmetry", toString() { return "file:/work/Supersymmetry"; },
  });
  assert.equal(commandCalls[0][0], "vscode.open");
  assert.equal(commandCalls[0][1].path, "/work/Supersymmetry/groovy/recipes/mixer.groovy");
  assert.match(provider.getTreeItem(file).tooltip, /current workspace file/i);
  await assert.rejects(() => provider.openWorkspaceFile(file, { scheme: "untitled", path: "/tmp" }),
    /local workspace file/);
});

test("failure is visible without assigning approval semantics", () => {
  const { api } = fakeVscode();
  const provider = new PrRecipeReviewTreeProvider(api, new PrRecipeReviewDocuments(api));
  provider.setFailure("Git is unavailable; run workbench setup.");
  const failure = provider.getTreeItem(provider.getChildren()[0]);
  assert.equal(failure.iconPath.id, "error");
  assert.equal(failure.contextValue, "workbenchPrReviewFailure");
  assert.match(failure.tooltip, /Setup/);
  assert.doesNotMatch(failure.tooltip, /approved|rejected/i);
});
