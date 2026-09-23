package dev.workbench.crucible.runtimegraph.adapter;

import com.cleanroommc.groovyscript.registry.NamedRegistry;
import com.cleanroommc.groovyscript.registry.VirtualizedRegistry;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;
import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import gregtech.api.recipes.Recipe;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.info.MaterialFlag;
import gregtech.api.unification.material.info.MaterialIconSet;
import gregtech.api.unification.material.properties.IMaterialProperty;
import gregtech.api.unification.material.properties.PropertyKey;
import gregtech.api.unification.material.registry.MaterialRegistry;

import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.IForgeRegistry;
import net.minecraftforge.registries.IForgeRegistryEntry;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Causal Groovy mutation events with exact source and surviving-object joins. */
public final class GroovyMutationEventAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "groovy-mutation-events"; }
    @Override public String categoryId() { return "pack-program-mutation"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        ProgramProvenanceTrace.requireSettled();
        GroovySourceCatalog.Catalog catalog = GroovySourceCatalog.capture();
        Map<Long, ProgramProvenanceTrace.Execution> executions =
            new LinkedHashMap<Long, ProgramProvenanceTrace.Execution>();
        for (ProgramProvenanceTrace.Execution execution : ProgramProvenanceTrace.executions()) {
            executions.put(Long.valueOf(execution.getOrdinal()), execution);
        }

        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (ProgramProvenanceTrace.Mutation mutation : ProgramProvenanceTrace.mutations()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "groovy-mutation-event");
            row.addProperty("mutation_ordinal", mutation.getOrdinal());
            row.addProperty("sequence", mutation.getSequence());
            row.addProperty("epoch_ordinal", mutation.getEpochOrdinal());
            row.addProperty("operation", mutation.getOperation());
            String semanticRole = semanticRole(mutation.getOperation());
            row.addProperty("semantic_role", semanticRole);
            row.addProperty(
                "retains_nonfinal_occurrence",
                "removal-tombstone".equals(semanticRole)
            );
            row.addProperty("final_membership_is_independent", true);
            if (mutation.getExecutionOrdinal() == null) {
                row.add("execution_ordinal", JsonNull.INSTANCE);
                row.add("execution_source_path", JsonNull.INSTANCE);
                row.addProperty("attribution", "epoch-framework-operation");
            } else {
                ProgramProvenanceTrace.Execution execution = executions.get(
                    mutation.getExecutionOrdinal()
                );
                if (execution == null) {
                    throw new IllegalStateException("mutation refers to an unknown execution");
                }
                String source = catalog.resolveSource(execution.getClassName());
                if (source == null) {
                    throw new IllegalStateException("mutation execution source is unresolved");
                }
                row.addProperty("execution_ordinal", mutation.getExecutionOrdinal());
                row.addProperty("execution_source_path", source);
                row.addProperty("attribution", "script-execution");
            }
            row.add("target", target(mutation.getTarget()));

            JsonArray subjects = new JsonArray();
            for (int ordinal = 0; ordinal < mutation.getSubjects().size(); ordinal++) {
                JsonObject subject = subject(mutation.getSubjects().get(ordinal), encoder);
                subject.addProperty("ordinal", ordinal);
                addVirtualStorageIdentity(subject, mutation, mutation.getSubjects().get(ordinal));
                subjects.add(subject);
            }
            row.add("subjects", subjects);

            JsonArray frames = sourceFrames(mutation.getFrames(), catalog);
            if (frames.size() > 0) {
                row.addProperty("call_chain_resolution", "direct-source-frame");
                row.add("unresolved_bridge_call_chain", new JsonArray());
            } else if (mutation.getExecutionOrdinal() != null) {
                row.addProperty("call_chain_resolution", "execution-context-only");
                row.add(
                    "unresolved_bridge_call_chain",
                    runtimeFrames(mutation.getFrames())
                );
            } else {
                row.addProperty("call_chain_resolution", "epoch-framework-only");
                row.add("unresolved_bridge_call_chain", new JsonArray());
            }
            row.addProperty("call_chain_truncated", mutation.areFramesTruncated());
            row.add("source_call_chain", frames);
            records.add(row);
        }
        if (records.isEmpty()) throw new IllegalStateException("Groovy mutation event trace is empty");
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static String semanticRole(String operation) {
        if (operation == null) throw new IllegalStateException("mutation operation is null");
        if (operation.contains("remove") || operation.contains("backup-before-remove")) {
            return "removal-tombstone";
        }
        if (operation.contains("add") || operation.contains("register")
            || operation.contains("mark-scripted")) {
            return "addition-occurrence";
        }
        return "in-place-mutation-occurrence";
    }

    private static JsonObject target(Object value) {
        JsonObject result = new JsonObject();
        if (value == null) {
            result.addProperty("target_kind", "null");
            result.add("runtime_class", JsonNull.INSTANCE);
            return result;
        }
        result.addProperty("runtime_class", value.getClass().getName());
        if (value instanceof Material) {
            result.addProperty("target_kind", "gt-material");
            result.addProperty("material", ((Material) value).getResourceLocation().toString());
        } else if (value instanceof MaterialRegistry) {
            result.addProperty("target_kind", "gt-material-registry");
            result.addProperty("registry_modid", ((MaterialRegistry) value).getModid());
        } else if (value instanceof NamedRegistry) {
            NamedRegistry registry = (NamedRegistry) value;
            result.addProperty("target_kind", "groovy-virtualized-registry");
            result.addProperty("registry_name", registry.getName());
            JsonArray aliases = new JsonArray();
            List<String> values = new ArrayList<String>(registry.getAliases());
            Collections.sort(values);
            for (String alias : values) aliases.add(alias);
            result.add("aliases", aliases);
        } else if (value instanceof IForgeRegistry<?>) {
            result.addProperty("target_kind", "forge-registry");
            result.addProperty(
                "registry_supertype",
                ((IForgeRegistry<?>) value).getRegistrySuperType().getName()
            );
        } else if (value instanceof Class<?>) {
            result.addProperty("target_kind", "reloadable-registry-class");
            result.addProperty("class_name", ((Class<?>) value).getName());
        } else {
            result.addProperty("target_kind", "opaque-runtime-target");
        }
        return result;
    }

    private static JsonObject subject(Object value, StableValueEncoder encoder) {
        JsonObject result = new JsonObject();
        if (value == null) {
            result.addProperty("subject_kind", "null");
            result.add("runtime_class", JsonNull.INSTANCE);
            return result;
        }
        result.addProperty("runtime_class", value.getClass().getName());
        if (value instanceof Recipe) {
            Recipe recipe = (Recipe) value;
            result.addProperty("subject_kind", "gt-recipe");
            JsonObject identity = GtRecipeCapture.identityFor(recipe);
            if (identity == null) {
                result.add("final_recipe_identity", JsonNull.INSTANCE);
                result.addProperty("final_state", "not-present");
            } else {
                result.add("final_recipe_identity", identity);
                result.addProperty("final_state", "present");
            }
            JsonObject projection = GtRecipeCapture.encodeRecipe(recipe, encoder);
            result.addProperty("semantic_sha256", CanonicalJson.sha256(projection));
            result.add("recipe", projection);
        } else if (value instanceof Material) {
            result.addProperty("subject_kind", "gt-material");
            result.addProperty("material", ((Material) value).getResourceLocation().toString());
        } else if (value instanceof MaterialFlag) {
            result.addProperty("subject_kind", "gt-material-flag");
            result.addProperty("flag", value.toString());
        } else if (value instanceof MaterialIconSet) {
            result.addProperty("subject_kind", "gt-material-icon-set");
            result.addProperty("icon_set", value.toString());
        } else if (value instanceof PropertyKey<?>) {
            result.addProperty("subject_kind", "gt-material-property-key");
            result.addProperty("property_key", value.toString());
        } else if (value instanceof IMaterialProperty) {
            result.addProperty("subject_kind", "gt-material-property-value");
            result.add("value", encoder.encode(value));
        } else if (value instanceof IForgeRegistryEntry<?>) {
            result.addProperty("subject_kind", "forge-registry-entry");
            ResourceLocation name = ((IForgeRegistryEntry<?>) value).getRegistryName();
            if (name == null) result.add("registry_name", JsonNull.INSTANCE);
            else result.addProperty("registry_name", name.toString());
        } else if (value instanceof ResourceLocation) {
            result.addProperty("subject_kind", "resource-location");
            result.addProperty("value", value.toString());
        } else if (value instanceof String || value instanceof Character
            || value instanceof Number || value instanceof Boolean) {
            result.addProperty("subject_kind", "scalar");
            result.add("value", scalar(value));
        } else if (value instanceof Class<?>) {
            result.addProperty("subject_kind", "class");
            result.addProperty("class_name", ((Class<?>) value).getName());
        } else {
            result.addProperty("subject_kind", "opaque-runtime-object");
            result.addProperty("semantic_identity_captured", false);
        }
        return result;
    }

    private static JsonElement scalar(Object value) {
        if (value instanceof Boolean) return new JsonPrimitive((Boolean) value);
        if (value instanceof Byte || value instanceof Short || value instanceof Integer) {
            return new JsonPrimitive(((Number) value).intValue());
        }
        if (value instanceof Long) return new JsonPrimitive(((Long) value).longValue());
        if (value instanceof Float || value instanceof Double) {
            JsonObject typed = new JsonObject();
            typed.addProperty("value_kind", value instanceof Float ? "float32" : "float64");
            typed.addProperty("decimal", value.toString());
            return typed;
        }
        return new JsonPrimitive(String.valueOf(value));
    }

    private static void addVirtualStorageIdentity(
        JsonObject subject,
        ProgramProvenanceTrace.Mutation mutation,
        Object value
    ) {
        if (!(mutation.getTarget() instanceof VirtualizedRegistry<?>)) return;
        VirtualizedRegistry<?> registry = (VirtualizedRegistry<?>) mutation.getTarget();
        int scripted = identityOrdinal(registry.getScriptedRecipes(), value);
        int backup = identityOrdinal(registry.getBackupRecipes(), value);
        JsonObject storage = new JsonObject();
        storage.addProperty("scripted_ordinal", scripted);
        storage.addProperty("backup_ordinal", backup);
        storage.addProperty("present_in_scripted_storage", scripted >= 0);
        storage.addProperty("present_in_backup_storage", backup >= 0);
        subject.add("virtualized_storage_identity", storage);
    }

    private static int identityOrdinal(Collection<?> values, Object target) {
        int ordinal = 0;
        for (Object value : values) {
            if (value == target) return ordinal;
            ordinal++;
        }
        return -1;
    }

    private static JsonArray sourceFrames(
        List<StackTraceElement> raw,
        GroovySourceCatalog.Catalog catalog
    ) {
        JsonArray result = new JsonArray();
        int ordinal = 0;
        for (StackTraceElement frame : raw) {
            String source = catalog.resolveSource(frame.getClassName());
            if (source == null) continue;
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", ordinal++);
            row.addProperty("source_path", source);
            row.addProperty("compiled_class_name", frame.getClassName());
            row.addProperty("method_name", frame.getMethodName());
            row.addProperty("line", frame.getLineNumber());
            result.add(row);
        }
        return result;
    }

    private static JsonArray runtimeFrames(List<StackTraceElement> raw) {
        JsonArray result = new JsonArray();
        int ordinal = 0;
        for (StackTraceElement frame : raw) {
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", ordinal++);
            row.addProperty("runtime_class", frame.getClassName());
            row.addProperty("method_name", frame.getMethodName());
            if (frame.getFileName() == null) row.add("file_name", JsonNull.INSTANCE);
            else row.addProperty("file_name", frame.getFileName());
            row.addProperty("line", frame.getLineNumber());
            row.addProperty("native_method", frame.isNativeMethod());
            result.add(row);
        }
        return result;
    }
}
