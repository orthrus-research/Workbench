package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeRootCoremodPrefixTest {
    private static final String OWNER = NativeCoremodPrefix.TARGET.replace('.', '/');
    private static final String MESSAGE = "All fundamental core mods are successfully located";

    private static ClassNode fixture() {
        var node = NativeCoremodPrefixTest.fixture(); var method = node.methods.getFirst();
        for (int i = 0; i < 3; i++) method.instructions.remove(method.instructions.getLast());
        method.visitFieldInsn(Opcodes.GETSTATIC, OWNER, "rootPlugins", "[Ljava/lang/String;");
        method.visitVarInsn(Opcodes.ASTORE, 3); method.visitInsn(Opcodes.ICONST_0); method.visitVarInsn(Opcodes.ISTORE, 4);
        Label loop = new Label(), done = new Label(), populated = new Label();
        method.visitLabel(loop); method.visitVarInsn(Opcodes.ILOAD, 4); method.visitVarInsn(Opcodes.ALOAD, 3);
        method.visitInsn(Opcodes.ARRAYLENGTH); method.visitJumpInsn(Opcodes.IF_ICMPGE, done);
        method.visitVarInsn(Opcodes.ALOAD, 1); method.visitVarInsn(Opcodes.ALOAD, 3); method.visitVarInsn(Opcodes.ILOAD, 4);
        method.visitInsn(Opcodes.AALOAD); method.visitTypeInsn(Opcodes.NEW, "java/io/File"); method.visitInsn(Opcodes.DUP);
        method.visitMethodInsn(Opcodes.INVOKESTATIC, "net/minecraftforge/fml/common/launcher/FMLTweaker", "getJarLocation", "()Ljava/net/URI;", false);
        method.visitMethodInsn(Opcodes.INVOKESPECIAL, "java/io/File", "<init>", "(Ljava/net/URI;)V", false);
        method.visitMethodInsn(Opcodes.INVOKESTATIC, OWNER, "loadCoreMod",
                "(Lnet/minecraft/launchwrapper/LaunchClassLoader;Ljava/lang/String;Ljava/io/File;)Lnet/minecraftforge/fml/relauncher/CoreModManager$FMLPluginWrapper;", false);
        method.visitInsn(Opcodes.POP); method.visitIincInsn(4, 1); method.visitJumpInsn(Opcodes.GOTO, loop);
        method.visitLabel(done); method.visitFieldInsn(Opcodes.GETSTATIC, OWNER, "loadPlugins", "Ljava/util/List;");
        method.visitMethodInsn(Opcodes.INVOKEINTERFACE, "java/util/List", "isEmpty", "()Z", true);
        method.visitJumpInsn(Opcodes.IFEQ, populated); method.visitTypeInsn(Opcodes.NEW, "java/lang/RuntimeException");
        method.visitInsn(Opcodes.DUP); method.visitLdcInsn("native corrupt installation");
        method.visitMethodInsn(Opcodes.INVOKESPECIAL, "java/lang/RuntimeException", "<init>", "(Ljava/lang/String;)V", false);
        method.visitInsn(Opcodes.ATHROW); method.visitLabel(populated);
        method.visitFieldInsn(Opcodes.GETSTATIC, "net/minecraftforge/fml/common/FMLLog", "log", "Lorg/apache/logging/log4j/Logger;");
        method.visitLdcInsn(MESSAGE);
        method.visitMethodInsn(Opcodes.INVOKEINTERFACE, "org/apache/logging/log4j/Logger", "debug", "(Ljava/lang/String;)V", true);
        method.visitMethodInsn(Opcodes.INVOKESTATIC, "com/cleanroommc/loader/CleanroomModDiscoverer", "instance", "()Ljava/lang/Object;", false);
        method.visitInsn(Opcodes.POP); method.visitInsn(Opcodes.RETURN); method.maxStack = 6; method.maxLocals = 5;
        return node;
    }

    @Test void rootSeamPreservesLoopAndNativeCorruptionCheckWithoutDiscoveryOrOriginalMutation() {
        var node = fixture(); var original = node.methods.getFirst(); var before = original.instructions.toArray();
        var extracted = NativeCoremodPrefix.extractRoots(node); var instructions = extracted.instructions.toArray();
        assertArrayEquals(before, original.instructions.toArray()); assertEquals(1, node.methods.size());
        assertEquals(NativeCoremodPrefix.ROOT_METHOD, extracted.name);
        assertEquals(3, Arrays.stream(instructions).filter(value -> value instanceof JumpInsnNode).count());
        assertEquals(1, Arrays.stream(instructions).filter(value -> value.getOpcode() == Opcodes.ATHROW).count());
        assertFalse(Arrays.stream(instructions).anyMatch(value -> value instanceof MethodInsnNode call && call.owner.contains("CleanroomModDiscoverer")));
        assertEquals(Opcodes.RETURN, instructions[instructions.length - 1].getOpcode());
        assertEquals("debug", ((MethodInsnNode)instructions[instructions.length - 2]).name);
    }

    @Test void rootBoundaryMustBeUniqueAndFollowedByOriginalLogCall() {
        var duplicate = fixture(); duplicate.methods.getFirst().instructions.add(new LdcInsnNode(MESSAGE));
        duplicate.methods.getFirst().instructions.add(new MethodInsnNode(Opcodes.INVOKEINTERFACE,
                "org/apache/logging/log4j/Logger", "debug", "(Ljava/lang/String;)V", true));
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extractRoots(duplicate));
        var changed = fixture();
        for (var instruction : changed.methods.getFirst().instructions)
            if (instruction instanceof MethodInsnNode call && "debug".equals(call.name)) call.name = "changed";
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extractRoots(changed));
    }

    @Test void rootRegistrationLocationAndCorruptionCheckCannotBeOmitted() {
        for (String name : List.of("getJarLocation", "loadCoreMod", "isEmpty")) {
            var node = fixture();
            for (var instruction : node.methods.getFirst().instructions)
                if (instruction instanceof MethodInsnNode call && name.equals(call.name)) call.name = "replacement";
            assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extractRoots(node), name);
        }
    }

    @Test void rootSeamCannotLeaveItsRegionOrRunDiscovery() {
        var branch = fixture(); var end = new LabelNode();
        var instructions = branch.methods.getFirst().instructions;
        instructions.add(end);
        for (var instruction : instructions) if (instruction instanceof JumpInsnNode jump && jump.getOpcode() == Opcodes.GOTO) {
            jump.label = end; break;
        }
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extractRoots(branch));
        var discovery = fixture();
        for (var instruction : discovery.methods.getFirst().instructions) if (instruction instanceof LdcInsnNode constant && MESSAGE.equals(constant.cst)) {
            discovery.methods.getFirst().instructions.insertBefore(instruction,
                    new MethodInsnNode(Opcodes.INVOKESTATIC, "com/cleanroommc/loader/CleanroomModDiscoverer", "instance", "()Ljava/lang/Object;", false));
            break;
        }
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extractRoots(discovery));
    }

    @Test void existingRootOrEnvironmentSeamRejectsReextraction() {
        for (String name : List.of(NativeCoremodPrefix.METHOD, NativeCoremodPrefix.ROOT_METHOD)) {
            var node = fixture(); node.methods.add(new MethodNode(Opcodes.ACC_STATIC, name, NativeCoremodPrefix.DESCRIPTOR, null, null));
            assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extractRoots(node));
        }
    }

    @Test void runtimeRequiresTheExactOriginalArtifactBytes() {
        var writer = new ClassWriter(0); fixture().accept(writer);
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.applyRoots(writer.toByteArray()));
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.applyRoots(null));
    }
}
