package dev.workbench.worldgenprototype.world.plan;

import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/** Atomically published plans; an individual chunk captures one snapshot. */
public final class WorldStudioPlans {

    private static final AtomicLong NEXT_VERSION = new AtomicLong(1L);
    private static final AtomicReference<Snapshot> CURRENT = new AtomicReference<>(
            new Snapshot(1L, WorldStudioPlan.defaults())
    );

    private WorldStudioPlans() {
    }

    public static Snapshot current() {
        return CURRENT.get();
    }

    public static synchronized Snapshot publish(WorldStudioPlan plan) {
        if (plan == null) {
            throw new IllegalArgumentException("plan must not be null");
        }
        Snapshot current = CURRENT.get();
        if (current.hash().equals(plan.hash())) {
            return current;
        }
        Snapshot snapshot = new Snapshot(NEXT_VERSION.incrementAndGet(), plan);
        CURRENT.set(snapshot);
        return snapshot;
    }

    public static final class Snapshot {

        private final long version;
        private final WorldStudioPlan plan;

        private Snapshot(long version, WorldStudioPlan plan) {
            this.version = version;
            this.plan = plan;
        }

        public long version() {
            return version;
        }

        public WorldStudioPlan plan() {
            return plan;
        }

        public String hash() {
            return plan.hash();
        }
    }
}
