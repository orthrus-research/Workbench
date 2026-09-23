"use strict";

const REGION_DEFINITIONS = Object.freeze([
  Object.freeze({ key: "workspace", label: "Workspace", icon: "folder-opened" }),
  Object.freeze({ key: "exact-environment", label: "Exact Environment", icon: "symbol-namespace" }),
  Object.freeze({ key: "support-limitations", label: "Support and Limitations", icon: "shield" }),
  Object.freeze({ key: "active-work", label: "Active Work", icon: "pulse" }),
  Object.freeze({ key: "recovery-required", label: "Recovery Required", icon: "history" }),
  Object.freeze({ key: "recommended-jobs", label: "Recommended Jobs", icon: "run-all" }),
]);

function node(type, properties = {}) {
  return Object.freeze({ type, ...properties });
}

function valueText(value) {
  if (value === null || value === undefined) return "unavailable";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function fact(region, key, label, value) {
  return node("fact", {
    id: `workspace-home-v2:${region}:fact:${key}`,
    label,
    value: valueText(value),
  });
}

class WorkspaceHomeV2TreeProvider {
  constructor(vscode) {
    this.vscode = vscode;
    this.home = null;
    this.workSession = Object.freeze({ status: null, timeline: null, recovery: null });
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }

  dispose() {
    this.emitter.dispose();
  }

  reset() {
    this.home = null;
    this.workSession = Object.freeze({ status: null, timeline: null, recovery: null });
    this.emitter.fire(undefined);
  }

  setHome(home) {
    this.home = home;
    this.workSession = Object.freeze({ status: null, timeline: null, recovery: null });
    this.emitter.fire(undefined);
  }

  setWorkSession(value) {
    if (!this.home) throw new Error("Workspace Home V2 must load before its Work Session");
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Workspace Home V2 Work Session view must be an object");
    }
    const extra = Object.keys(value).filter((key) => !["status", "timeline", "recovery"].includes(key));
    if (extra.length) throw new Error(`Workspace Home V2 Work Session fields changed: ${extra.join(",")}`);
    const status = value.status || null;
    const timeline = value.timeline || null;
    const recovery = value.recovery || null;
    const expectedSessionId = this.home.session.session_id;
    if (!expectedSessionId) {
      throw new Error("Workspace Home V2 has no exact owner-returned session identity");
    }
    for (const [surface, projection] of Object.entries({ status, timeline, recovery })) {
      if (projection && projection.session_id !== expectedSessionId) {
        throw new Error(`${surface} surface session identity differs from Workspace Home V2`);
      }
    }
    if (status && status.session_record_id !== this.home.session.record_id) {
      throw new Error("status surface session record identity differs from Workspace Home V2");
    }
    if (recovery && recovery.session_record_id !== this.home.session.record_id) {
      throw new Error("recovery surface session record identity differs from Workspace Home V2");
    }
    if (timeline && status && timeline.events.some((event) => (
      event.session_record_id !== status.session_record_id
      || event.task_id !== status.task.task_id
    ))) {
      throw new Error("timeline surface owner identities differ from Work Session status");
    }
    this.workSession = Object.freeze({ status, timeline, recovery });
    this.emitter.fire(undefined);
  }

  getChildren(element) {
    if (!element) {
      // The empty state is rendered by package.json viewsWelcome entries so a
      // first-time user sees installation, setup, and Recipe Review actions
      // before any core-owned Home projection is requested.
      if (!this.home) return [];
      // These six identities are stable product regions. Their contents are
      // projections of owner-returned order and identities; the client does
      // not rank jobs or infer another support/recovery state.
      return REGION_DEFINITIONS.map((region) => node("region", {
        id: `workspace-home-v2-region:${region.key}`,
        region: region.key,
        label: region.label,
        icon: region.icon,
      }));
    }
    if (element.type === "region") return this.regionChildren(element.region);
    if (element.type === "job") {
      const capability = element.job.capability;
      const result = [node("job-inspect", {
        id: `workspace-home-v2:job-inspect:${element.job.id}`,
        job: element.job,
      }),
      fact("recommended-jobs", `${element.job.id}:basis`, "Availability basis",
        `${element.job.availability_basis.kind}/${element.job.availability_basis.scope}`),
      fact("recommended-jobs", `${element.job.id}:global-effect`, "Global capability effect",
        element.job.availability_basis.global_capability_effect),
      ...element.job.tool_inputs.map((input, index) => fact(
        "recommended-jobs", `${element.job.id}:tool:${index}`,
        `Tool input · ${input.kind}`, `${input.path} · ${input.sha256}`,
      )),
      ];
      if (element.job.next_safe_action) result.push(fact(
        "recommended-jobs", `${element.job.id}:next-safe-action`,
        "Next safe action", element.job.next_safe_action,
      ));
      if (capability) result.push(
        fact("recommended-jobs", `${element.job.id}:capability-id`, "Capability ID", capability.capability_id),
        fact("recommended-jobs", `${element.job.id}:availability`, "Capability availability", capability.availability),
        fact("recommended-jobs", `${element.job.id}:authority`, "Capability authority", capability.authority),
        fact("recommended-jobs", `${element.job.id}:handler`, "Handler",
          `${capability.handler.kind}/${capability.handler.executable ? "executable" : "non-executable"}`),
        ...capability.limitations.map((limitation, index) => fact(
          "recommended-jobs", `${element.job.id}:capability-limitation:${index}`,
          "Capability limitation", limitation,
        )),
      );
      if (element.job.state === "unavailable") result.push(
        fact("recommended-jobs", `${element.job.id}:reason`, "Reason", element.job.unavailable_reason),
        ...element.job.blockers.map((blocker, index) => fact(
          "recommended-jobs", `${element.job.id}:blocker:${index}`, "Blocker", blocker,
        )),
      );
      return result;
    }
    if (element.type === "timeline-event") {
      return [
        fact("active-work", `${element.event.event_id}:id`, "Event ID", element.event.event_id),
        fact("active-work", `${element.event.event_id}:record`, "Session record ID", element.event.session_record_id),
        fact("active-work", `${element.event.event_id}:task`, "Task ID", element.event.task_id),
        ...element.event.owner_record_refs.map((ownerRef) => node("owner-reference", {
          id: `workspace-home-v2:event-owner:${element.event.event_id}:${ownerRef.record_id}`,
          owner_reference: ownerRef,
        })),
        ...element.event.next_actions.map((action) => node("session-action", {
          id: `workspace-home-v2:event-action:${element.event.event_id}:${action.action_id}`,
          action,
          label: "Next action",
        })),
      ];
    }
    if (element.type === "problem") {
      return [fact("support-limitations", `${element.problem.id}:detail`, "Detail", element.problem.detail)];
    }
    return [];
  }

  regionChildren(region) {
    const home = this.home;
    if (region === "workspace") {
      return [
        fact(region, "home-id", "Home ID", home.home_id),
        fact(region, "workspace-id", "Workspace ID", home.workspace.workspace_id),
        fact(region, "workspace-revision", "Workspace revision", home.workspace.workspace_revision),
        fact(region, "root", "Workspace root", home.workspace.root),
        fact(region, "kind", "Project kind", home.workspace.kind),
        fact(region, "state", "Home state", home.status.state),
        fact(region, "adoption", "Adoption", home.adoption.state),
      ];
    }
    if (region === "exact-environment") {
      const context = home.base_home.context;
      return [
        fact(region, "capability-catalog-id", "Capability catalog ID", home.capability_catalog.catalog_id),
        fact(region, "capability-catalog-freshness", "Capability catalog freshness", home.capability_catalog.freshness),
        fact(region, "catalog-id", "Catalog ID", home.catalog.catalog_digest),
        fact(region, "platform", "Platform", context.platform),
        fact(region, "profile", "Profile", context.profile),
        fact(region, "build", "Build", context.build),
      ];
    }
    if (region === "support-limitations") {
      return [
        node("new-project", {
          id: "workspace-home-v2:new-project",
          label: "New project",
          value: home.new_project.state,
          detail: home.new_project.reason,
        }),
        ...home.problems.map((problem) => node("problem", {
          id: `workspace-home-v2:problem:${problem.id}`,
          problem,
        })),
        ...home.limitations.map((limitation, index) => node("limitation", {
          id: `workspace-home-v2:limitation:${index}`,
          value: limitation,
        })),
      ];
    }
    if (region === "active-work") {
      const status = this.workSession.status;
      const result = [
        fact(region, "session-id", "Session ID", home.session.session_id),
        fact(region, "session-record-id", "Session record ID", home.session.record_id),
        fact(region, "session-freshness", "Session freshness", home.session.freshness),
      ];
      if (!status) return result;
      result.push(
        fact(region, "summary-id", "Session summary ID", status.summary_id),
        fact(region, "task-id", "Task ID", status.task.task_id),
        fact(region, "lifecycle", "Lifecycle", status.lifecycle),
        fact(region, "latest-sequence", "Latest sequence", status.latest_sequence),
      );
      for (const ownerRef of status.owner_record_refs) {
        result.push(node("owner-reference", {
          id: `workspace-home-v2:active-work:owner:${ownerRef.record_id}`,
          owner_reference: ownerRef,
        }));
      }
      for (const action of status.next_actions) {
        result.push(node("session-action", {
          id: `workspace-home-v2:active-work:action:${action.action_id}`,
          action,
          label: "Next action",
        }));
      }
      result.push(
        node("work-session-operation", {
          id: "workspace-home-v2:session-operation:resume",
          operation: "resume",
          session_id: status.session_id,
          label: "Resume session through Workbench core…",
          detail: "Append exact VS Code navigation provenance; no owner action is executed.",
        }),
        node("work-session-operation", {
          id: "workspace-home-v2:session-operation:close",
          operation: "close",
          session_id: status.session_id,
          label: "Close session navigation through Workbench core…",
          detail: "Close navigation only; owner work and evidence are unchanged.",
        }),
      );
      const timeline = this.workSession.timeline;
      if (timeline) {
        result.push(
          fact(region, "timeline-integrity", "Timeline integrity", timeline.integrity.state),
          fact(region, "timeline-next-sequence", "Timeline next sequence", timeline.next_sequence),
          fact(region, "timeline-has-more", "Timeline has more", timeline.has_more),
          ...timeline.events.map((event) => node("timeline-event", {
            id: `workspace-home-v2:timeline-event:${event.event_id}`,
            event,
          })),
        );
      }
      return result;
    }
    if (region === "recovery-required") {
      const preview = this.workSession.recovery;
      const summaryRecovery = this.workSession.status?.recovery || null;
      const adoptionRows = [
        fact(region, "adoption-state", "Adoption recovery", home.adoption.recovery_state),
        ...home.adoption.recovery_reasons.map((reason, index) => fact(
          region, `adoption-reason:${index}`, "Adoption recovery reason", reason,
        )),
      ];
      if (preview) {
        return [
          ...adoptionRows,
          node("work-session-operation", {
            id: "workspace-home-v2:session-operation:recovery-preview",
            operation: "recovery-preview",
            session_id: preview.session_id,
            label: "Preview core-owned recovery…",
            detail: preview.required ? "Recovery is required." : "Recovery is not required.",
          }),
          node("work-session-operation", {
            id: "workspace-home-v2:session-operation:recovery-apply",
            operation: "recovery-apply",
            session_id: preview.session_id,
            label: "Apply core-owned recovery…",
            available: preview.required,
            detail: preview.required
              ? "Explicit apply will revalidate current owner custody."
              : "Workbench reports no required recovery; apply is unavailable.",
          }),
          fact(region, "required", "Recovery required", preview.required),
          fact(region, "automatic", "Automatic", preview.automatic),
          fact(region, "reason", "Reason", preview.reason),
          ...preview.safe_actions.map((action) => node("recovery-action", {
            id: `workspace-home-v2:recovery-action:${action.action_id}`,
            action,
          })),
          ...preview.owner_record_refs.map((ownerRef) => node("owner-reference", {
            id: `workspace-home-v2:recovery-owner:${ownerRef.record_id}`,
            owner_reference: ownerRef,
          })),
        ];
      }
      if (summaryRecovery) {
        return [
          ...adoptionRows,
          fact(region, "state", "Recovery state", summaryRecovery.state),
          fact(region, "reason", "Reason", summaryRecovery.reason),
          ...summaryRecovery.safe_action_ids.map((actionId, index) => fact(
            region, `safe-action:${index}`, "Safe action ID", actionId,
          )),
        ];
      }
      return [...adoptionRows, fact(region, "state", "Recovery state", home.session.state)];
    }
    if (region === "recommended-jobs") {
      // Owner order is part of Home V2. Never sort or promote these jobs here.
      return home.jobs.map((job, index) => node("job", {
        id: `workspace-home-v2:job:${job.id}`,
        job,
        ordinal: index + 1,
      }));
    }
    throw new Error(`unsupported Workspace Home V2 region: ${region}`);
  }

  getTreeItem(element) {
    const api = this.vscode;
    const none = api.TreeItemCollapsibleState.None;
    const collapsed = api.TreeItemCollapsibleState.Collapsed;
    let item;
    if (element.type === "load") {
      item = new api.TreeItem("Open this workspace", none);
      item.iconPath = new api.ThemeIcon("home");
      item.tooltip = "Load the installed core's strict Workspace Home V2 projection.";
      item.command = { command: "workbench.workspaceHome.open", title: "Open Workspace Home" };
    } else if (element.type === "region") {
      item = new api.TreeItem(element.label, collapsed);
      item.iconPath = new api.ThemeIcon(element.icon);
    } else if (element.type === "fact") {
      item = new api.TreeItem(element.label, none);
      item.description = element.value;
      item.tooltip = element.value;
    } else if (element.type === "job") {
      item = new api.TreeItem(`${element.ordinal}. ${element.job.title}`,
        element.job.state === "available" && !element.job.capability ? none : collapsed);
      item.description = element.job.state;
      item.contextValue = element.job.state === "available"
        ? "workbenchHomeV2JobAvailable" : "workbenchHomeV2JobUnavailable";
      item.iconPath = new api.ThemeIcon(element.job.state === "available" ? "pass-filled" : "lock");
      item.tooltip = element.job.state === "available"
        ? `${element.job.purpose}\nExact argv: ${JSON.stringify(element.job.argv)}\nEligibility: ${element.job.eligibility_digest}`
        : `${element.job.purpose}\n${element.job.unavailable_reason}`;
    } else if (element.type === "job-inspect") {
      item = new api.TreeItem("Inspect exact owner-returned job…", none);
      item.iconPath = new api.ThemeIcon("preview");
      item.contextValue = "workbenchHomeV2JobInspect";
      item.tooltip = "Home argv is a preview; execution requires fresh core-side session and catalog validation.";
      item.command = {
        command: "workbench.workspaceHome.inspectValue",
        title: "Inspect exact Workspace Home job",
        arguments: ["Workspace Home job", element.job],
      };
    } else if (element.type === "problem") {
      item = new api.TreeItem(element.problem.id, collapsed);
      item.description = element.problem.severity;
      item.iconPath = new api.ThemeIcon(element.problem.severity === "info" ? "info" : "warning");
    } else if (element.type === "limitation") {
      item = new api.TreeItem(element.value, none);
      item.iconPath = new api.ThemeIcon("info");
      item.tooltip = element.value;
    } else if (element.type === "new-project") {
      item = new api.TreeItem(element.label, none);
      item.description = element.value;
      item.iconPath = new api.ThemeIcon("lock");
      item.tooltip = element.detail;
    } else if (element.type === "owner-reference") {
      item = new api.TreeItem(element.owner_reference.record_kind, none);
      item.description = element.owner_reference.record_id;
      item.contextValue = "workbenchHomeV2OwnerReference";
      item.iconPath = new api.ThemeIcon("references");
      item.tooltip = `${element.owner_reference.owner_id}\n${element.owner_reference.uri}`;
      item.command = {
        command: "workbench.workspaceHome.inspectOwner",
        title: "Inspect exact owner reference",
        arguments: [element.owner_reference],
      };
    } else if (element.type === "recovery-action") {
      item = new api.TreeItem(element.action.action_id, none);
      item.description = element.action.availability;
      item.contextValue = "workbenchHomeV2RecoveryAction";
      item.iconPath = new api.ThemeIcon("history");
      item.tooltip = `Owner: ${element.action.owner_id}\nDigest: ${element.action.action_digest}`;
      item.command = {
        command: "workbench.workspaceHome.inspectValue",
        title: "Inspect recovery action",
        arguments: ["Recovery action", element.action],
      };
    } else if (element.type === "session-action") {
      item = new api.TreeItem(`${element.label}: ${element.action.action_id}`, none);
      item.description = element.action.availability;
      item.contextValue = "workbenchHomeV2SessionAction";
      item.iconPath = new api.ThemeIcon("preview");
      item.tooltip = `Owner: ${element.action.owner_id}\nMutation budget: ${element.action.mutation_budget}`;
      item.command = {
        command: "workbench.workspaceHome.inspectValue",
        title: "Inspect Work Session action",
        arguments: [element.label, element.action],
      };
    } else if (element.type === "timeline-event") {
      item = new api.TreeItem(
        `#${element.event.sequence} · ${element.event.kind}`,
        collapsed,
      );
      item.description = element.event.lifecycle;
      item.contextValue = "workbenchHomeV2TimelineEvent";
      item.iconPath = new api.ThemeIcon("history");
      item.tooltip = element.event.event_id;
      item.command = {
        command: "workbench.workspaceHome.inspectValue",
        title: "Inspect Work Session event",
        arguments: ["Work Session timeline event", element.event],
      };
    } else if (element.type === "work-session-operation") {
      item = new api.TreeItem(element.label, none);
      item.description = element.available === false ? "unavailable" : "public core port";
      item.contextValue = "workbenchHomeV2SessionOperation";
      item.iconPath = new api.ThemeIcon(
        element.available === false ? "lock" : element.operation === "recovery-apply" ? "history" : "play",
      );
      item.tooltip = element.detail;
      item.command = {
        command: "workbench.workspaceHome.inspectValue",
        title: "Open Work Session operation",
        arguments: ["Work Session operation", {
          operation: element.operation,
          session_id: element.session_id,
          available: element.available !== false,
        }],
      };
    } else {
      throw new Error(`unsupported Workspace Home V2 tree element: ${element.type}`);
    }
    item.id = element.id;
    return item;
  }
}

module.exports = { REGION_DEFINITIONS, WorkspaceHomeV2TreeProvider };
