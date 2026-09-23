package dev.workbench.cleanmixhandler.target;

import dev.workbench.cleanmixhandler.HarnessCounters;

public final class ChildTarget extends ParentTarget {

    @Override
    public void control() {
        HarnessCounters.childControlCount++;
    }
}
