"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const test = require("node:test");

const {
  FeatureRecordTreeProvider,
  FeatureVirtualDocuments,
  bindTransactionReview,
  exactUtf8,
  retainedFileUri,
} = require("../featureRecordTree");

function fakeVscode() {
  const calls = [];
  const files = new Map();
  class EventEmitter {
    constructor() {
      this.event = (listener) => {
        this.listener = listener;
        return { dispose() {} };
      };
    }
    fire(value) { if (this.listener) this.listener(value); }
    dispose() {}
  }
  class TreeItem {
    constructor(label, collapsibleState) {
      this.label = label;
      this.collapsibleState = collapsibleState;
    }
  }
  class ThemeIcon { constructor(id) { this.id = id; } }
  const api = {
    EventEmitter,
    ThemeIcon,
    TreeItem,
    TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
    Uri: {
      from(parts) {
        return {
          ...parts,
          toString() { return `${parts.scheme}:${parts.path}`; },
        };
      },
      parse(value) {
        return {
          scheme: value.slice(0, value.indexOf(":")),
          toString() { return value; },
        };
      },
      file(value) {
        return {
          scheme: "file",
          fsPath: value,
          toString() { return `file:${value}`; },
        };
      },
    },
    FileType: { File: 1 },
    commands: {
      async executeCommand(...arguments_) {
        calls.push(arguments_);
      },
    },
    workspace: {
      async openTextDocument(uri) { return { uri }; },
      fs: {
        async stat(uri) {
          const bytes = files.get(uri.toString());
          if (!bytes) throw new Error("fixture file is missing");
          return { type: 1, size: bytes.byteLength };
        },
        async readFile(uri) {
          const bytes = files.get(uri.toString());
          if (!bytes) throw new Error("fixture file is missing");
          return bytes;
        },
      },
    },
    window: {
      async showTextDocument(document, options) { calls.push(["show", document, options]); },
    },
  };
  return { api, calls, files };
}

function fixture() {
  const before = Buffer.from("before\n", "utf8");
  const after = Buffer.from("after\n", "utf8");
  const recordId = `workbench-developer-material-fluid-recipe-plan:sha256:${"1".repeat(64)}`;
  const record = {
    collection: "plans",
    diagnostic_code: null,
    family: "material-fluid-recipe",
    operation_count: 1,
    plan_id: recordId,
    record_id: recordId,
    record_kind: "workbench-developer-material-fluid-recipe-plan",
    record_state: "experimental-ready",
    reference: recordId,
    uri: "file:///state/record.json",
    verification_state: "ready",
    workspace_uri: "file:///workspace",
  };
  const compact = {
    after_sha256: crypto.createHash("sha256").update(after).digest("hex"),
    after_size: after.byteLength,
    before_sha256: crypto.createHash("sha256").update(before).digest("hex"),
    before_size: before.byteLength,
    diff: "-before\n+after\n",
    ordinal: 0,
    path: "groovy/Probe.groovy",
    role: "recipe-script",
  };
  return {
    catalog: {
      limitations: ["Invalid owner records are omitted."],
      records: [record],
    },
    presentation: {
      actions: [
        { action: "check", available: true, consent_id: null, reason: null },
        { action: "recover", available: false, consent_id: null, reason: "no interrupted transaction" },
      ],
      authority_boundary: { source: "profile" },
      collection: "plans",
      family: "material-fluid-recipe",
      id: `workbench-developer-feature-presentation:sha256:${"2".repeat(64)}`,
      limitations: ["Runtime evidence is separate."],
      operations: [{
        after_base64: after.toString("base64"),
        after_sha256: crypto.createHash("sha256").update(after).digest("hex"),
        after_size: after.byteLength,
        before_base64: before.toString("base64"),
        before_sha256: crypto.createHash("sha256").update(before).digest("hex"),
        before_size: before.byteLength,
        diff: "-before\n+after\n",
        operation: "update",
        ordinal: 0,
        outcome: "replace-exact-file",
        path: "groovy/Probe.groovy",
        role: "recipe-script",
      }],
      owner_record: {
        diagnostic_code: null,
        id: recordId,
        kind: record.record_kind,
        state: "experimental-ready",
        uri: record.uri,
      },
      plan_id: recordId,
      request: { name: "Radon" },
      review: { summary: "one source change" },
      runtime: {
        action_available: true,
        outcome: null,
        record_id: null,
        requirement: { mode: "cold-start" },
        state: "available-not-observed",
      },
      schema_version: 1,
      verification: { reason: null, state: "ready" },
      workspace_uri: "file:///workspace",
    },
    record,
    transaction: {
      actions: [
        { action: "check", available: true, consent_id: null, reason: null, record_id: recordId },
        { action: "apply", available: true, consent_id: recordId, reason: null, record_id: recordId },
        {
          action: "rollback",
          available: false,
          consent_id: null,
          reason: "workspace does not match reviewed after bytes",
          record_id: null,
        },
        {
          action: "recover",
          available: false,
          consent_id: null,
          reason: "no interrupted transaction is retained",
          record_id: recordId,
        },
      ],
      current_effective_state: "planned",
      family: "material-fluid-recipe",
      id: `workbench-developer-feature-transaction-view:sha256:${"3".repeat(64)}`,
      limitations: ["Point-in-time view; every owner mutation revalidates under its lock."],
      operations: [compact],
      plan_freshness: {
        format: "verification-v1",
        plan_id: recordId,
        reason: null,
        schema_version: 1,
        state: "ready",
      },
      plan_id: recordId,
      records: [record],
      workspace_match: {
        operations: [{
          actual_sha256: compact.before_sha256,
          actual_size: compact.before_size,
          ordinal: 0,
          path: compact.path,
          reason: null,
          state: "matches-before",
        }],
        reason: null,
        state: "matches-before",
      },
      workspace_uri: "file:///workspace",
    },
  };
}

function runtimeFixture() {
  const value = fixture();
  value.record.collection = "runs";
  value.record.family = "recipe-change";
  value.presentation.collection = "runs";
  value.presentation.family = "recipe-change";
  value.presentation.schema_version = 2;
  value.presentation.runtime = {
    action_available: true,
    outcome: "failed",
    record_id: value.presentation.owner_record.id,
    requirement: null,
    sides: [
      {
        assertion: { id: "assessment-baseline", state: "incomplete" },
        error: {
          kind: "MarkerError",
          message: "required marker was not retained",
          phase: "assertion",
        },
        outcome: "failed",
        probe: {
          id: "probe-baseline",
          overlay_id: "overlay-baseline",
          overlay_uri: "file:///state/probes/baseline/overlay.json",
          script_uri: "file:///state/probes/baseline/Probe.groovy",
        },
        receipt: {
          final_launch: {
            id: "receipt-baseline",
            sha256: "a".repeat(64),
            size: 42,
            uri: "file:///state/runtime/baseline/final-launch.json",
          },
          groovy_log: null,
          runtime_session: {
            id: "session-baseline",
            sha256: "b".repeat(64),
            size: 84,
            uri: "file:///state/runtime/baseline/session.json",
          },
        },
        role: "baseline",
        state: "incomplete",
      },
      {
        assertion: null,
        error: { kind: "PriorSideFailed", message: "not run", phase: "blocked" },
        outcome: "not-run",
        probe: null,
        receipt: null,
        role: "candidate",
        state: "incomplete",
      },
    ],
    state: "incomplete",
  };
  return value;
}

test("the native tree stays inert until explicit discovery and exposes owner sections", async () => {
  const { api } = fakeVscode();
  const value = fixture();
  let catalogLoads = 0;
  let presentationLoads = 0;
  let transactionLoads = 0;
  const documents = new FeatureVirtualDocuments(api);
  const provider = new FeatureRecordTreeProvider(api, {
    documents,
    async loadCatalog() { catalogLoads += 1; return value.catalog; },
    async loadPresentation() { presentationLoads += 1; return value.presentation; },
    async loadTransaction() { transactionLoads += 1; return value.transaction; },
  });
  const initial = await provider.getChildren();
  assert.equal(initial[0].type, "load");
  assert.equal(catalogLoads, 0);

  await provider.refresh();
  const roots = await provider.getChildren();
  assert.deepEqual(roots.map((item) => item.type), ["family", "catalog-limitations"]);
  const collections = await provider.getChildren(roots[0]);
  const records = await provider.getChildren(collections[0]);
  const sections = await provider.getChildren(records[0]);
  assert.deepEqual(sections.map((item) => item.name), [
    "current", "lineage", "actions", "operations", "limitations", "owner",
  ]);
  assert.equal(presentationLoads, 1);
  assert.equal(transactionLoads, 1);
  assert.equal((await provider.getChildren(records[0])).length, 6);
  assert.equal(presentationLoads, 1, "validated presentation should be cached per discovery snapshot");
  assert.equal(transactionLoads, 1, "current transaction should be cached per discovery snapshot");

  const actions = await provider.getChildren(sections.find((item) => item.name === "actions"));
  assert.equal(provider.getTreeItem(actions[0]).description, "CURRENTLY available");
  assert.equal(provider.getTreeItem(actions[1]).description, "CURRENTLY available");
  assert.match(provider.getTreeItem(actions[2]).description, /reviewed after bytes/);
  assert.match(provider.getTreeItem(records[0]).description, /^CURRENT planned/);
  const current = sections.find((item) => item.name === "current");
  assert.equal(provider.getTreeItem(current).description, "planned · matches-before · ready");
  const currentFields = await provider.getChildren(current);
  assert.deepEqual(currentFields.slice(0, 3).map((item) => item.value), [
    "planned", "matches-before", "ready",
  ]);
  const owner = await provider.getChildren(sections.find((item) => item.name === "owner"));
  assert.deepEqual(owner.map((item) => item.type), [
    "owner-presentation", "section", "section", "section", "section",
  ]);
});

test("V2 runtime sides render exact diagnostics, absent evidence, and retained file links", async () => {
  const { api, calls, files } = fakeVscode();
  const value = runtimeFixture();
  const finalBytes = Buffer.from('{"state":"complete"}\n', "utf8");
  const finalReference = value.presentation.runtime.sides[0].receipt.final_launch;
  finalReference.sha256 = crypto.createHash("sha256").update(finalBytes).digest("hex");
  finalReference.size = finalBytes.byteLength;
  files.set(finalReference.uri, finalBytes);
  const documents = new FeatureVirtualDocuments(api);
  const provider = new FeatureRecordTreeProvider(api, {
    documents,
    async loadCatalog() { return value.catalog; },
    async loadPresentation() { return value.presentation; },
    resolveEvidenceUri(uri) {
      return retainedFileUri(api, uri, { host: "native" });
    },
  });
  await provider.refresh();
  const family = (await provider.getChildren())[0];
  const collection = (await provider.getChildren(family))[0];
  const record = (await provider.getChildren(collection))[0];
  const runtime = (await provider.getChildren(record)).find((item) => item.name === "runtime");
  assert.equal(provider.getTreeItem(runtime).description, "2 sides");

  const runtimeChildren = await provider.getChildren(runtime);
  const sides = runtimeChildren.filter((item) => item.type === "runtime-side");
  assert.deepEqual(sides.map((side) => provider.getTreeItem(side).label), ["Baseline", "Candidate"]);
  assert.equal(provider.getTreeItem(sides[0]).description, "incomplete · failed");

  const baseline = await provider.getChildren(sides[0]);
  const diagnostic = baseline.find((item) => item.type === "runtime-error");
  assert.equal(provider.getTreeItem(diagnostic).description, "assertion · MarkerError");
  assert.match(provider.getTreeItem(diagnostic).tooltip, /required marker was not retained/);
  const probe = baseline.find((item) => item.type === "runtime-probe");
  const probeFiles = (await provider.getChildren(probe))
    .filter((item) => item.type === "projected-uri");
  assert.equal(probeFiles.length, 2);
  assert.equal(provider.getTreeItem(probeFiles[0]).command, undefined);
  assert.match(provider.getTreeItem(probeFiles[0]).tooltip, /does not provide byte custody/);

  const receipt = baseline.find((item) => item.type === "runtime-receipt");
  const retained = await provider.getChildren(receipt);
  assert.deepEqual(retained.map((item) => item.label), [
    "Final launch receipt", "Runtime session receipt", "Groovy log",
  ]);
  const finalLaunch = provider.getTreeItem(retained[0]);
  assert.equal(finalLaunch.command.command, "workbench.feature.records.openEvidence");
  assert.equal(finalLaunch.command.arguments[0], retained[0]);
  assert.match(finalLaunch.tooltip, new RegExp(`SHA-256: ${finalReference.sha256}`));
  const opened = await provider.openEvidence(retained[0]);
  assert.equal(documents.provideTextDocumentContent(opened), '{"state":"complete"}\n');
  assert.equal(calls.at(-1)[0], "show");

  files.set(finalReference.uri, Buffer.from('{"state":"tampered"}\n', "utf8"));
  await assert.rejects(
    () => provider.openEvidence(retained[0]),
    /sealed file size|sealed size and digest/,
  );
  assert.equal(provider.getTreeItem(retained[2]).description, "none retained");

  const candidate = await provider.getChildren(sides[1]);
  const candidateReceipt = candidate.find((item) => item.type === "runtime-receipt");
  assert.equal(provider.getTreeItem(candidateReceipt).description, "none retained");
  const candidateProbe = candidate.find((item) => item.type === "runtime-probe");
  assert.equal(provider.getTreeItem(candidateProbe).description, "none retained");
});

test("retained evidence URI mapping is exact for native and Windows-to-WSL hosts", () => {
  const { api } = fakeVscode();
  const uri = "file:///home/dev/state/runtime%20evidence/session.json";
  assert.equal(retainedFileUri(api, uri, { host: "native" }).toString(), uri);
  const mapped = retainedFileUri(api, uri, {
    distribution: "Ubuntu-24.04",
    host: "windows-wsl",
  });
  assert.equal(
    mapped.fsPath,
    "\\\\wsl.localhost\\Ubuntu-24.04\\home\\dev\\state\\runtime evidence\\session.json",
  );
  assert.throws(
    () => retainedFileUri(api, "file://other/home/dev/session.json", {
      distribution: "Ubuntu", host: "windows-wsl",
    }),
    /cannot be mapped/,
  );
  assert.throws(
    () => retainedFileUri(api, "file:///home/dev%2Fother/session.json", {
      distribution: "Ubuntu", host: "windows-wsl",
    }),
    /cannot be mapped/,
  );
});

test("operation nodes open exact UTF-8 before and after bytes through vscode.diff", async () => {
  const { api, calls } = fakeVscode();
  const value = fixture();
  const documents = new FeatureVirtualDocuments(api);
  const provider = new FeatureRecordTreeProvider(api, {
    documents,
    async loadCatalog() { return value.catalog; },
    async loadPresentation() { return value.presentation; },
    async loadTransaction() { return value.transaction; },
  });
  await provider.refresh();
  const family = (await provider.getChildren())[0];
  const collection = (await provider.getChildren(family))[0];
  const record = (await provider.getChildren(collection))[0];
  const operationsSection = (await provider.getChildren(record)).find((item) => item.name === "operations");
  const operation = (await provider.getChildren(operationsSection))[0];
  assert.equal(operation.type, "transaction-operation");
  assert.equal(provider.getTreeItem(operation).description, "recipe-script · matches-before");
  const treeItem = provider.getTreeItem(operation);
  assert.equal(treeItem.command.command, "workbench.feature.records.openDiff");

  await provider.openDiff(operation);
  assert.equal(calls[0][0], "vscode.diff");
  assert.equal(documents.provideTextDocumentContent(calls[0][1]), "before\n");
  assert.equal(documents.provideTextDocumentContent(calls[0][2]), "after\n");
});

test("selecting and refreshing a plan re-queries its point-in-time transaction", async () => {
  const { api } = fakeVscode();
  const value = fixture();
  let transactionLoads = 0;
  const documents = new FeatureVirtualDocuments(api);
  const provider = new FeatureRecordTreeProvider(api, {
    documents,
    async loadCatalog() { return value.catalog; },
    async loadPresentation() { return value.presentation; },
    async loadTransaction() { transactionLoads += 1; return value.transaction; },
  });
  await provider.refresh();
  const family = (await provider.getChildren())[0];
  const collection = (await provider.getChildren(family))[0];
  const record = (await provider.getChildren(collection))[0];

  const opened = await provider.openRecord(record);
  assert.equal(opened.current_effective_state, "planned");
  assert.equal(transactionLoads, 1);
  assert.match(provider.getTreeItem(record).description, /^CURRENT planned/);
  await provider.openRecord(record);
  assert.equal(transactionLoads, 2, "selecting the plan must refresh a point-in-time transaction");

  await provider.refresh();
  const refreshedFamily = (await provider.getChildren())[0];
  const refreshedCollection = (await provider.getChildren(refreshedFamily))[0];
  const refreshedRecord = (await provider.getChildren(refreshedCollection))[0];
  await provider.getChildren(refreshedRecord);
  assert.equal(transactionLoads, 3, "view refresh must invalidate the cached current transaction");
});

test("compact changes cannot escape their exact owner presentation custody", () => {
  const value = fixture();
  assert.equal(bindTransactionReview(value.transaction, value.presentation).operations.length, 1);
  const changed = structuredClone(value.transaction);
  changed.operations[0].diff = "+invented\n";
  assert.throws(
    () => bindTransactionReview(changed, value.presentation),
    /differs from its owner plan presentation/,
  );
  const foreign = structuredClone(value.presentation);
  foreign.plan_id = `workbench-developer-material-fluid-recipe-plan:sha256:${"f".repeat(64)}`;
  assert.throws(
    () => bindTransactionReview(value.transaction, foreign),
    /does not bind to its exact owner plan presentation/,
  );
});

test("the text diff bridge rejects bytes VS Code cannot represent exactly", () => {
  assert.equal(exactUtf8(Buffer.from("exact\n").toString("base64"), "fixture"), "exact\n");
  assert.throws(() => exactUtf8(Buffer.from([0xff]).toString("base64"), "fixture"), /not UTF-8/);
});
