package research.orthrus.axiom;

import java.io.OutputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Complete MVP console evidence; explicit bounded captures remain available to controlled probes. */
final class NativeConsoleCapture extends OutputStream {
    private final int half;
    private final byte[] first, last;
    private final MessageDigest digest;
    private final ByteArrayOutputStream complete;
    private long count;
    NativeConsoleCapture() { this(0); }
    NativeConsoleCapture(int maximumBytes) {
        if (maximumBytes != 0 && maximumBytes != 65536 && maximumBytes != 131072 && maximumBytes != 262144)
            throw new IllegalArgumentException("Console allocation differs");
        complete = maximumBytes == 0 ? new ByteArrayOutputStream() : null;
        half = maximumBytes / 2; first = new byte[half]; last = new byte[half];
        try { digest = MessageDigest.getInstance("SHA-256"); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
    @Override public synchronized void write(int value) {
        byte next = (byte)value;
        digest.update(next);
        if (complete != null) complete.write(value);
        else if (count < half) first[(int)count] = next;
        else last[(int)((count - half) % half)] = next;
        count++;
    }
    @Override public synchronized void write(byte[] bytes, int offset, int length) {
        Objects.checkFromIndexSize(offset, length, bytes.length);
        for (int i = offset; i < offset + length; i++) write(bytes[i]);
    }
    synchronized Map<String,Object> snapshot() {
        if (complete != null) return Map.of("complete", true, "totalBytes", count, "retainedBytes", complete.size(),
                "encoding", "utf-8", "head", complete.toString(StandardCharsets.UTF_8), "tail", "", "sha256", currentDigest());
        int head = (int)Math.min(count, half), tail = (int)Math.min(Math.max(count - half, 0), half);
        byte[] ending = new byte[tail];
        int start = count <= 2 * half ? 0 : (int)((count - half) % half);
        for (int i = 0; i < tail; i++) ending[i] = last[(start + i) % half];
        return Map.of("complete", count <= 2 * half, "totalBytes", count, "retainedBytes", head + tail,
                "encoding", "utf-8", "head", new String(first, 0, head, StandardCharsets.UTF_8),
                "tail", new String(ending, StandardCharsets.UTF_8), "sha256", currentDigest());
    }
    private String currentDigest() {
        try { return HexFormat.of().formatHex(((MessageDigest)digest.clone()).digest()); }
        catch (CloneNotSupportedException impossible) { throw new IllegalStateException(impossible); }
    }
}
