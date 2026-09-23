package dev.workbench.worldgenobservatory.trace;

import dev.workbench.worldgenobservatory.config.ObservatoryConfig;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

public final class TraceSinks {

    private static final TraceSink NO_OP = new NoOpTraceSink();
    private static final TraceSink LOGGING = new LoggingTraceSink();

    private TraceSinks() {
    }

    public static TraceSink current() {
        return ObservatoryConfig.enabled ? LOGGING : NO_OP;
    }

    private static final class NoOpTraceSink implements TraceSink {

        @Override
        public boolean enabled() {
            return false;
        }

        @Override
        public boolean checkpointsEnabled() {
            return false;
        }

        @Override
        public void emit(TraceRecord record) {
            // Intentionally empty. Disabled tracing allocates no records and
            // computes no chunk digests because callers guard on enabled().
        }
    }

    private static final class LoggingTraceSink implements TraceSink {

        private static final Logger LOGGER = LogManager.getLogger("WorkbenchWorldgenObservatory");

        @Override
        public boolean enabled() {
            return true;
        }

        @Override
        public boolean checkpointsEnabled() {
            return ObservatoryConfig.emitBlockStateCheckpoints;
        }

        @Override
        public void emit(TraceRecord record) {
            LOGGER.info("WORLDGEN_OBSERVATORY {}", record.toJson());
        }
    }
}
