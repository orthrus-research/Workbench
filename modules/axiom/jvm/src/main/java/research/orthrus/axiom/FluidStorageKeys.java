// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import static research.orthrus.axiom.FluidSupport.gregtechId;

final class FluidStorageKeys {

    public final FluidStorageKey LIQUID = new FluidStorageKey(gregtechId("liquid"),
            MaterialIconType.liquid,
            m -> prefixedRegistryName("liquid.", FluidEnvironment.current().storageKeys().LIQUID, m),
            m -> m.hasProperty(PropertyKey.DUST) ? "gregtech.fluid.liquid_generic" : "gregtech.fluid.generic",
            FluidState.LIQUID);

    public final FluidStorageKey GAS = new FluidStorageKey(gregtechId("gas"),
            MaterialIconType.gas,
            m -> prefixedRegistryName("gas.", FluidEnvironment.current().storageKeys().GAS, m),
            m -> {
                if (m.hasProperty(PropertyKey.DUST)) {
                    return "gregtech.fluid.gas_vapor";
                }

                FluidProperty property = m.getProperty(FluidDomain.FLUID);
                if (m.isElement() || (property != null && property.getPrimaryKey() != FluidEnvironment.current().storageKeys().LIQUID)) {
                    return "gregtech.fluid.gas_generic";
                }
                return "gregtech.fluid.generic";
            },
            FluidState.GAS);

    public final FluidStorageKey PLASMA = new FluidStorageKey(gregtechId("plasma"),
            MaterialIconType.plasma,
            m -> "plasma." + m.getName(),
            m -> "gregtech.fluid.plasma",
            FluidState.PLASMA, -1);

    FluidStorageKeys() {}

    /**
     * @param prefix   the prefix string for the registry name
     * @param key      the key which does not require the prefix
     * @param material the material to create a registry name for
     * @return the registry name
     */
    private static  String prefixedRegistryName( String prefix,  FluidStorageKey key,
                                                         FluidMaterial material) {
        FluidProperty property = material.getProperty(FluidDomain.FLUID);
        if (property != null && property.getPrimaryKey() != key) {
            return prefix + material.getName();
        }
        return material.getName();
    }
}
