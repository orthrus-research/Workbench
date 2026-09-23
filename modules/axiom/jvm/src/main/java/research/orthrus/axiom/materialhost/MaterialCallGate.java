package research.orthrus.axiom.materialhost;

import java.lang.invoke.*;
import java.lang.reflect.Modifier;
import java.util.*;
import org.codehaus.groovy.vmplugin.v8.IndyInterface;
import org.codehaus.groovy.runtime.ScriptBytecodeAdapter;
import groovy.lang.GroovyObject;

/**
 * Candidate-only admission wrapper around the ORIGINAL Groovy invokedynamic
 * target. Never dispatches or evaluates material semantics itself. This boundary
 * is under qualification and is not sufficient without source/bytecode admission.
 */
public final class MaterialCallGate {
    private MaterialCallGate() {}
    private static final Map<String,GuestMembers> GUEST_MEMBERS=new LinkedHashMap<>();
    private static final List<String> VIOLATIONS=new ArrayList<>();
    private static final Map<String,Integer> OBSERVATIONS=new LinkedHashMap<>();
    private static final Map<String,Map<String,Object>> RECORD_COMPILATIONS=new TreeMap<>();
    private static Set<String> sourceClasses;
    private static MaterialAdmissionPolicy policy;
    private static MaterialMapperBindings mapperBindings;
    private static List<MaterialPropertyBindings> propertyBindings;
    private static MaterialScriptBindings scriptBindings;
    private static volatile boolean resourceFailure;
    private static volatile boolean linkageFailure;
    private static final Class<?>[] resourceCause=new Class<?>[32];
    private static int resourceCauseLength;
    private static final Set<String> RESERVED=Set.of("getMetaClass","setMetaClass","metaClass","getClass","class",
            "invokeMethod","invokeStaticMethod","getBinding","setBinding","binding","getProperty","setProperty",
            "propertyMissing","methodMissing","getProperties","properties","getClassLoader","classLoader","finalize");
    // Native Groovy's pickStaticMethod can fall back to the Class metaclass if
    // a guest overload does not match. These names are deliberately outside this
    // bounded static-helper surface, even when a guest declares an overload.
    // We do not emulate Groovy's selection/coercion to predict that fallback.
    private static final Set<String> CLASS_METHODS=Arrays.stream(Class.class.getMethods())
            .map(java.lang.reflect.Method::getName).collect(java.util.stream.Collectors.toUnmodifiableSet());
    record Member(String name,String descriptor,int access) {
        boolean onReceiver(boolean staticReceiver) {return !staticReceiver||Modifier.isStatic(access);}
    }
    record GuestMembers(List<Member> fields,List<Member> methods) {
        GuestMembers {fields=List.copyOf(fields);methods=List.copyOf(methods);}
        boolean admits(String operation,String member,boolean staticReceiver) {
            if(member.startsWith("$")||member.startsWith("this$dist$")||RESERVED.contains(member))return false;
            return switch(operation) {
                case "invoke" -> !(staticReceiver&&CLASS_METHODS.contains(member))
                        &&methods.stream().anyMatch(value->value.name().equals(member)&&value.onReceiver(staticReceiver));
                case "getProperty", "setProperty" -> fields.stream()
                        .anyMatch(value->value.name().equals(member)&&value.onReceiver(staticReceiver));
                default -> false;
            };
        }
    }
    public static synchronized void bind(Set<String> classes,Map<String,Object> selectedPolicy) {
        if(sourceClasses!=null||classes.isEmpty())throw new IllegalStateException("Call gate requires one fresh admitted program");
        policy=new MaterialAdmissionPolicy(selectedPolicy);
        sourceClasses=Set.copyOf(classes);
    }
    static MaterialAdmissionPolicy policy() {
        if(policy==null)throw new IllegalStateException("Call gate policy not bound");
        return policy;
    }
    public static synchronized void bindNativeMappers(Map<String,Object> nativeBindings) {
        if(mapperBindings!=null)throw new IllegalStateException("Native mapper bindings already installed");
        mapperBindings=new MaterialMapperBindings(nativeBindings,policy().nativeObjectMappers());
        scriptBindings=new MaterialScriptBindings(nativeBindings);
    }
    /** Bind the original registered GT property container after preInit has
     * populated its recipe maps. Only profile-admitted value classes enter it. */
    public static synchronized void bindNativeRecipeProperties(Map<String,Object> nativeBindings,
            Object mods, Map<String,Object> containers, Map<Object,Map<String,?>> nativeProperties,
            Map<String,Object> additionalRoots) {
        if(propertyBindings!=null)throw new IllegalStateException("Native property bindings already installed");
        var graph=new IdentityHashMap<Object,Map<String,?>>();
        graph.putAll(nativeProperties);graph.put(mods,containers);
        var selected=new ArrayList<MaterialPropertyBindings>();
        selected.add(new MaterialPropertyBindings(nativeBindings,"mods",mods,graph,
                value->nativeProperties.containsKey(value)||policy().nativePropertyValue(value.getClass())));
        for(var entry:additionalRoots.entrySet()) {
            Object root=entry.getValue();
            if(root==null||!policy().nativePropertyValue(root.getClass()))
                throw new IllegalStateException("Native recipe root type was not selected: "+entry.getKey());
            var rootGraph=new IdentityHashMap<Object,Map<String,?>>();rootGraph.put(root,Map.of());
            selected.add(new MaterialPropertyBindings(nativeBindings,entry.getKey(),root,rootGraph,value->false));
        }
        propertyBindings=List.copyOf(selected);
    }
    static boolean collectionAdditionOperand(MaterialAdmissionPolicy selected, Object value) {
        return value instanceof Collection<?> && selected.listOperation(value.getClass().getName(),"iterator");
    }
    static boolean collectionFlattenOperand(Object value) {
        if(value==null||value.getClass()!=ArrayList.class)return false;
        var pending=new ArrayDeque<Object>();pending.add(value);
        var seen=Collections.newSetFromMap(new IdentityHashMap<Object,Boolean>());
        while(!pending.isEmpty()) {
            Object next=pending.removeLast();if(!seen.add(next))continue;
            if(next instanceof Collection<?>) {
                if(next.getClass()!=ArrayList.class)return false;
                for(Object element:((ArrayList<?>)next).toArray())if(element!=null)pending.add(element);
            } else if(next.getClass().isArray()) {
                for(int i=0;i<java.lang.reflect.Array.getLength(next);i++) {
                    Object element=java.lang.reflect.Array.get(next,i);if(element!=null)pending.add(element);
                }
            }
        }
        return true;
    }
    static boolean collectionJoinOperand(Object value) {
        if(value==null||value.getClass()!=ArrayList.class)return false;
        // Saved name construction joins strings. Inspect the exact list's raw
        // elements before Groovy's formatter can call any object callbacks.
        for(Object element:((ArrayList<?>)value).toArray())if(element!=null&&element.getClass()!=String.class)return false;
        return true;
    }
    static boolean collectionCombinationsOperand(Object value) {
        if(value==null||value.getClass()!=ArrayList.class)return false;
        for(Object dimension:((ArrayList<?>)value).toArray()) {
            // The original helper stops at an empty dimension. Later values
            // are not converted or iterated on that path.
            if(dimension==null)return true;
            if(dimension.getClass()!=ArrayList.class)return false;
            if(((ArrayList<?>)dimension).isEmpty())return true;
        }
        return true;
    }
    static boolean collectionMultiplyOperand(MaterialAdmissionPolicy selected,Object value,Object factor) {
        return value!=null&&value.getClass()==ArrayList.class
                &&(factor==null||selected.numeric(factor.getClass().getName()));
    }
    private static boolean referenceCastOperands(MaterialAdmissionPolicy selected,Object value,Class<?> target) {
        if(value!=null&&value.getClass()==ArrayList.class&&target==int[].class) {
            // Original Groovy owns allocation, conversion and native failures.
            // Inspect exact list elements without entering custom Number or
            // collection callbacks during that conversion.
            for(Object element:((ArrayList<?>)value).toArray())
                if(element!=null&&!selected.numeric(element.getClass().getName())
                        &&element.getClass()!=Character.class&&element.getClass()!=String.class)return false;
        }
        return true;
    }
    /** Original compiler cell assignment, with no overridden setter callback. */
    public static void setReference(groovy.lang.Reference<Object> reference,Object value) {
        if(reference!=null&&reference.getClass()!=groovy.lang.Reference.class)
            throw reject("candidate.reference",reference.getClass().getName());
        reference.set(value);
    }
    static synchronized boolean owns(String name) {
        if(sourceClasses==null)throw new IllegalStateException("Call gate not bound");
        return sourceClasses.stream().anyMatch(root->name.equals(root)||name.startsWith(root+"$"))||MaterialTraitClasses.ownsDefinition(name);
    }
    static synchronized void register(String name,GuestMembers members) {
        if(!owns(name)||GUEST_MEMBERS.putIfAbsent(name,members)!=null)
            throw reject("candidate.class-identity",name);
    }
    public static synchronized List<String> violations() {return List.copyOf(VIOLATIONS);}
    public static synchronized Map<String,Integer> observations() {return Map.copyOf(OBSERVATIONS);}
    private static final Set<String> OBSERVED_INSTANCE_TYPES=new HashSet<>();
    public static synchronized boolean observedInstance(String name) {return OBSERVED_INSTANCE_TYPES.contains(name);}
    static String observationKey(String operation,String owner,String member,boolean guest) {
        // Saved catalog field names are already in the source inventory. Keep
        // their counts by owner, not thousands of repeated observation keys.
        // Native members and guest calls remain individually named.
        return operation+" "+owner+"#"+(guest&&Set.of("getProperty","setProperty","getField").contains(operation)
                ?"<declared-fields>":member);
    }
    static boolean nativeDelegate(MaterialAdmissionPolicy admission,Object delegate,String member,Object[] arguments) {
        return delegate!=null&&!(delegate instanceof Class<?>)
                &&(admission.nativeMethod(delegate.getClass(),member,false)&&nativeMethodOperands(admission,delegate,member,arguments)
                    ||admission.nativePrivateMethod(delegate.getClass(),member,false)
                    ||admission.nativeExtension(delegate.getClass(),member,false)
                        &&nativeExtensionOperands(admission,delegate.getClass(),member,arguments));
    }
    private static boolean nativeExtensionOperands(MaterialAdmissionPolicy admission,Class<?> type,String member,Object[] arguments) {
        if(type==ArrayList.class&&member.equals("multiply"))return arguments.length==2
                &&collectionMultiplyOperand(admission,arguments[0],arguments[1]);
        if(type==ArrayList.class&&member.equals("combinations"))return arguments.length==1
                &&collectionCombinationsOperand(arguments[0]);
        // Original tap/each helpers invoke source callbacks. Each body must
        // remain a registered, guarded source closure, including delegate calls.
        if(Set.of("tap","each").contains(member))return arguments.length==2&&arguments[1] instanceof groovy.lang.Closure<?>
                &&GUEST_MEMBERS.containsKey(arguments[1].getClass().getName());
        if(type.getName().equals("it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap")&&member.equals("putAt")) {
            // DGM also has an Object/String overload that enters property
            // dispatch, including metadata setters and wrapper unwrapping.
            // Admit ordinary map entries without entering those callback routes.
            if(arguments.length!=3||arguments[2] instanceof org.codehaus.groovy.runtime.wrappers.Wrapper)return false;
            if(arguments[1]==null)return true;
            if(!(arguments[1] instanceof String key)||RESERVED.contains(key)||key.startsWith("$"))return false;
            return groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type).getMetaProperty(key)==null;
        }
        if(type!=String.class||!member.equals("plus"))return true;
        if(arguments.length!=2)return false;
        return stringConcatenationOperand(admission,arguments[1],Collections.newSetFromMap(new IdentityHashMap<>()));
    }
    private static boolean stringConcatenationOperand(MaterialAdmissionPolicy admission,Object value,Set<Object> visiting) {
        if(value==null||value instanceof String||value instanceof Boolean||value instanceof Character
                ||admission.numeric(value.getClass().getName()))return true;
        if(value.getClass()==org.codehaus.groovy.runtime.GStringImpl.class) {
            if(!visiting.add(value))return false;
            try {
                // GStringImpl.getValues() invalidates its formatting cache.
                // Inspect the original capture field without getter/formatter
                // calls, preserving Groovy's cache and evaluation behavior.
                var values=groovy.lang.GString.class.getDeclaredField("values");
                if(values.getType()!=Object[].class||Modifier.isStatic(values.getModifiers())||!values.trySetAccessible())
                    throw reject("candidate.api-drift","groovy.lang.GString#values");
                for(Object captured:(Object[])values.get(value))
                    if(!stringConcatenationOperand(admission,captured,visiting))return false;
                return true;
            } catch(ReflectiveOperationException absent) {
                throw reject("candidate.api-drift","groovy.lang.GString#values");
            } finally {visiting.remove(value);}
        }
        // The selected Material implementation formats its native registry path.
        // Keep Groovy's original formatter and reject subclasses or other objects
        // whose toString callback has not been established by this slice.
        return value.getClass().getName().equals("gregtech.api.unification.material.Material")
                &&admission.nativeMethod(value.getClass(),"toString",false);
    }
    private static boolean numericOperand(MaterialAdmissionPolicy admission,Object value) {
        if(value==null)return false;
        if(admission.numeric(value.getClass().getName()))return true;
        if(value.getClass()!=org.codehaus.groovy.runtime.wrappers.PojoWrapper.class)return false;
        // Explicit compiler casts retain a type constraint for Groovy overload
        // selection. Inspect the original wrapper's fields; do not replace it
        // or admit overridden unwrap/getType callbacks or nested wrappers.
        var wrapper=(org.codehaus.groovy.runtime.wrappers.PojoWrapper)value;
        Object wrapped=wrapper.unwrap();Class<?> constrained=wrapper.getType();
        return wrapped!=null&&admission.numeric(wrapped.getClass().getName())&&constrained!=null
                &&(admission.numeric(constrained.getName())||Set.of(byte.class,short.class,int.class,long.class,float.class,double.class).contains(constrained));
    }
    /** Only original generated bodies that passed this worker's bytecode gate. */
    static boolean sourceClosure(Object value) {
        return value instanceof groovy.lang.Closure<?>
                &&value.getClass().getSuperclass()==groovy.lang.Closure.class
                &&org.codehaus.groovy.runtime.GeneratedClosure.class.isAssignableFrom(value.getClass())
                &&GUEST_MEMBERS.containsKey(value.getClass().getName());
    }
    private static boolean nativeMethodOperands(MaterialAdmissionPolicy admission,Object receiver,String member,Object[] arguments) {
        Class<?> type=receiver instanceof Class<?> represented?represented:receiver.getClass();
        if(type.getName().equals("gregtech.api.recipes.builders.AssemblyLineRecipeBuilder")&&member.equals("scannerResearch")) {
            // Inspect the complete native overload family, but admit only the
            // stack form. Do not run/coerce a UnaryOperator, map or closure.
            if(receiver instanceof Class<?>||arguments.length!=2)return false;
            if(arguments[1]==null)return true; // original validation owns null
            try {return arguments[1].getClass()==Class.forName("net.minecraft.item.ItemStack",false,type.getClassLoader());}
            catch(ClassNotFoundException absent) {return false;}
        }
        // Let original Groovy select the overload and perform SAM conversion.
        // A preconstructed proxy or foreign closure must not cross this route.
        if(type.getName().equals("net.minecraft.item.ItemStack")&&member.equals("transform"))
            return !(receiver instanceof Class<?>)&&arguments.length==2
                    &&(arguments[1]==null||sourceClosure(arguments[1])||arguments[1].getClass()==type);
        if(Set.of("com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipeBuilder$Shaped",
                "com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipeBuilder$Shapeless").contains(type.getName())
                &&member.equals("recipeFunction"))
            return !(receiver instanceof Class<?>)&&arguments.length==2&&(arguments[1]==null||sourceClosure(arguments[1]));
        if(type.getName().equals("com.cleanroommc.groovyscript.compat.vanilla.OreDict")&&member.equals("remove"))
            return !(receiver instanceof Class<?>)&&arguments.length==3&&(arguments[1]==null||arguments[1] instanceof String)
                    &&(arguments[2]==null||arguments[2].getClass().getName().equals("net.minecraft.item.ItemStack"));
        if(member.equals("filter"))return arguments.length==2&&arguments[1] instanceof groovy.lang.Closure<?>
                &&GUEST_MEMBERS.containsKey(arguments[1].getClass().getName());
        // Reflect the complete removeAll family, but keep execution on the
        // original stream's bound remover. The inherited Collection overload
        // has separate membership/iterator callbacks outside this operation.
        if(type.getName().equals("com.cleanroommc.groovyscript.helper.SimpleObjectStream")&&member.equals("removeAll"))
            return !(receiver instanceof Class<?>)&&arguments.length==1;
        if(type.getName().equals("net.minecraft.block.state.BlockStateContainer$StateImplementation")
                &&Set.of("withProperty","func_177226_a").contains(member))
            return !(receiver instanceof Class<?>)&&arguments.length==3&&arguments[1]!=null
                    &&arguments[1].getClass().getName().equals("net.minecraft.block.properties.PropertyEnum")
                    &&arguments[2] instanceof Enum<?> value
                    &&value.getDeclaringClass().getName().equals("net.minecraft.block.BlockLog$EnumAxis");
        if(type==Math.class&&member.equals("max"))return arguments.length==3
                &&arguments[1]!=null&&admission.numeric(arguments[1].getClass().getName())
                &&arguments[2]!=null&&admission.numeric(arguments[2].getClass().getName());
        if(type==Math.class&&member.equals("ceil"))return arguments.length==2
                &&arguments[1]!=null&&admission.numeric(arguments[1].getClass().getName());
        if(receiver instanceof java.io.PrintStream&&member.equals("println"))return receiver==System.out
                &&(arguments.length==1||arguments.length==2&&(arguments[1]==null||arguments[1] instanceof String));
        if(type==String.class&&member.equals("replace"))return arguments.length==3
                &&(arguments[1]==null||arguments[1] instanceof String||arguments[1] instanceof Character)
                &&(arguments[2]==null||arguments[2] instanceof String||arguments[2] instanceof Character);
        if(type==ArrayList.class&&member.equals("indexOf"))return !(receiver instanceof Class<?>)
                &&arguments.length==2&&(arguments[1]==null||arguments[1] instanceof String);
        if(type==ArrayList.class&&member.equals("addAll"))return !(receiver instanceof Class<?>)
                &&(arguments.length==2||arguments.length==3&&arguments[1] instanceof Integer)
                &&arguments[arguments.length-1]!=null&&arguments[arguments.length-1].getClass()==ArrayList.class;
        if(type.getName().equals("it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap")&&member.equals("put"))
            return !(receiver instanceof Class<?>)&&arguments.length==3&&(arguments[1]==null||arguments[1].getClass()==String.class);
        return true;
    }
    private static boolean declaredClosureOwner(groovy.lang.Closure<?> closure,String member) {
        var seen=Collections.newSetFromMap(new IdentityHashMap<Object,Boolean>());
        while(seen.add(closure)) {
            // Use only the compiler's unchanged owner/delegate resolution. No
            // overridden getter or alternate native delegate may borrow an
            // owner's admission when Groovy selects its actual target.
            try {
                for(String name:List.of("getOwner","getDelegate","getResolveStrategy"))
                    if(closure.getClass().getMethod(name).getDeclaringClass()!=groovy.lang.Closure.class)return false;
            } catch(NoSuchMethodException absent) {return false;}
            if(closure.getResolveStrategy()!=groovy.lang.Closure.OWNER_FIRST
                    &&closure.getResolveStrategy()!=groovy.lang.Closure.OWNER_ONLY)return false;
            Object owner=closure.getOwner();
            if(owner==null||closure.getDelegate()!=owner)return false;
            Class<?> type=owner instanceof Class<?> cls?cls:owner.getClass();
            GuestMembers members=GUEST_MEMBERS.get(type.getName());
            if(members==null)return false;
            if(members.admits("invoke",member,owner instanceof Class<?>))return true;
            if(!(owner instanceof groovy.lang.Closure<?> outer))return false;
            closure=outer;
        }
        return false;
    }
    private static boolean sourceBeanProperty(Class<?> type,String name,boolean write,boolean staticReceiver) {
        if(name.startsWith("$")||RESERVED.contains(name))return false;
        var property=groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type).getMetaProperty(name);
        if(!(property instanceof groovy.lang.MetaBeanProperty bean))return false;
        var accessor=write?bean.getSetter():bean.getGetter();
        if(!(accessor instanceof org.codehaus.groovy.reflection.CachedMethod cached))return false;
        var method=cached.getCachedMethod();
        if(method.getDeclaringClass()!=type||!Modifier.isPublic(method.getModifiers())
                ||staticReceiver&&!Modifier.isStatic(method.getModifiers())
                ||staticReceiver&&CLASS_METHODS.contains(method.getName())
                ||method.getParameterCount()!=(write?1:0)||RESERVED.contains(method.getName()))return false;
        var members=GUEST_MEMBERS.get(type.getName());
        if(members==null)return false;
        String descriptor=MethodType.methodType(method.getReturnType(),method.getParameterTypes()).descriptorString();
        return members.methods().stream().anyMatch(member->member.name().equals(method.getName())
                &&member.descriptor().equals(descriptor)&&member.onReceiver(staticReceiver));
    }
    static synchronized void compilerConstant() {
        OBSERVATIONS.merge("compiler.constant AnnotationCollectorMode#PREFER_EXPLICIT_MERGED",1,Integer::sum);
    }
    static synchronized void recordCompilation(String name,int version,List<Map<String,Object>> components,byte[] original,byte[] guarded) {
        try {
            var digest=java.security.MessageDigest.getInstance("SHA-256");
            RECORD_COMPILATIONS.put(name,Map.of("classVersion",version,"components",List.copyOf(components),
                "originalSha256",HexFormat.of().formatHex(digest.digest(original)),
                "guardedSha256",HexFormat.of().formatHex(digest.digest(guarded))));
        } catch(java.security.NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
    }
    public static synchronized Map<String,Map<String,Object>> recordCompilations() {return Map.copyOf(RECORD_COMPILATIONS);}
    /** Injected at original candidate exception-handler entries. It does not
     * allocate, wrap, log, replace or rethrow the original caught object. */
    public static synchronized void caught(Throwable failure) {
        if(resourceFailure||linkageFailure)return;
        for(int depth=0;failure!=null&&depth<resourceCause.length;depth++) {
            resourceCause[depth]=failure.getClass();
            resourceCauseLength=depth+1;
            if(failure instanceof LinkageError||failure instanceof ClassNotFoundException)linkageFailure=true;
            if(failure instanceof VirtualMachineError) {
                resourceFailure=true;return;
            }
            // Native Groovy reflection wraps the original VM error. Only unwrap
            // these trusted wrappers; do not invoke arbitrary throwable getters
            // or formatting callbacks supplied by source.
            if(failure instanceof org.codehaus.groovy.runtime.InvokerInvocationException
                    ||failure instanceof java.lang.reflect.InvocationTargetException
                    ||failure instanceof java.lang.reflect.UndeclaredThrowableException
                    ||failure instanceof java.util.concurrent.ExecutionException
                    ||failure instanceof ExceptionInInitializerError
                    ||failure instanceof BootstrapMethodError
                    ||failure instanceof NoClassDefFoundError
                    ||failure instanceof ClassNotFoundException
                    ||failure.getClass()==groovy.lang.GroovyRuntimeException.class)failure=failure.getCause();
            else return;
        }
    }
    public static boolean resourceFailure() {return resourceFailure;}
    public static boolean linkageFailure() {return linkageFailure;}
    public static synchronized List<String> resourceCause() {
        if(!resourceFailure)return List.of();
        return caughtCause();
    }
    public static synchronized List<String> caughtCause() {
        var result=new ArrayList<String>();
        for(int i=0;i<resourceCauseLength;i++)result.add(resourceCause[i].getName());
        return List.copyOf(result);
    }
    static synchronized UnsupportedOperationException reject(String reason,String detail) {
        String value=reason+": "+detail;VIOLATIONS.add(value);
        return new UnsupportedOperationException("Unadmitted candidate operation: "+value);
    }
    public static CallSite bootstrap(MethodHandles.Lookup caller,String operation,MethodType type,String member,int flags) throws Exception {
        if(!owns(caller.lookupClass().getName())&&!MaterialTraitClasses.caller(caller.lookupClass()))throw reject("candidate.call-owner",caller.lookupClass().getName());
        CallSite original=IndyInterface.bootstrap(caller,operation,type,member,flags);
        MethodHandle check=MethodHandles.lookup().findStatic(MaterialCallGate.class,"check",
                MethodType.methodType(void.class,String.class,String.class,Class.class,int.class,Object[].class));
        check=MethodHandles.insertArguments(check,0,operation,member,type.returnType(),flags);
        check=check.asCollector(Object[].class,type.parameterCount()).asType(type.changeReturnType(void.class));
        // dynamicInvoker follows the native site's own cache/switchpoint changes;
        // the check cannot be optimized out by native call-site retargeting.
        return new ConstantCallSite(MethodHandles.foldArguments(original.dynamicInvoker(),check));
    }
    // The compiler's nested-class dispatchers use these static adapters rather
    // than invokedynamic. Preserve native sender, receiver, arguments, return
    // value and throwable. Only the existing operation check is added.
    public static Object invokeMethodOnCurrentN(Class<?> sender,GroovyObject receiver,String name,Object[] arguments) throws Throwable {
        check("invoke",name,Object.class,0,withReceiver(receiver,arguments));
        return ScriptBytecodeAdapter.invokeMethodOnCurrentN(sender,receiver,name,arguments);
    }
    public static Object invokeMethodN(Class<?> sender,Object receiver,String name,Object[] arguments) throws Throwable {
        check("invoke",name,Object.class,0,withReceiver(receiver,arguments));
        return ScriptBytecodeAdapter.invokeMethodN(sender,receiver,name,arguments);
    }
    /** Keep the generated proxy's original InvokerHelper interception route. */
    public static Object invokeDelegate(Object receiver,String name,Object arguments) {
        if(!(arguments instanceof Object[] values))throw reject("candidate.trait-delegate","Expected native argument array");
        check("invoke",name,Object.class,0,withReceiver(receiver,values));
        return org.codehaus.groovy.runtime.InvokerHelper.invokeMethod(receiver,name,arguments);
    }
    public static Object getGroovyObjectProperty(Class<?> sender,GroovyObject receiver,String name) throws Throwable {
        check("getProperty",name,Object.class,0,new Object[]{receiver});
        return ScriptBytecodeAdapter.getGroovyObjectProperty(sender,receiver,name);
    }
    public static Object getProperty(Class<?> sender,Object receiver,String name) throws Throwable {
        check("getProperty",name,Object.class,0,new Object[]{receiver});
        return ScriptBytecodeAdapter.getProperty(sender,receiver,name);
    }
    public static void setGroovyObjectProperty(Object value,Class<?> sender,GroovyObject receiver,String name) throws Throwable {
        check("setProperty",name,void.class,0,new Object[]{receiver,value});
        ScriptBytecodeAdapter.setGroovyObjectProperty(value,sender,receiver,name);
    }
    public static void setProperty(Object value,Class<?> sender,Object receiver,String name) throws Throwable {
        check("setProperty",name,void.class,0,new Object[]{receiver,value});
        ScriptBytecodeAdapter.setProperty(value,sender,receiver,name);
    }
    public static void setField(Object value,Class<?> sender,Object receiver,String name) throws Throwable {
        if(receiver==null||receiver instanceof Class<?>||!policy.recipeMapMember(receiver.getClass().getName(),"max",name))
            throw reject("candidate.attribute", "setField " + name);
        synchronized(MaterialCallGate.class) {OBSERVATIONS.merge("setField "+receiver.getClass().getName()+"#"+name,1,Integer::sum);}
        // Native coercion, field access and exception unwrapping are unchanged.
        ScriptBytecodeAdapter.setField(value,sender,receiver,name);
    }
    public static Object getField(Class<?> sender,Object receiver,String name) throws Throwable {
        Class<?> type=receiver instanceof Class<?> represented?represented:receiver==null?null:receiver.getClass();
        GuestMembers members=type==null?null:GUEST_MEMBERS.get(type.getName());
        if(members==null&&type!=null)members=MaterialTraitClasses.members(type);
        if(members==null||!members.admits("getProperty",name,receiver instanceof Class<?>))
            throw reject("candidate.attribute","getField "+name);
        synchronized(MaterialCallGate.class) {OBSERVATIONS.merge("getField "+type.getName()+"#"+name,1,Integer::sum);}
        return ScriptBytecodeAdapter.getField(sender,receiver,name);
    }
    public static Object[] despreadList(Object[] arguments,Object[] spreads,int[] positions) {
        // Original array carriers and ordinary Groovy list literals do not
        // introduce arbitrary iteration callbacks. Safe invalid scalar spreads
        // still reach Groovy's own diagnostic and formatting. Leave malformed
        // carrier/position failures to the original helper as well.
        if(spreads!=null&&positions!=null)for(int i=0;i<Math.min(spreads.length,positions.length);i++) {
            Object spread=spreads[i];
            if(spread!=null&&!spread.getClass().isArray()&&spread.getClass()!=ArrayList.class
                    &&!(spread instanceof String)&&!(spread instanceof Boolean)&&!(spread instanceof Character)
                    &&!policy.numeric(spread.getClass().getName()))
                throw reject("candidate.bridge-spread","Unselected spread operand: "+spread.getClass().getName());
        }
        return ScriptBytecodeAdapter.despreadList(arguments,spreads,positions);
    }
    private static Object[] withReceiver(Object receiver,Object[] arguments) {
        Object[] checked=new Object[arguments.length+1];checked[0]=receiver;
        System.arraycopy(arguments,0,checked,1,arguments.length);return checked;
    }
    /** Guard compiler-emitted truth conversion without replacing Groovy truth.
     * Admission must precede the original helper's possible asBoolean callback. */
    public static boolean booleanUnbox(Object value) {
        check("cast","()",boolean.class,0,new Object[]{value});
        return org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.booleanUnbox(value);
    }
    public static double doubleUnbox(Object value) {
        check("cast","()",double.class,0,new Object[]{value});
        return org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.doubleUnbox(value);
    }
    public static float floatUnbox(Object value) {
        check("cast","()",float.class,0,new Object[]{value});
        return org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.floatUnbox(value);
    }
    public static short shortUnbox(Object value) {
        check("cast","()",short.class,0,new Object[]{value});
        return org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.shortUnbox(value);
    }
    /** Original compiler range construction may compare or advance endpoints.
     * Keep native numeric/character semantics, rejecting custom callbacks first. */
    public static List<?> createRange(Object from,Object to,boolean exclusiveLeft,boolean exclusiveRight) throws Throwable {
        check("range","..",List.class,0,new Object[]{from,to});
        return ScriptBytecodeAdapter.createRange(from,to,exclusiveLeft,exclusiveRight);
    }
    /** Preserve native numeric negation without admitting arbitrary negative()
     * callbacks or the helper's recursive collection conversion. */
    public static Object unaryMinus(Object value) throws Throwable {
        check("unary","minus",Object.class,0,new Object[]{value});
        return ScriptBytecodeAdapter.unaryMinus(value);
    }
    public static Object unaryPlus(Object value) throws Throwable {
        check("unary","plus",Object.class,0,new Object[]{value});
        return ScriptBytecodeAdapter.unaryPlus(value);
    }
    private static synchronized void check(String operation,String member,Class<?> returnType,int flags,Object[] arguments) {
        Object receiver=arguments.length==0?null:arguments[0];
        Class<?> type=receiver instanceof Class<?> represented?represented:receiver==null?null:receiver.getClass();
        String name=type==null?"null":type.getName();
        if(receiver!=null&&!(receiver instanceof Class<?>))OBSERVED_INSTANCE_TYPES.add(name);
        OBSERVATIONS.merge(observationKey(operation,name,member,GUEST_MEMBERS.containsKey(name)
                ||type!=null&&MaterialTraitClasses.members(type)!=null),1,Integer::sum);
        boolean permitted=false;
        if(operation.equals("range")) {
            permitted=arguments.length==2&&Arrays.stream(arguments).allMatch(value->value==null
                    ||policy.numeric(value.getClass().getName())||value.getClass()==Character.class);
        } else if(operation.equals("unary")) {
            permitted=arguments.length==1&&Set.of("minus","plus").contains(member)&&policy.numericOperation(member)
                    &&(receiver==null||policy.numeric(receiver.getClass().getName()));
        } else if(operation.equals("cast")) {
            // The selected native call site performs conversion unchanged. The
            // profile names exact additional reference-coercion pairs.
            permitted=receiver==null||returnType.isInstance(receiver)
                    ||returnType.isPrimitive()&&type!=null&&(policy.numeric(name)||type==Boolean.class||type==Character.class)
                    ||type!=null&&policy.referenceCast(name,returnType.getName())&&referenceCastOperands(policy,receiver,returnType);
        } else if(receiver==null&&(flags&IndyInterface.SAFE_NAVIGATION)!=0
                &&Set.of("invoke","getProperty").contains(operation)) {
            // Do not synthesize null or skip argument evaluation. Native Groovy
            // receives the same flags and already-evaluated arguments unchanged.
            permitted=true;
        } else if(type!=null) {
            var generated=MaterialTraitClasses.members(type);
            if(operation.equals("getProperty")&&member.equals("metaClass")&&receiver instanceof Class<?>
                    &&policy.nativeMetaClass(name))permitted=true;
            else if(operation.equals("setProperty")&&receiver instanceof groovy.lang.MetaClass meta
                    &&policy.nativeMetaClassMethod(meta.getTheClass().getName(),member))
                permitted=arguments.length==2&&arguments[1] instanceof groovy.lang.Closure<?>
                        &&GUEST_MEMBERS.containsKey(arguments[1].getClass().getName());
            else if(operation.equals("getProperty")&&member.equals("metaClass")&&receiver instanceof Class<?> &&policy.recipeMap(name))permitted=true;
            else if(operation.equals("setProperty")&&receiver instanceof groovy.lang.MetaClass meta
                    &&policy.recipeMapMember(meta.getTheClass().getName(),"modifyMax",member)) {
                permitted=arguments.length==2&&arguments[1] instanceof groovy.lang.Closure<?>
                        &&GUEST_MEMBERS.containsKey(arguments[1].getClass().getName());
            } else if(operation.equals("invoke")&&!(receiver instanceof Class<?>)&&policy.recipeMapMember(name,"modifyMax",member))permitted=true;
            else if(operation.equals("invoke")&&member.equals("is")&&policy.recipeMap(name)&&!(receiver instanceof Class<?>))
                permitted=arguments.length==2;
            else if(operation.equals("init"))permitted=GUEST_MEMBERS.containsKey(name)||policy.constructor(type);
            else if(operation.equals("invoke")&&member.equals("withTraits")&&!(receiver instanceof Class<?>)
                    &&GUEST_MEMBERS.containsKey(name)) {
                permitted=arguments.length>1&&Arrays.stream(arguments).skip(1).allMatch(value->value instanceof Class<?> trait&&MaterialTraitClasses.traitClass(trait));
            } else if(operation.equals("invoke")&&member.equals("tap")&&(generated!=null||GUEST_MEMBERS.containsKey(name))) {
                permitted=arguments.length==2&&arguments[1] instanceof groovy.lang.Closure<?>
                    &&GUEST_MEMBERS.containsKey(arguments[1].getClass().getName());
            } else if(generated!=null) {
                permitted=generated.admits(operation,member,receiver instanceof Class<?>);
                // A native trait proxy can reach the same global mapper as its
                // source class. Only the retained original binding is admitted;
                // Groovy still resolves the method and may fail natively.
                if(!permitted&&Set.of("invoke","getProperty").contains(operation)&&mapperBindings!=null)
                    permitted=mapperBindings.named(member);
            }
            else if(GUEST_MEMBERS.containsKey(name)) {
                permitted=GUEST_MEMBERS.get(name).admits(operation,member,receiver instanceof Class<?>);
                // An immediate zero-argument source closure uses native indy
                // "call", while the compiler declares only doCall. Groovy's
                // closure metaclass selects that guarded body (including its
                // implicit-it/default-argument or missing-method behavior).
                if(!permitted&&operation.equals("invoke")&&member.equals("call")&&arguments.length==1
                        &&receiver instanceof groovy.lang.Closure<?>
                        &&type.getSuperclass()==groovy.lang.Closure.class
                        &&org.codehaus.groovy.runtime.GeneratedClosure.class.isAssignableFrom(type))
                    permitted=GUEST_MEMBERS.get(name).admits("invoke","doCall",false);
                if(!permitted&&Set.of("getProperty","setProperty").contains(operation))
                    permitted=sourceBeanProperty(type,member,operation.equals("setProperty"),receiver instanceof Class<?>);
                if(!permitted&&operation.equals("invoke")&&receiver instanceof groovy.lang.Closure<?> closure)
                    permitted=declaredClosureOwner(closure,member);
                // Original Groovy resolves missing class/script calls through
                // its global closure binding. Admit only the exact native
                // identity installed by the host, never arbitrary missing calls.
                if(!permitted&&Set.of("invoke","getProperty").contains(operation)&&mapperBindings!=null)
                    permitted=mapperBindings.named(member);
                if(!permitted&&operation.equals("getProperty")&&propertyBindings!=null)
                    permitted=propertyBindings.stream().anyMatch(binding->binding.named(member));
                if(!permitted&&!RESERVED.contains(member)&&Set.of("getProperty","setProperty").contains(operation)&&scriptBindings!=null)
                    permitted=scriptBindings.admits(receiver,member,operation.equals("setProperty"));
                // DGM.tap clones the original closure and sets DELEGATE_FIRST.
                // Admit declared delegate members but leave Groovy to resolve
                // and dispatch them, including its original owner fallback.
                if(!permitted&&receiver instanceof groovy.lang.Closure<?> closure
                        &&closure.getResolveStrategy()==groovy.lang.Closure.DELEGATE_FIRST) {
                    Object delegate=closure.getDelegate();
                    GuestMembers members=delegate==null?null:MaterialTraitClasses.members(delegate.getClass());
                    if(members!=null)permitted=members.admits(operation,member,false);
                    // DGM.with uses the same native delegate strategy. Permit
                    // only already-admitted public native method families; the
                    // original closure/metaclass still selects and invokes them.
                    if(!permitted&&operation.equals("invoke"))permitted=nativeDelegate(policy,delegate,member,arguments);
                }
                if(operation.equals("getProperty")&&Set.of("log","eventManager","event_manager").contains(member))permitted=true;
                if(operation.equals("getProperty")&&member.equals("delegate")&&receiver instanceof groovy.lang.Closure<?> closure
                        &&closure.getDelegate()!=null&&policy.recipeMap(closure.getDelegate().getClass().getName()))permitted=true;
            } else if(operation.equals("invoke")&&mapperBindings!=null&&mapperBindings.call(receiver,member))permitted=true;
            else if(operation.equals("getProperty")&&propertyBindings!=null
                    &&propertyBindings.stream().anyMatch(binding->binding.read(receiver,member)))permitted=true;
            else if(operation.equals("getProperty")&&receiver instanceof Class<?> &&policy.nativeStaticField(type,member))permitted=true;
            else if(operation.equals("getProperty")&&receiver instanceof Class<?> &&policy.staticFields(name)) {
                if(Set.of("name","simpleName").contains(member))permitted=true;
                else try { permitted=Modifier.isStatic(type.getField(member).getModifiers()); }
                catch(NoSuchFieldException absent) {permitted=false;}
            } else if(operation.equals("invoke")&&policy.nativeMethod(type,member,receiver instanceof Class<?>)) {
                // String.replace(CharSequence, CharSequence) formats its inputs.
                // Preserve native replacement without arbitrary sequence callbacks.
                permitted=nativeMethodOperands(policy,receiver,member,arguments);
            }
            else if(operation.equals("invoke")&&policy.nativePrivateMethod(type,member,receiver instanceof Class<?>))permitted=true;
            else if(operation.equals("invoke")&&policy.nativeExtension(type,member,receiver instanceof Class<?>)) {
                permitted=nativeExtensionOperands(policy,type,member,arguments);
            }
            else if(operation.equals("getProperty")&&member.equals("length")&&type.isArray()
                    &&!(receiver instanceof Class<?>)&&arguments.length==1&&policy.listOperation(name,"length")) {
                var property=groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type).getMetaProperty(member);
                permitted=property!=null&&property.getClass()==groovy.lang.MetaArrayLengthProperty.class;
            }
            else if(operation.equals("setProperty")&&policy.nativeFieldWrite(type,receiver,member))
                permitted=arguments.length==2&&(arguments[1]==null||arguments[1] instanceof Character
                        ||arguments[1] instanceof Boolean||policy.numeric(arguments[1].getClass().getName()));
            else if(!(receiver instanceof Class<?>)&&!(receiver instanceof GroovyObject)
                    &&Set.of("getProperty","setProperty").contains(operation)
                    &&policy.nativeBeanProperty(type,receiver,member,operation.equals("setProperty")))permitted=true;
            else if(operation.equals("invoke")&&member.equals("asType")&&arguments.length==2
                    &&arguments[1] instanceof Class<?> target)
                permitted=policy.referenceCast(name,target.getName())&&referenceCastOperands(policy,receiver,target);
            else if(operation.equals("invoke")&&!(receiver instanceof Class<?>)&&policy.numeric(name)&&policy.numericOperation(member))
                permitted=Arrays.stream(arguments).skip(1).allMatch(value->numericOperand(policy,value));
            else if(operation.equals("invoke")&&policy.listOperation(name,member)) {
                if(member.equals("plus"))permitted=arguments.length==2&&collectionAdditionOperand(policy,arguments[1]);
                else if(member.equals("flatten"))permitted=arguments.length==1&&collectionFlattenOperand(receiver);
                else if(member.equals("join"))permitted=collectionJoinOperand(receiver)
                        &&(arguments.length==1||arguments.length==2&&(arguments[1]==null||arguments[1] instanceof String));
                else if(Set.of("size","iterator").contains(member))permitted=arguments.length==1;
                else if(member.equals("getAt"))permitted=arguments.length==2&&arguments[1] instanceof Integer;
                else permitted=arguments.length==2&&(arguments[1] instanceof groovy.lang.Closure<?>
                        &&GUEST_MEMBERS.containsKey(arguments[1].getClass().getName())
                        ||member.equals("grep")&&arguments[1] instanceof Class<?> trait&&MaterialTraitClasses.traitClass(trait));
            }
            // The selected GroovyScript InvokerHelperVisitor changes map literals
            // to fastutil, so a stock-Groovy LinkedHashMap assumption is wrong.
            else if(operation.equals("getProperty")&&policy.map(name)
                    &&!RESERVED.contains(member)&&!member.startsWith("$"))
                permitted=true;
            else if(operation.equals("invoke")&&policy.map(name)&&Set.of("get","getAt").contains(member))
                permitted=arguments.length==2&&(arguments[1] instanceof String||arguments[1] instanceof Integer
                    ||arguments[1] instanceof Enum<?> key&&policy.staticFields(key.getDeclaringClass().getName()));
        }
        if(!permitted)throw reject("candidate.dispatch",operation+" "+name+"#"+member);
    }
}
