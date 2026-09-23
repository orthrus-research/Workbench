package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Adds a separate original Foundation launch prefix ending after its native tweak queue.
 * No launch-argument callback, launch target or game entry is reached. The original
 * launch method remains intact. The sole failure adaptation retains Foundation's
 * fatal log and rethrows its caught Throwable instead of calling System.exit(1),
 * so the existing supervised worker can preserve the native cause.
 */
public final class NativeEarlyLaunchPrefix {
    public static final String TARGET = "top.outlands.foundation.LaunchHandler";
    public static final String METHOD = "axiom$initializeEarlyTweaks";
    public static final String DESCRIPTOR = "([Ljava/lang/String;)Ljava/util/List;";
    public static final String ORIGINAL_SHA256 = "1ffe4afdec34038ba3acb1f1e4d73b32d0dafa2e4d8a8cfedf75e8dd17683e0c";
    private static final String OWNER = TARGET.replace('.', '/');
    private static final String TWEAKER = "net/minecraft/launchwrapper/ITweaker";
    private static final String DELEGATE = "top/outlands/foundation/TransformerDelegate";
    private NativeEarlyLaunchPrefix() {}

    public static byte[] apply(byte[] input) {
        return apply(input, false);
    }

    /** Same bootstrap, with the later Loader stop installed after Foundation's reset. */
    public static byte[] applySelection(byte[] input) {
        return apply(input, true);
    }

    private static byte[] apply(byte[] input, boolean selection) {
        if (input == null || !ORIGINAL_SHA256.equals(digest(input)))
            throw new IllegalArgumentException("Native Foundation LaunchHandler differs from the source-bound original artifact");
        var node = new ClassNode();
        // Expanded frames remain valid when the late tail is removed; computing
        // frames here would load native classes ahead of their transformation phase.
        new ClassReader(input).accept(node, ClassReader.EXPAND_FRAMES);
        var prefix = extract(node);
        if (selection) {
            var reset = Arrays.stream(prefix.instructions.toArray()).filter(i -> i instanceof MethodInsnNode call
                    && DELEGATE.equals(call.owner) && "fillTransformerHolder".equals(call.name)).toList();
            require(reset.size() == 1, "Native transformer reset is not unique");
            prefix.instructions.insert(reset.getFirst(), new MethodInsnNode(Opcodes.INVOKESTATIC,
                    "research/orthrus/axiom/materialhost/NativeEarlyClassSpace", "installSelectionPrefix", "()V", false));
        }
        node.methods.add(prefix);
        var writer = new ClassWriter(0);
        node.accept(writer);
        return writer.toByteArray();
    }

    // Structural regression seam only. Runtime admission always requires apply's digest.
    static MethodNode extract(ClassNode node) {
        require(OWNER.equals(node.name) && node.methods.stream().noneMatch(m -> METHOD.equals(m.name)),
                "Native launch owner or extraction state differs");
        var matches = node.methods.stream().filter(m -> "launch".equals(m.name) && "([Ljava/lang/String;)V".equals(m.desc)).toList();
        require(matches.size() == 1 && matches.getFirst().access == Opcodes.ACC_PUBLIC,
                "Native launch descriptor or access differs");
        var original = matches.getFirst();
        var instructions = original.instructions.toArray();
        int iterator = uniqueCall(instructions, "java/util/List", "iterator", "()Ljava/util/Iterator;");
        int boundary = previousOpcode(instructions, iterator);
        require(instructions[boundary] instanceof VarInsnNode load && load.getOpcode() == Opcodes.ALOAD,
                "Native launch-argument iteration boundary differs");
        int allTweakersLocal = ((VarInsnNode)instructions[boundary]).var;
        int injection = uniqueCall(instructions, TWEAKER, "injectIntoClassLoader", "(Lnet/minecraft/launchwrapper/LaunchClassLoader;)V");
        int completedListLoad = nextOpcode(instructions, injection);
        int completedTweakerLoad = nextOpcode(instructions, completedListLoad);
        int completedAdd = nextOpcode(instructions, completedTweakerLoad);
        require(instructions[completedListLoad] instanceof VarInsnNode completed && completed.getOpcode() == Opcodes.ALOAD
                        && completed.var == allTweakersLocal
                        && instructions[completedTweakerLoad] instanceof VarInsnNode tweaker && tweaker.getOpcode() == Opcodes.ALOAD
                        && isCall(instructions[completedAdd], "java/util/List", "add", "(Ljava/lang/Object;)Z")
                        && completedAdd < boundary,
                "Native completed-tweaker list provenance differs");
        int loopEnd = previousOpcode(instructions, boundary);
        require(instructions[loopEnd] instanceof JumpInsnNode back && back.getOpcode() == Opcodes.IFEQ
                        && indexOf(instructions, back.label) < loopEnd,
                "Native complete tweak-loop boundary differs");
        int empty = previousOpcode(instructions, loopEnd);
        require(isCall(instructions[empty], "java/util/List", "isEmpty", "()Z"),
                "Native outer tweak-loop condition differs");
        int argumentCall = uniqueCall(instructions, TWEAKER, "getLaunchArguments", "()[Ljava/lang/String;");
        int targetCall = uniqueCall(instructions, TWEAKER, "getLaunchTarget", "()Ljava/lang/String;");
        require(iterator < argumentCall && argumentCall < targetCall,
                "Native argument and target tail differs");
        require(countCalls(instructions, boundary, DELEGATE, "fillTransformerHolder") == 1
                        && countCalls(instructions, boundary, DELEGATE, "registerExplicitTransformer") == 2
                        && countCalls(instructions, boundary, "net/minecraft/launchwrapper/LaunchClassLoader", "findClass") == 7
                        && countCalls(instructions, boundary, "java/util/List", "isEmpty") == 3
                        && countCalls(instructions, boundary, "java/util/List", "getFirst") == 2
                        && countCalls(instructions, boundary, "java/util/List", "removeFirst") == 2
                        && countCalls(instructions, boundary, "java/util/List", "remove") == 1
                        && countCalls(instructions, boundary, TWEAKER, "acceptOptions") == 1
                        && countCalls(instructions, boundary, TWEAKER, "injectIntoClassLoader") == 1,
                "Native bootstrap or tweak dispatch sequence differs");

        var outerHandlers = original.tryCatchBlocks.stream().filter(b -> "java/lang/Throwable".equals(b.type)).toList();
        require(outerHandlers.size() == 1, "Native launch failure handler is not unique");
        var outer = outerHandlers.getFirst();
        int handler = indexOf(instructions, outer.handler);
        require(indexOf(instructions, outer.start) < boundary && indexOf(instructions, outer.end) > targetCall
                        && indexOf(instructions, outer.end) < handler,
                "Native launch failure region differs");
        int exit = uniqueCall(instructions, "java/lang/System", "exit", "(I)V");
        require(exit > handler, "Native process exit is outside its failure handler");
        var catchBody = new ArrayList<AbstractInsnNode>();
        for (int i = handler; i <= exit; i++) if (instructions[i].getOpcode() >= 0) catchBody.add(instructions[i]);
        require(catchBody.size() == 7
                        && catchBody.get(0) instanceof VarInsnNode caught && caught.getOpcode() == Opcodes.ASTORE
                        && catchBody.get(1) instanceof FieldInsnNode logger && logger.getOpcode() == Opcodes.GETSTATIC
                        && "top/outlands/foundation/boot/Foundation".equals(logger.owner) && "LOGGER".equals(logger.name)
                        && "Lorg/apache/logging/log4j/Logger;".equals(logger.desc)
                        && catchBody.get(2) instanceof LdcInsnNode text && "Unable to launch".equals(text.cst)
                        && catchBody.get(3) instanceof VarInsnNode error && error.getOpcode() == Opcodes.ALOAD && error.var == caught.var
                        && isCall(catchBody.get(4), "org/apache/logging/log4j/Logger", "fatal", "(Ljava/lang/String;Ljava/lang/Throwable;)V")
                        && catchBody.get(5).getOpcode() == Opcodes.ICONST_1
                        && instructions[nextOpcode(instructions, exit)].getOpcode() == Opcodes.RETURN,
                "Native fatal-log and process-exit shape differs");
        int exitArgument = indexOf(instructions, catchBody.get(5));
        int caughtLocal = ((VarInsnNode)catchBody.getFirst()).var;

        var included = Collections.newSetFromMap(new IdentityHashMap<AbstractInsnNode,Boolean>());
        for (int i = 0; i < boundary; i++) included.add(instructions[i]);
        for (int i = handler; i < exitArgument; i++) included.add(instructions[i]);
        var labels = new IdentityHashMap<LabelNode,LabelNode>();
        for (var instruction : instructions) if (included.contains(instruction) && instruction instanceof LabelNode label)
            labels.put(label, new LabelNode());
        for (var instruction : included) validateControlFlow(instruction, labels);

        var result = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC,
                METHOD, DESCRIPTOR, null, new String[]{"java/lang/Throwable"});
        for (int i = 0; i < boundary; i++) result.instructions.add(instructions[i].clone(labels));
        var stop = new LabelNode();
        result.instructions.add(stop);
        // Return the existing allTweakers local, without dispatching another callback.
        result.instructions.add(new VarInsnNode(Opcodes.ALOAD, allTweakersLocal));
        result.instructions.add(new InsnNode(Opcodes.ARETURN));
        for (int i = handler; i < exitArgument; i++) result.instructions.add(instructions[i].clone(labels));
        result.instructions.add(new VarInsnNode(Opcodes.ALOAD, caughtLocal));
        result.instructions.add(new InsnNode(Opcodes.ATHROW));
        for (var block : original.tryCatchBlocks) {
            if (block == outer) {
                result.tryCatchBlocks.add(new TryCatchBlockNode(labels.get(block.start), stop, labels.get(block.handler), block.type));
            } else {
                require(included.contains(block.start) && included.contains(block.end) && included.contains(block.handler),
                        "Native nested failure region crosses the early boundary");
                result.tryCatchBlocks.add(new TryCatchBlockNode(labels.get(block.start), labels.get(block.end), labels.get(block.handler), block.type));
            }
        }
        // Source line nodes are cloned. Local-variable ranges from the full launch
        // extend into the omitted game tail, so the added method has no such table.
        result.maxStack = original.maxStack;
        result.maxLocals = original.maxLocals;
        return result;
    }

    private static void validateControlFlow(AbstractInsnNode instruction, Map<LabelNode,LabelNode> labels) {
        int opcode = instruction.getOpcode();
        require(opcode != Opcodes.RETURN && opcode != Opcodes.IRETURN && opcode != Opcodes.LRETURN
                        && opcode != Opcodes.FRETURN && opcode != Opcodes.DRETURN && opcode != Opcodes.ARETURN
                        && opcode != Opcodes.JSR && opcode != Opcodes.RET,
                "Native launch prefix has an unexpected early exit");
        if (instruction instanceof JumpInsnNode jump) requireLabel(labels, jump.label);
        if (instruction instanceof TableSwitchInsnNode table) {
            requireLabel(labels, table.dflt); table.labels.forEach(label -> requireLabel(labels, label));
        }
        if (instruction instanceof LookupSwitchInsnNode table) {
            requireLabel(labels, table.dflt); table.labels.forEach(label -> requireLabel(labels, label));
        }
        if (instruction instanceof LineNumberNode line) requireLabel(labels, line.start);
        if (instruction instanceof FrameNode frame) {
            if (frame.local != null) for (Object value : frame.local) if (value instanceof LabelNode label) requireLabel(labels, label);
            if (frame.stack != null) for (Object value : frame.stack) if (value instanceof LabelNode label) requireLabel(labels, label);
        }
    }

    private static void requireLabel(Map<LabelNode,LabelNode> labels, LabelNode label) {
        require(labels.containsKey(label), "Native control flow leaves the extracted early launch");
    }
    private static int uniqueCall(AbstractInsnNode[] instructions, String owner, String method, String descriptor) {
        int index = -1;
        for (int i = 0; i < instructions.length; i++) if (isCall(instructions[i], owner, method, descriptor)) {
            require(index == -1, "Native call boundary is not unique: " + owner + "." + method); index = i;
        }
        require(index != -1, "Native call boundary is missing: " + owner + "." + method);
        return index;
    }
    private static boolean isCall(AbstractInsnNode instruction, String owner, String method, String descriptor) {
        return instruction instanceof MethodInsnNode call && owner.equals(call.owner) && method.equals(call.name) && descriptor.equals(call.desc);
    }
    private static long countCalls(AbstractInsnNode[] instructions, int boundary, String owner, String method) {
        return Arrays.stream(instructions, 0, boundary).filter(i -> i instanceof MethodInsnNode call && owner.equals(call.owner) && method.equals(call.name)).count();
    }
    private static int previousOpcode(AbstractInsnNode[] instructions, int start) {
        for (int i = start - 1; i >= 0; i--) if (instructions[i].getOpcode() >= 0) return i;
        throw new IllegalArgumentException("Native opcode boundary has no predecessor");
    }
    private static int nextOpcode(AbstractInsnNode[] instructions, int start) {
        for (int i = start + 1; i < instructions.length; i++) if (instructions[i].getOpcode() >= 0) return i;
        throw new IllegalArgumentException("Native opcode boundary has no successor");
    }
    private static int indexOf(AbstractInsnNode[] instructions, AbstractInsnNode target) {
        for (int i = 0; i < instructions.length; i++) if (instructions[i] == target) return i;
        throw new IllegalArgumentException("Native instruction boundary is absent");
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalArgumentException(message); }
    private static String digest(byte[] input) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(input)); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
