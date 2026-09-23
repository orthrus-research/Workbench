package dev.workbench.crucible.runtimegraph.adapter;

import appeng.api.AEApi;
import appeng.api.IAppEngApi;
import appeng.api.config.TunnelType;
import appeng.api.definitions.IBlockDefinition;
import appeng.api.definitions.IBlocks;
import appeng.api.definitions.IDefinitions;
import appeng.api.definitions.IItemDefinition;
import appeng.api.definitions.IItems;
import appeng.api.definitions.IMaterials;
import appeng.api.definitions.IParts;
import appeng.api.definitions.ITileDefinition;
import appeng.api.exceptions.MissingDefinitionException;
import appeng.api.features.IGrinderRecipe;
import appeng.api.features.IInscriberRecipe;
import appeng.api.features.IRegistryContainer;
import appeng.api.features.IWorldGen;
import appeng.api.implementations.items.IStorageCell;
import appeng.api.storage.IStorageChannel;
import appeng.api.util.AEColor;
import appeng.api.util.AEColoredItemDefinition;

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

import net.minecraft.block.Block;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraftforge.common.capabilities.Capability;

import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Static AE2 definition and registry authority. Network, inventory, crafting-job,
 * player, locatable, GUI, and world execution are deliberately not invoked.
 */
public final class AppliedEnergisticsDefinitionAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "ae2-domain-definitions"; }
    @Override public String categoryId() { return "applied-energistics-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        IAppEngApi api = AEApi.instance();
        if (api == null) throw new IllegalStateException("AE2 API is unavailable");

        DefinitionCounts definitions = captureDefinitions(api.definitions(), records, encoder);
        RegistryCounts registries = captureRegistries(api, records, encoder);
        requireExactCounts(definitions, registries);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "ae2-definition-authority");
        authority.addProperty("definition_method_count", definitions.methodCount);
        authority.addProperty("definition_count", definitions.definitionCount);
        authority.addProperty(
            "unimplemented_definition_count", definitions.unimplementedDefinitionCount
        );
        authority.addProperty("colored_definition_count", definitions.coloredDefinitionCount);
        authority.addProperty("colored_variant_count", definitions.coloredVariantCount);
        authority.addProperty("storage_cell_definition_count", definitions.storageCellCount);
        authority.addProperty("grinder_recipe_count", registries.grinderRecipeCount);
        authority.addProperty("inscriber_recipe_count", registries.inscriberRecipeCount);
        authority.addProperty("storage_channel_count", registries.storageChannelCount);
        authority.addProperty("cell_handler_count", registries.cellHandlerCount);
        authority.addProperty("cell_gui_handler_count", registries.cellGuiHandlerCount);
        authority.addProperty("charger_rate_count", registries.chargerRateCount);
        authority.addProperty("p2p_attunement_count", registries.p2pAttunementCount);
        authority.addProperty("matter_cannon_ammo_count", registries.matterCannonAmmoCount);
        authority.addProperty("grid_cache_count", registries.gridCacheCount);
        authority.addProperty("wireless_handler_count", registries.wirelessHandlerCount);
        authority.addProperty(
            "special_comparison_provider_count", registries.specialComparisonProviderCount
        );
        authority.addProperty("movable_rule_count", registries.movableRuleCount);
        authority.addProperty("recipe_handler_count", registries.recipeHandlerCount);
        authority.addProperty("subitem_resolver_count", registries.subitemResolverCount);
        authority.addProperty("worldgen_rule_count", registries.worldgenRuleCount);
        authority.addProperty("definition_methods_enumerated_from_public_api", true);
        authority.addProperty("contextual_lookup_invoked", false);
        authority.addProperty("network_created", false);
        authority.addProperty("cell_inventory_opened", false);
        authority.addProperty("crafting_job_created", false);
        authority.addProperty("player_or_locatable_state_captured", false);
        authority.addProperty("world_generation_invoked", false);
        records.add(authority);

        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static DefinitionCounts captureDefinitions(
        IDefinitions definitions,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        if (definitions == null) throw new IllegalStateException("AE2 definitions are unavailable");
        DefinitionCounts counts = new DefinitionCounts();
        captureDefinitionGroup(
            "blocks", IBlocks.class, definitions.blocks(), records, encoder, counts
        );
        captureDefinitionGroup(
            "items", IItems.class, definitions.items(), records, encoder, counts
        );
        captureDefinitionGroup(
            "materials", IMaterials.class, definitions.materials(), records, encoder, counts
        );
        captureDefinitionGroup(
            "parts", IParts.class, definitions.parts(), records, encoder, counts
        );
        return counts;
    }

    private static void captureDefinitionGroup(
        String group,
        Class<?> contract,
        Object owner,
        List<JsonObject> records,
        StableValueEncoder encoder,
        DefinitionCounts counts
    ) {
        if (owner == null || !contract.isInstance(owner)) {
            throw new IllegalStateException("invalid AE2 definition group " + group);
        }
        List<Method> methods = new ArrayList<Method>();
        for (Method method : contract.getDeclaredMethods()) {
            if (Modifier.isStatic(method.getModifiers()) || method.isSynthetic()
                || method.isBridge() || method.getParameterTypes().length != 0) {
                throw new IllegalStateException(
                    "unsupported AE2 definition method surface " + contract.getName()
                        + '#' + method.getName()
                );
            }
            methods.add(method);
        }
        Collections.sort(methods, new Comparator<Method>() {
            @Override
            public int compare(Method left, Method right) {
                return left.getName().compareTo(right.getName());
            }
        });

        Set<String> methodNames = new LinkedHashSet<String>();
        for (Method method : methods) {
            if (!methodNames.add(method.getName())) {
                throw new IllegalStateException(
                    "overloaded AE2 definition method " + contract.getName() + '#' + method.getName()
                );
            }
            Object value = invoke(method, owner);
            counts.methodCount++;
            if (value instanceof MissingDefinition) {
                MissingDefinition missing = (MissingDefinition) value;
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "ae2-unimplemented-definition");
                row.addProperty("definition_group", group);
                row.addProperty("definition_method", method.getName());
                row.addProperty("definition_id", group + '.' + method.getName());
                row.addProperty("declared_return_type", method.getReturnType().getName());
                row.addProperty("exception_class", missing.exceptionClass);
                row.addProperty("reason", missing.message);
                row.addProperty("implemented", false);
                records.add(row);
                counts.unimplementedDefinitionCount++;
            } else if (value instanceof IItemDefinition) {
                records.add(definition(group, method.getName(), (IItemDefinition) value, encoder));
                counts.definitionCount++;
                if (storageCell(
                    group, method.getName(), (IItemDefinition) value, records, encoder
                )) counts.storageCellCount++;
            } else if (value instanceof AEColoredItemDefinition) {
                captureColored(
                    group, method.getName(), (AEColoredItemDefinition) value,
                    records, encoder, counts
                );
            } else {
                throw new IllegalStateException(
                    "AE2 definition method returned an unsupported type "
                        + contract.getName() + '#' + method.getName() + ": "
                        + (value == null ? "null" : value.getClass().getName())
                );
            }
        }
    }

    private static JsonObject definition(
        String group,
        String methodName,
        IItemDefinition definition,
        StableValueEncoder encoder
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "ae2-definition");
        row.addProperty("definition_group", group);
        row.addProperty("definition_method", methodName);
        row.addProperty("definition_id", group + '.' + methodName);
        row.addProperty("identifier", requiredText(definition.identifier(), "AE2 identifier"));
        row.addProperty("runtime_class", definition.getClass().getName());
        row.addProperty("enabled", definition.isEnabled());
        row.add("item", optional(definition.maybeItem(), encoder));
        row.add("stack", optional(definition.maybeStack(1), encoder));
        if (definition instanceof IBlockDefinition) {
            IBlockDefinition block = (IBlockDefinition) definition;
            row.add("block", optional(block.maybeBlock(), encoder));
            row.add("item_block", optional(block.maybeItemBlock(), encoder));
        } else {
            row.add("block", JsonNull.INSTANCE);
            row.add("item_block", JsonNull.INSTANCE);
        }
        if (definition instanceof ITileDefinition) {
            row.add("tile_entity_class", optionalClass(((ITileDefinition) definition).maybeEntity()));
        } else {
            row.add("tile_entity_class", JsonNull.INSTANCE);
        }
        row.addProperty("contextual_equality_invoked", false);
        return row;
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static boolean storageCell(
        String group,
        String methodName,
        IItemDefinition definition,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        Optional<Item> item = definition.maybeItem();
        Optional<ItemStack> stack = definition.maybeStack(1);
        if (!item.isPresent() || !stack.isPresent() || !(item.get() instanceof IStorageCell)) {
            return false;
        }
        IStorageCell cell = (IStorageCell) item.get();
        ItemStack value = stack.get();
        IStorageChannel channel = cell.getChannel();
        if (channel == null) {
            throw new IllegalStateException("AE2 storage cell lacks a storage channel");
        }
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "ae2-storage-cell-definition");
        row.addProperty("definition_id", group + '.' + methodName);
        row.add("item_stack", encoder.encode(value));
        row.addProperty("item_runtime_class", item.get().getClass().getName());
        row.addProperty("bytes", cell.getBytes(value));
        row.addProperty("bytes_per_type", cell.getBytesPerType(value));
        row.addProperty("total_types", cell.getTotalTypes(value));
        row.addProperty("storable_in_storage_cell", cell.storableInStorageCell());
        row.addProperty("is_storage_cell", cell.isStorageCell(value));
        row.add("idle_drain", encoder.encode(Double.valueOf(cell.getIdleDrain())));
        row.addProperty("storage_channel_class", channel.getClass().getName());
        row.addProperty("inventory_opened", false);
        records.add(row);
        return true;
    }

    private static void captureColored(
        String group,
        String methodName,
        AEColoredItemDefinition definition,
        List<JsonObject> records,
        StableValueEncoder encoder,
        DefinitionCounts counts
    ) {
        counts.coloredDefinitionCount++;
        for (AEColor color : AEColor.values()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-colored-definition-variant");
            row.addProperty("definition_group", group);
            row.addProperty("definition_method", methodName);
            row.addProperty("definition_id", group + '.' + methodName);
            row.addProperty("color", color.name());
            row.addProperty("runtime_class", definition.getClass().getName());
            Item item = definition.item(color);
            Block block = definition.block(color);
            Class<?> entity = definition.entity(color);
            ItemStack stack = definition.stack(color, 1);
            row.add("item", encoder.encode(item));
            row.add("block", encoder.encode(block));
            if (entity == null) row.add("tile_entity_class", JsonNull.INSTANCE);
            else row.addProperty("tile_entity_class", entity.getName());
            row.add("stack", encoder.encode(stack));
            row.addProperty("contextual_equality_invoked", false);
            records.add(row);
            counts.coloredVariantCount++;
        }
    }

    private static RegistryCounts captureRegistries(
        IAppEngApi api,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        IRegistryContainer container = api.registries();
        if (container == null
            || !"appeng.core.features.registries.RegistryContainer".equals(
                container.getClass().getName()
            )) {
            throw new IllegalStateException("AE2 registry container implementation drifted");
        }
        RegistryCounts counts = new RegistryCounts();
        captureGrinder(container, records, encoder, counts);
        captureInscriber(container, records, encoder, counts);
        captureStorageChannels(api, records, encoder, counts);
        captureCellRegistry(container, records, counts);
        captureCharger(container, records, encoder, counts);
        captureP2P(container, records, encoder, counts);
        captureMatterCannon(container, records, encoder, counts);
        captureGridCaches(container, records, counts);
        captureWireless(container, records, counts);
        captureComparisons(container, records, counts);
        captureMovable(container, records, encoder, counts);
        captureRecipeHandlers(container, records, counts);
        captureWorldgen(container, records, encoder, counts);
        return counts;
    }

    private static void captureGrinder(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Collection<IGrinderRecipe> recipes = container.grinder().getRecipes();
        if (recipes == null) throw new IllegalStateException("AE2 grinder recipes are null");
        List<JsonObject> definitions = new ArrayList<JsonObject>();
        for (IGrinderRecipe recipe : recipes) {
            if (recipe == null) throw new IllegalStateException("AE2 grinder recipe is null");
            JsonObject value = new JsonObject();
            value.add("input", encoder.encode(recipe.getInput()));
            value.add("output", encoder.encode(recipe.getOutput()));
            value.add("optional_output", optional(recipe.getOptionalOutput(), encoder));
            value.add("second_optional_output", optional(recipe.getSecondOptionalOutput(), encoder));
            value.add("optional_chance", encoder.encode(Float.valueOf(recipe.getOptionalChance())));
            value.add(
                "second_optional_chance",
                encoder.encode(Float.valueOf(recipe.getSecondOptionalChance()))
            );
            value.addProperty("required_turns", recipe.getRequiredTurns());
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-grinder-recipe");
            row.addProperty("runtime_class", recipe.getClass().getName());
            row.add("definition", value);
            definitions.add(row);
        }
        addSemanticOccurrences(records, definitions);
        counts.grinderRecipeCount = definitions.size();
    }

    private static void captureInscriber(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Collection<IInscriberRecipe> recipes = container.inscriber().getRecipes();
        if (recipes == null) throw new IllegalStateException("AE2 inscriber recipes are null");
        List<JsonObject> definitions = new ArrayList<JsonObject>();
        for (IInscriberRecipe recipe : recipes) {
            if (recipe == null || recipe.getProcessType() == null) {
                throw new IllegalStateException("invalid AE2 inscriber recipe");
            }
            JsonObject value = new JsonObject();
            value.add("inputs", encoder.encode(recipe.getInputs()));
            value.add("output", encoder.encode(recipe.getOutput()));
            value.add("top_inputs", encoder.encode(recipe.getTopInputs()));
            value.add("bottom_inputs", encoder.encode(recipe.getBottomInputs()));
            value.addProperty("process_type", recipe.getProcessType().name());
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-inscriber-recipe");
            row.addProperty("runtime_class", recipe.getClass().getName());
            row.add("definition", value);
            definitions.add(row);
        }
        addSemanticOccurrences(records, definitions);
        counts.inscriberRecipeCount = definitions.size();
    }

    private static void captureStorageChannels(
        IAppEngApi api,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Collection<IStorageChannel<? extends appeng.api.storage.data.IAEStack<?>>> channels =
            api.storage().storageChannels();
        if (channels == null) throw new IllegalStateException("AE2 storage channels are null");
        Set<String> classes = new LinkedHashSet<String>();
        for (IStorageChannel<?> channel : channels) {
            if (channel == null || !classes.add(channel.getClass().getName())) {
                throw new IllegalStateException("invalid or duplicate AE2 storage channel");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-storage-channel");
            row.addProperty("runtime_class", channel.getClass().getName());
            row.addProperty("transfer_factor", channel.transferFactor());
            row.addProperty("units_per_byte", channel.getUnitsPerByte());
            row.addProperty("stack_list_created", false);
            row.addProperty("packet_or_nbt_decoding_invoked", false);
            records.add(row);
        }
        counts.storageChannelCount = channels.size();
    }

    private static void captureCellRegistry(
        IRegistryContainer container,
        List<JsonObject> records,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.cell(), "appeng.core.features.registries.cell.CellRegistry"
        );
        Collection<?> handlers = collectionField(registry, "handlers");
        Collection<?> guiHandlers = collectionField(registry, "guiHandlers");
        counts.cellHandlerCount = addClassOccurrences(records, "ae2-cell-handler", handlers);
        counts.cellGuiHandlerCount = addClassOccurrences(
            records, "ae2-cell-gui-handler", guiHandlers
        );
    }

    private static void captureCharger(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.charger(), "appeng.core.features.registries.charger.ChargerRegistry"
        );
        Map<?, ?> rates = mapField(registry, "chargeRates");
        for (Map.Entry<?, ?> entry : rates.entrySet()) {
            if (!(entry.getKey() instanceof Item) || !(entry.getValue() instanceof Double)) {
                throw new IllegalStateException("invalid AE2 charger rate entry");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-charger-rate");
            row.add("item", encoder.encode(entry.getKey()));
            row.add("charge_rate", encoder.encode(entry.getValue()));
            records.add(row);
        }
        counts.chargerRateCount = rates.size();
    }

    private static void captureP2P(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.p2pTunnel(), "appeng.core.features.registries.P2PTunnelRegistry"
        );
        Map<?, ?> itemTunnels = mapField(registry, "tunnels");
        Map<?, ?> modTunnels = mapField(registry, "modIdTunnels");
        Map<?, ?> capabilityTunnels = mapField(registry, "capTunnels");
        for (Map.Entry<?, ?> entry : itemTunnels.entrySet()) {
            JsonObject row = tunnel("ae2-p2p-item-attunement", entry.getValue());
            row.add("item_stack", encoder.encode(entry.getKey()));
            records.add(row);
        }
        for (Map.Entry<?, ?> entry : modTunnels.entrySet()) {
            if (!(entry.getKey() instanceof String)) {
                throw new IllegalStateException("invalid AE2 P2P mod attunement key");
            }
            JsonObject row = tunnel("ae2-p2p-mod-attunement", entry.getValue());
            row.addProperty("mod_id", (String) entry.getKey());
            records.add(row);
        }
        for (Map.Entry<?, ?> entry : capabilityTunnels.entrySet()) {
            if (!(entry.getKey() instanceof Capability<?>)) {
                throw new IllegalStateException("invalid AE2 P2P capability key");
            }
            JsonObject row = tunnel("ae2-p2p-capability-attunement", entry.getValue());
            row.addProperty("capability_name", ((Capability<?>) entry.getKey()).getName());
            records.add(row);
        }
        counts.p2pAttunementCount =
            itemTunnels.size() + modTunnels.size() + capabilityTunnels.size();
    }

    private static JsonObject tunnel(String recordType, Object value) {
        if (!(value instanceof TunnelType)) {
            throw new IllegalStateException("invalid AE2 P2P tunnel type");
        }
        JsonObject row = new JsonObject();
        row.addProperty("record_type", recordType);
        row.addProperty("tunnel_type", ((TunnelType) value).name());
        row.addProperty("contextual_matching_invoked", false);
        return row;
    }

    private static void captureMatterCannon(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.matterCannon(),
            "appeng.core.features.registries.MatterCannonAmmoRegistry"
        );
        Map<?, ?> values = mapField(registry, "DamageModifiers");
        for (Map.Entry<?, ?> entry : values.entrySet()) {
            if (!(entry.getKey() instanceof ItemStack) || !(entry.getValue() instanceof Double)) {
                throw new IllegalStateException("invalid AE2 matter cannon ammo entry");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-matter-cannon-ammo");
            row.add("item_stack", encoder.encode(entry.getKey()));
            row.add("penetration", encoder.encode(entry.getValue()));
            records.add(row);
        }
        counts.matterCannonAmmoCount = values.size();
    }

    private static void captureGridCaches(
        IRegistryContainer container,
        List<JsonObject> records,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.gridCache(), "appeng.core.features.registries.GridCacheRegistry"
        );
        Map<?, ?> values = mapField(registry, "caches");
        for (Map.Entry<?, ?> entry : values.entrySet()) {
            if (!(entry.getKey() instanceof Class<?>) || !(entry.getValue() instanceof Class<?>)) {
                throw new IllegalStateException("invalid AE2 grid cache binding");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-grid-cache-binding");
            row.addProperty("contract_class", ((Class<?>) entry.getKey()).getName());
            row.addProperty("implementation_class", ((Class<?>) entry.getValue()).getName());
            row.addProperty("cache_instance_created", false);
            records.add(row);
        }
        counts.gridCacheCount = values.size();
    }

    private static void captureWireless(
        IRegistryContainer container,
        List<JsonObject> records,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.wireless(), "appeng.core.features.registries.WirelessRegistry"
        );
        counts.wirelessHandlerCount = addClassOccurrences(
            records, "ae2-wireless-handler", collectionField(registry, "handlers")
        );
    }

    private static void captureComparisons(
        IRegistryContainer container,
        List<JsonObject> records,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.specialComparison(),
            "appeng.core.features.registries.SpecialComparisonRegistry"
        );
        counts.specialComparisonProviderCount = addClassOccurrences(
            records, "ae2-special-comparison-provider",
            collectionField(registry, "CompRegistry")
        );
    }

    private static void captureMovable(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.movable(), "appeng.core.features.registries.MovableTileRegistry"
        );
        Collection<?> blacklisted = collectionField(registry, "blacklisted");
        for (Object value : blacklisted) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-movable-block-blacklist");
            row.add("block", encoder.encode(value));
            records.add(row);
        }
        Collection<?> whitelist = collectionField(registry, "test");
        for (Object value : whitelist) {
            if (!(value instanceof Class<?>)) {
                throw new IllegalStateException("invalid AE2 movable tile whitelist entry");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-movable-tile-whitelist");
            row.addProperty("tile_entity_class", ((Class<?>) value).getName());
            records.add(row);
        }
        Collection<?> handlers = collectionField(registry, "handlers");
        int handlerCount = addClassOccurrences(records, "ae2-movable-handler", handlers);
        counts.movableRuleCount = blacklisted.size() + whitelist.size() + handlerCount;
    }

    private static void captureRecipeHandlers(
        IRegistryContainer container,
        List<JsonObject> records,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.recipes(), "appeng.core.features.registries.RecipeHandlerRegistry"
        );
        Map<?, ?> handlers = mapField(registry, "handlers");
        for (Map.Entry<?, ?> entry : handlers.entrySet()) {
            if (!(entry.getKey() instanceof String) || !(entry.getValue() instanceof Class<?>)) {
                throw new IllegalStateException("invalid AE2 recipe handler entry");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-recipe-handler");
            row.addProperty("handler_key", (String) entry.getKey());
            row.addProperty("handler_class", ((Class<?>) entry.getValue()).getName());
            row.addProperty("handler_instantiated", false);
            records.add(row);
        }
        Collection<?> resolvers = collectionField(registry, "resolvers");
        counts.subitemResolverCount = addClassOccurrences(
            records, "ae2-subitem-resolver", resolvers
        );
        counts.recipeHandlerCount = handlers.size();
    }

    private static void captureWorldgen(
        IRegistryContainer container,
        List<JsonObject> records,
        StableValueEncoder encoder,
        RegistryCounts counts
    ) {
        Object registry = requireRuntimeClass(
            container.worldgen(), "appeng.core.features.registries.WorldGenRegistry"
        );
        Object raw = field(registry, "types");
        if (!(raw instanceof Object[])) {
            throw new IllegalStateException("AE2 worldgen type registry is not an array");
        }
        Object[] values = (Object[]) raw;
        IWorldGen.WorldGenType[] types = IWorldGen.WorldGenType.values();
        if (values.length != types.length) {
            throw new IllegalStateException("AE2 worldgen type registry length drifted");
        }
        for (int index = 0; index < values.length; index++) {
            Object value = values[index];
            if (value == null
                || !"appeng.core.features.registries.WorldGenRegistry$TypeSet".equals(
                    value.getClass().getName()
                )) {
                throw new IllegalStateException("invalid AE2 worldgen type rule");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "ae2-worldgen-rule");
            row.addProperty("worldgen_type", types[index].name());
            row.add("disabled_provider_classes", encoder.encode(field(value, "badProviders")));
            row.add("disabled_dimensions", encoder.encode(field(value, "badDimensions")));
            row.add("enabled_dimensions", encoder.encode(field(value, "enabledDimensions")));
            row.addProperty("world_context_evaluated", false);
            row.addProperty("world_generation_invoked", false);
            records.add(row);
        }
        counts.worldgenRuleCount = values.length;
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
            records.add(row);
        }
    }

    private static int addClassOccurrences(
        List<JsonObject> records,
        String recordType,
        Collection<?> values
    ) {
        Map<String, Integer> ordinals = new LinkedHashMap<String, Integer>();
        for (Object value : values) {
            if (value == null) throw new IllegalStateException(recordType + " contains null");
            String className = value.getClass().getName();
            Integer ordinal = ordinals.get(className);
            int index = ordinal == null ? 0 : ordinal.intValue();
            ordinals.put(className, Integer.valueOf(index + 1));
            JsonObject row = new JsonObject();
            row.addProperty("record_type", recordType);
            row.addProperty("runtime_class", className);
            row.addProperty("class_duplicate_ordinal", index);
            row.addProperty("contextual_behavior_invoked", false);
            records.add(row);
        }
        return values.size();
    }

    private static JsonElement optional(Optional<?> value, StableValueEncoder encoder) {
        if (value == null) throw new IllegalStateException("AE2 Optional is null");
        return value.isPresent() ? encoder.encode(value.get()) : JsonNull.INSTANCE;
    }

    private static JsonElement optionalClass(Optional<? extends Class<?>> value) {
        if (value == null) throw new IllegalStateException("AE2 class Optional is null");
        return value.isPresent()
            ? new com.google.gson.JsonPrimitive(value.get().getName())
            : JsonNull.INSTANCE;
    }

    private static Object requireRuntimeClass(Object value, String className) {
        if (value == null || !className.equals(value.getClass().getName())) {
            throw new IllegalStateException(
                "AE2 runtime class drifted for " + className + ": "
                    + (value == null ? "null" : value.getClass().getName())
            );
        }
        return value;
    }

    private static Object field(Object owner, String name) {
        Field candidate = null;
        for (Class<?> type = owner.getClass(); type != null && type != Object.class;
             type = type.getSuperclass()) {
            try {
                candidate = type.getDeclaredField(name);
                break;
            } catch (NoSuchFieldException ignored) {
                // Continue through the exact runtime hierarchy.
            }
        }
        if (candidate == null || Modifier.isStatic(candidate.getModifiers())) {
            throw new IllegalStateException(
                "required AE2 instance field is unavailable: "
                    + owner.getClass().getName() + '.' + name
            );
        }
        candidate.setAccessible(true);
        return ReflectionAccess.read(candidate, owner);
    }

    private static Collection<?> collectionField(Object owner, String name) {
        Object value = field(owner, name);
        if (!(value instanceof Collection<?>)) {
            throw new IllegalStateException("AE2 field is not a collection: " + name);
        }
        return (Collection<?>) value;
    }

    private static Map<?, ?> mapField(Object owner, String name) {
        Object value = field(owner, name);
        if (!(value instanceof Map<?, ?>)) {
            throw new IllegalStateException("AE2 field is not a map: " + name);
        }
        return (Map<?, ?>) value;
    }

    private static Object invoke(Method method, Object owner) {
        try {
            return method.invoke(owner);
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot invoke AE2 definition method " + method, exception);
        } catch (InvocationTargetException exception) {
            Throwable cause = exception.getCause();
            if (cause instanceof MissingDefinitionException) {
                String message = cause.getMessage();
                return new MissingDefinition(
                    cause.getClass().getName(),
                    message == null || message.isEmpty() ? "definition unavailable" : message
                );
            }
            throw new IllegalStateException(
                "AE2 definition method failed " + method,
                cause == null ? exception : cause
            );
        }
    }

    private static String requiredText(String value, String label) {
        if (value == null || value.isEmpty()) throw new IllegalStateException(label + " is empty");
        return value;
    }

    private static void requireExactCounts(
        DefinitionCounts definitions,
        RegistryCounts registries
    ) {
        if (
            definitions.methodCount != 224
            || definitions.definitionCount != 213
            || definitions.unimplementedDefinitionCount != 4
            || definitions.coloredDefinitionCount != 7
            || definitions.coloredVariantCount != 119
            || definitions.storageCellCount != 11
            || registries.grinderRecipeCount != 43
            || registries.inscriberRecipeCount != 14
            || registries.storageChannelCount != 2
            || registries.cellHandlerCount != 3
            || registries.cellGuiHandlerCount != 2
            || registries.chargerRateCount != 8
            || registries.p2pAttunementCount != 516
            || registries.matterCannonAmmoCount != 38
            || registries.gridCacheCount != 8
            || registries.wirelessHandlerCount != 5
            || registries.specialComparisonProviderCount != 0
            || registries.movableRuleCount != 24
            || registries.recipeHandlerCount != 0
            || registries.subitemResolverCount != 1
            || registries.worldgenRuleCount != 3
        ) {
            throw new IllegalStateException(
                "AE2 finite definition or registry universe differs from exact 0.56.6 profile"
            );
        }
    }

    private static final class DefinitionCounts {
        private int methodCount;
        private int definitionCount;
        private int unimplementedDefinitionCount;
        private int coloredDefinitionCount;
        private int coloredVariantCount;
        private int storageCellCount;
    }

    private static final class MissingDefinition {
        private final String exceptionClass;
        private final String message;

        private MissingDefinition(String exceptionClass, String message) {
            this.exceptionClass = exceptionClass;
            this.message = message;
        }
    }

    private static final class RegistryCounts {
        private int grinderRecipeCount;
        private int inscriberRecipeCount;
        private int storageChannelCount;
        private int cellHandlerCount;
        private int cellGuiHandlerCount;
        private int chargerRateCount;
        private int p2pAttunementCount;
        private int matterCannonAmmoCount;
        private int gridCacheCount;
        private int wirelessHandlerCount;
        private int specialComparisonProviderCount;
        private int movableRuleCount;
        private int recipeHandlerCount;
        private int subitemResolverCount;
        private int worldgenRuleCount;
    }
}
