package dev.workbench.crucible.runtimegraph.provenance;

import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

/**
 * Launch-local custody for Groovy execution and mutation events.
 *
 * The trace deliberately retains runtime object identity until the final
 * adapter checkpoint. Atlas-facing encoders can then bind an operation to the
 * exact surviving material or recipe occurrence instead of guessing from a
 * value that happened to compare equal.
 */
public final class ProgramProvenanceTrace {
    private static final int MAX_STACK_FRAMES = 512;

    private static final List<Epoch> EPOCHS = new ArrayList<Epoch>();
    private static final List<Execution> EXECUTIONS = new ArrayList<Execution>();
    private static final List<Mutation> MUTATIONS = new ArrayList<Mutation>();
    private static final Map<Object, DeferredRegistration> DEFERRED_REGISTRATIONS =
        new IdentityHashMap<Object, DeferredRegistration>();
    private static final ThreadLocal<Execution> CURRENT_EXECUTION =
        new ThreadLocal<Execution>();

    private static Epoch currentEpoch;
    private static long sequence;

    private ProgramProvenanceTrace() {}

    public static synchronized void beginEpoch(
        String stage,
        boolean reloadable,
        boolean initialLoad
    ) {
        if (currentEpoch != null) {
            throw new IllegalStateException("Groovy execution epochs overlap");
        }
        Epoch epoch = new Epoch(
            EPOCHS.size(),
            nextSequence(),
            "groovy-load-stage",
            stage,
            reloadable,
            initialLoad,
            null
        );
        EPOCHS.add(epoch);
        currentEpoch = epoch;
    }

    public static synchronized void endEpoch(String stage) {
        if (currentEpoch == null || !currentEpoch.stage.equals(stage)) {
            throw new IllegalStateException("Groovy execution epoch end does not match its start");
        }
        if (CURRENT_EXECUTION.get() != null) {
            throw new IllegalStateException("Groovy execution remained open at epoch end");
        }
        currentEpoch.endSequence = nextSequence();
        currentEpoch.completed = true;
        currentEpoch = null;
    }

    public static synchronized Execution beginExecution(String kind, String className) {
        if (currentEpoch == null) {
            throw new IllegalStateException("Groovy script execution occurred outside an epoch");
        }
        if (CURRENT_EXECUTION.get() != null) {
            throw new IllegalStateException("nested Groovy script execution is unsupported");
        }
        Execution execution = new Execution(
            EXECUTIONS.size(),
            nextSequence(),
            currentEpoch.ordinal,
            kind,
            className,
            null,
            null,
            null,
            null,
            null
        );
        EXECUTIONS.add(execution);
        CURRENT_EXECUTION.set(execution);
        return execution;
    }

    public static synchronized void endExecution(
        Execution execution,
        Throwable failure
    ) {
        if (CURRENT_EXECUTION.get() != execution) {
            throw new IllegalStateException("Groovy execution end does not match its start");
        }
        execution.endSequence = nextSequence();
        execution.completed = true;
        execution.failureClass = failure == null ? null : failure.getClass().getName();
        CURRENT_EXECUTION.remove();
    }

    public static synchronized void recordMutation(
        String operation,
        Object target,
        Object... subjects
    ) {
        if (currentEpoch == null) return;
        Execution execution = CURRENT_EXECUTION.get();
        StackTraceElement[] raw = new Throwable().getStackTrace();
        int count = Math.min(raw.length, MAX_STACK_FRAMES);
        List<StackTraceElement> frames = new ArrayList<StackTraceElement>(count);
        for (int index = 0; index < count; index++) frames.add(raw[index]);
        List<Object> retainedSubjects = new ArrayList<Object>();
        if (subjects != null) {
            for (Object subject : subjects) retainedSubjects.add(subject);
        }
        Mutation mutation = new Mutation(
            MUTATIONS.size(),
            nextSequence(),
            currentEpoch.ordinal,
            execution == null ? null : Long.valueOf(execution.ordinal),
            operation,
            target,
            retainedSubjects,
            frames,
            raw.length > MAX_STACK_FRAMES
        );
        MUTATIONS.add(mutation);
        if (execution != null) execution.mutationCount++;
    }

    /** Retain the script execution that created a deferred Groovy event listener. */
    public static synchronized void registerDeferredListener(
        Object listener,
        String eventBus,
        String priority,
        String declaredEventClass
    ) {
        if (listener == null) throw new IllegalArgumentException("null deferred listener");
        Execution execution = CURRENT_EXECUTION.get();
        DEFERRED_REGISTRATIONS.put(
            listener,
            new DeferredRegistration(
                execution == null ? null : Long.valueOf(execution.ordinal),
                execution == null ? null : execution.className,
                currentEpoch == null ? false : currentEpoch.initialLoad,
                eventBus,
                priority,
                declaredEventClass
            )
        );
    }

    /**
     * Re-establish source custody while a listener closure runs after its
     * original Groovy load epoch has ended.
     */
    public static synchronized DeferredCallback beginDeferredCallback(
        Object listener,
        String runtimeEventClass
    ) {
        DeferredRegistration registration = DEFERRED_REGISTRATIONS.get(listener);
        if (registration == null || registration.executionOrdinal == null) {
            return DeferredCallback.noop();
        }
        if (currentEpoch != null || CURRENT_EXECUTION.get() != null) {
            return DeferredCallback.noop();
        }
        Epoch epoch = new Epoch(
            EPOCHS.size(),
            nextSequence(),
            "deferred-event-callback",
            "event:" + runtimeEventClass,
            false,
            registration.initialLoad,
            runtimeEventClass
        );
        EPOCHS.add(epoch);
        currentEpoch = epoch;
        Execution execution = new Execution(
            EXECUTIONS.size(),
            nextSequence(),
            epoch.ordinal,
            "deferred-event-listener",
            registration.className,
            registration.executionOrdinal,
            runtimeEventClass,
            registration.eventBus,
            registration.priority,
            registration.declaredEventClass
        );
        EXECUTIONS.add(execution);
        CURRENT_EXECUTION.set(execution);
        return new DeferredCallback(execution, epoch, registration);
    }

    public static synchronized void endDeferredCallback(
        DeferredCallback callback,
        Throwable failure
    ) {
        if (callback == null || callback.noop) return;
        if (
            CURRENT_EXECUTION.get() != callback.execution
            || currentEpoch != callback.epoch
        ) {
            throw new IllegalStateException("deferred Groovy callback custody differs");
        }
        callback.execution.endSequence = nextSequence();
        callback.execution.completed = true;
        callback.execution.failureClass = failure == null
            ? null
            : failure.getClass().getName();
        CURRENT_EXECUTION.remove();
        callback.epoch.endSequence = nextSequence();
        callback.epoch.completed = true;
        currentEpoch = null;
    }

    public static synchronized List<Epoch> epochs() {
        return Collections.unmodifiableList(new ArrayList<Epoch>(EPOCHS));
    }

    public static synchronized List<Execution> executions() {
        return Collections.unmodifiableList(new ArrayList<Execution>(EXECUTIONS));
    }

    public static synchronized List<Mutation> mutations() {
        return Collections.unmodifiableList(new ArrayList<Mutation>(MUTATIONS));
    }

    public static synchronized void requireSettled() {
        if (currentEpoch != null || CURRENT_EXECUTION.get() != null) {
            throw new IllegalStateException("Groovy provenance trace is not settled");
        }
        for (Epoch epoch : EPOCHS) {
            if (!epoch.completed) throw new IllegalStateException("Groovy epoch is incomplete");
        }
        for (Execution execution : EXECUTIONS) {
            if (!execution.completed) {
                throw new IllegalStateException("Groovy script execution is incomplete");
            }
        }
    }

    private static long nextSequence() {
        return sequence++;
    }

    public static final class Epoch {
        private final long ordinal;
        private final long beginSequence;
        private final String kind;
        private final String stage;
        private final boolean reloadable;
        private final boolean initialLoad;
        private final String triggerClassName;
        private long endSequence = -1;
        private boolean completed;

        private Epoch(
            long ordinal,
            long beginSequence,
            String kind,
            String stage,
            boolean reloadable,
            boolean initialLoad,
            String triggerClassName
        ) {
            if (kind == null || kind.isEmpty() || stage == null || stage.isEmpty()) {
                throw new IllegalArgumentException("invalid epoch identity");
            }
            this.ordinal = ordinal;
            this.beginSequence = beginSequence;
            this.kind = kind;
            this.stage = stage;
            this.reloadable = reloadable;
            this.initialLoad = initialLoad;
            this.triggerClassName = triggerClassName;
        }

        public long getOrdinal() { return ordinal; }
        public long getBeginSequence() { return beginSequence; }
        public long getEndSequence() { return endSequence; }
        public String getKind() { return kind; }
        public String getStage() { return stage; }
        public boolean isReloadable() { return reloadable; }
        public boolean isInitialLoad() { return initialLoad; }
        public String getTriggerClassName() { return triggerClassName; }
        public boolean isCompleted() { return completed; }
    }

    public static final class Execution {
        private final long ordinal;
        private final long beginSequence;
        private final long epochOrdinal;
        private final String kind;
        private final String className;
        private final Long parentExecutionOrdinal;
        private final String triggerClassName;
        private final String eventBus;
        private final String eventPriority;
        private final String declaredEventClass;
        private long endSequence = -1;
        private long mutationCount;
        private boolean completed;
        private String failureClass;

        private Execution(
            long ordinal,
            long beginSequence,
            long epochOrdinal,
            String kind,
            String className,
            Long parentExecutionOrdinal,
            String triggerClassName,
            String eventBus,
            String eventPriority,
            String declaredEventClass
        ) {
            if (kind == null || kind.isEmpty() || className == null || className.isEmpty()) {
                throw new IllegalArgumentException("invalid Groovy execution identity");
            }
            this.ordinal = ordinal;
            this.beginSequence = beginSequence;
            this.epochOrdinal = epochOrdinal;
            this.kind = kind;
            this.className = className;
            this.parentExecutionOrdinal = parentExecutionOrdinal;
            this.triggerClassName = triggerClassName;
            this.eventBus = eventBus;
            this.eventPriority = eventPriority;
            this.declaredEventClass = declaredEventClass;
        }

        public long getOrdinal() { return ordinal; }
        public long getBeginSequence() { return beginSequence; }
        public long getEndSequence() { return endSequence; }
        public long getEpochOrdinal() { return epochOrdinal; }
        public String getKind() { return kind; }
        public String getClassName() { return className; }
        public Long getParentExecutionOrdinal() { return parentExecutionOrdinal; }
        public String getTriggerClassName() { return triggerClassName; }
        public String getEventBus() { return eventBus; }
        public String getEventPriority() { return eventPriority; }
        public String getDeclaredEventClass() { return declaredEventClass; }
        public long getMutationCount() { return mutationCount; }
        public boolean isCompleted() { return completed; }
        public String getFailureClass() { return failureClass; }
    }

    public static final class Mutation {
        private final long ordinal;
        private final long sequence;
        private final long epochOrdinal;
        private final Long executionOrdinal;
        private final String operation;
        private final Object target;
        private final List<Object> subjects;
        private final List<StackTraceElement> frames;
        private final boolean framesTruncated;

        private Mutation(
            long ordinal,
            long sequence,
            long epochOrdinal,
            Long executionOrdinal,
            String operation,
            Object target,
            List<Object> subjects,
            List<StackTraceElement> frames,
            boolean framesTruncated
        ) {
            if (operation == null || operation.isEmpty()) {
                throw new IllegalArgumentException("empty mutation operation");
            }
            this.ordinal = ordinal;
            this.sequence = sequence;
            this.epochOrdinal = epochOrdinal;
            this.executionOrdinal = executionOrdinal;
            this.operation = operation;
            this.target = target;
            this.subjects = Collections.unmodifiableList(subjects);
            this.frames = Collections.unmodifiableList(frames);
            this.framesTruncated = framesTruncated;
        }

        public long getOrdinal() { return ordinal; }
        public long getSequence() { return sequence; }
        public long getEpochOrdinal() { return epochOrdinal; }
        public Long getExecutionOrdinal() { return executionOrdinal; }
        public String getOperation() { return operation; }
        public Object getTarget() { return target; }
        public List<Object> getSubjects() { return subjects; }
        public List<StackTraceElement> getFrames() { return frames; }
        public boolean areFramesTruncated() { return framesTruncated; }
    }

    private static final class DeferredRegistration {
        private final Long executionOrdinal;
        private final String className;
        private final boolean initialLoad;
        private final String eventBus;
        private final String priority;
        private final String declaredEventClass;

        private DeferredRegistration(
            Long executionOrdinal,
            String className,
            boolean initialLoad,
            String eventBus,
            String priority,
            String declaredEventClass
        ) {
            this.executionOrdinal = executionOrdinal;
            this.className = className;
            this.initialLoad = initialLoad;
            this.eventBus = eventBus;
            this.priority = priority;
            this.declaredEventClass = declaredEventClass;
        }
    }

    public static final class DeferredCallback {
        private static final DeferredCallback NOOP = new DeferredCallback();
        private final boolean noop;
        private final Execution execution;
        private final Epoch epoch;
        private final DeferredRegistration registration;

        private DeferredCallback() {
            this.noop = true;
            this.execution = null;
            this.epoch = null;
            this.registration = null;
        }

        private DeferredCallback(
            Execution execution,
            Epoch epoch,
            DeferredRegistration registration
        ) {
            this.noop = false;
            this.execution = execution;
            this.epoch = epoch;
            this.registration = registration;
        }

        private static DeferredCallback noop() { return NOOP; }
        public boolean isNoop() { return noop; }
        public String getEventBus() {
            return registration == null ? null : registration.eventBus;
        }
        public String getPriority() {
            return registration == null ? null : registration.priority;
        }
        public String getDeclaredEventClass() {
            return registration == null ? null : registration.declaredEventClass;
        }
    }
}
