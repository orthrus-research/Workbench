package research.orthrus.axiom.materialtest;

import org.codehaus.groovy.control.BytecodeProcessor;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Observation-only native compiler hook. No code rewriting or admission claim. */
public final class GroovyCandidateBytecode implements BytecodeProcessor {
    private final Map<String,Object> classes=new LinkedHashMap<>();
    public Map<String,Object> observations() { return Map.copyOf(classes); }
    @Override public byte[] processBytecode(String name,byte[] original) {
        var node=new ClassNode();new ClassReader(original).accept(node,0);
        if(!node.name.replace('/','.').equals(name))throw new IllegalArgumentException("Candidate class identity differs");
        var rows=new ArrayList<Map<String,Object>>();
        for(MethodNode method:node.methods) {
            int line=0;
            for(AbstractInsnNode instruction:method.instructions) {
                if(instruction instanceof LineNumberNode number) {line=number.line;continue;}
                var row=new LinkedHashMap<String,Object>();
                row.put("method",method.name+method.desc);row.put("opcode",instruction.getOpcode());
                if(line>0)row.put("line",line);
                if(instruction instanceof MethodInsnNode call) {
                    row.put("kind","method");row.put("owner",call.owner);row.put("name",call.name);row.put("descriptor",call.desc);
                } else if(instruction instanceof FieldInsnNode field) {
                    row.put("kind","field");row.put("owner",field.owner);row.put("name",field.name);row.put("descriptor",field.desc);
                } else if(instruction instanceof InvokeDynamicInsnNode call) {
                    row.put("kind","indy");row.put("name",call.name);row.put("descriptor",call.desc);
                    row.put("bootstrap",call.bsm.toString());row.put("arguments",Arrays.stream(call.bsmArgs).map(String::valueOf).toList());
                } else if(instruction instanceof TypeInsnNode type) {
                    row.put("kind","type");row.put("type",type.desc);
                } else if(instruction instanceof LdcInsnNode constant && (constant.cst instanceof Type || constant.cst instanceof Handle || constant.cst instanceof ConstantDynamic)) {
                    row.put("kind","constant-type");row.put("value",constant.cst.toString());
                } else continue;
                rows.add(row);
            }
        }
        try {
            var value=Map.of("sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(original)),
                    "superName",node.superName,"interfaces",node.interfaces,"source",node.sourceFile,"instructions",rows);
            Object previous=classes.putIfAbsent(name,value);
            if(previous!=null&&!previous.equals(value))throw new IllegalStateException("Candidate class bytecode changed before definition");
        } catch(java.security.NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
        return original;
    }

    /** Controlled synthetic cache records exercise the ORIGINAL ensureLoaded
     * route. They are not pack recipes or positive material-source acceptance. */
    public static Map<String,Object> cacheWitness(com.cleanroommc.groovyscript.sandbox.CustomGroovyScriptEngine engine,
                                                 boolean guarded) throws Exception {
        Class<?> compiled=Class.forName("com.cleanroommc.groovyscript.sandbox.CompiledClass");
        var constructor=compiled.getDeclaredConstructor(String.class,String.class);constructor.setAccessible(true);
        var data=compiled.getDeclaredField("data");data.setAccessible(true);
        var clazz=compiled.getDeclaredField("clazz");clazz.setAccessible(true);
        var ensure=compiled.getDeclaredMethod("ensureLoaded",groovy.lang.GroovyClassLoader.class,Map.class,String.class);
        ensure.setAccessible(true);
        var cache=new LinkedHashMap<String,Object>();
        Object good=constructor.newInstance("classes/MaterialEdits.groovy","classes.MaterialEdits");
        data.set(good,cacheClass("classes.MaterialEdits",false));
        ensure.invoke(good,engine.getClassLoader(),cache,engine.getCacheRoot().toString());
        Class<?> defined=(Class<?>)clazz.get(good);
        Object value=defined.getMethod("value").invoke(null);
        var rejected=new ArrayList<String>();
        if(guarded)for(String name:List.of("research.orthrus.axiom.materialhost.Injected","material.DeveloperMaterials")) {
            Object bad=constructor.newInstance("material/DeveloperMaterials.groovy",name);
            data.set(bad,cacheClass(name,true));
            try {ensure.invoke(bad,engine.getClassLoader(),cache,engine.getCacheRoot().toString());}
            catch(java.lang.reflect.InvocationTargetException failure) {
                if(!(failure.getCause() instanceof UnsupportedOperationException))throw failure;
                if(clazz.get(bad)!=null||cache.containsKey(name))throw new AssertionError("Refused cached class was defined");
                rejected.add(name);
            }
        }
        return Map.of("cachedDefinitionWitness",true,"guarded",guarded,"nativeBody",value,
                "cacheMembership",cache.keySet().stream().toList(),"rejectedBeforeDefinition",rejected,
                "violations",research.orthrus.axiom.materialhost.MaterialCallGate.violations(),
                "validityQualified",false,"generatedFormsQualified",false);
    }
    private static byte[] cacheClass(String name,boolean forbiddenBody) {
        var writer=new ClassWriter(0);
        writer.visit(Opcodes.V1_8,Opcodes.ACC_PUBLIC,name.replace('.','/'),null,"java/lang/Object",null);
        var method=writer.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC,"value","()Ljava/lang/Object;",null,null);
        method.visitCode();
        if(forbiddenBody)method.visitMethodInsn(Opcodes.INVOKESTATIC,"java/lang/System","getProperties","()Ljava/util/Properties;",false);
        else method.visitLdcInsn("original-cache-body");
        method.visitInsn(Opcodes.ARETURN);method.visitMaxs(1,0);method.visitEnd();writer.visitEnd();return writer.toByteArray();
    }
}
