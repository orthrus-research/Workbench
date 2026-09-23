package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.block.Block;
import net.minecraft.item.Item;
import net.minecraft.util.ResourceLocation;

import wile.rsgauges.ModContent;
import wile.rsgauges.detail.ModConfig;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** RSGauges registered content and final server-relevant configuration. */
public final class RsGaugesDefinitionAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "rsgauges-domain-definitions"; }
    @Override public String categoryId() { return "rsgauges-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<String> namespaces = CompanionDefinitionSupport.namespaces("rsgauges");
        int blockCount = CompanionDefinitionSupport.captureOwnedBlocks(
            "rsgauges", namespaces, records, encoder
        );
        int itemCount = CompanionDefinitionSupport.captureOwnedItems(
            "rsgauges", namespaces, records, encoder
        );
        int declaredBlockCount = captureDeclaredBlocks(records);
        int declaredItemCount = captureDeclaredItems(records);
        int configurationCount = 0;
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "rsgauges", "feature-optout", ModConfig.optouts, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "rsgauges", "performance", ModConfig.tweaks, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "rsgauges", "testing", ModConfig.zmisc, records, encoder
        );
        if (blockCount == 0 || itemCount == 0 || declaredBlockCount == 0
            || declaredItemCount == 0 || configurationCount == 0) {
            throw new IllegalStateException("RSGauges definition universe is empty");
        }
        requireExactCounts(
            blockCount,
            itemCount,
            declaredBlockCount,
            declaredItemCount,
            configurationCount
        );

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "rsgauges-definition-authority");
        authority.addProperty("owned_block_count", blockCount);
        authority.addProperty("owned_item_count", itemCount);
        authority.addProperty("declared_block_count", declaredBlockCount);
        authority.addProperty("declared_item_count", declaredItemCount);
        authority.addProperty("configuration_value_count", configurationCount);
        authority.addProperty("redstone_or_sensor_evaluation_invoked", false);
        authority.addProperty("world_or_tile_state_captured", false);
        authority.addProperty("client_behavior_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void requireExactCounts(
        int blockCount,
        int itemCount,
        int declaredBlockCount,
        int declaredItemCount,
        int configurationCount
    ) {
        if (blockCount != 121 || itemCount != 122 || declaredBlockCount != 121
            || declaredItemCount != 1 || configurationCount != 32) {
            throw new IllegalStateException(
                "RSGauges definition counts drifted: blocks=" + blockCount
                    + ", items=" + itemCount
                    + ", declared_blocks=" + declaredBlockCount
                    + ", declared_items=" + declaredItemCount
                    + ", configuration_values=" + configurationCount
            );
        }
    }

    private static int captureDeclaredBlocks(List<JsonObject> records) {
        List<Block> blocks = ModContent.getRegisteredBlocks();
        for (int ordinal = 0; ordinal < blocks.size(); ordinal++) {
            Block block = blocks.get(ordinal);
            ResourceLocation name = Block.REGISTRY.getNameForObject(block);
            if (name == null || !"rsgauges".equals(name.getNamespace())) {
                throw new IllegalStateException("invalid RSGauges declared block " + name);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "rsgauges-declared-block-membership");
            row.addProperty("membership_ordinal", ordinal);
            row.addProperty("definition_id", name.toString());
            row.addProperty("runtime_class", block.getClass().getName());
            records.add(row);
        }
        return blocks.size();
    }

    private static int captureDeclaredItems(List<JsonObject> records) {
        List<Item> items = ModContent.registeredItems;
        for (int ordinal = 0; ordinal < items.size(); ordinal++) {
            Item item = items.get(ordinal);
            ResourceLocation name = Item.REGISTRY.getNameForObject(item);
            if (name == null || !"rsgauges".equals(name.getNamespace())) {
                throw new IllegalStateException("invalid RSGauges declared item " + name);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "rsgauges-declared-item-membership");
            row.addProperty("membership_ordinal", ordinal);
            row.addProperty("definition_id", name.toString());
            row.addProperty("runtime_class", item.getClass().getName());
            records.add(row);
        }
        return items.size();
    }
}
