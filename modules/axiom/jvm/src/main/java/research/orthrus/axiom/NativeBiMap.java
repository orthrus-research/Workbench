package research.orthrus.axiom;

import java.util.*;

/** Original selected Guava BiMap; delegate views retain upstream behavior. */
final class NativeBiMap<K,V> extends AbstractMap<K,V> {
    private final RegistryRuntime runtime;
    private final Map<K,V> values;
    @SuppressWarnings("unchecked") NativeBiMap(RegistryRuntime runtime) {
        this(runtime, (Map<K,V>) runtime.utility("com.google.common.collect.HashBiMap", "create", new Class<?>[0], null));
    }
    @SuppressWarnings("unchecked") NativeBiMap(RegistryRuntime runtime, NativeBiMap<K,V> source) {
        this(runtime, (Map<K,V>) runtime.utility("com.google.common.collect.HashBiMap", "create", new Class<?>[]{Map.class}, null, source));
    }
    private NativeBiMap(RegistryRuntime runtime, Map<K,V> values) { this.runtime = runtime; this.values = values; }
    @SuppressWarnings("unchecked") Map<K,V> unmodifiableView() {
        return (Map<K,V>) runtime.utility("com.google.common.collect.Maps", "unmodifiableBiMap",
                new Class<?>[]{runtime.nativeType("com.google.common.collect.BiMap")}, null, values);
    }
    @SuppressWarnings("unchecked") NativeBiMap<V,K> inverse() {
        return new NativeBiMap<>(runtime, (Map<V,K>) runtime.utility("com.google.common.collect.BiMap", "inverse", new Class<?>[0], values));
    }
    @Override public Set<Entry<K,V>> entrySet() { return values.entrySet(); }
    @Override public V put(K key, V value) { return values.put(key, value); }
    @Override public V get(Object key) { return values.get(key); }
    @Override public V remove(Object key) { return values.remove(key); }
    @Override public boolean containsKey(Object key) { return values.containsKey(key); }
    @Override public boolean containsValue(Object value) { return values.containsValue(value); }
}
