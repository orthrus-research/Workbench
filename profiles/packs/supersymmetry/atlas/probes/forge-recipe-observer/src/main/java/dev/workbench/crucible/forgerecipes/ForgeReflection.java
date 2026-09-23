package dev.workbench.crucible.forgerecipes;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.Hashing;
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Exact reflection at the original Forge runtime; no replacement game classes. */
final class ForgeReflection {
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();
    private static final Map<List<Object>,Method> RESOLVED_METHODS = new ConcurrentHashMap<List<Object>,Method>();
    private static final Map<List<Object>,MethodHandle> PUBLIC_FIELDS = new ConcurrentHashMap<List<Object>,MethodHandle>();
    private static final Map<List<Object>,MethodHandle> VIRTUAL_CONTRACTS = new ConcurrentHashMap<List<Object>,MethodHandle>();
    private ForgeReflection() {}

    static Class<?> type(String name) {
        try { return Class.forName(name, false, Thread.currentThread().getContextClassLoader()); }
        catch (ClassNotFoundException failure) { throw new IllegalStateException("Missing original class: " + name, failure); }
    }
    static boolean is(Object value, String name) { return value != null && type(name).isInstance(value); }
    static Object field(Object target, String name) { return fieldNames(target, new String[]{name}); }
    static Object fieldNames(Object target, String[] names) {
        Class<?> owner = target instanceof Class<?> ? (Class<?>) target : target.getClass();
        for (String name : names) for (Class<?> current = owner; current != null; current = current.getSuperclass()) {
            try {
                Field field = current.getDeclaredField(name);
                field.setAccessible(true);
                return field.get(target instanceof Class<?> ? null : target);
            } catch (NoSuchFieldException absent) { /* Continue with the actual superclass. */ }
            catch (IllegalAccessException failure) { throw new IllegalStateException("Cannot read " + owner.getName() + "." + name, failure); }
        }
        throw new IllegalStateException("Missing original field: " + owner.getName() + "." + Arrays.toString(names));
    }
    /** Exact public field descriptor avoids resolving unrelated absent client field types. */
    static Object readPublicField(Object receiver,Class<?> owner,String name,Class<?> result) {
        boolean isStatic=receiver==null;
        List<Object> key=Arrays.<Object>asList(owner,name,result,isStatic);
        MethodHandle handle=PUBLIC_FIELDS.get(key);
        if(handle==null) {
            try {
                handle=isStatic?MethodHandles.publicLookup().findStaticGetter(owner,name,result)
                    :MethodHandles.publicLookup().findGetter(owner,name,result);
                PUBLIC_FIELDS.putIfAbsent(key,handle);handle=PUBLIC_FIELDS.get(key);
            } catch(ReflectiveOperationException failure) {
                throw new IllegalStateException("Missing original public field contract: "+owner.getName()+"."+name+":"+result.getName(),failure);
            }
        }
        try {return isStatic?handle.invoke():handle.invoke(receiver);}
        catch(Throwable failure) {throw new IllegalStateException("Original public field read failed: "+owner.getName()+"."+name,failure);}
    }
    static Object call(Object target, String name, Object... arguments) { return callNames(target, new String[]{name}, arguments); }
    static MethodHandle virtualContract(Class<?> owner,String name,Class<?> result,Class<?>... parameters) {
        MethodType signature=MethodType.methodType(result,parameters);
        List<Object> key=Arrays.<Object>asList(owner,name,signature);
        MethodHandle handle=VIRTUAL_CONTRACTS.get(key);
        if(handle!=null)return handle;
        try {
            handle=MethodHandles.publicLookup().findVirtual(owner,name,signature);
            VIRTUAL_CONTRACTS.putIfAbsent(key,handle);return VIRTUAL_CONTRACTS.get(key);
        } catch(ReflectiveOperationException failure) {
            throw new IllegalStateException("Missing original virtual contract: "+owner.getName()+"."+name+signature,failure);
        }
    }
    /** Exact original zero-argument contract; unrelated client signatures are not resolved. */
    static Object callVirtual(Object target,Class<?> owner,String name,Class<?> result) {
        MethodHandle handle=virtualContract(owner,name,result);
        try {return handle.invoke(target);}
        catch(Throwable failure) {throw new IllegalStateException("Original virtual contract failed: "+owner.getName()+"."+name+" on "+target.getClass().getName(),failure);}
    }
    static String virtualOwner(Object target,String name,Class<?> result,Class<?>... parameters) {
        MethodHandle handle=virtualContract(target.getClass(),name,result,parameters);
        return MethodHandles.publicLookup().revealDirect(handle).getDeclaringClass().getName();
    }
    static Object callNames(Object target, String[] names, Object... arguments) {
        Class<?> owner = target instanceof Class<?> ? (Class<?>) target : target.getClass();
        List<Object> key=new ArrayList<Object>();key.add(owner);key.add(target instanceof Class<?>);
        key.add(Arrays.asList(names.clone()));
        for(Object argument:arguments)key.add(argument==null?null:argument.getClass());
        Method cached=RESOLVED_METHODS.get(key);
        if(cached!=null)return invokeResolved(cached,target,arguments);
        for (String name : names) {
            Method chosen = null;
            // Registry identity belongs to Forge's public IForgeRegistryEntry
            // contract. Enumerating a concrete mod Item also resolves unrelated
            // tooltip signatures, which can reference absent client-only types.
            // Invoking the interface method preserves ordinary virtual dispatch.
            if (!(target instanceof Class<?>) && arguments.length == 0 && name.equals("getRegistryName"))
                chosen = zeroArgumentInterfaceMethod(owner, name);
            Method[] candidates = chosen == null ? owner.getMethods() : new Method[0];
            for (Method method : candidates) {
                if (!method.getName().equals(name) || method.isBridge() || method.getParameterTypes().length != arguments.length
                    || Modifier.isStatic(method.getModifiers()) != (target instanceof Class<?>)) continue;
                Class<?>[] parameters = method.getParameterTypes();
                boolean matches = true;
                for (int i = 0; i < parameters.length; i++) {
                    Class<?> parameter = boxed(parameters[i]);
                    if (arguments[i] == null ? parameters[i].isPrimitive() : !parameter.isInstance(arguments[i])) matches = false;
                }
                if (!matches) continue;
                if (chosen != null && !Arrays.equals(chosen.getParameterTypes(), parameters))
                    throw new IllegalStateException("Ambiguous original method: " + owner.getName() + "." + name);
                chosen = method;
            }
            if (chosen != null) {
                chosen.setAccessible(true);
                RESOLVED_METHODS.putIfAbsent(key,chosen);
                return invokeResolved(chosen,target,arguments);
            }
        }
        throw new IllegalStateException("Missing original method: " + owner.getName() + "." + Arrays.toString(names));
    }
    private static Object invokeResolved(Method chosen,Object target,Object[] arguments) {
        try {return chosen.invoke(target instanceof Class<?> ? null : target,arguments);}
        catch(IllegalAccessException failure){throw new IllegalStateException("Cannot invoke original method "+chosen,failure);}
        catch(InvocationTargetException failure){throw new IllegalStateException("Original method failed: "+chosen,failure.getCause());}
    }
    private static Method zeroArgumentInterfaceMethod(Class<?> owner, String name) {
        for (Class<?> current=owner; current!=null; current=current.getSuperclass()) {
            for (Class<?> contract:current.getInterfaces()) {
                try {
                    Method method=contract.getMethod(name);
                    if (Modifier.isPublic(contract.getModifiers()) && !Modifier.isStatic(method.getModifiers())) return method;
                } catch (NoSuchMethodException absent) { /* This interface does not declare the operation. */ }
            }
        }
        return null;
    }
    private static Class<?> boxed(Class<?> type) {
        if (type == int.class) return Integer.class;
        if (type == long.class) return Long.class;
        if (type == boolean.class) return Boolean.class;
        if (type == byte.class) return Byte.class;
        if (type == short.class) return Short.class;
        if (type == float.class) return Float.class;
        if (type == double.class) return Double.class;
        if (type == char.class) return Character.class;
        return type;
    }
    static List<Object> list(Object value) {
        if (value == null) throw new IllegalStateException("Unexpected null collection");
        List<Object> result = new ArrayList<Object>();
        if (value instanceof Iterable<?>) for (Object row : (Iterable<?>) value) result.add(row);
        else if (value.getClass().isArray()) for (int i = 0; i < Array.getLength(value); i++) result.add(Array.get(value, i));
        else throw new IllegalStateException("Unsupported collection " + value.getClass().getName());
        return result;
    }
    static Map<String,Object> row(Object... pairs) {
        if (pairs.length % 2 != 0) throw new IllegalArgumentException("Pairs required");
        Map<String,Object> result = new LinkedHashMap<String,Object>();
        for (int i = 0; i < pairs.length; i += 2) {
            String key = (String) pairs[i];
            if (result.containsKey(key)) throw new IllegalArgumentException("Duplicate field " + key);
            result.put(key, pairs[i + 1]);
        }
        return result;
    }
    static byte[] bytes(Object value) { return CanonicalJson.bytes(GSON.toJsonTree(value)); }
    static String hash(Object value) { return Hashing.sha256(bytes(value)); }
    static void sort(List<Map<String,Object>> rows) {
        Collections.sort(rows, new Comparator<Map<String,Object>>() {
            public int compare(Map<String,Object> a, Map<String,Object> b) { return CanonicalJson.compareUnsigned(bytes(a), bytes(b)); }
        });
    }
    static Map<String,Object> item(Object stack) {
        if (stack == null || Boolean.TRUE.equals(callNames(stack, new String[]{"isEmpty", "func_190926_b"})))
            throw new IllegalStateException("Empty item stack in finite recipe evidence");
        Object item = callNames(stack, new String[]{"getItem", "func_77973_b"});
        Object name = call(item, "getRegistryName");
        if (name == null) throw new IllegalStateException("Unregistered item stack");
        return row("count", callNames(stack, new String[]{"getCount", "func_190916_E"}),
            "item_damage", callNames(stack, new String[]{"getItemDamage", "func_77952_i"}),
            "metadata", callNames(stack, new String[]{"getMetadata", "func_77960_j"}),
            "registry_name", name.toString(), "tag", nbt(callNames(stack, new String[]{"getTagCompound", "func_77978_p"})));
    }
    static Object copyItem(Object stack) { return callNames(stack, new String[]{"copy", "func_77946_l"}); }
    static Map<String,Object> registeredReference(Object object,Object registry,String kind) {
        Object name=callNames(registry,new String[]{"getNameForObject","func_177774_c"},object);
        Object rawId=callNames(registry,new String[]{"getIDForObject","func_148757_b"},object);
        if(name==null || !(rawId instanceof Integer) || ((Integer)rawId)<0
            || callNames(registry,new String[]{"getObject","func_82594_a"},name)!=object
            || callNames(registry,new String[]{"getObjectById","func_148754_a"},rawId)!=object
            || !name.equals(call(object,"getRegistryName")))
            throw new IllegalStateException("Invalid original "+kind+" registry-reference roundtrip");
        return row("runtime_class",object.getClass().getName(),"projection_kind","registry-reference",
            "registry_kind",kind,"registry_name",name.toString(),"numeric_id",rawId);
    }
    static Map<String,Object> fluid(Object stack) {
        if (stack == null) return null;
        Object fluid = call(stack, "getFluid");
        if (fluid == null) throw new IllegalStateException("Unregistered fluid stack");
        return row("amount", field(stack, "amount"), "fluid_name", call(fluid, "getName"), "tag", nbt(field(stack, "tag")));
    }
    static Object nbt(Object value) { return nbt(value, new IdentityHashMap<Object,Boolean>()); }
    private static Object nbt(Object value, IdentityHashMap<Object,Boolean> visiting) {
        if (value == null) return null;
        if (visiting.put(value, Boolean.TRUE) != null) throw new IllegalStateException("Cyclic NBT object");
        try {
            int id = ((Number) callNames(value, new String[]{"getId", "func_74732_a"})).intValue();
            Object content;
            switch (id) {
                case 0: throw new IllegalStateException("Unexpected NBT end marker as a captured value");
                case 1: content = callNames(value, new String[]{"getByte", "func_150290_f"}); break;
                case 2: content = callNames(value, new String[]{"getShort", "func_150289_e"}); break;
                case 3: content = callNames(value, new String[]{"getInt", "func_150287_d"}); break;
                case 4: content = callNames(value, new String[]{"getLong", "func_150291_c"}); break;
                case 5: content = floating((Float) callNames(value, new String[]{"getFloat", "func_150288_h"})); break;
                case 6: content = floating((Double) callNames(value, new String[]{"getDouble", "func_150286_g"})); break;
                case 7: content = list(callNames(value, new String[]{"getByteArray", "func_150292_c"})); break;
                case 8: content = callNames(value, new String[]{"getString", "func_150285_a_"}); break;
                case 9: {
                    int count = ((Number) callNames(value, new String[]{"tagCount", "func_74745_c"})).intValue();
                    int elementType = ((Number) callNames(value, new String[]{"getTagType", "func_150303_d"})).intValue();
                    if (elementType < 0 || elementType > 12) throw new IllegalStateException("Invalid original NBT list type");
                    List<Object> items = new ArrayList<Object>();
                    for (int i = 0; i < count; i++) {
                        Object child = callNames(value, new String[]{"get", "func_179238_g"}, i);
                        if (((Number) callNames(child, new String[]{"getId", "func_74732_a"})).intValue() != elementType)
                            throw new IllegalStateException("Original NBT list type differs from child");
                        items.add(nbt(child, visiting));
                    }
                    return row("tag_id", id, "element_type", elementType, "value", items);
                }
                case 10: {
                    List<Object> keys = list(callNames(value, new String[]{"getKeySet", "func_150296_c"}));
                    Collections.sort(keys, Comparator.comparing(Object::toString));
                    Map<String,Object> items = new LinkedHashMap<String,Object>();
                    for (Object key : keys) items.put((String) key, nbt(callNames(value, new String[]{"getTag", "func_74781_a"}, key), visiting));
                    content = items; break;
                }
                case 11: content = list(callNames(value, new String[]{"getIntArray", "func_150302_c"})); break;
                // Original Minecraft 1.12.2 exposes no public long-array getter.
                case 12: content = list(fieldNames(value, new String[]{"data", "field_193587_b"})); break;
                default: throw new IllegalStateException("Unsupported original NBT tag " + id);
            }
            return row("tag_id", id, "value", content);
        } finally { visiting.remove(value); }
    }
    static Object floating(Number number) {
        if (number instanceof Float) return row("decimal", Float.toString(number.floatValue()),
            "raw_bits", Integer.toUnsignedString(Float.floatToRawIntBits(number.floatValue())), "value_kind", "float32");
        return row("decimal", Double.toString(number.doubleValue()),
            "raw_bits", Long.toUnsignedString(Double.doubleToRawLongBits(number.doubleValue())), "value_kind", "float64");
    }
    static Object value(Object object) { return value(object, new IdentityHashMap<Object,Boolean>()); }
    private static Object value(Object object, IdentityHashMap<Object,Boolean> visiting) {
        if (object == null || object instanceof String || object instanceof Boolean || object instanceof Byte
            || object instanceof Short || object instanceof Integer || object instanceof Long) return object;
        if (object instanceof Float || object instanceof Double) return floating((Number) object);
        if (object instanceof Enum<?>) return row("runtime_class", object.getClass().getName(), "name", ((Enum<?>) object).name());
        if (object instanceof Class<?>) return row("class_name", ((Class<?>) object).getName(), "value_kind", "class");
        String executableClass = object.getClass().getName();
        if (executableClass.contains("$$Lambda$") || executableClass.startsWith("java.util.function.")
            || executableInterface(object.getClass())) {
            List<String> interfaces = new ArrayList<String>();
            for (Class<?> contract : object.getClass().getInterfaces()) interfaces.add(contract.getName());
            Collections.sort(interfaces);
            int hiddenAddress = executableClass.indexOf("/0x");
            if (hiddenAddress >= 0) executableClass = executableClass.substring(0, hiddenAddress);
            return row("runtime_class", executableClass, "projection_kind", "opaque-executable",
                "behavior_body_captured", false, "functional_interfaces", interfaces);
        }
        if (visiting.put(object, Boolean.TRUE) != null) throw new IllegalStateException("Cyclic recipe property " + object.getClass().getName());
        try {
            if (object instanceof Map<?,?>) {
                List<Map<String,Object>> rows = new ArrayList<Map<String,Object>>();
                for (Map.Entry<?,?> entry : ((Map<?,?>) object).entrySet()) rows.add(row("key", value(entry.getKey(), visiting), "value", value(entry.getValue(), visiting)));
                sort(rows); return row("value_kind", "map", "entries", rows);
            }
            if (object instanceof Collection<?> || object.getClass().isArray()) {
                List<Object> rows = new ArrayList<Object>();
                for (Object entry : list(object)) rows.add(value(entry, visiting));
                if (object instanceof java.util.Set<?>) Collections.sort(rows, (a,b) -> CanonicalJson.compareUnsigned(bytes(a), bytes(b)));
                return rows;
            }
            // Record original property data; executable behavior is not serialized.
            String className = object.getClass().getName();
            // Original SussyPatches 1.9.2 stores the untranslated key and ordered
            // arguments in this record. Admit its exact data class, preserving
            // every field rather than evaluating client tooltip behavior.
            boolean translationData=className.equals("dev.tianmi.sussypatches.api.recipe.property.InfoProperty$TranslationData");
            if (!translationData) {
                if (is(object, "net.minecraft.item.ItemStack")) return item(object);
                if (is(object, "net.minecraftforge.fluids.FluidStack")) return fluid(object);
                if (is(object, "net.minecraft.nbt.NBTBase")) return nbt(object);
                if (is(object, "net.minecraft.util.ResourceLocation")) return object.toString();
                if (is(object, "net.minecraft.world.biome.Biome"))
                    return registeredReference(object,fieldNames(type("net.minecraft.world.biome.Biome"),new String[]{"REGISTRY","field_185377_q"}),"minecraft-biome");
                if (ForgeBlockStateValue.supports(object)) return ForgeBlockStateValue.encode(object);
                if (!className.startsWith("gregtech.") && !className.startsWith("supersymmetry."))
                    throw new IllegalStateException("Unsupported recipe property class " + className);
            }
            Map<String,Object> fields = new java.util.TreeMap<String,Object>();
            for (Class<?> current = object.getClass(); current != Object.class && current != null; current = current.getSuperclass()) {
                for (Field field : current.getDeclaredFields()) {
                    if (Modifier.isStatic(field.getModifiers()) || field.isSynthetic()) continue;
                    field.setAccessible(true);
                    try { fields.put(current.getName() + "." + field.getName(), value(field.get(object), visiting)); }
                    catch (IllegalAccessException failure) { throw new IllegalStateException("Cannot capture recipe property", failure); }
                }
            }
            if (fields.isEmpty()) throw new IllegalStateException("Opaque recipe property class " + className);
            return row("runtime_class", className, "fields", fields);
        } finally { visiting.remove(object); }
    }
    private static boolean executableInterface(Class<?> owner) {
        if (owner == null) return false;
        for (Class<?> contract : owner.getInterfaces()) {
            if (contract.getName().startsWith("java.util.function.")
                || contract.getName().equals("gregtech.api.recipes.ingredients.nbtmatch.NBTMatcher")
                || executableInterface(contract)) return true;
        }
        return executableInterface(owner.getSuperclass());
    }
}
