package dev.workbench.worldgenobservatory.trace;

public interface TraceSink {

    boolean enabled();

    boolean checkpointsEnabled();

    void emit(TraceRecord record);
}
