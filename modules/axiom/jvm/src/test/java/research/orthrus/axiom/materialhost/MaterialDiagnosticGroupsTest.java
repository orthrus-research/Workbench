package research.orthrus.axiom.materialhost;

import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class MaterialDiagnosticGroupsTest {
    private Map<String,Object> row(String severity,String message,int line) {
        return Map.of("severity",severity,"message",message,"channel","log4j","locations",List.of(Map.of("path","groovy/A.groovy","line",line)));
    }
    @Test void adjacentWarningsRetainEveryLineAndLocationWithoutDuplicateMetadata() {
        var first=row("warning","***",4);var next=row("warning","native warning",4);
        var result=MaterialDiagnosticGroups.group(List.of(first,next,first));
        assertEquals(1,result.size());assertEquals("***\nnative warning\n***",result.getFirst().get("message"));
        assertEquals(3,result.getFirst().get("nativeLogEvents"));assertEquals(first.get("locations"),result.getFirst().get("locations"));
        assertEquals("***",first.get("message"));
    }
    @Test void differentLocationsErrorsAndNonadjacentWarningsStaySeparate() {
        var a=row("warning","warning",4);var b=row("warning","warning",5);var e=row("error","failure",4);
        var input=List.of(a,b,a,e,e,a);assertEquals(input,MaterialDiagnosticGroups.group(input));
    }
    @Test void thrownWarningsKeepTheirIndividualCausality() {
        var a=new LinkedHashMap<>(row("warning","failure",4));a.put("trace","original throwable");
        assertEquals(List.of(a,a),MaterialDiagnosticGroups.group(List.of(a,a)));
    }
}
