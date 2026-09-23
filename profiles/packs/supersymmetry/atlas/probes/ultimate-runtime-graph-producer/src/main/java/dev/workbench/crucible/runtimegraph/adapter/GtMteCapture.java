package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.RuntimeMethodSurface;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.GregTechAPI;
import gregtech.api.capability.IMultipleRecipeMaps;
import gregtech.api.capability.impl.AbstractRecipeLogic;
import gregtech.api.metatileentity.ITieredMetaTileEntity;
import gregtech.api.metatileentity.MTETrait;
import gregtech.api.metatileentity.MetaTileEntity;
import gregtech.api.metatileentity.WorkableTieredMetaTileEntity;
import gregtech.api.metatileentity.multiblock.MultiblockControllerBase;
import gregtech.api.recipes.RecipeMap;

import net.minecraft.item.ItemStack;
import net.minecraft.util.ResourceLocation;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Shared exact registry view for independent MTE and machine-binding categories. */
final class GtMteCapture {
    private static final Field MTE_TRAITS = ReflectionAccess.requireAssignableField(
        MetaTileEntity.class, "mteTraits", Map.class
    );

    private GtMteCapture() {}

    static List<Entry> entries() {
        if (!GregTechAPI.MTE_REGISTRY.isFrozen()) {
            throw new IllegalStateException("GT MTE registry is not frozen");
        }
        List<ResourceLocation> keys = new ArrayList<ResourceLocation>(
            GregTechAPI.MTE_REGISTRY.getKeys()
        );
        Collections.sort(keys);
        if (keys.isEmpty()) throw new IllegalStateException("GT MTE registry is empty");
        Set<Integer> numericIds = new HashSet<Integer>();
        List<Entry> result = new ArrayList<Entry>(keys.size());
        for (ResourceLocation key : keys) {
            MetaTileEntity mte = GregTechAPI.MTE_REGISTRY.getObject(key);
            int numericId = GregTechAPI.MTE_REGISTRY.getIdByObjectName(key);
            if (mte == null || !key.equals(mte.metaTileEntityId)
                || !key.equals(GregTechAPI.MTE_REGISTRY.getNameForObject(mte))
                || numericId < 0 || !numericIds.add(Integer.valueOf(numericId))
                || GregTechAPI.MTE_REGISTRY.getObjectById(numericId) != mte) {
                throw new IllegalStateException("invalid GT MTE registry row " + key);
            }
            ItemStack stack = mte.getStackForm();
            if (stack == null || stack.isEmpty() || stack.getCount() != 1) {
                throw new IllegalStateException("GT MTE has invalid stack form " + key);
            }
            result.add(new Entry(key, numericId, mte, stack, mte.getRecipeLogic()));
        }
        return result;
    }

    static JsonObject mte(Entry entry, StableValueEncoder encoder) {
        MetaTileEntity mte = entry.mte;
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "gt-meta-tile-entity");
        row.addProperty("registry_name", entry.key.toString());
        row.addProperty("numeric_id", entry.numericId);
        row.addProperty("runtime_class", mte.getClass().getName());
        row.addProperty("meta_name", mte.getMetaName());
        row.addProperty("meta_full_name", mte.getMetaFullName());
        row.addProperty("controller_kind", controllerKind(mte));
        row.add("stack_form", encoder.encode(entry.stack));
        if (mte instanceof ITieredMetaTileEntity) {
            row.addProperty("tier", ((ITieredMetaTileEntity) mte).getTier());
        } else {
            row.add("tier", JsonNull.INSTANCE);
        }
        row.addProperty("has_recipe_logic", entry.logic != null);
        if (entry.logic == null) {
            row.add("recipe_logic_class", JsonNull.INSTANCE);
            row.add("recipe_logic_consumes_energy", JsonNull.INSTANCE);
        } else {
            row.addProperty("recipe_logic_class", entry.logic.getClass().getName());
            row.addProperty("recipe_logic_consumes_energy", entry.logic.consumesEnergy());
        }
        row.addProperty(
            "recipe_map_projection",
            "gt-machine-recipe-maps category"
        );
        row.add("traits", traits(mte));
        row.add("prototype_hooks", RuntimeMethodSurface.declaredHooks(
            mte,
            MetaTileEntity.class,
            "createMetaTileEntity",
            "initializeInventory",
            "createStructurePattern",
            "checkRecipe",
            "updateFormedValid",
            "getRecipeMap",
            "getSound"
        ));
        return row;
    }

    static List<JsonObject> bindings(Entry entry) {
        List<JsonObject> result = new ArrayList<JsonObject>();
        if (entry.mte instanceof IMultipleRecipeMaps) {
            IMultipleRecipeMaps multiple = (IMultipleRecipeMaps) entry.mte;
            RecipeMap<?>[] maps = multiple.getAvailableRecipeMaps();
            if (maps == null || maps.length == 0) {
                throw new IllegalStateException("multi-map MTE has no available maps " + entry.key);
            }
            Set<RecipeMap<?>> seen = Collections.newSetFromMap(
                new java.util.IdentityHashMap<RecipeMap<?>, Boolean>()
            );
            for (int ordinal = 0; ordinal < maps.length; ordinal++) {
                RecipeMap<?> map = requireMap(maps[ordinal], entry.key);
                if (!seen.add(map)) {
                    throw new IllegalStateException("multi-map MTE repeats a map " + entry.key);
                }
                result.add(binding(entry, map, "IMultipleRecipeMaps.getAvailableRecipeMaps", ordinal, "runtime-selectable"));
            }
            RecipeMap<?> current = multiple.getCurrentRecipeMap();
            if (current != null && !seen.contains(current)) {
                throw new IllegalStateException("multi-map MTE current map is unavailable " + entry.key);
            }
            JsonObject selection = new JsonObject();
            selection.addProperty("record_type", "gt-machine-recipe-map-selection");
            selection.addProperty("machine", entry.key.toString());
            selection.addProperty("authority", "IMultipleRecipeMaps.getCurrentRecipeMap");
            if (current == null) selection.add("current_recipe_map", JsonNull.INSTANCE);
            else selection.addProperty("current_recipe_map", current.getUnlocalizedName());
            result.add(selection);
            return result;
        }
        if (entry.logic == null) {
            result.add(noBinding(entry, "MetaTileEntity.getRecipeLogic returned null"));
            return result;
        }
        RecipeMap<?> map = entry.logic.getRecipeMap();
        if (map == null) {
            result.add(noBinding(entry, "AbstractRecipeLogic.getRecipeMap returned null"));
            return result;
        }
        map = requireMap(map, entry.key);
        if (entry.mte.getRecipeMap() != map) {
            throw new IllegalStateException("MTE and recipe logic disagree on map " + entry.key);
        }
        result.add(binding(entry, map, "AbstractRecipeLogic.getRecipeMap", 0, "direct"));
        return result;
    }

    private static JsonObject binding(
        Entry entry,
        RecipeMap<?> map,
        String authority,
        int ordinal,
        String selection
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "gt-machine-recipe-map-binding");
        row.addProperty("machine", entry.key.toString());
        row.addProperty("recipe_map", map.getUnlocalizedName());
        row.addProperty("authority", authority);
        row.addProperty("ordinal", ordinal);
        row.addProperty("selection", selection);
        return row;
    }

    private static JsonObject noBinding(Entry entry, String reason) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "gt-machine-no-recipe-map-binding");
        row.addProperty("machine", entry.key.toString());
        row.addProperty("reason", reason);
        return row;
    }

    private static RecipeMap<?> requireMap(RecipeMap<?> map, ResourceLocation machine) {
        if (map == null || map.getUnlocalizedName() == null
            || RecipeMap.getByName(map.getUnlocalizedName()) != map) {
            throw new IllegalStateException("MTE references unregistered recipe map " + machine);
        }
        return map;
    }

    @SuppressWarnings("unchecked")
    private static JsonArray traits(MetaTileEntity mte) {
        Map<String, MTETrait> traits = (Map<String, MTETrait>) ReflectionAccess.read(
            MTE_TRAITS, mte
        );
        List<Map.Entry<String, MTETrait>> entries =
            new ArrayList<Map.Entry<String, MTETrait>>(traits.entrySet());
        Collections.sort(entries, Comparator.comparing(Map.Entry::getKey));
        Set<Integer> networkIds = new HashSet<Integer>();
        JsonArray result = new JsonArray();
        for (Map.Entry<String, MTETrait> entry : entries) {
            MTETrait trait = entry.getValue();
            if (trait == null || !entry.getKey().equals(trait.getName())
                || trait.getMetaTileEntity() != mte
                || !networkIds.add(Integer.valueOf(trait.getNetworkID()))) {
                throw new IllegalStateException("invalid MTE trait registry " + mte.metaTileEntityId);
            }
            JsonObject row = new JsonObject();
            row.addProperty("name", entry.getKey());
            row.addProperty("network_id", trait.getNetworkID());
            row.addProperty("runtime_class", trait.getClass().getName());
            result.add(row);
        }
        return result;
    }

    private static String controllerKind(MetaTileEntity mte) {
        if (mte instanceof MultiblockControllerBase) return "multiblock-controller";
        if (mte instanceof WorkableTieredMetaTileEntity) return "singleblock-workable";
        return "meta-tile-entity";
    }

    static final class Entry {
        final ResourceLocation key;
        final int numericId;
        final MetaTileEntity mte;
        final ItemStack stack;
        final AbstractRecipeLogic logic;

        Entry(
            ResourceLocation key,
            int numericId,
            MetaTileEntity mte,
            ItemStack stack,
            AbstractRecipeLogic logic
        ) {
            this.key = key;
            this.numericId = numericId;
            this.mte = mte;
            this.stack = stack;
            this.logic = logic;
        }
    }
}
