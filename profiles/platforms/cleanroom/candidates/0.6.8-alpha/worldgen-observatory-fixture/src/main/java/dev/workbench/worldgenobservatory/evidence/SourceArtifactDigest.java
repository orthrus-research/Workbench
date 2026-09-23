package dev.workbench.worldgenobservatory.evidence;

import java.io.InputStream;
import java.net.JarURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.CodeSource;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;
import javax.annotation.Nullable;

/** Stable content identity for a jar or an Unimined source-set directory. */
public final class SourceArtifactDigest {

    private SourceArtifactDigest() {
    }

    @Nullable
    public static String sha256ClassSource(Class<?> type) {
        Path source = classSourcePath(type);
        return source == null ? null : sha256(source);
    }

    /** Resolve the jar or classpath root from which a runtime class was read. */
    @Nullable
    public static Path classSourcePath(Class<?> type) {
        try {
            CodeSource source = type.getProtectionDomain().getCodeSource();
            if (source != null && source.getLocation() != null) {
                return Paths.get(source.getLocation().toURI());
            }
        } catch (Throwable ignored) {
            // Foundation can define transformed classes without a CodeSource.
        }
        try {
            String resourceName = type.getName().replace('.', '/') + ".class";
            URL resource = type.getResource('/' + resourceName);
            if (resource == null) {
                return null;
            }
            if ("jar".equals(resource.getProtocol())) {
                return Paths.get(
                    ((JarURLConnection) resource.openConnection()).getJarFileURL().toURI()
                );
            }
            if (!"file".equals(resource.getProtocol())) {
                return null;
            }
            Path root = Paths.get(resource.toURI()).toAbsolutePath().normalize();
            int segmentCount = resourceName.split("/").length;
            for (int index = 0; index < segmentCount && root != null; index++) {
                root = root.getParent();
            }
            return root;
        } catch (Throwable ignored) {
            return null;
        }
    }

    @Nullable
    public static String sha256(Path source) {
        try {
            Path normalized = source.toAbsolutePath().normalize();
            if (Files.isRegularFile(normalized, LinkOption.NOFOLLOW_LINKS)) {
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                updateFileBytes(digest, normalized, Files.size(normalized));
                return hex(digest.digest());
            }
            if (!Files.isDirectory(normalized, LinkOption.NOFOLLOW_LINKS)) {
                return null;
            }

            List<Entry> entries = new ArrayList<>();
            try (Stream<Path> paths = Files.walk(normalized)) {
                paths.filter(path -> Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
                    .forEach(path -> entries.add(new Entry(normalized, path)));
            }
            entries.sort(Comparator.comparing(
                entry -> entry.relativeUtf8,
                SourceArtifactDigest::compareUnsigned
            ));

            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Entry entry : entries) {
                updateInt(digest, entry.relativeUtf8.length);
                digest.update(entry.relativeUtf8);
                long length = Files.size(entry.path);
                updateLong(digest, length);
                updateFileBytes(digest, entry.path, length);
            }
            return hex(digest.digest());
        } catch (Throwable unavailable) {
            return null;
        }
    }

    private static void updateFileBytes(MessageDigest digest, Path path, long expectedLength)
        throws Exception {
        long observedLength = 0L;
        try (InputStream input = Files.newInputStream(path)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                digest.update(buffer, 0, read);
                observedLength += read;
            }
        }
        if (observedLength != expectedLength) {
            throw new IllegalStateException("Source changed while its digest was being computed");
        }
    }

    private static int compareUnsigned(byte[] left, byte[] right) {
        int length = Math.min(left.length, right.length);
        for (int index = 0; index < length; index++) {
            int comparison = Integer.compare(left[index] & 0xFF, right[index] & 0xFF);
            if (comparison != 0) {
                return comparison;
            }
        }
        return Integer.compare(left.length, right.length);
    }

    private static void updateInt(MessageDigest digest, int value) {
        digest.update((byte) (value >>> 24));
        digest.update((byte) (value >>> 16));
        digest.update((byte) (value >>> 8));
        digest.update((byte) value);
    }

    private static void updateLong(MessageDigest digest, long value) {
        for (int shift = 56; shift >= 0; shift -= 8) {
            digest.update((byte) (value >>> shift));
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte element : bytes) {
            value.append(Character.forDigit((element >>> 4) & 0x0F, 16));
            value.append(Character.forDigit(element & 0x0F, 16));
        }
        return value.toString();
    }

    private static final class Entry {
        private final Path path;
        private final byte[] relativeUtf8;

        private Entry(Path root, Path path) {
            this.path = path;
            this.relativeUtf8 = root.relativize(path)
                .toString()
                .replace(java.io.File.separatorChar, '/')
                .getBytes(StandardCharsets.UTF_8);
        }
    }
}
