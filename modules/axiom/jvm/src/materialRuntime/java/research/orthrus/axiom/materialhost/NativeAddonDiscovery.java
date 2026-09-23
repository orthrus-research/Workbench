package research.orthrus.axiom.materialhost;

import com.google.gson.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.discovery.*;
import net.minecraftforge.fml.common.discovery.asm.ASMModParser;
import java.io.*;
import java.lang.reflect.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Native JAR candidate states, explicitly not complete-composition mod activation. */
public final class NativeAddonDiscovery {
    private static final List<String> QUERIES = List.of("nuclearcraft", "actuallyadditions", "applecore", "gcys");
    private static final String RESOURCE = "axiom-addon-inventory.json";
    private NativeAddonDiscovery() {}

    public static Map<String,Object> inspect(Path home, NativeInitializationTrace trace) throws Exception {
        trace.begin("addon-candidate-discovery");
        byte[] raw;
        try (var input = Launch.classLoader.getResourceAsStream(RESOURCE)) {
            if (input == null) {
                trace.returned("addon-candidate-discovery");
                return Map.of("status", "not-supplied", "activationQualified", false,
                        "loadedQueriesResolved", false, "reason", "Complete profile-bound addon inventory not supplied");
            }
            raw = input.readNBytes((4 << 20) + 1);
        }
        if (raw.length > 4 << 20) throw new IllegalStateException("Addon inventory exceeds bound");
        var inventory = JsonParser.parseString(new String(raw, java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();
        if (!inventory.get("schema").getAsString().equals("axiom.native-addon-inventory.v6")
                || !inventory.get("candidateLayout").getAsString().equals("original-flat-profile-mod-filenames")
                || !inventory.get("candidateResourceScope").getAsString().equals("original-entrypoints-metadata-manifests-native-server-language-and-api-packages")
                || !inventory.get("side").getAsString().equals("server")
                || !inventory.get("selectedArtifactCoverage").getAsString().equals("complete-declared-selection")
                || inventory.get("activationQualified").getAsBoolean())
            throw new IllegalStateException("Addon inventory does not describe this bounded server context");
        var declarations = new TreeMap<String,List<Map<String,Object>>>();
        var discovery = new NativeAddonCandidates(home, inventory.get("candidateInputScope").getAsString());
        var libraries = new NativeAddonLibraryCandidates(inventory.getAsJsonArray("artifacts"), trace);
        var sideRestricted = new ArrayList<Map<String,Object>>();
        int sideEligible = 0;
        int classFiles = 0, modDeclarations = 0;
        for (var artifact : libraries.artifacts()) {
            classFiles += artifact.get("classFiles").getAsInt();
            for (var element : artifact.getAsJsonArray("declarations")) {
                var declaration = element.getAsJsonObject();
                String hash = declaration.get("sha256").getAsString();
                if (!hash.matches("[0-9a-f]{64}")) throw new IllegalStateException("Invalid original mod class digest");
                byte[] bytes;
                try (var input = Launch.classLoader.getResourceAsStream("axiom-addon-classes/" + hash)) {
                    if (input == null) throw new IllegalStateException("Original mod declaration bytes absent");
                    bytes = input.readNBytes((4 << 20) + 1);
                }
                if (bytes.length > 4 << 20 || !digest(bytes).equals(hash))
                    throw new IllegalStateException("Original mod declaration bytes differ");
                var parser = new ASMModParser(new ByteArrayInputStream(bytes));
                parser.validate();
                String name = parser.getASMType().getClassName();
                if (!name.equals(declaration.get("class").getAsString()))
                    throw new IllegalStateException("Original mod class identity differs");
                var annotations = parser.getAnnotations().stream().filter(a ->
                        a.getASMType().getClassName().equals("net.minecraftforge.fml.common.Mod")).toList();
                if (annotations.size() != 1) throw new IllegalStateException("Ambiguous native Mod declaration");
                var descriptor = annotations.getFirst().getValues();
                if (!new Gson().toJsonTree(descriptor).equals(declaration.get("annotation")))
                    throw new IllegalStateException("Native Mod annotation differs from inventory");
                String id = (String)descriptor.get("modid");
                if (id == null) throw new IllegalStateException("Native Mod declaration lacks modid");
                modDeclarations++;
                var row = new LinkedHashMap<String,Object>();
                row.put("class", name); row.put("classSha256", hash);
                row.put("artifact", artifact.get("outputPath").getAsString());
                row.put("artifactSha256", artifact.get("sha256").getAsString());
                // Only JDK collections may cross the native classloader lifetime.
                var manifest = new TreeMap<String,String>();
                artifact.getAsJsonObject("manifest").entrySet().forEach(e -> manifest.put(e.getKey(), e.getValue().getAsString()));
                row.put("jarManifest", manifest);
                row.put("annotation", descriptor);
                // Native metadata evaluation only: no candidate filtering, mod class
                // definition, dependency sorting or activation is implied by this call.
                var candidate = new ModCandidate(home.resolve(artifact.get("outputPath").getAsString()).toFile(),
                        home.resolve(artifact.get("outputPath").getAsString()).toFile(), ContainerType.JAR);
                var container = new FMLModContainer(name, candidate, descriptor);
                boolean eligible = container.shouldLoadInEnvironment();
                row.put("nativeSideEligible", eligible);
                if (eligible) sideEligible++;
                if (Boolean.TRUE.equals(descriptor.get("clientSideOnly")) || Boolean.TRUE.equals(descriptor.get("serverSideOnly")))
                    sideRestricted.add(Map.of("modId", id, "class", name, "classSha256", hash, "nativeSideEligible", eligible));
                declarations.computeIfAbsent(id, ignored -> new ArrayList<>()).add(row);
            }
            discovery.discover(artifact);
        }
        var duplicates = declarations.entrySet().stream().filter(e -> e.getValue().size() > 1).map(Map.Entry::getKey).toList();
        var apiProviders = discovery.apiProviders(trace);
        var configured = configuredStates(discovery.containers());
        var activation = NativeAddonActivation.inspect(discovery.containers(), trace);
        var injectedActivation = NativeCoremodComposition.activate(trace);
        var queries = new LinkedHashMap<String,Object>();
        for (String id : QUERIES) {
            var row = new LinkedHashMap<String,Object>();
            var candidates = declarations.getOrDefault(id, List.of());
            row.put("declarationStatus", candidates.isEmpty() ? "no-Mod-annotation-in-selected-artifacts" : "declared");
            row.put("candidates", candidates);
            if (configured.containsKey(id)) row.put("configuredEnabled", configured.get(id));
            if (discovery.metadata(id) != null) row.put("nativeCandidate", discovery.metadata(id));
            row.put("loadedState", "unresolved");
            queries.put(id, row);
        }
        var result = new LinkedHashMap<String,Object>();
        result.put("status", "native-candidates-observed"); result.put("inventorySha256", digest(raw));
        result.put("packRevision", inventory.get("packRevision").getAsString()); result.put("side", "server");
        result.put("selectedArtifacts", inventory.getAsJsonArray("artifacts").size());
        result.put("classFiles", classFiles); result.put("modDeclarations", modDeclarations);
        result.put("nativeSideEligibility", Map.of("method", "original FMLModContainer.shouldLoadInEnvironment",
                "side", FMLCommonHandler.instance().getSide().name(), "scope", "raw-Mod-annotations-not-filtered-discovery",
                "evaluated", modDeclarations, "eligible", sideEligible, "rejected", modDeclarations - sideEligible,
                "restrictedDeclarations", sideRestricted));
        result.put("duplicateDeclaredModIds", duplicates); result.put("queries", queries);
        result.put("nativeCandidateDiscovery", discovery.observation());
        result.put("nativeLibraryCandidates", libraries.observation());
        result.put("nativeCandidateActivation", activation);
        result.put("nativeInjectedCoremodActivation", injectedActivation);
        result.put("nativeApiProviders", apiProviders);
        if (discovery.metadata("gregtechfoodoption") != null)
            result.put("gtfoNativeCandidate", discovery.metadata("gregtechfoodoption"));
        result.put("gtfoCandidates", declarations.getOrDefault("gregtechfoodoption", List.of()));
        if (configured.containsKey("gregtechfoodoption")) result.put("gtfoConfiguredEnabled", configured.get("gregtechfoodoption"));
        Path states = home.resolve("config/fmlModState.properties");
        result.put("modStateFilePresent", Files.isRegularFile(states));
        if (Files.isRegularFile(states)) result.put("modStateFileSha256", digest(Files.readAllBytes(states)));
        result.put("configuredStateMethod", "original Loader.disableRequestedMods on native metadata containers; original loader map restored");
        result.put("activationQualified", false); result.put("loadedQueriesResolved", false);
        result.put("modClassesDefined", false); result.put("modCallbacksExecuted", false);
        result.put("unresolved", List.of("Cleanroom classpath/library candidate selection, injected containers and coremod/mixin discovery",
                "complete-composition enabled LoadController state (JAR candidate bus state observed separately)",
                "native dependency sorting and construction order"));
        trace.returned("addon-candidate-discovery");
        return result;
    }

    private static Map<String,Boolean> configuredStates(Map<String,ModContainer> containers) throws Exception {
        var loader = Loader.instance();
        Field named = Loader.class.getDeclaredField("namedMods"); named.setAccessible(true);
        Field forced = Loader.class.getDeclaredField("forcedModFile"); forced.setAccessible(true);
        Object previous = named.get(loader), previousFile = forced.get(loader);
        // The worker does not inherit launcher system-property mod-state overrides.
        if (!System.getProperty("fml.modStates", "").isEmpty())
            throw new IllegalStateException("Unbound launcher mod-state override");
        try {
            named.set(loader, containers);
            Method disable = Loader.class.getDeclaredMethod("disableRequestedMods"); disable.setAccessible(true);
            disable.invoke(loader);
            Field enabled = FMLModContainer.class.getDeclaredField("enabled"); enabled.setAccessible(true);
            var result = new LinkedHashMap<String,Boolean>();
            for (var entry : containers.entrySet()) result.put(entry.getKey(), enabled.getBoolean(entry.getValue()));
            return result;
        } finally {
            named.set(loader, previous); forced.set(loader, previousFile);
        }
    }

    private static String digest(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }
}
