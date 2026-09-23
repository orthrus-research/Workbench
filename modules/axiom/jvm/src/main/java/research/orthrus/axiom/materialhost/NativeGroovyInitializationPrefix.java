package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Original preInit prerequisites and transformed dispatch head, before FML mod callbacks. */
public final class NativeGroovyInitializationPrefix {
    public static final String METHOD = "axiom$initializeGroovy";
    public static final String PREINIT_METHOD = "axiom$preinitialize";
    public static final String LOADER = "net/minecraftforge/fml/common/Loader";
    public static final String CONTROLLER = "net/minecraftforge/fml/common/LoadController";
    public static final String DISPATCH = "(Lnet/minecraftforge/fml/common/LoaderState;[Ljava/lang/Object;)V";
    public static final String PREINIT_SHA256 = "825b8d135df07dbb1d7330f702bcc8d59a28b172822a23bc4b87c89d1bb27b0b";
    // Original deobfuscation changes the deferred ResourceLocation lambda's
    // bootstrap descriptors from nf to its SRG class name; no operation is removed.
    public static final String NATIVE_PREINIT_SHA256 = "1ac461a6e8689f35d1e7942f4dc1187ae34b2fe1fc89d9f5f5025db8c1565a7a";
    private NativeGroovyInitializationPrefix() {}

    public static byte[] applyLoader(byte[] input) { return applyLoader(input, PREINIT_SHA256); }
    public static byte[] applyLoaderAfterNativeTransforms(byte[] input) { return applyLoader(input, NATIVE_PREINIT_SHA256); }
    private static byte[] applyLoader(byte[] input, String expected) {
        var node = read(input);
        var original = method(node, LOADER, "preinitializeMods", "()V");
        require(expected.equals(methodDigest(original, LOADER)), "Original Loader preInit method differs");
        var calls = Arrays.stream(original.instructions.toArray()).filter(i -> i instanceof MethodInsnNode)
                .map(i -> (MethodInsnNode)i).toList();
        var dispatches = calls.stream().filter(c -> c.owner.equals(CONTROLLER)
                && c.name.equals("distributeStateMessage") && c.desc.equals(DISPATCH)).toList();
        require(dispatches.size() == 1, "Original preInit dispatch is not unique");
        var prefix = through(node, original, dispatches.getFirst(), true);
        var dispatch = Arrays.stream(prefix.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && c.owner.equals(CONTROLLER) && c.name.equals("distributeStateMessage") && c.desc.equals(DISPATCH))
                .map(i -> (MethodInsnNode)i).findFirst().orElseThrow();
        // Preserve the exact original receiver, phase and event-data operands.
        // Only the bounded copy targets the corresponding dispatch-head copy.
        dispatch.name = METHOD;
        node.methods.add(prefix);
        return write(node);
    }

    public static byte[] applyController(byte[] input) {
        var node = read(input);
        var original = method(node, CONTROLLER, "distributeStateMessage", DISPATCH);
        var boundary = groovyBoundary(node, original);
        node.methods.add(through(node, original, boundary, false));
        return write(node);
    }

    /** Complete original preInit copy; the original method and native order remain intact. */
    public static byte[] applyCompleteLoader(byte[] input) { return applyCompleteLoader(input, PREINIT_SHA256); }
    public static byte[] applyCompleteLoaderAfterNativeTransforms(byte[] input) {
        return applyCompleteLoader(input, NATIVE_PREINIT_SHA256);
    }
    private static byte[] applyCompleteLoader(byte[] input, String expected) {
        var node = read(input);
        var original = method(node, LOADER, "preinitializeMods", "()V");
        require(expected.equals(methodDigest(original, LOADER)), "Original Loader preInit method differs");
        var complete = completeCopy(node, original);
        var dispatches = Arrays.stream(complete.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && c.owner.equals(CONTROLLER) && c.name.equals("distributeStateMessage") && c.desc.equals(DISPATCH))
                .map(i -> (MethodInsnNode)i).toList();
        require(dispatches.size() == 1, "Original preInit dispatch is not unique");
        dispatches.getFirst().name = PREINIT_METHOD;
        var registries = Arrays.stream(complete.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && c.owner.equals("net/minecraftforge/registries/GameData") && c.name.equals("fireRegistryEvents"))
                .toList();
        require(registries.size() == 1, "Original non-recipe registry dispatch differs");
        complete.instructions.insert(registries.getFirst(), observation("afterRegistryEvents"));
        node.methods.add(complete);
        return write(node);
    }

    /** Preserve the transformed Groovy head and complete native event dispatch in one call. */
    public static byte[] applyCompleteController(byte[] input) {
        var node = read(input);
        var original = method(node, CONTROLLER, "distributeStateMessage", DISPATCH);
        groovyBoundary(node, original);
        var complete = completeCopy(node, original);
        var boundary = groovyBoundary(node, complete);
        complete.instructions.insertBefore(boundary, observation("beforeModPreInit"));
        for (var instruction : complete.instructions.toArray()) if (instruction.getOpcode() == Opcodes.RETURN)
            complete.instructions.insertBefore(instruction, observation("afterModPreInit"));
        node.methods.add(complete);
        return write(node);
    }

    private static MethodInsnNode observation(String name) {
        return new MethodInsnNode(Opcodes.INVOKESTATIC, "research/orthrus/axiom/materialhost/NativeGroovyClassSpace", name, "()V", false);
    }
    private static MethodNode completeCopy(ClassNode node, MethodNode original) {
        require(node.methods.stream().noneMatch(m -> m.name.equals(PREINIT_METHOD) || m.name.equals(METHOD)),
                "An initialization continuation was already installed");
        var copy = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC, PREINIT_METHOD,
                original.desc, original.signature, original.exceptions.toArray(String[]::new));
        original.accept(copy);
        return copy;
    }

    private static AbstractInsnNode groovyBoundary(ClassNode node, MethodNode original) {
        var checks = Arrays.stream(original.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && c.owner.equals("net/minecraftforge/fml/common/LoaderState") && c.name.equals("hasEvent")
                && c.desc.equals("()Z")).toList();
        require(checks.size() == 1, "Original controller event boundary differs");
        AbstractInsnNode boundary = previousOpcode(checks.getFirst());
        require(boundary instanceof VarInsnNode self && self.getOpcode() == Opcodes.ALOAD && self.var == 1,
                "Original controller state operand differs");
        var prefix = through(node, original, boundary, false);
        var hooks = Arrays.stream(prefix.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && c.owner.equals(CONTROLLER)).map(i -> (MethodInsnNode)i).toList();
        require(hooks.size() == 1, "Original Groovy dispatch head is absent or ambiguous");
        var hook = method(node, CONTROLLER, hooks.getFirst().name, hooks.getFirst().desc);
        require(mergedGroovy(hook), "Dispatch head is not the original Groovy mixin");
        var nativeCalls = Arrays.stream(hook.instructions.toArray()).filter(i -> i instanceof MethodInsnNode c
                && c.owner.equals("com/cleanroommc/groovyscript/GroovyScript")).map(i -> ((MethodInsnNode)i).name).toList();
        require(nativeCalls.equals(List.of("initializeGroovyPreInit", "runGroovyScriptsInLoader", "runGroovyScriptsInLoader")),
                "Original Groovy lifecycle hook differs");
        return boundary;
    }

    public static byte[] preInitMethodEvidence(byte[] input) {
        return methodBytes(method(read(input), LOADER, "preinitializeMods", "()V"), LOADER);
    }
    private static byte[] methodBytes(MethodNode method, String owner) {
        var writer = new ClassWriter(0);
        writer.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC, owner, null, "java/lang/Object", null);
        method.accept(writer); writer.visitEnd(); return writer.toByteArray();
    }
    static String methodDigest(MethodNode method, String owner) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(methodBytes(method, owner))); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }

    private static MethodNode through(ClassNode owner, MethodNode original, AbstractInsnNode boundary, boolean include) {
        require(owner.methods.stream().noneMatch(m -> m.name.equals(METHOD)), "Groovy prefix was already installed");
        var included = Collections.newSetFromMap(new IdentityHashMap<AbstractInsnNode,Boolean>());
        var labels = new IdentityHashMap<LabelNode,LabelNode>();
        for (var instruction : original.instructions) {
            if (instruction == boundary && !include) break;
            included.add(instruction);
            if (instruction instanceof LabelNode label) labels.put(label, new LabelNode());
            if (instruction == boundary) break;
        }
        var result = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC, METHOD, original.desc,
                original.signature, original.exceptions.toArray(String[]::new));
        for (var instruction : original.instructions) {
            if (!included.contains(instruction)) break;
            if (instruction instanceof JumpInsnNode jump) require(labels.containsKey(jump.label), "PreInit branch crosses the boundary");
            require(!(instruction instanceof TableSwitchInsnNode) && !(instruction instanceof LookupSwitchInsnNode),
                    "Unexpected preInit switch boundary");
            if (instruction instanceof FrameNode frame) {
                for (var values : List.of(frame.local == null ? List.of() : frame.local, frame.stack == null ? List.of() : frame.stack))
                    for (Object value : values) if (value instanceof LabelNode label)
                        require(labels.containsKey(label), "PreInit frame crosses the boundary");
            }
            result.instructions.add(instruction.clone(labels));
        }
        for (var block : original.tryCatchBlocks) {
            boolean start = included.contains(block.start), end = included.contains(block.end), handler = included.contains(block.handler);
            require(!(start || end || handler) || start && end && handler, "PreInit exception region crosses the boundary");
            if (start) result.tryCatchBlocks.add(new TryCatchBlockNode(labels.get(block.start), labels.get(block.end), labels.get(block.handler), block.type));
        }
        result.instructions.add(new InsnNode(Opcodes.RETURN));
        result.maxLocals = original.maxLocals; result.maxStack = original.maxStack;
        return result;
    }

    private static boolean mergedGroovy(MethodNode method) {
        var annotations = new ArrayList<AnnotationNode>();
        if (method.visibleAnnotations != null) annotations.addAll(method.visibleAnnotations);
        if (method.invisibleAnnotations != null) annotations.addAll(method.invisibleAnnotations);
        for (var annotation : annotations) if (annotation.desc.equals("Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;")
                && annotation.values != null) for (int i = 0; i < annotation.values.size(); i += 2)
            if (annotation.values.get(i).equals("mixin") && annotation.values.get(i + 1)
                    .equals("com.cleanroommc.groovyscript.core.mixin.LoaderControllerMixin")) return true;
        return false;
    }
    private static AbstractInsnNode previousOpcode(AbstractInsnNode instruction) {
        do { instruction = instruction.getPrevious(); } while (instruction != null && instruction.getOpcode() < 0);
        require(instruction != null, "PreInit boundary has no operand"); return instruction;
    }
    private static MethodNode method(ClassNode node, String owner, String name, String descriptor) {
        require(owner.equals(node.name), "PreInit method owner differs");
        var methods = node.methods.stream().filter(m -> m.name.equals(name) && m.desc.equals(descriptor)).toList();
        require(methods.size() == 1, "PreInit method is absent or ambiguous: " + name); return methods.getFirst();
    }
    private static ClassNode read(byte[] input) { var node = new ClassNode(); new ClassReader(input).accept(node, ClassReader.EXPAND_FRAMES); return node; }
    private static byte[] write(ClassNode node) { var writer = new ClassWriter(0); node.accept(writer); return writer.toByteArray(); }
    private static void require(boolean value, String message) { if (!value) throw new IllegalArgumentException(message); }
}
