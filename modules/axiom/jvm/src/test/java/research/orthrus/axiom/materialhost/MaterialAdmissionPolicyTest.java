package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import research.orthrus.axiom.Json;
import java.nio.charset.StandardCharsets;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialAdmissionPolicyTest {
    public static class NumericFields {
        protected float hardness=1.0f;
        public static float shared=2.0f;
        public final float fixed=3.0f;
        public Object reference;
    }
    public static class NumericFieldsChild extends NumericFields {}
    public static class OtherNumericFields extends NumericFieldsChild {}
    @Test void selectedGroovyScriptFieldAliasKeepsOriginalFieldAccessAndConversion() throws Exception {
        String path=System.getenv("AXIOM_EARLY_GROOVYSCRIPT_JAR");
        org.junit.jupiter.api.Assumptions.assumeTrue(path!=null&&!path.isBlank(),"Exact GroovyScript artifact is required");
        try(var loader=new java.net.URLClassLoader(new java.net.URL[]{java.nio.file.Path.of(path).toUri().toURL()},getClass().getClassLoader())) {
            var type=loader.loadClass("com.cleanroommc.groovyscript.sandbox.mapper.RemappedCachedField");
            var field=NumericFields.class.getDeclaredField("hardness");
            var mapped=(org.codehaus.groovy.reflection.CachedField)type.getConstructor(java.lang.reflect.Field.class,String.class).newInstance(field,"mappedHardness");
            assertEquals("mappedHardness",mapped.getName());assertEquals(field,mapped.getCachedField());
            var raw=input();raw.put("nativeWriteFields",Map.of(NumericFields.class.getName(),List.of("mappedHardness:F"),
                    NumericFieldsChild.class.getName(),List.of("mappedHardness:F")));var policy=new MaterialAdmissionPolicy(raw);
            var registry=groovy.lang.GroovySystem.getMetaClassRegistry();var original=registry.getMetaClass(NumericFieldsChild.class);
            var metadata=new groovy.lang.DelegatingMetaClass(original) {
                @Override public groovy.lang.MetaProperty getMetaProperty(String name){return name.equals("mappedHardness")?mapped:super.getMetaProperty(name);}
            };
            metadata.initialize();registry.setMetaClass(NumericFieldsChild.class,metadata);
            try {
                var bean=new NumericFieldsChild();assertTrue(policy.nativeFieldWrite(NumericFieldsChild.class,bean,"mappedHardness"));assertEquals(1f,bean.hardness);
                var direct=new org.codehaus.groovy.reflection.CachedField(field);var expected=new NumericFieldsChild();
                for(Object value:Arrays.asList(new java.math.BigDecimal("4.5"),-0d,Double.NaN,null,true)) {
                    Throwable failure=null;
                    try {direct.setProperty(expected,value);}catch(RuntimeException nativeFailure){failure=nativeFailure;}
                    if(failure==null)mapped.setProperty(bean,value);
                    else {var actual=assertThrows(RuntimeException.class,()->mapped.setProperty(bean,value));assertEquals(failure.getClass(),actual.getClass());}
                    assertEquals(Float.floatToRawIntBits(expected.hardness),Float.floatToRawIntBits(bean.hardness));
                }
            } finally {registry.setMetaClass(NumericFieldsChild.class,original);}
        }
    }
    @Test void nativeNumericFieldWriteRequiresExactOriginalMetadataAndBothOwners() throws Exception {
        var raw=input();var bean=new NumericFieldsChild();
        raw.put("nativeWriteFields",Map.of(NumericFields.class.getName(),List.of("hardness:F"),
                NumericFieldsChild.class.getName(),List.of("hardness:F")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(policy.nativeFieldWrite(NumericFieldsChild.class,bean,"hardness"));
        assertEquals(1f,bean.hardness);
        assertFalse(policy.nativeFieldWrite(OtherNumericFields.class,new OtherNumericFields(),"hardness"));
        assertFalse(policy.nativeFieldWrite(NumericFieldsChild.class,NumericFieldsChild.class,"hardness"));
        assertFalse(policy.nativeFieldWrite(NumericFields.class,bean,"hardness"));
        assertFalse(policy.nativeFieldWrite(NumericFieldsChild.class,bean,"class"));
        for(var fields:List.of(Map.of(NumericFieldsChild.class.getName(),List.of("hardness:F")),
                Map.of(NumericFieldsChild.class.getName(),List.of("hardness:D"),NumericFields.class.getName(),List.of("hardness:F")))) {
            raw.put("nativeWriteFields",fields);
            assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeFieldWrite(NumericFieldsChild.class,bean,"hardness"));
        }
        for(String name:List.of("shared","fixed","reference")) {
            raw.put("nativeWriteFields",Map.of(NumericFields.class.getName(),List.of(name+":F")));
            assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeFieldWrite(NumericFields.class,new NumericFields(),name));
        }
        for(String descriptor:List.of("Ljava/lang/Object;","[F","V","Z","C")) {
            raw.put("nativeWriteFields",Map.of(NumericFields.class.getName(),List.of("hardness:"+descriptor)));
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        }
        var registry=groovy.lang.GroovySystem.getMetaClassRegistry();
        var original=registry.getMetaClass(NumericFieldsChild.class);int[] calls={0};
        var replacement=new groovy.lang.DelegatingMetaClass(original) {
            @Override public groovy.lang.MetaProperty getMetaProperty(String name) {
                if(!name.equals("hardness"))return super.getMetaProperty(name);
                return new groovy.lang.MetaProperty(name,float.class) {
                    public Object getProperty(Object receiver){calls[0]++;throw new AssertionError("Unselected getter");}
                    public void setProperty(Object receiver,Object value){calls[0]++;throw new AssertionError("Unselected setter");}
                };
            }
        };
        replacement.initialize();registry.setMetaClass(NumericFieldsChild.class,replacement);
        try {assertFalse(policy.nativeFieldWrite(NumericFieldsChild.class,bean,"hardness"));assertEquals(0,calls[0]);}
        finally {registry.setMetaClass(NumericFieldsChild.class,original);}
        assertTrue(policy.nativeFieldWrite(NumericFieldsChild.class,bean,"hardness"));
        raw.remove("nativeWriteFields");assertFalse(new MaterialAdmissionPolicy(raw).nativeFieldWrite(NumericFieldsChild.class,bean,"hardness"));
    }
    @Test void nativeCeilingPreservesScalarConversionAndRefusesCustomNumberCallbacks() throws Exception {
        var raw=input();raw.put("nativeMethods",Map.of("java.lang.Math",List.of("ceil(D)D")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(policy.nativeMethod(Math.class,"ceil",true));
        assertFalse(policy.nativeMethod(Math.class,"floor",true));
        var guard=MaterialCallGate.class.getDeclaredMethod("nativeMethodOperands",
                MaterialAdmissionPolicy.class,Object.class,String.class,Object[].class);
        guard.setAccessible(true);
        Object[] values={new java.math.BigDecimal("0.5"),-0.5d,Double.NaN,Double.POSITIVE_INFINITY,2};
        for(Object value:values) {
            Object[] call={Math.class,value};
            assertEquals(true,guard.invoke(null,policy,Math.class,"ceil",call));
            assertSame(value,call[1]);
            double expected=(double)org.codehaus.groovy.runtime.InvokerHelper.invokeStaticMethod(Math.class,"ceil",new Object[]{value});
            double actual=(double)org.codehaus.groovy.runtime.InvokerHelper.invokeStaticMethod(Math.class,"ceil",new Object[]{call[1]});
            assertEquals(Double.doubleToRawLongBits(expected),Double.doubleToRawLongBits(actual));
        }
        int[] calls={0};
        Number hostile=new Number() {
            public int intValue(){calls[0]++;return 1;}
            public long longValue(){calls[0]++;return 1;}
            public float floatValue(){calls[0]++;return 1;}
            public double doubleValue(){calls[0]++;return 1;}
        };
        for(Object value:new Object[]{hostile,"0.5",null,new Object()})
            assertEquals(false,guard.invoke(null,policy,Math.class,"ceil",new Object[]{Math.class,value}));
        assertEquals(0,calls[0]);
        raw.put("nativeMethods",Map.of("java.lang.Math",List.of("ceil(F)F")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeMethod(Math.class,"ceil",true));
    }
    @Test void nativeStaticReadsRequireExactOriginalFieldMetadata() throws Exception {
        var raw=input();raw.put("nativeReadFields",Map.of("java.lang.System",List.of("out:Ljava/io/PrintStream;")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(policy.nativeStaticField(System.class,"out"));
        assertFalse(policy.nativeStaticField(System.class,"err"));assertFalse(policy.nativeStaticField(System.class,"in"));
        assertFalse(policy.nativeStaticField(String.class,"out"));
        assertFalse(policy.nativeBeanProperty(System.class,"out",true));
        raw.put("nativeReadFields",Map.of("java.lang.System",List.of("out:Ljava/lang/Object;")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeStaticField(System.class,"out"));
        raw.put("nativeMetaClassMethods",Map.of("fixture.Selected",List.of("callGroovySpawn")));
        var selected=new MaterialAdmissionPolicy(raw);
        assertTrue(selected.nativeMetaClassMethod("fixture.Selected","callGroovySpawn"));
        assertFalse(selected.nativeMetaClassMethod("fixture.Other","callGroovySpawn"));
        assertFalse(selected.nativeMetaClassMethod("fixture.Selected","other"));
    }
    @Test void nativeEntryPropertyUsesOriginalPublicInterfaceAccessor() throws Exception {
        var map=new it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap<String,Object>();
        Object value=new Object();map.put("selected",value);
        Object entry=map.entrySet().iterator().next();Class<?> type=entry.getClass();
        var raw=input();raw.put("nativeMethods",Map.of(type.getName(),List.of(
                "getKey()Ljava/lang/Object;","getValue()Ljava/lang/Object;")));
        var policy=new MaterialAdmissionPolicy(raw);
        var metadata=groovy.lang.GroovySystem.getMetaClassRegistry().getMetaClass(type);
        var key=(groovy.lang.MetaBeanProperty)metadata.getMetaProperty("key");
        assertEquals(Map.Entry.class,key.getGetter().getDeclaringClass().getTheClass());
        assertTrue(policy.nativeBeanProperty(type,"key",false));
        assertTrue(policy.nativeBeanProperty(type,"value",false));
        assertEquals("selected",key.getProperty(entry));
        assertSame(value,metadata.getMetaProperty("value").getProperty(entry));
        assertFalse(policy.nativeBeanProperty(type,"value",true));
        assertFalse(policy.nativeBeanProperty(type,"class",false));
        assertFalse(policy.nativeBeanProperty(AbstractMap.SimpleEntry.class,"key",false));
        raw.put("nativeMethods",Map.of(type.getName(),List.of("getKey()Ljava/lang/String;")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeBeanProperty(type,"key",false));
    }
    @Test void groovyHelperUsesOriginalDefinitionWhenNativeCollectionLoaderCannotSeeIt() throws Exception {
        Class<?> selected=it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap.class;
        try(var nativeLoader=new java.net.URLClassLoader(new java.net.URL[]{selected.getProtectionDomain().getCodeSource().getLocation()},null)) {
            Class<?> foreign=Class.forName(selected.getName(),true,nativeLoader);
            assertThrows(ClassNotFoundException.class,()->Class.forName("org.codehaus.groovy.runtime.DefaultGroovyMethods",false,nativeLoader));
            Object map=foreign.getConstructor().newInstance();
            foreign.getMethod("put",Object.class,Object.class).invoke(map,"alpha",1);
            foreign.getMethod("put",Object.class,Object.class).invoke(map,"beta",2);
            Object entries=foreign.getMethod("entrySet").invoke(map);
            String receiver=entries.getClass().getName();var raw=input();
            raw.put("nativeExtensions",Map.of(receiver,List.of(
                    "org/codehaus/groovy/runtime/DefaultGroovyMethods#toList(Ljava/lang/Iterable;)Ljava/util/List;")));
            var policy=new MaterialAdmissionPolicy(raw);
            assertTrue(policy.nativeExtension(entries.getClass(),"toList",false));
            assertFalse(policy.nativeExtension(entries.getClass(),"toList",true));
            assertFalse(policy.nativeExtension(entries.getClass(),"clear",false));
            assertEquals(List.of(Map.entry("alpha",1),Map.entry("beta",2)),org.codehaus.groovy.runtime.DefaultGroovyMethods.toList((Iterable<?>)entries));
            raw.put("nativeExtensions",Map.of(receiver,List.of(
                    "org/codehaus/groovy/runtime/DefaultGroovyMethods#toList(Ljava/util/Collection;)Ljava/util/List;")));
            assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeExtension(entries.getClass(),"toList",false));
        }
    }
    public static class PrivateFamily {
        private Object read() {throw new AssertionError("Admission invoked private method");}
        private Object read(int value) {throw new AssertionError("Admission invoked private method");}
        private static Object staticRead() {return null;}
        protected Object protectedRead() {return null;}
        public Object publicRead() {return null;}
    }
    public static class PrivateChild extends PrivateFamily {}
    @Test void privateAdmissionRequiresExactDeclaredFamilyAndInstanceWithoutInvocation() throws Exception {
        var raw=input();String owner=PrivateFamily.class.getName();
        raw.put("nativePrivateMethods",Map.of(owner,List.of("read()Ljava/lang/Object;","read(I)Ljava/lang/Object;")));
        var policy=new MaterialAdmissionPolicy(raw);
        for(int i=0;i<2;i++)assertTrue(policy.nativePrivateMethod(PrivateFamily.class,"read",false));
        assertFalse(policy.nativePrivateMethod(PrivateFamily.class,"read",true));
        assertFalse(policy.nativePrivateMethod(PrivateChild.class,"read",false));
        assertFalse(policy.nativePrivateMethod(PrivateFamily.class,"getClass",false));
        assertFalse(policy.nativeMethod(PrivateFamily.class,"read",false));
        assertFalse(policy.nativeBeanProperty(PrivateFamily.class,"read",false));
        for(var signatures:List.of(List.of("read()Ljava/lang/Object;"),List.of("read()I","read(I)Ljava/lang/Object;"))) {
            raw.put("nativePrivateMethods",Map.of(owner,signatures));
            var drifted=new MaterialAdmissionPolicy(raw);
            assertThrows(UnsupportedOperationException.class,()->drifted.nativePrivateMethod(PrivateFamily.class,"read",false));
        }
        assertTrue(policy.nativePrivateMethod(PrivateFamily.class,"read",false));
    }
    @Test void privateAdmissionCannotBorrowDifferentVisibilityStaticOrInheritedMembers() throws Exception {
        for(String name:List.of("staticRead","protectedRead","publicRead")) {
            var raw=input();raw.put("nativePrivateMethods",Map.of(PrivateFamily.class.getName(),List.of(name+"()Ljava/lang/Object;")));
            assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativePrivateMethod(PrivateFamily.class,name,false));
        }
        var inherited=input();inherited.put("nativePrivateMethods",Map.of(PrivateChild.class.getName(),List.of("read()Ljava/lang/Object;","read(I)Ljava/lang/Object;")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(inherited).nativePrivateMethod(PrivateChild.class,"read",false));
        var malformed=input();malformed.put("nativePrivateMethods",Map.of(PrivateFamily.class.getName(),List.of("read")));
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(malformed));
        assertFalse(new MaterialAdmissionPolicy(input()).nativePrivateMethod(PrivateFamily.class,"read",false));
    }
    @Test void observationCompactionRetainsNativeMembersAndGuestCalls() {
        assertEquals("getProperty fixture.Owner#<declared-fields>",MaterialCallGate.observationKey("getProperty","fixture.Owner","Aluminium",true));
        assertEquals("getProperty native.Owner#Aluminium",MaterialCallGate.observationKey("getProperty","native.Owner","Aluminium",false));
        assertEquals("invoke fixture.Owner#material",MaterialCallGate.observationKey("invoke","fixture.Owner","material",true));
        assertEquals("setProperty native.Owner#modifyMaxInputs",MaterialCallGate.observationKey("setProperty","native.Owner","modifyMaxInputs",false));
    }
    @Test void nativeDelegateUsesExactMethodPolicyWithoutInvokingIt() throws Exception {
        var raw=input();raw.put("nativeMethods",Map.of(Overloads.class.getName(),List.of("read(I)I","read(Ljava/lang/String;)Ljava/lang/String;")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(MaterialCallGate.nativeDelegate(policy,new Overloads(),"read",new Object[]{null,1}));
        assertFalse(MaterialCallGate.nativeDelegate(policy,Overloads.class,"read",new Object[]{null,1}));
        assertFalse(MaterialCallGate.nativeDelegate(policy,null,"read",new Object[]{null,1}));
        assertFalse(MaterialCallGate.nativeDelegate(policy,"other","read",new Object[]{null,1}));
        assertFalse(MaterialCallGate.nativeDelegate(policy,new Overloads(),"getClass",new Object[]{null}));
        raw.put("nativeMethods",Map.of("java.util.ArrayList",List.of("addAll(Ljava/util/Collection;)Z","addAll(ILjava/util/Collection;)Z","indexOf(Ljava/lang/Object;)I"),
                "it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap",List.of("put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;")));
        raw.put("nativeExtensions",Map.of("java.lang.String",List.of(
                "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
                "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;"),
                "it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap",List.of(
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/util/Map;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;")));
        var collections=new MaterialAdmissionPolicy(raw);var list=new ArrayList<>();
        var hostile=new ArrayList<>() {public Object[] toArray(){throw new AssertionError("Unselected conversion");}};
        assertTrue(MaterialCallGate.nativeDelegate(collections,list,"addAll",new Object[]{null,new ArrayList<>()}));
        assertFalse(MaterialCallGate.nativeDelegate(collections,list,"addAll",new Object[]{null,hostile}));
        assertTrue(MaterialCallGate.nativeDelegate(collections,list,"indexOf",new Object[]{null,"clinker"}));
        assertFalse(MaterialCallGate.nativeDelegate(collections,list,"indexOf",new Object[]{null,new Object()}));
        var map=new it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap<>();
        assertTrue(MaterialCallGate.nativeDelegate(collections,map,"put",new Object[]{null,"type",new Object()}));
        assertFalse(MaterialCallGate.nativeDelegate(collections,map,"put",new Object[]{null,new Object(),new Object()}));
        assertTrue(MaterialCallGate.nativeDelegate(collections,map,"putAt",new Object[]{null,"Steel",new Object()}));
        assertFalse(MaterialCallGate.nativeDelegate(collections,map,"putAt",new Object[]{null,new Object(),new Object()}));
        assertFalse(MaterialCallGate.nativeDelegate(collections,map,"putAt",new Object[]{null,"metaClass",new Object()}));
        var formatter=new Object(){public String toString(){throw new AssertionError("Unselected formatter");}};
        assertTrue(MaterialCallGate.nativeDelegate(collections,"native:","plus",new Object[]{null,"ore"}));
        assertTrue(MaterialCallGate.nativeDelegate(collections,"native:","plus",new Object[]{null,5}));
        assertFalse(MaterialCallGate.nativeDelegate(collections,"native:","plus",new Object[]{null,formatter}));
    }
    @Test void ClassReferenceDoesNotCountAsObservedInstance() {
        assertFalse(MaterialCallGate.observedInstance(Overloads.class.getName()));
    }
    private MaterialAdmissionPolicy stringPolicy() throws Exception {
        var raw=input();raw.put("nativeExtensions",Map.of("java.lang.String",List.of(
                "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
                "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;")));
        return new MaterialAdmissionPolicy(raw);
    }
    @Test void nativeStringConcatenationPreservesGStringCapturesAndCache() throws Exception {
        var policy=stringPolicy();var text=new org.codehaus.groovy.runtime.GStringImpl(new Object[]{"white"},new String[]{"lamp/","_lamp"});
        assertEquals("lamp/white_lamp",text.toString());
        var cache=text.getClass().getDeclaredField("cachedStringLiteral");cache.setAccessible(true);
        var cacheable=text.getClass().getDeclaredField("cacheable");cacheable.setAccessible(true);
        Object original=cache.get(text);assertNotNull(original);assertEquals(true,cacheable.get(text));
        assertTrue(MaterialCallGate.nativeDelegate(policy,"projectred-illumination:","plus",new Object[]{null,text}));
        assertSame(original,cache.get(text));assertEquals(true,cacheable.get(text));
        assertEquals("projectred-illumination:lamp/white_lamp",
                org.codehaus.groovy.runtime.StringGroovyMethods.plus("projectred-illumination:",(CharSequence)text));
        var nested=new org.codehaus.groovy.runtime.GStringImpl(new Object[]{text,text,3,null},new String[]{"",":",":",":",""});
        assertTrue(MaterialCallGate.nativeDelegate(policy,"nested:","plus",new Object[]{null,nested}));
        assertEquals("nested:lamp/white_lamp:lamp/white_lamp:3:null",
                org.codehaus.groovy.runtime.StringGroovyMethods.plus("nested:",(CharSequence)nested));
    }
    @Test void nativeStringConcatenationRefusesUnselectedInterpolationWithoutEvaluation() throws Exception {
        var policy=stringPolicy();int[] calls={0};
        Object formatter=new Object(){public String toString(){calls[0]++;throw new AssertionError("Unselected formatter invoked");}};
        var hostile=new org.codehaus.groovy.runtime.GStringImpl(new Object[]{formatter},new String[]{"",""});
        assertFalse(MaterialCallGate.nativeDelegate(policy,"x","plus",new Object[]{null,hostile}));
        var closure=new groovy.lang.Closure<Object>(this){public Object doCall(){calls[0]++;return "callback";}};
        var deferred=new org.codehaus.groovy.runtime.GStringImpl(new Object[]{closure},new String[]{"",""});
        assertFalse(MaterialCallGate.nativeDelegate(policy,"x","plus",new Object[]{null,deferred}));
        var subclass=new org.codehaus.groovy.runtime.GStringImpl(new Object[]{"value"},new String[]{"",""}) {
            @Override public Object[] getValues(){throw new AssertionError("Unselected getter invoked");}
        };
        assertFalse(MaterialCallGate.nativeDelegate(policy,"x","plus",new Object[]{null,subclass}));
        Object[] captured={"initial"};var cycle=new org.codehaus.groovy.runtime.GStringImpl(captured,new String[]{"",""});
        captured[0]=cycle;
        assertFalse(MaterialCallGate.nativeDelegate(policy,"x","plus",new Object[]{null,cycle}));
        assertEquals(0,calls[0]);
    }
    @Test void mapperPolicyIsExplicitAndDoesNotAdmitOtherRegistries() throws Exception {
        var raw=input();assertTrue(new MaterialAdmissionPolicy(raw).nativeObjectMappers().isEmpty());
        raw.put("nativeObjectMappers",List.of("material","metaitem","item","ore"));
        assertEquals(Set.of("material","metaitem","item","ore"),new MaterialAdmissionPolicy(raw).nativeObjectMappers());
        for(Object names:List.of(List.of("resource"),List.of("blockstate"),List.of("*"),List.of("material","material"))) {
            raw.put("nativeObjectMappers",names);
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        }
        raw.remove("nativeObjectMappers");
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
    }
    @Test void primitiveIntArrayAdmissionIsReadOnlyAndExact() throws Exception {
        var policy=new MaterialAdmissionPolicy(input());
        assertTrue(policy.listOperation(int[].class.getName(),"getAt"));
        assertFalse(policy.listOperation(int[].class.getName(),"putAt"));
        assertFalse(policy.listOperation(int[].class.getName(),"collect"));
        assertFalse(policy.listOperation(float[].class.getName(),"getAt"));
    }
    @Test void packHelperOperationsAreExactNativeFamilies() throws Exception {
        var policy=new MaterialAdmissionPolicy(input());
        assertTrue(policy.constructor(ArrayList.class));
        assertTrue(policy.nativeMethod(ArrayList.class,"add",false));
        assertTrue(policy.nativeMethod(ArrayList.class,"iterator",false));
        assertTrue(policy.nativeMethod(String.class,"toLowerCase",false));
        assertTrue(policy.nativeMethod(String.class,"startsWith",false));
        assertFalse(policy.nativeMethod(ArrayList.class,"clear",false));
        assertTrue(policy.referenceCast("java.lang.Integer","short"));
        assertTrue(policy.referenceCast("java.lang.Integer","java.lang.Short"));
        assertFalse(policy.referenceCast("java.lang.Integer","java.lang.Class"));
        assertTrue(policy.staticCall("java/lang/Long#valueOf(J)Ljava/lang/Long;"));
        assertTrue(policy.staticCall("java/lang/Float#valueOf(F)Ljava/lang/Float;"));
        assertTrue(policy.virtualCall("java/lang/Short#shortValue()S"));
        assertTrue(policy.virtualCall("java/util/Iterator#next()Ljava/lang/Object;"));
        assertFalse(policy.virtualCall("java/util/Iterator#remove()V"));
    }
    @Test void recipeMapExtensionsAreExactOwnerAndFourNativeFieldsOnly() throws Exception {
        var policy=new MaterialAdmissionPolicy(input());
        String owner="gregtech.api.recipes.RecipeMap";
        assertTrue(policy.recipeMap(owner));
        for(String suffix:List.of("Inputs","Outputs","FluidInputs","FluidOutputs")) {
            assertTrue(policy.recipeMapMember(owner,"max","max"+suffix));
            assertTrue(policy.recipeMapMember(owner,"modifyMax","modifyMax"+suffix));
        }
        assertFalse(policy.recipeMapMember(owner,"max","metaClass"));
        assertFalse(policy.recipeMapMember("java.lang.Class","max","maxInputs"));
        for(Object value:List.of(Map.of(),Map.of(owner,List.of("Inputs")),Map.of("java.lang.Class",List.of("Inputs")))) {
            var raw=input();raw.put("recipeMapExtensions",value);
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        }
    }
    @SuppressWarnings("unchecked")
    private Map<String,Object> input() throws Exception {
        try(var stream=getClass().getResourceAsStream("/axiom-material-admission.json")) {
            assertNotNull(stream);
            return (Map<String,Object>)Json.parse(new String(stream.readAllBytes(),StandardCharsets.UTF_8));
        }
    }
    public static class Overloads {
        public Overloads() {}
        public Overloads(int value) {}
        public int read(int value) {return value;}
        public String read(String value) {return value;}
    }
    public static class InheritedOverloads extends Overloads {}
    public static class UnselectedOverloads extends InheritedOverloads {}
    @Test void nativeFamilyRequiresEveryExactOverloadButDoesNotSelectOne() throws Exception {
        var raw=input();String owner=Overloads.class.getName();
        raw.put("nativeMethods",Map.of(owner,List.of("read(I)I","read(Ljava/lang/String;)Ljava/lang/String;")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(policy.nativeMethod(Overloads.class,"read",false));
        assertFalse(policy.nativeMethod(Overloads.class,"read",true));
        assertFalse(policy.nativeMethod(Overloads.class,"getClass",false));
        assertFalse(policy.nativeMethod(String.class,"read",false));
        raw.put("nativeMethods",Map.of(owner,List.of("read(I)I")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeMethod(Overloads.class,"read",false));
    }
    @Test void constructorFamilyRejectsAdditionalOrChangedSignatures() throws Exception {
        var raw=input();String owner=Overloads.class.getName();
        raw.put("constructors",Map.of(owner,List.of("()V","(I)V")));
        assertTrue(new MaterialAdmissionPolicy(raw).constructor(Overloads.class));
        raw.put("constructors",Map.of(owner,List.of("()V")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).constructor(Overloads.class));
    }
    @Test void cachedDescriptorsRemainPolicyAndReceiverSpecific() throws Exception {
        var raw=input();String owner=Overloads.class.getName();
        raw.put("constructors",Map.of(owner,List.of("()V","(I)V")));
        raw.put("nativeMethods",Map.of(owner,List.of("read(I)I","read(Ljava/lang/String;)Ljava/lang/String;")));
        var policy=new MaterialAdmissionPolicy(raw);
        for(int i=0;i<3;i++) {
            assertTrue(policy.constructor(Overloads.class));
            assertTrue(policy.nativeMethod(Overloads.class,"read",false));
            assertFalse(policy.nativeMethod(Overloads.class,"read",true));
        }
        raw.put("nativeMethods",Map.of(owner,List.of("read(I)I")));
        raw.put("constructors",Map.of(owner,List.of("()V")));
        var drifted=new MaterialAdmissionPolicy(raw);
        for(int i=0;i<2;i++) {
            assertThrows(UnsupportedOperationException.class,()->drifted.nativeMethod(Overloads.class,"read",false));
            assertThrows(UnsupportedOperationException.class,()->drifted.constructor(Overloads.class));
        }
        assertTrue(policy.nativeMethod(Overloads.class,"read",false));
    }
    @Test void mappedNameChecksNativeOverloadsWithoutInvokingOrRenamingThem() throws Exception {
        var raw=input();String owner=Overloads.class.getName();
        raw.put("nativeMethods",Map.of(owner,List.of("read(I)I","read(Ljava/lang/String;)Ljava/lang/String;")));
        var names=new LinkedHashMap<String,String>();names.put("authoredRead","read");
        raw.put("nativeMethodMappings",Map.of(owner,names));
        var policy=new MaterialAdmissionPolicy(raw);names.clear();
        assertTrue(policy.nativeMethod(Overloads.class,"authoredRead",false));
        assertFalse(policy.nativeMethod(Overloads.class,"authoredRead",true));
        assertFalse(policy.nativeMethod(String.class,"authoredRead",false));
        assertFalse(policy.nativeMethod(Overloads.class,"anotherAlias",false));
        raw.put("nativeMethodMappings",Map.of(owner,Map.of("authoredRead","read")));
        raw.put("nativeMethods",Map.of(owner,List.of("read(I)I")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeMethod(Overloads.class,"authoredRead",false));
        raw.put("nativeMethods",Map.of(InheritedOverloads.class.getName(),List.of("read(I)I","read(Ljava/lang/String;)Ljava/lang/String;")));
        var inherited=new MaterialAdmissionPolicy(raw);
        assertTrue(inherited.nativeMethod(InheritedOverloads.class,"authoredRead",false));
        assertFalse(inherited.nativeMethod(InheritedOverloads.class,"authoredRead",true));
        assertFalse(inherited.nativeMethod(UnselectedOverloads.class,"authoredRead",false));
        assertFalse(inherited.nativeMethod(Overloads.class,"authoredRead",false));
        assertFalse(inherited.nativeMethod(InheritedOverloads.class,"anotherAlias",false));
        raw.put("nativeMethods",Map.of(InheritedOverloads.class.getName(),List.of("read(I)I")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeMethod(InheritedOverloads.class,"authoredRead",false));
    }
    @Test void nativeMethodMappingNamesMustBeLiteralAndVersioned() throws Exception {
        var missing=input();missing.remove("nativeMethodMappings");
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(missing));
        for(String name:List.of("*","read()","read/path")) {
            var raw=input();raw.put("nativeMethodMappings",Map.of("fixture.Owner",Map.of(name,"read")));
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        }
    }
    public static class Extension {
        public static Overloads basic(Overloads receiver) { throw new AssertionError("Admission must not invoke an extension"); }
    }
    public static class NativeBean {
        public int fieldOnly;
        private int temperature=-1;
        public int getMaterialRGB() {throw new AssertionError("Admission invoked getter");}
        public void setMaterialRGB(int color) {throw new AssertionError("Admission invoked setter");}
        public boolean isEnabled() {throw new AssertionError("Admission invoked boolean getter");}
        public String getURL() {throw new AssertionError("Admission invoked acronym getter");}
        public void setOverloaded(int value) {}
        public void setOverloaded(String value) {}
    }
    public static class NativeBeanChild extends NativeBean {}
    public static class UnselectedBeanChild extends NativeBeanChild {}
    public static class EffectiveBean {
        private int calls;
        private RuntimeException failure;
        private final Object item=new Object();
        public Object getItem() {calls++;if(failure!=null)throw failure;return item;}
        public Object get(String name) {throw new AssertionError("Admission invoked generic get");}
    }
    public static class UnselectedEffectiveBean extends EffectiveBean {}
    @Test void effectiveNativeGetterMetadataPreservesOriginalDispatchAndFailure() throws Exception {
        var raw=input();raw.put("nativeMethods",Map.of(EffectiveBean.class.getName(),List.of("getItem()Ljava/lang/Object;")));
        var policy=new MaterialAdmissionPolicy(raw);var bean=new EffectiveBean();
        var registry=groovy.lang.GroovySystem.getMetaClassRegistry();
        assertNull(registry.getMetaClass(EffectiveBean.class).getMetaProperty("Item"));
        assertTrue(policy.nativeBeanProperty(EffectiveBean.class,bean,"Item",false));
        assertEquals(0,bean.calls);
        assertSame(bean.item,org.codehaus.groovy.runtime.InvokerHelper.getProperty(bean,"Item"));
        assertEquals(1,bean.calls);
        bean.failure=new IllegalStateException("original getter failure");
        assertTrue(policy.nativeBeanProperty(EffectiveBean.class,bean,"Item",false));
        assertEquals(1,bean.calls);
        assertSame(bean.failure,assertThrows(IllegalStateException.class,
                ()->org.codehaus.groovy.runtime.InvokerHelper.getProperty(bean,"Item")));
        assertEquals(2,bean.calls);
        assertFalse(policy.nativeBeanProperty(EffectiveBean.class,bean,"Item",true));
        assertFalse(policy.nativeBeanProperty(EffectiveBean.class,bean,"Unknown",false));
        assertFalse(policy.nativeBeanProperty(EffectiveBean.class,EffectiveBean.class,"Item",false));
        assertFalse(policy.nativeBeanProperty(EffectiveBean.class,new UnselectedEffectiveBean(),"Item",false));
        assertFalse(policy.nativeBeanProperty(UnselectedEffectiveBean.class,new UnselectedEffectiveBean(),"Item",false));
        raw.put("nativeMethods",Map.of(EffectiveBean.class.getName(),List.of("get(Ljava/lang/String;)Ljava/lang/Object;")));
        assertFalse(new MaterialAdmissionPolicy(raw).nativeBeanProperty(EffectiveBean.class,bean,"Item",false));
        assertEquals(2,bean.calls);
    }
    @Test void effectiveNativeGetterRefusesCustomSelectorWithoutCallingIt() throws Exception {
        var raw=input();raw.put("nativeMethods",Map.of(EffectiveBean.class.getName(),List.of("getItem()Ljava/lang/Object;")));
        var policy=new MaterialAdmissionPolicy(raw);var registry=groovy.lang.GroovySystem.getMetaClassRegistry();
        var prior=registry.getMetaClass(EffectiveBean.class);
        var custom=new groovy.lang.MetaClassImpl(EffectiveBean.class) {
            @Override public groovy.lang.MetaProperty getEffectiveGetMetaProperty(Class sender,Object receiver,String name,boolean callToSuper) {
                throw new AssertionError("Admission invoked custom selector");
            }
        };
        custom.initialize();
        try {
            registry.setMetaClass(EffectiveBean.class,custom);
            assertFalse(policy.nativeBeanProperty(EffectiveBean.class,new EffectiveBean(),"Item",false));
        } finally {registry.setMetaClass(EffectiveBean.class,prior);}
    }
    private Map<String,Object> beanPolicy() throws Exception {
        var raw=input();raw.put("nativeMethods",Map.of(NativeBean.class.getName(),List.of(
                "getMaterialRGB()I","setMaterialRGB(I)V","isEnabled()Z","getURL()Ljava/lang/String;",
                "setOverloaded(I)V","setOverloaded(Ljava/lang/String;)V")));
        return raw;
    }
    @Test void nativeBeanAdmissionUsesGroovyMetadataWithoutInvokingAccessors() throws Exception {
        var policy=new MaterialAdmissionPolicy(beanPolicy());
        assertTrue(policy.nativeBeanProperty(NativeBean.class,"materialRGB",false));
        assertTrue(policy.nativeBeanProperty(NativeBean.class,"materialRGB",true));
        assertTrue(policy.nativeBeanProperty(NativeBean.class,"enabled",false));
        assertTrue(policy.nativeBeanProperty(NativeBean.class,"URL",false));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"uRL",false));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"enabled",true));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"fieldOnly",false));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"overloaded",true));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"class",false));
        assertFalse(policy.nativeBeanProperty(String.class,"materialRGB",false));
    }
    @Test void nativeBeanReadCannotBorrowSetterAdmissionOrHideDescriptorDrift() throws Exception {
        var raw=beanPolicy();raw.put("nativeMethods",Map.of(NativeBean.class.getName(),List.of("setMaterialRGB(I)V")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"materialRGB",false));
        assertTrue(policy.nativeBeanProperty(NativeBean.class,"materialRGB",true));
        raw.put("nativeMethods",Map.of(NativeBean.class.getName(),List.of("getMaterialRGB()J")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeBeanProperty(NativeBean.class,"materialRGB",false));
    }
    @Test void nativeFieldReadsRequireExactOwnerDescriptorAndNeverAdmitWrites() throws Exception {
        var raw=beanPolicy();raw.put("nativeReadFields",Map.of(NativeBean.class.getName(),List.of("temperature:I")));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(policy.nativeBeanProperty(NativeBean.class,"temperature",false));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"temperature",true));
        assertFalse(policy.nativeBeanProperty(NativeBean.class,"fieldOnly",false));
        raw.put("nativeReadFields",Map.of(NativeBean.class.getName(),List.of("temperature:J")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeBeanProperty(NativeBean.class,"temperature",false));
        raw.put("nativeReadFields",Map.of(NativeBeanChild.class.getName(),List.of("fieldOnly:I"),NativeBean.class.getName(),List.of("fieldOnly:I")));
        var inherited=new MaterialAdmissionPolicy(raw);
        assertTrue(inherited.nativeBeanProperty(NativeBeanChild.class,"fieldOnly",false));
        assertFalse(inherited.nativeBeanProperty(NativeBeanChild.class,"fieldOnly",true));
        assertFalse(inherited.nativeBeanProperty(UnselectedBeanChild.class,"fieldOnly",false));
        raw.put("nativeReadFields",Map.of(NativeBeanChild.class.getName(),List.of("fieldOnly:I")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeBeanProperty(NativeBeanChild.class,"fieldOnly",false));
        raw.put("nativeReadFields",Map.of(NativeBeanChild.class.getName(),List.of("fieldOnly:J"),NativeBean.class.getName(),List.of("fieldOnly:I")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeBeanProperty(NativeBeanChild.class,"fieldOnly",false));
        raw.remove("nativeReadFields");
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
    }
    @Test void nativeStringExtensionRequiresAllReceiverApplicableOriginalOverloads() throws Exception {
        var raw=input();String prefix="org/codehaus/groovy/runtime/StringGroovyMethods#plus";
        raw.put("nativeExtensions",Map.of("java.lang.String",List.of(
                prefix+"(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
                prefix+"(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;")));
        assertTrue(new MaterialAdmissionPolicy(raw).nativeExtension(String.class,"plus",false));
        assertFalse(new MaterialAdmissionPolicy(raw).nativeExtension(StringBuffer.class,"plus",false));
        raw.put("nativeExtensions",Map.of("java.lang.String",List.of(prefix+"(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeExtension(String.class,"plus",false));
    }
    @Test void nativeExtensionAdmissionChecksOriginalStaticDescriptorWithoutInstallingIt() throws Exception {
        var raw=input();String owner=Extension.class.getName().replace('.','/');
        String receiver=Overloads.class.getName();String descriptor="basic(L"+receiver.replace('.','/')+";)L"+receiver.replace('.','/')+";";
        raw.put("nativeExtensions",Map.of(receiver,List.of(owner+"#"+descriptor)));
        var policy=new MaterialAdmissionPolicy(raw);
        assertTrue(policy.nativeExtension(Overloads.class,"basic",false));
        assertFalse(policy.nativeExtension(Overloads.class,"basic",true));
        assertFalse(policy.nativeExtension(Overloads.class,"getClass",false));
        assertFalse(policy.nativeExtension(String.class,"basic",false));
        raw.put("nativeExtensions",Map.of(receiver,List.of(owner+"#basic()Ljava/lang/Object;")));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeExtension(Overloads.class,"basic",false));
        raw.put("nativeExtensions",Map.of(receiver,List.of("missing/Original#"+descriptor)));
        assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeExtension(Overloads.class,"basic",false));
    }
    @Test void nativeExtensionPolicyIsExplicitAndCannotUseWildcards() throws Exception {
        var raw=input();raw.remove("nativeExtensions");
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        for(String signature:List.of("Owner#*()V","Owner#basic","Owner#basic()")) {
            var invalid=input();invalid.put("nativeExtensions",Map.of("fixture.Receiver",List.of(signature)));
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(invalid));
        }
    }
    @Test void callerMutationCannotChangeAnAdmittedPolicy() throws Exception {
        var raw=input();var names=new ArrayList<>(List.of("first"));
        raw.put("numericOperations",names);
        var policy=new MaterialAdmissionPolicy(raw);names.clear();raw.clear();
        assertTrue(policy.numericOperation("first"));assertFalse(policy.numericOperation("second"));
    }
    @Test void shapeWildcardsDuplicatesAndUnversionedPolicyRefuse() throws Exception {
        for(String key:List.of("schema","sourceRoots","sourceTransforms","compilerStaticCalls","nativeMethods")) {
            var raw=input();raw.remove(key);
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        }
        var unknown=input();unknown.put("allowEverything",true);
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(unknown));
        var transform=input();transform.put("sourceTransforms",List.of("groovy.transform.CompileStatic"));
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(transform));
        for(List<String> bad:List.of(List.of("*"),List.of("plus","plus"))) {
            var raw=input();raw.put("numericOperations",bad);
            assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(raw));
        }
    }
    @Test void compilerOperationsAreExactAndNativeFastutilMapIsExplicit() throws Exception {
        var policy=new MaterialAdmissionPolicy(input());
        assertTrue(policy.staticCall("java/lang/Integer#valueOf(I)Ljava/lang/Integer;"));
        assertFalse(policy.staticCall("java/lang/Integer#valueOf(Ljava/lang/String;)Ljava/lang/Integer;"));
        assertFalse(policy.staticCall("java/lang/System#getProperties()Ljava/util/Properties;"));
        assertTrue(policy.compilerAllocation("groovy/lang/Reference"));
        assertFalse(policy.compilerAllocation("java/lang/ProcessBuilder"));
        assertTrue(policy.map("it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap"));
        assertTrue(policy.map("java.util.LinkedHashMap"));
        assertFalse(policy.map("java.util.Hashtable"));
    }
    @Test void invalidDescriptorAndUnsafeSourceRootRefuse() throws Exception {
        var method=input();method.put("nativeMethods",Map.of("fixture.Type",List.of("read")));
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(method));
        var root=input();root.put("sourceRoots",List.of("../host/"));
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(root));
    }
    @Test void nativeLiteralAndStringCompilerVocabularyIsExplicit() throws Exception {
        var policy=new MaterialAdmissionPolicy(input());
        assertTrue(policy.compilerStaticField("java/lang/Integer#TYPE:Ljava/lang/Class;"));
        assertFalse(policy.compilerStaticField("java/lang/Integer#TYPE:Ljava/lang/Object;"));
        assertTrue(policy.staticCall("org/codehaus/groovy/runtime/ScriptBytecodeAdapter#createPojoWrapper(Ljava/lang/Object;Ljava/lang/Class;)Lorg/codehaus/groovy/runtime/wrappers/Wrapper;"));
        assertTrue(policy.compilerConstructor("java/math/BigDecimal#<init>(Ljava/lang/String;)V"));
        assertTrue(policy.compilerConstructor("java/math/BigInteger#<init>(Ljava/lang/String;)V"));
        assertTrue(policy.compilerConstructor("org/codehaus/groovy/runtime/GStringImpl#<init>([Ljava/lang/Object;[Ljava/lang/String;)V"));
        assertTrue(policy.referenceCast("org.codehaus.groovy.runtime.GStringImpl","java.lang.String"));
        assertFalse(policy.referenceCast("java.lang.Object","java.lang.String"));
        assertFalse(policy.referenceCast("java.lang.String","org.codehaus.groovy.runtime.GStringImpl"));
        var malformed=input();malformed.put("compilerStaticFields",List.of("java/lang/Integer#TYPE"));
        assertThrows(IllegalArgumentException.class,()->new MaterialAdmissionPolicy(malformed));
    }
}
