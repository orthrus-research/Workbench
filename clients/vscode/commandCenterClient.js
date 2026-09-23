"use strict";

const {
  MAX_TIMEOUT_MS,
  invokeCoreJson,
  invokeCoreText,
} = require("./coreCommandClient");

const CATALOG_FORMAT = "workbench-live-console-command-catalog-v2";
const REVIEW_FORMAT = "workbench-live-console-command-review-v2";
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const IDENTIFIER = /^[a-z][a-z0-9.-]*$/;
const OPTION_KEY = /^[a-z][a-z0-9_]*$/;
const AVAILABILITIES = new Set(["available", "experimental", "unavailable"]);
const RISKS = new Set(["read-only", "writes-output", "mutating", "destructive"]);
const PREVIEWS = new Set([
  "none", "append-show", "plan-then-apply", "inert-only", "confirm-and-show",
]);
const OWNER_PREVIEWS = new Set(["append-show", "plan-then-apply", "confirm-and-show"]);
const OPTION_KINDS = new Set(["text", "path", "integer", "boolean", "choice", "json"]);
const STRUCTURED_NARGS = new Set(["one", "one_or_more", "zero_or_more", "optional"]);
const MAX_CATALOG_OUTPUT = 8 * 1024 * 1024;
const MAX_REVIEW_OUTPUT = 1024 * 1024;
const MAX_ASSIGNMENT_BYTES = 24 * 1024;

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
    throw new Error(`${label} keys differ; missing=${missing.join(",")}; extra=${extra.join(",")}`);
  }
}

function string(value, label) {
  if (typeof value !== "string" || !value
      || Buffer.byteLength(value, "utf8") > 16 * 1024
      || /[\u0000-\u0008\u000b-\u001f]/.test(value)) {
    throw new Error(`${label} must be bounded nonempty text`);
  }
  return value;
}

function nullableString(value, label) {
  return value === null ? null : string(value, label);
}

function reviewText(value, label) {
  if (typeof value !== "string" || !value
      || Buffer.byteLength(value, "utf8") > 256 * 1024
      || value.includes("\0")) {
    throw new Error(`${label} must be bounded nonempty text`);
  }
  return value;
}

function identifier(value, label, option = false) {
  const selected = string(value, label);
  if (!(option ? OPTION_KEY : IDENTIFIER).test(selected)) {
    throw new Error(`${label} is invalid`);
  }
  return selected;
}

function digest(value, label) {
  const selected = string(value, label);
  if (!DIGEST.test(selected)) {
    throw new Error(`${label} is not a canonical SHA-256 ID`);
  }
  return selected;
}

function boolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
  return value;
}

function nonnegativeInteger(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} must be a nonnegative safe integer`);
  }
  return value;
}

function member(value, choices, label) {
  if (!choices.has(value)) throw new Error(`${label} is unsupported`);
  return value;
}

function validateSuite(value) {
  const suite = object(value, "catalog suite");
  exactKeys(suite, [
    "suite_id", "title", "summary", "authority", "availability", "command_count",
  ], [], "catalog suite");
  return Object.freeze({
    suite_id: identifier(suite.suite_id, "suite ID"),
    title: string(suite.title, "suite title"),
    summary: string(suite.summary, "suite summary"),
    authority: string(suite.authority, "suite authority"),
    availability: member(suite.availability, AVAILABILITIES, "suite availability"),
    command_count: nonnegativeInteger(suite.command_count, "suite command count"),
  });
}

function validateOption(value) {
  const option = object(value, "catalog option");
  exactKeys(option, [
    "key", "label", "help", "flags", "kind", "required", "positional", "choices",
    "nargs", "repeat", "placement", "sensitive", "required_group", "console_managed",
  ], ["default", "mutex_group", "metavar"], "catalog option");
  if (!Array.isArray(option.flags) || !Array.isArray(option.choices)
      || option.flags.length > 16 || option.choices.length > 4096) {
    throw new Error("catalog option flags or choices are outside the supported bound");
  }
  const parsed = {
    key: identifier(option.key, "option key", true),
    label: string(option.label, "option label"),
    help: string(option.help, "option help"),
    flags: Object.freeze(option.flags.map((item) => string(item, "option flag"))),
    kind: member(option.kind, OPTION_KINDS, "option kind"),
    required: boolean(option.required, "option required"),
    positional: boolean(option.positional, "option positional"),
    choices: Object.freeze(option.choices.map((item) => string(item, "option choice"))),
    nargs: typeof option.nargs === "number"
      ? nonnegativeInteger(option.nargs, "option nargs")
      : string(option.nargs, "option nargs"),
    repeat: boolean(option.repeat, "option repeat"),
    placement: nonnegativeInteger(option.placement, "option placement"),
    sensitive: boolean(option.sensitive, "option sensitive"),
    required_group: boolean(option.required_group, "option required group"),
    console_managed: boolean(option.console_managed, "option console managed"),
    ...(Object.hasOwn(option, "default") ? { default: option.default } : {}),
    ...(Object.hasOwn(option, "mutex_group")
      ? { mutex_group: string(option.mutex_group, "option mutex group") } : {}),
    ...(Object.hasOwn(option, "metavar")
      ? { metavar: string(option.metavar, "option metavar") } : {}),
  };
  if (typeof parsed.nargs === "string" && !STRUCTURED_NARGS.has(parsed.nargs)) {
    throw new Error(`catalog option ${parsed.key} has unsupported nargs`);
  }
  if (parsed.positional === (parsed.flags.length > 0)) {
    throw new Error(`catalog option ${parsed.key} must be positional or flagged, but not both`);
  }
  if (parsed.kind === "choice" && parsed.choices.length === 0) {
    throw new Error(`catalog option ${parsed.key} has no choices`);
  }
  if (new Set(parsed.flags).size !== parsed.flags.length
      || new Set(parsed.choices).size !== parsed.choices.length) {
    throw new Error(`catalog option ${parsed.key} contains duplicate metadata`);
  }
  if (parsed.required_group && !parsed.mutex_group) {
    throw new Error(`catalog option ${parsed.key} has no mutex group`);
  }
  return Object.freeze(parsed);
}

function validateCommand(value, suiteIds) {
  const command = object(value, "catalog command");
  exactKeys(command, [
    "command_id", "suite_id", "title", "summary", "authority", "risk", "preview",
    "availability", "documentation", "document", "limitations", "command_preview",
    "action_digest", "options",
  ], [], "catalog command");
  const suiteId = identifier(command.suite_id, "command suite ID");
  if (!suiteIds.has(suiteId)) throw new Error(`command references unknown suite ${suiteId}`);
  if (!Array.isArray(command.limitations) || !Array.isArray(command.options)
      || command.limitations.length > 256 || command.options.length > 256) {
    throw new Error("catalog command metadata is outside the supported bound");
  }
  const options = command.options.map(validateOption);
  if (new Set(options.map((option) => option.key)).size !== options.length) {
    throw new Error(`catalog command ${command.command_id} has duplicate option keys`);
  }
  return Object.freeze({
    command_id: identifier(command.command_id, "command ID"),
    suite_id: suiteId,
    title: string(command.title, "command title"),
    summary: string(command.summary, "command summary"),
    authority: string(command.authority, "command authority"),
    risk: member(command.risk, RISKS, "command risk"),
    preview: member(command.preview, PREVIEWS, "command preview"),
    availability: member(command.availability, AVAILABILITIES, "command availability"),
    documentation: nullableString(command.documentation, "command documentation"),
    document: nullableString(command.document, "command document"),
    limitations: Object.freeze(command.limitations.map((item) => string(item, "command limitation"))),
    command_preview: string(command.command_preview, "command preview text"),
    action_digest: digest(command.action_digest, "command action digest"),
    options: Object.freeze(options),
  });
}

function validateCatalog(value) {
  const catalog = object(value, "Workbench command catalog");
  exactKeys(catalog, ["format_version", "catalog_digest", "suites", "commands"], [], "Workbench command catalog");
  if (catalog.format_version !== CATALOG_FORMAT) {
    throw new Error("Workbench returned an unsupported command catalog");
  }
  if (!Array.isArray(catalog.suites) || !Array.isArray(catalog.commands)
      || catalog.suites.length < 1 || catalog.suites.length > 256
      || catalog.commands.length < 1 || catalog.commands.length > 4096) {
    throw new Error("Workbench command catalog size is outside the supported bound");
  }
  const suites = catalog.suites.map(validateSuite);
  const suiteIds = new Set(suites.map((suite) => suite.suite_id));
  if (suiteIds.size !== suites.length) throw new Error("catalog contains duplicate suite IDs");
  const commands = catalog.commands.map((command) => validateCommand(command, suiteIds));
  if (new Set(commands.map((command) => command.command_id)).size !== commands.length) {
    throw new Error("catalog contains duplicate command IDs");
  }
  for (const suite of suites) {
    if (commands.filter((command) => command.suite_id === suite.suite_id).length !== suite.command_count) {
      throw new Error(`catalog suite ${suite.suite_id} has a stale command count`);
    }
  }
  return Object.freeze({
    format_version: CATALOG_FORMAT,
    catalog_digest: digest(catalog.catalog_digest, "catalog digest"),
    suites: Object.freeze(suites),
    commands: Object.freeze(commands),
  });
}

function assignmentArguments(values) {
  if (!(values instanceof Map)) throw new Error("command assignments must be a Map");
  const arguments_ = [];
  let encodedBytes = 0;
  for (const [key, value] of values) {
    if (!OPTION_KEY.test(key)) throw new Error(`unsafe Workbench option key: ${key}`);
    const encoded = JSON.stringify(value);
    const assignment = `${key}:=${encoded}`;
    encodedBytes += Buffer.byteLength(assignment, "utf8");
    if (encoded === undefined || encodedBytes > MAX_ASSIGNMENT_BYTES) {
      throw new Error(`Workbench option ${key} is outside the assignment boundary`);
    }
    arguments_.push("--set", assignment);
  }
  return arguments_;
}

class ExactIntegerArrayParser {
  constructor(source) {
    this.source = source;
    this.index = 0;
  }

  parse() {
    const value = this.value(0);
    this.whitespace();
    if (this.index !== this.source.length) throw new Error("unexpected content after integer input");
    return value;
  }

  value(depth) {
    this.whitespace();
    if (this.source[this.index] === "[") {
      if (depth >= 64) throw new Error("integer input nesting exceeds the supported bound");
      return this.array(depth + 1);
    }
    const match = /^-?(?:0|[1-9]\d*)/.exec(this.source.slice(this.index));
    if (!match) throw new Error("integer arrays may contain only JSON integers and arrays");
    this.index += match[0].length;
    const next = this.source[this.index];
    if (next !== undefined && next !== "," && next !== "]" && !/[ \t\r\n]/.test(next)) {
      throw new Error("integer arrays may contain only JSON integers and arrays");
    }
    return match[0];
  }

  array(depth) {
    this.index += 1;
    const values = [];
    this.whitespace();
    if (this.source[this.index] === "]") {
      this.index += 1;
      return values;
    }
    while (true) {
      values.push(this.value(depth));
      this.whitespace();
      if (this.source[this.index] === "]") {
        this.index += 1;
        return values;
      }
      if (this.source[this.index] !== ",") throw new Error("integer arrays must use JSON comma separators");
      this.index += 1;
    }
  }

  whitespace() {
    while (this.index < this.source.length && /[ \t\r\n]/.test(this.source[this.index])) this.index += 1;
  }
}

function parseExactIntegerInput(raw, multiple) {
  if (typeof raw !== "string" || Buffer.byteLength(raw, "utf8") > 64 * 1024) {
    throw new Error("integer input exceeds the supported bound");
  }
  if (!multiple) {
    if (!/^-?\d+$/.test(raw)) throw new Error("integer input must contain decimal digits");
    return raw;
  }
  const value = new ExactIntegerArrayParser(raw).parse();
  if (!Array.isArray(value)) throw new Error("structured integer input must be a JSON array");
  return value;
}

function validateReview(value, catalogDigest, command) {
  const review = object(value, "Workbench command review");
  exactKeys(review, [
    "format_version", "catalog_digest", "action_digest", "review_digest", "command_id",
    "risk", "preview", "preview_intent", "execute_intent", "preview_command", "execute_command",
  ], [], "Workbench command review");
  if (review.format_version !== REVIEW_FORMAT
      || !["preview", "inert", "execute"].includes(review.preview_intent)
      || review.execute_intent !== "execute") {
    throw new Error("Workbench returned an unsupported command review");
  }
  const parsed = Object.freeze({
    format_version: REVIEW_FORMAT,
    catalog_digest: digest(review.catalog_digest, "review catalog digest"),
    action_digest: digest(review.action_digest, "review action digest"),
    review_digest: digest(review.review_digest, "review digest"),
    command_id: identifier(review.command_id, "review command ID"),
    risk: member(review.risk, RISKS, "review risk"),
    preview: member(review.preview, PREVIEWS, "review preview"),
    preview_intent: review.preview_intent,
    execute_intent: "execute",
    preview_command: reviewText(review.preview_command, "review preview command"),
    execute_command: reviewText(review.execute_command, "review execute command"),
  });
  if (parsed.catalog_digest !== catalogDigest
      || parsed.action_digest !== command.action_digest
      || parsed.command_id !== command.command_id
      || parsed.risk !== command.risk
      || parsed.preview !== command.preview) {
    throw new Error("Workbench command review does not match the selected catalog action");
  }
  const expectedPreviewIntent = parsed.preview === "none"
    ? "execute" : parsed.preview === "inert-only" ? "inert" : "preview";
  if (parsed.preview_intent !== expectedPreviewIntent) {
    throw new Error("Workbench command review preview intent contradicts its catalog strategy");
  }
  return parsed;
}

function composeCommandFlow(catalogDigest, command, assignments) {
  digest(catalogDigest, "catalog digest");
  digest(command.action_digest, "command action digest");
  if (!Array.isArray(assignments)) throw new Error("command assignments are invalid");
  const base = [
    "console", "run", identifier(command.command_id, "command ID"),
    ...assignments,
    "--expect-catalog-digest", catalogDigest,
    "--expect-action-digest", command.action_digest,
  ];
  return Object.freeze({
    commandReviewArguments: Object.freeze([...base, "--review-json"]),
    bindReview(value) {
      const review = validateReview(value, catalogDigest, command);
      const bound = [...base, "--expect-review-digest", review.review_digest];
      return Object.freeze({
        review,
        ownerPreviewArguments: OWNER_PREVIEWS.has(command.preview)
          ? Object.freeze([...bound]) : undefined,
        executeArguments: Object.freeze([...bound, "--execute"]),
      });
    },
  });
}

async function invokeCatalog(executable, options = {}) {
  const value = await invokeCoreJson(executable, ["console", "catalog", "--json"], {
    ...options,
    maximumOutput: MAX_CATALOG_OUTPUT,
    timeoutMs: 60_000,
    label: "Workbench command catalog",
  });
  return validateCatalog(value);
}

async function invokeCommandReview(executable, arguments_, options = {}) {
  return invokeCoreJson(executable, arguments_, {
    ...options,
    maximumOutput: MAX_REVIEW_OUTPUT,
    timeoutMs: 60_000,
    label: "Workbench command review",
  });
}

async function invokeCommandOutput(executable, arguments_, options = {}) {
  return invokeCoreText(executable, arguments_, {
    ...options,
    maximumOutput: 48 * 1024 * 1024,
    timeoutMs: options.timeoutMs === undefined ? MAX_TIMEOUT_MS : options.timeoutMs,
  });
}

module.exports = {
  CATALOG_FORMAT,
  REVIEW_FORMAT,
  assignmentArguments,
  composeCommandFlow,
  invokeCatalog,
  invokeCommandOutput,
  invokeCommandReview,
  parseExactIntegerInput,
  validateCatalog,
  validateReview,
};
