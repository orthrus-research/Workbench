package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Extracts closed, unchanged native initialization prefixes into their original owner.
 * The separate environment and root-registration seams never replace handleLaunch
 * or enter discovery. Each is an alternative entry for a fresh worker.
 */
public final class NativeCoremodPrefix {
    public static final String TARGET = "net.minecraftforge.fml.relauncher.CoreModManager";
    public static final String METHOD = "axiom$initializeCoremodEnvironment";
    public static final String ROOT_METHOD = "axiom$initializeRootCoremods";
    public static final String DESCRIPTOR = "(Ljava/io/File;Lnet/minecraft/launchwrapper/LaunchClassLoader;Lnet/minecraftforge/fml/common/launcher/FMLTweaker;)V";
    public static final String ORIGINAL_SHA256 = "89e20ba1a6592f695a0f7d1c0513cff24ffc07feed09b41acb4bd9d888dd5d30";
    private static final String OWNER = TARGET.replace('.', '/');
    private NativeCoremodPrefix() {}

    public static byte[] apply(byte[] input) {
        return apply(input, false);
    }

    /** Independent fresh-worker seam; never invoke after the environment-only seam. */
    public static byte[] applyRoots(byte[] input) {
        return apply(input, true);
    }

    private static byte[] apply(byte[] input, boolean roots) {
        if (input == null || !ORIGINAL_SHA256.equals(digest(input)))
            throw new IllegalArgumentException("Native CoreModManager differs from the source-bound original artifact");
        var node = new ClassNode();
        new ClassReader(input).accept(node, 0);
        node.methods.add(roots ? extractRoots(node) : extract(node));
        var writer = new ClassWriter(0);
        node.accept(writer);
        return writer.toByteArray();
    }

    // Package access permits structural tests using synthetic bytecode. Runtime
    // admission always enters apply(), which requires the exact original digest.
    static MethodNode extract(ClassNode node) {
        if (!OWNER.equals(node.name) || node.methods.stream().anyMatch(m -> METHOD.equals(m.name) || ROOT_METHOD.equals(m.name)))
            throw new IllegalArgumentException("Native coremod owner or extraction state differs");
        var matches = node.methods.stream().filter(m -> "handleLaunch".equals(m.name) && DESCRIPTOR.equals(m.desc)).toList();
        if (matches.size() != 1 || (matches.getFirst().access & Opcodes.ACC_STATIC) == 0)
            throw new IllegalArgumentException("Native handleLaunch descriptor differs");
        MethodNode original = matches.getFirst();
        var instructions = original.instructions.toArray();
        int end = -1, writes = 0;
        for (int i = 0; i < instructions.length; i++) {
            if (instructions[i] instanceof FieldInsnNode field && field.getOpcode() == Opcodes.PUTSTATIC
                    && OWNER.equals(field.owner) && "loadPlugins".equals(field.name)) {
                if (!"Ljava/util/List;".equals(field.desc))
                    throw new IllegalArgumentException("Native plugin-list type differs");
                end = i; writes++;
            }
        }
        if (writes != 1) throw new IllegalArgumentException("Native plugin-list boundary is not unique");
        var included = Collections.newSetFromMap(new IdentityHashMap<AbstractInsnNode,Boolean>());
        var labels = new IdentityHashMap<LabelNode,LabelNode>();
        var fields = new ArrayList<String>();
        var calls = new ArrayList<String>();
        var constants = new ArrayList<Object>();
        for (int i = 0; i <= end; i++) {
            var instruction = instructions[i]; included.add(instruction);
            if (instruction instanceof LabelNode label) labels.put(label, new LabelNode());
            if (instruction instanceof FieldInsnNode field && field.getOpcode() == Opcodes.PUTSTATIC)
                fields.add(field.owner + "." + field.name + ":" + field.desc);
            if (instruction instanceof MethodInsnNode call) calls.add(call.owner + "." + call.name + call.desc);
            if (instruction instanceof LdcInsnNode constant) constants.add(constant.cst);
            if (instruction.getOpcode() == Opcodes.RETURN || instruction.getOpcode() == Opcodes.JSR
                    || instruction.getOpcode() == Opcodes.RET)
                throw new IllegalArgumentException("Native prefix has an unexpected early exit");
        }
        if (!fields.equals(List.of(OWNER + ".mcDir:Ljava/io/File;",
                OWNER + ".tweaker:Lnet/minecraftforge/fml/common/launcher/FMLTweaker;",
                OWNER + ".deobfuscatedEnvironment:Z", OWNER + ".loadPlugins:Ljava/util/List;"))
                || Collections.frequency(calls, "com/cleanroommc/common/CleanroomEnvironment.isDev()Z") != 1
                || Collections.frequency(calls, "net/minecraftforge/fml/common/launcher/FMLTweaker.injectCascadingTweak(Ljava/lang/String;)V") != 2
                || Collections.frequency(calls, "net/minecraft/launchwrapper/LaunchClassLoader.registerTransformer(Ljava/lang/String;)V") != 1
                || !constants.containsAll(List.of("net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker",
                        "org.spongepowered.asm.launch.MixinTweaker", "net.minecraftforge.fml.common.asm.transformers.PatchingTransformer")))
            throw new IllegalArgumentException("Native environment initialization shape differs");
        return copy(original, end, METHOD);
    }

    static MethodNode extractRoots(ClassNode node) {
        // Retain the environment-only shape checks, without executing that seam.
        extract(node);
        var original = node.methods.stream().filter(m -> "handleLaunch".equals(m.name)
                && DESCRIPTOR.equals(m.desc)).findFirst().orElseThrow();
        var instructions = original.instructions.toArray();
        int end = -1, boundaries = 0, rootReads = 0, locations = 0, registrations = 0, emptyChecks = 0;
        for (int i = 0; i < instructions.length; i++) {
            var instruction = instructions[i];
            if (instruction instanceof LdcInsnNode constant && "All fundamental core mods are successfully located".equals(constant.cst)) {
                int next = i + 1;
                while (next < instructions.length && instructions[next].getOpcode() < 0) next++;
                if (next >= instructions.length || !(instructions[next] instanceof MethodInsnNode call)
                        || !"org/apache/logging/log4j/Logger".equals(call.owner) || !"debug".equals(call.name)
                        || !"(Ljava/lang/String;)V".equals(call.desc))
                    throw new IllegalArgumentException("Native root completion boundary differs");
                end = next; boundaries++;
            }
        }
        if (boundaries != 1) throw new IllegalArgumentException("Native root completion boundary is not unique");
        for (int i = 0; i <= end; i++) {
            var instruction = instructions[i];
            if (instruction instanceof FieldInsnNode field && OWNER.equals(field.owner)
                    && "rootPlugins".equals(field.name) && "[Ljava/lang/String;".equals(field.desc)
                    && field.getOpcode() == Opcodes.GETSTATIC) rootReads++;
            if (instruction instanceof MethodInsnNode call) {
                if ("net/minecraftforge/fml/common/launcher/FMLTweaker".equals(call.owner)
                        && "getJarLocation".equals(call.name) && "()Ljava/net/URI;".equals(call.desc)) locations++;
                if (OWNER.equals(call.owner) && "loadCoreMod".equals(call.name)
                        && "(Lnet/minecraft/launchwrapper/LaunchClassLoader;Ljava/lang/String;Ljava/io/File;)Lnet/minecraftforge/fml/relauncher/CoreModManager$FMLPluginWrapper;".equals(call.desc)) registrations++;
                if ("java/util/List".equals(call.owner) && "isEmpty".equals(call.name) && "()Z".equals(call.desc)) emptyChecks++;
                if (call.owner.contains("CleanroomModDiscoverer"))
                    throw new IllegalArgumentException("Native root seam reaches broad discovery");
            }
        }
        if (rootReads != 1 || locations != 1 || registrations != 1 || emptyChecks != 1)
            throw new IllegalArgumentException("Native original root registration shape differs");
        return copy(original, end, ROOT_METHOD);
    }

    private static MethodNode copy(MethodNode original, int end, String method) {
        var instructions = original.instructions.toArray();
        var included = Collections.newSetFromMap(new IdentityHashMap<AbstractInsnNode,Boolean>());
        var labels = new IdentityHashMap<LabelNode,LabelNode>();
        for (int i = 0; i <= end; i++) {
            included.add(instructions[i]);
            if (instructions[i] instanceof LabelNode label) labels.put(label, new LabelNode());
            if (instructions[i].getOpcode() == Opcodes.RETURN || instructions[i].getOpcode() == Opcodes.JSR
                    || instructions[i].getOpcode() == Opcodes.RET)
                throw new IllegalArgumentException("Native prefix has an unexpected early exit");
        }
        var extracted = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC | Opcodes.ACC_SYNTHETIC,
                method, DESCRIPTOR, null, null);
        for (int i = 0; i <= end; i++) {
            var instruction = instructions[i];
            if (instruction instanceof JumpInsnNode jump) requireLabel(labels, jump.label);
            if (instruction instanceof TableSwitchInsnNode table) {
                requireLabel(labels, table.dflt); table.labels.forEach(label -> requireLabel(labels, label));
            }
            if (instruction instanceof LookupSwitchInsnNode table) {
                requireLabel(labels, table.dflt); table.labels.forEach(label -> requireLabel(labels, label));
            }
            extracted.instructions.add(instruction.clone(labels));
        }
        for (TryCatchBlockNode block : original.tryCatchBlocks) {
            boolean start = included.contains(block.start), finish = included.contains(block.end), handler = included.contains(block.handler);
            if ((start || finish || handler) && !(start && finish && handler))
                throw new IllegalArgumentException("Native exception region crosses the extraction boundary");
            if (start) extracted.tryCatchBlocks.add(new TryCatchBlockNode(labels.get(block.start), labels.get(block.end), labels.get(block.handler), block.type));
        }
        extracted.instructions.add(new InsnNode(Opcodes.RETURN));
        extracted.maxStack = original.maxStack;
        extracted.maxLocals = original.maxLocals;
        return extracted;
    }

    private static void requireLabel(Map<LabelNode,LabelNode> labels, LabelNode label) {
        if (!labels.containsKey(label)) throw new IllegalArgumentException("Native control flow leaves the extracted prefix");
    }

    private static String digest(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
