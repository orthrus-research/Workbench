package dev.workbench.worldgenprototype.world.plan;

import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class WorldStudioPlansTest {

    @Test
    void publishingAnIdenticalPlanIsAnIdempotentNoOp() {
        WorldStudioPlan changed = WorldStudioPlan.defaultsBuilder()
                .profile("idempotency_test")
                .build();
        WorldStudioPlans.Snapshot first = WorldStudioPlans.publish(changed);
        WorldStudioPlans.Snapshot repeated = WorldStudioPlans.publish(changed);

        assertSame(first, repeated);
        assertTrue(first.version() >= 2L);
    }
}
