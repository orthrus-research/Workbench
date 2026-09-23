package research.orthrus.axiom.materialhost;

import org.apache.logging.log4j.Level;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.core.Appender;
import org.apache.logging.log4j.core.LoggerContext;
import org.apache.logging.log4j.core.config.Configuration;
import org.apache.logging.log4j.core.config.DefaultConfiguration;
import org.apache.logging.log4j.core.impl.Log4jLogEvent;
import org.apache.logging.log4j.message.SimpleMessage;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.parallel.ResourceLock;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import research.orthrus.axiom.Json;

import java.io.PrintWriter;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/** Exercises the supplied material host's logger observer without starting its native context. */
@ResourceLock("log4j-root-context")
class NativeEarlyDiagnosticsTest {
    private static final String HOST = "research.orthrus.axiom.materialhost.NativeEarlyClassSpace";
    private LoggerContext context;
    private Configuration originalConfiguration;
    private Appender observer;
    private Method hasErrors;
    private Field rows;
    private Map<String, Object> observed;
    private boolean hadCompleteness;
    private Object previousCompleteness;

    @BeforeEach void attachSuppliedObserver() throws Exception {
        Class<?> type;
        try {
            type = Class.forName(HOST + "$EarlyDiagnostics", true, getClass().getClassLoader());
        } catch (ClassNotFoundException unavailable) {
            Assumptions.assumeTrue(false, "Supply the freshly compiled native early host for observer qualification");
            return;
        }
        context = (LoggerContext) LogManager.getContext(false);
        originalConfiguration = context.getConfiguration();
        context.reconfigure(warningConfiguration());
        var constructor = type.getDeclaredConstructor(int.class);
        constructor.setAccessible(true);
        observer = (Appender) constructor.newInstance(65536);
        hasErrors = type.getDeclaredMethod("hasErrors");
        hasErrors.setAccessible(true);
        rows = type.getDeclaredField("rows");
        rows.setAccessible(true);
        var owner = Class.forName(HOST, true, type.getClassLoader());
        var field = owner.getDeclaredField("observed");
        field.setAccessible(true);
        @SuppressWarnings("unchecked") var sharedObservations = (Map<String, Object>) field.get(null);
        observed = sharedObservations;
        hadCompleteness = observed.containsKey("nativeDiagnosticsComplete");
        previousCompleteness = observed.get("nativeDiagnosticsComplete");
        observed.put("nativeDiagnosticsComplete", true);
    }

    @AfterEach void restoreLoggerConfiguration() throws Exception {
        try {
            if (observer != null) ((AutoCloseable) observer).close();
        } finally {
            if (observed != null) {
                if (hadCompleteness) observed.put("nativeDiagnosticsComplete", previousCompleteness);
                else observed.remove("nativeDiagnosticsComplete");
            }
            if (context != null && originalConfiguration != null) context.reconfigure(originalConfiguration);
        }
    }

    @Test void preservesWarningsAcrossNativeReconfigurationAndRetainsTheLaterError() throws Exception {
        var logger = context.getLogger("axiom.native-observer-regression");
        logger.warn("native warning before reconfiguration");
        assertFalse(hasErrors());
        var prior = context.getConfiguration();
        context.reconfigure(warningConfiguration());
        assertNotSame(prior, context.getConfiguration());
        logger.warn("native warning after reconfiguration");
        assertFalse(hasErrors());
        logger.error("native error after reconfiguration", new IllegalStateException("original native cause"));

        var retained = retained();
        assertEquals(List.of("native warning before reconfiguration", "native warning after reconfiguration",
                "native error after reconfiguration"), retained.stream().map(row -> row.get("message")).toList());
        assertEquals(List.of("warning", "warning", "error"), retained.stream().map(row -> row.get("severity")).toList());
        assertTrue(retained.stream().allMatch(row -> "axiom.native-observer-regression".equals(row.get("logger"))
                && "unlocated".equals(row.get("locationStatus"))));
        assertTrue(retained.getLast().get("trace").toString().contains("java.lang.IllegalStateException: original native cause"));
        assertTrue(hasErrors());
    }

    @Test void mvpDefaultRetainsLargeWarningsAndTheFinalNativeError() throws Exception {
        ((AutoCloseable) observer).close();
        var constructor = observer.getClass().getDeclaredConstructor(); constructor.setAccessible(true);
        observer = (Appender) constructor.newInstance();
        String message="native warning µ 😀\n".repeat(100000);
        observer.append(warning(message));
        context.getLogger("axiom.native-observer-regression").error("final native error",new IllegalStateException("original cause"));
        assertEquals(message,retained().getFirst().get("message"));
        assertEquals(2,retained().size());assertTrue(hasErrors());
        assertTrue(retained().getLast().get("trace").toString().contains("original cause"));
        assertEquals(true,observed.get("nativeDiagnosticsComplete"));assertTrue(protocolBytes()>1_048_576);
    }

    @Test void preInitAllocationRetainsCompleteNativeRowsAndStillReportsOverflow() throws Exception {
        ((AutoCloseable) observer).close();
        var constructor = observer.getClass().getDeclaredConstructor(int.class); constructor.setAccessible(true);
        observer = (Appender) constructor.newInstance(131072);
        String message = "native preInit warning ".repeat(4800);
        observer.append(warning(message));
        assertEquals(message, retained().getFirst().get("message"));
        assertFalse(hasErrors()); assertEquals(true, observed.get("nativeDiagnosticsComplete"));
        assertTrue(protocolBytes() > 65536 && protocolBytes() <= 131072);
        observer.append(warning(message));
        assertEquals(1, retained().size()); assertTrue(hasErrors());
        assertEquals(false, observed.get("nativeDiagnosticsComplete")); assertTrue(protocolBytes() <= 131072);
    }

    @Test void compilerCascadeAllocationRetainsLaterErrorsAfterLongWarnings() throws Exception {
        ((AutoCloseable) observer).close();
        var constructor = observer.getClass().getDeclaredConstructor(int.class); constructor.setAccessible(true);
        observer = (Appender) constructor.newInstance(262144);
        String message = "native compiler warning ".repeat(7000);
        observer.append(warning(message));
        context.getLogger("axiom.native-observer-regression").error("final saved compiler failure");
        assertEquals(2, retained().size()); assertEquals("final saved compiler failure", retained().getLast().get("message"));
        assertTrue(hasErrors()); assertEquals(true, observed.get("nativeDiagnosticsComplete"));
        assertTrue(protocolBytes() > 131072 && protocolBytes() <= 262144);
        observer.append(warning(message));
        assertEquals(2, retained().size()); assertEquals(false, observed.get("nativeDiagnosticsComplete"));
    }

    @Test void closeStopsCaptureAndDoesNotReattachOnAnotherReconfiguration() throws Exception {
        var logger = context.getLogger("axiom.native-observer-regression");
        logger.warn("retained before close");
        assertEquals(1, retained().size());
        ((AutoCloseable) observer).close();
        assertTrue(observer.isStopped());

        logger.warn("after close");
        context.reconfigure(warningConfiguration());
        logger.error("after close and reconfiguration");
        assertEquals(List.of("retained before close"), retained().stream().map(row -> row.get("message")).toList());
        assertFalse(context.getRootLogger().getAppenders().containsKey(observer.getName()));
        assertFalse(hasErrors());
    }

    @Test void overflowPreservesEvidenceAndNativeCallerContinuesAcrossReconfiguration() throws Exception {
        var logger = context.getLogger("axiom.native-observer-regression");
        logger.warn("retained before overflow");
        assertFalse(hasErrors());
        var continued = new AtomicBoolean();
        assertDoesNotThrow(() -> {
            logger.warn("x".repeat(65536));
            continued.set(true);
        });
        assertTrue(continued.get(), "A diagnostic budget must not change native callback control flow");
        assertEquals(List.of("retained before overflow"), retained().stream().map(row -> row.get("message")).toList());
        assertIncompleteAndBounded();

        context.reconfigure(warningConfiguration());
        assertDoesNotThrow(() -> logger.warn("later warning"));
        assertEquals("retained before overflow", retained().getFirst().get("message"));
        assertIncompleteAndBounded();
    }

    @Test void emptyMessageFloodAccountsForDiagnosticMetadataAndArrayOverhead() throws Exception {
        var returned = new AtomicInteger();
        assertDoesNotThrow(() -> {
            for (int i = 0; i < 10000; i++) {
                observer.append(warning(""));
                returned.incrementAndGet();
            }
        });
        assertEquals(10000, returned.get());
        assertFalse(retained().isEmpty());
        assertTrue(retained().size() < 10000, "Empty messages still occupy serialized evidence space");
        assertTrue(retained().stream().allMatch(row -> "".equals(row.get("message"))));
        assertIncompleteAndBounded();
    }

    @Test void oversizedLoggerIdentityCannotBypassAnEmptyMessageBudget() throws Exception {
        var event = Log4jLogEvent.newBuilder().setLoggerName("L".repeat(65536))
                .setLevel(Level.WARN).setMessage(new SimpleMessage("")).build();
        assertDoesNotThrow(() -> observer.append(event));
        assertTrue(retained().isEmpty(), "An oversized row must not be partially retained");
        assertIncompleteAndBounded();
    }

    @ParameterizedTest @MethodSource("messagesWithOversizedWireEncoding")
    void multibyteAndEscapedDiagnosticsUseTheActualProtocolByteBudget(String message) throws Exception {
        assertTrue(message.length() < 65536, "These cases must defeat a Java character-count budget");
        assertTrue(Json.write(List.of(Map.of("message", message))).getBytes(StandardCharsets.UTF_8).length > 65536);
        assertDoesNotThrow(() -> observer.append(warning(message)));
        assertTrue(retained().isEmpty(), "Diagnostics are retained as whole rows, never silently truncated");
        assertIncompleteAndBounded();
    }

    static Stream<String> messagesWithOversizedWireEncoding() {
        return Stream.of("\u0000".repeat(12000), "\uD83D\uDEA7".repeat(6000), "\u00e9".repeat(40000),
                "\"\\\n".repeat(12000));
    }

    @Test void fittingUnicodeAndControlDiagnosticsRemainExact() throws Exception {
        String message = "native \u0000\n\t\"\\\uD83D\uDEA7\u00e9";
        observer.append(warning(message));
        assertEquals(message, retained().getFirst().get("message"));
        assertFalse(hasErrors());
        assertEquals(true, observed.get("nativeDiagnosticsComplete"));
        assertTrue(protocolBytes() <= 65536);
    }

    @Test void hugeThrowableTraceCannotEscapeTheBoundOrInterruptItsNativeProducer() throws Exception {
        observer.append(warning("retained before huge trace"));
        var traceFinished = new AtomicBoolean();
        var failure = new IllegalStateException("original native failure") {
            @Override public void printStackTrace(PrintWriter output) {
                for (int i = 0; i < 20000; i++) output.println("original native frame " + i);
                traceFinished.set(true);
            }
        };
        var event = Log4jLogEvent.newBuilder().setLoggerName("axiom.native-observer-regression")
                .setLevel(Level.ERROR).setMessage(new SimpleMessage("native trace overflow")).setThrown(failure).build();
        assertDoesNotThrow(() -> observer.append(event));
        assertTrue(traceFinished.get(), "The bounded sink must discard excess trace without throwing into its producer");
        assertEquals(List.of("retained before huge trace"), retained().stream().map(row -> row.get("message")).toList());
        assertIncompleteAndBounded();
    }

    private static Configuration warningConfiguration() {
        var configuration = new DefaultConfiguration();
        configuration.getRootLogger().setLevel(Level.WARN);
        // Keep deliberate test diagnostics out of console output, especially the bound witness.
        for (String name : List.copyOf(configuration.getRootLogger().getAppenders().keySet()))
            configuration.getRootLogger().removeAppender(name);
        return configuration;
    }

    private static Log4jLogEvent warning(String message) {
        return Log4jLogEvent.newBuilder().setLoggerName("axiom.native-observer-regression")
                .setLevel(Level.WARN).setMessage(new SimpleMessage(message)).build();
    }

    private boolean hasErrors() throws Exception { return (boolean) hasErrors.invoke(observer); }

    private void assertIncompleteAndBounded() throws Exception {
        assertEquals(false, observed.get("nativeDiagnosticsComplete"));
        assertTrue(hasErrors(), "An observation gap must prevent early pipeline qualification");
        assertTrue(protocolBytes() <= 65536, "Retained diagnostics must fit the worker's actual JSON encoding");
    }

    private int protocolBytes() throws Exception {
        return Json.write(retained()).getBytes(StandardCharsets.UTF_8).length;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> retained() throws Exception {
        return List.copyOf((List<Map<String, Object>>) rows.get(observer));
    }
}
