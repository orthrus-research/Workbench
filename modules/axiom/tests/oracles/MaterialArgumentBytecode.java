package research.orthrus.axiom.materialhost;

import org.codehaus.groovy.control.BytecodeProcessor;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.MessageDigest;
import java.util.*;

/** Observe actual complete-program compiler output; never rewrite/admit a class. */
public final class MaterialArgumentBytecode implements BytecodeProcessor {
    private final Map<String,Object> observations=new TreeMap<>();
    public Map<String,Object> observations() {return Map.copyOf(observations);}
    @Override public byte[] processBytecode(String name,byte[] bytes) {
        var node=new ClassNode();new ClassReader(bytes).accept(node,0);
        var methods=new ArrayList<Map<String,Object>>();
        for(MethodNode method:node.methods) {
            var instructions=new ArrayList<Map<String,Object>>();
            for(AbstractInsnNode instruction:method.instructions) {
                if(instruction instanceof MethodInsnNode call)instructions.add(Map.of("kind","method","opcode",call.getOpcode(),"owner",call.owner,"name",call.name,"descriptor",call.desc));
                else if(instruction instanceof FieldInsnNode field)instructions.add(Map.of("kind","field","opcode",field.getOpcode(),"owner",field.owner,"name",field.name,"descriptor",field.desc));
                else if(instruction instanceof InvokeDynamicInsnNode call)instructions.add(Map.of("kind","indy","name",call.name,"descriptor",call.desc,
                    "bootstrap",call.bsm.toString(),"arguments",Arrays.stream(call.bsmArgs).map(String::valueOf).toList()));
            }
            methods.add(Map.of("name",method.name,"descriptor",method.desc,"access",method.access,"instructions",instructions));
        }
        try {
            observations.put(name,Map.of("sha256",HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)),
                "classVersion",node.version,"access",node.access,"superName",node.superName,"interfaces",node.interfaces,
                "recordComponents",node.recordComponents==null?List.of():node.recordComponents.stream()
                    .map(c->Map.of("name",c.name,"descriptor",c.descriptor)).toList(),
                "fields",node.fields.stream().map(f->Map.of("name",f.name,"descriptor",f.desc,"access",f.access)).toList(),"methods",methods));
        } catch(java.security.NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
        return bytes;
    }
}
