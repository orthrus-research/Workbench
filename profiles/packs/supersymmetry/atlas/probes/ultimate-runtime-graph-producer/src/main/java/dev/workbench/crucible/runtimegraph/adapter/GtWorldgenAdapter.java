package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;
import dev.workbench.crucible.runtimegraph.worldgen.WorldgenInitializerTrace;

import gregtech.api.worldgen.bedrockFluids.BedrockFluidVeinHandler;
import gregtech.api.worldgen.config.BedrockFluidDepositDefinition;
import gregtech.api.worldgen.config.OreDepositDefinition;
import gregtech.api.worldgen.config.WorldGenRegistry;
import gregtech.api.worldgen.filler.FillerEntry;
import gregtech.api.worldgen.populator.IVeinPopulator;
import gregtech.api.worldgen.shape.ShapeGenerator;

import it.unimi.dsi.fastutil.ints.Int2ObjectMap;

import net.minecraft.block.state.IBlockState;
import net.minecraft.util.math.Vec3i;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.math.BigDecimal;
import java.util.regex.Pattern;

/** Final GT ore/fluid worldgen authority reconciled to accepted initializer JSON. */
public final class GtWorldgenAdapter implements CaptureAdapter {
    private static final Pattern INTEGER_TOKEN = Pattern.compile("-?(?:0|[1-9][0-9]*)");
    @Override public String adapterId() { return "gt-worldgen"; }
    @Override public String categoryId() { return "world-generative"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        List<OreDepositDefinition> ores = new ArrayList<OreDepositDefinition>(
            WorldGenRegistry.getOreDeposits()
        );
        List<BedrockFluidDepositDefinition> fluids =
            new ArrayList<BedrockFluidDepositDefinition>(
                WorldGenRegistry.getBedrockVeinDeposits()
            );
        Map<Integer, String> dimensions = dimensions();
        WorldgenInitializerTrace.Snapshot trace = WorldgenInitializerTrace.global()
            .requireFinalSnapshot(oreRefs(ores), fluidRefs(fluids), dimensions);
        if (trace.oreDefinitions.size() != ores.size()
            || trace.fluidDefinitions.size() != fluids.size()) {
            throw new IllegalStateException("GT worldgen trace size differs from final registry");
        }
        reconcileBedrockHandler(fluids);

        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (WorldgenInitializerTrace.DimensionRecord dimension : trace.dimensions) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "gt-worldgen-named-dimension");
            row.addProperty("dimension_id", dimension.dimensionId);
            row.addProperty("name", dimension.name);
            records.add(row);
        }
        for (int index = 0; index < ores.size(); index++) {
            WorldgenInitializerTrace.DefinitionRecord retained =
                trace.oreDefinitions.get(index);
            OreDepositDefinition definition = ores.get(index);
            if (retained.identity != definition) {
                throw new IllegalStateException("GT ore worldgen trace identity differs");
            }
            records.add(ore(retained, definition, encoder));
        }
        for (int index = 0; index < fluids.size(); index++) {
            WorldgenInitializerTrace.DefinitionRecord retained =
                trace.fluidDefinitions.get(index);
            BedrockFluidDepositDefinition definition = fluids.get(index);
            if (retained.identity != definition) {
                throw new IllegalStateException("GT fluid worldgen trace identity differs");
            }
            records.add(fluid(retained, definition, encoder));
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "gt-worldgen-registry-authority");
        authority.addProperty("ore_deposit_count", ores.size());
        authority.addProperty("bedrock_fluid_deposit_count", fluids.size());
        authority.addProperty("named_dimension_count", dimensions.size());
        authority.addProperty("initializer_retention", "accepted-json-reconciled-to-final-object-identity");
        authority.addProperty("runtime_registry", WorldGenRegistry.class.getName());
        records.add(authority);
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static JsonObject ore(
        WorldgenInitializerTrace.DefinitionRecord retained,
        OreDepositDefinition definition,
        StableValueEncoder encoder
    ) {
        JsonObject config = retained.initializer;
        requireOreAgreement(config, definition);
        ShapeGenerator generator = definition.getShapeGenerator();
        if (generator == null || definition.getBlockFiller() == null) {
            throw new IllegalStateException("GT ore definition lacks generator or filler");
        }
        Vec3i maximum = generator.getMaxSize();
        if (maximum == null) throw new IllegalStateException("GT generator maximum size is null");

        JsonObject row = common(retained, definition.getClass());
        row.addProperty("record_type", "gt-worldgen-ore-deposit");
        nullable(row, "assigned_name", definition.getAssignedName());
        nullable(row, "description", definition.getDescription());
        row.addProperty("weight", definition.getWeight());
        row.addProperty("priority", definition.getPriority());
        row.add("density", encoder.encode(Float.valueOf(definition.getDensity())));
        row.addProperty("minimum_height", definition.getMinimumHeight());
        row.addProperty("maximum_height", definition.getMaximumHeight());
        row.addProperty("count_as_vein", definition.isVein());
        row.addProperty("generator_class", stable(generator));
        JsonObject generatorSize = new JsonObject();
        generatorSize.addProperty("x", maximum.getX());
        generatorSize.addProperty("y", maximum.getY());
        generatorSize.addProperty("z", maximum.getZ());
        row.add("generator_maximum_size", generatorSize);
        row.addProperty("filler_class", stable(definition.getBlockFiller()));
        row.add("filler_outputs", fillerOutputs(definition, encoder));
        IVeinPopulator populator = definition.getVeinPopulator();
        if (populator == null) {
            row.add("populator_class", JsonNull.INSTANCE);
            row.add("populator_runtime", JsonNull.INSTANCE);
        } else {
            row.addProperty("populator_class", stable(populator));
            row.add("populator_runtime", encoder.encode(populator));
        }
        row.add("dimension_selectors", dimensionSelectors(config));
        row.add("declared_ore_materials", declaredOreMaterials(config));
        row.add("biome_modifier_executable", executable(definition.getBiomeWeightModifier()));
        row.add("dimension_filter_executable", executable(definition.getDimensionFilter()));
        row.add("generation_predicate_executable", executable(definition.getGenerationPredicate()));
        return row;
    }

    private static JsonObject fluid(
        WorldgenInitializerTrace.DefinitionRecord retained,
        BedrockFluidDepositDefinition definition,
        StableValueEncoder encoder
    ) {
        JsonObject config = retained.initializer;
        requireFluidAgreement(config, definition);
        Fluid stored = definition.getStoredFluid();
        JsonObject row = common(retained, definition.getClass());
        row.addProperty("record_type", "gt-worldgen-bedrock-fluid-deposit");
        nullable(row, "assigned_name", definition.getAssignedName());
        nullable(row, "description", definition.getDescription());
        row.addProperty("weight", definition.getWeight());
        row.addProperty("minimum_yield_inclusive", definition.getMinimumYield());
        row.addProperty("maximum_yield_exclusive", definition.getMaximumYield());
        row.addProperty("depletion_amount", definition.getDepletionAmount());
        row.addProperty("depletion_chance_percent", definition.getDepletionChance());
        row.addProperty("depleted_yield", definition.getDepletedYield());
        row.addProperty("fluid_name", stored.getName());
        row.add("fluid_runtime", encoder.encode(stored));
        row.add("dimension_selectors", dimensionSelectors(config));
        row.add("biome_modifier_executable", executable(definition.getBiomeWeightModifier()));
        row.add("dimension_filter_executable", executable(definition.getDimensionFilter()));
        return row;
    }

    private static JsonObject common(
        WorldgenInitializerTrace.DefinitionRecord retained,
        Class<?> runtimeClass
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("deposit_scope", retained.scope.id);
        row.addProperty("deposit_name", retained.name);
        row.addProperty("registry_ordinal", retained.registryOrdinal);
        row.addProperty("name_occurrence_ordinal", retained.nameOccurrenceOrdinal);
        row.addProperty("initializer_attempt_ordinal", retained.initializerAttemptOrdinal);
        row.addProperty("initializer_sha256", retained.initializerSha256);
        row.add("accepted_initializer_projection", stableJson(retained.initializer));
        row.addProperty("runtime_class", StableValueEncoder.stableClassName(runtimeClass));
        return row;
    }

    private static JsonArray fillerOutputs(
        OreDepositDefinition definition,
        StableValueEncoder encoder
    ) {
        List<FillerEntry> entries = definition.getBlockFiller().getAllPossibleStates();
        if (entries == null || entries.isEmpty()) {
            throw new IllegalStateException("GT worldgen filler exposes no possible states");
        }
        JsonArray result = new JsonArray();
        for (int entryOrdinal = 0; entryOrdinal < entries.size(); entryOrdinal++) {
            FillerEntry entry = entries.get(entryOrdinal);
            if (entry == null) throw new IllegalStateException("GT filler contains null entry");
            Collection<IBlockState> states = entry.getPossibleResults();
            if (states == null || states.isEmpty()) {
                throw new IllegalStateException("GT filler entry has no possible results");
            }
            List<JsonElement> encoded = new ArrayList<JsonElement>();
            for (IBlockState state : states) {
                if (state == null) throw new IllegalStateException("GT filler result is null");
                encoded.add(encoder.encode(state));
            }
            Collections.sort(encoded, new Comparator<JsonElement>() {
                @Override public int compare(JsonElement left, JsonElement right) {
                    return dev.workbench.crucible.runtimegraph.CanonicalJson.compareUnsigned(
                        dev.workbench.crucible.runtimegraph.CanonicalJson.bytes(left),
                        dev.workbench.crucible.runtimegraph.CanonicalJson.bytes(right)
                    );
                }
            });
            for (int resultOrdinal = 0; resultOrdinal < encoded.size(); resultOrdinal++) {
                JsonObject output = new JsonObject();
                output.addProperty("entry_ordinal", entryOrdinal);
                output.addProperty("result_ordinal", resultOrdinal);
                output.add("block_state", encoded.get(resultOrdinal));
                result.add(output);
            }
        }
        return result;
    }

    private static JsonArray dimensionSelectors(JsonObject config) {
        JsonArray values;
        boolean defaulted;
        if (config.has("dimension_filter")) {
            values = config.getAsJsonArray("dimension_filter");
            defaulted = false;
        } else {
            values = new JsonArray();
            values.add("is_surface_world");
            defaulted = true;
        }
        JsonArray result = new JsonArray();
        for (int ordinal = 0; ordinal < values.size(); ordinal++) {
            String token = values.get(ordinal).getAsString();
            JsonObject row = new JsonObject();
            row.addProperty("ordinal", ordinal);
            row.addProperty("token", token);
            row.addProperty("kind", selectorKind(token));
            row.addProperty("defaulted", defaulted);
            result.add(row);
        }
        return result;
    }

    private static String selectorKind(String token) {
        if ("is_surface_world".equals(token)) return "surface_world";
        if ("is_nether".equals(token)) return "nether_world";
        if (token.startsWith("dimension_id:")) {
            String remainder = token.substring("dimension_id:".length());
            int separator = remainder.indexOf(':');
            if (separator < 0) {
                Integer.parseInt(remainder);
                return "dimension_id";
            }
            if (remainder.indexOf(':', separator + 1) >= 0) {
                throw new IllegalStateException("invalid GT dimension range selector");
            }
            if (separator > 0) Integer.parseInt(remainder.substring(0, separator));
            if (separator + 1 < remainder.length()) {
                Integer.parseInt(remainder.substring(separator + 1));
            }
            return "dimension_id_range";
        }
        if (token.startsWith("name:")) return "dimension_type_name";
        if (token.startsWith("provider_class:")) return "provider_class";
        throw new IllegalStateException("unknown GT dimension selector: " + token);
    }

    private static JsonArray declaredOreMaterials(JsonObject config) {
        Set<String> names = new LinkedHashSet<String>();
        collectOreMaterials(config.get("filler"), names);
        List<String> sorted = new ArrayList<String>(names);
        Collections.sort(sorted);
        JsonArray result = new JsonArray();
        for (String name : sorted) result.add(name);
        return result;
    }

    private static void collectOreMaterials(JsonElement value, Set<String> result) {
        if (value == null || value.isJsonNull()) return;
        if (value.isJsonPrimitive() && value.getAsJsonPrimitive().isString()) {
            String text = value.getAsString();
            if (text.startsWith("ore:") && text.length() > 4) result.add(text.substring(4));
        } else if (value.isJsonArray()) {
            for (JsonElement child : value.getAsJsonArray()) collectOreMaterials(child, result);
        } else if (value.isJsonObject()) {
            for (Map.Entry<String, JsonElement> entry : value.getAsJsonObject().entrySet()) {
                collectOreMaterials(entry.getValue(), result);
            }
        }
    }

    private static JsonObject executable(Object value) {
        if (value == null) throw new IllegalStateException("GT worldgen executable is null");
        JsonObject result = new JsonObject();
        result.addProperty("runtime_class", stable(value));
        result.addProperty("behavior_body_captured", false);
        result.addProperty("declarative_source", "accepted_initializer_json");
        return result;
    }

    /** Preserve JSON number lexemes while removing untyped floating primitives. */
    private static JsonElement stableJson(JsonElement value) {
        if (value == null || value.isJsonNull()) return JsonNull.INSTANCE;
        if (value.isJsonObject()) {
            JsonObject result = new JsonObject();
            for (Map.Entry<String, JsonElement> entry : value.getAsJsonObject().entrySet()) {
                result.add(entry.getKey(), stableJson(entry.getValue()));
            }
            return result;
        }
        if (value.isJsonArray()) {
            JsonArray result = new JsonArray();
            for (JsonElement child : value.getAsJsonArray()) result.add(stableJson(child));
            return result;
        }
        if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) {
            return value.deepCopy();
        }
        String token = value.getAsString();
        if (INTEGER_TOKEN.matcher(token).matches()) return value.deepCopy();
        BigDecimal decimal;
        try {
            decimal = new BigDecimal(token);
        } catch (NumberFormatException error) {
            throw new IllegalStateException("invalid accepted GT worldgen number token", error);
        }
        JsonObject result = new JsonObject();
        result.addProperty("decimal", decimal.toPlainString());
        result.addProperty("source_lexeme", token);
        result.addProperty("value_kind", "json-decimal-token");
        return result;
    }

    private static void requireOreAgreement(
        JsonObject config,
        OreDepositDefinition definition
    ) {
        requireInt(config, "weight", definition.getWeight());
        if (!config.has("density")
            || Float.compare(config.get("density").getAsFloat(), definition.getDensity()) != 0) {
            throw new IllegalStateException("GT ore density differs from initializer");
        }
        requireOptionalInt(config, "priority", 0, definition.getPriority());
        requireOptionalInt(config, "min_height", Integer.MIN_VALUE, definition.getMinimumHeight());
        requireOptionalInt(config, "max_height", Integer.MAX_VALUE, definition.getMaximumHeight());
        boolean vein = !config.has("count_as_vein") || config.get("count_as_vein").getAsBoolean();
        if (vein != definition.isVein()) {
            throw new IllegalStateException("GT ore vein flag differs from initializer");
        }
        if (!config.has("generator") || !config.has("filler")) {
            throw new IllegalStateException("GT ore initializer lacks generator or filler");
        }
    }

    private static void requireFluidAgreement(
        JsonObject config,
        BedrockFluidDepositDefinition definition
    ) {
        requireInt(config, "weight", definition.getWeight());
        JsonObject yields = config.getAsJsonObject("yield");
        requireInt(yields, "min", definition.getMinimumYield());
        requireInt(yields, "max", definition.getMaximumYield());
        JsonObject depletion = config.getAsJsonObject("depletion");
        requireInt(depletion, "amount", definition.getDepletionAmount());
        int chance = Math.max(0, Math.min(100, depletion.get("chance").getAsInt()));
        if (chance != definition.getDepletionChance()) {
            throw new IllegalStateException("GT fluid depletion chance differs from initializer");
        }
        int depleted = depletion.has("depleted_yield")
            ? depletion.get("depleted_yield").getAsInt()
            : 0;
        if (depleted != definition.getDepletedYield()) {
            throw new IllegalStateException("GT fluid depleted yield differs from initializer");
        }
        Fluid configured = FluidRegistry.getFluid(config.get("fluid").getAsString());
        if (configured == null || configured != definition.getStoredFluid()) {
            throw new IllegalStateException("GT fluid identity differs from final Forge registry");
        }
    }

    private static void requireInt(JsonObject value, String key, int expected) {
        if (!value.has(key) || value.get(key).getAsInt() != expected) {
            throw new IllegalStateException("GT worldgen integer differs: " + key);
        }
    }

    private static void requireOptionalInt(
        JsonObject value,
        String key,
        int defaultValue,
        int expected
    ) {
        int configured = value.has(key) ? value.get(key).getAsInt() : defaultValue;
        if (configured != expected) {
            throw new IllegalStateException("GT worldgen optional integer differs: " + key);
        }
    }

    private static void reconcileBedrockHandler(
        List<BedrockFluidDepositDefinition> definitions
    ) {
        List<Map.Entry<BedrockFluidDepositDefinition, Integer>> active =
            new ArrayList<Map.Entry<BedrockFluidDepositDefinition, Integer>>(
                BedrockFluidVeinHandler.veinList.entrySet()
            );
        if (active.size() != definitions.size()) {
            throw new IllegalStateException("GT bedrock-fluid active list size differs");
        }
        for (int index = 0; index < definitions.size(); index++) {
            Map.Entry<BedrockFluidDepositDefinition, Integer> row = active.get(index);
            if (row.getKey() != definitions.get(index)
                || row.getValue() == null
                || row.getValue().intValue() != definitions.get(index).getWeight()) {
                throw new IllegalStateException("GT bedrock-fluid active list identity differs");
            }
        }
    }

    private static List<WorldgenInitializerTrace.DefinitionRef> oreRefs(
        List<OreDepositDefinition> values
    ) {
        List<WorldgenInitializerTrace.DefinitionRef> result =
            new ArrayList<WorldgenInitializerTrace.DefinitionRef>();
        for (OreDepositDefinition value : values) {
            result.add(new WorldgenInitializerTrace.DefinitionRef(value, value.getDepositName()));
        }
        return result;
    }

    private static List<WorldgenInitializerTrace.DefinitionRef> fluidRefs(
        List<BedrockFluidDepositDefinition> values
    ) {
        List<WorldgenInitializerTrace.DefinitionRef> result =
            new ArrayList<WorldgenInitializerTrace.DefinitionRef>();
        for (BedrockFluidDepositDefinition value : values) {
            result.add(new WorldgenInitializerTrace.DefinitionRef(value, value.getDepositName()));
        }
        return result;
    }

    private static Map<Integer, String> dimensions() {
        Map<Integer, String> result = new LinkedHashMap<Integer, String>();
        for (Int2ObjectMap.Entry<String> entry
            : WorldGenRegistry.getNamedDimensions().int2ObjectEntrySet()) {
            if (result.put(Integer.valueOf(entry.getIntKey()), entry.getValue()) != null) {
                throw new IllegalStateException("duplicate GT named dimension ID");
            }
        }
        return result;
    }

    private static String stable(Object value) {
        return StableValueEncoder.stableClassName(value.getClass());
    }

    private static void nullable(JsonObject target, String key, String value) {
        if (value == null) target.add(key, JsonNull.INSTANCE);
        else target.addProperty(key, value);
    }
}
