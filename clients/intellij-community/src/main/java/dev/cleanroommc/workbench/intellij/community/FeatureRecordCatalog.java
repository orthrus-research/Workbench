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
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict, bounded retained-record index returned by the installed Workbench core. */
public final class FeatureRecordCatalog {
    public static final String FORMAT = "workbench-developer-feature-record-catalog-v1";
    public static final int MAX_BYTES = 16 * 1024 * 1024;
    private static final int MAX_TEXT_BYTES = 64 * 1024;
    private static final int MAX_RECORDS = 4096;
    private static final Pattern CONTENT_ID = Pattern.compile(
            "^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$"
    );
    private static final List<String> COLLECTION_ORDER = List.of(
            "plans", "receipts", "rollbacks", "recoveries", "runs"
    );
    private static final Set<String> COLLECTIONS = Set.copyOf(COLLECTION_ORDER);
    private static final Set<String> FAMILIES = Set.of(
            "material-fluid-recipe", "recipe-change", "quest-for-process"
    );
    private static final Set<String> RUNTIME_FAMILIES = Set.of(
            "material-fluid-recipe", "recipe-change"
    );

    private final String rawJson;
    private final String id;
    private final String stateRootUri;
    private final String familyFilter;
    private final String collectionFilter;
    private final List<String> limitations;
    private final List<Record> records;

    private FeatureRecordCatalog(
            @NotNull String rawJson,
            @NotNull String id,
            @NotNull String stateRootUri,
            @Nullable String familyFilter,
            @Nullable String collectionFilter,
            @NotNull List<String> limitations,
            @NotNull List<Record> records
    ) {
        this.rawJson = rawJson;
        this.id = id;
        this.stateRootUri = stateRootUri;
        this.familyFilter = familyFilter;
        this.collectionFilter = collectionFilter;
        this.limitations = List.copyOf(limitations);
        this.records = List.copyOf(records);
    }

    public static @NotNull FeatureRecordCatalog parse(@NotNull String json) {
        int encodedSize = json.getBytes(StandardCharsets.UTF_8).length;
        require(encodedSize >= 1 && encodedSize <= MAX_BYTES,
                "Workbench feature record catalog size is outside the supported bound");
        StrictJson.validate(json, "feature record catalog");
        final JsonElement parsed;
        try {
            parsed = JsonParser.parseString(json);
        } catch (RuntimeException error) {
            throw new IllegalArgumentException(
                    "Workbench feature record catalog is invalid JSON", error
            );
        }
        JsonObject root = object(parsed, "feature record catalog");
        exactKeys(root, Set.of(
                "filters", "format", "id", "kind", "limitations", "records",
                "schema_version", "state_root_uri"
        ), "feature record catalog");
        equal(string(root, "format", false), FORMAT,
                "Workbench returned an unsupported feature record catalog");
        equal(string(root, "kind", false), "workbench-developer-feature-record-catalog",
                "Workbench returned an unsupported feature record catalog kind");
        require(integer(root, "schema_version") == 1,
                "Workbench returned an unsupported feature record catalog schema");
        String id = contentId(root, "id");
        require(id.startsWith("workbench-developer-feature-record-catalog:sha256:"),
                "Workbench feature record catalog ID has the wrong kind");
        CanonicalJson.verifySealedObject(
                root, "workbench-developer-feature-record-catalog", id
        );
        String stateRootUri = fileUri(root, "state_root_uri");

        JsonObject filters = object(required(root, "filters"), "feature record filters");
        exactKeys(filters, Set.of("collection", "family"), "feature record filters");
        String family = nullableMember(filters, "family", FAMILIES,
                "Workbench feature family filter is unsupported");
        String collection = nullableMember(filters, "collection", COLLECTIONS,
                "Workbench feature collection filter is unsupported");

        List<String> limitations = strings(array(root, "limitations"),
                "record discovery limitation", 4096);
        JsonArray rows = array(root, "records");
        require(rows.size() <= MAX_RECORDS,
                "Workbench feature record catalog exceeds the supported bound");
        List<Record> records = new ArrayList<>();
        Set<String> identities = new HashSet<>();
        for (JsonElement element : rows) {
            Record record = record(element);
            require(identities.add(record.collection() + "\0" + record.recordId()),
                    "Workbench feature record catalog contains a duplicate record");
            require(family == null || family.equals(record.family()),
                    "Workbench record does not match its family filter");
            require(collection == null || collection.equals(record.collection()),
                    "Workbench record does not match its collection filter");
            records.add(record);
        }
        for (int index = 1; index < records.size(); index++) {
            require(compare(records.get(index - 1), records.get(index)) <= 0,
                    "Workbench feature record catalog is not in canonical order");
        }
        return new FeatureRecordCatalog(
                json, id, stateRootUri, family, collection, limitations, records
        );
    }

    private static @NotNull Record record(@NotNull JsonElement element) {
        JsonObject value = object(element, "feature record");
        exactKeys(value, Set.of(
                "collection", "diagnostic_code", "family", "operation_count", "plan_id",
                "record_id", "record_kind", "record_state", "reference", "uri",
                "verification_state", "workspace_uri"
        ), "feature record");
        String family = member(value, "family", FAMILIES,
                "Workbench feature family is unsupported");
        String collection = member(value, "collection", COLLECTIONS,
                "Workbench feature collection is unsupported");
        require(!collection.equals("runs") || RUNTIME_FAMILIES.contains(family),
                "Workbench returned a runtime record for an unsupported family");
        String recordId = contentId(value, "record_id");
        String reference = contentId(value, "reference");
        equal(reference, recordId, "Workbench feature record reference changed identity");
        String recordKind = string(value, "record_kind", false);
        String expectedKind = FeatureRecordKinds.expected(family, collection);
        equal(recordKind, expectedKind,
                "Workbench feature record kind does not match its family and collection");
        FeatureRecordKinds.requireIdKind(recordId, recordKind, "record identity");
        String planId = contentId(value, "plan_id");
        FeatureRecordKinds.requireIdKind(
                planId, FeatureRecordKinds.expected(family, "plans"), "plan identity"
        );
        int operationCount = integer(value, "operation_count");
        require(operationCount >= 1,
                "Workbench feature record has no retained operations");
        return new Record(
                family,
                collection,
                recordId,
                reference,
                recordKind,
                string(value, "record_state", false),
                planId,
                nullableString(value, "diagnostic_code"),
                operationCount,
                member(value, "verification_state", Set.of("ready", "stale"),
                        "Workbench feature verification state is unsupported"),
                fileUri(value, "uri"),
                fileUri(value, "workspace_uri")
        );
    }

    private static int compare(@NotNull Record left, @NotNull Record right) {
        int family = left.family().compareTo(right.family());
        if (family != 0) {
            return family;
        }
        int collection = Integer.compare(
                COLLECTION_ORDER.indexOf(left.collection()),
                COLLECTION_ORDER.indexOf(right.collection())
        );
        return collection != 0 ? collection : left.recordId().compareTo(right.recordId());
    }

    private static @NotNull JsonObject object(@NotNull JsonElement value, @NotNull String label) {
        require(value.isJsonObject(), "Workbench " + label + " must be an object");
        return value.getAsJsonObject();
    }

    private static @NotNull JsonElement required(@NotNull JsonObject value, @NotNull String key) {
        require(value.has(key), "Workbench feature record catalog is missing " + key);
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
            int maximumCount
    ) {
        require(values.size() <= maximumCount,
                "Workbench " + label + " values exceed the supported bound");
        List<String> result = new ArrayList<>();
        for (JsonElement value : values) {
            result.add(boundedString(value, label, false));
        }
        return List.copyOf(result);
    }

    private static @NotNull String string(
            @NotNull JsonObject value,
            @NotNull String key,
            boolean allowEmpty
    ) {
        return boundedString(required(value, key), key, allowEmpty);
    }

    private static @Nullable String nullableString(
            @NotNull JsonObject value,
            @NotNull String key
    ) {
        JsonElement member = required(value, key);
        return member.isJsonNull() ? null : boundedString(member, key, false);
    }

    private static @NotNull String boundedString(
            @NotNull JsonElement value,
            @NotNull String label,
            boolean allowEmpty
    ) {
        require(value.isJsonPrimitive() && value.getAsJsonPrimitive().isString(),
                "Workbench " + label + " must be a string");
        String parsed = value.getAsString();
        int byteLength = parsed.getBytes(StandardCharsets.UTF_8).length;
        require((allowEmpty || byteLength >= 1) && byteLength <= MAX_TEXT_BYTES,
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
        String parsed = string(value, key, false);
        require(CONTENT_ID.matcher(parsed).matches(),
                "Workbench " + key + " is not a canonical content ID");
        return parsed;
    }

    private static @NotNull String fileUri(@NotNull JsonObject value, @NotNull String key) {
        String parsed = string(value, key, false);
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

    private static @NotNull String member(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull Set<String> choices,
            @NotNull String message
    ) {
        String parsed = string(value, key, false);
        require(choices.contains(parsed), message + ": " + parsed);
        return parsed;
    }

    private static @Nullable String nullableMember(
            @NotNull JsonObject value,
            @NotNull String key,
            @NotNull Set<String> choices,
            @NotNull String message
    ) {
        JsonElement member = required(value, key);
        if (member.isJsonNull()) {
            return null;
        }
        String parsed = boundedString(member, key, false);
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

    public @NotNull String id() {
        return id;
    }

    public @NotNull String stateRootUri() {
        return stateRootUri;
    }

    public @Nullable String familyFilter() {
        return familyFilter;
    }

    public @Nullable String collectionFilter() {
        return collectionFilter;
    }

    public @NotNull List<String> limitations() {
        return limitations;
    }

    public @NotNull List<Record> records() {
        return records;
    }

    public record Record(
            @NotNull String family,
            @NotNull String collection,
            @NotNull String recordId,
            @NotNull String reference,
            @NotNull String recordKind,
            @NotNull String recordState,
            @NotNull String planId,
            @Nullable String diagnosticCode,
            int operationCount,
            @NotNull String verificationState,
            @NotNull String uri,
            @NotNull String workspaceUri
    ) {
    }
}
