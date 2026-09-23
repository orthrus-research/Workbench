package dev.workbench.crucible.runtimegraph;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Map;

/** Exact, shape-checked access to pack-core registries without public enumerators. */
public final class PackCoreReflection {
    private PackCoreReflection() {}

    public static Field field(Class<?> owner, String name, Class<?> type, boolean requireStatic) {
        try {
            Field field = owner.getDeclaredField(name);
            if (field.getDeclaringClass() != owner || field.getType() != type
                || Modifier.isStatic(field.getModifiers()) != requireStatic) {
                throw new IllegalStateException(owner.getName() + "#" + name + " shape changed");
            }
            field.setAccessible(true);
            return field;
        } catch (ReflectiveOperationException exception) {
            throw new IllegalStateException("cannot bind " + owner.getName() + "#" + name, exception);
        }
    }

    @SuppressWarnings("unchecked")
    public static <K, V> Map<K, V> staticMap(Class<?> owner, String name) {
        Field field = field(owner, name, Map.class, true);
        try {
            Object value = field.get(null);
            if (!(value instanceof Map<?, ?>)) {
                throw new IllegalStateException(owner.getName() + "#" + name + " is not a map");
            }
            return (Map<K, V>) value;
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot read " + owner.getName() + "#" + name, exception);
        }
    }

    public static Object value(Field field, Object target) {
        try {
            return field.get(target);
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot read " + field, exception);
        }
    }
}
