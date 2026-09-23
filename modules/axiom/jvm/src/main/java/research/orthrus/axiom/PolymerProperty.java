// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


class PolymerProperty implements IMaterialProperty {

    @Override
    public void verifyProperty(MaterialProperties properties) {
        properties.ensureSet(PropertyKey.DUST, true);
        properties.ensureSet(PropertyKey.INGOT, true);

        FluidMaterial.require(properties.getMaterial()).addFlags(MaterialFlags.FLAMMABLE, MaterialFlags.NO_SMASHING,
                MaterialFlags.DISABLE_DECOMPOSITION);
    }
}
