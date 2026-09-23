"use strict";

// Home V2 embeds this identity-bearing base record, so its parser remains
// version-exact even though it is no longer an independently invokable UI.
const HOME_FORMAT = "workbench-workspace-home-v1";
const MAX_ACTIONS = 5;
const MAX_ITEMS = 4096;
const MAX_TEXT_BYTES = 256 * 1024;

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, required, optional, label) {
  const actual = new Set(Object.keys(value));
  const allowed = new Set([...required, ...optional]);
  const missing = required.filter((key) => !actual.has(key));
  const extra = [...actual].filter((key) => !allowed.has(key));
  if (missing.length || extra.length) {
    throw new Error(`${label} fields changed; missing=${missing.join(",")}; extra=${extra.join(",")}`);
  }
}

function text(value, label, { nullable = false, allowEmpty = false } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || (!allowEmpty && !value) || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > MAX_TEXT_BYTES) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function boolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
  return value;
}

function count(value, label) {
  if (!Number.isSafeInteger(value) || value < 0 || value > Number.MAX_SAFE_INTEGER) {
    throw new Error(`${label} is outside its supported bound`);
  }
  return value;
}

function stringArray(value, label, maximum = MAX_ITEMS) {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new Error(`${label} is outside its supported bound`);
  }
  return Object.freeze(value.map((item, index) => text(item, `${label}[${index}]`)));
}

function validateAction(value, index) {
  const action = object(value, `workspace Home action ${index}`);
  exactKeys(action, [
    "id", "title", "purpose", "available", "argv", "blockers", "unavailable_reason",
  ], [], `workspace Home action ${index}`);
  const available = boolean(action.available, `workspace Home action ${index} availability`);
  const blockers = stringArray(action.blockers, `workspace Home action ${index} blockers`, 256);
  let argv = null;
  let unavailableReason = null;
  if (available) {
    if (!Array.isArray(action.argv) || action.argv.length < 1 || action.argv.length > 256) {
      throw new Error(`available workspace Home action ${index} lacks exact argv`);
    }
    argv = Object.freeze(action.argv.map((item, itemIndex) => text(
      item,
      `workspace Home action ${index} argv[${itemIndex}]`,
    )));
    if (argv[0] !== "workbench" || blockers.length || action.unavailable_reason !== null) {
      throw new Error(`available workspace Home action ${index} is internally inconsistent`);
    }
  } else {
    if (action.argv !== null || blockers.length < 1) {
      throw new Error(`blocked workspace Home action ${index} must carry owner blockers`);
    }
    unavailableReason = text(
      action.unavailable_reason,
      `workspace Home action ${index} unavailable reason`,
    );
  }
  return Object.freeze({
    id: text(action.id, `workspace Home action ${index} ID`),
    title: text(action.title, `workspace Home action ${index} title`),
    purpose: text(action.purpose, `workspace Home action ${index} purpose`),
    available,
    argv,
    blockers,
    unavailable_reason: unavailableReason,
  });
}

function validateProblem(value, index) {
  const problem = object(value, `workspace Home problem ${index}`);
  exactKeys(problem, ["id", "severity", "title", "detail", "repair"], [], `workspace Home problem ${index}`);
  if (!["blocker", "warning", "info"].includes(problem.severity)) {
    throw new Error(`workspace Home problem ${index} severity is invalid`);
  }
  const repair = object(problem.repair, `workspace Home problem ${index} repair`);
  exactKeys(repair, ["action", "command"], ["mutates"], `workspace Home problem ${index} repair`);
  const parsedRepair = {
    action: text(repair.action, `workspace Home problem ${index} repair action`),
    command: text(repair.command, `workspace Home problem ${index} repair command`, { nullable: true }),
  };
  if (Object.hasOwn(repair, "mutates")) {
    parsedRepair.mutates = boolean(repair.mutates, `workspace Home problem ${index} repair mutation flag`);
  }
  return Object.freeze({
    id: text(problem.id, `workspace Home problem ${index} ID`),
    severity: problem.severity,
    title: text(problem.title, `workspace Home problem ${index} title`),
    detail: text(problem.detail, `workspace Home problem ${index} detail`),
    repair: Object.freeze(parsedRepair),
  });
}

function validateWorkspaceHome(value) {
  const home = object(value, "workspace Home");
  exactKeys(home, [
    "format", "schema_version", "read_only", "workspace", "status", "repository",
    "context", "problems", "actions", "gaps", "owner_records", "limitations",
  ], [], "workspace Home");
  if (home.format !== HOME_FORMAT || home.schema_version !== 1 || home.read_only !== true) {
    throw new Error("workspace Home identity changed");
  }

  const workspace = object(home.workspace, "workspace Home identity");
  exactKeys(workspace, [
    "requested_path", "root", "display_name", "kind", "recognition",
  ], [], "workspace Home identity");
  if (!["exact", "bounded"].includes(workspace.recognition)) {
    throw new Error("workspace Home recognition is invalid");
  }
  const parsedWorkspace = Object.freeze({
    requested_path: text(workspace.requested_path, "workspace Home requested path"),
    root: text(workspace.root, "workspace Home root"),
    display_name: text(workspace.display_name, "workspace Home display name"),
    kind: text(workspace.kind, "workspace Home kind"),
    recognition: workspace.recognition,
  });

  const status = object(home.status, "workspace Home status");
  exactKeys(status, ["status", "blockers", "warnings", "information"], [], "workspace Home status");
  if (!["ready", "attention", "blocked"].includes(status.status)) {
    throw new Error("workspace Home status is invalid");
  }
  const parsedStatus = Object.freeze({
    status: status.status,
    blockers: count(status.blockers, "workspace Home blocker count"),
    warnings: count(status.warnings, "workspace Home warning count"),
    information: count(status.information, "workspace Home information count"),
  });

  if (!Array.isArray(home.actions) || home.actions.length < 1 || home.actions.length > MAX_ACTIONS) {
    throw new Error("workspace Home must return one to five ordered actions");
  }
  const actions = home.actions.map(validateAction);
  if (new Set(actions.map((action) => action.id)).size !== actions.length) {
    throw new Error("workspace Home action IDs are duplicated");
  }
  if (!Array.isArray(home.problems) || home.problems.length > MAX_ITEMS) {
    throw new Error("workspace Home problems are outside the supported bound");
  }
  const problems = home.problems.map(validateProblem);

  if (!Array.isArray(home.gaps) || home.gaps.length > MAX_ITEMS) {
    throw new Error("workspace Home gaps are outside the supported bound");
  }
  const gaps = home.gaps.map((value_, index) => {
    const gap = object(value_, `workspace Home gap ${index}`);
    exactKeys(gap, ["id", "summary"], [], `workspace Home gap ${index}`);
    return Object.freeze({
      id: text(gap.id, `workspace Home gap ${index} ID`),
      summary: text(gap.summary, `workspace Home gap ${index} summary`),
    });
  });

  object(home.repository, "workspace Home repository");
  object(home.context, "workspace Home context");
  object(home.owner_records, "workspace Home owner records");
  const limitations = stringArray(home.limitations, "workspace Home limitations");
  return Object.freeze({
    format: HOME_FORMAT,
    schema_version: 1,
    read_only: true,
    workspace: parsedWorkspace,
    status: parsedStatus,
    repository: home.repository,
    context: home.context,
    problems: Object.freeze(problems),
    actions: Object.freeze(actions),
    gaps: Object.freeze(gaps),
    owner_records: home.owner_records,
    limitations,
  });
}

module.exports = {
  HOME_FORMAT,
  validateWorkspaceHome,
};
