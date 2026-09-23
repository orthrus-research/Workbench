package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.Hashing;
import dev.workbench.crucible.runtimegraph.JsonValueNormalization;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import ivorius.ivtoolkit.blocks.IvBlockCollection;
import ivorius.reccomplex.files.SimpleLeveledRegistry;
import ivorius.reccomplex.json.SerializableStringTypeRegistry;
import ivorius.reccomplex.world.gen.feature.structure.Structure;
import ivorius.reccomplex.world.gen.feature.structure.StructureRegistry;
import ivorius.reccomplex.world.gen.feature.structure.generic.GenericStructure;
import ivorius.reccomplex.world.gen.feature.structure.generic.StructureSaveHandler;
import ivorius.reccomplex.world.gen.feature.structure.generic.generation.GenerationType;

import net.minecraft.block.state.IBlockState;
import net.minecraft.nbt.CompressedStreamTools;
import net.minecraft.nbt.NBTBase;
import net.minecraft.nbt.NBTTagByteArray;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.nbt.NBTTagIntArray;
import net.minecraft.nbt.NBTTagList;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Recurrent Complex registry and serialized definitions without placement or generation. */
public final class RecurrentComplexDefinitionAdapter implements CaptureAdapter {
    private static final Field BLOCK_STATES = ReflectionAccess.requireField(
        IvBlockCollection.class, "blockStates", IBlockState[].class
    );

    @Override public String adapterId() { return "recurrent-complex-structure-definitions"; }
    @Override public String categoryId() { return "recurrent-complex-structure-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        StructureRegistry registry = StructureRegistry.INSTANCE;
        if (registry == null) throw new IllegalStateException("Recurrent Complex registry unavailable");
        List<String> ids = new ArrayList<String>(registry.ids());
        Collections.sort(ids);
        int activeCount = 0;
        int genericCount = 0;
        int generationOccurrenceCount = 0;
        int blockCompositionCount = 0;
        int worldDataMemberCount = 0;
        for (String id : ids) {
            Structure<?> structure = registry.get(id);
            SimpleLeveledRegistry<?>.Status status = registry.status(id);
            if (structure == null || status == null) {
                throw new IllegalStateException("incomplete Recurrent Complex entry " + id);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "recurrent-complex-structure-definition");
            row.addProperty("structure_id", id);
            row.addProperty("active", status.isActive());
            row.addProperty("domain", status.getDomain());
            row.addProperty("level", status.getLevel().toString());
            row.addProperty("runtime_class", structure.getClass().getName());
            row.addProperty("rotatable", structure.isRotatable());
            row.addProperty("mirrorable", structure.isMirrorable());
            row.addProperty("blocking", structure.isBlocking());
            row.addProperty("dependencies_resolved", structure.areDependenciesResolved());
            row.add("size", encoder.encode(structure.size()));
            IvBlockCollection blocks = structure.blockCollection();
            row.addProperty("block_multiplicity", blocks.getBlockMultiplicity());
            blockCompositionCount += captureBlockComposition(id, blocks, records, encoder);
            if (structure instanceof GenericStructure) {
                GenericStructure generic = (GenericStructure) structure;
                JsonElement definition = JsonValueNormalization.typedDecimals(
                    new JsonParser().parse(StructureSaveHandler.INSTANCE.toJSON(generic))
                );
                normalizeStructureRootTransformer(definition, id);
                row.add("canonical_definition", definition);
                worldDataMemberCount += captureWorldData(
                    id, generic.worldDataCompound, records, encoder
                );
                row.addProperty("definition_sha256", CanonicalJson.sha256(definition));
                row.addProperty("root_transformer_identity_normalized", true);
                row.addProperty(
                    "root_transformer_identity_policy", "owning-structure-scoped"
                );
                genericCount++;
                for (int ordinal = 0; ordinal < generic.generationTypes.size(); ordinal++) {
                    GenerationType generationType = generic.generationTypes.get(ordinal);
                    JsonObject occurrence = new JsonObject();
                    occurrence.addProperty(
                        "record_type", "recurrent-complex-generation-type-occurrence"
                    );
                    occurrence.addProperty("structure_id", id);
                    occurrence.addProperty("occurrence_ordinal", ordinal);
                    occurrence.addProperty("generation_type_id", generationType.id());
                    occurrence.addProperty(
                        "registered_type_id",
                        StructureRegistry.GENERATION_TYPES.iDForType(generationType.getClass())
                    );
                    occurrence.addProperty("runtime_class", generationType.getClass().getName());
                    occurrence.addProperty("placer_invoked", false);
                    records.add(occurrence);
                    generationOccurrenceCount++;
                }
            } else {
                row.addProperty("canonical_definition_available", false);
            }
            row.addProperty("generation_invoked", false);
            row.addProperty("placement_invoked", false);
            records.add(row);
            if (status.isActive()) activeCount++;
        }
        int generationTypeCount = captureTypes(
            "generation", StructureRegistry.GENERATION_TYPES, records
        );
        int transformerTypeCount = captureTypes(
            "transformer", StructureRegistry.TRANSFORMERS, records
        );
        if (ids.isEmpty() || activeCount == 0 || genericCount == 0
            || generationOccurrenceCount == 0 || generationTypeCount == 0
            || transformerTypeCount == 0) {
            throw new IllegalStateException("Recurrent Complex definition universe is empty");
        }
        requireExactCounts(
            ids.size(), activeCount, genericCount, generationOccurrenceCount,
            blockCompositionCount, worldDataMemberCount,
            generationTypeCount, transformerTypeCount
        );
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "recurrent-complex-definition-authority");
        authority.addProperty("structure_count", ids.size());
        authority.addProperty("active_structure_count", activeCount);
        authority.addProperty("generic_structure_count", genericCount);
        authority.addProperty("generation_type_occurrence_count", generationOccurrenceCount);
        authority.addProperty("block_state_composition_count", blockCompositionCount);
        authority.addProperty("world_data_member_count", worldDataMemberCount);
        authority.addProperty("generation_type_count", generationTypeCount);
        authority.addProperty("transformer_type_count", transformerTypeCount);
        authority.addProperty("canonical_serializer_used", true);
        authority.addProperty("root_transformer_identity_normalized_count", genericCount);
        authority.addProperty(
            "root_transformer_identity_policy", "owning-structure-scoped"
        );
        authority.addProperty("generation_invoked", false);
        authority.addProperty("placement_invoked", false);
        authority.addProperty("random_id_generation_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void normalizeStructureRootTransformer(
        JsonElement definition,
        String structureId
    ) {
        if (!definition.isJsonObject()) {
            throw new IllegalStateException("RC definition is not an object " + structureId);
        }
        JsonObject outer = definition.getAsJsonObject().getAsJsonObject("transformer");
        JsonObject root = outer == null ? null : outer.getAsJsonObject("transformer");
        if (root == null || !root.has("id") || !root.get("id").isJsonPrimitive()
            || !root.get("id").getAsJsonPrimitive().isString()) {
            throw new IllegalStateException(
                "RC root transformer identity is unavailable " + structureId
            );
        }
        root.addProperty("id", "__workbench_structure_root__");
    }

    private static void requireExactCounts(
        int structureCount,
        int activeCount,
        int genericCount,
        int generationOccurrenceCount,
        int blockCompositionCount,
        int worldDataMemberCount,
        int generationTypeCount,
        int transformerTypeCount
    ) {
        if (structureCount != 522 || activeCount != 331 || genericCount != 522
            || generationOccurrenceCount != 570 || blockCompositionCount != 9430
            || worldDataMemberCount != 1566 || generationTypeCount != 7
            || transformerTypeCount != 11) {
            throw new IllegalStateException(
                "Recurrent Complex definition counts drifted: structures=" + structureCount
                    + ", active=" + activeCount + ", generic=" + genericCount
                    + ", generation_occurrences=" + generationOccurrenceCount
                    + ", block_compositions=" + blockCompositionCount
                    + ", world_data_members=" + worldDataMemberCount
                    + ", generation_types=" + generationTypeCount
                    + ", transformer_types=" + transformerTypeCount
            );
        }
    }

    private static int captureBlockComposition(
        String structureId,
        IvBlockCollection blocks,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        IBlockState[] states = (IBlockState[]) ReflectionAccess.read(BLOCK_STATES, blocks);
        Map<String, StateCount> counts = new LinkedHashMap<String, StateCount>();
        for (IBlockState state : states) {
            JsonElement encoded = encoder.encode(state);
            JsonElement semantic = encoded.deepCopy();
            if (!semantic.isJsonObject()
                || semantic.getAsJsonObject().remove("block_state_id") == null) {
                throw new IllegalStateException(
                    "Recurrent Complex block state lacks launch-scoped numeric identity"
                );
            }
            byte[] canonical = CanonicalJson.bytes(semantic);
            String key = Hashing.sha256(canonical);
            StateCount current = counts.get(key);
            if (current == null) counts.put(key, new StateCount(encoded, semantic, canonical));
            else {
                if (!Arrays.equals(current.canonical, canonical)) {
                    throw new IllegalStateException("block-state digest collision");
                }
                current.count++;
            }
        }
        List<String> keys = new ArrayList<String>(counts.keySet());
        Collections.sort(keys);
        for (String key : keys) {
            StateCount value = counts.get(key);
            JsonObject row = new JsonObject();
            row.addProperty(
                "record_type", "recurrent-complex-block-state-composition"
            );
            row.addProperty("structure_id", structureId);
            row.addProperty("state_sha256", key);
            row.addProperty("block_count", value.count);
            row.add("block_state", value.state);
            row.add("semantic_block_state", value.semanticState);
            records.add(row);
        }
        return keys.size();
    }

    private static int captureWorldData(
        String structureId,
        NBTTagCompound worldData,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        List<String> keys = new ArrayList<String>(worldData.getKeySet());
        Collections.sort(keys);
        for (String key : keys) {
            NBTBase value = worldData.getTag(key);
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "recurrent-complex-world-data-member");
            row.addProperty("structure_id", structureId);
            row.addProperty("member_name", key);
            row.addProperty("tag_id", value.getId());
            row.addProperty("content_sha256", nbtSha256(key, value));
            row.addProperty("element_count", nbtElementCount(value));
            if (value.getId() >= 1 && value.getId() <= 8) {
                row.add("scalar_value", encoder.encode(value));
            }
            records.add(row);
        }
        return keys.size();
    }

    private static String nbtSha256(String key, NBTBase value) {
        NBTTagCompound wrapper = new NBTTagCompound();
        wrapper.setTag(key, value);
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try {
            CompressedStreamTools.writeCompressed(wrapper, output);
        } catch (IOException exception) {
            throw new IllegalStateException("cannot serialize RC world-data member", exception);
        }
        return Hashing.sha256(output.toByteArray());
    }

    private static int nbtElementCount(NBTBase value) {
        if (value instanceof NBTTagList) return ((NBTTagList) value).tagCount();
        if (value instanceof NBTTagCompound) return ((NBTTagCompound) value).getKeySet().size();
        if (value instanceof NBTTagByteArray) return ((NBTTagByteArray) value).getByteArray().length;
        if (value instanceof NBTTagIntArray) return ((NBTTagIntArray) value).getIntArray().length;
        return 1;
    }

    private static int captureTypes(
        String registryKind,
        SerializableStringTypeRegistry<?> registry,
        List<JsonObject> records
    ) {
        Collection<String> registered = registry.allIDs();
        List<String> ids = new ArrayList<String>(registered);
        Collections.sort(ids);
        for (String id : ids) {
            Class<?> type = registry.typeForID(id);
            if (type == null) throw new IllegalStateException("missing RC type " + id);
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "recurrent-complex-registered-type");
            row.addProperty("registry_kind", registryKind);
            row.addProperty("registered_type_id", id);
            row.addProperty("runtime_class", type.getName());
            records.add(row);
        }
        return ids.size();
    }

    private static final class StateCount {
        private final JsonElement state;
        private final JsonElement semanticState;
        private final byte[] canonical;
        private int count = 1;

        private StateCount(
            JsonElement state,
            JsonElement semanticState,
            byte[] canonical
        ) {
            this.state = state;
            this.semanticState = semanticState;
            this.canonical = canonical;
        }
    }
}
