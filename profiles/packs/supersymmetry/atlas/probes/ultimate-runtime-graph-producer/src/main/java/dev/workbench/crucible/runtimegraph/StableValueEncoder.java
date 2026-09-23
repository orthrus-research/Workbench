package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import gregtech.api.unification.Element;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.info.MaterialFlag;
import gregtech.api.unification.material.info.MaterialIconSet;

import net.minecraft.enchantment.Enchantment;
import net.minecraft.block.Block;
import net.minecraft.block.properties.IProperty;
import net.minecraft.block.state.IBlockState;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTBase;
import net.minecraft.nbt.NBTTagByte;
import net.minecraft.nbt.NBTTagByteArray;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.nbt.NBTTagDouble;
import net.minecraft.nbt.NBTTagFloat;
import net.minecraft.nbt.NBTTagInt;
import net.minecraft.nbt.NBTTagIntArray;
import net.minecraft.nbt.NBTTagList;
import net.minecraft.nbt.NBTTagLong;
import net.minecraft.nbt.NBTTagShort;
import net.minecraft.nbt.NBTTagString;
import net.minecraft.util.ResourceLocation;
import net.minecraft.world.biome.Biome;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.registries.IForgeRegistryEntry;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.IdentityHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Bounded structural encoder for profile-extension material properties. */
public final class StableValueEncoder {
    private static final int MAX_DEPTH = 16;
    private static final int MAX_COLLECTION = 1000000;

    private final Set<String> diagnostics = new LinkedHashSet<String>();
    private int unsupportedCount;

    /** A bounded NBT projection could not be constructed within its declared budget. */
    public static final class EncodingLimitException extends RuntimeException {
        private final String code;

        private EncodingLimitException(String code) {
            super(code, null, false, false);
            this.code = code;
        }

        public String getCode() {
            return code;
        }
    }

    public JsonElement encode(Object value) {
        return encode(value, "$", 0, new IdentityHashMap<Object, Boolean>());
    }

    /**
     * Encode NBT without first materializing an unbounded JSON tree.
     *
     * <p>The depth is measured in the eventual record, where the NBT object is
     * the value of {@code canonical_value} at depth one.  The node and text
     * budgets apply only to the NBT projection; callers reserve the remainder
     * of their record-wide limits for the record envelope.</p>
     */
    public JsonElement encodeBoundedNbt(
        NBTBase value,
        int maximumRecordDepth,
        int maximumNbtNodes,
        int maximumNbtTextCodePoints
    ) {
        if (value == null) return JsonNull.INSTANCE;
        if (maximumRecordDepth < 2 || maximumNbtNodes < 1
            || maximumNbtTextCodePoints < 0) {
            throw new IllegalArgumentException("invalid bounded NBT budget");
        }
        NbtBudget budget = new NbtBudget(
            maximumRecordDepth,
            maximumNbtNodes,
            maximumNbtTextCodePoints
        );
        return boundedNbt(value, 1, budget);
    }

    public int getUnsupportedCount() {
        return unsupportedCount;
    }

    public List<String> getDiagnostics() {
        List<String> result = new ArrayList<String>(diagnostics);
        Collections.sort(result);
        return result;
    }

    private JsonElement encode(
        Object value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        if (value == null) return JsonNull.INSTANCE;
        if (depth > MAX_DEPTH) return unsupported("depth-limit", value, path);
        if (value instanceof String || value instanceof Character) {
            return new JsonPrimitive(String.valueOf(value));
        }
        if (value instanceof Boolean) return new JsonPrimitive((Boolean) value);
        if (value instanceof Byte || value instanceof Short || value instanceof Integer) {
            return new JsonPrimitive(((Number) value).intValue());
        }
        if (value instanceof Long) return new JsonPrimitive(((Long) value).longValue());
        if (value instanceof BigInteger || value instanceof BigDecimal) {
            JsonObject result = typed(value);
            result.addProperty("decimal", value.toString());
            return result;
        }
        if (value instanceof Float) return floating(((Float) value).floatValue());
        if (value instanceof Double) return floating(((Double) value).doubleValue());
        if (value instanceof Enum<?>) {
            JsonObject result = typed(value);
            result.addProperty("name", ((Enum<?>) value).name());
            return result;
        }
        if (value instanceof Class<?>) {
            JsonObject result = new JsonObject();
            result.addProperty("class_name", stableClassName((Class<?>) value));
            result.addProperty("value_kind", "class");
            return result;
        }
        if (value instanceof ResourceLocation) return new JsonPrimitive(value.toString());
        if (value instanceof Material) return material((Material) value);
        if (value instanceof MaterialFlag) return new JsonPrimitive(value.toString());
        if (value instanceof MaterialIconSet) return iconSet((MaterialIconSet) value);
        if (value instanceof Element) return element((Element) value);
        if (value instanceof Fluid) return fluid((Fluid) value);
        if (value instanceof FluidStack) {
            return fluidStack((FluidStack) value, path, depth, ancestors);
        }
        if (value instanceof ItemStack) return itemStack((ItemStack) value, path, depth, ancestors);
        if (value instanceof NBTBase) return nbt((NBTBase) value, path, depth, ancestors);
        if (value instanceof IBlockState) return blockState((IBlockState) value);
        if (value instanceof Block) {
            return registered(value, Block.REGISTRY.getNameForObject((Block) value));
        }
        if (value instanceof Biome) {
            return registered(value, Biome.REGISTRY.getNameForObject((Biome) value));
        }
        if (value instanceof UUID) return new JsonPrimitive(value.toString());
        if (value instanceof Enchantment) {
            ResourceLocation name = Enchantment.REGISTRY.getNameForObject((Enchantment) value);
            return registered(value, name);
        }
        if (value instanceof Item) {
            return registered(value, Item.REGISTRY.getNameForObject((Item) value));
        }
        if (value instanceof IForgeRegistryEntry<?>) {
            return registered(value, ((IForgeRegistryEntry<?>) value).getRegistryName());
        }
        if (value.getClass().isArray()) {
            return array(value, path, depth, ancestors);
        }
        if (value instanceof Map<?, ?>) {
            return map((Map<?, ?>) value, path, depth, ancestors);
        }
        if (value instanceof Set<?>) {
            return collection((Collection<?>) value, path, depth, ancestors, true);
        }
        if (value instanceof Collection<?>) {
            return collection((Collection<?>) value, path, depth, ancestors, false);
        }
        if (value instanceof Map.Entry<?, ?>) {
            JsonObject result = typed(value);
            Map.Entry<?, ?> entry = (Map.Entry<?, ?>) value;
            result.add("key", encode(entry.getKey(), path + ".key", depth + 1, ancestors));
            result.add("value", encode(entry.getValue(), path + ".value", depth + 1, ancestors));
            return result;
        }
        String className = value.getClass().getName();
        if (className.startsWith("java.util.function.") || className.contains("$$Lambda")) {
            return executable(value, path, depth, ancestors);
        }
        if (className.startsWith("java.") || className.startsWith("javax.")) {
            return unsupported("unsupported-java-value", value, path);
        }
        return reflect(value, path, depth, ancestors);
    }

    private JsonElement reflect(
        Object value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        if (ancestors.put(value, Boolean.TRUE) != null) {
            return unsupported("object-cycle", value, path);
        }
        try {
            JsonObject result = typed(value);
            JsonObject fields = new JsonObject();
            List<Field> reflected = ReflectionAccess.instanceFields(value.getClass());
            for (Field field : reflected) {
                String key = stableClassName(field.getDeclaringClass()) + '#' + field.getName();
                try {
                    fields.add(
                        key,
                        encode(field.get(value), path + '.' + key, depth + 1, ancestors)
                    );
                } catch (IllegalAccessException exception) {
                    fields.add(key, unsupported("inaccessible-field", value, path + '.' + key));
                }
            }
            result.add("fields", fields);
            result.addProperty(
                "projection_kind",
                reflected.isEmpty() ? "state-free-type" : "structural-object"
            );
            return result;
        } finally {
            ancestors.remove(value);
        }
    }

    private JsonElement executable(
        Object value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        if (ancestors.put(value, Boolean.TRUE) != null) {
            return unsupported("executable-cycle", value, path);
        }
        try {
            JsonObject result = new JsonObject();
            result.addProperty("runtime_class", stableClassName(value.getClass()));
            result.addProperty("projection_kind", "opaque-executable");
            result.addProperty("behavior_body_captured", false);
            List<String> interfaces = new ArrayList<String>();
            for (Class<?> contract : value.getClass().getInterfaces()) {
                interfaces.add(contract.getName());
            }
            Collections.sort(interfaces);
            JsonArray interfaceRows = new JsonArray();
            for (String contract : interfaces) interfaceRows.add(contract);
            result.add("functional_interfaces", interfaceRows);
            JsonObject captures = new JsonObject();
            for (Field field : ReflectionAccess.instanceFields(value.getClass())) {
                String key = stableClassName(field.getDeclaringClass()) + '#' + field.getName();
                try {
                    captures.add(
                        key,
                        encode(field.get(value), path + '.' + key, depth + 1, ancestors)
                    );
                } catch (IllegalAccessException exception) {
                    captures.add(key, unsupported("inaccessible-field", value, path + '.' + key));
                }
            }
            result.add("captured_values", captures);
            return result;
        } finally {
            ancestors.remove(value);
        }
    }

    private JsonElement array(
        Object value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        if (ancestors.put(value, Boolean.TRUE) != null) {
            return unsupported("array-cycle", value, path);
        }
        try {
            int length = Array.getLength(value);
            if (length > MAX_COLLECTION) return unsupported("array-bound", value, path);
            JsonArray result = new JsonArray();
            for (int index = 0; index < length; index++) {
                result.add(encode(Array.get(value, index), path + '[' + index + ']', depth + 1, ancestors));
            }
            return result;
        } finally {
            ancestors.remove(value);
        }
    }

    private JsonElement collection(
        Collection<?> value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors,
        boolean sort
    ) {
        if (value.size() > MAX_COLLECTION) return unsupported("collection-bound", value, path);
        if (ancestors.put(value, Boolean.TRUE) != null) {
            return unsupported("collection-cycle", value, path);
        }
        try {
            List<JsonElement> rows = new ArrayList<JsonElement>();
            int index = 0;
            for (Object item : value) {
                rows.add(encode(item, path + '[' + index++ + ']', depth + 1, ancestors));
            }
            if (sort) Collections.sort(rows, jsonComparator());
            JsonArray result = new JsonArray();
            for (JsonElement row : rows) result.add(row);
            return result;
        } finally {
            ancestors.remove(value);
        }
    }

    private JsonElement map(
        Map<?, ?> value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        if (value.size() > MAX_COLLECTION) return unsupported("map-bound", value, path);
        if (ancestors.put(value, Boolean.TRUE) != null) {
            return unsupported("map-cycle", value, path);
        }
        try {
            List<JsonObject> entries = new ArrayList<JsonObject>();
            int index = 0;
            for (Map.Entry<?, ?> entry : value.entrySet()) {
                JsonObject row = new JsonObject();
                row.add("key", encode(entry.getKey(), path + ".key[" + index + ']', depth + 1, ancestors));
                row.add("value", encode(entry.getValue(), path + ".value[" + index + ']', depth + 1, ancestors));
                entries.add(row);
                index++;
            }
            Collections.sort(entries, new Comparator<JsonObject>() {
                @Override
                public int compare(JsonObject left, JsonObject right) {
                    return CanonicalJson.compareUnsigned(
                        CanonicalJson.bytes(left.get("key")),
                        CanonicalJson.bytes(right.get("key"))
                    );
                }
            });
            JsonArray result = new JsonArray();
            for (JsonObject entry : entries) result.add(entry);
            return result;
        } finally {
            ancestors.remove(value);
        }
    }

    private JsonElement itemStack(
        ItemStack stack,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        JsonObject result = new JsonObject();
        ResourceLocation name = stack.isEmpty() ? null : Item.REGISTRY.getNameForObject(stack.getItem());
        result.addProperty("count", stack.getCount());
        result.addProperty("item_damage", stack.getItemDamage());
        result.addProperty("metadata", stack.getMetadata());
        if (name == null) result.add("registry_name", JsonNull.INSTANCE);
        else result.addProperty("registry_name", name.toString());
        result.add("tag", encode(stack.getTagCompound(), path + ".tag", depth + 1, ancestors));
        return result;
    }

    private JsonElement fluidStack(
        FluidStack stack,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("amount", stack.amount);
        if (stack.getFluid() == null) result.add("fluid_name", JsonNull.INSTANCE);
        else result.addProperty("fluid_name", stack.getFluid().getName());
        result.add("tag", encode(stack.tag, path + ".tag", depth + 1, ancestors));
        return result;
    }

    private static JsonObject blockState(IBlockState state) {
        JsonObject result = typed(state);
        ResourceLocation block = Block.REGISTRY.getNameForObject(state.getBlock());
        if (block == null) result.add("block_registry_name", JsonNull.INSTANCE);
        else result.addProperty("block_registry_name", block.toString());
        result.addProperty("block_state_id", Block.getStateId(state));
        List<Map.Entry<IProperty<?>, Comparable<?>>> values =
            new ArrayList<Map.Entry<IProperty<?>, Comparable<?>>>(
                state.getProperties().entrySet()
            );
        Collections.sort(values, new Comparator<Map.Entry<IProperty<?>, Comparable<?>>>() {
            @Override
            public int compare(
                Map.Entry<IProperty<?>, Comparable<?>> left,
                Map.Entry<IProperty<?>, Comparable<?>> right
            ) {
                return left.getKey().getName().compareTo(right.getKey().getName());
            }
        });
        JsonObject properties = new JsonObject();
        for (Map.Entry<IProperty<?>, Comparable<?>> entry : values) {
            properties.addProperty(entry.getKey().getName(), propertyValue(entry));
        }
        result.add("properties", properties);
        return result;
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static String propertyValue(Map.Entry<IProperty<?>, Comparable<?>> entry) {
        return ((IProperty) entry.getKey()).getName(entry.getValue());
    }

    private JsonElement nbt(
        NBTBase value,
        String path,
        int depth,
        IdentityHashMap<Object, Boolean> ancestors
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("tag_id", value.getId());
        switch (value.getId()) {
            case 1: result.addProperty("value", ((NBTTagByte) value).getByte()); break;
            case 2: result.addProperty("value", ((NBTTagShort) value).getShort()); break;
            case 3: result.addProperty("value", ((NBTTagInt) value).getInt()); break;
            case 4: result.addProperty("value", ((NBTTagLong) value).getLong()); break;
            case 5: result.add("value", floating(((NBTTagFloat) value).getFloat())); break;
            case 6: result.add("value", floating(((NBTTagDouble) value).getDouble())); break;
            case 7: result.add("value", array(((NBTTagByteArray) value).getByteArray(), path, depth, ancestors)); break;
            case 8: result.addProperty("value", ((NBTTagString) value).getString()); break;
            case 9: {
                NBTTagList list = (NBTTagList) value;
                JsonArray rows = new JsonArray();
                for (int index = 0; index < list.tagCount(); index++) {
                    rows.add(nbt(list.get(index), path + '[' + index + ']', depth + 1, ancestors));
                }
                result.add("value", rows);
                break;
            }
            case 10: {
                NBTTagCompound compound = (NBTTagCompound) value;
                List<String> keys = new ArrayList<String>(compound.getKeySet());
                Collections.sort(keys);
                JsonObject rows = new JsonObject();
                for (String key : keys) {
                    rows.add(key, nbt(compound.getTag(key), path + '.' + key, depth + 1, ancestors));
                }
                result.add("value", rows);
                break;
            }
            case 11: result.add("value", array(((NBTTagIntArray) value).getIntArray(), path, depth, ancestors)); break;
            default: result.add("value", unsupported("unsupported-nbt-tag", value, path));
        }
        return result;
    }

    private JsonElement boundedNbt(NBTBase value, int depth, NbtBudget budget) {
        budget.node(depth);
        JsonObject result = new JsonObject();
        budget.text("tag_id");
        budget.node(depth + 1);
        result.addProperty("tag_id", value.getId());
        budget.text("value");
        switch (value.getId()) {
            case 1:
                budget.node(depth + 1);
                result.addProperty("value", ((NBTTagByte) value).getByte());
                break;
            case 2:
                budget.node(depth + 1);
                result.addProperty("value", ((NBTTagShort) value).getShort());
                break;
            case 3:
                budget.node(depth + 1);
                result.addProperty("value", ((NBTTagInt) value).getInt());
                break;
            case 4:
                budget.node(depth + 1);
                result.addProperty("value", ((NBTTagLong) value).getLong());
                break;
            case 5:
                result.add(
                    "value",
                    boundedFloating(((NBTTagFloat) value).getFloat(), depth + 1, budget)
                );
                break;
            case 6:
                result.add(
                    "value",
                    boundedFloating(((NBTTagDouble) value).getDouble(), depth + 1, budget)
                );
                break;
            case 7: {
                byte[] values = ((NBTTagByteArray) value).getByteArray();
                budget.node(depth + 1);
                budget.nodes(depth + 2, values.length);
                JsonArray rows = new JsonArray();
                for (byte item : values) rows.add(item);
                result.add("value", rows);
                break;
            }
            case 8: {
                String text = ((NBTTagString) value).getString();
                budget.node(depth + 1);
                budget.text(text);
                result.addProperty("value", text);
                break;
            }
            case 9: {
                NBTTagList list = (NBTTagList) value;
                budget.node(depth + 1);
                budget.requireAvailableNodes(list.tagCount());
                JsonArray rows = new JsonArray();
                for (int index = 0; index < list.tagCount(); index++) {
                    rows.add(boundedNbt(list.get(index), depth + 2, budget));
                }
                result.add("value", rows);
                break;
            }
            case 10: {
                NBTTagCompound compound = (NBTTagCompound) value;
                Set<String> keySet = compound.getKeySet();
                budget.node(depth + 1);
                budget.requireAvailableNodes(keySet.size());
                for (String key : keySet) budget.text(key);
                List<String> keys = new ArrayList<String>(keySet);
                Collections.sort(keys);
                JsonObject rows = new JsonObject();
                for (String key : keys) {
                    rows.add(key, boundedNbt(compound.getTag(key), depth + 2, budget));
                }
                result.add("value", rows);
                break;
            }
            case 11: {
                int[] values = ((NBTTagIntArray) value).getIntArray();
                budget.node(depth + 1);
                budget.nodes(depth + 2, values.length);
                JsonArray rows = new JsonArray();
                for (int item : values) rows.add(item);
                result.add("value", rows);
                break;
            }
            default:
                throw new EncodingLimitException("unsupported-nbt-tag-" + value.getId());
        }
        return result;
    }

    private static JsonObject boundedFloating(float value, int depth, NbtBudget budget) {
        String decimal = Float.toString(value);
        String rawBits = Integer.toUnsignedString(Float.floatToRawIntBits(value));
        budget.node(depth);
        budget.nodes(depth + 1, 3);
        budget.text("decimal");
        budget.text(decimal);
        budget.text("raw_bits");
        budget.text(rawBits);
        budget.text("value_kind");
        budget.text("float32");
        return floating(value);
    }

    private static JsonObject boundedFloating(double value, int depth, NbtBudget budget) {
        String decimal = Double.toString(value);
        String rawBits = Long.toUnsignedString(Double.doubleToRawLongBits(value));
        budget.node(depth);
        budget.nodes(depth + 1, 3);
        budget.text("decimal");
        budget.text(decimal);
        budget.text("raw_bits");
        budget.text(rawBits);
        budget.text("value_kind");
        budget.text("float64");
        return floating(value);
    }

    private static final class NbtBudget {
        private final int maximumDepth;
        private final int maximumNodes;
        private final int maximumTextCodePoints;
        private int nodes;
        private int textCodePoints;

        private NbtBudget(
            int maximumDepth,
            int maximumNodes,
            int maximumTextCodePoints
        ) {
            this.maximumDepth = maximumDepth;
            this.maximumNodes = maximumNodes;
            this.maximumTextCodePoints = maximumTextCodePoints;
        }

        private void node(int depth) {
            nodes(depth, 1);
        }

        private void nodes(int depth, int count) {
            if (depth > maximumDepth) {
                throw new EncodingLimitException("depth-limit");
            }
            if (count < 0 || count > maximumNodes - nodes) {
                throw new EncodingLimitException("node-limit");
            }
            nodes += count;
        }

        private void requireAvailableNodes(int minimumAdditionalNodes) {
            if (minimumAdditionalNodes < 0
                || minimumAdditionalNodes > maximumNodes - nodes) {
                throw new EncodingLimitException("node-limit");
            }
        }

        private void text(String value) {
            int count = value.codePointCount(0, value.length());
            if (count > maximumTextCodePoints - textCodePoints) {
                throw new EncodingLimitException("text-limit");
            }
            textCodePoints += count;
        }
    }

    private static JsonObject material(Material value) {
        JsonObject result = typed(value);
        result.addProperty("registry_name", value.getRegistryName());
        return result;
    }

    private static JsonObject iconSet(MaterialIconSet value) {
        JsonObject result = typed(value);
        result.addProperty("id", value.id);
        result.addProperty("name", value.name);
        return result;
    }

    private static JsonObject element(Element value) {
        JsonObject result = typed(value);
        result.addProperty("decay_to", value.decayTo);
        result.addProperty("half_life_seconds", value.halfLifeSeconds);
        result.addProperty("is_isotope", value.isIsotope);
        result.addProperty("name", value.name);
        result.addProperty("neutrons", value.neutrons);
        result.addProperty("protons", value.protons);
        result.addProperty("symbol", value.symbol);
        return result;
    }

    private static JsonObject fluid(Fluid value) {
        JsonObject result = typed(value);
        result.addProperty("name", value.getName());
        return result;
    }

    private static JsonObject registered(Object value, ResourceLocation name) {
        JsonObject result = typed(value);
        if (name == null) result.add("registry_name", JsonNull.INSTANCE);
        else result.addProperty("registry_name", name.toString());
        return result;
    }

    private static JsonObject floating(float value) {
        JsonObject result = new JsonObject();
        result.addProperty("decimal", Float.toString(value));
        result.addProperty("raw_bits", Integer.toUnsignedString(Float.floatToRawIntBits(value)));
        result.addProperty("value_kind", "float32");
        return result;
    }

    private static JsonObject floating(double value) {
        JsonObject result = new JsonObject();
        result.addProperty("decimal", Double.toString(value));
        result.addProperty("raw_bits", Long.toUnsignedString(Double.doubleToRawLongBits(value)));
        result.addProperty("value_kind", "float64");
        return result;
    }

    private static JsonObject typed(Object value) {
        JsonObject result = new JsonObject();
        result.addProperty("runtime_class", stableClassName(value.getClass()));
        return result;
    }

    public static String stableClassName(Class<?> value) {
        String className = value.getName();
        int hiddenSuffix = className.indexOf("/0x");
        return hiddenSuffix < 0 ? className : className.substring(0, hiddenSuffix);
    }

    private JsonObject unsupported(String code, Object value, String path) {
        unsupportedCount++;
        String className = value == null ? "null" : stableClassName(value.getClass());
        diagnostics.add(code + '|' + className + '|' + path);
        JsonObject result = new JsonObject();
        result.addProperty("code", code);
        result.addProperty("runtime_class", className);
        result.addProperty("value_kind", "unsupported");
        return result;
    }

    private static Comparator<JsonElement> jsonComparator() {
        return new Comparator<JsonElement>() {
            @Override
            public int compare(JsonElement left, JsonElement right) {
                return CanonicalJson.compareUnsigned(
                    CanonicalJson.bytes(left),
                    CanonicalJson.bytes(right)
                );
            }
        };
    }
}
