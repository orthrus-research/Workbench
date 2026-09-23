// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


import static research.orthrus.axiom.FluidSupport.gregtechId;

final class FluidAttributes {

    /**
     * Attribute for acidic fluids.
     */
    public final FluidAttribute ACID = new FluidAttribute(gregtechId("acid"),
            list -> list.add(ConstructionDependencies.localize("gregtech.fluid.type_acid.tooltip")),
            list -> list.add(ConstructionDependencies.localize("gregtech.fluid_pipe.acid_proof")));

    FluidAttributes() {}
}
