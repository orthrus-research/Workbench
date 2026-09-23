"use strict";

const crypto = require("node:crypto");

function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => (
    `${JSON.stringify(key)}:${canonical(value[key])}`
  )).join(",")}}`;
}

function contentId(prefix, value, excluded) {
  const material = structuredClone(value);
  delete material[excluded];
  return `${prefix}:sha256:${crypto.createHash("sha256")
    .update(`${canonical(material)}\n`).digest("hex")}`;
}

function baseHome() {
  return {
    format: "workbench-workspace-home-v1",
    schema_version: 1,
    read_only: true,
    workspace: {
      requested_path: "/workspace",
      root: "/workspace",
      display_name: "Example Mod",
      kind: "cleanroom-mod",
      recognition: "bounded",
    },
    status: { status: "attention", blockers: 0, warnings: 1, information: 0 },
    repository: {
      state: "observed", root: "/workspace", branch: "main", head: "a".repeat(40),
      dirty: false, changes: { unstaged: 0, untracked: 0 },
    },
    context: {
      platform: {
        state: "observed", kind: "cleanroom", minecraft_version: "1.12.2",
        cleanroom_version: "0.3.0-alpha",
      },
      profile: {
        state: "unresolved", profile_family_id: null, selected_profile: null,
        platform_profile_id: null, maturity: null, stable_release: false,
        support_claimed: false,
      },
      build: { state: "observed", provider: "gradle", wrapper: { version: "8.7" } },
      mod_ids: ["example"],
      source_surfaces: { source: ["src/main/java"], resources: [], groovy: [], configuration: [] },
    },
    problems: [{
      id: "PROFILE_UNRESOLVED", severity: "warning", title: "No exact profile",
      detail: "No supported pack profile was selected.",
      repair: { action: "Select an exact profile.", command: null, mutates: false },
    }],
    actions: [{
      id: "workspace-health", title: "Check workspace health",
      purpose: "Inspect the current workspace.", available: true,
      argv: ["workbench", "doctor", "/workspace"], blockers: [], unavailable_reason: null,
    }, {
      id: "run-development-client", title: "Run a development client",
      purpose: "Start the exact development client.", available: false, argv: null,
      blockers: ["EXACT_RUN_PROFILE_REQUIRED"],
      unavailable_reason: "No exact development run profile was found.",
    }],
    gaps: [{ id: "generic-run", summary: "Generic run is not admitted." }],
    owner_records: {
      workspace_doctor_format: "workbench-project-intelligence-workspace-doctor-report-v1",
      exact_pack_context_format: null,
    },
    limitations: ["Home V1 is read-only."],
  };
}

function ownerReference(
  kind, ownerId, recordFormat, recordDigest, freshness = "current", identities = {},
) {
  const material = { kind, owner_id: ownerId, record_digest: recordDigest };
  return {
    id: contentId("owner-ref", material, "not-present"),
    kind,
    owner_id: ownerId,
    record_format: recordFormat,
    record_id: identities.record_id || null,
    session_id: identities.session_id || null,
    catalog_id: identities.catalog_id || null,
    record_revision: recordDigest,
    record_digest: recordDigest,
    bound_workspace_revision: `sha256:${"7".repeat(64)}`,
    freshness,
    integrity: freshness === "corrupt" ? "failed" : "verified",
    validation_problem: freshness === "corrupt" ? "OWNER_VALIDATION_FAILED" : null,
  };
}

function homeV2Fixture() {
  const base = baseHome();
  const workspaceIdentity = { root: "/workspace", kind: "cleanroom-mod" };
  const workspaceId = contentId("workspace", workspaceIdentity, "not-present");
  const workspaceRevision = `sha256:${"7".repeat(64)}`;
  const doctor = ownerReference(
    "workspace-context", "project-intelligence",
    "workbench-project-intelligence-workspace-doctor-report-v1",
    `sha256:${"8".repeat(64)}`,
  );
  const session = ownerReference(
    "work-session", "workbench-shell", "workbench-work-session-summary-v1",
    `sha256:${"9".repeat(64)}`, "current", {
      record_id: `work-session-record:sha256:${"2".repeat(64)}`,
      session_id: `work-session-v2-${"1".repeat(32)}`,
    },
  );
  const capabilityCatalog = ownerReference(
    "product-capability-catalog", "workbench-shell",
    "workbench-product-capability-catalog-v1",
    `sha256:${"a".repeat(64)}`, "current", {
      record_id: `workbench-product-capabilities:sha256:${"2".repeat(64)}`,
      catalog_id: `workbench-product-capabilities:sha256:${"2".repeat(64)}`,
    },
  );
  const catalogDigest = `sha256:${"b".repeat(64)}`;
  const capability = {
    capability_id: `capability:sha256:${"e".repeat(64)}`,
    capability_key: "doctor.inspect",
    title: "Inspect workspace",
    summary: "Inspect the exact workspace without provisioning.",
    authority: "Project Intelligence facts composed by Workbench Shell",
    risk: "writes-output",
    availability: "experimental",
    handler: { kind: "process", registered: true, executable: true },
    catalog_action: {
      action_digest: `sha256:${"c".repeat(64)}`,
      command_id: "doctor.inspect",
      suite_id: "doctor",
    },
    limitations: [],
  };
  const available = {
    id: "workspace-health",
    rank: 10,
    title: "Check workspace health",
    purpose: "Inspect the current workspace.",
    state: "available",
    argv: ["python3", "/suite/tools/workbench.py", "doctor", "/workspace"],
    blockers: [],
    unavailable_reason: null,
    owner_ref_ids: [doctor.id, capabilityCatalog.id].sort(),
    eligibility_binding: {
      workspace_revision: workspaceRevision,
      session_revision: null,
      capability_catalog_revision: capabilityCatalog.record_revision,
    },
    command_id: "doctor.inspect",
    catalog_digest: catalogDigest,
    action_digest: `sha256:${"c".repeat(64)}`,
    capability_id: capability.capability_id,
    capability_key: capability.capability_key,
    capability,
    availability_basis: {
      kind: "owner-context-resolution", scope: "workspace-context", owner_ref_id: doctor.id,
      owner_record_revision: doctor.record_revision, command_id: "doctor.inspect",
      capability_id: capability.capability_id, global_capability_effect: "retained-unmodified",
    },
    tool_inputs: [],
    next_safe_action: null,
    arguments: { workspace: "/workspace" },
    eligibility_digest: "pending",
  };
  available.eligibility_digest = contentId("", available, "eligibility_digest")
    .replace(":sha256:", "sha256:");
  const unavailable = {
    id: "run-development-client",
    rank: 60,
    title: "Run a development client",
    purpose: "Start the exact development client.",
    state: "unavailable",
    argv: null,
    blockers: ["EXACT_RUN_PROFILE_REQUIRED"],
    unavailable_reason: "No exact development run profile was found.",
    owner_ref_ids: [doctor.id, session.id].sort(),
    eligibility_binding: {
      workspace_revision: workspaceRevision,
      session_revision: session.record_revision,
      capability_catalog_revision: null,
    },
    command_id: null,
    catalog_digest: catalogDigest,
    action_digest: null,
    capability_id: null,
    capability_key: null,
    capability: null,
    availability_basis: {
      kind: "base-home", scope: "base-home", owner_ref_id: doctor.id,
      owner_record_revision: doctor.record_revision, command_id: null,
      capability_id: null, global_capability_effect: "not-applicable",
    },
    tool_inputs: [],
    next_safe_action: null,
    arguments: null,
    eligibility_digest: "pending",
  };
  unavailable.eligibility_digest = contentId("", unavailable, "eligibility_digest")
    .replace(":sha256:", "sha256:");
  const value = {
    format: "workbench-workspace-home-v2",
    schema_version: 2,
    home_id: "pending",
    operation: "open",
    read_only: true,
    local_state_effect: "none",
    workspace: {
      ...base.workspace,
      workspace_id: workspaceId,
      workspace_revision: workspaceRevision,
      source_revision: `git:${base.repository.head}`,
      dirty_fingerprint: `sha256:${"d".repeat(64)}`,
    },
    status: {
      state: "attention", base_home_state: "attention", blockers: 0, warnings: 2, information: 0,
    },
    freshness: { workspace: "current", adoption: "not-applicable", session: "current", capability_catalog: "current" },
    owner_records: [doctor, capabilityCatalog, session].sort((left, right) => (
      `${left.kind}\0${left.owner_id}\0${left.id}`.localeCompare(`${right.kind}\0${right.owner_id}\0${right.id}`)
    )),
    session: {
      state: "available", owner_ref_id: session.id,
      record_id: session.record_id,
      session_id: session.session_id, catalog_id: null,
      record_revision: session.record_revision, freshness: "current", reason: null,
    },
    capability_catalog: {
      state: "available", owner_ref_id: capabilityCatalog.id,
      record_id: capabilityCatalog.record_id,
      session_id: null,
      catalog_id: capabilityCatalog.catalog_id,
      record_revision: capabilityCatalog.record_revision, freshness: "current", reason: null,
    },
    jobs: [available, unavailable],
    catalog: { format_version: "workbench-live-console-command-catalog-v2", catalog_digest: catalogDigest },
    new_project: {
      state: "unavailable", admitted_kinds: [], owner_ref_ids: [],
      blockers: ["OWNER_ADMITTED_NEW_KIND_ABSENT"],
      reason: "No owner-admitted frozen kind exists.",
      next_safe_action: "Admit an exact owner record through boundary change control.",
    },
    adoption: {
      state: "unadopted", binding_id: null, session_id: null, state_revision: null,
      adopted_workspace_id: null, adopted_workspace_root: null,
      freshness: "not-applicable", stale_reasons: [], recovery_state: "none",
      recovery_reasons: [],
      interrupted_write_count: 0,
    },
    problems: [{
      id: "OWNER_ADMITTED_NEW_KIND_ABSENT", severity: "warning",
      detail: "No owner-backed new-project kind is frozen.",
    }],
    base_home: base,
    limitations: [
      "Home V2 is a projection; owner records remain authoritative.",
      "Open performs no source or runtime mutation.",
    ],
  };
  value.home_id = contentId("workspace-home", value, "home_id");
  return value;
}

function availableHomeV2Fixture() {
  const value = homeV2Fixture();
  const owner = ownerReference(
    "new-project-construction",
    "cleanroom-platform-profile",
    "workbench-cleanroom-mod-construction-owner-v2",
    `sha256:${"f".repeat(64)}`,
    "current",
    {
      record_id: `workbench-cleanroom-mod-construction-owner:sha256:${"6".repeat(64)}`,
    },
  );
  value.owner_records.push(owner);
  value.owner_records.sort((left, right) => (
    `${left.kind}\0${left.owner_id}\0${left.id}`.localeCompare(
      `${right.kind}\0${right.owner_id}\0${right.id}`,
    )
  ));
  value.new_project = {
    state: "available",
    admitted_kinds: ["workbench-new-project-kind:cleanroom-mod"],
    owner_ref_ids: [owner.id],
    blockers: [],
    reason: "The Cleanroom profile owns one exact fresh-project constructor.",
    next_safe_action: "workbench new cleanroom-mod preview --help",
  };
  value.home_id = contentId("workspace-home", value, "home_id");
  return value;
}

module.exports = {
  availableHomeV2Fixture,
  contentId,
  homeV2Fixture,
};
