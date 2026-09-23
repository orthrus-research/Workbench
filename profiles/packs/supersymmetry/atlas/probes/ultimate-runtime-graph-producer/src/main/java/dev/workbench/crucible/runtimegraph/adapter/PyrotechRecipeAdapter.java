package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.item.ItemStack;
import net.minecraft.item.crafting.Ingredient;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.ForgeRegistry;
import net.minecraftforge.registries.IForgeRegistryEntry;
import net.minecraftforge.registries.RegistryManager;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Exact Pyrotech custom-recipe projections without invoking contextual lookup. */
public final class PyrotechRecipeAdapter implements CaptureAdapter {
    private static final String PREFIX = "com.codetaylor.mc.pyrotech.";

    private static final List<RecipeFamily> FAMILIES = Arrays.asList(
        family("granite_anvil_recipe", "modules.tech.basic.recipe.AnvilRecipe",
            "tool-strike-forming", "granite-anvil", null,
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("hits", "getHits"), value("tool_type", "getType"),
            enumMatches("accepted_tiers", "isTier", "modules.tech.basic.recipe.AnvilRecipe$EnumTier")),
        family("barrel_recipe", "modules.tech.basic.recipe.BarrelRecipe",
            "fluid-batch-processing", "barrel", null,
            ingredientArray("input_items", "getInputItems"), value("input_fluid", "getInputFluid"),
            value("output_fluid", "getOutput"), value("time_ticks", "getTimeTicks")),
        family("bloomery_recipe", "modules.tech.bloomery.recipe.BloomeryRecipe",
            "bloomery-smelting", "bloomery", null, bloomeryProjections()),
        family("campfire_recipe", "modules.tech.basic.recipe.CampfireRecipe",
            "open-fire-heating", "campfire", "vanilla-smelting-fallback",
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("time_ticks", "getTicks")),
        family("chopping_block_recipe", "modules.tech.basic.recipe.ChoppingBlockRecipe",
            "tool-strike-cutting", "chopping-block", null,
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("chops", "getChops"), value("quantities", "getQuantities")),
        family("compacting_bin_recipe", "modules.tech.basic.recipe.CompactingBinRecipe",
            "manual-compaction", "compacting-bin", null, compactingProjections()),
        family("compost_bin_recipe", "modules.tech.basic.recipe.CompostBinRecipe",
            "composting", "compost-bin", null,
            value("input", "getInput"), value("output", "getOutput"),
            value("compost_value", "getCompostValue")),
        family("crucible_brick_recipe", "modules.tech.machine.recipe.BrickCrucibleRecipe",
            "solid-to-fluid-heating", "brick-crucible", null, crucibleProjections()),
        family("crucible_stone_recipe", "modules.tech.machine.recipe.StoneCrucibleRecipe",
            "solid-to-fluid-heating", "stone-crucible", null, crucibleProjections()),
        family("crude_drying_rack_recipe", "modules.tech.basic.recipe.CrudeDryingRackRecipe",
            "passive-drying", "crude-drying-rack", null, dryingProjections()),
        family("drying_rack_recipe", "modules.tech.basic.recipe.DryingRackRecipe",
            "passive-drying", "drying-rack", null, dryingProjections()),
        family("kiln_brick_recipe", "modules.tech.machine.recipe.BrickKilnRecipe",
            "enclosed-kiln-heating", "brick-kiln", null, kilnProjections()),
        family("kiln_pit_recipe", "modules.tech.basic.recipe.KilnPitRecipe",
            "pit-kiln-heating", "pit-kiln", null, kilnProjections()),
        family("kiln_stone_recipe", "modules.tech.machine.recipe.StoneKilnRecipe",
            "enclosed-kiln-heating", "stone-kiln", null, kilnProjections()),
        family("mechanical_compacting_bin_recipe",
            "modules.tech.machine.recipe.MechanicalCompactingBinRecipe",
            "mechanical-compaction", "mechanical-compacting-bin", null,
            compactingProjections()),
        family("mill_brick_recipe", "modules.tech.machine.recipe.BrickSawmillRecipe",
            "powered-cutting", "brick-sawmill", null, sawmillProjections()),
        family("mill_stone_recipe", "modules.tech.machine.recipe.StoneSawmillRecipe",
            "powered-cutting", "stone-sawmill", null, sawmillProjections()),
        family("oven_brick_recipe", "modules.tech.machine.recipe.BrickOvenRecipe",
            "enclosed-oven-heating", "brick-oven", "vanilla-smelting-fallback",
            ovenProjections()),
        family("oven_stone_recipe", "modules.tech.machine.recipe.StoneOvenRecipe",
            "enclosed-oven-heating", "stone-oven", "vanilla-smelting-fallback",
            ovenProjections()),
        family("pit_recipe", "modules.tech.refractory.recipe.PitBurnRecipe",
            "refractory-pit-burning", "refractory-burn-pit", null,
            value("input_block_matcher", "getInputMatcher"), value("output", "getOutput"),
            value("burn_stages", "getBurnStages"), value("time_ticks", "getTimeTicks"),
            value("fluid_produced", "getFluidProduced"),
            value("failure_chance", "getFailureChance"),
            value("failure_items", "getFailureItems"),
            value("requires_refractory_blocks", "requiresRefractoryBlocks"),
            value("fluid_level_affects_failure", "doesFluidLevelAffectFailureChance")),
        family("soaking_pot_recipe", "modules.tech.basic.recipe.SoakingPotRecipe",
            "fluid-infusion", "soaking-pot", null,
            ingredient("input_item", "getInputItem"), value("input_fluid", "getInputFluid"),
            value("output", "getOutput"),
            value("campfire_required", "isCampfireRequired"),
            value("time_ticks", "getTimeTicks")),
        family("tanning_rack_recipe", "modules.tech.basic.recipe.TanningRackRecipe",
            "passive-tanning", "tanning-rack", null,
            ingredient("input_item", "getInputItem"), value("output", "getOutput"),
            value("rain_failure_item", "getRainFailureItem"),
            value("time_ticks", "getTimeTicks")),
        family("wither_forge_recipe", "modules.tech.bloomery.recipe.WitherForgeRecipe",
            "wither-forge-smelting", "wither-forge", null, bloomeryProjections()),
        family("worktable_recipe", "modules.tech.basic.recipe.WorktableRecipe",
            "tool-assisted-crafting", "worktable", "vanilla-crafting-wrapper-fallback",
            value("wrapped_recipe", "getRecipe"), value("tools", "getToolList"),
            value("tool_damage", "getToolDamage"), value("stages", "getStages"),
            value("output", "getOutput"))
    );

    @Override public String adapterId() { return "pyrotech-custom-recipes"; }
    @Override public String categoryId() { return "pyrotech-recipes"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        int recipeCount = 0;
        int nonemptyRegistryCount = 0;
        int dynamicFallbackRegistryCount = 0;
        for (RecipeFamily family : FAMILIES) {
            Class<?> expectedClass = load(family.runtimeClass);
            ForgeRegistry<?> registry = registry(family.registryName);
            if (registry.getRegistrySuperType() != expectedClass) {
                throw new IllegalStateException(
                    family.registryName + " registry supertype changed from " + family.runtimeClass
                );
            }
            List<ResourceLocation> keys = new ArrayList<ResourceLocation>(registry.getKeys());
            Collections.sort(keys);
            if (!keys.isEmpty()) nonemptyRegistryCount++;
            if (family.dynamicFallback != null) dynamicFallbackRegistryCount++;
            records.add(registryRecord(family, expectedClass, keys.size()));
            Set<Integer> numericIds = new HashSet<Integer>();
            for (ResourceLocation key : keys) {
                Object recipe = registry.getValue(key);
                int numericId = registry.getID(key);
                if (recipe == null || recipe.getClass() != expectedClass || numericId < 0
                    || !numericIds.add(Integer.valueOf(numericId))
                    || !(recipe instanceof IForgeRegistryEntry<?>)
                    || !key.equals(((IForgeRegistryEntry<?>) recipe).getRegistryName())) {
                    throw new IllegalStateException(
                        "invalid Pyrotech recipe " + family.registryName + '/' + key
                    );
                }
                records.add(recipeRecord(family, recipe, key, numericId, encoder));
                recipeCount++;
            }
        }
        if (FAMILIES.size() != 24 || nonemptyRegistryCount != 19 || recipeCount != 700) {
            throw new IllegalStateException(
                "Pyrotech custom recipe universe drifted: registries=" + FAMILIES.size()
                    + " nonempty=" + nonemptyRegistryCount + " recipes=" + recipeCount
            );
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "pyrotech-recipe-catalog-authority");
        authority.addProperty("registry_count", FAMILIES.size());
        authority.addProperty("nonempty_registry_count", nonemptyRegistryCount);
        authority.addProperty("empty_registry_count", FAMILIES.size() - nonemptyRegistryCount);
        authority.addProperty("custom_recipe_count", recipeCount);
        authority.addProperty("dynamic_fallback_registry_count", dynamicFallbackRegistryCount);
        authority.addProperty("contextual_lookup_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static ForgeRegistry<?> registry(String name) {
        ForgeRegistry<?> registry = RegistryManager.ACTIVE.getRegistry(new ResourceLocation(name));
        if (registry == null) throw new IllegalStateException("missing Pyrotech registry " + name);
        return registry;
    }

    private static JsonObject registryRecord(
        RecipeFamily family,
        Class<?> runtimeClass,
        int recipeCount
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "pyrotech-recipe-registry");
        row.addProperty("registry_name", family.registryName);
        row.addProperty("runtime_class", family.runtimeClass);
        row.addProperty("process_family", family.processFamily);
        row.addProperty("machine_class", family.machineClass);
        row.addProperty("custom_recipe_count", recipeCount);
        row.addProperty("custom_registry_empty", recipeCount == 0);
        if (family.dynamicFallback == null) row.add("dynamic_fallback", JsonNull.INSTANCE);
        else row.addProperty("dynamic_fallback", family.dynamicFallback);
        row.add("contextual_lookup_methods", methodSurface(runtimeClass, "getRecipe", true));
        row.addProperty("contextual_lookup_invoked", false);
        row.addProperty(
            "contextual_boundary",
            family.dynamicFallback == null
                ? "registered finite projections do not execute recipe matching or process behavior"
                : "empty or unmatched custom lookup may delegate to a contextual runtime fallback"
        );
        return row;
    }

    private static JsonObject recipeRecord(
        RecipeFamily family,
        Object recipe,
        ResourceLocation key,
        int numericId,
        StableValueEncoder encoder
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "pyrotech-custom-recipe");
        row.addProperty("recipe_name", key.toString());
        row.addProperty("recipe_registry_name", family.registryName);
        row.addProperty("numeric_id", numericId);
        row.addProperty("runtime_class", recipe.getClass().getName());
        row.addProperty("process_family", family.processFamily);
        row.addProperty("machine_class", family.machineClass);
        JsonObject projections = new JsonObject();
        JsonObject owners = new JsonObject();
        for (Projection projection : family.projections) {
            if (projection.kind == ProjectionKind.ENUM_MATCHES) {
                projections.add(
                    projection.name,
                    enumMatches(recipe, projection, owners)
                );
                continue;
            }
            Method method = zeroArgumentMethod(recipe.getClass(), projection.method);
            Object value = invoke(method, recipe);
            owners.addProperty(projection.name, method.getDeclaringClass().getName());
            if (projection.kind == ProjectionKind.INGREDIENT) {
                if (!(value instanceof Ingredient)) {
                    throw new IllegalStateException(projection.method + " no longer returns Ingredient");
                }
                projections.add(projection.name, ingredient((Ingredient) value, encoder));
            } else if (projection.kind == ProjectionKind.INGREDIENT_ARRAY) {
                if (!(value instanceof Ingredient[])) {
                    throw new IllegalStateException(projection.method + " no longer returns Ingredient[]");
                }
                JsonArray values = new JsonArray();
                for (Ingredient ingredient : (Ingredient[]) value) {
                    if (ingredient == null) {
                        throw new IllegalStateException("null Pyrotech ingredient array member");
                    }
                    values.add(ingredient(ingredient, encoder));
                }
                projections.add(projection.name, values);
            } else {
                projections.add(projection.name, encoder.encode(value));
            }
        }
        row.add("projections", projections);
        row.add("projection_method_owners", owners);
        row.add("matching_method_surface", methodSurface(recipe.getClass(), "matches", false));
        row.addProperty("contextual_matching_invoked", false);
        row.addProperty(
            "contextual_boundary",
            "matching, random outputs, inventory mutation, world state, fuel, and machine execution were not invoked"
        );
        row.addProperty("registry_numeric_ordinal", numericId);
        return row;
    }

    private static JsonObject ingredient(Ingredient ingredient, StableValueEncoder encoder) {
        ItemStack[] matching = ingredient.getMatchingStacks();
        if (matching == null) throw new IllegalStateException("null Pyrotech ingredient alternatives");
        List<JsonElement> alternatives = new ArrayList<JsonElement>(matching.length);
        for (ItemStack stack : matching) {
            if (stack == null || stack.isEmpty() || !ingredient.apply(stack.copy())) {
                throw new IllegalStateException("Pyrotech ingredient alternative failed its predicate");
            }
            alternatives.add(encoder.encode(stack));
        }
        Collections.sort(alternatives, new Comparator<JsonElement>() {
            @Override
            public int compare(JsonElement left, JsonElement right) {
                return CanonicalJson.compareUnsigned(CanonicalJson.bytes(left), CanonicalJson.bytes(right));
            }
        });
        JsonObject row = new JsonObject();
        row.addProperty("runtime_class", ingredient.getClass().getName());
        row.addProperty("empty", ingredient == Ingredient.EMPTY);
        row.addProperty("finite_alternative_count", alternatives.size());
        row.addProperty("alternative_predicates_validated", true);
        JsonArray values = new JsonArray();
        for (JsonElement alternative : alternatives) values.add(alternative);
        row.add("matching_stacks", values);
        return row;
    }

    private static JsonArray enumMatches(
        Object recipe,
        Projection projection,
        JsonObject owners
    ) {
        Class<?> enumType = load(projection.enumClass);
        Object[] constants = enumType.getEnumConstants();
        if (constants == null) throw new IllegalStateException(projection.enumClass + " is not an enum");
        Method method;
        try {
            method = recipe.getClass().getMethod(projection.method, enumType);
        } catch (NoSuchMethodException exception) {
            throw new IllegalStateException("missing enum matcher " + projection.method, exception);
        }
        owners.addProperty(projection.name, method.getDeclaringClass().getName());
        JsonArray result = new JsonArray();
        for (Object constant : constants) {
            Object accepted = invoke(method, recipe, constant);
            if (!(accepted instanceof Boolean)) {
                throw new IllegalStateException(projection.method + " no longer returns boolean");
            }
            if (((Boolean) accepted).booleanValue()) {
                result.add(((Enum<?>) constant).name());
            }
        }
        return result;
    }

    private static Method zeroArgumentMethod(Class<?> owner, String name) {
        try {
            Method method = owner.getMethod(name);
            if (Modifier.isStatic(method.getModifiers()) || method.isBridge() || method.isSynthetic()) {
                throw new IllegalStateException("invalid projection method " + owner.getName() + '#' + name);
            }
            return method;
        } catch (NoSuchMethodException exception) {
            throw new IllegalStateException("missing projection method " + owner.getName() + '#' + name, exception);
        }
    }

    private static Object invoke(Method method, Object owner, Object... arguments) {
        try {
            return method.invoke(owner, arguments);
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot invoke projection " + signature(method), exception);
        } catch (InvocationTargetException exception) {
            throw new IllegalStateException("projection failed " + signature(method), exception.getCause());
        }
    }

    private static JsonArray methodSurface(Class<?> owner, String name, boolean requireStatic) {
        List<Method> methods = new ArrayList<Method>();
        for (Method method : owner.getMethods()) {
            if (!name.equals(method.getName()) || method.isBridge() || method.isSynthetic()
                || Modifier.isStatic(method.getModifiers()) != requireStatic) continue;
            methods.add(method);
        }
        Collections.sort(methods, new Comparator<Method>() {
            @Override
            public int compare(Method left, Method right) {
                return signature(left).compareTo(signature(right));
            }
        });
        if (methods.isEmpty()) {
            throw new IllegalStateException("missing contextual method " + owner.getName() + '#' + name);
        }
        JsonArray result = new JsonArray();
        for (Method method : methods) {
            JsonObject row = new JsonObject();
            row.addProperty("declaring_class", method.getDeclaringClass().getName());
            row.addProperty("signature", signature(method));
            row.addProperty("method_body_captured", false);
            result.add(row);
        }
        return result;
    }

    private static String signature(Method method) {
        StringBuilder value = new StringBuilder();
        value.append(method.getDeclaringClass().getName()).append('#')
            .append(method.getName()).append('(');
        for (int index = 0; index < method.getParameterTypes().length; index++) {
            if (index > 0) value.append(',');
            value.append(method.getParameterTypes()[index].getName());
        }
        return value.append("):" ).append(method.getReturnType().getName()).toString();
    }

    private static Class<?> load(String name) {
        String qualified = name.startsWith(PREFIX) ? name : PREFIX + name;
        try {
            return Class.forName(qualified, false, PyrotechRecipeAdapter.class.getClassLoader());
        } catch (ClassNotFoundException exception) {
            throw new IllegalStateException("missing exact Pyrotech class " + qualified, exception);
        }
    }

    private static RecipeFamily family(
        String registryPath,
        String runtimeClass,
        String processFamily,
        String machineClass,
        String dynamicFallback,
        Projection... projections
    ) {
        return new RecipeFamily(
            "pyrotech:" + registryPath,
            PREFIX + runtimeClass,
            processFamily,
            machineClass,
            dynamicFallback,
            Arrays.asList(projections)
        );
    }

    private static Projection value(String name, String method) {
        return new Projection(name, method, ProjectionKind.VALUE, null);
    }

    private static Projection ingredient(String name, String method) {
        return new Projection(name, method, ProjectionKind.INGREDIENT, null);
    }

    private static Projection ingredientArray(String name, String method) {
        return new Projection(name, method, ProjectionKind.INGREDIENT_ARRAY, null);
    }

    private static Projection enumMatches(String name, String method, String enumClass) {
        return new Projection(name, method, ProjectionKind.ENUM_MATCHES, PREFIX + enumClass);
    }

    private static Projection[] bloomeryProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("output_bloom", "getOutputBloom"),
            value("bloom_yield_min", "getBloomYieldMin"),
            value("bloom_yield_max", "getBloomYieldMax"),
            value("accepted_anvil_tiers", "getAnvilTiers"),
            value("slag_count", "getSlagCount"), value("time_ticks", "getTimeTicks"),
            value("failure_chance", "getFailureChance"),
            value("failure_items", "getFailureItems"),
            value("slag_item", "getSlagItemStack"), value("language_key", "getLangKey")
        };
    }

    private static Projection[] compactingProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("amount", "getAmount"), value("required_tool_uses", "getRequiredToolUses")
        };
    }

    private static Projection[] crucibleProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output_fluid", "getOutput"),
            value("time_ticks", "getTimeTicks")
        };
    }

    private static Projection[] dryingProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("time_ticks", "getTimeTicks")
        };
    }

    private static Projection[] kilnProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("time_ticks", "getTimeTicks"),
            value("failure_chance", "getFailureChance"),
            value("failure_items", "getFailureItems")
        };
    }

    private static Projection[] ovenProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("time_ticks", "getTimeTicks")
        };
    }

    private static Projection[] sawmillProjections() {
        return new Projection[] {
            ingredient("input", "getInput"), value("output", "getOutput"),
            value("time_ticks", "getTimeTicks"), ingredient("blade", "getBlade"),
            value("wood_chips", "getWoodChips")
        };
    }

    private enum ProjectionKind { VALUE, INGREDIENT, INGREDIENT_ARRAY, ENUM_MATCHES }

    private static final class Projection {
        private final String name;
        private final String method;
        private final ProjectionKind kind;
        private final String enumClass;

        private Projection(String name, String method, ProjectionKind kind, String enumClass) {
            this.name = name;
            this.method = method;
            this.kind = kind;
            this.enumClass = enumClass;
        }
    }

    private static final class RecipeFamily {
        private final String registryName;
        private final String runtimeClass;
        private final String processFamily;
        private final String machineClass;
        private final String dynamicFallback;
        private final List<Projection> projections;

        private RecipeFamily(
            String registryName,
            String runtimeClass,
            String processFamily,
            String machineClass,
            String dynamicFallback,
            List<Projection> projections
        ) {
            this.registryName = registryName;
            this.runtimeClass = runtimeClass;
            this.processFamily = processFamily;
            this.machineClass = machineClass;
            this.dynamicFallback = dynamicFallback;
            this.projections = projections;
        }
    }
}
