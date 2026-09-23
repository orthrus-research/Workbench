package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Assumptions;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.ZipFile;
import static org.junit.jupiter.api.Assertions.*;

class NativeEarlyLaunchPrefixTest {
    private static final String OWNER = NativeEarlyLaunchPrefix.TARGET.replace('.', '/');
    private static final String TWEAKER = "net/minecraft/launchwrapper/ITweaker";

    private static ClassNode fixture() {
        var node = new ClassNode(); node.version = Opcodes.V25;
        node.name = OWNER; node.superName = "java/lang/Object";
        var method = new MethodNode(Opcodes.ACC_PUBLIC, "launch", "([Ljava/lang/String;)V", null, null);
        var body = method.instructions;
        body.add(new MethodInsnNode(Opcodes.INVOKESTATIC, "top/outlands/foundation/TransformerDelegate", "fillTransformerHolder", "()V", false));
        for (int i = 0; i < 2; i++) body.add(new MethodInsnNode(Opcodes.INVOKESTATIC,
                "top/outlands/foundation/TransformerDelegate", "registerExplicitTransformer", "()V", false));
        var asmStart = new LabelNode(); var asmEnd = new LabelNode(); var asmHandler = new LabelNode(); var asmDone = new LabelNode();
        body.add(asmStart);
        for (int i = 0; i < 7; i++) body.add(new MethodInsnNode(Opcodes.INVOKESTATIC,
                "net/minecraft/launchwrapper/LaunchClassLoader", "findClass", "()V", false));
        body.add(asmEnd); body.add(new JumpInsnNode(Opcodes.GOTO, asmDone));
        body.add(asmHandler); body.add(new InsnNode(Opcodes.POP)); body.add(asmDone);
        method.tryCatchBlocks.add(new TryCatchBlockNode(asmStart, asmEnd, asmHandler, "java/lang/ClassNotFoundException"));
        var start = new LabelNode(); var names = new LabelNode(); var tweaks = new LabelNode(); var outerCheck = new LabelNode();
        body.add(start); body.add(names);
        body.add(new VarInsnNode(Opcodes.ALOAD, 10));
        call(body, "java/util/List", "isEmpty", "()Z");
        body.add(new JumpInsnNode(Opcodes.IFNE, tweaks));
        body.add(new VarInsnNode(Opcodes.ALOAD, 10)); call(body, "java/util/List", "getFirst", "()Ljava/lang/Object;"); body.add(new InsnNode(Opcodes.POP));
        for (int i = 0; i < 2; i++) {
            body.add(new VarInsnNode(Opcodes.ALOAD, 10)); call(body, "java/util/List", "removeFirst", "()Ljava/lang/Object;"); body.add(new InsnNode(Opcodes.POP));
        }
        body.add(new JumpInsnNode(Opcodes.GOTO, names));
        body.add(tweaks); body.add(new VarInsnNode(Opcodes.ALOAD, 14)); call(body, "java/util/List", "isEmpty", "()Z");
        body.add(new JumpInsnNode(Opcodes.IFNE, outerCheck));
        body.add(new VarInsnNode(Opcodes.ALOAD, 14)); call(body, "java/util/List", "getFirst", "()Ljava/lang/Object;");
        body.add(new VarInsnNode(Opcodes.ASTORE, 16));
        body.add(new VarInsnNode(Opcodes.ALOAD, 16));
        for (int i = 0; i < 4; i++) body.add(new InsnNode(Opcodes.ACONST_NULL));
        call(body, TWEAKER, "acceptOptions", "(Ljava/util/List;Ljava/io/File;Ljava/io/File;Ljava/lang/String;)V");
        body.add(new VarInsnNode(Opcodes.ALOAD, 16)); body.add(new InsnNode(Opcodes.ACONST_NULL));
        call(body, TWEAKER, "injectIntoClassLoader", "(Lnet/minecraft/launchwrapper/LaunchClassLoader;)V");
        body.add(new VarInsnNode(Opcodes.ALOAD, 13)); body.add(new VarInsnNode(Opcodes.ALOAD, 16));
        call(body, "java/util/List", "add", "(Ljava/lang/Object;)Z"); body.add(new InsnNode(Opcodes.POP));
        body.add(new VarInsnNode(Opcodes.ALOAD, 14)); body.add(new VarInsnNode(Opcodes.ALOAD, 16));
        call(body, "java/util/List", "remove", "(Ljava/lang/Object;)Z"); body.add(new InsnNode(Opcodes.POP));
        body.add(new JumpInsnNode(Opcodes.GOTO, tweaks));
        body.add(outerCheck); body.add(new VarInsnNode(Opcodes.ALOAD, 10)); call(body, "java/util/List", "isEmpty", "()Z");
        body.add(new JumpInsnNode(Opcodes.IFEQ, names));
        var late = new LabelNode(); body.add(late); body.add(new LineNumberNode(113, late));
        body.add(new VarInsnNode(Opcodes.ALOAD, 13)); call(body, "java/util/List", "iterator", "()Ljava/util/Iterator;"); body.add(new InsnNode(Opcodes.POP));
        body.add(new VarInsnNode(Opcodes.ALOAD, 16)); call(body, TWEAKER, "getLaunchArguments", "()[Ljava/lang/String;"); body.add(new InsnNode(Opcodes.POP));
        body.add(new VarInsnNode(Opcodes.ALOAD, 15)); call(body, TWEAKER, "getLaunchTarget", "()Ljava/lang/String;"); body.add(new InsnNode(Opcodes.POP));
        var end = new LabelNode(); var handler = new LabelNode(); var done = new LabelNode();
        body.add(end); body.add(new JumpInsnNode(Opcodes.GOTO, done)); body.add(handler);
        body.add(new VarInsnNode(Opcodes.ASTORE, 14));
        body.add(new FieldInsnNode(Opcodes.GETSTATIC, "top/outlands/foundation/boot/Foundation", "LOGGER", "Lorg/apache/logging/log4j/Logger;"));
        body.add(new LdcInsnNode("Unable to launch")); body.add(new VarInsnNode(Opcodes.ALOAD, 14));
        call(body, "org/apache/logging/log4j/Logger", "fatal", "(Ljava/lang/String;Ljava/lang/Throwable;)V");
        body.add(new InsnNode(Opcodes.ICONST_1));
        body.add(new MethodInsnNode(Opcodes.INVOKESTATIC, "java/lang/System", "exit", "(I)V", false));
        body.add(done); body.add(new InsnNode(Opcodes.RETURN));
        method.tryCatchBlocks.add(new TryCatchBlockNode(start, end, handler, "java/lang/Throwable"));
        method.localVariables = new ArrayList<>(List.of(new LocalVariableNode("allTweakers", "Ljava/util/List;", null, start, done, 13)));
        method.maxStack = 7; method.maxLocals = 19;
        node.methods.add(method); return node;
    }

    private static void call(InsnList body, String owner, String name, String descriptor) {
        body.add(new MethodInsnNode(Opcodes.INVOKEINTERFACE, owner, name, descriptor, true));
    }
    private static MethodInsnNode find(MethodNode method, String name) {
        return Arrays.stream(method.instructions.toArray()).filter(i -> i instanceof MethodInsnNode call && name.equals(call.name))
                .map(i -> (MethodInsnNode)i).findFirst().orElseThrow();
    }

    @Test void preservesNativeLoopAndOriginalMethodButStopsBeforeLateCallbacks() {
        var node = fixture(); var original = node.methods.getFirst();
        var before = original.instructions.toArray(); var blocks = List.copyOf(original.tryCatchBlocks);
        var extracted = NativeEarlyLaunchPrefix.extract(node);
        assertEquals(1, node.methods.size()); assertArrayEquals(before, original.instructions.toArray());
        assertEquals(blocks, original.tryCatchBlocks); assertEquals(1, original.localVariables.size());
        assertEquals(NativeEarlyLaunchPrefix.METHOD, extracted.name);
        assertEquals(NativeEarlyLaunchPrefix.DESCRIPTOR, extracted.desc);
        assertTrue(extracted.localVariables == null || extracted.localVariables.isEmpty());
        var copy = extracted.instructions.toArray();
        int cutoff = original.instructions.indexOf(find(original, "iterator").getPrevious());
        for (int i = 0; i < cutoff; i++) {
            assertNotSame(before[i], copy[i]); assertEquals(before[i].getOpcode(), copy[i].getOpcode());
            if (before[i] instanceof MethodInsnNode call) {
                var cloned = assertInstanceOf(MethodInsnNode.class, copy[i]);
                assertEquals(call.owner, cloned.owner); assertEquals(call.name, cloned.name); assertEquals(call.desc, cloned.desc);
            }
        }
        assertFalse(Arrays.stream(copy).anyMatch(i -> i instanceof MethodInsnNode call
                && Set.of("getLaunchArguments", "getLaunchTarget", "iterator", "exit").contains(call.name)));
        assertEquals(1, Arrays.stream(copy).filter(i -> i.getOpcode() == Opcodes.ARETURN).count());
        var returned = Arrays.stream(copy).filter(i -> i.getOpcode() == Opcodes.ARETURN).findFirst().orElseThrow();
        assertEquals(13, assertInstanceOf(VarInsnNode.class, returned.getPrevious()).var);
    }

    @Test void preservesNativeFatalLogAndRethrowsTheSameCaughtThrowable() {
        var original = fixture().methods.getFirst(); var node = fixture();
        var extracted = NativeEarlyLaunchPrefix.extract(node);
        assertEquals(2, extracted.tryCatchBlocks.size());
        var internal = extracted.tryCatchBlocks.getFirst(); var outer = extracted.tryCatchBlocks.getLast();
        assertEquals("java/lang/ClassNotFoundException", internal.type); assertEquals("java/lang/Throwable", outer.type);
        var copy = Arrays.asList(extracted.instructions.toArray());
        assertTrue(copy.containsAll(List.of(internal.start, internal.end, internal.handler, outer.start, outer.end, outer.handler)));
        assertEquals(Opcodes.ALOAD, outer.end.getNext().getOpcode());
        assertEquals(Opcodes.ARETURN, outer.end.getNext().getNext().getOpcode());
        var fatal = find(extracted, "fatal");
        assertEquals(find(original, "fatal").owner, fatal.owner); assertEquals(find(original, "fatal").desc, fatal.desc);
        var rethrowLoad = assertInstanceOf(VarInsnNode.class, fatal.getNext());
        assertEquals(Opcodes.ALOAD, rethrowLoad.getOpcode()); assertEquals(14, rethrowLoad.var);
        assertEquals(Opcodes.ATHROW, rethrowLoad.getNext().getOpcode());
        assertNotSame(node.methods.getFirst().tryCatchBlocks.getLast().handler, outer.handler);
    }

    @Test void runtimeRefusesUnboundClassBytesBeforeStructuralExtraction() {
        var writer = new ClassWriter(0); fixture().accept(writer);
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.apply(writer.toByteArray()));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.apply(null));
    }

    @Test void missingOrChangedQueueBoundaryAndDispatchRefuse() {
        var wrongCondition = fixture(); var body = wrongCondition.methods.getFirst();
        for (var instruction : body.instructions.toArray()) if (instruction instanceof JumpInsnNode jump && jump.getOpcode() == Opcodes.IFEQ)
            body.instructions.set(jump, new JumpInsnNode(Opcodes.IFNE, jump.label));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(wrongCondition));
        var missingDispatch = fixture(); find(missingDispatch.methods.getFirst(), "acceptOptions").name = "bypassOptions";
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(missingDispatch));
        var missingTail = fixture(); find(missingTail.methods.getFirst(), "getLaunchArguments").name = "bypassArguments";
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(missingTail));
        var duplicate = fixture(); duplicate.methods.getFirst().instructions.add(new MethodInsnNode(Opcodes.INVOKEINTERFACE,
                "java/util/List", "iterator", "()Ljava/util/Iterator;", true));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(duplicate));
    }

    @Test void changedCompletedListAndFailureIdentityRefuse() {
        var wrongList = fixture();
        ((VarInsnNode)find(wrongList.methods.getFirst(), "iterator").getPrevious()).var = 12;
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(wrongList));
        var wrongCause = fixture(); ((VarInsnNode)find(wrongCause.methods.getFirst(), "fatal").getPrevious()).var = 12;
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(wrongCause));
        var wrongExit = fixture();
        var exit = find(wrongExit.methods.getFirst(), "exit");
        wrongExit.methods.getFirst().instructions.set(exit.getPrevious(), new InsnNode(Opcodes.ICONST_0));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(wrongExit));
    }

    @Test void crossingControlFlowAndExceptionRegionsRefuse() {
        var outgoing = fixture(); var tail = outgoing.methods.getFirst().tryCatchBlocks.getLast().end;
        outgoing.methods.getFirst().instructions.insert(new JumpInsnNode(Opcodes.GOTO, tail));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(outgoing));
        var crossing = fixture(); crossing.methods.getFirst().tryCatchBlocks.getFirst().end = crossing.methods.getFirst().tryCatchBlocks.getLast().end;
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(crossing));
        var earlyReturn = fixture(); earlyReturn.methods.getFirst().instructions.insert(new InsnNode(Opcodes.RETURN));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(earlyReturn));
    }

    @Test void ownerDescriptorAndRepeatedExtractionRefuse() {
        var owner = fixture(); owner.name = "fixture/Replacement";
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(owner));
        var descriptor = fixture(); descriptor.methods.getFirst().desc = "()V";
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(descriptor));
        var duplicate = fixture(); duplicate.methods.add(NativeEarlyLaunchPrefix.extract(duplicate));
        assertThrows(IllegalArgumentException.class, () -> NativeEarlyLaunchPrefix.extract(duplicate));
    }

    @Test void exactSelectedArtifactPreservesLaunchAndDefinesWithoutNativeInitialization() throws Exception {
        String artifact = System.getenv("AXIOM_EARLY_FOUNDATION_JAR");
        Assumptions.assumeTrue(artifact != null && !artifact.isBlank(), "Exact Foundation artifact was not supplied");
        byte[] originalBytes;
        try (var jar = new ZipFile(Path.of(artifact).toFile());
                var input = jar.getInputStream(Objects.requireNonNull(jar.getEntry(OWNER + ".class")))) {
            originalBytes = input.readAllBytes();
        }
        byte[] transformedBytes = NativeEarlyLaunchPrefix.apply(originalBytes);
        var original = new ClassNode(); var transformed = new ClassNode();
        new ClassReader(originalBytes).accept(original, ClassReader.EXPAND_FRAMES);
        new ClassReader(transformedBytes).accept(transformed, ClassReader.EXPAND_FRAMES);
        assertEquals(original.methods.size() + 1, transformed.methods.size());
        var originalLaunch = original.methods.stream().filter(m -> "launch".equals(m.name)).findFirst().orElseThrow();
        var retainedLaunch = transformed.methods.stream().filter(m -> "launch".equals(m.name)).findFirst().orElseThrow();
        assertArrayEquals(methodBytes(original, originalLaunch), methodBytes(transformed, retainedLaunch),
                "Original launch instructions, exception regions, frames and debug metadata must remain intact");
        var early = transformed.methods.stream().filter(m -> NativeEarlyLaunchPrefix.METHOD.equals(m.name)).findFirst().orElseThrow();
        assertEquals(NativeEarlyLaunchPrefix.DESCRIPTOR, early.desc);
        assertEquals(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC, early.access);
        assertEquals(List.of("java/lang/Throwable"), early.exceptions);
        var instructions = early.instructions.toArray();
        assertFalse(Arrays.stream(instructions).anyMatch(i -> i instanceof MethodInsnNode call
                && (Set.of("getLaunchArguments", "getLaunchTarget").contains(call.name)
                    || "java/lang/System".equals(call.owner) && "exit".equals(call.name)
                    || "java/lang/invoke/MethodHandle".equals(call.owner))));
        for (String name : List.of("acceptOptions", "injectIntoClassLoader"))
            assertEquals(1, Arrays.stream(instructions).filter(i -> i instanceof MethodInsnNode call
                    && TWEAKER.equals(call.owner) && name.equals(call.name)).count());
        assertEquals(1, Arrays.stream(instructions).filter(i -> i.getOpcode() == Opcodes.ARETURN).count());
        var outer = early.tryCatchBlocks.stream().filter(b -> "java/lang/Throwable".equals(b.type)).findFirst().orElseThrow();
        var successfulReturn = nextOpcode(outer.end);
        assertEquals(Opcodes.ALOAD, successfulReturn.getOpcode());
        assertEquals(Opcodes.ARETURN, nextOpcode(successfulReturn).getOpcode());
        var fatal = find(early, "fatal");
        var caughtRead = assertInstanceOf(VarInsnNode.class, previousOpcode(fatal));
        var rethrowRead = assertInstanceOf(VarInsnNode.class, nextOpcode(fatal));
        assertEquals(Opcodes.ALOAD, rethrowRead.getOpcode()); assertEquals(caughtRead.var, rethrowRead.var);
        assertEquals(Opcodes.ATHROW, nextOpcode(rethrowRead).getOpcode());

        // Define only. No reflective member resolution, constructor, callback or
        // initialization is invoked; this is not native execution or acceptance.
        var definitions = new ClassLoader(ClassLoader.getPlatformClassLoader()) {
            Class<?> define(byte[] bytes) { return defineClass(NativeEarlyLaunchPrefix.TARGET, bytes, 0, bytes.length); }
        };
        Class<?> defined = definitions.define(transformedBytes);
        assertEquals(NativeEarlyLaunchPrefix.TARGET, defined.getName());
        assertSame(definitions, defined.getClassLoader());
    }

    @Test void exactOriginalArtifactsProveWrappedRootAndMixinProxyQueueRecipe() throws Exception {
        String foundation = System.getenv("AXIOM_EARLY_FOUNDATION_JAR");
        String cleanroom = System.getenv("AXIOM_EARLY_CLEANROOM_JAR");
        String cleanmix = System.getenv("AXIOM_EARLY_CLEANMIX_JAR");
        Assumptions.assumeTrue(foundation != null && cleanroom != null && cleanmix != null,
                "Exact Foundation, Cleanroom and CleanMix artifacts were not supplied");
        assertArtifact(foundation, "a9f5cf9cb54715edf22d6bcb49bae50f281f818b1731f53f22aab85a48a7b54b");
        assertArtifact(cleanroom, "48043f4ea69605ec9b1250b9bd01106b07023b78614bac734a8a296e690e7893");
        assertArtifact(cleanmix, "0b92f8443dd5972346e4ee60a421407cf0215dbf1c9cd06b6f435e3f5f9a0d04");
        String transformerPackage = "net/minecraftforge/fml/common/asm/transformers/";
        List<String> rootNames = List.of("SideTransformer", "EventSubscriptionTransformer", "EventSubscriberTransformer",
                "SoundEngineFixTransformer", "LWJGLTransformer");
        var plugin = readClass(cleanroom, "net/minecraftforge/fml/relauncher/FMLCorePlugin");
        var declared = method(plugin, "getASMTransformerClass");
        assertEquals(rootNames.stream().map(n -> (transformerPackage + n).replace('/', '.')).toList(),
                Arrays.stream(declared.instructions.toArray()).filter(i -> i instanceof LdcInsnNode)
                        .map(i -> ((LdcInsnNode)i).cst).toList());
        var wrapperFactory = readClass(cleanroom, "net/minecraftforge/fml/common/asm/ASMTransformerWrapper");
        assertTrue(Arrays.stream(method(wrapperFactory, "getWrapperName").instructions.toArray())
                .anyMatch(i -> i instanceof InvokeDynamicInsnNode dynamic && Arrays.asList(dynamic.bsmArgs).contains("$wrapper.\u0001")));
        var wrapper = readClass(cleanroom, "net/minecraftforge/fml/common/asm/ASMTransformerWrapper$TransformerWrapper");
        assertTrue(wrapper.fields.stream().anyMatch(f -> "parent".equals(f.name)
                && "Lnet/minecraft/launchwrapper/IClassTransformer;".equals(f.desc) && (f.access & Opcodes.ACC_FINAL) != 0));
        var priority = method(wrapper, "getPriority");
        assertEquals(List.of(Opcodes.ALOAD, Opcodes.GETFIELD, Opcodes.INVOKEINTERFACE, Opcodes.IRETURN), opcodes(priority));
        var priorityCall = find(priority, "getPriority");
        assertEquals("net/minecraft/launchwrapper/IClassTransformer", priorityCall.owner);
        var originalPriority = method(readClass(foundation, "net/minecraft/launchwrapper/IClassTransformer"), "getPriority");
        assertEquals(List.of(Opcodes.ICONST_0, Opcodes.IRETURN), opcodes(originalPriority));
        for (String name : rootNames)
            assertTrue(readClass(cleanroom, transformerPackage + name).methods.stream().noneMatch(m -> "getPriority".equals(m.name)));

        String proxyName = "org/spongepowered/asm/mixin/transformer/Proxy";
        var proxy = readClass(cleanmix, proxyName);
        assertTrue(proxy.methods.stream().noneMatch(m -> "getPriority".equals(m.name)));
        assertTrue(proxy.fields.stream().anyMatch(f -> "isActive".equals(f.name) && "Z".equals(f.desc) && f.access == Opcodes.ACC_PRIVATE));
        var activeWrites = Arrays.stream(method(proxy, "<init>").instructions.toArray())
                .filter(i -> i instanceof FieldInsnNode f && f.getOpcode() == Opcodes.PUTFIELD && "isActive".equals(f.name)).toList();
        assertEquals(2, activeWrites.size());
        assertEquals(Opcodes.ICONST_1, previousOpcode(activeWrites.getFirst()).getOpcode());
        assertEquals(Opcodes.ICONST_0, previousOpcode(activeWrites.getLast()).getOpcode());
        var transform = method(proxy, "transform");
        var condition = Arrays.stream(transform.instructions.toArray()).filter(i -> i instanceof JumpInsnNode).findFirst().orElseThrow();
        assertEquals(Opcodes.IFEQ, condition.getOpcode());
        var passthrough = assertInstanceOf(VarInsnNode.class, nextOpcode(((JumpInsnNode)condition).label));
        assertEquals(Opcodes.ALOAD, passthrough.getOpcode()); assertEquals(3, passthrough.var);
        assertEquals(Opcodes.ARETURN, nextOpcode(passthrough).getOpcode());
        var begin = method(readClass(cleanmix, "org/spongepowered/asm/service/mojang/AbstractMixinServiceLaunchWrapper"), "beginPhase");
        assertEquals(proxyName.replace('/', '.'), assertInstanceOf(LdcInsnNode.class,
                previousOpcode(find(begin, "registerTransformer"))).cst);
        for (var ownerAndMethod : List.of(List.of("org/spongepowered/asm/launch/MixinBootstrap", "start"),
                List.of("org/spongepowered/asm/mixin/MixinEnvironment", "gotoPhase")))
            assertEquals(1, Arrays.stream(method(readClass(cleanmix, ownerAndMethod.getFirst()), ownerAndMethod.getLast()).instructions.toArray())
                    .filter(i -> i instanceof MethodInsnNode call && "beginPhase".equals(call.name)).count());
        // These assertions inspect original bytecode. They do not initialize a
        // transformer, execute a callback, or certify that the native queue ran.
    }

    private static void assertArtifact(String artifact, String sha256) throws Exception {
        assertEquals(sha256, HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(Path.of(artifact)))));
    }
    private static ClassNode readClass(String artifact, String name) throws Exception {
        try (var jar = new ZipFile(artifact); var input = jar.getInputStream(Objects.requireNonNull(jar.getEntry(name + ".class")))) {
            var node = new ClassNode(); new ClassReader(input.readAllBytes()).accept(node, 0); return node;
        }
    }
    private static MethodNode method(ClassNode owner, String name) {
        return owner.methods.stream().filter(m -> name.equals(m.name)).findFirst().orElseThrow();
    }
    private static List<Integer> opcodes(MethodNode method) {
        return Arrays.stream(method.instructions.toArray()).map(AbstractInsnNode::getOpcode).filter(opcode -> opcode >= 0).toList();
    }

    private static byte[] methodBytes(ClassNode owner, MethodNode method) {
        var writer = new ClassWriter(0);
        writer.visit(owner.version, owner.access, owner.name, owner.signature, owner.superName, owner.interfaces.toArray(String[]::new));
        method.accept(writer); writer.visitEnd();
        return writer.toByteArray();
    }
    private static AbstractInsnNode previousOpcode(AbstractInsnNode instruction) {
        do { instruction = instruction.getPrevious(); } while (instruction != null && instruction.getOpcode() < 0);
        return Objects.requireNonNull(instruction);
    }
    private static AbstractInsnNode nextOpcode(AbstractInsnNode instruction) {
        do { instruction = instruction.getNext(); } while (instruction != null && instruction.getOpcode() < 0);
        return Objects.requireNonNull(instruction);
    }
}
