package dev.workbench.crucible.cleanmixtrace;

import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.instrument.Instrumentation;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.CodeSource;
import java.security.ProtectionDomain;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/** Fail-open NDJSON sink called by the exact instrumented MixinService. */
public final class CleanMixDiscoveryTraceRuntime {

    public static final String RAW_FORMAT =
        "workbench-cleanmix-defining-loader-discovery-trace-raw-v2";
    public static final String OUTPUT_PROPERTY =
        "workbench.cleanmix.discovery_trace.output";
    public static final String CAPTURE_ID_PROPERTY =
        "workbench.cleanmix.discovery_trace.capture_id";
    private static final String AGENT_ID =
        "workbench-cleanmix-defining-loader-discovery-trace-agent-v2";

    private static volatile State state;

    private CleanMixDiscoveryTraceRuntime() {
    }

    public static synchronized void start(Instrumentation instrumentation) {
        if (state != null) {
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
                new Thread(current::captureEnded, "workbench-cleanmix-discovery-trace-shutdown")
            );
        } catch (Throwable failure) {
            current.internalFailure("shutdown_hook_install", failure);
        }
    }

    public static void transformerInstalled() {
        safely(current -> current.record(
            "transformer_installed",
            null,
            null,
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

    public static void stageStart(
        String stage,
        ClassLoader definingLoader,
        String bypassPropertyName,
        String bypassPropertyValue
    ) {
        safely(current -> current.stageStart(
            stage, definingLoader, bypassPropertyName, bypassPropertyValue
        ));
    }

    public static long attemptStart(String stage, String mechanism) {
        State current = state;
        if (current == null || !current.available()) {
            return -1L;
        }
        try {
            return current.attemptStart(stage, mechanism);
        } catch (Throwable failure) {
            current.noteWriteFailure();
            return -1L;
        }
    }

    public static void providerConstructed(long attempt, String stage, Object provider) {
        safely(current -> current.record(
            "provider_constructed",
            stage,
            attempt,
            providerFields(provider)
        ));
    }

    public static void bootstrapReturned(long attempt, Object provider) {
        safely(current -> current.record(
            "bootstrap_returned",
            "bootstrap",
            attempt,
            providerFields(provider)
        ));
    }

    public static void bootstrapServiceClassName(
        long attempt,
        Object provider,
        String serviceClassName
    ) {
        Map<String, Object> payload = providerFields(provider);
        payload.put("service_class_name", serviceClassName);
        safely(current -> current.record(
            "bootstrap_service_class_name",
            "bootstrap",
            attempt,
            payload
        ));
    }

    public static void validityReturned(long attempt, Object provider, boolean valid) {
        Map<String, Object> payload = providerFields(provider);
        payload.put("valid", valid);
        safely(current -> current.record(
            "validity_returned",
            "service",
            attempt,
            payload
        ));
    }

    public static void serviceNameReturned(long attempt, Object provider, String name) {
        Map<String, Object> payload = providerFields(provider);
        payload.put("service_name", name);
        safely(current -> current.record(
            "service_name_returned",
            "service",
            attempt,
            payload
        ));
    }

    public static void attemptCompleted(long attempt, String stage, String outcome) {
        safely(current -> current.record(
            "attempt_end",
            stage,
            attempt,
            fields("outcome", outcome)
        ));
    }

    public static void attemptFailed(
        long attempt,
        String stage,
        String operation,
        Throwable failure
    ) {
        Map<String, Object> payload = failureFields(failure);
        payload.put("operation", operation);
        safely(current -> current.record("attempt_failure", stage, attempt, payload));
    }

    public static void serviceSelected(long attempt, Object provider) {
        safely(current -> current.serviceSelected(attempt, provider));
    }

    public static void stageFailure(String stage, String operation, Throwable failure) {
        Map<String, Object> payload = failureFields(failure);
        payload.put("operation", operation);
        safely(current -> current.stageFailure(stage, payload));
    }

    public static void stageEnd(String stage, String outcome) {
        safely(current -> current.stageEnd(stage, outcome));
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

    private static Map<String, Object> providerFields(Object provider) {
        Map<String, Object> result = new LinkedHashMap<>();
        if (provider == null) {
            result.put("provider_class", null);
            result.put("provider_loader_class", null);
            result.put("provider_loader_identity", null);
            result.put("provider_code_source_uri", null);
            return result;
        }
        Class<?> providerClass = provider.getClass();
        ClassLoader providerLoader = providerClass.getClassLoader();
        result.put("provider_class", providerClass.getName());
        result.put("provider_loader_class", loaderClass(providerLoader));
        result.put("provider_loader_identity", loaderIdentity(providerLoader));
        result.put("provider_code_source_uri", codeSourceUri(providerClass.getProtectionDomain()));
        return result;
    }

    private static Map<String, Object> failureFields(Throwable failure) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("exception_class", failure == null ? null : failure.getClass().getName());
        result.put("message", failure == null ? null : failure.getMessage());
        StackTraceElement[] stack = failure == null ? null : failure.getStackTrace();
        result.put(
            "stack_top",
            stack == null || stack.length == 0 ? null : stack[0].toString()
        );
        return result;
    }

    private static String loaderClass(ClassLoader loader) {
        return loader == null ? "bootstrap" : loader.getClass().getName();
    }

    private static String loaderIdentity(ClassLoader loader) {
        return loaderClass(loader) + "@" + Integer.toHexString(System.identityHashCode(loader));
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
        private final AtomicLong eventSequence = new AtomicLong();
        private final AtomicLong attemptSequence = new AtomicLong();
        private boolean closed;
        private boolean writeFailure;
        private int transformAppliedCount;
        private boolean transformFailure;
        private boolean bootstrapStarted;
        private String bootstrapOutcome;
        private boolean serviceStarted;
        private String serviceOutcome;
        private int selectedCount;
        private boolean stageFailure;

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
                    "Workbench CleanMix discovery trace unavailable: "
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
                null,
                null,
                fields(
                    "agent_id", AGENT_ID,
                    "health", "starting",
                    "java_version", System.getProperty("java.version"),
                    "redefine_supported", instrumentation.isRedefineClassesSupported(),
                    "retransform_supported", instrumentation.isRetransformClassesSupported()
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
                null,
                null,
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
                null,
                null,
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
            payload.putAll(failureFields(failure));
            record("transform_failure", null, null, payload);
        }

        synchronized void stageStart(
            String stage,
            ClassLoader definingLoader,
            String bypassPropertyName,
            String bypassPropertyValue
        ) {
            if ("bootstrap".equals(stage)) {
                bootstrapStarted = true;
            } else if ("service".equals(stage)) {
                serviceStarted = true;
            }
            record(
                "stage_start",
                stage,
                null,
                fields(
                    "defining_loader_class", loaderClass(definingLoader),
                    "defining_loader_identity", loaderIdentity(definingLoader),
                    "defining_loader_parent_class",
                        definingLoader == null ? null : loaderClass(definingLoader.getParent()),
                    "defining_loader_parent_identity",
                        definingLoader == null ? null : loaderIdentity(definingLoader.getParent()),
                    "bypass_property_name", bypassPropertyName,
                    "bypass_property_value", bypassPropertyValue,
                    "mechanism", bypassPropertyValue == null ? "service_loader" : "system_property"
                )
            );
        }

        synchronized long attemptStart(String stage, String mechanism) {
            long attempt = attemptSequence.incrementAndGet();
            record(
                "attempt_start",
                stage,
                attempt,
                fields("mechanism", mechanism)
            );
            return attempt;
        }

        synchronized void serviceSelected(long attempt, Object provider) {
            selectedCount++;
            record("provider_selected", "service", attempt, providerFields(provider));
        }

        synchronized void stageFailure(String stage, Map<String, Object> payload) {
            stageFailure = true;
            record("stage_failure", stage, null, payload);
        }

        synchronized void stageEnd(String stage, String outcome) {
            if ("bootstrap".equals(stage)) {
                bootstrapOutcome = outcome;
            } else if ("service".equals(stage)) {
                serviceOutcome = outcome;
            }
            record("stage_end", stage, null, fields("outcome", outcome));
        }

        synchronized void internalFailure(String operation, Throwable failure) {
            writeFailure = true;
            if (!available()) {
                return;
            }
            Map<String, Object> payload = failureFields(failure);
            payload.put("operation", operation);
            try {
                record("observer_failure", null, null, payload);
            } catch (Throwable ignored) {
                writeFailure = true;
            }
        }

        synchronized void noteWriteFailure() {
            writeFailure = true;
        }

        synchronized void captureEnded() {
            if (!available()) {
                return;
            }
            boolean healthy =
                !writeFailure
                    && !transformFailure
                    && transformAppliedCount == 1
                    && bootstrapStarted
                    && "completed".equals(bootstrapOutcome)
                    && serviceStarted
                    && "selected".equals(serviceOutcome)
                    && selectedCount == 1
                    && !stageFailure;
            try {
                record(
                    "capture_end",
                    null,
                    null,
                    fields(
                        "health", healthy ? "healthy" : "failed",
                        "transform_applied_count", transformAppliedCount,
                        "bootstrap_started", bootstrapStarted,
                        "bootstrap_outcome", bootstrapOutcome,
                        "service_started", serviceStarted,
                        "service_outcome", serviceOutcome,
                        "selected_count", selectedCount,
                        "stage_failure", stageFailure,
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

        synchronized void record(
            String event,
            String stage,
            Long attempt,
            Map<String, Object> payload
        ) {
            if (!available()) {
                return;
            }
            long sequence = eventSequence.getAndIncrement();
            try {
                writer.write('{');
                field(writer, "format", RAW_FORMAT, true);
                field(writer, "capture_id", captureId, false);
                field(writer, "sequence", sequence, false);
                field(writer, "event", event, false);
                field(writer, "stage", stage, false);
                field(writer, "attempt", attempt, false);
                writer.write(",\"payload\":");
                object(writer, payload);
                writer.write('}');
                writer.newLine();
                writer.flush();
            } catch (Throwable failure) {
                writeFailure = true;
                throw new IllegalStateException("could not write CleanMix discovery trace", failure);
            }
        }

        private static Map<String, Object> loaderFields(
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            String outputSha256,
            String reason
        ) {
            return fields(
                "target_class", CleanMixDiscoveryTraceAgent.TARGET_CLASS.replace('/', '.'),
                "defining_loader_class", loaderClass(loader),
                "defining_loader_identity", loaderIdentity(loader),
                "defining_loader_parent_class", loader == null ? null : loaderClass(loader.getParent()),
                "defining_loader_parent_identity", loader == null ? null : loaderIdentity(loader.getParent()),
                "target_code_source_uri", codeSourceUri(protectionDomain),
                "input_sha256", inputSha256,
                "output_sha256", outputSha256,
                "reason", reason
            );
        }

        private static void object(BufferedWriter output, Map<String, Object> value)
            throws IOException {
            output.write('{');
            boolean first = true;
            for (Map.Entry<String, Object> entry : value.entrySet()) {
                field(output, entry.getKey(), entry.getValue(), first);
                first = false;
            }
            output.write('}');
        }

        private static void field(
            BufferedWriter output,
            String name,
            Object value,
            boolean first
        ) throws IOException {
            if (!first) {
                output.write(',');
            }
            string(output, name);
            output.write(':');
            if (value == null) {
                output.write("null");
            } else if (value instanceof Boolean || value instanceof Number) {
                output.write(value.toString());
            } else {
                string(output, value.toString());
            }
        }

        private static void string(BufferedWriter output, String value) throws IOException {
            output.write('"');
            for (int index = 0; index < value.length(); index++) {
                char item = value.charAt(index);
                switch (item) {
                    case '"':
                        output.write("\\\"");
                        break;
                    case '\\':
                        output.write("\\\\");
                        break;
                    case '\b':
                        output.write("\\b");
                        break;
                    case '\f':
                        output.write("\\f");
                        break;
                    case '\n':
                        output.write("\\n");
                        break;
                    case '\r':
                        output.write("\\r");
                        break;
                    case '\t':
                        output.write("\\t");
                        break;
                    default:
                        if (item < 0x20) {
                            output.write(String.format("\\u%04x", (int) item));
                        } else {
                            output.write(item);
                        }
                }
            }
            output.write('"');
        }
    }
}
