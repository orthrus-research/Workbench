"use strict";

const crypto = require("node:crypto");
const path = require("node:path");

const { invokeCoreJson } = require("./coreCommandClient");
const { pathForCoreLaunch, resolveCoreLaunch } = require("./coreLaunch");
const { validateWorkspaceHome } = require("./workspaceHomeClient");

const HOME_V2_FORMAT = "workbench-workspace-home-v2";
const CATALOG_FORMAT = "workbench-live-console-command-catalog-v2";
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const HOME_ID = /^workspace-home:sha256:[0-9a-f]{64}$/;
const WORKSPACE_ID = /^workspace:sha256:[0-9a-f]{64}$/;
const OWNER_REF_ID = /^owner-ref:sha256:[0-9a-f]{64}$/;
const SESSION_ID = /^work-session-v2-[0-9a-f]{32}$/;
const SESSION_RECORD_ID = /^work-session-record:sha256:[0-9a-f]{64}$/;
const CAPABILITY_CATALOG_ID = /^workbench-product-capabilities:sha256:[0-9a-f]{64}$/;
const BINDING_ID = /^workspace-home-binding:sha256:[0-9a-f]{64}$/;
const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,255}$/;
const FRESHNESS = new Set(["current", "stale", "corrupt", "not-applicable", "unavailable"]);
const CLEANROOM_NEW_PROJECT_KIND = "workbench-new-project-kind:cleanroom-mod";
const CLEANROOM_NEW_PROJECT_OWNER_KIND = "new-project-construction";
const CLEANROOM_NEW_PROJECT_OWNER_ID = "cleanroom-platform-profile";
const CLEANROOM_NEW_PROJECT_OWNER_FORMAT = "workbench-cleanroom-mod-construction-owner-v2";
const CLEANROOM_NEW_PROJECT_SAFE_ACTION = "workbench new cleanroom-mod preview --help";
const NEW_PROJECT_UNAVAILABLE_BLOCKERS = new Set([
  "NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE",
  "OWNER_ADMITTED_NEW_KIND_ABSENT",
]);
const MAX_JOBS = 5;
const MAX_ROWS = 4096;
const MAX_TEXT_BYTES = 256 * 1024;

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error(`${label} must be an ordinary object`);
  }
  return value;
}

function exactKeys(value, required, label) {
  const actual = new Set(Object.keys(value));
  const missing = required.filter((key) => !actual.has(key));
  const extra = [...actual].filter((key) => !required.includes(key));
  if (missing.length || extra.length) {
    throw new Error(`${label} fields changed; missing=${missing.join(",")}; extra=${extra.join(",")}`);
  }
}

function text(value, label, { nullable = false, empty = false } = {}) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || (!empty && !value) || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > MAX_TEXT_BYTES) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function patterned(value, pattern, label, { nullable = false } = {}) {
  const selected = text(value, label, { nullable });
  if (selected !== null && !pattern.test(selected)) throw new Error(`${label} is invalid`);
  return selected;
}

function boolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
  return value;
}

function integer(value, label, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) throw new Error(`${label} is invalid`);
  return value;
}

function member(value, choices, label) {
  if (!choices.has(value)) throw new Error(`${label} is unsupported`);
  return value;
}

function array(value, label, maximum = MAX_ROWS) {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new Error(`${label} is outside its supported bound`);
  }
  return value;
}

function strings(value, label, maximum = 256) {
  return array(value, label, maximum).map((item, index) => text(item, `${label}[${index}]`));
}

function canonicalJson(value) {
  if (value === null) return "null";
  if (["string", "boolean"].includes(typeof value)) return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Home identity contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const selected = object(value, "Home identity material");
  return `{${Object.keys(selected).sort().map((key) => (
    `${JSON.stringify(key)}:${canonicalJson(selected[key])}`
  )).join(",")}}`;
}

function contentIdentity(prefix, value, excluded) {
  const material = { ...value };
  delete material[excluded];
  const digest = crypto.createHash("sha256").update(`${canonicalJson(material)}\n`).digest("hex");
  return prefix ? `${prefix}:sha256:${digest}` : `sha256:${digest}`;
}

function freeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

function compareOwnerReference(left, right) {
  for (const field of ["kind", "owner_id", "id"]) {
    if (left[field] < right[field]) return -1;
    if (left[field] > right[field]) return 1;
  }
  return 0;
}

function validateOwnerReference(value, index) {
  const ref = object(value, `Home V2 owner reference ${index}`);
  exactKeys(ref, [
    "id", "kind", "owner_id", "record_format", "record_id", "session_id",
    "catalog_id", "record_revision", "record_digest", "bound_workspace_revision",
    "freshness", "integrity", "validation_problem",
  ], `Home V2 owner reference ${index}`);
  const kind = patterned(ref.kind, IDENTITY, `Home V2 owner reference ${index} kind`);
  const ownerId = patterned(ref.owner_id, IDENTITY, `Home V2 owner reference ${index} owner ID`);
  const recordDigest = patterned(ref.record_digest, DIGEST, `Home V2 owner reference ${index} digest`);
  const id = patterned(ref.id, OWNER_REF_ID, `Home V2 owner reference ${index} ID`);
  const expectedId = contentIdentity("owner-ref", {
    kind, owner_id: ownerId, record_digest: recordDigest,
  }, "not-present");
  if (id !== expectedId || ref.record_revision !== recordDigest) {
    throw new Error(`Home V2 owner reference ${index} identity changed`);
  }
  const freshness = member(ref.freshness, new Set([
    "current", "stale", "corrupt", "not-applicable",
  ]), `Home V2 owner reference ${index} freshness`);
  const integrity = member(
    ref.integrity, new Set(["verified", "failed"]), `Home V2 owner reference ${index} integrity`,
  );
  if ((freshness === "corrupt") !== (integrity === "failed")) {
    throw new Error(`Home V2 owner reference ${index} corrupt state is inconsistent`);
  }
  const recordId = text(ref.record_id, `Home V2 owner reference ${index} record ID`, { nullable: true });
  const sessionId = patterned(
    ref.session_id, SESSION_ID, `Home V2 owner reference ${index} session ID`, { nullable: true },
  );
  const catalogId = patterned(
    ref.catalog_id, CAPABILITY_CATALOG_ID,
    `Home V2 owner reference ${index} capability catalog ID`, { nullable: true },
  );
  const validationProblem = text(
    ref.validation_problem, `Home V2 owner reference ${index} validation problem`, { nullable: true },
  );
  if (validationProblem !== null && validationProblem !== "OWNER_VALIDATION_FAILED") {
    throw new Error(`Home V2 owner reference ${index} validation problem is unsupported`);
  }
  if (validationProblem !== null && (freshness !== "corrupt" || integrity !== "failed"
      || recordId !== null || sessionId !== null || catalogId !== null)) {
    throw new Error(`Home V2 owner reference ${index} rejected record was not isolated`);
  }
  if (validationProblem === null && kind === "work-session"
      && (recordId === null || !SESSION_RECORD_ID.test(recordId)
      || sessionId === null || catalogId !== null)) {
    throw new Error(`Home V2 owner reference ${index} lost its Work Session identities`);
  }
  if (validationProblem === null && kind === "product-capability-catalog"
      && (catalogId === null || recordId !== catalogId || sessionId !== null)) {
    throw new Error(`Home V2 owner reference ${index} lost its capability catalog identity`);
  }
  if (!["work-session", "product-capability-catalog"].includes(kind)
      && (sessionId !== null || catalogId !== null)) {
    throw new Error(`Home V2 owner reference ${index} carries a reserved source identity`);
  }
  return {
    id,
    kind,
    owner_id: ownerId,
    record_format: text(ref.record_format, `Home V2 owner reference ${index} format`),
    record_id: recordId,
    session_id: sessionId,
    catalog_id: catalogId,
    record_revision: recordDigest,
    record_digest: recordDigest,
    bound_workspace_revision: patterned(
      ref.bound_workspace_revision, DIGEST, `Home V2 owner reference ${index} workspace binding`,
      { nullable: true },
    ),
    freshness,
    integrity,
    validation_problem: validationProblem,
  };
}

function validateOwnerProjection(value, label, ownerById, kind) {
  const projection = object(value, label);
  exactKeys(projection, [
    "state", "owner_ref_id", "record_id", "session_id", "catalog_id",
    "record_revision", "freshness", "reason",
  ], label);
  const state = member(projection.state, new Set(["available", "unavailable"]), `${label} state`);
  const freshness = member(projection.freshness, FRESHNESS, `${label} freshness`);
  const ownerRefId = patterned(projection.owner_ref_id, OWNER_REF_ID, `${label} owner reference ID`, { nullable: true });
  const recordRevision = patterned(projection.record_revision, DIGEST, `${label} record revision`, { nullable: true });
  const sessionId = patterned(projection.session_id, SESSION_ID, `${label} session ID`, { nullable: true });
  const catalogId = patterned(
    projection.catalog_id, CAPABILITY_CATALOG_ID, `${label} catalog ID`, { nullable: true },
  );
  const recordId = text(projection.record_id, `${label} record ID`, { nullable: true });
  const reason = text(projection.reason, `${label} reason`, { nullable: true });
  if (ownerRefId === null) {
    if (state !== "unavailable" || freshness !== "unavailable"
        || recordId !== null || recordRevision !== null || sessionId !== null
        || catalogId !== null || reason === null) {
      throw new Error(`${label} absent owner state is inconsistent`);
    }
  } else {
    const ownerRef = ownerById.get(ownerRefId);
    if (!ownerRef || ownerRef.kind !== kind || ownerRef.record_revision !== recordRevision
        || ownerRef.freshness !== freshness || ownerRef.record_id !== recordId
        || ownerRef.session_id !== sessionId || ownerRef.catalog_id !== catalogId) {
      throw new Error(`${label} differs from its exact owner reference`);
    }
    const shouldBeAvailable = ["current", "not-applicable"].includes(freshness);
    if ((state === "available") !== shouldBeAvailable
        || (shouldBeAvailable && reason !== null) || (!shouldBeAvailable && reason === null)) {
      throw new Error(`${label} owner availability is inconsistent`);
    }
    if (kind === "work-session") {
      if (sessionId === null || catalogId !== null
          || !SESSION_RECORD_ID.test(recordId || "")) {
        throw new Error(`${label} omits or changes the owner-returned session identity`);
      }
    } else if (kind === "product-capability-catalog") {
      if (catalogId === null || sessionId !== null || recordId !== catalogId) {
        throw new Error(`${label} omits or changes the owner-returned catalog identity`);
      }
    }
  }
  return {
    state,
    owner_ref_id: ownerRefId,
    record_id: recordId,
    session_id: sessionId,
    catalog_id: catalogId,
    record_revision: recordRevision,
    freshness,
    reason,
  };
}

function validateAvailabilityBasis(
  value, label, jobId, commandId, actionDigest, capabilityId, capability, context,
) {
  const basis = object(value, `${label} availability basis`);
  exactKeys(basis, [
    "kind", "scope", "owner_ref_id", "owner_record_revision", "command_id",
    "capability_id", "global_capability_effect",
  ], `${label} availability basis`);
  const parsed = {
    kind: member(
      basis.kind,
      new Set(["base-home", "owner-context-resolution", "product-capability"]),
      `${label} availability basis kind`,
    ),
    scope: member(
      basis.scope,
      new Set(["base-home", "workspace-context", "cleanroom-fixture", "global"]),
      `${label} availability basis scope`,
    ),
    owner_ref_id: patterned(
      basis.owner_ref_id, OWNER_REF_ID, `${label} availability basis owner`, { nullable: true },
    ),
    owner_record_revision: patterned(
      basis.owner_record_revision, DIGEST, `${label} availability basis revision`, { nullable: true },
    ),
    command_id: patterned(
      basis.command_id, IDENTITY, `${label} availability basis command`, { nullable: true },
    ),
    capability_id: patterned(
      basis.capability_id, IDENTITY, `${label} availability basis capability`, { nullable: true },
    ),
    global_capability_effect: member(
      basis.global_capability_effect,
      new Set(["not-applicable", "retained-unmodified", "authoritative"]),
      `${label} availability basis global effect`,
    ),
  };
  const doctors = [...context.ownerById.values()].filter((reference) => (
    reference.kind === "workspace-context" && reference.owner_id === "project-intelligence"
  ));
  if (doctors.length !== 1) throw new Error(`${label} lost its exact workspace-context owner`);
  const doctor = doctors[0];
  const fixtures = [...context.ownerById.values()].filter((reference) => (
    reference.kind === "cleanroom-fixture-lock"
    && reference.owner_id === "cleanroom-platform-profile"
  ));
  if (fixtures.length > 1) throw new Error(`${label} has duplicate Cleanroom fixture owners`);
  const fixture = fixtures[0] || null;
  const capabilityCatalog = context.capabilityCatalogOwnerRefId
    ? context.ownerById.get(context.capabilityCatalogOwnerRefId) : null;
  const exactContextCapability = capability
    && capability.catalog_action.action_digest === actionDigest
    && capability.availability !== "unavailable"
    && capability.handler.registered === true
    && capability.handler.executable === true;
  const fixtureResolution = jobId === "cleanroom-fixture-build"
    && commandId === "cleanroom.fixture-build" && fixture
    && fixture.freshness === "current" && fixture.integrity === "verified"
    && fixture.validation_problem === null
    && fixture.bound_workspace_revision === context.workspaceRevision
    && exactContextCapability;
  const workspaceResolution = jobId === "workspace-health"
    && commandId === "doctor.inspect"
    && doctor.freshness === "current" && doctor.integrity === "verified"
    && doctor.validation_problem === null
    && doctor.bound_workspace_revision === context.workspaceRevision
    && exactContextCapability;
  let expected;
  if (commandId === null) {
    expected = {
      kind: "base-home", scope: "base-home", owner_ref_id: doctor.id,
      owner_record_revision: doctor.record_revision, command_id: null,
      capability_id: null, global_capability_effect: "not-applicable",
    };
  } else if (fixtureResolution) {
    expected = {
      kind: "owner-context-resolution", scope: "cleanroom-fixture",
      owner_ref_id: fixture.id, owner_record_revision: fixture.record_revision,
      command_id: commandId, capability_id: capabilityId,
      global_capability_effect: "retained-unmodified",
    };
  } else if (workspaceResolution) {
    expected = {
      kind: "owner-context-resolution", scope: "workspace-context",
      owner_ref_id: doctor.id, owner_record_revision: doctor.record_revision,
      command_id: commandId, capability_id: capabilityId,
      global_capability_effect: "retained-unmodified",
    };
  } else {
    expected = {
      kind: "product-capability", scope: "global",
      owner_ref_id: capabilityCatalog ? capabilityCatalog.id : null,
      owner_record_revision: capabilityCatalog ? capabilityCatalog.record_revision : null,
      command_id: commandId, capability_id: capabilityId,
      global_capability_effect: "authoritative",
    };
  }
  if (JSON.stringify(parsed) !== JSON.stringify(expected)) {
    throw new Error(`${label} availability basis differs from exact owner resolution`);
  }
  return parsed;
}

function validateJob(value, index, context) {
  const job = object(value, `Home V2 job ${index}`);
  exactKeys(job, [
    "id", "rank", "title", "purpose", "state", "argv", "blockers",
    "unavailable_reason", "owner_ref_ids", "eligibility_binding", "command_id",
    "catalog_digest", "action_digest", "capability_id", "capability_key", "capability",
    "availability_basis", "tool_inputs", "next_safe_action", "arguments",
    "eligibility_digest",
  ], `Home V2 job ${index}`);
  const id = patterned(job.id, IDENTITY, `Home V2 job ${index} ID`);
  const state = member(job.state, new Set(["available", "unavailable"]), `Home V2 job ${index} state`);
  const blockers = strings(job.blockers, `Home V2 job ${index} blockers`);
  if (new Set(blockers).size !== blockers.length
      || blockers.some((item, offset) => offset > 0 && blockers[offset - 1] > item)) {
    throw new Error(`Home V2 job ${index} blockers are not canonical`);
  }
  const ownerRefIds = strings(job.owner_ref_ids, `Home V2 job ${index} owner references`)
    .map((item, offset) => patterned(item, OWNER_REF_ID, `Home V2 job ${index} owner reference ${offset}`));
  if (new Set(ownerRefIds).size !== ownerRefIds.length
      || ownerRefIds.some((item, offset) => offset > 0 && ownerRefIds[offset - 1] > item)
      || ownerRefIds.some((item) => !context.ownerById.has(item))) {
    throw new Error(`Home V2 job ${index} owner references are inconsistent`);
  }
  const binding = object(job.eligibility_binding, `Home V2 job ${index} eligibility binding`);
  exactKeys(binding, [
    "workspace_revision", "session_revision", "capability_catalog_revision",
  ], `Home V2 job ${index} eligibility binding`);
  const parsedBinding = {
    workspace_revision: patterned(
      binding.workspace_revision, DIGEST, `Home V2 job ${index} workspace revision`,
    ),
    session_revision: patterned(
      binding.session_revision, DIGEST, `Home V2 job ${index} session revision`, { nullable: true },
    ),
    capability_catalog_revision: patterned(
      binding.capability_catalog_revision, DIGEST,
      `Home V2 job ${index} capability catalog revision`, { nullable: true },
    ),
  };
  const expectedSessionRevision = context.sessionOwnerRefId
    && ownerRefIds.includes(context.sessionOwnerRefId) ? context.sessionRevision : null;
  const expectedCapabilityCatalogRevision = context.capabilityCatalogOwnerRefId
    && ownerRefIds.includes(context.capabilityCatalogOwnerRefId)
    ? context.capabilityCatalogRevision : null;
  if (parsedBinding.workspace_revision !== context.workspaceRevision
      || parsedBinding.session_revision !== expectedSessionRevision
      || parsedBinding.capability_catalog_revision !== expectedCapabilityCatalogRevision) {
    throw new Error(`Home V2 job ${index} eligibility identity changed`);
  }
  const commandId = patterned(job.command_id, IDENTITY, `Home V2 job ${index} command ID`, { nullable: true });
  const actionDigest = patterned(job.action_digest, DIGEST, `Home V2 job ${index} action digest`, { nullable: true });
  if ((commandId === null) !== (actionDigest === null)) {
    throw new Error(`Home V2 job ${index} has a partial catalog identity`);
  }
  const capabilityId = patterned(
    job.capability_id, IDENTITY, `Home V2 job ${index} capability ID`, { nullable: true },
  );
  const capabilityKey = patterned(
    job.capability_key, IDENTITY, `Home V2 job ${index} capability key`, { nullable: true },
  );
  if ((capabilityId === null) !== (capabilityKey === null)) {
      throw new Error(`Home V2 job ${index} has a partial product capability identity`);
  }
  let capability = null;
  if (job.capability !== null) {
    if (capabilityId === null || capabilityKey === null || commandId === null || actionDigest === null) {
      throw new Error(`Home V2 job ${index} has a partial product capability`);
    }
    capability = validateCapability(
      job.capability, commandId, actionDigest, `Home V2 job ${index} capability`,
    );
    if (capability.capability_id !== capabilityId || capability.capability_key !== capabilityKey) {
      throw new Error(`Home V2 job ${index} product capability identity changed`);
    }
    if (!context.capabilityCatalogOwnerRefId
        || !ownerRefIds.includes(context.capabilityCatalogOwnerRefId)) {
      throw new Error(`Home V2 job ${index} capability lost its exact catalog owner reference`);
    }
  } else if (capabilityId !== null || capabilityKey !== null) {
    throw new Error(`Home V2 job ${index} has a partial product capability`);
  }
  let arguments_ = null;
  if (commandId === null) {
    if (job.arguments !== null) throw new Error(`Home V2 job ${index} has arguments without a catalog action`);
  } else {
    arguments_ = object(job.arguments, `Home V2 job ${index} arguments`);
  }
  if (id === "cleanroom-fixture-build" && arguments_ !== null) {
    exactKeys(
      arguments_, ["gradle_cmd", "java_home", "expected_input_digest", "state_root"],
      `Home V2 job ${index} fixture arguments`,
    );
    text(arguments_.gradle_cmd, `Home V2 job ${index} Gradle path`, { nullable: true });
    text(arguments_.java_home, `Home V2 job ${index} Java home`, { nullable: true });
    patterned(
      arguments_.expected_input_digest, DIGEST,
      `Home V2 job ${index} expected input digest`, { nullable: true },
    );
    const stateRoot = text(arguments_.state_root, `Home V2 job ${index} state root`);
    if (!path.posix.isAbsolute(stateRoot) && !path.win32.isAbsolute(stateRoot)) {
      throw new Error(`Home V2 job ${index} state root is not absolute`);
    }
  }
  const allowedToolKinds = new Set([
    "fixture-cleanup-init", "fixture-owner-lock", "fixture-owner-schema",
    "gradle-executable", "java-executable", "java-release", "profile-preflight-tool",
  ]);
  const toolInputs = array(job.tool_inputs, `Home V2 job ${index} tool inputs`, 256)
    .map((value, offset) => {
      const input = object(value, `Home V2 job ${index} tool input ${offset}`);
      exactKeys(input, ["kind", "path", "sha256", "size", "mode"], `Home V2 job ${index} tool input ${offset}`);
      const kind = text(input.kind, `Home V2 job ${index} tool input ${offset} kind`);
      const result = {
        kind,
        path: text(input.path, `Home V2 job ${index} tool input ${offset} path`),
        sha256: patterned(input.sha256, DIGEST, `Home V2 job ${index} tool input ${offset} digest`),
        size: integer(input.size, `Home V2 job ${index} tool input ${offset} size`),
        mode: integer(input.mode, `Home V2 job ${index} tool input ${offset} mode`),
      };
      if (!allowedToolKinds.has(kind) || result.mode > 0o7777) {
        throw new Error(`Home V2 job ${index} tool input ${offset} is unsupported`);
      }
      return result;
    });
  const toolKeys = toolInputs.map((input) => `${input.kind}\0${input.path}`);
  if (JSON.stringify(toolKeys) !== JSON.stringify([...new Set(toolKeys)].sort())
      || (id !== "cleanroom-fixture-build" && toolInputs.length)) {
    throw new Error(`Home V2 job ${index} tool input order or ownership changed`);
  }
  const nextSafeAction = text(
    job.next_safe_action, `Home V2 job ${index} next safe action`, { nullable: true },
  );
  if (id !== "cleanroom-fixture-build" && nextSafeAction !== null) {
    throw new Error(`Home V2 job ${index} invented a Cleanroom fixture next action`);
  }
  const catalogDigest = patterned(job.catalog_digest, DIGEST, `Home V2 job ${index} catalog digest`);
  if (catalogDigest !== context.catalogDigest) throw new Error(`Home V2 job ${index} catalog identity changed`);
  const availabilityBasis = validateAvailabilityBasis(
    job.availability_basis, `Home V2 job ${index}`, id, commandId, actionDigest,
    capabilityId, capability, context,
  );
  if (availabilityBasis.owner_ref_id !== null
      && !ownerRefIds.includes(availabilityBasis.owner_ref_id)) {
    throw new Error(`Home V2 job ${index} availability basis lost its owner reference`);
  }
  const globallyExecutable = capability
    && capability.availability !== "unavailable"
    && capability.handler.registered === true
    && capability.handler.executable === true
    && capability.catalog_action.action_digest === actionDigest;
  if (state === "available"
      && availabilityBasis.kind !== "owner-context-resolution"
      && !globallyExecutable) {
    throw new Error(`available Home V2 job ${index} was not authorized by its exact availability basis`);
  }
  if (state === "available" && id === "cleanroom-fixture-build"
      && (JSON.stringify(toolInputs.map((input) => input.kind))
          !== JSON.stringify([...allowedToolKinds].sort()) || nextSafeAction !== null)) {
    throw new Error(`Home V2 job ${index} executable fixture tool custody is incomplete`);
  }
  let argv = null;
  let reason = null;
  if (state === "available") {
    argv = strings(job.argv, `Home V2 job ${index} argv`, 256);
    if (!argv.length || blockers.length || job.unavailable_reason !== null
        || commandId === null || ownerRefIds.some((refId) => (
          ["stale", "corrupt"].includes(context.ownerById.get(refId).freshness)
        ))) {
      throw new Error(`available Home V2 job ${index} depends on blocked, stale, or corrupt owner state`);
    }
  } else {
    reason = text(job.unavailable_reason, `Home V2 job ${index} unavailable reason`);
    if (job.argv !== null || blockers.length < 1) {
      throw new Error(`unavailable Home V2 job ${index} was made executable`);
    }
  }
  const eligibilityDigest = patterned(
    job.eligibility_digest, DIGEST, `Home V2 job ${index} eligibility digest`,
  );
  if (eligibilityDigest !== contentIdentity("", job, "eligibility_digest")) {
    throw new Error(`Home V2 job ${index} eligibility identity changed`);
  }
  return {
    id,
    rank: integer(job.rank, `Home V2 job ${index} rank`),
    title: text(job.title, `Home V2 job ${index} title`),
    purpose: text(job.purpose, `Home V2 job ${index} purpose`),
    state,
    argv,
    blockers,
    unavailable_reason: reason,
    owner_ref_ids: ownerRefIds,
    eligibility_binding: parsedBinding,
    command_id: commandId,
    catalog_digest: catalogDigest,
    action_digest: actionDigest,
    capability_id: capabilityId,
    capability_key: capabilityKey,
    capability,
    availability_basis: availabilityBasis,
    tool_inputs: toolInputs,
    next_safe_action: nextSafeAction,
    arguments: arguments_ === null ? null : structuredClone(arguments_),
    eligibility_digest: eligibilityDigest,
  };
}

function validateCapability(value, commandId, actionDigest, label) {
  const capability = object(value, label);
  exactKeys(capability, [
    "capability_id", "capability_key", "title", "summary", "authority", "risk",
    "availability", "handler", "catalog_action", "limitations",
  ], label);
  const capabilityId = patterned(
    capability.capability_id, /^capability:sha256:[0-9a-f]{64}$/, `${label} ID`,
  );
  const capabilityKey = patterned(
    capability.capability_key, /^[a-z][a-z0-9.-]+$/, `${label} key`,
  );
  text(capability.title, `${label} title`);
  text(capability.summary, `${label} summary`);
  text(capability.authority, `${label} authority`);
  member(
    capability.risk,
    new Set(["read-only", "writes-output", "mutating", "destructive"]),
    `${label} risk`,
  );
  member(
    capability.availability,
    new Set(["available", "experimental", "unavailable"]),
    `${label} availability`,
  );
  strings(capability.limitations, `${label} limitations`);

  const catalogAction = object(capability.catalog_action, `${label} catalog action`);
  exactKeys(catalogAction, ["action_digest", "command_id", "suite_id"], `${label} catalog action`);
  if (catalogAction.command_id !== commandId || catalogAction.action_digest !== actionDigest
      || !/^[a-z][a-z0-9-]+$/.test(catalogAction.suite_id)) {
    throw new Error(`${label} catalog binding changed`);
  }
  const handler = object(capability.handler, `${label} handler`);
  exactKeys(handler, ["kind", "registered", "executable"], `${label} handler`);
  member(handler.kind, new Set(["process", "document"]), `${label} handler kind`);
  if (handler.registered !== true || typeof handler.executable !== "boolean"
      || (handler.kind === "document" && handler.executable !== false)
      || (capability.availability === "unavailable" && handler.executable !== false)) {
    throw new Error(`${label} handler binding changed`);
  }
  return structuredClone({
    ...capability,
    capability_id: capabilityId,
    capability_key: capabilityKey,
  });
}

function validateWorkspaceHomeV2(value) {
  const home = object(value, "Workspace Home V2");
  exactKeys(home, [
    "format", "schema_version", "home_id", "operation", "read_only", "local_state_effect",
    "workspace", "status", "freshness", "owner_records", "session", "capability_catalog",
    "jobs", "catalog", "new_project", "adoption", "problems", "base_home", "limitations",
  ], "Workspace Home V2");
  if (home.format !== HOME_V2_FORMAT || home.schema_version !== 2 || home.read_only !== true) {
    throw new Error("Workspace Home V2 identity or format changed");
  }
  const operation = member(home.operation, new Set(["open", "adopt", "reopen"]), "Home V2 operation");
  const localStateEffect = member(
    home.local_state_effect, new Set(["none", "adoption-binding-created"]),
    "Home V2 local state effect",
  );
  const baseHome = validateWorkspaceHome(home.base_home);
  const workspace = object(home.workspace, "Home V2 workspace");
  exactKeys(workspace, [
    "requested_path", "root", "display_name", "kind", "recognition", "workspace_id",
    "workspace_revision", "source_revision", "dirty_fingerprint",
  ], "Home V2 workspace");
  const workspaceId = patterned(workspace.workspace_id, WORKSPACE_ID, "Home V2 workspace ID");
  if (workspaceId !== contentIdentity("workspace", { root: workspace.root, kind: workspace.kind }, "not-present")
      || workspace.root !== baseHome.workspace.root
      || workspace.kind !== baseHome.workspace.kind) {
    throw new Error("Home V2 workspace identity changed");
  }
  const parsedWorkspace = {
    requested_path: text(workspace.requested_path, "Home V2 requested path"),
    root: text(workspace.root, "Home V2 root"),
    display_name: text(workspace.display_name, "Home V2 display name"),
    kind: text(workspace.kind, "Home V2 workspace kind"),
    recognition: member(workspace.recognition, new Set(["exact", "bounded"]), "Home V2 recognition"),
    workspace_id: workspaceId,
    workspace_revision: patterned(workspace.workspace_revision, DIGEST, "Home V2 workspace revision"),
    source_revision: text(workspace.source_revision, "Home V2 source revision"),
    dirty_fingerprint: patterned(workspace.dirty_fingerprint, DIGEST, "Home V2 dirty fingerprint"),
  };
  const status = object(home.status, "Home V2 status");
  exactKeys(status, ["state", "base_home_state", "blockers", "warnings", "information"], "Home V2 status");
  const parsedStatus = {
    state: member(status.state, new Set(["ready", "attention", "blocked"]), "Home V2 status"),
    base_home_state: member(status.base_home_state, new Set(["ready", "attention", "blocked"]), "Home V2 base status"),
    blockers: integer(status.blockers, "Home V2 blocker count"),
    warnings: integer(status.warnings, "Home V2 warning count"),
    information: integer(status.information, "Home V2 information count"),
  };
  if (parsedStatus.base_home_state !== baseHome.status.status) {
    throw new Error("Home V2 base status changed");
  }
  const freshness = object(home.freshness, "Home V2 freshness");
  exactKeys(
    freshness, ["workspace", "adoption", "session", "capability_catalog"],
    "Home V2 freshness",
  );
  const parsedFreshness = Object.fromEntries(Object.entries(freshness).map(([key, item]) => [
    key, member(item, FRESHNESS, `Home V2 ${key} freshness`),
  ]));
  if (parsedFreshness.workspace !== "current") throw new Error("Home V2 workspace freshness is not current");
  const ownerRecords = array(home.owner_records, "Home V2 owner records", 256)
    .map(validateOwnerReference);
  if (!ownerRecords.length || new Set(ownerRecords.map((item) => item.id)).size !== ownerRecords.length
      || ownerRecords.some((item, index) => index > 0 && compareOwnerReference(ownerRecords[index - 1], item) > 0)) {
    throw new Error("Home V2 owner record identities are not canonical");
  }
  const ownerById = new Map(ownerRecords.map((item) => [item.id, item]));
  const session = validateOwnerProjection(home.session, "Home V2 session", ownerById, "work-session");
  const capabilityCatalog = validateOwnerProjection(
    home.capability_catalog, "Home V2 capability catalog", ownerById,
    "product-capability-catalog",
  );
  if (session.freshness !== parsedFreshness.session
      || capabilityCatalog.freshness !== parsedFreshness.capability_catalog) {
    throw new Error("Home V2 owner projection freshness changed");
  }
  const catalog = object(home.catalog, "Home V2 catalog");
  exactKeys(catalog, ["format_version", "catalog_digest"], "Home V2 catalog");
  if (catalog.format_version !== CATALOG_FORMAT) throw new Error("Home V2 catalog format changed");
  const parsedCatalog = {
    format_version: CATALOG_FORMAT,
    catalog_digest: patterned(catalog.catalog_digest, DIGEST, "Home V2 catalog digest"),
  };
  const rawJobs = array(home.jobs, "Home V2 jobs", MAX_JOBS);
  if (!rawJobs.length) throw new Error("Home V2 must expose one to five jobs");
  const jobs = rawJobs.map((item, index) => validateJob(item, index, {
    ownerById,
    workspaceRevision: parsedWorkspace.workspace_revision,
    sessionRevision: session.record_revision,
    capabilityCatalogRevision: capabilityCatalog.record_revision,
    sessionOwnerRefId: session.owner_ref_id,
    capabilityCatalogOwnerRefId: capabilityCatalog.owner_ref_id,
    catalogDigest: parsedCatalog.catalog_digest,
  }));
  if (new Set(jobs.map((item) => item.id)).size !== jobs.length
      || jobs.some((item, index) => index > 0 && (
        jobs[index - 1].rank > item.rank
        || (jobs[index - 1].rank === item.rank && jobs[index - 1].id > item.id)
      ))) {
    throw new Error("Home V2 job order or identities changed");
  }
  const newProject = object(home.new_project, "Home V2 new-project state");
  exactKeys(newProject, [
    "state", "admitted_kinds", "owner_ref_ids", "blockers", "reason", "next_safe_action",
  ], "Home V2 new-project state");
  const newProjectState = member(
    newProject.state, new Set(["available", "unavailable"]), "Home V2 new-project state",
  );
  const admittedKinds = strings(newProject.admitted_kinds, "Home V2 admitted kinds");
  const newProjectOwnerIds = strings(newProject.owner_ref_ids, "Home V2 new-project owners");
  const newProjectBlockers = strings(newProject.blockers, "Home V2 new-project blockers");
  const newProjectReason = text(newProject.reason, "Home V2 new-project reason");
  const newProjectNextAction = text(
    newProject.next_safe_action, "Home V2 new-project next safe action",
  );
  if (newProjectState === "unavailable") {
    if (admittedKinds.length || newProjectOwnerIds.length
        || newProjectBlockers.length !== 1
        || !NEW_PROJECT_UNAVAILABLE_BLOCKERS.has(newProjectBlockers[0])) {
      throw new Error("Home V2 invented new-project construction authority");
    }
  } else {
    if (JSON.stringify(admittedKinds) !== JSON.stringify([CLEANROOM_NEW_PROJECT_KIND])
        || newProjectOwnerIds.length !== 1 || newProjectBlockers.length
        || newProjectNextAction !== CLEANROOM_NEW_PROJECT_SAFE_ACTION) {
      throw new Error("Home V2 Cleanroom new-project authority changed");
    }
    const ownerRefId = patterned(
      newProjectOwnerIds[0], OWNER_REF_ID, "Home V2 new-project owner reference ID",
    );
    const owner = ownerById.get(ownerRefId);
    if (!owner || owner.kind !== CLEANROOM_NEW_PROJECT_OWNER_KIND
        || owner.owner_id !== CLEANROOM_NEW_PROJECT_OWNER_ID
        || owner.record_format !== CLEANROOM_NEW_PROJECT_OWNER_FORMAT
        || owner.record_id === null || owner.integrity !== "verified"
        || !new Set(["current", "not-applicable"]).has(owner.freshness)
        || owner.validation_problem !== null) {
      throw new Error("Home V2 Cleanroom new-project owner reference is inconsistent");
    }
  }
  const parsedNewProject = {
    state: newProjectState,
    admitted_kinds: admittedKinds,
    owner_ref_ids: newProjectOwnerIds,
    blockers: newProjectBlockers,
    reason: newProjectReason,
    next_safe_action: newProjectNextAction,
  };
  const adoption = object(home.adoption, "Home V2 adoption");
  exactKeys(adoption, [
    "state", "binding_id", "session_id", "state_revision", "adopted_workspace_id",
    "adopted_workspace_root", "freshness", "stale_reasons", "recovery_state",
    "recovery_reasons", "interrupted_write_count",
  ], "Home V2 adoption");
  const parsedAdoption = {
    state: member(adoption.state, new Set(["unadopted", "adopted"]), "Home V2 adoption state"),
    binding_id: patterned(adoption.binding_id, BINDING_ID, "Home V2 binding ID", { nullable: true }),
    session_id: patterned(adoption.session_id, SESSION_ID, "Home V2 adopted session ID", { nullable: true }),
    state_revision: patterned(adoption.state_revision, DIGEST, "Home V2 adoption revision", { nullable: true }),
    adopted_workspace_id: patterned(
      adoption.adopted_workspace_id, WORKSPACE_ID, "Home V2 adopted workspace ID", { nullable: true },
    ),
    adopted_workspace_root: text(
      adoption.adopted_workspace_root, "Home V2 adopted workspace root", { nullable: true },
    ),
    freshness: member(adoption.freshness, FRESHNESS, "Home V2 adoption freshness"),
    stale_reasons: strings(adoption.stale_reasons, "Home V2 adoption stale reasons"),
    recovery_state: member(
      adoption.recovery_state, new Set(["none", "required"]), "Home V2 adoption recovery state",
    ),
    recovery_reasons: strings(adoption.recovery_reasons, "Home V2 adoption recovery reasons"),
    interrupted_write_count: integer(
      adoption.interrupted_write_count, "Home V2 interrupted write count",
    ),
  };
  const allowedStaleReasons = new Set([
    "WORKSPACE_REVISION_CHANGED", "WORKSPACE_ID_CHANGED", "WORKSPACE_ROOT_CHANGED",
    "SOURCE_REVISION_CHANGED", "OWNER_RECORD_REVISIONS_CHANGED",
  ]);
  const allowedRecoveryReasons = new Set([
    "INTERRUPTED_ADOPTION_WRITE", "WORKSPACE_LOCATION_CHANGED",
  ]);
  if (new Set(parsedAdoption.stale_reasons).size !== parsedAdoption.stale_reasons.length
      || parsedAdoption.stale_reasons.some((item, index) => (
        !allowedStaleReasons.has(item)
        || (index > 0 && parsedAdoption.stale_reasons[index - 1] > item)
      ))
      || new Set(parsedAdoption.recovery_reasons).size !== parsedAdoption.recovery_reasons.length
      || parsedAdoption.recovery_reasons.some((item, index) => (
        !allowedRecoveryReasons.has(item)
        || (index > 0 && parsedAdoption.recovery_reasons[index - 1] > item)
      ))
      || ((parsedAdoption.recovery_state === "required")
          !== Boolean(parsedAdoption.recovery_reasons.length))
      || (parsedAdoption.recovery_reasons.includes("INTERRUPTED_ADOPTION_WRITE")
          !== (parsedAdoption.interrupted_write_count > 0))) {
    throw new Error("Home V2 adoption recovery state is inconsistent");
  }
  if (parsedAdoption.freshness !== parsedFreshness.adoption
      || (parsedAdoption.state === "unadopted" && (
        parsedAdoption.binding_id !== null || parsedAdoption.session_id !== null
        || parsedAdoption.state_revision !== null || parsedAdoption.adopted_workspace_id !== null
        || parsedAdoption.adopted_workspace_root !== null
        || parsedAdoption.freshness !== "not-applicable"
        || parsedAdoption.stale_reasons.length || parsedAdoption.recovery_state !== "none"
        || parsedAdoption.recovery_reasons.length
        || parsedAdoption.interrupted_write_count !== 0
      )) || (parsedAdoption.state === "adopted" && (
        parsedAdoption.binding_id === null || parsedAdoption.session_id !== session.session_id
        || parsedAdoption.state_revision === null || parsedAdoption.adopted_workspace_id === null
        || parsedAdoption.adopted_workspace_root === null
        || !["current", "stale"].includes(parsedAdoption.freshness)
        || ((parsedAdoption.freshness === "stale") !== Boolean(parsedAdoption.stale_reasons.length))
      ))) {
    throw new Error("Home V2 adoption identity is inconsistent");
  }
  if (parsedAdoption.state === "adopted") {
    const workspaceIdChanged = parsedAdoption.adopted_workspace_id !== parsedWorkspace.workspace_id;
    const workspaceRootChanged = parsedAdoption.adopted_workspace_root !== parsedWorkspace.root;
    const locationChanged = workspaceIdChanged || workspaceRootChanged;
    if (parsedAdoption.binding_id !== contentIdentity(
      "workspace-home-binding", { workspace_id: parsedAdoption.adopted_workspace_id }, "not-present",
    ) || parsedAdoption.stale_reasons.includes("WORKSPACE_ID_CHANGED") !== workspaceIdChanged
      || parsedAdoption.stale_reasons.includes("WORKSPACE_ROOT_CHANGED") !== workspaceRootChanged
      || parsedAdoption.recovery_reasons.includes("WORKSPACE_LOCATION_CHANGED") !== locationChanged
      || (operation === "adopt" && (
        parsedAdoption.freshness !== "current"
        || parsedAdoption.stale_reasons.length || locationChanged
      ))) {
      throw new Error("Home V2 adoption move identity is inconsistent");
    }
  }
  const problems = array(home.problems, "Home V2 problems").map((item, index) => {
    const problem = object(item, `Home V2 problem ${index}`);
    exactKeys(problem, ["id", "severity", "detail"], `Home V2 problem ${index}`);
    return {
      id: patterned(problem.id, IDENTITY, `Home V2 problem ${index} ID`),
      severity: member(problem.severity, new Set(["blocker", "warning", "info"]), `Home V2 problem ${index} severity`),
      detail: text(problem.detail, `Home V2 problem ${index} detail`),
    };
  });
  if (new Set(problems.map((item) => item.id)).size !== problems.length
      || problems.some((item, index) => index > 0 && problems[index - 1].id > item.id)) {
    throw new Error("Home V2 problem identities are not canonical");
  }
  const homeId = patterned(home.home_id, HOME_ID, "Home V2 ID");
  if (homeId !== contentIdentity("workspace-home", home, "home_id")) {
    throw new Error("Home V2 content identity changed");
  }
  return freeze({
    format: HOME_V2_FORMAT,
    schema_version: 2,
    home_id: homeId,
    operation,
    read_only: true,
    local_state_effect: localStateEffect,
    workspace: parsedWorkspace,
    status: parsedStatus,
    freshness: parsedFreshness,
    owner_records: ownerRecords,
    session,
    capability_catalog: capabilityCatalog,
    jobs,
    catalog: parsedCatalog,
    new_project: parsedNewProject,
    adoption: parsedAdoption,
    problems,
    base_home: baseHome,
    limitations: strings(home.limitations, "Home V2 limitations"),
  });
}

async function invokeWorkspaceHomeV2(executable, workspace, options = {}) {
  const launch = options.launch || resolveCoreLaunch(executable, {
    platform: options.platform,
    environment: options.environment,
  });
  const mappedWorkspace = pathForCoreLaunch(workspace, launch, "workspace Home V2 path");
  const mappedStateRoot = options.stateRoot === undefined
    ? undefined
    : pathForCoreLaunch(
      options.stateRoot, launch, "workspace Home V2 product-spine state root",
    );
  const value = await invokeCoreJson(executable, [
    "open", mappedWorkspace,
    ...(mappedStateRoot === undefined ? [] : ["--state-root", mappedStateRoot]),
    "--json",
  ], {
    ...options,
    launch,
    maximumOutput: 16 * 1024 * 1024,
    timeoutMs: options.timeoutMs === undefined ? 120_000 : options.timeoutMs,
    label: "Workbench workspace Home V2",
  });
  return validateWorkspaceHomeV2(value);
}

module.exports = {
  HOME_V2_FORMAT,
  invokeWorkspaceHomeV2,
  validateWorkspaceHomeV2,
};
