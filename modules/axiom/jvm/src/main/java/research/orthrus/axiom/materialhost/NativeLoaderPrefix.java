package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Adds the complete original Loader selection prefix, ending before CONSTRUCTING. */
public final class NativeLoaderPrefix {
    public static final String TARGET = "net.minecraftforge.fml.common.Loader";
    public static final String METHOD = "axiom$selectMods";
    public static final String CONSTRUCTION_METHOD = "axiom$constructMods";
    public static final String DESCRIPTOR = "(Ljava/util/List;)V";
    public static final String ORIGINAL_SHA256 = "a72e35e80e6abaa5b9f52cabbd1f641839bd144277ff52e6367e8cfaea32d30c";
    public static final String ORIGINAL_METHOD_SHA256 = "c465f0c60777828936858d02f15c94e5fc6c82850d9458a4f7572ab06f4fe35a";
    private static final String OWNER = TARGET.replace('.', '/');
    private static final String CONTROLLER = "net/minecraftforge/fml/common/LoadController";
    private static final String STATE = "net/minecraftforge/fml/common/LoaderState";
    private NativeLoaderPrefix() {}

    public static byte[] apply(byte[] input) {
        if (input == null || !ORIGINAL_SHA256.equals(digest(input)))
            throw new IllegalArgumentException("Native Loader differs from the source-bound original artifact");
        return append(input);
    }

    /** Foundation invokes explicit transformers after the ordinary native chain. */
    public static byte[] applyAfterNativeTransforms(byte[] input) {
        if (input == null || !ORIGINAL_METHOD_SHA256.equals(methodDigest(input)))
            throw new IllegalArgumentException("Native Loader selection method differs after native transformations");
        return append(input);
    }

    static String methodDigest(byte[] input) {
        var node = new ClassNode();
        new ClassReader(input).accept(node, ClassReader.EXPAND_FRAMES);
        require(OWNER.equals(node.name), "Native Loader method owner differs");
        var methods = node.methods.stream().filter(m -> "loadMods".equals(m.name) && DESCRIPTOR.equals(m.desc)).toList();
        require(methods.size() == 1, "Native Loader selection method is not unique");
        var writer = new ClassWriter(0);
        writer.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC, OWNER, null, "java/lang/Object", null);
        methods.getFirst().accept(writer); writer.visitEnd();
        return digest(writer.toByteArray());
    }

    private static byte[] append(byte[] input) {
        var node = new ClassNode();
        new ClassReader(input).accept(node, ClassReader.EXPAND_FRAMES);
        node.methods.add(extract(node));
        node.methods.add(extractConstruction(node));
        // Keep existing frames; resolving common superclasses here would define
        // native classes before the original bootstrap has installed transformers.
        var writer = new ClassWriter(0);
        node.accept(writer);
        return writer.toByteArray();
    }

    static MethodNode extract(ClassNode node) {
        require(OWNER.equals(node.name) && node.methods.stream().noneMatch(m -> METHOD.equals(m.name)),
                "Native Loader owner or extraction state differs");
        var methods = node.methods.stream().filter(m -> "loadMods".equals(m.name) && DESCRIPTOR.equals(m.desc)).toList();
        require(methods.size() == 1 && methods.getFirst().access == Opcodes.ACC_PUBLIC,
                "Native Loader loadMods descriptor or access differs");
        var original = methods.getFirst();
        var instructions = original.instructions.toArray();
        int late = uniqueCall(instructions, "com/cleanroommc/cleanmix/CleanMixHooks", "loadMixinBooterLateMixins",
                "(Lnet/minecraftforge/fml/common/discovery/ASMDataTable;)V");
        int boundary = nextOpcode(instructions, late);
        int controller = nextOpcode(instructions, boundary);
        int state = nextOpcode(instructions, controller);
        int force = nextOpcode(instructions, state);
        int transition = nextOpcode(instructions, force);
        require(instructions[boundary] instanceof VarInsnNode self && self.getOpcode() == Opcodes.ALOAD && self.var == 0
                        && instructions[controller] instanceof FieldInsnNode field && field.getOpcode() == Opcodes.GETFIELD
                        && OWNER.equals(field.owner) && "modController".equals(field.name)
                        && ("L" + CONTROLLER + ";").equals(field.desc)
                        && instructions[state] instanceof FieldInsnNode phase && phase.getOpcode() == Opcodes.GETSTATIC
                        && STATE.equals(phase.owner) && "CONSTRUCTING".equals(phase.name) && ("L" + STATE + ";").equals(phase.desc)
                        && instructions[force].getOpcode() == Opcodes.ICONST_0
                        && isCall(instructions[transition], CONTROLLER, "transition", "(L" + STATE + ";Z)V"),
                "Native late-mixin to construction boundary differs");
        var expected = List.of("initializeLoader", "identifyDuplicates", "disableRequestedMods", "sortModList");
        for (String name : expected)
            require(countCalls(instructions, boundary, OWNER, name) == 1, "Native selection prerequisite differs: " + name);
        for (String name : List.of("addBuiltInModContainers", "identifyMods", "getASMTable"))
            require(countCalls(instructions, boundary, "com/cleanroommc/discovery/CleanroomModDiscoverer", name) == 1,
                    "Native discovery prerequisite differs: " + name);
        require(countCalls(instructions, boundary, "net/minecraftforge/fml/common/ModAPIManager", "manageAPI") == 1
                        && countCalls(instructions, boundary, "net/minecraftforge/fml/common/ModAPIManager", "cleanupAPIContainers") == 2
                        && countCalls(instructions, boundary, "net/minecraftforge/common/config/ConfigManager", "loadData") == 1
                        && countCalls(instructions, boundary, CONTROLLER, "transition") == 1
                        && countCalls(instructions, boundary, CONTROLLER, "distributeStateMessage") == 1,
                "Native API, configuration or loading-event sequence differs");
        int loading = uniqueCall(Arrays.copyOf(instructions, boundary), CONTROLLER, "distributeStateMessage", "(Ljava/lang/Class;)V");
        require(instructions[previousOpcode(instructions, loading)] instanceof LdcInsnNode event
                        && Type.getObjectType("net/minecraftforge/fml/common/event/FMLLoadEvent").equals(event.cst),
                "Native selection must dispatch only the original FMLLoadEvent");

        var labels = new IdentityHashMap<LabelNode,LabelNode>();
        var included = Collections.newSetFromMap(new IdentityHashMap<AbstractInsnNode,Boolean>());
        for (int i = 0; i < boundary; i++) {
            included.add(instructions[i]);
            if (instructions[i] instanceof LabelNode label) labels.put(label, new LabelNode());
        }
        var result = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC,
                METHOD, DESCRIPTOR, null, original.exceptions.toArray(String[]::new));
        for (int i = 0; i < boundary; i++) {
            var instruction = instructions[i];
            int opcode = instruction.getOpcode();
            require(opcode < Opcodes.IRETURN || opcode > Opcodes.RETURN, "Native selection prefix has an unexpected return");
            require(opcode != Opcodes.JSR && opcode != Opcodes.RET, "Native selection prefix has an obsolete branch");
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
            result.instructions.add(instruction.clone(labels));
        }
        for (var block : original.tryCatchBlocks) {
            boolean start = included.contains(block.start), end = included.contains(block.end), handler = included.contains(block.handler);
            require(!(start || end || handler) || start && end && handler,
                    "Native Loader exception region crosses the selection boundary");
            if (start) result.tryCatchBlocks.add(new TryCatchBlockNode(labels.get(block.start), labels.get(block.end),
                    labels.get(block.handler), block.type));
        }
        result.instructions.add(new InsnNode(Opcodes.RETURN));
        result.maxStack = original.maxStack; result.maxLocals = original.maxLocals;
        return result;
    }

    static MethodNode extractConstruction(ClassNode node) {
        require(OWNER.equals(node.name) && node.methods.stream().noneMatch(m -> CONSTRUCTION_METHOD.equals(m.name)),
                "Native Loader construction extraction state differs");
        var originals = node.methods.stream().filter(m -> "loadMods".equals(m.name) && DESCRIPTOR.equals(m.desc)).toList();
        require(originals.size() == 1, "Original construction method is not unique");
        var original = originals.getFirst();
        var result = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC,
                CONSTRUCTION_METHOD, DESCRIPTOR, original.signature, original.exceptions.toArray(String[]::new));
        // Copy the whole original method, including its live local variables and
        // frames. Observation at the selection boundary does not replay the
        // prefix or reconstruct the native construction-event arguments.
        original.accept(result);
        var instructions = result.instructions.toArray();
        int late = uniqueCall(instructions, "com/cleanroommc/cleanmix/CleanMixHooks", "loadMixinBooterLateMixins",
                "(Lnet/minecraftforge/fml/common/discovery/ASMDataTable;)V");
        int dispatch = uniqueCall(instructions, CONTROLLER, "distributeStateMessage", "(L" + STATE + ";[Ljava/lang/Object;)V");
        require(dispatch > late && Arrays.stream(instructions, dispatch, instructions.length).anyMatch(i ->
                i instanceof FieldInsnNode field && STATE.equals(field.owner) && "PREINITIALIZATION".equals(field.name)),
                "Native construction does not end at the original preInit boundary");
        String observer = "research/orthrus/axiom/materialhost/NativeConstructionClassSpace";
        result.instructions.insert(instructions[late], new MethodInsnNode(Opcodes.INVOKESTATIC,
                observer, "beforeConstruction", "()V", false));
        result.instructions.insertBefore(instructions[dispatch], new MethodInsnNode(Opcodes.INVOKESTATIC,
                observer, "constructionDispatchStarted", "()V", false));
        return result;
    }

    private static long countCalls(AbstractInsnNode[] instructions, int end, String owner, String name) {
        return Arrays.stream(instructions, 0, end).filter(i -> i instanceof MethodInsnNode call
                && owner.equals(call.owner) && name.equals(call.name)).count();
    }
    private static int uniqueCall(AbstractInsnNode[] instructions, String owner, String name, String descriptor) {
        int index = -1;
        for (int i = 0; i < instructions.length; i++) if (isCall(instructions[i], owner, name, descriptor)) {
            require(index == -1, "Native Loader call is not unique: " + name); index = i;
        }
        require(index != -1, "Native Loader call is absent: " + name); return index;
    }
    private static boolean isCall(AbstractInsnNode instruction, String owner, String name, String descriptor) {
        return instruction instanceof MethodInsnNode call && owner.equals(call.owner)
                && name.equals(call.name) && descriptor.equals(call.desc);
    }
    private static int nextOpcode(AbstractInsnNode[] instructions, int start) {
        for (int i = start + 1; i < instructions.length; i++) if (instructions[i].getOpcode() >= 0) return i;
        throw new IllegalArgumentException("Native Loader boundary has no successor");
    }
    private static int previousOpcode(AbstractInsnNode[] instructions, int start) {
        for (int i = start - 1; i >= 0; i--) if (instructions[i].getOpcode() >= 0) return i;
        throw new IllegalArgumentException("Native Loader boundary has no predecessor");
    }
    private static void requireLabel(Map<LabelNode,LabelNode> labels, LabelNode label) {
        require(labels.containsKey(label), "Native control flow leaves the extracted Loader selection");
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalArgumentException(message); }
    private static String digest(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
