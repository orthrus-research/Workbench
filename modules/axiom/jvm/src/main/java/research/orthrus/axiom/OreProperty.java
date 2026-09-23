// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import org.apache.commons.lang3.tuple.Pair;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.List;

class OreProperty implements IMaterialProperty {

    /**
     * List of Ore byproducts.
     * <p>
     * Default: none, meaning only this property's FluidMaterial.
     */
    //
    private final List<FluidMaterial> oreByProducts = new ArrayList<>();

    /**
     * Crushed Ore output amount multiplier during Maceration.
     * <p>
     * Default: 1 (no multiplier).
     */
    //
    private int oreMultiplier;

    /**
     * Byproducts output amount multiplier during Maceration.
     * <p>
     * Default: 1 (no multiplier).
     */
    //
    private int byProductMultiplier;

    /**
     * Should ore block use the emissive texture.
     * <p>
     * Default: false.
     */
    //
    private boolean emissive;

    /**
     * FluidMaterial to which smelting of this Ore will result.
     * <p>
     * FluidMaterial will have a Dust Property.
     * Default: none.
     */
    //

    private FluidMaterial directSmeltResult;

    /**
     * FluidMaterial in which this Ore should be washed to give additional output.
     * <p>
     * FluidMaterial will have a NativeFluid Property.
     * Default: none.
     */
    //

    private FluidMaterial washedIn;

    /**
     * The amount of FluidMaterial that the ore should be washed in
     * in the Chemical Bath.
     * <p>
     * Default 100 mb
     */
    private int washedAmount = 100;

    /**
     * During Electromagnetic Separation, this Ore will be separated
     * into this FluidMaterial and the FluidMaterial specified by this field.
     * Limit 2 Materials
     * <p>
     * FluidMaterial will have a Dust Property.
     * Default: none.
     */
    //
    private final List<FluidMaterial> separatedInto = new ArrayList<>();

    public OreProperty(int oreMultiplier, int byProductMultiplier) {
        this.oreMultiplier = oreMultiplier;
        this.byProductMultiplier = byProductMultiplier;
        this.emissive = false;
    }

    public OreProperty(int oreMultiplier, int byProductMultiplier, boolean emissive) {
        this.oreMultiplier = oreMultiplier;
        this.byProductMultiplier = byProductMultiplier;
        this.emissive = emissive;
    }

    /**
     * Default values constructor.
     */
    public OreProperty() {
        this(1, 1);
    }

    public void setOreMultiplier(int multiplier) {
        this.oreMultiplier = multiplier;
    }

    public int getOreMultiplier() {
        return this.oreMultiplier;
    }

    public void setByProductMultiplier(int multiplier) {
        this.byProductMultiplier = multiplier;
    }

    public int getByProductMultiplier() {
        return this.byProductMultiplier;
    }

    public boolean isEmissive() {
        return emissive;
    }

    public void setEmissive(boolean emissive) {
        this.emissive = emissive;
    }

    public void setDirectSmeltResult( FluidMaterial m) {
        this.directSmeltResult = m;
    }


    public FluidMaterial getDirectSmeltResult() {
        return this.directSmeltResult;
    }

    public void setWashedIn( FluidMaterial m) {
        this.washedIn = m;
    }

    public void setWashedIn( FluidMaterial m, int washedAmount) {
        this.washedIn = m;
        this.washedAmount = washedAmount;
    }

    public Pair<FluidMaterial, Integer> getWashedIn() {
        return Pair.of(this.washedIn, this.washedAmount);
    }

    public void setSeparatedInto(FluidMaterial... materials) {
        this.separatedInto.addAll(Arrays.asList(materials));
    }


    public List<FluidMaterial> getSeparatedInto() {
        return this.separatedInto;
    }

    /**
     * Set the ore byproducts for this property
     *
     * @param materials the materials to use as byproducts
     */
    public void setOreByProducts( FluidMaterial... materials) {
        setOreByProducts(Arrays.asList(materials));
    }

    /**
     * Set the ore byproducts for this property
     *
     * @param materials the materials to use as byproducts
     */
    public void setOreByProducts( Collection<FluidMaterial> materials) {
        this.oreByProducts.clear();
        this.oreByProducts.addAll(materials);
    }

    /**
     * Add ore byproducts to this property
     *
     * @param materials the materials to add as byproducts
     */
    public void addOreByProducts( FluidMaterial... materials) {
        this.oreByProducts.addAll(Arrays.asList(materials));
    }

    public List<FluidMaterial> getOreByProducts() {
        return this.oreByProducts;
    }


    public final FluidMaterial getOreByProduct(int index) {
        if (this.oreByProducts.isEmpty()) return null;
        return this.oreByProducts.get(ConstructionDependencies.clamp(index, 0, this.oreByProducts.size() - 1));
    }


    public final FluidMaterial getOreByProduct(int index,  FluidMaterial fallback) {
        FluidMaterial material = getOreByProduct(index);
        return material != null ? material : fallback;
    }

    @Override
    public void verifyProperty(MaterialProperties properties) {
        properties.ensureSet(PropertyKey.DUST, true);

        if (directSmeltResult != null) directSmeltResult.getProperties().ensureSet(PropertyKey.DUST, true);
        if (washedIn != null) washedIn.getProperties().ensureSet(FluidDomain.FLUID, true);
        separatedInto.forEach(m -> m.getProperties().ensureSet(PropertyKey.DUST, true));
        oreByProducts.forEach(m -> m.getProperties().ensureSet(PropertyKey.DUST, true));
    }
}
