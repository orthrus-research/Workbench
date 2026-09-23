package research.orthrus.axiom;

import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;
import org.junit.jupiter.api.Test;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class FluidRegistrationTest {
    record Key(String name, int priority) implements FluidRegistration.Key {
        @Override public int getRegistrationPriority() { return priority; }
        @Override public int hashCode() { return name.hashCode(); }
        @Override public String toString() { return name; }
    }
    private final Key liquid = new Key("liquid", 0);
    private final MaterialState material = new MaterialState(1, "susy", "coolant", MaterialPhase.OPEN::canModifyMaterials);
    private final List<String> effects = new ArrayList<>();
    private FluidRegistration<Key, Object> storage() {
        return new FluidRegistration<>(liquid, () -> (modid, state, key) -> {
            effects.add("default:" + modid + ":" + state); return new Object();
        }, state -> effects.add("collision:" + state));
    }

    @Test void emptyStorageInvokesDefaultBuilderAndFinalizesQueue() {
        var storage = storage();
        storage.registerFluids(material);
        assertNotNull(storage.get(liquid));
        assertEquals(List.of("default:susy:coolant"), effects);
        assertEquals("FluidStorageImpl has already been registered", assertThrows(IllegalStateException.class,
                () -> storage.registerFluids(material)).getMessage());
        assertThrows(IllegalArgumentException.class, () -> storage.getQueuedBuilder(liquid));
        assertThrows(IllegalStateException.class, () -> storage.enqueueRegistration(liquid, null));
    }

    @Test void manualAssociationSuppressesDefaultEvenWhenItsValueIsNull() {
        var storage = storage(); storage.store(liquid, null); storage.registerFluids(material);
        assertTrue(effects.isEmpty());
        assertNull(storage.get(liquid));
        Object after = new Object(); storage.store(liquid, after);
        assertSame(after, storage.get(liquid)); // store remains writable after queue finalization
    }

    @Test void duplicateQueueIsRejectedWithoutReplacingBuilder() {
        var storage = storage();
        FluidRegistration.Builder<Key, Object> original = (m, s, k) -> null;
        storage.enqueueRegistration(liquid, original);
        assertEquals("FluidStorageKey liquid is already queued", assertThrows(IllegalArgumentException.class,
                () -> storage.enqueueRegistration(liquid, (m, s, k) -> new Object())).getMessage());
        assertSame(original, storage.getQueuedBuilder(liquid));
    }

    @Test void builderEffectsPrecedeAssociationCollision() {
        var storage = storage(); Object existing = new Object();
        storage.store(liquid, existing);
        storage.enqueueRegistration(liquid, (owner, state, key) -> { effects.add("built"); return new Object(); });
        storage.registerFluids(material);
        assertEquals(List.of("built", "collision:coolant"), effects);
        assertSame(existing, storage.get(liquid));
    }

    @Test void prioritiesAndNativeMapTieOrderAreRetained() {
        var storage = storage();
        var order = new Object2ObjectOpenHashMap<Key, Object>();
        for (var key : List.of(new Key("Aa", 0), new Key("BB", 0), new Key("first", 7), new Key("last", -1))) {
            order.put(key, new Object());
            storage.enqueueRegistration(key, (owner, state, actual) -> { effects.add(actual.name()); return actual; });
        }
        storage.registerFluids(material);
        assertEquals(order.keySet().stream().sorted(Comparator.comparingInt(k -> -k.priority())).map(Key::name).toList(), effects);
        assertEquals("first", effects.getFirst()); assertEquals("last", effects.getLast());
    }

    @Test void minimumIntegerPriorityRetainsNativeNegationOverflow() {
        var storage = storage();
        for (var key : List.of(new Key("zero", 0), new Key("maximum", Integer.MAX_VALUE), new Key("minimum", Integer.MIN_VALUE)))
            storage.enqueueRegistration(key, (owner, state, actual) -> { effects.add(actual.name()); return actual; });
        storage.registerFluids(material);
        assertEquals(List.of("minimum", "maximum", "zero"), effects);
    }

    @Test void failedPassRetainsQueueAndEarlierEffectsAndRetryRebuildsThem() {
        var storage = storage();
        var first = new Key("first", 1);
        var failing = new Key("failing", 0);
        var attempts = new AtomicInteger(); Object earlier = new Object();
        storage.enqueueRegistration(first, (owner, state, key) -> { effects.add("first"); return earlier; });
        storage.enqueueRegistration(failing, (owner, state, key) -> {
            effects.add("failing");
            if (attempts.getAndIncrement() == 0) throw new IllegalArgumentException("builder failed");
            return key;
        });
        assertThrows(IllegalArgumentException.class, () -> storage.registerFluids(material));
        assertSame(earlier, storage.get(first));
        assertNotNull(storage.getQueuedBuilder(failing));
        storage.registerFluids(material);
        assertEquals(List.of("first", "failing", "first", "collision:coolant", "failing"), effects);
        assertSame(earlier, storage.get(first));
        assertSame(failing, storage.get(failing));
    }

    @Test void failedDefaultBuilderRemainsQueuedRatherThanBeingReplacedOnRetry() {
        var factories = new AtomicInteger();
        var storage = new FluidRegistration<Key, Object>(liquid, () -> {
            factories.incrementAndGet(); return (m, s, k) -> { throw new IllegalStateException("unavailable"); };
        }, state -> {});
        for (int i = 0; i < 2; i++) assertThrows(IllegalStateException.class, () -> storage.registerFluids(material));
        assertEquals(1, factories.get());
        assertNotNull(storage.getQueuedBuilder(liquid));
    }

    @Test void noSyntheticFluidIsCreatedWhenBuilderFails() {
        var storage = storage();
        storage.enqueueRegistration(liquid, (m, s, k) -> { throw Failure.unsupported("fluid.builder", "Unimplemented producer"); });
        var failure = assertThrows(Failure.class, () -> storage.registerFluids(material));
        assertEquals("unsupported", failure.kind);
        assertNull(storage.get(liquid));
        assertTrue(effects.isEmpty());
    }
}
