package dev.workbench.crucible.runtimegraph;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** SHA-256 helpers without transport-dependent formatting. */
public final class Hashing {
    private Hashing() {}

    public static String sha256(byte[] value) {
        MessageDigest digest = newDigest();
        digest.update(value);
        return hex(digest.digest());
    }

    public static String sha256(Path path) throws IOException {
        MessageDigest digest = newDigest();
        byte[] buffer = new byte[1024 * 1024];
        InputStream input = Files.newInputStream(path);
        try {
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read != 0) digest.update(buffer, 0, read);
            }
        } finally {
            input.close();
        }
        return hex(digest.digest());
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("runtime lacks SHA-256", exception);
        }
    }

    private static String hex(byte[] value) {
        StringBuilder result = new StringBuilder(value.length * 2);
        for (byte item : value) result.append(String.format("%02x", Integer.valueOf(item & 0xff)));
        return result.toString();
    }
}
