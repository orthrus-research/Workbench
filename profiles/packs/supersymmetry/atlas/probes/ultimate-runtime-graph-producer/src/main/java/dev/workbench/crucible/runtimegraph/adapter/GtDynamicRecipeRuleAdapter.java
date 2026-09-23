package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.RuntimeMethodSurface;

import gregtech.api.recipes.RecipeMap;
import gregtech.api.recipes.machines.RecipeMapFurnace;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Non-finite RecipeMap lookup overrides, classified separately from finite rows. */
public final class GtDynamicRecipeRuleAdapter implements CaptureAdapter {
    private static final String FINITE = RecipeMap.class.getName();
    private static final String FURNACE =
        "gregtech.api.recipes.machines.RecipeMapFurnace";
    private static final String FLUID_CANNER =
        "gregtech.api.recipes.machines.RecipeMapFluidCanner";
    private static final String FORMING_PRESS =
        "gregtech.api.recipes.machines.RecipeMapFormingPress";
    private static final String SCANNER =
        "gregtech.api.recipes.machines.RecipeMapScanner";
    private static final String GROUP = "supersymmetry.api.recipes.RecipeMapGroup";

    @Override public String adapterId() { return "gt-dynamic-recipe-rules"; }
    @Override public String categoryId() { return "transformation-dynamic-rule"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (RecipeMap<?> map : GtRecipeCapture.maps()) {
            String owner = RuntimeMethodSurface.publicOwner(
                map,
                "findRecipe",
                long.class,
                List.class,
                List.class,
                boolean.class
            );
            if (FINITE.equals(owner)) continue;
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "gt-dynamic-recipe-rule");
            row.addProperty("recipe_map", map.getUnlocalizedName());
            row.addProperty("runtime_class", map.getClass().getName());
            row.addProperty("find_recipe_owner", owner);
            row.addProperty("rule_kind", ruleKind(owner));
            row.addProperty("finite_recipe_precedence", "consulted before synthesized fallback");
            row.addProperty("lookup_execution_invoked", false);
            row.addProperty("contextual_inputs_required", true);
            row.add("override_hooks", RuntimeMethodSurface.declaredHooks(
                map,
                RecipeMap.class,
                "findRecipe",
                "find",
                "prepareRecipeFind"
            ));
            semantics(row, owner);
            if (GROUP.equals(owner)) row.add("ordered_member_maps", groupMembers(map));
            else row.add("ordered_member_maps", JsonNull.INSTANCE);
            records.add(row);
        }
        if (records.isEmpty()) {
            throw new IllegalStateException("no dynamic GT recipe-map rules were observed");
        }
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
    }

    private static String ruleKind(String owner) {
        if (FURNACE.equals(owner)) return "minecraft_smelting_fallback";
        if (FLUID_CANNER.equals(owner)) return "fluid_handler_item_fill_or_drain";
        if (FORMING_PRESS.equals(owner)) return "forming_press_contextual_nbt";
        if (SCANNER.equals(owner)) return "ordered_custom_scanner_logic";
        if (GROUP.equals(owner)) return "ordered_recipe_map_delegation";
        return "unclassified_custom_find_recipe_override";
    }

    private static void semantics(JsonObject row, String owner) {
        if (FURNACE.equals(owner)) {
            row.addProperty("condition", "first item input with nonempty ModHandler smelting output");
            row.addProperty("duration_ticks", RecipeMapFurnace.RECIPE_DURATION);
            row.addProperty("eut", RecipeMapFurnace.RECIPE_EUT);
            row.addProperty("selection", "finite native recipe, then Minecraft smelting fallback");
        } else if (FLUID_CANNER.equals(owner)) {
            row.addProperty("condition", "item fluid-handler capability can drain or accept a supplied fluid");
            row.addProperty("selection", "finite native recipe, then drain, then fill");
            row.addProperty("duration_formula", "max(16, transferred_amount/64)");
            row.addProperty("eut", 4);
        } else if (FORMING_PRESS.equals(owner)) {
            row.addProperty("condition", "contextual mold/NBT naming or item combination path");
            row.addProperty("selection", "finite native recipe before synthesized contextual recipe");
        } else if (SCANNER.equals(owner)) {
            row.addProperty("condition", "first registered custom scanner logic accepting live inputs");
            row.addProperty("selection", "finite native recipe before ordered custom scanner logics");
        } else if (GROUP.equals(owner)) {
            row.addProperty("condition", "first ordered member recipe map returning a recipe");
            row.addProperty("selection", "ordered parallel-stream findFirst over member maps");
        } else {
            row.addProperty("condition", "opaque custom RecipeMap.findRecipe implementation");
            row.addProperty("selection", "unresolved executable boundary");
        }
    }

    private static JsonArray groupMembers(RecipeMap<?> map) {
        Field field = null;
        for (Class<?> current = map.getClass(); current != null && current != RecipeMap.class;
             current = current.getSuperclass()) {
            try {
                field = current.getDeclaredField("recipeMaps");
                break;
            } catch (NoSuchFieldException ignored) {
                // Continue to the next exact superclass.
            }
        }
        if (field == null || !field.getType().isArray()
            || Modifier.isStatic(field.getModifiers())) {
            throw new IllegalStateException("dynamic recipe-map group lost recipeMaps array");
        }
        field.setAccessible(true);
        Object value;
        try {
            value = field.get(map);
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot read dynamic recipe-map group members", exception);
        }
        JsonArray result = new JsonArray();
        int length = Array.getLength(value);
        if (length == 0) throw new IllegalStateException("dynamic recipe-map group is empty");
        for (int index = 0; index < length; index++) {
            Object member = Array.get(value, index);
            if (!(member instanceof RecipeMap<?>)) {
                throw new IllegalStateException("dynamic recipe-map group member shape changed");
            }
            RecipeMap<?> recipeMap = (RecipeMap<?>) member;
            if (RecipeMap.getByName(recipeMap.getUnlocalizedName()) != recipeMap) {
                throw new IllegalStateException("dynamic recipe-map group member is unregistered");
            }
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", index);
            row.addProperty("recipe_map", recipeMap.getUnlocalizedName());
            result.add(row);
        }
        return result;
    }
}
