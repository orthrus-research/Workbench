package research.orthrus.axiom;

/** Explicit unresolved operations, never successful empty registries or game stubs. */
final class ConstructionDependencies {
    private ConstructionDependencies() {}
    static int clamp(int value, int minimum, int maximum) {
        // Original selected Minecraft utility bytecode, no duplicate arithmetic.
        return (int) FluidEnvironment.current().runtime().utility("rk", "a",
                new Class<?>[]{int.class, int.class, int.class}, null, value, minimum, maximum);
    }
    static String localize(String key) {
        throw Failure.unsupported("material.presentation", "Client localization is not installed: " + key);
    }
}
