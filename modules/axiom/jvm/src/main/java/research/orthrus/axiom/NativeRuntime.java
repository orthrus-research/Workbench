package research.orthrus.axiom;

import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Admission of the real, profile-selected JVM. No modeled Java execution or fallback runtime. */
public final class NativeRuntime {
    private NativeRuntime() {}
    static boolean windows() { return System.getProperty("os.name").startsWith("Windows"); }
    private static final String POLICY = Target.resource(windows() ? "/axiom/jvm-runtime-windows-x64.json" : "/axiom/jvm-runtime.json");
    private static Map<String, Object> receipt;

    /** One preflight per process, before input evaluation. The installation must remain trusted and unchanged. */
    public static synchronized Map<String, Object> require() {
        if (receipt != null) return receipt;
        Map<String, Object> policy = Json.object(Json.parse(POLICY));
        Map<String, String> observed = Map.of("runtimeVersion", Runtime.version().toString(),
                "vendor", System.getProperty("java.vendor"), "vmName", System.getProperty("java.vm.name"),
                "os", windows() ? "Windows" : System.getProperty("os.name"), "architecture", System.getProperty("os.arch"));
        for (var entry : observed.entrySet()) {
            if (!Objects.equals(policy.get(entry.getKey()), entry.getValue()))
                throw mismatch("Expected " + entry.getKey() + "=" + policy.get(entry.getKey()) + "; observed " + entry.getValue());
        }
        List<String> arguments = List.copyOf(ManagementFactory.getRuntimeMXBean().getInputArguments());
        validateArguments(arguments);
        for (Object required : Json.array(policy.getOrDefault("requiredVmArguments", List.of())))
            if (!arguments.contains(required)) throw mismatch("Required runtime argument is absent: " + required);
        if (windows() && arguments.stream().anyMatch(a -> a.startsWith("-Xshare:") && !a.equals("-Xshare:off")))
            throw mismatch("Windows execution requires shared archives disabled");
        try {
            Path home = Path.of(System.getProperty("java.home")).toRealPath();
            verifyFiles(home, Json.array(policy.get("runtimeFiles")));
        } catch (IOException failure) { throw mismatch("Selected runtime files unavailable: " + failure.getMessage()); }
        Map<String, Object> identity = new LinkedHashMap<>();
        identity.put("policySha256", Json.bytesDigest(POLICY.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        identity.put("observed", observed);
        identity.put("vmArguments", arguments);
        Map<String, String> properties = new TreeMap<>();
        for (String key : List.of("file.encoding", "native.encoding", "user.language", "user.country", "user.timezone"))
            properties.put(key, System.getProperty(key, ""));
        identity.put("properties", Map.copyOf(properties));
        identity.put("runtimeId", "axiom-jvm:sha256:" + Json.digest(identity));
        identity.put("execution", "native-jvm");
        identity.put("profile", policy.get("profile"));
        identity.put("identityScope", policy.get("identityScope"));
        identity.put("verification", "process-preflight");
        identity.put("loadedMemoryAttested", false);
        receipt = Collections.unmodifiableMap(identity);
        return receipt;
    }

    static void validateArguments(List<String> arguments) {
        for (String argument : arguments) {
            for (String forbidden : List.of("-javaagent", "-agentlib", "-agentpath", "-Xbootclasspath", "-Xrun",
                    "--patch-module", "--upgrade-module-path", "-Djava.system.class.loader", "-Djava.home",
                    "-Djava.library.path", "-Dsun.boot.library.path", "-Djava.security.properties",
                    "-XX:SharedArchiveFile", "-XX:ArchiveClassesAtExit")) {
                if (argument.startsWith(forbidden)) throw mismatch("Unadmitted runtime override: " + argument);
            }
        }
    }

    static void verifyFiles(Path home, List<Object> files) throws IOException {
        Set<String> seen = new HashSet<>();
        for (Object value : files) {
            Map<String, Object> row = Json.object(value);
            String name = Json.string(row.get("path"));
            Path relative = Path.of(name);
            if (name.isEmpty() || relative.isAbsolute() || name.contains("\\") || name.contains(":") || !relative.normalize().toString().replace('\\','/').equals(name)
                    || relative.startsWith("..") || !seen.add(name)) throw mismatch("Unsafe or duplicate runtime entry: " + name);
            Path path = home;
            for (Path part : relative) {
                path = path.resolve(part);
                if (Files.isSymbolicLink(path)) throw mismatch("Indirect runtime entry: " + name);
            }
            if (!Files.isRegularFile(path) || Files.size(path) != ((Number)row.get("size")).longValue())
                throw mismatch("Missing or resized runtime entry: " + name);
            try {
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                try (var stream = Files.newInputStream(path)) {
                    byte[] buffer = new byte[65536];
                    for (int count; (count = stream.read(buffer)) != -1;) digest.update(buffer, 0, count);
                }
                if (!HexFormat.of().formatHex(digest.digest()).equals(row.get("sha256")))
                    throw mismatch("Changed runtime entry: " + name);
            } catch (java.security.NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
        }
    }

    private static Failure mismatch(String message) {
        return new Failure("execution-error", "runtime.mismatch", message);
    }
}
