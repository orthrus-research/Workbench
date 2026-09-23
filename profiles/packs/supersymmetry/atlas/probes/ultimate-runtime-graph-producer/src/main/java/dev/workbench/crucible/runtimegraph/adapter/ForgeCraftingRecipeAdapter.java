package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.RuntimeMethodSurface;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.inventory.InventoryCrafting;
import net.minecraft.item.ItemStack;
import net.minecraft.item.crafting.IRecipe;
import net.minecraft.item.crafting.Ingredient;
import net.minecraft.util.NonNullList;
import net.minecraft.util.ResourceLocation;
import net.minecraft.world.World;
import net.minecraftforge.common.crafting.IShapedRecipe;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.registries.ForgeRegistry;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Final Forge crafting registry with public finite projections and contextual boundaries. */
public final class ForgeCraftingRecipeAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "forge-crafting-recipes"; }
    @Override public String categoryId() { return "transformation-crafting"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        List<ResourceLocation> keys = new ArrayList<ResourceLocation>(
            ForgeRegistries.RECIPES.getKeys()
        );
        Collections.sort(keys);
        if (keys.isEmpty()) throw new IllegalStateException("Forge crafting registry is empty");
        if (!(ForgeRegistries.RECIPES instanceof ForgeRegistry<?>)) {
            throw new IllegalStateException("Forge crafting registry has no numeric authority");
        }
        @SuppressWarnings("unchecked")
        ForgeRegistry<IRecipe> numericRegistry =
            (ForgeRegistry<IRecipe>) ForgeRegistries.RECIPES;
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>(keys.size());
        Set<Integer> numericIds = new HashSet<Integer>();
        for (ResourceLocation key : keys) {
            IRecipe recipe = ForgeRegistries.RECIPES.getValue(key);
            int numericId = recipe == null ? -1 : numericRegistry.getID(recipe);
            if (recipe == null || recipe.getRegistryName() == null
                || !key.equals(recipe.getRegistryName()) || numericId < 0
                || !numericIds.add(Integer.valueOf(numericId))) {
                throw new IllegalStateException("invalid final crafting recipe " + key);
            }
            records.add(recipe(key, numericId, recipe, encoder));
        }
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static JsonObject recipe(
        ResourceLocation key,
        int numericId,
        IRecipe recipe,
        StableValueEncoder encoder
    ) {
        NonNullList<Ingredient> ingredients = recipe.getIngredients();
        ItemStack output = recipe.getRecipeOutput();
        if (ingredients == null || output == null) {
            throw new IllegalStateException("crafting recipe has a null public projection " + key);
        }
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "forge-crafting-recipe");
        row.addProperty("registry_name", key.toString());
        row.addProperty("numeric_id", numericId);
        row.addProperty("runtime_class", recipe.getClass().getName());
        row.addProperty("dynamic", recipe.isDynamic());
        if (recipe.getGroup() == null) row.add("group", JsonNull.INSTANCE);
        else row.addProperty("group", recipe.getGroup());
        row.add("public_output", encoder.encode(output));

        JsonArray ingredientRows = new JsonArray();
        for (int ordinal = 0; ordinal < ingredients.size(); ordinal++) {
            Ingredient ingredient = ingredients.get(ordinal);
            if (ingredient == null) {
                throw new IllegalStateException("null crafting ingredient " + key + ':' + ordinal);
            }
            ItemStack[] matching = ingredient.getMatchingStacks();
            if (matching == null) {
                throw new IllegalStateException("null crafting alternatives " + key + ':' + ordinal);
            }
            List<com.google.gson.JsonElement> alternatives =
                new ArrayList<com.google.gson.JsonElement>(matching.length);
            for (ItemStack stack : matching) {
                if (stack == null || stack.isEmpty() || !ingredient.apply(stack.copy())) {
                    throw new IllegalStateException(
                        "crafting alternative disagrees with predicate " + key + ':' + ordinal
                    );
                }
                alternatives.add(encoder.encode(stack));
            }
            Collections.sort(alternatives, new Comparator<com.google.gson.JsonElement>() {
                @Override
                public int compare(
                    com.google.gson.JsonElement left,
                    com.google.gson.JsonElement right
                ) {
                    return dev.workbench.crucible.runtimegraph.CanonicalJson.compareUnsigned(
                        dev.workbench.crucible.runtimegraph.CanonicalJson.bytes(left),
                        dev.workbench.crucible.runtimegraph.CanonicalJson.bytes(right)
                    );
                }
            });
            JsonObject value = new JsonObject();
            value.addProperty("ordinal", ordinal);
            value.addProperty("runtime_class", ingredient.getClass().getName());
            value.addProperty("empty_slot", ingredient == Ingredient.EMPTY);
            value.addProperty("contextual_predicate", ingredient != Ingredient.EMPTY);
            JsonArray values = new JsonArray();
            for (com.google.gson.JsonElement alternative : alternatives) values.add(alternative);
            value.add("matching_stacks", values);
            ingredientRows.add(value);
        }
        row.add("ingredients", ingredientRows);

        if (recipe instanceof IShapedRecipe) {
            IShapedRecipe shaped = (IShapedRecipe) recipe;
            JsonObject shape = new JsonObject();
            shape.addProperty("width", shaped.getRecipeWidth());
            shape.addProperty("height", shaped.getRecipeHeight());
            shape.addProperty(
                "ingredient_area_matches",
                shaped.getRecipeWidth() * shaped.getRecipeHeight() == ingredients.size()
            );
            row.add("shape", shape);
        } else {
            row.add("shape", JsonNull.INSTANCE);
        }

        JsonObject methods = new JsonObject();
        methods.addProperty("matches", RuntimeMethodSurface.publicOwnerByShape(
            recipe, IRecipe.class, boolean.class, InventoryCrafting.class, World.class
        ));
        methods.addProperty("get_crafting_result", RuntimeMethodSurface.publicOwnerByShape(
            recipe, IRecipe.class, ItemStack.class, InventoryCrafting.class
        ));
        methods.addProperty("get_remaining_items", RuntimeMethodSurface.publicOwnerByShape(
            recipe, IRecipe.class, NonNullList.class, InventoryCrafting.class
        ));
        methods.addProperty("can_fit", RuntimeMethodSurface.publicOwnerByShape(
            recipe, IRecipe.class, boolean.class, int.class, int.class
        ));
        row.add("method_owners", methods);
        row.addProperty("contextual_execution_invoked", false);
        row.addProperty(
            "contextual_boundary",
            "matches/getCraftingResult/getRemainingItems/canFit require an inventory or world"
        );
        row.addProperty("registry_selection_ordinal", numericId);
        row.addProperty("selection", "ascending final numeric registry ID; first matches wins");
        return row;
    }
}
