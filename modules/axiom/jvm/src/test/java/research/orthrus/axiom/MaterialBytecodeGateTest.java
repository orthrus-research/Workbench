package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import research.orthrus.axiom.materialhost.MaterialBytecodeGate;
import research.orthrus.axiom.materialhost.MaterialCallGate;
import java.util.*;
import java.util.function.Consumer;
import static org.junit.jupiter.api.Assertions.*;

/** Closed bytecode inspection plus guarded native Groovy conversion regressions. */
class MaterialBytecodeGateTest {
    private byte[] candidate(String name,Consumer<MethodVisitor> body) {
        var writer=new ClassWriter(0);
        writer.visit(Opcodes.V1_8,Opcodes.ACC_PUBLIC,name.replace('.','/'),null,"java/lang/Object",null);
        var method=writer.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC,"test","()Ljava/lang/Object;",null,null);
        method.visitCode();body.accept(method);method.visitInsn(Opcodes.ARETURN);method.visitMaxs(4,0);method.visitEnd();writer.visitEnd();
        return writer.toByteArray();
    }
    @Test void bytecodeIsClosedAndNativeBootstrapIsWrappedWithoutChangingItsArguments() throws Throwable {
        var names=new LinkedHashSet<String>();
        for(String simple:List.of("Direct","Field","Handle","Allocation","Bootstrap","Good","Catch"))names.add("fixture."+simple);
        for(int i=0;i<8;i++)names.add("fixture.Bridge"+i);
        names.add("fixture.FakeBridge");
        names.add("fixture.NativeCompilerCalls");
        names.add("fixture.WrongInterfaceCall");
        names.add("fixture.BooleanBridge");
        names.add("fixture.DoubleBridge");
        names.add("fixture.FloatBridge");
        names.add("fixture.ShortBridge");
        names.add("fixture.CompiledTruth");
        names.add("fixture.CompiledSpread");
        names.add("fixture.SpreadBridge");
        for(int i=0;i<5;i++)names.add("fixture.WrongSpread"+i);
        names.add("fixture.DefaultArguments");
        names.add("fixture.CapturedAssignments");
        names.add("fixture.ClosureOwners");
        names.add("fixture.NativeExtensions");
        names.add("fixture.StoredTransforms");
        names.add("fixture.SourceBeans");
        for(int i=0;i<4;i++)names.add("fixture.WrongDefault"+i);
        names.add("fixture.RangeBridge");
        names.add("fixture.UnaryBridge");
        names.add("fixture.UnaryPlusBridge");
        for(int i=0;i<4;i++)names.add("fixture.WrongUnary"+i);
        for(int i=0;i<4;i++)names.add("fixture.WrongUnaryPlus"+i);
        for(int i=0;i<4;i++)names.add("fixture.WrongRange"+i);
        for(int i=0;i<5;i++)names.add("fixture.WrongBoolean"+i);
        for(int i=0;i<5;i++)names.add("fixture.WrongDouble"+i);
        for(int i=0;i<5;i++)names.add("fixture.WrongFloat"+i);
        for(int i=0;i<5;i++)names.add("fixture.WrongShort"+i);
        var policy=Json.object(Json.parse(Target.resource("/axiom-material-admission.json")));
        var casts=new LinkedHashMap<>(Json.object(policy.get("referenceCasts")));
        casts.put("java.util.ArrayList",List.of("boolean","[I"));
        casts.put("java.lang.Double",List.of("java.lang.Integer"));
        casts.put(AllowedTruth.class.getName(),List.of("boolean")); // fixture-specific callback, never a product default
        policy.put("referenceCasts",casts);
        policy.put("nativePrivateMethods",Map.of(PrivateAccessor.class.getName(),List.of("read()Ljava/lang/Object;")));
        policy.put("nativeMetaClassMethods",Map.of(NativeMetaSurface.class.getName(),List.of("callGroovySpawn")));
        policy.put("nativeReadFields",Map.of("java.lang.System",List.of("out:Ljava/io/PrintStream;")));
        policy.put("nativeWriteFields",Map.of(NativeNumericField.class.getName(),List.of("hardness:F"),
                NativeNumericChild.class.getName(),List.of("hardness:F")));
        var lists=new LinkedHashMap<>(Json.object(policy.get("listOperations")));
        lists.put("[Ljava.lang.Object;",List.of("length","getAt"));
        lists.put("[I",List.of("length","getAt"));
        var listOperations=new ArrayList<>((List<String>)lists.get("java.util.ArrayList"));
        listOperations.addAll(List.of("flatten","plus","iterator","join"));lists.put("java.util.ArrayList",listOperations);policy.put("listOperations",lists);
        var methods=new LinkedHashMap<>(Json.object(policy.get("nativeMethods")));
        var stringMethods=new ArrayList<>((List<String>)methods.get("java.lang.String"));
        stringMethods.addAll(List.of("replace(CC)Ljava/lang/String;","replace(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Ljava/lang/String;",
                "split(Ljava/lang/String;)[Ljava/lang/String;","split(Ljava/lang/String;I)[Ljava/lang/String;",
                "substring(I)Ljava/lang/String;","substring(II)Ljava/lang/String;",
                "toUpperCase()Ljava/lang/String;","toUpperCase(Ljava/util/Locale;)Ljava/lang/String;"));
        methods.put("java.lang.String",stringMethods);policy.put("nativeMethods",methods);
        var extensions=new LinkedHashMap<>(Json.object(policy.get("nativeExtensions")));
        extensions.put("java.lang.String",List.of(
                "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
                "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;"));
        extensions.put("java.util.ArrayList",List.of(
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#multiply(Ljava/lang/Iterable;Ljava/lang/Number;)Ljava/util/Collection;",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#multiply(Ljava/util/List;Ljava/lang/Number;)Ljava/util/List;",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#combinations(Ljava/lang/Iterable;)Ljava/util/List;",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#combinations(Ljava/lang/Iterable;Lgroovy/lang/Closure;)Ljava/util/List;"));
        extensions.put(NativeTapSurface.class.getName(),List.of(
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#tap(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#each(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#each(Ljava/lang/Iterable;Lgroovy/lang/Closure;)Ljava/lang/Iterable;"));
        methods.put(NativeTapSurface.class.getName(),List.of("accept(Ljava/lang/Object;)Ljava/lang/Object;","fail(Ljava/lang/Throwable;)Ljava/lang/Object;",
                "filter(Lgroovy/lang/Closure;)Ljava/util/List;"));
        extensions.put("it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap",List.of(
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",
                "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/util/Map;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;"));
        policy.put("nativeExtensions",extensions);
        var arrayMethods=new ArrayList<>((List<String>)methods.get("java.util.ArrayList"));
        arrayMethods.addAll(List.of("addAll(Ljava/util/Collection;)Z","addAll(ILjava/util/Collection;)Z","indexOf(Ljava/lang/Object;)I"));
        methods.put("java.util.ArrayList",arrayMethods);
        methods.put("it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap",List.of("put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;"));
        methods.put("java.io.PrintStream",List.of("println()V","println(Z)V","println(C)V","println(I)V","println(J)V",
                "println(F)V","println(D)V","println([C)V","println(Ljava/lang/String;)V","println(Ljava/lang/Object;)V"));
        methods.put("java.lang.Math",List.of("max(II)I","max(JJ)J","max(FF)F","max(DD)D","ceil(D)D"));
        var numericOperations=new ArrayList<>((List<String>)policy.get("numericOperations"));
        numericOperations.addAll(List.of("div","intdiv","mod","leftShift"));policy.put("numericOperations",numericOperations);
        var compilerFields=new ArrayList<>((List<String>)policy.get("compilerStaticFields"));
        compilerFields.add("java/lang/Float#TYPE:Ljava/lang/Class;");policy.put("compilerStaticFields",compilerFields);
        methods.put("net.minecraft.item.ItemStack",List.of(
                "transform(Lcom/cleanroommc/groovyscript/compat/vanilla/ItemStackTransformer;)Lnet/minecraft/item/ItemStack;",
                "transform(Lnet/minecraft/item/ItemStack;)Lnet/minecraft/item/ItemStack;"));
        methods.put("com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipeBuilder$Shapeless",List.of(
                "recipeFunction(Lgroovy/lang/Closure;)Lcom/cleanroommc/groovyscript/compat/vanilla/CraftingRecipeBuilder$Shapeless;"));
        MaterialCallGate.bind(names,policy);
        var gate=new MaterialBytecodeGate();
        int priorViolations=MaterialCallGate.violations().size();
        Map<String,Consumer<MethodVisitor>> denied=new LinkedHashMap<>();
        denied.put("Direct",method->method.visitMethodInsn(Opcodes.INVOKESTATIC,"java/lang/System","getProperties","()Ljava/util/Properties;",false));
        denied.put("Field",method->method.visitFieldInsn(Opcodes.GETSTATIC,"java/lang/System","out","Ljava/io/PrintStream;"));
        denied.put("Handle",method->method.visitLdcInsn(new Handle(Opcodes.H_INVOKESTATIC,"java/lang/invoke/MethodHandles","lookup","()Ljava/lang/invoke/MethodHandles$Lookup;",false)));
        denied.put("Allocation",method->method.visitTypeInsn(Opcodes.NEW,"java/lang/ProcessBuilder"));
        denied.put("Bootstrap",method->method.visitInvokeDynamicInsn("invoke","()Ljava/lang/Object;",
                new Handle(Opcodes.H_INVOKESTATIC,"untrusted/Bootstrap","bootstrap","()Ljava/lang/Object;",false)));
        for(var entry:denied.entrySet()) {
            String name="fixture."+entry.getKey();byte[] code=candidate(name,entry.getValue());
            assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,code),name);
        }
        assertEquals(priorViolations+5,MaterialCallGate.violations().size());
        String descriptor="(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/MethodType;Ljava/lang/String;I)Ljava/lang/invoke/CallSite;";
        byte[] original=candidate("fixture.Good",method->{
            method.visitLdcInsn(Type.getType("Lfixture/Good;"));
            method.visitInvokeDynamicInsn("invoke","(Ljava/lang/Class;)Ljava/lang/Object;",
                    new Handle(Opcodes.H_INVOKESTATIC,"org/codehaus/groovy/vmplugin/v8/IndyInterface","bootstrap",descriptor,false),"test",0);
        });
        byte[] input=original.clone();byte[] output=gate.processBytecode("fixture.Good",input);
        assertArrayEquals(original,input,"Input byte array must not be mutated");
        var parsed=new ClassNode();new ClassReader(output).accept(parsed,0);
        InvokeDynamicInsnNode call=(InvokeDynamicInsnNode)parsed.methods.getFirst().instructions.get(1);
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),call.bsm.getOwner());
        assertEquals("bootstrap",call.bsm.getName());assertEquals(descriptor,call.bsm.getDesc());
        assertEquals("invoke",call.name);assertArrayEquals(new Object[]{"test",0},call.bsmArgs);
        var writer=new ClassWriter(ClassWriter.COMPUTE_FRAMES);
        writer.visit(Opcodes.V1_8,Opcodes.ACC_PUBLIC,"fixture/Catch",null,"java/lang/Object",null);
        var method=writer.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC,"test","()Ljava/lang/Object;",null,null);
        Label start=new Label(),end=new Label(),handler=new Label();
        method.visitCode();method.visitTryCatchBlock(start,end,handler,"java/lang/Throwable");
        method.visitLabel(start);method.visitInsn(Opcodes.ACONST_NULL);method.visitInsn(Opcodes.ATHROW);
        method.visitLabel(end);method.visitLabel(handler);method.visitVarInsn(Opcodes.ASTORE,0);
        method.visitVarInsn(Opcodes.ALOAD,0);method.visitInsn(Opcodes.ARETURN);
        method.visitMaxs(1,1);method.visitEnd();writer.visitEnd();
        var caught=new ClassNode();new ClassReader(gate.processBytecode("fixture.Catch",writer.toByteArray())).accept(caught,0);
        var body=caught.methods.getFirst();var block=body.tryCatchBlocks.getFirst();
        assertEquals("java/lang/Throwable",block.type);
        AbstractInsnNode entry=block.handler;
        boolean frameBeforeHook=false;
        while(entry.getOpcode()<0) {frameBeforeHook|=entry instanceof FrameNode;entry=entry.getNext();}
        assertTrue(frameBeforeHook,"Handler stack-map frame must stay at its original entry");
        assertEquals(Opcodes.DUP,entry.getOpcode());
        var hook=assertInstanceOf(MethodInsnNode.class,entry.getNext());
        assertEquals("caught",hook.name);assertEquals("(Ljava/lang/Throwable;)V",hook.desc);
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),hook.owner);
        assertEquals(Opcodes.ASTORE,hook.getNext().getOpcode(),"Original handler still receives the original throwable");
        assertTrue(body.maxStack>=2);
        List<String> bridgeCalls=List.of(
            "invokeMethodOnCurrentN(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;",
            "invokeMethodN(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;",
            "getGroovyObjectProperty(Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;)Ljava/lang/Object;",
            "getProperty(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;",
            "getField(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;",
            "setGroovyObjectProperty(Ljava/lang/Object;Ljava/lang/Class;Lgroovy/lang/GroovyObject;Ljava/lang/String;)V",
            "setProperty(Ljava/lang/Object;Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;)V",
            "despreadList([Ljava/lang/Object;[Ljava/lang/Object;[I)[Ljava/lang/Object;");
        for(int i=0;i<bridgeCalls.size();i++) {
            String signature=bridgeCalls.get(i),name="fixture.Bridge"+i;
            String member=signature.substring(0,signature.indexOf('(')),desc=signature.substring(signature.indexOf('('));
            var bridge=new ClassNode();new ClassReader(candidate(name,m->m.visitMethodInsn(Opcodes.INVOKESTATIC,
                "org/codehaus/groovy/runtime/ScriptBytecodeAdapter",member,desc,false))).accept(bridge,0);
            var synthetic=bridge.methods.getFirst();synthetic.name="this$dist$invoke$1";
            synthetic.desc="(Ljava/lang/String;Ljava/lang/Object;)Ljava/lang/Object;";
            synthetic.access=Opcodes.ACC_PUBLIC|Opcodes.ACC_SYNTHETIC;
            var encoded=new ClassWriter(0);bridge.accept(encoded);
            var inspected=new ClassNode();new ClassReader(gate.processBytecode(name,encoded.toByteArray())).accept(inspected,0);
            var adapter=(MethodInsnNode)inspected.methods.getFirst().instructions.getFirst();
            assertEquals(MaterialCallGate.class.getName().replace('.','/'),adapter.owner);
            assertEquals(member,adapter.name);assertEquals(desc,adapter.desc);
            assertEquals(Opcodes.INVOKESTATIC,adapter.getOpcode());
        }
        byte[] fake=candidate("fixture.FakeBridge",m->m.visitMethodInsn(Opcodes.INVOKESTATIC,
            "org/codehaus/groovy/runtime/ScriptBytecodeAdapter","invokeMethodN",
            "(Ljava/lang/Class;Ljava/lang/Object;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/Object;",false));
        assertThrows(UnsupportedOperationException.class,()->gate.processBytecode("fixture.FakeBridge",fake),
            "Ordinary candidate methods must not acquire unguarded native adapter calls");
        byte[] numeric=candidate("fixture.NativeCompilerCalls",m->{
            m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,"java/lang/Short","shortValue","()S",false);
            m.visitMethodInsn(Opcodes.INVOKEINTERFACE,"java/util/Iterator","hasNext","()Z",true);
            m.visitMethodInsn(Opcodes.INVOKEINTERFACE,"java/util/Iterator","next","()Ljava/lang/Object;",true);
        });
        assertDoesNotThrow(()->gate.processBytecode("fixture.NativeCompilerCalls",numeric));
        byte[] unrelated=candidate("fixture.WrongInterfaceCall",m->m.visitMethodInsn(Opcodes.INVOKEINTERFACE,
                "java/util/Iterator","remove","()V",true));
        assertThrows(UnsupportedOperationException.class,()->gate.processBytecode("fixture.WrongInterfaceCall",unrelated));
        booleanConversion(gate);
        spreadArguments(gate);
        rangeConversion(gate);
        numericDivision();
        unaryNegation(gate);
        unaryPositive(gate);
        privateAccessor();
        recipeStringOperations();
        nativeCollectionMutations();
        defaultArguments(gate);
        capturedAssignments(gate);
        closureOwners(gate);
        nativeConsoleAndExtensions(gate);
        sourceBeanAccessors(gate);
        // The selected-artifact subcase must not skip the ordinary compiler
        // checks when the optional native conformance jar is unavailable.
        String nativeJar=System.getenv("AXIOM_EARLY_GROOVYSCRIPT_JAR");
        if(nativeJar!=null&&!nativeJar.isBlank())
            research.orthrus.axiom.materialhost.NativeRecipeFunctionObservationsTest.storedSourceTransforms(gate);
        assertThrows(IllegalStateException.class,()->MaterialCallGate.bind(names,policy),"No in-process reset for another candidate");
    }
    private void sourceBeanAccessors(MaterialBytecodeGate gate) throws Throwable {
        String source="""
                package fixture
                class SourceBeans {
                    public Object stored
                    public int reads
                    public int writes
                    public Throwable failure
                    public static Object static_stored
                    Object getPayload() { reads++; return stored }
                    void setPayload(Object value) { writes++; stored = value }
                    Object getReadOnly() { return stored }
                    Object getBroken() { throw failure }
                    static Object getStaticPayload() { return static_stored }
                    static String getName() { return "saved name" }
                }
                """;
        var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
        try(var loader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config)) {
            Class<?> type=loader.parseClass(source,"SourceBeans.groovy");Object bean=type.getConstructor().newInstance();
            Object value=new Object();MaterialCallGate.setProperty(value,getClass(),bean,"payload");
            assertSame(value,MaterialCallGate.getProperty(getClass(),bean,"payload"));
            assertSame(value,type.getField("stored").get(bean));
            assertEquals(1,type.getField("reads").get(bean));assertEquals(1,type.getField("writes").get(bean));
            assertSame(value,MaterialCallGate.getProperty(getClass(),bean,"readOnly"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.setProperty(null,getClass(),bean,"readOnly"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.getProperty(getClass(),type,"payload"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.getProperty(getClass(),type,"name"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.getProperty(getClass(),bean,"class"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.getProperty(getClass(),bean,"unknown"));
            type.getField("static_stored").set(null,value);
            assertSame(value,MaterialCallGate.getProperty(getClass(),type,"staticPayload"));
            var failure=new IllegalStateException("original getter failure");type.getField("failure").set(bean,failure);
            assertSame(failure,assertThrows(IllegalStateException.class,()->MaterialCallGate.getProperty(getClass(),bean,"broken")));
            var changed=new groovy.lang.ExpandoMetaClass(type,false,true);changed.initialize();
            changed.registerInstanceMethod("getPayload",new groovy.lang.Closure<Object>(this) {
                public Object doCall(){throw new AssertionError("Unselected accessor replacement");}
            });
            groovy.lang.GroovySystem.getMetaClassRegistry().setMetaClass(type,changed);
            try {assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.getProperty(getClass(),bean,"payload"));}
            finally {groovy.lang.GroovySystem.getMetaClassRegistry().removeMetaClass(type);}
        }
    }
    public static class NativeTapSurface implements Iterable<Object> {
        final List<Object> accepted=new ArrayList<>();
        int iteratorCalls;
        public java.util.Iterator<Object> iterator() {iteratorCalls++;return accepted.iterator();}
        public List<Object> filter(groovy.lang.Closure<?> callback) {return org.codehaus.groovy.runtime.DefaultGroovyMethods.findAll(accepted,callback);}
        public Object accept(Object value) {accepted.add(value);return value;}
        public Object fail(Throwable problem) throws Throwable {throw problem;}
    }
    public static class NativeNumericField { protected float hardness=1.0f; }
    public static class NativeNumericChild extends NativeNumericField {}
    public static class UnselectedNumericChild extends NativeNumericChild {}
    public static class NativeMetaSurface {}
    public static class OtherMetaSurface {}
    private void nativeConsoleAndExtensions(MaterialBytecodeGate gate) throws Throwable {
        var bytes=new java.io.ByteArrayOutputStream();var previous=System.out;
        try(var stream=new java.io.PrintStream(bytes,true,java.nio.charset.StandardCharsets.UTF_8);
                var other=new java.io.PrintStream(new java.io.ByteArrayOutputStream())) {
            System.setOut(stream);
            assertSame(stream,MaterialCallGate.getProperty(getClass(),System.class,"out"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.getProperty(getClass(),System.class,"err"));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.setProperty(other,getClass(),System.class,"out"));
            MaterialCallGate.invokeMethodN(getClass(),stream,"println",new Object[]{"native console witness"});
            assertEquals("native console witness"+System.lineSeparator(),bytes.toString(java.nio.charset.StandardCharsets.UTF_8));
            Object hostile=new Object(){public String toString(){throw new AssertionError("Unselected formatter");}};
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),stream,"println",new Object[]{hostile}));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),other,"println",new Object[]{"wrong stream"}));
            assertSame(stream,System.out);
        } finally {System.setOut(previous);}
        String source="""
                package fixture
                class NativeExtensions {
                    static Closure install(Class target) {
                        def callback = { value -> value }
                        target.metaClass.callGroovySpawn = callback
                        callback
                    }
                    static void wrong(Class target) { target.metaClass.other = { -> 1 } }
                    static void supplied(Class target, Closure callback) { target.metaClass.callGroovySpawn = callback }
                    static Closure tapCallback(Object marker) { return { receiver -> accept(marker); accept(receiver) } }
                    static Object suppliedTap(Object target, Closure callback) { target.tap(callback) }
                    static Object nestedTap(Object target, Closure callback) { target.tap { tap(callback) } }
                    static Object failingTap(Object target, Throwable problem) { target.tap { fail(problem) } }
                    static Closure eachCallback(Object sink) { return { value -> sink.accept(value) } }
                    static Object suppliedEach(Object target, Closure callback) { target.each(callback) }
                    static Object nestedEach(Object target, Closure callback) { target.tap { each(callback) } }
                    static Object failingEach(Object target, Throwable problem) { target.each { target.fail(problem) } }
                    static Closure filterCallback(Object sink) { return { value -> sink.accept(value); true } }
                    static Object suppliedFilter(Object target, Closure callback) { target.filter(callback) }
                    static Object nestedFilter(Object target, Closure callback) { target.tap { filter(callback) } }
                    static Object castProduct(float time, Object ratio) { time * (float) ratio }
                    static Object ceilInteger(Object value) { int count = Math.ceil(value); count }
                    static Object positive(Object value) { +value }
                    static Object shortValue(Object sink, Object value) { (short) sink.accept(value) }
                    static Object writeField(Object target, Object sink, Object value) { target.hardness = sink.accept(value) }
                    static Object integerArray(Object values) { values as int[] }
                    static Object castIntegerArray(Object values) { (int[]) values }
                    static Object arrayLength(Object values) { values.length }
                    static Object arrayAt(Object values, int index) { values[index] }
                    static Object listProduct(Object values, Object count) { (values * count).combinations() }
                }
                """;
        var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
        try(var loader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config)) {
            var type=loader.parseClass(source,"NativeExtensions.groovy");
            type.getMethod("install",Class.class).invoke(null,NativeMetaSurface.class);
            Object marker=new Object();assertSame(marker,org.codehaus.groovy.runtime.InvokerHelper.invokeMethod(
                    new NativeMetaSurface(),"callGroovySpawn",new Object[]{marker}));
            var wrong=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("wrong",Class.class).invoke(null,NativeMetaSurface.class));
            assertInstanceOf(UnsupportedOperationException.class,wrong.getCause());
            var owner=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("install",Class.class).invoke(null,OtherMetaSurface.class));
            assertInstanceOf(UnsupportedOperationException.class,owner.getCause());
            var external=new groovy.lang.Closure<Object>(this){public Object doCall(Object value){throw new AssertionError("Unselected callback");}};
            var supplied=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("supplied",Class.class,groovy.lang.Closure.class).invoke(null,NativeMetaSurface.class,external));
            assertInstanceOf(UnsupportedOperationException.class,supplied.getCause());
            try(var nativeLoader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader())) {
                var original=nativeLoader.parseClass(source,"NativeExtensions.groovy");
                for(Object value:Arrays.asList((byte)2,(short)3,4,Long.MAX_VALUE,-0d,3.5d,Float.NaN,
                        Double.POSITIVE_INFINITY,new java.math.BigDecimal("3.01"),new java.math.BigInteger("12345678901234567890"),'A',null,true)) {
                    var expectedTarget=new NativeNumericChild();var actualTarget=new NativeNumericChild();
                    var expectedSink=new NativeTapSurface();var actualSink=new NativeTapSurface();
                    Object expected=null;Throwable failure=null;
                    try {expected=original.getMethod("writeField",Object.class,Object.class,Object.class).invoke(null,expectedTarget,expectedSink,value);}
                    catch(java.lang.reflect.InvocationTargetException nativeFailure){failure=nativeFailure.getCause();}
                    if(failure==null)assertSame(expected,type.getMethod("writeField",Object.class,Object.class,Object.class).invoke(null,actualTarget,actualSink,value));
                    else {
                        var rejected=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("writeField",Object.class,Object.class,Object.class).invoke(null,actualTarget,actualSink,value));
                        assertEquals(failure.getClass(),rejected.getCause().getClass());assertEquals(failure.getMessage(),rejected.getCause().getMessage());
                    }
                    assertEquals(Float.floatToRawIntBits(expectedTarget.hardness),Float.floatToRawIntBits(actualTarget.hardness));
                    assertEquals(Arrays.asList(value),expectedSink.accepted);assertEquals(expectedSink.accepted,actualSink.accepted);
                }
                Number hostileFieldValue=new Number() {
                    public int intValue(){throw new AssertionError("Unselected conversion");}
                    public long longValue(){throw new AssertionError("Unselected conversion");}
                    public float floatValue(){throw new AssertionError("Unselected conversion");}
                    public double doubleValue(){throw new AssertionError("Unselected conversion");}
                };
                for(Object target:List.of(new NativeNumericChild(),new UnselectedNumericChild(),NativeNumericChild.class)) {
                    var rejected=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("writeField",Object.class,Object.class,Object.class).invoke(null,target,new NativeTapSurface(),hostileFieldValue));
                    assertInstanceOf(UnsupportedOperationException.class,rejected.getCause());
                }
                var unknown=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("writeField",Object.class,Object.class,Object.class).invoke(null,new UnselectedNumericChild(),new NativeTapSurface(),3d));
                assertInstanceOf(UnsupportedOperationException.class,unknown.getCause());
                for(Object[] values:List.of(new Object[]{100f,3},new Object[]{-0f,2d},new Object[]{Float.NaN,3},new Object[]{Float.MIN_VALUE,0.5d})) {
                    Object expected=original.getMethod("castProduct",float.class,Object.class).invoke(null,values);
                    Object actual=type.getMethod("castProduct",float.class,Object.class).invoke(null,values);
                    assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
                }
                for(Object value:List.of(-32769,32768,65535L,3.75d,'x')) {
                    var originalSink=new NativeTapSurface();var checkedSink=new NativeTapSurface();
                    assertEquals(original.getMethod("shortValue",Object.class,Object.class).invoke(null,originalSink,value),
                            type.getMethod("shortValue",Object.class,Object.class).invoke(null,checkedSink,value));
                    assertEquals(List.of(value),originalSink.accepted);assertEquals(originalSink.accepted,checkedSink.accepted);
                }
                for(Object value:List.of(0.5d,-0.5d,Double.NaN,Double.POSITIVE_INFINITY,2147483648d)) {
                    Object expected=original.getMethod("ceilInteger",Object.class).invoke(null,value);
                    Object actual=type.getMethod("ceilInteger",Object.class).invoke(null,value);
                    assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
                    assertSame(value,original.getMethod("positive",Object.class).invoke(null,value));
                    assertSame(value,type.getMethod("positive",Object.class).invoke(null,value));
                }
                for(String method:List.of("integerArray","castIntegerArray")) {
                    for(List<?> elements:List.of(List.of(),List.of(0,0,0,128,0,0),
                            List.of(-1,2147483648L,3.5d,'A',"7"),Arrays.asList(0,null),List.of("invalid"))) {
                        var values=new ArrayList<>(elements);
                        int[] expected=null;Throwable nativeFailure=null;
                        try {
                            expected=(int[])original.getMethod(method,Object.class).invoke(null,values);
                        } catch(java.lang.reflect.InvocationTargetException failed) {
                            nativeFailure=failed.getCause();
                        }
                        if(nativeFailure==null) {
                            var actual=(int[])type.getMethod(method,Object.class).invoke(null,values);
                            assertArrayEquals(expected,actual);
                        } else {
                            var actual=assertThrows(java.lang.reflect.InvocationTargetException.class,
                                    ()->type.getMethod(method,Object.class).invoke(null,values));
                            assertEquals(nativeFailure.getClass(),actual.getCause().getClass());
                            assertEquals(nativeFailure.getMessage(),actual.getCause().getMessage());
                        }
                        assertEquals(elements,values,"Native conversion must retain the source list");
                    }
                    var number=new UnselectedArrayNumber();
                    var rejected=assertThrows(java.lang.reflect.InvocationTargetException.class,
                            ()->type.getMethod(method,Object.class).invoke(null,new ArrayList<>(List.of(number))));
                    assertInstanceOf(UnsupportedOperationException.class,rejected.getCause());assertEquals(0,number.calls);
                    var collection=new UnselectedArrayList();
                    var outer=assertThrows(java.lang.reflect.InvocationTargetException.class,
                            ()->type.getMethod(method,Object.class).invoke(null,collection));
                    assertInstanceOf(UnsupportedOperationException.class,outer.getCause());assertEquals(0,collection.calls);
                }
                var dimension=new ArrayList<>(List.of(new Object(),new Object()));
                for(Object array:List.of(new Object[]{marker,null},new int[]{0,128,0},new Object[0],new int[0])) {
                    assertEquals(original.getMethod("arrayLength",Object.class).invoke(null,array),
                            type.getMethod("arrayLength",Object.class).invoke(null,array));
                    if(java.lang.reflect.Array.getLength(array)>0)
                        for(int index:List.of(0,-1))
                            assertEquals(original.getMethod("arrayAt",Object.class,int.class).invoke(null,array,index),
                                    type.getMethod("arrayAt",Object.class,int.class).invoke(null,array,index));
                }
                var lengthObject=new UnselectedLength();
                var wrongLength=assertThrows(java.lang.reflect.InvocationTargetException.class,
                        ()->type.getMethod("arrayLength",Object.class).invoke(null,lengthObject));
                assertInstanceOf(UnsupportedOperationException.class,wrongLength.getCause());assertEquals(0,lengthObject.calls);
                var unselectedArray=assertThrows(java.lang.reflect.InvocationTargetException.class,
                        ()->type.getMethod("arrayLength",Object.class).invoke(null,(Object)new Integer[]{1}));
                assertInstanceOf(UnsupportedOperationException.class,unselectedArray.getCause());
                var dimensions=new ArrayList<>(List.of(dimension));
                for(int count:List.of(0,1,2,3)) {
                    Object expected=original.getMethod("listProduct",Object.class,Object.class).invoke(null,dimensions,count);
                    Object actual=type.getMethod("listProduct",Object.class,Object.class).invoke(null,dimensions,count);
                    assertEquals(expected,actual);
                    assertSame(dimension,dimensions.getFirst());
                }
            }
            var target=new NativeTapSurface();
            var callback=(groovy.lang.Closure<?>)type.getMethod("tapCallback",Object.class).invoke(null,marker);
            Object originalDelegate=callback.getDelegate();int originalStrategy=callback.getResolveStrategy();
            assertSame(target,type.getMethod("suppliedTap",Object.class,groovy.lang.Closure.class).invoke(null,target,callback));
            assertEquals(List.of(marker,target),target.accepted);
            assertSame(originalDelegate,callback.getDelegate());assertEquals(originalStrategy,callback.getResolveStrategy());
            var problem=new IllegalArgumentException("original tap failure");
            var failed=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("failingTap",Object.class,Throwable.class).invoke(null,target,problem));
            assertSame(problem,failed.getCause());
            for(String method:List.of("suppliedTap","nestedTap")) {
                var rejected=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod(method,Object.class,groovy.lang.Closure.class).invoke(null,target,external));
                assertInstanceOf(UnsupportedOperationException.class,rejected.getCause());
                assertEquals(List.of(marker,target),target.accepted);
            }
            for(var entries:List.of(Arrays.asList(marker,null,marker),List.of())) {
                var iterable=new NativeTapSurface();iterable.accepted.addAll(entries);
                var sink=new NativeTapSurface();
                var each=(groovy.lang.Closure<?>)type.getMethod("eachCallback",Object.class).invoke(null,sink);
                assertSame(iterable,type.getMethod("suppliedEach",Object.class,groovy.lang.Closure.class).invoke(null,iterable,each));
                assertEquals(entries,sink.accepted);assertEquals(1,iterable.iteratorCalls);
                sink.accepted.clear();
                assertSame(iterable,org.codehaus.groovy.runtime.DefaultGroovyMethods.each(iterable,each));
                assertEquals(entries,sink.accepted);assertEquals(2,iterable.iteratorCalls);
                for(String method:List.of("suppliedEach","nestedEach")) {
                    var rejected=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod(method,Object.class,groovy.lang.Closure.class).invoke(null,iterable,external));
                    assertInstanceOf(UnsupportedOperationException.class,rejected.getCause());
                    assertEquals(2,iterable.iteratorCalls);assertEquals(entries,sink.accepted);
                }
            }
            var eachFailure=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod("failingEach",Object.class,Throwable.class).invoke(null,target,problem));
            assertSame(problem,eachFailure.getCause());assertEquals(1,target.iteratorCalls);
            var changedIterable=new NativeTapSurface() {public java.util.Iterator<Object> iterator(){throw new AssertionError("Unselected iterator override");}};
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),changedIterable,"each",new Object[]{callback}));
            var sink=new NativeTapSurface();
            var filter=(groovy.lang.Closure<?>)type.getMethod("filterCallback",Object.class).invoke(null,sink);
            assertEquals(target.accepted,type.getMethod("suppliedFilter",Object.class,groovy.lang.Closure.class).invoke(null,target,filter));
            assertEquals(target.accepted,sink.accepted);
            for(String method:List.of("suppliedFilter","nestedFilter")) {
                var rejected=assertThrows(java.lang.reflect.InvocationTargetException.class,()->type.getMethod(method,Object.class,groovy.lang.Closure.class).invoke(null,target,external));
                assertInstanceOf(UnsupportedOperationException.class,rejected.getCause());assertEquals(target.accepted,sink.accepted);
            }
        } finally {groovy.lang.GroovySystem.getMetaClassRegistry().removeMetaClass(NativeMetaSurface.class);}
    }
    private void nativeCollectionMutations() throws Throwable {
        var token=new Object();var source=new ArrayList<>(Arrays.asList(token,null));var target=new ArrayList<Object>();
        assertEquals(true,MaterialCallGate.invokeMethodN(getClass(),target,"addAll",new Object[]{source}));
        assertEquals(source,target);assertSame(token,target.getFirst());
        assertEquals(true,MaterialCallGate.invokeMethodN(getClass(),target,"addAll",new Object[]{0,source}));
        assertEquals(Arrays.asList(token,null,token,null),target);assertEquals(Arrays.asList(token,null),source);
        var nativeFailure=assertThrows(IndexOutOfBoundsException.class,()->new ArrayList<>().addAll(-1,source));
        var guardedFailure=assertThrows(RuntimeException.class,()->MaterialCallGate.invokeMethodN(getClass(),target,"addAll",new Object[]{-1,source}));
        assertEquals(nativeFailure.getClass(),guardedFailure.getClass());
        var hostile=new ArrayList<Object>() {
            public Object[] toArray(){throw new AssertionError("Unselected collection conversion");}
        };
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),target,"addAll",new Object[]{hostile}));
        assertEquals(Arrays.asList(token,null,token,null),target);
        Object equalityCallback=new Object(){public boolean equals(Object value){throw new AssertionError("Unselected equality callback");}};
        var names=new ArrayList<Object>(Arrays.asList(equalityCallback,null,"clinker","rebar","clinker"));
        for(Object name:Arrays.asList("clinker","rebar","missing",null))
            assertEquals(names.indexOf(name),MaterialCallGate.invokeMethodN(getClass(),names,"indexOf",new Object[]{name}));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),names,"indexOf",new Object[]{equalityCallback}));
        var changedList=new ArrayList<Object>() {public int indexOf(Object value){throw new AssertionError("Unselected list override");}};
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),changedList,"indexOf",new Object[]{"clinker"}));
        var map=new it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap<Object,Object>();
        assertNull(MaterialCallGate.invokeMethodN(getClass(),map,"put",new Object[]{"type",token}));
        assertSame(token,MaterialCallGate.invokeMethodN(getClass(),map,"put",new Object[]{"type",source}));
        assertSame(source,map.get("type"));
        Object hostileKey=new Object(){public int hashCode(){throw new AssertionError("Unselected key hashing");}};
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),map,"put",new Object[]{hostileKey,token}));
        assertEquals(1,map.size());
        var original=new it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap<Object,Object>();original.putAll(map);
        for(Object key:Arrays.asList("type","Steel",null)) {
            Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),original,"putAt",new Object[]{key,token});
            Object actual=MaterialCallGate.invokeMethodN(getClass(),map,"putAt",new Object[]{key,token});
            assertSame(expected,actual);assertEquals(original,map);assertSame(token,map.get(key));
        }
        var originalMeta=org.codehaus.groovy.runtime.InvokerHelper.getMetaClass(map);
        for(Object key:List.of(hostileKey,"metaClass","class","properties","$native","empty"))
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),map,"putAt",new Object[]{key,token}));
        var wrapped=new org.codehaus.groovy.runtime.wrappers.PojoWrapper(token,Object.class) {
            public Object unwrap(){throw new AssertionError("Unselected map value callback");}
        };
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),map,"putAt",new Object[]{"Steel",wrapped}));
        assertSame(originalMeta,org.codehaus.groovy.runtime.InvokerHelper.getMetaClass(map));
        assertEquals(original,map);
    }

    private void closureOwners(MaterialBytecodeGate gate) throws Throwable {
        String source="""
                package fixture
                class ClosureOwners {
                    int calls = 0
                    Object pick(Object value) { calls++; value }
                    Object run() { [0].collect { pick(7) } }
                    Object nested() { [0].collect { [0].collect { pick(9) } } }
                    static Object staticPick(Object value) { value }
                    static Object staticRun() { [0].collect { staticPick(12) } }
                    Object fail(Throwable problem) { throw problem }
                    Closure failing(Throwable problem) { return { fail(problem) } }
                    Closure callback() { return { pick(4) } }
                    Object immediate(Object marker) { return { pick(marker) }() }
                    Object implicitArgument() { return { it }() }
                    Object explicitEmpty() { return { -> 12 }() }
                    Object nestedImmediate(Object marker) { return { return { pick(marker) }() }() }
                    Object immediateFailure(Throwable problem) { return { fail(problem) }() }
                    Object wrongArity() { return { a, b -> pick(a) }() }
                    static Object supplied(Closure callback) { return callback() }
                }
                """;
        var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
        try(var guarded=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config);
                var nativeLoader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader())) {
            Class<?> checked=guarded.parseClass(source,"ClosureOwners.groovy");
            Class<?> original=nativeLoader.parseClass(source,"ClosureOwners.groovy");
            Object instance=checked.getConstructor().newInstance(),nativeInstance=original.getConstructor().newInstance();
            for(String name:List.of("run","nested","staticRun"))
                assertEquals(original.getMethod(name).invoke(nativeInstance),checked.getMethod(name).invoke(instance));
            assertEquals(2,checked.getMethod("getCalls").invoke(instance));
            var problem=new IllegalStateException("original owner failure");
            var failing=(groovy.lang.Closure<?>)checked.getMethod("failing",Throwable.class).invoke(instance,problem);
            Throwable failure=assertThrows(Throwable.class,failing::call);
            while(failure.getCause()!=null)failure=failure.getCause();assertSame(problem,failure);
            var callback=(groovy.lang.Closure<?>)checked.getMethod("callback").invoke(instance);
            var poison=new PoisonClosureDelegate();callback.setDelegate(poison);
            assertThrows(UnsupportedOperationException.class,callback::call);
            assertEquals(0,poison.calls);assertEquals(2,checked.getMethod("getCalls").invoke(instance));
            callback.setDelegate(callback.getOwner());callback.setResolveStrategy(groovy.lang.Closure.TO_SELF);
            assertThrows(UnsupportedOperationException.class,callback::call);
            callback.setResolveStrategy(groovy.lang.Closure.OWNER_FIRST);
            assertEquals(4,callback.call());assertEquals(3,checked.getMethod("getCalls").invoke(instance));
            Object immediate=checked.getConstructor().newInstance(),nativeImmediate=original.getConstructor().newInstance();
            Object marker=new Object();
            for(int repeat=0;repeat<3;repeat++) {
                for(String name:List.of("immediate","nestedImmediate")) {
                    assertSame(marker,original.getMethod(name,Object.class).invoke(nativeImmediate,marker));
                    assertSame(marker,checked.getMethod(name,Object.class).invoke(immediate,marker));
                }
                for(String name:List.of("implicitArgument","explicitEmpty"))
                    assertEquals(original.getMethod(name).invoke(nativeImmediate),checked.getMethod(name).invoke(immediate));
            }
            assertEquals(6,checked.getMethod("getCalls").invoke(immediate));
            assertEquals(6,original.getMethod("getCalls").invoke(nativeImmediate));
            for(var entry:List.of(Map.entry(checked,immediate),Map.entry(original,nativeImmediate))) {
                var failed=assertThrows(java.lang.reflect.InvocationTargetException.class,
                        ()->entry.getKey().getMethod("immediateFailure",Throwable.class).invoke(entry.getValue(),problem));
                assertSame(problem,failed.getCause(),"The original closure preserves the thrown object");
                var arity=assertThrows(java.lang.reflect.InvocationTargetException.class,
                        ()->entry.getKey().getMethod("wrongArity").invoke(entry.getValue()));
                assertInstanceOf(groovy.lang.MissingMethodException.class,arity.getCause());
            }
            int[] unselectedCalls={0};
            var unselected=new groovy.lang.Closure<Object>(this) {
                public Object doCall() {unselectedCalls[0]++;return marker;}
            };
            var refused=assertThrows(java.lang.reflect.InvocationTargetException.class,
                    ()->checked.getMethod("supplied",groovy.lang.Closure.class).invoke(null,unselected));
            assertInstanceOf(UnsupportedOperationException.class,refused.getCause());assertEquals(0,unselectedCalls[0]);
            callback.setDelegate(poison);
            var delegated=assertThrows(java.lang.reflect.InvocationTargetException.class,
                    ()->checked.getMethod("supplied",groovy.lang.Closure.class).invoke(null,callback));
            assertInstanceOf(UnsupportedOperationException.class,delegated.getCause());assertEquals(0,poison.calls);
        }
    }
    public static class PoisonClosureDelegate {
        int calls;
        public Object pick(Object value) {calls++;throw new AssertionError("Unselected delegate executed");}
    }
    public static class UnselectedArrayNumber extends Number {
        int calls;
        public int intValue() {calls++;return 128;}
        public long longValue() {calls++;return 128;}
        public float floatValue() {calls++;return 128;}
        public double doubleValue() {calls++;return 128;}
    }
    public static class UnselectedArrayList extends ArrayList<Object> {
        int calls;
        @Override public Iterator<Object> iterator() {calls++;throw new AssertionError("Unselected iteration");}
        @Override public Object[] toArray() {calls++;throw new AssertionError("Unselected conversion");}
    }
    public static class UnselectedLength {
        int calls;
        public int getLength() {calls++;return 2;}
    }
    private void capturedAssignments(MaterialBytecodeGate gate) throws Throwable {
        String source="""
                package fixture
                class CapturedAssignments {
                    static Object run() {
                        def value = 0
                        def read = { value }
                        value = 4
                        def first = [0].collect(read)
                        value = 7
                        [first, [0].collect(read)]
                    }
                }
                """;
        var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
        try(var guarded=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config);
                var nativeLoader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader())) {
            Class<?> checked=guarded.parseClass(source,"CapturedAssignments.groovy");
            Class<?> nativeClass=nativeLoader.parseClass(source,"CapturedAssignments.groovy");
            assertEquals(nativeClass.getMethod("run").invoke(null),checked.getMethod("run").invoke(null));
            assertEquals(List.of(List.of(4),List.of(7)),checked.getMethod("run").invoke(null));
        }
        var reference=new groovy.lang.Reference<Object>("old");Object value=new Object();
        MaterialCallGate.setReference(reference,value);assertSame(value,reference.get());
        MaterialCallGate.setReference(reference,null);assertNull(reference.get());
        assertThrows(NullPointerException.class,()->MaterialCallGate.setReference(null,value));
        int[] calls={0};var hostile=new groovy.lang.Reference<Object>() {
            public void set(Object value){calls[0]++;throw new AssertionError("Unselected setter invoked");}
        };
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.setReference(hostile,value));
        assertEquals(0,calls[0]);
    }

    private void defaultArguments(MaterialBytecodeGate gate) throws Throwable {
        String source="""
                package fixture
                class DefaultArguments {
                    int calls
                    Throwable failure
                    def get(int amount = 1000) { calls = calls + 1; if (amount == 0) throw failure; amount }
                    static def quantity(int amount = 7) { amount }
                }
                """;
        var original=new LinkedHashMap<String,byte[]>();
        var config=new org.codehaus.groovy.control.CompilerConfiguration();
        config.setBytecodePostprocessor((name,bytes)->{original.put(name,bytes.clone());return gate.processBytecode(name,bytes);});
        try(var guarded=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config);
                var nativeLoader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader())) {
            Class<?> checked=guarded.parseClass(source,"DefaultArguments.groovy");
            Class<?> nativeClass=nativeLoader.parseClass(source,"DefaultArguments.groovy");
            Object instance=checked.getConstructor().newInstance(),nativeInstance=nativeClass.getConstructor().newInstance();
            assertEquals(nativeClass.getMethod("get").invoke(nativeInstance),checked.getMethod("get").invoke(instance));
            assertEquals(nativeClass.getMethod("get",int.class).invoke(nativeInstance,23),checked.getMethod("get",int.class).invoke(instance,23));
            assertEquals(2,checked.getMethod("getCalls").invoke(instance));
            assertEquals(nativeClass.getMethod("quantity").invoke(null),checked.getMethod("quantity").invoke(null));
            var failure=new IllegalStateException("native default target failure");
            checked.getMethod("setFailure",Throwable.class).invoke(instance,failure);
            assertSame(failure,assertThrows(java.lang.reflect.InvocationTargetException.class,()->checked.getMethod("get",int.class).invoke(instance,0)).getCause());
        }
        for(int i=0;i<4;i++) {
            var node=new ClassNode();new ClassReader(original.get("fixture.DefaultArguments")).accept(node,0);
            String old=node.name;node.name="fixture/WrongDefault"+i;
            for(var method:node.methods)for(var instruction:method.instructions) {
                if(instruction instanceof MethodInsnNode call&&call.owner.equals(old))call.owner=node.name;
                if(instruction instanceof FieldInsnNode field&&field.owner.equals(old))field.owner=node.name;
            }
            var method=node.methods.stream().filter(m->m.name.equals("get")&&m.desc.equals("()Ljava/lang/Object;")).findFirst().orElseThrow();
            MethodInsnNode call=null;for(var instruction:method.instructions)if(instruction instanceof MethodInsnNode m&&m.name.equals("get"))call=m;
            assertNotNull(call);
            switch(i) {
                case 0 -> method.visibleAnnotations=null;
                case 1 -> {call.setOpcode(Opcodes.INVOKEINTERFACE);call.itf=true;}
                case 2 -> call.owner="java/lang/Object";
                case 3 -> call.desc="(J)Ljava/lang/Object;";
            }
            var writer=new ClassWriter(0);node.accept(writer);
            var refusal=assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(node.name.replace('/','.'),writer.toByteArray()));
            assertTrue(refusal.getMessage().contains("get: "),refusal.getMessage());
        }
    }

    private void recipeStringOperations() throws Throwable {
        String value="weapons_grade_uranium";
        assertEquals(value.replace("uranium",""),MaterialCallGate.invokeMethodN(getClass(),value,"replace",new Object[]{"uranium",""}));
        assertEquals(value.replace('_','-'),MaterialCallGate.invokeMethodN(getClass(),value,"replace",new Object[]{'_','-'}));
        assertArrayEquals(value.split("_"),(String[])MaterialCallGate.invokeMethodN(getClass(),value,"split",new Object[]{"_"}));
        assertEquals(value.substring(8),MaterialCallGate.invokeMethodN(getClass(),value,"substring",new Object[]{8}));
        assertEquals(value.substring(0,7),MaterialCallGate.invokeMethodN(getClass(),value,"substring",new Object[]{0,7}));
        assertEquals(value.toUpperCase(),MaterialCallGate.invokeMethodN(getClass(),value,"toUpperCase",new Object[0]));
        assertEquals("i".toUpperCase(java.util.Locale.forLanguageTag("tr")),MaterialCallGate.invokeMethodN(getClass(),"i","toUpperCase",new Object[]{java.util.Locale.forLanguageTag("tr")}));
        assertThrows(IndexOutOfBoundsException.class,()->MaterialCallGate.invokeMethodN(getClass(),value,"substring",new Object[]{-1}));
        for(Object scalar:Arrays.asList(null,"ore",5,true,'x'))
            assertEquals(org.codehaus.groovy.runtime.StringGroovyMethods.plus("native:",scalar),MaterialCallGate.invokeMethodN(getClass(),"native:","plus",new Object[]{scalar}));
        assertThrows(NullPointerException.class,()->MaterialCallGate.invokeMethodN(getClass(),value,"replace",new Object[]{null,""}));
        int[] callbacks={0};
        var hostile=new CharSequence() {
            public int length(){callbacks[0]++;throw new AssertionError("Unselected sequence length");}
            public char charAt(int i){callbacks[0]++;throw new AssertionError("Unselected character");}
            public CharSequence subSequence(int a,int b){callbacks[0]++;throw new AssertionError("Unselected subsequence");}
            public String toString(){callbacks[0]++;throw new AssertionError("Unselected formatting");}
        };
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),value,"replace",new Object[]{hostile,""}));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),value,"replace",new Object[]{"_",hostile}));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),value,"plus",new Object[]{hostile}));
        var words=new ArrayList<>(Arrays.asList("Weapons","Grade",null));
        assertEquals(org.codehaus.groovy.runtime.DefaultGroovyMethods.join(words,null),MaterialCallGate.invokeMethodN(getClass(),words,"join",new Object[0]));
        assertEquals(org.codehaus.groovy.runtime.DefaultGroovyMethods.join(words,"_"),MaterialCallGate.invokeMethodN(getClass(),words,"join",new Object[]{"_"}));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),words,"join",new Object[]{hostile}));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),new ArrayList<>(List.of(hostile)),"join",new Object[0]));
        assertEquals(0,callbacks[0]);
    }
    private void numericDivision() throws Throwable {
        for(Object[] values:List.of(new Object[]{7,2},new Object[]{3L,9L},new Object[]{Float.NaN,-0f},new Object[]{0d,-0d})) {
            Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),Math.class,"max",values);
            Object actual=MaterialCallGate.invokeMethodN(getClass(),Math.class,"max",values);
            assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
        }
        for(Object[] values:List.of(new Object[]{7,2},new Object[]{1,3},new Object[]{7L,2d},
                new Object[]{new java.math.BigDecimal("3.5"),2})) {
            Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),values[0],"div",new Object[]{values[1]});
            Object actual=MaterialCallGate.invokeMethodN(getClass(),values[0],"div",new Object[]{values[1]});
            assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
        }
        assertThrows(ArithmeticException.class,()->MaterialCallGate.invokeMethodN(getClass(),1,"div",new Object[]{0}));
        for(Object[] values:List.of(new Object[]{4,0},new Object[]{4,4},new Object[]{4,32},new Object[]{4,-1},
                new Object[]{3L,40},new Object[]{java.math.BigInteger.ONE,65},new Object[]{java.math.BigInteger.TEN,-1})) {
            Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),values[0],"leftShift",new Object[]{values[1]});
            Object actual=MaterialCallGate.invokeMethodN(getClass(),values[0],"leftShift",new Object[]{values[1]});
            assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
        }
        for(Object[] values:List.of(new Object[]{4,2d},new Object[]{4f,2},new Object[]{4,java.math.BigDecimal.ONE})) {
            var expected=assertThrows(UnsupportedOperationException.class,()->org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),values[0],"leftShift",new Object[]{values[1]}));
            var actual=assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),values[0],"leftShift",new Object[]{values[1]}));
            assertEquals(expected.getMessage(),actual.getMessage());
        }
        for(String operation:List.of("intdiv","mod")) {
            for(Object[] values:List.of(new Object[]{7,2},new Object[]{-7,2},new Object[]{7L,2L},
                    new Object[]{java.math.BigInteger.valueOf(11),java.math.BigInteger.valueOf(3)})) {
                Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),values[0],operation,new Object[]{values[1]});
                Object actual=MaterialCallGate.invokeMethodN(getClass(),values[0],operation,new Object[]{values[1]});
                assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
            }
            assertThrows(ArithmeticException.class,()->MaterialCallGate.invokeMethodN(getClass(),1,operation,new Object[]{0}));
        }
        Number hostile=new Number() {
            public int intValue(){throw new AssertionError("Unselected conversion invoked");}
            public long longValue(){throw new AssertionError("Unselected conversion invoked");}
            public float floatValue(){throw new AssertionError("Unselected conversion invoked");}
            public double doubleValue(){throw new AssertionError("Unselected conversion invoked");}
        };
        for(Object operand:List.of(2f,2d,2L,java.math.BigDecimal.TEN)) {
            var wrapper=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createPojoWrapper(operand,operand.getClass());
            Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),100f,"multiply",new Object[]{wrapper});
            Object actual=MaterialCallGate.invokeMethodN(getClass(),100f,"multiply",new Object[]{wrapper});
            assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
            assertSame(operand,wrapper.unwrap());assertSame(operand.getClass(),wrapper.getType());
        }
        var subclass=new org.codehaus.groovy.runtime.wrappers.PojoWrapper(1f,float.class) {
            public Object unwrap(){throw new AssertionError("Unselected wrapper callback");}
            public Class<?> getType(){throw new AssertionError("Unselected type callback");}
        };
        for(Object operand:List.of(subclass,
                org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createPojoWrapper(hostile,float.class),
                org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createPojoWrapper("2",float.class),
                org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createPojoWrapper(2f,Object.class),
                org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createPojoWrapper(null,float.class),
                org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createPojoWrapper(subclass,float.class)))
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),100f,"multiply",new Object[]{operand}));
        for(String operation:List.of("div","intdiv","mod","leftShift")) {
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),1,operation,new Object[]{hostile}));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),hostile,operation,new Object[]{2}));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),Integer.class,operation,new Object[]{2}));
        }
    }
    public static class UnselectedUnary {
        int calls;
        public Object negative() {calls++;throw new AssertionError("Unselected negative callback invoked");}
        public Object positive() {calls++;throw new AssertionError("Unselected positive callback invoked");}
    }
    public static class PrivateAccessor {
        int calls;
        final Object value=new Object();
        RuntimeException failure;
        private Object read() {calls++;if(failure!=null)throw failure;return value;}
        private Object other() {throw new AssertionError("Unselected private accessor invoked");}
    }
    public static class PrivateAccessorChild extends PrivateAccessor {}
    private void privateAccessor() throws Throwable {
        var original=new PrivateAccessor();var guarded=new PrivateAccessor();
        assertSame(original.value,org.codehaus.groovy.runtime.ScriptBytecodeAdapter.invokeMethodN(getClass(),original,"read",new Object[0]));
        assertSame(guarded.value,MaterialCallGate.invokeMethodN(getClass(),guarded,"read",new Object[0]));
        assertEquals(1,original.calls);assertEquals(original.calls,guarded.calls);
        RuntimeException failure=new IllegalArgumentException("Original accessor failure");
        guarded.failure=failure;
        assertSame(failure,assertThrows(RuntimeException.class,()->MaterialCallGate.invokeMethodN(getClass(),guarded,"read",new Object[0])));
        assertEquals(2,guarded.calls);
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),guarded,"other",new Object[0]));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),PrivateAccessor.class,"read",new Object[0]));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),new PrivateAccessorChild(),"read",new Object[0]));
        assertTrue(java.lang.reflect.Modifier.isPrivate(PrivateAccessor.class.getDeclaredMethod("read").getModifiers()));
    }
    private void unaryNegation(MaterialBytecodeGate gate) throws Throwable {
        String owner="org/codehaus/groovy/runtime/ScriptBytecodeAdapter";
        String descriptor="(Ljava/lang/Object;)Ljava/lang/Object;";
        var parsed=new ClassNode();new ClassReader(gate.processBytecode("fixture.UnaryBridge",
                candidate("fixture.UnaryBridge",m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"unaryMinus",descriptor,false)))).accept(parsed,0);
        var call=(MethodInsnNode)parsed.methods.getFirst().instructions.getFirst();
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),call.owner);
        assertEquals("unaryMinus",call.name);assertEquals(descriptor,call.desc);
        assertEquals(Opcodes.INVOKESTATIC,call.getOpcode());assertFalse(call.itf);
        List<Consumer<MethodVisitor>> wrong=List.of(
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"unaryMinus","(Ljava/lang/Number;)Ljava/lang/Object;",false),
            m->m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,owner,"unaryMinus",descriptor,false),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"unaryMinus",descriptor,true),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,"untrusted/ScriptBytecodeAdapter","unaryMinus",descriptor,false));
        for(int i=0;i<wrong.size();i++) {
            String name="fixture.WrongUnary"+i;byte[] code=candidate(name,wrong.get(i));
            assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,code));
        }
        for(Object value:List.of((byte)7,(short)9,0,7,Integer.MIN_VALUE,11L,Long.MIN_VALUE,
                -0.0d,3.5f,Double.NaN,Double.POSITIVE_INFINITY,new java.math.BigInteger("12345678901234567890"),
                new java.math.BigDecimal("3.50"))) {
            Object expected=org.codehaus.groovy.runtime.ScriptBytecodeAdapter.unaryMinus(value);
            Object actual=MaterialCallGate.unaryMinus(value);
            assertEquals(expected,actual);assertEquals(expected.getClass(),actual.getClass());
        }
        var nativeFailure=assertThrows(Throwable.class,()->org.codehaus.groovy.runtime.ScriptBytecodeAdapter.unaryMinus(null));
        var checkedFailure=assertThrows(Throwable.class,()->MaterialCallGate.unaryMinus(null));
        assertEquals(nativeFailure.getClass(),checkedFailure.getClass());assertEquals(nativeFailure.getMessage(),checkedFailure.getMessage());
        var unselected=new UnselectedUnary();
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.unaryMinus(unselected));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.unaryMinus(new ArrayList<>(List.of(unselected))));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.unaryMinus(Integer.class));
        assertEquals(0,unselected.calls);
    }
    private void unaryPositive(MaterialBytecodeGate gate) throws Throwable {
        String owner="org/codehaus/groovy/runtime/ScriptBytecodeAdapter",descriptor="(Ljava/lang/Object;)Ljava/lang/Object;";
        var parsed=new ClassNode();new ClassReader(gate.processBytecode("fixture.UnaryPlusBridge",
                candidate("fixture.UnaryPlusBridge",m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"unaryPlus",descriptor,false)))).accept(parsed,0);
        var call=(MethodInsnNode)parsed.methods.getFirst().instructions.getFirst();
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),call.owner);assertEquals("unaryPlus",call.name);
        assertEquals(descriptor,call.desc);assertEquals(Opcodes.INVOKESTATIC,call.getOpcode());assertFalse(call.itf);
        List<Consumer<MethodVisitor>> wrong=List.of(
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"unaryPlus","(Ljava/lang/Number;)Ljava/lang/Object;",false),
                m->m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,owner,"unaryPlus",descriptor,false),
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"unaryPlus",descriptor,true),
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,"untrusted/ScriptBytecodeAdapter","unaryPlus",descriptor,false));
        for(int i=0;i<wrong.size();i++) {
            String name="fixture.WrongUnaryPlus"+i;byte[] code=candidate(name,wrong.get(i));
            assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,code));
        }
        for(Object value:List.of((byte)7,(short)9,0,7,Integer.MIN_VALUE,11L,Long.MIN_VALUE,-0.0d,3.5f,
                Double.NaN,Double.POSITIVE_INFINITY,new java.math.BigInteger("12345678901234567890"),new java.math.BigDecimal("3.50"))) {
            assertSame(value,org.codehaus.groovy.runtime.ScriptBytecodeAdapter.unaryPlus(value));
            assertSame(value,MaterialCallGate.unaryPlus(value));
        }
        var expected=assertThrows(Throwable.class,()->org.codehaus.groovy.runtime.ScriptBytecodeAdapter.unaryPlus(null));
        var actual=assertThrows(Throwable.class,()->MaterialCallGate.unaryPlus(null));
        assertEquals(expected.getClass(),actual.getClass());assertEquals(expected.getMessage(),actual.getMessage());
        var unselected=new UnselectedUnary();
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.unaryPlus(unselected));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.unaryPlus(new ArrayList<>(List.of(unselected))));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.unaryPlus(Integer.class));assertEquals(0,unselected.calls);
    }
    private void spreadArguments(MaterialBytecodeGate gate) throws Exception {
        String owner="org/codehaus/groovy/runtime/ScriptBytecodeAdapter";
        String descriptor="([Ljava/lang/Object;[Ljava/lang/Object;[I)[Ljava/lang/Object;";
        var parsed=new ClassNode();
        new ClassReader(gate.processBytecode("fixture.SpreadBridge",candidate("fixture.SpreadBridge",m->
                m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"despreadList",descriptor,false)))).accept(parsed,0);
        var call=(MethodInsnNode)parsed.methods.getFirst().instructions.getFirst();
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),call.owner);
        assertEquals("despreadList",call.name);assertEquals(descriptor,call.desc);
        List<Consumer<MethodVisitor>> wrong=List.of(
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"despreadList","([Ljava/lang/Object;[Ljava/lang/Object;[J)[Ljava/lang/Object;",false),
                m->m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,owner,"despreadList",descriptor,false),
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"despreadList",descriptor,true),
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,"untrusted/ScriptBytecodeAdapter","despreadList",descriptor,false),
                m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"spreadMap","(Ljava/lang/Object;)Ljava/lang/Object;",false));
        for(int i=0;i<wrong.size();i++) {
            String name="fixture.WrongSpread"+i;byte[] bytes=candidate(name,wrong.get(i));
            assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,bytes));
        }
        String source="""
                package fixture
                class CompiledSpread {
                    static int count
                    static Object next(Object value) { count = count + 1; value }
                    static Object collect(Object... values) { values }
                    static Object spread(Object values) { collect(next('before'), *next(values), next('after')) }
                }
                """;
        var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
        try(var guarded=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config);
                var nativeLoader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader())) {
            Class<?> checked=guarded.parseClass(source,"CompiledSpread.groovy");
            Class<?> original=nativeLoader.parseClass(source,"CompiledSpread.groovy");Object token=new Object();
            for(Object values:Arrays.asList(new ArrayList<>(Arrays.asList(token,null,token)),
                    new Object[]{token,null,token},new int[]{1,2},new ArrayList<>(),null)) {
                checked.getMethod("setCount",int.class).invoke(null,0);original.getMethod("setCount",int.class).invoke(null,0);
                assertArrayEquals((Object[])original.getMethod("spread",Object.class).invoke(null,values),
                        (Object[])checked.getMethod("spread",Object.class).invoke(null,values));
                assertEquals(3,checked.getMethod("getCount").invoke(null));
                assertEquals(original.getMethod("getCount").invoke(null),checked.getMethod("getCount").invoke(null));
            }
            var nativeFailure=assertThrows(java.lang.reflect.InvocationTargetException.class,
                    ()->original.getMethod("spread",Object.class).invoke(null,"invalid spread"));
            var guardedFailure=assertThrows(java.lang.reflect.InvocationTargetException.class,
                    ()->checked.getMethod("spread",Object.class).invoke(null,"invalid spread"));
            assertEquals(nativeFailure.getCause().getClass(),guardedFailure.getCause().getClass());
            assertEquals(nativeFailure.getCause().getMessage(),guardedFailure.getCause().getMessage());
        }
        int[] callbacks={0};
        var hostile=new ArrayList<>() {
            @Override public Object[] toArray(){callbacks[0]++;throw new AssertionError("Unselected spread conversion");}
        };
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.despreadList(new Object[0],new Object[]{hostile},new int[]{0}));
        Object formatter=new Object(){public String toString(){callbacks[0]++;throw new AssertionError("Unselected spread formatting");}};
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.despreadList(new Object[0],new Object[]{formatter},new int[]{0}));
        assertEquals(0,callbacks[0]);
        assertArrayEquals(new Object[]{"kept"},MaterialCallGate.despreadList(new Object[]{"kept"},new Object[]{hostile},new int[0]));
        assertEquals(assertThrows(RuntimeException.class,()->org.codehaus.groovy.runtime.ScriptBytecodeAdapter.despreadList(
                        new Object[0],new Object[]{new int[]{1}},new int[]{1})).getClass(),
                assertThrows(RuntimeException.class,()->MaterialCallGate.despreadList(new Object[0],new Object[]{new int[]{1}},new int[]{1})).getClass());
    }
    public static class AllowedTruth {
        int calls;
        RuntimeException failure;
        public boolean asBoolean() {calls++;if(failure!=null)throw failure;return true;}
    }
    public static class UnselectedTruth extends AllowedTruth {}
    private void booleanConversion(MaterialBytecodeGate gate) throws Throwable {
        String owner="org/codehaus/groovy/runtime/typehandling/DefaultTypeTransformation";
        for(String kind:List.of("Double","Float","Short")) {
            String helper=kind.toLowerCase(java.util.Locale.ROOT)+"Unbox",primitive=switch(kind){case "Double"->"D";case "Float"->"F";default->"S";};
            String descriptor="(Ljava/lang/Object;)"+primitive,bridgeName="fixture."+kind+"Bridge";
            var bridge=new ClassNode();
            new ClassReader(gate.processBytecode(bridgeName,candidate(bridgeName,m->
                    m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,helper,descriptor,false)))).accept(bridge,0);
            var nativeCall=(MethodInsnNode)bridge.methods.getFirst().instructions.getFirst();
            assertEquals(MaterialCallGate.class.getName().replace('.','/'),nativeCall.owner);
            assertEquals(helper,nativeCall.name);assertEquals(descriptor,nativeCall.desc);
            assertEquals(Opcodes.INVOKESTATIC,nativeCall.getOpcode());assertFalse(nativeCall.itf);
            List<Consumer<MethodVisitor>> wrongFloating=List.of(
                    m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,helper,"(Ljava/lang/String;)"+primitive,false),
                    m->m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,owner,helper,descriptor,false),
                    m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,helper,descriptor,true),
                    m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"castTo"+kind,descriptor,false),
                    m->m.visitMethodInsn(Opcodes.INVOKESTATIC,"untrusted/DefaultTypeTransformation",helper,descriptor,false));
            for(int i=0;i<wrongFloating.size();i++) {
                String name="fixture.Wrong"+kind+i;byte[] code=candidate(name,wrongFloating.get(i));
                assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,code));
            }
        }
        for(Object value:Arrays.asList((byte)1,(short)-2,3,4L,5f,-0d,Double.NaN,Double.POSITIVE_INFINITY,
                'x',Float.MIN_VALUE,-0f,Float.intBitsToFloat(0x7fa12345),java.math.BigInteger.TEN,java.math.BigDecimal.ONE)) {
            assertEquals(Double.doubleToRawLongBits(org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.doubleUnbox(value)),
                    Double.doubleToRawLongBits(MaterialCallGate.doubleUnbox(value)));
            assertEquals(Float.floatToRawIntBits(org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.floatUnbox(value)),
                    Float.floatToRawIntBits(MaterialCallGate.floatUnbox(value)));
        }
        for(Object value:Arrays.asList((byte)1,(short)-2,-32769,32768,65535L,65536L,Long.MIN_VALUE,
                5.9f,-0d,Double.NaN,Double.POSITIVE_INFINITY,'x',java.math.BigInteger.TEN,java.math.BigDecimal.ONE))
            assertEquals(org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.shortUnbox(value),MaterialCallGate.shortUnbox(value));
        for(Object value:Arrays.asList(null,true,false))
            assertEquals(assertThrows(RuntimeException.class,()->org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.shortUnbox(value)).getClass(),
                    assertThrows(RuntimeException.class,()->MaterialCallGate.shortUnbox(value)).getClass());
        var number=new Number() {
            public int intValue(){throw new AssertionError("Unselected conversion");}
            public long longValue(){throw new AssertionError("Unselected conversion");}
            public float floatValue(){throw new AssertionError("Unselected conversion");}
            public double doubleValue(){throw new AssertionError("Unselected conversion");}
        };
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.doubleUnbox(number));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.floatUnbox(number));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.shortUnbox(number));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),Math.class,"max",new Object[]{1,number}));
        assertEquals(assertThrows(RuntimeException.class,()->org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.floatUnbox(null)).getClass(),
                assertThrows(RuntimeException.class,()->MaterialCallGate.floatUnbox(null)).getClass());
        assertEquals(assertThrows(RuntimeException.class,()->org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.doubleUnbox(null)).getClass(),
                assertThrows(RuntimeException.class,()->MaterialCallGate.doubleUnbox(null)).getClass());
        byte[] original=candidate("fixture.BooleanBridge",m->m.visitMethodInsn(Opcodes.INVOKESTATIC,
                owner,"booleanUnbox","(Ljava/lang/Object;)Z",false));
        var parsed=new ClassNode();new ClassReader(gate.processBytecode("fixture.BooleanBridge",original)).accept(parsed,0);
        var call=(MethodInsnNode)parsed.methods.getFirst().instructions.getFirst();
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),call.owner);
        assertEquals("booleanUnbox",call.name);assertEquals("(Ljava/lang/Object;)Z",call.desc);
        assertEquals(Opcodes.INVOKESTATIC,call.getOpcode());assertFalse(call.itf);
        List<Consumer<MethodVisitor>> wrong=List.of(
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"booleanUnbox","(Ljava/lang/String;)Z",false),
            m->m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,owner,"booleanUnbox","(Ljava/lang/Object;)Z",false),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"booleanUnbox","(Ljava/lang/Object;)Z",true),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"castToBoolean","(Ljava/lang/Object;)Z",false),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,"untrusted/DefaultTypeTransformation","booleanUnbox","(Ljava/lang/Object;)Z",false));
        for(int i=0;i<wrong.size();i++) {
            String name="fixture.WrongBoolean"+i;byte[] code=candidate(name,wrong.get(i));
            assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,code));
        }
        for(Object value:Arrays.asList(null,false,true,0,1,0L,1L,0d,1d,'\0','x',
                java.math.BigInteger.ZERO,java.math.BigDecimal.ONE,new ArrayList<>(),new ArrayList<>(List.of(1)))) {
            assertEquals(org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.booleanUnbox(value),
                    MaterialCallGate.booleanUnbox(value));
        }
        var selected=new AllowedTruth();assertTrue(MaterialCallGate.booleanUnbox(selected));
        assertEquals(1,selected.calls,"Original asBoolean must execute exactly once");
        selected.failure=new IllegalStateException("original conversion failure");
        var nativeFailure=assertThrows(RuntimeException.class,()->
                org.codehaus.groovy.runtime.typehandling.DefaultTypeTransformation.booleanUnbox(selected));
        var guardedFailure=assertThrows(RuntimeException.class,()->MaterialCallGate.booleanUnbox(selected));
        assertEquals(nativeFailure.getClass(),guardedFailure.getClass());
        assertSame(nativeFailure.getCause(),guardedFailure.getCause());assertEquals(3,selected.calls);
        var unselected=new UnselectedTruth();
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.booleanUnbox(unselected));
        assertEquals(0,unselected.calls,"Unselected asBoolean must be refused before dispatch");
        String source="""
                package fixture
                class CompiledTruth {
                    static int count
                    static Object next(Object value) { count = count + 1; value }
                    static Object both(Object value) { next(value) && next(value) }
                    static Object either(Object value) { next(value) || next(value) }
                    static Object negate(Object value) { !next(value) }
                    static Object negative(Object value) { -next(value) }
                    static Object flattener() { return { Object value -> next(value) } }
                    static Object range(Object from, Object to) { next(from)..<next(to) }
                }
                """;
        var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
        try(var guarded=new groovy.lang.GroovyClassLoader(getClass().getClassLoader(),config);
                var nativeLoader=new groovy.lang.GroovyClassLoader(getClass().getClassLoader())) {
            Class<?> checked=guarded.parseClass(source,"CompiledTruth.groovy");
            Class<?> nativeClass=nativeLoader.parseClass(source,"CompiledTruth.groovy");
            for(String operation:List.of("both","either","negate"))for(Object value:Arrays.asList(null,false,true,0,1)) {
                checked.getMethod("setCount",int.class).invoke(null,0);
                nativeClass.getMethod("setCount",int.class).invoke(null,0);
                assertEquals(nativeClass.getMethod(operation,Object.class).invoke(null,value),
                        checked.getMethod(operation,Object.class).invoke(null,value));
                assertEquals(nativeClass.getMethod("getCount").invoke(null),checked.getMethod("getCount").invoke(null),
                        "Native short circuit and operand evaluation count: "+operation+" "+value);
            }
            checked.getMethod("setCount",int.class).invoke(null,0);
            nativeClass.getMethod("setCount",int.class).invoke(null,0);
            assertEquals(nativeClass.getMethod("range",Object.class,Object.class).invoke(null,1,4),
                    checked.getMethod("range",Object.class,Object.class).invoke(null,1,4));
            assertEquals(2,checked.getMethod("getCount").invoke(null));
            assertEquals(nativeClass.getMethod("getCount").invoke(null),checked.getMethod("getCount").invoke(null));
            checked.getMethod("setCount",int.class).invoke(null,0);
            nativeClass.getMethod("setCount",int.class).invoke(null,0);
            assertEquals(nativeClass.getMethod("negative",Object.class).invoke(null,7L),
                    checked.getMethod("negative",Object.class).invoke(null,7L));
            assertEquals(1,checked.getMethod("getCount").invoke(null));
            assertEquals(nativeClass.getMethod("getCount").invoke(null),checked.getMethod("getCount").invoke(null));
            var list=new ArrayList<>(List.of(1,new ArrayList<>(Arrays.asList(2,null))));
            assertEquals(org.codehaus.groovy.runtime.DefaultGroovyMethods.flatten(list),
                    MaterialCallGate.invokeMethodN(getClass(),list,"flatten",new Object[0]));
            Object callback=checked.getMethod("flattener").invoke(null);
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),list,"flatten",new Object[]{callback}));
            assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.invokeMethodN(getClass(),list,"plus",new Object[]{callback}));
            assertEquals(1,checked.getMethod("getCount").invoke(null),"Unselected collection callback did not run");
        }
    }
    public static class UnselectedEndpoint implements Comparable<Object> {
        int calls;
        public int compareTo(Object value) {calls++;throw new AssertionError("Unselected comparison invoked");}
        public Object next() {calls++;throw new AssertionError("Unselected next invoked");}
    }
    private void rangeConversion(MaterialBytecodeGate gate) throws Throwable {
        String owner="org/codehaus/groovy/runtime/ScriptBytecodeAdapter";
        String descriptor="(Ljava/lang/Object;Ljava/lang/Object;ZZ)Ljava/util/List;";
        var parsed=new ClassNode();new ClassReader(gate.processBytecode("fixture.RangeBridge",
                candidate("fixture.RangeBridge",m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"createRange",descriptor,false)))).accept(parsed,0);
        var call=(MethodInsnNode)parsed.methods.getFirst().instructions.getFirst();
        assertEquals(MaterialCallGate.class.getName().replace('.','/'),call.owner);
        assertEquals("createRange",call.name);assertEquals(descriptor,call.desc);
        assertEquals(Opcodes.INVOKESTATIC,call.getOpcode());assertFalse(call.itf);
        List<Consumer<MethodVisitor>> wrong=List.of(
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"createRange","(Ljava/lang/Object;Ljava/lang/Object;Z)Ljava/util/List;",false),
            m->m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,owner,"createRange",descriptor,false),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,owner,"createRange",descriptor,true),
            m->m.visitMethodInsn(Opcodes.INVOKESTATIC,"untrusted/ScriptBytecodeAdapter","createRange",descriptor,false));
        for(int i=0;i<wrong.size();i++) {
            String name="fixture.WrongRange"+i;byte[] code=candidate(name,wrong.get(i));
            assertThrows(UnsupportedOperationException.class,()->gate.processBytecode(name,code));
        }
        for(Object[] endpoints:List.of(new Object[]{1,4},new Object[]{4,1},new Object[]{2,2},
                new Object[]{1L,4L},new Object[]{'a','c'},new Object[]{java.math.BigInteger.ONE,java.math.BigInteger.valueOf(3)}))
            for(boolean left:List.of(false,true))for(boolean right:List.of(false,true))
                assertEquals(org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createRange(endpoints[0],endpoints[1],left,right),
                        MaterialCallGate.createRange(endpoints[0],endpoints[1],left,right));
        var nativeFailure=assertThrows(Throwable.class,()->org.codehaus.groovy.runtime.ScriptBytecodeAdapter.createRange(null,3,false,false));
        var guardedFailure=assertThrows(Throwable.class,()->MaterialCallGate.createRange(null,3,false,false));
        assertEquals(nativeFailure.getClass(),guardedFailure.getClass());assertEquals(nativeFailure.getMessage(),guardedFailure.getMessage());
        var unselected=new UnselectedEndpoint();
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.createRange(unselected,3,false,false));
        assertThrows(UnsupportedOperationException.class,()->MaterialCallGate.createRange(1,unselected,false,false));
        assertEquals(0,unselected.calls);
    }
}
