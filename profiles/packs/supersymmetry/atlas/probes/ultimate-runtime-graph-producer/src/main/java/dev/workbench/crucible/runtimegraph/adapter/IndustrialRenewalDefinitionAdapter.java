package dev.workbench.crucible.runtimegraph.adapter;

import cassiokf.industrialrenewal.config.IRConfig;
import cassiokf.industrialrenewal.recipes.LatheRecipe;

import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.item.ItemStack;
import net.minecraft.util.ResourceLocation;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Industrial Renewal content, final lathe definitions, and scalar pack configuration. */
public final class IndustrialRenewalDefinitionAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "industrial-renewal-domain-definitions"; }
    @Override public String categoryId() { return "industrial-renewal-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<String> namespaces = CompanionDefinitionSupport.namespaces("industrialrenewal");

        int blockCount = CompanionDefinitionSupport.captureOwnedBlocks(
            "industrial-renewal", namespaces, records, encoder
        );
        int itemCount = CompanionDefinitionSupport.captureOwnedItems(
            "industrial-renewal", namespaces, records, encoder
        );
        int recipeCount = captureLatheRecipes(records, encoder);
        int configurationCount = 0;
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "industrial-renewal", "main", IRConfig.MainConfig.Main, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "industrial-renewal", "generation", IRConfig.MainConfig.Generation, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "industrial-renewal", "recipes", IRConfig.MainConfig.Recipes, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "industrial-renewal", "railroad", IRConfig.MainConfig.Railroad, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "industrial-renewal", "render", IRConfig.MainConfig.Render, records, encoder
        );
        configurationCount += CompanionDefinitionSupport.captureConfigurationObject(
            "industrial-renewal", "sounds", IRConfig.MainConfig.Sounds, records, encoder
        );
        if (blockCount == 0 || itemCount == 0 || recipeCount == 0 || configurationCount == 0) {
            throw new IllegalStateException("Industrial Renewal definition universe is empty");
        }
        requireExactCounts(blockCount, itemCount, recipeCount, configurationCount);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "industrial-renewal-definition-authority");
        authority.addProperty("owned_block_count", blockCount);
        authority.addProperty("owned_item_count", itemCount);
        authority.addProperty("lathe_recipe_count", recipeCount);
        authority.addProperty("configuration_value_count", configurationCount);
        authority.addProperty("lathe_matching_invoked", false);
        authority.addProperty("machine_operation_invoked", false);
        authority.addProperty("multiblock_formation_invoked", false);
        authority.addProperty("world_generation_invoked", false);
        authority.addProperty("client_behavior_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void requireExactCounts(
        int blockCount,
        int itemCount,
        int recipeCount,
        int configurationCount
    ) {
        if (blockCount != 128 || itemCount != 166 || recipeCount != 2
            || configurationCount != 75) {
            throw new IllegalStateException(
                "Industrial Renewal definition counts drifted: blocks=" + blockCount
                    + ", items=" + itemCount
                    + ", lathe_recipes=" + recipeCount
                    + ", configuration_values=" + configurationCount
            );
        }
    }

    private static int captureLatheRecipes(
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        List<LatheRecipe> recipes = new ArrayList<LatheRecipe>(LatheRecipe.LATHE_RECIPES);
        for (int ordinal = 0; ordinal < recipes.size(); ordinal++) {
            LatheRecipe recipe = recipes.get(ordinal);
            if (recipe == null) throw new IllegalStateException("null Industrial Renewal lathe recipe");
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "industrial-renewal-lathe-recipe");
            row.addProperty("recipe_ordinal", ordinal);
            ResourceLocation name = recipe.getRegistryName();
            if (name == null) row.add("registry_name", JsonNull.INSTANCE);
            else row.addProperty("registry_name", name.toString());
            row.add("inputs", encoder.encode(recipe.getInput()));
            ItemStack output = recipe.getRecipeOutput();
            row.add("output", encoder.encode(output));
            row.addProperty("process_time", recipe.getProcessTime());
            row.addProperty("matching_invoked", false);
            records.add(row);
        }
        return recipes.size();
    }
}
