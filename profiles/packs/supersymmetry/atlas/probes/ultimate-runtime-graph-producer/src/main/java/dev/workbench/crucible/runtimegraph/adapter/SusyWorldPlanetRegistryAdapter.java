package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.world.biome.Biome;
import net.minecraftforge.common.BiomeManager;
import supersymmetry.common.world.Planet;
import supersymmetry.common.world.SuSyDimensions;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Loaded SuSy world-planet definitions, independent of celestial metadata. */
public final class SusyWorldPlanetRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-world-planet-registry"; }
    @Override public String categoryId() { return "susy-world-planets"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<Integer, Planet> registry = SuSyDimensions.PLANETS;
        if (registry == null) throw new IllegalStateException("SuSy world planet registry is null");
        for (Map.Entry<Integer, Planet> entry : registry.entrySet()) {
            Planet planet = entry.getValue();
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-world-planet");
            row.addProperty("registry_dimension", entry.getKey());
            row.addProperty("id", planet.getId());
            row.addProperty("dimension", planet.getDimID());
            row.addProperty("registry_dimension_matches", entry.getKey().intValue() == planet.getDimID());
            row.addProperty("name", planet.getPlanetName());
            row.addProperty("loaded", planet.isLoaded());
            row.addProperty("average_ground_level", planet.getAverageGroundLevel());
            row.addProperty("biome_size", planet.getBiomeSize());
            row.add("stone", encoder.encode(planet.getStone()));
            row.add("bedrock", encoder.encode(planet.getBedrock()));
            row.add("gravity", encoder.encode(Double.valueOf(planet.gravity)));
            row.add("drag_multiplier", encoder.encode(Double.valueOf(planet.dragMultiplier)));
            row.addProperty("supports_fire", planet.supportsFire);
            JsonArray biomes = new JsonArray();
            for (BiomeManager.BiomeEntry biomeEntry : planet.getBiomeList()) {
                JsonObject biome = new JsonObject();
                biome.addProperty("registry_name", Biome.REGISTRY.getNameForObject(biomeEntry.biome).toString());
                biome.addProperty("weight", biomeEntry.itemWeight);
                biomes.add(biome);
            }
            row.add("biomes", biomes);
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-world-planet-registry-authority");
        authority.addProperty("planet_count", registry.size());
        authority.addProperty("global_biome_count", SuSyDimensions.BIOMES == null ? -1 : SuSyDimensions.BIOMES.size());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
