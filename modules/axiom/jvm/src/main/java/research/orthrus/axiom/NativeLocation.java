package research.orthrus.axiom;

/** Original Minecraft ResourceLocation value, not a duplicate normalization algorithm. */
final class NativeLocation {
    private final RegistryRuntime runtime;
    private final Object value;
    NativeLocation(String text) { this(FluidEnvironment.current().runtime(), text); }
    NativeLocation(String namespace, String path) { this(FluidEnvironment.current().runtime(), namespace, path); }
    NativeLocation(RegistryRuntime runtime, String... parts) { this.runtime = runtime; value = runtime.location(parts); }
    String getNamespace() { return (String) runtime.utility("nf", "b", new Class<?>[0], value); }
    String getPath() { return (String) runtime.utility("nf", "a", new Class<?>[0], value); }
    @Override public String toString() { return value.toString(); }
    @Override public int hashCode() { return value.hashCode(); }
    @Override public boolean equals(Object other) { return other instanceof NativeLocation location && value.equals(location.value); }
}
