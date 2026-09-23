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

import gregtech.api.GregTechAPI;
import gregtech.api.unification.OreDictUnifier;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.registry.IMaterialRegistryManager;
import gregtech.api.unification.material.registry.MaterialRegistry;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.api.unification.stack.ItemMaterialInfo;
import gregtech.api.unification.stack.MaterialStack;
import gregtech.api.unification.stack.UnificationEntry;

import net.minecraft.item.ItemStack;
import net.minecraftforge.oredict.OreDictionary;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/** Realized material/item relations and preferred GT variants. */
public final class GtUnificationAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "gt-unification"; }
    @Override public String categoryId() { return "material-item-relations"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        IMaterialRegistryManager manager = GregTechAPI.materialManager;
        if (manager == null || manager.getPhase() != IMaterialRegistryManager.Phase.FROZEN) {
            throw new IllegalStateException("GT material manager is not FROZEN");
        }
        StableValueEncoder encoder = new StableValueEncoder();
        Map<String, Variant> variants = new LinkedHashMap<String, Variant>();

        String[] oreNames = OreDictionary.getOreNames();
        if (oreNames == null) throw new IllegalStateException("ore dictionary names are null");
        for (String oreName : oreNames) {
            for (ItemStack stack : OreDictionary.getOres(oreName, false)) {
                addVariant(variants, stack, "ore-dictionary:" + oreName, encoder);
            }
        }
        List<Map.Entry<ItemStack, ItemMaterialInfo>> itemInfos = OreDictUnifier.getAllItemInfos();
        if (itemInfos == null) throw new IllegalStateException("GT item material infos are null");
        for (Map.Entry<ItemStack, ItemMaterialInfo> entry : itemInfos) {
            addVariant(variants, entry.getKey(), "item-material-info", encoder);
        }

        List<Material> materials = persistentMaterials(manager.getRegistries());
        List<OrePrefix> prefixes = new ArrayList<OrePrefix>(OrePrefix.values());
        Collections.sort(prefixes, Comparator.comparing(prefix -> prefix.name));
        for (OrePrefix prefix : prefixes) {
            for (Material material : materials) {
                if (!prefix.doGenerateItem(material)) continue;
                ItemStack stack = OreDictUnifier.get(prefix, material);
                if (stack != null && !stack.isEmpty()) {
                    addVariant(
                        variants,
                        stack,
                        "generated-form:" + prefix.name + '|' + material.getRegistryName(),
                        encoder
                    );
                }
            }
            if (prefix.isSelfReferencing) {
                ItemStack stack = OreDictUnifier.get(new UnificationEntry(prefix));
                if (stack != null && !stack.isEmpty()) {
                    addVariant(variants, stack, "self-referencing-form:" + prefix.name, encoder);
                }
            }
        }

        List<JsonObject> records = new ArrayList<JsonObject>();
        for (Map.Entry<ItemStack, ItemMaterialInfo> entry : itemInfos) {
            ItemStack stack = entry.getKey();
            ItemMaterialInfo info = entry.getValue();
            if (stack == null || stack.isEmpty() || info == null) {
                throw new IllegalStateException("GT item material info row is invalid");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "item-material-info");
            JsonElement item = encoder.encode(stack);
            row.add("item", item);
            row.addProperty("item_identity_sha256", CanonicalJson.sha256(item));
            row.add("materials", materialStacks(info.getMaterials()));
            row.addProperty("material_sequence_semantics", "first-entry-is-effective-fallback");
            records.add(row);
        }

        for (Variant variant : variants.values()) {
            records.add(classify(variant, encoder));
        }
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static JsonObject classify(Variant variant, StableValueEncoder encoder) {
        ItemStack stack = variant.stack;
        Set<String> names = OreDictUnifier.getOreDictionaryNames(stack);
        if (names == null || names.contains(null)) {
            throw new IllegalStateException("GT ore-name classification is invalid");
        }
        List<String> oreNames = new ArrayList<String>(names);
        Collections.sort(oreNames);
        for (String name : oreNames) {
            int id = OreDictionary.getOreID(name);
            if (id < 0 || !name.equals(OreDictionary.getOreName(id))) {
                throw new IllegalStateException("GT ore-name classification does not round trip");
            }
        }
        UnificationEntry entry = OreDictUnifier.getUnificationEntry(stack);
        OrePrefix prefix = OreDictUnifier.getPrefix(stack);
        if ((entry == null) != (prefix == null)
            || (entry != null && entry.orePrefix != prefix)) {
            throw new IllegalStateException("GT unification entry/prefix classifications disagree");
        }
        ItemMaterialInfo info = OreDictUnifier.getMaterialInfo(stack);
        MaterialStack effective = null;
        boolean skippedEmptyInfo = info != null && info.getMaterials().isEmpty();
        if (!skippedEmptyInfo) effective = OreDictUnifier.getMaterial(stack);
        ItemStack preferred = OreDictUnifier.getUnificated(stack);

        JsonObject row = new JsonObject();
        row.addProperty("record_type", "material-item-unification");
        row.add("item", variant.payload);
        row.addProperty("item_identity_sha256", variant.identity);
        JsonArray sources = new JsonArray();
        for (String source : variant.sources) sources.add(source);
        row.add("observation_sources", sources);
        JsonArray namesJson = new JsonArray();
        for (String name : oreNames) namesJson.add(name);
        row.add("ore_dictionary_names", namesJson);
        if (entry == null) row.add("unification_entry", JsonNull.INSTANCE);
        else {
            JsonObject entryJson = new JsonObject();
            entryJson.addProperty("prefix", entry.orePrefix.name);
            if (entry.material == null) entryJson.add("material", JsonNull.INSTANCE);
            else entryJson.addProperty("material", entry.material.getRegistryName());
            row.add("unification_entry", entryJson);
        }
        row.add("material_info", info == null ? JsonNull.INSTANCE : materialStacks(info.getMaterials()));
        if (info == null) row.add("material_info_sequence_semantics", JsonNull.INSTANCE);
        else row.addProperty(
            "material_info_sequence_semantics",
            "first-entry-is-effective-fallback"
        );
        row.add("effective_material", materialStack(effective));
        if (effective == null) row.addProperty("effective_material_origin", "none");
        else if (entry != null) {
            row.addProperty("effective_material_origin", "unification-entry-or-prefix-material");
        } else {
            row.addProperty("effective_material_origin", "item-material-info-first-entry");
        }
        row.addProperty("effective_material_lookup_skipped_empty_info", skippedEmptyInfo);
        if (preferred == null || preferred.isEmpty()) {
            row.add("preferred_item", JsonNull.INSTANCE);
            row.add("preferred_item_identity_sha256", JsonNull.INSTANCE);
        } else {
            JsonElement preferredJson = encoder.encode(preferred);
            row.add("preferred_item", preferredJson);
            row.addProperty("preferred_item_identity_sha256", CanonicalJson.sha256(preferredJson));
        }
        row.addProperty(
            "status",
            entry == null && info == null && oreNames.isEmpty() ? "no-identity" : "resolved"
        );
        return row;
    }

    private static void addVariant(
        Map<String, Variant> variants,
        ItemStack stack,
        String source,
        StableValueEncoder encoder
    ) {
        if (stack == null || stack.isEmpty()) {
            throw new IllegalStateException("GT unification source contains an empty item");
        }
        JsonElement payload = encoder.encode(stack);
        String identity = CanonicalJson.sha256(payload);
        Variant current = variants.get(identity);
        if (current == null) {
            current = new Variant(stack.copy(), payload, identity);
            variants.put(identity, current);
        }
        current.sources.add(source);
    }

    private static JsonArray materialStacks(Collection<MaterialStack> values) {
        JsonArray result = new JsonArray();
        if (values == null) return result;
        for (MaterialStack value : values) result.add(materialStack(value));
        return result;
    }

    private static JsonElement materialStack(MaterialStack value) {
        if (value == null) return JsonNull.INSTANCE;
        if (value.material == null) throw new IllegalStateException("material stack has null material");
        JsonObject result = new JsonObject();
        result.addProperty("material", value.material.getRegistryName());
        result.addProperty("amount", value.amount);
        return result;
    }

    private static List<Material> persistentMaterials(Collection<MaterialRegistry> registries) {
        List<Material> result = new ArrayList<Material>();
        Set<String> names = new HashSet<String>();
        for (MaterialRegistry registry : registries) {
            for (Material material : registry.getAllMaterials()) {
                if (material == null || !names.add(material.getRegistryName())) {
                    throw new IllegalStateException("persistent material universe is invalid");
                }
                result.add(material);
            }
        }
        Collections.sort(result, Comparator.comparing(Material::getRegistryName));
        return result;
    }

    private static final class Variant {
        private final ItemStack stack;
        private final JsonElement payload;
        private final String identity;
        private final Set<String> sources = new TreeSet<String>();

        private Variant(ItemStack stack, JsonElement payload, String identity) {
            this.stack = stack;
            this.payload = payload;
            this.identity = identity;
        }
    }
}
