package research.orthrus.axiom.materialhost;

import groovy.lang.GroovyObject;
import groovy.lang.GroovySystem;
import groovy.lang.MetaProperty;
import java.util.*;
import java.util.function.Predicate;

/** Admit reads of already registered native properties by object and metadata
 * identity. Never evaluate a property getter or install an expansion. */
final class MaterialPropertyBindings {
    private record Property(Object value, MetaProperty metadata) {}
    private record Receiver(Map<String,?> source, Map<String,Property> properties) {}
    private final Map<String,Object> bindings;
    private final String bindingName;
    private final Object root;
    private final IdentityHashMap<Object,Receiver> receivers = new IdentityHashMap<>();

    MaterialPropertyBindings(Map<String,Object> bindings, String bindingName, Object root,
            Map<Object,Map<String,?>> graph, Predicate<Object> selected) {
        this.bindings = Objects.requireNonNull(bindings);
        this.bindingName = Objects.requireNonNull(bindingName);
        this.root = Objects.requireNonNull(root);
        if (bindings.get(bindingName) != root) throw new IllegalStateException("Native property root binding differs");
        graph.forEach((receiver, source) -> {
            // These native containers use the registry's metaclass. Per-instance
            // GroovyObject metadata would require a different identity contract.
            if (receiver == null || receiver instanceof GroovyObject)
                throw new IllegalStateException("Native property receiver differs");
            var members = new TreeMap<String,Property>();
            source.forEach((name, value) -> {
                if (value == null || !selected.test(value)) return;
                MetaProperty metadata = GroovySystem.getMetaClassRegistry().getMetaClass(receiver.getClass()).getMetaProperty(name);
                if (metadata == null) throw new IllegalStateException("Native property metadata absent: " + name);
                members.put(name, new Property(value, metadata));
            });
            receivers.put(receiver, new Receiver(source, Map.copyOf(members)));
        });
        if (!receivers.containsKey(root)) throw new IllegalStateException("Native property root was not selected");
    }

    boolean named(String name) {
        if (!bindingName.equals(name)) return false;
        if (bindings.get(name) != root) throw MaterialCallGate.reject("candidate.property-binding-drift", name);
        return true;
    }

    boolean read(Object receiver, String name) {
        Receiver selected = receivers.get(receiver);
        Property property = selected == null ? null : selected.properties.get(name);
        if (property == null) return false;
        named(bindingName);
        if (selected.source.get(name) != property.value
                || GroovySystem.getMetaClassRegistry().getMetaClass(receiver.getClass()).getMetaProperty(name) != property.metadata)
            throw MaterialCallGate.reject("candidate.property-binding-drift", receiver.getClass().getName() + "#" + name);
        return true;
    }
}
