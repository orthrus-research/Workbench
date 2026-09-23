// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.
package research.orthrus.axiom;


import it.unimi.dsi.fastutil.ints.Int2ObjectMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.objects.Object2ObjectMap;
import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;

final class MaterialRegistryManager implements IMaterialRegistryManager {

    private final RegistryRuntime runtime;

    private final Object2ObjectMap<String, MaterialRegistryImpl> registries = new Object2ObjectOpenHashMap<>();
    private final Int2ObjectMap<MaterialRegistryImpl> networkIds = new Int2ObjectOpenHashMap<>();


    private Collection<MaterialState> registeredMaterials;

    private final MaterialRegistryImpl gregtechRegistry;

    private MaterialPhase registrationPhase = MaterialPhase.PRE;

    MaterialRegistryManager(RegistryRuntime runtime) {
        this.runtime = runtime;
        this.gregtechRegistry = createInternalRegistry();
    }




    @Override
    public MaterialRegistry createRegistry( String modid) {
        if (getPhase() != MaterialPhase.PRE) {
            throw new IllegalStateException("Cannot create registries in phase " + getPhase());
        }

        if (registries.containsKey(modid))
            throw new IllegalArgumentException(String.format("Material registry already exists for modid %s", modid));
        MaterialRegistryImpl registry = new MaterialRegistryImpl(runtime, this, modid);
        registries.put(modid, registry);
        networkIds.put(registry.getNetworkId(), registry);
        return registry;
    }


    @Override
    public MaterialRegistry getRegistry( String modid) {
        MaterialRegistry registry = registries.get(modid);
        return registry != null ? registry : gregtechRegistry;
    }


    @Override
    public MaterialRegistry getRegistry(int networkId) {
        MaterialRegistry registry = networkIds.get(networkId);
        return registry != null ? registry : gregtechRegistry;
    }


    @Override
    public Collection<MaterialRegistry> getRegistries() {
        if (getPhase() == MaterialPhase.PRE) {
            throw new IllegalStateException("Cannot get all material registries during phase " + getPhase());
        }
        return Collections.unmodifiableCollection(registries.values());
    }


    @Override
    public Collection<MaterialState> getRegisteredMaterials() {
        if (registeredMaterials == null ||
                (getPhase() != MaterialPhase.CLOSED && getPhase() != MaterialPhase.FROZEN)) {
            throw new IllegalStateException("Cannot retrieve all materials before registration");
        }
        return registeredMaterials;
    }


    @Override
    public MaterialState getMaterial( String name) {
        if (!name.isEmpty()) {
            String modid;
            String materialName;
            int index = name.indexOf(':');
            if (index >= 0) {
                modid = name.substring(0, index);
                materialName = name.substring(index + 1);
            } else {
                modid = "gregtech";
                materialName = name;
            }
            return getRegistry(modid).getObject(materialName);
        }
        return null;
    }


    @Override
    public MaterialPhase getPhase() {
        return registrationPhase;
    }

    public void unfreezeRegistries() {
        registries.values().forEach(MaterialRegistryImpl::unfreeze);
        registrationPhase = MaterialPhase.OPEN;
    }

    public void closeRegistries() {
        registries.values().forEach(MaterialRegistryImpl::closeRegistry);
        Collection<MaterialState> collection = new ArrayList<>();
        for (MaterialRegistry registry : registries.values()) {
            collection.addAll(registry.getAllMaterials());
        }
        registeredMaterials = Collections.unmodifiableCollection(collection);
        registrationPhase = MaterialPhase.CLOSED;
    }

    public void freezeRegistries() {
        registries.values().forEach(MaterialRegistryImpl::freeze);
        registrationPhase = MaterialPhase.FROZEN;
    }


    private MaterialRegistryImpl createInternalRegistry() {
        MaterialRegistryImpl registry = new MaterialRegistryImpl(runtime, this, "gregtech");
        this.registries.put("gregtech", registry);
        return registry;
    }


    public MaterialRegistry getDefaultRegistry() {
        return gregtechRegistry;
    }


    public MaterialState getDefaultFallback() {
        return gregtechRegistry.getFallbackMaterial();
    }
}
