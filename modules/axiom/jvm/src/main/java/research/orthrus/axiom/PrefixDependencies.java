package research.orthrus.axiom;

/** Required, live source-catalog/config access. No inferred materials or config defaults. */
final class PrefixDependencies {
    interface Inputs {
        // The original Materials static field value, including null if still unset.
        FluidMaterial material(String field);
        boolean generateLowQualityGems();
        boolean allUniqueStoneTypes();
    }
    static Inputs requireInputs() { return FluidEnvironment.current().prefixInputs(); }
    static FluidMaterial material(String field) { return requireInputs().material(field); }
    static boolean generateLowQualityGems() { return requireInputs().generateLowQualityGems(); }
    static boolean allUniqueStoneTypes() { return requireInputs().allUniqueStoneTypes(); }
    private PrefixDependencies() {}
}
