package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;

import gregtech.api.GregTechAPI;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.registry.IMaterialRegistryManager;
import gregtech.api.unification.material.registry.MaterialRegistry;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.api.unification.stack.MaterialStack;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Complete GT prefix definitions and frozen material-generation eligibility. */
public final class GtOrePrefixAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "gt-ore-prefixes"; }
    @Override public String categoryId() { return "material-form-eligibility"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        IMaterialRegistryManager manager = GregTechAPI.materialManager;
        if (manager == null || manager.getPhase() != IMaterialRegistryManager.Phase.FROZEN) {
            throw new IllegalStateException("GT material manager is not FROZEN");
        }
        List<Material> materials = persistentMaterials(manager.getRegistries());
        List<OrePrefix> prefixes = new ArrayList<OrePrefix>(OrePrefix.values());
        Collections.sort(prefixes, Comparator.comparing(prefix -> prefix.name));
        Set<String> names = new HashSet<String>();
        Set<Integer> ids = new HashSet<Integer>();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (OrePrefix prefix : prefixes) {
            if (prefix == null || !names.add(prefix.name) || !ids.add(prefix.id)
                || !prefix.name.equals(prefix.name()) || OrePrefix.getPrefix(prefix.name) != prefix) {
                throw new IllegalStateException("GT ore-prefix registry identity is invalid");
            }
            if (prefix.isSelfReferencing && prefix.materialType == null) {
                throw new IllegalStateException("self-referencing prefix lacks material: " + prefix.name);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "gt-ore-prefix");
            row.addProperty("name", prefix.name);
            row.addProperty("numeric_id", prefix.id);
            row.addProperty("unification_enabled", prefix.isUnificationEnabled);
            row.addProperty("self_referencing", prefix.isSelfReferencing);
            row.addProperty("marker_prefix", prefix.isMarkerPrefix());
            row.addProperty("max_stack_size", prefix.maxStackSize);
            row.addProperty("base_material_amount", prefix.getMaterialAmount(null));
            if (prefix.getAlternativeOreName() == null) {
                row.add("alternative_ore_name", JsonNull.INSTANCE);
            } else {
                row.addProperty("alternative_ore_name", prefix.getAlternativeOreName());
            }
            if (prefix.materialType == null) row.add("material_type", JsonNull.INSTANCE);
            else row.addProperty("material_type", prefix.materialType.getRegistryName());
            if (prefix.materialIconType == null) row.add("material_icon_type", JsonNull.INSTANCE);
            else {
                JsonObject icon = new JsonObject();
                icon.addProperty("id", prefix.materialIconType.id);
                icon.addProperty("name", prefix.materialIconType.name);
                row.add("material_icon_type", icon);
            }
            JsonArray secondary = new JsonArray();
            for (MaterialStack material : prefix.secondaryMaterials) {
                if (material == null || material.material == null) {
                    throw new IllegalStateException("prefix secondary material is invalid");
                }
                JsonObject value = new JsonObject();
                value.addProperty("material", material.material.getRegistryName());
                value.addProperty("amount", material.amount);
                secondary.add(value);
            }
            row.add("secondary_materials", secondary);

            JsonArray eligibility = new JsonArray();
            for (Material material : materials) {
                JsonObject decision = new JsonObject();
                decision.addProperty("material", material.getRegistryName());
                decision.addProperty("do_generate_item", prefix.doGenerateItem(material));
                decision.addProperty("ignored", prefix.isIgnored(material));
                decision.addProperty("amount_modified", prefix.isAmountModified(material));
                decision.addProperty("effective_material_amount", prefix.getMaterialAmount(material));
                eligibility.add(decision);
            }
            row.add("material_eligibility", eligibility);
            records.add(row);
        }
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
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
}
