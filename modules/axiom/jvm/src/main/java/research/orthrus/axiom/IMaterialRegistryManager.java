// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.
package research.orthrus.axiom;



import java.util.Collection;

interface IMaterialRegistryManager {

    /**
     * Create a registry for a modid. Accessible when in phase {@link MaterialPhase#PRE}.
     *
     * @param modid the mod id for the registry
     * @return the registry for the mod
     */

    MaterialRegistry createRegistry( String modid);

    /**
     * Get a mod's registry. Accessible during all phases.
     *
     * @param modid the modid of the mod
     * @return the registry associated with the mod, or the GregTech registry if it does not have one
     */

    MaterialRegistry getRegistry( String modid);

    /**
     * Get a mod's registry. Accessible during all phases.
     *
     * @param networkId the network ID of the registry
     * @return the registry associated with the network ID, or the GregTech registry if it does not have one
     */

    MaterialRegistry getRegistry(int networkId);

    /**
     * Accessible when in phases:
     * <ul>
     * <li>{@link MaterialPhase#OPEN}</li>
     * <li>{@link MaterialPhase#CLOSED}</li>
     * <li>{@link MaterialPhase#FROZEN}</li>
     * </ul>
     *
     * @return all the MaterialState Registries
     */

    Collection<MaterialRegistry> getRegistries();

    /**
     * Accessible when in phases:
     * <ul>
     * <li>{@link MaterialPhase#CLOSED}</li>
     * <li>{@link MaterialPhase#FROZEN}</li>
     * </ul>
     *
     * @return all registered materials.
     */

    Collection<MaterialState> getRegisteredMaterials();

    /**
     * Get a material from a String in formats:
     * <ul>
     * <li>{@code "modid:registry_name"}</li>
     * <li>{@code "registry_name"} - where modid is inferred to be {@link GTValues#MODID}</li>
     * </ul>
     *
     * Intended for use in reading/writing materials from/to NBT tags.
     *
     * @param name the name of the material in the above format
     * @return the material associated with the name
     */
    MaterialState getMaterial(String name);

    /**
     * @return the current phase in the material registration process
     * @see MaterialPhase
     */

    MaterialPhase getPhase();

    default boolean canModifyMaterials() {
        return this.getPhase() != MaterialPhase.FROZEN && this.getPhase() != MaterialPhase.PRE;
    }

}
