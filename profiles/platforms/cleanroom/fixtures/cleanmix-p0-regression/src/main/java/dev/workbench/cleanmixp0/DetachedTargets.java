package dev.workbench.cleanmixp0;

/**
 * Target types deliberately unrelated in the JVM hierarchy. The corresponding
 * mixins inherit from one another, allowing target application order to differ
 * from class-definition superclass order.
 */
public final class DetachedTargets {

    private DetachedTargets() {
    }

    public static final class TwoParent {
        public void exercise() {
            P0Counters.originalBodies++;
        }

        public void control() {
            P0Counters.detachedTwoParentControl++;
        }
    }

    public static final class TwoChild {
        public void exercise() {
            P0Counters.originalBodies++;
        }

        public void control() {
            P0Counters.detachedTwoChildControl++;
        }
    }

    public static final class ThreeBase {
        public void exercise() {
            P0Counters.originalBodies++;
        }

        public void control() {
            P0Counters.detachedThreeBaseControl++;
        }
    }

    public static final class ThreeMiddle {
        public void exercise() {
            P0Counters.originalBodies++;
        }

        public void control() {
            P0Counters.detachedThreeMiddleControl++;
        }
    }

    public static final class ThreeLeaf {
        public void exercise() {
            P0Counters.originalBodies++;
        }

        public void control() {
            P0Counters.detachedThreeLeafControl++;
        }
    }
}
