// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.
package research.orthrus.axiom;




class GTControlledRegistry<K, V> extends NativeNamedRegistry<K, V> {

    protected boolean frozen = true;
    protected final int maxId;

    public GTControlledRegistry(RegistryRuntime runtime, int maxId) {
        super(runtime);
        this.maxId = maxId;
    }

    public boolean isFrozen() {
        return frozen;
    }

    public void freeze() {
        if (frozen) {
            throw new IllegalStateException("Registry is already frozen!");
        }

        if (!checkActiveModContainerIsGregtech()) {
            return;
        }

        this.frozen = true;
    }

    public void unfreeze() {
        if (!frozen) {
            throw new IllegalStateException("Registry is already unfrozen!");
        }

        if (!checkActiveModContainerIsGregtech()) {
            return;
        }

        this.frozen = false;
    }

    private boolean checkActiveModContainerIsGregtech() {
        RegistryRuntime.ActiveMod container = runtime.activeModContainer();
        return container != null && container.getModId().equals("gregtech");
    }

    public void register(int id,  K key,  V value) {
        if (id < 0 || id >= maxId) {
            throw new IndexOutOfBoundsException("Id is out of range: " + id);
        }

        super.putObject(key, value);

        V objectWithId = getObjectById(id);
        if (objectWithId != null) {
            throw new IllegalArgumentException(
                    String.format("Tried to reassign id %d to %s (%s), but it is already assigned to %s (%s)!",
                            id, value, key, objectWithId, getNameForObject(objectWithId)));
        }
        underlyingIntegerMap.put(value, id);
    }

    @Override
    public void putObject( K key,  V value) {
        throw new UnsupportedOperationException("Use #register(int, String, T)");
    }

    public int getIdByObjectName(K key) {
        V valueWithKey = getObject(key);
        return valueWithKey == null ? 0 : getIDForObject(valueWithKey);
    }
}
