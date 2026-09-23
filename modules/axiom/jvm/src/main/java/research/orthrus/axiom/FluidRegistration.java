// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.
package research.orthrus.axiom;



import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;

import java.util.Comparator;
import java.util.Map;

final class FluidRegistration<K extends FluidRegistration.Key, F> {

    private final Map<K, F> map = new Object2ObjectOpenHashMap<>();
    private Map<K, Builder<K, F>> toRegister = new Object2ObjectOpenHashMap<>();

    private boolean registered = false;

    interface Key { int getRegistrationPriority(); }
    @FunctionalInterface interface Builder<K, F> {
        F build(String modid, MaterialState material, K key);
    }

    private final K liquidKey;
    private final java.util.function.Supplier<Builder<K, F>> defaultBuilder;
    private final java.util.function.Consumer<MaterialState> collision;

    FluidRegistration(K liquidKey, java.util.function.Supplier<Builder<K, F>> defaultBuilder,
                      java.util.function.Consumer<MaterialState> collision) {
        this.liquidKey = java.util.Objects.requireNonNull(liquidKey);
        this.defaultBuilder = java.util.Objects.requireNonNull(defaultBuilder);
        this.collision = java.util.Objects.requireNonNull(collision);
    }


    public void enqueueRegistration( K key,  Builder<K, F> builder) {
        if (registered) {
            throw new IllegalStateException("Cannot enqueue a builder after registration");
        }

        if (toRegister.containsKey(key)) {
            throw new IllegalArgumentException("FluidStorageKey " + key + " is already queued");
        }
        toRegister.put(key, builder);
    }


    public  Builder<K, F> getQueuedBuilder( K key) {
        if (registered) {
            throw new IllegalArgumentException("FluidStorageImpl has already been registered");
        }
        return toRegister.get(key);
    }

    /**
     * Register the enqueued fluids
     *
     * @param material the material the fluid is based off of
     */

    public void registerFluids( MaterialState material) {
        if (registered) {
            throw new IllegalStateException("FluidStorageImpl has already been registered");
        }

        // If nothing is queued for registration and nothing is manually stored,
        // we need something for the registry to handle this will prevent cases
        // of a material having a fluid property but no fluids actually created
        // for the material.
        if (toRegister.isEmpty() && map.isEmpty()) {
            enqueueRegistration(liquidKey, defaultBuilder.get());
        }

        toRegister.entrySet().stream()
                .sorted(Comparator.comparingInt(e -> -e.getKey().getRegistrationPriority()))
                .forEach(entry -> {
                    F fluid = entry.getValue().build(material.getModid(), material, entry.getKey());
                    if (!storeNoOverwrites(entry.getKey(), fluid)) {
                        collision.accept(material);
                    }
                });
        toRegister = null;
        registered = true;
    }


    public  F get( K key) {
        return map.get(key);
    }

    /**
     * Will do nothing if an existing fluid association would be overwritten.
     *
     * @param key   the key to associate with the fluid
     * @param fluid the fluid to associate with the key
     * @return if the associations were successfully updated
     */
    private boolean storeNoOverwrites( K key,  F fluid) {
        if (map.containsKey(key)) {
            return false;
        }
        store(key, fluid);
        return true;
    }


    public void store( K key,  F fluid) {
        map.put(key, fluid);
    }
}
