package dev.workbench.crucible.cleanmixtrace;

import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.CodeSource;
import java.security.ProtectionDomain;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Exact Foundation final-definition observer.
 *
 * <p>The byte-guarded hook surrounds {@code ActualClassLoader.findClass}. A
 * target is sealed only after the method returns a newly defined Class and the
 * exact Foundation dump entry written from the byte array handed to
 * {@code defineClass} can be read. Cached returns are retained separately and
 * cannot satisfy a definition claim.</p>
 */
public final class FoundationFinalDefinitionRuntime {

    public static final String ENABLE_PROPERTY =
        "workbench.cleanmix.final_definition.enabled";
    public static final String OUTPUT_PROPERTY =
        "workbench.cleanmix.final_definition.output";
    public static final String CAPTURE_ID_PROPERTY =
        "workbench.cleanmix.final_definition.capture_id";
    public static final String TARGETS_PROPERTY =
        "workbench.cleanmix.final_definition.targets";
    public static final String EXPECTED_INPUT_SHA256_PROPERTY =
        "workbench.cleanmix.final_definition.expected_input_sha256";
    public static final String AGENT_ID =
        "workbench-foundation-final-definition-agent-v1";
    public static final String RAW_FORMAT =
        "workbench-foundation-final-class-definition-raw-v1";

    private static final State STATE = State.open();

    private FoundationFinalDefinitionRuntime() {
    }

    static boolean enabled() {
        return Boolean.parseBoolean(System.getProperty(ENABLE_PROPERTY, "false"));
    }

    static String expectedTargetSha256() {
        String configured = System.getProperty(EXPECTED_INPUT_SHA256_PROPERTY, "");
        if (CleanMixDiscoveryTraceAgent.LEGACY_FINAL_DEFINITION_TARGET_SHA256
                .equals(configured)) {
            return configured;
        }
        return CleanMixDiscoveryTraceAgent.FINAL_DEFINITION_TARGET_SHA256;
    }

    static void start(Instrumentation instrumentation) {
        STATE.captureStarted(instrumentation);
        Runtime.getRuntime().addShutdownHook(
            new Thread(STATE::captureEnded, "workbench-foundation-final-definition-shutdown")
        );
    }

    static void transformerInstalled() {
        STATE.transformerInstalled();
    }

    static void transformApplied(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String outputSha256
    ) {
        STATE.transformApplied(
            target, loader, domain, inputSha256, outputSha256
        );
    }

    static void transformRejected(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String reason
    ) {
        STATE.transformRejected(target, loader, domain, inputSha256, reason);
    }

    static void transformFailed(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, Throwable failure
    ) {
        STATE.transformFailed(target, loader, domain, inputSha256, failure);
    }

    public static void findStarted(Object loader, String className) {
        STATE.findStarted(loader, className);
    }

    public static void findReturned(Object loader, Class<?> definedClass) {
        STATE.findReturned(loader, definedClass);
    }

    public static void findThrew(Throwable failure, Object loader) {
        STATE.findThrew(failure, loader);
    }

    private static final class FindAttempt {
        final String className;
        final boolean target;
        final boolean cachedBefore;

        FindAttempt(String className, boolean target, boolean cachedBefore) {
            this.className = className;
            this.target = target;
            this.cachedBefore = cachedBefore;
        }
    }

    private static final class State {
        private final BufferedWriter writer;
        private final String captureId;
        private final List<String> targets;
        private final Set<String> targetSet;
        private final AtomicLong sequence = new AtomicLong();
        private final ThreadLocal<ArrayDeque<FindAttempt>> attempts =
            ThreadLocal.withInitial(ArrayDeque::new);
        private final Map<String, String> finalDigests = new LinkedHashMap<>();
        private final Map<String, Integer> finalSizes = new LinkedHashMap<>();
        private final Set<String> reobservedTargets = new LinkedHashSet<>();
        private int observerFailureCount;
        private int definitionFailureCount;
        private boolean targetTransformed;
        private boolean transformFailure;
        private boolean writeFailure;
        private boolean closed;

        private State(
            BufferedWriter writer, String captureId, List<String> targets
        ) {
            this.writer = writer;
            this.captureId = captureId;
            this.targets = targets;
            this.targetSet = new LinkedHashSet<>(targets);
        }

        static State open() {
            String output = System.getProperty(OUTPUT_PROPERTY);
            String captureId = System.getProperty(CAPTURE_ID_PROPERTY);
            List<String> targets = parseTargets(System.getProperty(TARGETS_PROPERTY));
            if (!enabled() || output == null || output.isBlank()
                    || captureId == null || captureId.isBlank() || targets.isEmpty()) {
                return new State(null, captureId, targets);
            }
            try {
                Path path = Path.of(output);
                if (!path.isAbsolute() || Files.exists(path, LinkOption.NOFOLLOW_LINKS)) {
                    return new State(null, captureId, targets);
                }
                Path parent = path.getParent();
                if (parent != null) {
                    Files.createDirectories(parent);
                }
                return new State(
                    Files.newBufferedWriter(
                        path, StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE
                    ),
                    captureId,
                    targets
                );
            } catch (Throwable ignored) {
                return new State(null, captureId, targets);
            }
        }

        private static List<String> parseTargets(String configured) {
            if (configured == null || configured.isBlank()) {
                return List.of();
            }
            Set<String> unique = new LinkedHashSet<>();
            for (String raw : configured.split(",", -1)) {
                String target = raw.trim();
                if (target.isEmpty() || target.length() > 8192
                        || target.indexOf('/') >= 0 || target.indexOf('\\') >= 0
                        || target.indexOf('\0') >= 0 || target.startsWith(".")
                        || target.endsWith(".") || target.contains("..")) {
                    return List.of();
                }
                unique.add(target);
            }
            List<String> result = new ArrayList<>(unique);
            Collections.sort(result);
            return Collections.unmodifiableList(result);
        }

        boolean available() {
            return writer != null && !closed;
        }

        synchronized void captureStarted(Instrumentation instrumentation) {
            record("capture_start", fields(
                "agent_id", AGENT_ID,
                "java_version", System.getProperty("java.version"),
                "targets", targets,
                "foundation_dump_enabled",
                    Boolean.parseBoolean(System.getProperty("foundation.dump", "false")),
                "redefine_supported", instrumentation.isRedefineClassesSupported(),
                "retransform_supported", instrumentation.isRetransformClassesSupported()
            ));
        }

        synchronized void transformerInstalled() {
            record("transformer_installed", fields(
                "target_class", CleanMixDiscoveryTraceAgent.FINAL_DEFINITION_TARGET_CLASS,
                "expected_input_sha256",
                    expectedTargetSha256(),
                "retransform_requested", false
            ));
        }

        synchronized void transformApplied(
            String target, ClassLoader loader, ProtectionDomain domain,
            String inputSha256, String outputSha256
        ) {
            targetTransformed = true;
            record("transform_applied", transformFields(
                target, loader, domain, inputSha256, outputSha256, null
            ));
        }

        synchronized void transformRejected(
            String target, ClassLoader loader, ProtectionDomain domain,
            String inputSha256, String reason
        ) {
            transformFailure = true;
            record("transform_rejected", transformFields(
                target, loader, domain, inputSha256, null, reason
            ));
        }

        synchronized void transformFailed(
            String target, ClassLoader loader, ProtectionDomain domain,
            String inputSha256, Throwable failure
        ) {
            transformFailure = true;
            Map<String, Object> payload = transformFields(
                target, loader, domain, inputSha256, null,
                "instrumentation_failure"
            );
            payload.putAll(failureFields(failure));
            record("transform_failure", payload);
        }

        void findStarted(Object loader, String className) {
            boolean target = targetSet.contains(className);
            boolean cached = false;
            if (target) {
                try {
                    Method method = loader.getClass().getMethod(
                        "isClassLoaded", String.class
                    );
                    cached = Boolean.TRUE.equals(method.invoke(loader, className));
                } catch (Throwable failure) {
                    observerFailure("class_loaded_check", failure);
                }
            }
            attempts.get().push(new FindAttempt(className, target, cached));
            if (target) {
                synchronized (this) {
                    record("find_started", fields(
                        "target_class", className,
                        "defining_loader_class", loaderClass(loader),
                        "defining_loader_identity", identity(loader),
                        "cached_before", cached
                    ));
                }
            }
        }

        void findReturned(Object loader, Class<?> definedClass) {
            FindAttempt attempt = popAttempt();
            if (attempt == null || !attempt.target) {
                return;
            }
            if (attempt.cachedBefore) {
                synchronized (this) {
                    reobservedTargets.add(attempt.className);
                    record("cached_return", fields(
                        "target_class", attempt.className,
                        "defined_class", definedClass.getName(),
                        "class_identity", identity(definedClass),
                        "defining_loader_class", loaderClass(definedClass.getClassLoader()),
                        "defining_loader_identity", identity(definedClass.getClassLoader())
                    ));
                }
                return;
            }
            try {
                recordFinalDefinition(loader, definedClass, attempt.className);
            } catch (Throwable failure) {
                observerFailure("final_definition_binding", failure);
            }
        }

        void findThrew(Throwable failure, Object loader) {
            FindAttempt attempt = popAttempt();
            if (attempt == null || !attempt.target) {
                return;
            }
            synchronized (this) {
                definitionFailureCount++;
                Map<String, Object> payload = fields(
                    "target_class", attempt.className,
                    "defining_loader_class", loaderClass(loader),
                    "defining_loader_identity", identity(loader),
                    "cached_before", attempt.cachedBefore
                );
                payload.putAll(failureFields(failure));
                record("definition_failure", payload);
            }
        }

        private FindAttempt popAttempt() {
            ArrayDeque<FindAttempt> stack = attempts.get();
            FindAttempt result = stack.isEmpty() ? null : stack.pop();
            if (stack.isEmpty()) {
                attempts.remove();
            }
            return result;
        }

        private void recordFinalDefinition(
            Object loader, Class<?> definedClass, String requestedName
        ) throws Throwable {
            if (!requestedName.equals(definedClass.getName())) {
                throw new IllegalStateException(
                    "Foundation findClass returned " + definedClass.getName()
                        + " for " + requestedName
                );
            }
            if (definedClass.getClassLoader() != loader) {
                throw new IllegalStateException(
                    "Foundation target was not defined by the observed loader"
                );
            }
            Field dumpField = loader.getClass().getSuperclass()
                .getDeclaredField("dumpSubDir");
            dumpField.setAccessible(true);
            Object rawRoot = dumpField.get(null);
            if (!(rawRoot instanceof File)) {
                throw new IllegalStateException("Foundation dump directory is unavailable");
            }
            Path root = ((File) rawRoot).toPath().toAbsolutePath().normalize();
            String relative = requestedName.replace('.', '/') + ".class";
            Path target = root.resolve(relative).normalize();
            if (!target.startsWith(root) || Files.isSymbolicLink(target)
                    || !Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException(
                    "Foundation final dump entry is unavailable: " + relative
                );
            }
            byte[] finalBytes = Files.readAllBytes(target);
            if (finalBytes.length == 0) {
                throw new IllegalStateException(
                    "Foundation final dump entry is empty: " + relative
                );
            }
            String digest = CleanMixDiscoveryTraceAgent.sha256(finalBytes);
            synchronized (this) {
                String previous = finalDigests.putIfAbsent(requestedName, digest);
                if (previous != null) {
                    throw new IllegalStateException(
                        "target received more than one non-cached definition: "
                            + requestedName
                    );
                }
                finalSizes.put(requestedName, finalBytes.length);
                record("final_definition", fields(
                    "target_class", requestedName,
                    "defined_class", definedClass.getName(),
                    "class_identity", identity(definedClass),
                    "defining_loader_class", loaderClass(definedClass.getClassLoader()),
                    "defining_loader_identity", identity(definedClass.getClassLoader()),
                    "code_source_uri", codeSourceUri(definedClass),
                    "dump_relative_path", relative,
                    "final_bytecode_sha256", digest,
                    "final_bytecode_size", finalBytes.length,
                    "cached_before", false
                ));
            }
        }

        synchronized void observerFailure(String operation, Throwable failure) {
            observerFailureCount++;
            if (!available()) {
                writeFailure = true;
                return;
            }
            try {
                Map<String, Object> payload = fields("operation", operation);
                payload.putAll(failureFields(failure));
                record("observer_failure", payload);
            } catch (Throwable ignored) {
                writeFailure = true;
            }
        }

        synchronized void captureEnded() {
            if (!available()) {
                return;
            }
            List<String> observed = new ArrayList<>(finalDigests.keySet());
            Collections.sort(observed);
            List<String> missing = new ArrayList<>(targets);
            missing.removeAll(finalDigests.keySet());
            boolean healthy = !writeFailure && !transformFailure
                && observerFailureCount == 0 && definitionFailureCount == 0
                && targetTransformed && missing.isEmpty();
            try {
                record("capture_end", fields(
                    "health", healthy ? "healthy" : "failed",
                    "requested_targets", targets,
                    "defined_targets", observed,
                    "missing_targets", missing,
                    "definition_count", finalDigests.size(),
                    "cached_return_target_count", reobservedTargets.size(),
                    "definition_failure_count", definitionFailureCount,
                    "observer_failure_count", observerFailureCount,
                    "write_failure", writeFailure
                ));
            } finally {
                try {
                    writer.close();
                } catch (IOException ignored) {
                    writeFailure = true;
                }
                closed = true;
            }
        }

        synchronized void record(String event, Map<String, Object> payload) {
            if (!available()) {
                return;
            }
            Map<String, Object> row = fields(
                "format", RAW_FORMAT,
                "capture_id", captureId,
                "sequence", sequence.getAndIncrement(),
                "event", event,
                "payload", payload
            );
            try {
                writer.write(json(row));
                writer.newLine();
                writer.flush();
            } catch (IOException failure) {
                writeFailure = true;
                throw new IllegalStateException(
                    "final-definition trace write failed", failure
                );
            }
        }
    }

    private static Map<String, Object> transformFields(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String outputSha256, String reason
    ) {
        return fields(
            "target_class", target.replace('/', '.'),
            "defining_loader_class", loaderClass(loader),
            "defining_loader_identity", identity(loader),
            "target_code_source_uri", codeSourceUri(domain),
            "input_sha256", inputSha256,
            "output_sha256", outputSha256,
            "reason", reason
        );
    }

    private static Map<String, Object> failureFields(Throwable failure) {
        StackTraceElement[] trace = failure.getStackTrace();
        return fields(
            "exception_class", failure.getClass().getName(),
            "message", String.valueOf(failure.getMessage()),
            "stack_top", trace.length == 0 ? null : trace[0].toString()
        );
    }

    private static String loaderClass(Object loader) {
        return loader == null ? "bootstrap" : loader.getClass().getName();
    }

    private static String identity(Object value) {
        return value == null ? "bootstrap" : value.getClass().getName() + "@"
            + Integer.toHexString(System.identityHashCode(value));
    }

    private static String codeSourceUri(Class<?> type) {
        try {
            ProtectionDomain domain = type.getProtectionDomain();
            return codeSourceUri(domain);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static String codeSourceUri(ProtectionDomain domain) {
        try {
            CodeSource source = domain == null ? null : domain.getCodeSource();
            URL location = source == null ? null : source.getLocation();
            return location == null ? null : location.toExternalForm();
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < values.length; index += 2) {
            result.put((String) values[index], values[index + 1]);
        }
        return result;
    }

    private static String json(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String) {
            return quote((String) value);
        }
        if (value instanceof Number || value instanceof Boolean) {
            return String.valueOf(value);
        }
        if (value instanceof Map<?, ?>) {
            StringBuilder result = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(quote((String) entry.getKey()))
                    .append(':').append(json(entry.getValue()));
            }
            return result.append('}').toString();
        }
        if (value instanceof Iterable<?>) {
            StringBuilder result = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(json(item));
            }
            return result.append(']').toString();
        }
        throw new IllegalArgumentException(
            "unsupported JSON value: " + value.getClass().getName()
        );
    }

    private static String quote(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2);
        result.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"': result.append("\\\""); break;
                case '\\': result.append("\\\\"); break;
                case '\b': result.append("\\b"); break;
                case '\f': result.append("\\f"); break;
                case '\n': result.append("\\n"); break;
                case '\r': result.append("\\r"); break;
                case '\t': result.append("\\t"); break;
                default:
                    if (character < 0x20) {
                        result.append(String.format("\\u%04x", (int) character));
                    } else {
                        result.append(character);
                    }
            }
        }
        return result.append('"').toString();
    }
}
