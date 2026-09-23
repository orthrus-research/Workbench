package dev.workbench.crucible.forgerecipes;

import java.lang.invoke.MethodHandle;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Original registered block plus every listed property; no world or metadata inference. */
final class ForgeBlockStateValue {
    private static final String STATE = "net.minecraft.block.state.IBlockState";
    private static final String BLOCK = "net.minecraft.block.Block";
    private static final String CONTAINER = "net.minecraft.block.state.BlockStateContainer";
    private static final String PROPERTY = "net.minecraft.block.properties.IProperty";
    private static final String MAP = "com.google.common.collect.ImmutableMap";
    private static final String LIST = "com.google.common.collect.ImmutableList";
    private static final String OPTIONAL = "com.google.common.base.Optional";
    private static final String EXTENDED = "net.minecraftforge.common.property.IExtendedBlockState";
    private ForgeBlockStateValue() {}

    static boolean supports(Object value) { return value != null && type(STATE).isInstance(value); }
    static Map<String,Object> encode(Object value) {
        require(supports(value), "Value is not an original IBlockState");
        return encode(value, new NativeAccess());
    }

    /** Package-local protocol seam for synthetic tests, never replacement game classes. */
    interface Access {
        Object block(Object state);
        Map<?,?> properties(Object state);
        Collection<?> declaredProperties(Object block);
        Collection<?> validStates(Object block);
        Object defaultState(Object block);
        boolean hasUnlistedProperties(Object state);
        Map<String,Object> registeredBlock(Object block);
        String name(Object property);
        String serialized(Object property,Object value);
        Object parsed(Object property,String value);
        Collection<?> allowed(Object property);
        Object withProperty(Object state,Object property,Object value);
    }

    static Map<String,Object> encode(Object state,Access access) {
        require(state != null && !access.hasUnlistedProperties(state), "Unlisted block-state properties are not captured");
        Object block = access.block(state);
        require(block != null, "Block state has no block");
        Map<String,Object> registered = access.registeredBlock(block);
        Map<?,?> properties = access.properties(state);
        Collection<?> declared = access.declaredProperties(block);
        require(properties != null && declared != null && properties.size() == declared.size()
            && properties.keySet().containsAll(declared), "Listed block-state property inventory differs from its container");
        boolean retained = false;
        for (Object valid : access.validStates(block)) if (valid == state) retained = true;
        require(retained, "Block state is not a canonical state in its original container");
        TreeMap<String,Object> ordered = new TreeMap<String,Object>();
        for (Object property : properties.keySet()) {
            String name = access.name(property);
            require(name != null && !name.isEmpty() && !ordered.containsKey(name), "Invalid or duplicate listed property name");
            ordered.put(name, property);
        }
        Object restored = access.defaultState(block);
        Map<String,Object> values = new LinkedHashMap<String,Object>();
        for (Map.Entry<String,Object> entry : ordered.entrySet()) {
            Object property = entry.getValue(), value = properties.get(property);
            require(value instanceof Comparable<?> && access.allowed(property).contains(value), "Listed property value is outside the original domain");
            String serialized = access.serialized(property,value);
            require(serialized != null && !serialized.isEmpty(), "Listed property has no original serialized value");
            Object parsed = access.parsed(property,serialized);
            require(parsed != null && Objects.equals(parsed,value)
                && serialized.equals(access.serialized(property,parsed)), "Listed property serialization does not roundtrip");
            restored = access.withProperty(restored,property,parsed);
            values.put(entry.getKey(),serialized);
        }
        require(restored == state && access.block(restored) == block
            && properties.equals(access.properties(restored)), "Original listed block state does not roundtrip");
        return row("runtime_class",state.getClass().getName(), "projection_kind","registry-listed-block-state",
            "block",registered, "properties",values);
    }

    private static void require(boolean condition,String message) {
        if (!condition) throw new IllegalStateException(message);
    }
    static Object invoke(Object target,String owner,String[] names,Class<?> result,Class<?>[] parameters,Object... arguments) {
        MethodHandle handle = null;
        IllegalStateException absent = null;
        for (String name : names) {
            try { handle = virtualContract(type(owner),name,result,parameters); break; }
            catch (IllegalStateException failure) { absent = failure; }
        }
        if (handle == null) throw new IllegalStateException("Missing original block-state contract: "+owner+"."+Arrays.toString(names),absent);
        List<Object> values = new ArrayList<Object>(); values.add(target); values.addAll(Arrays.asList(arguments));
        try { return handle.invokeWithArguments(values); }
        catch (Throwable failure) { throw new IllegalStateException("Original block-state contract failed: "+owner+"."+Arrays.toString(names),failure); }
    }
    private static Object zero(Object target,String owner,String mcp,String srg,Class<?> result) {
        return invoke(target,owner,new String[]{mcp,srg},result,new Class<?>[0]);
    }

    private static final class NativeAccess implements Access {
        public Object block(Object state) {
            return zero(state,STATE,"getBlock","func_177230_c",type(BLOCK));
        }
        public Map<?,?> properties(Object state) {
            return (Map<?,?>)zero(state,STATE,"getProperties","func_177228_b",type(MAP));
        }
        private Object container(Object block) {
            return zero(block,BLOCK,"getBlockState","func_176194_O",type(CONTAINER));
        }
        public Collection<?> declaredProperties(Object block) {
            return (Collection<?>)zero(container(block),CONTAINER,"getProperties","func_177623_d",Collection.class);
        }
        public Collection<?> validStates(Object block) {
            return (Collection<?>)zero(container(block),CONTAINER,"getValidStates","func_177619_a",type(LIST));
        }
        public Object defaultState(Object block) {
            return zero(block,BLOCK,"getDefaultState","func_176223_P",type(STATE));
        }
        public boolean hasUnlistedProperties(Object state) {
            return type(EXTENDED).isInstance(state) && !((Map<?,?>)zero(state,EXTENDED,"getUnlistedProperties","getUnlistedProperties",type(MAP))).isEmpty();
        }
        public Map<String,Object> registeredBlock(Object block) {
            return registeredReference(block,fieldNames(type(BLOCK),new String[]{"REGISTRY","field_149771_c"}),"block");
        }
        public String name(Object property) {
            return (String)zero(property,PROPERTY,"getName","func_177701_a",String.class);
        }
        public String serialized(Object property,Object value) {
            return (String)invoke(property,PROPERTY,new String[]{"getName","func_177702_a"},String.class,
                new Class<?>[]{Comparable.class},value);
        }
        public Object parsed(Object property,String value) {
            Object parsed = invoke(property,PROPERTY,new String[]{"parseValue","func_185929_b"},type(OPTIONAL),new Class<?>[]{String.class},value);
            return Boolean.TRUE.equals(zero(parsed,OPTIONAL,"isPresent","isPresent",boolean.class))
                ? zero(parsed,OPTIONAL,"get","get",Object.class) : null;
        }
        public Collection<?> allowed(Object property) {
            return (Collection<?>)zero(property,PROPERTY,"getAllowedValues","func_177700_c",Collection.class);
        }
        public Object withProperty(Object state,Object property,Object value) {
            return invoke(state,STATE,new String[]{"withProperty","func_177226_a"},type(STATE),
                new Class<?>[]{type(PROPERTY),Comparable.class},property,value);
        }
    }
}
