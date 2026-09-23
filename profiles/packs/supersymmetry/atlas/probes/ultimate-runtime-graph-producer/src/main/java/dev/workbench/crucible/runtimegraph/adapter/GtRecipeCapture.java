package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.recipes.Recipe;
import gregtech.api.recipes.RecipeMap;
import gregtech.api.recipes.category.GTRecipeCategory;
import gregtech.api.recipes.chance.BaseChanceEntry;
import gregtech.api.recipes.chance.output.BoostableChanceOutput;
import gregtech.api.recipes.chance.output.ChancedOutputList;
import gregtech.api.recipes.ingredients.GTRecipeInput;
import gregtech.api.recipes.map.Branch;
import gregtech.api.recipes.recipeproperties.RecipeProperty;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.oredict.OreDictionary;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Exact identity union and explicit row projection shared by GT recipe adapters. */
final class GtRecipeCapture {
    private static final Field LOOKUP = ReflectionAccess.requireField(
        RecipeMap.class, "lookup", Branch.class
    );
    private static final IdentityHashMap<Recipe, JsonObject> RECIPE_IDENTITIES =
        new IdentityHashMap<Recipe, JsonObject>();

    private GtRecipeCapture() {}

    static void beginIdentityIndex() {
        RECIPE_IDENTITIES.clear();
    }

    static JsonObject identityFor(Recipe recipe) {
        JsonObject identity = RECIPE_IDENTITIES.get(recipe);
        return identity == null ? null : identity.deepCopy();
    }

    static List<RecipeMap<?>> maps() {
        List<RecipeMap<?>> maps = new ArrayList<RecipeMap<?>>(RecipeMap.getRecipeMaps());
        Collections.sort(maps, Comparator.comparing(RecipeMap::getUnlocalizedName));
        Set<String> names = new HashSet<String>();
        for (RecipeMap<?> map : maps) {
            if (map == null || map.getUnlocalizedName() == null
                || map.getUnlocalizedName().isEmpty()
                || !names.add(map.getUnlocalizedName())
                || RecipeMap.getByName(map.getUnlocalizedName()) != map) {
                throw new IllegalStateException("invalid GT recipe map registry");
            }
        }
        if (maps.isEmpty()) throw new IllegalStateException("GT recipe map registry is empty");
        return maps;
    }

    static Membership membership(RecipeMap<?> map) {
        Branch lookup = (Branch) ReflectionAccess.read(LOOKUP, map);
        List<Recipe> active = new ArrayList<Recipe>();
        IdentityHashMap<Recipe, Boolean> activeSet = new IdentityHashMap<Recipe, Boolean>();
        lookup.getRecipes(false).forEach(recipe -> {
            if (recipe == null) throw new IllegalStateException("GT lookup contains null recipe");
            if (activeSet.put(recipe, Boolean.TRUE) == null) active.add(recipe);
        });
        IdentityHashMap<Recipe, Boolean> categorySet = new IdentityHashMap<Recipe, Boolean>();
        int categoryOccurrences = 0;
        List<Map.Entry<GTRecipeCategory, List<Recipe>>> categories =
            new ArrayList<Map.Entry<GTRecipeCategory, List<Recipe>>>(
                map.getRecipesByCategory().entrySet()
            );
        Collections.sort(categories, Comparator.comparing(
            entry -> entry.getKey().getUniqueID()
        ));
        for (Map.Entry<GTRecipeCategory, List<Recipe>> entry : categories) {
            GTRecipeCategory category = entry.getKey();
            if (category == null || category.getRecipeMap() != map || entry.getValue() == null) {
                throw new IllegalStateException("invalid category index for " + map.getUnlocalizedName());
            }
            for (Recipe recipe : entry.getValue()) {
                if (recipe == null || recipe.getRecipeCategory() != category) {
                    throw new IllegalStateException("GT recipe/category identity mismatch");
                }
                categoryOccurrences++;
                categorySet.put(recipe, Boolean.TRUE);
            }
        }
        IdentityHashMap<Recipe, Boolean> union = new IdentityHashMap<Recipe, Boolean>();
        union.putAll(activeSet);
        union.putAll(categorySet);
        int both = 0;
        int lookupOnly = 0;
        int categoryOnly = 0;
        for (Recipe recipe : union.keySet()) {
            boolean inLookup = activeSet.containsKey(recipe);
            boolean inCategory = categorySet.containsKey(recipe);
            if (inLookup && inCategory) both++;
            else if (inLookup) lookupOnly++;
            else categoryOnly++;
        }
        return new Membership(
            lookup,
            activeSet,
            categorySet,
            active.size(),
            categoryOccurrences,
            union.size(),
            both,
            lookupOnly,
            categoryOnly
        );
    }

    static List<JsonObject> recipes(RecipeMap<?> map, StableValueEncoder encoder) {
        Membership membership = membership(map);
        List<Draft> drafts = new ArrayList<Draft>();
        IdentityHashMap<Recipe, Boolean> emitted = new IdentityHashMap<Recipe, Boolean>();
        List<Map.Entry<GTRecipeCategory, List<Recipe>>> categories =
            new ArrayList<Map.Entry<GTRecipeCategory, List<Recipe>>>(
                map.getRecipesByCategory().entrySet()
            );
        Collections.sort(categories, Comparator.comparing(
            entry -> entry.getKey().getUniqueID()
        ));
        for (Map.Entry<GTRecipeCategory, List<Recipe>> entry : categories) {
            for (Recipe recipe : entry.getValue()) {
                if (emitted.put(recipe, Boolean.TRUE) != null) {
                    throw new IllegalStateException(
                        "one GT recipe identity occurs multiple times in category index: "
                            + map.getUnlocalizedName()
                    );
                }
                drafts.add(draft(map, recipe, membership, encoder));
            }
        }
        for (Recipe recipe : membership.active.keySet()) {
            if (emitted.put(recipe, Boolean.TRUE) == null) {
                drafts.add(draft(map, recipe, membership, encoder));
            }
        }
        Collections.sort(drafts, new Comparator<Draft>() {
            @Override
            public int compare(Draft left, Draft right) {
                int value = CanonicalJson.compareUnsigned(left.bytes, right.bytes);
                if (value != 0) return value;
                value = Boolean.compare(right.lookupActive, left.lookupActive);
                if (value != 0) return value;
                return Boolean.compare(right.categoryPresent, left.categoryPresent);
            }
        });
        Map<String, Integer> ordinals = new LinkedHashMap<String, Integer>();
        List<JsonObject> result = new ArrayList<JsonObject>(drafts.size());
        for (Draft draft : drafts) {
            Integer previous = ordinals.get(draft.sha256);
            int ordinal = previous == null ? 0 : previous.intValue();
            ordinals.put(draft.sha256, Integer.valueOf(ordinal + 1));
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "gt-recipe");
            row.addProperty("recipe_map", map.getUnlocalizedName());
            row.addProperty("semantic_sha256", draft.sha256);
            row.addProperty("duplicate_ordinal", ordinal);
            row.addProperty("lookup_active", draft.lookupActive);
            row.addProperty("category_present", draft.categoryPresent);
            row.add("recipe", draft.value);
            JsonObject identity = new JsonObject();
            identity.addProperty("recipe_map", map.getUnlocalizedName());
            identity.addProperty("semantic_sha256", draft.sha256);
            identity.addProperty("duplicate_ordinal", ordinal);
            if (RECIPE_IDENTITIES.put(draft.recipe, identity) != null) {
                throw new IllegalStateException("GT recipe identity was indexed twice");
            }
            result.add(row);
        }
        if (result.size() != membership.unionIdentityCount) {
            throw new IllegalStateException("GT recipe union cardinality drifted");
        }
        return result;
    }

    private static Draft draft(
        RecipeMap<?> map,
        Recipe recipe,
        Membership membership,
        StableValueEncoder encoder
    ) {
        if (recipe.getRecipeCategory() == null
            || recipe.getRecipeCategory().getRecipeMap() != map) {
            throw new IllegalStateException("GT recipe points outside its map");
        }
        JsonObject value = encodeRecipe(recipe, encoder);
        byte[] bytes = CanonicalJson.bytes(value);
        return new Draft(
            recipe,
            value,
            bytes,
            dev.workbench.crucible.runtimegraph.Hashing.sha256(bytes),
            membership.active.containsKey(recipe),
            membership.category.containsKey(recipe)
        );
    }

    static JsonObject encodeRecipe(Recipe recipe, StableValueEncoder encoder) {
        JsonObject row = new JsonObject();
        row.addProperty("runtime_class", recipe.getClass().getName());
        row.addProperty("category", recipe.getRecipeCategory().getUniqueID());
        row.addProperty("duration", recipe.getDuration());
        row.addProperty("eut", recipe.getEUt());
        row.addProperty("hidden", recipe.isHidden());
        row.addProperty("crafttweaker_recipe", recipe.getIsCTRecipe());
        row.addProperty("groovy_recipe", recipe.isGroovyRecipe());
        row.add("item_inputs", inputs(recipe.getInputs(), encoder));
        row.add("fluid_inputs", inputs(recipe.getFluidInputs(), encoder));
        row.add("item_outputs", values(recipe.getOutputs(), encoder));
        row.add("fluid_outputs", values(recipe.getFluidOutputs(), encoder));
        row.add("chanced_item_outputs", chanced(recipe.getChancedOutputs(), encoder));
        row.add("chanced_fluid_outputs", chanced(recipe.getChancedFluidOutputs(), encoder));

        List<Map.Entry<RecipeProperty<?>, Object>> properties =
            new ArrayList<Map.Entry<RecipeProperty<?>, Object>>(recipe.getPropertyValues());
        Collections.sort(properties, Comparator.comparing(
            entry -> entry.getKey().getKey()
        ));
        JsonArray propertyRows = new JsonArray();
        Set<String> keys = new HashSet<String>();
        for (Map.Entry<RecipeProperty<?>, Object> property : properties) {
            if (property.getKey() == null || property.getKey().getKey() == null
                || !keys.add(property.getKey().getKey())) {
                throw new IllegalStateException("GT recipe property keys are invalid");
            }
            JsonObject value = new JsonObject();
            value.addProperty("key", property.getKey().getKey());
            value.addProperty("property_class", property.getKey().getClass().getName());
            value.addProperty("hidden", property.getKey().isHidden());
            value.add("value", encoder.encode(property.getValue()));
            propertyRows.add(value);
        }
        row.add("properties", propertyRows);
        return row;
    }

    private static JsonArray inputs(
        List<GTRecipeInput> inputs,
        StableValueEncoder encoder
    ) {
        JsonArray result = new JsonArray();
        for (int ordinal = 0; ordinal < inputs.size(); ordinal++) {
            GTRecipeInput input = inputs.get(ordinal);
            if (input == null) throw new IllegalStateException("GT recipe input is null");
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", ordinal);
            row.addProperty("runtime_class", input.getClass().getName());
            row.addProperty("amount", input.getAmount());
            row.addProperty("non_consumable", input.isNonConsumable());
            row.addProperty("ore_dictionary", input.isOreDict());
            if (input.isOreDict()) {
                row.addProperty("ore_dictionary_id", input.getOreDict());
                row.addProperty("ore_dictionary_name", OreDictionary.getOreName(input.getOreDict()));
            } else {
                row.add("ore_dictionary_id", JsonNull.INSTANCE);
                row.add("ore_dictionary_name", JsonNull.INSTANCE);
            }
            row.addProperty("has_nbt_matching_condition", input.hasNBTMatchingCondition());
            row.add("nbt_matcher", encoder.encode(input.getNBTMatcher()));
            row.add("nbt_condition", encoder.encode(input.getNBTMatchingCondition()));
            FluidStack fluid = input.getInputFluidStack();
            row.add("fluid_stack", encoder.encode(fluid));
            ItemStack[] stacks = input.getInputStacks();
            List<JsonElement> stackRows = new ArrayList<JsonElement>();
            if (stacks != null) {
                for (ItemStack stack : stacks) {
                    if (stack == null || stack.isEmpty()) {
                        throw new IllegalStateException("GT recipe input representative is empty");
                    }
                    stackRows.add(encoder.encode(stack));
                }
            }
            Collections.sort(stackRows, new Comparator<JsonElement>() {
                @Override
                public int compare(JsonElement left, JsonElement right) {
                    return CanonicalJson.compareUnsigned(
                        CanonicalJson.bytes(left), CanonicalJson.bytes(right)
                    );
                }
            });
            JsonArray representatives = new JsonArray();
            for (JsonElement stack : stackRows) representatives.add(stack);
            row.add("item_stack_representatives", representatives);
            result.add(row);
        }
        return result;
    }

    private static JsonArray values(Iterable<?> values, StableValueEncoder encoder) {
        JsonArray result = new JsonArray();
        int ordinal = 0;
        for (Object value : values) {
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", ordinal++);
            row.add("value", encoder.encode(value));
            result.add(row);
        }
        return result;
    }

    private static JsonObject chanced(
        ChancedOutputList<?, ?> outputs,
        StableValueEncoder encoder
    ) {
        JsonObject result = new JsonObject();
        result.addProperty(
            "logic_class",
            outputs.getChancedOutputLogic().getClass().getName()
        );
        JsonArray entries = new JsonArray();
        int ordinal = 0;
        for (Object raw : outputs.getChancedEntries()) {
            if (!(raw instanceof BaseChanceEntry<?>)) {
                throw new IllegalStateException("GT chanced output entry shape changed");
            }
            BaseChanceEntry<?> entry = (BaseChanceEntry<?>) raw;
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", ordinal++);
            row.addProperty("runtime_class", raw.getClass().getName());
            row.addProperty("chance", entry.getChance());
            if (raw instanceof BoostableChanceOutput<?>) {
                row.addProperty(
                    "chance_boost",
                    ((BoostableChanceOutput<?>) raw).getChanceBoost()
                );
            } else {
                row.add("chance_boost", JsonNull.INSTANCE);
            }
            row.add("value", encoder.encode(entry.getIngredient()));
            entries.add(row);
        }
        result.add("entries", entries);
        return result;
    }

    static final class Membership {
        final Branch lookup;
        final IdentityHashMap<Recipe, Boolean> active;
        final IdentityHashMap<Recipe, Boolean> category;
        final int lookupIdentityCount;
        final int categoryOccurrenceCount;
        final int unionIdentityCount;
        final int bothCount;
        final int lookupOnlyCount;
        final int categoryOnlyCount;

        Membership(
            Branch lookup,
            IdentityHashMap<Recipe, Boolean> active,
            IdentityHashMap<Recipe, Boolean> category,
            int lookupIdentityCount,
            int categoryOccurrenceCount,
            int unionIdentityCount,
            int bothCount,
            int lookupOnlyCount,
            int categoryOnlyCount
        ) {
            this.lookup = lookup;
            this.active = active;
            this.category = category;
            this.lookupIdentityCount = lookupIdentityCount;
            this.categoryOccurrenceCount = categoryOccurrenceCount;
            this.unionIdentityCount = unionIdentityCount;
            this.bothCount = bothCount;
            this.lookupOnlyCount = lookupOnlyCount;
            this.categoryOnlyCount = categoryOnlyCount;
        }
    }

    private static final class Draft {
        final Recipe recipe;
        final JsonObject value;
        final byte[] bytes;
        final String sha256;
        final boolean lookupActive;
        final boolean categoryPresent;

        Draft(
            Recipe recipe,
            JsonObject value,
            byte[] bytes,
            String sha256,
            boolean lookupActive,
            boolean categoryPresent
        ) {
            this.recipe = recipe;
            this.value = value;
            this.bytes = bytes;
            this.sha256 = sha256;
            this.lookupActive = lookupActive;
            this.categoryPresent = categoryPresent;
        }
    }
}
