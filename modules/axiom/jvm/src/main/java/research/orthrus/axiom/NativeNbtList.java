package research.orthrus.axiom;

/** Native homogeneous NBT list; no substituted array/list storage. */
final class NativeNbtList extends NativeNbtValue {
    NativeNbtList(RegistryRuntime runtime, Object value) { super(runtime, value); }
    private Object call(String method, Class<?>[] parameters, Object... args) { return runtime.utility("ge", method, parameters, value, args); }
    private Object index(String method, int index) { return call(method, new Class<?>[]{int.class}, index); }
    public void appendTag(NativeNbtValue tag) { call("a", new Class<?>[]{runtime.nativeType("gn")}, tag == null ? null : tag.value); }
    public void set(int index, NativeNbtValue tag) { call("a", new Class<?>[]{int.class, runtime.nativeType("gn")}, index, tag == null ? null : tag.value); }
    public NativeNbtValue removeTag(int index) { return runtime.wrapNbt(index("a", index)); }
    public NativeNbtValue get(int index) { return runtime.wrapNbt(index("i", index)); }
    public NativeNbtCompound getCompoundTagAt(int index) { return (NativeNbtCompound) runtime.wrapNbt(index("b", index)); }
    public String getStringTagAt(int index) { return (String) index("h", index); }
    public int tagCount() { return (int) call("c", new Class<?>[0]); }
    public int getTagType() { return (int) call("g", new Class<?>[0]); }
    @Override public NativeNbtList copy() { return (NativeNbtList) super.copy(); }
}
