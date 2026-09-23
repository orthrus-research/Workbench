package dev.workbench.crucible.runtimegraph.client;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;

import mezz.jei.api.IJeiRuntime;
import mezz.jei.api.IModRegistry;
import mezz.jei.api.IRecipeRegistry;
import mezz.jei.api.ingredients.IIngredientHelper;
import mezz.jei.api.ingredients.IIngredientRegistry;
import mezz.jei.api.recipe.IIngredientType;
import mezz.jei.api.recipe.IRecipeCategory;
import mezz.jei.api.recipe.IRecipeWrapper;

import net.minecraft.client.Minecraft;
import net.minecraft.client.resources.Language;
import net.minecraft.client.resources.ResourcePackRepository;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.JsonToNBT;
import net.minecraft.nbt.NBTException;
import net.minecraft.nbt.NBTTagCompound;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Deterministic public-API projection of naturally settled client presentation. */
public final class ClientJeiCapture {
    private static final String GT_FLUID_VEIN_INFO =
        "gregtech.integration.jei.basic.GTFluidVeinInfo";
    private static final String GROOVYSCRIPT_SHAPED_RECIPE_WRAPPER =
        "com.cleanroommc.groovyscript.compat.mods.jei.ShapedRecipeWrapper";
    private static IModRegistry modRegistry;
    private static IJeiRuntime runtime;

    private ClientJeiCapture() {}

    static synchronized void register(IModRegistry value) {
        if (value == null || modRegistry != null) {
            throw new IllegalStateException("HEI mod registry callback must occur once");
        }
        modRegistry = value;
    }

    static synchronized void runtimeAvailable(IJeiRuntime value) {
        if (value == null || runtime != null) {
            throw new IllegalStateException("HEI runtime callback must occur once");
        }
        runtime = value;
    }

    public static synchronized boolean isSettled() {
        if (modRegistry == null || runtime == null || runtime.getIngredientFilter() == null) {
            return false;
        }
        Object filter = runtime.getIngredientFilter();
        if (!"mezz.jei.ingredients.IngredientFilter".equals(filter.getClass().getName())) {
            throw new IllegalStateException(
                "unexpected HEI ingredient filter " + filter.getClass().getName()
            );
        }
        try {
            Field afterBlock = filter.getClass().getDeclaredField("afterBlock");
            Field delegatedActions = filter.getClass().getDeclaredField("delegatedActions");
            if (afterBlock.getType() != boolean.class
                || !List.class.isAssignableFrom(delegatedActions.getType())) {
                throw new IllegalStateException("HEI readiness field shape drifted");
            }
            afterBlock.setAccessible(true);
            delegatedActions.setAccessible(true);
            return afterBlock.getBoolean(filter) && delegatedActions.get(filter) == null;
        } catch (ReflectiveOperationException failure) {
            throw new IllegalStateException("cannot inspect HEI readiness", failure);
        }
    }

    static synchronized AdapterSnapshot snapshot(String adapterId) {
        if (!isSettled()) throw new IllegalStateException("HEI is not settled");
        if ("jei-ingredient-types".equals(adapterId)) return ingredientTypes();
        if ("jei-ingredients".equals(adapterId)) return ingredients();
        if ("jei-recipe-categories".equals(adapterId)) return recipeCategories();
        if ("jei-recipe-wrappers".equals(adapterId)) return recipeWrappers();
        if ("jei-recipe-catalysts".equals(adapterId)) return recipeCatalysts();
        if ("client-resource-localization-state".equals(adapterId)) {
            return resourceState();
        }
        throw new IllegalArgumentException("unknown client adapter " + adapterId);
    }

    private static AdapterSnapshot ingredientTypes() {
        IIngredientRegistry ingredients = ingredientRegistry();
        List<TypeDescriptor> types = types(ingredients);
        List<JsonObject> rows = new ArrayList<JsonObject>();
        for (TypeDescriptor type : types) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "jei-ingredient-type");
            row.addProperty("type_key", type.key);
            row.addProperty("ingredient_class", type.type.getIngredientClass().getName());
            row.addProperty("helper_class", type.helper.getClass().getName());
            row.addProperty("registered_ingredient_count", all(ingredients, type).size());
            rows.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "jei-ingredient-type-authority");
        authority.addProperty("registered_type_count", types.size());
        authority.addProperty("source", "IIngredientRegistry.getRegisteredIngredientTypes");
        rows.add(authority);
        return complete(rows);
    }

    private static AdapterSnapshot ingredients() {
        IIngredientRegistry registry = ingredientRegistry();
        List<TypeDescriptor> types = types(registry);
        Map<IIngredientType<?>, TypeDescriptor> byIdentity = byIdentity(types);
        Set<String> visible = visibleIngredientKeys(registry, byIdentity);
        List<JsonObject> drafts = new ArrayList<JsonObject>();
        for (TypeDescriptor type : types) {
            for (Object value : all(registry, type)) {
                JsonObject identity = ingredientIdentity(type, value);
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "jei-ingredient-occurrence");
                row.addProperty("type_key", type.key);
                row.addProperty("ingredient_identity_sha256", CanonicalJson.sha256(identity));
                row.add("helper_identity", identity);
                row.addProperty("display_name", text(type.helper.getDisplayName(value)));
                row.addProperty("valid", type.helper.isValidIngredient(value));
                row.addProperty("on_server", type.helper.isIngredientOnServer(value));
                row.addProperty("craftable", registry.isIngredientCraftable(value));
                row.addProperty("visible_with_empty_filter", visible.contains(key(type, value)));
                drafts.add(row);
            }
        }
        Collections.sort(drafts, jsonComparator());
        Map<String, Integer> ordinals = new HashMap<String, Integer>();
        for (JsonObject row : drafts) {
            String key = CanonicalJson.sha256(row);
            Integer prior = ordinals.get(key);
            int ordinal = prior == null ? 0 : prior.intValue() + 1;
            ordinals.put(key, Integer.valueOf(ordinal));
            row.addProperty("duplicate_ordinal", ordinal);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "jei-ingredient-authority");
        authority.addProperty("registered_occurrence_count", drafts.size());
        authority.addProperty("visible_identity_count", visible.size());
        authority.addProperty("filter_text", text(runtime.getIngredientFilter().getFilterText()));
        authority.addProperty("filter_mutated_by_capture", false);
        drafts.add(authority);
        return complete(drafts);
    }

    private static AdapterSnapshot recipeCategories() {
        List<IRecipeCategory<?>> categories = categories();
        List<JsonObject> rows = new ArrayList<JsonObject>();
        Set<String> uids = new HashSet<String>();
        for (IRecipeCategory<?> category : categories) {
            String uid = text(category.getUid());
            if (!uids.add(uid)) throw new IllegalStateException("duplicate HEI category " + uid);
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "jei-recipe-category");
            row.addProperty("uid", uid);
            row.addProperty("title", text(category.getTitle()));
            row.addProperty("mod_name", text(category.getModName()));
            row.addProperty("implementation_class", category.getClass().getName());
            row.addProperty("background_class", category.getBackground().getClass().getName());
            row.addProperty("background_width", category.getBackground().getWidth());
            row.addProperty("background_height", category.getBackground().getHeight());
            if (category.getIcon() == null) row.addProperty("icon_present", false);
            else {
                row.addProperty("icon_present", true);
                row.addProperty("icon_class", category.getIcon().getClass().getName());
                row.addProperty("icon_width", category.getIcon().getWidth());
                row.addProperty("icon_height", category.getIcon().getHeight());
            }
            rows.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "jei-recipe-category-authority");
        authority.addProperty("category_count", categories.size());
        authority.addProperty("source", "IRecipeRegistry.getRecipeCategories");
        rows.add(authority);
        return complete(rows);
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static AdapterSnapshot recipeWrappers() {
        IIngredientRegistry ingredients = ingredientRegistry();
        Map<IIngredientType<?>, TypeDescriptor> types = byIdentity(types(ingredients));
        IRecipeRegistry recipes = recipeRegistry();
        List<JsonObject> drafts = new ArrayList<JsonObject>();
        for (IRecipeCategory category : categories()) {
            String uid = text(category.getUid());
            List<IRecipeWrapper> wrappers = recipes.getRecipeWrappers(category);
            if (wrappers == null) throw new IllegalStateException("null HEI wrappers " + uid);
            for (IRecipeWrapper wrapper : wrappers) {
                if (wrapper == null) throw new IllegalStateException("null HEI wrapper " + uid);
                RecordingIngredients recording = new RecordingIngredients(ingredients);
                wrapper.getIngredients(recording);
                String implementation = wrapper.getClass().getName();
                boolean gtFluidVein = GT_FLUID_VEIN_INFO.equals(implementation);
                if (gtFluidVein && !"gregtech:fluid_spawn_location".equals(uid)) {
                    throw new IllegalStateException(
                        "GT fluid-vein wrapper appeared outside its exact category"
                    );
                }
                boolean bdsandmCrateRecipe = isBdsandmCrateRecipe(wrapper, implementation);
                JsonObject slots = new JsonObject();
                slots.add(
                    "inputs",
                    slots(recording.inputSnapshot(), types, gtFluidVein, false)
                );
                slots.add(
                    "outputs",
                    slots(recording.outputSnapshot(), types, gtFluidVein, bdsandmCrateRecipe)
                );
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "jei-recipe-wrapper");
                row.addProperty("category_uid", uid);
                row.addProperty("implementation_class", implementation);
                if (gtFluidVein) {
                    row.addProperty(
                        "normalization",
                        "gregtech-fluid-vein-cumulative-bucket-slot-v1"
                    );
                } else if (bdsandmCrateRecipe) {
                    row.addProperty(
                        "normalization",
                        "bdsandm-redundant-crate-capability-cache-v1"
                    );
                }
                row.addProperty("presentation_sha256", CanonicalJson.sha256(slots));
                row.add("slots", slots);
                drafts.add(row);
            }
        }
        Collections.sort(drafts, jsonComparator());
        addDuplicateOrdinals(drafts);
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "jei-recipe-wrapper-authority");
        authority.addProperty("wrapper_occurrence_count", drafts.size());
        authority.addProperty("wrapper_execution", "getIngredients-only");
        authority.addProperty("gui_layout_or_draw_invoked", false);
        JsonArray normalizations = new JsonArray();
        JsonObject gtNormalization = new JsonObject();
        gtNormalization.addProperty(
            "normalization_id", "gregtech-fluid-vein-cumulative-bucket-slot-v1"
        );
        gtNormalization.addProperty(
            "basis", "exact-runtime-bytecode-appends-one-identical-bucket-slot-per-call"
        );
        normalizations.add(gtNormalization);
        JsonObject crateNormalization = new JsonObject();
        crateNormalization.addProperty(
            "normalization_id", "bdsandm-redundant-crate-capability-cache-v1"
        );
        crateNormalization.addProperty(
            "basis",
            "two-exact-crate-outputs-add-a-root-cache-identical-to-ForgeCaps.Parent-on-repeat"
        );
        normalizations.add(crateNormalization);
        authority.add("known_normalizations", normalizations);
        drafts.add(authority);
        return complete(drafts);
    }

    private static boolean isBdsandmCrateRecipe(
        IRecipeWrapper wrapper, String implementation
    ) {
        if (!GROOVYSCRIPT_SHAPED_RECIPE_WRAPPER.equals(implementation)) return false;
        mezz.jei.plugins.vanilla.crafting.ShapelessRecipeWrapper<?> shaped =
            (mezz.jei.plugins.vanilla.crafting.ShapelessRecipeWrapper<?>) wrapper;
        String name = String.valueOf(shaped.getRegistryName());
        return "bdsandm:wood_crate".equals(name) || "bdsandm:metal_crate".equals(name);
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static AdapterSnapshot recipeCatalysts() {
        IIngredientRegistry ingredients = ingredientRegistry();
        Map<IIngredientType<?>, TypeDescriptor> types = byIdentity(types(ingredients));
        IRecipeRegistry recipes = recipeRegistry();
        List<JsonObject> drafts = new ArrayList<JsonObject>();
        for (IRecipeCategory category : categories()) {
            String uid = text(category.getUid());
            List<Object> catalysts = recipes.getRecipeCatalysts(category);
            if (catalysts == null) throw new IllegalStateException("null HEI catalysts " + uid);
            for (Object catalyst : catalysts) {
                TypeDescriptor type = descriptor(ingredients, types, catalyst);
                JsonObject identity = ingredientIdentity(type, catalyst);
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "jei-recipe-catalyst-occurrence");
                row.addProperty("category_uid", uid);
                row.addProperty("type_key", type.key);
                row.addProperty("ingredient_identity_sha256", CanonicalJson.sha256(identity));
                row.add("helper_identity", identity);
                drafts.add(row);
            }
        }
        Collections.sort(drafts, jsonComparator());
        addDuplicateOrdinals(drafts);
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "jei-recipe-catalyst-authority");
        authority.addProperty("catalyst_occurrence_count", drafts.size());
        authority.addProperty("source", "IRecipeRegistry.getRecipeCatalysts");
        drafts.add(authority);
        return complete(drafts);
    }

    private static AdapterSnapshot resourceState() {
        Minecraft minecraft = Minecraft.getMinecraft();
        Language language = minecraft.getLanguageManager().getCurrentLanguage();
        if (language == null) throw new IllegalStateException("client language is unavailable");
        List<String> packs = new ArrayList<String>();
        for (ResourcePackRepository.Entry entry
            : minecraft.getResourcePackRepository().getRepositoryEntries()) {
            packs.add(text(entry.getResourcePackName()));
        }
        Collections.sort(packs);
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "client-resource-localization-authority");
        row.addProperty("language_code", text(language.getLanguageCode()));
        row.addProperty("language_bidirectional", language.isBidirectional());
        row.addProperty("force_unicode_font", minecraft.gameSettings.forceUnicodeFont);
        row.addProperty("connected_world_present", minecraft.world != null);
        row.addProperty("dedicated_connection", !minecraft.isSingleplayer());
        JsonArray selected = new JsonArray();
        for (String pack : packs) selected.add(pack);
        row.add("selected_resource_packs", selected);
        row.addProperty("selected_resource_pack_count", packs.size());
        row.addProperty("display_names_captured_by", "HEI ingredient and category helpers");
        return complete(Collections.singletonList(row));
    }

    private static IIngredientRegistry ingredientRegistry() {
        IIngredientRegistry result = modRegistry.getIngredientRegistry();
        if (result == null) throw new IllegalStateException("HEI ingredient registry missing");
        return result;
    }

    private static IRecipeRegistry recipeRegistry() {
        IRecipeRegistry result = runtime.getRecipeRegistry();
        if (result == null) throw new IllegalStateException("HEI recipe registry missing");
        return result;
    }

    private static List<IRecipeCategory<?>> categories() {
        List<IRecipeCategory<?>> result = new ArrayList<IRecipeCategory<?>>();
        for (Object raw : recipeRegistry().getRecipeCategories()) {
            if (!(raw instanceof IRecipeCategory)) {
                throw new IllegalStateException("HEI returned invalid category");
            }
            result.add((IRecipeCategory<?>) raw);
        }
        Collections.sort(result, new Comparator<IRecipeCategory<?>>() {
            @Override public int compare(IRecipeCategory<?> left, IRecipeCategory<?> right) {
                return text(left.getUid()).compareTo(text(right.getUid()));
            }
        });
        return result;
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static List<TypeDescriptor> types(IIngredientRegistry registry) {
        Collection<IIngredientType> registered = registry.getRegisteredIngredientTypes();
        if (registered == null || registered.isEmpty()) {
            throw new IllegalStateException("HEI ingredient types are empty");
        }
        List<TypeDescriptor> result = new ArrayList<TypeDescriptor>();
        Set<String> keys = new HashSet<String>();
        for (IIngredientType type : registered) {
            if (type == null || type.getIngredientClass() == null) {
                throw new IllegalStateException("HEI returned malformed ingredient type");
            }
            IIngredientHelper<Object> helper = registry.getIngredientHelper(type);
            if (helper == null) throw new IllegalStateException("HEI type lacks helper");
            JsonObject identity = new JsonObject();
            identity.addProperty("ingredient_class", type.getIngredientClass().getName());
            identity.addProperty("helper_class", helper.getClass().getName());
            String key = CanonicalJson.sha256(identity);
            if (!keys.add(key)) throw new IllegalStateException("duplicate HEI type key");
            result.add(new TypeDescriptor(type, helper, key));
        }
        Collections.sort(result, Comparator.comparing(value -> value.key));
        return result;
    }

    private static Map<IIngredientType<?>, TypeDescriptor> byIdentity(
        List<TypeDescriptor> types
    ) {
        Map<IIngredientType<?>, TypeDescriptor> result =
            new IdentityHashMap<IIngredientType<?>, TypeDescriptor>();
        for (TypeDescriptor type : types) result.put(type.type, type);
        return result;
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static Collection<Object> all(
        IIngredientRegistry registry, TypeDescriptor type
    ) {
        Collection<Object> values = registry.getAllIngredients((IIngredientType) type.type);
        if (values == null) throw new IllegalStateException("HEI ingredients are null");
        return values;
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static TypeDescriptor descriptor(
        IIngredientRegistry registry,
        Map<IIngredientType<?>, TypeDescriptor> types,
        Object value
    ) {
        if (value == null) throw new IllegalStateException("HEI ingredient is null");
        IIngredientType type = registry.getIngredientType(value);
        TypeDescriptor result = types.get(type);
        if (result == null) throw new IllegalStateException("HEI ingredient uses unknown type");
        return result;
    }

    private static JsonObject ingredientIdentity(TypeDescriptor type, Object value) {
        return ingredientIdentity(type, value, false);
    }

    private static JsonObject ingredientIdentity(
        TypeDescriptor type, Object value, boolean normalizeBdsandmCrateOutput
    ) {
        if (value == null || !type.type.getIngredientClass().isInstance(value)) {
            throw new IllegalStateException("HEI ingredient disagrees with its type");
        }
        String uniqueId = text(type.helper.getUniqueId(value));
        String fullUniqueId = text(type.helper.getFullUniqueId(value));
        String wildcardId = text(type.helper.getWildcardId(value));
        String modId = text(type.helper.getModId(value));
        String resourceId = text(type.helper.getResourceId(value));
        JsonObject row = new JsonObject();
        row.addProperty("type_key", type.key);
        row.addProperty("unique_id", uniqueId);
        row.addProperty("full_unique_id", normalizeBdsandmCrateOutput
            ? normalizeBdsandmCrateFullUniqueId(fullUniqueId, modId, resourceId)
            : fullUniqueId);
        row.addProperty("wildcard_id", wildcardId);
        row.addProperty("mod_id", modId);
        row.addProperty("resource_id", resourceId);
        return row;
    }

    private static String normalizeBdsandmCrateFullUniqueId(
        String fullUniqueId, String modId, String resourceId
    ) {
        if (!"bdsandm".equals(modId)
            || !("wood_crate".equals(resourceId) || "metal_crate".equals(resourceId))) {
            return fullUniqueId;
        }
        int compoundStart = fullUniqueId.indexOf(":{");
        if (compoundStart < 0) return fullUniqueId;
        try {
            NBTTagCompound root = JsonToNBT.getTagFromJson(
                fullUniqueId.substring(compoundStart + 1)
            );
            if (!root.hasKey("crateCap", 10) || !root.hasKey("ForgeCaps", 10)) {
                return fullUniqueId;
            }
            NBTTagCompound forgeCaps = root.getCompoundTag("ForgeCaps");
            if (!forgeCaps.hasKey("Parent", 10)
                || !root.getCompoundTag("crateCap").equals(
                    forgeCaps.getCompoundTag("Parent")
                )) {
                return fullUniqueId;
            }
            root.removeTag("crateCap");
            return fullUniqueId.substring(0, compoundStart + 1) + root.toString();
        } catch (NBTException failure) {
            throw new IllegalStateException(
                "BDS&M crate full unique ID contains malformed NBT", failure
            );
        }
    }

    private static String key(TypeDescriptor type, Object value) {
        return type.key + ':' + CanonicalJson.sha256(ingredientIdentity(type, value));
    }

    private static Set<String> visibleIngredientKeys(
        IIngredientRegistry registry,
        Map<IIngredientType<?>, TypeDescriptor> types
    ) {
        if (!runtime.getIngredientFilter().getFilterText().isEmpty()) {
            throw new IllegalStateException("HEI filter text must be empty for catalog capture");
        }
        List<Object> filtered = runtime.getIngredientFilter().getFilteredIngredients();
        if (filtered == null) throw new IllegalStateException("HEI filtered ingredients null");
        Set<String> result = new HashSet<String>();
        for (Object value : filtered) result.add(key(descriptor(registry, types, value), value));
        return result;
    }

    private static JsonArray slots(
        Map<IIngredientType<?>, List<List<Object>>> source,
        Map<IIngredientType<?>, TypeDescriptor> types,
        boolean gtFluidVein,
        boolean normalizeBdsandmCrateOutputs
    ) {
        List<Map.Entry<IIngredientType<?>, List<List<Object>>>> entries =
            new ArrayList<Map.Entry<IIngredientType<?>, List<List<Object>>>>(source.entrySet());
        Collections.sort(entries, Comparator.comparing(entry -> {
            TypeDescriptor type = types.get(entry.getKey());
            if (type == null) throw new IllegalStateException("wrapper used unknown HEI type");
            return type.key;
        }));
        JsonArray result = new JsonArray();
        for (Map.Entry<IIngredientType<?>, List<List<Object>>> entry : entries) {
            TypeDescriptor type = types.get(entry.getKey());
            JsonObject typeRow = new JsonObject();
            typeRow.addProperty("type_key", type.key);
            JsonArray slotRows = new JsonArray();
            int ordinal = 0;
            List<List<Object>> capturedSlots = entry.getValue();
            if (gtFluidVein && isExactGtBucketSlotSequence(capturedSlots, type)) {
                capturedSlots = Collections.singletonList(capturedSlots.get(0));
            }
            for (List<Object> slot : capturedSlots) {
                JsonObject slotRow = new JsonObject();
                slotRow.addProperty("slot_ordinal", ordinal++);
                List<JsonObject> alternatives = new ArrayList<JsonObject>();
                for (Object value : slot) {
                    alternatives.add(ingredientIdentity(
                        type, value, normalizeBdsandmCrateOutputs
                    ));
                }
                Collections.sort(alternatives, jsonComparator());
                JsonArray values = new JsonArray();
                for (JsonObject value : alternatives) values.add(value);
                slotRow.add("alternatives", values);
                slotRows.add(slotRow);
            }
            typeRow.add("slots", slotRows);
            result.add(typeRow);
        }
        return result;
    }

    private static boolean isExactGtBucketSlotSequence(
        List<List<Object>> slots, TypeDescriptor type
    ) {
        if (slots == null || slots.size() < 2
            || !ItemStack.class.equals(type.type.getIngredientClass())) return false;
        String first = slotIdentity(slots.get(0), type);
        for (int index = 1; index < slots.size(); index++) {
            if (!first.equals(slotIdentity(slots.get(index), type))) return false;
        }
        List<Object> firstSlot = slots.get(0);
        if (firstSlot == null || firstSlot.size() != 1) return false;
        JsonObject identity = ingredientIdentity(type, firstSlot.get(0));
        String modId = identity.get("mod_id").getAsString();
        String resourceId = identity.get("resource_id").getAsString();
        String uniqueId = identity.get("unique_id").getAsString();
        String fullUniqueId = identity.get("full_unique_id").getAsString();
        String wildcardId = identity.get("wildcard_id").getAsString();
        if (!uniqueId.equals(fullUniqueId)) return false;
        if ("forge".equals(modId) && "bucketfilled".equals(resourceId)) {
            return "forge:bucketfilled".equals(wildcardId)
                && uniqueId.startsWith("forge:bucketfilled:")
                && uniqueId.endsWith(";");
        }
        return "minecraft".equals(modId) && "lava_bucket".equals(resourceId)
            && "minecraft:lava_bucket".equals(wildcardId)
            && "minecraft:lava_bucket:lava;".equals(uniqueId);
    }

    private static String slotIdentity(List<Object> slot, TypeDescriptor type) {
        List<JsonObject> identities = new ArrayList<JsonObject>();
        for (Object value : slot) identities.add(ingredientIdentity(type, value));
        Collections.sort(identities, jsonComparator());
        JsonArray result = new JsonArray();
        for (JsonObject identity : identities) result.add(identity);
        return CanonicalJson.sha256(result);
    }

    private static void addDuplicateOrdinals(List<JsonObject> rows) {
        Map<String, Integer> ordinals = new LinkedHashMap<String, Integer>();
        for (JsonObject row : rows) {
            String key = CanonicalJson.sha256(row);
            Integer prior = ordinals.get(key);
            int ordinal = prior == null ? 0 : prior.intValue() + 1;
            ordinals.put(key, Integer.valueOf(ordinal));
            row.addProperty("duplicate_ordinal", ordinal);
        }
    }

    private static Comparator<JsonObject> jsonComparator() {
        return new Comparator<JsonObject>() {
            @Override public int compare(JsonObject left, JsonObject right) {
                return CanonicalJson.compareUnsigned(
                    CanonicalJson.bytes(left), CanonicalJson.bytes(right)
                );
            }
        };
    }

    private static AdapterSnapshot complete(List<JsonObject> rows) {
        return new AdapterSnapshot(rows, Collections.<String>emptyList(), 0);
    }

    private static String text(String value) {
        if (value == null || value.indexOf('\0') >= 0) {
            throw new IllegalStateException("HEI returned invalid text");
        }
        return value;
    }

    private static final class TypeDescriptor {
        private final IIngredientType<?> type;
        private final IIngredientHelper<Object> helper;
        private final String key;

        private TypeDescriptor(
            IIngredientType<?> type, IIngredientHelper<Object> helper, String key
        ) {
            this.type = type;
            this.helper = helper;
            this.key = key;
        }
    }
}
