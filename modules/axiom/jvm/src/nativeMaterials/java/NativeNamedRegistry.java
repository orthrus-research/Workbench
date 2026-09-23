package research.orthrus.axiom.nativeconstruction;

import java.util.*;
import net.minecraft.util.registry.RegistryNamespaced;
import net.minecraft.util.registry.RegistrySimple;
import net.minecraft.util.IntIdentityHashBiMap;

/** Method-name bindings only; native maps, IDs and iteration stay in Cleanroom's class. */
class NativeNamedRegistry<K, V> implements Iterable<V> {
    protected final RegistryRuntime runtime;
    private final RegistryNamespaced registry = new RegistryNamespaced();
    protected final Map<K, V> registryObjects = field(RegistrySimple.class, "field_82596_a");
    private final IntIdentityHashBiMap<V> integers = field(RegistryNamespaced.class, "field_148759_a");
    protected final IntegerMap underlyingIntegerMap = new IntegerMap();
    NativeNamedRegistry(RegistryRuntime runtime) { this.runtime = runtime; }
    @SuppressWarnings("unchecked") private <T> T field(Class<?> owner, String name) {
        try { var field = owner.getDeclaredField(name); field.setAccessible(true); return (T) field.get(registry); }
        catch (ReflectiveOperationException failure) { throw new IllegalStateException("Native material registry binding unavailable", failure); }
    }
    protected final class IntegerMap { void put(V value, int id) { integers.func_186814_a(value, id); } }
    public void putObject(K key, V value) { registry.func_82595_a(key, value); }
    @SuppressWarnings("unchecked") public V getObject(K key) { return (V) registry.func_82594_a(key); }
    @SuppressWarnings("unchecked") public K getNameForObject(V value) { return (K) registry.func_177774_c(value); }
    public int getIDForObject(V value) { return registry.func_148757_b(value); }
    @SuppressWarnings("unchecked") public V getObjectById(int id) { return (V) registry.func_148754_a(id); }
    @SuppressWarnings("unchecked") public Iterator<V> iterator() { return registry.iterator(); }
}
