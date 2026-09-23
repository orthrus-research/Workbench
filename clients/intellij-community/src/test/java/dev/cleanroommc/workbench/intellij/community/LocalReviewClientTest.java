package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import java.util.List;

final class LocalReviewClientTest {
    @Test void explicitSessionBaselineAndNoMutation() {
        String session = "work-session-v2-" + "a".repeat(32);
        assertEquals(List.of("context", "run", session, "--", "review", "local", "--baseline-ref=HEAD"), LocalReviewClient.arguments(session, "HEAD", null));
        assertThrows(IllegalArgumentException.class, () -> LocalReviewClient.arguments("latest", "HEAD", null));
        assertThrows(IllegalArgumentException.class, () -> LocalReviewClient.arguments(session, "HEAD\nmain", null));
        assertThrows(IllegalArgumentException.class, () -> LocalReviewClient.arguments(session, "HEAD", "latest"));
    }

    @Test void oldAndCancelledAnalysisNeverBecomesCurrent() {
        var epoch = new LocalReviewEpoch();
        long first = epoch.invalidate();
        long second = epoch.invalidate();
        assertFalse(epoch.current(first));
        assertTrue(epoch.current(second));
        epoch.invalidate();
        assertFalse(epoch.current(second));
    }
}
