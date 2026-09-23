package dev.workbench.crucible.runtimegraph.adapter;

import biomesoplenty.api.biome.IExtendedBiome;
import biomesoplenty.api.enums.BOPClimates;
import biomesoplenty.api.generation.GeneratorStage;
import biomesoplenty.api.generation.IGenerator;
import biomesoplenty.common.config.GameplayConfigurationHandler;
import biomesoplenty.common.config.MiscConfigurationHandler;
import biomesoplenty.common.world.GeneratorRegistry;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.util.ResourceLocation;
import net.minecraft.world.biome.Biome;
import net.minecraftforge.common.BiomeDictionary;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Final BOP biome, climate, and generator definitions without random selection or generation. */
public final class BiomesOPlentyDefinitionAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "biomes-o-plenty-world-definitions"; }
    @Override public String categoryId() { return "biomes-o-plenty-world-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Counts counts = captureBiomes(records, encoder);
        counts.climateEntryCount = captureClimates(records, encoder);
        counts.generatorTypeCount = captureGeneratorTypes(records, encoder);
        counts.configurationCount = captureConfiguration(records, encoder);
        if (counts.biomeCount == 0 || counts.extendedBiomeCount == 0
            || counts.generatorDefinitionCount == 0 || counts.climateEntryCount == 0
            || counts.generatorTypeCount == 0 || counts.configurationCount == 0) {
            throw new IllegalStateException("Biomes O Plenty definition universe is empty");
        }
        requireExactCounts(counts);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "biomes-o-plenty-definition-authority");
        authority.addProperty("owned_biome_count", counts.biomeCount);
        authority.addProperty("extended_biome_count", counts.extendedBiomeCount);
        authority.addProperty("biome_generator_definition_count", counts.generatorDefinitionCount);
        authority.addProperty("climate_weight_entry_count", counts.climateEntryCount);
        authority.addProperty("generator_type_count", counts.generatorTypeCount);
        authority.addProperty("configuration_value_count", counts.configurationCount);
        authority.addProperty("random_biome_selection_invoked", false);
        authority.addProperty("generator_builder_invoked", false);
        authority.addProperty("generator_execution_invoked", false);
        authority.addProperty("chunk_or_world_mutation_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void requireExactCounts(Counts counts) {
        if (counts.biomeCount != 66 || counts.extendedBiomeCount != 66
            || counts.generatorDefinitionCount != 748 || counts.climateEntryCount != 80
            || counts.generatorTypeCount != 36 || counts.configurationCount != 5) {
            throw new IllegalStateException(
                "BOP definition counts drifted: owned_biomes=" + counts.biomeCount
                    + ", extended_biomes=" + counts.extendedBiomeCount
                    + ", biome_generators=" + counts.generatorDefinitionCount
                    + ", climate_entries=" + counts.climateEntryCount
                    + ", generator_types=" + counts.generatorTypeCount
                    + ", configuration_values=" + counts.configurationCount
            );
        }
    }

    private static Counts captureBiomes(List<JsonObject> records, StableValueEncoder encoder) {
        Counts counts = new Counts();
        List<ResourceLocation> names = new ArrayList<ResourceLocation>(Biome.REGISTRY.getKeys());
        Collections.sort(names);
        for (ResourceLocation name : names) {
            Biome biome = Biome.REGISTRY.getObject(name);
            if (!(biome instanceof IExtendedBiome)) continue;
            IExtendedBiome extended = (IExtendedBiome) biome;
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "biomes-o-plenty-biome-definition");
            row.addProperty("biome_id", name.toString());
            row.addProperty("numeric_biome_id", Biome.getIdForBiome(biome));
            row.addProperty("namespace", name.getNamespace());
            row.addProperty("runtime_class", biome.getClass().getName());
            row.add("base_height", encoder.encode(Float.valueOf(biome.getBaseHeight())));
            row.add("height_variation", encoder.encode(Float.valueOf(biome.getHeightVariation())));
            row.add("default_temperature", encoder.encode(Float.valueOf(biome.getDefaultTemperature())));
            row.add("rainfall", encoder.encode(Float.valueOf(biome.getRainfall())));
            row.addProperty("high_humidity", biome.isHighHumidity());
            row.addProperty("snow_enabled", biome.getEnableSnow());
            row.addProperty("can_rain", biome.canRain());
            row.addProperty("biome_owner", extended.getBiomeOwner().name());
            ResourceLocation base = Biome.REGISTRY.getNameForObject(extended.getBaseBiome());
            if (base == null) row.add("base_biome", JsonNull.INSTANCE);
            else row.addProperty("base_biome", base.toString());
            ResourceLocation beach = extended.getBeachLocation();
            if (beach == null) row.add("beach_biome", JsonNull.INSTANCE);
            else row.addProperty("beach_biome", beach.toString());
            row.add("biome_dictionary_types", biomeTypes(biome));
            row.add("climate_weight_map", encoder.encode(extended.getWeightMap()));
            records.add(row);
            counts.extendedBiomeCount++;
            if ("biomesoplenty".equals(name.getNamespace())) counts.biomeCount++;

            for (GeneratorStage stage : GeneratorStage.values()) {
                Collection<IGenerator> generators = extended.getGenerationManager()
                    .getGeneratorsForStage(stage);
                List<IGenerator> ordered = new ArrayList<IGenerator>(generators);
                Collections.sort(ordered, new Comparator<IGenerator>() {
                    @Override
                    public int compare(IGenerator left, IGenerator right) {
                        int value = left.getName().compareTo(right.getName());
                        if (value != 0) return value;
                        value = left.getIdentifier().compareTo(right.getIdentifier());
                        if (value != 0) return value;
                        return left.getClass().getName().compareTo(right.getClass().getName());
                    }
                });
                for (int ordinal = 0; ordinal < ordered.size(); ordinal++) {
                    IGenerator generator = ordered.get(ordinal);
                    JsonObject generatorRow = new JsonObject();
                    generatorRow.addProperty(
                        "record_type", "biomes-o-plenty-biome-generator-definition"
                    );
                    generatorRow.addProperty("biome_id", name.toString());
                    generatorRow.addProperty("stage", stage.name());
                    generatorRow.addProperty("stage_ordinal", stage.ordinal());
                    generatorRow.addProperty("generator_ordinal", ordinal);
                    generatorRow.addProperty("generator_name", generator.getName());
                    generatorRow.addProperty("generator_identifier", generator.getIdentifier());
                    generatorRow.addProperty("runtime_class", generator.getClass().getName());
                    generatorRow.add(
                        "definition_state",
                        withoutNonsemanticRuntimeCaches(encoder.encode(generator))
                    );
                    generatorRow.addProperty("execution_invoked", false);
                    records.add(generatorRow);
                    counts.generatorDefinitionCount++;
                }
            }
        }
        return counts;
    }

    @SuppressWarnings("unchecked")
    private static int captureClimates(List<JsonObject> records, StableValueEncoder encoder) {
        Field entriesField = ReflectionAccess.requireAssignableField(
            BOPClimates.class, "landBiomes", List.class
        );
        Field totalField = ReflectionAccess.requireField(
            BOPClimates.class, "totalBiomesWeight", Integer.TYPE
        );
        int count = 0;
        for (BOPClimates climate : BOPClimates.values()) {
            List<BOPClimates.WeightedBiomeEntry> entries =
                (List<BOPClimates.WeightedBiomeEntry>) ReflectionAccess.read(entriesField, climate);
            JsonObject climateRow = new JsonObject();
            climateRow.addProperty("record_type", "biomes-o-plenty-climate-authority");
            climateRow.addProperty("climate_id", climate.name());
            if (climate.biomeType == null) climateRow.add("biome_type", JsonNull.INSTANCE);
            else climateRow.addProperty("biome_type", climate.biomeType.name());
            climateRow.addProperty(
                "total_biome_weight", ((Integer) ReflectionAccess.read(totalField, climate)).intValue()
            );
            climateRow.addProperty("weighted_entry_count", entries.size());
            climateRow.addProperty("random_selection_invoked", false);
            records.add(climateRow);
            for (int ordinal = 0; ordinal < entries.size(); ordinal++) {
                BOPClimates.WeightedBiomeEntry entry = entries.get(ordinal);
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "biomes-o-plenty-climate-biome-weight");
                row.addProperty("climate_id", climate.name());
                row.addProperty("entry_ordinal", ordinal);
                row.addProperty("weight", entry.weight);
                row.add("biome", encoder.encode(entry.biome));
                records.add(row);
                count++;
            }
        }
        return count;
    }

    @SuppressWarnings("unchecked")
    private static int captureGeneratorTypes(
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        Field classesField = ReflectionAccess.requireAssignableField(
            GeneratorRegistry.class, "generatorClasses", Map.class
        );
        Field buildersField = ReflectionAccess.requireAssignableField(
            GeneratorRegistry.class, "generatorBuilders", Map.class
        );
        Map<String, Class<? extends IGenerator>> classes =
            (Map<String, Class<? extends IGenerator>>) ReflectionAccess.read(classesField, null);
        Map<String, Object> builders =
            (Map<String, Object>) ReflectionAccess.read(buildersField, null);
        List<String> identifiers = new ArrayList<String>(classes.keySet());
        Collections.sort(identifiers);
        if (!builders.keySet().equals(classes.keySet())) {
            throw new IllegalStateException("BOP generator class and builder registries differ");
        }
        for (String identifier : identifiers) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "biomes-o-plenty-generator-type-definition");
            row.addProperty("generator_identifier", identifier);
            row.addProperty("generator_class", classes.get(identifier).getName());
            row.add(
                "builder",
                withoutNonsemanticRuntimeCaches(encoder.encode(builders.get(identifier)))
            );
            row.addProperty("builder_invoked", false);
            records.add(row);
        }
        return identifiers.size();
    }

    private static int captureConfiguration(
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        Map<String, Object> values = new LinkedHashMap<String, Object>();
        values.put("gameplay#flowerDropsNeedShears", GameplayConfigurationHandler.flowerDropsNeedShears);
        values.put("misc#enableFogColours", MiscConfigurationHandler.enableFogColours);
        values.put("misc#overrideTitlePanorama", MiscConfigurationHandler.overrideTitlePanorama);
        values.put("misc#trailVisbilityMode", MiscConfigurationHandler.trailVisbilityMode);
        values.put("misc#useBoPWorldTypeDefault", MiscConfigurationHandler.useBoPWorldTypeDefault);
        for (Map.Entry<String, Object> entry : values.entrySet()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "biomes-o-plenty-configuration-value");
            row.addProperty("configuration_id", entry.getKey());
            row.add("value", encoder.encode(entry.getValue()));
            records.add(row);
        }
        return values.size();
    }

    private static JsonArray biomeTypes(Biome biome) {
        List<String> names = new ArrayList<String>();
        Set<BiomeDictionary.Type> types = BiomeDictionary.getTypes(biome);
        for (BiomeDictionary.Type type : types) names.add(type.getName());
        Collections.sort(names);
        JsonArray result = new JsonArray();
        for (String name : names) result.add(name);
        return result;
    }

    /** Remove a StellarCore memoization field whose value is launch-order dependent. */
    private static JsonElement withoutNonsemanticRuntimeCaches(JsonElement value) {
        JsonElement result = value.deepCopy();
        removeNonsemanticRuntimeCaches(result);
        return result;
    }

    private static void removeNonsemanticRuntimeCaches(JsonElement value) {
        if (value == null || value.isJsonNull() || value.isJsonPrimitive()) return;
        if (value.isJsonArray()) {
            for (JsonElement item : value.getAsJsonArray()) {
                removeNonsemanticRuntimeCaches(item);
            }
            return;
        }
        JsonObject object = value.getAsJsonObject();
        object.remove("net.minecraft.block.properties.PropertyEnum#stellar_core$cachedHashCode");
        for (Map.Entry<String, JsonElement> entry : object.entrySet()) {
            removeNonsemanticRuntimeCaches(entry.getValue());
        }
    }

    private static final class Counts {
        private int biomeCount;
        private int extendedBiomeCount;
        private int generatorDefinitionCount;
        private int climateEntryCount;
        private int generatorTypeCount;
        private int configurationCount;
    }
}
