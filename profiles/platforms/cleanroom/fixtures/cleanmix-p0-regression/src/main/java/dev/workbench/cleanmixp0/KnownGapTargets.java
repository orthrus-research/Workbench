package dev.workbench.cleanmixp0;

public final class KnownGapTargets {

    private KnownGapTargets() {
    }

    public static class LazyParent {
        public void exercise() {
            P0Counters.originalBodies++;
        }
    }

    public static final class LazyChild extends LazyParent {
    }

    public static final class Reentrant {
        public int value() {
            P0Counters.originalBodies++;
            return 23;
        }
    }

    public static class ThreeBase {
        public void exercise() {
            P0Counters.originalBodies++;
        }

        public void control() {
            P0Counters.baseControl++;
        }
    }

    public static class ThreeMiddle extends ThreeBase {
        @Override
        public void control() {
            P0Counters.middleControl++;
        }
    }

    public static final class ThreeLeaf extends ThreeMiddle {
        @Override
        public void control() {
            P0Counters.leafControl++;
        }
    }
}
