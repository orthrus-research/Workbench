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
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/** Fail-open observer for CleanMix configuration admission and phase custody. */
public final class CleanMixConfigLifecycleRuntime {

    public static final String RAW_FORMAT =
        "workbench-cleanmix-config-lifecycle-raw-v1";
    public static final String ENABLE_PROPERTY =
        "workbench.cleanmix.config_lifecycle.enabled";
    public static final String OUTPUT_PROPERTY =
        "workbench.cleanmix.config_lifecycle.output";
    public static final String CAPTURE_ID_PROPERTY =
        "workbench.cleanmix.config_lifecycle.capture_id";
    private static final String AGENT_ID =
        "workbench-cleanmix-config-lifecycle-agent-v1";

    private static volatile State state;

    private CleanMixConfigLifecycleRuntime() {
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
                new Thread(current::captureEnded, "workbench-cleanmix-config-lifecycle-shutdown")
            );
        } catch (Throwable failure) {
            current.observerFailure("shutdown_hook_install", failure);
        }
    }

    public static void transformerInstalled() {
        safely(State::transformerInstalled);
    }

    public static void transformApplied(
        String target,
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        String outputSha256
    ) {
        safely(current -> current.transformApplied(
            target, loader, protectionDomain, inputSha256, outputSha256
        ));
    }

    public static void transformRejected(
        String target,
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        String reason
    ) {
        safely(current -> current.transformRejected(
            target, loader, protectionDomain, inputSha256, reason
        ));
    }

    public static void transformFailed(
        String target,
        ClassLoader loader,
        ProtectionDomain protectionDomain,
        String inputSha256,
        Throwable failure
    ) {
        safely(current -> current.transformFailed(
            target, loader, protectionDomain, inputSha256, failure
        ));
    }

    public static void configCreateStarted(
        String requestedConfig, Object fallbackEnvironment, Object suppliedSource
    ) {
        safely(current -> current.configCreateStarted(
            requestedConfig, fallbackEnvironment, suppliedSource
        ));
    }

    public static void resourceLocated(Object resource, String requestedConfig) {
        safely(current -> current.resourceLocated(resource, requestedConfig));
    }

    public static void featureCheckStarted(Object config) {
        safely(current -> current.featureCheckStarted(config));
    }

    public static void featureCheckReturned(boolean passed, Object config) {
        safely(current -> current.featureCheckReturned(passed, config));
    }

    public static void featureCheckThrew(Throwable failure, Object config) {
        safely(current -> current.featureCheckThrew(failure, config));
    }

    public static void configCreateReturned(Object config, String requestedConfig) {
        safely(current -> current.configCreateReturned(config, requestedConfig));
    }

    public static void configCreateThrew(Throwable failure, String requestedConfig) {
        safely(current -> current.configCreateThrew(failure, requestedConfig));
    }

    public static void registrationStarted(Object config) {
        safely(current -> current.registrationStarted(config));
    }

    public static void registrationReturned(Object config) {
        safely(current -> current.registrationReturned(config));
    }

    public static void registrationThrew(Throwable failure, Object config) {
        safely(current -> current.registrationThrew(failure, config));
    }

    public static void phasePassStarted(Object environment) {
        safely(current -> current.phasePassStarted(environment));
    }

    public static void phaseEligibility(boolean eligible, Object configHandle) {
        safely(current -> current.phaseEligibility(eligible, configHandle));
    }

    public static void phasePassReturned(Object environment) {
        safely(current -> current.phasePassReturned(environment));
    }

    public static void phasePassThrew(Throwable failure, Object environment) {
        safely(current -> current.phasePassThrew(failure, environment));
    }

    public static void stageStarted(Object config, String stage) {
        safely(current -> current.stage(config, stage, "started", null));
    }

    public static void stageReturned(Object config, String stage) {
        safely(current -> current.stage(config, stage, "completed", null));
    }

    public static void stageThrew(Throwable failure, Object config, String stage) {
        safely(current -> current.stage(config, stage, "threw", failure));
    }

    public static void batchPromoted(Object batch, Object environment) {
        safely(current -> current.batchPromoted(batch, environment));
    }

    public static void batchThrew(Throwable failure, Object batch, Object environment) {
        safely(current -> current.batchThrew(failure, batch, environment));
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
            current.observerFailure("callback", failure);
        }
    }

    private static Object invoke(Object target, String methodName, Object... arguments)
        throws Throwable {
        if (target == null) {
            return null;
        }
        Method selected = null;
        for (Method method : target.getClass().getMethods()) {
            if (method.getName().equals(methodName)
                    && method.getParameterCount() == arguments.length) {
                selected = method;
                break;
            }
        }
        if (selected == null) {
            throw new NoSuchMethodException(target.getClass().getName() + "." + methodName);
        }
        try {
            selected.setAccessible(true);
            return selected.invoke(target, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause() == null ? failure : failure.getCause();
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

    private static String value(Object item) {
        return item == null ? null : item.toString();
    }

    private static String configName(Object config) {
        try {
            return value(invoke(config, "getName"));
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Object apiConfig(Object config) {
        if (config == null) {
            return null;
        }
        try {
            return invoke(config, "getConfig");
        } catch (Throwable ignored) {
            return config;
        }
    }

    private static String configPhase(Object config) {
        try {
            Object environment = invoke(config, "getEnvironment");
            return phase(environment);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static String phase(Object environment) {
        try {
            return value(invoke(environment, "getPhase"));
        } catch (Throwable ignored) {
            return value(environment);
        }
    }

    private static Boolean required(Object config) {
        try {
            Object result = invoke(apiConfig(config), "isRequired");
            return result instanceof Boolean ? (Boolean) result : null;
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Map<String, Object> sourceFields(Object source) {
        String id = null;
        String description = null;
        if (source != null) {
            try {
                id = value(invoke(source, "getId"));
            } catch (Throwable ignored) {
            }
            try {
                description = value(invoke(source, "getDescription"));
            } catch (Throwable ignored) {
            }
        }
        return fields("source_id", id, "source_description", description);
    }

    private static Object configSource(Object config) {
        try {
            return invoke(config, "getSource");
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static List<Map<String, Object>> featureStates(Object config) {
        List<Map<String, Object>> result = new ArrayList<>();
        try {
            Object raw = fieldValue(config, "requiredFeatures");
            if (!(raw instanceof Iterable<?> features)) {
                return result;
            }
            ClassLoader loader = config.getClass().getClassLoader();
            Class<?> featureClass = Class.forName(
                "org.spongepowered.asm.mixin.MixinEnvironment$Feature", false, loader
            );
            Method isActive = featureClass.getMethod("isActive", String.class);
            for (Object feature : features) {
                String id = value(feature);
                String normalized = id == null
                    ? null : id.trim().toUpperCase(Locale.ROOT);
                Object active = normalized == null ? null : isActive.invoke(null, normalized);
                result.add(fields("id", id, "normalized_id", normalized, "active", active));
            }
        } catch (Throwable failure) {
            result.add(fields(
                "id", "observer-unavailable",
                "normalized_id", "OBSERVER-UNAVAILABLE",
                "active", null
            ));
        }
        return result;
    }

    private static String identity(Object item) {
        return item == null ? null : item.getClass().getName() + "@"
            + Integer.toHexString(System.identityHashCode(item));
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

    private static Map<String, Object> failureFields(Throwable failure) {
        return fields(
            "exception_class", failure == null ? null : failure.getClass().getName(),
            "message", failure == null ? null : failure.getMessage(),
            "stack_top", failure == null || failure.getStackTrace().length == 0
                ? null : failure.getStackTrace()[0].toString()
        );
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < values.length; index += 2) {
            result.put((String) values[index], values[index + 1]);
        }
        return result;
    }

    private static final class Request {
        final long attempt;
        final String requestedConfig;
        String resourceUrl;
        String resourceUrlBasis;
        String createOutcome;
        String admissionDecision;
        String lastDeferralReason;
        String terminalOutcome;
        String terminalReason;
        String lastEligibilityKey;
        String configName;
        Object handle;
        boolean featureCheckPassed = true;

        Request(long attempt, String requestedConfig) {
            this.attempt = attempt;
            this.requestedConfig = requestedConfig;
        }
    }

    private static final class Registration {
        final Request request;
        final Object config;
        final boolean duplicate;

        Registration(Request request, Object config, boolean duplicate) {
            this.request = request;
            this.config = config;
            this.duplicate = duplicate;
        }
    }

    private static final class State {
        private final String captureId;
        private final BufferedWriter writer;
        private final AtomicLong sequence = new AtomicLong();
        private final AtomicLong attemptSequence = new AtomicLong();
        private final ThreadLocal<Deque<Request>> requestStack =
            ThreadLocal.withInitial(ArrayDeque::new);
        private final ThreadLocal<Request> latestRequest = new ThreadLocal<>();
        private final ThreadLocal<Deque<Registration>> registrationStack =
            ThreadLocal.withInitial(ArrayDeque::new);
        private final ThreadLocal<Deque<String>> phaseStack =
            ThreadLocal.withInitial(ArrayDeque::new);
        private final ThreadLocal<Deque<Boolean>> phaseRecordStack =
            ThreadLocal.withInitial(ArrayDeque::new);
        private final Map<Object, Request> requestByHandle = new IdentityHashMap<>();
        private final Map<String, Request> admittedByName = new LinkedHashMap<>();
        private final List<Request> requests = new ArrayList<>();
        private final Set<String> transformedTargets = new LinkedHashSet<>();
        private boolean closed;
        private boolean writeFailure;
        private boolean transformFailure;
        private int observerFailureCount;
        private int failureCount;
        private String lastRecordedPhasePass;

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
                    "Workbench CleanMix config lifecycle unavailable: "
                        + failure.getClass().getName() + ": " + failure.getMessage()
                );
                return new State(captureId, null);
            }
        }

        boolean available() {
            return writer != null && !closed;
        }

        synchronized void captureStarted(Instrumentation instrumentation) {
            List<Map<String, Object>> targets = new ArrayList<>();
            for (Map.Entry<String, String> entry
                    : CleanMixDiscoveryTraceAgent.lifecycleTargetsForReceipt().entrySet()) {
                targets.add(fields(
                    "target_class", entry.getKey().replace('/', '.'),
                    "expected_input_sha256", entry.getValue()
                ));
            }
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
                "target_count", CleanMixDiscoveryTraceAgent
                    .lifecycleTargetsForReceipt().size(),
                "retransform_requested", false
            ));
        }

        synchronized void transformApplied(
            String target,
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            String outputSha256
        ) {
            transformedTargets.add(target);
            record("transform_applied", transformFields(
                target, loader, protectionDomain, inputSha256, outputSha256, null
            ));
        }

        synchronized void transformRejected(
            String target,
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            String reason
        ) {
            transformFailure = true;
            record("transform_rejected", transformFields(
                target, loader, protectionDomain, inputSha256, null, reason
            ));
        }

        synchronized void transformFailed(
            String target,
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            Throwable failure
        ) {
            transformFailure = true;
            Map<String, Object> payload = transformFields(
                target, loader, protectionDomain, inputSha256, null, "weave_failed"
            );
            payload.putAll(failureFields(failure));
            record("transform_failure", payload);
        }

        synchronized void configCreateStarted(
            String requestedConfig, Object fallbackEnvironment, Object suppliedSource
        ) {
            Request request = new Request(
                attemptSequence.incrementAndGet(), requestedConfig
            );
            Map<String, Object> suppliedSourceFields = sourceFields(suppliedSource);
            Object description = suppliedSourceFields.get("source_description");
            if (description instanceof String sourceDescription
                    && sourceDescription.startsWith("file:")
                    && sourceDescription.endsWith(".jar")) {
                request.resourceUrl = "jar:" + sourceDescription + "!/" + requestedConfig;
                request.resourceUrlBasis =
                    "config_source_description_plus_requested_path";
            }
            requests.add(request);
            requestStack.get().push(request);
            Map<String, Object> payload = fields(
                "attempt", request.attempt,
                "requested_config", requestedConfig,
                "fallback_phase", phase(fallbackEnvironment)
            );
            payload.putAll(suppliedSourceFields);
            payload.put("resolved_resource_url", request.resourceUrl);
            payload.put("resource_url_basis", request.resourceUrlBasis);
            record("config_create_started", payload);
        }

        synchronized void resourceLocated(Object resource, String requestedConfig) {
            Request request = currentRequest(requestedConfig);
            String resourceUrl = value(resource);
            if (request != null) {
                request.resourceUrl = resourceUrl;
                request.resourceUrlBasis = "cleanmix_service_get_resource";
            }
            record("resource_located", fields(
                "attempt", request == null ? null : request.attempt,
                "requested_config", requestedConfig,
                "resource_url", resourceUrl,
                "resource_url_basis", request == null
                    ? null : request.resourceUrlBasis
            ));
        }

        synchronized void featureCheckStarted(Object config) {
            Request request = currentRequest(configName(config));
            record("feature_check_started", featureFields(request, config, null, null));
        }

        synchronized void featureCheckReturned(boolean passed, Object config) {
            Request request = currentRequest(configName(config));
            if (request != null) {
                request.featureCheckPassed = passed;
            }
            record(
                "feature_check_completed",
                featureFields(request, config, passed ? "passed" : "failed", null)
            );
        }

        synchronized void featureCheckThrew(Throwable failure, Object config) {
            failureCount++;
            Request request = currentRequest(configName(config));
            if (request != null) {
                request.featureCheckPassed = false;
            }
            record(
                "feature_check_completed",
                featureFields(request, config, "threw", failure)
            );
        }

        synchronized void configCreateReturned(Object config, String requestedConfig) {
            Request request = popRequest(requestedConfig);
            if (request == null) {
                observerFailure(
                    "create_stack_underflow",
                    new IllegalStateException("no request for " + requestedConfig)
                );
                return;
            }
            request.handle = config;
            request.configName = configName(config);
            request.createOutcome = config == null ? "returned_null" : "returned_config";
            if (config != null) {
                requestByHandle.put(config, request);
                Object inner = apiConfig(config);
                if (inner != null) {
                    requestByHandle.put(inner, request);
                }
            }
            latestRequest.set(request);
            Map<String, Object> payload = requestFields(request, config);
            payload.put("outcome", request.createOutcome);
            record("config_create_completed", payload);
        }

        synchronized void configCreateThrew(Throwable failure, String requestedConfig) {
            failureCount++;
            Request request = popRequest(requestedConfig);
            if (request == null) {
                request = new Request(attemptSequence.incrementAndGet(), requestedConfig);
                requests.add(request);
            }
            request.createOutcome = "threw";
            request.terminalOutcome = "threw";
            request.terminalReason = "config_create_threw";
            latestRequest.set(request);
            Map<String, Object> payload = requestFields(request, null);
            payload.putAll(failureFields(failure));
            record("config_create_completed", payload);
        }

        synchronized void registrationStarted(Object config) {
            Request request = config == null ? latestRequest.get() : requestByHandle.get(config);
            String name = configName(config);
            boolean duplicate = name != null && admittedByName.containsKey(name);
            registrationStack.get().push(new Registration(request, config, duplicate));
            record("registration_started", fields(
                "attempt", request == null ? null : request.attempt,
                "requested_config", request == null ? null : request.requestedConfig,
                "config_name", name,
                "config_identity", identity(config),
                "previously_admitted", duplicate
            ));
        }

        synchronized void registrationReturned(Object config) {
            Registration registration = popRegistration();
            if (registration == null) {
                observerFailure(
                    "registration_stack_underflow",
                    new IllegalStateException("registration return without entry")
                );
                return;
            }
            Request request = registration.request;
            String decision;
            String reason;
            if (config == null) {
                decision = "rejected";
                if (request != null && "threw".equals(request.createOutcome)) {
                    reason = "config_create_threw";
                } else if (request != null && !request.featureCheckPassed) {
                    reason = "required_features_unavailable";
                } else {
                    reason = "config_create_returned_null";
                }
            } else if (registration.duplicate) {
                decision = "duplicate";
                reason = "already_registered";
            } else {
                decision = "admitted";
                reason = "queued";
            }
            if (request != null) {
                request.admissionDecision = decision;
                if ("rejected".equals(decision) || "duplicate".equals(decision)) {
                    request.terminalOutcome = decision;
                    request.terminalReason = reason;
                } else {
                    String name = configName(config);
                    request.configName = name;
                    admittedByName.put(name, request);
                }
            }
            Map<String, Object> payload = requestFields(request, config);
            payload.put("decision", decision);
            payload.put("reason_code", reason);
            record("admission_decision", payload);
        }

        synchronized void registrationThrew(Throwable failure, Object config) {
            failureCount++;
            Registration registration = popRegistration();
            Request request = registration == null ? null : registration.request;
            if (request != null) {
                request.admissionDecision = "threw";
                request.terminalOutcome = "threw";
                request.terminalReason = "registration_threw";
            }
            Map<String, Object> payload = requestFields(request, config);
            payload.put("decision", "threw");
            payload.put("reason_code", "registration_threw");
            payload.putAll(failureFields(failure));
            record("admission_decision", payload);
        }

        synchronized void phasePassStarted(Object environment) {
            String currentPhase = phase(environment);
            phaseStack.get().push(currentPhase == null ? "unknown" : currentPhase);
            int pending = pendingCount();
            boolean recorded = pending > 0
                && !currentPhase.equals(lastRecordedPhasePass);
            if (recorded) {
                lastRecordedPhasePass = currentPhase;
            }
            phaseRecordStack.get().push(recorded);
            if (recorded) {
                record("phase_pass_started", fields(
                    "consumption_phase", currentPhase,
                    "pending_count", pending
                ));
            }
        }

        synchronized void phaseEligibility(boolean eligible, Object configHandle) {
            String name = configName(configHandle);
            Request request = admittedByName.get(name);
            String consumptionPhase = currentPhase();
            String reason = eligible ? "phase_reached" : "phase_not_reached";
            String eligibilityKey = consumptionPhase + ":" + eligible;
            if (request != null && !eligible) {
                request.lastDeferralReason = reason;
            }
            if (request != null && eligibilityKey.equals(request.lastEligibilityKey)) {
                return;
            }
            if (request != null) {
                request.lastEligibilityKey = eligibilityKey;
            }
            record("phase_eligibility", fields(
                "attempt", request == null ? null : request.attempt,
                "config_name", name,
                "config_identity", identity(configHandle),
                "queued_phase", configPhase(configHandle),
                "consumption_phase", consumptionPhase,
                "eligible", eligible,
                "outcome", eligible ? "consumed" : "deferred",
                "reason_code", reason
            ));
        }

        synchronized void phasePassReturned(Object environment) {
            if (phaseRecordStack.get().poll() == Boolean.TRUE) {
                record("phase_pass_completed", fields(
                    "consumption_phase", phase(environment),
                    "outcome", "completed",
                    "pending_count", pendingCount()
                ));
            }
            popPhase();
        }

        synchronized void phasePassThrew(Throwable failure, Object environment) {
            failureCount++;
            if (phaseRecordStack.get().poll() == Boolean.TRUE) {
                Map<String, Object> payload = fields(
                    "consumption_phase", phase(environment),
                    "outcome", "threw",
                    "pending_count", pendingCount()
                );
                payload.putAll(failureFields(failure));
                record("phase_pass_completed", payload);
            }
            popPhase();
        }

        synchronized void stage(
            Object config, String stageName, String outcome, Throwable failure
        ) {
            if (failure != null) {
                failureCount++;
            }
            String name = configName(config);
            Request request = admittedByName.get(name);
            if (request != null && failure != null) {
                request.terminalOutcome = "threw";
                request.terminalReason = stageName + "_threw";
            }
            Map<String, Object> payload = fields(
                "attempt", request == null ? null : request.attempt,
                "config_name", name,
                "config_identity", identity(config),
                "stage", stageName,
                "outcome", outcome,
                "consumption_phase", currentPhase()
            );
            if (failure != null) {
                payload.putAll(failureFields(failure));
            }
            record("config_stage", payload);
        }

        synchronized void batchPromoted(Object batch, Object environment) {
            if (!(batch instanceof Iterable<?> configs)) {
                observerFailure(
                    "batch_not_iterable",
                    new IllegalArgumentException("prepare batch is not iterable")
                );
                return;
            }
            for (Object config : configs) {
                String name = configName(config);
                Request request = admittedByName.get(name);
                if (request != null) {
                    request.terminalOutcome = "active";
                    request.terminalReason = "promoted_to_active";
                }
                record("batch_promotion", fields(
                    "attempt", request == null ? null : request.attempt,
                    "config_name", name,
                    "config_identity", identity(config),
                    "consumption_phase", phase(environment),
                    "outcome", "active",
                    "reason_code", "promoted_to_active"
                ));
            }
        }

        synchronized void batchThrew(
            Throwable failure, Object batch, Object environment
        ) {
            failureCount++;
            if (batch instanceof Iterable<?> configs) {
                for (Object config : configs) {
                    String name = configName(config);
                    Request request = admittedByName.get(name);
                    if (request != null) {
                        request.terminalOutcome = "threw";
                        request.terminalReason = "prepare_batch_threw";
                    }
                    Map<String, Object> payload = fields(
                        "attempt", request == null ? null : request.attempt,
                        "config_name", name,
                        "config_identity", identity(config),
                        "consumption_phase", phase(environment),
                        "outcome", "threw",
                        "reason_code", "prepare_batch_threw"
                    );
                    payload.putAll(failureFields(failure));
                    record("batch_promotion", payload);
                }
            }
        }

        synchronized void observerFailure(String operation, Throwable failure) {
            observerFailureCount++;
            writeFailure = true;
            if (available()) {
                Map<String, Object> payload = fields("operation", operation);
                payload.putAll(failureFields(failure));
                try {
                    record("observer_failure", payload);
                } catch (Throwable ignored) {
                    writeFailure = true;
                }
            }
        }

        synchronized void captureEnded() {
            if (!available()) {
                return;
            }
            int admitted = 0;
            int active = 0;
            int deferred = 0;
            for (Request request : requests) {
                if ("admitted".equals(request.admissionDecision)) {
                    admitted++;
                }
                if (request.terminalOutcome == null) {
                    request.terminalOutcome = "deferred";
                    request.terminalReason = request.lastDeferralReason == null
                        ? "not_consumed_before_shutdown" : request.lastDeferralReason;
                }
                if ("active".equals(request.terminalOutcome)) {
                    active++;
                } else if ("deferred".equals(request.terminalOutcome)) {
                    deferred++;
                }
                record("terminal_state", fields(
                    "attempt", request.attempt,
                    "requested_config", request.requestedConfig,
                    "config_name", request.configName,
                    "outcome", request.terminalOutcome,
                    "reason_code", request.terminalReason
                ));
            }
            Set<String> expected = CleanMixDiscoveryTraceAgent
                .lifecycleTargetsForReceipt().keySet();
            boolean healthy = !writeFailure && !transformFailure
                && transformedTargets.equals(expected) && !requests.isEmpty();
            List<String> transformed = new ArrayList<>(transformedTargets);
            Collections.sort(transformed);
            try {
                record("capture_end", fields(
                    "health", healthy ? "healthy" : "failed",
                    "transformed_targets", transformed,
                    "attempt_count", requests.size(),
                    "admitted_count", admitted,
                    "active_count", active,
                    "deferred_count", deferred,
                    "failure_count", failureCount,
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

        private Request currentRequest(String requestedConfig) {
            for (Request request : requestStack.get()) {
                if (requestedConfig == null
                        || requestedConfig.equals(request.requestedConfig)
                        || requestedConfig.equals(request.configName)) {
                    return request;
                }
            }
            return requestStack.get().peek();
        }

        private Request popRequest(String requestedConfig) {
            Deque<Request> stack = requestStack.get();
            Request request = stack.peek();
            if (request != null && requestedConfig.equals(request.requestedConfig)) {
                return stack.pop();
            }
            for (Request candidate : new ArrayList<>(stack)) {
                if (requestedConfig.equals(candidate.requestedConfig)) {
                    stack.remove(candidate);
                    return candidate;
                }
            }
            return null;
        }

        private Registration popRegistration() {
            return registrationStack.get().poll();
        }

        private String currentPhase() {
            return phaseStack.get().peek();
        }

        private void popPhase() {
            phaseStack.get().poll();
        }

        private int pendingCount() {
            int count = 0;
            for (Request request : requests) {
                if ("admitted".equals(request.admissionDecision)
                        && request.terminalOutcome == null) {
                    count++;
                }
            }
            return count;
        }

        private Map<String, Object> requestFields(Request request, Object config) {
            Map<String, Object> payload = fields(
                "attempt", request == null ? null : request.attempt,
                "requested_config", request == null ? null : request.requestedConfig,
                "config_name", configName(config),
                "config_identity", identity(config),
                "resource_url", request == null ? null : request.resourceUrl,
                "resource_url_basis", request == null
                    ? null : request.resourceUrlBasis,
                "required", required(config),
                "queued_phase", configPhase(config)
            );
            payload.putAll(sourceFields(configSource(config)));
            return payload;
        }

        private Map<String, Object> featureFields(
            Request request, Object config, String outcome, Throwable failure
        ) {
            Map<String, Object> payload = fields(
                "attempt", request == null ? null : request.attempt,
                "config_name", configName(config),
                "required", required(config),
                "required_features", featureStates(config),
                "outcome", outcome
            );
            if (failure != null) {
                payload.putAll(failureFields(failure));
            }
            return payload;
        }

        private Map<String, Object> transformFields(
            String target,
            ClassLoader loader,
            ProtectionDomain protectionDomain,
            String inputSha256,
            String outputSha256,
            String reason
        ) {
            return fields(
                "target_class", target.replace('/', '.'),
                "defining_loader_class", loaderClass(loader),
                "defining_loader_identity", loaderIdentity(loader),
                "target_code_source_uri", codeSourceUri(protectionDomain),
                "input_sha256", inputSha256,
                "output_sha256", outputSha256,
                "reason", reason
            );
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
                throw new IllegalStateException("config lifecycle trace write failed", failure);
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
                result.append(':').append(json(entry.getValue()));
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
