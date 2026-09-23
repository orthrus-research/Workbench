package research.orthrus.axiom.materialhost;

import groovy.lang.Closure;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Admission identity tests, not substitutes for native mapper conformance. */
class MaterialMapperBindingsTest {
    private static Closure<Object> closure() {
        return new Closure<Object>(null) {public Object doCall(Object value) {throw new AssertionError("Admission invoked mapper");}};
    }
    @Test void exactNamesAndClosureIdentityOnlyWithoutInvocation() {
        var material=closure();var item=closure();
        var entries=new HashMap<String,Object>(Map.of("material",material,"item",item));
        var gate=new MaterialMapperBindings(entries,Set.of("material"));
        assertTrue(gate.named("material"));assertFalse(gate.named("item"));assertFalse(gate.named("missing"));
        assertTrue(gate.call(material,"call"));assertTrue(gate.call(material,"doCall"));
        assertFalse(gate.call(item,"call"));assertFalse(gate.call(closure(),"call"));
        assertFalse(gate.call(material,"getOwner"));assertFalse(gate.call(material,"setDelegate"));
    }
    @Test void replacedOrRemovedBindingsCannotReuseAdmission() {
        var material=closure();var entries=new HashMap<String,Object>(Map.of("material",material));
        var gate=new MaterialMapperBindings(entries,Set.of("material"));
        entries.put("material",closure());
        assertThrows(UnsupportedOperationException.class,()->gate.named("material"));
        assertThrows(UnsupportedOperationException.class,()->gate.call(material,"call"));
        entries.clear();assertThrows(UnsupportedOperationException.class,()->gate.named("material"));
    }
    @Test void missingNonClosureAndEmptySelectionAreExplicit() {
        assertThrows(IllegalStateException.class,()->new MaterialMapperBindings(Map.of(),Set.of("material")));
        assertThrows(IllegalStateException.class,()->new MaterialMapperBindings(Map.of("material","water"),Set.of("material")));
        assertFalse(new MaterialMapperBindings(Map.of("material",closure()),Set.of()).named("material"));
    }
    @Test void recipeMapperBindingsRemainSeparateExactIdentities() {
        var names=Set.of("material","metaitem","item","ore","fluid","liquid","recipemap");
        var entries=new HashMap<String,Object>();
        names.forEach(name->entries.put(name,closure()));
        entries.put("resource",closure());
        var gate=new MaterialMapperBindings(entries,names);
        for(String name:names) {
            Object original=entries.get(name);
            assertTrue(gate.named(name));assertTrue(gate.call(original,"call"));
            assertFalse(gate.call(original,"getOwner"));assertFalse(gate.call(original,"setDelegate"));
            entries.put(name,closure());
            assertThrows(UnsupportedOperationException.class,()->gate.named(name));
            assertThrows(UnsupportedOperationException.class,()->gate.call(original,"call"));
            entries.put(name,original);
        }
        assertFalse(gate.named("resource"));assertFalse(gate.call(entries.get("resource"),"call"));
        assertFalse(gate.call(closure(),"call"));
    }
}
