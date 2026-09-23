package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict parsers for Work Session status, timeline, and recovery projections. */
public final class WorkSessionV2 {
    public static final String SUMMARY_FORMAT = "workbench-work-session-summary-v1";
    public static final String EVENT_FORMAT = "workbench-work-session-event-v1";
    public static final String TIMELINE_FORMAT = "workbench-work-session-timeline-v1";
    public static final String RECOVERY_FORMAT = "workbench-work-session-recovery-preview-v1";
    public static final int MAX_STATUS_BYTES = 8 * 1024 * 1024;
    public static final int MAX_TIMELINE_BYTES = 48 * 1024 * 1024;
    private static final Pattern SESSION_ID = Pattern.compile(
            "^work-session-v2-[0-9a-f]{32}$"
    );
    private static final Pattern RECORD_ID = Pattern.compile(
            "^work-session-record:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern SUMMARY_ID = Pattern.compile(
            "^work-session-summary:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern EVENT_ID = Pattern.compile(
            "^work-session-event:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern PLAIN_ID = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}$"
    );
    private static final Pattern TIMESTAMP = Pattern.compile(
            "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                    + "(?:\\.[0-9]{1,9})?Z$"
    );
    private static final Set<String> LIFECYCLES = Set.of(
            "discovered", "ready", "attention", "blocked", "running", "complete",
            "failed", "cancelled", "incomplete", "recoverable"
    );
    private static final Set<String> AVAILABILITIES = Set.of(
            "available", "unavailable", "experimental", "blocked", "stale", "unknown"
    );
    private static final Set<String> MUTATION_BUDGETS = Set.of(
            "read-only", "writes-output", "mutating", "destructive"
    );

    private WorkSessionV2() {
    }

    public static @NotNull Status parseStatus(@NotNull String json) {
        JsonObject root = ProductSpineJson.parse(
                json, "Work Session status", MAX_STATUS_BYTES, 150_000
        );
        ProductSpineJson.exact(root, "Work Session status",
                "format_version", "summary_id", "session_id", "session_record_id", "task",
                "workspace", "identities", "lifecycle", "created_at", "updated_at",
                "latest_sequence", "latest_event_id", "event_count", "closed", "frontend_ids",
                "owner_record_refs", "result_refs", "problems", "next_actions", "recovery",
                "limitations", "unknowns", "integrity_state", "integrity_problem_ids");
        ProductSpineJson.require(SUMMARY_FORMAT.equals(ProductSpineJson.string(
                        root, "format_version", "Work Session status")),
                "Workbench returned an unsupported Work Session status format");
        String summaryId = ProductSpineJson.patterned(
                root, "summary_id", "Work Session status", SUMMARY_ID
        );
        CanonicalJson.verifyObjectIdentity(root, "summary_id", "work-session-summary", false);
        String sessionId = sessionId(root, "session_id", "Work Session status");
        String recordId = ProductSpineJson.patterned(
                root, "session_record_id", "Work Session status", RECORD_ID
        );
        Task task = task(ProductSpineJson.object(root, "task", "Work Session status"));
        Workspace workspace = workspace(
                ProductSpineJson.object(root, "workspace", "Work Session status")
        );
        validateIdentities(ProductSpineJson.object(root, "identities", "Work Session status"));
        String lifecycle = ProductSpineJson.member(
                root, "lifecycle", "Work Session status", LIFECYCLES
        );
        timestamp(root, "created_at", "Work Session status");
        timestamp(root, "updated_at", "Work Session status");
        int latestSequence = ProductSpineJson.integer(
                root, "latest_sequence", "Work Session status", -1, 99_999
        );
        int eventCount = ProductSpineJson.integer(
                root, "event_count", "Work Session status", 0, 100_000
        );
        ProductSpineJson.require(
                (eventCount == 0 && latestSequence == -1)
                        || (eventCount > 0 && latestSequence == eventCount - 1),
                "Work Session status event counters are inconsistent"
        );
        String latestEventId = ProductSpineJson.nullablePatterned(
                root, "latest_event_id", "Work Session status", EVENT_ID
        );
        ProductSpineJson.require((eventCount == 0) == (latestEventId == null),
                "Work Session status latest event identity is inconsistent");
        boolean closed = ProductSpineJson.bool(root, "closed", "Work Session status");
        List<String> frontendIds = identifiers(
                ProductSpineJson.strings(root, "frontend_ids", "Work Session status", 256),
                "Work Session frontend ID"
        );
        List<OwnerReference> ownerReferences = ownerReferences(
                root, "owner_record_refs", "Work Session status"
        );
        validateResults(root, "result_refs", "Work Session status");
        validateProblems(root, "problems", "Work Session status");
        List<Action> nextActions = actions(root, "next_actions", "Work Session status");
        Recovery recovery = recovery(root.get("recovery"), "Work Session status recovery");
        validateRecoveryActions(recovery, nextActions, lifecycle);
        ProductSpineJson.strings(root, "limitations", "Work Session status", 128);
        ProductSpineJson.strings(root, "unknowns", "Work Session status", 128);
        String integrityState = ProductSpineJson.member(
                root, "integrity_state", "Work Session status",
                Set.of("verified", "recoverable", "corrupt")
        );
        List<String> integrityProblemIds = identifiers(
                ProductSpineJson.strings(
                        root, "integrity_problem_ids", "Work Session status", 256
                ),
                "Work Session integrity problem ID"
        );
        return new Status(
                json, summaryId, sessionId, recordId, task, workspace, lifecycle,
                latestSequence, eventCount, latestEventId, closed, frontendIds,
                ownerReferences, nextActions, recovery, integrityState, integrityProblemIds
        );
    }

    public static @NotNull Timeline parseTimeline(@NotNull String json) {
        JsonObject root = ProductSpineJson.parse(
                json, "Work Session timeline", MAX_TIMELINE_BYTES, 500_000
        );
        ProductSpineJson.exact(root, "Work Session timeline",
                "format_version", "session_id", "after_sequence", "events",
                "next_sequence", "has_more", "integrity");
        ProductSpineJson.require(TIMELINE_FORMAT.equals(ProductSpineJson.string(
                        root, "format_version", "Work Session timeline")),
                "Workbench returned an unsupported Work Session timeline format");
        String sessionId = sessionId(root, "session_id", "Work Session timeline");
        int afterSequence = ProductSpineJson.integer(
                root, "after_sequence", "Work Session timeline", -1, 99_999
        );
        JsonArray rows = ProductSpineJson.array(
                root, "events", "Work Session timeline", 4096
        );
        List<Event> events = new ArrayList<>();
        String previous = null;
        for (int index = 0; index < rows.size(); index++) {
            int expectedSequence = afterSequence + index + 1;
            String expectedPrevious = index > 0 || afterSequence == -1 ? previous : null;
            boolean bindPrevious = index > 0 || afterSequence == -1;
            Event event = event(
                    ProductSpineJson.object(rows.get(index), "Work Session timeline event"),
                    sessionId, expectedSequence, expectedPrevious, bindPrevious
            );
            events.add(event);
            previous = event.eventId();
        }
        int nextSequence = ProductSpineJson.integer(
                root, "next_sequence", "Work Session timeline", -1, 99_999
        );
        int expectedNext = events.isEmpty() ? afterSequence : events.getLast().sequence();
        ProductSpineJson.require(nextSequence == expectedNext,
                "Work Session timeline cursor is inconsistent");
        boolean hasMore = ProductSpineJson.bool(root, "has_more", "Work Session timeline");
        Integrity integrity = integrity(
                ProductSpineJson.object(root, "integrity", "Work Session timeline")
        );
        return new Timeline(
                json, sessionId, afterSequence, List.copyOf(events), nextSequence, hasMore, integrity
        );
    }

    public static @NotNull RecoveryPreview parseRecovery(@NotNull String json) {
        JsonObject root = ProductSpineJson.parse(
                json, "Work Session recovery preview", MAX_STATUS_BYTES, 150_000
        );
        ProductSpineJson.exact(root, "Work Session recovery preview",
                "format_version", "session_id", "session_record_id", "latest_sequence",
                "required", "automatic", "reason", "owner_record_refs", "owner_resolution",
                "owner_resolution_problems", "safe_actions", "integrity");
        ProductSpineJson.require(RECOVERY_FORMAT.equals(ProductSpineJson.string(
                        root, "format_version", "Work Session recovery preview")),
                "Workbench returned an unsupported Work Session recovery format");
        String sessionId = sessionId(root, "session_id", "Work Session recovery preview");
        String recordId = ProductSpineJson.patterned(
                root, "session_record_id", "Work Session recovery preview", RECORD_ID
        );
        int latestSequence = ProductSpineJson.integer(
                root, "latest_sequence", "Work Session recovery preview", -1, 99_999
        );
        boolean required = ProductSpineJson.bool(
                root, "required", "Work Session recovery preview"
        );
        ProductSpineJson.require(!ProductSpineJson.bool(
                        root, "automatic", "Work Session recovery preview"),
                "Work Session recovery must never be automatic");
        String reason = ProductSpineJson.nullableString(
                root, "reason", "Work Session recovery preview"
        );
        List<OwnerReference> references = ownerReferences(
                root, "owner_record_refs", "Work Session recovery preview"
        );
        String resolution = ProductSpineJson.member(
                root, "owner_resolution", "Work Session recovery preview",
                Set.of("not-requested", "partial", "verified")
        );
        validateResolutionProblems(root);
        List<Action> safeActions = actions(
                root, "safe_actions", "Work Session recovery preview"
        );
        Integrity integrity = integrity(
                ProductSpineJson.object(root, "integrity", "Work Session recovery preview")
        );
        ProductSpineJson.require(!"corrupt".equals(integrity.journalState()) || required,
                "Corrupt Work Session state was hidden from recovery");
        return new RecoveryPreview(
                json, sessionId, recordId, latestSequence, required, false, reason,
                references, resolution, safeActions, integrity
        );
    }

    static @NotNull String requireSessionId(@NotNull String value) {
        ProductSpineJson.require(SESSION_ID.matcher(value).matches(),
                "Work Session selector is not one exact session ID");
        return value;
    }

    private static @NotNull Event event(
            @NotNull JsonObject row,
            @NotNull String expectedSessionId,
            int expectedSequence,
            @Nullable String expectedPrevious,
            boolean bindPrevious
    ) {
        String label = "Work Session event";
        ProductSpineJson.exact(row, label,
                "format_version", "event_id", "session_id", "session_record_id", "sequence",
                "previous_event_id", "occurred_at", "frontend", "kind", "task_id", "lifecycle",
                "stage", "action", "result_refs", "problems", "next_actions", "owner_record_refs",
                "recovery", "workspace_observation", "closed", "message", "limitations", "unknowns");
        ProductSpineJson.require(EVENT_FORMAT.equals(ProductSpineJson.string(
                        row, "format_version", label)),
                "Workbench returned an unsupported Work Session event format");
        String eventId = ProductSpineJson.patterned(row, "event_id", label, EVENT_ID);
        CanonicalJson.verifyObjectIdentity(row, "event_id", "work-session-event", false);
        String sessionId = sessionId(row, "session_id", label);
        ProductSpineJson.require(expectedSessionId.equals(sessionId),
                "Work Session event session ID changed");
        int sequence = ProductSpineJson.integer(row, "sequence", label, 0, 99_999);
        ProductSpineJson.require(sequence == expectedSequence,
                "Work Session event sequence changed");
        String previous = ProductSpineJson.nullablePatterned(
                row, "previous_event_id", label, EVENT_ID
        );
        ProductSpineJson.require(!bindPrevious || java.util.Objects.equals(previous, expectedPrevious),
                "Work Session event chain changed");
        String recordId = ProductSpineJson.patterned(
                row, "session_record_id", label, RECORD_ID
        );
        timestamp(row, "occurred_at", label);
        Frontend frontend = frontend(ProductSpineJson.object(row, "frontend", label));
        String kind = ProductSpineJson.patterned(row, "kind", label, PLAIN_ID);
        String taskId = ProductSpineJson.patterned(row, "task_id", label, PLAIN_ID);
        String lifecycle = ProductSpineJson.member(row, "lifecycle", label, LIFECYCLES);
        validateStage(row.get("stage"));
        validateNullableAction(row.get("action"), label + " action");
        validateResults(row, "result_refs", label);
        validateProblems(row, "problems", label);
        List<Action> nextActions = actions(row, "next_actions", label);
        List<OwnerReference> ownerReferences = ownerReferences(
                row, "owner_record_refs", label
        );
        Recovery recovery = recovery(row.get("recovery"), label + " recovery");
        validateRecoveryActions(recovery, nextActions, lifecycle);
        if (!row.get("workspace_observation").isJsonNull()) {
            workspace(ProductSpineJson.object(row.get("workspace_observation"), label + " workspace"));
        }
        ProductSpineJson.bool(row, "closed", label);
        ProductSpineJson.nullableString(row, "message", label);
        ProductSpineJson.strings(row, "limitations", label, 128);
        ProductSpineJson.strings(row, "unknowns", label, 128);
        return new Event(eventId, sessionId, recordId, sequence, previous, frontend, kind,
                taskId, lifecycle, ownerReferences, nextActions, recovery);
    }

    private static @NotNull Task task(@NotNull JsonObject row) {
        ProductSpineJson.exact(row, "Work Session task",
                "task_id", "owner_id", "label", "owner_record_ref");
        if (!row.get("owner_record_ref").isJsonNull()) {
            ownerReference(ProductSpineJson.object(
                    row.get("owner_record_ref"), "Work Session task owner reference"
            ), 0);
        }
        return new Task(
                ProductSpineJson.patterned(row, "task_id", "Work Session task", PLAIN_ID),
                ProductSpineJson.patterned(row, "owner_id", "Work Session task", PLAIN_ID),
                ProductSpineJson.nullableString(row, "label", "Work Session task")
        );
    }

    private static @NotNull Workspace workspace(@NotNull JsonObject row) {
        ProductSpineJson.exact(row, "Work Session workspace",
                "identity_id", "canonical_root", "root_uri", "source_revision",
                "dirty_fingerprint");
        String root = ProductSpineJson.string(row, "canonical_root", "Work Session workspace");
        ProductSpineJson.require(root.startsWith("/"),
                "Work Session workspace root must be absolute");
        return new Workspace(
                ProductSpineJson.patterned(row, "identity_id", "Work Session workspace", PLAIN_ID),
                root,
                ProductSpineJson.string(row, "root_uri", "Work Session workspace"),
                ProductSpineJson.nullablePatterned(
                        row, "source_revision", "Work Session workspace", PLAIN_ID),
                ProductSpineJson.nullablePatterned(
                        row, "dirty_fingerprint", "Work Session workspace", PLAIN_ID)
        );
    }

    private static void validateIdentities(@NotNull JsonObject row) {
        ProductSpineJson.exact(row, "Work Session identities",
                "core_id", "catalog_id", "host_adapter_id", "platform_profile_id",
                "pack_profile_id");
        for (String key : List.of("core_id", "catalog_id", "host_adapter_id")) {
            ProductSpineJson.patterned(row, key, "Work Session identities", PLAIN_ID);
        }
        ProductSpineJson.nullablePatterned(
                row, "platform_profile_id", "Work Session identities", PLAIN_ID
        );
        ProductSpineJson.nullablePatterned(
                row, "pack_profile_id", "Work Session identities", PLAIN_ID
        );
    }

    private static @NotNull List<OwnerReference> ownerReferences(
            @NotNull JsonObject parent, @NotNull String key, @NotNull String label
    ) {
        JsonArray rows = ProductSpineJson.array(parent, key, label, 256);
        List<OwnerReference> result = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            OwnerReference reference = ownerReference(
                    ProductSpineJson.object(rows.get(index), label + " owner reference"), index
            );
            ProductSpineJson.require(ids.add(reference.recordId()),
                    label + " repeats owner record " + reference.recordId());
            result.add(reference);
        }
        return List.copyOf(result);
    }

    private static @NotNull OwnerReference ownerReference(
            @NotNull JsonObject row, int index
    ) {
        String label = "Work Session owner reference " + index;
        ProductSpineJson.exact(row, label,
                "owner_id", "record_id", "record_kind", "uri", "digest",
                "last_verified_state", "verified_at");
        String uri = ProductSpineJson.string(row, "uri", label);
        ProductSpineJson.require(uri.contains(":"),
                "Work Session owner reference URI has no scheme");
        String verified = ProductSpineJson.nullableString(row, "verified_at", label);
        if (verified != null) {
            ProductSpineJson.require(TIMESTAMP.matcher(verified).matches(),
                    "Work Session owner verification time is invalid");
        }
        return new OwnerReference(
                ProductSpineJson.patterned(row, "owner_id", label, PLAIN_ID),
                ProductSpineJson.patterned(row, "record_id", label, PLAIN_ID),
                ProductSpineJson.patterned(row, "record_kind", label, PLAIN_ID),
                uri,
                ProductSpineJson.nullablePatterned(row, "digest", label, PLAIN_ID),
                ProductSpineJson.nullablePatterned(
                        row, "last_verified_state", label, PLAIN_ID),
                verified
        );
    }

    private static @NotNull List<Action> actions(
            @NotNull JsonObject parent, @NotNull String key, @NotNull String label
    ) {
        JsonArray rows = ProductSpineJson.array(parent, key, label, 128);
        List<Action> result = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            Action action = action(ProductSpineJson.object(rows.get(index), label + " action"));
            ProductSpineJson.require(ids.add(action.actionId()),
                    label + " repeats action " + action.actionId());
            result.add(action);
        }
        return List.copyOf(result);
    }

    private static @NotNull Action action(@NotNull JsonObject row) {
        ProductSpineJson.exact(row, "Work Session action",
                "action_id", "action_digest", "owner_id", "availability",
                "mutation_budget", "arguments");
        JsonObject arguments = ProductSpineJson.object(
                row, "arguments", "Work Session action"
        ).deepCopy();
        return new Action(
                ProductSpineJson.patterned(row, "action_id", "Work Session action", PLAIN_ID),
                ProductSpineJson.patterned(row, "action_digest", "Work Session action", PLAIN_ID),
                ProductSpineJson.patterned(row, "owner_id", "Work Session action", PLAIN_ID),
                ProductSpineJson.member(row, "availability", "Work Session action", AVAILABILITIES),
                ProductSpineJson.member(row, "mutation_budget", "Work Session action", MUTATION_BUDGETS),
                arguments
        );
    }

    private static void validateNullableAction(
            @NotNull JsonElement value, @NotNull String label
    ) {
        if (!value.isJsonNull()) {
            action(ProductSpineJson.object(value, label));
        }
    }

    private static @Nullable Recovery recovery(
            @NotNull JsonElement value, @NotNull String label
    ) {
        if (value.isJsonNull()) {
            return null;
        }
        JsonObject row = ProductSpineJson.object(value, label);
        ProductSpineJson.exact(row, label,
                "state", "reason", "owner_record_refs", "safe_action_ids");
        return new Recovery(
                ProductSpineJson.member(row, "state", label,
                        Set.of("required", "recovering", "resolved")),
                ProductSpineJson.string(row, "reason", label),
                ownerReferences(row, "owner_record_refs", label),
                identifiers(ProductSpineJson.strings(
                        row, "safe_action_ids", label, 128), "Work Session safe action ID")
        );
    }

    private static void validateRecoveryActions(
            @Nullable Recovery recovery,
            @NotNull List<Action> actions,
            @NotNull String lifecycle
    ) {
        if ("recoverable".equals(lifecycle)) {
            ProductSpineJson.require(recovery != null && "required".equals(recovery.state())
                            && !recovery.safeActionIds().isEmpty(),
                    "Recoverable Work Session state lacks exact safe actions");
        }
        if (recovery != null) {
            Set<String> actionIds = actions.stream().map(Action::actionId)
                    .collect(java.util.stream.Collectors.toSet());
            ProductSpineJson.require(actionIds.containsAll(recovery.safeActionIds()),
                    "Work Session recovery references an absent safe action");
        }
    }

    private static @NotNull Integrity integrity(@NotNull JsonObject row) {
        ProductSpineJson.exact(row, "Work Session integrity",
                "state", "journal_state", "summary_state", "problems");
        String state = ProductSpineJson.member(row, "state", "Work Session integrity",
                Set.of("verified", "summary-regenerated", "corrupt"));
        String journal = ProductSpineJson.member(
                row, "journal_state", "Work Session integrity",
                Set.of("verified", "recoverable", "corrupt")
        );
        String summary = ProductSpineJson.member(
                row, "summary_state", "Work Session integrity",
                Set.of("verified", "summary-regenerated")
        );
        validateProblems(row, "problems", "Work Session integrity");
        ProductSpineJson.require(("corrupt".equals(journal)) == ("corrupt".equals(state))
                        && ("corrupt".equals(journal) || state.equals(summary)),
                "Work Session integrity projection is internally inconsistent");
        return new Integrity(state, journal, summary);
    }

    private static void validateResults(
            @NotNull JsonObject parent, @NotNull String key, @NotNull String label
    ) {
        JsonArray rows = ProductSpineJson.array(parent, key, label, 256);
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            JsonObject row = ProductSpineJson.object(rows.get(index), label + " result");
            ProductSpineJson.exact(row, label + " result", "result_id", "owner_record_ref");
            String id = ProductSpineJson.patterned(row, "result_id", label + " result", PLAIN_ID);
            ProductSpineJson.require(ids.add(id), label + " repeats result " + id);
            ownerReference(ProductSpineJson.object(
                    row, "owner_record_ref", label + " result"), index
            );
        }
    }

    private static void validateProblems(
            @NotNull JsonObject parent, @NotNull String key, @NotNull String label
    ) {
        JsonArray rows = ProductSpineJson.array(parent, key, label, 256);
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            JsonObject row = ProductSpineJson.object(rows.get(index), label + " problem");
            ProductSpineJson.exact(row, label + " problem",
                    "problem_id", "code", "severity", "message", "affected_identity",
                    "evidence_refs", "next_action_id");
            String id = ProductSpineJson.patterned(row, "problem_id", label + " problem", PLAIN_ID);
            ProductSpineJson.require(ids.add(id), label + " repeats problem " + id);
            ProductSpineJson.patterned(row, "code", label + " problem", PLAIN_ID);
            ProductSpineJson.member(row, "severity", label + " problem",
                    Set.of("info", "warning", "error", "fatal"));
            ProductSpineJson.string(row, "message", label + " problem");
            ProductSpineJson.nullablePatterned(
                    row, "affected_identity", label + " problem", PLAIN_ID
            );
            ownerReferences(row, "evidence_refs", label + " problem");
            ProductSpineJson.nullablePatterned(
                    row, "next_action_id", label + " problem", PLAIN_ID
            );
        }
    }

    private static @NotNull Frontend frontend(@NotNull JsonObject row) {
        ProductSpineJson.exact(row, "Work Session frontend",
                "frontend_id", "kind", "version", "instance_id", "process_id");
        String frontendId = ProductSpineJson.patterned(
                row, "frontend_id", "Work Session frontend", PLAIN_ID
        );
        String kind = ProductSpineJson.member(row, "kind", "Work Session frontend",
                Set.of("cli", "vscode", "intellij-community", "service", "test"));
        String version = ProductSpineJson.string(row, "version", "Work Session frontend");
        String instanceId = ProductSpineJson.nullablePatterned(
                row, "instance_id", "Work Session frontend", PLAIN_ID
        );
        JsonElement process = row.get("process_id");
        Integer processId = null;
        if (!process.isJsonNull()) {
            processId = ProductSpineJson.integer(
                    row, "process_id", "Work Session frontend", 1, Integer.MAX_VALUE
            );
        }
        return new Frontend(frontendId, kind, version, instanceId, processId);
    }

    private static void validateStage(@NotNull JsonElement value) {
        if (value.isJsonNull()) return;
        JsonObject row = ProductSpineJson.object(value, "Work Session stage");
        ProductSpineJson.exact(row, "Work Session stage", "stage_id", "state");
        ProductSpineJson.patterned(row, "stage_id", "Work Session stage", PLAIN_ID);
        ProductSpineJson.member(row, "state", "Work Session stage",
                Set.of("pending", "running", "complete", "failed", "cancelled",
                        "incomplete", "blocked"));
    }

    private static void validateResolutionProblems(@NotNull JsonObject root) {
        JsonArray rows = ProductSpineJson.array(
                root, "owner_resolution_problems", "Work Session recovery preview", 256
        );
        for (JsonElement value : rows) {
            JsonObject row = ProductSpineJson.object(value, "Work Session owner resolution problem");
            ProductSpineJson.exact(row, "Work Session owner resolution problem",
                    "record_id", "message");
            ProductSpineJson.patterned(
                    row, "record_id", "Work Session owner resolution problem", PLAIN_ID
            );
            ProductSpineJson.string(row, "message", "Work Session owner resolution problem");
        }
    }

    private static @NotNull String sessionId(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        return ProductSpineJson.patterned(value, key, label, SESSION_ID);
    }

    private static void timestamp(
            @NotNull JsonObject value, @NotNull String key, @NotNull String label
    ) {
        String selected = ProductSpineJson.string(value, key, label);
        ProductSpineJson.require(TIMESTAMP.matcher(selected).matches(),
                "Workbench " + label + "." + key + " is not an RFC 3339 UTC timestamp");
    }

    private static @NotNull List<String> identifiers(
            @NotNull List<String> values, @NotNull String label
    ) {
        for (String value : values) {
            ProductSpineJson.require(PLAIN_ID.matcher(value).matches(), label + " is invalid");
        }
        return List.copyOf(values);
    }

    public record Status(
            @NotNull String rawJson, @NotNull String summaryId,
            @NotNull String sessionId, @NotNull String sessionRecordId,
            @NotNull Task task, @NotNull Workspace workspace, @NotNull String lifecycle,
            int latestSequence, int eventCount, @Nullable String latestEventId, boolean closed,
            @NotNull List<String> frontendIds,
            @NotNull List<OwnerReference> ownerReferences,
            @NotNull List<Action> nextActions, @Nullable Recovery recovery,
            @NotNull String integrityState, @NotNull List<String> integrityProblemIds
    ) {
        public Status {
            frontendIds = List.copyOf(frontendIds);
            ownerReferences = List.copyOf(ownerReferences);
            nextActions = List.copyOf(nextActions);
            integrityProblemIds = List.copyOf(integrityProblemIds);
        }
    }

    public record Timeline(
            @NotNull String rawJson, @NotNull String sessionId, int afterSequence,
            @NotNull List<Event> events, int nextSequence, boolean hasMore,
            @NotNull Integrity integrity
    ) {
        public Timeline { events = List.copyOf(events); }
    }

    public record RecoveryPreview(
            @NotNull String rawJson, @NotNull String sessionId,
            @NotNull String sessionRecordId, int latestSequence,
            boolean required, boolean automatic, @Nullable String reason,
            @NotNull List<OwnerReference> ownerReferences,
            @NotNull String ownerResolution, @NotNull List<Action> safeActions,
            @NotNull Integrity integrity
    ) {
        public RecoveryPreview {
            ownerReferences = List.copyOf(ownerReferences);
            safeActions = List.copyOf(safeActions);
        }
    }

    public record Event(
            @NotNull String eventId, @NotNull String sessionId,
            @NotNull String sessionRecordId, int sequence,
            @Nullable String previousEventId, @NotNull Frontend frontend,
            @NotNull String kind,
            @NotNull String taskId, @NotNull String lifecycle,
            @NotNull List<OwnerReference> ownerReferences,
            @NotNull List<Action> nextActions, @Nullable Recovery recovery
    ) {
        public Event {
            ownerReferences = List.copyOf(ownerReferences);
            nextActions = List.copyOf(nextActions);
        }
    }

    public record Frontend(
            @NotNull String frontendId, @NotNull String kind,
            @NotNull String version, @Nullable String instanceId,
            @Nullable Integer processId
    ) { }

    public record Task(
            @NotNull String taskId, @NotNull String ownerId, @Nullable String label
    ) { }

    public record Workspace(
            @NotNull String identityId, @NotNull String canonicalRoot,
            @NotNull String rootUri, @Nullable String sourceRevision,
            @Nullable String dirtyFingerprint
    ) { }

    public record OwnerReference(
            @NotNull String ownerId, @NotNull String recordId,
            @NotNull String recordKind, @NotNull String uri,
            @Nullable String digest, @Nullable String lastVerifiedState,
            @Nullable String verifiedAt
    ) { }

    public record Action(
            @NotNull String actionId, @NotNull String actionDigest,
            @NotNull String ownerId, @NotNull String availability,
            @NotNull String mutationBudget, @NotNull JsonObject arguments
    ) {
        public Action { arguments = arguments.deepCopy(); }

        @Override public @NotNull JsonObject arguments() {
            return arguments.deepCopy();
        }
    }

    public record Recovery(
            @NotNull String state, @NotNull String reason,
            @NotNull List<OwnerReference> ownerReferences,
            @NotNull List<String> safeActionIds
    ) {
        public Recovery {
            ownerReferences = List.copyOf(ownerReferences);
            safeActionIds = List.copyOf(safeActionIds);
        }
    }

    public record Integrity(
            @NotNull String state, @NotNull String journalState,
            @NotNull String summaryState
    ) { }
}
