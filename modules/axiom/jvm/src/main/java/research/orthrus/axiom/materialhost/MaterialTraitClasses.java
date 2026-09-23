package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.*;
import java.util.*;

/** Source-derived trait types and native definitions, scoped by actual loader.
 * This catalog never constructs a proxy or selects a trait implementation. */
public final class MaterialTraitClasses {
    private MaterialTraitClasses() {}
    private static final Map<String,Entry> SOURCE=new LinkedHashMap<>();
    private static final Map<ClassLoader,Map<String,Entry>> GENERATED=new IdentityHashMap<>();
    private static final List<Map<String,Object>> OBSERVED=new ArrayList<>();
    private static final Map<String,Map<String,Object>> COMPILED=new TreeMap<>();
    private static final ThreadLocal<ClassLoader> DEFINING=new ThreadLocal<>();
    private static final ThreadLocal<String> DEFINITION_NAME=new ThreadLocal<>();
    private static final Map<ClassLoader,Integer> SCOPES=new IdentityHashMap<>();
    private static Set<String> DECLARED_TRAITS=Set.of();
    public static void bind(Set<String> traits) {
        if(!SOURCE.isEmpty()||!GENERATED.isEmpty())throw new IllegalStateException("Trait catalog already in use");
        DECLARED_TRAITS=Set.copyOf(traits);
    }
    // Later dispatch needs identities/signatures, not compiler instruction trees.
    // Keeping ClassNode here retained every compiled pack class for the worker's
    // lifetime, including line tables and method bodies no longer inspected.
    private record Entry(String kind,Set<String> methods,MaterialCallGate.GuestMembers members) {
        private Entry(String kind,ClassNode node,MaterialCallGate.GuestMembers members) {
            this(kind,Set.copyOf(node.methods.stream().map(m->m.name+m.desc).toList()),members);
        }
    }

    public static synchronized byte[] define(ClassLoader loader,String name,byte[] original) {
        if(DEFINING.get()!=null)throw new IllegalStateException("Nested trait definition callback");
        DEFINING.set(loader);DEFINITION_NAME.set(name);
        try {
            var node=new ClassNode();new ClassReader(original).accept(node,0);
            String kind=generatedKind(node);
            if(kind==null)throw MaterialCallGate.reject("candidate.trait-definition",name);
            byte[] guarded=new MaterialBytecodeGate().processBytecode(name,original);
            var row=new LinkedHashMap<String,Object>();row.put("kind",kind);row.put("name",name);
            row.put("classVersion",node.version);row.put("superName",node.superName);row.put("interfaces",List.copyOf(node.interfaces));
            row.put("originalSha256",digest(original));row.put("guardedSha256",digest(guarded));
            row.put("methodOrderIndependentSha256",MaterialTraitLoaderHook.methodOrderIndependentSha256(original));
            row.put("methodOrder",node.methods.stream().map(m->m.name+m.desc).toList());
            row.put("definitionScope",scope(loader));OBSERVED.add(Map.copyOf(row));
            return guarded;
        } finally {DEFINING.remove();DEFINITION_NAME.remove();}
    }
    private static int scope(ClassLoader loader) {
        // Stable observation ordinal within this worker, not a semantic class ID.
        return SCOPES.computeIfAbsent(loader,k->SCOPES.size()+1);
    }
    private static String digest(byte[] bytes) {
        try {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}
        catch(NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
    }
    private static Entry find(String internal) {
        String name=internal.replace('/','.');ClassLoader loader=DEFINING.get();
        Entry generated=loader==null?null:GENERATED.getOrDefault(loader,Map.of()).get(name);
        return generated!=null?generated:SOURCE.get(name);
    }
    static boolean defining() {return DEFINING.get()!=null;}
    static boolean ownsDefinition(String name) {
        if(!defining())return false;
        return name.equals(DEFINITION_NAME.get())||GENERATED.getOrDefault(DEFINING.get(),Map.of()).containsKey(name);
    }
    static boolean traitInterface(String internal) {
        String name=internal.replace('/','.');
        return DECLARED_TRAITS.contains(name)||DECLARED_TRAITS.stream().anyMatch(t->name.equals(t+"$Trait$FieldHelper"));
    }
    static boolean traitClass(Class<?> type) {
        Entry entry=SOURCE.get(type.getName());return entry!=null&&entry.kind.equals("trait")&&type.isInterface();
    }
    private static String generatedKind(ClassNode node) {
        if(node.superName.equals("java/lang/Object")&&node.name.endsWith("$TraitAdapter")
                &&!node.interfaces.isEmpty()&&node.interfaces.stream().allMatch(i->i.equals("groovy/lang/GroovyObject")||traitInterface(i))
                &&node.interfaces.stream().anyMatch(MaterialTraitClasses::traitInterface))return "adapter";
        Entry parent=find(node.superName);
        if(parent!=null&&parent.kind.equals("adapter")&&node.interfaces.contains("groovy/lang/GeneratedGroovyProxy")) {
            var delegate=node.fields.stream().filter(f->f.name.equals("$delegate")&&f.desc.startsWith("L")).toList();
            if(delegate.size()==1&&SOURCE.containsKey(Type.getType(delegate.getFirst().desc).getClassName())
                    &&node.interfaces.stream().allMatch(i->Set.of("groovy/lang/GroovyObject","groovy/lang/GeneratedGroovyProxy").contains(i)||traitInterface(i)))return "proxy";
        }
        return null;
    }
    static boolean acceptsHierarchy(ClassNode node) {
        if(defining())return generatedKind(node)!=null;
        return node.interfaces.stream().allMatch(i->Set.of("groovy/lang/GroovyObject","org/codehaus/groovy/runtime/GeneratedClosure").contains(i)||traitInterface(i));
    }
    static boolean internalField(ClassNode node,FieldInsnNode field) {
        return defining()&&field.owner.equals(node.name)
            ||helper(node)&&field.getOpcode()==Opcodes.GETSTATIC&&field.owner.equals("java/lang/Boolean")&&field.name.equals("TYPE")&&field.desc.equals("Ljava/lang/Class;");
    }
    static boolean helper(ClassNode node) {return DECLARED_TRAITS.stream().anyMatch(t->node.name.replace('/','.').equals(t+"$Trait$Helper"));}
    static boolean directCall(ClassNode node,MethodNode method,MethodInsnNode call) {
        String signature=call.owner+"#"+call.name+call.desc;
        if((helper(node)||defining())&&call.getOpcode()==Opcodes.INVOKESTATIC&&Set.of(
                "org/codehaus/groovy/runtime/ScriptBytecodeAdapter#compareEqual(Ljava/lang/Object;Ljava/lang/Object;)Z",
                "org/codehaus/groovy/runtime/typehandling/DefaultTypeTransformation#booleanUnbox(Ljava/lang/Object;)Z").contains(signature))return true;
        if(!defining())return false;
        if("adapter".equals(generatedKind(node))&&method.name.contains("trait$super$")&&call.getOpcode()==Opcodes.INVOKESTATIC
                &&Set.of("org/codehaus/groovy/runtime/ScriptBytecodeAdapter#invokeMethodOnSuper0(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;)Ljava/lang/Object;",
                    "org/codehaus/groovy/runtime/ScriptBytecodeAdapter#invokeMethodOnSuperN(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;").contains(signature))return true;
        Entry target=find(call.owner);
        return target!=null&&Set.of("helper","field-helper","trait","adapter").contains(target.kind)
                &&target.methods.contains(call.name+call.desc);
    }
    static boolean nativeInitialization(ClassNode node,MethodNode method,InvokeDynamicInsnNode call) {
        return defining()&&"adapter".equals(generatedKind(node))&&method.name.equals("<init>")
            &&call.name.equals("invoke")&&call.bsmArgs.length==2&&call.bsmArgs[0].equals("$init$")
            &&call.desc.equals("(Ljava/lang/Class;L"+node.name+";)Ljava/lang/Object;");
    }
    static boolean delegateCall(ClassNode node,MethodInsnNode call) {
        return defining()&&"proxy".equals(generatedKind(node))&&call.getOpcode()==Opcodes.INVOKESTATIC
            &&call.owner.equals("org/codehaus/groovy/runtime/InvokerHelper")&&call.name.equals("invokeMethod")
            &&call.desc.equals("(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)Ljava/lang/Object;");
    }
    static void compilation(ClassNode node,byte[] original,byte[] guarded) {
        Entry entry=SOURCE.get(node.name.replace('/','.'));
        if(!defining()&&entry!=null&&!entry.kind.equals("source"))COMPILED.put(node.name.replace('/','.'),Map.of(
            "kind",entry.kind,"classVersion",node.version,"originalSha256",digest(original),"guardedSha256",digest(guarded),
            "superName",node.superName,"interfaces",List.copyOf(node.interfaces)));
    }
    static void register(ClassNode node,MaterialCallGate.GuestMembers members,byte[] original) {
        String name=node.name.replace('/','.');String kind="source";
        if(defining()) {
            kind=generatedKind(node);
            var entries=GENERATED.computeIfAbsent(DEFINING.get(),k->new LinkedHashMap<>());
            if(kind==null||entries.putIfAbsent(name,new Entry(kind,node,members))!=null)
                throw MaterialCallGate.reject("candidate.trait-identity",name);
        } else {
            if(DECLARED_TRAITS.contains(name))kind="trait";
            else if(DECLARED_TRAITS.stream().anyMatch(t->name.equals(t+"$Trait$Helper")))kind="helper";
            else if(DECLARED_TRAITS.stream().anyMatch(t->name.equals(t+"$Trait$FieldHelper")))kind="field-helper";
            SOURCE.put(name,new Entry(kind,node,members));
        }
    }
    static MaterialCallGate.GuestMembers members(Class<?> type) {
        Entry entry=GENERATED.getOrDefault(type.getClassLoader(),Map.of()).get(type.getName());
        if(entry==null)return null;
        var fields=new ArrayList<MaterialCallGate.Member>();var methods=new ArrayList<MaterialCallGate.Member>();
        for(Class<?> current=type;current!=null;current=current.getSuperclass()) {
            Entry inherited=GENERATED.getOrDefault(type.getClassLoader(),Map.of()).get(current.getName());
            if(inherited!=null) {
                fields.addAll(inherited.members.fields());methods.addAll(inherited.members.methods());
                for(var method:inherited.members.methods()) {
                    String property=null;
                    if(method.name().startsWith("get")&&method.name().length()>3&&method.descriptor().startsWith("()"))property=method.name().substring(3);
                    if(method.name().startsWith("is")&&method.name().length()>2&&method.descriptor().equals("()Z"))property=method.name().substring(2);
                    if(method.name().startsWith("set")&&method.name().length()>3&&method.descriptor().endsWith(")V"))property=method.name().substring(3);
                    if(property!=null)fields.add(new MaterialCallGate.Member(Character.toLowerCase(property.charAt(0))+property.substring(1),"",method.access()));
                }
            }
        }
        return new MaterialCallGate.GuestMembers(fields,methods);
    }
    static boolean caller(Class<?> type) {return GENERATED.getOrDefault(type.getClassLoader(),Map.of()).containsKey(type.getName());}
    public static synchronized List<Map<String,Object>> observations() {return List.copyOf(OBSERVED);}
    public static synchronized Map<String,Map<String,Object>> compilations() {return Map.copyOf(COMPILED);}
}
