package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Assumptions;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.nio.file.Path;
import java.util.*;
import java.util.zip.ZipFile;
import static org.junit.jupiter.api.Assertions.*;

class NativeGroovyInitializationPrefixTest {
    private byte[] original(String variable, String member) throws Exception {
        String path = System.getenv(variable);
        Assumptions.assumeTrue(path != null && !path.isBlank(), "Exact original artifact required: " + variable);
        try (var zip = new ZipFile(Path.of(path).toFile()); var input = zip.getInputStream(zip.getEntry(member + ".class"))) {
            return input.readAllBytes();
        }
    }
    private ClassNode read(byte[] bytes) { var node = new ClassNode(); new ClassReader(bytes).accept(node, ClassReader.EXPAND_FRAMES); return node; }
    private byte[] write(ClassNode node) { var writer = new ClassWriter(0); node.accept(writer); return writer.toByteArray(); }
    private MethodNode method(ClassNode node, String name) { return node.methods.stream().filter(m -> m.name.equals(name)).findFirst().orElseThrow(); }
    private String digest(ClassNode node, MethodNode method) { return NativeGroovyInitializationPrefix.methodDigest(method, node.name); }
    private void originalsPreserved(ClassNode before, ClassNode after) {
        assertEquals(before.methods.size() + 1, after.methods.size());
        for (int i = 0; i < before.methods.size(); i++) assertEquals(digest(before, before.methods.get(i)), digest(after, after.methods.get(i)));
    }
    @Test void loaderPreservesOriginalPrerequisitesArgumentsAndEarlyFailureReturn() throws Exception {
        byte[] input = original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.LOADER);
        var before = read(input); var after = read(NativeGroovyInitializationPrefix.applyLoader(input));
        originalsPreserved(before, after);
        var prefix = method(after, NativeGroovyInitializationPrefix.METHOD);
        var source = method(before, "preinitializeMods");
        assertEquals(NativeGroovyInitializationPrefix.PREINIT_SHA256, digest(before, source));
        var calls = Arrays.stream(prefix.instructions.toArray()).filter(i -> i instanceof MethodInsnNode).map(i -> (MethodInsnNode)i).toList();
        assertEquals(List.of("isInState", "warn", "beginPhase", "step", "fireCreateRegistryEvents", "getASMTable", "findObjectHolders",
                "getASMTable", "findHolders", "getASMTable", "injectCapabilities", "getASMTable", NativeGroovyInitializationPrefix.METHOD),
                calls.stream().map(c -> c.name).toList());
        assertEquals(NativeGroovyInitializationPrefix.CONTROLLER, calls.getLast().owner);
        assertEquals(NativeGroovyInitializationPrefix.DISPATCH, calls.getLast().desc);
        assertEquals(2, Arrays.stream(prefix.instructions.toArray()).filter(i -> i.getOpcode() == Opcodes.RETURN).count());
        assertEquals(source.maxLocals, prefix.maxLocals);
        assertEquals(source.maxStack, prefix.maxStack);
        assertFalse(calls.stream().anyMatch(c -> Set.of("fireRegistryEvents", "transition", "inject").contains(c.name)));
    }
    @Test void nativePinDiffersOnlyByOriginalResourceLocationLambdaDeobfuscation() throws Exception {
        var node = read(original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.LOADER));
        var source = method(node, "preinitializeMods");
        var dynamic = Arrays.stream(source.instructions.toArray()).filter(i -> i instanceof InvokeDynamicInsnNode)
                .map(i -> (InvokeDynamicInsnNode)i).findFirst().orElseThrow();
        var handle = assertInstanceOf(Handle.class, dynamic.bsmArgs[1]);
        assertEquals("(Lnf;)Z", handle.getDesc());
        dynamic.bsmArgs[1] = new Handle(handle.getTag(), handle.getOwner(), handle.getName(),
                "(Lnet/minecraft/util/ResourceLocation;)Z", handle.isInterface());
        assertEquals(Type.getMethodType("(Lnf;)Z"), dynamic.bsmArgs[2]);
        dynamic.bsmArgs[2] = Type.getMethodType("(Lnet/minecraft/util/ResourceLocation;)Z");
        assertEquals(NativeGroovyInitializationPrefix.NATIVE_PREINIT_SHA256, digest(node, source));
        originalsPreserved(node, read(NativeGroovyInitializationPrefix.applyLoaderAfterNativeTransforms(write(node))));
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyLoader(write(node)));
    }
    @Test void changedPrerequisiteAndRepeatedExtractionRefuse() throws Exception {
        byte[] input = original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.LOADER);
        var node = read(input);
        var call = Arrays.stream(method(node, "preinitializeMods").instructions.toArray())
                .filter(i -> i instanceof MethodInsnNode c && c.name.equals("injectCapabilities")).map(i -> (MethodInsnNode)i).findFirst().orElseThrow();
        call.name = "omittedCapabilities";
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyLoader(write(node)));
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyLoader(NativeGroovyInitializationPrefix.applyLoader(input)));
    }
    /** Structural fixture combines the original hook body and controller for cut validation.
     * Actual Mixin application and invocation are tested by the native stage. */
    private ClassNode controller() throws Exception {
        var node = read(original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.CONTROLLER));
        var mixin = read(original("AXIOM_GROOVY_JAR", "com/cleanroommc/groovyscript/core/mixin/LoaderControllerMixin"));
        var hook = method(mixin, "preInit"); hook.name = "handler$fixture$preInit";
        var annotation = new AnnotationNode("Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;");
        annotation.values = new ArrayList<>(List.of("mixin", "com.cleanroommc.groovyscript.core.mixin.LoaderControllerMixin"));
        hook.visibleAnnotations = new ArrayList<>(List.of(annotation)); hook.invisibleAnnotations = null;
        node.methods.add(hook);
        var source = node.methods.stream().filter(m -> m.name.equals("distributeStateMessage") && m.desc.equals(NativeGroovyInitializationPrefix.DISPATCH)).findFirst().orElseThrow();
        var head = new InsnList();
        head.add(new VarInsnNode(Opcodes.ALOAD, 0)); head.add(new VarInsnNode(Opcodes.ALOAD, 1)); head.add(new VarInsnNode(Opcodes.ALOAD, 2));
        head.add(new InsnNode(Opcodes.ACONST_NULL));
        head.add(new MethodInsnNode(Opcodes.INVOKEVIRTUAL, node.name, hook.name, hook.desc, false));
        source.instructions.insert(head); source.maxStack = Math.max(source.maxStack, 4);
        return node;
    }
    @Test void controllerCopyRunsOriginalHookWithoutDispatchingTheFmlEvent() throws Exception {
        var before = controller(); var after = read(NativeGroovyInitializationPrefix.applyController(write(before)));
        originalsPreserved(before, after);
        var prefix = method(after, NativeGroovyInitializationPrefix.METHOD);
        var calls = Arrays.stream(prefix.instructions.toArray()).filter(i -> i instanceof MethodInsnNode).map(i -> (MethodInsnNode)i).toList();
        assertEquals(1, calls.size()); assertEquals("handler$fixture$preInit", calls.getFirst().name);
        assertFalse(Arrays.stream(prefix.instructions.toArray()).anyMatch(i -> i instanceof FieldInsnNode f && f.name.equals("masterChannel")));
        assertEquals(Opcodes.RETURN, prefix.instructions.getLast().getOpcode());
    }
    @Test void absentChangedOrUnownedGroovyHookRefuses() throws Exception {
        byte[] original = original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.CONTROLLER);
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyController(original));
        var unowned = controller(); method(unowned, "handler$fixture$preInit").visibleAnnotations = null;
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyController(write(unowned)));
        var changed = controller(); var hook = method(changed, "handler$fixture$preInit");
        Arrays.stream(hook.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c && c.name.equals("initializeGroovyPreInit"))
                .map(i -> (MethodInsnNode)i).findFirst().orElseThrow().name = "skipGroovy";
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyController(write(changed)));
    }

    @Test void completeLoaderPreservesEveryOriginalOperationAndNonRecipePredicate() throws Exception {
        byte[] input = original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.LOADER);
        var before = read(input); var after = read(NativeGroovyInitializationPrefix.applyCompleteLoader(input));
        originalsPreserved(before, after);
        var source = method(before, "preinitializeMods");
        var copy = method(after, NativeGroovyInitializationPrefix.PREINIT_METHOD);
        var observations = new ArrayList<String>();
        for (var instruction : copy.instructions.toArray()) if (instruction instanceof MethodInsnNode call) {
            if (call.owner.equals("research/orthrus/axiom/materialhost/NativeGroovyClassSpace")) {
                observations.add(call.name); copy.instructions.remove(call);
            } else if (call.owner.equals(NativeGroovyInitializationPrefix.CONTROLLER)
                    && call.name.equals(NativeGroovyInitializationPrefix.PREINIT_METHOD)) call.name = "distributeStateMessage";
        }
        assertEquals(List.of("afterRegistryEvents"), observations);
        copy.name = source.name; copy.access = source.access;
        assertEquals(digest(before, source), digest(after, copy));
    }

    @Test void completeControllerPreservesOriginalHookAndDispatchWithoutReplay() throws Exception {
        var before = controller(); var after = read(NativeGroovyInitializationPrefix.applyCompleteController(write(before)));
        originalsPreserved(before, after);
        var source = before.methods.stream().filter(m -> m.name.equals("distributeStateMessage")
                && m.desc.equals(NativeGroovyInitializationPrefix.DISPATCH)).findFirst().orElseThrow();
        var copy = method(after, NativeGroovyInitializationPrefix.PREINIT_METHOD);
        var observations = new ArrayList<String>();
        for (var instruction : copy.instructions.toArray()) if (instruction instanceof MethodInsnNode call
                && call.owner.equals("research/orthrus/axiom/materialhost/NativeGroovyClassSpace")) {
            observations.add(call.name); copy.instructions.remove(call);
        }
        assertEquals(List.of("beforeModPreInit", "afterModPreInit"), observations);
        copy.name = source.name; copy.access = source.access;
        assertEquals(digest(before, source), digest(after, copy));
    }

    @Test void completeContinuationRefusesMixedOrRepeatedPrefixes() throws Exception {
        byte[] input = original("AXIOM_EARLY_CLEANROOM_JAR", NativeGroovyInitializationPrefix.LOADER);
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyCompleteLoader(
                NativeGroovyInitializationPrefix.applyCompleteLoader(input)));
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyCompleteLoader(
                NativeGroovyInitializationPrefix.applyLoader(input)));
        assertThrows(IllegalArgumentException.class, () -> NativeGroovyInitializationPrefix.applyCompleteController(
                NativeGroovyInitializationPrefix.applyController(write(controller()))));
    }
}
