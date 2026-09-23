package research.orthrus.axiom.materialhost;

import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.util.*;

/** Retains the identity returned by an original native lambda factory. The
 * observer never calls the function, constructs one, or replaces its result. */
public final class NativeRecipeFunctionObservations {
    public static final String TARGET="com.cleanroommc.groovyscript.compat.vanilla.ItemStackMixinExpansion";
    public static final String BOMBLET_TARGET="icbm.classic.content.cluster.bomblet.ItemBombDroplet";
    public static final String BACKPACK_TARGET="com.cleanroommc.retrosophisticatedbackpacks.capability.BackpackWrapper";
    public static final String CARGO_TARGET="icbm.classic.ICBMClassic";
    public static final String CONDITION_TARGET="me.ichun.mods.ichunutil.common.recipe.internal.RecipeCompactPorkchop";
    public static final String MULTI_RECIPE_TARGET="vazkii.arl.recipe.MultiRecipe";
    public static final String BLACKLIST_TARGET="vazkii.arl.recipe.BlacklistOreIngredient";
    public static final String GADGET_TARGET="com.direwolf20.buildinggadgets.common.items.gadgets.GadgetGeneric";
    public static final Set<String> VALUE_FACTORY_TARGETS=Set.of(GADGET_TARGET,
            "vazkii.quark.decoration.feature.VariedBookshelves","vazkii.quark.decoration.feature.VariedChests");
    public static final Set<String> STORAGE_TARGETS=Set.of(
            "funwayguy.bdsandm.inventory.capability.CapabilityProviderBarrel",
            "funwayguy.bdsandm.inventory.capability.CapabilityProviderCrate");
    private static final String OWNER=TARGET.replace('.','/');
    private static final String STACK="Lnet/minecraft/item/ItemStack;";
    private static final String FUNCTION="Lcom/cleanroommc/groovyscript/compat/vanilla/ItemStackTransformer;";
    private static final String NBT="Lnet/minecraft/nbt/NBTTagCompound;";
    private static final String CAPABILITIES="("+STACK+NBT+")Lnet/minecraftforge/common/capabilities/ICapabilityProvider;";
    private static final String CALLBACK=NativeRecipeFunctionObservations.class.getName().replace('.','/');
    private record ValueFactory(String method,String descriptor,String routeHash,String implementation,String implementationDescriptor,
                                String implementationHash,boolean captureOwner) {}
    private static final Map<String,ValueFactory> VALUE_FACTORIES=Map.of(
            GADGET_TARGET,new ValueFactory("initCapabilities",CAPABILITIES,
                    "3757be1401003f18a36af7224e7be361acfc70d83d12ca234f35e37e14a6bf2d","getEnergyMax","()I",
                    "0c1c91f2e564a6c020cd99b59a5766dcb47afd8a5c2f19ef7035c7a613046f4f",true),
            "vazkii.quark.decoration.feature.VariedBookshelves",new ValueFactory("preInit",
                    "(Lnet/minecraftforge/fml/common/event/FMLPreInitializationEvent;)V",
                    "9d3f18f58c7617aae3a71fdd04ffa792155b7c37313180e13fda8491aa5cdf23","lambda$preInit$0","("+STACK+")Z",
                    "b7acf16552db5e0bfa1708026a8c87e73dcd27389b775065e7763f8ae9e672ae",false),
            "vazkii.quark.decoration.feature.VariedChests",new ValueFactory("fixTrappedChestRecipe",
                    "(Lnet/minecraft/item/crafting/IRecipe;)V",
                    "a25176035abb2d247679665238feae2725130e05ff8504710e0d2f5e48b201e2","lambda$fixTrappedChestRecipe$1","("+STACK+")Z",
                    "5c5dce30de4fd6b2d86a6d2e9f60631536da812d9ff6ff8c6389e96d7024c183",false));
    private static final Handle FACTORY=new Handle(Opcodes.H_INVOKESTATIC,"java/lang/invoke/LambdaMetafactory","metafactory",
            "(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/String;Ljava/lang/invoke/MethodType;Ljava/lang/invoke/MethodType;Ljava/lang/invoke/MethodHandle;Ljava/lang/invoke/MethodType;)Ljava/lang/invoke/CallSite;",false);
    private static final Handle IMPLEMENTATION=new Handle(Opcodes.H_INVOKESTATIC,OWNER,"lambda$reuse$1","("+STACK+")"+STACK,true);
    private static final Map<String,Object> REUSE=Map.of("kind","native-lambda-factory-result","owner",TARGET,
            "factoryMethod","reuse()"+STACK,"implementation",IMPLEMENTATION.getName()+IMPLEMENTATION.getDesc(),"capturedArguments",List.of());
    private static final Handle NO_RETURN_IMPLEMENTATION=new Handle(Opcodes.H_INVOKESTATIC,OWNER,"lambda$noReturn$2","("+STACK+")"+STACK,true);
    private static final Map<String,Object> NO_RETURN=Map.of("kind","native-lambda-factory-result","owner",TARGET,
            "factoryMethod","noReturn()"+STACK,"implementation",NO_RETURN_IMPLEMENTATION.getName()+NO_RETURN_IMPLEMENTATION.getDesc(),"capturedArguments",List.of());
    private static final Map<Object,Map<String,Object>> IDENTITIES=new IdentityHashMap<>();
    private static final Map<Object,Map<Thread,Object>> CRAFTING_MATCHES=new IdentityHashMap<>();
    private static final Set<String> transformedInputs=new TreeSet<>();
    private static final Map<String,Map<String,Object>> SOURCE_COMPILATIONS=new TreeMap<>();
    private static long factoryResults;
    private NativeRecipeFunctionObservations() {}

    /** Fail closed on any change to the selected factory route or implementation. */
    public static byte[] apply(byte[] input) {
        byte[] output=applyNoReturn(applyReuse(input));
        var node=new ClassNode();new ClassReader(output).accept(node,0);
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        String stackTransform="("+STACK+")"+STACK;
        require(methodDigest(method(selected,"transform",stackTransform)).equals("3654c882e0e06855e7a8471845e0c8d2714d5b7743df46f117a0e37940e2a840")
                &&methodDigest(method(selected,"lambda$transform$3","("+STACK+STACK+")"+STACK))
                    .equals("eebb35d04bd03f1408288c6e7e8226116644e98941550d6131fd841b9820c264"),"stored stack transform route");
        var stackRoute=method(node,"transform",stackTransform);
        var stackFactory=instructions(stackRoute).stream().filter(i->i instanceof InvokeDynamicInsnNode).findFirst().orElseThrow();
        var stackHook=new InsnList();stackHook.add(new InsnNode(Opcodes.DUP));stackHook.add(new VarInsnNode(Opcodes.ALOAD,1));
        stackHook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"stackTransformFactoryResult","(Ljava/lang/Object;Ljava/lang/Object;)V",false));
        stackRoute.instructions.insert(stackFactory,stackHook);stackRoute.maxStack+=2;
        String descriptor="("+NBT+")Lcom/cleanroommc/groovyscript/api/INBTResourceStack;";
        require(methodDigest(method(selected,"withNbt",descriptor)).equals("07e41cf5b9d09d1b5b8b6861c27c8e184933ec48726b3514530ffee39bfb65bf")
                &&methodDigest(method(selected,"lambda$withNbt$6","("+NBT+NBT+")Z")).equals("a40e4492cd8ef0426c109f2d6217f991904dc36ca5b5164959bd5807aa74808e"),"NBT matcher route");
        var withNbt=method(node,"withNbt",descriptor);
        var factory=instructions(withNbt).stream().filter(i->i instanceof InvokeDynamicInsnNode).findFirst().orElseThrow();
        var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));hook.add(new VarInsnNode(Opcodes.ALOAD,1));
        hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"withNbtFactoryResult","(Ljava/lang/Object;Ljava/lang/Object;)V",false));
        withNbt.instructions.insert(factory,hook);withNbt.maxStack=Math.max(4,withNbt.maxStack);
        var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}
        return writer.toByteArray();
    }
    /** These original factories store deferred constructors/config predicates.
     * Observe their results without constructing cargo or testing a condition. */
    public static byte[] applyCraftingFactory(byte[] input) {
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        String owner=selected.name.replace('/','.');boolean cargo=owner.equals(CARGO_TARGET);
        require(cargo||owner.equals(CONDITION_TARGET),"crafting factory owner");
        String name=cargo?"registerRecipes":"parse";
        String descriptor=cargo?"(Lnet/minecraftforge/event/RegistryEvent$Register;)V"
                :"(Lnet/minecraftforge/common/crafting/JsonContext;Lcom/google/gson/JsonObject;)Lnet/minecraft/item/crafting/IRecipe;";
        require(methodDigest(method(selected,name,descriptor)).equals(cargo
                ?"17e4931aace863880943fe4f6952fe043c6f74618e21956ee31afe210531a0d5"
                :"4a3a9feb1496c0929942e7aed6ccdb81e4e621af237ba2838a8fff0f73efbbdf"),"crafting factory route");
        if(!cargo)require(methodDigest(method(selected,"lambda$parse$0","()Z"))
                .equals("d23be3f02dac90936850a6b178cb4cde7aba68bae5fe1487c51143be4b0c9cfd"),"crafting condition body");
        var node=new ClassNode();new ClassReader(input).accept(node,0);var route=method(node,name,descriptor);
        var factories=instructions(route).stream().filter(i->i instanceof InvokeDynamicInsnNode).toList();
        require(factories.size()==(cargo?2:1),"crafting factory count");
        for(var instruction:factories) {
            var factory=(InvokeDynamicInsnNode)instruction;
            require(factory.bsm.equals(FACTORY)&&Type.getArgumentTypes(factory.desc).length==0,"crafting factory bootstrap");
            var implementation=(Handle)factory.bsmArgs[1];
            String identity=implementation.getOwner().replace('/','.')+"#"+implementation.getName()+implementation.getDesc();
            var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));hook.add(new LdcInsnNode(owner));
            hook.add(new LdcInsnNode(name+descriptor));hook.add(new LdcInsnNode(identity));
            hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"craftingFactoryResult",
                    "(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V",false));
            route.instructions.insert(factory,hook);
        }
        route.maxStack+=4;var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}return writer.toByteArray();
    }
    /** Track only the original private crafting cache writes. Reading a
     * ThreadLocal with get() would initialize it; the observer never does so. */
    public static byte[] applyAutoRegLib(byte[] input) {
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        boolean multi=selected.name.equals(MULTI_RECIPE_TARGET.replace('.','/'));
        require(multi||selected.name.equals(BLACKLIST_TARGET.replace('.','/')),"AutoRegLib observation owner");
        String constructor=multi?"(Lnet/minecraft/util/ResourceLocation;)V":"(Ljava/lang/String;Ljava/util/function/Predicate;)V";
        require(methodDigest(method(selected,"<init>",constructor)).equals(multi
                ?"6a4ad110256e662ecec606c8d8a296138caa27457757975b9535e49549256d5d"
                :"38ac4eaa32eee1cc134dc8d9ad8e6951f59d983299bd49b6b5f3f3bd15d8c332"),"AutoRegLib constructor");
        var node=new ClassNode();new ClassReader(input).accept(node,0);var creation=method(node,"<init>",constructor);
        if(multi) {
            String match="(Lnet/minecraft/inventory/InventoryCrafting;Lnet/minecraft/world/World;)Z";
            require(methodDigest(method(selected,"func_77569_a",match)).equals("e56d6bc29f7a56e93604648d03eb0f25c2d636e41b0ffae186d24d8b64b449d7")
                    &&methodDigest(method(selected,"func_77572_b","(Lnet/minecraft/inventory/InventoryCrafting;)"+STACK))
                        .equals("ceedba2813685d01222efd99b81f91011033f39b80f1158891fb52f52f76d083"),"MultiRecipe cache routes");
            var returns=instructions(creation).stream().filter(i->i.getOpcode()==Opcodes.RETURN).toList();require(returns.size()==1,"MultiRecipe constructor return");
            var hook=new InsnList();hook.add(new VarInsnNode(Opcodes.ALOAD,0));
            hook.add(new FieldInsnNode(Opcodes.GETFIELD,node.name,"matched","Ljava/lang/ThreadLocal;"));
            hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"craftingCacheCreated","(Ljava/lang/Object;)V",false));
            creation.instructions.insertBefore(returns.getFirst(),hook);creation.maxStack+=1;
            var route=method(node,"func_77569_a",match);int writes=0;
            for(var instruction:route.instructions.toArray())if(instruction instanceof MethodInsnNode call
                    &&call.owner.equals("java/lang/ThreadLocal")&&call.name.equals("set")&&call.desc.equals("(Ljava/lang/Object;)V")) {
                route.instructions.insertBefore(call,new InsnNode(Opcodes.DUP2));
                route.instructions.insert(call,new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"craftingCacheWritten",
                        "(Ljava/lang/Object;Ljava/lang/Object;)V",false));writes++;
            }
            require(writes==2,"MultiRecipe cache writes");route.maxStack+=2;
        } else {
            var calls=instructions(creation).stream().filter(i->i instanceof MethodInsnNode call
                    &&call.owner.equals("java/util/function/Predicate")&&call.name.equals("negate")
                    &&call.desc.equals("()Ljava/util/function/Predicate;")).toList();require(calls.size()==1,"Blacklist predicate factory");
            var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));hook.add(new VarInsnNode(Opcodes.ALOAD,2));
            hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"negatedPredicateResult","(Ljava/lang/Object;Ljava/lang/Object;)V",false));
            creation.instructions.insert(calls.getFirst(),hook);creation.maxStack+=2;
        }
        var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}return writer.toByteArray();
    }
    public static byte[] applyValueFactory(byte[] input) {
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        String owner=selected.name.replace('/','.');var pin=VALUE_FACTORIES.get(owner);require(pin!=null,"stored value factory owner");
        require(methodDigest(method(selected,pin.method,pin.descriptor)).equals(pin.routeHash)
                &&methodDigest(method(selected,pin.implementation,pin.implementationDescriptor)).equals(pin.implementationHash),"stored value factory route");
        var node=new ClassNode();new ClassReader(input).accept(node,0);var route=method(node,pin.method,pin.descriptor);
        var factories=instructions(route).stream().filter(i->i instanceof InvokeDynamicInsnNode).toList();
        require(factories.size()==1,"stored value factory count");var factory=(InvokeDynamicInsnNode)factories.getFirst();
        var implementation=(Handle)factory.bsmArgs[1];
        require(factory.bsm.equals(FACTORY)&&implementation.getOwner().equals(node.name)
                &&implementation.getName().equals(pin.implementation)&&implementation.getDesc().equals(pin.implementationDescriptor)
                &&Type.getArgumentTypes(factory.desc).length==(pin.captureOwner?1:0),"stored value factory bootstrap");
        var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));hook.add(new LdcInsnNode(owner));
        hook.add(pin.captureOwner?new VarInsnNode(Opcodes.ALOAD,0):new InsnNode(Opcodes.ACONST_NULL));
        hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"valueFactoryResult","(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",false));
        route.instructions.insert(factory,hook);route.maxStack+=3;
        var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}return writer.toByteArray();
    }
    public static void valueFactoryResult(Object value,String owner,Object capture) {
        var pin=VALUE_FACTORIES.get(owner);
        require(pin!=null&&value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost().getName().equals(owner)
                &&(pin.captureOwner?capture!=null:capture==null),"stored value factory identity");
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",owner,
                "factoryMethod",pin.method+pin.descriptor,"implementation",pin.implementation+pin.implementationDescriptor,
                "capturedArguments",pin.captureOwner?List.of(capture):List.of());
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    public static synchronized void craftingCacheCreated(Object value) {
        require(value!=null&&value.getClass()==ThreadLocal.class&&!CRAFTING_MATCHES.containsKey(value),"fresh crafting cache");
        CRAFTING_MATCHES.put(value,new IdentityHashMap<>());
    }
    public static synchronized void craftingCacheWritten(Object cache,Object value) {
        require(CRAFTING_MATCHES.containsKey(cache),"original crafting cache write");
        CRAFTING_MATCHES.get(cache).put(Thread.currentThread(),value);
    }
    public static synchronized Map<String,Object> craftingCache(Object cache) {
        if(!CRAFTING_MATCHES.containsKey(cache))return null;
        var row=new LinkedHashMap<String,Object>();row.put("type",ThreadLocal.class.getName());
        row.put("scope","current-native-initialization-thread-crafting-match-value");
        row.put("value",CRAFTING_MATCHES.get(cache).get(Thread.currentThread()));row.put("threadLocalGetsByObserver",0);return row;
    }
    public static void negatedPredicateResult(Object value,Object original) {
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost()==java.util.function.Predicate.class
                &&original instanceof java.util.function.Predicate<?>,"original negated predicate");
        var identity=Map.<String,Object>of("kind","native-predicate-negate-result","owner","java.util.function.Predicate",
                "factoryMethod","negate()Ljava/util/function/Predicate;","capturedArguments",List.of(original));
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    /** Retain the original bound ItemStack.copy supplier without evaluating it. */
    public static byte[] applyBomblet(byte[] input) {
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        require(selected.name.equals(BOMBLET_TARGET.replace('.','/')),"bomblet owner");
        require(methodDigest(method(selected,"initCapabilities",CAPABILITIES))
                .equals("757d40cf87cd1abb774cbeb1e0512b279a715f34515c4cab4de4ee5e0ab1cbd4"),"bomblet capability factory route");
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        var method=method(node,"initCapabilities",CAPABILITIES);
        var factories=instructions(method).stream().filter(i->i instanceof InvokeDynamicInsnNode).toList();
        require(factories.size()==1,"bomblet factory count");
        var factory=(InvokeDynamicInsnNode)factories.getFirst();
        var copy=new Handle(Opcodes.H_INVOKEVIRTUAL,"net/minecraft/item/ItemStack","func_77946_l","()"+STACK,false);
        require(factory.name.equals("get")&&factory.desc.equals("("+STACK+")Ljava/util/function/Supplier;")
                &&factory.bsm.equals(FACTORY)&&Arrays.equals(factory.bsmArgs,new Object[]{
                    Type.getMethodType("()Ljava/lang/Object;"),copy,Type.getMethodType("()"+STACK)}),"bomblet factory bootstrap");
        var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));hook.add(new VarInsnNode(Opcodes.ALOAD,1));
        hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"bombletFactoryResult","(Ljava/lang/Object;Ljava/lang/Object;)V",false));
        method.instructions.insert(factory,hook);method.maxStack+=2;
        var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}
        return writer.toByteArray();
    }
    /** Observe the original storage callback factory result and its three
     * captures. Serialization and callback dispatch both mutate the native stack. */
    public static byte[] applyStorage(byte[] input) {
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        require(STORAGE_TARGETS.contains(selected.name.replace('/','.')),"storage provider owner");
        boolean barrel=selected.name.endsWith("Barrel");
        String descriptor="("+STACK+")L"+selected.name+";";
        String implementation="lambda$setParentStack$0",bodyDescriptor="("+STACK+STACK+")V";
        require(methodDigest(method(selected,"setParentStack",descriptor)).equals(barrel
                ?"b00d020f77c7082e59e0452f0410c9fa07b0f7a32fab334f89f7479dbc0e8d54"
                :"8e8bf42ec0d19d9b44b151b1877e5c04b46d2ab4e506a631e085acf3fc4f3b52"),"storage factory route");
        require(methodDigest(method(selected,implementation,bodyDescriptor)).equals(barrel
                ?"d33153edcd797cf5640d0120bb2e8ac1f9112ed79ee2a6574c7464ba8aad738d"
                :"4cbb9235f16eaeed0b40bba5e74b836cf43dde675354ff7dd7ed9da4dadce902"),"storage callback body");
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        var route=method(node,"setParentStack",descriptor);
        var factories=instructions(route).stream().filter(i->i instanceof InvokeDynamicInsnNode).toList();
        require(factories.size()==1,"storage factory count");
        var factory=(InvokeDynamicInsnNode)factories.getFirst();
        require(factory.name.equals("onCrateChanged")&&factory.desc.equals("(L"+node.name+";"+STACK+STACK+
                ")Lfunwayguy/bdsandm/inventory/capability/ICrateCallback;")&&factory.bsm.equals(FACTORY)
                &&Arrays.equals(factory.bsmArgs,new Object[]{Type.getMethodType("()V"),
                    new Handle(Opcodes.H_INVOKESPECIAL,node.name,implementation,bodyDescriptor,false),Type.getMethodType("()V")}),
                "storage factory bootstrap");
        var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));
        for(int local:List.of(0,1,2))hook.add(new VarInsnNode(Opcodes.ALOAD,local));
        hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"storageFactoryResult",
                "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)V",false));
        route.instructions.insert(factory,hook);route.maxStack=Math.max(6,route.maxStack);
        var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}
        return writer.toByteArray();
    }
    /** Original Kotlin capacity factories, including those retained by native
     * ItemStack copies through deserializeNBT. Keep their bootstrap and result. */
    public static byte[] applyBackpack(byte[] input) {
        var selected=new ClassNode();new ClassReader(input).accept(selected,ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
        require(selected.name.equals(BACKPACK_TARGET.replace('.','/')),"backpack owner");
        String constructor="(Lkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function0;Ljava/util/UUID;ILkotlin/jvm/internal/DefaultConstructorMarker;)V";
        var pins=Map.of("<init>"+constructor,"e546ce4f0d2bc4de78060fd452bc6c8c92fcfeb493257c459af6b9558f512ca6",
                "deserializeNBT("+NBT+")V","a4ba697ed9484ed08fd6fac0596d142b433644bb033b02ee66854c6a2314a58f",
                "_init_$lambda$0()I","85addb510c1cdec1f6a910ff4c403f61dc6419f203af022392ebb73268906c4d",
                "_init_$lambda$1()I","0ef6ae5763f4e01c6a35c41bb687d69ec06df346aa2782b68f02408e26be4a6d",
                "deserializeNBT$lambda$0("+NBT+")I","0d64a2ed1ca656f87b5db3fb3243548dce8754cef1d79610a0c63db262aac336",
                "deserializeNBT$lambda$1("+NBT+")I","db1728f54d8b17614afa462a9a51371209ef1c37d2093394b09600933f38c886");
        for(var pin:pins.entrySet()) {
            int descriptor=pin.getKey().indexOf('(');
            require(methodDigest(method(selected,pin.getKey().substring(0,descriptor),pin.getKey().substring(descriptor)))
                    .equals(pin.getValue()),"backpack route "+pin.getKey());
        }
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        for(boolean nbt:List.of(false,true)) {
            var route=method(node,nbt?"deserializeNBT":"<init>",nbt?"("+NBT+")V":constructor);
            var factories=instructions(route).stream().filter(i->i instanceof InvokeDynamicInsnNode).toList();
            require(factories.size()==2,"backpack factory count");
            for(int index=0;index<2;index++) {
                var factory=(InvokeDynamicInsnNode)factories.get(index);
                String implementation=(nbt?"deserializeNBT":"_init_")+"$lambda$"+index;
                var target=new Handle(Opcodes.H_INVOKESTATIC,node.name,implementation,nbt?"("+NBT+")I":"()I",false);
                require(factory.name.equals("invoke")&&factory.desc.equals("("+(nbt?NBT:"")+")Lkotlin/jvm/functions/Function0;")
                        &&factory.bsm.equals(FACTORY)&&Arrays.equals(factory.bsmArgs,new Object[]{
                            Type.getMethodType("()Ljava/lang/Object;"),target,Type.getMethodType("()Ljava/lang/Integer;")}),
                        "backpack factory bootstrap");
                var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));hook.add(new LdcInsnNode(implementation));
                hook.add(nbt?new VarInsnNode(Opcodes.ALOAD,1):new InsnNode(Opcodes.ACONST_NULL));
                hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,"backpackFactoryResult",
                        "(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",false));
                route.instructions.insert(factory,hook);
            }
            route.maxStack+=3;
        }
        var writer=new ClassWriter(0);node.accept(writer);
        synchronized(IDENTITIES) {transformedInputs.add(digest(input));}
        return writer.toByteArray();
    }
    static byte[] applyReuse(byte[] input) {
        return applyTransformFactory(input,"reuse",IMPLEMENTATION,"reuseFactoryResult",false);
    }
    static byte[] applyNoReturn(byte[] input) {
        return applyTransformFactory(input,"noReturn",NO_RETURN_IMPLEMENTATION,"noReturnFactoryResult",true);
    }
    private static byte[] applyTransformFactory(byte[] input,String name,Handle target,String callback,boolean emptyReturn) {
        var node=new ClassNode();new ClassReader(input).accept(node,0);
        require(node.name.equals(OWNER)&&(node.access&Opcodes.ACC_INTERFACE)!=0,"owner");
        MethodNode creation=method(node,name,"()"+STACK);
        MethodNode implementation=method(node,target.getName(),target.getDesc());
        var body=instructions(creation);var lambda=instructions(implementation);
        require(creation.access==Opcodes.ACC_PUBLIC&&creation.tryCatchBlocks.isEmpty()&&body.size()==4,name+" body");
        require(implementation.access==(Opcodes.ACC_PRIVATE|Opcodes.ACC_STATIC|Opcodes.ACC_SYNTHETIC)
                &&implementation.tryCatchBlocks.isEmpty()&&lambda.size()==2&&lambda.get(1).getOpcode()==Opcodes.ARETURN,
                name+" lambda body");
        if(emptyReturn)require(lambda.getFirst() instanceof FieldInsnNode field&&field.getOpcode()==Opcodes.GETSTATIC
                &&field.owner.equals("net/minecraft/item/ItemStack")&&field.name.equals("field_190927_a")&&field.desc.equals(STACK),
                "noReturn original empty stack");
        else require(loadZero(lambda.getFirst()),"reuse original argument");
        require(loadZero(body.get(0))&&body.get(1) instanceof InvokeDynamicInsnNode
                &&body.get(2) instanceof MethodInsnNode&&body.get(3).getOpcode()==Opcodes.ARETURN,"factory route");
        var factory=(InvokeDynamicInsnNode)body.get(1);var transform=(MethodInsnNode)body.get(2);
        var signature=Type.getMethodType(target.getDesc());
        require(factory.name.equals("transform")&&factory.desc.equals("()"+FUNCTION)&&factory.bsm.equals(FACTORY)
                &&Arrays.equals(factory.bsmArgs,new Object[]{signature,target,signature}),"factory bootstrap");
        require(transform.getOpcode()==Opcodes.INVOKEINTERFACE&&transform.itf&&transform.owner.equals(OWNER)
                &&transform.name.equals("transform")&&transform.desc.equals("("+FUNCTION+")"+STACK),"original transform");
        var hook=new InsnList();hook.add(new InsnNode(Opcodes.DUP));
        hook.add(new MethodInsnNode(Opcodes.INVOKESTATIC,CALLBACK,callback,"(Ljava/lang/Object;)V",false));
        creation.instructions.insert(factory,hook);creation.maxStack=Math.max(3,creation.maxStack);
        var writer=new ClassWriter(0);node.accept(writer);return writer.toByteArray();
    }
    private static MethodNode method(ClassNode node,String name,String descriptor) {
        var methods=node.methods.stream().filter(m->m.name.equals(name)&&m.desc.equals(descriptor)).toList();
        require(methods.size()==1,"method "+name);return methods.getFirst();
    }
    private static List<AbstractInsnNode> instructions(MethodNode method) {
        return Arrays.stream(method.instructions.toArray()).filter(i->i.getOpcode()>=0).toList();
    }
    private static boolean loadZero(AbstractInsnNode instruction) {
        return instruction instanceof VarInsnNode load&&load.getOpcode()==Opcodes.ALOAD&&load.var==0;
    }
    private static void require(boolean valid,String detail) {
        if(!valid)throw new IllegalArgumentException("Original native ingredient factory differs: "+detail);
    }
    private static String digest(byte[] bytes) {
        try {return HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(bytes));}
        catch(java.security.NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
    }
    private static String methodDigest(MethodNode method) {
        var writer=new ClassWriter(0);writer.visit(Opcodes.V17,Opcodes.ACC_PUBLIC,"Witness",null,"java/lang/Object",null);
        method.accept(writer);writer.visitEnd();return digest(writer.toByteArray());
    }
    public static void reuseFactoryResult(Object value) {
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost().getName().equals(TARGET),"result identity");
        synchronized(IDENTITIES) {IDENTITIES.put(value,REUSE);factoryResults++;}
    }
    public static void noReturnFactoryResult(Object value) {
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost().getName().equals(TARGET),"noReturn result identity");
        synchronized(IDENTITIES) {IDENTITIES.put(value,NO_RETURN);factoryResults++;}
    }
    public static void withNbtFactoryResult(Object value,Object capturedNbt) {
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost().getName().equals(TARGET),"NBT result identity");
        require(capturedNbt==null||capturedNbt.getClass().getName().equals("net.minecraft.nbt.NBTTagCompound"),"NBT capture identity");
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",TARGET,
                "factoryMethod","withNbt("+NBT+")Lcom/cleanroommc/groovyscript/api/INBTResourceStack;",
                "implementation","lambda$withNbt$6("+NBT+NBT+")Z","capturedArguments",Collections.singletonList(capturedNbt));
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    public static void stackTransformFactoryResult(Object value,Object stack) {
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost().getName().equals(TARGET),"stack transform identity");
        require(stack==null||stack.getClass().getName().equals("net.minecraft.item.ItemStack"),"stack transform capture");
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",TARGET,
                "factoryMethod","transform("+STACK+")"+STACK,"implementation","lambda$transform$3("+STACK+STACK+")"+STACK,
                "capturedArguments",Collections.singletonList(stack));
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    public static void craftingFactoryResult(Object value,String owner,String factory,String implementation) {
        require((owner.equals(CARGO_TARGET)||owner.equals(CONDITION_TARGET))&&value!=null&&value.getClass().isHidden()
                &&value.getClass().getNestHost().getName().equals(owner),"crafting factory identity");
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",owner,
                "factoryMethod",factory,"implementation",implementation,"capturedArguments",List.of());
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    public static void bombletFactoryResult(Object value,Object capturedStack) {
        require(value instanceof java.util.function.Supplier<?> &&value.getClass().isHidden()
                &&value.getClass().getNestHost().getName().equals(BOMBLET_TARGET),"bomblet result identity");
        require(capturedStack!=null&&capturedStack.getClass().getName().equals("net.minecraft.item.ItemStack"),"bomblet capture identity");
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",BOMBLET_TARGET,
                "factoryMethod","initCapabilities"+CAPABILITIES,
                "implementation","net.minecraft.item.ItemStack#func_77946_l()"+STACK,
                "capturedArguments",List.of(capturedStack));
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    public static void storageFactoryResult(Object value,Object provider,Object stack,Object capturedStack) {
        require(provider!=null&&STORAGE_TARGETS.contains(provider.getClass().getName()),"storage captured provider");
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost()==provider.getClass(),
                "storage callback identity");
        require(stack==capturedStack&&(stack==null||stack.getClass().getName().equals("net.minecraft.item.ItemStack")),
                "storage captured stack");
        String owner=provider.getClass().getName();
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",owner,
                "factoryMethod","setParentStack("+STACK+")L"+owner.replace('.','/')+";",
                "implementation","lambda$setParentStack$0("+STACK+STACK+")V",
                "capturedArguments",Arrays.asList(provider,stack,capturedStack));
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    public static void backpackFactoryResult(Object value,String implementation,Object capturedNbt) {
        require(value!=null&&value.getClass().isHidden()&&value.getClass().getNestHost().getName().equals(BACKPACK_TARGET)
                &&Arrays.stream(value.getClass().getInterfaces()).anyMatch(t->t.getName().equals("kotlin.jvm.functions.Function0")),
                "backpack factory result identity");
        require(Set.of("_init_$lambda$0","_init_$lambda$1","deserializeNBT$lambda$0","deserializeNBT$lambda$1")
                .contains(implementation),"backpack factory implementation");
        boolean nbt=implementation.startsWith("deserializeNBT");
        require(nbt?capturedNbt!=null&&capturedNbt.getClass().getName().equals("net.minecraft.nbt.NBTTagCompound")
                :capturedNbt==null,"backpack factory capture");
        var identity=Map.<String,Object>of("kind","native-lambda-factory-result","owner",BACKPACK_TARGET,
                "factoryMethod",nbt?"deserializeNBT("+NBT+")V":"<init>(Lkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function0;Ljava/util/UUID;ILkotlin/jvm/internal/DefaultConstructorMarker;)V",
                "implementation",implementation+(nbt?"("+NBT+")I":"()I"),
                "capturedArguments",nbt?List.of(capturedNbt):List.of());
        synchronized(IDENTITIES) {IDENTITIES.put(value,identity);factoryResults++;}
    }
    /** Metadata for original compiler output after its existing admission pass. */
    static void sourceCompilation(ClassNode node,byte[] input,byte[] output) {
        if(!node.superName.equals("groovy/lang/Closure")||!node.interfaces.contains("org/codehaus/groovy/runtime/GeneratedClosure"))return;
        var lines=new TreeSet<Integer>();
        for(var method:node.methods)if(method.name.equals("doCall"))for(var instruction:method.instructions)
            if(instruction instanceof LineNumberNode line)lines.add(line.line);
        synchronized(SOURCE_COMPILATIONS) {
            SOURCE_COMPILATIONS.put(node.name.replace('/','.'),Map.of("sourceFile",node.sourceFile==null?"unknown":node.sourceFile,
                    "bodyLines",List.copyOf(lines),"originalClassSha256",digest(input),"guardedClassSha256",digest(output)));
        }
    }
    /** Inspect original SAM storage without invoking a proxy, closure, getter or
     * captured value. Owner state is an identity boundary, not a serialized script. */
    public static Map<String,Object> storedSourceIdentity(Object value) throws ReflectiveOperationException {
        if(value==null)return null;
        Class<?> cls=value.getClass();
        if(java.lang.reflect.Proxy.isProxyClass(cls)&&cls.getInterfaces().length==1
                &&cls.getInterfaces()[0].getName().equals("com.cleanroommc.groovyscript.compat.vanilla.ItemStackTransformer")) {
            Object handler=java.lang.reflect.Proxy.getInvocationHandler(value);
            if(handler.getClass()!=org.codehaus.groovy.runtime.ConvertedClosure.class)return null;
            Object closure=stored(org.codehaus.groovy.runtime.ConversionHandler.class,handler,"delegate");
            if(!MaterialCallGate.sourceClosure(closure))return null;
            var row=new TreeMap<String,Object>();row.put("kind","original-groovy-sam-proxy");
            row.put("interface",cls.getInterfaces()[0].getName());row.put("handler",handler.getClass().getName());
            row.put("methodName",stored(org.codehaus.groovy.runtime.ConvertedClosure.class,handler,"methodName"));
            row.put("capturedArguments",List.of(closure));return row;
        }
        if(!MaterialCallGate.sourceClosure(value))return null;
        var row=new TreeMap<String,Object>();row.put("kind","guarded-source-closure");row.put("class",cls.getName());
        synchronized(SOURCE_COMPILATIONS) {
            var compiled=SOURCE_COMPILATIONS.get(cls.getName());
            if(compiled==null)throw new IllegalStateException("Stored source closure has no compilation identity");
            row.put("compilation",compiled);
        }
        Object owner=stored(groovy.lang.Closure.class,value,"owner");
        Object delegate=stored(groovy.lang.Closure.class,value,"delegate");
        Object thisObject=stored(groovy.lang.Closure.class,value,"thisObject");
        row.put("ownerType",owner==null?null:owner instanceof Class<?> type?type.getName():owner.getClass().getName());
        row.put("thisObjectType",thisObject==null?null:thisObject instanceof Class<?> type?type.getName():thisObject.getClass().getName());
        row.put("delegateIsOwner",delegate==owner);
        row.put("delegateType",delegate==null?null:delegate instanceof Class<?> type?type.getName():delegate.getClass().getName());
        row.put("resolveStrategy",stored(groovy.lang.Closure.class,value,"resolveStrategy"));
        row.put("ownerStateObserved",false);
        var fields=new ArrayList<String>();var captures=new ArrayList<Object>();
        for(var field:Arrays.stream(cls.getDeclaredFields()).sorted(Comparator.comparing(java.lang.reflect.Field::getName)).toList()) {
            if(java.lang.reflect.Modifier.isStatic(field.getModifiers()))continue;
            field.setAccessible(true);Object capture=field.get(value);fields.add(field.getName());
            if(capture!=null&&capture.getClass()==groovy.lang.Reference.class)
                capture=stored(groovy.lang.Reference.class,capture,"value");
            captures.add(capture);
        }
        row.put("capturedFields",fields);row.put("capturedArguments",captures);return row;
    }
    private static Object stored(Class<?> owner,Object receiver,String name) throws ReflectiveOperationException {
        var field=owner.getDeclaredField(name);field.setAccessible(true);return field.get(receiver);
    }
    public static Map<String,Object> identity(Object value) {
        synchronized(IDENTITIES) {return IDENTITIES.get(value);}
    }
    public static Map<String,Object> observations() {
        synchronized(IDENTITIES) {return Map.of("factoryResults",factoryResults,"distinctFunctions",IDENTITIES.size(),
                "inputClassSha256",List.copyOf(transformedInputs),"functionInvocationsByObserver",0);}
    }
}
