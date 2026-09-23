package research.orthrus.axiom.materialhost;

import java.lang.reflect.*;
import java.lang.invoke.*;
import java.util.*;

/** Shared lookup of original native fields/getters without a prepared compile classpath. */
final class NativeObservationAccess {
    private record MethodKey(Class<?> owner, String name, List<Class<?>> parameters) {}
    private record FieldKey(Class<?> owner, String name) {}
    private static final Map<MethodKey,Method> methods = new HashMap<>();
    private static final Map<FieldKey,Field> fields = new HashMap<>();
    private record HandleKey(Class<?> owner, String name, MethodType signature, boolean isStatic) {}
    private static final Map<HandleKey,MethodHandle> handles = new HashMap<>();
    private NativeObservationAccess() {}

    static Class<?> type(String name) throws ClassNotFoundException {
        return Class.forName(name, false, NativeObservationAccess.class.getClassLoader());
    }
    static Object field(String owner, Object receiver, String name) throws ReflectiveOperationException {
        return field(type(owner), receiver, name);
    }
    static Object field(Class<?> owner, Object receiver, String name) throws ReflectiveOperationException {
        var key = new FieldKey(owner, name); var field = fields.get(key);
        if (field == null) { field = owner.getDeclaredField(name); field.setAccessible(true); fields.put(key, field); }
        return field.get(receiver);
    }
    static Object call(Object receiver, String name) throws ReflectiveOperationException {
        return invoke(receiver.getClass(), receiver, name, new Class<?>[0]);
    }
    static Object call(Object receiver, String name, Class<?> parameter, Object argument) throws ReflectiveOperationException {
        return invoke(receiver.getClass(), receiver, name, new Class<?>[]{parameter}, argument);
    }
    static Object callStatic(String owner, String name) throws ReflectiveOperationException {
        return invoke(type(owner), null, name, new Class<?>[0]);
    }
    static Object callStatic(String owner, String name, Class<?> parameter, Object argument) throws ReflectiveOperationException {
        return invoke(type(owner), null, name, new Class<?>[]{parameter}, argument);
    }
    static Object callStatic(String owner, String name, Class<?>[] parameters, Object... arguments) throws ReflectiveOperationException {
        return invoke(type(owner), null, name, parameters, arguments);
    }
    /** Resolve only the requested original descriptor. Reflective method
     * enumeration would also resolve unrelated CLIENT-only signature types.
     */
    static Object virtual(String owner, Object receiver, String name, Class<?> result, Class<?>[] parameters,
            Object... arguments) throws ReflectiveOperationException {
        return exact(type(owner), receiver, name, result, parameters, false, arguments);
    }
    static Object staticMethod(String owner, String name, Class<?> result, Class<?>[] parameters,
            Object... arguments) throws ReflectiveOperationException {
        return exact(type(owner), null, name, result, parameters, true, arguments);
    }
    private static Object exact(Class<?> owner, Object receiver, String name, Class<?> result, Class<?>[] parameters,
            boolean isStatic, Object[] arguments) throws ReflectiveOperationException {
        var signature = MethodType.methodType(result, parameters); var key = new HandleKey(owner, name, signature, isStatic);
        var handle = handles.get(key);
        if (handle == null) {
            var lookup = MethodHandles.privateLookupIn(owner, MethodHandles.lookup());
            handle = isStatic ? lookup.findStatic(owner, name, signature) : lookup.findVirtual(owner, name, signature);
            handles.put(key, handle);
        }
        var values = new ArrayList<Object>(); if (!isStatic) values.add(receiver); Collections.addAll(values, arguments);
        try { return handle.invokeWithArguments(values); }
        catch (Throwable failure) { throw new InvocationTargetException(failure); }
    }
    private static Object invoke(Class<?> owner, Object receiver, String name, Class<?>[] parameters,
            Object... arguments) throws ReflectiveOperationException {
        var key = new MethodKey(owner, name, List.of(parameters)); var method = methods.get(key);
        if (method == null) { method = owner.getMethod(name, parameters); method.setAccessible(true); methods.put(key, method); }
        return method.invoke(receiver, arguments);
    }
    static Object materialManager() throws ReflectiveOperationException {
        // getInstance() would create a manager when native initialization never did.
        Object manager = field("gregtech.core.unification.material.internal.MaterialRegistryManager", null, "INSTANCE");
        if (manager == null) throw new IllegalStateException("Original material registry manager was not initialized");
        return manager;
    }
    static String materialName(Object material) throws ReflectiveOperationException {
        return (String)call(material, "getRegistryName");
    }
}
