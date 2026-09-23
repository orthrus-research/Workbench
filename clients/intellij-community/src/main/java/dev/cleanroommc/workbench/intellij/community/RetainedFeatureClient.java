package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
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
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** Direct no-shell client for retained discovery, current transactions, and owner bytes. */
final class RetainedFeatureClient {
    private RetainedFeatureClient() {
    }

    static @NotNull List<String> recordsArguments(
            @NotNull CoreLaunch launch,
            @Nullable String stateRoot
    ) {
        List<String> arguments = new ArrayList<>(List.of("feature", "records"));
        addStateRoot(arguments, launch, stateRoot);
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    static @NotNull List<String> presentationArguments(
            @NotNull CoreLaunch launch,
            @NotNull FeatureRecordCatalog.Record record,
            @Nullable String stateRoot
    ) {
        List<String> arguments = new ArrayList<>(List.of(
                "feature", "present", record.family(), record.collection(), record.reference()
        ));
        addStateRoot(arguments, launch, stateRoot);
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    static @NotNull List<String> transactionArguments(
            @NotNull CoreLaunch launch,
            @NotNull FeatureRecordCatalog.Record record,
            @Nullable String stateRoot
    ) {
        List<String> arguments = new ArrayList<>(List.of(
                "feature", "transaction", record.family(), record.planId()
        ));
        addStateRoot(arguments, launch, stateRoot);
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    static void validateLink(
            @NotNull FeatureRecordCatalog.Record record,
            @NotNull FeaturePresentation presentation
    ) {
        require(record.family().equals(presentation.family()),
                "Workbench presentation changed record family");
        require(record.collection().equals(presentation.collection()),
                "Workbench presentation changed record collection");
        require(record.recordId().equals(presentation.ownerRecord().id()),
                "Workbench presentation changed record identity");
        require(record.recordKind().equals(presentation.ownerRecord().kind()),
                "Workbench presentation changed record kind");
        require(record.recordState().equals(presentation.ownerRecord().state()),
                "Workbench presentation changed record state");
        require(Objects.equals(
                        record.diagnosticCode(), presentation.ownerRecord().diagnosticCode()),
                "Workbench presentation changed record diagnostic");
        require(record.uri().equals(presentation.ownerRecord().uri()),
                "Workbench presentation changed record URI");
        require(record.planId().equals(presentation.planId()),
                "Workbench presentation changed plan identity");
        require(record.workspaceUri().equals(presentation.workspaceUri()),
                "Workbench presentation changed workspace URI");
        require(record.verificationState().equals(presentation.verification().state()),
                "Workbench presentation changed verification state");
        require(record.operationCount() == presentation.operations().size(),
                "Workbench presentation changed operation count");
    }

    static void validateTransactionLink(
            @NotNull FeatureRecordCatalog.Record selected,
            @NotNull FeaturePresentation presentation,
            @NotNull TransactionView transaction
    ) {
        validateLink(selected, presentation);
        require(selected.family().equals(transaction.family()),
                "Workbench transaction changed selected record family");
        require(selected.planId().equals(transaction.planId()),
                "Workbench transaction changed selected plan identity");
        require(selected.workspaceUri().equals(transaction.workspaceUri()),
                "Workbench transaction changed selected workspace URI");
        require(presentation.planId().equals(transaction.planId()),
                "Workbench transaction changed owner presentation plan identity");
        require(presentation.workspaceUri().equals(transaction.workspaceUri()),
                "Workbench transaction changed owner presentation workspace URI");

        FeatureRecordCatalog.Record linked = transaction.records().stream()
                .filter(record -> record.collection().equals(selected.collection())
                        && record.recordId().equals(selected.recordId()))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException(
                        "Workbench transaction omitted the selected retained record"
                ));
        require(linked.equals(selected),
                "Workbench transaction changed the selected retained record");
        require(presentation.verification().format()
                        .equals(transaction.planFreshness().format())
                        && presentation.verification().planId()
                        .equals(transaction.planFreshness().planId())
                        && presentation.verification().state()
                        .equals(transaction.planFreshness().state())
                        && Objects.equals(
                        presentation.verification().reason(),
                        transaction.planFreshness().reason()
                ), "Workbench transaction freshness changed during owner reopen");
        require(presentation.operations().size() == transaction.operations().size(),
                "Workbench transaction changed the reviewed operation count");
        for (int index = 0; index < transaction.operations().size(); index++) {
            TransactionView.Operation compact = transaction.operations().get(index);
            FeaturePresentation.Operation owner = presentation.operations().get(index);
            require(compact.ordinal() == owner.ordinal()
                            && compact.path().equals(owner.path())
                            && compact.role().equals(owner.role())
                            && compact.diff().equals(owner.diff())
                            && compact.beforeSha256().equals(owner.beforeSha256())
                            && compact.beforeSize() == owner.beforeSize()
                            && compact.afterSha256().equals(owner.afterSha256())
                            && compact.afterSize() == owner.afterSize(),
                    "Workbench transaction changed reviewed operation " + index);
        }
    }

    private static void addStateRoot(
            @NotNull List<String> arguments,
            @NotNull CoreLaunch launch,
            @Nullable String stateRoot
    ) {
        if (stateRoot == null || stateRoot.isBlank()) {
            return;
        }
        arguments.add("--state-root");
        arguments.add(launch.commandPath(stateRoot.trim(), "developer feature state root"));
    }

    private static void require(boolean condition, @NotNull String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }

    /**
     * Strict consumer for the Shell-owned compact current transaction projection.
     * It validates protocol meaning only; all family construction remains in the core.
     */
    static final class TransactionView {
        static final String FORMAT = "workbench-developer-feature-transaction-view-v1";
        static final String KIND = "workbench-developer-feature-transaction-view";
        static final int MAX_BYTES = 16 * 1024 * 1024;
        private static final int MAX_TEXT_BYTES = 16 * 1024 * 1024;
        private static final int MAX_SMALL_TEXT_BYTES = 64 * 1024;
        private static final int MAX_OPERATIONS = 64;
        private static final int MAX_RECORDS = 4096;
        private static final Pattern CONTENT_ID = Pattern.compile(
                "^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$"
        );
        private static final Pattern DIGEST = Pattern.compile("^[0-9a-f]{64}$");
        private static final List<String> COLLECTIONS = List.of(
                "plans", "receipts", "rollbacks", "recoveries", "runs"
        );
        private static final Set<String> FAMILIES = Set.of(
                "material-fluid-recipe", "recipe-change", "quest-for-process"
        );
        private static final Set<String> EFFECTIVE_STATES = Set.of(
                "applied", "drifted", "interrupted", "matches-after-without-receipt",
                "mixed", "planned", "restored", "unavailable"
        );
        private static final Set<String> MATCH_STATES = Set.of(
                "matches-before", "matches-after", "mixed", "drifted", "unavailable"
        );
        private static final Set<String> OPERATION_MATCH_STATES = Set.of(
                "matches-before", "matches-after", "drifted"
        );

        private final String rawJson;
        private final String id;
        private final String family;
        private final String workspaceUri;
        private final String planId;
        private final String currentEffectiveState;
        private final List<String> limitations;
        private final List<Operation> operations;
        private final WorkspaceMatch workspaceMatch;
        private final PlanFreshness planFreshness;
        private final List<FeatureRecordCatalog.Record> records;
        private final List<Action> actions;

        private TransactionView(
                @NotNull String rawJson,
                @NotNull String id,
                @NotNull String family,
                @NotNull String workspaceUri,
                @NotNull String planId,
                @NotNull String currentEffectiveState,
                @NotNull List<String> limitations,
                @NotNull List<Operation> operations,
                @NotNull WorkspaceMatch workspaceMatch,
                @NotNull PlanFreshness planFreshness,
                @NotNull List<FeatureRecordCatalog.Record> records,
                @NotNull List<Action> actions
        ) {
            this.rawJson = rawJson;
            this.id = id;
            this.family = family;
            this.workspaceUri = workspaceUri;
            this.planId = planId;
            this.currentEffectiveState = currentEffectiveState;
            this.limitations = List.copyOf(limitations);
            this.operations = List.copyOf(operations);
            this.workspaceMatch = workspaceMatch;
            this.planFreshness = planFreshness;
            this.records = List.copyOf(records);
            this.actions = List.copyOf(actions);
        }

        static @NotNull TransactionView parse(@NotNull String json) {
            int encodedSize = json.getBytes(StandardCharsets.UTF_8).length;
            require(encodedSize >= 1 && encodedSize <= MAX_BYTES,
                    "Workbench transaction view size is outside the supported bound");
            StrictJson.validate(json, "developer feature transaction view");
            final JsonElement parsed;
            try {
                parsed = JsonParser.parseString(json);
            } catch (RuntimeException error) {
                throw new IllegalArgumentException(
                        "Workbench transaction view is invalid JSON", error
                );
            }
            JsonObject root = object(parsed, "developer feature transaction view");
            exactKeys(root, Set.of(
                    "actions", "current_effective_state", "family", "format", "id",
                    "kind", "limitations", "operations", "plan_freshness", "plan_id",
                    "records", "schema_version", "workspace_match", "workspace_uri"
            ), "developer feature transaction view");
            equal(string(root, "format", false, MAX_SMALL_TEXT_BYTES), FORMAT,
                    "Workbench returned an unsupported transaction format");
            equal(string(root, "kind", false, MAX_SMALL_TEXT_BYTES), KIND,
                    "Workbench returned an unsupported transaction kind");
            require(integer(root, "schema_version") == 1,
                    "Workbench returned an unsupported transaction schema");
            String id = contentId(root, "id");
            require(id.startsWith(KIND + ":sha256:"),
                    "Workbench transaction ID has the wrong kind");
            CanonicalJson.verifySealedObject(root, KIND, id);
            String family = member(root, "family", FAMILIES,
                    "Workbench transaction family is unsupported");
            String workspaceUri = fileUri(root, "workspace_uri");
            String planId = contentId(root, "plan_id");
            FeatureRecordKinds.requireIdKind(
                    planId, FeatureRecordKinds.expected(family, "plans"), "transaction plan identity"
            );
            String effectiveState = member(root, "current_effective_state", EFFECTIVE_STATES,
                    "Workbench transaction effective state is unsupported");
            List<String> limitations = strings(
                    array(root, "limitations"), "transaction limitation", 8192
            );
            List<Operation> operations = operations(root);
            WorkspaceMatch workspaceMatch = workspaceMatch(root, operations);
            PlanFreshness planFreshness = planFreshness(root, planId);
            List<FeatureRecordCatalog.Record> records = records(
                    root, family, planId, workspaceUri, operations.size()
            );
            List<Action> actions = actions(root);

            boolean recoveryAvailable = actions.get(3).available();
            String derivedState = derivedEffectiveState(
                    workspaceMatch.state(), records, recoveryAvailable
            );
            equal(effectiveState, derivedState,
                    "Workbench transaction retained lineage changed effective state");
            validateActions(actions, workspaceMatch, planFreshness, records, planId);

            return new TransactionView(
                    json, id, family, workspaceUri, planId, effectiveState, limitations,
                    operations, workspaceMatch, planFreshness, records, actions
            );
        }

        private static @NotNull List<Operation> operations(@NotNull JsonObject root) {
            JsonArray values = array(root, "operations");
            require(values.size() >= 1 && values.size() <= MAX_OPERATIONS,
                    "Workbench transaction operations are outside the supported bound");
            List<Operation> result = new ArrayList<>();
            for (int index = 0; index < values.size(); index++) {
                JsonObject value = object(values.get(index), "transaction operation");
                exactKeys(value, Set.of(
                        "after_sha256", "after_size", "before_sha256", "before_size",
                        "diff", "ordinal", "path", "role"
                ), "transaction operation");
                int ordinal = integer(value, "ordinal");
                require(ordinal == index,
                        "Workbench transaction operation ordinals must be contiguous from zero");
                String path = string(value, "path", false, MAX_SMALL_TEXT_BYTES);
                validateRelativePath(path);
                long beforeSize = longInteger(value, "before_size");
                long afterSize = longInteger(value, "after_size");
                require(beforeSize >= 0 && afterSize >= 0,
                        "Workbench transaction operation sizes must be nonnegative");
                result.add(new Operation(
                        ordinal,
                        path,
                        string(value, "role", false, MAX_SMALL_TEXT_BYTES),
                        string(value, "diff", true, MAX_TEXT_BYTES),
                        digest(value, "before_sha256"),
                        beforeSize,
                        digest(value, "after_sha256"),
                        afterSize
                ));
            }
            return List.copyOf(result);
        }

        private static @NotNull WorkspaceMatch workspaceMatch(
                @NotNull JsonObject root,
                @NotNull List<Operation> operations
        ) {
            JsonObject value = object(required(root, "workspace_match"), "workspace match");
            exactKeys(value, Set.of("operations", "reason", "state"), "workspace match");
            String state = member(value, "state", MATCH_STATES,
                    "Workbench workspace match state is unsupported");
            String reason = nullableString(value, "reason", MAX_SMALL_TEXT_BYTES);
            JsonArray rows = array(value, "operations");
            if (state.equals("unavailable")) {
                require(rows.isEmpty() && reason != null,
                        "Workbench unavailable workspace match changed");
                return new WorkspaceMatch(state, reason, List.of());
            }
            require(rows.size() == operations.size(),
                    "Workbench workspace match operation count changed");
            require(reason == null, "Workbench available workspace match cannot have a reason");
            List<WorkspaceOperation> parsed = new ArrayList<>();
            for (int index = 0; index < rows.size(); index++) {
                JsonObject row = object(rows.get(index), "workspace operation match");
                exactKeys(row, Set.of(
                        "actual_sha256", "actual_size", "ordinal", "path", "reason", "state"
                ), "workspace operation match");
                Operation operation = operations.get(index);
                int ordinal = integer(row, "ordinal");
                require(ordinal == index, "Workbench workspace operation ordinal changed");
                String path = string(row, "path", false, MAX_SMALL_TEXT_BYTES);
                equal(path, operation.path(), "Workbench workspace operation path changed");
                String matchState = member(row, "state", OPERATION_MATCH_STATES,
                        "Workbench workspace operation match is unsupported");
                String actualDigest = nullableDigest(row, "actual_sha256");
                Long actualSize = nullableLong(row, "actual_size");
                require(actualSize == null || actualSize >= 0,
                        "Workbench workspace operation size must be nonnegative");
                String operationReason = nullableString(row, "reason", MAX_SMALL_TEXT_BYTES);
                if (matchState.equals("matches-before")) {
                    equal(actualDigest, operation.beforeSha256(),
                            "Workbench before-byte workspace match changed");
                    require(actualSize != null && actualSize == operation.beforeSize(),
                            "Workbench before-byte workspace size changed");
                    require(operationReason == null,
                            "Workbench matched before bytes cannot have a drift reason");
                } else if (matchState.equals("matches-after")) {
                    equal(actualDigest, operation.afterSha256(),
                            "Workbench after-byte workspace match changed");
                    require(actualSize != null && actualSize == operation.afterSize(),
                            "Workbench after-byte workspace size changed");
                    require(operationReason == null,
                            "Workbench matched after bytes cannot have a drift reason");
                }
                parsed.add(new WorkspaceOperation(
                        ordinal, path, matchState, actualDigest, actualSize, operationReason
                ));
            }
            Set<String> observed = new HashSet<>();
            parsed.forEach(row -> observed.add(row.state()));
            String derived = observed.equals(Set.of("matches-before"))
                    ? "matches-before"
                    : observed.equals(Set.of("matches-after"))
                    ? "matches-after"
                    : Set.of("matches-before", "matches-after").containsAll(observed)
                    ? "mixed"
                    : "drifted";
            equal(state, derived, "Workbench aggregate workspace match changed");
            return new WorkspaceMatch(state, null, parsed);
        }

        private static @NotNull PlanFreshness planFreshness(
                @NotNull JsonObject root,
                @NotNull String planId
        ) {
            JsonObject value = object(required(root, "plan_freshness"), "plan freshness");
            exactKeys(value, Set.of("format", "plan_id", "reason", "schema_version", "state"),
                    "plan freshness");
            require(integer(value, "schema_version") == 1,
                    "Workbench plan freshness schema is unsupported");
            String linkedPlan = contentId(value, "plan_id");
            equal(linkedPlan, planId, "Workbench plan freshness changed plan identity");
            return new PlanFreshness(
                    string(value, "format", false, MAX_SMALL_TEXT_BYTES),
                    linkedPlan,
                    member(value, "state", Set.of("ready", "stale"),
                            "Workbench plan freshness state is unsupported"),
                    nullableString(value, "reason", MAX_SMALL_TEXT_BYTES)
            );
        }

        private static @NotNull List<FeatureRecordCatalog.Record> records(
                @NotNull JsonObject root,
                @NotNull String family,
                @NotNull String planId,
                @NotNull String workspaceUri,
                int operationCount
        ) {
            JsonArray rows = array(root, "records");
            require(rows.size() <= MAX_RECORDS,
                    "Workbench transaction retained records exceed the supported bound");

            JsonObject filters = new JsonObject();
            filters.addProperty("family", family);
            filters.add("collection", JsonNull.INSTANCE);
            JsonObject envelope = new JsonObject();
            envelope.add("filters", filters);
            envelope.addProperty("format", FeatureRecordCatalog.FORMAT);
            envelope.addProperty("kind", "workbench-developer-feature-record-catalog");
            envelope.add("limitations", new JsonArray());
            envelope.add("records", rows.deepCopy());
            envelope.addProperty("schema_version", 1);
            envelope.addProperty("state_root_uri", workspaceUri);
            envelope.addProperty("id", CanonicalJson.contentId(
                    "workbench-developer-feature-record-catalog", envelope
            ));
            List<FeatureRecordCatalog.Record> records = FeatureRecordCatalog.parse(
                    envelope.toString()
            ).records();
            boolean hasPlan = false;
            for (FeatureRecordCatalog.Record record : records) {
                require(record.planId().equals(planId),
                        "Workbench transaction retained record changed plan identity");
                require(record.workspaceUri().equals(workspaceUri),
                        "Workbench transaction retained record changed workspace URI");
                require(record.operationCount() == operationCount,
                        "Workbench transaction retained record changed operation count");
                if (record.collection().equals("plans") && record.recordId().equals(planId)) {
                    hasPlan = true;
                }
            }
            require(hasPlan, "Workbench transaction omitted its retained plan");
            return records;
        }

        private static @NotNull List<Action> actions(@NotNull JsonObject root) {
            JsonArray values = array(root, "actions");
            require(values.size() == 4,
                    "Workbench transaction must publish exactly four owner actions");
            List<String> expectedOrder = List.of("check", "apply", "rollback", "recover");
            List<Action> result = new ArrayList<>();
            for (int index = 0; index < values.size(); index++) {
                JsonObject value = object(values.get(index), "transaction action");
                exactKeys(value, Set.of(
                        "action", "available", "consent_id", "reason", "record_id"
                ), "transaction action");
                String name = string(value, "action", false, MAX_SMALL_TEXT_BYTES);
                equal(name, expectedOrder.get(index),
                        "Workbench transaction action order or identity changed");
                result.add(new Action(
                        name,
                        bool(value, "available"),
                        nullableContentId(value, "consent_id"),
                        nullableContentId(value, "record_id"),
                        nullableString(value, "reason", MAX_SMALL_TEXT_BYTES)
                ));
            }
            return List.copyOf(result);
        }

        private static void validateActions(
                @NotNull List<Action> actions,
                @NotNull WorkspaceMatch workspaceMatch,
                @NotNull PlanFreshness freshness,
                @NotNull List<FeatureRecordCatalog.Record> records,
                @NotNull String planId
        ) {
            Action check = actions.get(0);
            Action apply = actions.get(1);
            Action rollback = actions.get(2);
            Action recover = actions.get(3);
            require(check.available() && check.consentId() == null
                            && Objects.equals(check.recordId(), planId) && check.reason() == null,
                    "Workbench transaction check action changed");

            boolean applyAvailable = workspaceMatch.state().equals("matches-before")
                    && freshness.state().equals("ready") && !recover.available();
            String applyReason = applyAvailable
                    ? null
                    : recover.available()
                    ? "an interrupted transaction must be recovered before applying"
                    : freshness.state().equals("stale")
                    ? "plan is stale"
                    : "workspace does not match reviewed before bytes";
            require(apply.available() == applyAvailable
                            && Objects.equals(apply.consentId(), applyAvailable ? planId : null)
                            && Objects.equals(apply.recordId(), planId)
                            && Objects.equals(apply.reason(), applyReason),
                    "Workbench transaction apply action changed");

            List<String> appliedReceipts = records.stream()
                    .filter(record -> record.collection().equals("receipts")
                            && record.recordState().equals("applied"))
                    .map(FeatureRecordCatalog.Record::recordId)
                    .sorted()
                    .toList();
            boolean rollbackAvailable = workspaceMatch.state().equals("matches-after")
                    && appliedReceipts.size() == 1 && !recover.available();
            String rollbackReason = rollbackAvailable
                    ? null
                    : recover.available()
                    ? "an interrupted transaction must be recovered before rolling back"
                    : !workspaceMatch.state().equals("matches-after")
                    ? "workspace does not match reviewed after bytes"
                    : "rollback requires one unambiguous retained applied receipt";
            require(rollback.available() == rollbackAvailable
                            && rollback.consentId() == null
                            && Objects.equals(
                            rollback.recordId(),
                            rollbackAvailable ? appliedReceipts.getFirst() : null
                    ) && Objects.equals(rollback.reason(), rollbackReason),
                    "Workbench transaction rollback action changed");

            String recoverReason = recover.available()
                    ? null : "no interrupted transaction is retained";
            require(recover.consentId() == null && Objects.equals(recover.recordId(), planId)
                            && Objects.equals(recover.reason(), recoverReason),
                    "Workbench transaction recovery action changed");
        }

        private static @NotNull String derivedEffectiveState(
                @NotNull String match,
                @NotNull List<FeatureRecordCatalog.Record> records,
                boolean recoveryAvailable
        ) {
            if (recoveryAvailable) {
                return "interrupted";
            }
            boolean applied = records.stream().anyMatch(record ->
                    record.collection().equals("receipts")
                            && record.recordState().equals("applied")
            );
            boolean restored = records.stream().anyMatch(record ->
                    (Set.of("rollbacks", "recoveries").contains(record.collection())
                            && Set.of("restored", "rolled-back").contains(record.recordState()))
                            || (record.collection().equals("receipts")
                            && record.recordState().equals("rejected")
                            && Objects.equals(
                            record.diagnosticCode(),
                            "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
                    ))
            );
            return switch (match) {
                case "matches-before" -> restored ? "restored" : "planned";
                case "matches-after" -> applied ? "applied" : "matches-after-without-receipt";
                default -> match;
            };
        }

        private static void validateRelativePath(@NotNull String path) {
            require(!path.startsWith("/") && path.indexOf('\\') < 0
                            && path.indexOf('\0') < 0,
                    "Workbench transaction operation path is unsafe");
            for (String component : path.split("/", -1)) {
                require(!component.isEmpty() && !component.equals(".")
                                && !component.equals(".."),
                        "Workbench transaction operation path is unsafe");
            }
        }

        private static @NotNull JsonObject object(
                @NotNull JsonElement value,
                @NotNull String label
        ) {
            require(value.isJsonObject(), "Workbench " + label + " must be an object");
            return value.getAsJsonObject();
        }

        private static @NotNull JsonElement required(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
            require(value.has(key), "Workbench transaction view is missing " + key);
            return value.get(key);
        }

        private static @NotNull JsonArray array(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
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
                result.add(boundedString(value, label, false, MAX_SMALL_TEXT_BYTES));
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
            return member.isJsonNull()
                    ? null : boundedString(member, key, false, maximumBytes);
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

        private static @NotNull String contentId(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
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

        private static @NotNull String digest(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
            String parsed = string(value, key, false, MAX_SMALL_TEXT_BYTES);
            require(DIGEST.matcher(parsed).matches(),
                    "Workbench " + key + " is not a canonical SHA-256 digest");
            return parsed;
        }

        private static @Nullable String nullableDigest(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
            JsonElement member = required(value, key);
            if (member.isJsonNull()) {
                return null;
            }
            String parsed = boundedString(member, key, false, MAX_SMALL_TEXT_BYTES);
            require(DIGEST.matcher(parsed).matches(),
                    "Workbench " + key + " is not a canonical SHA-256 digest");
            return parsed;
        }

        private static @NotNull String fileUri(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
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
                throw new IllegalArgumentException(
                        "Workbench " + key + " must be an integer", error
                );
            }
        }

        private static long longInteger(@NotNull JsonObject value, @NotNull String key) {
            JsonElement member = required(value, key);
            require(member.isJsonPrimitive() && member.getAsJsonPrimitive().isNumber(),
                    "Workbench " + key + " must be an integer");
            try {
                return member.getAsBigDecimal().longValueExact();
            } catch (ArithmeticException | NumberFormatException error) {
                throw new IllegalArgumentException(
                        "Workbench " + key + " must be an integer", error
                );
            }
        }

        private static @Nullable Long nullableLong(
                @NotNull JsonObject value,
                @NotNull String key
        ) {
            JsonElement member = required(value, key);
            return member.isJsonNull() ? null : longInteger(value, key);
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
                @NotNull Set<String> expected,
                @NotNull String label
        ) {
            Set<String> actual = value.keySet();
            Set<String> missing = new LinkedHashSet<>(expected);
            missing.removeAll(actual);
            Set<String> extra = new LinkedHashSet<>(actual);
            extra.removeAll(expected);
            require(missing.isEmpty() && extra.isEmpty(),
                    "Workbench " + label + " keys differ; missing=" + missing
                            + "; extra=" + extra);
        }

        private static void equal(
                @Nullable Object actual,
                @Nullable Object expected,
                @NotNull String message
        ) {
            require(Objects.equals(actual, expected), message);
        }

        @NotNull String rawJson() {
            return rawJson;
        }

        @NotNull String id() {
            return id;
        }

        @NotNull String family() {
            return family;
        }

        @NotNull String workspaceUri() {
            return workspaceUri;
        }

        @NotNull String planId() {
            return planId;
        }

        @NotNull String currentEffectiveState() {
            return currentEffectiveState;
        }

        @NotNull List<String> limitations() {
            return limitations;
        }

        @NotNull List<Operation> operations() {
            return operations;
        }

        @NotNull WorkspaceMatch workspaceMatch() {
            return workspaceMatch;
        }

        @NotNull PlanFreshness planFreshness() {
            return planFreshness;
        }

        @NotNull List<FeatureRecordCatalog.Record> records() {
            return records;
        }

        @NotNull List<Action> actions() {
            return actions;
        }

        record Operation(
                int ordinal,
                @NotNull String path,
                @NotNull String role,
                @NotNull String diff,
                @NotNull String beforeSha256,
                long beforeSize,
                @NotNull String afterSha256,
                long afterSize
        ) {
        }

        record WorkspaceOperation(
                int ordinal,
                @NotNull String path,
                @NotNull String state,
                @Nullable String actualSha256,
                @Nullable Long actualSize,
                @Nullable String reason
        ) {
        }

        record WorkspaceMatch(
                @NotNull String state,
                @Nullable String reason,
                @NotNull List<WorkspaceOperation> operations
        ) {
            WorkspaceMatch {
                operations = List.copyOf(operations);
            }
        }

        record PlanFreshness(
                @NotNull String format,
                @NotNull String planId,
                @NotNull String state,
                @Nullable String reason
        ) {
        }

        record Action(
                @NotNull String action,
                boolean available,
                @Nullable String consentId,
                @Nullable String recordId,
                @Nullable String reason
        ) {
        }
    }
}
