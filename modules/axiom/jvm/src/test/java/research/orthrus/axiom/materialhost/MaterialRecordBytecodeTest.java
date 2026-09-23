package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Shape tests only; actual emitted records run in isolated native comparisons. */
class MaterialRecordBytecodeTest {
    private ClassNode record() {
        var node=new ClassNode();node.name="fixture/Reagent";node.superName="java/lang/Record";
        node.version=Opcodes.V25;node.access=Opcodes.ACC_PUBLIC|Opcodes.ACC_FINAL|Opcodes.ACC_RECORD;
        node.recordComponents=new ArrayList<>();
        for(var pair:List.of(new String[]{"name","Ljava/lang/String;"},new String[]{"amount","I"})) {
            node.recordComponents.add(new RecordComponentNode(pair[0],pair[1],null));
            node.fields.add(new FieldNode(Opcodes.ACC_PRIVATE|Opcodes.ACC_FINAL,pair[0],pair[1],null,null));
            node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC,pair[0],"()"+pair[1],null,null));
        }
        return node;
    }
    private InvokeDynamicInsnNode bootstrap(ClassNode node,String method,String desc) {
        return new InvokeDynamicInsnNode(method,"(L"+node.name+";"+desc.substring(1),
            new Handle(Opcodes.H_INVOKESTATIC,"java/lang/runtime/ObjectMethods","bootstrap",
                "(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/TypeDescriptor;Ljava/lang/Class;Ljava/lang/String;[Ljava/lang/invoke/MethodHandle;)Ljava/lang/Object;",false),
            Type.getObjectType(node.name),"name;amount",
            new Handle(Opcodes.H_GETFIELD,node.name,"name","Ljava/lang/String;",false),
            new Handle(Opcodes.H_GETFIELD,node.name,"amount","I",false));
    }
    @Test void bootstrapRequiresExactDeclaringRecordAndComponentHandles() {
        for(var pair:List.of(new String[]{"toString","()Ljava/lang/String;"},new String[]{"hashCode","()I"},new String[]{"equals","(Ljava/lang/Object;)Z"})) {
            var node=record();var method=new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_FINAL,pair[0],pair[1],null,null);
            var call=bootstrap(node,pair[0],pair[1]);Object[] original=call.bsmArgs.clone();Handle handle=call.bsm;
            assertTrue(MaterialRecordBytecode.bootstrap(node,method,call));
            assertArrayEquals(original,call.bsmArgs);assertSame(handle,call.bsm);
            call.bsmArgs[2]=new Handle(Opcodes.H_GETSTATIC,"java/lang/System","out","Ljava/io/PrintStream;",false);
            assertFalse(MaterialRecordBytecode.bootstrap(node,method,call));
            call.bsmArgs=original;call.bsmArgs[1]="amount;name";
            assertFalse(MaterialRecordBytecode.bootstrap(node,method,call));
        }
    }
    @Test void recordIdentityIsNotOnlyASuperclassName() {
        var node=record();assertTrue(MaterialRecordBytecode.isRecord(node));
        node.access &= ~Opcodes.ACC_RECORD;assertFalse(MaterialRecordBytecode.isRecord(node));
        node=record();node.fields.getFirst().access=Opcodes.ACC_PUBLIC;assertFalse(MaterialRecordBytecode.isRecord(node));
        node=record();node.methods.clear();assertFalse(MaterialRecordBytecode.isRecord(node));
        node=record();node.recordComponents.getFirst().descriptor="Ljava/lang/Object;";assertFalse(MaterialRecordBytecode.isRecord(node));
    }
    @Test void nativeRecordHelpersAreScopedToTheirGeneratedMethods() {
        var node=record();var ctor=new MethodNode(Opcodes.ACC_PUBLIC,"<init>","(Ljava/util/Map;)V",null,null);
        var call=new MethodInsnNode(Opcodes.INVOKEINTERFACE,"java/util/Map","get","(Ljava/lang/Object;)Ljava/lang/Object;",true);
        assertTrue(MaterialRecordBytecode.directCall(node,ctor,call));
        ctor.name="arbitrary";assertFalse(MaterialRecordBytecode.directCall(node,ctor,call));
        var method=new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_FINAL,"getAt","(I)Ljava/lang/Object;",null,null);
        assertTrue(MaterialRecordBytecode.allocation(node,method,"java/lang/IllegalArgumentException"));
        assertFalse(MaterialRecordBytecode.allocation(node,method,"java/lang/ProcessBuilder"));
        assertFalse(MaterialRecordBytecode.directCall(node,method,
            new MethodInsnNode(Opcodes.INVOKESTATIC,"java/lang/System","getProperties","()Ljava/util/Properties;",false)));
    }
    @Test void unnamedClassesDoNotBecomeCompilerConstantsByNameAlone() {
        var node=record();node.name="Expression$00000000$0000$0000$0000$000000000000";
        assertFalse(MaterialRecordBytecode.compilerConstant(node));
    }
    @Test void namedConstructorSelectorRetainsNativeDispatchOnlyToThisRecord() {
        var node=record();var ctor=new MethodNode(Opcodes.ACC_PUBLIC,"<init>","(Ljava/util/Map;)V",null,null);
        var owner=new LdcInsnNode(Type.getObjectType(node.name));ctor.instructions.add(owner);
        var call=new MethodInsnNode(Opcodes.INVOKESTATIC,"org/codehaus/groovy/runtime/ScriptBytecodeAdapter",
            "selectConstructorAndTransformArguments","([Ljava/lang/Object;ILjava/lang/Class;)I",false);
        ctor.instructions.add(call);
        assertTrue(MaterialRecordBytecode.directCall(node,ctor,call));
        assertTrue(MaterialRecordBytecode.allocation(node,ctor,"java/lang/IllegalArgumentException"));
        owner.cst=Type.getObjectType("other/Record");assertFalse(MaterialRecordBytecode.directCall(node,ctor,call));
        owner.cst=Type.getObjectType(node.name);ctor.name="arbitrary";
        assertFalse(MaterialRecordBytecode.directCall(node,ctor,call));
    }
    @Test void compilerMetadataHelperCanOnlyReadTheOriginalRecordCollectorMode() {
        var node=new ClassNode();node.name="Expression$00000000$0000$0000$0000$000000000000";
        node.superName="java/lang/Object";node.access=Opcodes.ACC_PUBLIC|Opcodes.ACC_SUPER;
        node.interfaces.add("groovy/lang/GroovyObject");
        node.fields.add(new FieldNode(4106,"$staticClassInfo","Lorg/codehaus/groovy/reflection/ClassInfo;",null,null));
        node.fields.add(new FieldNode(4233,"__$stMC","Z",null,null));
        node.fields.add(new FieldNode(4226,"metaClass","Lgroovy/lang/MetaClass;",null,null));
        node.methods.add(new MethodNode(1,"<init>","()V",null,null));
        var eval=new MethodNode(9,"eval","()Ljava/lang/Object;",null,null);node.methods.add(eval);
        node.methods.add(new MethodNode(4100,"$getStaticMetaClass","()Lgroovy/lang/MetaClass;",null,null));
        node.methods.add(new MethodNode(4097,"getMetaClass","()Lgroovy/lang/MetaClass;",null,null));
        node.methods.add(new MethodNode(4097,"setMetaClass","(Lgroovy/lang/MetaClass;)V",null,null));
        node.methods.add(new MethodNode(4105,"$getLookup","()Ljava/lang/invoke/MethodHandles$Lookup;",null,null));
        eval.instructions.add(new LdcInsnNode(Type.getObjectType("groovy/transform/AnnotationCollectorMode")));
        var call=new InvokeDynamicInsnNode("getProperty","(Ljava/lang/Class;)Ljava/lang/Object;",
            new Handle(Opcodes.H_INVOKESTATIC,"org/codehaus/groovy/vmplugin/v8/IndyInterface","bootstrap",
                "(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/MethodType;Ljava/lang/String;I)Ljava/lang/invoke/CallSite;",false),
            "PREFER_EXPLICIT_MERGED",0);
        eval.instructions.add(call);eval.instructions.add(new InsnNode(Opcodes.ARETURN));
        assertTrue(MaterialRecordBytecode.compilerConstant(node));
        call.bsmArgs[0]="classLoader";assertFalse(MaterialRecordBytecode.compilerConstant(node));
        call.bsmArgs[0]="PREFER_EXPLICIT_MERGED";
        eval.instructions.insert(new InsnNode(Opcodes.NOP));assertFalse(MaterialRecordBytecode.compilerConstant(node));
    }
}
