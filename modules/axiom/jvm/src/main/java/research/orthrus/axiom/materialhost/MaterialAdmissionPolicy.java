package research.orthrus.axiom.materialhost;

import java.lang.invoke.MethodType;
import java.lang.reflect.Modifier;
import java.util.*;

/** Immutable profile input, never chosen by candidate source. Policy membership
 * is admission, not evidence that every admitted semantic operation is qualified.
 * The caller must bind these exact bytes alongside the native program/runtime.
 */
public final class MaterialAdmissionPolicy {
    private static final Set<String> KEYS=Set.of("schema","profile","context","qualification","sourceRoots","sourceTransforms",
            "staticFields","constructors","nativeMethods","nativeMethodMappings","compilerStaticCalls","compilerVirtualCalls",
            "compilerConstructors","compilerStaticFields","referenceCasts","numericTypes","numericOperations","mapTypes","listOperations","recipeMapExtensions","nativeExtensions","nativeReadFields","nativeObjectMappers");
    private final Map<String,Set<String>> nativeMethods,constructors,listClosures,referenceCasts;
    private final Map<String,Set<String>> nativePrivateMethods;
    private final Map<String,Set<String>> nativeMetaClassMethods;
    private final Map<String,Map<String,String>> nativeMethodMappings;
    private final Set<String> staticFields,staticCalls,virtualCalls,compilerConstructors,numericTypes,numericOperations,mapTypes;
    private final String context;
    private final Set<String> compilerStaticFields;
    private final Map<String,Set<String>> recipeMapExtensions;
    private final Map<String,Set<String>> nativeExtensions;
    private final Map<String,Set<String>> nativeReadFields;
    private final Map<String,Set<String>> nativeWriteFields;
    private final Set<String> nativeObjectMappers;
    // Class identity and this policy are immutable in the worker. Cache only
    // descriptor-family verification, never Groovy metaclass selection, values,
    // native calls or constructor results. Failed/drifted checks are not cached.
    private final ClassValue<Map<String,Boolean>> checkedMethods=new ClassValue<>() {
        @Override protected Map<String,Boolean> computeValue(Class<?> type) {
            return new java.util.concurrent.ConcurrentHashMap<>();
        }
    };
    private final Set<Class<?>> checkedConstructors=java.util.concurrent.ConcurrentHashMap.newKeySet();

    public MaterialAdmissionPolicy(Map<String,Object> input) {
        var requiredKeys=new HashSet<>(input.keySet());requiredKeys.remove("nativePrivateMethods");
        requiredKeys.remove("nativeMetaClassMethods");
        requiredKeys.remove("nativeWriteFields");
        if(!requiredKeys.equals(KEYS)||!"axiom.material-admission.v1".equals(input.get("schema")))
            throw new IllegalArgumentException("Material admission policy shape differs");
        for(String key:List.of("profile","context","qualification"))string(input.get(key));
        context=(String)input.get("context");
        Set<String> roots=strings(input.get("sourceRoots"));
        if(!Set.of("groovy.transform.TupleConstructor","groovy.transform.RecordType","groovy.transform.Trait").containsAll(strings(input.get("sourceTransforms"))))
            throw new IllegalArgumentException("Unknown native source transform");
        if(roots.isEmpty()||roots.stream().anyMatch(root->!root.matches("[A-Za-z][A-Za-z0-9_]*/")))
            throw new IllegalArgumentException("Material admission source roots differ");
        nativeMethods=table(input.get("nativeMethods"));constructors=table(input.get("constructors"));
        nativePrivateMethods=table(input.getOrDefault("nativePrivateMethods",Map.of()));
        nativeMetaClassMethods=table(input.getOrDefault("nativeMetaClassMethods",Map.of()));
        for(var names:nativeMetaClassMethods.values())for(String name:names)
            if(!name.matches("[A-Za-z_$][A-Za-z0-9_$]*"))throw new IllegalArgumentException("Expected native metaclass method name");
        nativeMethodMappings=mappings(input.get("nativeMethodMappings"));
        listClosures=table(input.get("listOperations"));
        referenceCasts=table(input.get("referenceCasts"));
        recipeMapExtensions=table(input.get("recipeMapExtensions"));
        nativeExtensions=table(input.get("nativeExtensions"));
        nativeReadFields=table(input.get("nativeReadFields"));
        nativeWriteFields=table(input.getOrDefault("nativeWriteFields",Map.of()));
        for(var signatures:nativeWriteFields.values())for(String signature:signatures)
            if(!signature.matches("[A-Za-z_$][A-Za-z0-9_$]*:[BSIJFD]"))
                throw new IllegalArgumentException("Expected exact numeric native write field descriptor");
        nativeObjectMappers=strings(input.get("nativeObjectMappers"));
        if(!Set.of("material","metaitem","item","ore","fluid","liquid","recipemap").containsAll(nativeObjectMappers))
            throw new IllegalArgumentException("Unqualified native object mapper");
        for(var signatures:nativeReadFields.values())for(String signature:signatures)
            if(!signature.matches("[A-Za-z_$][A-Za-z0-9_$]*:[A-Za-z0-9_/$;\\[]+"))
                throw new IllegalArgumentException("Expected exact native read field descriptor");
        for(var signatures:nativeExtensions.values())for(String signature:signatures)
            if(!signature.matches("[A-Za-z0-9_/$]+#[A-Za-z_$][A-Za-z0-9_$]*\\([^)]*\\).+"))
                throw new IllegalArgumentException("Expected exact native extension descriptor");
        if(!recipeMapExtensions.keySet().equals(Set.of("gregtech.api.recipes.RecipeMap"))
                ||!recipeMapExtensions.values().iterator().next().equals(Set.of("Inputs","Outputs","FluidInputs","FluidOutputs")))
            throw new IllegalArgumentException("RecipeMap extension surface differs");
        compilerStaticFields=strings(input.get("compilerStaticFields"));
        staticFields=strings(input.get("staticFields"));staticCalls=strings(input.get("compilerStaticCalls"));
        virtualCalls=strings(input.get("compilerVirtualCalls"));compilerConstructors=strings(input.get("compilerConstructors"));
        numericTypes=strings(input.get("numericTypes"));numericOperations=strings(input.get("numericOperations"));
        mapTypes=strings(input.get("mapTypes"));
        for(var methods:List.of(nativeMethods,nativePrivateMethods))for(var rows:methods.values())
            for(String value:rows)if(!value.matches("[A-Za-z_$][A-Za-z0-9_$]*\\([^)]*\\).+"))
                throw new IllegalArgumentException("Expected exact native method descriptor");
        for(var rows:constructors.values())for(String value:rows)if(!value.matches("\\([^)]*\\)V"))
            throw new IllegalArgumentException("Expected exact constructor descriptor");
        for(var rows:List.of(staticCalls,virtualCalls,compilerConstructors))for(String value:rows)
            if(!value.matches("[A-Za-z0-9_/$]+#[A-Za-z0-9_$<>]+\\([^)]*\\).+"))
                throw new IllegalArgumentException("Expected exact compiler call descriptor");
        for(String value:compilerStaticFields)if(!value.matches("[A-Za-z0-9_/$]+#[A-Za-z0-9_$]+:[A-Za-z0-9_/$;\\[]+"))
            throw new IllegalArgumentException("Expected exact compiler field descriptor");
    }
    public String context() {return context;}
    public Set<String> nativeObjectMappers() {return nativeObjectMappers;}
    boolean nativePropertyValue(Class<?> type) {return nativeMethods.containsKey(type.getName());}
    boolean staticFields(String owner) {return staticFields.contains(owner);}
    boolean nativeMetaClass(String owner) {return nativeMetaClassMethods.containsKey(owner);}
    boolean nativeMetaClassMethod(String owner,String name) {return nativeMetaClassMethods.getOrDefault(owner,Set.of()).contains(name);}
    boolean nativeStaticField(Class<?> type,String name) {
        var expected=nativeReadFields.getOrDefault(type.getName(),Set.of());
        if(expected.stream().noneMatch(signature->signature.startsWith(name+":")))return false;
        var property=groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type).getMetaProperty(name);
        // System.setOut makes Groovy expose the field through a bean property.
        // Its original read still uses the field when there is no getter.
        org.codehaus.groovy.reflection.CachedField cached=property instanceof org.codehaus.groovy.reflection.CachedField direct?direct
                :property instanceof groovy.lang.MetaBeanProperty bean&&bean.getGetter()==null?bean.getField():null;
        if(cached==null)return false;
        var field=cached.getCachedField();
        if(field.getDeclaringClass()!=type||!Modifier.isPublic(field.getModifiers())||!Modifier.isStatic(field.getModifiers())
                ||!expected.contains(name+":"+field.getType().descriptorString()))
            throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name);
        return true;
    }
    boolean recipeMap(String owner) {return recipeMapExtensions.containsKey(owner);}
    boolean recipeMapMember(String owner,String prefix,String member) {
        return recipeMapExtensions.getOrDefault(owner,Set.of()).stream().anyMatch(suffix->member.equals(prefix+suffix));
    }
    boolean numeric(String owner) {return numericTypes.contains(owner);}
    boolean numericOperation(String name) {return numericOperations.contains(name);}
    boolean map(String owner) {return mapTypes.contains(owner);}
    boolean listOperation(String owner,String name) {return listClosures.getOrDefault(owner,Set.of()).contains(name);}
    boolean staticCall(String signature) {return staticCalls.contains(signature);}
    boolean virtualCall(String signature) {return virtualCalls.contains(signature);}
    boolean compilerConstructor(String signature) {return compilerConstructors.contains(signature);}
    boolean compilerAllocation(String owner) {return compilerConstructors.stream().anyMatch(value->value.startsWith(owner+"#<init>("));}
    boolean compilerStaticField(String signature) {return compilerStaticFields.contains(signature);}
    boolean referenceCast(String source,String target) {return referenceCasts.getOrDefault(source,Set.of()).contains(target);}

    boolean constructor(Class<?> type) {
        Set<String> expected=constructors.get(type.getName());
        if(expected==null||expected.isEmpty())return false;
        if(checkedConstructors.contains(type))return true;
        Set<String> actual=new HashSet<>();
        for(var constructor:type.getConstructors())actual.add(MethodType.methodType(void.class,constructor.getParameterTypes()).descriptorString());
        if(!actual.equals(expected))throw MaterialCallGate.reject("candidate.api-drift",type.getName()+" constructors");
        checkedConstructors.add(type);
        return true;
    }
    boolean nativeMethod(Class<?> type,String name,boolean staticReceiver) {
        // Mapping only selects the native descriptor family to inspect. The
        // original Groovy call site still receives the author's unmodified name
        // and performs its own mapping/overload selection and invocation.
        Set<String> declared=nativeMethods.get(type.getName());
        if(declared==null)return false;
        String nativeName=name;
        // Original Minecraft methods can be inherited by an explicitly
        // selected mod receiver. Bind aliases at their mapped native class;
        // this does not inherit receiver admission or skip its overload check.
        for(Class<?> owner=type;owner!=null;owner=owner.getSuperclass()) {
            String mapped=nativeMethodMappings.getOrDefault(owner.getName(),Map.of()).get(name);
            if(mapped!=null) {nativeName=mapped;break;}
        }
        String cacheKey=nativeName+(staticReceiver?":static":":instance");
        var cached=checkedMethods.get(type);
        Boolean checked=cached.get(cacheKey);
        if(checked!=null)return checked;
        Set<String> expected=new HashSet<>();
        for(String value:declared)if(value.startsWith(nativeName+"("))expected.add(value);
        if(expected.isEmpty())return false;
        // Do not implement Groovy overload selection. Every callable public
        // overload in this admitted family must match its pinned descriptor set;
        // the ORIGINAL native metaclass/expansion chooses the actual target.
        Set<String> actual=new HashSet<>();
        boolean receiverApplicable=false;
        for(var method:type.getMethods())if(method.getName().equals(nativeName)&&Modifier.isPublic(method.getModifiers()))
        {
            actual.add(nativeName+MethodType.methodType(method.getReturnType(),method.getParameterTypes()).descriptorString());
            if(!staticReceiver||Modifier.isStatic(method.getModifiers()))receiverApplicable=true;
        }
        if(!actual.equals(expected))throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name
                +"; expected "+new TreeSet<>(expected)+"; observed "+new TreeSet<>(actual));
        // A Class receiver must not borrow an instance member's admission and
        // fall through to java.lang.Class/Groovy reflection of the same name.
        cached.put(cacheKey,receiverApplicable);
        return receiverApplicable;
    }
    boolean nativePrivateMethod(Class<?> type,String name,boolean staticReceiver) {
        if(staticReceiver)return false;
        var expected=new HashSet<String>();
        for(String value:nativePrivateMethods.getOrDefault(type.getName(),Set.of()))
            if(value.startsWith(name+"("))expected.add(value);
        if(expected.isEmpty())return false;
        String cacheKey="private:"+name;
        var cached=checkedMethods.get(type);
        if(Boolean.TRUE.equals(cached.get(cacheKey)))return true;
        // A private family is confined to the exact declaring class. Public or
        // inherited overloads must not acquire admission through this route.
        for(var method:type.getMethods())if(method.getName().equals(name))
            throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name);
        var actual=new HashSet<String>();
        for(var method:type.getDeclaredMethods())if(method.getName().equals(name)) {
            int modifiers=method.getModifiers();
            if(!Modifier.isPrivate(modifiers)||Modifier.isStatic(modifiers)||Modifier.isNative(modifiers)
                    ||method.isBridge()||method.isSynthetic())
                throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name);
            actual.add(name+MethodType.methodType(method.getReturnType(),method.getParameterTypes()).descriptorString());
        }
        if(!actual.equals(expected))throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name);
        // No accessibility or metaclass mutation and no invocation. The
        // original Groovy call site still owns private access and dispatch.
        cached.put(cacheKey,true);return true;
    }
    boolean nativeFieldWrite(Class<?> type,Object receiver,String name) {
        if(receiver==null||receiver instanceof Class<?>||receiver instanceof groovy.lang.GroovyObject
                ||receiver.getClass()!=type)return false;
        var expected=nativeWriteFields.getOrDefault(type.getName(),Set.of());
        if(expected.stream().noneMatch(signature->signature.startsWith(name+":")))return false;
        // Inspect original mapped Groovy field metadata on every admission. Do
        // not invoke a setter, choose a conversion, or write the field here.
        var property=groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type).getMetaProperty(name);
        if(!(property instanceof org.codehaus.groovy.reflection.CachedField cached))return false;
        if(property.getClass()!=org.codehaus.groovy.reflection.CachedField.class) {
            // Selected GroovyScript changes only the reported name. Its field
            // access and conversion still inherit original CachedField methods.
            if(!property.getClass().getName().equals("com.cleanroommc.groovyscript.sandbox.mapper.RemappedCachedField")
                    ||property.getClass().getSuperclass()!=org.codehaus.groovy.reflection.CachedField.class)return false;
            try {
                for(String operation:List.of("getCachedField","getProperty","setProperty")) {
                    Class<?>[] parameters=switch(operation) {
                        case "getCachedField" -> new Class<?>[0];
                        case "getProperty" -> new Class<?>[]{Object.class};
                        default -> new Class<?>[]{Object.class,Object.class};
                    };
                    if(property.getClass().getMethod(operation,parameters).getDeclaringClass()!=org.codehaus.groovy.reflection.CachedField.class)return false;
                }
            } catch(NoSuchMethodException drift) {return false;}
        }
        var field=cached.getCachedField();
        String signature=name+":"+field.getType().descriptorString();
        if(Modifier.isStatic(field.getModifiers())||Modifier.isFinal(field.getModifiers())
                ||!expected.contains(signature)
                ||field.getDeclaringClass()!=type&&!nativeWriteFields.getOrDefault(field.getDeclaringClass().getName(),Set.of()).contains(signature))
            throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name);
        return true;
    }
    boolean nativeBeanProperty(Class<?> type,String name,boolean write) {
        return nativeBeanProperty(type,null,name,write);
    }
    boolean nativeBeanProperty(Class<?> type,Object receiver,String name,boolean write) {
        if(receiver!=null&&(receiver instanceof Class<?>||receiver instanceof groovy.lang.GroovyObject
                ||receiver.getClass()!=type))return false;
        if(!nativeMethods.containsKey(type.getName())&&!nativeReadFields.containsKey(type.getName()))return false;
        // Read the ORIGINAL Groovy metadata; do not derive get/is/set names,
        // choose overloads, coerce values or invoke an accessor here. Field reads
        // require their own exact descriptor; dynamic closure properties and
        // multiple-setter dispatch remain outside this bounded path.
        var metadata=groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type);
        var property=metadata.getMetaProperty(name);
        if(!write&&property instanceof org.codehaus.groovy.reflection.CachedField cached) {
            var field=cached.getCachedField();
            var expected=nativeReadFields.getOrDefault(type.getName(),Set.of());
            if(expected.stream().noneMatch(signature->signature.startsWith(name+":")))return false;
            if(field.getDeclaringClass()!=type&&!nativeReadFields.getOrDefault(field.getDeclaringClass().getName(),Set.of())
                        .contains(name+":"+field.getType().descriptorString())||Modifier.isStatic(field.getModifiers())
                    ||!expected.contains(name+":"+field.getType().descriptorString()))
                throw MaterialCallGate.reject("candidate.api-drift",type.getName()+"#"+name);
            return true;
        }
        groovy.lang.MetaMethod accessor;
        if(property instanceof groovy.lang.MetaBeanProperty bean)accessor=write?bean.getSetter():bean.getGetter();
        else if(property==null&&!write&&receiver!=null&&metadata instanceof groovy.lang.MetaClassImpl original) {
            // Groovy can resolve an effective getter (for example .Item) even
            // when its named property table has no entry. Inspect the original
            // selector's metadata without calling MetaProperty.getProperty.
            // Generic get/propertyMissing and custom selectors stay unadmitted.
            try {
                if(metadata.getClass().getMethod("getEffectiveGetMetaProperty",Class.class,Object.class,String.class,boolean.class)
                        .getDeclaringClass()!=groovy.lang.MetaClassImpl.class)return false;
            } catch(NoSuchMethodException absent) {return false;}
            var effective=original.getEffectiveGetMetaProperty(type,receiver,name,false);
            if(effective==null||effective.getClass()!=org.codehaus.groovy.runtime.metaclass.MethodMetaProperty.GetBeanMethodMetaProperty.class)
                return false;
            accessor=((org.codehaus.groovy.runtime.metaclass.MethodMetaProperty)effective).getMetaMethod();
        } else return false;
        if(!(accessor instanceof org.codehaus.groovy.reflection.CachedMethod cached))return false;
        var method=cached.getCachedMethod();
        if(!Modifier.isPublic(method.getModifiers())||Modifier.isStatic(method.getModifiers())
                ||method.getParameterCount()!=(write?1:0))return false;
        try {
            var implementation=type.getMethod(method.getName(),method.getParameterTypes());
            // Groovy exposes inaccessible native implementations through their
            // public interface accessor (for example fastutil's MapEntry).
            // Require that same virtual signature and the selected receiver's
            // full native family; leave actual property dispatch to Groovy.
            if(!implementation.equals(method)&&!(method.getDeclaringClass().isInterface()
                    &&method.getDeclaringClass().isAssignableFrom(type)
                    &&implementation.getReturnType()==method.getReturnType()))return false;
        } catch(NoSuchMethodException absent) {return false;}
        return nativeMethod(type,method.getName(),false);
    }
    boolean nativeExtension(Class<?> receiver,String name,boolean staticReceiver) {
        if(staticReceiver)return false;
        var expected=new TreeMap<String,Set<String>>();
        for(String signature:nativeExtensions.getOrDefault(receiver.getName(),Set.of())) {
            int split=signature.indexOf('#');String member=signature.substring(split+1);
            if(member.startsWith(name+"("))expected.computeIfAbsent(signature.substring(0,split),key->new HashSet<>()).add(member);
        }
        if(expected.isEmpty())return false;
        for(var family:expected.entrySet()) {
            try {
                ClassLoader loader=receiver.getClassLoader();
                // JDK receivers such as String use the bootstrap loader, which
                // cannot see the original Groovy extension library in this host.
                if(loader==null)loader=MaterialAdmissionPolicy.class.getClassLoader();
                // Native platform collections can live above Groovy's loader.
                // Its built-in helpers must use the same original definitions
                // as this gate, independent of the collection's defining loader.
                Class<?> owner=switch(family.getKey()) {
                    case "org/codehaus/groovy/runtime/DefaultGroovyMethods" -> org.codehaus.groovy.runtime.DefaultGroovyMethods.class;
                    case "org/codehaus/groovy/runtime/StringGroovyMethods" -> org.codehaus.groovy.runtime.StringGroovyMethods.class;
                    default -> Class.forName(family.getKey().replace('/','.'),false,loader);
                };
                var actual=new HashSet<String>();
                for(var method:owner.getDeclaredMethods())if(method.getName().equals(name)&&Modifier.isPublic(method.getModifiers())
                        &&Modifier.isStatic(method.getModifiers())&&method.getParameterCount()>0
                        &&method.getParameterTypes()[0].isAssignableFrom(receiver)) {
                    actual.add(name+MethodType.methodType(method.getReturnType(),method.getParameterTypes()).descriptorString());
                }
                if(!actual.equals(family.getValue()))throw MaterialCallGate.reject("candidate.api-drift",family.getKey()+"#"+name);
            } catch(ClassNotFoundException|LinkageError failure) {
                throw MaterialCallGate.reject("candidate.api-drift",family.getKey()+"#"+name+": "+failure);
            }
        }
        // Admission does not install, select or invoke an extension. Original
        // Groovy dispatch must still fail natively if its config disabled it.
        return true;
    }
    private static String string(Object value) {
        if(!(value instanceof String text)||text.isBlank()||text.length()>1024||text.indexOf('*')>=0)
            throw new IllegalArgumentException("Expected literal policy token");
        return text;
    }
    private static Set<String> strings(Object raw) {
        if(!(raw instanceof List<?> list)||list.size()>4096)throw new IllegalArgumentException("Expected bounded policy list");
        Set<String> result=new LinkedHashSet<>();
        for(Object value:list)if(!result.add(string(value)))throw new IllegalArgumentException("Duplicate policy member");
        return Collections.unmodifiableSet(result);
    }
    private static Map<String,Set<String>> table(Object raw) {
        if(!(raw instanceof Map<?,?> map)||map.size()>256)throw new IllegalArgumentException("Expected bounded policy table");
        Map<String,Set<String>> result=new LinkedHashMap<>();
        map.forEach((key,value)->result.put(string(key),strings(value)));
        return Collections.unmodifiableMap(result);
    }
    private static Map<String,Map<String,String>> mappings(Object raw) {
        if(!(raw instanceof Map<?,?> owners)||owners.size()>256)throw new IllegalArgumentException("Expected native method mapping table");
        var result=new LinkedHashMap<String,Map<String,String>>();
        owners.forEach((owner,value)-> {
            if(!(value instanceof Map<?,?> names)||names.size()>256)throw new IllegalArgumentException("Expected bounded native method names");
            var mapping=new LinkedHashMap<String,String>();
            names.forEach((from,to)-> {
                String alias=string(from),nativeName=string(to);
                if(!alias.matches("[A-Za-z_$][A-Za-z0-9_$]*")||!nativeName.matches("[A-Za-z_$][A-Za-z0-9_$]*"))
                    throw new IllegalArgumentException("Expected literal native method names");
                mapping.put(alias,nativeName);
            });
            result.put(string(owner),Map.copyOf(mapping));
        });
        return Map.copyOf(result);
    }
}
