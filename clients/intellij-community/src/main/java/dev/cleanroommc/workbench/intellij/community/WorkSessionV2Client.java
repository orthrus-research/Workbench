package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** No-shell CLI-parity client for Work Session status, timeline, and recovery. */
final class WorkSessionV2Client {
    private static final String FRONTEND = "intellij-community";
    private static final String LIVE_CONSOLE_FORMAT = "workbench-live-console-session-v1";
    private static final String ARTIFACT_EVENTS_FORMAT = "workbench-owner-artifact-events-v1";
    private static final String ARTIFACT_RANGE_FORMAT = "workbench-owner-artifact-range-v1";
    private static final int MAX_ARTIFACT_EVENT_PAGE = 1024;
    private static final int MAX_ARTIFACT_RANGE_BYTES = 8 * 1024 * 1024;
    private static final int MAX_ARTIFACT_OUTPUT_BYTES = 16 * 1024 * 1024;
    private static final Pattern LIVE_CONSOLE_RECORD_ID = Pattern.compile(
            "^[A-Za-z0-9._-]{8,120}$"
    );
    private static final Pattern LIVE_CONSOLE_EVENT_ID = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}$"
    );
    private static final Pattern DIGEST = Pattern.compile("^sha256:[0-9a-f]{64}$");
    private static final Pattern STREAM = Pattern.compile("^[a-z][a-z0-9-]{0,47}$");
    private static final Set<String> BOUNDARIES = Set.of("lf", "crlf", "cr", "limit", "eof");

    private WorkSessionV2Client() {
    }

    static @NotNull List<String> statusArguments(@Nullable String sessionId) {
        List<String> arguments = new ArrayList<>(List.of("session", "status"));
        if (sessionId != null) arguments.add(WorkSessionV2.requireSessionId(sessionId));
        arguments.addAll(List.of("--frontend", FRONTEND));
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    static @NotNull List<String> timelineArguments(
            @NotNull String sessionId, int afterSequence, int limit
    ) {
        ProductSpineJson.require(afterSequence >= -1 && afterSequence <= 99_999,
                "Work Session timeline cursor is invalid");
        ProductSpineJson.require(limit >= 1 && limit <= 4096,
                "Work Session timeline limit is invalid");
        return List.of(
                "session", "timeline", WorkSessionV2.requireSessionId(sessionId),
                "--after-sequence", Integer.toString(afterSequence),
                "--limit", Integer.toString(limit),
                "--frontend", FRONTEND, "--json"
        );
    }

    static @NotNull List<String> recoveryArguments(@Nullable String sessionId) {
        return recoveryArguments(sessionId, false);
    }

    static @NotNull List<String> recoveryArguments(
            @Nullable String sessionId, boolean apply
    ) {
        List<String> arguments = new ArrayList<>(List.of("session", "recover"));
        if (sessionId != null) arguments.add(WorkSessionV2.requireSessionId(sessionId));
        if (apply) arguments.add("--apply");
        arguments.addAll(List.of("--frontend", FRONTEND));
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    static @NotNull List<String> resumeArguments(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            @Nullable String workspace
    ) {
        List<String> arguments = new ArrayList<>(List.of(
                "session", "resume", WorkSessionV2.requireSessionId(sessionId)
        ));
        if (workspace != null) {
            ProductSpineJson.require(!workspace.isBlank() && workspace.indexOf('\0') < 0
                            && workspace.length() <= 32 * 1024,
                    "Work Session workspace path is invalid");
            arguments.addAll(List.of(
                    "--workspace",
                    launch.commandPath(workspace, "Work Session workspace")
            ));
        }
        arguments.addAll(List.of("--frontend", FRONTEND, "--json"));
        return List.copyOf(arguments);
    }

    static @NotNull List<String> closeArguments(@NotNull String sessionId) {
        return List.of(
                "session", "close", WorkSessionV2.requireSessionId(sessionId),
                "--frontend", FRONTEND, "--json"
        );
    }

    static @NotNull List<String> artifactRangeArguments(
            @NotNull String sessionId,
            @NotNull WorkSessionV2.OwnerReference reference,
            @NotNull String eventId
    ) {
        WorkSessionV2.OwnerReference owner = liveConsoleOwner(reference);
        ProductSpineJson.require(LIVE_CONSOLE_EVENT_ID.matcher(eventId).matches(),
                "live-console event selector is invalid");
        return List.of(
                "session", "artifact", WorkSessionV2.requireSessionId(sessionId),
                owner.recordId(), owner.digest(), eventId,
                "--frontend", FRONTEND, "--json"
        );
    }

    static @NotNull List<String> artifactEventsArguments(
            @NotNull String sessionId,
            @NotNull WorkSessionV2.OwnerReference reference,
            int afterSequence,
            int limit
    ) {
        WorkSessionV2.OwnerReference owner = liveConsoleOwner(reference);
        ProductSpineJson.require(afterSequence >= -1,
                "live-console event cursor is invalid");
        ProductSpineJson.require(limit >= 1 && limit <= MAX_ARTIFACT_EVENT_PAGE,
                "live-console event page size is invalid");
        return List.of(
                "session", "artifact", WorkSessionV2.requireSessionId(sessionId),
                owner.recordId(), owner.digest(),
                "--after-sequence", Integer.toString(afterSequence),
                "--limit", Integer.toString(limit),
                "--frontend", FRONTEND, "--json"
        );
    }

    static @NotNull WorkSessionV2.Status status(
            @NotNull CoreLaunch launch,
            @Nullable String sessionId,
            @Nullable String workingDirectory
    ) throws IOException {
        return WorkSessionV2.parseStatus(CommandProcess.capture(
                launch, statusArguments(sessionId), WorkSessionV2.MAX_STATUS_BYTES,
                120, workingDirectory
        ));
    }

    static @NotNull WorkSessionV2.Timeline timeline(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            int afterSequence,
            int limit,
            @Nullable String workingDirectory
    ) throws IOException {
        return WorkSessionV2.parseTimeline(CommandProcess.capture(
                launch, timelineArguments(sessionId, afterSequence, limit),
                WorkSessionV2.MAX_TIMELINE_BYTES, 120, workingDirectory
        ));
    }

    static @NotNull WorkSessionV2.RecoveryPreview recovery(
            @NotNull CoreLaunch launch,
            @Nullable String sessionId,
            @Nullable String workingDirectory
    ) throws IOException {
        return WorkSessionV2.parseRecovery(CommandProcess.capture(
                launch, recoveryArguments(sessionId), WorkSessionV2.MAX_STATUS_BYTES,
                120, workingDirectory
        ));
    }

    static @NotNull WorkSessionV2.Status recoveryApply(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            @Nullable String workingDirectory
    ) throws IOException {
        return WorkSessionV2.parseStatus(CommandProcess.capture(
                launch, recoveryArguments(sessionId, true), WorkSessionV2.MAX_STATUS_BYTES,
                120, workingDirectory
        ));
    }

    static @NotNull WorkSessionV2.Status resume(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            @Nullable String workspace,
            @Nullable String workingDirectory
    ) throws IOException {
        return WorkSessionV2.parseStatus(CommandProcess.capture(
                launch, resumeArguments(launch, sessionId, workspace),
                WorkSessionV2.MAX_STATUS_BYTES,
                120, workingDirectory
        ));
    }

    static @NotNull WorkSessionV2.Status close(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            @Nullable String workingDirectory
    ) throws IOException {
        return WorkSessionV2.parseStatus(CommandProcess.capture(
                launch, closeArguments(sessionId), WorkSessionV2.MAX_STATUS_BYTES,
                120, workingDirectory
        ));
    }

    static @NotNull OwnerArtifactRange artifactRange(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            @NotNull WorkSessionV2.OwnerReference reference,
            @NotNull String eventId,
            @Nullable String workingDirectory
    ) throws IOException {
        WorkSessionV2.OwnerReference owner = liveConsoleOwner(reference);
        OwnerArtifactRange result = parseArtifactRange(CommandProcess.capture(
                launch, artifactRangeArguments(sessionId, owner, eventId),
                MAX_ARTIFACT_OUTPUT_BYTES, 120, workingDirectory
        ));
        ProductSpineJson.require(result.sessionId().equals(sessionId)
                        && result.ownerRecordId().equals(owner.recordId())
                        && result.ownerDigest().equals(owner.digest())
                        && result.eventId().equals(eventId),
                "Work Session owner artifact response changed its selected identity");
        return result;
    }

    static @NotNull OwnerArtifactEvents artifactEvents(
            @NotNull CoreLaunch launch,
            @NotNull String sessionId,
            @NotNull WorkSessionV2.OwnerReference reference,
            int afterSequence,
            int limit,
            @Nullable String workingDirectory
    ) throws IOException {
        WorkSessionV2.OwnerReference owner = liveConsoleOwner(reference);
        OwnerArtifactEvents result = parseArtifactEvents(CommandProcess.capture(
                launch, artifactEventsArguments(sessionId, owner, afterSequence, limit),
                WorkSessionV2.MAX_STATUS_BYTES, 120, workingDirectory
        ));
        ProductSpineJson.require(result.sessionId().equals(sessionId)
                        && result.ownerRecordId().equals(owner.recordId())
                        && result.ownerDigest().equals(owner.digest())
                        && result.afterSequence() == afterSequence
                        && result.limit() == limit,
                "Work Session owner artifact events changed their selected identity");
        return result;
    }

    static @NotNull OwnerArtifactEvents parseArtifactEvents(@NotNull String json) {
        String label = "Work Session owner artifact events";
        JsonObject row = ProductSpineJson.parse(
                json, label, WorkSessionV2.MAX_STATUS_BYTES, 150_000
        );
        ProductSpineJson.exact(row, label,
                "format_version", "session_id", "owner_record_id", "owner_digest",
                "current_owner_digest", "after_sequence", "limit", "events",
                "has_more", "next_after_sequence");
        ProductSpineJson.require(ARTIFACT_EVENTS_FORMAT.equals(ProductSpineJson.string(
                        row, "format_version", label)),
                "Workbench returned an unsupported owner artifact events format");
        String sessionId = WorkSessionV2.requireSessionId(
                ProductSpineJson.string(row, "session_id", label)
        );
        String ownerRecordId = ProductSpineJson.patterned(
                row, "owner_record_id", label, LIVE_CONSOLE_RECORD_ID
        );
        String ownerDigest = ProductSpineJson.patterned(
                row, "owner_digest", label, DIGEST
        );
        String currentOwnerDigest = ProductSpineJson.patterned(
                row, "current_owner_digest", label, DIGEST
        );
        int afterSequence = ProductSpineJson.integer(
                row, "after_sequence", label, -1, Integer.MAX_VALUE
        );
        int limit = ProductSpineJson.integer(
                row, "limit", label, 1, MAX_ARTIFACT_EVENT_PAGE
        );
        JsonArray values = ProductSpineJson.array(row, "events", label, limit);
        List<OwnerArtifactEvent> events = new ArrayList<>();
        Set<String> eventIds = new HashSet<>();
        int previousSequence = afterSequence;
        for (int index = 0; index < values.size(); index++) {
            String eventLabel = "Work Session owner artifact event " + index;
            JsonObject event = ProductSpineJson.object(values.get(index), eventLabel);
            ProductSpineJson.exact(event, eventLabel,
                    "event_id", "sequence", "kind", "severity", "subsystem", "message",
                    "stream", "artifact", "byte_start", "byte_end", "boundary");
            int sequence = ProductSpineJson.integer(
                    event, "sequence", eventLabel, 1, Integer.MAX_VALUE
            );
            ProductSpineJson.require(sequence > previousSequence,
                    "Work Session owner artifact event order changed");
            previousSequence = sequence;
            String eventId = ProductSpineJson.patterned(
                    event, "event_id", eventLabel, LIVE_CONSOLE_EVENT_ID
            );
            ProductSpineJson.require(eventIds.add(eventId),
                    "Work Session owner artifact event identity was repeated");
            String stream = ProductSpineJson.patterned(event, "stream", eventLabel, STREAM);
            String artifact = ProductSpineJson.string(event, "artifact", eventLabel);
            ProductSpineJson.require(artifact.equals(stream + ".raw"),
                    "Work Session owner artifact stream binding changed");
            int byteStart = ProductSpineJson.integer(
                    event, "byte_start", eventLabel, 0, Integer.MAX_VALUE
            );
            int byteEnd = ProductSpineJson.integer(
                    event, "byte_end", eventLabel, byteStart, Integer.MAX_VALUE
            );
            ProductSpineJson.require(byteEnd - byteStart <= MAX_ARTIFACT_RANGE_BYTES,
                    "Work Session owner artifact event range exceeds its bound");
            events.add(new OwnerArtifactEvent(
                    eventId, sequence,
                    ProductSpineJson.string(event, "kind", eventLabel),
                    ProductSpineJson.string(event, "severity", eventLabel),
                    ProductSpineJson.string(event, "subsystem", eventLabel),
                    ProductSpineJson.string(event, "message", eventLabel),
                    stream, artifact, byteStart, byteEnd,
                    ProductSpineJson.member(event, "boundary", eventLabel, BOUNDARIES)
            ));
        }
        boolean hasMore = ProductSpineJson.bool(row, "has_more", label);
        int nextAfterSequence = ProductSpineJson.integer(
                row, "next_after_sequence", label, -1, Integer.MAX_VALUE
        );
        int expectedNext = events.isEmpty()
                ? afterSequence : events.getLast().sequence();
        ProductSpineJson.require(nextAfterSequence == expectedNext
                        && (!hasMore || events.size() == limit),
                "Work Session owner artifact event cursor is inconsistent");
        return new OwnerArtifactEvents(
                sessionId, ownerRecordId, ownerDigest, currentOwnerDigest,
                afterSequence, limit, events, hasMore, nextAfterSequence
        );
    }

    static @NotNull OwnerArtifactRange parseArtifactRange(@NotNull String json) {
        String label = "Work Session owner artifact range";
        JsonObject row = ProductSpineJson.parse(
                json, label, MAX_ARTIFACT_OUTPUT_BYTES, 150_000
        );
        ProductSpineJson.exact(row, label,
                "format_version", "session_id", "owner_record_id", "owner_digest",
                "current_owner_digest", "event_id", "sequence", "stream", "artifact",
                "byte_start", "byte_end", "byte_count", "content_sha256", "encoding",
                "content_base64", "utf8");
        ProductSpineJson.require(ARTIFACT_RANGE_FORMAT.equals(ProductSpineJson.string(
                        row, "format_version", label)),
                "Workbench returned an unsupported owner artifact range format");
        String sessionId = WorkSessionV2.requireSessionId(
                ProductSpineJson.string(row, "session_id", label)
        );
        String ownerRecordId = ProductSpineJson.patterned(
                row, "owner_record_id", label, LIVE_CONSOLE_RECORD_ID
        );
        String ownerDigest = ProductSpineJson.patterned(
                row, "owner_digest", label, DIGEST
        );
        String currentOwnerDigest = ProductSpineJson.patterned(
                row, "current_owner_digest", label, DIGEST
        );
        String eventId = ProductSpineJson.patterned(
                row, "event_id", label, LIVE_CONSOLE_EVENT_ID
        );
        int sequence = ProductSpineJson.integer(
                row, "sequence", label, 1, Integer.MAX_VALUE
        );
        String stream = ProductSpineJson.patterned(row, "stream", label, STREAM);
        String artifact = ProductSpineJson.string(row, "artifact", label);
        ProductSpineJson.require(artifact.equals(stream + ".raw"),
                "Work Session owner artifact stream binding changed");
        int byteStart = ProductSpineJson.integer(
                row, "byte_start", label, 0, Integer.MAX_VALUE
        );
        int byteEnd = ProductSpineJson.integer(
                row, "byte_end", label, byteStart, Integer.MAX_VALUE
        );
        int byteCount = ProductSpineJson.integer(
                row, "byte_count", label, 0, MAX_ARTIFACT_RANGE_BYTES
        );
        ProductSpineJson.require(byteEnd - byteStart == byteCount,
                "Work Session owner artifact byte range is inconsistent");
        String contentSha256 = ProductSpineJson.patterned(
                row, "content_sha256", label, DIGEST
        );
        ProductSpineJson.require("base64".equals(ProductSpineJson.string(
                        row, "encoding", label)),
                "Work Session owner artifact encoding is unsupported");
        JsonElement base64Value = ProductSpineJson.required(row, "content_base64", label);
        ProductSpineJson.require(base64Value.isJsonPrimitive()
                        && base64Value.getAsJsonPrimitive().isString(),
                "Workbench Work Session owner artifact content must be base64 text");
        String contentBase64 = base64Value.getAsString();
        ProductSpineJson.require(contentBase64.length()
                        <= Math.ceilDiv(MAX_ARTIFACT_RANGE_BYTES, 3) * 4,
                "Workbench Work Session owner artifact content exceeds its byte bound");
        final byte[] content;
        try {
            content = Base64.getDecoder().decode(contentBase64);
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException(
                    "Workbench Work Session owner artifact content is not canonical base64", error
            );
        }
        ProductSpineJson.require(content.length == byteCount
                        && Base64.getEncoder().encodeToString(content).equals(contentBase64)
                        && digest(content).equals(contentSha256),
                "Work Session owner artifact content binding changed");
        String exactUtf8 = exactUtf8(content);
        JsonElement utf8Value = ProductSpineJson.required(row, "utf8", label);
        ProductSpineJson.require(utf8Value.isJsonNull()
                        || (utf8Value.isJsonPrimitive()
                        && utf8Value.getAsJsonPrimitive().isString()),
                "Workbench Work Session owner artifact UTF-8 projection is invalid");
        String suppliedUtf8 = utf8Value.isJsonNull() ? null : utf8Value.getAsString();
        ProductSpineJson.require(java.util.Objects.equals(suppliedUtf8, exactUtf8),
                "Work Session owner artifact UTF-8 projection changed");
        return new OwnerArtifactRange(
                sessionId, ownerRecordId, ownerDigest, currentOwnerDigest, eventId,
                sequence, stream, artifact, byteStart, byteEnd, byteCount, contentSha256,
                contentBase64, exactUtf8
        );
    }

    private static @NotNull WorkSessionV2.OwnerReference liveConsoleOwner(
            @NotNull WorkSessionV2.OwnerReference reference
    ) {
        ProductSpineJson.require("workbench-shell".equals(reference.ownerId())
                        && LIVE_CONSOLE_FORMAT.equals(reference.recordKind())
                        && LIVE_CONSOLE_RECORD_ID.matcher(reference.recordId()).matches()
                        && reference.digest() != null
                        && DIGEST.matcher(reference.digest()).matches(),
                "Work Session owner is not one exact live-console V1 record");
        return reference;
    }

    private static @NotNull String digest(byte @NotNull [] content) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(content);
            return "sha256:" + HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    private static @Nullable String exactUtf8(byte @NotNull [] content) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(content)).toString();
        } catch (CharacterCodingException error) {
            return null;
        }
    }

    record OwnerArtifactEvent(
            @NotNull String eventId,
            int sequence,
            @NotNull String kind,
            @NotNull String severity,
            @NotNull String subsystem,
            @NotNull String message,
            @NotNull String stream,
            @NotNull String artifact,
            int byteStart,
            int byteEnd,
            @NotNull String boundary
    ) { }

    record OwnerArtifactEvents(
            @NotNull String sessionId,
            @NotNull String ownerRecordId,
            @NotNull String ownerDigest,
            @NotNull String currentOwnerDigest,
            int afterSequence,
            int limit,
            @NotNull List<OwnerArtifactEvent> events,
            boolean hasMore,
            int nextAfterSequence
    ) {
        OwnerArtifactEvents { events = List.copyOf(events); }
    }

    record OwnerArtifactRange(
            @NotNull String sessionId,
            @NotNull String ownerRecordId,
            @NotNull String ownerDigest,
            @NotNull String currentOwnerDigest,
            @NotNull String eventId,
            int sequence,
            @NotNull String stream,
            @NotNull String artifact,
            int byteStart,
            int byteEnd,
            int byteCount,
            @NotNull String contentSha256,
            @NotNull String contentBase64,
            @Nullable String utf8
    ) { }
}
