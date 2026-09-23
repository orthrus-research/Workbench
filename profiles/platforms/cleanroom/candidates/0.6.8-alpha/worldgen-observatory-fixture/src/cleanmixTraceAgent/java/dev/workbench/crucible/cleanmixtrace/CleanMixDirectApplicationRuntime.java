package dev.workbench.crucible.cleanmixtrace;

import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.instrument.Instrumentation;
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

/** Exact, opt-in observation of Mixin target application. */
public final class CleanMixDirectApplicationRuntime {

    public static final String ENABLE_PROPERTY =
        "workbench.cleanmix.direct_application.enabled";
    public static final String OUTPUT_PROPERTY =
        "workbench.cleanmix.direct_application.output";
    public static final String CAPTURE_ID_PROPERTY =
        "workbench.cleanmix.direct_application.capture_id";
    public static final String TARGETS_PROPERTY =
        "workbench.cleanmix.direct_application.targets";
    public static final String AGENT_ID =
        "workbench-cleanmix-direct-application-agent-v1";
    public static final String RAW_FORMAT =
        "workbench-cleanmix-direct-application-raw-v1";

    private static final State STATE = State.open();

    private CleanMixDirectApplicationRuntime() {
    }

    static boolean enabled() {
        return Boolean.parseBoolean(System.getProperty(ENABLE_PROPERTY, "false"));
    }

    static void start(Instrumentation instrumentation) {
        STATE.captureStarted(instrumentation);
        Runtime.getRuntime().addShutdownHook(
            new Thread(STATE::captureEnded, "workbench-cleanmix-direct-application-shutdown")
        );
    }

    static void transformerInstalled() {
        STATE.transformerInstalled();
    }

    static void transformApplied(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String outputSha256
    ) {
        STATE.transformApplied(target, loader, domain, inputSha256, outputSha256);
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

    public static void applicationStarted(String targetName, Object mixins) {
        try {
            STATE.applicationStarted(targetName, mixins);
        } catch (Throwable observerFailure) {
            suppressObserverFailure("application_started_callback", observerFailure);
        }
    }

    public static void applicationCompleted(String targetName, Object mixins) {
        try {
            STATE.applicationCompleted(targetName, mixins);
        } catch (Throwable observerFailure) {
            suppressObserverFailure("application_completed_callback", observerFailure);
        }
    }

    public static void applicationThrew(
        Throwable failure, String targetName, Object mixins
    ) {
        try {
            STATE.applicationThrew(failure, targetName, mixins);
        } catch (Throwable observerFailure) {
            suppressObserverFailure("application_threw_callback", observerFailure);
        }
    }

    private static void suppressObserverFailure(
        String operation, Throwable observerFailure
    ) {
        try {
            STATE.observerFailure(operation, observerFailure);
        } catch (Throwable ignored) {
            // Evidence becomes unavailable; never replace the observed outcome.
        }
    }

    private static final class Attempt {
        final long applicationId;
        final String targetName;
        final List<String> mixins;
        final boolean requested;

        Attempt(
            long applicationId, String targetName, List<String> mixins,
            boolean requested
        ) {
            this.applicationId = applicationId;
            this.targetName = targetName;
            this.mixins = mixins;
            this.requested = requested;
        }
    }

    private static final class State {
        private final BufferedWriter writer;
        private final String captureId;
        private final List<String> targets;
        private final Set<String> targetSet;
        private final AtomicLong sequence = new AtomicLong();
        private final AtomicLong applicationSequence = new AtomicLong(1);
        private final ThreadLocal<ArrayDeque<Attempt>> attempts =
            ThreadLocal.withInitial(ArrayDeque::new);
        private final Map<String, Integer> started = new LinkedHashMap<>();
        private final Map<String, Integer> completed = new LinkedHashMap<>();
        private int applicationFailureCount;
        private int observerFailureCount;
        private boolean targetTransformed;
        private boolean transformFailure;
        private boolean writeFailure;
        private boolean closed;

        private State(BufferedWriter writer, String captureId, List<String> targets) {
            this.writer = writer;
            this.captureId = captureId;
            this.targets = targets;
            this.targetSet = new LinkedHashSet<>(targets);
            for (String target : targets) {
                started.put(target, 0);
                completed.put(target, 0);
            }
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
                "redefine_supported", instrumentation.isRedefineClassesSupported(),
                "retransform_supported", instrumentation.isRetransformClassesSupported()
            ));
        }

        synchronized void transformerInstalled() {
            record("transformer_installed", fields(
                "target_class", CleanMixDiscoveryTraceAgent.DIRECT_APPLICATION_TARGET_CLASS,
                "expected_input_sha256",
                    CleanMixDiscoveryTraceAgent.DIRECT_APPLICATION_TARGET_SHA256,
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
                target, loader, domain, inputSha256, null, "instrumentation_failure"
            );
            payload.putAll(failureFields(failure));
            record("transform_failure", payload);
        }

        void applicationStarted(String targetName, Object rawMixins) {
            List<String> mixins;
            try {
                mixins = mixinNames(rawMixins);
            } catch (Throwable failure) {
                observerFailure("mixin_identity", failure);
                mixins = List.of("<observer-failure>");
            }
            boolean requested = targetSet.contains(targetName);
            Attempt attempt = new Attempt(
                applicationSequence.getAndIncrement(), targetName, mixins, requested
            );
            attempts.get().push(attempt);
            if (!requested) {
                return;
            }
            synchronized (this) {
                started.put(targetName, started.get(targetName) + 1);
                record("application_started", fields(
                    "application_id", attempt.applicationId,
                    "target_class", targetName,
                    "mixins", mixins,
                    "thread_name", Thread.currentThread().getName()
                ));
            }
        }

        void applicationCompleted(String targetName, Object ignoredMixins) {
            Attempt attempt = popAttempt();
            if (attempt == null) {
                observerFailure(
                    "application_stack",
                    new IllegalStateException("application return lacks a matching start")
                );
                return;
            }
            if (!attempt.targetName.equals(targetName)) {
                observerFailure(
                    "application_stack",
                    new IllegalStateException("application target changed before return")
                );
                return;
            }
            if (!attempt.requested) {
                return;
            }
            synchronized (this) {
                completed.put(targetName, completed.get(targetName) + 1);
                record("application_completed", fields(
                    "application_id", attempt.applicationId,
                    "target_class", targetName,
                    "mixins", attempt.mixins,
                    "thread_name", Thread.currentThread().getName()
                ));
            }
        }

        void applicationThrew(
            Throwable failure, String targetName, Object ignoredMixins
        ) {
            Attempt attempt = popAttempt();
            if (attempt == null || !attempt.requested) {
                return;
            }
            synchronized (this) {
                applicationFailureCount++;
                Map<String, Object> payload = fields(
                    "application_id", attempt.applicationId,
                    "target_class", targetName,
                    "mixins", attempt.mixins,
                    "thread_name", Thread.currentThread().getName()
                );
                payload.putAll(failureFields(failure));
                record("application_failure", payload);
            }
        }

        private Attempt popAttempt() {
            ArrayDeque<Attempt> stack = attempts.get();
            Attempt result = stack.isEmpty() ? null : stack.pop();
            if (stack.isEmpty()) {
                attempts.remove();
            }
            return result;
        }

        synchronized void observerFailure(String operation, Throwable failure) {
            observerFailureCount++;
            if (!available()) {
                writeFailure = true;
                return;
            }
            Map<String, Object> payload = fields("operation", operation);
            payload.putAll(failureFields(failure));
            record("observer_failure", payload);
        }

        synchronized void captureEnded() {
            if (!available()) {
                return;
            }
            List<String> missing = new ArrayList<>();
            List<String> repeated = new ArrayList<>();
            for (String target : targets) {
                if (completed.get(target) == 0) {
                    missing.add(target);
                }
                if (started.get(target) != 1 || completed.get(target) != 1) {
                    repeated.add(target);
                }
            }
            boolean healthy = !writeFailure && !transformFailure
                && observerFailureCount == 0 && applicationFailureCount == 0
                && targetTransformed && missing.isEmpty() && repeated.isEmpty();
            try {
                record("capture_end", fields(
                    "health", healthy ? "healthy" : "failed",
                    "requested_targets", targets,
                    "started_counts", orderedCounts(started),
                    "completed_counts", orderedCounts(completed),
                    "missing_targets", missing,
                    "non_singleton_targets", repeated,
                    "application_failure_count", applicationFailureCount,
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

        private List<Map<String, Object>> orderedCounts(Map<String, Integer> counts) {
            List<Map<String, Object>> result = new ArrayList<>();
            for (String target : targets) {
                result.add(fields("target_class", target, "count", counts.get(target)));
            }
            return result;
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
                closed = true;
                try {
                    writer.close();
                } catch (IOException ignored) {
                    // The failed raw stream remains inadmissible without a footer.
                }
            }
        }
    }

    private static List<String> mixinNames(Object rawMixins) throws Exception {
        if (!(rawMixins instanceof Iterable<?>)) {
            throw new IllegalArgumentException("mixin collection is not iterable");
        }
        List<String> result = new ArrayList<>();
        for (Object mixin : (Iterable<?>) rawMixins) {
            Method accessor = null;
            for (Class<?> type : mixin.getClass().getInterfaces()) {
                if (type.getName().equals(
                        "org.spongepowered.asm.mixin.extensibility.IMixinInfo")) {
                    accessor = type.getMethod("getClassName");
                    break;
                }
            }
            if (accessor == null) {
                throw new IllegalStateException("mixin lacks IMixinInfo identity");
            }
            Object name = accessor.invoke(mixin);
            if (!(name instanceof String) || ((String) name).isBlank()) {
                throw new IllegalStateException("mixin class name is unavailable");
            }
            result.add((String) name);
        }
        if (result.isEmpty()) {
            throw new IllegalStateException("target application has no mixins");
        }
        return Collections.unmodifiableList(result);
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
