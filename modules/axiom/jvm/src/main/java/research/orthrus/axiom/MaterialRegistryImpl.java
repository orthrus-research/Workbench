// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.
package research.orthrus.axiom;



import java.util.Collection;
import java.util.Collections;

class MaterialRegistryImpl extends MaterialRegistry {

    private final MaterialRegistryManager owner;

    private final int networkId;
    private final String modid;

    private boolean isRegistryClosed = false;
    private MaterialState fallbackMaterial = null;

    protected MaterialRegistryImpl(RegistryRuntime runtime, MaterialRegistryManager owner, String modid) {
        super(runtime);
        this.owner = owner;
        this.networkId = runtime.nextNetworkId();
        this.modid = modid;
    }

    @Override
    public void register(MaterialState material) {
        this.register(material.getId(), material.toString(), material);
    }

    @Override
    public void register(int id,  String key,  MaterialState value) {
        if (isRegistryClosed) {
            runtime.error(
                    "Materials cannot be registered in the PostMaterialEvent (or after)! Must be added in the MaterialEvent. Skipping material {}...",
                    key);
            return;
        }
        super.register(id, key, value);
    }


    @Override
    public Collection<MaterialState> getAllMaterials() {
        return Collections.unmodifiableCollection(this.registryObjects.values());
    }

    @Override
    public void setFallbackMaterial( MaterialState material) {
        this.fallbackMaterial = material;
    }


    @Override
    public MaterialState getFallbackMaterial() {
        if (this.fallbackMaterial == null) {
            this.fallbackMaterial = owner.getDefaultFallback();
        }
        return this.fallbackMaterial;
    }

    @Override
    public int getNetworkId() {
        return this.networkId;
    }


    @Override
    public String getModid() {
        return this.modid;
    }

    public void closeRegistry() {
        this.isRegistryClosed = true;
    }
}
