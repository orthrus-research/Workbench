package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict, authority-preserving diagnosis and reproduction-capsule read models. */
public final class DiagnoseV1 {
    public static final String DIAGNOSIS_FORMAT = "workbench-diagnosis-v1";
    public static final String CAPSULE_INSPECTION_FORMAT =
            "workbench-reproduction-capsule-inspection-v1";
    public static final int MAX_DIAGNOSIS_BYTES = 32 * 1024 * 1024;
    public static final int MAX_CAPSULE_INSPECTION_BYTES = 2 * 1024 * 1024;

    private static final Pattern DIAGNOSIS_ID = Pattern.compile(
            "^workbench-diagnosis:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern CAPSULE_ID = Pattern.compile(
            "^workbench-reproduction-capsule:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern DIGEST = Pattern.compile("^sha256:[0-9a-f]{64}$");
    private static final Pattern IDENTITY = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}$"
    );
    private static final Pattern SESSION_ID = Pattern.compile(
            "^work-session-v2-[0-9a-f]{32}$"
    );
    private static final Pattern ACTION_ID = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9._:-]{1,255}$"
    );
    private static final Pattern STREAM = Pattern.compile("^[a-z][a-z0-9-]{0,47}$");
    private static final Pattern ARTIFACT = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$"
    );
    private static final Set<String> SEVERITIES = Set.of(
            "trace", "debug", "info", "warning", "error", "fatal", "unknown"
    );
    private static final Set<String> BOUNDARIES = Set.of(
            "lf", "crlf", "cr", "limit", "eof"
    );
    private static final Set<String> CLEANROOM_STAGES = Set.of("build", "client", "server");
    private static final Set<String> CLEANROOM_STAGE_STATES = Set.of(
            "not-run", "passed", "failed"
    );
    private static final Set<String> MUTATIONS = Set.of(
            "read-only", "isolated-target-only"
    );
    private static final Set<String> REQUIRED_EXCLUSIONS = Set.of(
            "credentials", "personal-worlds", "protected-binaries"
    );

    private DiagnoseV1() {
    }

    public static @NotNull Diagnosis parseDiagnosis(@NotNull String json) {
        String label = "diagnosis";
        JsonObject root = ProductSpineJson.parse(
                json, label, MAX_DIAGNOSIS_BYTES, 200_000
        );
        ProductSpineJson.exact(root, label,
                "format", "schema_version", "diagnosis_id", "work_session_id",
                "outcome", "target_owner_ref", "current_owner_ref", "command",
                "timeline", "observed_failures", "wrappers", "secondary_failures",
                "shutdown_noise", "contributing_conditions", "classifications",
                "unknowns", "next_experiments", "fingerprint", "limitations");
        ProductSpineJson.require(DIAGNOSIS_FORMAT.equals(ProductSpineJson.string(
                        root, "format", label)),
                "Workbench returned an unsupported diagnosis format");
        ProductSpineJson.require(ProductSpineJson.integer(
                        root, "schema_version", label, 1, 1) == 1,
                "Workbench returned an unsupported diagnosis schema");
        String diagnosisId = ProductSpineJson.patterned(
                root, "diagnosis_id", label, DIAGNOSIS_ID
        );
        String sessionId = ProductSpineJson.nullablePatterned(
                root, "work_session_id", label, SESSION_ID
        );
        String outcome = ProductSpineJson.member(
                root, "outcome", label, Set.of("failed", "inconclusive")
        );
        OwnerReference target = ownerReference(
                ProductSpineJson.object(root, "target_owner_ref", label),
                "diagnosis target owner"
        );
        OwnerReference current = ownerReference(
                ProductSpineJson.object(root, "current_owner_ref", label),
                "diagnosis current owner"
        );
        ProductSpineJson.require(
                target.ownerId().equals(current.ownerId())
                        && target.recordId().equals(current.recordId())
                        && target.recordKind().equals(current.recordKind())
                        && target.uri().equals(current.uri()),
                "Workbench diagnosis owner identity changed during resolution"
        );
        Command command = command(ProductSpineJson.object(root, "command", label));
        List<Event> timeline = events(root, "timeline", "diagnosis timeline", 1, 100_000);
        for (int index = 1; index < timeline.size(); index++) {
            ProductSpineJson.require(
                    timeline.get(index).sequence() > timeline.get(index - 1).sequence(),
                    "Workbench diagnosis timeline order changed"
            );
        }
        Map<String, Event> timelineById = new HashMap<>();
        for (Event event : timeline) {
            ProductSpineJson.require(timelineById.put(event.eventId(), event) == null,
                    "Workbench diagnosis timeline repeats an event identity");
        }
        List<Claim> observed = claims(
                root, "observed_failures", "diagnosis observed failures", timelineById
        );
        List<Claim> wrappers = claims(
                root, "wrappers", "diagnosis wrappers", timelineById
        );
        List<Claim> secondary = claims(
                root, "secondary_failures", "diagnosis secondary failures", timelineById
        );
        List<Claim> shutdown = claims(
                root, "shutdown_noise", "diagnosis shutdown noise", timelineById
        );
        List<Claim> contributing = claims(
                root, "contributing_conditions", "diagnosis contributing conditions", timelineById
        );
        List<Classification> classifications = classifications(root);
        List<Unknown> unknowns = unknowns(root);
        List<NextAction> nextActions = nextActions(root);
        String fingerprint = ProductSpineJson.patterned(
                root, "fingerprint", label, DIGEST
        );
        List<String> limitations = boundedStrings(
                root, "limitations", "diagnosis limitations", 1, 128, 8192
        );
        validateJson(root, "diagnosis", new JsonBounds(), 0);
        verifyDiagnosisIdentity(root, diagnosisId);
        return new Diagnosis(
                json, diagnosisId, sessionId, outcome, target, current, command,
                timeline, observed, wrappers, secondary, shutdown, contributing,
                classifications, unknowns, nextActions, fingerprint, limitations
        );
    }

    public static @NotNull CapsuleInspection parseCapsuleInspection(@NotNull String json) {
        String label = "reproduction capsule inspection";
        JsonObject root = ProductSpineJson.parse(
                json, label, MAX_CAPSULE_INSPECTION_BYTES, 50_000
        );
        ProductSpineJson.exact(root, label,
                "format", "capsule_id", "diagnosis_id", "fingerprint",
                "member_count", "replay_action", "privacy_review", "limitations");
        ProductSpineJson.require(CAPSULE_INSPECTION_FORMAT.equals(ProductSpineJson.string(
                        root, "format", label)),
                "Workbench returned an unsupported capsule inspection format");
        String capsuleId = ProductSpineJson.patterned(
                root, "capsule_id", label, CAPSULE_ID
        );
        String diagnosisId = ProductSpineJson.patterned(
                root, "diagnosis_id", label, DIAGNOSIS_ID
        );
        String fingerprint = ProductSpineJson.patterned(
                root, "fingerprint", label, DIGEST
        );
        ProductSpineJson.require(ProductSpineJson.integer(
                        root, "member_count", label, 2, 2) == 2,
                "Workbench capsule membership changed");
        ReplayAction replayAction = replayAction(
                ProductSpineJson.object(root, "replay_action", label)
        );
        PrivacyReview privacy = privacyReview(
                ProductSpineJson.object(root, "privacy_review", label)
        );
        List<String> limitations = boundedStrings(
                root, "limitations", "capsule limitations", 1, 128, 8192
        );
        validateJson(root, label, new JsonBounds(), 0);
        return new CapsuleInspection(
                json, capsuleId, diagnosisId, fingerprint, replayAction,
                privacy, limitations
        );
    }

    static @NotNull String requireSessionId(@NotNull String value) {
        ProductSpineJson.require(SESSION_ID.matcher(value).matches(),
                "Workbench diagnosis Work Session ID is invalid");
        return value;
    }

    private static @NotNull OwnerReference ownerReference(
            @NotNull JsonObject row, @NotNull String label
    ) {
        ProductSpineJson.exact(row, label,
                "owner_id", "record_id", "record_kind", "uri", "digest",
                "last_verified_state");
        String uri = ProductSpineJson.string(row, "uri", label);
        try {
            URI parsed = new URI(uri);
            ProductSpineJson.require(parsed.isAbsolute(),
                    "Workbench " + label + " URI is not absolute");
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("Workbench " + label + " URI is invalid", error);
        }
        return new OwnerReference(
                ProductSpineJson.patterned(row, "owner_id", label, IDENTITY),
                ProductSpineJson.patterned(row, "record_id", label, IDENTITY),
                ProductSpineJson.patterned(row, "record_kind", label, IDENTITY),
                uri,
                ProductSpineJson.patterned(row, "digest", label, DIGEST),
                ProductSpineJson.patterned(row, "last_verified_state", label, IDENTITY)
        );
    }

    private static @NotNull Command command(@NotNull JsonObject row) {
        String label = "diagnosis command";
        ProductSpineJson.exact(row, label, "command_id", "intent", "shell");
        return new Command(
                ProductSpineJson.patterned(row, "command_id", label, IDENTITY),
                ProductSpineJson.member(row, "intent", label, Set.of("inspect", "execute")),
                ProductSpineJson.bool(row, "shell", label)
        );
    }

    private static @NotNull RawRange rawRange(
            @NotNull JsonObject row, @NotNull String label
    ) {
        ProductSpineJson.exact(row, label,
                "stream", "artifact", "byte_start", "byte_end", "boundary");
        String stream = ProductSpineJson.patterned(row, "stream", label, STREAM);
        String artifact = ProductSpineJson.patterned(row, "artifact", label, ARTIFACT);
        long byteStart = longInteger(row, "byte_start", label, 0, Long.MAX_VALUE);
        long byteEnd = longInteger(row, "byte_end", label, 1, Long.MAX_VALUE);
        ProductSpineJson.require(byteEnd > byteStart,
                "Workbench " + label + " is not one non-empty byte range");
        String boundary = ProductSpineJson.member(row, "boundary", label, BOUNDARIES);
        return new RawRange(stream, artifact, byteStart, byteEnd, boundary);
    }

    private static @NotNull Event event(
            @NotNull JsonObject row, @NotNull String label, boolean claim
    ) {
        if (claim) {
            ProductSpineJson.exact(row, label,
                    "event_id", "sequence", "kind", "severity", "subsystem",
                    "message", "raw_range", "claim_state", "owner_id");
        } else {
            ProductSpineJson.exact(row, label,
                    "event_id", "sequence", "kind", "severity", "subsystem",
                    "message", "raw_range");
        }
        return new Event(
                ProductSpineJson.patterned(row, "event_id", label, IDENTITY),
                longInteger(row, "sequence", label, 1, Long.MAX_VALUE),
                ProductSpineJson.patterned(row, "kind", label, IDENTITY),
                ProductSpineJson.member(row, "severity", label, SEVERITIES),
                ProductSpineJson.patterned(row, "subsystem", label, IDENTITY),
                boundedString(row, "message", label, 1024 * 1024, true),
                rawRange(ProductSpineJson.object(row, "raw_range", label), label + " raw range")
        );
    }

    private static @NotNull List<Event> events(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int minimum,
            int maximum
    ) {
        JsonArray rows = boundedArray(root, key, label, minimum, maximum);
        List<Event> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            result.add(event(
                    ProductSpineJson.object(rows.get(index), label + " " + index),
                    label + " " + index,
                    false
            ));
        }
        return List.copyOf(result);
    }

    private static @NotNull List<Classification> classifications(@NotNull JsonObject root) {
        String label = "diagnosis classifications";
        JsonArray rows = boundedArray(root, "classifications", label, 0, 256);
        List<Classification> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            String rowLabel = label + " " + index;
            JsonObject row = ProductSpineJson.object(rows.get(index), rowLabel);
            ProductSpineJson.exact(row, rowLabel,
                    "classification_id", "claim_state", "owner_id", "owner_record_id",
                    "owner_record_kind", "owner_record_uri", "owner_record_digest",
                    "stage", "state", "required_markers", "observed_markers",
                    "effective_exit_code", "cleanup_contained", "artifact_digest", "detail");
            ProductSpineJson.require("cleanroom-dev-loop-stage".equals(
                            ProductSpineJson.string(row, "classification_id", rowLabel)),
                    "Workbench " + rowLabel + " classification identity is unsupported");
            ProductSpineJson.require("observed".equals(
                            ProductSpineJson.string(row, "claim_state", rowLabel)),
                    "Workbench " + rowLabel + " is not an owner-returned observation");
            ProductSpineJson.require("workbench-shell".equals(
                            ProductSpineJson.string(row, "owner_id", rowLabel)),
                    "Workbench " + rowLabel + " owner is unsupported");
            ProductSpineJson.require("workbench-cleanroom-dev-loop-receipt".equals(
                            ProductSpineJson.string(row, "owner_record_kind", rowLabel)),
                    "Workbench " + rowLabel + " owner record kind is unsupported");
            String ownerRecordUri = schemaString(
                    row, "owner_record_uri", rowLabel, 3, 8192
            );
            requireAbsoluteUri(ownerRecordUri, rowLabel + " owner record");
            result.add(new Classification(
                    "cleanroom-dev-loop-stage",
                    "observed",
                    "workbench-shell",
                    ProductSpineJson.patterned(
                            row, "owner_record_id", rowLabel, IDENTITY
                    ),
                    "workbench-cleanroom-dev-loop-receipt",
                    ownerRecordUri,
                    ProductSpineJson.patterned(
                            row, "owner_record_digest", rowLabel, DIGEST
                    ),
                    ProductSpineJson.member(row, "stage", rowLabel, CLEANROOM_STAGES),
                    ProductSpineJson.member(row, "state", rowLabel, CLEANROOM_STAGE_STATES),
                    uniqueSchemaStrings(
                            row, "required_markers", rowLabel, 32, 1, 256
                    ),
                    uniqueSchemaStrings(
                            row, "observed_markers", rowLabel, 32, 1, 256
                    ),
                    nullableInteger(row, "effective_exit_code", rowLabel, 0, 255),
                    nullableBoolean(row, "cleanup_contained", rowLabel),
                    ProductSpineJson.nullablePatterned(
                            row, "artifact_digest", rowLabel, DIGEST
                    ),
                    schemaString(row, "detail", rowLabel, 1, 8192)
            ));
        }
        return List.copyOf(result);
    }

    private static @NotNull List<Claim> claims(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            @NotNull Map<String, Event> timeline
    ) {
        JsonArray rows = boundedArray(root, key, label, 0, 100_000);
        List<Claim> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            String rowLabel = label + " " + index;
            JsonObject row = ProductSpineJson.object(rows.get(index), rowLabel);
            Event event = event(row, rowLabel, true);
            ProductSpineJson.require("observed".equals(ProductSpineJson.string(
                            row, "claim_state", rowLabel)),
                    "Workbench " + rowLabel + " is not an owner-returned observation");
            String ownerId = ProductSpineJson.patterned(
                    row, "owner_id", rowLabel, IDENTITY
            );
            ProductSpineJson.require(event.equals(timeline.get(event.eventId())),
                    "Workbench " + rowLabel + " changed its retained event");
            result.add(new Claim(event, "observed", ownerId));
        }
        return List.copyOf(result);
    }

    private static @NotNull List<Unknown> unknowns(@NotNull JsonObject root) {
        String label = "diagnosis unknowns";
        JsonArray rows = boundedArray(root, "unknowns", label, 1, 256);
        List<Unknown> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            String rowLabel = label + " " + index;
            JsonObject row = ProductSpineJson.object(rows.get(index), rowLabel);
            ProductSpineJson.exact(row, rowLabel, "id", "claim_state", "owner_id", "detail");
            ProductSpineJson.require("unknown".equals(ProductSpineJson.string(
                            row, "claim_state", rowLabel)),
                    "Workbench diagnosis unknown changed claim state");
            result.add(new Unknown(
                    ProductSpineJson.patterned(row, "id", rowLabel, IDENTITY),
                    "unknown",
                    ProductSpineJson.patterned(row, "owner_id", rowLabel, IDENTITY),
                    boundedString(row, "detail", rowLabel, 8192, false)
            ));
        }
        return List.copyOf(result);
    }

    private static @NotNull List<NextAction> nextActions(@NotNull JsonObject root) {
        String label = "diagnosis next actions";
        JsonArray rows = boundedArray(root, "next_experiments", label, 1, 128);
        List<NextAction> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            String rowLabel = label + " " + index;
            JsonObject row = ProductSpineJson.object(rows.get(index), rowLabel);
            ProductSpineJson.exact(row, rowLabel,
                    "action_id", "arguments", "context_digest", "mutation");
            JsonObject arguments = ProductSpineJson.object(row, "arguments", rowLabel);
            ProductSpineJson.require(arguments.size() <= 64,
                    "Workbench " + rowLabel + " arguments exceed their bound");
            validateJson(arguments, rowLabel + " arguments", new JsonBounds(), 0);
            result.add(new NextAction(
                    ProductSpineJson.patterned(row, "action_id", rowLabel, IDENTITY),
                    arguments.deepCopy(),
                    ProductSpineJson.patterned(row, "context_digest", rowLabel, DIGEST),
                    ProductSpineJson.member(row, "mutation", rowLabel, MUTATIONS)
            ));
        }
        return List.copyOf(result);
    }

    private static @NotNull ReplayAction replayAction(@NotNull JsonObject row) {
        String label = "capsule replay action";
        ProductSpineJson.exact(row, label, "action_id", "arguments", "mutation");
        JsonObject arguments = ProductSpineJson.object(row, "arguments", label);
        ProductSpineJson.require(arguments.size() <= 256,
                "Workbench capsule replay arguments exceed their bound");
        validateJson(arguments, "capsule replay arguments", new JsonBounds(), 0);
        return new ReplayAction(
                ProductSpineJson.patterned(row, "action_id", label, ACTION_ID),
                arguments.deepCopy(),
                ProductSpineJson.member(row, "mutation", label, MUTATIONS)
        );
    }

    private static @NotNull PrivacyReview privacyReview(@NotNull JsonObject row) {
        String label = "capsule privacy review";
        ProductSpineJson.exact(row, label, "approved", "excluded");
        ProductSpineJson.require(ProductSpineJson.bool(row, "approved", label),
                "Workbench capsule privacy review is not approved");
        List<String> excluded = boundedStrings(row, "excluded", label, 3, 64, 128);
        ProductSpineJson.require(new HashSet<>(excluded).size() == excluded.size()
                        && excluded.containsAll(REQUIRED_EXCLUSIONS),
                "Workbench capsule privacy exclusions are incomplete or duplicated");
        return new PrivacyReview(true, excluded);
    }

    private static @NotNull JsonArray boundedArray(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int minimum,
            int maximum
    ) {
        JsonArray rows = ProductSpineJson.array(root, key, label, maximum);
        ProductSpineJson.require(rows.size() >= minimum,
                "Workbench " + label + " is below its row bound");
        return rows;
    }

    private static @NotNull List<String> boundedStrings(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int minimum,
            int maximum,
            int maximumBytes
    ) {
        JsonArray rows = boundedArray(root, key, label, minimum, maximum);
        List<String> result = new ArrayList<>();
        for (int index = 0; index < rows.size(); index++) {
            JsonElement item = rows.get(index);
            ProductSpineJson.require(item.isJsonPrimitive()
                            && item.getAsJsonPrimitive().isString(),
                    "Workbench " + label + " row must be a string");
            result.add(boundedText(item.getAsString(), label + " " + index, maximumBytes, false));
        }
        return List.copyOf(result);
    }

    private static @NotNull String boundedString(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int maximumBytes,
            boolean allowEmpty
    ) {
        JsonElement item = ProductSpineJson.required(root, key, label);
        ProductSpineJson.require(item.isJsonPrimitive()
                        && item.getAsJsonPrimitive().isString(),
                "Workbench " + label + "." + key + " must be a string");
        return boundedText(item.getAsString(), label + "." + key, maximumBytes, allowEmpty);
    }

    private static @NotNull List<String> uniqueSchemaStrings(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int maximum,
            int minimumCodePoints,
            int maximumCodePoints
    ) {
        JsonArray rows = ProductSpineJson.array(root, key, label, maximum);
        List<String> result = new ArrayList<>();
        Set<String> unique = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            JsonElement item = rows.get(index);
            ProductSpineJson.require(item.isJsonPrimitive()
                            && item.getAsJsonPrimitive().isString(),
                    "Workbench " + label + "." + key + " row must be a string");
            String selected = schemaText(
                    item.getAsString(), label + "." + key + " " + index,
                    minimumCodePoints, maximumCodePoints
            );
            ProductSpineJson.require(unique.add(selected),
                    "Workbench " + label + "." + key + " repeats a marker");
            result.add(selected);
        }
        return List.copyOf(result);
    }

    private static @NotNull String schemaString(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int minimumCodePoints,
            int maximumCodePoints
    ) {
        JsonElement item = ProductSpineJson.required(root, key, label);
        ProductSpineJson.require(item.isJsonPrimitive()
                        && item.getAsJsonPrimitive().isString(),
                "Workbench " + label + "." + key + " must be a string");
        return schemaText(
                item.getAsString(), label + "." + key,
                minimumCodePoints, maximumCodePoints
        );
    }

    private static @NotNull String schemaText(
            @NotNull String value,
            @NotNull String label,
            int minimumCodePoints,
            int maximumCodePoints
    ) {
        int count = value.codePointCount(0, value.length());
        ProductSpineJson.require(count >= minimumCodePoints && count <= maximumCodePoints,
                "Workbench " + label + " is outside its text bound");
        return value;
    }

    private static void requireAbsoluteUri(@NotNull String value, @NotNull String label) {
        try {
            ProductSpineJson.require(new URI(value).isAbsolute(),
                    "Workbench " + label + " URI is not absolute");
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("Workbench " + label + " URI is invalid", error);
        }
    }

    private static @Nullable Integer nullableInteger(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            int minimum,
            int maximum
    ) {
        JsonElement item = ProductSpineJson.required(root, key, label);
        return item.isJsonNull()
                ? null
                : ProductSpineJson.integer(root, key, label, minimum, maximum);
    }

    private static @Nullable Boolean nullableBoolean(
            @NotNull JsonObject root, @NotNull String key, @NotNull String label
    ) {
        JsonElement item = ProductSpineJson.required(root, key, label);
        return item.isJsonNull() ? null : ProductSpineJson.bool(root, key, label);
    }

    private static @NotNull String boundedText(
            @NotNull String value,
            @NotNull String label,
            int maximumBytes,
            boolean allowEmpty
    ) {
        ProductSpineJson.require((allowEmpty || !value.isEmpty())
                        && value.indexOf('\0') < 0
                        && value.getBytes(StandardCharsets.UTF_8).length <= maximumBytes,
                "Workbench " + label + " is invalid");
        return value;
    }

    private static long longInteger(
            @NotNull JsonObject root,
            @NotNull String key,
            @NotNull String label,
            long minimum,
            long maximum
    ) {
        JsonElement item = ProductSpineJson.required(root, key, label);
        ProductSpineJson.require(item.isJsonPrimitive()
                        && item.getAsJsonPrimitive().isNumber()
                        && item.getAsString().matches("-?(?:0|[1-9][0-9]*)"),
                "Workbench " + label + "." + key + " must be an integer");
        try {
            long selected = Long.parseLong(item.getAsString());
            ProductSpineJson.require(selected >= minimum && selected <= maximum,
                    "Workbench " + label + "." + key + " is outside its integer bound");
            return selected;
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException(
                    "Workbench " + label + "." + key + " is outside its integer bound", error
            );
        }
    }

    private static void validateJson(
            @NotNull JsonElement value,
            @NotNull String label,
            @NotNull JsonBounds bounds,
            int depth
    ) {
        bounds.nodes += 1;
        ProductSpineJson.require(bounds.nodes <= 200_000 && depth <= 64,
                "Workbench " + label + " exceeds its JSON bound");
        if (value.isJsonNull() || (value.isJsonPrimitive()
                && value.getAsJsonPrimitive().isBoolean())) {
            return;
        }
        if (value.isJsonPrimitive() && value.getAsJsonPrimitive().isString()) {
            boundedText(value.getAsString(), label, 1024 * 1024, true);
            return;
        }
        if (value.isJsonPrimitive() && value.getAsJsonPrimitive().isNumber()) {
            ProductSpineJson.require(value.getAsString().matches("-?(?:0|[1-9][0-9]*)"),
                    "Workbench " + label + " contains a non-integer number");
            try {
                Long.parseLong(value.getAsString());
            } catch (NumberFormatException error) {
                throw new IllegalArgumentException(
                        "Workbench " + label + " integer is outside its bound", error
                );
            }
            return;
        }
        if (value.isJsonArray()) {
            ProductSpineJson.require(value.getAsJsonArray().size() <= 100_000,
                    "Workbench " + label + " exceeds its array bound");
            for (int index = 0; index < value.getAsJsonArray().size(); index++) {
                validateJson(value.getAsJsonArray().get(index), label, bounds, depth + 1);
            }
            return;
        }
        JsonObject row = ProductSpineJson.object(value, label);
        ProductSpineJson.require(row.size() <= 256,
                "Workbench " + label + " exceeds its object bound");
        for (Map.Entry<String, JsonElement> entry : row.entrySet()) {
            boundedText(entry.getKey(), label + " key", 8192, false);
            validateJson(entry.getValue(), label + "." + entry.getKey(), bounds, depth + 1);
        }
    }

    private static void verifyDiagnosisIdentity(
            @NotNull JsonObject root, @NotNull String suppliedId
    ) {
        JsonObject body = root.deepCopy();
        body.remove("diagnosis_id");
        String canonical = pythonCanonical(body) + "\n";
        final byte[] digest;
        try {
            digest = MessageDigest.getInstance("SHA-256").digest(
                    canonical.getBytes(StandardCharsets.UTF_8)
            );
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("this Java runtime has no SHA-256 provider", error);
        }
        StringBuilder hex = new StringBuilder(64);
        for (byte octet : digest) {
            hex.append(String.format("%02x", octet & 0xff));
        }
        ProductSpineJson.require(
                suppliedId.equals("workbench-diagnosis:sha256:" + hex),
                "Workbench diagnosis content identity differs from its exact body"
        );
    }

    private static @NotNull String pythonCanonical(@NotNull JsonElement value) {
        StringBuilder output = new StringBuilder();
        appendPythonCanonical(value, output, 0);
        return output.toString();
    }

    private static void appendPythonCanonical(
            @NotNull JsonElement value, @NotNull StringBuilder output, int depth
    ) {
        ProductSpineJson.require(depth <= 64,
                "Workbench diagnosis exceeds its canonical JSON depth");
        if (value.isJsonNull()) {
            output.append("null");
        } else if (value.isJsonArray()) {
            output.append('[');
            for (int index = 0; index < value.getAsJsonArray().size(); index++) {
                if (index > 0) output.append(',');
                appendPythonCanonical(value.getAsJsonArray().get(index), output, depth + 1);
            }
            output.append(']');
        } else if (value.isJsonObject()) {
            output.append('{');
            List<Map.Entry<String, JsonElement>> entries = new ArrayList<>(
                    value.getAsJsonObject().entrySet()
            );
            entries.sort((left, right) -> compareCodePoints(left.getKey(), right.getKey()));
            for (int index = 0; index < entries.size(); index++) {
                if (index > 0) output.append(',');
                appendPythonString(entries.get(index).getKey(), output);
                output.append(':');
                appendPythonCanonical(entries.get(index).getValue(), output, depth + 1);
            }
            output.append('}');
        } else {
            JsonPrimitive primitive = value.getAsJsonPrimitive();
            if (primitive.isString()) appendPythonString(primitive.getAsString(), output);
            else if (primitive.isBoolean()) output.append(primitive.getAsBoolean() ? "true" : "false");
            else if (primitive.isNumber()) output.append(primitive.getAsString());
            else throw new IllegalArgumentException("Workbench diagnosis JSON primitive is unsupported");
        }
    }

    private static void appendPythonString(
            @NotNull String value, @NotNull StringBuilder output
    ) {
        output.append('"');
        for (int offset = 0; offset < value.length(); ) {
            int codePoint = value.codePointAt(offset);
            int width = Character.charCount(codePoint);
            if (width == 1 && Character.isSurrogate(value.charAt(offset))) {
                throw new IllegalArgumentException("Workbench diagnosis JSON has an unpaired surrogate");
            }
            switch (codePoint) {
                case '"' -> output.append("\\\"");
                case '\\' -> output.append("\\\\");
                case '\b' -> output.append("\\b");
                case '\f' -> output.append("\\f");
                case '\n' -> output.append("\\n");
                case '\r' -> output.append("\\r");
                case '\t' -> output.append("\\t");
                default -> {
                    if (codePoint >= 0x20 && codePoint <= 0x7e) {
                        output.appendCodePoint(codePoint);
                    } else if (codePoint <= 0xffff) {
                        output.append(String.format("\\u%04x", codePoint));
                    } else {
                        int selected = codePoint - 0x10000;
                        output.append(String.format("\\u%04x", 0xd800 + (selected >> 10)));
                        output.append(String.format("\\u%04x", 0xdc00 + (selected & 0x3ff)));
                    }
                }
            }
            offset += width;
        }
        output.append('"');
    }

    private static int compareCodePoints(@NotNull String left, @NotNull String right) {
        int leftOffset = 0;
        int rightOffset = 0;
        while (leftOffset < left.length() && rightOffset < right.length()) {
            int leftCode = left.codePointAt(leftOffset);
            int rightCode = right.codePointAt(rightOffset);
            if (leftCode != rightCode) return Integer.compare(leftCode, rightCode);
            leftOffset += Character.charCount(leftCode);
            rightOffset += Character.charCount(rightCode);
        }
        return Integer.compare(left.length() - leftOffset, right.length() - rightOffset);
    }

    private static final class JsonBounds {
        private int nodes;
    }

    public record OwnerReference(
            @NotNull String ownerId,
            @NotNull String recordId,
            @NotNull String recordKind,
            @NotNull String uri,
            @NotNull String digest,
            @NotNull String lastVerifiedState
    ) {
    }

    public record Command(
            @NotNull String commandId, @NotNull String intent, boolean shell
    ) {
    }

    public record RawRange(
            @NotNull String stream,
            @NotNull String artifact,
            long byteStart,
            long byteEnd,
            @NotNull String boundary
    ) {
    }

    public record Event(
            @NotNull String eventId,
            long sequence,
            @NotNull String kind,
            @NotNull String severity,
            @NotNull String subsystem,
            @NotNull String message,
            @NotNull RawRange rawRange
    ) {
    }

    public record Claim(
            @NotNull Event event,
            @NotNull String claimState,
            @NotNull String ownerId
    ) {
    }

    public record Classification(
            @NotNull String classificationId,
            @NotNull String claimState,
            @NotNull String ownerId,
            @NotNull String ownerRecordId,
            @NotNull String ownerRecordKind,
            @NotNull String ownerRecordUri,
            @NotNull String ownerRecordDigest,
            @NotNull String stage,
            @NotNull String state,
            @NotNull List<String> requiredMarkers,
            @NotNull List<String> observedMarkers,
            @Nullable Integer effectiveExitCode,
            @Nullable Boolean cleanupContained,
            @Nullable String artifactDigest,
            @NotNull String detail
    ) {
        public Classification {
            requiredMarkers = List.copyOf(requiredMarkers);
            observedMarkers = List.copyOf(observedMarkers);
        }
    }

    public record Unknown(
            @NotNull String id,
            @NotNull String claimState,
            @NotNull String ownerId,
            @NotNull String detail
    ) {
    }

    public record NextAction(
            @NotNull String actionId,
            @NotNull JsonObject arguments,
            @NotNull String contextDigest,
            @NotNull String mutation
    ) {
        public NextAction {
            arguments = arguments.deepCopy();
        }

        @Override
        public @NotNull JsonObject arguments() {
            return arguments.deepCopy();
        }
    }

    public record Diagnosis(
            @NotNull String rawJson,
            @NotNull String diagnosisId,
            @Nullable String workSessionId,
            @NotNull String outcome,
            @NotNull OwnerReference targetOwner,
            @NotNull OwnerReference currentOwner,
            @NotNull Command command,
            @NotNull List<Event> timeline,
            @NotNull List<Claim> observedFailures,
            @NotNull List<Claim> wrappers,
            @NotNull List<Claim> secondaryFailures,
            @NotNull List<Claim> shutdownNoise,
            @NotNull List<Claim> contributingConditions,
            @NotNull List<Classification> classifications,
            @NotNull List<Unknown> unknowns,
            @NotNull List<NextAction> nextActions,
            @NotNull String fingerprint,
            @NotNull List<String> limitations
    ) {
        public Diagnosis {
            timeline = List.copyOf(timeline);
            observedFailures = List.copyOf(observedFailures);
            wrappers = List.copyOf(wrappers);
            secondaryFailures = List.copyOf(secondaryFailures);
            shutdownNoise = List.copyOf(shutdownNoise);
            contributingConditions = List.copyOf(contributingConditions);
            classifications = List.copyOf(classifications);
            unknowns = List.copyOf(unknowns);
            nextActions = List.copyOf(nextActions);
            limitations = List.copyOf(limitations);
        }
    }

    public record ReplayAction(
            @NotNull String actionId,
            @NotNull JsonObject arguments,
            @NotNull String mutation
    ) {
        public ReplayAction {
            arguments = arguments.deepCopy();
        }

        @Override
        public @NotNull JsonObject arguments() {
            return arguments.deepCopy();
        }
    }

    public record PrivacyReview(boolean approved, @NotNull List<String> excluded) {
        public PrivacyReview {
            excluded = List.copyOf(excluded);
        }
    }

    public record CapsuleInspection(
            @NotNull String rawJson,
            @NotNull String capsuleId,
            @NotNull String diagnosisId,
            @NotNull String fingerprint,
            @NotNull ReplayAction replayAction,
            @NotNull PrivacyReview privacyReview,
            @NotNull List<String> limitations
    ) {
        public CapsuleInspection {
            limitations = List.copyOf(limitations);
        }
    }
}
