package dev.cleanroommc.workbench.intellij.community;

import java.util.concurrent.atomic.AtomicLong;

/** An old or cancelled analysis can never replace a newer editor observation. */
final class LocalReviewEpoch {
    private final AtomicLong sequence = new AtomicLong();
    long invalidate() { return sequence.incrementAndGet(); }
    boolean current(long ticket) { return sequence.get() == ticket; }
}
