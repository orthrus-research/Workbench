package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.RuntimeMethodSurface;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.recipes.RecipeMap;
import gregtech.api.recipes.category.GTRecipeCategory;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/** Registered GT recipe maps independent of their finite recipe occurrences. */
public final class GtRecipeMapAdapter implements CaptureAdapter {
    private static final Field MODIFY_ITEM_INPUTS = booleanField("modifyItemInputs");
    private static final Field MODIFY_ITEM_OUTPUTS = booleanField("modifyItemOutputs");
    private static final Field MODIFY_FLUID_INPUTS = booleanField("modifyFluidInputs");
    private static final Field MODIFY_FLUID_OUTPUTS = booleanField("modifyFluidOutputs");
    private static final Field ALLOW_EMPTY_OUTPUT = booleanField("allowEmptyOutput");
    private static final Field HAS_ORE_INPUTS = booleanField("hasOreDictedInputs");
    private static final Field HAS_NBT_INPUTS = booleanField("hasNBTMatcherInputs");
    private static final Field ON_RECIPE_BUILD = ReflectionAccess.requireAssignableField(
        RecipeMap.class, "onRecipeBuildAction", java.util.function.Consumer.class
    );

    @Override public String adapterId() { return "gt-recipe-maps"; }
    @Override public String categoryId() { return "transformation-recipe-map"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (RecipeMap<?> map : GtRecipeCapture.maps()) {
            GtRecipeCapture.Membership membership = GtRecipeCapture.membership(map);
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "gt-recipe-map");
            row.addProperty("name", map.getUnlocalizedName());
            row.addProperty("translation_key", map.getTranslationKey());
            row.addProperty("runtime_class", map.getClass().getName());
            row.addProperty("max_item_inputs", map.getMaxInputs());
            row.addProperty("max_item_outputs", map.getMaxOutputs());
            row.addProperty("max_fluid_inputs", map.getMaxFluidInputs());
            row.addProperty("max_fluid_outputs", map.getMaxFluidOutputs());
            row.addProperty("hidden", map.isHidden);
            row.addProperty("modify_item_inputs", bool(MODIFY_ITEM_INPUTS, map));
            row.addProperty("modify_item_outputs", bool(MODIFY_ITEM_OUTPUTS, map));
            row.addProperty("modify_fluid_inputs", bool(MODIFY_FLUID_INPUTS, map));
            row.addProperty("modify_fluid_outputs", bool(MODIFY_FLUID_OUTPUTS, map));
            row.addProperty("allow_empty_output", bool(ALLOW_EMPTY_OUTPUT, map));
            row.addProperty("has_ore_dictionary_inputs", bool(HAS_ORE_INPUTS, map));
            row.addProperty("has_nbt_matcher_inputs", bool(HAS_NBT_INPUTS, map));
            row.addProperty("lookup_identity_count", membership.lookupIdentityCount);
            row.addProperty("category_occurrence_count", membership.categoryOccurrenceCount);
            row.addProperty("union_identity_count", membership.unionIdentityCount);
            row.addProperty("lookup_and_category_count", membership.bothCount);
            row.addProperty("lookup_only_count", membership.lookupOnlyCount);
            row.addProperty("category_only_count", membership.categoryOnlyCount);
            String findOwner = RuntimeMethodSurface.publicOwner(
                map,
                "findRecipe",
                long.class,
                List.class,
                List.class,
                boolean.class
            );
            row.addProperty("find_recipe_owner", findOwner);
            row.addProperty("dynamic", !RecipeMap.class.getName().equals(findOwner));
            row.add("chance_function", encoder.encode(map.getChanceFunction()));
            row.add("on_recipe_build_action", encoder.encode(
                ReflectionAccess.read(ON_RECIPE_BUILD, map)
            ));
            if (map.getSound() == null || map.getSound().getRegistryName() == null) {
                row.add("sound", JsonNull.INSTANCE);
            } else {
                row.addProperty("sound", map.getSound().getRegistryName().toString());
            }
            if (map.getSmallRecipeMap() == null) row.add("small_recipe_map", JsonNull.INSTANCE);
            else row.addProperty("small_recipe_map", map.getSmallRecipeMap().getUnlocalizedName());

            List<Map.Entry<GTRecipeCategory, List<gregtech.api.recipes.Recipe>>> categories =
                new ArrayList<Map.Entry<GTRecipeCategory, List<gregtech.api.recipes.Recipe>>>(
                    map.getRecipesByCategory().entrySet()
                );
            Collections.sort(categories, Comparator.comparing(
                entry -> entry.getKey().getUniqueID()
            ));
            JsonArray categoryRows = new JsonArray();
            for (Map.Entry<GTRecipeCategory, List<gregtech.api.recipes.Recipe>> entry : categories) {
                JsonObject value = new JsonObject();
                value.addProperty("unique_id", entry.getKey().getUniqueID());
                value.addProperty("name", entry.getKey().getName());
                value.addProperty("mod_id", entry.getKey().getModid());
                value.addProperty("translation_key", entry.getKey().getTranslationKey());
                value.addProperty("recipe_occurrence_count", entry.getValue().size());
                categoryRows.add(value);
            }
            row.add("categories", categoryRows);
            records.add(row);
        }
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static Field booleanField(String name) {
        return ReflectionAccess.requireField(RecipeMap.class, name, boolean.class);
    }

    private static boolean bool(Field field, RecipeMap<?> map) {
        return ((Boolean) ReflectionAccess.read(field, map)).booleanValue();
    }
}
