package research.orthrus.axiom;

import java.io.IOException;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

/** Disposable native event class space, not a mod loader or a security sandbox.
 * The transformer itself belongs to this loader: superclass discovery must see
 * the same candidate definitions that receive the transformations. */
final class NativeEventSpace extends ClassLoader {
    static final String PREFIX = "research.orthrus.axiom.nativeevents.";
    static final Set<String> MATERIAL_EVENTS = Set.of("research.orthrus.axiom.materialevents.MaterialRegistryEvent",
            "research.orthrus.axiom.materialevents.MaterialEvent", "research.orthrus.axiom.materialevents.PostMaterialEvent");
    private final Map<String, byte[]> definitions;
    private final Map<String, Map<String, String>> transformations = new LinkedHashMap<>();
    private final Object subscription, subscriber;
    private final Method subscriptionMethod, subscriberMethod;
    private boolean ready;

    NativeEventSpace(Map<String, byte[]> input) {
        super(NativeEventSpace.class.getClassLoader());
        NativeRuntime.require();
        verifyLibraries();
        if (input.size() > 10_000) throw Failure.request("Too many event-space classes");
        Map<String, byte[]> copy = new LinkedHashMap<>();
        long size = 0;
        for (var row : input.entrySet()) {
            String name = row.getKey();
            if (!name.matches("[A-Za-z_$][A-Za-z0-9_$]*(\\.[A-Za-z_$][A-Za-z0-9_$]*)+") ||
                    name.startsWith("java.") || name.startsWith("javax.") || name.startsWith("jdk.") ||
                    name.startsWith("sun.") || name.startsWith("research.orthrus.axiom."))
                throw Failure.request("Reserved or invalid event-space class: " + name);
            byte[] bytes = Objects.requireNonNull(row.getValue()).clone();
            size += bytes.length;
            if (bytes.length > 1_048_576 || size > 64L * 1_048_576)
                throw Failure.request("Event-space byte bound exceeded");
            copy.put(name, bytes);
        }
        definitions = Collections.unmodifiableMap(copy);
        try {
            Class<?> first = loadClass(PREFIX + "EventSubscriptionTransformer");
            Class<?> second = loadClass(PREFIX + "EventSubscriberTransformer");
            subscription = first.getConstructor().newInstance();
            subscriber = second.getConstructor().newInstance();
            subscriptionMethod = first.getMethod("transform", String.class, String.class, byte[].class);
            subscriberMethod = second.getMethod("transform", String.class, String.class, byte[].class);
            ready = true;
        } catch (ReflectiveOperationException failure) { throw binding(failure); }
    }

    @Override protected synchronized Class<?> loadClass(String name, boolean resolve) throws ClassNotFoundException {
        Class<?> type = findLoadedClass(name);
        if (type == null) {
            if (name.startsWith(PREFIX) || MATERIAL_EVENTS.contains(name) || definitions.containsKey(name)) type = findClass(name);
            else type = super.loadClass(name, false);
        }
        if (resolve) resolveClass(type);
        return type;
    }

    @Override protected Class<?> findClass(String name) throws ClassNotFoundException {
        byte[] bytes = definitions.get(name);
        if (bytes == null && (name.startsWith(PREFIX) || MATERIAL_EVENTS.contains(name))) {
            try (var stream = getParent().getResourceAsStream(name.replace('.', '/') + ".class")) {
                if (stream == null) throw new ClassNotFoundException(name);
                bytes = Main.read(stream, 1_048_576);
            } catch (IOException failure) { throw new ClassNotFoundException(name, failure); }
        }
        if (bytes == null) throw new ClassNotFoundException(name);
        String before = Json.bytesDigest(bytes);
        // FMLCorePlugin's selected order. The framework's own implementation is
        // initialized before either transformer can recursively use it.
        if (ready && (!name.startsWith(PREFIX) || name.equals(PREFIX + "GenericEvent"))) {
            bytes = transform(subscriptionMethod, subscription, name, bytes);
            bytes = transform(subscriberMethod, subscriber, name, bytes);
        }
        transformations.put(name, Map.of("inputSha256", before, "outputSha256", Json.bytesDigest(bytes)));
        return defineClass(name, bytes, 0, bytes.length);
    }

    Map<String, Map<String, String>> transformations() { return Collections.unmodifiableMap(new LinkedHashMap<>(transformations)); }

    private static byte[] transform(Method method, Object transformer, String name, byte[] bytes) {
        try { return (byte[]) method.invoke(transformer, name, name, bytes); }
        catch (InvocationTargetException failure) {
            if (failure.getCause() instanceof RuntimeException runtime) throw runtime;
            if (failure.getCause() instanceof Error error) throw error;
            throw binding(failure.getCause());
        } catch (ReflectiveOperationException failure) { throw binding(failure); }
    }

    private static void verifyLibraries() {
        var lock = Json.object(Json.parse(Target.resource("/axiom/cleanroom-events.lock.json")));
        Map<String, String> expected = new HashMap<>();
        for (Object value : Json.array(lock.get("oracleLibraries"))) {
            var row = Json.object(value);
            expected.put(Path.of(Json.string(row.get("path"))).getFileName().toString(), Json.string(row.get("sha256")));
        }
        for (Class<?> type : List.of(com.google.common.reflect.TypeToken.class,
                com.google.common.util.concurrent.internal.InternalFutureFailureAccess.class,
                org.jspecify.annotations.NonNull.class, org.objectweb.asm.ClassReader.class,
                org.objectweb.asm.tree.ClassNode.class, org.apache.logging.log4j.LogManager.class,
                org.apache.logging.log4j.core.Logger.class)) {
            try {
                Path path = Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI());
                if (!Objects.equals(expected.get(path.getFileName().toString()), Json.bytesDigest(Files.readAllBytes(path))))
                    throw Failure.request("Loaded event library differs from selected platform: " + type.getName());
            } catch (IOException | java.net.URISyntaxException failure) { throw binding(failure); }
        }
    }

    private static Failure binding(Throwable failure) {
        return new Failure("execution-error", "events.native-binding", "Native event binding failed: " + failure);
    }
}
