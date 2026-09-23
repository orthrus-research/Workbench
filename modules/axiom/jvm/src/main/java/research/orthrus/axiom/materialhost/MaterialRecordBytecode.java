package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;

/** Recognizes native Groovy 4 record output; never synthesizes record behavior. */
final class MaterialRecordBytecode {
    private MaterialRecordBytecode() {}
    private static final Handle OBJECT_METHODS=new Handle(Opcodes.H_INVOKESTATIC,"java/lang/runtime/ObjectMethods","bootstrap",
        "(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/TypeDescriptor;Ljava/lang/Class;Ljava/lang/String;[Ljava/lang/invoke/MethodHandle;)Ljava/lang/Object;",false);
    private static final Set<String> COMPONENT_TYPES=Set.of("Ljava/lang/String;","I","Z","D");
    private static final Set<String> STATIC_CALLS=Set.of(
        "org/codehaus/groovy/runtime/typehandling/DefaultTypeTransformation#booleanUnbox(Ljava/lang/Object;)Z",
        "org/codehaus/groovy/runtime/typehandling/DefaultTypeTransformation#doubleUnbox(Ljava/lang/Object;)D",
        "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
        "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;",
        "org/codehaus/groovy/runtime/ScriptBytecodeAdapter#isCase(Ljava/lang/Object;Ljava/lang/Object;)Z");
    private static final Set<String> MAP_CALLS=Set.of(
        "java/util/Map#get(Ljava/lang/Object;)Ljava/lang/Object;",
        "java/util/Map#containsKey(Ljava/lang/Object;)Z",
        "java/util/Map#keySet()Ljava/util/Set;",
        "java/util/Set#iterator()Ljava/util/Iterator;",
        "java/util/Iterator#hasNext()Z","java/util/Iterator#next()Ljava/lang/Object;",
        "java/util/List#contains(Ljava/lang/Object;)Z");

    static boolean compilerConstant(ClassNode node) {
        // StaticTypeCheckingSupport.evaluateExpression compiles the RecordType
        // annotation collector's enum mode in a fresh Expression$UUID class.
        // Recognize that exact constant expression, not arbitrary helper names.
        if(!node.name.matches("Expression\\$[0-9a-f]{8}\\$[0-9a-f]{4}\\$[0-9a-f]{4}\\$[0-9a-f]{4}\\$[0-9a-f]{12}")
                ||!node.superName.equals("java/lang/Object")||node.access!=(Opcodes.ACC_PUBLIC|Opcodes.ACC_SUPER)
                ||!node.interfaces.equals(List.of("groovy/lang/GroovyObject")))return false;
        if(node.fields.size()!=3||!new HashSet<>(node.fields.stream().map(f->f.name+":"+f.desc+":"+f.access).toList()).equals(Set.of(
                "$staticClassInfo:Lorg/codehaus/groovy/reflection/ClassInfo;:4106","__$stMC:Z:4233","metaClass:Lgroovy/lang/MetaClass;:4226")))return false;
        if(node.methods.size()!=6||!new HashSet<>(node.methods.stream().map(m->m.name+m.desc+":"+m.access).toList()).equals(Set.of(
                "<init>()V:1","eval()Ljava/lang/Object;:9","$getStaticMetaClass()Lgroovy/lang/MetaClass;:4100",
                "getMetaClass()Lgroovy/lang/MetaClass;:4097","setMetaClass(Lgroovy/lang/MetaClass;)V:4097",
                "$getLookup()Ljava/lang/invoke/MethodHandles$Lookup;:4105")))return false;
        MethodNode eval=node.methods.stream().filter(m->m.name.equals("eval")).findFirst().orElseThrow();
        var instructions=Arrays.stream(eval.instructions.toArray()).filter(i->i.getOpcode()>=0).toList();
        return eval.tryCatchBlocks.isEmpty()&&instructions.size()==3
                &&instructions.get(0) instanceof LdcInsnNode literal
                &&literal.cst.equals(Type.getObjectType("groovy/transform/AnnotationCollectorMode"))
                &&instructions.get(1) instanceof InvokeDynamicInsnNode call&&call.name.equals("getProperty")
                &&call.desc.equals("(Ljava/lang/Class;)Ljava/lang/Object;")
                &&call.bsm.equals(new Handle(Opcodes.H_INVOKESTATIC,"org/codehaus/groovy/vmplugin/v8/IndyInterface","bootstrap",
                    "(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/MethodType;Ljava/lang/String;I)Ljava/lang/invoke/CallSite;",false))
                &&Arrays.equals(call.bsmArgs,new Object[]{"PREFER_EXPLICIT_MERGED",0})
                &&instructions.get(2).getOpcode()==Opcodes.ARETURN;
    }

    static boolean isRecord(ClassNode node) {
        if(!"java/lang/Record".equals(node.superName)||node.version<Opcodes.V16
                ||(node.access&(Opcodes.ACC_RECORD|Opcodes.ACC_FINAL))!=(Opcodes.ACC_RECORD|Opcodes.ACC_FINAL)
                ||node.recordComponents==null)return false;
        Set<String> names=new HashSet<>();
        for(RecordComponentNode component:node.recordComponents) {
            if(!COMPONENT_TYPES.contains(component.descriptor)||!names.add(component.name))return false;
            if(node.fields.stream().noneMatch(f->f.name.equals(component.name)&&f.desc.equals(component.descriptor)
                    &&f.access==(Opcodes.ACC_PRIVATE|Opcodes.ACC_FINAL)))return false;
            if(node.methods.stream().noneMatch(m->m.name.equals(component.name)&&m.desc.equals("()"+component.descriptor)
                    &&m.access==Opcodes.ACC_PUBLIC))return false;
        }
        return true;
    }

    static boolean bootstrap(ClassNode node,MethodNode method,InvokeDynamicInsnNode call) {
        if(!isRecord(node)||!call.bsm.equals(OBJECT_METHODS)||!call.name.equals(method.name)
                ||method.access!=(Opcodes.ACC_PUBLIC|Opcodes.ACC_FINAL))return false;
        String descriptor=switch(method.name) {
            case "toString" -> "()Ljava/lang/String;";
            case "equals" -> "(Ljava/lang/Object;)Z";
            case "hashCode" -> "()I";
            default -> "";
        };
        if(!method.desc.equals(descriptor)||!call.desc.equals("(L"+node.name+";"+descriptor.substring(1)))return false;
        var expected=new ArrayList<Object>();expected.add(Type.getObjectType(node.name));
        expected.add(String.join(";",node.recordComponents.stream().map(c->c.name).toList()));
        for(RecordComponentNode component:node.recordComponents)
            expected.add(new Handle(Opcodes.H_GETFIELD,node.name,component.name,component.descriptor,false));
        return Arrays.equals(expected.toArray(),call.bsmArgs);
    }

    static boolean directCall(ClassNode node,MethodNode method,MethodInsnNode call) {
        if(!isRecord(node))return false;
        boolean constructor=method.name.equals("<init>");
        boolean generated=Set.of("getAt(I)Ljava/lang/Object;","toList()Ljava/util/List;","toMap()Ljava/util/Map;")
            .contains(method.name+method.desc)&&method.access==(Opcodes.ACC_PUBLIC|Opcodes.ACC_FINAL);
        if(constructor&&call.getOpcode()==Opcodes.INVOKESPECIAL&&call.owner.equals("java/lang/Record")
                &&call.name.equals("<init>")&&call.desc.equals("()V"))return true;
        if(!constructor&&!generated)return false;
        String signature=call.owner+"#"+call.name+call.desc;
        // A one-component record's native named constructor resolves this(...)
        // dynamically. Keep Groovy's selector, restricted to this exact class.
        if(namedConstructor(method)&&call.getOpcode()==Opcodes.INVOKESTATIC
                &&signature.equals("org/codehaus/groovy/runtime/ScriptBytecodeAdapter#selectConstructorAndTransformArguments([Ljava/lang/Object;ILjava/lang/Class;)I")) {
            AbstractInsnNode previous=call.getPrevious();
            while(previous!=null&&previous.getOpcode()<0)previous=previous.getPrevious();
            return previous instanceof LdcInsnNode literal&&literal.cst.equals(Type.getObjectType(node.name));
        }
        if(call.getOpcode()==Opcodes.INVOKESTATIC&&STATIC_CALLS.contains(signature))return true;
        if(constructor&&method.desc.equals("(Ljava/util/Map;)V")&&call.getOpcode()==Opcodes.INVOKEINTERFACE
                &&MAP_CALLS.contains(signature))return true;
        if(call.getOpcode()==Opcodes.INVOKEVIRTUAL&&call.owner.equals(node.name)
                &&node.recordComponents.stream().anyMatch(c->c.name.equals(call.name)&&call.desc.equals("()"+c.descriptor)))return true;
        return (method.name.equals("getAt")||namedConstructor(method))&&call.getOpcode()==Opcodes.INVOKESPECIAL
                &&signature.equals("java/lang/IllegalArgumentException#<init>(Ljava/lang/String;)V");
    }

    static boolean allocation(ClassNode node,MethodNode method,String type) {
        return isRecord(node)&&(method.name.equals("getAt")&&method.desc.equals("(I)Ljava/lang/Object;")||namedConstructor(method))
                &&type.equals("java/lang/IllegalArgumentException");
    }
    private static boolean namedConstructor(MethodNode method) {
        return method.name.equals("<init>")&&method.desc.equals("(Ljava/util/Map;)V")&&method.access==Opcodes.ACC_PUBLIC;
    }
}
