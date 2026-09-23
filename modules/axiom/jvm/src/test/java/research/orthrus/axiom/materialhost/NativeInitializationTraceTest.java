package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import static org.junit.jupiter.api.Assertions.*;

class NativeInitializationTraceTest {
    @SuppressWarnings("unchecked")
    private static List<Map<String,Object>> rows(Map<String,Object> snapshot) {
        return (List<Map<String,Object>>)snapshot.get("steps");
    }
    @Test void recordsRealVisitOrderDurationAndUnvisitedOrDeferredWork() {
        var clock=new AtomicLong(100);var trace=new NativeInitializationTrace(clock::get);
        trace.declare("second","hook","native.Second#run");trace.declare("first","hook","native.First#run");
        trace.declare("unvisited","stage","native.Later");trace.defer("recipes","native.PostInit","not composed");
        trace.begin("first");clock.set(150);trace.returned("first");trace.begin("second");clock.set(170);trace.returned("second");
        var rows=rows(trace.snapshot());assertEquals(2,rows.get(0).get("sequence"));assertEquals(1,rows.get(1).get("sequence"));
        assertEquals(50L,rows.get(1).get("elapsedNanos"));assertEquals(20L,rows.get(0).get("elapsedNanos"));
        assertEquals("not-reached",rows.get(2).get("status"));assertFalse(rows.get(2).containsKey("elapsedNanos"));
        assertEquals("deferred",rows.get(3).get("status"));assertFalse(rows.get(3).containsKey("sequence"));
    }
    @Test void failureIsNotCompletionAndSnapshotsDoNotChange() {
        var trace=new NativeInitializationTrace();trace.declare("setup","stage","native.Setup");
        var before=trace.snapshot();trace.begin("setup");trace.failedRunning(new LinkageError("missing native dependency"));
        var row=rows(trace.snapshot()).getFirst();assertEquals("threw",row.get("status"));assertEquals("java.lang.LinkageError",row.get("exceptionType"));
        assertEquals("not-reached",rows(before).getFirst().get("status"));
        assertThrows(UnsupportedOperationException.class,()->row.put("status","returned"));
        assertThrows(UnsupportedOperationException.class,()->rows(before).clear());
    }
    @Test void cannotMarkUnvisitedDeferredOrRepeatedHookAsExecuted() {
        var trace=new NativeInitializationTrace();trace.declare("hook","hook","native.Hook");
        trace.defer("later","native.Later","not composed");
        assertThrows(IllegalStateException.class,()->trace.returned("hook"));
        assertThrows(IllegalStateException.class,()->trace.begin("later"));
        assertThrows(IllegalArgumentException.class,()->trace.begin("invented"));
        assertThrows(IllegalArgumentException.class,()->trace.declare("hook","hook","Other"));
        trace.begin("hook");trace.returned("hook");assertThrows(IllegalStateException.class,()->trace.begin("hook"));
    }
}
