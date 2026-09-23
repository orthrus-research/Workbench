package research.orthrus.axiom.materialhost;

import groovy.lang.*;
import java.lang.reflect.Field;
import java.util.*;

/** Admission for variables stored by the original Script/Binding implementation. */
final class MaterialScriptBindings {
    private static final Field SCRIPT_BINDING = field(Script.class, "binding");
    private static final Field BINDING_VARIABLES = field(Binding.class, "variables");
    private static final Field CLOSURE_THIS = field(Closure.class, "thisObject");
    private final Map<String,Object> nativeVariables;
    private final Set<String> protectedNames;
    private final Set<String> writtenNames = new HashSet<>();

    MaterialScriptBindings(Map<String,Object> nativeVariables) {
        this.nativeVariables = Objects.requireNonNull(nativeVariables);
        var names = new HashSet<>(nativeVariables.keySet());
        names.addAll(List.of("out", "globals", "binding", "metaClass", "class", "properties"));
        protectedNames = Set.copyOf(names);
    }

    boolean admits(Object receiver, String name, boolean write) {
        if (name.startsWith("$") || protectedNames.contains(name)) return false;
        try {
            Object script = receiver instanceof Closure<?> ? CLOSURE_THIS.get(receiver) : receiver;
            if (!(script instanceof Script) || script.getClass().getSuperclass() != Script.class) return false;
            Object binding = SCRIPT_BINDING.get(script);
            if (binding == null || binding.getClass() != Binding.class || BINDING_VARIABLES.get(binding) != nativeVariables)
                throw MaterialCallGate.reject("candidate.script-binding-drift", name);
            if (write) { writtenNames.add(name); return true; }
            return writtenNames.contains(name) && nativeVariables.containsKey(name);
        } catch (IllegalAccessException impossible) { throw new IllegalStateException("Original script binding is inaccessible", impossible); }
    }

    private static Field field(Class<?> owner, String name) {
        try { var field = owner.getDeclaredField(name); field.setAccessible(true); return field; }
        catch (ReflectiveOperationException failure) { throw new ExceptionInInitializerError(failure); }
    }
}
