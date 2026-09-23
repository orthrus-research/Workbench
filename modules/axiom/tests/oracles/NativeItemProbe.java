package research.orthrus.axiom.nativeconstruction;

import java.nio.file.*;
import java.util.*;
import net.minecraft.block.Block;
import net.minecraft.item.*;
import net.minecraft.item.crafting.*;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.util.*;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.common.capabilities.*;
import net.minecraftforge.event.AttachCapabilitiesEvent;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.*;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.oredict.*;

/** Real native values in an explicit test universe, never a fabricated pack registry. */
public final class NativeItemProbe {
    static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    static Item item(String name) {
        Item value=ForgeRegistries.ITEMS.getValue(new ResourceLocation(name));
        check(value!=null,"actual registered item: "+name); return value;
    }
    static ItemStack stack(String name, int count, int meta) { return new ItemStack(item(name),count,meta); }
    static Object capture(ItemStack stack) {
        return List.of(stack.func_77973_b().getRegistryName().toString(),stack.func_190916_E(),stack.func_77960_j(),stack.serializeNBT().toString());
    }
    static List<String> names(Collection<ItemStack> stacks) {
        return stacks.stream().map(s->s.func_77973_b().getRegistryName().toString()).toList();
    }
    public static final class Provider implements ICapabilitySerializable<NBTTagCompound> {
        int value=7;
        public boolean hasCapability(Capability<?> cap, EnumFacing side) { return false; }
        public <T> T getCapability(Capability<T> cap, EnumFacing side) { return null; }
        public NBTTagCompound serializeNBT() { var n=new NBTTagCompound(); n.func_74768_a("value",value); return n; }
        public void deserializeNBT(NBTTagCompound n) { value=n.func_74762_e("value"); }
    }
    public static final class CapabilityItem extends Item {
        final Map<ItemStack,Provider> parents=new IdentityHashMap<>();
        @Override public ICapabilityProvider initCapabilities(ItemStack stack,NBTTagCompound nbt) {
            var p=new Provider(); parents.put(stack,p); return p;
        }
    }
    public static final class DistinctMetadataItem extends Item {
        @Override public int getDamage(ItemStack stack) { return 17; }
        @Override public int getMetadata(ItemStack stack) { return 3; }
    }
    public static final class CapObserver {
        final Map<ItemStack,Provider> providers=new IdentityHashMap<>();
        int wrongGeneric;
        @SubscribeEvent public void stack(AttachCapabilitiesEvent<ItemStack> event) {
            if (!(event.getObject().func_77973_b() instanceof CapabilityItem)) return;
            var p=new Provider(); providers.put(event.getObject(),p);
            var id=new ResourceLocation("fixture:state"); event.addCapability(id,p);
            try { event.addCapability(id,p); throw new AssertionError("duplicate capability key admitted"); }
            catch (IllegalStateException expected) { }
        }
        @SubscribeEvent public void item(AttachCapabilitiesEvent<Item> event) { wrongGeneric++; }
    }
    public static final class OreObserver {
        final List<String> events=new ArrayList<>();
        @SubscribeEvent(priority=EventPriority.LOWEST) public void ore(OreDictionary.OreRegisterEvent event) {
            events.add(event.getName());
        }
    }
    public static final class FailedOre {
        @SubscribeEvent(priority=EventPriority.HIGHEST) public void ore(OreDictionary.OreRegisterEvent event) {
            if (event.getName().equals("dustFailureFixture")) throw new IllegalStateException("ore-listener-failed");
        }
    }
    static Item register(String name, Item value) {
        value.setRegistryName(new ResourceLocation(name)); ForgeRegistries.ITEMS.register(value); return value;
    }
    static Map<String,Object> identities(CapObserver caps) {
        var entries=new TreeMap<String,Object>(); int blockItems=0;
        for (Item item:ForgeRegistries.ITEMS) {
            var name=item.getRegistryName();
            check(name.equals(item.delegate.name()) && item.delegate.get()==item,"native item delegate");
            int id=Item.func_150891_b(item); check(Item.func_150899_d(id)==item,"native item ID roundtrip");
            String block="";
            if (item instanceof ItemBlock ib) {
                blockItems++; Block b=ib.func_179223_d(); block=b.getRegistryName().toString();
                check(Item.func_150898_a(b)==item,"native block-to-item registration callback");
            }
            entries.put(name.toString(),List.of(id,item.getClass().getName(),item.func_77614_k(),block));
        }
        check(entries.keySet().stream().allMatch(n->n.startsWith("minecraft:")),"baseline contains vanilla items only");
        int vectors=0;
        for (String name:List.of("minecraft:stone","minecraft:planks","minecraft:dye","minecraft:iron_ingot","minecraft:diamond_pickaxe"))
            for (int meta:new int[]{0,1,4,15,16,32767}) for (int count:new int[]{1,2,64}) {
                ItemStack a=stack(name,count,meta), b=a.func_77946_l();
                check(a!=b && a.func_77973_b()==b.func_77973_b() && a.func_77960_j()==b.func_77960_j() && b.func_190916_E()==count,"native item copy");
                var tag=new NBTTagCompound(); tag.func_74778_a("fixture","before"); a.func_77982_d(tag);
                b=a.func_77946_l(); b.func_77978_p().func_74778_a("fixture","after");
                check(a.func_77978_p().func_74779_i("fixture").equals("before"),"native deep tag copy");
                ItemStack decoded=new ItemStack(a.serializeNBT());
                check(capture(a).equals(capture(decoded)),"native NBT roundtrip"); vectors++;
            }
        check(stack("minecraft:iron_ingot",0,0).func_190926_b(),"native zero-count emptiness");
        var capabilityItem=(CapabilityItem)register("fixture:capability",new CapabilityItem());
        ItemStack a=new ItemStack(capabilityItem,3,5);
        check(capabilityItem.parents.containsKey(a) && caps.providers.containsKey(a),"parent and native attachment event reached");
        capabilityItem.parents.get(a).value=19; caps.providers.get(a).value=23;
        ItemStack b=a.func_77946_l(), decoded=new ItemStack(a.serializeNBT());
        for (var s:List.of(b,decoded)) {
            check(capabilityItem.parents.get(s).value==19 && caps.providers.get(s).value==23,"native parent and event capability roundtrip");
            check(s.areCapsCompatible(a),"native serializable capability equality");
        }
        caps.providers.get(b).value=29;
        check(caps.providers.get(a).value==23 && !b.areCapsCompatible(a),"fresh providers and capability divergence");
        check(caps.wrongGeneric==0,"native generic capability event filtering");
        return Map.of("items",entries,"count",entries.size(),"blockItems",blockItems,"stackVectors",vectors,
                "capabilityRoundtrips",2,"capabilityParentAndEvent",true);
    }
    static List<IRecipe> recipeFixtures(boolean enabled) {
        check(ForgeRegistries.RECIPES.getKeys().isEmpty(),"item prefix has no registered crafting recipes; never clear registry");
        var recipes=new ArrayList<IRecipe>();
        if (!enabled) return recipes;
        var choices=List.of(new ItemStack[]{stack("minecraft:iron_ingot",1,0)},
                new ItemStack[]{stack("minecraft:stick",1,0)},
                new ItemStack[]{stack("minecraft:iron_ingot",1,0)},
                new ItemStack[]{stack("minecraft:iron_ingot",1,0),stack("minecraft:gold_ingot",1,0)},
                new ItemStack[]{stack("minecraft:iron_ingot",1,0),stack("minecraft:apple",1,0)},
                new ItemStack[]{stack("minecraft:apple",1,0),stack("minecraft:iron_ingot",1,0)});
        for (int i=0;i<choices.size();i++) {
            NonNullList<Ingredient> ingredients=NonNullList.func_191196_a();
            ingredients.add(Ingredient.func_193369_a(choices.get(i)));
            var output=stack(i==2 ? "minecraft:cookie" : "minecraft:apple",1,0);
            IRecipe recipe=i==1 ? new ShapelessRecipes("fixture",output,ingredients) : new ShapedRecipes("fixture",1,1,ingredients,output);
            recipe.setRegistryName(new ResourceLocation("fixture:rewrite_"+i)); ForgeRegistries.RECIPES.register(recipe); recipes.add(recipe);
        }
        return recipes;
    }
    static Map<String,Object> oreBaseline(List<IRecipe> recipes, OreObserver observer) {
        String[] oreNames=OreDictionary.getOreNames(); // original complete initialization and recipe rewrite
        var ores=new TreeMap<String,Object>(); int stacks=0;
        for (String name:oreNames) {
            ores.put(name,OreDictionary.getOres(name).stream().map(NativeItemProbe::capture).toList());
            stacks+=OreDictionary.getOres(name).size();
        }
        check(observer.events.size()==stacks,"each initial native ore registration delivers an event");
        for (int i=0;i<recipes.size();i++) {
            Ingredient ing=recipes.get(i).func_192400_c().get(0);
            check((ing instanceof OreIngredient)==(i<2 || i==5),"native recipe rewrite, exclusions and order-sensitive mixed alternatives: "+i);
        }
        check(ForgeRegistries.RECIPES.getKeys().size()==recipes.size(),"rewrite retains same actual recipe registry");
        return Map.of("names",ores,"nameCount",oreNames.length,"stackCount",stacks,"events",List.copyOf(observer.events),
                "recipeUniverse",recipes.isEmpty()?"empty-at-item-prefix":"six-explicit-native-fixtures",
                "recipes",recipes.size(),"rewrittenIngredients",recipes.isEmpty()?0:3);
    }
    static Map<String,Object> unification(OreObserver observer) throws Exception {
        int before=observer.events.size(); OreDictUnifier.init();
        check(observer.events.size()==before,"GT initial scan calls handler directly without reposting ore events");
        var iron=SourceMaterialCatalog.Iron; var aluminium=SourceMaterialCatalog.Aluminium;
        check(OreDictUnifier.get(OrePrefix.ingot,iron).func_77973_b()==item("minecraft:iron_ingot"),"vanilla iron joins actual GT material");
        check(OreDictUnifier.get(OrePrefix.dust,aluminium).func_190926_b(),"missing generated GT form is not fabricated");
        check(OreDictUnifier.get(OrePrefix.dye,MarkerMaterials.Color.Blue).func_77960_j()==4,"native dye metadata joins marker material");
        check(OreDictUnifier.getUnificationEntry(stack("minecraft:dye",1,4)).material==SourceMaterialCatalog.Lapis,"marker prefix does not overwrite real material reverse mapping");
        check(OreDictUnifier.getMaterial(stack("minecraft:stone",1,0)).material==SourceMaterialCatalog.Stone,"self-referencing prefix uses its actual default material");
        var pending=OrePrefix.class.getDeclaredField("generatedMaterials"); pending.setAccessible(true);
        check(((Set<?>)pending.get(OrePrefix.ingot)).contains(iron),"initial scan queues actual prefix material processing");
        check(OreDictUnifier.hasOreDictionary(stack("minecraft:dye",1,4),"dye") && OreDictUnifier.hasOreDictionary(stack("minecraft:dye",1,4),"dyeBlue"),"wildcard and exact metadata names union");
        check(!OreDictUnifier.hasOreDictionary(stack("minecraft:dye",1,3),"dyeBlue"),"metadata remains distinct");
        check(OreDictUnifier.getOreDictionaryEntryOrEmpty(item("minecraft:apple"))!=null,"native empty view");
        int namesBefore=OreDictionary.getOreNames().length;
        check(OreDictionary.getOres("fixture_noncreating",false).isEmpty() && !OreDictionary.doesOreNameExist("fixture_noncreating"),"noncreating native lookup");
        OreDictionary.registerOre("Unknown",stack("minecraft:apple",1,0));
        OreDictionary.registerOre("fixture_empty",ItemStack.field_190927_a);
        check(OreDictionary.getOreNames().length==namesBefore,"ignored invalid registrations do not create names");
        Item single=register("fixture:single",new Item());
        Item multi=register("fixture:multi",new Item().func_77627_a(true));
        ItemStack wild=new ItemStack(multi,1,ItemConstants.W), exact=new ItemStack(multi,2,4);
        var tag=new NBTTagCompound(); tag.func_74778_a("value","original"); exact.func_77982_d(tag);
        OreDictionary.registerOre("fixtureWildcard",wild); OreDictionary.registerOre("dustAluminium",exact);
        int observed=observer.events.size();
        exact.func_77978_p().func_74778_a("value","changed"); exact.func_190920_e(7);
        OreDictionary.registerOre("dustAluminium",exact);
        check(observer.events.size()==observed && OreDictionary.getOres("dustAluminium").size()==1,"native duplicate ignores count and NBT");
        check(OreDictionary.getOres("dustAluminium").get(0).func_190916_E()==2 && OreDictionary.getOres("dustAluminium").get(0).func_77978_p().func_74779_i("value").equals("original"),"ore dictionary stored native deep copy");
        check(OreDictUnifier.getUnificationEntry(exact).material==aluminium,"late native event reaches actual GT listener");
        check(OreDictUnifier.get(OrePrefix.dust,aluminium,3).func_190916_E()==3,"registered form supplies requested count");
        check(((Set<?>)pending.get(OrePrefix.dust)).contains(aluminium),"later ore event queues material; handlers are not executed");
        ItemStack unified=OreDictUnifier.getUnificated(exact);
        check(unified.func_77973_b()==multi && unified.func_190916_E()==7 && unified.func_77978_p()==null,"original unification preserves count but constructs a fresh item/meta stack, not arbitrary NBT");
        check(OreDictUnifier.getDust(aluminium,2*MaterialVoltages.M).func_190916_E()==2,"material amount chooses registered dust form");
        check(OreDictUnifier.getDust(aluminium,MaterialVoltages.M/4).func_190926_b(),"missing small dust form remains absent");
        check(OreDictUnifier.getIngot(iron,9*MaterialVoltages.M).func_77973_b()==item("minecraft:iron_block"),"material amount selects actual native block form");
        Set<String> union=OreDictUnifier.getOreDictionaryNames(exact);
        check(union.equals(Set.of("fixtureWildcard","dustAluminium")),"multi variant wildcard union");
        try { union.add("invalid"); throw new AssertionError("GT name view mutable"); } catch (UnsupportedOperationException expected) { }
        OreDictionary.registerOre("fixtureSingle",new ItemStack(single,1,3));
        check(OreDictUnifier.hasOreDictionary(new ItemStack(single,1,9),"fixtureSingle"),"single-variant cache intentionally ignores queried metadata");
        var returned=OreDictUnifier.getAllWithOreDictionaryName("dustAluminium").get(0); returned.func_190920_e(55);
        check(OreDictUnifier.get("dustAluminium").func_190916_E()==2,"GT name lookup defensive copy");
        var info=new ItemMaterialInfo(new MaterialStack(iron,MaterialVoltages.M));
        OreDictUnifier.registerOre(new ItemStack(single,1,ItemConstants.W),info);
        check(OreDictUnifier.getMaterial(new ItemStack(single,1,12)).material==iron,"material info wildcard fallback");
        Item distinct=register("fixture:distinct_metadata",new DistinctMetadataItem().func_77627_a(true));
        var distinctStack=new ItemStack(distinct,1,5);
        check(distinctStack.func_77952_i()==17 && distinctStack.func_77960_j()==3,"native damage and metadata are different dispatch methods");
        check(new ItemAndMetadata(distinctStack).itemDamage==17,"GT item key reads damage, not metadata");
        OreDictionary.registerOre("fixtureDistinct",distinctStack);
        check(OreDictUnifier.getOreDictionaryEntry(distinct).get((short)17).contains("fixtureDistinct"),"GT variant cache uses actual damage accessor");
        check(OreDictUnifier.getOreDictionaryEntry(distinct).get((short)3)==null,"metadata does not alias damage key");
        int matches=0;
        for (int target:new int[]{0,1,4,15,ItemConstants.W}) for (int input:new int[]{0,1,4,15,ItemConstants.W}) for (boolean strict:new boolean[]{false,true}) {
            boolean actual=OreDictionary.itemMatches(new ItemStack(multi,1,target),new ItemStack(multi,1,input),strict);
            check(actual==(target==input || target==ItemConstants.W && !strict),"native directional wildcard match"); matches++;
        }
        var comparator=OreDictUnifier.getSimpleItemStackComparator();
        for (String name:List.of("aaa:first","zzz:first","gregtech:first","gregtech:second")) {
            Item candidate=register(name,new Item()); OreDictionary.registerOre("ingotIron",candidate);
        }
        var selected=names(OreDictUnifier.getAll(new UnificationEntry(OrePrefix.ingot,iron)));
        SourceCatalogConfiguration.compat.modPriorities=new String[]{"zzz","aaa"};
        check(comparator==OreDictUnifier.getSimpleItemStackComparator(),"original comparator instance remains cached");
        check(selected.equals(names(OreDictUnifier.getAll(new UnificationEntry(OrePrefix.ingot,iron)))),"config mutation does not rebuild existing ordering");
        var failed=new FailedOre(); MinecraftForge.EVENT_BUS.register(failed);
        int beforeFailure=observer.events.size();
        try { OreDictionary.registerOre("dustFailureFixture",multi); throw new AssertionError("listener failure swallowed"); }
        catch (IllegalStateException expected) { check(expected.getMessage().equals("ore-listener-failed"),"original event failure surfaced"); }
        finally { MinecraftForge.EVENT_BUS.unregister(failed); }
        check(OreDictionary.getOres("dustFailureFixture").size()==1 && observer.events.size()==beforeFailure,"map/list survive failed listener; lower listener not reached");
        check(OreDictUnifier.get("dustFailureFixture").func_190926_b(),"failed event does not fabricate GT cache update");
        OreDictionary.registerOre("dustFailureFixture",multi);
        check(observer.events.size()==beforeFailure && OreDictUnifier.get("dustFailureFixture").func_190926_b(),"retry deduplicates before event and does not repair partial state");
        return Map.of("selectedIron",selected,"eventsAfterScan",observer.events.size()-before,"wildcardVectors",matches,
                "missingFormBeforeRegistration",true,"cachedComparator",true,"failurePartialState",true,"duplicateRegistration",true);
    }
    public static Object run(String request) throws Exception {
        var properties=new Properties(); try(var reader=Files.newBufferedReader(Path.of(request))) { properties.load(reader); }
        var home=net.minecraftforge.fml.relauncher.FMLInjectionData.class.getDeclaredField("minecraftHome"); home.setAccessible(true);
        check(home.get(null)==null,"fresh explicit native home"); home.set(null,Path.of(request).getParent().toFile());
        var cfg=CatalogConfiguration.load(Path.of(properties.getProperty("config")),SourceCatalogConfiguration.class);
        var loader=Loader.instance(); var controller=Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        Object previous=controller.get(loader); check(previous==null,"no mod discovery"); controller.set(loader,new LoadController(loader));
        var metadata=new ModMetadata(); metadata.modId="gregtech"; metadata.name="gregtech"; loader.setActiveModContainer(new DummyModContainer(metadata));
        var caps=new CapObserver(); var ores=new OreObserver();
        MinecraftForge.EVENT_BUS.register(caps); MinecraftForge.EVENT_BUS.register(ores);
        try(var env=new FluidEnvironment()) {
            var identities=identities(caps);
            var recipes=recipeFixtures(Boolean.parseBoolean(properties.getProperty("recipes","false")));
            var baseline=oreBaseline(recipes,ores);
            env.bindCatalog(new CatalogInputs(SourceMaterialCatalog.class,cfg));
            new MaterialLifecycle(env.runtime(),new MaterialEvents(),new MaterialLifecycle.Dependencies() {
                public void initializeMarkers() { MarkerMaterials.register(); }
                public void registerMaterials() { SourceMaterialCatalog.register(); }
                public FluidMaterial aluminium() { return SourceMaterialCatalog.Aluminium; }
            }).execute();
            check(env.runtime().materials().getPhase()==MaterialPhase.FROZEN,"complete material lifecycle before unification");
            var unifier=unification(ores);
            if (!recipes.isEmpty()) {
                var ingredient=recipes.get(0).func_192400_c().get(0);
                check(ingredient.apply(new ItemStack(item("gregtech:first"))),"rewritten native ingredient observes later ore membership");
            }
            return Map.of("identities",identities,"oreBaseline",baseline,"unifier",unifier,"configuration",cfg.evidence(),
                    "materialCount",env.runtime().materials().getRegisteredMaterials().size(),"materialPhase","FROZEN");
        } finally {
            MinecraftForge.EVENT_BUS.unregister(OreDictUnifier.class); MinecraftForge.EVENT_BUS.unregister(ores);
            MinecraftForge.EVENT_BUS.unregister(caps); controller.set(loader,previous);
        }
    }
}
