package research.orthrus.axiom.materialhost;

import groovy.lang.ExpandoMetaClass;
import groovy.lang.GroovySystem;
import org.codehaus.groovy.runtime.InvokerHelper;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialPropertyBindingsTest {
    public static class Root {
        final Container value = new Container();
        int reads;
        public Container getGregtech() {reads++; return value;}
    }
    public static class Container {
        final Object recipe = new Object(), unrelated = new Object();
        int reads;
        public Object getCentrifuge() {reads++; return recipe;}
        public Object getUnrelated() {throw new AssertionError("Unselected getter executed");}
    }
    private static final class Fixture {
        final Root root = new Root();
        final Map<String,Object> bindings = new HashMap<>(Map.of("mods",root));
        final Map<String,Object> properties = new HashMap<>(Map.of("centrifuge",root.value.recipe,
                "unrelated",root.value.unrelated));
        final MaterialPropertyBindings gate;
        Fixture() {
            var graph = new IdentityHashMap<Object,Map<String,?>>();
            graph.put(root,Map.of("gregtech",root.value));graph.put(root.value,properties);
            gate = new MaterialPropertyBindings(bindings,"mods",root,graph,
                    value -> value == root.value || value == root.value.recipe);
        }
    }
    @Test void readsRequireExactRegisteredIdentitiesAndDoNotInvokeGetters() {
        var f = new Fixture();
        assertTrue(f.gate.named("mods"));assertFalse(f.gate.named("other"));
        assertTrue(f.gate.read(f.root,"gregtech"));assertTrue(f.gate.read(f.root.value,"centrifuge"));
        assertFalse(f.gate.read(f.root.value,"unrelated"));assertFalse(f.gate.read(f.root.value,"missing"));
        assertFalse(f.gate.read(new Root(),"gregtech"));assertFalse(f.gate.read(new Container(),"centrifuge"));
        assertFalse(f.gate.read(f.root.value,"metaClass"));assertFalse(f.gate.read(f.root.value,"class"));
        assertEquals(0,f.root.reads);assertEquals(0,f.root.value.reads);
        assertSame(f.root.value,InvokerHelper.getProperty(f.root,"gregtech"));
        assertSame(f.root.value.recipe,InvokerHelper.getProperty(f.root.value,"centrifuge"));
        assertEquals(1,f.root.reads);assertEquals(1,f.root.value.reads);
    }
    @Test void changedRootAndRegisteredValueCannotReuseAdmission() {
        var f = new Fixture();f.bindings.put("mods",new Root());
        assertThrows(UnsupportedOperationException.class,()->f.gate.named("mods"));
        assertThrows(UnsupportedOperationException.class,()->f.gate.read(f.root.value,"centrifuge"));
        f.bindings.put("mods",f.root);f.properties.put("centrifuge",new Object());
        assertThrows(UnsupportedOperationException.class,()->f.gate.read(f.root.value,"centrifuge"));
        f.properties.remove("centrifuge");
        assertThrows(UnsupportedOperationException.class,()->f.gate.read(f.root.value,"centrifuge"));
        assertEquals(0,f.root.value.reads);
    }
    @Test void replacedNativeMetadataIsRejectedBeforeItsGetter() {
        var f = new Fixture();var registry=GroovySystem.getMetaClassRegistry();var original=registry.getMetaClass(Container.class);
        try {
            var changed = new ExpandoMetaClass(Container.class,false,true);
            changed.registerBeanProperty("centrifuge",new Object());changed.initialize();registry.setMetaClass(Container.class,changed);
            assertThrows(UnsupportedOperationException.class,()->f.gate.read(f.root.value,"centrifuge"));
            assertEquals(0,f.root.value.reads);
        } finally {registry.setMetaClass(Container.class,original);}
    }
    @Test void missingBindingOrMetadataCannotCreateAnAdmittedProperty() {
        var root=new Root();var graph=new IdentityHashMap<Object,Map<String,?>>();graph.put(root,Map.of("missing",new Object()));
        assertThrows(IllegalStateException.class,()->new MaterialPropertyBindings(Map.of(),"mods",root,graph,value->true));
        assertThrows(IllegalStateException.class,()->new MaterialPropertyBindings(Map.of("mods",root),"mods",root,graph,value->true));
    }
    @Test void storedNativeAliasRequiresItsOwnOriginalBindingIdentity() {
        var root=new Container();var bindings=new HashMap<String,Object>(Map.of("oreDict",root,"ore_dict",root));
        var graph=new IdentityHashMap<Object,Map<String,?>>();graph.put(root,Map.of());
        var canonical=new MaterialPropertyBindings(bindings,"oreDict",root,graph,value->false);
        var alias=new MaterialPropertyBindings(bindings,"ore_dict",root,graph,value->false);
        assertTrue(canonical.named("oreDict"));assertTrue(alias.named("ore_dict"));
        assertFalse(alias.named("ore_dictionary"));assertFalse(alias.read(root,"unrelated"));
        assertSame(root,new groovy.lang.Binding(bindings).getVariable("ore_dict"));assertEquals(0,root.reads);
        bindings.remove("ore_dict");assertThrows(UnsupportedOperationException.class,()->alias.named("ore_dict"));
        assertThrows(IllegalStateException.class,()->new MaterialPropertyBindings(bindings,"ore_dict",root,graph,value->false));
        bindings.put("ore_dict",new Container());assertThrows(UnsupportedOperationException.class,()->alias.named("ore_dict"));
        assertEquals(0,root.reads);
    }
    @Test void directNativeRootUsesIdentityWithoutAdmittingItsOtherProperties() {
        var root=new Container();var bindings=new HashMap<String,Object>(Map.of("crafting",root));
        var graph=new IdentityHashMap<Object,Map<String,?>>();graph.put(root,Map.of());
        var gate=new MaterialPropertyBindings(bindings,"crafting",root,graph,value->false);
        assertTrue(gate.named("crafting"));assertFalse(gate.named("mods"));
        assertFalse(gate.read(root,"unrelated"));assertFalse(gate.read(root,"metaClass"));
        assertEquals(0,root.reads);
        bindings.put("crafting",new Container());
        assertThrows(UnsupportedOperationException.class,()->gate.named("crafting"));
    }
}
