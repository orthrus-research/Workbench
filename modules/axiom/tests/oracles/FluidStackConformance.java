package research.orthrus.axiom;

import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Custom fluid fixtures, never substituted WATER/LAVA or an executed pack. */
public final class FluidStackConformance {
    private static final List<Object> trace = new ArrayList<>();
    @FunctionalInterface interface Action { Object run(); }
    static void check(boolean test, String message) { if (!test) throw new AssertionError(message); }
    static <T extends Throwable> void fails(Class<T> type, Action action) {
        try { action.run(); } catch (Throwable error) {
            if (type.isInstance(error)) return;
            throw new AssertionError("Expected " + type.getName(), error);
        }
        throw new AssertionError("Expected " + type.getName());
    }
    static NativeFluid fluid(String name) { return new NativeFluid(name, new NativeLocation("fixture", "still"), new NativeLocation("fixture", "flow")); }
    static NativeFluid register(String owner, String name) {
        var env = FluidEnvironment.current(); env.runtime().activeMod(owner);
        var f = fluid(name); env.registry().registerFluid(f); return f;
    }
    static void constructors(RegistryRuntime runtime) {
        try (var env = FluidEnvironment.isolatedProducer(runtime)) {
            fails(IllegalArgumentException.class, () -> new NativeFluidStack((NativeFluid) null, 1));
            fails(IllegalArgumentException.class, () -> new NativeFluidStack(fluid("absent"), 1));
            var first = register("first", "same"); var alternative = register("second", "same");
            var stack = new NativeFluidStack(alternative, -50);
            check(stack.getFluid() == alternative && stack.amount == -50, "registered alternative retains identity before rebinding; amounts not validated");
            var ghost = new NativeFluidStack(fluid("same"), 4);
            fails(NullPointerException.class, ghost::getFluid); // name check admits it, delegate lookup returns null.
            check(env.registry().getFluidStack("absent", 1) == null, "missing fluid-stack query returns null");
            check(env.registry().getFluidStack("same", Integer.MIN_VALUE).getFluid() == first, "query uses current default");
            check(stack.getTranslationKey().equals(alternative.getTranslationKey(stack)), "native stack translation dispatch");
            check(env.registry().makeDelegate(alternative).type() == NativeFluid.class, "native fluid delegate type");
            check(env.registry().makeDelegate(alternative).name().toString().equals("minecraft:same"), "delegate location is made from unqualified fluid name");
            trace.add(List.of(stack.amount, stack.getTranslationKey(), env.registry().getMaxID()));
        }
    }
    static void nbtContracts(RegistryRuntime runtime) {
        try (var env = FluidEnvironment.isolatedProducer(runtime)) {
            var f = register("fixture", "coolant");
            var tag = runtime.newNbtCompound(); var nested = runtime.newNbtCompound();
            fails(IllegalArgumentException.class, () -> { tag.setTag("null", null); return null; });
            check(!tag.hasKey("null"), "Cleanroom null guard fails before mutation");
            int[] values = {1,2,3}; nested.setIntArray("values", values); tag.setTag("nested", nested);
            check(tag.getCompoundTag("nested") == nested && tag.getTag("nested") == nested, "wrapper preserves native object aliasing");
            var stack = new NativeFluidStack(f, 3, tag);
            check(stack.tag != tag && stack.tag.equals(tag), "constructor deep-copies NBT");
            values[0] = 99;
            check(nested.getIntArray("values")[0] == 99 && stack.tag.getCompoundTag("nested").getIntArray("values")[0] == 1,
                    "native set array aliases source; copy deep-copies array");
            var output = runtime.newNbtCompound(); check(stack.writeToNBT(output) == output, "write mutates and returns same compound");
            check(output.getCompoundTag("Tag") == stack.tag, "write aliases tag, no defensive copy");
            var loaded = NativeFluidStack.loadFluidStackFromNBT(output);
            check(loaded.tag == stack.tag && loaded.getFluid() == f, "load aliases compound tag");
            var copied = loaded.copy(); check(copied.tag != loaded.tag && copied.isFluidStackIdentical(loaded), "stack copy deep-copies tags");
            output.setString("Tag", "wrong type");
            check(NativeFluidStack.loadFluidStackFromNBT(output).tag.isEmpty(), "wrong Tag type returns native empty compound, not null");
            output.removeTag("Tag"); check(NativeFluidStack.loadFluidStackFromNBT(output).tag == null, "absent Tag is null");
            output.setString("Amount", "9"); check(NativeFluidStack.loadFluidStackFromNBT(output).amount == 0, "wrong amount type is zero, not parsed");
            output.setDouble("Amount", -1.25); check(NativeFluidStack.loadFluidStackFromNBT(output).amount == -2, "native numeric coercion floors double");
            output.setInteger("FluidName", 1); check(NativeFluidStack.loadFluidStackFromNBT(output) == null, "FluidName must be string");
            check(NativeFluidStack.loadFluidStackFromNBT(null) == null, "null NBT is safe");
            var empty = new NativeFluidStack(f, 1, runtime.newNbtCompound()); var absent = new NativeFluidStack(f, 1);
            check(!empty.isFluidEqual(absent), "null and empty NBT differ");
            var reused = runtime.newNbtCompound(); reused.setTag("Tag", tag); absent.writeToNBT(reused);
            check(reused.getCompoundTag("Tag") == tag, "writing null tag does not erase previous target Tag");
            check(NativeFluidStack.areFluidStackTagsEqual(null,null) && !NativeFluidStack.areFluidStackTagsEqual(null,empty), "nullable tag comparisons");
            trace.add(List.of(copied.tag.toString(), copied.tag.hashCode(), stack.amount));
        }
    }
    static void defaults(RegistryRuntime runtime) {
        try (var env = FluidEnvironment.isolatedProducer(runtime)) {
            var r = env.registry(); var a = register("first", "same"); var b = register("second", "same");
            var aStack = new NativeFluidStack(a, 7); var bStack = new NativeFluidStack(b, 7);
            check(!aStack.isFluidEqual(bStack), "different registered alternatives initially unequal");
            r.addBucketForFluid(a); var buckets = r.getBucketFluids();
            var view = r.getRegisteredFluids(); var idView = r.getRegisteredFluidIDs();
            var ids = new NativeBiMap<NativeFluid,Integer>(runtime); ids.put(a, 21);
            r.initFluidIDs(ids, new HashSet<>(Set.of("second:same")));
            check(r.getFluid("same") == b && aStack.getFluid() == b && bStack.getFluid() == b, "all mapped delegates rebind to new default");
            check(aStack.isFluidStackIdentical(bStack), "stack equality follows delegate rebinding");
            check(r.getMaxID() == 1 && r.fluidIDs.get(b) == 21, "maxID is map size, not largest ID");
            check(ids == r.fluidIDs && ids.containsKey(b) && !ids.containsKey(a), "default loading mutates and retains caller ID map");
            check(view.get("same") == a && idView.containsKey(a), "old read-only views stay on replaced maps");
            check(buckets.contains(a) && r.getBucketFluids().contains(b), "default loading invalidates bucket cache only for future reads");
            fails(UnsupportedOperationException.class, () -> { r.getRegisteredFluids().clear(); return null; });
            var restored = new NativeFluidStack(a, 1); check(restored.getFluid() == b, "constructing with old native identity follows its rebound delegate");
            check(r.getModId(restored).equals("second"), "stack mod ID follows current referent, not original owner");
            var written = runtime.newNbtCompound(); r.writeDefaultFluidList(written);
            check(written.getTagList("DefaultFluidList", NativeNbtTypes.TAG_STRING).getStringTagAt(0).equals("second:same"), "native default list persists selected owner");
            var alternativeDefaults = runtime.newNbtCompound(); var list = runtime.newNbtList(); list.appendTag(runtime.nbtString("first:same"));
            alternativeDefaults.setTag("DefaultFluidList", list); r.loadFluidDefaults(alternativeDefaults);
            check(aStack.getFluid() == a && bStack.getFluid() == a, "NBT default restore rebinds existing stacks");
            check(r.getMaxID() == 1, "NBT default restore leaves maxID unchanged");
            trace.add(List.of(written.toString(), aStack.amount, r.getDefaultFluidName(a)));
        }
    }
    static void failures(RegistryRuntime runtime) {
        try (var env = FluidEnvironment.isolatedProducer(runtime)) {
            var r = env.registry(); var a = register("first", "same"); var b = register("second", "same");
            var stack = new NativeFluidStack(a, 1); var oldDelegate = r.makeDelegate(a);
            runtime.activeMod("first"); r.registerFluid(a); check(r.makeDelegate(a) != oldDelegate, "re-registering replaces stored delegate even when name is duplicate");
            var ids = new NativeBiMap<NativeFluid,Integer>(runtime); ids.put(a,4);
            r.initFluidIDs(ids,new HashSet<>(Set.of("second:same")));
            check(stack.getFluid() == a && new NativeFluidStack(a,1).getFluid() == b, "old displaced delegates are not rebound");
            var missing = new NativeBiMap<NativeFluid,Integer>(runtime); missing.put(b,3);
            r.initFluidIDs(missing,new HashSet<>(Set.of("missing:same")));
            check(r.getFluid("same") == a, "missing selected owner falls back to original local default owner");
            var input = new NativeBiMap<NativeFluid,Integer>(runtime); input.put(a,9);
            fails(ArrayIndexOutOfBoundsException.class, () -> { r.initFluidIDs(input,Set.of("malformed")); return null; });
            check(r.getMaxID() == 1, "init maxID is changed before failing default parse");
            fails(UnsupportedOperationException.class, () -> { r.initFluidIDs(input,Set.of()); return null; });
            var defaults = new HashSet<String>(); r.initFluidIDs(input,defaults);
            check(defaults.equals(Set.of("first:same")), "empty mutable default set is filled with local defaults");
            r.validateFluidRegistry();
            var rogue = fluid("rogue"); r.fluids.put("rogue",rogue);
            fails(IllegalStateException.class, () -> { r.validateFluidRegistry(); return null; });
            trace.add(List.of(defaults.stream().sorted().toList(), stack.getFluid() == a));
        }
    }
    static void missingDefault(RegistryRuntime runtime) {
        try (var env = FluidEnvironment.isolatedProducer(runtime)) {
            var r=env.registry(); var a=register("first","same"); var b=register("second","same");
            var stack=new NativeFluidStack(a,1); var ids=new NativeBiMap<NativeFluid,Integer>(runtime);
            r.initFluidIDs(ids,new HashSet<>(Set.of("second:same")));
            check(ids.containsKey(b) && ids.get(b)==null && r.fluidNames.get(null).equals("same"), "missing old ID retains native null ID mapping");
            check(stack.getFluid()==b, "null ID does not prevent rebinding");
            trace.add(List.of(r.getMaxID(),r.fluidNames.size()));
        }
    }
    static void materialsAndGroovy(RegistryRuntime runtime) throws Exception {
        try (var env=FluidEnvironment.isolatedProducer(runtime)) {
            runtime.activeMod("gregtech"); runtime.materials().unfreezeRegistries();
            var material=new FluidMaterial.Builder(4000,new NativeLocation("gregtech","stack_fixture")).liquid().plasma().build();
            fails(IllegalArgumentException.class,()->material.getFluid(1));
            runtime.materials().closeRegistries(); runtime.materials().freezeRegistries(); env.registerMaterialFluids();
            var stack=material.getFluid(13); check(stack.getFluid()==material.getFluid(), "material amount overload uses registered fluid");
            check(material.getPlasma(7).getFluid()==material.getFluid(env.storageKeys().PLASMA), "plasma overload preserves source identity");
            check(material.getProperty(PropertyKey.FLUID).solidifiesFrom(-3).getFluid()==stack.getFluid(), "solidifying stack uses native factory");
            var shell=new groovy.lang.GroovyShell(FluidStackConformance.class.getClassLoader(), new groovy.lang.Binding(Map.of("material",material,"tag",runtime.newNbtCompound())));
            Object result=shell.evaluate("""
                def stack = material.getFluid(37)
                stack.tag = tag
                tag.setString('developer', 'source')
                def copy = stack.copy()
                assert copy == stack
                assert !copy.tag.is(stack.tag)
                copy.amount = -9
                assert copy == stack
                assert !copy.isFluidStackIdentical(stack)
                try { material.getFluid('bad amount'); assert false }
                catch (groovy.lang.MissingMethodException expected) { assert expected.method == 'getFluid' }
                return [copy.amount, copy.tag.getString('developer')]
                """, "FluidStackDeveloperFixture.groovy");
            check(result.equals(List.of(-9,"source")), "real Groovy dispatch and native stack semantics"); trace.add(result); shell.getClassLoader().close();
        }
    }
    static Object raw(Object value, String method, Class<?>[] parameters, Object... args) throws Exception {
        return value.getClass().getMethod(method,parameters).invoke(value,args);
    }
    static void nbtVectors(RegistryRuntime runtime) throws Exception {
        var random=new Random(0x4e4254L);
        for(int i=0;i<4096;i++) {
            var nbt=runtime.newNbtCompound(); Object original=runtime.nativeType("fy").getConstructor().newInstance();
            int number=random.nextInt(); double real=(random.nextDouble()-.5)*1e10;
            switch(i%6) {
                case 0 -> { nbt.setInteger("value",number); raw(original,"a",new Class<?>[]{String.class,int.class},"value",number); }
                case 1 -> { nbt.setDouble("value",real); raw(original,"a",new Class<?>[]{String.class,double.class},"value",real); }
                case 2 -> { nbt.setString("value","s"+number); raw(original,"a",new Class<?>[]{String.class,String.class},"value","s"+number); }
                case 3 -> { nbt.setLong("value",(long)number<<32); raw(original,"a",new Class<?>[]{String.class,long.class},"value",(long)number<<32); }
                case 4 -> { nbt.setShort("value",(short)number); raw(original,"a",new Class<?>[]{String.class,short.class},"value",(short)number); }
                case 5 -> { nbt.setByte("value",(byte)number); raw(original,"a",new Class<?>[]{String.class,byte.class},"value",(byte)number); }
            }
            check(nbt.toString().equals(original.toString()) && nbt.hashCode()==original.hashCode(), "native NBT storage/hash binding");
            check(nbt.getInteger("value")== (int)raw(original,"h",new Class<?>[]{String.class},"value"), "native NBT integer coercion binding");
            check(nbt.getString("value").equals(raw(original,"l",new Class<?>[]{String.class},"value")), "native NBT string binding");
            check(nbt.hasKey("value",99)==(boolean)raw(original,"b",new Class<?>[]{String.class,int.class},"value",99), "native numeric type binding");
            trace.add(List.of(nbt.toString(),nbt.getInteger("value"),nbt.getTagId("value")));
        }
    }
    static void stackVectors(RegistryRuntime runtime) {
        try (var env=FluidEnvironment.isolatedProducer(runtime)) {
            var a=register("a","same"); var b=register("b","same"); var random=new Random(0x535441434bL);
            for(int i=0;i<8192;i++) {
                var tag=runtime.newNbtCompound(); tag.setInteger("x",random.nextInt(4));
                var x=new NativeFluidStack(i%2==0?a:b,random.nextInt(),i%3==0?null:tag);
                var y=new NativeFluidStack(i%5==0?a:b,random.nextInt(),i%7==0?null:tag);
                check(x.equals(y)==x.isFluidEqual(y), "equals delegates to native fluid/tag comparison");
                var copy=x.copy(); check(copy.isFluidStackIdentical(x), "native vector copy");
                int expected=31+x.getFluid().hashCode(); if(x.tag!=null)expected=31*expected+x.tag.hashCode();
                check(expected==x.hashCode(), "native amount-independent hash");
                var saved=runtime.newNbtCompound(); x.writeToNBT(saved); var loaded=NativeFluidStack.loadFluidStackFromNBT(saved);
                check(loaded.getFluid()==env.registry().getFluid("same"), "NBT load resolves current default, not saved alternative");
                trace.add(Arrays.asList(x.amount,y.amount,x.isFluidEqual(y),x.containsFluid(y),x.isFluidStackIdentical(y),
                        NativeFluidStack.areFluidStackTagsEqual(x,y),saved.toString(),x.getFluid()==a));
                if(i%127==0) {
                    var ids=new NativeBiMap<NativeFluid,Integer>(runtime,env.registry().fluidIDs);
                    env.registry().initFluidIDs(ids,new HashSet<>(Set.of((i%2==0?"a":"b")+":same")));
                }
            }
        }
    }
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        org.apache.logging.log4j.core.config.Configurator.setLevel("FML",org.apache.logging.log4j.Level.OFF);
        try(var runtime=RegistryRuntime.open(Path.of(args[0]))) {
            if(args.length>1 && args[1].equals("source-edit")) {
                try(var env=FluidEnvironment.isolatedProducer(runtime)) {
                    var stack=new NativeFluidStack(register("fixture","edit"),7);
                    System.out.println(Json.write(Map.of("amount",stack.amount,"name",stack.getFluid().getName(),"wholePackParity",false))); return;
                }
            }
            constructors(runtime); nbtContracts(runtime); defaults(runtime); failures(runtime); missingDefault(runtime);
            materialsAndGroovy(runtime); nbtVectors(runtime); stackVectors(runtime);
            String digest=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Json.write(trace).getBytes(StandardCharsets.UTF_8)));
            System.out.println(Json.write(Map.of("scenarios",6,"stackVectors",8192,"nbtVectors",4096,"traceDigest",digest,
                    "kernelIsolation",true,"minecraftLaunched",false,"wholePackParity",false)));
        }
    }
}
