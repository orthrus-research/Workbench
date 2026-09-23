package dev.workbench.crucible.runtimegraph;

import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.regex.Pattern;

/** Fail-closed system-property configuration for an armed capture. */
public final class CaptureConfiguration {
    private static final String PREFIX = "workbench.runtimeGraph.";
    private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z0-9._:-]{1,160}");
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");

    private final boolean enabled;
    private final String captureId;
    private final String launchId;
    private final String inputManifestSha256;
    private final String candidateLockSha256;
    private final String adapterProfileSha256;
    private final Path output;
    private final Path staging;
    private final String experiment;
    private final boolean compatibilityOnly;
    private final String shutdownCompatibilityId;
    private final String shutdownCompatibilityManifestSha256;
    private final String shutdownCompatibilityLaunchSha256;

    private CaptureConfiguration(
        boolean enabled,
        String captureId,
        String launchId,
        String inputManifestSha256,
        String candidateLockSha256,
        String adapterProfileSha256,
        Path output,
        Path staging,
        String experiment,
        boolean compatibilityOnly,
        String shutdownCompatibilityId,
        String shutdownCompatibilityManifestSha256,
        String shutdownCompatibilityLaunchSha256
    ) {
        this.enabled = enabled;
        this.captureId = captureId;
        this.launchId = launchId;
        this.inputManifestSha256 = inputManifestSha256;
        this.candidateLockSha256 = candidateLockSha256;
        this.adapterProfileSha256 = adapterProfileSha256;
        this.output = output;
        this.staging = staging;
        this.experiment = experiment;
        this.compatibilityOnly = compatibilityOnly;
        this.shutdownCompatibilityId = shutdownCompatibilityId;
        this.shutdownCompatibilityManifestSha256 = shutdownCompatibilityManifestSha256;
        this.shutdownCompatibilityLaunchSha256 = shutdownCompatibilityLaunchSha256;
    }

    public static CaptureConfiguration read() {
        boolean enabled = booleanProperty("enabled");
        boolean compatibilityOnly = booleanProperty("compatibility_only");
        if (enabled && compatibilityOnly) {
            throw new IllegalArgumentException(
                "runtime graph capture and compatibility-only modes are mutually exclusive"
            );
        }
        if (!enabled && !compatibilityOnly) {
            return new CaptureConfiguration(
                false, "", "", "", "", "", null, null, "none",
                false, "", "", ""
            );
        }
        if (compatibilityOnly) {
            return new CaptureConfiguration(
                false, "", "", "", "", "", null, null, "none",
                true,
                identifier("shutdown_compatibility_id"),
                sha256("shutdown_compatibility_manifest_sha256"),
                sha256("shutdown_compatibility_launch_sha256")
            );
        }
        String captureId = identifier("capture_id");
        String launchId = identifier("launch_id");
        String inputManifest = sha256("input_manifest_sha256");
        String candidateLock = sha256("candidate_lock_sha256");
        String adapterProfile = sha256("adapter_profile_sha256");
        String rawOutput = required("output");
        Path output = Paths.get(rawOutput);
        if (!output.isAbsolute()) {
            throw new IllegalArgumentException("runtime graph output must be absolute");
        }
        output = output.normalize();
        Path parent = output.getParent();
        if (parent == null || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("runtime graph output parent must be an existing directory");
        }
        if (Files.exists(output, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("runtime graph output must not exist: " + output);
        }
        Path staging = parent.resolve(output.getFileName() + ".staging-" + captureId);
        if (Files.exists(staging, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("runtime graph staging path already exists: " + staging);
        }
        String experiment = System.getProperty(PREFIX + "experiment", "none");
        if (!"none".equals(experiment) && !"program-reload".equals(experiment)
            && !"client-presentation".equals(experiment)) {
            throw new IllegalArgumentException("runtime graph experiment is unsupported");
        }
        return new CaptureConfiguration(
            true,
            captureId,
            launchId,
            inputManifest,
            candidateLock,
            adapterProfile,
            output,
            staging,
            experiment,
            false,
            "",
            "",
            ""
        );
    }

    private static boolean booleanProperty(String name) {
        String value = System.getProperty(PREFIX + name, "false");
        if (!"true".equals(value) && !"false".equals(value)) {
            throw new IllegalArgumentException(
                "runtime graph " + name + " must be exactly true or false"
            );
        }
        return "true".equals(value);
    }

    private static String required(String name) {
        String value = System.getProperty(PREFIX + name);
        if (value == null || value.isEmpty() || value.indexOf('\0') >= 0
            || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException("missing/invalid runtime graph property " + name);
        }
        return value;
    }

    private static String identifier(String name) {
        String value = required(name);
        if (!IDENTIFIER.matcher(value).matches()) {
            throw new IllegalArgumentException("runtime graph " + name + " is not a bounded identifier");
        }
        return value;
    }

    private static String sha256(String name) {
        String value = required(name);
        if (!SHA256.matcher(value).matches()) {
            throw new IllegalArgumentException("runtime graph " + name + " is not a SHA-256");
        }
        return value;
    }

    public boolean isEnabled() { return enabled; }
    public String getCaptureId() { return captureId; }
    public String getLaunchId() { return launchId; }
    public String getInputManifestSha256() { return inputManifestSha256; }
    public String getCandidateLockSha256() { return candidateLockSha256; }
    public String getAdapterProfileSha256() { return adapterProfileSha256; }
    public Path getOutput() { return output; }
    public Path getStaging() { return staging; }
    public boolean isProgramReloadExperiment() { return "program-reload".equals(experiment); }
    public boolean isClientPresentationCapture() {
        return "client-presentation".equals(experiment);
    }
    public String getExperiment() { return experiment; }
    public boolean isCompatibilityOnly() { return compatibilityOnly; }
    public String getShutdownCompatibilityId() { return shutdownCompatibilityId; }
    public String getShutdownCompatibilityManifestSha256() {
        return shutdownCompatibilityManifestSha256;
    }
    public String getShutdownCompatibilityLaunchSha256() {
        return shutdownCompatibilityLaunchSha256;
    }
    public String getPhysicalSide() {
        return isClientPresentationCapture() ? "client" : "dedicated_server";
    }
}
