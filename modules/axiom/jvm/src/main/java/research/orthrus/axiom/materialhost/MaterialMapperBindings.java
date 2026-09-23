package research.orthrus.axiom.materialhost;

import groovy.lang.Closure;
import java.util.*;

/** Identity-only admission of already registered native bindings. Never resolves
 * a material, invokes a closure or selects/coerces its arguments. */
final class MaterialMapperBindings {
    private final Map<String,Object> bindings;
    private final Map<String,Closure<?>> selected;

    MaterialMapperBindings(Map<String,Object> bindings,Set<String> names) {
        this.bindings=Objects.requireNonNull(bindings);
        var entries=new LinkedHashMap<String,Closure<?>>();
        for(String name:names) {
            if(!(bindings.get(name) instanceof Closure<?> closure))
                throw new IllegalStateException("Native mapper binding absent: "+name);
            entries.put(name,closure);
        }
        selected=Map.copyOf(entries);
    }
    boolean named(String name) {
        var closure=selected.get(name);
        if(closure==null)return false;
        if(bindings.get(name)!=closure)throw MaterialCallGate.reject("candidate.mapper-binding-drift",name);
        return true;
    }
    boolean call(Object receiver,String member) {
        if(!Set.of("call","doCall").contains(member))return false;
        for(var entry:selected.entrySet())if(entry.getValue()==receiver)return named(entry.getKey());
        return false;
    }
}
