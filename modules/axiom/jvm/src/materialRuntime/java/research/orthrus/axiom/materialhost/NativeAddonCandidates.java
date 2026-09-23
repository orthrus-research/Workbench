package research.orthrus.axiom.materialhost;

import com.google.gson.*;
import com.google.common.collect.ListMultimap;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.discovery.*;
import net.minecraftforge.fml.common.versioning.ArtifactVersion;
import java.io.*;
import java.lang.reflect.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Original JAR/container/metadata discovery; never global activation or subscriber discovery. */
public final class NativeAddonCandidates {
    private final String scope;
    private final Map<String,ModContainer> containers = new LinkedHashMap<>();
    private final Map<String,Object> metadata = new TreeMap<>();
    private final ASMDataTable apiTable = new ASMDataTable();
    private final Map<File,String> sourceIdentities = new HashMap<>();
    private final List<Map<String,Object>> excludedEntries = new ArrayList<>();
    private final Map<String,Object> injected;
    private long inputBytes;
    private int artifacts;

    public NativeAddonCandidates(Path home, String scope) throws Exception {
        if (!Set.of("entrypoints-and-metadata", "complete-artifacts").contains(scope))
            throw new IllegalArgumentException("Unknown native candidate input scope");
        this.scope = scope;
        if ("true".equals(System.getProperty("fml.enableJsonAnnotations")))
            throw new IllegalStateException("JSON annotation discovery is not the selected native context");
        ModContainerFactory.instance();
        if (!ModContainerFactory.modTypes.keySet().stream().map(t -> t.getClassName()).toList()
                .equals(List.of("net.minecraftforge.fml.common.Mod")))
            throw new IllegalStateException("Unbound custom native container factory");
        var loader = Loader.instance();
        for (String name : List.of("injectedBefore", "injectedAfter"))
            if (!injections(loader, name).isEmpty())
                throw new IllegalStateException("Native dependency reading requires fresh loader state");
        Method read = Loader.class.getDeclaredMethod("readInjectedDependencies"); read.setAccessible(true);
        read.invoke(loader);
        // The original reader may log and retain partial results; never repair/reset them.
        injected = new LinkedHashMap<>();
        Path file = home.resolve("config/injectedDependencies.json");
        injected.put("filePresent", Files.isRegularFile(file));
        if (Files.isRegularFile(file)) injected.put("inputSha256", digest(Files.readAllBytes(file)));
        injected.put("before", dependencyMap(injections(loader, "injectedBefore")));
        injected.put("after", dependencyMap(injections(loader, "injectedAfter")));
        injected.put("method", "original Loader.readInjectedDependencies");
    }

    public void discover(JsonObject artifact) throws Exception {
        var input = artifact.getAsJsonObject("candidateInput");
        String expected = input.get("sha256").getAsString();
        long size = input.get("size").getAsLong();
        inputBytes += size;
        if (!expected.matches("[0-9a-f]{64}") || size < 1 || size > 256L << 20 || inputBytes > 2L << 30)
            throw new IllegalStateException("Native candidate input identity/bound differs");
        if (scope.equals("complete-artifacts") && (!expected.equals(artifact.get("sha256").getAsString())
                || size != artifact.get("size").getAsLong()))
            throw new IllegalStateException("Native full-artifact reference differs from selected original");
        Path path = NativeAddonLibraryCandidates.inputPath(artifact);
        // Immutable discovery inputs are provisioned once and verified by the runtime.
        // Neither projected nor full original JARs are copied through worker writes.
        if (!Files.isRegularFile(path) || Files.size(path) != size)
            throw new IllegalStateException("Native candidate file absent/resized");
        artifacts++;
        var hash = MessageDigest.getInstance("SHA-256"); long length = 0;
        try (var source = Files.newInputStream(path)) {
            byte[] buffer = new byte[65536];
            for (int count; (count = source.read(buffer)) != -1;) {
                length += count;
                if (length > size) throw new IllegalStateException("Native candidate input exceeded declared size");
                hash.update(buffer, 0, count);
            }
        }
        if (length != size || !HexFormat.of().formatHex(hash.digest()).equals(expected))
            throw new IllegalStateException("Native candidate input bytes differ");
        var candidate = new ModCandidate(path.toFile(), path.toFile(), ContainerType.JAR);
        // This complete original call owns entry filtering, parser/factory decisions,
        // metadata fallback, version properties and native exception/log behavior.
        sourceIdentities.put(candidate.getModContainer(), artifact.get("sha256").getAsString());
        var found = candidate.explore(apiTable);
        for (var declaration : artifact.getAsJsonArray("declarations")) {
            var d = declaration.getAsJsonObject();
            String entry = d.get("entry").getAsString();
            if (!candidate.getClassList().contains(entry.substring(0, entry.length() - 6)))
                excludedEntries.add(Map.of("class", d.get("class").getAsString(),
                        "classSha256", d.get("sha256").getAsString(), "entry", entry));
        }
        for (ModContainer container : found) {
            if (container.getMod() != null) throw new IllegalStateException("Candidate discovery constructed a mod instance");
            if (containers.putIfAbsent(container.getModId(), container) != null)
                throw new IllegalStateException("Duplicate discovered candidate ID: " + container.getModId());
            metadata.put(container.getModId(), describe(container, artifact));
        }
    }

    public Map<String,ModContainer> containers() { return containers; }
    public Map<String,Object> apiProviders(NativeInitializationTrace trace) throws Exception {
        return NativeAddonApis.inspect(apiTable, sourceIdentities, trace);
    }
    public Object metadata(String id) { return metadata.get(id); }
    public Map<String,Object> observation() throws Exception {
        var result = new LinkedHashMap<String,Object>();
        result.put("method", "original ModCandidate.explore -> JarDiscoverer -> ModContainerFactory -> bindMetadata");
        result.put("inputScope", scope); result.put("artifacts", artifacts);
        result.put("inputBytes", inputBytes); result.put("containers", containers.size());
        result.put("candidateOrder", new ArrayList<>(containers.keySet()));
        result.put("metadataSha256", digest(new Gson().toJson(metadata).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        result.put("classEntryExclusions", excludedEntries); result.put("injectedDependencies", injected);
        result.put("completeAsmSubscriberTable", false); result.put("activationQualified", false);
        return result;
    }

    private static Map<String,Object> describe(ModContainer container, JsonObject artifact) throws Exception {
        Field className = FMLModContainer.class.getDeclaredField("className"); className.setAccessible(true);
        var value = new TreeMap<String,Object>();
        value.put("class", className.get(container)); value.put("modId", container.getModId());
        value.put("artifactSha256", artifact.get("sha256").getAsString());
        value.put("name", container.getName()); value.put("version", container.getVersion());
        value.put("classVersion", container.getClassVersion());
        value.put("autogeneratedMetadata", container.getMetadata().autogenerated);
        value.put("minecraftRange", String.valueOf(container.acceptableMinecraftVersionRange()));
        value.put("requirements", container.getRequirements().stream().map(String::valueOf).sorted().toList());
        value.put("after", container.getDependencies().stream().map(String::valueOf).toList());
        value.put("before", container.getDependants().stream().map(String::valueOf).toList());
        return value;
    }

    @SuppressWarnings("unchecked")
    private static ListMultimap<String,ArtifactVersion> injections(Loader loader, String name) throws Exception {
        Field field = Loader.class.getDeclaredField(name); field.setAccessible(true);
        return (ListMultimap<String,ArtifactVersion>)field.get(loader);
    }
    private static Map<String,Object> dependencyMap(ListMultimap<String,ArtifactVersion> values) {
        var result = new TreeMap<String,Object>();
        values.keySet().forEach(key -> result.put(key, values.get(key).stream().map(String::valueOf).toList()));
        return result;
    }
    private static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
