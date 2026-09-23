// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.
package research.orthrus.axiom;



import java.util.Collection;

public abstract class MaterialRegistry extends GTControlledRegistry<String, MaterialState> {

    public MaterialRegistry(RegistryRuntime runtime) {
        super(runtime, Short.MAX_VALUE);
    }

    public abstract void register(MaterialState material);


    public abstract Collection<MaterialState> getAllMaterials();

    /**
     * Set the fallback material for this registry.
     * Using {@link #getObjectById(int)} or related will still return {@code null} when an entry cannot be found.
     * This is only for manual fallback usage.
     *
     * @param material the fallback material
     */
    public abstract void setFallbackMaterial( MaterialState material);

    /**
     * Using {@link #getObjectById(int)} or related will still return {@code null} when an entry cannot be found.
     * This is only for manual fallback usage.
     *
     * @return the fallback material, used for when another material does not exist
     */

    public abstract MaterialState getFallbackMaterial();

    /**
     * @return the network ID for this registry
     */
    public abstract int getNetworkId();


    public abstract String getModid();
}
