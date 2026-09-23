// Extracted from pinned GTCEu; LGPL-3.0. See sources/NOTICE.md and spec/material-properties.md.
package research.orthrus.axiom;



class IngotProperty implements IMaterialProperty {

    /**
     * Specifies a material into which this material parts turn when heated
     */
    private MaterialState smeltInto;

    /**
     * Specifies a material into which this material parts turn when heated in arc furnace
     */
    private MaterialState arcSmeltInto;

    /**
     * Specifies a MaterialState into which this MaterialState Macerates into.
     * <p>
     * Default: this MaterialState.
     */
    private MaterialState macerateInto;

    /**
     * MaterialState which obtained when this material is polarized
     */
    
    private MaterialState magneticMaterial;

    public void setSmeltingInto(MaterialState smeltInto) {
        this.smeltInto = smeltInto;
    }

    public MaterialState getSmeltingInto() {
        return this.smeltInto;
    }

    public void setArcSmeltingInto(MaterialState arcSmeltingInto) {
        this.arcSmeltInto = arcSmeltingInto;
    }

    public MaterialState getArcSmeltInto() {
        return this.arcSmeltInto;
    }

    public void setMagneticMaterial( MaterialState magneticMaterial) {
        this.magneticMaterial = magneticMaterial;
    }

    
    public MaterialState getMagneticMaterial() {
        return magneticMaterial;
    }

    public void setMacerateInto(MaterialState macerateInto) {
        this.macerateInto = macerateInto;
    }

    public MaterialState getMacerateInto() {
        return macerateInto;
    }

    @Override
    public void verifyProperty(MaterialProperties properties) {
        properties.ensureSet(PropertyKey.DUST, true);
        if (properties.hasProperty(PropertyKey.GEM)) {
            throw new IllegalStateException(
                    "Material " + properties.getMaterial() +
                            " has both Ingot and Gem Property, which is not allowed!");
        }

        if (smeltInto == null) smeltInto = properties.getMaterial();
        else smeltInto.getProperties().ensureSet(PropertyKey.INGOT, true);

        if (arcSmeltInto == null) arcSmeltInto = properties.getMaterial();
        else arcSmeltInto.getProperties().ensureSet(PropertyKey.INGOT, true);

        if (macerateInto == null) macerateInto = properties.getMaterial();
        else macerateInto.getProperties().ensureSet(PropertyKey.INGOT, true);

        if (magneticMaterial != null) magneticMaterial.getProperties().ensureSet(PropertyKey.INGOT, true);
    }
}
