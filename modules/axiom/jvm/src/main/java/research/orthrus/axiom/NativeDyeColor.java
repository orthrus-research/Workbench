package research.orthrus.axiom;

/** Typed identity handle to the original hash-bound enum, never a name-based substitute. */
final class NativeDyeColor {
    private final RegistryRuntime runtime;
    private final Object value;
    NativeDyeColor(RegistryRuntime runtime, Object value) { this.runtime = runtime; this.value = value; }
    static NativeDyeColor[] values() { return FluidEnvironment.current().runtime().dyeColors(); }
    String getName() { return (String) runtime.utility("ahs", "m", new Class<?>[0], value); }
    int getMetadata() { return (int) runtime.utility("ahs", "a", new Class<?>[0], value); }
    int getDyeDamage() { return (int) runtime.utility("ahs", "b", new Class<?>[0], value); }
    String enumName() { runtime.identity(); return ((Enum<?>) value).name(); }
    @Override public int hashCode() { return value.hashCode(); }
    @Override public boolean equals(Object other) { return other instanceof NativeDyeColor color && color.value == value; }
    @Override public String toString() { runtime.identity(); return value.toString(); }
}
