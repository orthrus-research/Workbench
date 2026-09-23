package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;
import java.util.zip.ZipFile;
import static org.junit.jupiter.api.Assertions.*;

class NativeServerOwnerPrefixTest {
    private static byte[] original() throws Exception {
        String path = System.getenv("AXIOM_EARLY_CLEANROOM_JAR");
        Assumptions.assumeTrue(path != null && !path.isBlank(), "Exact Cleanroom artifact is required");
        try (var zip = new ZipFile(path); var stream = zip.getInputStream(zip.getEntry(NativeServerOwnerPrefix.TARGET.replace('.', '/') + ".class"))) {
            return stream.readAllBytes();
        }
    }
    private static ClassNode read(byte[] bytes) {
        var node = new ClassNode(); new ClassReader(bytes).accept(node, ClassReader.EXPAND_FRAMES); return node;
    }
    private static byte[] write(ClassNode node) { var writer = new ClassWriter(0); node.accept(writer); return writer.toByteArray(); }
    private static byte[] method(MethodNode method) {
        var writer = new ClassWriter(0); writer.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC, "Witness", null, "java/lang/Object", null);
        method.accept(writer); writer.visitEnd(); return writer.toByteArray();
    }
    @Test void preservesOriginalMethodsAndStopsBeforeConfigurationAndLoader() throws Exception {
        byte[] original = original(); var before = read(original); var after = read(NativeServerOwnerPrefix.apply(original));
        assertEquals(before.methods.size() + 1, after.methods.size());
        for (int i = 0; i < before.methods.size(); i++) assertArrayEquals(method(before.methods.get(i)), method(after.methods.get(i)));
        var prefix = after.methods.getLast();
        assertEquals(NativeServerOwnerPrefix.METHOD, prefix.name);
        assertEquals(NativeServerOwnerPrefix.DESCRIPTOR, prefix.desc);
        assertEquals(List.of(Opcodes.ALOAD, Opcodes.ALOAD, Opcodes.PUTFIELD, Opcodes.RETURN),
                Arrays.stream(prefix.instructions.toArray()).map(AbstractInsnNode::getOpcode).toList());
        assertEquals("server", ((FieldInsnNode)prefix.instructions.get(2)).name);
        assertArrayEquals(NativeServerOwnerPrefix.apply(original), NativeServerOwnerPrefix.applyAfterNativeTransforms(original));
    }
    @Test void rejectsChangedOwnershipContinuationOwnerAndRepeatedInstallation() throws Exception {
        byte[] original = original();
        for (String mutation : List.of("assignment", "continuation", "owner", "descriptor")) {
            var node = read(original); var loading = node.methods.stream().filter(m -> m.name.equals("beginServerLoading")).findFirst().orElseThrow();
            switch (mutation) {
                case "assignment" -> Arrays.stream(loading.instructions.toArray()).filter(i -> i instanceof FieldInsnNode)
                        .map(i -> (FieldInsnNode)i).findFirst().orElseThrow().name = "other";
                case "continuation" -> Arrays.stream(loading.instructions.toArray()).filter(i -> i instanceof MethodInsnNode)
                        .map(i -> (MethodInsnNode)i).findFirst().orElseThrow().name = "differentConfig";
                case "owner" -> node.name += "Other";
                case "descriptor" -> loading.desc = "(Ljava/lang/Object;)V";
            }
            assertThrows(IllegalArgumentException.class, () -> NativeServerOwnerPrefix.applyAfterNativeTransforms(write(node)), mutation);
            assertThrows(IllegalArgumentException.class, () -> NativeServerOwnerPrefix.apply(write(node)), mutation);
        }
        assertThrows(IllegalArgumentException.class, () -> NativeServerOwnerPrefix.applyAfterNativeTransforms(NativeServerOwnerPrefix.apply(original)));
        assertThrows(IllegalArgumentException.class, () -> NativeServerOwnerPrefix.applyAfterNativeTransforms(null));
    }
}
