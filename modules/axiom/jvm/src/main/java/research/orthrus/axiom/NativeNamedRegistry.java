package research.orthrus.axiom;

import java.util.*;

/** Delegates collection behavior to the original selected JVM classes, never a modeled identity map. */
class NativeNamedRegistry<K, V> implements Iterable<V> {
    protected final RegistryRuntime runtime;
    private final Object registry;
    protected final Map<K, V> registryObjects;
    protected final IntegerMap underlyingIntegerMap;

    @SuppressWarnings("unchecked")
    NativeNamedRegistry(RegistryRuntime runtime) {
        this(runtime, runtime.newRegistry());
    }
    @SuppressWarnings("unchecked")
    NativeNamedRegistry(RegistryRuntime runtime, Object registry) {
        this.runtime = runtime;
        this.registry = registry;
        registryObjects = (Map<K, V>) runtime.registryMap(registry);
        underlyingIntegerMap = new IntegerMap(runtime.integerMap(registry));
    }

    protected final class IntegerMap {
        private final Object map;
        IntegerMap(Object map) { this.map = map; }
        void put(V value, int id) { runtime.putId(map, value, id); }
    }
    public void putObject(K key, V value) { runtime.putName(registry, key, value); }
    @SuppressWarnings("unchecked") public V getObject(K key) { return (V) runtime.getObject(registry, key); }
    @SuppressWarnings("unchecked") public K getNameForObject(V value) { return (K) runtime.getName(registry, value); }
    public int getIDForObject(V value) { return runtime.getId(registry, value); }
    @SuppressWarnings("unchecked") public V getObjectById(int id) { return (V) runtime.getById(registry, id); }
    @SuppressWarnings("unchecked") public Iterator<V> iterator() { return (Iterator<V>) runtime.iterator(registry); }
}
