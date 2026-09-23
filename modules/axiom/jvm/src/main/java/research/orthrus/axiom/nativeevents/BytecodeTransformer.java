package research.orthrus.axiom.nativeevents;

/** Native bytecode transformation port; this does not start LaunchWrapper. */
public interface BytecodeTransformer {
    byte[] transform(String name, String transformedName, byte[] bytes);
}
