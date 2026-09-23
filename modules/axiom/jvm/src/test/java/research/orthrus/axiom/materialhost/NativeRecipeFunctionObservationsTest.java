package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.zip.ZipFile;
import static org.junit.jupiter.api.Assertions.*;

public class NativeRecipeFunctionObservationsTest {
    @Test void storedValueAndCacheHooksPreserveNativeInstructionsAndRejectDrift() throws Exception {
        var targets=new LinkedHashMap<String,String>();
        targets.put(NativeRecipeFunctionObservations.MULTI_RECIPE_TARGET,"AXIOM_EARLY_AUTOREGLIB_JAR");
        targets.put(NativeRecipeFunctionObservations.BLACKLIST_TARGET,"AXIOM_EARLY_AUTOREGLIB_JAR");
        for(String owner:NativeRecipeFunctionObservations.VALUE_FACTORY_TARGETS)
            targets.put(owner,owner.equals(NativeRecipeFunctionObservations.GADGET_TARGET)?"AXIOM_EARLY_GADGETS_JAR":"AXIOM_EARLY_QUARK_JAR");
        for(var target:targets.entrySet()) {
            String path=System.getenv(target.getValue());Assumptions.assumeTrue(path!=null,"Exact stored value artifact required");
            byte[] input;try(var zip=new ZipFile(path);var in=zip.getInputStream(zip.getEntry(target.getKey().replace('.','/')+".class"))) {
                input=in.readAllBytes();
            }
            boolean auto=target.getValue().equals("AXIOM_EARLY_AUTOREGLIB_JAR");
            java.util.function.Function<byte[],byte[]> transform=auto?NativeRecipeFunctionObservations::applyAutoRegLib:NativeRecipeFunctionObservations::applyValueFactory;
            var before=read(input);var after=read(transform.apply(input));var changedRoutes=new ArrayList<String>();int hooks=0;
            for(int i=0;i<before.methods.size();i++) {
                var original=before.methods.get(i);var observed=after.methods.get(i);
                for(var instruction:observed.instructions.toArray())if(instruction instanceof MethodInsnNode call
                        &&call.owner.equals(NativeRecipeFunctionObservations.class.getName().replace('.','/'))) {
                    changedRoutes.add(original.name+original.desc);hooks++;
                    if(call.name.equals("craftingCacheWritten")) {
                        var nativeWrite=(MethodInsnNode)call.getPrevious();assertEquals("set",nativeWrite.name);
                        assertEquals(Opcodes.DUP2,nativeWrite.getPrevious().getOpcode());observed.instructions.remove(nativeWrite.getPrevious());
                    } else {
                        int arguments=call.name.equals("valueFactoryResult")?3:2;
                        for(int j=0;j<arguments;j++)observed.instructions.remove(call.getPrevious());
                    }
                    observed.instructions.remove(call);
                }
                observed.maxStack=original.maxStack;assertArrayEquals(method(original),method(observed),original.name+original.desc);
            }
            assertEquals(target.getKey().equals(NativeRecipeFunctionObservations.MULTI_RECIPE_TARGET)?3:1,hooks);
            for(String route:new HashSet<>(changedRoutes)) {
                var changed=read(input);changed.methods.stream().filter(m->route.equals(m.name+m.desc)).findFirst().orElseThrow()
                        .instructions.insert(new InsnNode(Opcodes.NOP));
                assertThrows(IllegalArgumentException.class,()->transform.apply(write(changed)));
            }
            assertThrows(IllegalArgumentException.class,()->transform.apply(transform.apply(input)));
        }
    }
    @Test void nativeCacheObservationsKeepThreadValuesAndNegationDeferred() throws Exception {
        var cache=new ThreadLocal<Object>();NativeRecipeFunctionObservations.craftingCacheCreated(cache);
        assertNull(NativeRecipeFunctionObservations.craftingCache(cache).get("value"));
        Object nativeValue=new Object();cache.set(nativeValue);NativeRecipeFunctionObservations.craftingCacheWritten(cache,nativeValue);
        assertSame(nativeValue,NativeRecipeFunctionObservations.craftingCache(cache).get("value"));
        var other=new java.util.concurrent.atomic.AtomicReference<Map<String,Object>>();
        var thread=new Thread(()->other.set(NativeRecipeFunctionObservations.craftingCache(cache)));thread.start();thread.join();
        assertNull(other.get().get("value"));assertSame(nativeValue,cache.get());
        cache.set(null);NativeRecipeFunctionObservations.craftingCacheWritten(cache,null);
        assertNull(NativeRecipeFunctionObservations.craftingCache(cache).get("value"));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.craftingCacheCreated(cache));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.craftingCacheWritten(new ThreadLocal<>(),null));
        var calls=new java.util.concurrent.atomic.AtomicInteger();java.util.function.Predicate<Object> original=value->{calls.incrementAndGet();return value!=null;};
        var negated=original.negate();NativeRecipeFunctionObservations.negatedPredicateResult(negated,original);
        assertSame(original,((List<?>)NativeRecipeFunctionObservations.identity(negated).get("capturedArguments")).getFirst());
        assertEquals(0,calls.get());assertFalse(negated.test(nativeValue));assertTrue(negated.test(null));assertEquals(2,calls.get());
    }
    @Test void craftingFactoriesPreserveOriginalMethodsAndRejectChangedInputs() throws Exception {
        for(var selection:Map.of(NativeRecipeFunctionObservations.CARGO_TARGET,"AXIOM_EARLY_ICBM_JAR",
                NativeRecipeFunctionObservations.CONDITION_TARGET,"AXIOM_EARLY_ICHUN_JAR").entrySet()) {
            String path=System.getenv(selection.getValue());Assumptions.assumeTrue(path!=null,"Exact crafting factory artifact required");
            byte[] input;try(var zip=new ZipFile(path);var in=zip.getInputStream(zip.getEntry(selection.getKey().replace('.','/')+".class"))) {
                input=in.readAllBytes();
            }
            var before=read(input);var after=read(NativeRecipeFunctionObservations.applyCraftingFactory(input));
            assertEquals(before.methods.size(),after.methods.size());int hooks=0;
            for(int i=0;i<before.methods.size();i++) {
                var original=before.methods.get(i);var observed=after.methods.get(i);
                for(var instruction:observed.instructions.toArray())if(instruction instanceof MethodInsnNode call
                        &&call.owner.equals(NativeRecipeFunctionObservations.class.getName().replace('.','/'))
                        &&call.name.equals("craftingFactoryResult")) {
                    for(int capture=0;capture<3;capture++) {
                        assertInstanceOf(LdcInsnNode.class,call.getPrevious());observed.instructions.remove(call.getPrevious());
                    }
                    assertEquals(Opcodes.DUP,call.getPrevious().getOpcode());observed.instructions.remove(call.getPrevious());
                    observed.instructions.remove(call);hooks++;
                }
                observed.maxStack=original.maxStack;assertArrayEquals(method(original),method(observed),original.name);
            }
            boolean cargo=selection.getKey().equals(NativeRecipeFunctionObservations.CARGO_TARGET);assertEquals(cargo?2:1,hooks);
            for(String mutation:cargo?List.of("registerRecipes"):List.of("parse","lambda$parse$0")) {
                var changed=read(input);changed.methods.stream().filter(m->m.name.equals(mutation)).findFirst().orElseThrow()
                        .instructions.insert(new InsnNode(Opcodes.NOP));
                assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyCraftingFactory(write(changed)));
            }
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyCraftingFactory(
                    NativeRecipeFunctionObservations.applyCraftingFactory(input)));
        }
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.craftingFactoryResult(new Object(),
                NativeRecipeFunctionObservations.CARGO_TARGET,"registerRecipes","constructor"));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.stackTransformFactoryResult(new Object(),null));
    }
    private static byte[] originalBackpack() throws Exception {
        String path=System.getenv("AXIOM_EARLY_BACKPACKS_JAR");
        Assumptions.assumeTrue(path!=null&&!path.isBlank(),"Exact Retro Sophisticated Backpacks artifact is required");
        try(var zip=new ZipFile(path);var in=zip.getInputStream(zip.getEntry(
                NativeRecipeFunctionObservations.BACKPACK_TARGET.replace('.','/')+".class"))) {return in.readAllBytes();}
    }
    @Test void backpackObservationPreservesEveryOriginalMethodAndRejectsDrift() throws Exception {
        byte[] input=originalBackpack(),output=NativeRecipeFunctionObservations.applyBackpack(input);
        var before=read(input);var after=read(output);assertEquals(before.methods.size(),after.methods.size());
        int hooks=0;
        for(int i=0;i<before.methods.size();i++) {
            var original=before.methods.get(i);var observed=after.methods.get(i);
            for(var instruction:observed.instructions.toArray())if(instruction instanceof MethodInsnNode hook
                    &&hook.owner.equals(NativeRecipeFunctionObservations.class.getName().replace('.','/'))
                    &&hook.name.equals("backpackFactoryResult")) {
                if(original.name.equals("deserializeNBT")) {
                    assertEquals(Opcodes.ALOAD,hook.getPrevious().getOpcode());assertEquals(1,((VarInsnNode)hook.getPrevious()).var);
                } else assertEquals(Opcodes.ACONST_NULL,hook.getPrevious().getOpcode());
                observed.instructions.remove(hook.getPrevious());assertInstanceOf(LdcInsnNode.class,hook.getPrevious());
                observed.instructions.remove(hook.getPrevious());assertEquals(Opcodes.DUP,hook.getPrevious().getOpcode());
                observed.instructions.remove(hook.getPrevious());observed.instructions.remove(hook);hooks++;
            }
            observed.maxStack=original.maxStack;assertArrayEquals(method(original),method(observed),original.name+original.desc);
        }
        assertEquals(4,hooks);
        for(var original:before.methods)if(original.name.contains("lambda$")||original.name.equals("deserializeNBT")
                &&original.desc.contains("NBTTagCompound")||original.name.equals("<init>")&&original.desc.contains("DefaultConstructorMarker")) {
            var changed=read(input);changed.methods.stream().filter(m->m.name.equals(original.name)&&m.desc.equals(original.desc))
                    .findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyBackpack(write(changed)));
        }
        var wrongOwner=read(input);wrongOwner.name+="Other";
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyBackpack(write(wrongOwner)));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyBackpack(output));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.backpackFactoryResult(new Object(),"_init_$lambda$0",null));
    }
    @Test void originalBackpackNbtFactoriesKeepCapturesAndDeferReadsAndFailures() throws Exception {
        var original=read(originalBackpack());
        var route=original.methods.stream().filter(m->m.name.equals("deserializeNBT")&&m.desc.contains("NBTTagCompound")).findFirst().orElseThrow();
        var factories=Arrays.stream(route.instructions.toArray()).filter(n->n instanceof InvokeDynamicInsnNode).map(n->(InvokeDynamicInsnNode)n).toList();
        for(var factory:factories)for(boolean observed:List.of(false,true)) {
            String nbt="net/minecraft/nbt/NBTTagCompound",host=NativeRecipeFunctionObservations.BACKPACK_TARGET.replace('.','/');
            String function="kotlin/jvm/functions/Function0";var definitions=new HashMap<String,byte[]>();
            var w=new ClassWriter(ClassWriter.COMPUTE_FRAMES|ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT|Opcodes.ACC_INTERFACE,function,null,"java/lang/Object",null);
            w.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"invoke","()Ljava/lang/Object;",null,null).visitEnd();w.visitEnd();
            definitions.put(function.replace('/','.'),w.toByteArray());w=new ClassWriter(ClassWriter.COMPUTE_FRAMES|ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,nbt,null,"java/lang/Object",null);constructor(w);
            w.visitField(Opcodes.ACC_PUBLIC,"reads","I",null,null).visitEnd();
            w.visitField(Opcodes.ACC_PUBLIC,"failure","Ljava/lang/RuntimeException;",null,null).visitEnd();
            var m=w.visitMethod(Opcodes.ACC_PUBLIC,"func_74762_e","(Ljava/lang/String;)I",null,null);m.visitCode();
            m.visitVarInsn(Opcodes.ALOAD,0);m.visitInsn(Opcodes.DUP);m.visitFieldInsn(Opcodes.GETFIELD,nbt,"reads","I");
            m.visitInsn(Opcodes.ICONST_1);m.visitInsn(Opcodes.IADD);m.visitFieldInsn(Opcodes.PUTFIELD,nbt,"reads","I");
            m.visitVarInsn(Opcodes.ALOAD,0);m.visitFieldInsn(Opcodes.GETFIELD,nbt,"failure","Ljava/lang/RuntimeException;");
            var success=new Label();m.visitJumpInsn(Opcodes.IFNULL,success);m.visitVarInsn(Opcodes.ALOAD,0);
            m.visitFieldInsn(Opcodes.GETFIELD,nbt,"failure","Ljava/lang/RuntimeException;");m.visitInsn(Opcodes.ATHROW);
            m.visitLabel(success);m.visitIntInsn(Opcodes.BIPUSH,7);m.visitInsn(Opcodes.IRETURN);m.visitMaxs(0,0);m.visitEnd();w.visitEnd();
            definitions.put(nbt.replace('/','.'),w.toByteArray());w=new ClassWriter(ClassWriter.COMPUTE_FRAMES|ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,host,null,"java/lang/Object",null);
            var implementation=(Handle)factory.bsmArgs[1];
            original.methods.stream().filter(body->body.name.equals(implementation.getName())&&body.desc.equals(implementation.getDesc()))
                    .findFirst().orElseThrow().accept(w);
            m=w.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC,"factory",factory.desc,null,null);m.visitCode();m.visitVarInsn(Opcodes.ALOAD,0);
            m.visitInvokeDynamicInsn(factory.name,factory.desc,factory.bsm,factory.bsmArgs);
            if(observed) {
                m.visitInsn(Opcodes.DUP);m.visitLdcInsn(implementation.getName());m.visitVarInsn(Opcodes.ALOAD,0);
                m.visitMethodInsn(Opcodes.INVOKESTATIC,NativeRecipeFunctionObservations.class.getName().replace('.','/'),"backpackFactoryResult",
                        "(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",false);
            }
            m.visitInsn(Opcodes.ARETURN);m.visitMaxs(0,0);m.visitEnd();w.visitEnd();definitions.put(host.replace('/','.'),w.toByteArray());
            var loader=new ClassLoader(getClass().getClassLoader()) {protected Class<?> findClass(String name) throws ClassNotFoundException {
                byte[] bytes=definitions.get(name);if(bytes==null)throw new ClassNotFoundException(name);return defineClass(name,bytes,0,bytes.length);
            }};
            var nbtType=loader.loadClass(nbt.replace('/','.'));Object capture=nbtType.getConstructor().newInstance();
            Object value=loader.loadClass(host.replace('/','.')).getMethod("factory",nbtType).invoke(null,capture);
            var identity=NativeRecipeFunctionObservations.identity(value);
            if(observed) {
                assertSame(capture,((List<?>)identity.get("capturedArguments")).getFirst());
                assertEquals(implementation.getName()+implementation.getDesc(),identity.get("implementation"));
                assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.backpackFactoryResult(value,implementation.getName(),new Object()));
                assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.backpackFactoryResult(value,"other",capture));
            } else assertNull(identity);
            assertEquals(0,nbtType.getField("reads").get(capture));
            var invoke=loader.loadClass(function.replace('/','.')).getMethod("invoke");assertEquals(7,invoke.invoke(value));
            assertEquals(1,nbtType.getField("reads").get(capture));var failure=new IllegalStateException("native capacity read failure");
            nbtType.getField("failure").set(capture,failure);
            assertSame(failure,assertThrows(InvocationTargetException.class,()->invoke.invoke(value)).getCause());
            assertEquals(2,nbtType.getField("reads").get(capture));
        }
    }
    private static byte[] originalStorage(String entry) throws Exception {
        String path=System.getenv("AXIOM_EARLY_BDSANDM_JAR");
        Assumptions.assumeTrue(path!=null&&!path.isBlank(),"Exact BDSandM artifact is required");
        try(var zip=new ZipFile(path);var in=zip.getInputStream(zip.getEntry(entry.replace('.','/')+".class"))) {
            return in.readAllBytes();
        }
    }
    @Test void storageObservationPreservesOriginalMethodsAndRejectsFactoryOrCallbackDrift() throws Exception {
        for(String target:NativeRecipeFunctionObservations.STORAGE_TARGETS) {
            byte[] input=originalStorage(target),output=NativeRecipeFunctionObservations.applyStorage(input);
            var before=read(input);var after=read(output);assertEquals(before.methods.size(),after.methods.size());
            for(int i=0;i<before.methods.size();i++) {
                var original=before.methods.get(i);var observed=after.methods.get(i);
                if(original.name.equals("setParentStack")) {
                    var hook=(MethodInsnNode)Arrays.stream(observed.instructions.toArray())
                            .filter(n->n instanceof MethodInsnNode m&&m.name.equals("storageFactoryResult")).findFirst().orElseThrow();
                    for(int local:List.of(2,1,0)) {
                        assertEquals(Opcodes.ALOAD,hook.getPrevious().getOpcode());
                        assertEquals(local,((VarInsnNode)hook.getPrevious()).var);observed.instructions.remove(hook.getPrevious());
                    }
                    assertEquals(Opcodes.DUP,hook.getPrevious().getOpcode());observed.instructions.remove(hook.getPrevious());
                    observed.instructions.remove(hook);observed.maxStack=original.maxStack;
                }
                assertArrayEquals(method(original),method(observed),original.name);
            }
            for(String mutation:List.of("owner","factory","body","callback")) {
                var node=read(input);var route=node.methods.stream().filter(m->m.name.equals(
                        mutation.equals("callback")?"lambda$setParentStack$0":"setParentStack")).findFirst().orElseThrow();
                if(mutation.equals("owner"))node.name+="Other";
                else if(mutation.equals("factory"))((InvokeDynamicInsnNode)Arrays.stream(route.instructions.toArray())
                        .filter(n->n instanceof InvokeDynamicInsnNode).findFirst().orElseThrow()).name="other";
                else route.instructions.insert(new InsnNode(Opcodes.NOP));
                assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyStorage(write(node)),mutation);
            }
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyStorage(output));
        }
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.storageFactoryResult(null,new Object(),null,null));
    }
    @Test void originalStorageFactoriesRetainCapturesAndDeferCallbackEffectsAndFailures() throws Exception {
        for(String target:NativeRecipeFunctionObservations.STORAGE_TARGETS)for(boolean observed:List.of(false,true)) {
            var node=read(originalStorage(target));var route=node.methods.stream().filter(m->m.name.equals("setParentStack")).findFirst().orElseThrow();
            var factory=(InvokeDynamicInsnNode)Arrays.stream(route.instructions.toArray()).filter(n->n instanceof InvokeDynamicInsnNode).findFirst().orElseThrow();
            var implementation=(Handle)factory.bsmArgs[1];String owner=target.replace('.','/'),stack="net/minecraft/item/ItemStack";
            String callback="funwayguy/bdsandm/inventory/capability/ICrateCallback";
            var definitions=new HashMap<String,byte[]>();definitions.put(callback.replace('/','.'),originalStorage(callback));
            var w=new ClassWriter(ClassWriter.COMPUTE_FRAMES|ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,stack,null,"java/lang/Object",null);constructor(w);w.visitEnd();
            definitions.put(stack.replace('/','.'),w.toByteArray());w=new ClassWriter(ClassWriter.COMPUTE_FRAMES|ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,owner,null,"java/lang/Object",null);constructor(w);
            w.visitField(Opcodes.ACC_PUBLIC,"calls","I",null,null).visitEnd();
            w.visitField(Opcodes.ACC_PUBLIC,"failure","Ljava/lang/RuntimeException;",null,null).visitEnd();
            var m=w.visitMethod(Opcodes.ACC_PRIVATE|Opcodes.ACC_SYNTHETIC,implementation.getName(),implementation.getDesc(),null,null);
            m.visitCode();m.visitVarInsn(Opcodes.ALOAD,0);m.visitInsn(Opcodes.DUP);m.visitFieldInsn(Opcodes.GETFIELD,owner,"calls","I");
            m.visitInsn(Opcodes.ICONST_1);m.visitInsn(Opcodes.IADD);m.visitFieldInsn(Opcodes.PUTFIELD,owner,"calls","I");
            m.visitVarInsn(Opcodes.ALOAD,0);m.visitFieldInsn(Opcodes.GETFIELD,owner,"failure","Ljava/lang/RuntimeException;");
            var success=new Label();m.visitJumpInsn(Opcodes.IFNULL,success);m.visitVarInsn(Opcodes.ALOAD,0);
            m.visitFieldInsn(Opcodes.GETFIELD,owner,"failure","Ljava/lang/RuntimeException;");m.visitInsn(Opcodes.ATHROW);
            m.visitLabel(success);m.visitInsn(Opcodes.RETURN);m.visitMaxs(0,0);m.visitEnd();
            m=w.visitMethod(Opcodes.ACC_PUBLIC,"factory","(L"+stack+";)L"+callback+";",null,null);m.visitCode();
            m.visitVarInsn(Opcodes.ALOAD,0);m.visitVarInsn(Opcodes.ALOAD,1);m.visitVarInsn(Opcodes.ALOAD,1);
            m.visitInvokeDynamicInsn(factory.name,factory.desc,factory.bsm,factory.bsmArgs);
            if(observed) {
                m.visitInsn(Opcodes.DUP);m.visitVarInsn(Opcodes.ALOAD,0);m.visitVarInsn(Opcodes.ALOAD,1);m.visitVarInsn(Opcodes.ALOAD,1);
                m.visitMethodInsn(Opcodes.INVOKESTATIC,NativeRecipeFunctionObservations.class.getName().replace('.','/'),"storageFactoryResult",
                        "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)V",false);
            }
            m.visitInsn(Opcodes.ARETURN);m.visitMaxs(0,0);m.visitEnd();w.visitEnd();definitions.put(target,w.toByteArray());
            var loader=new ClassLoader(getClass().getClassLoader()) {protected Class<?> findClass(String name) throws ClassNotFoundException {
                byte[] bytes=definitions.get(name);if(bytes==null)throw new ClassNotFoundException(name);return defineClass(name,bytes,0,bytes.length);
            }};
            Class<?> ownerType=loader.loadClass(target),stackType=loader.loadClass(stack.replace('/','.'));
            Object provider=ownerType.getConstructor().newInstance(),item=stackType.getConstructor().newInstance();
            for(Object captured:Arrays.asList(item,null)) {
                Object result=ownerType.getMethod("factory",stackType).invoke(provider,captured);
                var identity=NativeRecipeFunctionObservations.identity(result);
                if(observed) {
                    assertEquals(Arrays.asList(provider,captured,captured),identity.get("capturedArguments"));
                    assertEquals(implementation.getName()+implementation.getDesc(),identity.get("implementation"));
                } else assertNull(identity);
                assertEquals(0,ownerType.getField("calls").get(provider));
                var invoke=loader.loadClass(callback.replace('/','.')).getMethod("onCrateChanged");invoke.invoke(result);
                assertEquals(1,ownerType.getField("calls").get(provider));
                var failure=new IllegalStateException("native callback failure");ownerType.getField("failure").set(provider,failure);
                assertSame(failure,assertThrows(InvocationTargetException.class,()->invoke.invoke(result)).getCause());
                assertEquals(2,ownerType.getField("calls").get(provider));ownerType.getField("calls").set(provider,0);
                ownerType.getField("failure").set(provider,null);
            }
        }
    }
    private static void constructor(ClassWriter writer) {
        var m=writer.visitMethod(Opcodes.ACC_PUBLIC,"<init>","()V",null,null);m.visitCode();m.visitVarInsn(Opcodes.ALOAD,0);
        m.visitMethodInsn(Opcodes.INVOKESPECIAL,"java/lang/Object","<init>","()V",false);m.visitInsn(Opcodes.RETURN);m.visitMaxs(0,0);m.visitEnd();
    }
    private static byte[] originalBomblet() throws Exception {
        String path=System.getenv("AXIOM_EARLY_ICBM_JAR");
        Assumptions.assumeTrue(path!=null&&!path.isBlank(),"Exact ICBM artifact is required");
        try(var zip=new ZipFile(path);var in=zip.getInputStream(zip.getEntry(NativeRecipeFunctionObservations.BOMBLET_TARGET.replace('.','/')+".class"))) {
            return in.readAllBytes();
        }
    }
    @Test void bombletObservationPreservesEveryOriginalMethodAndRejectsFactoryDrift() throws Exception {
        byte[] input=originalBomblet(),output=NativeRecipeFunctionObservations.applyBomblet(input);
        var before=read(input);var after=read(output);
        assertEquals(before.methods.size(),after.methods.size());
        for(int i=0;i<before.methods.size();i++) {
            var original=before.methods.get(i);var observed=after.methods.get(i);
            if(original.name.equals("initCapabilities")) {
                var hook=(MethodInsnNode)Arrays.stream(observed.instructions.toArray()).filter(n->n instanceof MethodInsnNode m&&m.name.equals("bombletFactoryResult")).findFirst().orElseThrow();
                assertEquals(1,((VarInsnNode)hook.getPrevious()).var);observed.instructions.remove(hook.getPrevious());
                assertEquals(Opcodes.DUP,hook.getPrevious().getOpcode());observed.instructions.remove(hook.getPrevious());
                observed.instructions.remove(hook);observed.maxStack=original.maxStack;
            }
            assertArrayEquals(method(original),method(observed),original.name);
        }
        for(String mutation:List.of("owner","factory","body")) {
            var node=read(input);var route=node.methods.stream().filter(m->m.name.equals("initCapabilities")).findFirst().orElseThrow();
            if(mutation.equals("owner"))node.name+="Other";
            else if(mutation.equals("factory"))((InvokeDynamicInsnNode)Arrays.stream(route.instructions.toArray()).filter(n->n instanceof InvokeDynamicInsnNode).findFirst().orElseThrow()).name="other";
            else route.instructions.insert(new InsnNode(Opcodes.NOP));
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyBomblet(write(node)),mutation);
        }
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.applyBomblet(output));
    }
    @Test void originalBombletMethodReferenceKeepsItsCaptureCopyAndExceptionBehavior() throws Exception {
        var source=read(originalBomblet());var route=source.methods.stream().filter(m->m.name.equals("initCapabilities")).findFirst().orElseThrow();
        var factory=(InvokeDynamicInsnNode)Arrays.stream(route.instructions.toArray()).filter(n->n instanceof InvokeDynamicInsnNode).findFirst().orElseThrow();
        for(boolean observed:List.of(false,true)) {
            String stack="net/minecraft/item/ItemStack",host=NativeRecipeFunctionObservations.BOMBLET_TARGET.replace('.','/');
            var definitions=new HashMap<String,byte[]>();var w=new ClassWriter(ClassWriter.COMPUTE_FRAMES|ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,stack,null,"java/lang/Object",null);
            w.visitField(Opcodes.ACC_PUBLIC,"copies","I",null,null).visitEnd();
            w.visitField(Opcodes.ACC_PUBLIC,"failure","Ljava/lang/RuntimeException;",null,null).visitEnd();
            var m=w.visitMethod(Opcodes.ACC_PUBLIC,"<init>","()V",null,null);m.visitCode();m.visitVarInsn(Opcodes.ALOAD,0);
            m.visitMethodInsn(Opcodes.INVOKESPECIAL,"java/lang/Object","<init>","()V",false);m.visitInsn(Opcodes.RETURN);m.visitMaxs(0,0);m.visitEnd();
            // A test copy receiver makes any accidental observer invocation visible.
            m=w.visitMethod(Opcodes.ACC_PUBLIC,"func_77946_l","()L"+stack+";",null,null);m.visitCode();m.visitVarInsn(Opcodes.ALOAD,0);m.visitInsn(Opcodes.DUP);
            m.visitFieldInsn(Opcodes.GETFIELD,stack,"copies","I");m.visitInsn(Opcodes.ICONST_1);m.visitInsn(Opcodes.IADD);m.visitFieldInsn(Opcodes.PUTFIELD,stack,"copies","I");
            m.visitVarInsn(Opcodes.ALOAD,0);m.visitFieldInsn(Opcodes.GETFIELD,stack,"failure","Ljava/lang/RuntimeException;");var success=new Label();m.visitJumpInsn(Opcodes.IFNULL,success);
            m.visitVarInsn(Opcodes.ALOAD,0);m.visitFieldInsn(Opcodes.GETFIELD,stack,"failure","Ljava/lang/RuntimeException;");m.visitInsn(Opcodes.ATHROW);m.visitLabel(success);
            m.visitTypeInsn(Opcodes.NEW,stack);m.visitInsn(Opcodes.DUP);m.visitMethodInsn(Opcodes.INVOKESPECIAL,stack,"<init>","()V",false);m.visitInsn(Opcodes.ARETURN);m.visitMaxs(0,0);m.visitEnd();w.visitEnd();
            definitions.put(stack.replace('/','.'),w.toByteArray());w=new ClassWriter(ClassWriter.COMPUTE_MAXS);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,host,null,"java/lang/Object",null);
            m=w.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC,"factory",factory.desc,null,null);m.visitCode();m.visitVarInsn(Opcodes.ALOAD,0);m.visitInsn(Opcodes.DUP);
            m.visitMethodInsn(Opcodes.INVOKEVIRTUAL,"java/lang/Object","getClass","()Ljava/lang/Class;",false);m.visitInsn(Opcodes.POP);
            m.visitInvokeDynamicInsn(factory.name,factory.desc,factory.bsm,factory.bsmArgs);
            if(observed) {m.visitInsn(Opcodes.DUP);m.visitVarInsn(Opcodes.ALOAD,0);m.visitMethodInsn(Opcodes.INVOKESTATIC,NativeRecipeFunctionObservations.class.getName().replace('.','/'),"bombletFactoryResult","(Ljava/lang/Object;Ljava/lang/Object;)V",false);}
            m.visitInsn(Opcodes.ARETURN);m.visitMaxs(0,0);m.visitEnd();w.visitEnd();definitions.put(host.replace('/','.'),w.toByteArray());
            var loader=new ClassLoader(getClass().getClassLoader()) {protected Class<?> findClass(String name) throws ClassNotFoundException {
                byte[] bytes=definitions.get(name);if(bytes==null)throw new ClassNotFoundException(name);return defineClass(name,bytes,0,bytes.length);
            }};
            Class<?> stackType=loader.loadClass(stack.replace('/','.'));Object item=stackType.getConstructor().newInstance();Method create=loader.loadClass(host.replace('/','.')).getMethod("factory",stackType);
            var supplier=(java.util.function.Supplier<?>)create.invoke(null,item);assertEquals(0,stackType.getField("copies").get(item));
            var identity=NativeRecipeFunctionObservations.identity(supplier);
            if(observed) {assertNotNull(identity);assertSame(item,((List<?>)identity.get("capturedArguments")).getFirst());assertTrue(((String)identity.get("implementation")).contains("func_77946_l"));}
            else assertNull(identity);
            assertNotSame(item,supplier.get());assertEquals(1,stackType.getField("copies").get(item));
            var failure=new IllegalStateException("original copy failure");stackType.getField("failure").set(item,failure);assertSame(failure,assertThrows(IllegalStateException.class,supplier::get));
            Object count=NativeRecipeFunctionObservations.observations().get("factoryResults");
            assertInstanceOf(NullPointerException.class,assertThrows(InvocationTargetException.class,()->create.invoke(null,new Object[]{null})).getCause());
            assertEquals(count,NativeRecipeFunctionObservations.observations().get("factoryResults"));
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.bombletFactoryResult(new Object(),item));
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.bombletFactoryResult(supplier,new Object()));
        }
    }
    @Test void originalRecipeStreamFiltersItsCopyAndUsesTheBoundRemover() throws Exception {
        String path=System.getenv("AXIOM_EARLY_GROOVYSCRIPT_JAR");
        Assumptions.assumeTrue(path!=null&&!path.isBlank(),"Exact GroovyScript artifact is required");
        try(var loader=new java.net.URLClassLoader(new java.net.URL[]{java.nio.file.Path.of(path).toUri().toURL()},getClass().getClassLoader())) {
            Class<?> type=Class.forName("com.cleanroommc.groovyscript.helper.SimpleObjectStream",true,loader);
            var source=new ArrayList<Object>(List.of("keep","drop","keep"));
            Object stream=type.getConstructor(Collection.class).newInstance(source);
            Map<String,Object> raw;
            try(var in=getClass().getResourceAsStream("/axiom-material-admission.json")) {
                raw=new LinkedHashMap<>();
                ((Map<?,?>)research.orthrus.axiom.Json.parse(new String(in.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8)))
                        .forEach((key,value)->raw.put((String)key,value));
            }
            raw.put("nativeMethods",Map.of(type.getName(),List.of("removeAll()Lcom/cleanroommc/groovyscript/helper/SimpleObjectStream;",
                    "removeAll(Ljava/util/Collection;)Z")));
            var policy=new MaterialAdmissionPolicy(raw);
            assertTrue(MaterialCallGate.nativeDelegate(policy,stream,"removeAll",new Object[]{stream}));
            var unselected=new ArrayList<Object>() {public boolean contains(Object value){throw new AssertionError("Unselected membership callback");}};
            assertFalse(MaterialCallGate.nativeDelegate(policy,stream,"removeAll",new Object[]{stream,unselected}));
            raw.put("nativeMethods",Map.of(type.getName(),List.of("removeAll()Lcom/cleanroommc/groovyscript/helper/SimpleObjectStream;")));
            assertThrows(UnsupportedOperationException.class,()->new MaterialAdmissionPolicy(raw).nativeMethod(type,"removeAll",false));
            var seen=new ArrayList<Object>();
            var callback=new groovy.lang.Closure<Boolean>(this) {
                public Boolean doCall(Object value) {seen.add(value);return value.equals("keep");}
            };
            assertSame(stream,type.getMethod("filter",groovy.lang.Closure.class).invoke(stream,callback));
            assertEquals(List.of("keep","drop","keep"),seen);assertEquals(seen,source);
            assertEquals(List.of("keep","keep"),type.getMethod("getList").invoke(stream));
            var removed=new ArrayList<Object>();
            java.util.function.Predicate<Object> remover=value->{removed.add(value);return source.remove(value);};
            assertSame(stream,type.getMethod("setRemover",java.util.function.Predicate.class).invoke(stream,remover));
            assertSame(stream,type.getMethod("removeAll").invoke(stream));
            assertEquals(List.of("keep","keep"),removed);assertEquals(List.of("drop"),source);
            assertEquals(List.of(),type.getMethod("getList").invoke(stream));
            Object defaults=type.getConstructor(Collection.class).newInstance(List.of("one","two"));
            var nullable=new groovy.lang.Closure<Object>(this) {public Object doCall(Object value){return value.equals("one")?null:1;}};
            assertSame(defaults,type.getMethod("filter",groovy.lang.Closure.class).invoke(defaults,nullable));
            assertEquals(List.of("one","two"),type.getMethod("getList").invoke(defaults));
            var problem=new IllegalStateException("original stream callback failure");
            var failing=new groovy.lang.Closure<Object>(this) {public Object doCall(Object value){throw problem;}};
            assertSame(problem,assertThrows(InvocationTargetException.class,()->type.getMethod("filter",groovy.lang.Closure.class).invoke(defaults,failing)).getCause());
            assertEquals(List.of("one","two"),type.getMethod("getList").invoke(defaults));
            assertInstanceOf(NullPointerException.class,assertThrows(InvocationTargetException.class,()->type.getMethod("removeAll").invoke(defaults)).getCause());
        }
    }
    /** Fixture copy boundary around the unchanged selected transform methods.
     * The real stack and registered recipe are verified by the native lane. */
    public static void storedSourceTransforms(MaterialBytecodeGate gate) throws Exception {
        String owner=NativeRecipeFunctionObservations.TARGET,stack="net.minecraft.item.ItemStack";
        String sam="com.cleanroommc.groovyscript.compat.vanilla.ItemStackTransformer";
        var directory=java.nio.file.Files.createTempDirectory("axiom-stored-transform-");
        try {
            var node=read(original());node.interfaces.clear();node.fields.clear();
            node.methods.removeIf(m->!m.name.equals("transform")&&!m.name.equals("lambda$transform$3"));
            node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"exactCopy","()L"+owner.replace('.','/')+";",null,null));
            node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"grs$setTransformer","(L"+sam.replace('.','/')+";)V",null,null));
            node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"grs$getItemStack","()Lnet/minecraft/item/ItemStack;",null,null));
            var path=directory.resolve(owner.replace('.','/')+".class");java.nio.file.Files.createDirectories(path.getParent());
            java.nio.file.Files.write(path,write(node));
            try(var zip=new ZipFile(System.getenv("AXIOM_EARLY_GROOVYSCRIPT_JAR"))) {
                java.nio.file.Files.write(directory.resolve(sam.replace('.','/')+".class"),zip.getInputStream(zip.getEntry(sam.replace('.','/')+".class")).readAllBytes());
            }
            var stackSource=directory.resolve("ItemStack.java");java.nio.file.Files.writeString(stackSource,"""
                    package net.minecraft.item;
                    import com.cleanroommc.groovyscript.compat.vanilla.*;
                    public class ItemStack implements ItemStackMixinExpansion {
                        public int copies;
                        public Object state;
                        public RuntimeException failure;
                        public ItemStackTransformer stored;
                        public ItemStackMixinExpansion exactCopy() {
                            copies++;
                            if(failure!=null)throw failure;
                            ItemStack copy=new ItemStack();copy.state=state;copy.stored=stored;return copy;
                        }
                        public void grs$setTransformer(ItemStackTransformer value) {stored=value;}
                        public ItemStack grs$getItemStack() {return this;}
                    }
                    """);
            var builderSource=directory.resolve("CraftingRecipeBuilder.java");java.nio.file.Files.writeString(builderSource,"""
                    package com.cleanroommc.groovyscript.compat.vanilla;
                    public class CraftingRecipeBuilder {
                        public static class Shapeless {
                            public groovy.lang.Closure stored;
                            public int stores;
                            public Shapeless recipeFunction(groovy.lang.Closure value) {stored=value;stores++;return this;}
                        }
                    }
                    """);
            String classpath=directory+java.io.File.pathSeparator+System.getProperty("axiom.test.runtimeClasspath",System.getProperty("java.class.path"));
            assertEquals(0,javax.tools.ToolProvider.getSystemJavaCompiler().run(null,null,null,"-proc:none","-classpath",classpath,
                    "-d",directory.toString(),stackSource.toString(),builderSource.toString()));
            try(var definitions=new java.net.URLClassLoader(new java.net.URL[]{directory.toUri().toURL()},NativeRecipeFunctionObservationsTest.class.getClassLoader())) {
                var config=new org.codehaus.groovy.control.CompilerConfiguration();config.setBytecodePostprocessor(gate);
                try(var guarded=new groovy.lang.GroovyClassLoader(definitions,config);var original=new groovy.lang.GroovyClassLoader(definitions)) {
                    String source="""
                            package fixture
                            class StoredTransforms {
                                int calls
                                void tick() { calls++ }
                                Closure callback(Object result) { return { ignored -> tick(); result } }
                                Closure failing(Throwable problem) { return { ignored -> tick(); throw problem } }
                                Closure recipe() { return { output, inputs, info -> tick(); output } }
                                Object install(Object stack, Object callback) { stack.transform(callback) }
                                Object installRecipe(Object builder, Closure callback) { builder.recipeFunction(callback) }
                            }
                            """;
                    Class<?> checked=guarded.parseClass(source,"StoredTransforms.groovy"),nativeType=original.parseClass(source,"StoredTransforms.groovy");
                    Class<?> stackType=definitions.loadClass(stack),samType=definitions.loadClass(sam);
                    Class<?> builderType=definitions.loadClass("com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipeBuilder$Shapeless");
                    for(var sourceType:java.util.List.of(nativeType,checked)) {
                        Object instance=sourceType.getConstructor().newInstance(),item=stackType.getConstructor().newInstance();
                        Object marker=stackType.getConstructor().newInstance(),state=new Object();stackType.getField("state").set(item,state);
                        var callback=sourceType.getMethod("callback",Object.class).invoke(instance,marker);
                        Object copy=sourceType.getMethod("install",Object.class,Object.class).invoke(instance,item,callback);
                        assertNotSame(item,copy);assertSame(state,stackType.getField("state").get(copy));
                        assertNull(stackType.getField("stored").get(item));assertEquals(1,stackType.getField("copies").get(item));
                        assertEquals(0,sourceType.getMethod("getCalls").invoke(instance));
                        Object function=stackType.getField("stored").get(copy);
                        assertTrue(Proxy.isProxyClass(function.getClass()));
                        var handler=assertInstanceOf(org.codehaus.groovy.runtime.ConvertedClosure.class,Proxy.getInvocationHandler(function));
                        assertSame(callback,handler.getDelegate());
                        if(sourceType==checked) {
                            var identity=NativeRecipeFunctionObservations.storedSourceIdentity(function);
                            assertEquals("original-groovy-sam-proxy",identity.get("kind"));
                            assertSame(callback,((java.util.List<?>)identity.get("capturedArguments")).getFirst());
                            var closure=NativeRecipeFunctionObservations.storedSourceIdentity(callback);
                            assertEquals(java.util.List.of("result"),closure.get("capturedFields"));
                            assertSame(marker,((java.util.List<?>)closure.get("capturedArguments")).getFirst());
                            assertEquals(true,closure.get("delegateIsOwner"));
                            assertEquals("StoredTransforms.groovy",((java.util.Map<?,?>)closure.get("compilation")).get("sourceFile"));
                            assertEquals(0,sourceType.getMethod("getCalls").invoke(instance));
                        }
                        // Explicit conformance calls, never initialization/observer calls.
                        assertSame(marker,samType.getMethod("transform",stackType).invoke(function,item));
                        assertEquals(1,sourceType.getMethod("getCalls").invoke(instance));
                        var problem=new IllegalStateException("original callback failure");
                        var failing=sourceType.getMethod("failing",Throwable.class).invoke(instance,problem);
                        Object failedCopy=sourceType.getMethod("install",Object.class,Object.class).invoke(instance,item,failing);
                        var failure=assertThrows(InvocationTargetException.class,()->samType.getMethod("transform",stackType)
                                .invoke(stackType.getField("stored").get(failedCopy),item));assertSame(problem,failure.getCause());
                        assertEquals(2,sourceType.getMethod("getCalls").invoke(instance));
                        Object constantCopy=sourceType.getMethod("install",Object.class,Object.class).invoke(instance,item,marker);
                        assertSame(marker,samType.getMethod("transform",stackType).invoke(stackType.getField("stored").get(constantCopy),item));
                        stackType.getField("failure").set(item,problem);
                        assertSame(problem,assertThrows(InvocationTargetException.class,()->sourceType.getMethod("install",Object.class,Object.class)
                                .invoke(instance,item,callback)).getCause());
                        assertEquals(2,sourceType.getMethod("getCalls").invoke(instance));
                        Object builder=builderType.getConstructor().newInstance();
                        var recipe=sourceType.getMethod("recipe").invoke(instance);
                        assertSame(builder,sourceType.getMethod("installRecipe",Object.class,groovy.lang.Closure.class).invoke(instance,builder,recipe));
                        assertSame(recipe,builderType.getField("stored").get(builder));assertEquals(1,builderType.getField("stores").get(builder));
                        assertEquals(2,sourceType.getMethod("getCalls").invoke(instance));
                        assertSame(marker,((groovy.lang.Closure<?>)recipe).call(marker,java.util.Map.of(),null));
                        assertEquals(3,sourceType.getMethod("getCalls").invoke(instance));
                    }
                    Object instance=checked.getConstructor().newInstance(),item=stackType.getConstructor().newInstance(),builder=builderType.getConstructor().newInstance();
                    var foreign=new groovy.lang.Closure<Object>(null){public Object doCall(Object value){throw new AssertionError("Foreign callback invoked");}};
                    Object proxy=Proxy.newProxyInstance(definitions,new Class<?>[]{samType},(receiver,method,args)->{throw new AssertionError("Foreign proxy invoked");});
                    for(Object value:java.util.List.of(foreign,proxy,new Object())) {
                        assertInstanceOf(UnsupportedOperationException.class,assertThrows(InvocationTargetException.class,
                                ()->checked.getMethod("install",Object.class,Object.class).invoke(instance,item,value)).getCause());
                        assertFalse(MaterialCallGate.nativeDelegate(MaterialCallGate.policy(),item,"transform",new Object[]{item,value}));
                    }
                    assertEquals(0,stackType.getField("copies").get(item));
                    assertInstanceOf(UnsupportedOperationException.class,assertThrows(InvocationTargetException.class,
                            ()->checked.getMethod("installRecipe",Object.class,groovy.lang.Closure.class).invoke(instance,builder,foreign)).getCause());
                    assertFalse(MaterialCallGate.nativeDelegate(MaterialCallGate.policy(),builder,"recipeFunction",new Object[]{builder,foreign}));
                    assertEquals(0,builderType.getField("stores").get(builder));
                    assertNull(NativeRecipeFunctionObservations.storedSourceIdentity(proxy));
                }
            }
        } finally {
            try(var paths=java.nio.file.Files.walk(directory)) {
                for(var path:paths.sorted(java.util.Comparator.reverseOrder()).toList())java.nio.file.Files.delete(path);
            }
        }
    }
    private static byte[] original() throws Exception {
        String path=System.getenv("AXIOM_EARLY_GROOVYSCRIPT_JAR");
        Assumptions.assumeTrue(path!=null&&!path.isBlank(),"Exact GroovyScript artifact is required");
        try(var zip=new ZipFile(path);var stream=zip.getInputStream(zip.getEntry(NativeRecipeFunctionObservations.TARGET.replace('.','/')+".class"))) {
            return stream.readAllBytes();
        }
    }
    private static ClassNode read(byte[] bytes) {var node=new ClassNode();new ClassReader(bytes).accept(node,0);return node;}
    private static byte[] write(ClassNode node) {var writer=new ClassWriter(0);node.accept(writer);return writer.toByteArray();}
    private static byte[] method(MethodNode method) {
        var writer=new ClassWriter(0);writer.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,"Witness",null,"java/lang/Object",null);
        method.accept(writer);writer.visitEnd();return writer.toByteArray();
    }
    @Test void retainsEveryOriginalInstructionAndRejectsChangedFactory() throws Exception {
        byte[] original=original();var before=read(original);var after=read(NativeRecipeFunctionObservations.apply(original));
        assertEquals(before.methods.size(),after.methods.size());
        for(int i=0;i<before.methods.size();i++) {
            var source=before.methods.get(i);var observed=after.methods.get(i);
            boolean stackTransform=source.name.equals("transform")&&source.desc.equals("(Lnet/minecraft/item/ItemStack;)Lnet/minecraft/item/ItemStack;");
            if(source.name.equals("reuse")||source.name.equals("noReturn")||source.name.equals("withNbt")||stackTransform) {
                var hook=Arrays.stream(observed.instructions.toArray()).filter(n->n instanceof MethodInsnNode call&&call.name.endsWith("FactoryResult")).findFirst().orElseThrow();
                if(source.name.equals("withNbt")||stackTransform) {
                    assertEquals(1,((VarInsnNode)hook.getPrevious()).var);observed.instructions.remove(hook.getPrevious());
                }
                assertEquals(Opcodes.DUP,hook.getPrevious().getOpcode());
                observed.instructions.remove(hook.getPrevious());observed.instructions.remove(hook);observed.maxStack=source.maxStack;
            }
            assertArrayEquals(method(source),method(observed),source.name);
        }
        for(String mutation:List.of("owner","bootstrap","implementation","continuation","nbt-capture","nbt-predicate","empty-stack-owner","empty-stack-field","empty-stack-opcode","no-return-bootstrap","no-return-body","stack-transform","stack-transform-body")) {
            var node=read(original);var reuse=node.methods.stream().filter(m->m.name.equals("reuse")).findFirst().orElseThrow();
            switch(mutation) {
                case "owner" -> node.name+="Other";
                case "bootstrap" -> {var call=(InvokeDynamicInsnNode)Arrays.stream(reuse.instructions.toArray()).filter(n->n instanceof InvokeDynamicInsnNode).findFirst().orElseThrow();call.name="other";}
                case "implementation" -> node.methods.stream().filter(m->m.name.equals("lambda$reuse$1")).findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
                case "continuation" -> ((MethodInsnNode)Arrays.stream(reuse.instructions.toArray()).filter(n->n instanceof MethodInsnNode).findFirst().orElseThrow()).name="other";
                case "nbt-capture" -> node.methods.stream().filter(m->m.name.equals("withNbt")).findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
                case "nbt-predicate" -> node.methods.stream().filter(m->m.name.equals("lambda$withNbt$6")).findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
                case "stack-transform" -> node.methods.stream().filter(m->m.name.equals("transform")&&m.desc.equals("(Lnet/minecraft/item/ItemStack;)Lnet/minecraft/item/ItemStack;"))
                        .findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
                case "stack-transform-body" -> node.methods.stream().filter(m->m.name.equals("lambda$transform$3"))
                        .findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
                case "empty-stack-owner", "empty-stack-field", "empty-stack-opcode" -> {
                    var empty=(FieldInsnNode)Arrays.stream(node.methods.stream().filter(m->m.name.equals("lambda$noReturn$2")).findFirst().orElseThrow().instructions.toArray()).filter(n->n instanceof FieldInsnNode).findFirst().orElseThrow();
                    if(mutation.equals("empty-stack-owner"))empty.owner+="Other";
                    else if(mutation.equals("empty-stack-field"))empty.name="other";
                    else empty.setOpcode(Opcodes.GETFIELD);
                }
                case "no-return-bootstrap" -> ((InvokeDynamicInsnNode)Arrays.stream(node.methods.stream().filter(m->m.name.equals("noReturn")).findFirst().orElseThrow().instructions.toArray()).filter(n->n instanceof InvokeDynamicInsnNode).findFirst().orElseThrow()).name="other";
                case "no-return-body" -> node.methods.stream().filter(m->m.name.equals("noReturn")).findFirst().orElseThrow().instructions.insert(new InsnNode(Opcodes.NOP));
            }
            assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.apply(write(node)),mutation);
        }
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.apply(NativeRecipeFunctionObservations.apply(original)));
    }
    @Test void originalFactoryIdentityReturnAndFailureSurviveObservation() throws Throwable {
        // Execute unchanged selected factory/lambda bodies. The receiving
        // transform and static empty-stack binding are test witnesses only.
        for(String factoryName:List.of("reuse","noReturn"))for(boolean observed:List.of(false,true)) {
            var node=read(original());node.interfaces.clear();node.fields.clear();
            node.methods.removeIf(m->!Set.of(factoryName,factoryName.equals("reuse")?"lambda$reuse$1":"lambda$noReturn$2").contains(m.name));
            String stack="net/minecraft/item/ItemStack",function="com/cleanroommc/groovyscript/compat/vanilla/ItemStackTransformer";
            String descriptor="(L"+function+";)L"+stack+";";
            node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"transform",descriptor,null,null));
            byte[] input=write(node),bytes=observed?(factoryName.equals("reuse")?NativeRecipeFunctionObservations.applyReuse(input):NativeRecipeFunctionObservations.applyNoReturn(input)):input;
            var definitions=new HashMap<String,byte[]>();definitions.put(NativeRecipeFunctionObservations.TARGET,bytes);
            var w=new ClassWriter(0);w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,stack,null,"java/lang/Object",null);
            w.visitField(Opcodes.ACC_PUBLIC|Opcodes.ACC_STATIC,"field_190927_a","L"+stack+";",null,null).visitEnd();
            var ctor=w.visitMethod(Opcodes.ACC_PUBLIC,"<init>","()V",null,null);ctor.visitCode();ctor.visitVarInsn(Opcodes.ALOAD,0);
            ctor.visitMethodInsn(Opcodes.INVOKESPECIAL,"java/lang/Object","<init>","()V",false);ctor.visitInsn(Opcodes.RETURN);ctor.visitMaxs(1,1);ctor.visitEnd();w.visitEnd();
            definitions.put(stack.replace('/','.'),w.toByteArray());w=new ClassWriter(0);
            w.visit(Opcodes.V17,Opcodes.ACC_PUBLIC|Opcodes.ACC_INTERFACE|Opcodes.ACC_ABSTRACT,function,null,"java/lang/Object",null);
            w.visitMethod(Opcodes.ACC_PUBLIC|Opcodes.ACC_ABSTRACT,"transform","(L"+stack+";)L"+stack+";",null,null).visitEnd();w.visitEnd();
            definitions.put(function.replace('/','.'),w.toByteArray());
            var loader=new ClassLoader(getClass().getClassLoader()) {
                protected Class<?> findClass(String name) throws ClassNotFoundException {
                    byte[] definition=definitions.get(name);if(definition==null)throw new ClassNotFoundException(name);
                    return defineClass(name,definition,0,definition.length);
                }
            };
            Class<?> owner=loader.loadClass(NativeRecipeFunctionObservations.TARGET),stackType=loader.loadClass(stack.replace('/','.'));
            Object empty=stackType.getConstructor().newInstance();stackType.getField("field_190927_a").set(null,empty);
            Object item=stackType.getConstructor().newInstance();var received=new ArrayList<Object>();var failure=new IllegalArgumentException("original transform failure");var fail=new boolean[1];
            Object receiver=Proxy.newProxyInstance(loader,new Class<?>[]{owner},(proxy,called,args)-> {
                if(called.isDefault())return InvocationHandler.invokeDefault(proxy,called,args);
                received.add(args[0]);if(fail[0])throw failure;return item;
            });
            Method reuse=owner.getMethod(factoryName);assertSame(item,reuse.invoke(receiver));assertEquals(1,received.size());
            Object nativeFunction=received.getFirst();var identity=NativeRecipeFunctionObservations.identity(nativeFunction);
            if(observed) {assertNotNull(identity);assertEquals(factoryName+"()Lnet/minecraft/item/ItemStack;",identity.get("factoryMethod"));}
            else assertNull(identity);
            assertEquals(0,NativeRecipeFunctionObservations.observations().get("functionInvocationsByObserver"));
            // Explicit test calls, never observer calls, verify original results.
            Method transform=loader.loadClass(function.replace('/','.')).getMethod("transform",stackType);
            assertSame(factoryName.equals("reuse")?item:empty,transform.invoke(nativeFunction,item));
            assertSame(factoryName.equals("reuse")?null:empty,transform.invoke(nativeFunction,new Object[]{null}));
            fail[0]=true;assertSame(failure,assertThrows(InvocationTargetException.class,()->reuse.invoke(receiver)).getCause());
            assertEquals(2,received.size());
        }
        Object unrelated=new Object();assertNull(NativeRecipeFunctionObservations.identity(unrelated));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.reuseFactoryResult(unrelated));
        assertThrows(IllegalArgumentException.class,()->NativeRecipeFunctionObservations.noReturnFactoryResult(unrelated));
    }
}
