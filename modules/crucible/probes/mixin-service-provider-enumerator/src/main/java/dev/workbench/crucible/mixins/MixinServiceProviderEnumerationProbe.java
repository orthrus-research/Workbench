package dev.workbench.crucible.mixins;

import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.security.CodeSource;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.ServiceLoader;
import java.util.Set;
import java.util.TreeMap;

/**
 * Dependency-free IMixinService provider enumerator for a launched process.
 *
 * <p>The probe uses the calling thread's context classloader. A launch hook can
 * call {@link #capture(String[])} without terminating the process; the main
 * entry point exits with status 2 after publishing typed failed evidence.</p>
 */
public final class MixinServiceProviderEnumerationProbe {
    private static final String FORMAT =
            "workbench-crucible-mixin-service-provider-enumeration-evidence-v1";
    private static final String EVIDENCE_PREFIX =
            "crucible-mixin-service-provider-enumeration:sha256:";
    private static final String CANONICALIZATION_ID = "workbench-canonical-json-v1";
    private static final String PROBE_ID =
            "workbench-mixin-service-provider-enumerator-v1";
    private static final String MECHANISM = "java.util.ServiceLoader.iterator";
    private static final String SERVICE_INTERFACE =
            "org.spongepowered.asm.service.IMixinService";
    private static final Set<String> ARGUMENTS;

    static {
        Set<String> names = new LinkedHashSet<String>();
        names.add("output");
        names.add("session-id");
        names.add("launch-id");
        names.add("profile-id");
        names.add("side");
        names.add("candidate-toolchain-lock-sha256");
        names.add("component-topology-receipt-id");
        names.add("component-topology-receipt-sha256");
        ARGUMENTS = Collections.unmodifiableSet(names);
    }

    private MixinServiceProviderEnumerationProbe() {
    }

    public static void main(String[] args) throws Exception {
        boolean complete = capture(args);
        if (!complete) {
            System.exit(2);
        }
    }

    /**
     * Enumerate providers and atomically publish complete or failed evidence.
     *
     * @return true only when ServiceLoader iteration reached exhaustion
     */
    public static boolean capture(String[] args) throws IOException {
        Config config = Config.parse(args);
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        String loaderClass = loader == null ? "bootstrap" : loader.getClass().getName();
        List<Map<String, Object>> providers = new ArrayList<Map<String, Object>>();
        Failure failure = null;
        Class<?> serviceInterface = null;

        try {
            serviceInterface = Class.forName(SERVICE_INTERFACE, false, loader);
        } catch (Throwable throwable) {
            failure = Failure.from("service_interface_load", throwable);
        }

        ServiceLoader<?> serviceLoader = null;
        if (failure == null) {
            try {
                serviceLoader = ServiceLoader.load(serviceInterface, loader);
            } catch (Throwable throwable) {
                failure = Failure.from("service_loader_create", throwable);
            }
        }

        if (failure == null) {
            try {
                Iterator<?> iterator = serviceLoader.iterator();
                Set<String> identities = new LinkedHashSet<String>();
                while (iterator.hasNext()) {
                    Object provider = iterator.next();
                    Map<String, Object> row = providerRow(provider);
                    String identity = row.get("service_class") + "\u0000"
                            + row.get("provider_artifact_sha256");
                    if (!identities.add(identity)) {
                        throw new ProbeFailure(
                                "service_loader_iteration",
                                "java.lang.IllegalStateException",
                                "ServiceLoader returned a duplicate provider identity"
                        );
                    }
                    providers.add(row);
                }
            } catch (ProbeFailure probeFailure) {
                failure = probeFailure.failure;
            } catch (Throwable throwable) {
                failure = Failure.from("service_loader_iteration", throwable);
            }
        }

        if (failure != null) {
            providers.clear();
        } else {
            Collections.sort(providers, new Comparator<Map<String, Object>>() {
                @Override
                public int compare(Map<String, Object> left, Map<String, Object> right) {
                    int byClass = string(left, "service_class")
                            .compareTo(string(right, "service_class"));
                    if (byClass != 0) {
                        return byClass;
                    }
                    int byArtifact = string(left, "provider_artifact_sha256")
                            .compareTo(string(right, "provider_artifact_sha256"));
                    if (byArtifact != 0) {
                        return byArtifact;
                    }
                    return string(left, "service_name")
                            .compareTo(string(right, "service_name"));
                }
            });
        }

        Map<String, Object> material = material(
                config,
                loaderClass,
                failure == null ? "enumerated" : "failed",
                providers,
                failure
        );
        String evidenceId = EVIDENCE_PREFIX + sha256(canonicalJson(material));
        Map<String, Object> document = new TreeMap<String, Object>(material);
        document.put("evidence_id", evidenceId);
        writeAtomically(config.output, canonicalJson(document));
        return failure == null;
    }

    private static Map<String, Object> providerRow(Object provider) throws ProbeFailure {
        Class<?> providerClass = provider.getClass();
        String serviceName;
        try {
            Method getName = providerClass.getMethod("getName");
            Object value = getName.invoke(provider);
            if (!(value instanceof String) || ((String) value).isEmpty()) {
                throw new IllegalStateException("provider getName returned no name");
            }
            serviceName = (String) value;
        } catch (Throwable throwable) {
            throw new ProbeFailure(Failure.from("provider_name", throwable));
        }

        Path source;
        URI sourceUri;
        try {
            ProtectionDomain domain = providerClass.getProtectionDomain();
            CodeSource codeSource = domain == null ? null : domain.getCodeSource();
            URL location = codeSource == null ? null : codeSource.getLocation();
            if (location == null) {
                throw new IllegalStateException("provider class has no code source");
            }
            sourceUri = location.toURI();
            if (!"file".equals(sourceUri.getScheme())) {
                throw new IllegalStateException("provider code source is not a file URI");
            }
            source = Paths.get(sourceUri);
            if (Files.isSymbolicLink(source)
                    || !Files.isRegularFile(source, LinkOption.NOFOLLOW_LINKS)) {
                throw new IllegalStateException(
                        "provider code source is not a regular non-symlink file"
                );
            }
            source = source.toRealPath(LinkOption.NOFOLLOW_LINKS);
            sourceUri = source.toUri();
        } catch (Throwable throwable) {
            throw new ProbeFailure(Failure.from("provider_code_source", throwable));
        }

        String digest;
        long size;
        try {
            digest = sha256(source);
            size = Files.size(source);
        } catch (Throwable throwable) {
            throw new ProbeFailure(Failure.from("artifact_measurement", throwable));
        }

        Map<String, Object> row = new TreeMap<String, Object>();
        row.put("provider_artifact_sha256", digest);
        row.put("provider_artifact_size_bytes", Long.valueOf(size));
        row.put("service_class", providerClass.getName());
        row.put("service_name", serviceName);
        row.put("source_uri", sourceUri.toASCIIString());
        return row;
    }

    private static Map<String, Object> material(
            Config config,
            String loaderClass,
            String state,
            List<Map<String, Object>> providers,
            Failure failure
    ) {
        Map<String, Object> document = new TreeMap<String, Object>();
        document.put("boundaries", boundaries());
        document.put("canonicalization_id", CANONICALIZATION_ID);
        document.put("failure", failure == null ? null : failure.value());
        document.put("format", FORMAT);
        document.put("probe", probe(loaderClass));
        document.put("providers", providers);
        document.put("schema_version", Integer.valueOf(1));
        document.put("session", config.session());
        document.put("state", state);
        document.put("summary", summary(providers));
        return document;
    }

    private static Map<String, Object> probe(String loaderClass) {
        Map<String, Object> probe = new TreeMap<String, Object>();
        probe.put("class_loader_class", loaderClass);
        probe.put("class_loader_kind", "thread_context");
        probe.put("mechanism", MECHANISM);
        probe.put("probe_id", PROBE_ID);
        probe.put("service_interface", SERVICE_INTERFACE);
        return probe;
    }

    private static Map<String, Object> boundaries() {
        Map<String, Object> boundaries = new TreeMap<String, Object>();
        boundaries.put("mixin_code_source_used_for_provider_attribution", Boolean.FALSE);
        boundaries.put("partial_provider_set_published", Boolean.FALSE);
        boundaries.put(
                "provider_source_from_implementation_protection_domain",
                Boolean.TRUE
        );
        boundaries.put("runtime_selection_proved", Boolean.FALSE);
        return boundaries;
    }

    private static Map<String, Object> summary(List<Map<String, Object>> providers) {
        Map<String, Integer> counts = new TreeMap<String, Integer>();
        for (Map<String, Object> provider : providers) {
            String name = string(provider, "service_name");
            Integer previous = counts.get(name);
            counts.put(name, Integer.valueOf(previous == null ? 1 : previous.intValue() + 1));
        }
        List<String> duplicates = new ArrayList<String>();
        for (Map.Entry<String, Integer> entry : counts.entrySet()) {
            if (entry.getValue().intValue() > 1) {
                duplicates.add(entry.getKey());
            }
        }
        Map<String, Object> summary = new TreeMap<String, Object>();
        summary.put("distinct_service_name_count", Integer.valueOf(counts.size()));
        summary.put("duplicate_service_names", duplicates);
        summary.put("provider_count", Integer.valueOf(providers.size()));
        return summary;
    }

    private static String string(Map<String, Object> value, String key) {
        return (String) value.get(key);
    }

    private static String canonicalJson(Object value) {
        StringBuilder result = new StringBuilder();
        appendJson(result, value);
        return result.toString();
    }

    @SuppressWarnings("unchecked")
    private static void appendJson(StringBuilder output, Object value) {
        if (value == null) {
            output.append("null");
        } else if (value instanceof String) {
            appendString(output, (String) value);
        } else if (value instanceof Boolean || value instanceof Number) {
            output.append(value.toString());
        } else if (value instanceof Map) {
            output.append('{');
            boolean first = true;
            Map<String, Object> sorted = new TreeMap<String, Object>(
                    (Map<String, Object>) value
            );
            for (Map.Entry<String, Object> entry : sorted.entrySet()) {
                if (!first) {
                    output.append(',');
                }
                first = false;
                appendString(output, entry.getKey());
                output.append(':');
                appendJson(output, entry.getValue());
            }
            output.append('}');
        } else if (value instanceof List) {
            output.append('[');
            boolean first = true;
            for (Object item : (List<Object>) value) {
                if (!first) {
                    output.append(',');
                }
                first = false;
                appendJson(output, item);
            }
            output.append(']');
        } else {
            throw new IllegalArgumentException("unsupported JSON value " + value.getClass());
        }
    }

    private static void appendString(StringBuilder output, String value) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"':
                    output.append("\\\"");
                    break;
                case '\\':
                    output.append("\\\\");
                    break;
                case '\b':
                    output.append("\\b");
                    break;
                case '\f':
                    output.append("\\f");
                    break;
                case '\n':
                    output.append("\\n");
                    break;
                case '\r':
                    output.append("\\r");
                    break;
                case '\t':
                    output.append("\\t");
                    break;
                default:
                    if (character < 0x20) {
                        output.append(String.format("\\u%04x", Integer.valueOf(character)));
                    } else {
                        output.append(character);
                    }
            }
        }
        output.append('"');
    }

    private static String sha256(Path path) throws IOException {
        MessageDigest digest = digest();
        byte[] buffer = new byte[65536];
        InputStream stream = Files.newInputStream(path);
        try {
            int count;
            while ((count = stream.read(buffer)) != -1) {
                digest.update(buffer, 0, count);
            }
        } finally {
            stream.close();
        }
        return hexadecimal(digest.digest());
    }

    private static String sha256(String value) {
        MessageDigest digest = digest();
        return hexadecimal(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
    }

    private static MessageDigest digest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    private static String hexadecimal(byte[] value) {
        StringBuilder result = new StringBuilder(value.length * 2);
        for (byte item : value) {
            result.append(String.format("%02x", Integer.valueOf(item & 0xff)));
        }
        return result.toString();
    }

    private static void writeAtomically(Path output, String canonical) throws IOException {
        if (Files.isSymbolicLink(output)) {
            throw new IOException("probe output cannot be a symlink");
        }
        Path absolute = output.toAbsolutePath().normalize();
        Path parent = absolute.getParent();
        if (parent == null) {
            throw new IOException("probe output has no parent");
        }
        Files.createDirectories(parent);
        Path temporary = Files.createTempFile(parent, ".mixin-provider-enumeration-", ".tmp");
        try {
            Files.write(temporary, (canonical + "\n").getBytes(StandardCharsets.UTF_8));
            try {
                Files.move(
                        temporary,
                        absolute,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING
                );
            } catch (AtomicMoveNotSupportedException unsupported) {
                Files.move(temporary, absolute, StandardCopyOption.REPLACE_EXISTING);
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    private static final class Config {
        private final Path output;
        private final Map<String, String> values;

        private Config(Path output, Map<String, String> values) {
            this.output = output;
            this.values = values;
        }

        private static Config parse(String[] args) {
            if (args.length % 2 != 0) {
                throw new IllegalArgumentException("probe arguments must be --name value pairs");
            }
            Map<String, String> values = new TreeMap<String, String>();
            for (int index = 0; index < args.length; index += 2) {
                String option = args[index];
                if (!option.startsWith("--")) {
                    throw new IllegalArgumentException("probe option lacks -- prefix");
                }
                String name = option.substring(2);
                if (!ARGUMENTS.contains(name)) {
                    throw new IllegalArgumentException("unknown probe option " + option);
                }
                String value = args[index + 1];
                if (value.isEmpty() || values.put(name, value) != null) {
                    throw new IllegalArgumentException("empty or duplicate probe option " + option);
                }
            }
            if (!values.keySet().equals(ARGUMENTS)) {
                throw new IllegalArgumentException("probe arguments are not the complete V1 surface");
            }
            String side = values.get("side");
            if (!("client".equals(side)
                    || "dedicated_server".equals(side)
                    || "integrated_server".equals(side))) {
                throw new IllegalArgumentException("unsupported logical side");
            }
            requireSha256(values.get("candidate-toolchain-lock-sha256"));
            requireSha256(values.get("component-topology-receipt-sha256"));
            String topologyId = values.get("component-topology-receipt-id");
            String prefix = "workbench-mixin-topology-receipt:sha256:";
            if (!topologyId.startsWith(prefix)) {
                throw new IllegalArgumentException("invalid topology receipt ID");
            }
            requireSha256(topologyId.substring(prefix.length()));
            return new Config(Paths.get(values.get("output")), values);
        }

        private Map<String, Object> session() {
            Map<String, Object> session = new TreeMap<String, Object>();
            session.put(
                    "candidate_toolchain_lock_sha256",
                    values.get("candidate-toolchain-lock-sha256")
            );
            session.put(
                    "component_topology_receipt_id",
                    values.get("component-topology-receipt-id")
            );
            session.put(
                    "component_topology_receipt_sha256",
                    values.get("component-topology-receipt-sha256")
            );
            session.put("launch_id", values.get("launch-id"));
            session.put("profile_id", values.get("profile-id"));
            session.put("session_id", values.get("session-id"));
            session.put("side", values.get("side"));
            return session;
        }

        private static void requireSha256(String value) {
            if (value == null || !value.matches("[0-9a-f]{64}")) {
                throw new IllegalArgumentException("invalid lowercase SHA-256 argument");
            }
        }
    }

    private static final class Failure {
        private final String stage;
        private final String exceptionClass;
        private final String message;

        private Failure(String stage, String exceptionClass, String message) {
            this.stage = stage;
            this.exceptionClass = exceptionClass;
            this.message = message;
        }

        private static Failure from(String stage, Throwable throwable) {
            String message = throwable.getMessage();
            if (message == null || message.isEmpty()) {
                message = throwable.getClass().getName();
            }
            return new Failure(stage, throwable.getClass().getName(), message);
        }

        private Map<String, Object> value() {
            Map<String, Object> result = new TreeMap<String, Object>();
            result.put("exception_class", exceptionClass);
            result.put("message", message);
            result.put("stage", stage);
            return result;
        }
    }

    private static final class ProbeFailure extends Exception {
        private final Failure failure;

        private ProbeFailure(Failure failure) {
            super(failure.message);
            this.failure = failure;
        }

        private ProbeFailure(String stage, String exceptionClass, String message) {
            this(new Failure(stage, exceptionClass, message));
        }
    }
}
