package dev.workbench.cleanmixhandler.target;

import dev.workbench.cleanmixhandler.HarnessCounters;

public class ParentTarget {

    public void exercise() {
        HarnessCounters.originalBodyCount++;
    }

    public void control() {
        HarnessCounters.parentControlCount++;
    }
}
