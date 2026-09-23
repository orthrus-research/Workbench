// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


class WoodProperty implements IMaterialProperty {

    @Override
    public void verifyProperty(MaterialProperties properties) {
        properties.ensureSet(PropertyKey.DUST);
        FluidMaterial.require(properties.getMaterial()).addFlags(MaterialFlags.FLAMMABLE);

        if (properties.hasProperty(PropertyKey.FLUID_PIPE)) {
            PrefixDependencies.requireInputs();
            OrePrefix.pipeTinyFluid.setIgnored(FluidMaterial.require(properties.getMaterial()));
            OrePrefix.pipeHugeFluid.setIgnored(FluidMaterial.require(properties.getMaterial()));
            OrePrefix.pipeQuadrupleFluid.setIgnored(FluidMaterial.require(properties.getMaterial()));
            OrePrefix.pipeNonupleFluid.setIgnored(FluidMaterial.require(properties.getMaterial()));
        }
    }
}
