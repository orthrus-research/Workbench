package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.item.ItemStack;
import net.minecraft.item.Item;
import net.minecraft.item.crafting.FurnaceRecipes;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fml.common.ObfuscationReflectionHelper;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/** Final FurnaceRecipes storage, order, public lookup outcomes, and experience mappings. */
public final class ForgeSmeltingRecipeAdapter implements CaptureAdapter {
    private static final Field EXPERIENCE_LIST = experienceListField();

    @Override public String adapterId() { return "forge-smelting-recipes"; }
    @Override public String categoryId() { return "transformation-smelting"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        FurnaceRecipes furnace = FurnaceRecipes.instance();
        Map<ItemStack, ItemStack> smelting = furnace.getSmeltingList();
        Map<?, ?> experience = experience(furnace);
        if (smelting == null || smelting.isEmpty() || experience == null) {
            throw new IllegalStateException("FurnaceRecipes storage is unavailable");
        }
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        List<Map.Entry<ItemStack, ItemStack>> rows =
            new ArrayList<Map.Entry<ItemStack, ItemStack>>(smelting.entrySet());
        int ordinal = 0;
        for (Map.Entry<ItemStack, ItemStack> entry : rows) {
            ItemStack input = entry.getKey();
            ItemStack output = entry.getValue();
            if (input == null || input.isEmpty() || output == null || output.isEmpty()) {
                throw new IllegalStateException("FurnaceRecipes contains an empty row");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "forge-smelting-recipe");
            int currentOrdinal = ordinal++;
            row.addProperty("storage_iteration_ordinal", currentOrdinal);
            row.add("registered_input", encoder.encode(input));
            row.add("registered_output", encoder.encode(output));
            row.add("input_selector", selector(input));
            JsonArray overlapOrdinals = new JsonArray();
            for (int prior = 0; prior < currentOrdinal; prior++) {
                if (selectorsOverlap(rows.get(prior).getKey(), input)) overlapOrdinals.add(prior);
            }
            row.add("overlap_predecessor_ordinals", overlapOrdinals);
            ItemStack selected = furnace.getSmeltingResult(input.copy());
            row.add("public_lookup_output", encoder.encode(selected));
            row.addProperty("public_lookup_matches_registered_output", ItemStack.areItemStacksEqual(
                selected, output
            ) && ItemStack.areItemStackTagsEqual(selected, output));
            row.add(
                "public_output_experience",
                encoder.encode(Float.valueOf(furnace.getSmeltingExperience(output.copy())))
            );
            row.addProperty("lookup_authority", "FurnaceRecipes.getSmeltingResult");
            row.addProperty("lookup_execution_invoked", true);
            int selectedOrdinal = selectedOrdinal(rows, input);
            if (selectedOrdinal < 0) {
                throw new IllegalStateException("FurnaceRecipes public selector has no storage row");
            }
            row.addProperty("public_lookup_selected_storage_ordinal", selectedOrdinal);
            row.addProperty("shadowed_by_predecessor", selectedOrdinal < currentOrdinal);
            records.add(row);
        }
        int experienceOrdinal = 0;
        for (Map.Entry<?, ?> entry : experience.entrySet()) {
            if (!(entry.getKey() instanceof ItemStack) || !(entry.getValue() instanceof Float)) {
                throw new IllegalStateException("FurnaceRecipes experience map shape changed");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "forge-smelting-experience-mapping");
            row.addProperty("storage_iteration_ordinal", experienceOrdinal++);
            row.add("output_pattern", encoder.encode(entry.getKey()));
            row.add("experience", encoder.encode(entry.getValue()));
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "forge-smelting-authority");
        authority.addProperty("smelting_row_count", smelting.size());
        authority.addProperty("experience_mapping_count", experience.size());
        authority.addProperty("smelting_storage_iteration_is_preserved", true);
        authority.addProperty("experience_storage_iteration_is_preserved", true);
        authority.addProperty("selector_item_identity", "exact");
        authority.addProperty("selector_metadata", "exact-or-wildcard-32767");
        authority.addProperty("selector_tag", "ignored-by-native-lookup");
        authority.addProperty("selector_count", "ignored-by-native-lookup");
        authority.addProperty("lookup_precedence", "first-matching-storage-iteration-row");
        authority.addProperty("lookup_precedence_observation", "executed-for-every-registered-input");
        authority.addProperty("overlap_census", "all-pairs-by-native-selector-shape");
        records.add(authority);
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static JsonObject selector(ItemStack stack) {
        ResourceLocation name = Item.REGISTRY.getNameForObject(stack.getItem());
        if (name == null) throw new IllegalStateException("smelting selector item is unregistered");
        JsonObject selector = new JsonObject();
        selector.addProperty("item_registry_name", name.toString());
        selector.addProperty(
            "metadata_constraint",
            stack.getMetadata() == 32767 ? "wildcard" : "exact"
        );
        if (stack.getMetadata() == 32767) selector.add("metadata", com.google.gson.JsonNull.INSTANCE);
        else selector.addProperty("metadata", stack.getMetadata());
        selector.addProperty("tag_constraint", "ignored");
        selector.addProperty("count_constraint", "ignored");
        return selector;
    }

    private static boolean selectorsOverlap(ItemStack left, ItemStack right) {
        if (left.getItem() != right.getItem()) return false;
        return left.getMetadata() == 32767
            || right.getMetadata() == 32767
            || left.getMetadata() == right.getMetadata();
    }

    private static boolean selectorMatches(ItemStack query, ItemStack registered) {
        return query.getItem() == registered.getItem()
            && (registered.getMetadata() == 32767
                || registered.getMetadata() == query.getMetadata());
    }

    private static int selectedOrdinal(
        List<Map.Entry<ItemStack, ItemStack>> rows,
        ItemStack query
    ) {
        for (int index = 0; index < rows.size(); index++) {
            if (selectorMatches(query, rows.get(index).getKey())) return index;
        }
        return -1;
    }

    private static Map<?, ?> experience(FurnaceRecipes furnace) {
        try {
            Object value = EXPERIENCE_LIST.get(furnace);
            if (!(value instanceof Map<?, ?>)) {
                throw new IllegalStateException("FurnaceRecipes experience field is not a map");
            }
            return (Map<?, ?>) value;
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot read FurnaceRecipes experience map", exception);
        }
    }

    private static Field experienceListField() {
        Field field;
        try {
            field = ObfuscationReflectionHelper.findField(
                FurnaceRecipes.class, "field_77605_c"
            );
        } catch (RuntimeException srgFailure) {
            try {
                field = ObfuscationReflectionHelper.findField(
                    FurnaceRecipes.class, "experienceList"
                );
            } catch (RuntimeException mcpFailure) {
                mcpFailure.addSuppressed(srgFailure);
                throw mcpFailure;
            }
        }
        int shape = field.getModifiers() & (Modifier.PUBLIC | Modifier.PROTECTED
            | Modifier.PRIVATE | Modifier.STATIC | Modifier.FINAL);
        if (field.getDeclaringClass() != FurnaceRecipes.class || field.getType() != Map.class
            || shape != (Modifier.PRIVATE | Modifier.FINAL)) {
            throw new IllegalStateException("FurnaceRecipes experience field shape changed");
        }
        field.setAccessible(true);
        return field;
    }
}
