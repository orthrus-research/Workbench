package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Stable method-owner evidence for contextual or executable runtime boundaries. */
public final class RuntimeMethodSurface {
    private RuntimeMethodSurface() {}

    public static String publicOwner(Object value, String name, Class<?>... parameters) {
        if (value == null) throw new IllegalArgumentException("method receiver is null");
        try {
            return value.getClass().getMethod(name, parameters).getDeclaringClass().getName();
        } catch (NoSuchMethodException exception) {
            throw new IllegalStateException(
                "required public method is absent: " + value.getClass().getName() + '.' + name,
                exception
            );
        }
    }

    public static String publicOwnerByShape(
        Object value,
        Class<?> authority,
        Class<?> returnType,
        Class<?>... parameters
    ) {
        if (value == null || authority == null || returnType == null) {
            throw new IllegalArgumentException("invalid method-shape request");
        }
        Method authorityMethod = null;
        for (Method candidate : authority.getDeclaredMethods()) {
            if (candidate.isBridge() || candidate.isSynthetic()
                || candidate.getReturnType() != returnType
                || !Arrays.equals(candidate.getParameterTypes(), parameters)) continue;
            if (authorityMethod != null) {
                throw new IllegalStateException(
                    "runtime authority has an ambiguous method shape: " + authority.getName()
                );
            }
            authorityMethod = candidate;
        }
        if (authorityMethod == null) {
            throw new IllegalStateException(
                "runtime authority lacks method shape: " + authority.getName()
            );
        }
        return publicOwner(value, authorityMethod.getName(), parameters);
    }

    public static JsonArray declaredHooks(
        Object value,
        Class<?> stopExclusive,
        String... names
    ) {
        if (value == null || stopExclusive == null || names == null) {
            throw new IllegalArgumentException("invalid method-surface request");
        }
        Set<String> selected = new HashSet<String>(Arrays.asList(names));
        List<Method> methods = new ArrayList<Method>();
        for (Class<?> current = value.getClass(); current != null && current != stopExclusive;
             current = current.getSuperclass()) {
            Method[] declared;
            try {
                declared = current.getDeclaredMethods();
            } catch (LinkageError sideIncompatible) {
                JsonArray boundary = new JsonArray();
                JsonObject row = new JsonObject();
                row.addProperty("declaring_class", current.getName());
                row.addProperty("projection_boundary", "side-incompatible-method-signature");
                row.addProperty("method_body_captured", false);
                boundary.add(row);
                return boundary;
            }
            for (Method method : declared) {
                if (method.isSynthetic() || method.isBridge()
                    || Modifier.isStatic(method.getModifiers())
                    || !selected.contains(method.getName())) continue;
                methods.add(method);
            }
        }
        Collections.sort(methods, new Comparator<Method>() {
            @Override
            public int compare(Method left, Method right) {
                return signature(left).compareTo(signature(right));
            }
        });
        JsonArray result = new JsonArray();
        for (Method method : methods) {
            JsonObject row = new JsonObject();
            row.addProperty("declaring_class", method.getDeclaringClass().getName());
            row.addProperty("final", Modifier.isFinal(method.getModifiers()));
            row.addProperty("name", method.getName());
            row.addProperty("signature", signature(method));
            row.addProperty("visibility", visibility(method.getModifiers()));
            result.add(row);
        }
        return result;
    }

    private static String signature(Method method) {
        StringBuilder value = new StringBuilder();
        value.append(method.getDeclaringClass().getName()).append('#')
            .append(method.getName()).append('(');
        Class<?>[] parameters = method.getParameterTypes();
        for (int index = 0; index < parameters.length; index++) {
            if (index > 0) value.append(',');
            value.append(parameters[index].getName());
        }
        return value.append("):" ).append(method.getReturnType().getName()).toString();
    }

    private static String visibility(int modifiers) {
        if (Modifier.isPublic(modifiers)) return "public";
        if (Modifier.isProtected(modifiers)) return "protected";
        if (Modifier.isPrivate(modifiers)) return "private";
        return "package";
    }
}
