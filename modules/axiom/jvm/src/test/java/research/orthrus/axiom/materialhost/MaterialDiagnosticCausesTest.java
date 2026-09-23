package research.orthrus.axiom.materialhost;

import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class MaterialDiagnosticCausesTest {
    private static StackTraceElement frame(String type,String method,int line) {
        return new StackTraceElement(type,method,"Same.groovy",line);
    }
    @SuppressWarnings("unchecked")
    private static List<Map<String,Object>> nodes(MaterialDiagnosticCauses.Snapshot snapshot) {
        return (List<Map<String,Object>>)snapshot.graph().get("exceptions");
    }
    @Test void causeMessagesFramesAndSuppressedIdentityRemainDistinct() {
        var cause=new IllegalArgumentException("native material failure");
        cause.setStackTrace(new StackTraceElement[]{frame("classes.Helper","fail",8),frame("classes.Helper","fail",8)});
        var root=new RuntimeException("wrapper",cause);
        root.setStackTrace(new StackTraceElement[]{frame("preInit.Events$_run_closure1","doCall",17)});
        root.addSuppressed(cause); // same identity, not a fabricated duplicate exception
        var snapshot=MaterialDiagnosticCauses.observe(root,Map.of("classes.Helper","groovy/classes/Helper.groovy","preInit.Events","groovy/preInit/Events.groovy"));
        var rows=nodes(snapshot);
        assertEquals(2,rows.size());assertEquals(1,rows.get(0).get("cause"));assertEquals(List.of(1),rows.get(0).get("suppressed"));
        assertEquals("wrapper",rows.get(0).get("message"));assertEquals("native material failure",rows.get(1).get("message"));
        assertEquals(2,((List<?>)rows.get(1).get("locations")).size());assertEquals(List.of(root,cause),snapshot.exceptions());
        assertSame(cause,root.getCause());assertSame(cause,root.getSuppressed()[0]);
        assertThrows(UnsupportedOperationException.class,()->rows.get(0).put("message","rewritten"));
    }
    @Test void cyclesSharedNodesAndNullMessagesAreRetainedWithoutRecursion() {
        var first=new Exception((String)null);var second=new Exception("second");first.initCause(second);second.initCause(first);
        first.addSuppressed(second);second.addSuppressed(first);
        var rows=nodes(MaterialDiagnosticCauses.observe(first,Map.of()));
        assertEquals(2,rows.size());assertNull(rows.get(0).get("message"));assertEquals(0,rows.get(1).get("cause"));
        assertEquals(List.of(0),rows.get(1).get("suppressed"));
    }
    @Test void noThrowableDoesNotTurnLoggingSiteIntoAnException() {
        var snapshot=MaterialDiagnosticCauses.observe(null,Map.of());
        assertNull(snapshot.graph().get("root"));assertEquals(List.of(),nodes(snapshot));assertEquals(List.of(),snapshot.exceptions());
    }
    @Test void exactClassIdentityNotBasenameSelectsSourceAndPreservesOrder() {
        var frames=new StackTraceElement[]{frame("other.Helper","wrong",2),frame("classes.Helper$_nested","inner",4),
            frame("classes.Helper","outer",9),frame("classes.Helper","unknown",-1)};
        var locations=MaterialDiagnosticCauses.locations(frames,Map.of("classes.Helper","groovy/classes/Helper.groovy"));
        assertEquals(List.of(4,9),locations.stream().map(row->row.get("line")).toList());
        assertEquals(List.of("inner","outer"),locations.stream().map(row->row.get("method")).toList());
        assertTrue(locations.stream().noneMatch(row->row.containsKey("column")));
        var sourceFrames=MaterialDiagnosticCauses.sourceFrames(frames,Map.of("classes.Helper","groovy/classes/Helper.groovy"));
        assertEquals(List.of(4,9,-1),sourceFrames.stream().map(row->row.get("line")).toList());
        assertTrue(sourceFrames.stream().noneMatch(row->row.containsKey("precision")));
    }
}
