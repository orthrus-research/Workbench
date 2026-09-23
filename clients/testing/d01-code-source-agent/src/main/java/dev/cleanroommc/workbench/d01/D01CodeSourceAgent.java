package dev.cleanroommc.workbench.d01;

import java.io.IOException;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.IllegalClassFormatException;
import java.lang.instrument.Instrumentation;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;

/**
 * Observes the real DailyLoopProbe ProtectionDomain inside the owned game JVM.
 *
 * This test agent is deliberately independent of the fixture artifact. It cannot
 * supply or replace fixture classes; it only rejects a non-JAR/duplicate origin
 * and retains the exact local JAR URI and bytes seen by the defining loader.
 */
public final class D01CodeSourceAgent {
    private static final String TARGET_BINARY = "dev.workbench.dailyloop.DailyLoopProbe";
    private static final String TARGET_INTERNAL = "dev/workbench/dailyloop/DailyLoopProbe";
    private static final String TARGET_RESOURCE = TARGET_INTERNAL + ".class";

    private D01CodeSourceAgent() {
    }

    public static void premain(String arguments, Instrumentation instrumentation) {
        require("workbench.d01.runtimeMode", "jar-only");
        final Path expected = Path.of(require("workbench.d01.expectedArtifact", null))
                .toAbsolutePath().normalize();
        final String expectedSha256 = require("workbench.d01.expectedArtifactSha256", null);
        final Path capture = Path.of(require("workbench.d01.probeCapture", null))
                .toAbsolutePath().normalize();
        final int rawClasspathOrigins = Integer.parseInt(
                require("workbench.d01.rawClasspathFixtureOriginCount", "0")
        );
        final int extraPathOrigins = Integer.parseInt(
                require("workbench.d01.extraPathFixtureOriginCount", "1")
        );
        instrumentation.addTransformer(new ClassFileTransformer() {
            @Override
            public byte[] transform(
                    ClassLoader loader,
                    String className,
                    Class<?> classBeingRedefined,
                    ProtectionDomain protectionDomain,
                    byte[] classfileBuffer
            ) throws IllegalClassFormatException {
                if (!TARGET_INTERNAL.equals(className)) {
                    return null;
                }
                try {
                    observe(loader, protectionDomain, expected, expectedSha256,
                            capture, rawClasspathOrigins, extraPathOrigins);
                } catch (Exception error) {
                    retainFailure(capture, error);
                    throw new IllegalClassFormatException(
                            "D01 CodeSource proof failed closed: " + error.getMessage()
                    );
                }
                return null;
            }
        }, false);
    }

    private static void observe(
            ClassLoader loader,
            ProtectionDomain domain,
            Path expected,
            String expectedSha256,
            Path capture,
            int rawClasspathOrigins,
            int extraPathOrigins
    ) throws Exception {
        if (domain == null || domain.getCodeSource() == null
                || domain.getCodeSource().getLocation() == null) {
            throw new IllegalStateException("fixture ProtectionDomain has no CodeSource");
        }
        URI location = domain.getCodeSource().getLocation().toURI();
        String expectedEntry = "!/" + TARGET_RESOURCE;
        String rawLocation = location.toASCIIString();
        if (!"jar".equals(location.getScheme()) || location.getAuthority() != null
                || location.getQuery() != null || location.getFragment() != null
                || !rawLocation.endsWith(expectedEntry)) {
            throw new IllegalStateException(
                    "fixture CodeSource is not one exact JAR-entry URI: " + location
            );
        }
        URI archiveLocation = URI.create(
                rawLocation.substring("jar:".length(), rawLocation.length() - expectedEntry.length())
        );
        if (!"file".equals(archiveLocation.getScheme())
                || archiveLocation.getAuthority() != null
                || archiveLocation.getQuery() != null
                || archiveLocation.getFragment() != null) {
            throw new IllegalStateException(
                    "fixture CodeSource archive is not one local file URI: " + archiveLocation
            );
        }
        Path expectedReal = expected.toRealPath(LinkOption.NOFOLLOW_LINKS);
        Path observedReal = Path.of(archiveLocation).toRealPath(LinkOption.NOFOLLOW_LINKS);
        if (!Files.isRegularFile(observedReal, LinkOption.NOFOLLOW_LINKS)
                || !observedReal.getFileName().toString().endsWith(".jar")
                || !observedReal.equals(expectedReal)) {
            throw new IllegalStateException("fixture CodeSource is not the exact installed JAR");
        }
        String observedSha256 = sha256(observedReal);
        if (!observedSha256.equals(expectedSha256)) {
            throw new IllegalStateException("fixture CodeSource bytes differ from the installed JAR");
        }
        List<String> resources = resources(loader, TARGET_RESOURCE);
        if (resources.size() != 1 || !resources.get(0).equals(rawLocation)) {
            throw new IllegalStateException(
                    "defining loader exposes duplicate or non-JAR fixture resources: "
                            + resources
            );
        }
        if (rawClasspathOrigins != 0) {
            throw new IllegalStateException("raw launch classpath retained fixture origins");
        }
        if (extraPathOrigins != 1) {
            throw new IllegalStateException("Cleanroom extra path did not retain one fixture origin");
        }
        String line = String.join(" ",
                "WORKBENCH_D01_CODE_SOURCE_PROBE",
                "runtime_mode=jar-only",
                "class=" + TARGET_BINARY,
                "code_source_uri=" + rawLocation,
                "code_source_jar_uri=" + expectedReal.toUri(),
                "code_source_sha256=" + observedSha256,
                "expected_sha256=" + expectedSha256,
                "fixture_origin_count=" + resources.size(),
                "raw_classpath_fixture_origin_count=" + rawClasspathOrigins,
                "extra_path_fixture_origin_count=" + extraPathOrigins,
                "outcome=passed"
        ) + "\n";
        Files.write(
                capture,
                line.getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE
        );
        System.out.print(line);
        System.out.flush();
    }

    private static void retainFailure(Path capture, Exception error) {
        String message = String.valueOf(error.getMessage())
                .replaceAll("[\\r\\n\\t ]+", " ")
                .trim();
        if (message.length() > 1024) {
            message = message.substring(0, 1024);
        }
        String line = "WORKBENCH_D01_CODE_SOURCE_PROBE outcome=failed error="
                + message + "\n";
        try {
            Files.write(
                    capture,
                    line.getBytes(StandardCharsets.UTF_8),
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE
            );
            System.err.print(line);
            System.err.flush();
        } catch (IOException ignored) {
            // The original observation failure remains authoritative.  The
            // surrounding owner also treats an absent success capture as a
            // hard failure, so capture I/O cannot turn this into a pass.
        }
    }

    private static List<String> resources(ClassLoader loader, String name) throws IOException {
        Enumeration<URL> values = loader == null
                ? ClassLoader.getSystemResources(name)
                : loader.getResources(name);
        List<String> result = new ArrayList<>();
        while (values.hasMoreElements()) {
            result.add(values.nextElement().toExternalForm());
        }
        return result;
    }

    private static String require(String name, String expected) {
        String value = System.getProperty(name);
        if (value == null || value.isBlank() || (expected != null && !expected.equals(value))) {
            throw new IllegalStateException("missing or invalid system property " + name);
        }
        return value;
    }

    private static String sha256(Path path) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (java.io.InputStream input = Files.newInputStream(path)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                if (count > 0) {
                    digest.update(buffer, 0, count);
                }
            }
        }
        StringBuilder result = new StringBuilder("sha256:");
        for (byte value : digest.digest()) {
            result.append(String.format("%02x", value & 0xff));
        }
        return result.toString();
    }
}
