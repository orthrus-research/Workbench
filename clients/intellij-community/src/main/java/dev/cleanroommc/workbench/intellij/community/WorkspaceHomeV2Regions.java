package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.List;

/** Six stable native Home regions composed only from strict owner projections. */
final class WorkspaceHomeV2Regions {
    static final List<Definition> DEFINITIONS = List.of(
            new Definition("workspace", "Workspace"),
            new Definition("exact-environment", "Exact Environment"),
            new Definition("support-limitations", "Support and Limitations"),
            new Definition("active-work", "Active Work"),
            new Definition("recovery-required", "Recovery Required"),
            new Definition("recommended-jobs", "Recommended Jobs")
    );

    private WorkspaceHomeV2Regions() {
    }

    static @NotNull List<Region> compose(
            @NotNull WorkspaceHomeV2 home,
            @Nullable WorkSessionV2.Status status,
            @Nullable WorkSessionV2.Timeline timeline,
            @Nullable WorkSessionV2.RecoveryPreview recovery
    ) {
        String expectedSessionId = home.session().sessionId();
        if (status != null) {
            require(expectedSessionId != null && expectedSessionId.equals(status.sessionId()),
                    "status surface session identity differs from Workspace Home V2");
            require(home.session().recordId().equals(status.sessionRecordId()),
                    "status surface session record identity differs from Workspace Home V2");
        }
        if (timeline != null) {
            require(expectedSessionId != null && expectedSessionId.equals(timeline.sessionId()),
                    "timeline surface session identity differs from Workspace Home V2");
            if (status != null) {
                for (WorkSessionV2.Event event : timeline.events()) {
                    require(status.sessionRecordId().equals(event.sessionRecordId())
                                    && status.task().taskId().equals(event.taskId()),
                            "timeline surface owner identities differ from Work Session status");
                }
            }
        }
        if (recovery != null) {
            require(expectedSessionId != null && expectedSessionId.equals(recovery.sessionId()),
                    "recovery surface session identity differs from Workspace Home V2");
            require(home.session().recordId().equals(recovery.sessionRecordId()),
                    "recovery surface session record identity differs from Workspace Home V2");
        }

        List<Region> regions = new ArrayList<>();
        regions.add(new Region(DEFINITIONS.get(0), List.of(
                row("home-id", "Home ID", home.homeId(), null),
                row("workspace-id", "Workspace ID", home.workspace().workspaceId(), null),
                row("workspace-revision", "Workspace revision",
                        home.workspace().workspaceRevision(), null),
                row("workspace-root", "Workspace root", home.workspace().root(), null),
                row("workspace-kind", "Project kind", home.workspace().kind(), null),
                row("home-state", "Home state", home.status().state(), null)
        )));
        regions.add(new Region(DEFINITIONS.get(1), List.of(
                row("capability-catalog-id", "Capability catalog ID",
                        value(home.capabilityCatalog().capabilityCatalogId()), null),
                row("capability-catalog-freshness", "Capability catalog freshness",
                        home.capabilityCatalog().freshness(), null),
                row("catalog-id", "Catalog ID", home.catalogDigest(), null),
                row("platform", "Platform", context(home, "platform"), null),
                row("profile", "Profile", context(home, "profile"), null),
                row("build", "Build", context(home, "build"), null)
        )));
        List<Row> support = new ArrayList<>();
        WorkspaceHomeV2.NewProject newProject = home.newProject();
        support.add(row(
                "new-project",
                "New project",
                newProject.state() + " · " + newProject.reason()
                        + " · next=" + newProject.nextSafeAction(),
                newProject
        ));
        for (WorkspaceHomeV2.Problem problem : home.problems()) {
            support.add(row("problem:" + problem.id(), problem.id(), problem.detail(), problem));
        }
        for (int index = 0; index < home.limitations().size(); index++) {
            support.add(row("limitation:" + index, "Limitation", home.limitations().get(index), null));
        }
        regions.add(new Region(DEFINITIONS.get(2), support));

        List<Row> active = new ArrayList<>();
        active.add(row("session-id", "Session ID", value(expectedSessionId), null));
        active.add(row("session-record-id", "Session record ID",
                value(home.session().recordId()), null));
        active.add(row("session-freshness", "Session freshness",
                home.session().freshness(), null));
        if (status != null) {
            active.add(row("summary-id", "Session summary ID", status.summaryId(), null));
            active.add(row("task-id", "Task ID", status.task().taskId(), null));
            active.add(row("lifecycle", "Lifecycle", status.lifecycle(), null));
            active.add(row("latest-sequence", "Latest sequence",
                    Integer.toString(status.latestSequence()), null));
            for (WorkSessionV2.OwnerReference reference : status.ownerReferences()) {
                active.add(row("owner:" + reference.recordId(), reference.recordKind(),
                        reference.recordId(), reference));
            }
            for (WorkSessionV2.Action action : status.nextActions()) {
                active.add(row("next-action:" + action.actionId(), "Next action",
                        action.actionId() + " · " + action.availability(), action));
            }
        }
        if (timeline != null) {
            active.add(row("timeline-integrity", "Timeline integrity",
                    timeline.integrity().state(), null));
            active.add(row("timeline-next-sequence", "Timeline next sequence",
                    Integer.toString(timeline.nextSequence()), null));
            active.add(row("timeline-has-more", "Timeline has more",
                    Boolean.toString(timeline.hasMore()), null));
            for (WorkSessionV2.Event event : timeline.events()) {
                active.add(row("event:" + event.eventId(),
                        "Event #" + event.sequence() + " · " + event.kind(),
                        event.lifecycle() + " · " + event.eventId(), event));
            }
        }
        regions.add(new Region(DEFINITIONS.get(3), active));

        List<Row> recoveryRows = new ArrayList<>();
        recoveryRows.add(row("adoption-state", "Adoption recovery",
                home.adoption().recoveryState(), null));
        for (int index = 0; index < home.adoption().recoveryReasons().size(); index++) {
            recoveryRows.add(row("adoption-reason:" + index,
                    "Adoption recovery reason",
                    home.adoption().recoveryReasons().get(index), null));
        }
        if (recovery != null) {
            recoveryRows.add(row("required", "Recovery required",
                    Boolean.toString(recovery.required()), null));
            recoveryRows.add(row("automatic", "Automatic", "false", null));
            recoveryRows.add(row("reason", "Reason", value(recovery.reason()), null));
            for (WorkSessionV2.Action action : recovery.safeActions()) {
                recoveryRows.add(row("safe-action:" + action.actionId(), "Safe action",
                        action.actionId() + " · " + action.availability(), action));
            }
            for (WorkSessionV2.OwnerReference reference : recovery.ownerReferences()) {
                recoveryRows.add(row("owner:" + reference.recordId(), reference.recordKind(),
                        reference.recordId(), reference));
            }
        } else if (status != null && status.recovery() != null) {
            recoveryRows.add(row("state", "Recovery state", status.recovery().state(), null));
            recoveryRows.add(row("reason", "Reason", status.recovery().reason(), null));
            for (String actionId : status.recovery().safeActionIds()) {
                recoveryRows.add(row("safe-action:" + actionId, "Safe action ID", actionId, null));
            }
        } else {
            recoveryRows.add(row("state", "Recovery state", home.session().state(), null));
        }
        regions.add(new Region(DEFINITIONS.get(4), recoveryRows));

        List<Row> jobs = new ArrayList<>();
        int ordinal = 1;
        for (WorkspaceHomeV2.Job job : home.jobs()) {
            String value = job.state();
            if (job.capability() != null) {
                JsonObject capability = job.capability();
                JsonObject handler = capability.getAsJsonObject("handler");
                List<String> axes = new ArrayList<>(List.of(
                        value,
                        "basis=" + job.availabilityBasis().kind()
                                + "/" + job.availabilityBasis().scope(),
                        "global=" + job.availabilityBasis().globalCapabilityEffect(),
                        "availability=" + capability.get("availability").getAsString(),
                        "authority=" + capability.get("authority").getAsString(),
                        "handler=" + handler.get("kind").getAsString() + "/"
                                + (handler.get("executable").getAsBoolean()
                                ? "executable" : "non-executable")
                ));
                capability.getAsJsonArray("limitations").forEach(limitation ->
                        axes.add("limitation=" + limitation.getAsString()));
                for (WorkspaceHomeV2.ToolInput tool : job.toolInputs()) {
                    axes.add("tool=" + tool.kind() + ":" + tool.sha256());
                }
                if (job.nextSafeAction() != null) {
                    axes.add("next=" + job.nextSafeAction());
                }
                value = String.join(" · ", axes);
            } else {
                List<String> axes = new ArrayList<>(List.of(
                        value,
                        "basis=" + job.availabilityBasis().kind()
                                + "/" + job.availabilityBasis().scope(),
                        "global=" + job.availabilityBasis().globalCapabilityEffect()
                ));
                for (WorkspaceHomeV2.ToolInput tool : job.toolInputs()) {
                    axes.add("tool=" + tool.kind() + ":" + tool.sha256());
                }
                if (job.nextSafeAction() != null) {
                    axes.add("next=" + job.nextSafeAction());
                }
                value = String.join(" · ", axes);
            }
            jobs.add(row("job:" + job.id(), ordinal + ". " + job.title(), value, job));
            ordinal++;
        }
        regions.add(new Region(DEFINITIONS.get(5), jobs));
        return List.copyOf(regions);
    }

    private static @NotNull String context(
            @NotNull WorkspaceHomeV2 home, @NotNull String key
    ) {
        return home.context().containsKey(key) ? home.context().get(key).toString() : "unavailable";
    }

    private static @NotNull String value(@Nullable String value) {
        return value == null ? "unavailable" : value;
    }

    private static @NotNull Row row(
            @NotNull String id,
            @NotNull String label,
            @NotNull String value,
            @Nullable Object ownerValue
    ) {
        return new Row(id, label, value, ownerValue);
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) throw new IllegalArgumentException(message);
    }

    record Definition(@NotNull String key, @NotNull String label) { }

    record Region(@NotNull Definition definition, @NotNull List<Row> rows) {
        Region { rows = List.copyOf(rows); }
    }

    record Row(
            @NotNull String id, @NotNull String label, @NotNull String value,
            @Nullable Object ownerValue
    ) { }
}
