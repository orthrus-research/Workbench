package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Adds a definition callback to the pinned native proxy loader, not its generator. */
public final class MaterialTraitLoaderHook {
    private MaterialTraitLoaderHook() {}
    public static final String TARGET="org.codehaus.groovy.runtime.ProxyGeneratorAdapter$InnerLoader";
    /** Evidence only. Native ProxyGeneratorAdapter.visitClass uses reflection's
     * method order. Sort declarations when comparing fresh workers, preserving
     * every method body, descriptor, attribute and the ordered interface list.
     * This byte array is never used to define or execute a class. */
    public static String methodOrderIndependentSha256(byte[] input) {
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        node.methods.sort(java.util.Comparator.comparing((MethodNode m)->m.name).thenComparing(m->m.desc));
        var writer=new ClassWriter(0);node.accept(writer);
        try {return java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(writer.toByteArray()));}
        catch(java.security.NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
    }
    public static byte[] apply(byte[] input,String callback) {
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        if(!node.name.equals(TARGET.replace('.','/'))||!node.superName.equals("groovy/lang/GroovyClassLoader"))
            throw new IllegalArgumentException("Native trait loader identity differs");
        var methods=node.methods.stream().filter(m->m.name.equals("defineClass")
            &&m.desc.equals("(Ljava/lang/String;[B)Ljava/lang/Class;")).toList();
        if(methods.size()!=1)throw new IllegalArgumentException("Native trait definition method differs");
        var method=methods.getFirst();int definitions=0;
        for(var instruction:method.instructions)if(instruction instanceof MethodInsnNode call
                &&call.getOpcode()==Opcodes.INVOKESPECIAL&&call.owner.equals("groovy/lang/GroovyClassLoader")
                &&call.name.equals("defineClass")&&call.desc.equals("(Ljava/lang/String;[BII)Ljava/lang/Class;"))definitions++;
        if(definitions!=1)throw new IllegalArgumentException("Native trait definition route differs");
        var hook=new InsnList();hook.add(new VarInsnNode(Opcodes.ALOAD,0));hook.add(new VarInsnNode(Opcodes.ALOAD,1));
        hook.add(new VarInsnNode(Opcodes.ALOAD,2));
        hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,callback,"define",
            "(Ljava/lang/ClassLoader;Ljava/lang/String;[B)[B",false));
        hook.add(new VarInsnNode(Opcodes.ASTORE,2));method.instructions.insert(hook);
        method.maxStack=Math.max(method.maxStack,3);
        var writer=new ClassWriter(0);node.accept(writer);return writer.toByteArray();
    }
}
