package research.orthrus.axiom;

/** Native defaulted superclass storage; Forge's wrapper supplies all exposed operations. */
class NativeDefaultedRegistry<K, V> extends NativeNamedRegistry<K, V> {
    NativeDefaultedRegistry(RegistryRuntime runtime, K defaultKey) {
        super(runtime, runtime.newDefaultedRegistry(defaultKey));
    }
}
