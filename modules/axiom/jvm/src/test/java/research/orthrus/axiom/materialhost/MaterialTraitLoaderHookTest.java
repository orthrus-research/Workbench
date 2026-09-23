package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialTraitLoaderHookTest {
    private byte[] loader(boolean definition) {
        var writer=new ClassWriter(0);
        writer.visit(Opcodes.V25,Opcodes.ACC_PUBLIC,MaterialTraitLoaderHook.TARGET.replace('.','/'),null,"groovy/lang/GroovyClassLoader",null);
        var method=writer.visitMethod(Opcodes.ACC_PUBLIC,"defineClass","(Ljava/lang/String;[B)Ljava/lang/Class;",null,null);
        method.visitCode();method.visitVarInsn(Opcodes.ALOAD,0);method.visitVarInsn(Opcodes.ALOAD,1);method.visitVarInsn(Opcodes.ALOAD,2);
        method.visitInsn(Opcodes.ICONST_0);method.visitVarInsn(Opcodes.ALOAD,2);method.visitInsn(Opcodes.ARRAYLENGTH);
        if(definition)method.visitMethodInsn(Opcodes.INVOKESPECIAL,"groovy/lang/GroovyClassLoader","defineClass","(Ljava/lang/String;[BII)Ljava/lang/Class;",false);
        method.visitInsn(Opcodes.ARETURN);method.visitMaxs(5,3);method.visitEnd();writer.visitEnd();return writer.toByteArray();
    }
    @Test void callbackIsBeforeTheUnchangedNativeDefineCall() {
        byte[] original=loader(true);byte[] copy=original.clone();
        var node=new ClassNode();new ClassReader(MaterialTraitLoaderHook.apply(original,"fixture/Observe")).accept(node,0);
        assertArrayEquals(copy,original);
        var instructions=node.methods.getFirst().instructions.toArray();
        assertEquals(Opcodes.ALOAD,instructions[0].getOpcode());
        var hook=assertInstanceOf(MethodInsnNode.class,instructions[3]);
        assertEquals("fixture/Observe",hook.owner);assertEquals("define",hook.name);
        assertEquals("(Ljava/lang/ClassLoader;Ljava/lang/String;[B)[B",hook.desc);
        assertEquals(Opcodes.ASTORE,instructions[4].getOpcode());
        assertEquals(1,java.util.Arrays.stream(instructions).filter(i->i instanceof MethodInsnNode c&&c.getOpcode()==Opcodes.INVOKESPECIAL).count());
    }
    @Test void changedNativeDefinitionRouteIsNotAssumedCompatible() {
        assertThrows(IllegalArgumentException.class,()->MaterialTraitLoaderHook.apply(loader(false),"fixture/Observe"));
    }
    @Test void evidenceNormalizationOnlyIgnoresMethodDeclarationOrder() {
        var node=new ClassNode();new ClassReader(loader(true)).accept(node,0);
        node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"other","()I",null,null));
        var original=new ClassWriter(0);node.accept(original);byte[] input=original.toByteArray(),copy=input.clone();
        String expected=MaterialTraitLoaderHook.methodOrderIndependentSha256(input);
        java.util.Collections.reverse(node.methods);
        var reordered=new ClassWriter(0);node.accept(reordered);
        assertEquals(expected,MaterialTraitLoaderHook.methodOrderIndependentSha256(reordered.toByteArray()));
        assertArrayEquals(copy,input);
        node.methods.getFirst().desc="()J";
        var changed=new ClassWriter(0);node.accept(changed);
        assertNotEquals(expected,MaterialTraitLoaderHook.methodOrderIndependentSha256(changed.toByteArray()));
        node.interfaces.add("fixture/First");node.interfaces.add("fixture/Last");
        var ordered=new ClassWriter(0);node.accept(ordered);java.util.Collections.reverse(node.interfaces);
        var reversed=new ClassWriter(0);node.accept(reversed);
        assertNotEquals(MaterialTraitLoaderHook.methodOrderIndependentSha256(ordered.toByteArray()),
            MaterialTraitLoaderHook.methodOrderIndependentSha256(reversed.toByteArray()));
    }
}
