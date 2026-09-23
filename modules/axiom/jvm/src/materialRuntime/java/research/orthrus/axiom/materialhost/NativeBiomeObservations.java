package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.Loader;
import com.google.gson.JsonParser;
import java.nio.file.*;
import java.util.*;
import java.util.function.Function;
import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;

/** Reads original biome memberships and initialized GT biome-map functions.
 * No biome initialization, lazy dictionary inference or world creation is invoked. */
final class NativeBiomeObservations {
    private static final String MOD = "biomesoplenty.common.init.ModBiomes";
    private static final String WORLDGEN = "gregtech.api.worldgen.config.WorldGenRegistry";
    private NativeBiomeObservations() {}

    static Map<String,Object> collect() {
        var result = new LinkedHashMap<String,Object>();
        result.put("schema", "axiom.native-biome-registrations.v1");
        result.put("scope", "original-selected-biomesoplenty-preinit-registrations");
        var gaps = new ArrayList<String>();
        var rows = new TreeMap<String,Object>();
        var disabled = new TreeSet<String>();
        try {
            var container = Loader.instance().getIndexedModList().get("biomesoplenty");
            require(container != null && Launch.classLoader.isClassLoaded(MOD), "Original Biomes O Plenty initialization is absent");
            Object proxy = field("biomesoplenty.core.BiomesOPlenty", null, "proxy");
            require(proxy != null && proxy.getClass().getName().equals("biomesoplenty.core.CommonProxy"), "Original SERVER proxy differs");
            Path source = container.getSource().toPath().toRealPath();
            Object registry = field("net.minecraftforge.fml.common.registry.ForgeRegistries", null, "BIOMES");
            var present = (Set<?>)field(MOD, null, "presentBiomes");
            var ids = (Map<?,?>)field(MOD, null, "biomeIdMap");
            var dictionary = (Map<?,?>)field("net.minecraftforge.common.BiomeDictionary", null, "biomeInfoMap");
            var enabled = new TreeSet<String>();
            for (var entry : ids.entrySet()) {
                String name = "biomesoplenty:" + entry.getKey();
                if (((Number)entry.getValue()).intValue() < 0) disabled.add(name); else enabled.add(name);
            }
            for (Object biome : (Iterable<?>)registry) {
                Object name = virtual("net.minecraftforge.registries.IForgeRegistryEntry", biome, "getRegistryName",
                        type("net.minecraft.util.ResourceLocation"), new Class<?>[0]);
                if (!name.toString().startsWith("biomesoplenty:")) continue;
                boolean sameSource = NativeEarlyClassSpace.sourceFile(biome.getClass().getProtectionDomain().getCodeSource().getLocation()).equals(source);
                boolean sameLoader = biome.getClass().getClassLoader() == Launch.classLoader;
                boolean sameRegistry = call(registry, "getValue", type("net.minecraft.util.ResourceLocation"), name) == biome;
                boolean samePresent = present.stream().anyMatch(value -> value == biome);
                require(sameSource && sameLoader && sameRegistry && samePresent, "Original biome identity differs: " + name);
                Object info = dictionary.get(name);
                var tags = new TreeSet<String>();
                if (info != null) for (Object tag : (Set<?>)field(info.getClass(), info, "types")) tags.add((String)call(tag, "getName"));
                rows.put(name.toString(), Map.of("class", biome.getClass().getName(), "codeSource", source.toString(),
                        "nativeClassLoaderIdentity", sameLoader, "nativeForgeRegistryIdentity", sameRegistry,
                        "nativePresentBiomeIdentity", samePresent, "dictionaryTypes", tags));
            }
            require(!enabled.isEmpty() && rows.keySet().equals(enabled), "Configured enabled biomes and original registries differ");
            Path home = Launch.minecraftHome.toPath().toRealPath();
            Path config = ((java.io.File)field(MOD, null, "biomeIdMapFile")).toPath().toRealPath();
            require(config.equals(home.resolve("config/biomesoplenty/biome_ids.json").toRealPath()), "Original biome configuration directory differs");
            result.put("configurationPath", "config/biomesoplenty/biome_ids.json");
            result.put("configurationDirectoryIdentity", true);
            result.put("configuredIds", new TreeMap<>(ids)); // Configured values are not Forge's assigned numeric IDs.
            result.put("serverProxy", proxy.getClass().getName());
            result.put("modState", Loader.instance().getModState(container).name());
        } catch (Exception | LinkageError failure) { gaps.add(failure.getClass().getName() + ": " + failure.getMessage()); }
        result.put("biomes", rows); result.put("disabledBiomes", disabled);
        return finish(result, gaps);
    }

    @SuppressWarnings("unchecked")
    static Map<String,Object> worldgen() {
        var result = new LinkedHashMap<String,Object>();
        result.put("schema", "axiom.native-worldgen-biome-bindings.v1");
        result.put("scope", "saved-gt-biome-map-definitions-after-original-initialization");
        var gaps = new ArrayList<String>();
        var rows = new TreeMap<String,Object>();
        try {
            require(Launch.classLoader.isClassLoaded(WORLDGEN), "Original GT worldgen initialization is absent");
            Object worldgen = field(WORLDGEN, null, "INSTANCE");
            Object registry = field("net.minecraftforge.fml.common.registry.ForgeRegistries", null, "BIOMES");
            Class<?> location = type("net.minecraft.util.ResourceLocation");
            for (String kind : List.of("vein", "fluid")) {
                var definitions = new HashMap<String,Object>();
                for (Object value : (List<?>)field(WORLDGEN, worldgen, kind.equals("vein")
                        ? "registeredVeinDefinitions" : "registeredBedrockVeinDefinitions"))
                    require(definitions.put((String)call(value, "getDepositName"), value) == null, "Duplicate original worldgen definition");
                Path root = Launch.minecraftHome.toPath().resolve("config/gregtech/worldgen/" + kind);
                try (var files = Files.walk(root)) {
                    for (Path path : files.filter(Files::isRegularFile).filter(p -> p.toString().endsWith(".json")).sorted().toList()) {
                        var config = JsonParser.parseString(Files.readString(path)).getAsJsonObject();
                        if (!config.has("biome_modifier") || !config.get("biome_modifier").isJsonObject()) continue;
                        var modifier = config.getAsJsonObject("biome_modifier");
                        if (!modifier.has("type") || !"biome_map".equals(modifier.get("type").getAsString())) continue;
                        String name = root.relativize(path).toString().replace('\\', '/');
                        Object definition = definitions.get(name);
                        String savedPath = "config/gregtech/worldgen/" + kind + "/" + name;
                        if (definition == null) { gaps.add("Original definition not registered: " + savedPath); continue; }
                        var function = (Function<Object,Integer>)call(definition, "getBiomeWeightModifier");
                        var weights = new TreeMap<String,Object>();
                        for (var entry : modifier.entrySet()) {
                            if (entry.getKey().equals("type")) continue;
                            Object key = location.getConstructor(String.class).newInstance(entry.getKey().toString());
                            Object biome = call(registry, "getValue", location, key);
                            require(biome != null, "Original biome missing for " + savedPath + ": " + key);
                            int expected = entry.getValue().getAsInt();
                            Integer actual = function.apply(biome); // Selected original lambda only reads its captured map.
                            require(Objects.equals(expected, actual), "Original biome weight differs for " + savedPath + ": " + key);
                            weights.put(key.toString(), Map.of("configuredWeight", expected, "nativeWeight", actual,
                                    "nativeBiomeClass", biome.getClass().getName(), "nativeForgeRegistryIdentity", true));
                        }
                        rows.put(savedPath, Map.of("definitionClass", definition.getClass().getName(),
                                "nativeDefinitionMembership", true, "biomeWeights", weights));
                    }
                }
            }
        } catch (Exception | LinkageError failure) { gaps.add(failure.getClass().getName() + ": " + failure.getMessage()); }
        result.put("definitions", rows);
        return finish(result, gaps);
    }

    private static Map<String,Object> finish(Map<String,Object> result, List<String> gaps) {
        result.put("status", gaps.isEmpty() ? "observed" : "incomplete");
        result.put("observationsComplete", gaps.isEmpty()); result.put("affectingGaps", gaps);
        return result;
    }
    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
