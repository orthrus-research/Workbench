package dev.workbench.crucible.cleanmixtrace;

import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.CodeSource;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/** Fail-open selected-service component observer for the exact CleanMix seam. */
public final class CleanMixServiceComponentRuntime {

    public static final String RAW_FORMAT =
        "workbench-cleanmix-selected-service-components-raw-v1";
    public static final String ENABLE_PROPERTY =
        "workbench.cleanmix.service_components.enabled";
    public static final String OUTPUT_PROPERTY =
        "workbench.cleanmix.service_components.output";
    public static final String CAPTURE_ID_PROPERTY =
        "workbench.cleanmix.service_components.capture_id";
    private static final String AGENT_ID =
        "workbench-cleanmix-selected-service-components-agent-v1";
    private static final Set<String> REQUIRED_ROLES = Set.of(
        "audit_trail",
        "bytecode_provider",
        "class_provider",
        "class_tracker",
        "logger",
        "service",
        "service_classloader",
        "transformer_provider"
    );

    private static volatile State state;

    private CleanMixServiceComponentRuntime() {
    }

    public static boolean enabled() {
        return Boolean.parseBoolean(System.getProperty(ENABLE_PROPERTY, "false"));
    }

    public static synchronized void start(Instrumentation instrumentation) {
        if (state != null || !enabled()) {
            return;
        }
        state = State.open();
        State current = state;
        if (!current.available()) {
            return;
        }
        current.captureStarted(instrumentation);
        try {
            Runtime.getRuntime().addShutdownHook(
                new Thread(current::captureEnded, "workbench-cleanmix-service-components-shutdown")
            );
        } catch (Throwable failure) {
            current.observerFailure("shutdown_hook_install", failure);
        }
    }

    public static void transformerInstalled() {
        safely(current -> current.record(
            "transformer_installed",
            fields(
                "target_class", CleanMixDiscoveryTraceAgent.TARGET_CLASS.replace('/', '.'),
                "expected_input_sha256", CleanMixDiscoveryTraceAgent.EXPECTED_TARGET_SHA256,
                "retransform_requested", false
            )
        ));
    }

    public static void transformApplied(
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        String outputSha256
    ) {
        safely(current -> current.transformApplied(
            loader, protectionDomain, inputSha256, outputSha256
        ));
    }

    public static void transformRejected(
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        String reason
    ) {
        safely(current -> current.transformRejected(
            loader, protectionDomain, inputSha256, reason
        ));
    }

    public static void transformFailed(
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        Throwable failure
    ) {
        safely(current -> current.transformFailed(
            loader, protectionDomain, inputSha256, failure
        ));
    }

    public static void serviceSelected(Object service) {
        safely(current -> current.serviceSelected(service));
    }

    public static void loggerObserved(Object logger) {
        safely(current -> current.observeComponent("logger", logger, reportedName(logger, "getId")));
    }

    private interface StateAction {
        void run(State current) throws Throwable;
    }

    private static void safely(StateAction action) {
        State current = state;
        if (current == null || !current.available()) {
            return;
        }
        try {
            action.run(current);
        } catch (Throwable failure) {
            current.noteWriteFailure();
        }
    }

    private static Object invoke(Object target, String methodName) throws Throwable {
        try {
            Method method = target.getClass().getMethod(methodName);
            return method.invoke(target);
        } catch (InvocationTargetException failure) {
            throw failure.getCause() == null ? failure : failure.getCause();
        }
    }

    private static String reportedName(Object target, String methodName) {
        if (target == null) {
            return null;
        }
        try {
            Object value = invoke(target, methodName);
            return value == null ? null : value.toString();
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Object fieldValue(Object target, String fieldName) throws Throwable {
        Class<?> type = target.getClass();
        while (type != null) {
            try {
                Field field = type.getDeclaredField(fieldName);
                field.setAccessible(true);
                return field.get(target);
            } catch (NoSuchFieldException ignored) {
                type = type.getSuperclass();
            }
        }
        throw new NoSuchFieldException(fieldName);
    }

    private static String loaderClass(ClassLoader loader) {
        return loader == null ? "bootstrap" : loader.getClass().getName();
    }

    private static String loaderIdentity(ClassLoader loader) {
        return loaderClass(loader) + "@" + Integer.toHexString(System.identityHashCode(loader));
    }

    private static String objectIdentity(Object value) {
        return value.getClass().getName()
            + "@" + Integer.toHexString(System.identityHashCode(value));
    }

    private static String codeSourceUri(Class<?> type) {
        try {
            ProtectionDomain protectionDomain = type.getProtectionDomain();
            CodeSource source = protectionDomain == null ? null : protectionDomain.getCodeSource();
            URL location = source == null ? null : source.getLocation();
            return location == null ? null : location.toExternalForm();
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Map<String, Object> componentFields(
        String role, Object component, String reportedName
    ) {
        if (component == null) {
            throw new IllegalStateException("component " + role + " is null");
        }
        Class<?> implementation = component.getClass();
        ClassLoader loader = implementation.getClassLoader();
        String source = codeSourceUri(implementation);
        if (source == null || source.isBlank()) {
            throw new IllegalStateException("component " + role + " lacks a code source");
        }
        return fields(
            "role", role,
            "implementation_class", implementation.getName(),
            "object_identity", objectIdentity(component),
            "implementation_loader_class", loaderClass(loader),
            "implementation_loader_identity", loaderIdentity(loader),
            "code_source_uri", source,
            "reported_name", reportedName
        );
    }

    private static Map<String, Object> failureFields(
        String role, String operation, Throwable failure
    ) {
        return fields(
            "role", role,
            "operation", operation,
            "exception_class", failure == null ? "java.lang.Throwable" : failure.getClass().getName(),
            "message", failure == null ? null : failure.getMessage()
        );
    }

    private static Map<String, Object> loaderFields(
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        String outputSha256,
        String reason
    ) {
        return fields(
            "defining_loader_class", loaderClass(loader),
            "defining_loader_identity", loaderIdentity(loader),
            "target_code_source_uri", codeSourceUri(protectionDomain),
            "input_sha256", inputSha256,
            "output_sha256", outputSha256,
            "reason", reason
        );
    }

    private static String codeSourceUri(ProtectionDomain protectionDomain) {
        try {
            CodeSource source = protectionDomain == null ? null : protectionDomain.getCodeSource();
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

    private static final class State {
        private final String captureId;
        private final BufferedWriter writer;
        private final AtomicLong sequence = new AtomicLong();
        private final Set<String> observedRoles = new LinkedHashSet<>();
        private final Set<String> failedRoles = new LinkedHashSet<>();
        private Object selectedService;
        private boolean closed;
        private boolean writeFailure;
        private boolean transformFailure;
        private int transformAppliedCount;

        private State(String captureId, BufferedWriter writer) {
            this.captureId = captureId;
            this.writer = writer;
        }

        static State open() {
            String captureId = System.getProperty(CAPTURE_ID_PROPERTY, "unbound");
            String output = System.getProperty(OUTPUT_PROPERTY);
            if (captureId == null || captureId.isBlank() || output == null || output.isBlank()) {
                return new State(captureId == null ? "unbound" : captureId, null);
            }
            try {
                Path path = Paths.get(output);
                BufferedWriter writer = Files.newBufferedWriter(
                    path,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE
                );
                return new State(captureId, writer);
            } catch (Throwable failure) {
                System.err.println(
                    "Workbench CleanMix service-component capture unavailable: "
                        + failure.getClass().getName() + ": " + failure.getMessage()
                );
                return new State(captureId, null);
            }
        }

        boolean available() {
            return writer != null && !closed;
        }

        synchronized void captureStarted(Instrumentation instrumentation) {
            record(
                "capture_start",
                fields(
                    "agent_id", AGENT_ID,
                    "java_version", System.getProperty("java.version"),
                    "target_class", CleanMixDiscoveryTraceAgent.TARGET_CLASS.replace('/', '.'),
                    "expected_input_sha256", CleanMixDiscoveryTraceAgent.EXPECTED_TARGET_SHA256
                )
            );
        }

        synchronized void transformApplied(
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            String outputSha256
        ) {
            transformAppliedCount++;
            record(
                "transform_applied",
                loaderFields(loader, protectionDomain, inputSha256, outputSha256, null)
            );
        }

        synchronized void transformRejected(
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            String reason
        ) {
            transformFailure = true;
            record(
                "transform_rejected",
                loaderFields(loader, protectionDomain, inputSha256, null, reason)
            );
        }

        synchronized void transformFailed(
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            Throwable failure
        ) {
            transformFailure = true;
            Map<String, Object> payload = loaderFields(
                loader, protectionDomain, inputSha256, null, "payload_load_failed"
            );
            payload.put("exception_class", failure.getClass().getName());
            payload.put("message", failure.getMessage());
            record("transform_failure", payload);
        }

        synchronized void serviceSelected(Object service) {
            if (selectedService != null) {
                componentFailure(
                    "service",
                    "duplicate_selection",
                    new IllegalStateException("selected service was observed more than once")
                );
                return;
            }
            selectedService = service;
            observeComponent("service", service, reportedName(service, "getName"));
            observeComponent("service_classloader", service.getClass().getClassLoader(), null);
            observeGetter("class_provider", service, "getClassProvider");
            observeGetter("bytecode_provider", service, "getBytecodeProvider");
            observeGetter("transformer_provider", service, "getTransformerProvider");
            observeGetter("class_tracker", service, "getClassTracker");
        }

        synchronized void observeGetter(String role, Object service, String method) {
            try {
                observeComponent(role, invoke(service, method), null);
            } catch (Throwable failure) {
                componentFailure(role, method, failure);
            }
        }

        synchronized void observeComponent(
            String role, Object component, String reportedName
        ) {
            if (observedRoles.contains(role) || failedRoles.contains(role)) {
                componentFailure(
                    role,
                    "duplicate_observation",
                    new IllegalStateException("component role was observed more than once")
                );
                return;
            }
            try {
                record("component_observed", componentFields(role, component, reportedName));
                observedRoles.add(role);
            } catch (Throwable failure) {
                componentFailure(role, "component_identity", failure);
            }
        }

        synchronized void componentFailure(String role, String operation, Throwable failure) {
            if (!observedRoles.contains(role)) {
                failedRoles.add(role);
            }
            record("component_failure", failureFields(role, operation, failure));
        }

        synchronized void observerFailure(String operation, Throwable failure) {
            writeFailure = true;
            if (available()) {
                record(
                    "observer_failure",
                    fields(
                        "operation", operation,
                        "exception_class", failure.getClass().getName(),
                        "message", failure.getMessage()
                    )
                );
            }
        }

        synchronized void noteWriteFailure() {
            writeFailure = true;
        }

        synchronized void captureEnded() {
            if (!available()) {
                return;
            }
            if (selectedService == null) {
                componentFailure(
                    "service",
                    "selection_not_observed",
                    new IllegalStateException("selected service object was not observed")
                );
            } else if (!observedRoles.contains("audit_trail")
                    && !failedRoles.contains("audit_trail")) {
                try {
                    Object auditTrail = fieldValue(selectedService, "auditTrail");
                    if (auditTrail == null) {
                        throw new IllegalStateException(
                            "auditTrail was not initialized by the normal runtime lifecycle"
                        );
                    }
                    observeComponent("audit_trail", auditTrail, null);
                } catch (Throwable failure) {
                    componentFailure("audit_trail", "read_initialized_field", failure);
                }
            }

            boolean healthy =
                !writeFailure
                    && !transformFailure
                    && transformAppliedCount == 1
                    && failedRoles.isEmpty()
                    && observedRoles.equals(REQUIRED_ROLES);
            List<String> observed = new ArrayList<>(observedRoles);
            List<String> failed = new ArrayList<>(failedRoles);
            Collections.sort(observed);
            Collections.sort(failed);
            try {
                record(
                    "capture_end",
                    fields(
                        "health", healthy ? "healthy" : "failed",
                        "transform_applied_count", transformAppliedCount,
                        "observed_roles", observed,
                        "failed_roles", failed,
                        "write_failure", writeFailure
                    )
                );
            } catch (Throwable failure) {
                writeFailure = true;
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
                throw new IllegalStateException("component trace write failed", failure);
            }
        }
    }

    private static String json(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String) {
            return quote((String) value);
        }
        if (value instanceof Boolean || value instanceof Number) {
            return value.toString();
        }
        if (value instanceof Map<?, ?> map) {
            StringBuilder result = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(quote(entry.getKey().toString()));
                result.append(':');
                result.append(json(entry.getValue()));
            }
            return result.append('}').toString();
        }
        if (value instanceof Iterable<?> iterable) {
            StringBuilder result = new StringBuilder("[");
            boolean first = true;
            for (Object item : iterable) {
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(json(item));
            }
            return result.append(']').toString();
        }
        return quote(value.toString());
    }

    private static String quote(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (character < 0x20) {
                        result.append(String.format("\\u%04x", (int) character));
                    } else {
                        result.append(character);
                    }
                }
            }
        }
        return result.append('"').toString();
    }
}
