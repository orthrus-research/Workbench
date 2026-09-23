package research.orthrus.axiom;

import java.util.Set;

/** Typed calls into selected original NBTTagCompound bytecode, including its aliasing and coercion. */
final class NativeNbtCompound extends NativeNbtValue {
    NativeNbtCompound(RegistryRuntime runtime, Object value) { super(runtime, value); }
    private Object call(String method, Class<?>[] parameters, Object... arguments) {
        return runtime.utility("fy", method, parameters, value, arguments);
    }
    private Object get(String method, String key) { return call(method, new Class<?>[]{String.class}, key); }
    private void set(String key, Class<?> type, Object value) { call("a", new Class<?>[]{String.class, type}, key, value); }
    public void setByte(String key, byte value) { set(key, byte.class, value); }
    public void setShort(String key, short value) { set(key, short.class, value); }
    public void setInteger(String key, int value) { set(key, int.class, value); }
    public void setLong(String key, long value) { set(key, long.class, value); }
    public void setFloat(String key, float value) { set(key, float.class, value); }
    public void setDouble(String key, double value) { set(key, double.class, value); }
    public void setString(String key, String value) { set(key, String.class, value); }
    public void setBoolean(String key, boolean value) { set(key, boolean.class, value); }
    public void setByteArray(String key, byte[] value) { set(key, byte[].class, value); }
    public void setIntArray(String key, int[] value) { set(key, int[].class, value); }
    public void setTag(String key, NativeNbtValue value) {
        NativeNbtGuard.requireTag(key, value);
        set(key, runtime.nativeType("gn"), value.value);
    }
    public boolean hasKey(String key) { return (boolean) get("e", key); }
    public boolean hasKey(String key, int type) { return (boolean) call("b", new Class<?>[]{String.class, int.class}, key, type); }
    public byte getTagId(String key) { return (byte) get("d", key); }
    public NativeNbtValue getTag(String key) { return runtime.wrapNbt(get("c", key)); }
    public byte getByte(String key) { return (byte) get("f", key); }
    public short getShort(String key) { return (short) get("g", key); }
    public int getInteger(String key) { return (int) get("h", key); }
    public long getLong(String key) { return (long) get("i", key); }
    public float getFloat(String key) { return (float) get("j", key); }
    public double getDouble(String key) { return (double) get("k", key); }
    public String getString(String key) { return (String) get("l", key); }
    public byte[] getByteArray(String key) { return (byte[]) get("m", key); }
    public int[] getIntArray(String key) { return (int[]) get("n", key); }
    public boolean getBoolean(String key) { return (boolean) get("q", key); }
    public NativeNbtCompound getCompoundTag(String key) { return (NativeNbtCompound) runtime.wrapNbt(get("p", key)); }
    public NativeNbtList getTagList(String key, int type) {
        return (NativeNbtList) runtime.wrapNbt(call("c", new Class<?>[]{String.class, int.class}, key, type));
    }
    public void removeTag(String key) { get("r", key); }
    @SuppressWarnings("unchecked") public Set<String> getKeySet() { return (Set<String>) call("c", new Class<?>[0]); }
    @Override public NativeNbtCompound copy() { return (NativeNbtCompound) super.copy(); }
    public void merge(NativeNbtCompound other) { call("a", new Class<?>[]{runtime.nativeType("fy")}, other.value); }
}
