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
import java.util.Collection;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/** Fail-open observer for Cleanroom-owned transformer delegation epochs. */
public final class CleanMixTransformerChainRuntime {

    public static final String RAW_FORMAT =
        "workbench-cleanmix-transformer-chain-raw-v1";
    public static final String ENABLE_PROPERTY =
        "workbench.cleanmix.transformer_chain.enabled";
    public static final String OUTPUT_PROPERTY =
        "workbench.cleanmix.transformer_chain.output";
    public static final String CAPTURE_ID_PROPERTY =
        "workbench.cleanmix.transformer_chain.capture_id";
    private static final String AGENT_ID =
        "workbench-cleanmix-transformer-chain-agent-v1";

    private static volatile State state;

    private CleanMixTransformerChainRuntime() {
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
                new Thread(current::captureEnded, "workbench-cleanmix-transformer-chain-shutdown")
            );
        } catch (Throwable failure) {
            current.observerFailure("shutdown_hook_install", failure);
        }
    }

    public static void transformerInstalled() {
        safely(State::transformerInstalled);
    }

    public static void transformApplied(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String outputSha256
    ) {
        safely(current -> current.transformApplied(
            target, loader, domain, inputSha256, outputSha256
        ));
    }

    public static void transformRejected(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String reason
    ) {
        safely(current -> current.transformRejected(
            target, loader, domain, inputSha256, reason
        ));
    }

    public static void transformFailed(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, Throwable failure
    ) {
        safely(current -> current.transformFailed(
            target, loader, domain, inputSha256, failure
        ));
    }

    public static void providerConstructed(Object provider) {
        safely(current -> current.providerConstructed(provider));
    }

    public static void serviceRefreshStarted(Object service) {
        safely(current -> current.serviceRefreshStarted(service));
    }

    public static void serviceRefreshCompleted(Object service) {
        safely(current -> current.serviceRefreshCompleted(service));
    }

    public static void serviceRefreshThrew(Throwable failure, Object service) {
        safely(current -> current.serviceRefreshThrew(failure, service));
    }

    public static void providerRefreshStarted(Object provider) {
        safely(current -> current.providerRefreshStarted(provider));
    }

    public static void exclusionChangeStarted(Object provider, String pattern) {
        safely(current -> current.exclusionChangeStarted(provider, pattern));
    }

    public static void exclusionChangeCompleted(Object provider, String pattern) {
        safely(current -> current.exclusionChangeCompleted(provider, pattern));
    }

    public static void delegationAccessStarted(Object provider) {
        safely(current -> current.delegationAccessStarted(provider));
    }

    public static void delegationAccessCompleted(Object provider, Object delegated) {
        safely(current -> current.delegationAccessCompleted(provider, delegated));
    }

    public static void delegationAccessThrew(Throwable failure, Object provider) {
        safely(current -> current.delegationAccessThrew(failure, provider));
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

    private static final class PendingRefresh {
        final long id;
        final String reason;
        final String phase;

        PendingRefresh(long id, String reason, String phase) {
            this.id = id;
            this.reason = reason;
            this.phase = phase;
        }
    }

    private static final class Access {
        final Object provider;
        final PendingRefresh refresh;

        Access(Object provider, PendingRefresh refresh) {
            this.provider = provider;
            this.refresh = refresh;
        }
    }

    private static final class ExclusionChange {
        final Object provider;
        final String pattern;
        final boolean presentBefore;

        ExclusionChange(Object provider, String pattern, boolean presentBefore) {
            this.provider = provider;
            this.pattern = pattern;
            this.presentBefore = presentBefore;
        }
    }

    private static final class State {
        private final BufferedWriter writer;
        private final String captureId;
        private final AtomicLong sequence = new AtomicLong();
        private final Map<Object, PendingRefresh> pendingRefreshes =
            Collections.synchronizedMap(new IdentityHashMap<>());
        private final ThreadLocal<Access> access = new ThreadLocal<>();
        private final ThreadLocal<ExclusionChange> exclusionChange = new ThreadLocal<>();
        private final ThreadLocal<Boolean> serviceRefresh =
            ThreadLocal.withInitial(() -> false);
        private final Set<String> transformedTargets = new LinkedHashSet<>();
        private Object lastProvider;
        private long nextRefreshId;
        private int epochCount;
        private int refreshCount;
        private int exclusionChangeCount;
        private int observerFailureCount;
        private int maxLiveCount;
        private int maxDelegatedCount;
        private boolean transformFailure;
        private boolean writeFailure;
        private boolean closed;

        private State(BufferedWriter writer, String captureId) {
            this.writer = writer;
            this.captureId = captureId;
        }

        static State open() {
            String output = System.getProperty(OUTPUT_PROPERTY);
            String captureId = System.getProperty(CAPTURE_ID_PROPERTY);
            if (output == null || output.isBlank() || captureId == null || captureId.isBlank()) {
                return new State(null, captureId);
            }
            try {
                Path path = Paths.get(output);
                if (!path.isAbsolute() || Files.exists(path)) {
                    return new State(null, captureId);
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
                    captureId
                );
            } catch (Throwable ignored) {
                return new State(null, captureId);
            }
        }

        boolean available() {
            return writer != null && !closed;
        }

        synchronized void captureStarted(Instrumentation instrumentation) {
            List<Map<String, Object>> targets = new ArrayList<>();
            for (Map.Entry<String, String> target
                    : CleanMixDiscoveryTraceAgent.chainTargetsForReceipt().entrySet()) {
                targets.add(fields(
                    "target_class", target.getKey().replace('/', '.'),
                    "expected_input_sha256", target.getValue()
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
                    .chainTargetsForReceipt().size(),
                "retransform_requested", false
            ));
        }

        synchronized void transformApplied(
            String target, ClassLoader loader, ProtectionDomain domain,
            String inputSha256, String outputSha256
        ) {
            transformedTargets.add(target.replace('/', '.'));
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

        synchronized void providerConstructed(Object provider) throws Throwable {
            lastProvider = provider;
            record("provider_created", fields(
                "provider_class", provider.getClass().getName(),
                "provider_identity", identity(provider),
                "provider_loader_class", loaderClass(provider.getClass().getClassLoader()),
                "provider_loader_identity", loaderIdentity(provider.getClass().getClassLoader()),
                "provider_code_source_uri", codeSourceUri(provider.getClass()),
                "provider_exclusions", sortedStrings(fieldValue(provider, "excludeTransformers"))
            ));
        }

        synchronized void serviceRefreshStarted(Object service) {
            serviceRefresh.set(true);
            record("service_refresh_started", fields(
                "service_class", service.getClass().getName(),
                "service_identity", identity(service),
                "phase", currentPhase(service)
            ));
        }

        synchronized void serviceRefreshCompleted(Object service) {
            record("service_refresh_completed", fields(
                "service_identity", identity(service),
                "phase", currentPhase(service),
                "outcome", "completed"
            ));
            serviceRefresh.remove();
        }

        synchronized void serviceRefreshThrew(Throwable failure, Object service) {
            Map<String, Object> payload = fields(
                "service_identity", identity(service),
                "phase", currentPhase(service),
                "outcome", "threw"
            );
            payload.putAll(failureFields(failure));
            record("service_refresh_completed", payload);
            serviceRefresh.remove();
        }

        synchronized void providerRefreshStarted(Object provider) throws Throwable {
            lastProvider = provider;
            registerRefresh(
                provider,
                serviceRefresh.get() ? "cleanmix_service_refresh" : "direct_provider_refresh",
                currentPhase(provider)
            );
        }

        synchronized void exclusionChangeStarted(Object provider, String pattern)
            throws Throwable {
            lastProvider = provider;
            Set<String> exclusions = stringSet(fieldValue(provider, "excludeTransformers"));
            exclusionChange.set(new ExclusionChange(
                provider, pattern, exclusions.contains(pattern)
            ));
        }

        synchronized void exclusionChangeCompleted(Object provider, String pattern)
            throws Throwable {
            ExclusionChange change = exclusionChange.get();
            exclusionChange.remove();
            Set<String> exclusions = stringSet(fieldValue(provider, "excludeTransformers"));
            boolean presentBefore = change != null && change.provider == provider
                && change.presentBefore;
            boolean presentAfter = exclusions.contains(pattern);
            exclusionChangeCount++;
            String reason = presentBefore
                ? "transformer_exclusion_reasserted" : "transformer_exclusion_added";
            PendingRefresh refresh = registerRefresh(provider, reason, currentPhase(provider));
            record("exclusion_change", fields(
                "refresh_id", refresh.id,
                "action", presentBefore ? "reasserted" : "added",
                "pattern", pattern,
                "present_before", presentBefore,
                "present_after", presentAfter,
                "provider_exclusions", sortedStrings(exclusions)
            ));
        }

        synchronized void delegationAccessStarted(Object provider) throws Throwable {
            lastProvider = provider;
            Object delegated = fieldValue(provider, "delegatedTransformers");
            int previous = ((Number) fieldValue(provider, "previousTransformerCount")).intValue();
            int liveCount = liveTransformers(provider).size();
            PendingRefresh refresh = pendingRefreshes.get(provider);
            if (delegated == null || previous != liveCount) {
                if (refresh == null) {
                    refresh = registerRefresh(
                        provider,
                        delegated == null ? "initial_cache_miss" : "live_chain_size_changed",
                        currentPhase(provider)
                    );
                } else if (previous != liveCount
                        && !"live_chain_size_changed".equals(refresh.reason)) {
                    refresh = registerRefresh(
                        provider, "live_chain_size_changed", currentPhase(provider)
                    );
                }
                access.set(new Access(provider, refresh));
            } else {
                access.remove();
            }
        }

        synchronized void delegationAccessCompleted(Object provider, Object delegated)
            throws Throwable {
            Access current = access.get();
            access.remove();
            if (current == null || current.provider != provider) {
                return;
            }
            pendingRefreshes.remove(provider);
            List<?> live = liveTransformers(provider);
            List<?> delegatedList = listValue(delegated);
            List<Map<String, Object>> liveRows = chainRows(live, false);
            List<Map<String, Object>> delegatedRows = chainRows(delegatedList, true);
            maxLiveCount = Math.max(maxLiveCount, liveRows.size());
            maxDelegatedCount = Math.max(maxDelegatedCount, delegatedRows.size());
            epochCount++;
            record("chain_epoch", fields(
                "epoch", epochCount,
                "refresh_id", current.refresh.id,
                "refresh_reason", current.refresh.reason,
                "phase", current.refresh.phase,
                "provider_class", provider.getClass().getName(),
                "provider_identity", identity(provider),
                "provider_loader_class", loaderClass(provider.getClass().getClassLoader()),
                "provider_loader_identity", loaderIdentity(provider.getClass().getClassLoader()),
                "provider_code_source_uri", codeSourceUri(provider.getClass()),
                "foundation_code_source_uri", foundationCodeSourceUri(provider),
                "previous_transformer_count",
                    ((Number) fieldValue(provider, "previousTransformerCount")).intValue(),
                "live_chain", liveRows,
                "delegated_chain", delegatedRows,
                "provider_exclusions", sortedStrings(fieldValue(provider, "excludeTransformers")),
                "foundation_transformer_exclusions", foundationExclusions(provider)
            ));
        }

        synchronized void delegationAccessThrew(Throwable failure, Object provider) {
            access.remove();
            observerFailure("delegation_access", failure);
        }

        private PendingRefresh registerRefresh(
            Object provider, String reason, String phase
        ) throws Throwable {
            PendingRefresh previous = pendingRefreshes.get(provider);
            PendingRefresh refresh = new PendingRefresh(++nextRefreshId, reason, phase);
            pendingRefreshes.put(provider, refresh);
            refreshCount++;
            Object cache = fieldValue(provider, "delegatedTransformers");
            int previousCount = ((Number) fieldValue(
                provider, "previousTransformerCount"
            )).intValue();
            int liveCount = liveTransformers(provider).size();
            record("refresh_requested", fields(
                "refresh_id", refresh.id,
                "reason_code", reason,
                "phase", phase,
                "provider_identity", identity(provider),
                "delegated_cache_present", cache != null,
                "previous_transformer_count", previousCount,
                "live_transformer_count", liveCount,
                "supersedes_refresh_id", previous == null ? null : previous.id
            ));
            return refresh;
        }

        private List<Map<String, Object>> chainRows(List<?> chain, boolean delegated)
            throws Throwable {
            List<Map<String, Object>> result = new ArrayList<>();
            int ordinal = 0;
            for (Object exposed : chain) {
                Object implementation = unwrap(exposed);
                result.add(fields(
                    "ordinal", ordinal++,
                    "reported_name", reportedName(exposed, implementation),
                    "implementation_class", implementation.getClass().getName(),
                    "wrapper_class", wrapperClass(exposed, implementation),
                    "implementation_loader_class",
                        loaderClass(implementation.getClass().getClassLoader()),
                    "implementation_loader_identity",
                        loaderIdentity(implementation.getClass().getClassLoader()),
                    "code_source_uri", codeSourceUri(implementation.getClass()),
                    "priority", priority(implementation),
                    "delegation_excluded", delegated
                        ? delegationExcluded(exposed) : delegationExcludedIfAvailable(exposed)
                ));
            }
            return result;
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
            Set<String> expected = new LinkedHashSet<>();
            for (String target : CleanMixDiscoveryTraceAgent
                    .chainTargetsForReceipt().keySet()) {
                expected.add(target.replace('/', '.'));
            }
            boolean healthy = !writeFailure && !transformFailure
                && observerFailureCount == 0
                && transformedTargets.equals(expected)
                && epochCount > 0 && maxLiveCount > 0 && maxDelegatedCount > 0;
            List<String> transformed = new ArrayList<>(transformedTargets);
            Collections.sort(transformed);
            int unresolved = pendingRefreshes.size();
            try {
                record("capture_end", fields(
                    "health", healthy ? "healthy" : "failed",
                    "transformed_targets", transformed,
                    "epoch_count", epochCount,
                    "refresh_count", refreshCount,
                    "unresolved_refresh_count", unresolved,
                    "exclusion_change_count", exclusionChangeCount,
                    "max_live_transformer_count", maxLiveCount,
                    "max_delegated_transformer_count", maxDelegatedCount,
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
                throw new IllegalStateException("transformer chain trace write failed", failure);
            }
        }
    }

    private static List<?> liveTransformers(Object provider) throws Throwable {
        ClassLoader loader = provider.getClass().getClassLoader();
        Class<?> delegate = Class.forName(
            "top.outlands.foundation.TransformerDelegate", false, loader
        );
        Object value = delegate.getMethod("getTransformers").invoke(null);
        return listValue(value);
    }

    private static List<String> foundationExclusions(Object provider) {
        try {
            ClassLoader loader = provider.getClass().getClassLoader();
            Class<?> launch = Class.forName(
                "net.minecraft.launchwrapper.Launch", false, loader
            );
            Field field = launch.getField("classLoader");
            Object classLoader = field.get(null);
            Object value = invoke(classLoader, "getTransformerExclusions");
            return sortedStrings(value);
        } catch (Throwable ignored) {
            return List.of();
        }
    }

    private static String foundationCodeSourceUri(Object provider) {
        try {
            Class<?> delegate = Class.forName(
                "top.outlands.foundation.TransformerDelegate", false,
                provider.getClass().getClassLoader()
            );
            return codeSourceUri(delegate);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Object unwrap(Object value) {
        if (value == null) {
            return null;
        }
        Object current = value;
        try {
            Object transformer = fieldValue(value, "transformer");
            if (transformer != null) {
                current = transformer;
            }
        } catch (Throwable ignored) {
        }
        if (current.getClass().getName().startsWith("$wrapper.")) {
            try {
                Object parent = fieldValue(current, "parent");
                if (parent != null) {
                    current = parent;
                }
            } catch (Throwable ignored) {
            }
        }
        return current;
    }

    private static String reportedName(Object exposed, Object implementation) {
        Object value = invokeIfAvailable(exposed, "getName");
        return value == null ? exposed.getClass().getName() : value.toString();
    }

    private static String wrapperClass(Object exposed, Object implementation) {
        if (exposed == implementation) {
            return null;
        }
        try {
            Object transformer = fieldValue(exposed, "transformer");
            if (transformer != null
                    && transformer.getClass().getName().startsWith("$wrapper.")) {
                return transformer.getClass().getName();
            }
        } catch (Throwable ignored) {
        }
        return exposed.getClass().getName();
    }

    private static Integer priority(Object implementation) {
        Object value = invokeIfAvailable(implementation, "getPriority");
        return value instanceof Number ? ((Number) value).intValue() : null;
    }

    private static Boolean delegationExcluded(Object exposed) {
        Object value = invokeIfAvailable(exposed, "isDelegationExcluded");
        return value instanceof Boolean ? (Boolean) value : false;
    }

    private static Boolean delegationExcludedIfAvailable(Object exposed) {
        Object value = invokeIfAvailable(exposed, "isDelegationExcluded");
        return value instanceof Boolean ? (Boolean) value : null;
    }

    private static String currentPhase(Object anchor) {
        try {
            Class<?> environment = Class.forName(
                "org.spongepowered.asm.mixin.MixinEnvironment", false,
                anchor.getClass().getClassLoader()
            );
            Object current = environment.getMethod("getCurrentEnvironment").invoke(null);
            Object phase = current.getClass().getMethod("getPhase").invoke(current);
            return phase == null ? "UNKNOWN" : phase.toString();
        } catch (Throwable ignored) {
            return "UNKNOWN";
        }
    }

    private static Object invoke(Object target, String name, Object... arguments)
        throws Throwable {
        if (target == null) {
            return null;
        }
        Method selected = null;
        Class<?> type = target.getClass();
        while (type != null && selected == null) {
            for (Method method : type.getDeclaredMethods()) {
                if (method.getName().equals(name)
                        && method.getParameterCount() == arguments.length) {
                    selected = method;
                    break;
                }
            }
            type = type.getSuperclass();
        }
        if (selected == null) {
            throw new NoSuchMethodException(name);
        }
        try {
            selected.setAccessible(true);
            return selected.invoke(target, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause() == null ? failure : failure.getCause();
        }
    }

    private static Object invokeIfAvailable(Object target, String name) {
        try {
            return invoke(target, name);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Object fieldValue(Object target, String name) throws Throwable {
        Class<?> type = target.getClass();
        while (type != null) {
            try {
                Field field = type.getDeclaredField(name);
                field.setAccessible(true);
                return field.get(target);
            } catch (NoSuchFieldException ignored) {
                type = type.getSuperclass();
            }
        }
        throw new NoSuchFieldException(name);
    }

    private static List<?> listValue(Object value) {
        if (value instanceof List<?>) {
            return (List<?>) value;
        }
        if (value instanceof Collection<?>) {
            return new ArrayList<>((Collection<?>) value);
        }
        return List.of();
    }

    private static Set<String> stringSet(Object value) {
        Set<String> result = new LinkedHashSet<>();
        if (value instanceof Iterable<?>) {
            for (Object item : (Iterable<?>) value) {
                if (item != null) {
                    result.add(item.toString());
                }
            }
        }
        return result;
    }

    private static List<String> sortedStrings(Object value) {
        List<String> result = new ArrayList<>(stringSet(value));
        Collections.sort(result);
        return result;
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
            return location == null ? null : location.toURI().toASCIIString();
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static String loaderClass(ClassLoader loader) {
        return loader == null ? "bootstrap" : loader.getClass().getName();
    }

    private static String loaderIdentity(ClassLoader loader) {
        return loader == null ? "bootstrap" : identity(loader);
    }

    private static String identity(Object value) {
        return value == null ? "null" : value.getClass().getName() + "@"
            + Integer.toHexString(System.identityHashCode(value));
    }

    private static Map<String, Object> transformFields(
        String target, ClassLoader loader, ProtectionDomain domain,
        String inputSha256, String outputSha256, String reason
    ) {
        return fields(
            "target_class", target.replace('/', '.'),
            "defining_loader_class", loaderClass(loader),
            "defining_loader_identity", loaderIdentity(loader),
            "target_code_source_uri", codeSourceUri(domain),
            "input_sha256", inputSha256,
            "output_sha256", outputSha256,
            "reason", reason
        );
    }

    private static Map<String, Object> failureFields(Throwable failure) {
        StackTraceElement[] stack = failure == null
            ? new StackTraceElement[0] : failure.getStackTrace();
        return fields(
            "exception_class", failure == null ? null : failure.getClass().getName(),
            "message", failure == null || failure.getMessage() == null
                ? null : failure.getMessage(),
            "stack_top", stack.length == 0 ? null : stack[0].toString()
        );
    }

    private static Map<String, Object> fields(Object... values) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < values.length; index += 2) {
            result.put(values[index].toString(), values[index + 1]);
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
        if (value instanceof Boolean || value instanceof Number) {
            return value.toString();
        }
        if (value instanceof Map<?, ?>) {
            StringBuilder result = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(quote(entry.getKey().toString()));
                result.append(':').append(json(entry.getValue()));
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
