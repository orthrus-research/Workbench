package research.orthrus.axiom.materialhost;

import groovy.lang.*;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialScriptBindingsTest {
    static class Saved extends Script {
        Saved(Binding binding) { super(binding); }
        public Object run() { return null; }
        @Override public Binding getBinding() { throw new AssertionError("Admission invoked a candidate getter"); }
    }
    @Test void originalVariableWritesReadsAndClosureResolution() {
        var values = new HashMap<String,Object>(); var binding = new Binding(values);
        var script = new Saved(binding); var admission = new MaterialScriptBindings(values);
        assertFalse(admission.admits(script, "saved", false));
        assertTrue(admission.admits(script, "saved", true));
        Object value = new Object(); script.setProperty("saved", value);
        assertSame(value, values.get("saved")); assertSame(value, script.getProperty("saved"));
        assertTrue(admission.admits(script, "saved", false));
        var closure = new Closure<Object>(script, script) {};
        assertTrue(admission.admits(closure, "saved", false));
        assertTrue(admission.admits(closure, "saved", true));
        values.remove("saved"); assertFalse(admission.admits(script, "saved", false));
    }
    @Test void protectsNativeNamesAndRejectsForeignOrReplacedBindings() {
        var values = new HashMap<String,Object>(Map.of("mods", new Object(), "material", new Object()));
        var script = new Saved(new Binding(values)); var admission = new MaterialScriptBindings(values);
        for (String name : List.of("mods", "material", "out", "globals", "metaClass", "binding", "$internal")) {
            assertFalse(admission.admits(script, name, true)); assertFalse(admission.admits(script, name, false));
        }
        assertFalse(admission.admits(new Object(), "saved", true));
        script.setBinding(new Binding());
        assertThrows(UnsupportedOperationException.class, () -> admission.admits(script, "saved", true));
        script.setBinding(new Binding(values) {});
        assertThrows(UnsupportedOperationException.class, () -> admission.admits(script, "saved", false));
    }
}
