package research.orthrus.axiom.materialhost;

import com.google.gson.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.relauncher.libraries.LibraryManager;
import net.minecraftforge.fml.relauncher.libraries.ModList;
import java.io.File;
import java.lang.reflect.Field;
import java.net.JarURLConnection;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.jar.JarFile;
import net.minecraftforge.common.ForgeVersion;

/** Original library selection on declared flat inputs, before complete launcher composition. */
public final class NativeAddonLibraryCandidates {
    private final List<JsonObject> artifacts = new ArrayList<>();
    private final Map<String,Object> observation = new LinkedHashMap<>();

    public NativeAddonLibraryCandidates(JsonArray inputs, NativeInitializationTrace trace) throws Exception {
        trace.declare("addon-library-candidates", "hook", "LibraryManager.getCandidates");
        trace.begin("addon-library-candidates");
        // Explicit profile directory and no launcher additions. Do not consult
        // the host's own classpath, invoke library setup, or stage worker JAR copies.
        if (System.getProperty("crl.dev.extrapath") != null || LibraryManager.ENABLE_AUTO_MOD_MOVEMENT)
            throw new IllegalStateException("Unbound native library selection override");
        Field cache = ModList.class.getDeclaredField("cache"); cache.setAccessible(true);
        if (!(cache.get(null) instanceof Map<?,?> lists) || !lists.isEmpty())
            throw new IllegalStateException("Native mod-list repository composition is not established");
        Field nativeHome = LibraryManager.class.getDeclaredField("minecraftHome"); nativeHome.setAccessible(true);
        Field candidates = LibraryManager.class.getDeclaredField("candidates"); candidates.setAccessible(true);
        Object previousHome = nativeHome.get(null), previousCandidates = candidates.get(null);
        if (previousCandidates != null)
            throw new IllegalStateException("Native library selection requires an unconsumed candidate cache");
        boolean hadArgs = Launch.blackboard.containsKey("forgeLaunchArgs");
        Object previousArgs = Launch.blackboard.get("forgeLaunchArgs");
        if (hadArgs && (!(previousArgs instanceof Map<?,?> args) || !args.isEmpty()))
            throw new IllegalStateException("Unbound native launcher arguments");
        var declared = new LinkedHashMap<Path,JsonObject>();
        var manifests = new ArrayList<Object>();
        var embedded = new TreeMap<String,String>();
        int present = 0;
        for (var input : inputs) {
            var artifact = input.getAsJsonObject();
            Path path = inputPath(artifact);
            if (!Files.isRegularFile(path) || declared.putIfAbsent(path, artifact) != null)
                throw new IllegalStateException("Native library input absent or duplicated");
            if (inspectManifest(path, artifact, embedded)) present++;
            manifests.add(new TreeMap<>(Map.of("artifactSha256", artifact.get("sha256").getAsString(),
                    "inputs", artifact.getAsJsonArray("manifestInputs"))));
        }
        Path home = runtimeRoot().resolve("native-addon-home");
        for (String name : List.of("mods/mod_list.json", "mods/" + ForgeVersion.mcVersion + "/mod_list.json"))
            if (Files.exists(home.resolve(name)))
                throw new IllegalStateException("External native mod-list inputs are not composed");
        var previousUrls = Launch.classLoader.getURLs();
        var order = new ArrayList<String>();
        try {
            Launch.blackboard.put("forgeLaunchArgs", Map.of());
            nativeHome.set(null, home.toFile());
            // Complete original call: native enumeration, raw manifest reading,
            // basic mod-list composition and candidate caching. The explicitly
            // absent Bansoukou/external lists do not admit their executable closure.
            List<File> selected = LibraryManager.getCandidates();
            if (LibraryManager.getCandidates() != selected)
                throw new IllegalStateException("Native library candidate cache identity differs");
            for (File file : selected) {
                JsonObject artifact = declared.remove(file.toPath());
                if (artifact == null) throw new IllegalStateException("Unbound/repeated native library candidate");
                artifacts.add(artifact);
                order.add(artifact.get("outputPath").getAsString());
            }
            if (!declared.isEmpty()) throw new IllegalStateException("Native enumeration omitted selected artifacts");
        } finally {
            candidates.set(null, previousCandidates);
            nativeHome.set(null, previousHome);
            if (hadArgs) Launch.blackboard.put("forgeLaunchArgs", previousArgs);
            else Launch.blackboard.remove("forgeLaunchArgs");
            if (Launch.blackboard.containsKey("forgeLaunchArgs") != hadArgs
                    || Launch.blackboard.get("forgeLaunchArgs") != previousArgs)
                throw new IllegalStateException("Native launcher arguments were not restored");
            if (candidates.get(null) != previousCandidates || nativeHome.get(null) != previousHome
                    || cache.get(null) != lists || !lists.isEmpty()
                    || !Arrays.equals(previousUrls, Launch.classLoader.getURLs()))
                throw new IllegalStateException("Native library/launcher state was not preserved");
        }
        observation.put("method", "original LibraryManager.getCandidates");
        observation.put("scope", "flat-profile-artifact-directory-only-not-complete-Cleanroom-selection");
        observation.put("artifactOrder", order);
        observation.put("originalFilenames", true);
        observation.put("launcherArgumentsRestored", true);
        observation.put("hostLibraryStateRestored", true);
        observation.put("nativeCacheIdentityObserved", true);
        observation.put("manifestSelection", Map.of("reader", "original java.util.jar.JarFile.getManifest",
                "artifacts", inputs.size(), "present", present, "rawBytesVerified", true,
                "identitySha256", digest(new Gson().toJson(manifests).getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                "bansoukouPresent", false, "containedDependencyCarriers", embedded));
        observation.put("containedDependencyExtractionExecuted", false);
        observation.put("externalModListsComposed", false);
        observation.put("classpathCandidatesComposed", false);
        observation.put("coremodFilteringApplied", false);
        observation.put("completeSelectionQualified", false);
        trace.returned("addon-library-candidates");
    }

    private static boolean inspectManifest(Path path, JsonObject artifact, Map<String,String> embedded) throws Exception {
        // Admission reads the SAME original resources as LibraryManager. Do not
        // infer absence from the old lossy manifest map or execute projected code.
        try (var jar = new JarFile(path.toFile())) {
            var declared = new HashMap<String,JsonObject>();
            for (var input : artifact.getAsJsonArray("manifestInputs")) {
                var row = input.getAsJsonObject();
                if (declared.putIfAbsent(row.get("entry").getAsString(), row) != null)
                    throw new IllegalStateException("Duplicate original manifest identity");
            }
            for (var it = jar.entries(); it.hasMoreElements();) {
                var entry = it.nextElement();
                if (!entry.getName().equalsIgnoreCase("META-INF/MANIFEST.MF") || entry.isDirectory()) continue;
                var row = declared.remove(entry.getName());
                if (row == null) throw new IllegalStateException("Unbound raw native manifest");
                try (var input = jar.getInputStream(entry)) {
                    byte[] raw = input.readNBytes((1 << 20) + 1);
                    if (raw.length > 1 << 20 || raw.length != row.get("size").getAsLong()
                            || !digest(raw).equals(row.get("sha256").getAsString()))
                        throw new IllegalStateException("Original native manifest bytes differ");
                }
            }
            if (!declared.isEmpty()) throw new IllegalStateException("Original native manifest omitted");
            var manifest = jar.getManifest();
            var attributes = new TreeMap<String,String>();
            if (manifest != null) manifest.getMainAttributes().forEach((k,v) -> attributes.put(k.toString(),v.toString()));
            if (!new Gson().toJsonTree(attributes).equals(artifact.get("manifest")))
                throw new IllegalStateException("Native manifest attributes differ from original inventory");
            if (manifest == null) return false;
            var main = manifest.getMainAttributes();
            if (main.getValue("Bansoukou") != null)
                throw new IllegalStateException("Bansoukou executable library-selection closure is not established");
            if (main.getValue("ContainedDeps") != null)
                embedded.put(artifact.get("outputPath").getAsString(), main.getValue("ContainedDeps"));
            return true;
        }
    }

    private static String digest(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }

    public List<JsonObject> artifacts() { return List.copyOf(artifacts); }
    public Map<String,Object> observation() { return observation; }

    public static Path inputPath(JsonObject artifact) throws Exception {
        String output = artifact.get("outputPath").getAsString();
        Path relative = Path.of(output);
        if (!output.startsWith("mods/") || relative.isAbsolute() || relative.getNameCount() != 2
                || !relative.normalize().equals(relative) || output.contains("\\") || !output.endsWith(".jar"))
            throw new IllegalStateException("Original flat profile mod filename required");
        String input = artifact.getAsJsonObject("candidateInput").get("path").getAsString();
        if (!input.equals("native-addon-home/" + output))
            throw new IllegalStateException("Native candidate filename differs from selected descriptor");
        return runtimeRoot().resolve(input);
    }

    private static Path runtimeRoot() throws Exception {
        var location = Launch.classLoader.getResource("axiom-addon-inventory.json");
        if (location == null || !(location.openConnection() instanceof JarURLConnection connection))
            throw new IllegalStateException("Native candidate inventory requires an ordinary packaged JAR");
        var inventory = Path.of(connection.getJarFileURL().toURI()).toRealPath();
        return inventory.getParent().getParent();
    }
}
