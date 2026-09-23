package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import static org.junit.jupiter.api.Assertions.*;

class NativeConsoleCaptureTest {
    @Test void mvpCaptureRetainsCompleteUtf8BeyondFormerAllocations() throws Exception {
        var capture = new NativeConsoleCapture();
        byte[] input = "original warning µ 😀\n".repeat(100000).getBytes(StandardCharsets.UTF_8);
        // Cross former head/tail and UTF-8 boundaries using arbitrary stream writes.
        for (int offset=0; offset<input.length; offset+=8191)
            capture.write(input,offset,Math.min(8191,input.length-offset));
        var report=capture.snapshot();
        assertEquals(true,report.get("complete"));
        assertEquals(input.length,report.get("retainedBytes"));
        assertEquals(new String(input,StandardCharsets.UTF_8),report.get("head"));
        assertEquals("",report.get("tail"));assertEquals(Json.bytesDigest(input),report.get("sha256"));
    }

    @Test void compilerCascadeAllocationRetainsAllBytesAndStillMarksOverflow() throws Exception {
        var capture = new NativeConsoleCapture(262144);
        byte[] input = "original compiler failure\n".repeat(7000).getBytes(StandardCharsets.UTF_8);
        capture.write(input); var report = capture.snapshot();
        assertEquals(true, report.get("complete")); assertEquals(Json.bytesDigest(input), report.get("sha256"));
        assertEquals(new String(input, StandardCharsets.UTF_8), report.get("head").toString() + report.get("tail"));
        capture.write(input); report = capture.snapshot();
        assertEquals(false, report.get("complete")); assertEquals(262144, report.get("retainedBytes"));
    }
    @Test void ordinaryConsoleAndItsDigestRemainComplete() throws Exception {
        var capture = new NativeConsoleCapture(); byte[] input = "original native warning\n".getBytes(StandardCharsets.UTF_8);
        capture.write(input); var report = capture.snapshot();
        assertEquals(true, report.get("complete")); assertEquals((long)input.length, report.get("totalBytes"));
        assertEquals("original native warning\n", report.get("head")); assertEquals("", report.get("tail"));
        assertEquals(Json.bytesDigest(input), report.get("sha256")); assertEquals(report, capture.snapshot());
    }
    @Test void overflowRetainsTheBeginningAndFinalCauseWithExplicitIncompleteEvidence() throws Exception {
        var capture = new NativeConsoleCapture(65536); byte[] input = new byte[100000]; Arrays.fill(input, (byte)'x');
        input[0] = 'A'; input[input.length - 1] = 'Z'; capture.write(input);
        var report = capture.snapshot(); assertEquals(false, report.get("complete"));
        assertEquals(100000L, report.get("totalBytes")); assertEquals(65536, report.get("retainedBytes"));
        assertEquals("A" + "x".repeat(32767), report.get("head"));
        assertEquals("x".repeat(32767) + "Z", report.get("tail"));
        assertEquals(Json.bytesDigest(input), report.get("sha256"));
    }
    @Test void preInitAllocationRetainsLongerOriginalConsoleWithinTheSameResponseLimit() throws Exception {
        var capture = new NativeConsoleCapture(131072); byte[] input = new byte[105455]; Arrays.fill(input, (byte)'w');
        capture.write(input); var report = capture.snapshot();
        assertEquals(true, report.get("complete")); assertEquals(input.length, report.get("retainedBytes"));
        assertEquals("w".repeat(input.length), report.get("head").toString() + report.get("tail"));
        assertEquals(Json.bytesDigest(input), report.get("sha256"));
        capture.write(input); report = capture.snapshot();
        assertEquals(false, report.get("complete")); assertEquals(131072, report.get("retainedBytes"));
    }
}
