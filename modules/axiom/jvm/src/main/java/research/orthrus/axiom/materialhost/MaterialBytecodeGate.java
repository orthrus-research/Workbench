package research.orthrus.axiom.materialhost;

import org.codehaus.groovy.control.BytecodeProcessor;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;

/** Closed candidate bytecode vocabulary, followed by native-dispatch guards.
 * Qualification is in progress. Unrecognized code is refused, never executed as
 * an unrestricted fallback. Trusted native library bytecode is not rewritten.
 */
public final class MaterialBytecodeGate implements BytecodeProcessor {
    private static final String INDY="org/codehaus/groovy/vmplugin/v8/IndyInterface";
    private static final String DESCRIPTOR="(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/MethodType;Ljava/lang/String;I)Ljava/lang/invoke/CallSite;";
    private static final Set<String> META=Set.of("$getStaticMetaClass","getMetaClass","setMetaClass","$getLookup");
    private static final Set<String> BRIDGE_CALLS=Set.of(
        "invokeMethodOnCurrentN(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;",
        "invokeMethodN(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;",
        "getGroovyObjectProperty(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;)Ljava/lang/Object;",
        "getProperty(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;",
        "getField(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;",
        "setGroovyObjectProperty(Ljava/lang/Object;Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;)V",
        "setProperty(Ljava/lang/Object;Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)V",
        "setField(Ljava/lang/Object;Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)V",
        "despreadList([Ljava/lang/Object;[Ljava/lang/Object;[I)[Ljava/lang/Object;");
    @Override public byte[] processBytecode(String name,byte[] input) {
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        boolean compilerConstant=MaterialRecordBytecode.compilerConstant(node);
        if(!node.name.replace('/','.').equals(name)||!compilerConstant&&!MaterialCallGate.owns(name))throw deny(name,"class identity");
        if(!Set.of("java/lang/Object","groovy/lang/Script","groovy/lang/Closure").contains(node.superName)
                &&!MaterialRecordBytecode.isRecord(node)&&!MaterialTraitClasses.defining())throw deny(name,"superclass "+node.superName);
        if(!MaterialTraitClasses.acceptsHierarchy(node))
            throw deny(name,"interfaces "+node.interfaces);
        var fields=new ArrayList<MaterialCallGate.Member>();
        var methods=new ArrayList<MaterialCallGate.Member>();
        for(FieldNode field:node.fields)if(!field.name.startsWith("$")&&!field.name.equals("metaClass"))
            fields.add(new MaterialCallGate.Member(field.name,field.desc,field.access));
        for(MethodNode method:node.methods) {
            if((method.access&Opcodes.ACC_NATIVE)!=0)throw deny(name,"native method");
            if(!META.contains(method.name)&&!method.name.startsWith("$")&&!method.name.startsWith("this$dist$")&&!constantInitializer(method))
                methods.add(new MaterialCallGate.Member(method.name,method.desc,method.access));
            for(AbstractInsnNode instruction:method.instructions) {
                if(instruction instanceof InvokeDynamicInsnNode call) {
                    if(compilerConstant&&method.name.equals("eval"))continue; // exact native enum metadata expression
                    if(MaterialRecordBytecode.bootstrap(node,method,call))continue; // retain the JVM's original bootstrap
                    if(call.bsm.getTag()!=Opcodes.H_INVOKESTATIC||!call.bsm.getOwner().equals(INDY)
                            ||!call.bsm.getName().equals("bootstrap")||!call.bsm.getDesc().equals(DESCRIPTOR)
                            ||call.bsmArgs.length!=2||!(call.bsmArgs[0] instanceof String)||!(call.bsmArgs[1] instanceof Integer))
                        throw deny(name,"unrecognized bootstrap");
                    if(MaterialTraitClasses.nativeInitialization(node,method,call))continue;
                    call.bsm=new Handle(Opcodes.H_INVOKESTATIC,MaterialCallGate.class.getName().replace('.','/'),"bootstrap",DESCRIPTOR,false);
                } else if(instruction instanceof MethodInsnNode call) {
                    if(MaterialTraitClasses.delegateCall(node,call)) {
                        call.owner=MaterialCallGate.class.getName().replace('.','/');call.name="invokeDelegate";
                    } else if(call.getOpcode()==Opcodes.INVOKEVIRTUAL&&!call.itf&&call.owner.equals("groovy/lang/Reference")
                            &&call.name.equals("set")&&call.desc.equals("(Ljava/lang/Object;)V")) {
                        call.setOpcode(Opcodes.INVOKESTATIC);call.owner=MaterialCallGate.class.getName().replace('.','/');
                        call.name="setReference";call.desc="(Lgroovy/lang/Reference;Ljava/lang/Object;)V";
                    } else if(call.getOpcode()==Opcodes.INVOKESTATIC&&!call.itf
                            &&call.owner.equals("org/codehaus/groovy/runtime/typehandling/DefaultTypeTransformation")
                            &&(call.name.equals("booleanUnbox")&&call.desc.equals("(Ljava/lang/Object;)Z")
                               ||call.name.equals("doubleUnbox")&&call.desc.equals("(Ljava/lang/Object;)D")
                               ||call.name.equals("floatUnbox")&&call.desc.equals("(Ljava/lang/Object;)F")
                               ||call.name.equals("shortUnbox")&&call.desc.equals("(Ljava/lang/Object;)S"))) {
                        // Original conversion helpers can invoke value callbacks.
                        // Apply the same cast admission as native indy conversion.
                        call.owner=MaterialCallGate.class.getName().replace('.','/');
                    } else if(call.getOpcode()==Opcodes.INVOKESTATIC&&!call.itf
                            &&call.owner.equals("org/codehaus/groovy/runtime/ScriptBytecodeAdapter")
                            &&call.name.equals("despreadList")
                            &&call.desc.equals("([Ljava/lang/Object;[Ljava/lang/Object;[I)[Ljava/lang/Object;")) {
                        // Ordinary source calls also use this original spread
                        // helper, not only generated Groovy bridge methods.
                        call.owner=MaterialCallGate.class.getName().replace('.','/');
                    } else if(call.getOpcode()==Opcodes.INVOKESTATIC&&!call.itf
                            &&call.owner.equals("org/codehaus/groovy/runtime/ScriptBytecodeAdapter")
                            &&Set.of("unaryMinus","unaryPlus").contains(call.name)
                            &&call.desc.equals("(Ljava/lang/Object;)Ljava/lang/Object;")) {
                        call.owner=MaterialCallGate.class.getName().replace('.','/');
                    } else if(call.getOpcode()==Opcodes.INVOKESTATIC&&!call.itf
                            &&call.owner.equals("org/codehaus/groovy/runtime/ScriptBytecodeAdapter")
                            &&call.name.equals("createRange")
                            &&call.desc.equals("(Ljava/lang/Object;Ljava/lang/Object;ZZ)Ljava/util/List;")) {
                        call.owner=MaterialCallGate.class.getName().replace('.','/');
                    } else if((compilerBridge(method)||Set.of("setProperty","setGroovyObjectProperty","setField","getField").contains(call.name))
                            &&call.getOpcode()==Opcodes.INVOKESTATIC
                            &&call.owner.equals("org/codehaus/groovy/runtime/ScriptBytecodeAdapter")
                            &&BRIDGE_CALLS.contains(call.name+call.desc))
                        call.owner=MaterialCallGate.class.getName().replace('.','/');
                    else if(!directCall(node,method,call)&&!MaterialRecordBytecode.directCall(node,method,call)
                            &&!MaterialTraitClasses.directCall(node,method,call))throw deny(name,method.name+": "+call.owner+"#"+call.name+call.desc);
                } else if(instruction instanceof FieldInsnNode field) {
                    boolean own=compilerConstant?field.owner.equals(node.name):MaterialCallGate.owns(field.owner.replace('/','.'));
                    boolean internal=field.name.startsWith("$")||field.name.equals("metaClass");
                    boolean compilerField=field.getOpcode()==Opcodes.GETSTATIC
                            &&MaterialCallGate.policy().compilerStaticField(field.owner+"#"+field.name+":"+field.desc);
                    if(!compilerField&&!constantField(node,method,field)&&!MaterialTraitClasses.internalField(node,field)
                            &&(!own||(internal&&!META.contains(method.name)&&!method.name.equals("<init>"))))
                        throw deny(name,"field "+field.owner+"#"+field.name);
                } else if(instruction instanceof TypeInsnNode type&&type.getOpcode()==Opcodes.NEW) {
                    if(!MaterialCallGate.owns(type.desc.replace('/','.'))&&!MaterialCallGate.policy().compilerAllocation(type.desc)
                            &&!MaterialRecordBytecode.allocation(node,method,type.desc))throw deny(name,"direct allocation "+type.desc);
                } else if(instruction instanceof LdcInsnNode value&&(value.cst instanceof Handle||value.cst instanceof ConstantDynamic))
                    throw deny(name,"unadmitted constant handle");
            }
            // Catching Throwable must not erase a JVM resource failure. Insert
            // after the existing handler frame/line pseudo-nodes, preserving the
            // exact throwable stack value and original exception-table ordering.
            // Inserting before FrameNode would move its stack-map frame away
            // from the handler entry and invalidate otherwise native bytecode.
            Set<LabelNode> handlers=new HashSet<>();
            for(TryCatchBlockNode block:method.tryCatchBlocks)if(handlers.add(block.handler)) {
                AbstractInsnNode position=block.handler;
                while(position.getNext()!=null&&position.getNext().getOpcode()<0)position=position.getNext();
                var observe=new InsnList();observe.add(new InsnNode(Opcodes.DUP));
                observe.add(new MethodInsnNode(Opcodes.INVOKESTATIC,MaterialCallGate.class.getName().replace('.','/'),
                        "caught","(Ljava/lang/Throwable;)V",false));
                method.instructions.insert(position,observe);
                method.maxStack=Math.max(method.maxStack,2);
            }
        }
        if(compilerConstant)MaterialCallGate.compilerConstant();
        else {
            var members=new MaterialCallGate.GuestMembers(fields,methods);
            if(!MaterialTraitClasses.defining())MaterialCallGate.register(name,members);
            MaterialTraitClasses.register(node,members,input);
        }
        var writer=new ClassWriter(0);node.accept(writer);byte[] output=writer.toByteArray();
        MaterialTraitClasses.compilation(node,input,output);
        NativeRecipeFunctionObservations.sourceCompilation(node,input,output);
        if(MaterialRecordBytecode.isRecord(node))MaterialCallGate.recordCompilation(name,node.version,
            node.recordComponents.stream().map(c->Map.<String,Object>of("name",c.name,"descriptor",c.descriptor)).toList(),input,output);
        return output;
    }
    private static boolean compilerBridge(MethodNode method) {
        // Groovy 4 InnerClassCompletionVisitor's native synthetic dispatchers.
        // Keep their bodies; guard the actual adapter call without emulating it.
        if(method.access!=(Opcodes.ACC_PUBLIC|Opcodes.ACC_SYNTHETIC)
                &&method.access!=(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC|Opcodes.ACC_SYNTHETIC))return false;
        return switch(method.desc) {
            case "(Ljava/lang/String;Ljava/lang/Object;)Ljava/lang/Object;" ->
                method.name.matches("this\\$dist\\$invoke\\$[1-9][0-9]*")||Set.of("methodMissing","$static_methodMissing").contains(method.name);
            case "(Ljava/lang/String;Ljava/lang/Object;)V" ->
                method.name.matches("this\\$dist\\$set\\$[1-9][0-9]*")||Set.of("propertyMissing","$static_propertyMissing").contains(method.name);
            case "(Ljava/lang/String;)Ljava/lang/Object;" ->
                method.name.matches("this\\$dist\\$get\\$[1-9][0-9]*")||Set.of("propertyMissing","$static_propertyMissing").contains(method.name);
            default -> false;
        };
    }
    private static boolean directCall(ClassNode owner,MethodNode method,MethodInsnNode call) {
        if(defaultArgumentBridge(owner,method,call))return true;
        // A nested native closure captures its enclosing closure's thisObject.
        if(owner.superName.equals("groovy/lang/Closure")&&method.name.equals("doCall")
                &&call.getOpcode()==Opcodes.INVOKEVIRTUAL&&call.owner.equals("groovy/lang/Closure")
                &&call.name.equals("getThisObject")&&call.desc.equals("()Ljava/lang/Object;"))return true;
        String signature=call.owner+"#"+call.name+call.desc;
        if(owner.superName.equals("groovy/lang/Closure")&&method.name.equals("doCall")&&call.getOpcode()==Opcodes.INVOKEVIRTUAL
                &&call.owner.equals(owner.name)&&call.name.equals("doCall")
                &&owner.methods.stream().anyMatch(m->m.name.equals(call.name)&&m.desc.equals(call.desc)))return true;
        if(method.name.equals("<clinit>")&&call.getOpcode()==Opcodes.INVOKESTATIC
                &&call.owner.equals(owner.name)&&call.name.equals("__$swapInit")&&call.desc.equals("()V")
                &&owner.methods.stream().anyMatch(MaterialBytecodeGate::constantInitializer))return true;
        if(call.getOpcode()==Opcodes.INVOKESTATIC&&MaterialCallGate.policy().staticCall(signature))return true;
        if((call.getOpcode()==Opcodes.INVOKEVIRTUAL||call.getOpcode()==Opcodes.INVOKEINTERFACE)
                &&MaterialCallGate.policy().virtualCall(signature))return true;
        if(call.name.equals("<init>")&&call.getOpcode()==Opcodes.INVOKESPECIAL) {
            if(MaterialCallGate.policy().compilerConstructor(signature))return true;
            if(MaterialCallGate.owns(call.owner.replace('/','.')))return true;
            return method.name.equals("<init>")&&Set.of("java/lang/Object","groovy/lang/Script","groovy/lang/Closure").contains(call.owner);
        }
        if(META.contains(method.name))return switch(call.owner+"#"+call.name+call.desc) {
            case "java/lang/Object#getClass()Ljava/lang/Class;",
                 "org/codehaus/groovy/runtime/ScriptBytecodeAdapter#initMetaClass(Ljava/lang/Object;)Lgroovy/lang/MetaClass;",
                 "org/codehaus/groovy/reflection/ClassInfo#getClassInfo(Ljava/lang/Class;)Lorg/codehaus/groovy/reflection/ClassInfo;",
                 "org/codehaus/groovy/reflection/ClassInfo#getMetaClass()Lgroovy/lang/MetaClass;",
                 "java/lang/invoke/MethodHandles#lookup()Ljava/lang/invoke/MethodHandles$Lookup;" -> true;
            default -> call.owner.equals(owner.name)&&call.name.equals("$getStaticMetaClass");
        };
        return method.name.equals("<init>")&&call.owner.equals(owner.name)&&call.name.equals("$getStaticMetaClass")
                &&call.desc.equals("()Lgroovy/lang/MetaClass;");
    }
    private static boolean defaultArgumentBridge(ClassNode owner,MethodNode method,MethodInsnNode call) {
        // Groovy Verifier emits an annotated overload which calls the fuller
        // method on the same candidate class. Its target body is guarded too.
        if(call.itf||!call.owner.equals(owner.name)||!call.name.equals(method.name)
                ||method.visibleAnnotations==null||method.visibleAnnotations.stream().noneMatch(a->a.desc.equals("Lgroovy/transform/Generated;")))return false;
        boolean isStatic=(method.access&Opcodes.ACC_STATIC)!=0;
        if(call.getOpcode()!=(isStatic?Opcodes.INVOKESTATIC:Opcodes.INVOKEVIRTUAL))return false;
        Type from=Type.getMethodType(method.desc),to=Type.getMethodType(call.desc);
        if(!from.getReturnType().equals(to.getReturnType())||from.getArgumentTypes().length>=to.getArgumentTypes().length)return false;
        return owner.methods.stream().anyMatch(target->target.name.equals(call.name)&&target.desc.equals(call.desc)
                &&(target.access&(Opcodes.ACC_NATIVE|Opcodes.ACC_ABSTRACT))==0
                &&((target.access&Opcodes.ACC_STATIC)!=0)==isStatic);
    }
    private static boolean constantInitializer(MethodNode method) {
        return method.name.equals("__$swapInit")&&method.desc.equals("()V")
                &&method.access==(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC|Opcodes.ACC_SYNTHETIC);
    }
    private static boolean constantField(ClassNode owner,MethodNode method,FieldInsnNode access) {
        // Groovy 4's literal hoisting: only the declaring class's synthetic
        // numeric slots, with writes confined to its original initializer.
        if(!access.owner.equals(owner.name)||!access.name.matches("\\$const\\$[0-9]+")
                ||!Set.of("J","F","D","Ljava/math/BigInteger;","Ljava/math/BigDecimal;").contains(access.desc))return false;
        boolean declared=owner.fields.stream().anyMatch(field->field.name.equals(access.name)&&field.desc.equals(access.desc)
                &&field.access==(Opcodes.ACC_PRIVATE|Opcodes.ACC_STATIC|Opcodes.ACC_SYNTHETIC));
        return declared&&(access.getOpcode()==Opcodes.GETSTATIC
                ||access.getOpcode()==Opcodes.PUTSTATIC&&constantInitializer(method));
    }
    private static UnsupportedOperationException deny(String name,String detail) {return MaterialCallGate.reject("candidate.bytecode",name+": "+detail);}
}
