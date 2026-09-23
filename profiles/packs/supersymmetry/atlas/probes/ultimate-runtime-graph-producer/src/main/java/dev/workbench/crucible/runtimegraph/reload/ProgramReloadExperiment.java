package dev.workbench.crucible.runtimegraph.reload;

import com.cleanroommc.groovyscript.GroovyScript;
import com.cleanroommc.groovyscript.sandbox.LoadStage;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.adapter.BetterQuestingDefinitionAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeCraftingRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeFluidAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeOreDictionaryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeRegistryAdapter;
import dev.workbench.crucible.runtimegraph.adapter.ForgeSmeltingRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtMaterialAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtRecipeAdapter;
import dev.workbench.crucible.runtimegraph.adapter.GtUnificationAdapter;
import dev.workbench.crucible.runtimegraph.adapter.PyrotechRecipeAdapter;
import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/** Executes one explicitly armed POST_INIT reload and retains before/after custody. */
public final class ProgramReloadExperiment {
    private static final int SEMANTIC_DELTA_EXAMPLE_LIMIT = 256;
    private static final List<CaptureAdapter> LENSES = Arrays.<CaptureAdapter>asList(
        new ForgeRegistryAdapter(),
        new ForgeFluidAdapter(),
        new ForgeOreDictionaryAdapter(),
        new GtMaterialAdapter(),
        new GtUnificationAdapter(),
        new ForgeCraftingRecipeAdapter(),
        new ForgeSmeltingRecipeAdapter(),
        new GtRecipeAdapter(),
        new PyrotechRecipeAdapter(),
        new BetterQuestingDefinitionAdapter()
    );

    private static boolean started;
    private static boolean completed;
    private static long reloadEpochOrdinal = -1L;
    private static int mutationCountBefore;
    private static int mutationCountAfter;
    private static int reloadExecutionFailureCount;
    private static final List<LensComparison> COMPARISONS =
        new ArrayList<LensComparison>();
    private static final Map<String, Integer> RELOAD_OPERATION_COUNTS =
        new TreeMap<String, Integer>();

    private ProgramReloadExperiment() {}

    public static synchronized void execute() {
        if (started) throw new IllegalStateException("program reload experiment repeated");
        started = true;
        ProgramProvenanceTrace.requireSettled();
        int epochCountBefore = ProgramProvenanceTrace.epochs().size();
        mutationCountBefore = ProgramProvenanceTrace.mutations().size();
        Map<String, LensSnapshot> before = snapshots();

        GroovyScript.runGroovyScriptsInLoader(LoadStage.POST_INIT);
        ProgramProvenanceTrace.requireSettled();

        List<ProgramProvenanceTrace.Epoch> epochs = ProgramProvenanceTrace.epochs();
        if (epochs.size() != epochCountBefore + 1) {
            throw new IllegalStateException("reload did not produce exactly one Groovy epoch");
        }
        ProgramProvenanceTrace.Epoch epoch = epochs.get(epochCountBefore);
        if (
            !epoch.isCompleted()
            || epoch.isInitialLoad()
            || !epoch.isReloadable()
            || !"groovy-load-stage".equals(epoch.getKind())
            || !"postInit".equals(epoch.getStage())
        ) {
            throw new IllegalStateException("reload epoch custody differs");
        }
        reloadEpochOrdinal = epoch.getOrdinal();
        Map<String, LensSnapshot> after = snapshots();
        if (!before.keySet().equals(after.keySet())) {
            throw new IllegalStateException("reload comparison lens closure differs");
        }
        for (String adapterId : before.keySet()) {
            COMPARISONS.add(new LensComparison(before.get(adapterId), after.get(adapterId)));
        }

        mutationCountAfter = ProgramProvenanceTrace.mutations().size();
        for (ProgramProvenanceTrace.Mutation mutation : ProgramProvenanceTrace.mutations()) {
            if (mutation.getEpochOrdinal() != reloadEpochOrdinal) continue;
            Integer current = RELOAD_OPERATION_COUNTS.get(mutation.getOperation());
            RELOAD_OPERATION_COUNTS.put(
                mutation.getOperation(),
                Integer.valueOf(current == null ? 1 : current.intValue() + 1)
            );
        }
        for (ProgramProvenanceTrace.Execution execution : ProgramProvenanceTrace.executions()) {
            if (
                execution.getEpochOrdinal() == reloadEpochOrdinal
                && execution.getFailureClass() != null
            ) {
                reloadExecutionFailureCount++;
            }
        }
        completed = true;
    }

    private static Map<String, LensSnapshot> snapshots() {
        Map<String, LensSnapshot> result = new LinkedHashMap<String, LensSnapshot>();
        for (CaptureAdapter adapter : LENSES) {
            AdapterSnapshot snapshot = adapter.snapshot();
            if (!snapshot.diagnostics().isEmpty() || snapshot.unsupportedValueCount() != 0) {
                throw new IllegalStateException(
                    "reload comparison lens is not exact: " + adapter.adapterId()
                );
            }
            LensSnapshot row = new LensSnapshot(
                adapter.adapterId(),
                adapter.categoryId(),
                snapshot.recordCount(),
                snapshot.recordsSha256(),
                snapshot.records()
            );
            if (result.put(row.adapterId, row) != null) {
                throw new IllegalStateException("duplicate reload comparison lens");
            }
        }
        return result;
    }

    public static synchronized List<JsonObject> records() {
        if (!completed) throw new IllegalStateException("program reload experiment is incomplete");
        List<JsonObject> records = new ArrayList<JsonObject>();
        int divergenceCount = 0;
        for (LensComparison comparison : COMPARISONS) {
            JsonObject before = comparison.before.record("pre-reload");
            before.addProperty("reload_epoch_ordinal", reloadEpochOrdinal);
            records.add(before);
            JsonObject after = comparison.after.record("post-reload");
            after.addProperty("reload_epoch_ordinal", reloadEpochOrdinal);
            records.add(after);

            JsonObject row = new JsonObject();
            row.addProperty("record_type", "reload-definition-comparison");
            row.addProperty("adapter_id", comparison.before.adapterId);
            row.addProperty("category_id", comparison.before.categoryId);
            row.addProperty("reload_epoch_ordinal", reloadEpochOrdinal);
            row.addProperty("before_record_count", comparison.before.recordCount);
            row.addProperty("after_record_count", comparison.after.recordCount);
            row.addProperty("before_records_sha256", comparison.before.recordsSha256);
            row.addProperty("after_records_sha256", comparison.after.recordsSha256);
            row.addProperty("record_count_equal", comparison.countEqual());
            row.addProperty("records_equal", comparison.equal());
            row.addProperty(
                "semantic_projection_id", comparison.before.semanticProjectionId
            );
            row.addProperty(
                "semantic_digest_algorithm",
                "sorted-per-record-sha256-json-array-v1"
            );
            row.addProperty(
                "before_semantic_records_sha256",
                comparison.before.semanticRecordsSha256
            );
            row.addProperty(
                "after_semantic_records_sha256",
                comparison.after.semanticRecordsSha256
            );
            row.addProperty("semantic_records_equal", comparison.semanticEqual());
            row.addProperty(
                "before_stable_identity_count", comparison.before.fingerprints.size()
            );
            row.addProperty(
                "after_stable_identity_count", comparison.after.fingerprints.size()
            );
            row.addProperty("added_identity_count", comparison.addedIdentityCount());
            row.addProperty("removed_identity_count", comparison.removedIdentityCount());
            row.addProperty(
                "semantic_changed_identity_count",
                comparison.semanticChangedIdentityCount()
            );
            row.addProperty(
                "exact_only_changed_identity_count",
                comparison.exactOnlyChangedIdentityCount()
            );
            JsonArray examples = comparison.semanticDeltaExamples();
            row.add("semantic_delta_examples", examples);
            row.addProperty(
                "unreported_semantic_delta_identity_count",
                comparison.semanticDeltaIdentityCount() - examples.size()
            );
            row.addProperty(
                "comparison_state", comparison.equal() ? "idempotent" : "divergent"
            );
            row.addProperty(
                "semantic_comparison_state",
                comparison.semanticEqual() ? "idempotent" : "divergent"
            );
            if (!comparison.equal()) divergenceCount++;
            records.add(row);
        }
        for (Map.Entry<String, Integer> operation : RELOAD_OPERATION_COUNTS.entrySet()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "reload-mutation-operation-summary");
            row.addProperty("reload_epoch_ordinal", reloadEpochOrdinal);
            row.addProperty("operation", operation.getKey());
            row.addProperty("occurrence_count", operation.getValue());
            records.add(row);
        }
        JsonObject trigger = new JsonObject();
        trigger.addProperty("record_type", "reload-trigger-observation");
        trigger.addProperty("reload_epoch_ordinal", reloadEpochOrdinal);
        trigger.addProperty("trigger_kind", "workbench-controlled-direct-loader-invocation");
        trigger.addProperty("load_stage", "postInit");
        trigger.addProperty("invocation_count", 1);
        trigger.addProperty("source_tree_mutated", false);
        trigger.addProperty("client_presentation_reload_invoked", false);
        records.add(trigger);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "reload-observation-authority");
        authority.addProperty("reload_epoch_ordinal", reloadEpochOrdinal);
        authority.addProperty("reload_completed", true);
        authority.addProperty("reload_execution_failure_count", reloadExecutionFailureCount);
        authority.addProperty("comparison_lens_count", COMPARISONS.size());
        authority.addProperty("idempotent_lens_count", COMPARISONS.size() - divergenceCount);
        authority.addProperty("divergent_lens_count", divergenceCount);
        int semanticDivergenceCount = 0;
        for (LensComparison comparison : COMPARISONS) {
            if (!comparison.semanticEqual()) semanticDivergenceCount++;
        }
        authority.addProperty(
            "semantic_idempotent_lens_count",
            COMPARISONS.size() - semanticDivergenceCount
        );
        authority.addProperty("semantic_divergent_lens_count", semanticDivergenceCount);
        authority.addProperty("mutation_count_before", mutationCountBefore);
        authority.addProperty("mutation_count_after", mutationCountAfter);
        authority.addProperty(
            "reload_mutation_occurrence_count", mutationCountAfter - mutationCountBefore
        );
        authority.addProperty("reload_mutation_operation_class_count", RELOAD_OPERATION_COUNTS.size());
        authority.addProperty("initial_and_reload_epochs_distinct", true);
        authority.addProperty("source_tree_mutated", false);
        authority.addProperty("client_presentation_reload_invoked", false);
        records.add(authority);
        return records;
    }

    public static synchronized List<String> diagnostics() {
        if (!completed) return Collections.singletonList("reload-experiment-incomplete");
        List<String> diagnostics = new ArrayList<String>();
        if (reloadExecutionFailureCount != 0) {
            diagnostics.add("reload-script-execution-failure");
        }
        for (LensComparison comparison : COMPARISONS) {
            if (!comparison.equal()) {
                diagnostics.add("reload-definition-divergence:" + comparison.before.adapterId);
            }
        }
        Collections.sort(diagnostics);
        return diagnostics;
    }

    private static final class LensSnapshot {
        private final String adapterId;
        private final String categoryId;
        private final int recordCount;
        private final String recordsSha256;
        private final String semanticProjectionId;
        private final String semanticRecordsSha256;
        private final Map<String, FingerprintAggregate> fingerprints;

        private LensSnapshot(
            String adapterId,
            String categoryId,
            int recordCount,
            String recordsSha256,
            JsonArray records
        ) {
            this.adapterId = adapterId;
            this.categoryId = categoryId;
            this.recordCount = recordCount;
            this.recordsSha256 = recordsSha256;
            this.semanticProjectionId = semanticProjectionId(adapterId);
            this.fingerprints = new TreeMap<String, FingerprintAggregate>();
            List<String> semanticHashes = new ArrayList<String>();
            for (JsonElement raw : records) {
                if (!raw.isJsonObject()) {
                    throw new IllegalStateException("reload lens record is not an object");
                }
                JsonObject exact = raw.getAsJsonObject();
                JsonObject semantic = semanticRecord(adapterId, exact);
                String exactHash = CanonicalJson.sha256(exact);
                String semanticHash = CanonicalJson.sha256(semantic);
                String identity = stableIdentity(adapterId, semantic, semanticHash);
                FingerprintAggregate aggregate = fingerprints.get(identity);
                if (aggregate == null) {
                    aggregate = new FingerprintAggregate();
                    fingerprints.put(identity, aggregate);
                }
                aggregate.exactHashes.add(exactHash);
                aggregate.semanticHashes.add(semanticHash);
                semanticHashes.add(semanticHash);
            }
            this.semanticRecordsSha256 = digestStrings(semanticHashes);
            for (FingerprintAggregate aggregate : fingerprints.values()) aggregate.seal();
        }

        private JsonObject record(String phase) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "reload-definition-snapshot");
            row.addProperty("adapter_id", adapterId);
            row.addProperty("category_id", categoryId);
            row.addProperty("phase", phase);
            row.addProperty("record_count", recordCount);
            row.addProperty("records_sha256", recordsSha256);
            return row;
        }
    }

    private static final class LensComparison {
        private final LensSnapshot before;
        private final LensSnapshot after;

        private LensComparison(LensSnapshot before, LensSnapshot after) {
            if (!before.adapterId.equals(after.adapterId)
                || !before.categoryId.equals(after.categoryId)
                || !before.semanticProjectionId.equals(after.semanticProjectionId)) {
                throw new IllegalStateException("reload comparison lens identity differs");
            }
            this.before = before;
            this.after = after;
        }

        private boolean countEqual() {
            return before.recordCount == after.recordCount;
        }

        private boolean equal() {
            return countEqual() && before.recordsSha256.equals(after.recordsSha256);
        }

        private boolean semanticEqual() {
            return countEqual()
                && before.semanticRecordsSha256.equals(after.semanticRecordsSha256);
        }

        private Set<String> addedIdentities() {
            Set<String> result = new LinkedHashSet<String>(after.fingerprints.keySet());
            result.removeAll(before.fingerprints.keySet());
            return result;
        }

        private Set<String> removedIdentities() {
            Set<String> result = new LinkedHashSet<String>(before.fingerprints.keySet());
            result.removeAll(after.fingerprints.keySet());
            return result;
        }

        private int addedIdentityCount() { return addedIdentities().size(); }
        private int removedIdentityCount() { return removedIdentities().size(); }

        private int semanticChangedIdentityCount() {
            int count = 0;
            for (String identity : before.fingerprints.keySet()) {
                FingerprintAggregate right = after.fingerprints.get(identity);
                if (right != null
                    && !before.fingerprints.get(identity).semanticDigest.equals(
                        right.semanticDigest
                    )) count++;
            }
            return count;
        }

        private int exactOnlyChangedIdentityCount() {
            int count = 0;
            for (String identity : before.fingerprints.keySet()) {
                FingerprintAggregate left = before.fingerprints.get(identity);
                FingerprintAggregate right = after.fingerprints.get(identity);
                if (right != null
                    && left.semanticDigest.equals(right.semanticDigest)
                    && !left.exactDigest.equals(right.exactDigest)) count++;
            }
            return count;
        }

        private int semanticDeltaIdentityCount() {
            return addedIdentityCount()
                + removedIdentityCount()
                + semanticChangedIdentityCount();
        }

        private JsonArray semanticDeltaExamples() {
            List<JsonObject> values = new ArrayList<JsonObject>();
            for (String identity : removedIdentities()) {
                values.add(delta(identity, "removed"));
            }
            for (String identity : addedIdentities()) {
                values.add(delta(identity, "added"));
            }
            for (String identity : before.fingerprints.keySet()) {
                FingerprintAggregate right = after.fingerprints.get(identity);
                if (right != null
                    && !before.fingerprints.get(identity).semanticDigest.equals(
                        right.semanticDigest
                    )) values.add(delta(identity, "changed"));
            }
            Collections.sort(values, (left, right) -> CanonicalJson.compareUnsigned(
                CanonicalJson.bytes(left), CanonicalJson.bytes(right)
            ));
            JsonArray result = new JsonArray();
            for (int index = 0;
                 index < values.size() && index < SEMANTIC_DELTA_EXAMPLE_LIMIT;
                 index++) {
                result.add(values.get(index));
            }
            return result;
        }

        private JsonObject delta(String identity, String state) {
            FingerprintAggregate left = before.fingerprints.get(identity);
            FingerprintAggregate right = after.fingerprints.get(identity);
            JsonObject row = new JsonObject();
            row.addProperty("identity", identity);
            row.addProperty("state", state);
            row.addProperty("before_occurrence_count", left == null ? 0 : left.count);
            row.addProperty("after_occurrence_count", right == null ? 0 : right.count);
            return row;
        }
    }

    private static final class FingerprintAggregate {
        private final List<String> exactHashes = new ArrayList<String>();
        private final List<String> semanticHashes = new ArrayList<String>();
        private int count;
        private String exactDigest;
        private String semanticDigest;

        private void seal() {
            count = exactHashes.size();
            if (count == 0 || count != semanticHashes.size()) {
                throw new IllegalStateException("reload fingerprint aggregate is empty");
            }
            exactDigest = digestStrings(exactHashes);
            semanticDigest = digestStrings(semanticHashes);
        }
    }

    private static String digestStrings(List<String> values) {
        List<String> ordered = new ArrayList<String>(values);
        Collections.sort(ordered);
        JsonArray array = new JsonArray();
        for (String value : ordered) array.add(value);
        return CanonicalJson.sha256(array);
    }

    private static String semanticProjectionId(String adapterId) {
        if ("forge-crafting-recipes".equals(adapterId)) {
            return "forge-crafting-definition-without-numeric-binding-v1";
        }
        if ("forge-ore-dictionary".equals(adapterId)) {
            return "forge-ore-dictionary-without-name-numeric-binding-v1";
        }
        if ("forge-registries".equals(adapterId)) {
            return "forge-registry-definition-without-numeric-bindings-v1";
        }
        if ("gt-recipes".equals(adapterId)) {
            return "gt-recipe-definition-without-ore-numeric-binding-v1";
        }
        if ("pyrotech-custom-recipes".equals(adapterId)) {
            return "pyrotech-definition-without-registry-numeric-binding-v1";
        }
        return "exact-runtime-record-v1";
    }

    private static JsonObject semanticRecord(String adapterId, JsonObject exact) {
        JsonObject result = exact.deepCopy();
        if ("forge-crafting-recipes".equals(adapterId)) {
            result.remove("numeric_id");
            result.remove("registry_selection_ordinal");
        } else if ("forge-ore-dictionary".equals(adapterId)) {
            result.remove("numeric_id");
            result.remove("name_ordinal");
        } else if ("forge-registries".equals(adapterId)) {
            removeFieldRecursively(result, "numeric_id");
            result.remove("blocked_numeric_ids");
        } else if ("gt-recipes".equals(adapterId)) {
            result.remove("semantic_sha256");
            result.remove("duplicate_ordinal");
            removeFieldRecursively(result, "ore_dictionary_id");
        } else if ("pyrotech-custom-recipes".equals(adapterId)) {
            result.remove("numeric_id");
            result.remove("registry_numeric_ordinal");
        }
        return result;
    }

    private static void removeFieldRecursively(JsonElement value, String field) {
        if (value.isJsonObject()) {
            JsonObject object = value.getAsJsonObject();
            object.remove(field);
            for (Map.Entry<String, JsonElement> entry : object.entrySet()) {
                removeFieldRecursively(entry.getValue(), field);
            }
        } else if (value.isJsonArray()) {
            for (JsonElement element : value.getAsJsonArray()) {
                removeFieldRecursively(element, field);
            }
        }
    }

    private static String stableIdentity(
        String adapterId,
        JsonObject semantic,
        String semanticHash
    ) {
        if ("forge-crafting-recipes".equals(adapterId)) {
            return "registry-name:" + requiredString(semantic, "registry_name");
        }
        if ("forge-ore-dictionary".equals(adapterId)) {
            return "ore-name:" + requiredString(semantic, "name");
        }
        if ("forge-registries".equals(adapterId)) {
            return "registry-name:" + requiredString(semantic, "registry_name");
        }
        if ("gt-recipes".equals(adapterId)) {
            JsonElement recipe = semantic.get("recipe");
            if (recipe == null || !recipe.isJsonObject()) {
                throw new IllegalStateException("GT reload record has no recipe payload");
            }
            return "recipe:"
                + requiredString(semantic, "recipe_map") + ':'
                + CanonicalJson.sha256(recipe);
        }
        if ("gt-unification".equals(adapterId)) {
            return "item:"
                + requiredString(semantic, "record_type") + ':'
                + requiredString(semantic, "item_identity_sha256");
        }
        if ("pyrotech-custom-recipes".equals(adapterId)) {
            String recordType = requiredString(semantic, "record_type");
            if ("pyrotech-custom-recipe".equals(recordType)) {
                return "recipe:"
                    + requiredString(semantic, "recipe_registry_name") + ':'
                    + requiredString(semantic, "recipe_name");
            }
            if (semantic.has("registry_name")) {
                return recordType + ":registry-name:"
                    + requiredString(semantic, "registry_name");
            }
            return recordType + ":" + semanticHash;
        }
        return "record-sha256:" + semanticHash;
    }

    private static String requiredString(JsonObject value, String field) {
        JsonElement element = value.get(field);
        if (element == null || !element.isJsonPrimitive()
            || !element.getAsJsonPrimitive().isString()) {
            throw new IllegalStateException("reload record lacks stable identity field " + field);
        }
        return element.getAsString();
    }
}
