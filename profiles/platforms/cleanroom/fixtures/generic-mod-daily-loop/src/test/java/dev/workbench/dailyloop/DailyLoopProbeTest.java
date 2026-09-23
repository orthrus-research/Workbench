package dev.workbench.dailyloop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

final class DailyLoopProbeTest {
    @Test
    void deterministicMarkerBindsExactRegistryObjectAndFixtureRevision() {
        assertEquals(
            "WORKBENCH_DAILY_LOOP_COMMON_READY "
                + "registry=workbench_daily_loop:probe_block fixture=1.0.0",
            DailyLoopProbe.markerFor("workbench_daily_loop:probe_block")
        );
    }
}
