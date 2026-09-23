package research.orthrus.axiom;

/** Additional native property catalog, activated only by the explicit fluid domain. */
final class FluidDomain {
    static final PropertyKey<FluidProperty> FLUID = PropertyKey.FLUID;
    static final PropertyKey<BlastProperty> BLAST = PropertyKey.BLAST;
    static void activate() { MaterialProperties.addBaseType(FLUID); }
}
