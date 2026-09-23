package research.orthrus.axiom;

/** Original selected NBT identity. No NBT representation or equality algorithm is copied. */
class NativeNbtValue {
    final RegistryRuntime runtime;
    final Object value;
    NativeNbtValue(RegistryRuntime runtime, Object value) { this.runtime = runtime; this.value = value; }
    public byte getId() { return (byte) runtime.utility("gn", "a", new Class<?>[0], value); }
    public NativeNbtValue copy() { return runtime.wrapNbt(runtime.utility("gn", "b", new Class<?>[0], value)); }
    public boolean isEmpty() { return (boolean) runtime.utility("gn", "b_", new Class<?>[0], value); }
    @Override public boolean equals(Object other) { return other instanceof NativeNbtValue nbt && value.equals(nbt.value); }
    @Override public int hashCode() { return value.hashCode(); }
    @Override public String toString() { return value.toString(); }
}
