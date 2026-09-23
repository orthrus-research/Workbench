package dev.workbench.cleanmixp0;

public final class LateTargets {

    private LateTargets() {
    }

    public static final class Target {
        public int value() {
            P0Counters.originalBodies++;
            return 7;
        }
    }

    public static final class Trigger {
        public int value() {
            return 11;
        }
    }
}
