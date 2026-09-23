package dev.workbench.crucible.runtimegraph;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/** Exact reflective seams isolated from semantic adapters. */
public final class ReflectionAccess {
    private ReflectionAccess() {}

    public static Field requireField(Class<?> owner, String name, Class<?> type) {
        try {
            Field field = owner.getDeclaredField(name);
            if (field.getType() != type) {
                throw new IllegalStateException(
                    owner.getName() + '.' + name + " has type " + field.getType().getName()
                );
            }
            field.setAccessible(true);
            return field;
        } catch (ReflectiveOperationException exception) {
            throw new IllegalStateException(
                "required field is unavailable: " + owner.getName() + '.' + name,
                exception
            );
        }
    }

    public static Field requireAssignableField(
        Class<?> owner,
        String name,
        Class<?> superType
    ) {
        try {
            Field field = owner.getDeclaredField(name);
            if (!superType.isAssignableFrom(field.getType())) {
                throw new IllegalStateException(
                    owner.getName() + '.' + name + " has non-assignable type "
                        + field.getType().getName()
                );
            }
            field.setAccessible(true);
            return field;
        } catch (ReflectiveOperationException exception) {
            throw new IllegalStateException(
                "required field is unavailable: " + owner.getName() + '.' + name,
                exception
            );
        }
    }

    public static Object read(Field field, Object owner) {
        try {
            return field.get(owner);
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot read exact field " + field, exception);
        }
    }

    public static List<Field> instanceFields(Class<?> type) {
        List<Field> result = new ArrayList<Field>();
        for (Class<?> current = type; current != null && current != Object.class;
             current = current.getSuperclass()) {
            for (Field field : current.getDeclaredFields()) {
                if (Modifier.isStatic(field.getModifiers()) || field.isSynthetic()) continue;
                try {
                    field.setAccessible(true);
                } catch (RuntimeException exception) {
                    continue;
                }
                result.add(field);
            }
        }
        Collections.sort(result, new Comparator<Field>() {
            @Override
            public int compare(Field left, Field right) {
                int owner = left.getDeclaringClass().getName().compareTo(
                    right.getDeclaringClass().getName()
                );
                return owner != 0 ? owner : left.getName().compareTo(right.getName());
            }
        });
        return result;
    }
}
