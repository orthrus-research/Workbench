package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fluids.FluidStack;

import techguns.Techguns;
import techguns.TGuns;
import techguns.TGArmors;
import techguns.TGConfig;
import techguns.TGOreClusters;
import techguns.blocks.machines.multiblocks.MultiBlockMachineSchematic;
import techguns.blocks.machines.multiblocks.MultiBlockRegister;
import techguns.entities.spawn.TGNpcSpawn;
import techguns.entities.spawn.TGNpcSpawnTable;
import techguns.entities.spawn.TGSpawnManager;
import techguns.items.armors.GenericArmor;
import techguns.items.armors.GenericShield;
import techguns.items.armors.TGArmorBonus;
import techguns.items.armors.TGArmorMaterial;
import techguns.items.guns.GenericGun;
import techguns.items.guns.ammo.AmmoType;
import techguns.items.guns.ammo.AmmoTypes;
import techguns.items.guns.ammo.AmmoVariant;
import techguns.tileentities.operation.AmmoPressBuildPlans;
import techguns.tileentities.operation.BlastFurnaceRecipes;
import techguns.tileentities.operation.CamoBenchRecipes;
import techguns.tileentities.operation.ChargingStationRecipe;
import techguns.tileentities.operation.ChemLabRecipes;
import techguns.tileentities.operation.FabricatorRecipe;
import techguns.tileentities.operation.GrinderRecipes;
import techguns.tileentities.operation.IMachineRecipe;
import techguns.tileentities.operation.MetalPressRecipes;
import techguns.tileentities.operation.ReactionChamberRecipe;
import techguns.tileentities.operation.UpgradeBenchRecipes;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Final TechGuns definition registries. Combat, matching, spawning, machine,
 * random-output, inventory, multiblock, world, and client behavior stay inert.
 */
public final class TechGunsDefinitionAdapter implements CaptureAdapter {
    private static final List<String> GUN_FIELD_PREFIXES = Arrays.asList(
        "techguns.items.guns."
    );
    private static final List<String> ARMOR_FIELD_PREFIXES = Arrays.asList(
        "techguns.items.armors."
    );
    private static final List<String> RECIPE_FIELD_PREFIXES = Arrays.asList(
        "techguns.tileentities.operation.", "techguns.util."
    );

    @Override public String adapterId() { return "techguns-domain-definitions"; }
    @Override public String categoryId() { return "techguns-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Counts counts = new Counts();

        captureGuns(records, encoder, counts);
        captureAmmo(records, encoder, counts);
        captureArmor(records, encoder, counts);
        captureMachineRecipes(records, encoder, counts);
        captureOreClusters(records, encoder, counts);
        captureSpawnTables(records, encoder, counts);
        captureMultiblocks(records, encoder, counts);
        captureConfiguration(records, encoder, counts);

        requireExactCounts(counts);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "techguns-definition-authority");
        authority.addProperty("gun_count", counts.gunCount);
        authority.addProperty("registered_gun_count", counts.registeredGunCount);
        authority.addProperty("unregistered_gun_count", counts.unregisteredGunCount);
        authority.addProperty("ammo_type_count", counts.ammoTypeCount);
        authority.addProperty("ammo_variant_count", counts.ammoVariantCount);
        authority.addProperty("armor_material_count", counts.armorMaterialCount);
        authority.addProperty("armor_count", counts.armorCount);
        authority.addProperty("shield_count", counts.shieldCount);
        authority.addProperty("machine_recipe_family_count", counts.machineRecipeFamilyCount);
        authority.addProperty("machine_recipe_count", counts.machineRecipeCount);
        authority.addProperty("ore_cluster_count", counts.oreClusterCount);
        authority.addProperty("ore_cluster_entry_count", counts.oreClusterEntryCount);
        authority.addProperty("item_ore_cluster_entry_count", counts.itemOreClusterEntryCount);
        authority.addProperty("fluid_ore_cluster_entry_count", counts.fluidOreClusterEntryCount);
        authority.addProperty("oredict_ore_cluster_entry_count", counts.oredictOreClusterEntryCount);
        authority.addProperty("spawn_table_count", counts.spawnTableCount);
        authority.addProperty("spawn_occurrence_count", counts.spawnOccurrenceCount);
        authority.addProperty("multiblock_count", counts.multiblockCount);
        authority.addProperty("configuration_value_count", counts.configurationValueCount);
        authority.addProperty("contextual_matching_invoked", false);
        authority.addProperty("combat_invoked", false);
        authority.addProperty("spawning_invoked", false);
        authority.addProperty("random_output_invoked", false);
        authority.addProperty("machine_operation_invoked", false);
        authority.addProperty("multiblock_formation_invoked", false);
        authority.addProperty("world_or_inventory_mutated", false);
        authority.addProperty("client_behavior_invoked", false);
        records.add(authority);

        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static void captureGuns(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        List<GenericGun> guns = new ArrayList<GenericGun>(GenericGun.guns);
        IdentityHashMap<GenericGun, List<String>> staticAliases = staticAliases(
            TGuns.class, GenericGun.class
        );
        Set<String> identities = new LinkedHashSet<String>();
        for (int ordinal = 0; ordinal < guns.size(); ordinal++) {
            GenericGun gun = guns.get(ordinal);
            if (gun == null) throw new IllegalStateException("TechGuns gun registry contains null");
            ResourceLocation name = Item.REGISTRY.getNameForObject(gun);
            List<String> aliases = staticAliases.get(gun);
            if (aliases == null) aliases = Collections.emptyList();
            if (name != null && !"techguns".equals(name.getNamespace())) {
                throw new IllegalStateException("invalid TechGuns gun registry owner " + name);
            }
            String identity = name == null
                ? unregisteredIdentity("gun", ordinal, aliases)
                : name.toString();
            if (!identities.add(identity)) {
                throw new IllegalStateException("invalid or duplicate TechGuns gun identity " + name);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-gun-definition");
            row.addProperty("gun_definition_id", identity);
            if (name == null) row.add("item_registry_name", JsonNull.INSTANCE);
            else row.addProperty("item_registry_name", name.toString());
            row.addProperty("registered_item", name != null);
            if (name == null) counts.unregisteredGunCount++;
            else counts.registeredGunCount++;
            row.addProperty("registry_ordinal", ordinal);
            JsonArray aliasRows = new JsonArray();
            for (String alias : aliases) aliasRows.add(alias);
            row.add("static_field_aliases", aliasRows);
            row.addProperty("runtime_class", gun.getClass().getName());
            if (name == null) row.add("item_stack", JsonNull.INSTANCE);
            else row.add("item_stack", encoder.encode(new ItemStack(gun)));
            row.add("definition_fields", fields(gun, GUN_FIELD_PREFIXES, encoder));
            row.addProperty("ammo_type_runtime_class", className(gun.getAmmoType()));
            row.addProperty("clip_size", gun.getClipsize());
            row.addProperty("ammo_count", gun.getAmmoCount());
            row.addProperty("semi_automatic", gun.isSemiAuto());
            row.add("spread", encoder.encode(Float.valueOf(gun.getSpread())));
            row.addProperty("hand_type", gun.getHandType().name());
            row.addProperty("combat_or_projectile_behavior_invoked", false);
            records.add(row);
        }
        counts.gunCount = guns.size();
    }

    private static <T> IdentityHashMap<T, List<String>> staticAliases(
        Class<?> owner,
        Class<T> valueType
    ) {
        List<Field> fields = new ArrayList<Field>();
        for (Field field : owner.getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers())
                && valueType.isAssignableFrom(field.getType())
                && !field.isSynthetic()) {
                field.setAccessible(true);
                fields.add(field);
            }
        }
        Collections.sort(fields, fieldComparator());
        IdentityHashMap<T, List<String>> result = new IdentityHashMap<T, List<String>>();
        for (Field field : fields) {
            Object raw = ReflectionAccess.read(field, null);
            if (raw == null) continue;
            T value = valueType.cast(raw);
            List<String> aliases = result.get(value);
            if (aliases == null) {
                aliases = new ArrayList<String>();
                result.put(value, aliases);
            }
            aliases.add(field.getName());
        }
        return result;
    }

    private static String unregisteredIdentity(
        String family,
        int ordinal,
        List<String> aliases
    ) {
        if (aliases.isEmpty()) {
            return "techguns:unregistered-" + family + "/ordinal-" + ordinal;
        }
        return "techguns:unregistered-" + family + '/' + aliases.get(0);
    }

    private static void captureAmmo(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        List<Field> fields = new ArrayList<Field>();
        for (Field field : AmmoTypes.class.getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers()) && field.getType() == AmmoType.class
                && !field.isSynthetic()) {
                field.setAccessible(true);
                fields.add(field);
            }
        }
        Collections.sort(fields, fieldComparator());
        IdentityHashMap<AmmoType, List<String>> aliases = new IdentityHashMap<AmmoType, List<String>>();
        for (Field field : fields) {
            AmmoType ammo = (AmmoType) ReflectionAccess.read(field, null);
            if (ammo == null) throw new IllegalStateException("null TechGuns ammo type " + field.getName());
            List<String> names = aliases.get(ammo);
            if (names == null) {
                names = new ArrayList<String>();
                aliases.put(ammo, names);
            }
            names.add(field.getName());
        }
        for (Field field : fields) {
            AmmoType ammo = (AmmoType) ReflectionAccess.read(field, null);
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-ammo-type-definition");
            row.addProperty("ammo_type_id", field.getName());
            row.addProperty("runtime_class", ammo.getClass().getName());
            row.addProperty("bullets_per_magazine", ammo.getBulletsPerMag());
            row.add("empty_magazines", encoder.encode(ammo.getEmptyMag()));
            JsonArray aliasRows = new JsonArray();
            for (String alias : aliases.get(ammo)) aliasRows.add(alias);
            row.add("static_field_aliases", aliasRows);
            records.add(row);

            List<AmmoVariant> variants = ammo.getVariants();
            if (variants == null) {
                throw new IllegalStateException("null TechGuns ammo variants " + field.getName());
            }
            for (int ordinal = 0; ordinal < variants.size(); ordinal++) {
                AmmoVariant variant = variants.get(ordinal);
                if (variant == null) throw new IllegalStateException("null TechGuns ammo variant");
                JsonObject variantRow = new JsonObject();
                variantRow.addProperty("record_type", "techguns-ammo-variant");
                variantRow.addProperty("ammo_type_id", field.getName());
                variantRow.addProperty("ordinal", ordinal);
                variantRow.addProperty("variant_key", requiredText(variant.getKey(), "ammo variant key"));
                variantRow.addProperty("runtime_class", variant.getClass().getName());
                variantRow.add("definition_fields", fields(
                    variant,
                    Arrays.asList("techguns.items.guns.ammo."),
                    encoder
                ));
                variantRow.addProperty("projectile_or_reload_behavior_invoked", false);
                records.add(variantRow);
                counts.ammoVariantCount++;
            }
        }
        counts.ammoTypeCount = fields.size();
    }

    private static void captureArmor(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        Set<String> materialNames = new LinkedHashSet<String>();
        for (TGArmorMaterial material : TGArmorMaterial.MATERIALS) {
            if (material == null || material.name == null || material.name.isEmpty()
                || !materialNames.add(material.name)) {
                throw new IllegalStateException("invalid or duplicate TechGuns armor material");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-armor-material-definition");
            row.addProperty("armor_material_id", material.name);
            row.addProperty("runtime_class", material.getClass().getName());
            row.add("definition_fields", fields(material, ARMOR_FIELD_PREFIXES, encoder));
            records.add(row);
        }
        counts.armorMaterialCount = TGArmorMaterial.MATERIALS.size();

        Set<String> armorNames = new LinkedHashSet<String>();
        for (GenericArmor armor : TGArmors.armors) {
            if (armor == null) throw new IllegalStateException("TechGuns armor registry contains null");
            ResourceLocation name = Item.REGISTRY.getNameForObject(armor);
            if (name == null || !"techguns".equals(name.getNamespace())
                || !armorNames.add(name.toString())) {
                throw new IllegalStateException("invalid or duplicate TechGuns armor identity " + name);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-armor-definition");
            row.addProperty("item_registry_name", name.toString());
            row.addProperty("runtime_class", armor.getClass().getName());
            row.addProperty("equipment_slot", armor.armorType.name());
            row.add("item_stack", encoder.encode(new ItemStack(armor)));
            row.add("definition_fields", fields(armor, ARMOR_FIELD_PREFIXES, encoder));
            JsonObject bonuses = new JsonObject();
            for (TGArmorBonus bonus : TGArmorBonus.values()) {
                bonuses.add(bonus.name(), encoder.encode(Float.valueOf(armor.getBonus(bonus))));
            }
            row.add("bonuses", bonuses);
            row.addProperty("wearer_or_damage_behavior_invoked", false);
            records.add(row);
        }
        counts.armorCount = TGArmors.armors.size();

        Set<String> shieldNames = new LinkedHashSet<String>();
        for (GenericShield shield : TGArmors.shields) {
            if (shield == null) throw new IllegalStateException("TechGuns shield registry contains null");
            ResourceLocation name = Item.REGISTRY.getNameForObject(shield);
            if (name == null || !"techguns".equals(name.getNamespace())
                || !shieldNames.add(name.toString())) {
                throw new IllegalStateException("invalid or duplicate TechGuns shield identity " + name);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-shield-definition");
            row.addProperty("item_registry_name", name.toString());
            row.addProperty("runtime_class", shield.getClass().getName());
            row.add("item_stack", encoder.encode(new ItemStack(shield)));
            row.add("definition_fields", fields(shield, ARMOR_FIELD_PREFIXES, encoder));
            row.addProperty("blocking_or_damage_behavior_invoked", false);
            records.add(row);
        }
        counts.shieldCount = TGArmors.shields.size();
    }

    private static void captureMachineRecipes(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        Map<String, Collection<? extends IMachineRecipe>> families =
            new LinkedHashMap<String, Collection<? extends IMachineRecipe>>();
        List<IMachineRecipe> ammoPress = new ArrayList<IMachineRecipe>();
        for (int plan = 0; plan < 4; plan++) {
            ammoPress.add(AmmoPressBuildPlans.getRecipeForType(plan));
        }
        families.put("ammo-press", ammoPress);
        families.put("blast-furnace", BlastFurnaceRecipes.getRecipes());
        families.put("camo-bench", CamoBenchRecipes.getRecipes());
        families.put("charging-station", ChargingStationRecipe.getRecipes());
        families.put("chemical-lab", ChemLabRecipes.getRecipes());
        families.put("fabricator", FabricatorRecipe.getRecipes());
        families.put("grinder", GrinderRecipes.recipes);
        families.put("metal-press", MetalPressRecipes.getRecipes());
        families.put("reaction-chamber", ReactionChamberRecipe.getRecipes().values());
        families.put("upgrade-bench", UpgradeBenchRecipes.recipes);

        JsonObject familyCounts = new JsonObject();
        for (Map.Entry<String, Collection<? extends IMachineRecipe>> family : families.entrySet()) {
            if (family.getValue() == null) {
                throw new IllegalStateException("null TechGuns machine recipe family " + family.getKey());
            }
            List<JsonObject> rows = new ArrayList<JsonObject>();
            for (IMachineRecipe recipe : family.getValue()) {
                if (recipe == null) {
                    throw new IllegalStateException("null TechGuns machine recipe " + family.getKey());
                }
                JsonObject definition = new JsonObject();
                definition.add("definition_fields", fields(recipe, RECIPE_FIELD_PREFIXES, encoder));
                definition.add("item_inputs", encoder.encode(recipe.getItemInputs()));
                definition.add("item_outputs", encoder.encode(recipe.getItemOutputs()));
                definition.add("fluid_inputs", encoder.encode(recipe.getFluidInputs()));
                definition.add("fluid_outputs", encoder.encode(recipe.getFluidOutputs()));
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "techguns-machine-recipe");
                row.addProperty("recipe_family", family.getKey());
                row.addProperty("runtime_class", recipe.getClass().getName());
                row.add("definition", definition);
                rows.add(row);
            }
            addSemanticOccurrences(records, rows);
            familyCounts.addProperty(family.getKey(), rows.size());
            counts.machineRecipeCount += rows.size();
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "techguns-machine-recipe-authority");
        authority.add("family_counts", familyCounts);
        authority.addProperty("family_count", families.size());
        authority.addProperty("recipe_count", counts.machineRecipeCount);
        authority.addProperty("finite_projection_getters_invoked", true);
        authority.addProperty("contextual_matching_invoked", false);
        authority.addProperty("random_output_invoked", false);
        authority.addProperty("machine_operation_invoked", false);
        records.add(authority);
        counts.machineRecipeFamilyCount = families.size();
    }

    private static void captureOreClusters(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        TGOreClusters owner = Techguns.orecluster;
        if (owner == null) throw new IllegalStateException("TechGuns ore cluster registry is null");
        Object raw = field(owner, "registry");
        if (!(raw instanceof Map<?, ?>)) {
            throw new IllegalStateException("TechGuns ore cluster registry is not a map");
        }
        Map<?, ?> clusters = (Map<?, ?>) raw;
        Set<String> identities = new LinkedHashSet<String>();
        for (Map.Entry<?, ?> entry : clusters.entrySet()) {
            if (!(entry.getKey() instanceof Enum<?>)
                || !(entry.getValue() instanceof TGOreClusters.OreCluster)) {
                throw new IllegalStateException("invalid TechGuns ore cluster entry");
            }
            String clusterId = ((Enum<?>) entry.getKey()).name();
            if (!identities.add(clusterId)) {
                throw new IllegalStateException("duplicate TechGuns ore cluster " + clusterId);
            }
            TGOreClusters.OreCluster cluster = (TGOreClusters.OreCluster) entry.getValue();
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-ore-cluster-definition");
            row.addProperty("ore_cluster_id", clusterId);
            row.addProperty("runtime_class", cluster.getClass().getName());
            row.addProperty("mining_level", cluster.getMininglevel());
            row.add("amount_multiplier", encoder.encode(Double.valueOf(cluster.getMultiplier_amount())));
            row.add("power_multiplier", encoder.encode(Double.valueOf(cluster.getMultiplier_power())));
            row.addProperty("weighted_entry_count", cluster.getOreEntries().size());
            row.addProperty("random_operation_invoked", false);
            records.add(row);

            List<TGOreClusters.OreClusterWeightedEntry> values = cluster.getOreEntries();
            if (values == null) throw new IllegalStateException("null TechGuns ore cluster entries");
            for (int ordinal = 0; ordinal < values.size(); ordinal++) {
                TGOreClusters.OreClusterWeightedEntry value = values.get(ordinal);
                if (value == null || value.isEmpty()) {
                    throw new IllegalStateException("empty TechGuns ore cluster weighted entry");
                }
                ItemStack item = value.getOre();
                FluidStack fluid = value.getFluid();
                Object oreNameRaw = field(value, "oredictname");
                String oreName = oreNameRaw == null ? null : (String) oreNameRaw;
                int kinds = (item != null && !item.isEmpty() ? 1 : 0)
                    + (fluid != null ? 1 : 0) + (oreName != null ? 1 : 0);
                if (kinds != 1) {
                    throw new IllegalStateException("TechGuns ore cluster entry kind is ambiguous");
                }
                JsonObject occurrence = new JsonObject();
                occurrence.addProperty("record_type", "techguns-ore-cluster-entry");
                occurrence.addProperty("ore_cluster_id", clusterId);
                occurrence.addProperty("ordinal", ordinal);
                occurrence.addProperty("weight", value.itemWeight);
                occurrence.add("item_stack", encoder.encode(item));
                occurrence.add("fluid_stack", encoder.encode(fluid));
                if (oreName == null) occurrence.add("ore_dictionary_name", JsonNull.INSTANCE);
                else occurrence.addProperty("ore_dictionary_name", oreName);
                occurrence.addProperty(
                    "entry_kind",
                    fluid != null ? "fluid" : oreName != null ? "ore-dictionary" : "item"
                );
                occurrence.addProperty("random_selection_invoked", false);
                records.add(occurrence);
                counts.oreClusterEntryCount++;
                if (fluid != null) counts.fluidOreClusterEntryCount++;
                else if (oreName != null) counts.oredictOreClusterEntryCount++;
                else counts.itemOreClusterEntryCount++;
            }
        }
        counts.oreClusterCount = clusters.size();
    }

    private static void captureSpawnTables(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        captureSpawnTable(
            "overworld", TGSpawnManager.spawnTableOverworld, records, encoder, counts
        );
        captureSpawnTable(
            "nether", TGSpawnManager.spawnTableNether, records, encoder, counts
        );
    }

    @SuppressWarnings("unchecked")
    private static void captureSpawnTable(
        String tableId,
        TGNpcSpawnTable table,
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        if (table == null) throw new IllegalStateException("null TechGuns spawn table " + tableId);
        Object maxDangerRaw = field(table, "maxDanger");
        Object spawnListRaw = field(table, "spawnlist");
        if (!(maxDangerRaw instanceof Integer) || !(spawnListRaw instanceof List<?>)) {
            throw new IllegalStateException("invalid TechGuns spawn table fields " + tableId);
        }
        int maxDanger = ((Integer) maxDangerRaw).intValue();
        List<?> levels = (List<?>) spawnListRaw;
        if (maxDanger < 0 || maxDanger + 1 != levels.size()) {
            throw new IllegalStateException("TechGuns spawn table danger universe drifted");
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "techguns-spawn-table-definition");
        authority.addProperty("spawn_table_id", tableId);
        authority.addProperty("maximum_danger_level", maxDanger);
        authority.addProperty("danger_level_count", levels.size());
        authority.addProperty("runtime_class", table.getClass().getName());
        authority.addProperty("world_or_biome_matching_invoked", false);
        authority.addProperty("spawning_invoked", false);
        records.add(authority);
        counts.spawnTableCount++;

        for (int danger = 0; danger < levels.size(); danger++) {
            Object levelRaw = levels.get(danger);
            if (!(levelRaw instanceof List<?>)) {
                throw new IllegalStateException("invalid TechGuns spawn danger list");
            }
            List<?> level = (List<?>) levelRaw;
            for (int ordinal = 0; ordinal < level.size(); ordinal++) {
                Object valueRaw = level.get(ordinal);
                if (!(valueRaw instanceof TGNpcSpawn)) {
                    throw new IllegalStateException("invalid TechGuns NPC spawn definition");
                }
                TGNpcSpawn value = (TGNpcSpawn) valueRaw;
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "techguns-npc-spawn-occurrence");
                row.addProperty("spawn_table_id", tableId);
                row.addProperty("danger_level", danger);
                row.addProperty("ordinal", ordinal);
                row.addProperty("runtime_class", value.getClass().getName());
                row.add("definition_fields", fields(
                    value, Arrays.asList("techguns.entities.spawn."), encoder
                ));
                row.addProperty("world_or_biome_matching_invoked", false);
                row.addProperty("spawning_invoked", false);
                records.add(row);
                counts.spawnOccurrenceCount++;
            }
        }
    }

    private static void captureMultiblocks(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        if (MultiBlockRegister.REGISTER == null) {
            throw new IllegalStateException("TechGuns multiblock registry is null");
        }
        Set<String> masters = new LinkedHashSet<String>();
        for (Map.Entry<Class<? extends net.minecraft.tileentity.TileEntity>,
                MultiBlockMachineSchematic> entry : MultiBlockRegister.REGISTER.entrySet()) {
            if (entry.getKey() == null || entry.getValue() == null
                || !masters.add(entry.getKey().getName())) {
                throw new IllegalStateException("invalid TechGuns multiblock definition");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-multiblock-definition");
            row.addProperty("master_tile_class", entry.getKey().getName());
            row.addProperty("schematic_class", entry.getValue().getClass().getName());
            row.add("definition_fields", fields(
                entry.getValue(),
                Arrays.asList("techguns.blocks.machines.multiblocks."),
                encoder
            ));
            row.add("method_surface", methodSurface(entry.getValue().getClass()));
            row.addProperty("formation_or_world_check_invoked", false);
            records.add(row);
        }
        counts.multiblockCount = MultiBlockRegister.REGISTER.size();
    }

    private static void captureConfiguration(
        List<JsonObject> records,
        StableValueEncoder encoder,
        Counts counts
    ) {
        List<Field> values = new ArrayList<Field>();
        for (Field field : TGConfig.class.getDeclaredFields()) {
            int modifiers = field.getModifiers();
            if (!Modifier.isPublic(modifiers) || !Modifier.isStatic(modifiers)
                || field.isSynthetic() || !configurationType(field.getType())) continue;
            field.setAccessible(true);
            values.add(field);
        }
        Collections.sort(values, fieldComparator());
        for (Field field : values) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "techguns-configuration-value");
            row.addProperty("configuration_key", field.getName());
            row.addProperty("declared_type", field.getType().getName());
            row.add("value", encoder.encode(ReflectionAccess.read(field, null)));
            records.add(row);
        }
        counts.configurationValueCount = values.size();
    }

    private static boolean configurationType(Class<?> type) {
        if (type.isPrimitive() || type == String.class || type.isEnum()) return true;
        Class<?> component = type.isArray() ? type.getComponentType() : null;
        return component != null
            && (component.isPrimitive() || component == String.class || component.isEnum());
    }

    private static JsonObject fields(
        Object value,
        List<String> allowedPrefixes,
        StableValueEncoder encoder
    ) {
        JsonObject result = new JsonObject();
        for (Field field : ReflectionAccess.instanceFields(value.getClass())) {
            String owner = field.getDeclaringClass().getName();
            if (!startsWithAny(owner, allowedPrefixes)) continue;
            String key = owner + '#' + field.getName();
            result.add(key, encoder.encode(ReflectionAccess.read(field, value)));
        }
        return result;
    }

    private static boolean startsWithAny(String value, List<String> prefixes) {
        for (String prefix : prefixes) if (value.startsWith(prefix)) return true;
        return false;
    }

    private static Object field(Object owner, String name) {
        for (Class<?> type = owner.getClass(); type != null && type != Object.class;
             type = type.getSuperclass()) {
            try {
                Field field = type.getDeclaredField(name);
                if (Modifier.isStatic(field.getModifiers())) {
                    throw new IllegalStateException("expected TechGuns instance field " + name);
                }
                field.setAccessible(true);
                return ReflectionAccess.read(field, owner);
            } catch (NoSuchFieldException ignored) {
                // Search the exact runtime hierarchy.
            }
        }
        throw new IllegalStateException(
            "required TechGuns field is unavailable: " + owner.getClass().getName() + '.' + name
        );
    }

    private static JsonArray methodSurface(Class<?> type) {
        List<String> signatures = new ArrayList<String>();
        for (Method method : type.getMethods()) {
            if (method.isSynthetic() || method.isBridge() || Modifier.isStatic(method.getModifiers())) {
                continue;
            }
            if (!method.getDeclaringClass().getName().startsWith(
                "techguns.blocks.machines.multiblocks."
            )) continue;
            StringBuilder signature = new StringBuilder();
            signature.append(method.getName()).append('(');
            Class<?>[] parameters = method.getParameterTypes();
            for (int index = 0; index < parameters.length; index++) {
                if (index != 0) signature.append(',');
                signature.append(parameters[index].getName());
            }
            signature.append("):").append(method.getReturnType().getName());
            signatures.add(signature.toString());
        }
        Collections.sort(signatures);
        JsonArray result = new JsonArray();
        for (String signature : signatures) result.add(signature);
        return result;
    }

    private static void addSemanticOccurrences(
        List<JsonObject> records,
        List<JsonObject> definitions
    ) {
        Collections.sort(definitions, new Comparator<JsonObject>() {
            @Override
            public int compare(JsonObject left, JsonObject right) {
                return CanonicalJson.compareUnsigned(
                    CanonicalJson.bytes(left), CanonicalJson.bytes(right)
                );
            }
        });
        Map<String, Integer> duplicateOrdinals = new HashMap<String, Integer>();
        for (JsonObject row : definitions) {
            String digest = CanonicalJson.sha256(row.get("definition"));
            Integer ordinal = duplicateOrdinals.get(digest);
            int value = ordinal == null ? 0 : ordinal.intValue();
            duplicateOrdinals.put(digest, Integer.valueOf(value + 1));
            row.addProperty("semantic_sha256", digest);
            row.addProperty("duplicate_ordinal", value);
            row.addProperty("contextual_matching_invoked", false);
            row.addProperty("machine_operation_invoked", false);
            records.add(row);
        }
    }

    private static Comparator<Field> fieldComparator() {
        return new Comparator<Field>() {
            @Override
            public int compare(Field left, Field right) {
                return left.getName().compareTo(right.getName());
            }
        };
    }

    private static String className(Object value) {
        return value == null ? "" : value.getClass().getName();
    }

    private static String requiredText(String value, String label) {
        if (value == null || value.isEmpty()) throw new IllegalStateException(label + " is empty");
        return value;
    }

    private static void requireExactCounts(Counts counts) {
        if (
            counts.gunCount != 42
            || counts.registeredGunCount != 41
            || counts.unregisteredGunCount != 1
            || counts.ammoTypeCount != 22
            || counts.ammoVariantCount != 35
            || counts.armorMaterialCount != 15
            || counts.armorCount != 57
            || counts.shieldCount != 3
            || counts.machineRecipeFamilyCount != 10
            || counts.machineRecipeCount != 204
            || counts.oreClusterCount != 9
            || counts.oreClusterEntryCount != 20
            || counts.itemOreClusterEntryCount != 19
            || counts.fluidOreClusterEntryCount != 1
            || counts.oredictOreClusterEntryCount != 0
            || counts.spawnTableCount != 2
            || counts.spawnOccurrenceCount != 0
            || counts.multiblockCount != 3
            || counts.configurationValueCount != 86
        ) {
            throw new IllegalStateException(
                "TechGuns finite definition universe differs from exact 2.0.2.0_pre3.2 profile"
            );
        }
    }

    private static final class Counts {
        private int gunCount;
        private int registeredGunCount;
        private int unregisteredGunCount;
        private int ammoTypeCount;
        private int ammoVariantCount;
        private int armorMaterialCount;
        private int armorCount;
        private int shieldCount;
        private int machineRecipeFamilyCount;
        private int machineRecipeCount;
        private int oreClusterCount;
        private int oreClusterEntryCount;
        private int itemOreClusterEntryCount;
        private int fluidOreClusterEntryCount;
        private int oredictOreClusterEntryCount;
        private int spawnTableCount;
        private int spawnOccurrenceCount;
        private int multiblockCount;
        private int configurationValueCount;
    }
}
