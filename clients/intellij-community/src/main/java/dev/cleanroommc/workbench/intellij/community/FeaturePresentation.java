package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict consumer for one Shell-owned retained developer-feature presentation. */
public final class FeaturePresentation {
    public static final String FORMAT = "workbench-developer-feature-presentation-v1";
    public static final String FORMAT_V2 = "workbench-developer-feature-presentation-v2";
    public static final int MAX_BYTES = 16 * 1024 * 1024;
    private static final int MAX_TEXT_BYTES = 16 * 1024 * 1024;
    private static final int MAX_SMALL_TEXT_BYTES = 64 * 1024;
    private static final int MAX_OPERATIONS = 4096;
    private static final Pattern CONTENT_ID = Pattern.compile(
            "^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern DIGEST = Pattern.compile("^[0-9a-f]{64}$");
    private static final Set<String> FAMILIES = Set.of(
            "material-fluid-recipe", "recipe-change", "quest-for-process"
    );
    private static final Set<String> COLLECTIONS = Set.of(
            "plans", "receipts", "rollbacks", "recoveries", "runs"
    );
    private static final Set<String> ACTIONS = Set.of(
            "apply", "check", "recover", "rollback", "run"
    );
    private static final Set<String> RUNTIME_FAMILIES = Set.of(
            "material-fluid-recipe", "recipe-change"
    );

    private final String rawJson;
    private final int schemaVersion;
    private final String id;
    private final String family;
    private final String collection;
    private final String workspaceUri;
    private final String planId;
    private final JsonObject authorityBoundary;
    private final JsonObject request;
    private final List<String> limitations;
    private final JsonObject review;
    private final Verification verification;
    private final OwnerRecord ownerRecord;
    private final RuntimeBoundary runtime;
    private final List<Action> actions;
    private final List<Operation> operations;

    private FeaturePresentation(
            @NotNull String rawJson,
            int schemaVersion,
            @NotNull String id,
            @NotNull String family,
            @NotNull String collection,
            @NotNull String workspaceUri,
            @NotNull String planId,
            @NotNull JsonObject authorityBoundary,
            @NotNull JsonObject request,
            @NotNull List<String> limitations,
            @NotNull JsonObject review,
            @NotNull Verification verification,
            @NotNull OwnerRecord ownerRecord,
            @NotNull RuntimeBoundary runtime,
            @NotNull List<Action> actions,
            @NotNull List<Operation> operations
    ) {
        this.rawJson = rawJson;
        this.schemaVersion = schemaVersion;
        this.id = id;
        this.family = family;
        this.collection = collection;
        this.workspaceUri = workspaceUri;
        this.planId = planId;
        this.authorityBoundary = authorityBoundary.deepCopy();
        this.request = request.deepCopy();
        this.limitations = List.copyOf(limitations);
        this.review = review.deepCopy();
        this.verification = verification;
        this.ownerRecord = ownerRecord;
        this.runtime = runtime;
        this.actions = List.copyOf(actions);
        this.operations = List.copyOf(operations);
    }

    public static @NotNull FeaturePresentation parse(@NotNull String json) {
        int encodedSize = json.getBytes(StandardCharsets.UTF_8).length;
        require(encodedSize >= 1 && encodedSize <= MAX_BYTES,
                "Workbench feature presentation size is outside the supported bound");
        StrictJson.validate(json, "feature presentation");
        final JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (RuntimeException error) {
            throw new IllegalArgumentException(
                    "Workbench feature presentation is invalid JSON", error
            );
        }
        JsonObject root = object(parsed, "feature presentation");
        exactKeys(root, Set.of(
                "actions", "authority_boundary", "collection", "family", "format", "id",
                "kind", "limitations", "operations", "owner_record", "plan_id", "request",
                "review", "runtime", "schema_version", "verification", "workspace_uri"
        ), "feature presentation");
        int schemaVersion = integer(root, "schema_version");
        String expectedFormat = switch (schemaVersion) {
            case 1 -> FORMAT;
            case 2 -> FORMAT_V2;
            default -> null;
        };
        require(expectedFormat != null,
                "Workbench returned an unsupported presentation schema");
        equal(string(root, "format", false, MAX_SMALL_TEXT_BYTES), expectedFormat,
                "Workbench returned an unsupported feature presentation");
        equal(string(root, "kind", false, MAX_SMALL_TEXT_BYTES),
                "workbench-developer-feature-presentation",
                "Workbench returned an unsupported presentation kind");
        String id = contentId(root, "id");
        require(id.startsWith("workbench-developer-feature-presentation:sha256:"),
                "Workbench feature presentation ID has the wrong kind");
        CanonicalJson.verifySealedObject(
                root, "workbench-developer-feature-presentation", id
        );
        String family = member(root, "family", FAMILIES,
                "Workbench feature family is unsupported");
        String collection = member(root, "collection", COLLECTIONS,
                "Workbench feature collection is unsupported");
        require(schemaVersion != 2 || collection.equals("runs"),
                "Workbench V2 presentation is reserved for retained runtime runs");
        require(!collection.equals("runs") || RUNTIME_FAMILIES.contains(family),
                "Workbench returned a runtime record for an unsupported family");
        String workspaceUri = fileUri(root, "workspace_uri");
        String planId = contentId(root, "plan_id");

        JsonObject authority = object(required(root, "authority_boundary"),
                "authority boundary");
        JsonObject request = object(required(root, "request"), "feature request");
        JsonObject review = object(required(root, "review"), "feature review");
        List<String> limitations = strings(array(root, "limitations"),
                "feature limitation", MAX_SMALL_TEXT_BYTES, 4096);
        FeatureRecordKinds.requireIdKind(
                planId, FeatureRecordKinds.expected(family, "plans"), "plan identity"
        );
        Verification verification = verification(root, planId);
        OwnerRecord ownerRecord = ownerRecord(root);
        String expectedOwnerKind = FeatureRecordKinds.expected(family, collection);
        equal(ownerRecord.kind(), expectedOwnerKind,
                "Workbench owner record kind does not match its family and collection");
        FeatureRecordKinds.requireIdKind(
                ownerRecord.id(), ownerRecord.kind(), "owner record identity"
        );
        RuntimeBoundary runtime = runtime(root, schemaVersion);
        require(runtime.actionAvailable() == RUNTIME_FAMILIES.contains(family),
                "Workbench runtime action availability contradicts its feature family");
        List<Action> actions = actions(root);
        for (Action action : actions) {
            if (action.action().equals("run") && action.available()) {
                require(RUNTIME_FAMILIES.contains(family)
                                && verification.state().equals("ready"),
                        "Workbench runtime action overclaims availability");
                equal(action.consentId(), planId,
                        "Workbench runtime action consent does not match its plan");
            }
        }
        List<Operation> operations = operations(root);
        return new FeaturePresentation(
                json, schemaVersion, id, family, collection, workspaceUri, planId,
                authority, request,
                limitations, review, verification, ownerRecord, runtime, actions, operations
        );
    }

    private static @NotNull Verification verification(
            @NotNull JsonObject root,
            @NotNull String expectedPlanId
    ) {
        JsonObject value = object(required(root, "verification"), "feature verification");
        exactKeys(value, Set.of("format", "plan_id", "reason", "schema_version", "state"),
                "feature verification");
        String format = string(value, "format", false, MAX_SMALL_TEXT_BYTES);
        require(integer(value, "schema_version") == 1,
                "Workbench returned an unsupported verification schema");
        String planId = contentId(value, "plan_id");
        equal(planId, expectedPlanId, "Workbench verification references a different plan");
        String state = member(value, "state", Set.of("ready", "stale"),
                "Workbench verification state is unsupported");
        return new Verification(format, planId, state,
                nullableString(value, "reason", MAX_SMALL_TEXT_BYTES));
    }

    private static @NotNull OwnerRecord ownerRecord(@NotNull JsonObject root) {
        JsonObject value = object(required(root, "owner_record"), "feature owner record");
        exactKeys(value, Set.of("diagnostic_code", "id", "kind", "state", "uri"),
                "feature owner record");
        return new OwnerRecord(
                nullableString(value, "diagnostic_code", MAX_SMALL_TEXT_BYTES),
                contentId(value, "id"),
                string(value, "kind", false, MAX_SMALL_TEXT_BYTES),
                string(value, "state", false, MAX_SMALL_TEXT_BYTES),
                fileUri(value, "uri")
        );
    }

    private static @NotNull RuntimeBoundary runtime(
            @NotNull JsonObject root,
            int schemaVersion
    ) {
        JsonObject value = object(required(root, "runtime"), "runtime boundary");
        Set<String> fields = schemaVersion == 2
                ? Set.of(
                "action_available", "outcome", "record_id", "requirement", "sides", "state"
        )
                : Set.of(
                "action_available", "outcome", "record_id", "requirement", "state"
        );
        exactKeys(value, fields, "runtime boundary");
        JsonElement requirement = required(value, "requirement");
        require(requirement.isJsonNull() || requirement.isJsonObject(),
                "runtime requirement must be an object or null");
        String recordId = nullableContentId(value, "record_id");
        String state = member(value, "state", Set.of(
                "available-not-observed", "required-not-available", "complete", "incomplete"
        ), "Workbench runtime state is unsupported");
        return new RuntimeBoundary(
                bool(value, "action_available"),
                nullableString(value, "outcome", MAX_SMALL_TEXT_BYTES),
                recordId,
                requirement.isJsonNull() ? null : requirement.getAsJsonObject().deepCopy(),
                state,
                schemaVersion == 2 ? runtimeSides(value) : List.of()
        );
    }

    private static @NotNull List<RuntimeSide> runtimeSides(@NotNull JsonObject runtime) {
        JsonArray values = array(runtime, "sides");
        require(values.size() >= 1 && values.size() <= 16,
                "Workbench runtime sides are outside the supported bound");
        List<RuntimeSide> result = new ArrayList<>();
        Set<String> roles = new HashSet<>();
        for (JsonElement element : values) {
            JsonObject value = object(element, "runtime side");
            exactKeys(value, Set.of(
                    "assertion", "error", "outcome", "probe", "receipt", "role", "state"
            ), "runtime side");
            String role = string(value, "role", false, 128);
            require(roles.add(role), "Workbench returned a duplicate runtime side role");
            result.add(new RuntimeSide(
                    role,
                    string(value, "state", false, MAX_SMALL_TEXT_BYTES),
                    string(value, "outcome", false, MAX_SMALL_TEXT_BYTES),
                    assertionReference(value),
                    runtimeError(value),
                    probeReference(value),
                    receiptCustody(value)
            ));
        }
        return List.copyOf(result);
    }

    private static @Nullable AssertionReference assertionReference(
            @NotNull JsonObject side
    ) {
        JsonElement element = required(side, "assertion");
        if (element.isJsonNull()) {
            return null;
        }
        JsonObject value = object(element, "runtime assertion reference");
        exactKeys(value, Set.of("id", "state"), "runtime assertion reference");
        return new AssertionReference(
                string(value, "id", false, MAX_SMALL_TEXT_BYTES),
                string(value, "state", false, MAX_SMALL_TEXT_BYTES)
        );
    }

    private static @Nullable RuntimeError runtimeError(@NotNull JsonObject side) {
        JsonElement element = required(side, "error");
        if (element.isJsonNull()) {
            return null;
        }
        JsonObject value = object(element, "runtime error");
        exactKeys(value, Set.of("kind", "message", "phase"), "runtime error");
        return new RuntimeError(
                string(value, "kind", false, MAX_SMALL_TEXT_BYTES),
                string(value, "message", false, MAX_SMALL_TEXT_BYTES),
                member(value, "phase", Set.of("assertion", "blocked", "capture", "execution"),
                        "Workbench runtime error phase is unsupported")
        );
    }

    private static @Nullable ProbeReference probeReference(@NotNull JsonObject side) {
        JsonElement element = required(side, "probe");
        if (element.isJsonNull()) {
            return null;
        }
        JsonObject value = object(element, "runtime probe reference");
        exactKeys(value, Set.of("id", "overlay_id", "overlay_uri", "script_uri"),
                "runtime probe reference");
        return new ProbeReference(
                string(value, "id", false, MAX_SMALL_TEXT_BYTES),
                string(value, "overlay_id", false, MAX_SMALL_TEXT_BYTES),
                fileUri(value, "overlay_uri"),
                fileUri(value, "script_uri")
        );
    }

    private static @Nullable ReceiptCustody receiptCustody(@NotNull JsonObject side) {
        JsonElement element = required(side, "receipt");
        if (element.isJsonNull()) {
            return null;
        }
        JsonObject value = object(element, "runtime receipt custody");
        exactKeys(value, Set.of("final_launch", "groovy_log", "runtime_session"),
                "runtime receipt custody");
        JsonElement log = required(value, "groovy_log");
        GroovyReference groovy = null;
        if (!log.isJsonNull()) {
            JsonObject reference = object(log, "retained Groovy reference");
            exactKeys(reference, Set.of("sha256", "size", "uri"),
                    "retained Groovy reference");
            long size = longInteger(reference, "size");
            require(size >= 0, "Workbench retained Groovy size must be nonnegative");
            groovy = new GroovyReference(
                    digest(reference, "sha256"),
                    size,
                    fileUri(reference, "uri")
            );
        }
        return new ReceiptCustody(
                receiptReference(value, "final_launch", "final launch receipt"),
                groovy,
                receiptReference(value, "runtime_session", "runtime session receipt")
        );
    }

    private static @NotNull ReceiptReference receiptReference(
            @NotNull JsonObject custody,
            @NotNull String key,
            @NotNull String label
    ) {
        JsonObject value = object(required(custody, key), label);
        exactKeys(value, Set.of("id", "sha256", "size", "uri"), label);
        long size = longInteger(value, "size");
        require(size >= 1, "Workbench " + label + " size must be positive");
        return new ReceiptReference(
                string(value, "id", false, MAX_SMALL_TEXT_BYTES),
                digest(value, "sha256"),
                size,
                fileUri(value, "uri")
        );
    }

    private static @NotNull List<Action> actions(@NotNull JsonObject root) {
        JsonArray values = array(root, "actions");
        require(values.size() <= 32, "Workbench feature actions exceed the supported bound");
        List<Action> result = new ArrayList<>();
        Set<String> observed = new HashSet<>();
        for (JsonElement element : values) {
            JsonObject value = object(element, "feature action");
            exactKeys(value, Set.of("action", "available", "consent_id", "reason"),
                    "feature action");
            String action = member(value, "action", ACTIONS,
                    "Workbench feature action is unsupported");
            require(observed.add(action), "Workbench returned a duplicate feature action");
            boolean available = bool(value, "available");
            String consentId = nullableContentId(value, "consent_id");
            String reason = nullableString(value, "reason", MAX_SMALL_TEXT_BYTES);
            require(available || reason != null,
                    "an unavailable Workbench action must explain why");
            result.add(new Action(action, available, consentId, reason));
        }
        return List.copyOf(result);
    }

    private static @NotNull List<Operation> operations(@NotNull JsonObject root) {
        JsonArray values = array(root, "operations");
        require(values.size() >= 1 && values.size() <= MAX_OPERATIONS,
                "Workbench feature operations are outside the supported bound");
        List<Operation> result = new ArrayList<>();
        Set<Integer> ordinals = new HashSet<>();
        for (JsonElement element : values) {
            JsonObject value = object(element, "feature operation");
            exactKeys(value, Set.of(
                    "after_base64", "after_sha256", "after_size", "before_base64",
                    "before_sha256", "before_size", "diff", "operation", "ordinal",
                    "outcome", "path", "role"
            ), "feature operation");
            int ordinal = integer(value, "ordinal");
            require(ordinal >= 0 && ordinals.add(ordinal),
                    "Workbench feature operation ordinal is invalid or duplicated");
            String beforeEncoded = string(value, "before_base64", true, MAX_TEXT_BYTES);
            String afterEncoded = string(value, "after_base64", true, MAX_TEXT_BYTES);
            byte[] before = decoded(beforeEncoded, "before bytes");
            byte[] after = decoded(afterEncoded, "after bytes");
            int beforeSize = integer(value, "before_size");
            int afterSize = integer(value, "after_size");
            require(beforeSize >= 0 && afterSize >= 0,
                    "Workbench operation byte size must be nonnegative");
            require(before.length == beforeSize && after.length == afterSize,
                    "Workbench operation byte size does not match its content");
            String beforeDigest = digest(value, "before_sha256");
            String afterDigest = digest(value, "after_sha256");
            equal(hexSha256(before), beforeDigest,
                    "Workbench operation before bytes do not match their digest");
            equal(hexSha256(after), afterDigest,
                    "Workbench operation after bytes do not match their digest");
            String operation = string(value, "operation", false, MAX_SMALL_TEXT_BYTES);
            require(operation.equals("update"), "Workbench feature operation is unsupported");
            result.add(new Operation(
                    ordinal,
                    string(value, "role", false, MAX_SMALL_TEXT_BYTES),
                    string(value, "path", false, MAX_SMALL_TEXT_BYTES),
                    operation,
                    string(value, "outcome", false, MAX_SMALL_TEXT_BYTES),
                    before,
                    beforeDigest,
                    beforeSize,
                    after,
                    afterDigest,
                    afterSize,
                    string(value, "diff", true, MAX_TEXT_BYTES)
            ));
        }
        result.sort(java.util.Comparator.comparingInt(Operation::ordinal));
        for (int index = 0; index < result.size(); index++) {
            require(result.get(index).ordinal() == index,
                    "Workbench operation ordinals must be contiguous from zero");
        }
        return List.copyOf(result);
    }

    private static byte @NotNull [] decoded(@NotNull String value, @NotNull String label) {
        try {
            byte[] bytes = Base64.getDecoder().decode(value);
            require(Base64.getEncoder().encodeToString(bytes).equals(value),
                    "Workbench " + label + " are not canonical base64");
            return bytes;
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("Workbench " + label + " are invalid base64", error);
        }
    }

    private static @NotNull String hexSha256(byte @NotNull [] bytes) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder value = new StringBuilder(64);
            for (byte octet : digest) {
                value.append(String.format("%02x", octet & 0xff));
            }
            return value.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("this Java runtime has no SHA-256 provider", error);
        }
    }

    private static @NotNull JsonObject object(@NotNull JsonElement value, @NotNull String label) {
        require(value.isJsonObject(), "Workbench " + label + " must be an object");
        return value.getAsJsonObject();
    }

    private static @NotNull JsonElement required(@NotNull JsonObject value, @NotNull String key) {
        require(value.has(key), "Workbench feature presentation is missing " + key);
        return value.get(key);
    }

    private static @NotNull JsonArray array(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonArray(), "Workbench " + key + " must be an array");
        return member.getAsJsonArray();
    }

    private static @NotNull List<String> strings(
            @NotNull JsonArray values,
            @NotNull String label,
            int maximumBytes,
            int maximumCount
    ) {
        require(values.size() <= maximumCount,
                "Workbench " + label + " values exceed the supported bound");
        List<String> result = new ArrayList<>();
        for (JsonElement value : values) {
            result.add(boundedString(value, label, true, maximumBytes));
        }
        return List.copyOf(result);
    }

    private static @NotNull String string(
            @NotNull JsonObject value,
            @NotNull String key,
            boolean allowEmpty,
            int maximumBytes
    ) {
        return boundedString(required(value, key), key, allowEmpty, maximumBytes);
    }

    private static @Nullable String nullableString(
            @NotNull JsonObject value,
            @NotNull String key,
            int maximumBytes
    ) {
        JsonElement member = required(value, key);
        return member.isJsonNull() ? null : boundedString(member, key, false, maximumBytes);
    }

    private static @NotNull String boundedString(
            @NotNull JsonElement value,
            @NotNull String label,
            boolean allowEmpty,
            int maximumBytes
    ) {
        require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString(),
                "Workbench " + label + " must be a string");
        String parsed = value.getAsString();
        int bytes = parsed.getBytes(StandardCharsets.UTF_8).length;
        require((allowEmpty || bytes >= 1) && bytes <= maximumBytes,
                "Workbench " + label + " is outside the supported bound");
        for (int offset = 0; offset < parsed.length(); ) {
            int codePoint = parsed.codePointAt(offset);
            require(!(codePoint <= 0x08 || (codePoint >= 0x0b && codePoint <= 0x1f)),
                    "Workbench " + label + " contains an unsupported control character");
            offset += Character.charCount(codePoint);
        }
        return parsed;
    }

    private static @NotNull String contentId(@NotNull JsonObject value, @NotNull String key) {
        String parsed = string(value, key, false, MAX_SMALL_TEXT_BYTES);
        require(CONTENT_ID.matcher(parsed).matches(),
                "Workbench " + key + " is not a canonical content ID");
        return parsed;
    }

    private static @Nullable String nullableContentId(
            @NotNull JsonObject value,
            @NotNull String key
    ) {
        JsonElement member = required(value, key);
        if (member.isJsonNull()) {
            return null;
        }
        String parsed = boundedString(member, key, false, MAX_SMALL_TEXT_BYTES);
        require(CONTENT_ID.matcher(parsed).matches(),
                "Workbench " + key + " is not a canonical content ID");
        return parsed;
    }

    private static @NotNull String digest(@NotNull JsonObject value, @NotNull String key) {
        String parsed = string(value, key, false, MAX_SMALL_TEXT_BYTES);
        require(DIGEST.matcher(parsed).matches(),
                "Workbench " + key + " is not a canonical SHA-256 digest");
        return parsed;
    }

    private static @NotNull String fileUri(@NotNull JsonObject value, @NotNull String key) {
        String parsed = string(value, key, false, MAX_SMALL_TEXT_BYTES);
        try {
            URI uri = new URI(parsed);
            require(uri.isAbsolute() && uri.getScheme().equalsIgnoreCase("file")
                            && !uri.isOpaque() && uri.getPath() != null
                            && uri.getPath().startsWith("/")
                            && uri.getQuery() == null && uri.getFragment() == null,
                    "Workbench " + key + " is not one absolute file URI");
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException(
                    "Workbench " + key + " is not one absolute file URI", error
            );
        }
        return parsed;
    }

    private static int integer(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isNumber(),
                "Workbench " + key + " must be an integer");
        try {
            return member.getAsBigDecimal().intValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalArgumentException("Workbench " + key + " must be an integer", error);
        }
    }

    private static long longInteger(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isNumber(),
                "Workbench " + key + " must be an integer");
        try {
            return member.getAsBigDecimal().longValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalArgumentException("Workbench " + key + " must be an integer", error);
        }
    }

    private static boolean bool(@NotNull JsonObject value, @NotNull String key) {
        JsonElement member = required(value, key);
        require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isBoolean(),
                "Workbench " + key + " must be boolean");
        return member.getAsBoolean();
    }

    private static @NotNull String member(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull Set<String> choices,
            @NotNull String message
    ) {
        String parsed = string(value, key, false, MAX_SMALL_TEXT_BYTES);
        require(choices.contains(parsed), message + ": " + parsed);
        return parsed;
    }

    private static void exactKeys(
            @NotNull JsonObject value,
            @NotNull Set<String> required,
            @NotNull String label
    ) {
        Set<String> actual = value.keySet();
        Set<String> missing = new LinkedHashSet<>(required);
        missing.removeAll(actual);
        Set<String> extra = new LinkedHashSet<>(actual);
        extra.removeAll(required);
        require(missing.isEmpty() && extra.isEmpty(),
                "Workbench " + label + " keys differ; missing=" + missing + "; extra=" + extra);
    }

    private static void equal(Object actual, Object expected, String message) {
        require(expected.equals(actual), message);
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }

    public @NotNull String rawJson() {
        return rawJson;
    }

    public int schemaVersion() {
        return schemaVersion;
    }

    public @NotNull String id() {
        return id;
    }

    public @NotNull String family() {
        return family;
    }

    public @NotNull String collection() {
        return collection;
    }

    public @NotNull String workspaceUri() {
        return workspaceUri;
    }

    public @NotNull String planId() {
        return planId;
    }

    public @NotNull JsonObject authorityBoundary() {
        return authorityBoundary.deepCopy();
    }

    public @NotNull JsonObject request() {
        return request.deepCopy();
    }

    public @NotNull List<String> limitations() {
        return limitations;
    }

    public @NotNull JsonObject review() {
        return review.deepCopy();
    }

    public @NotNull Verification verification() {
        return verification;
    }

    public @NotNull OwnerRecord ownerRecord() {
        return ownerRecord;
    }

    public @NotNull RuntimeBoundary runtime() {
        return runtime;
    }

    public @NotNull List<Action> actions() {
        return actions;
    }

    public @NotNull List<Operation> operations() {
        return operations;
    }

    public record Verification(
            @NotNull String format,
            @NotNull String planId,
            @NotNull String state,
            @Nullable String reason
    ) {
    }

    public record OwnerRecord(
            @Nullable String diagnosticCode,
            @NotNull String id,
            @NotNull String kind,
            @NotNull String state,
            @NotNull String uri
    ) {
    }

    public record RuntimeBoundary(
            boolean actionAvailable,
            @Nullable String outcome,
            @Nullable String recordId,
            @Nullable JsonObject requirement,
            @NotNull String state,
            @NotNull List<RuntimeSide> sides
    ) {
        public RuntimeBoundary {
            sides = List.copyOf(sides);
        }

        @Override
        public @Nullable JsonObject requirement() {
            return requirement == null ? null : requirement.deepCopy();
        }
    }

    public record RuntimeSide(
            @NotNull String role,
            @NotNull String state,
            @NotNull String outcome,
            @Nullable AssertionReference assertion,
            @Nullable RuntimeError error,
            @Nullable ProbeReference probe,
            @Nullable ReceiptCustody receipt
    ) {
    }

    public record AssertionReference(
            @NotNull String id,
            @NotNull String state
    ) {
    }

    public record RuntimeError(
            @NotNull String kind,
            @NotNull String message,
            @NotNull String phase
    ) {
    }

    public record ProbeReference(
            @NotNull String id,
            @NotNull String overlayId,
            @NotNull String overlayUri,
            @NotNull String scriptUri
    ) {
    }

    public record ReceiptCustody(
            @NotNull ReceiptReference finalLaunch,
            @Nullable GroovyReference groovyLog,
            @NotNull ReceiptReference runtimeSession
    ) {
    }

    public record ReceiptReference(
            @NotNull String id,
            @NotNull String sha256,
            long size,
            @NotNull String uri
    ) {
    }

    public record GroovyReference(
            @NotNull String sha256,
            long size,
            @NotNull String uri
    ) {
    }

    public record Action(
            @NotNull String action,
            boolean available,
            @Nullable String consentId,
            @Nullable String reason
    ) {
    }

    public record Operation(
            int ordinal,
            @NotNull String role,
            @NotNull String path,
            @NotNull String operation,
            @NotNull String outcome,
            byte @NotNull [] beforeBytes,
            @NotNull String beforeSha256,
            int beforeSize,
            byte @NotNull [] afterBytes,
            @NotNull String afterSha256,
            int afterSize,
            @NotNull String diff
    ) {
        public Operation {
            beforeBytes = beforeBytes.clone();
            afterBytes = afterBytes.clone();
        }

        @Override
        public byte @NotNull [] beforeBytes() {
            return beforeBytes.clone();
        }

        @Override
        public byte @NotNull [] afterBytes() {
            return afterBytes.clone();
        }
    }
}
