package research.orthrus.axiom;

import java.io.IOException;
import java.lang.reflect.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;

/** Native utility-library boundary. No launcher, remapper, game bootstrap or fallback map. */
final class RegistryRuntime implements AutoCloseable {
    record ActiveMod(String modId, boolean injectedFmlContainer) {
        ActiveMod(String modId) { this(modId, false); }
        String getModId() { return modId; }
    }
    record Diagnostic(String template, List<Object> arguments) {}

    private final URLClassLoader loader;
    private final Class<?> named, simple, integers;
    private final List<Diagnostic> diagnostics = new ArrayList<>();
    private boolean closed;
    private int nextNetworkId;
    private ActiveMod activeMod;
    private MaterialRegistryManager manager;
    private NativeDyeColor[] dyeColors;
    private final Map<Object, NativeNbtValue> nbtValues = new IdentityHashMap<>();
    private final Map<String, Object> identity;

    private RegistryRuntime(Path root) throws IOException, ReflectiveOperationException {
        NativeRuntime.require();
        root = root.toAbsolutePath().normalize();
        for (Path part = root; part != null; part = part.getParent())
            if (Files.isSymbolicLink(part)) throw Failure.request("Indirect registry runtime root");
        String raw = Target.resource("/axiom/registry-runtime.json");
        Map<String, Object> policy = Json.object(Json.parse(raw));
        try {
            Path collections = Path.of(it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap.class
                    .getProtectionDomain().getCodeSource().getLocation().toURI());
            if (!Json.bytesDigest(Files.readAllBytes(collections)).equals(Json.object(policy.get("collectionImplementation")).get("sha256")))
                throw Failure.request("Loaded Fastutil differs from the selected registry runtime");
        } catch (URISyntaxException failure) { throw Failure.request("Cannot identify loaded Fastutil"); }
        List<Object> files = Json.array(policy.get("runtimeFiles"));
        NativeRuntime.verifyFiles(root, files);
        List<URL> urls = new ArrayList<>();
        for (Object value : files) urls.add(root.resolve(Json.string(Json.object(value).get("path"))).toUri().toURL());
        // Only JDK parents: neither ambient classpath nor engine dependencies may shadow the selected utilities.
        loader = new URLClassLoader(urls.toArray(URL[]::new), ClassLoader.getPlatformClassLoader());
        try {
            Map<String, Object> classes = Json.object(policy.get("classes"));
            for (Object value : classes.values()) {
                var row = Json.object(value);
                String name = Json.string(row.get("name"));
                try (var input = loader.getResourceAsStream(name + ".class")) {
                    if (input == null || !Json.bytesDigest(Main.read(input, 1 << 20)).equals(row.get("sha256")))
                        throw Failure.request("Registry utility class differs: " + name);
                }
            }
            named = Class.forName("fh", false, loader);
            simple = Class.forName("fo", false, loader);
            integers = Class.forName("qz", false, loader);
            identity = Map.of("schema", "axiom.registry-runtime-receipt.v1", "profile", "cleanroom",
                    "policySha256", Json.bytesDigest(raw.getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                    "runtimeFiles", List.copyOf(files), "minecraftLaunched", false,
                    "execution", "native-untransformed-registry-utilities", "installedCompositionQualified", false);
        } catch (Throwable failure) {
            loader.close();
            throw failure;
        }
    }

    static RegistryRuntime open(Path root) {
        try { return new RegistryRuntime(root); }
        catch (IOException | ReflectiveOperationException failure) {
            throw new Failure("incomplete", "registry.runtime", "Registry utilities unavailable: " + failure);
        }
    }

    Map<String, Object> identity() { requireOpen(); return identity; }
    NativeDyeColor[] dyeColors() {
        requireOpen();
        if (dyeColors == null) {
            Object[] values = (Object[]) utility("ahs", "values", new Class<?>[0], null);
            dyeColors = Arrays.stream(values).map(value -> new NativeDyeColor(this, value)).toArray(NativeDyeColor[]::new);
        }
        return dyeColors.clone(); // Native values() returns a clone, identities stay interned.
    }
    ActiveMod activeModContainer() { requireOpen(); return activeMod; }
    void activeMod(String modId) { requireOpen(); activeMod = modId == null ? null : new ActiveMod(modId); }
    void activeMod(String modId, boolean injectedFmlContainer) {
        requireOpen(); activeMod = modId == null ? null : new ActiveMod(modId, injectedFmlContainer);
    }
    int nextNetworkId() { requireOpen(); return nextNetworkId++; }
    void error(String template, Object... arguments) {
        requireOpen(); diagnostics.add(new Diagnostic(template, Collections.unmodifiableList(Arrays.asList(arguments.clone()))));
    }
    List<Diagnostic> diagnostics() { requireOpen(); return List.copyOf(diagnostics); }

    MaterialRegistryManager materials() {
        requireOpen();
        if (manager == null) manager = new MaterialRegistryManager(this);
        return manager;
    }

    Object newRegistry() {
        requireOpen();
        try { return named.getConstructor().newInstance(); }
        catch (InvocationTargetException failure) { throw propagate(failure.getCause()); }
        catch (ReflectiveOperationException failure) { throw binding(failure); }
    }

    Object newDefaultedRegistry(Object defaultKey) {
        requireOpen();
        try { return nativeType("ey").getConstructor(Object.class).newInstance(defaultKey); }
        catch (InvocationTargetException failure) { throw propagate(failure.getCause()); }
        catch (ReflectiveOperationException failure) { throw binding(failure); }
    }

    Object location(String... parts) {
        requireOpen();
        try {
            Class<?> type = Class.forName("nf", true, loader);
            return parts.length == 1 ? type.getConstructor(String.class).newInstance(parts[0])
                    : type.getConstructor(String.class, String.class).newInstance(parts[0], parts[1]);
        } catch (InvocationTargetException failure) { throw propagate(failure.getCause()); }
        catch (ReflectiveOperationException failure) { throw binding(failure); }
    }
    Object utility(String type, String method, Class<?>[] parameters, Object receiver, Object... args) {
        requireOpen();
        try { return call(Class.forName(type, true, loader), method, parameters, receiver, args); }
        catch (ClassNotFoundException failure) { throw binding(failure); }
    }

    Class<?> nativeType(String name) {
        requireOpen();
        try { return Class.forName(name, false, loader); }
        catch (ClassNotFoundException failure) { throw binding(failure); }
    }
    NativeNbtCompound newNbtCompound() { return (NativeNbtCompound) newNbt("fy", new Class<?>[0]); }
    NativeNbtList newNbtList() { return (NativeNbtList) newNbt("ge", new Class<?>[0]); }
    NativeNbtValue nbtString(String value) { return newNbt("gm", new Class<?>[]{String.class}, value); }
    private NativeNbtValue newNbt(String name, Class<?>[] parameters, Object... args) {
        requireOpen();
        try { return wrapNbt(nativeType(name).getConstructor(parameters).newInstance(args)); }
        catch (InvocationTargetException failure) { throw propagate(failure.getCause()); }
        catch (ReflectiveOperationException failure) { throw binding(failure); }
    }
    NativeNbtValue wrapNbt(Object value) {
        requireOpen();
        if (value == null) return null;
        if (!nativeType("gn").isInstance(value)) throw binding(new IllegalArgumentException("Not a selected native NBT value"));
        // A compound/list retrieved again must retain alias identity, not merely equality.
        return nbtValues.computeIfAbsent(value, raw -> switch (raw.getClass().getName()) {
            case "fy" -> new NativeNbtCompound(this, raw);
            case "ge" -> new NativeNbtList(this, raw);
            default -> new NativeNbtValue(this, raw);
        });
    }

    Object registryMap(Object registry) { return field(simple, "c", registry); }
    Object integerMap(Object registry) { return field(named, "a", registry); }
    Object putName(Object registry, Object key, Object value) {
        return call(simple, "a", new Class<?>[]{Object.class, Object.class}, registry, key, value);
    }
    Object getObject(Object registry, Object key) { return call(named, "c", new Class<?>[]{Object.class}, registry, key); }
    Object getName(Object registry, Object value) { return call(named, "b", new Class<?>[]{Object.class}, registry, value); }
    int getId(Object registry, Object value) { return (int) call(named, "a", new Class<?>[]{Object.class}, registry, value); }
    Object getById(Object registry, int id) { return call(named, "a", new Class<?>[]{int.class}, registry, id); }
    Object iterator(Object registry) { return call(named, "iterator", new Class<?>[]{}, registry); }
    void putId(Object map, Object value, int id) { call(integers, "a", new Class<?>[]{Object.class, int.class}, map, value, id); }

    private Object field(Class<?> type, String name, Object receiver) {
        requireOpen();
        try { Field field = type.getDeclaredField(name); field.setAccessible(true); return field.get(receiver); }
        catch (ReflectiveOperationException failure) { throw binding(failure); }
    }
    private Object call(Class<?> type, String name, Class<?>[] types, Object receiver, Object... args) {
        requireOpen();
        try { return type.getMethod(name, types).invoke(receiver, args); }
        catch (InvocationTargetException failure) { throw propagate(failure.getCause()); }
        catch (ReflectiveOperationException failure) { throw binding(failure); }
    }
    private static RuntimeException propagate(Throwable cause) {
        if (cause instanceof RuntimeException runtime) return runtime;
        if (cause instanceof Error error) throw error;
        return binding(cause);
    }
    private static Failure binding(Throwable failure) {
        return new Failure("execution-error", "registry.native-binding", "Native registry binding failed: " + failure);
    }
    private void requireOpen() {
        if (closed) throw new IllegalStateException("Registry runtime is closed");
    }
    @Override public void close() throws IOException {
        if (!closed) { closed = true; nbtValues.clear(); loader.close(); }
    }
}
