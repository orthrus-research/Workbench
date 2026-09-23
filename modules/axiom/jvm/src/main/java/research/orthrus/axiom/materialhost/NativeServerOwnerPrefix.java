package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Original server ownership assignment, stopping before config/Loader continuation. */
public final class NativeServerOwnerPrefix {
    public static final String TARGET = "net.minecraftforge.fml.server.FMLServerHandler";
    public static final String METHOD = "axiom$bindServerOwner";
    public static final String DESCRIPTOR = "(Lnet/minecraft/server/MinecraftServer;)V";
    public static final String ORIGINAL_SHA256 = "71b644abebe4f94265ac2b00130cca0979e722d7a4b562a4491a38404afeab78";
    public static final String ORIGINAL_METHOD_SHA256 = "2f64ba2ff8678618b4537ac625e0e18b2a9cdc69f0808e7bb441952095f7a085";
    private static final String OWNER = TARGET.replace('.', '/');
    private NativeServerOwnerPrefix() {}

    public static byte[] apply(byte[] bytes) {
        require(bytes != null && ORIGINAL_SHA256.equals(digest(bytes)), "Original server handler artifact differs");
        return applyAfterNativeTransforms(bytes);
    }

    public static byte[] applyAfterNativeTransforms(byte[] bytes) {
        require(bytes != null, "Original server handler bytes are missing");
        var node = new ClassNode(); new ClassReader(bytes).accept(node, ClassReader.EXPAND_FRAMES);
        require(OWNER.equals(node.name), "Original server handler owner differs");
        var originals = node.methods.stream().filter(m -> m.name.equals("beginServerLoading") && m.desc.equals(DESCRIPTOR)).toList();
        require(originals.size() == 1 && originals.getFirst().access == Opcodes.ACC_PUBLIC,
                "Original server loading method differs");
        var original = originals.getFirst();
        var canonical = new ClassWriter(0);
        canonical.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC, OWNER, null, "java/lang/Object", null);
        original.accept(canonical); canonical.visitEnd();
        require(ORIGINAL_METHOD_SHA256.equals(digest(canonical.toByteArray())),
                "Original server loading method differs after native transformations");
        require(node.methods.stream().noneMatch(m -> m.name.equals(METHOD)), "Server ownership prefix was already installed");
        var code = Arrays.stream(original.instructions.toArray()).filter(i -> i.getOpcode() >= 0).toList();
        require(code.size() > 3 && code.get(0) instanceof VarInsnNode self && self.getOpcode() == Opcodes.ALOAD && self.var == 0
                && code.get(1) instanceof VarInsnNode server && server.getOpcode() == Opcodes.ALOAD && server.var == 1
                && code.get(2) instanceof FieldInsnNode field && field.getOpcode() == Opcodes.PUTFIELD
                && OWNER.equals(field.owner) && "server".equals(field.name) && "Lnet/minecraft/server/MinecraftServer;".equals(field.desc)
                && code.get(3) instanceof MethodInsnNode call && call.getOpcode() == Opcodes.INVOKESTATIC && !call.itf
                && "com/cleanroommc/kirino/KirinoCommonCore".equals(call.owner) && "configEvent".equals(call.name)
                && "()V".equals(call.desc) && original.tryCatchBlocks.isEmpty(), "Original server ownership boundary differs");
        var prefix = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_SYNTHETIC, METHOD, DESCRIPTOR, null, null);
        for (int i = 0; i < 3; i++) prefix.instructions.add(code.get(i).clone(new IdentityHashMap<>()));
        prefix.instructions.add(new InsnNode(Opcodes.RETURN)); prefix.maxStack = 2; prefix.maxLocals = 2;
        node.methods.add(prefix);
        var writer = new ClassWriter(0); node.accept(writer); return writer.toByteArray();
    }

    private static void require(boolean value, String message) { if (!value) throw new IllegalArgumentException(message); }
    private static String digest(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
