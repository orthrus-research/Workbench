package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Assumptions;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.nio.file.Path;
import java.util.*;
import java.util.zip.ZipFile;
import static org.junit.jupiter.api.Assertions.*;

class NativeLoaderPrefixTest {
    private static final String OWNER = NativeLoaderPrefix.TARGET.replace('.', '/');

    private static byte[] artifact(String variable, String name) throws Exception {
        String path = System.getenv(variable);
        Assumptions.assumeTrue(path != null && !path.isBlank(), "Exact original artifact was not supplied: " + variable);
        try (var zip = new ZipFile(Path.of(path).toFile()); var input = zip.getInputStream(Objects.requireNonNull(zip.getEntry(name + ".class")))) {
            return input.readAllBytes();
        }
    }
    private static ClassNode read(byte[] bytes) {
        var node = new ClassNode(); new ClassReader(bytes).accept(node, ClassReader.EXPAND_FRAMES); return node;
    }
    private static MethodNode method(ClassNode node, String name) {
        return node.methods.stream().filter(m -> name.equals(m.name)).findFirst().orElseThrow();
    }
    private static byte[] methodBytes(ClassNode owner, MethodNode method) {
        var writer = new ClassWriter(0);
        writer.visit(owner.version, owner.access, owner.name, owner.signature, owner.superName, owner.interfaces.toArray(String[]::new));
        method.accept(writer); writer.visitEnd(); return writer.toByteArray();
    }

    @Test void preservesEveryOriginalMethodAndDefinesTheExtendedOwner() throws Exception {
        byte[] raw = artifact("AXIOM_EARLY_CLEANROOM_JAR", OWNER);
        byte[] changed = NativeLoaderPrefix.apply(raw);
        var before = read(raw); var after = read(changed);
        assertEquals(before.methods.size() + 2, after.methods.size());
        for (int i = 0; i < before.methods.size(); i++)
            assertArrayEquals(methodBytes(before, before.methods.get(i)), methodBytes(after, after.methods.get(i)),
                    "Original method changed: " + before.methods.get(i).name);
        var definitionLoader = new ClassLoader(ClassLoader.getPlatformClassLoader()) {
            Class<?> define(byte[] bytes) { return defineClass(NativeLoaderPrefix.TARGET, bytes, 0, bytes.length); }
        };
        assertEquals(NativeLoaderPrefix.TARGET, definitionLoader.define(changed).getName());
        // Definition only: this check does not initialize or select native mods.
    }

    @Test void retainsTheCompleteSelectionAndExceptionRegionsBeforeConstruction() throws Exception {
        var original = read(artifact("AXIOM_EARLY_CLEANROOM_JAR", OWNER));
        var source = method(original, "loadMods"); var prefix = NativeLoaderPrefix.extract(original);
        assertEquals(NativeLoaderPrefix.DESCRIPTOR, prefix.desc);
        assertEquals(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC, prefix.access);
        assertEquals(source.tryCatchBlocks.size(), prefix.tryCatchBlocks.size());
        assertEquals(source.tryCatchBlocks.stream().map(b -> b.type).toList(), prefix.tryCatchBlocks.stream().map(b -> b.type).toList());
        var code = prefix.instructions.toArray();
        assertEquals(Opcodes.RETURN, code[code.length - 1].getOpcode());
        for (int i = 0; i < code.length - 1; i++) {
            assertNotSame(source.instructions.get(i), code[i]);
            assertEquals(source.instructions.get(i).getOpcode(), code[i].getOpcode());
        }
        var calls = Arrays.stream(code).filter(i -> i instanceof MethodInsnNode).map(i -> (MethodInsnNode)i).toList();
        assertEquals("loadMixinBooterLateMixins", calls.getLast().name);
        assertTrue(calls.stream().anyMatch(c -> "identifyMods".equals(c.name)));
        assertTrue(calls.stream().anyMatch(c -> "sortModList".equals(c.name)));
        assertEquals(1, calls.stream().filter(c -> "distributeStateMessage".equals(c.name)).count());
        assertTrue(calls.stream().filter(c -> "distributeStateMessage".equals(c.name)).allMatch(c -> "(Ljava/lang/Class;)V".equals(c.desc)));
        assertFalse(Arrays.stream(code).anyMatch(i -> i instanceof FieldInsnNode f && "CONSTRUCTING".equals(f.name)
                && "net/minecraftforge/fml/common/LoaderState".equals(f.owner)));
    }

    @Test void changedNativeSelectionAndConstructionBoundariesRefuse() throws Exception {
        byte[] raw = artifact("AXIOM_EARLY_CLEANROOM_JAR", OWNER);
        for (String target : List.of("initializeLoader", "identifyMods", "manageAPI", "disableRequestedMods", "sortModList",
                "cleanupAPIContainers", "loadData", "loadMixinBooterLateMixins")) {
            var node = read(raw); var source = method(node, "loadMods");
            var call = Arrays.stream(source.instructions.toArray()).filter(i -> i instanceof MethodInsnNode m && target.equals(m.name))
                    .map(i -> (MethodInsnNode)i).findFirst().orElseThrow();
            call.name += "Changed";
            assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.extract(node), target);
        }
        var phase = read(raw);
        Arrays.stream(method(phase, "loadMods").instructions.toArray()).filter(i -> i instanceof FieldInsnNode f && "CONSTRUCTING".equals(f.name)
                        && "net/minecraftforge/fml/common/LoaderState".equals(f.owner))
                .map(i -> (FieldInsnNode)i).findFirst().orElseThrow().name = "PREINITIALIZATION";
        assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.extract(phase));
        var duplicate = read(raw); duplicate.methods.add(NativeLoaderPrefix.extract(duplicate));
        assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.extract(duplicate));
    }

    @Test void constructionCopyRetainsEveryOriginalInstructionLocalAndExceptionRegion() throws Exception {
        var node = read(artifact("AXIOM_EARLY_CLEANROOM_JAR", OWNER));
        var original = method(node, "loadMods");
        var construction = NativeLoaderPrefix.extractConstruction(node);
        assertEquals(NativeLoaderPrefix.CONSTRUCTION_METHOD, construction.name);
        var observations = Arrays.stream(construction.instructions.toArray())
                .filter(i -> i instanceof MethodInsnNode c && c.owner.endsWith("/NativeConstructionClassSpace")).toList();
        assertEquals(2, observations.size());
        observations.forEach(construction.instructions::remove);
        construction.name = original.name; construction.access = original.access;
        assertArrayEquals(methodBytes(node, original), methodBytes(node, construction));
        assertEquals(original.maxLocals, construction.maxLocals);
        assertEquals(original.maxStack, construction.maxStack);
    }

    @Test void constructionObservationsBracketOnlyTheOriginalDispatchBoundary() throws Exception {
        var node = read(artifact("AXIOM_EARLY_CLEANROOM_JAR", OWNER));
        var construction = NativeLoaderPrefix.extractConstruction(node);
        var calls = Arrays.stream(construction.instructions.toArray()).filter(i -> i instanceof MethodInsnNode)
                .map(i -> (MethodInsnNode)i).toList();
        var before = calls.stream().filter(c -> c.name.equals("beforeConstruction")).findFirst().orElseThrow();
        assertEquals("loadMixinBooterLateMixins", calls.get(calls.indexOf(before) - 1).name);
        assertEquals("()V", before.desc);
        var dispatch = calls.stream().filter(c -> c.name.equals("constructionDispatchStarted")).findFirst().orElseThrow();
        var original = assertInstanceOf(MethodInsnNode.class, dispatch.getNext());
        assertEquals("distributeStateMessage", original.name);
        assertEquals("(Lnet/minecraftforge/fml/common/LoaderState;[Ljava/lang/Object;)V", original.desc);
        assertEquals("transition", calls.getLast().name);
        assertTrue(Arrays.stream(construction.instructions.toArray()).anyMatch(i -> i instanceof FieldInsnNode f
                && f.name.equals("PREINITIALIZATION") && f.owner.equals("net/minecraftforge/fml/common/LoaderState")));
        assertFalse(calls.stream().anyMatch(c -> c.name.equals("preinitializeMods") || c.name.equals("initializeGroovyPreInit")));
    }

    @Test void originalClassIdentityIsRequiredBeforeExtraction() {
        assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.apply(null));
        assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.apply(new byte[0]));
    }

    @Test void ordinaryClassChangesAreRetainedWhileTheEntireSelectionMethodStaysBound() throws Exception {
        byte[] raw = artifact("AXIOM_EARLY_CLEANROOM_JAR", OWNER);
        assertEquals(NativeLoaderPrefix.ORIGINAL_METHOD_SHA256, NativeLoaderPrefix.methodDigest(raw));
        var node = read(raw);
        var field = node.fields.stream().filter(f -> "modController".equals(f.name)).findFirst().orElseThrow();
        field.access = (field.access & ~(Opcodes.ACC_PRIVATE | Opcodes.ACC_PROTECTED)) | Opcodes.ACC_PUBLIC;
        var writer = new ClassWriter(0); node.accept(writer);
        byte[] transformed = writer.toByteArray();
        assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.apply(transformed));
        var extended = read(NativeLoaderPrefix.applyAfterNativeTransforms(transformed));
        assertEquals(field.access, extended.fields.stream().filter(f -> "modController".equals(f.name)).findFirst().orElseThrow().access);
        assertArrayEquals(methodBytes(node, method(node, "loadMods")), methodBytes(extended, method(extended, "loadMods")));
        // Even a change outside the structural cut checks must not be admitted
        // as the exact original method; preserve its complete source meaning.
        var constant = Arrays.stream(method(node, "loadMods").instructions.toArray()).filter(i -> i instanceof LdcInsnNode c
                && "Constructing Mods".equals(c.cst)).map(i -> (LdcInsnNode)i).findFirst().orElseThrow();
        constant.cst = "Changed selection";
        var altered = new ClassWriter(0); node.accept(altered);
        assertThrows(IllegalArgumentException.class, () -> NativeLoaderPrefix.applyAfterNativeTransforms(altered.toByteArray()));
    }

    @Test void selectionInstallsItsStopImmediatelyAfterTheOriginalTransformerReset() throws Exception {
        byte[] raw = artifact("AXIOM_EARLY_FOUNDATION_JAR", NativeEarlyLaunchPrefix.TARGET.replace('.', '/'));
        var original = read(raw); var changed = read(NativeEarlyLaunchPrefix.applySelection(raw));
        assertArrayEquals(methodBytes(original, method(original, "launch")), methodBytes(changed, method(changed, "launch")));
        var prefix = method(changed, NativeEarlyLaunchPrefix.METHOD);
        var install = Arrays.stream(prefix.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && "installSelectionPrefix".equals(c.name)).toList();
        assertEquals(1, install.size());
        var reset = assertInstanceOf(MethodInsnNode.class, install.getFirst().getPrevious());
        assertEquals("top/outlands/foundation/TransformerDelegate", reset.owner);
        assertEquals("fillTransformerHolder", reset.name);
        var early = read(NativeEarlyLaunchPrefix.apply(raw));
        assertFalse(Arrays.stream(method(early, NativeEarlyLaunchPrefix.METHOD).instructions.toArray())
                .anyMatch(i -> i instanceof MethodInsnNode c && "installSelectionPrefix".equals(c.name)));
    }
}
