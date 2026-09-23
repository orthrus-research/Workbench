package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeCoremodPrefixTest {
    private static final String OWNER = NativeCoremodPrefix.TARGET.replace('.', '/');

    static ClassNode fixture() {
        var node = new ClassNode(); node.version = Opcodes.V25; node.name = OWNER;
        node.superName = "java/lang/Object";
        var method = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC,
                "handleLaunch", NativeCoremodPrefix.DESCRIPTOR, null, null);
        method.visitVarInsn(Opcodes.ALOAD, 0);
        method.visitFieldInsn(Opcodes.PUTSTATIC, OWNER, "mcDir", "Ljava/io/File;");
        method.visitVarInsn(Opcodes.ALOAD, 2);
        method.visitFieldInsn(Opcodes.PUTSTATIC, OWNER, "tweaker", "Lnet/minecraftforge/fml/common/launcher/FMLTweaker;");
        method.visitMethodInsn(Opcodes.INVOKESTATIC, "com/cleanroommc/common/CleanroomEnvironment", "isDev", "()Z", false);
        method.visitFieldInsn(Opcodes.PUTSTATIC, OWNER, "deobfuscatedEnvironment", "Z");
        for (String name : List.of("net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker", "org.spongepowered.asm.launch.MixinTweaker")) {
            method.visitVarInsn(Opcodes.ALOAD, 2); method.visitLdcInsn(name);
            method.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "net/minecraftforge/fml/common/launcher/FMLTweaker", "injectCascadingTweak", "(Ljava/lang/String;)V", false);
        }
        method.visitVarInsn(Opcodes.ALOAD, 1);
        method.visitLdcInsn("net.minecraftforge.fml.common.asm.transformers.PatchingTransformer");
        method.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "net/minecraft/launchwrapper/LaunchClassLoader", "registerTransformer", "(Ljava/lang/String;)V", false);
        method.visitTypeInsn(Opcodes.NEW, "java/util/ArrayList"); method.visitInsn(Opcodes.DUP);
        method.visitMethodInsn(Opcodes.INVOKESPECIAL, "java/util/ArrayList", "<init>", "()V", false);
        method.visitFieldInsn(Opcodes.PUTSTATIC, OWNER, "loadPlugins", "Ljava/util/List;");
        method.visitFieldInsn(Opcodes.GETSTATIC, OWNER, "rootPlugins", "[Ljava/lang/String;");
        method.visitInsn(Opcodes.POP); method.visitInsn(Opcodes.RETURN); method.visitMaxs(2, 3);
        node.methods.add(method); return node;
    }

    @Test void extractionRetainsEveryPrefixInstructionWithoutChangingOriginalMethod() {
        var node = fixture(); var original = node.methods.getFirst();
        var before = original.instructions.toArray();
        var extracted = NativeCoremodPrefix.extract(node);
        assertEquals(1, node.methods.size()); assertArrayEquals(before, original.instructions.toArray());
        assertEquals(NativeCoremodPrefix.METHOD, extracted.name);
        var copy = extracted.instructions.toArray();
        assertEquals(before.length - 2, copy.length);
        for (int i = 0; i < copy.length - 1; i++) {
            assertNotSame(before[i], copy[i]); assertEquals(before[i].getOpcode(), copy[i].getOpcode());
        }
        var last = assertInstanceOf(FieldInsnNode.class, copy[copy.length - 2]);
        assertEquals("loadPlugins", last.name); assertEquals(Opcodes.RETURN, copy[copy.length - 1].getOpcode());
        assertFalse(Arrays.stream(copy).anyMatch(i -> i instanceof FieldInsnNode f && "rootPlugins".equals(f.name)));
    }

    @Test void runtimeRejectsEvenStructurallySimilarButUnboundClassBytes() {
        var writer = new ClassWriter(0); fixture().accept(writer);
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.apply(writer.toByteArray()));
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.apply(null));
    }

    @Test void duplicateOrMissingBoundaryIsRejected() {
        var duplicate = fixture();
        duplicate.methods.getFirst().instructions.add(new FieldInsnNode(Opcodes.PUTSTATIC, OWNER, "loadPlugins", "Ljava/util/List;"));
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(duplicate));
        var missing = fixture();
        for (var instruction : missing.methods.getFirst().instructions.toArray())
            if (instruction instanceof FieldInsnNode f && "loadPlugins".equals(f.name)) missing.methods.getFirst().instructions.remove(f);
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(missing));
    }

    @Test void outgoingControlFlowOrExceptionRegionsAreRejected() {
        var outgoing = fixture(); var after = new LabelNode();
        outgoing.methods.getFirst().instructions.insert(new JumpInsnNode(Opcodes.GOTO, after));
        outgoing.methods.getFirst().instructions.add(after);
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(outgoing));
        var crossing = fixture(); var start = new LabelNode(); var end = new LabelNode(); var handler = new LabelNode();
        crossing.methods.getFirst().instructions.insert(start);
        crossing.methods.getFirst().instructions.add(end); crossing.methods.getFirst().instructions.add(handler);
        crossing.methods.getFirst().tryCatchBlocks.add(new TryCatchBlockNode(start, end, handler, "java/lang/Exception"));
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(crossing));
    }

    @Test void containedExceptionRegionsArePreservedWithIndependentLabels() {
        var node = fixture(); var start = new LabelNode(); var end = new LabelNode(); var handler = new LabelNode();
        var body = node.methods.getFirst().instructions;
        body.insert(start); body.insert(start, end); body.insert(end, handler);
        node.methods.getFirst().tryCatchBlocks.add(new TryCatchBlockNode(start, end, handler, "java/lang/Exception"));
        var extracted = NativeCoremodPrefix.extract(node);
        assertEquals(1, extracted.tryCatchBlocks.size());
        var copy = extracted.tryCatchBlocks.getFirst();
        assertNotSame(start, copy.start); assertNotSame(end, copy.end); assertNotSame(handler, copy.handler);
        assertEquals("java/lang/Exception", copy.type);
        assertTrue(Arrays.asList(extracted.instructions.toArray()).containsAll(List.of(copy.start, copy.end, copy.handler)));
    }

    @Test void changedOwnerDescriptorAndInitializationSequenceAreRejected() {
        var owner = fixture(); owner.name = "fixture/Replacement";
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(owner));
        var descriptor = fixture(); descriptor.methods.getFirst().desc = "()V";
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(descriptor));
        var shape = fixture();
        for (var instruction : shape.methods.getFirst().instructions)
            if (instruction instanceof MethodInsnNode call && "isDev".equals(call.name)) call.name = "favorableConstant";
        assertThrows(IllegalArgumentException.class, () -> NativeCoremodPrefix.extract(shape));
    }
}
