package dev.workbench.cleanmixp0;

public final class PhaseTargets {

    private PhaseTargets() {
    }

    public static final class Preinit {
        public int value() {
            P0Counters.originalBodies++;
            return 1;
        }
    }

    public static final class Init {
        public int value() {
            P0Counters.originalBodies++;
            return 2;
        }
    }

    public static final class Default {
        public int value() {
            P0Counters.originalBodies++;
            return 3;
        }
    }
}
