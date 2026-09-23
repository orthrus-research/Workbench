"use strict";

const crypto = require("node:crypto");
const { TextDecoder } = require("node:util");

const MAX_RETAINED_EVIDENCE_BYTES = 32 * 1024 * 1024;

const FAMILY_TITLES = Object.freeze({
  "material-fluid-recipe": "Material, Fluid, and Recipe",
  "recipe-change": "Recipe Change",
  "quest-for-process": "Quest for Process",
});
const COLLECTION_TITLES = Object.freeze({
  plans: "Plans",
  receipts: "Application Receipts",
  rollbacks: "Rollback Receipts",
  recoveries: "Recovery Receipts",
  runs: "Runtime Runs",
});

function recordKey(record) {
  return `${record.family}\0${record.collection}\0${record.record_id}`;
}

function recordNodeId(record) {
  return `record:${record.family}:${record.collection}:${record.record_id}`;
}

function shortId(value) {
  return value.slice(value.lastIndexOf(":") + 1, value.lastIndexOf(":") + 13);
}

function runtimeRoleTitle(role) {
  if (role === "baseline") return "Baseline";
  if (role === "candidate") return "Candidate";
  return role;
}

function exactUtf8Bytes(input, label) {
  const bytes = Buffer.from(input);
  let value;
  try {
    value = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch (error) {
    throw new Error(`${label} is not UTF-8 text and cannot be represented by VS Code's text editor`, {
      cause: error,
    });
  }
  if (!Buffer.from(value, "utf8").equals(bytes)) {
    throw new Error(`${label} cannot be represented byte-for-byte by VS Code's text editor`);
  }
  return value;
}

function exactUtf8(base64, label) {
  return exactUtf8Bytes(Buffer.from(base64, "base64"), label);
}

function retainedFileUri(vscode, value, launch, label = "retained evidence URI") {
  if (typeof value !== "string" || !value || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > 128 * 1024) {
    throw new Error(`${label} is invalid`);
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch (error) {
    throw new Error(`${label} is invalid`, { cause: error });
  }
  if (parsed.protocol !== "file:" || parsed.username || parsed.password || parsed.port
      || parsed.search || parsed.hash) {
    throw new Error(`${label} is not one exact file URI`);
  }
  if (launch.host !== "windows-wsl") return vscode.Uri.parse(value, true);
  if (parsed.hostname || !/^[A-Za-z0-9._-]+$/.test(launch.distribution || "")
      || /%(?:2f|5c)/i.test(parsed.pathname)) {
    throw new Error(`${label} cannot be mapped through the configured WSL core`);
  }
  let linuxPath;
  try {
    linuxPath = decodeURIComponent(parsed.pathname);
  } catch (error) {
    throw new Error(`${label} has invalid path encoding`, { cause: error });
  }
  const components = linuxPath.split("/");
  if (!linuxPath.startsWith("/") || linuxPath === "/" || linuxPath.endsWith("/")
      || linuxPath.includes("\\") || linuxPath.includes("\0")
      || components.slice(1).some((part) => !part || part === "." || part === "..")) {
    throw new Error(`${label} is not one normalized absolute Linux file path`);
  }
  const unc = `\\\\wsl.localhost\\${launch.distribution}${linuxPath.replaceAll("/", "\\")}`;
  return vscode.Uri.file(unc);
}

class FeatureVirtualDocuments {
  constructor(vscode) {
    this.vscode = vscode;
    this.contents = new Map();
  }

  clear() {
    this.contents.clear();
  }

  release(uri) {
    if (uri?.scheme === "workbench-feature") this.contents.delete(uri.toString());
  }

  provideTextDocumentContent(uri) {
    const value = this.contents.get(uri.toString());
    if (value === undefined) throw new Error("Workbench virtual document is no longer retained");
    return value;
  }

  store(path, value) {
    if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > 48 * 1024 * 1024) {
      throw new Error("Workbench virtual document exceeds its supported boundary");
    }
    const uri = this.vscode.Uri.from({ scheme: "workbench-feature", path });
    if (!this.contents.has(uri.toString()) && this.contents.size >= 32 * 1024) {
      throw new Error("Workbench virtual document registry exceeds its supported boundary");
    }
    this.contents.set(uri.toString(), value);
    return uri;
  }

  presentation(presentation) {
    const digest = presentation.id.slice(presentation.id.lastIndexOf(":") + 1);
    return this.store(`/records/${digest}/presentation.json`, `${JSON.stringify(presentation, null, 2)}\n`);
  }

  transaction(transaction) {
    const digest = transaction.id.slice(transaction.id.lastIndexOf(":") + 1);
    return this.store(`/transactions/${digest}/current-state.json`, `${JSON.stringify(transaction, null, 2)}\n`);
  }

  evidence(reference, label, bytes) {
    const value = exactUtf8Bytes(bytes, label);
    const name = label.toLowerCase().replace(/[^a-z0-9.-]+/g, "-").replace(/^-|-$/g, "")
      || "evidence";
    return this.store(`/evidence/${reference.sha256}/${name}`, value);
  }

  operation(presentation, operation, side) {
    if (!['before', 'after'].includes(side)) throw new Error("Workbench diff side is invalid");
    const digest = presentation.id.slice(presentation.id.lastIndexOf(":") + 1);
    const bytes = operation[`${side}_base64`];
    const value = exactUtf8(bytes, `${operation.path} ${side} content`);
    return this.store(`/${side}/${digest}/${operation.ordinal}/${operation.path}`, value);
  }
}

function node(type, properties = {}) {
  return Object.freeze({ type, ...properties });
}

function jsonChildren(value, path = "") {
  if (value === null || typeof value !== "object") return [];
  return Object.entries(value).map(([key, child]) => node("json", {
    key,
    path: path ? `${path}.${key}` : key,
    value: child,
  }));
}

function bindTransactionReview(transaction, presentation) {
  if (presentation.collection !== "plans" || presentation.family !== transaction.family
      || presentation.plan_id !== transaction.plan_id
      || presentation.owner_record.id !== transaction.plan_id
      || presentation.workspace_uri !== transaction.workspace_uri
      || presentation.operations.length !== transaction.operations.length) {
    throw new Error("Current transaction does not bind to its exact owner plan presentation");
  }
  const operations = transaction.operations.map((compact, index) => {
    const owner = presentation.operations[index];
    for (const key of [
      "after_sha256", "after_size", "before_sha256", "before_size", "diff",
      "ordinal", "path", "role",
    ]) {
      if (compact[key] !== owner[key]) {
        throw new Error(`Current transaction operation ${index} differs from its owner plan presentation`);
      }
    }
    const workspace = transaction.workspace_match.operations[index];
    return Object.freeze({ compact, owner, workspace });
  });
  return Object.freeze({
    operations: Object.freeze(operations),
    presentation,
    transaction,
  });
}

class FeatureRecordTreeProvider {
  constructor(vscode, services) {
    this.vscode = vscode;
    this.services = services;
    this.catalog = null;
    this.presentations = new Map();
    this.inspections = new Map();
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }

  dispose() {
    this.emitter.dispose();
  }

  reset() {
    this.catalog = null;
    this.presentations.clear();
    this.inspections.clear();
    this.emitter.fire(undefined);
  }

  async refresh() {
    const catalog = await this.services.loadCatalog();
    this.catalog = catalog;
    this.presentations.clear();
    this.inspections.clear();
    this.emitter.fire(undefined);
    return catalog;
  }

  recordNodes() {
    if (!this.catalog) return [];
    return this.catalog.records.map((record) => node("record", {
      id: recordNodeId(record),
      record,
    }));
  }

  async presentationFor(record) {
    const key = recordKey(record);
    let value = this.presentations.get(key);
    if (!value) {
      value = await this.services.loadPresentation(record);
      this.presentations.set(key, value);
    }
    return value;
  }

  async inspectionFor(record, changedElement) {
    if (record.collection !== "plans") {
      throw new Error("Current transaction inspection requires one retained plan");
    }
    const key = recordKey(record);
    let value = this.inspections.get(key);
    if (!value) {
      const transaction = await this.services.loadTransaction(record);
      const presentation = await this.presentationFor(record);
      value = bindTransactionReview(transaction, presentation);
      this.inspections.set(key, value);
      this.emitter.fire(changedElement);
    }
    return value;
  }

  async getChildren(element) {
    if (!element) {
      if (!this.catalog) {
        return [node("load", { id: "load" })];
      }
      const families = [...new Set(this.catalog.records.map((record) => record.family))];
      const result = families.map((family) => node("family", { family, id: `family:${family}` }));
      if (this.catalog.limitations.length) {
        result.push(node("catalog-limitations", { id: "catalog-limitations" }));
      }
      if (!result.length) result.push(node("empty", { id: "empty" }));
      return result;
    }
    if (element.type === "family") {
      const collections = [...new Set(this.catalog.records
        .filter((record) => record.family === element.family)
        .map((record) => record.collection))];
      return collections.map((collection) => node("collection", {
        collection,
        family: element.family,
        id: `collection:${element.family}:${collection}`,
      }));
    }
    if (element.type === "collection") {
      return this.catalog.records
        .filter((record) => record.family === element.family
          && record.collection === element.collection)
        .map((record) => node("record", {
          id: recordNodeId(record),
          record,
        }));
    }
    if (element.type === "catalog-limitations") {
      return this.catalog.limitations.map((value, index) => node("limitation", {
        id: `catalog-limitation:${index}`,
        value,
      }));
    }
    if (element.type === "record") {
      if (element.record.collection === "plans") {
        let inspection;
        try {
          inspection = await this.inspectionFor(element.record, element);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          if (this.services.onError) this.services.onError(message);
          return [node("error", { id: `${element.id}:transaction-error`, message })];
        }
        return [
          node("transaction-section", { id: `${element.id}:current`, name: "current", inspection }),
          node("transaction-section", { id: `${element.id}:lineage`, name: "lineage", inspection }),
          node("transaction-section", { id: `${element.id}:actions`, name: "actions", inspection }),
          node("transaction-section", { id: `${element.id}:operations`, name: "operations", inspection }),
          node("transaction-section", { id: `${element.id}:limitations`, name: "limitations", inspection }),
          node("transaction-section", { id: `${element.id}:owner`, name: "owner", inspection }),
        ];
      }
      let presentation;
      try {
        presentation = await this.presentationFor(element.record);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (this.services.onError) this.services.onError(message);
        return [node("error", { id: `${element.id}:error`, message })];
      }
      return [
        node("section", { id: `${element.id}:overview`, name: "overview", presentation }),
        node("section", { id: `${element.id}:authority`, name: "authority", presentation }),
        node("section", { id: `${element.id}:request`, name: "request", presentation }),
        node("section", { id: `${element.id}:review`, name: "review", presentation }),
        node("section", { id: `${element.id}:runtime`, name: "runtime", presentation }),
        node("section", { id: `${element.id}:limitations`, name: "limitations", presentation }),
        node("section", { id: `${element.id}:actions`, name: "actions", presentation }),
        node("section", { id: `${element.id}:operations`, name: "operations", presentation }),
      ];
    }
    if (element.type === "transaction-section") return this.transactionSectionChildren(element);
    if (element.type === "section") return this.sectionChildren(element);
    if (element.type === "runtime-side") {
      const side = element.side;
      return [
        node("field", { id: `${element.id}:state`, name: "State", value: side.state }),
        node("field", { id: `${element.id}:outcome`, name: "Outcome", value: side.outcome }),
        node("runtime-assertion", { id: `${element.id}:assertion`, value: side.assertion }),
        node("runtime-error", { id: `${element.id}:error`, value: side.error }),
        node("runtime-probe", { id: `${element.id}:probe`, value: side.probe }),
        node("runtime-receipt", { id: `${element.id}:receipt`, value: side.receipt }),
      ];
    }
    if (element.type === "runtime-assertion") {
      if (element.value === null) return [];
      return [
        node("field", { id: `${element.id}:id`, name: "Assessment ID", value: element.value.id }),
        node("field", { id: `${element.id}:state`, name: "Assessment state", value: element.value.state }),
      ];
    }
    if (element.type === "runtime-error") {
      if (element.value === null) return [];
      return [
        node("field", { id: `${element.id}:phase`, name: "Phase", value: element.value.phase }),
        node("field", { id: `${element.id}:kind`, name: "Kind", value: element.value.kind }),
        node("field", { id: `${element.id}:message`, name: "Message", value: element.value.message }),
      ];
    }
    if (element.type === "runtime-probe") {
      if (element.value === null) return [];
      return [
        node("field", { id: `${element.id}:id`, name: "Probe ID", value: element.value.id }),
        node("field", { id: `${element.id}:overlay-id`, name: "Overlay ID", value: element.value.overlay_id }),
        node("projected-uri", {
          id: `${element.id}:overlay`,
          label: "Overlay specification",
          uri: element.value.overlay_uri,
        }),
        node("projected-uri", {
          id: `${element.id}:script`,
          label: "Probe script",
          uri: element.value.script_uri,
        }),
      ];
    }
    if (element.type === "runtime-receipt") {
      if (element.value === null) return [];
      const result = [
        node("retained-link", {
          id: `${element.id}:final-launch`,
          label: "Final launch receipt",
          reference: element.value.final_launch,
          uri: element.value.final_launch.uri,
        }),
        node("retained-link", {
          id: `${element.id}:runtime-session`,
          label: "Runtime session receipt",
          reference: element.value.runtime_session,
          uri: element.value.runtime_session.uri,
        }),
      ];
      if (element.value.groovy_log === null) {
        result.push(node("retained-absent", {
          id: `${element.id}:groovy-log`,
          label: "Groovy log",
        }));
      } else {
        result.push(node("retained-link", {
          id: `${element.id}:groovy-log`,
          label: "Groovy log",
          reference: element.value.groovy_log,
          uri: element.value.groovy_log.uri,
        }));
      }
      return result;
    }
    if (element.type === "json") return jsonChildren(element.value, element.path);
    return [];
  }

  transactionSectionChildren(element) {
    const { presentation, transaction, operations } = element.inspection;
    if (element.name === "current") {
      const details = [
        ["Effective state", transaction.current_effective_state],
        ["Workspace match", transaction.workspace_match.state],
        ["Plan freshness", transaction.plan_freshness.state],
        ["Workspace", transaction.workspace_uri],
        ["Transaction view", transaction.id],
      ];
      if (transaction.workspace_match.reason !== null) {
        details.push(["Workspace reason", transaction.workspace_match.reason]);
      }
      if (transaction.plan_freshness.reason !== null) {
        details.push(["Freshness reason", transaction.plan_freshness.reason]);
      }
      return details.map(([name, value], index) => node("field", {
        id: `${element.id}:field:${index}`,
        name,
        value,
      }));
    }
    if (element.name === "lineage") {
      return transaction.records.map((record) => node("lineage-record", {
        id: `${element.id}:${record.collection}:${record.record_id}`,
        record,
      }));
    }
    if (element.name === "actions") {
      return transaction.actions.map((action) => node("transaction-action", {
        action,
        id: `${element.id}:${action.action}`,
      }));
    }
    if (element.name === "operations") {
      return operations.map((review) => node("transaction-operation", {
        id: `${element.id}:${review.compact.ordinal}`,
        presentation,
        review,
      }));
    }
    if (element.name === "limitations") {
      return transaction.limitations.map((limitation, index) => node("limitation", {
        id: `${element.id}:${index}`,
        value: limitation,
      }));
    }
    if (element.name === "owner") {
      return [
        node("owner-presentation", {
          id: `${element.id}:json`,
          presentation,
        }),
        node("section", { id: `${element.id}:authority`, name: "authority", presentation }),
        node("section", { id: `${element.id}:request`, name: "request", presentation }),
        node("section", { id: `${element.id}:review`, name: "review", presentation }),
        node("section", { id: `${element.id}:runtime`, name: "runtime", presentation }),
      ];
    }
    return [];
  }

  sectionChildren(element) {
    const value = element.presentation;
    if (element.name === "overview") {
      const details = [
        ["Family", value.family],
        ["Collection", value.collection],
        ["Owner state", value.owner_record.state],
        ["Plan freshness", value.verification.state],
        ["Runtime", value.runtime.state],
        ["Workspace", value.workspace_uri],
        ["Owner record", value.owner_record.id],
        ["Plan", value.plan_id],
      ];
      if (value.owner_record.diagnostic_code !== null) {
        details.push(["Diagnostic", value.owner_record.diagnostic_code]);
      }
      if (value.verification.reason !== null) details.push(["Freshness reason", value.verification.reason]);
      return details.map(([name, detail], index) => node("field", {
        id: `${element.id}:field:${index}`,
        name,
        value: detail,
      }));
    }
    if (element.name === "authority") return jsonChildren(value.authority_boundary, "authority_boundary");
    if (element.name === "request") return jsonChildren(value.request, "request");
    if (element.name === "review") return jsonChildren(value.review, "review");
    if (element.name === "runtime") {
      const runtime = value.runtime;
      const result = [
        node("field", {
          id: `${element.id}:action-available`,
          name: "Action available",
          value: runtime.action_available,
        }),
        node("field", { id: `${element.id}:state`, name: "State", value: runtime.state }),
        node("field", {
          id: `${element.id}:outcome`,
          name: "Outcome",
          value: runtime.outcome === null ? "None observed" : runtime.outcome,
        }),
        node("field", {
          id: `${element.id}:record`,
          name: "Runtime record",
          value: runtime.record_id === null ? "None retained" : runtime.record_id,
        }),
      ];
      if (runtime.requirement === null) {
        result.push(node("field", {
          id: `${element.id}:requirement`,
          name: "Requirement",
          value: "None declared",
        }));
      } else {
        result.push(node("json", {
          id: `${element.id}:requirement`,
          key: "Requirement",
          path: "runtime.requirement",
          value: runtime.requirement,
        }));
      }
      if (value.schema_version === 2) {
        runtime.sides.forEach((side, index) => result.push(node("runtime-side", {
          id: `${element.id}:side:${index}`,
          index,
          side,
        })));
      }
      return result;
    }
    if (element.name === "limitations") {
      if (!value.limitations.length) return [node("none", { id: `${element.id}:none` })];
      return value.limitations.map((limitation, index) => node("limitation", {
        id: `${element.id}:limitation:${index}`,
        value: limitation,
      }));
    }
    if (element.name === "actions") {
      if (!value.actions.length) return [node("none", { id: `${element.id}:none` })];
      return value.actions.map((action) => node("action", {
        action,
        id: `${element.id}:action:${action.action}`,
      }));
    }
    if (element.name === "operations") {
      return value.operations.map((operation) => node("operation", {
        id: `${element.id}:operation:${operation.ordinal}`,
        operation,
        presentation: value,
      }));
    }
    return [];
  }

  getTreeItem(element) {
    const api = this.vscode;
    const collapsed = api.TreeItemCollapsibleState.Collapsed;
    const none = api.TreeItemCollapsibleState.None;
    let item;
    if (element.type === "load") {
      item = new api.TreeItem("Load retained records", none);
      item.command = { command: "workbench.feature.records.refresh", title: "Load retained records" };
      item.iconPath = new api.ThemeIcon("cloud-download");
      item.tooltip = "Query the installed Workbench core only after this explicit selection.";
    } else if (element.type === "empty") {
      item = new api.TreeItem("No retained records", none);
      item.description = "owner state is empty";
      item.iconPath = new api.ThemeIcon("info");
    } else if (element.type === "error") {
      item = new api.TreeItem("Record view could not be validated", none);
      item.description = element.message;
      item.tooltip = element.message;
      item.iconPath = new api.ThemeIcon("error");
    } else if (element.type === "family") {
      item = new api.TreeItem(FAMILY_TITLES[element.family] || element.family, collapsed);
      item.description = element.family;
      item.iconPath = new api.ThemeIcon("symbol-class");
    } else if (element.type === "collection") {
      item = new api.TreeItem(COLLECTION_TITLES[element.collection] || element.collection, collapsed);
      const count = this.catalog.records.filter((record) => record.family === element.family
        && record.collection === element.collection).length;
      item.description = `${count}`;
      item.iconPath = new api.ThemeIcon("folder-library");
    } else if (element.type === "catalog-limitations") {
      item = new api.TreeItem("Discovery limitations", collapsed);
      item.description = `${this.catalog.limitations.length}`;
      item.iconPath = new api.ThemeIcon("warning");
    } else if (element.type === "record") {
      item = new api.TreeItem(`${element.record.record_kind} · ${shortId(element.record.record_id)}`, collapsed);
      const inspection = this.inspections.get(recordKey(element.record));
      item.description = element.record.collection === "plans"
        ? inspection
          ? `CURRENT ${inspection.transaction.current_effective_state} · ${inspection.transaction.workspace_match.state}`
          : "select to inspect CURRENT state"
        : `${element.record.record_state} · ${element.record.verification_state}`;
      item.tooltip = [
        `Family: ${element.record.family}`,
        `Collection: ${element.record.collection}`,
        ...(inspection ? [
          `CURRENT effective state: ${inspection.transaction.current_effective_state}`,
          `CURRENT workspace match: ${inspection.transaction.workspace_match.state}`,
          `CURRENT plan freshness: ${inspection.transaction.plan_freshness.state}`,
        ] : [
          `Retained state: ${element.record.record_state}`,
          `Discovery-time plan freshness: ${element.record.verification_state}`,
        ]),
        `Operations: ${element.record.operation_count}`,
        `Workspace: ${element.record.workspace_uri}`,
        `Record: ${element.record.record_id}`,
        `Plan: ${element.record.plan_id}`,
      ].join("\n");
      const effectiveIcon = inspection && {
        applied: "pass-filled",
        restored: "debug-restart",
        planned: "edit",
        interrupted: "error",
        drifted: "warning",
        mixed: "warning",
        unavailable: "circle-slash",
        "matches-after-without-receipt": "warning",
      }[inspection.transaction.current_effective_state];
      item.iconPath = new api.ThemeIcon(
        effectiveIcon || (element.record.record_state === "applied" ? "pass-filled" : "history"),
      );
      item.contextValue = "workbenchFeatureRecord";
      item.command = {
        command: "workbench.feature.records.openRecord",
        title: element.record.collection === "plans"
          ? "Inspect current plan transaction" : "Open validated record presentation",
        arguments: [element],
      };
    } else if (element.type === "transaction-section") {
      const labels = {
        current: "CURRENT transaction state",
        lineage: "Retained lineage",
        actions: "Current owner actions",
        operations: "Reviewed changes",
        limitations: "Current-view limitations",
        owner: "Owner plan presentation",
      };
      const icons = {
        current: "pulse",
        lineage: "type-hierarchy",
        actions: "play-circle",
        operations: "diff-multiple",
        limitations: "warning",
        owner: "shield",
      };
      item = new api.TreeItem(labels[element.name], collapsed);
      item.iconPath = new api.ThemeIcon(icons[element.name]);
      if (element.name === "current") {
        const transaction = element.inspection.transaction;
        item.description = `${transaction.current_effective_state} · ${transaction.workspace_match.state} · ${transaction.plan_freshness.state}`;
        item.tooltip = [
          `CURRENT effective state: ${transaction.current_effective_state}`,
          `Workspace match: ${transaction.workspace_match.state}`,
          `Plan freshness: ${transaction.plan_freshness.state}`,
          "This is the Shell-sealed point-in-time view, separate from immutable record states.",
        ].join("\n");
      }
      if (element.name === "lineage") item.description = `${element.inspection.transaction.records.length} records`;
      if (element.name === "actions") {
        item.description = `${element.inspection.transaction.actions.filter((action) => action.available).length} available`;
      }
      if (element.name === "operations") item.description = `${element.inspection.operations.length}`;
      if (element.name === "limitations") item.description = `${element.inspection.transaction.limitations.length}`;
    } else if (element.type === "section") {
      const labels = {
        overview: "Overview",
        authority: "Authority boundary",
        request: "Request",
        review: "Owner review",
        runtime: "Runtime evidence",
        limitations: "Limitations",
        actions: "Available actions",
        operations: "Transaction operations",
      };
      item = new api.TreeItem(labels[element.name], collapsed);
      item.iconPath = new api.ThemeIcon({
        overview: "info",
        authority: "law",
        request: "list-selection",
        review: "shield",
        runtime: "pulse",
        limitations: "warning",
        actions: "play-circle",
        operations: "diff-multiple",
      }[element.name]);
      if (element.name === "operations") item.description = `${element.presentation.operations.length}`;
      if (element.name === "actions") {
        item.description = `${element.presentation.actions.filter((action) => action.available).length} available`;
      }
      if (element.name === "limitations") item.description = `${element.presentation.limitations.length}`;
      if (element.name === "runtime" && element.presentation.schema_version === 2) {
        item.description = `${element.presentation.runtime.sides.length} sides`;
      }
    } else if (element.type === "field") {
      item = new api.TreeItem(element.name, none);
      item.description = String(element.value);
      item.tooltip = `${element.name}: ${element.value}`;
    } else if (element.type === "limitation") {
      item = new api.TreeItem(element.value, none);
      item.iconPath = new api.ThemeIcon("warning");
      item.tooltip = element.value;
    } else if (element.type === "transaction-action") {
      item = new api.TreeItem(element.action.action, none);
      item.description = element.action.available ? "CURRENTLY available" : element.action.reason;
      item.iconPath = new api.ThemeIcon(element.action.available ? "pass-filled" : "circle-slash");
      item.tooltip = [
        `Current owner action: ${element.action.action}`,
        `Available: ${element.action.available}`,
        ...(element.action.record_id ? [`Owner record: ${element.action.record_id}`] : []),
        ...(element.action.consent_id ? [`Consent ID: ${element.action.consent_id}`] : []),
        ...(element.action.reason ? [`Reason: ${element.action.reason}`] : []),
        "This tree is read-only. Use the catalog-driven Command Center for a fresh owner review and execution.",
      ].join("\n");
    } else if (element.type === "lineage-record") {
      item = new api.TreeItem(
        `${COLLECTION_TITLES[element.record.collection] || element.record.collection} · ${shortId(element.record.record_id)}`,
        none,
      );
      item.description = `${element.record.record_state} · ${element.record.verification_state}`;
      item.iconPath = new api.ThemeIcon(element.record.collection === "plans" ? "edit" : "history");
      item.tooltip = [
        `Kind: ${element.record.record_kind}`,
        `Retained state: ${element.record.record_state}`,
        `Discovery-time freshness: ${element.record.verification_state}`,
        `Record: ${element.record.record_id}`,
        "The lineage is a plan-grouped immutable record set, not wall-clock chronology.",
      ].join("\n");
      item.command = {
        command: "workbench.feature.records.openRecord",
        title: "Open retained lineage record",
        arguments: [element],
      };
    } else if (element.type === "owner-presentation") {
      item = new api.TreeItem("Open exact owner presentation JSON", none);
      item.description = shortId(element.presentation.id);
      item.iconPath = new api.ThemeIcon("json");
      item.tooltip = "Open the owner-validated presentation that retains exact operation Base64 custody.";
      item.command = {
        command: "workbench.feature.records.openRecord",
        title: "Open exact owner presentation",
        arguments: [element],
      };
    } else if (element.type === "action") {
      item = new api.TreeItem(element.action.action, none);
      item.description = element.action.available ? "available" : element.action.reason;
      item.iconPath = new api.ThemeIcon(element.action.available ? "pass-filled" : "circle-slash");
      item.tooltip = [
        `Owner action: ${element.action.action}`,
        `Available: ${element.action.available}`,
        ...(element.action.consent_id ? [`Consent ID: ${element.action.consent_id}`] : []),
        ...(element.action.reason ? [`Reason: ${element.action.reason}`] : []),
        "Use the catalog-driven Command Center for owner-reviewed execution.",
      ].join("\n");
    } else if (element.type === "transaction-operation") {
      const { compact, workspace } = element.review;
      item = new api.TreeItem(compact.path, none);
      item.description = `${compact.role} · ${workspace?.state || "workspace unavailable"}`;
      item.iconPath = new api.ThemeIcon("diff");
      item.tooltip = [
        `Reviewed operation ${compact.ordinal}`,
        `Role: ${compact.role}`,
        `Workspace: ${workspace?.state || "unavailable"}`,
        `Before: ${compact.before_sha256} (${compact.before_size} bytes)`,
        `After: ${compact.after_sha256} (${compact.after_size} bytes)`,
        "The compact current-state view is bound to the owner presentation; selecting opens its exact digest-verified before/after bytes.",
      ].join("\n");
      item.contextValue = "workbenchFeatureOperation";
      item.command = {
        command: "workbench.feature.records.openDiff",
        title: "Open exact reviewed change",
        arguments: [element],
      };
    } else if (element.type === "operation") {
      item = new api.TreeItem(element.operation.path, none);
      item.description = `${element.operation.role} · ${element.operation.outcome}`;
      item.iconPath = new api.ThemeIcon("diff");
      item.tooltip = [
        `Operation ${element.operation.ordinal}: ${element.operation.operation}`,
        `Role: ${element.operation.role}`,
        `Outcome: ${element.operation.outcome}`,
        `Before: ${element.operation.before_sha256} (${element.operation.before_size} bytes)`,
        `After: ${element.operation.after_sha256} (${element.operation.after_size} bytes)`,
        "Select to open the exact owner-validated before/after bytes in VS Code's diff editor.",
      ].join("\n");
      item.contextValue = "workbenchFeatureOperation";
      item.command = {
        command: "workbench.feature.records.openDiff",
        title: "Open exact operation diff",
        arguments: [element],
      };
    } else if (element.type === "runtime-side") {
      item = new api.TreeItem(runtimeRoleTitle(element.side.role), collapsed);
      item.description = `${element.side.state} · ${element.side.outcome}`;
      item.tooltip = [
        `Role: ${element.side.role}`,
        `State: ${element.side.state}`,
        `Outcome: ${element.side.outcome}`,
        "These are the owner-projected runtime fields; the IDE does not infer gameplay meaning.",
      ].join("\n");
      item.iconPath = new api.ThemeIcon(element.side.error === null ? "pulse" : "error");
    } else if (element.type === "runtime-assertion") {
      const expandable = element.value !== null;
      item = new api.TreeItem("Assertion", expandable ? collapsed : none);
      item.description = expandable ? element.value.state : "none retained";
      item.iconPath = new api.ThemeIcon(expandable ? "checklist" : "circle-slash");
      item.tooltip = expandable
        ? `Assessment: ${element.value.id}\nState: ${element.value.state}`
        : "No assertion reference was retained for this runtime side.";
    } else if (element.type === "runtime-error") {
      const expandable = element.value !== null;
      item = new api.TreeItem("Diagnostic", expandable ? collapsed : none);
      item.description = expandable
        ? `${element.value.phase} · ${element.value.kind}`
        : "none retained";
      item.iconPath = new api.ThemeIcon(expandable ? "error" : "circle-slash");
      item.tooltip = expandable
        ? `${element.value.phase}: ${element.value.kind}\n${element.value.message}`
        : "No runtime diagnostic was retained for this side.";
    } else if (element.type === "runtime-probe") {
      const expandable = element.value !== null;
      item = new api.TreeItem("Probe", expandable ? collapsed : none);
      item.description = expandable ? element.value.id : "none retained";
      item.iconPath = new api.ThemeIcon(expandable ? "inspect" : "circle-slash");
      item.tooltip = expandable
        ? `Probe: ${element.value.id}\nOverlay: ${element.value.overlay_id}`
        : "No probe reference was retained for this runtime side.";
    } else if (element.type === "runtime-receipt") {
      const expandable = element.value !== null;
      item = new api.TreeItem("Retained evidence", expandable ? collapsed : none);
      item.description = expandable ? "bound references" : "none retained";
      item.iconPath = new api.ThemeIcon(expandable ? "archive" : "circle-slash");
      item.tooltip = expandable
        ? "Core-projected receipt and log references. Select a child to verify and open its retained bytes."
        : "No receipt custody was retained for this runtime side.";
    } else if (element.type === "retained-link") {
      item = new api.TreeItem(element.label, none);
      item.description = element.reference
        ? `${element.reference.size} bytes · ${element.reference.sha256.slice(0, 12)}`
        : element.uri;
      item.iconPath = new api.ThemeIcon("go-to-file");
      item.tooltip = [
        ...(element.reference?.id ? [`ID: ${element.reference.id}`] : []),
        ...(element.reference ? [
          `SHA-256: ${element.reference.sha256}`,
          `Size: ${element.reference.size} bytes`,
        ] : []),
        `URI: ${element.uri}`,
        "Selecting this reference host-maps its file URI, verifies the sealed size and SHA-256, and opens exact text bytes without interpreting them.",
      ].join("\n");
      item.command = {
        command: "workbench.feature.records.openEvidence",
        title: `Open ${element.label}`,
        arguments: [element],
      };
    } else if (element.type === "projected-uri") {
      item = new api.TreeItem(element.label, none);
      item.description = element.uri;
      item.iconPath = new api.ThemeIcon("file-code");
      item.tooltip = [
        `URI: ${element.uri}`,
        "The V2 projection does not provide byte custody for this probe file, so the IDE will not open it as validated evidence.",
      ].join("\n");
    } else if (element.type === "retained-absent") {
      item = new api.TreeItem(element.label, none);
      item.description = "none retained";
      item.iconPath = new api.ThemeIcon("circle-slash");
    } else if (element.type === "json") {
      const expandable = element.value !== null && typeof element.value === "object";
      item = new api.TreeItem(element.key, expandable ? collapsed : none);
      if (!expandable) item.description = JSON.stringify(element.value);
      item.tooltip = expandable
        ? element.path
        : `${element.path}: ${JSON.stringify(element.value)}`;
    } else {
      item = new api.TreeItem("None declared", none);
      item.iconPath = new api.ThemeIcon("circle-slash");
    }
    item.id = element.id;
    return item;
  }

  async openRecord(element) {
    if (!element || !["record", "lineage-record", "owner-presentation"].includes(element.type)) {
      throw new Error("Select one retained Workbench record");
    }
    if (element.type === "owner-presentation") {
      const uri = this.services.documents.presentation(element.presentation);
      const document = await this.vscode.workspace.openTextDocument(uri);
      await this.vscode.window.showTextDocument(document, { preview: true });
      return element.presentation;
    }
    if (element.record.collection === "plans") {
      const key = recordKey(element.record);
      this.inspections.delete(key);
      const inspection = await this.inspectionFor(element.record, element);
      const uri = this.services.documents.transaction(inspection.transaction);
      const document = await this.vscode.workspace.openTextDocument(uri);
      await this.vscode.window.showTextDocument(document, { preview: true });
      return inspection.transaction;
    }
    const presentation = await this.presentationFor(element.record);
    const uri = this.services.documents.presentation(presentation);
    const document = await this.vscode.workspace.openTextDocument(uri);
    await this.vscode.window.showTextDocument(document, { preview: true });
    return presentation;
  }

  async openDiff(element) {
    if (!element || !["operation", "transaction-operation"].includes(element.type)) {
      throw new Error("Select one Workbench transaction operation");
    }
    const operation = element.type === "transaction-operation" ? element.review.owner : element.operation;
    const before = this.services.documents.operation(element.presentation, operation, "before");
    const after = this.services.documents.operation(element.presentation, operation, "after");
    await this.vscode.commands.executeCommand(
      "vscode.diff",
      before,
      after,
      `${operation.path} — ${FAMILY_TITLES[element.presentation.family] || element.presentation.family}`,
      { preview: true },
    );
  }

  async openEvidence(element) {
    if (!element || element.type !== "retained-link" || !element.reference) {
      throw new Error("Select one retained Workbench evidence reference");
    }
    if (element.uri !== element.reference.uri
        || typeof element.reference.sha256 !== "string"
        || !/^[0-9a-f]{64}$/.test(element.reference.sha256)
        || !Number.isSafeInteger(element.reference.size) || element.reference.size < 0
        || element.reference.size > MAX_RETAINED_EVIDENCE_BYTES) {
      throw new Error("Retained Workbench evidence escaped the supported navigation boundary");
    }
    const source = this.services.resolveEvidenceUri(element.uri);
    const stat = await this.vscode.workspace.fs.stat(source);
    if (!stat || !Number.isSafeInteger(stat.size) || stat.size !== element.reference.size
        || (this.vscode.FileType
          && (stat.type & this.vscode.FileType.File) !== this.vscode.FileType.File)) {
      throw new Error("Retained Workbench evidence no longer matches its sealed file size");
    }
    const bytes = Buffer.from(await this.vscode.workspace.fs.readFile(source));
    const digest = crypto.createHash("sha256").update(bytes).digest("hex");
    if (bytes.byteLength !== element.reference.size || digest !== element.reference.sha256) {
      throw new Error("Retained Workbench evidence bytes do not match their sealed size and digest");
    }
    const uri = this.services.documents.evidence(element.reference, element.label, bytes);
    const document = await this.vscode.workspace.openTextDocument(uri);
    await this.vscode.window.showTextDocument(document, { preview: true });
    return uri;
  }
}

module.exports = {
  FeatureRecordTreeProvider,
  FeatureVirtualDocuments,
  bindTransactionReview,
  exactUtf8,
  recordKey,
  retainedFileUri,
};
