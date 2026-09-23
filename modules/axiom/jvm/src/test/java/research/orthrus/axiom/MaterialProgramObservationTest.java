package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialProgramObservationTest {
    @Test void unavailableObserverPreservesOriginalBootstrapAndSourceCustody() {
        var source=Map.of("sha256","saved-program","files",List.of("original.groovy"));
        var bootstrap=Map.of("admitted",false,"platformInitialization",Map.of("status","threw","cause","original native failure"));
        var result=new LinkedHashMap<String,Object>();result.put("bootstrap",bootstrap);result.put("sourceProgram",source);
        MaterialProgram.captureTransformations(result,new ClassLoader(null) {});
        assertSame(source,result.get("sourceProgram"));assertSame(bootstrap,result.get("bootstrap"));
        assertFalse(result.containsKey("transformations"));
        assertEquals("unavailable",Json.object(result.get("transformationObservation")).get("status"));
    }
    @Test void brokenTransformerDuringObserverLoadRemainsSecondaryEvidence() {
        var result=new LinkedHashMap<String,Object>();
        MaterialProgram.captureTransformations(result,new ClassLoader(null) {
            @Override protected Class<?> loadClass(String name,boolean resolve) {
                throw new NoClassDefFoundError("Original transformer dependency is missing");
            }
        });
        var observation=Json.object(result.get("transformationObservation"));
        assertEquals("java.lang.NoClassDefFoundError",Json.object(Json.array(observation.get("causes")).getFirst()).get("type"));
        assertFalse(result.containsKey("transformations"));
    }
}
