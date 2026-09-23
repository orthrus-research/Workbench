package research.orthrus.axiom.materialhost;

import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import java.lang.reflect.Array;
import java.lang.reflect.Modifier;
import java.util.*;

/** Typed values from existing native recipe storage. Unknown values are gaps;
 * reporting never calls matching, capability serialization or chance routines. */
final class NativeRecipeValues {
    private static final String STACK = "net.minecraft.item.ItemStack";
    private static final String FLUID_STACK = "net.minecraftforge.fluids.FluidStack";
    private static final String NBT = "net.minecraft.nbt.";
    private static final String INPUT = "gregtech.api.recipes.ingredients.GTRecipeInput";
    private static final Set<String> STORED_INGREDIENT_TYPES = Set.of(
            "net.minecraft.item.crafting.Ingredient", "net.minecraft.item.crafting.Ingredient$1",
            "net.minecraftforge.oredict.OreIngredient", "net.minecraftforge.common.crafting.IngredientNBT",
            "net.minecraftforge.common.crafting.CompoundIngredient", "gregtech.common.crafting.GTFluidCraftingIngredient",
            "gregtech.common.crafting.FacadeRecipe$FacadeIngredient", "techguns.recipes.IngredientHasNBTTag",
            "vazkii.arl.recipe.BlacklistOreIngredient", "codechicken.microblock.MicroIngredient");
    private static final String BACKPACK = "com.cleanroommc.retrosophisticatedbackpacks.";
    private static final String UPGRADE = BACKPACK+"capability.upgrade.";
    private static final Set<String> BACKPACK_UPGRADES = Set.of("Crafting", "Deposit", "Feeding", "Filter", "Pickup", "Restock",
            "AdvancedDeposit", "AdvancedFeeding", "AdvancedFilter", "AdvancedPickup", "AdvancedRestock");
    private static final Set<String> BACKPACK_ENUMS = Set.of(BACKPACK+"backpack.SortType",
            UPGRADE+"IBasicFilterable$FilterType", UPGRADE+"IAdvancedFilterable$MatchType", UPGRADE+"IFilterUpgrade$FilterWayType",
            UPGRADE+"CraftingUpgradeWrapper$CraftingDestination", UPGRADE+"AdvancedFeedingUpgradeWrapper$FeedingStrategy$Hunger",
            UPGRADE+"AdvancedFeedingUpgradeWrapper$FeedingStrategy$Health");
    final Set<String> gaps = new TreeSet<>();
    final Map<String,Integer> oreIds = new TreeMap<>();
    private final Set<Object> active = Collections.newSetFromMap(new IdentityHashMap<>());
    private final Catalog catalog;

    NativeRecipeValues() {this(null);}
    NativeRecipeValues(Catalog catalog) {this.catalog=catalog;}

    /** Shared native objects remain shared in the report. In particular one
     * ore dictionary list can be referenced by thousands of ingredients. */
    static final class Catalog {
        final Map<String,Object> values=new TreeMap<>();
        final Set<String> gaps=new TreeSet<>();
        private final Map<Object,String> identities=new IdentityHashMap<>();
        private final Map<String,Set<String>> valueGaps=new HashMap<>();
        Object observe(Object value,NativeRecipeValues encoder) throws ReflectiveOperationException {
            String id=identities.get(value);
            if(id==null) {
                id=Integer.toString(identities.size());identities.put(value,id);
                var child=new NativeRecipeValues(this);
                Object encoded;
                try {encoded=child.encodeInline(value);}
                catch(ReflectiveOperationException|RuntimeException|LinkageError failure) {
                    var unavailable=new TreeMap<String,Object>();
                    unavailable.put("value",child.unknown(value,"native-value-read-failed"));
                    unavailable.put("failure",NativeProgramObservations.trace(failure));encoded=unavailable;
                }
                values.put(id,encoded);valueGaps.put(id,Set.copyOf(child.gaps));gaps.addAll(child.gaps);
            }
            encoder.gaps.addAll(valueGaps.getOrDefault(id,Set.of()));
            return Map.of("nativeValueRef",id);
        }
    }

    Object unknown(Object value, String reason) {
        String name = value == null ? "null" : value.getClass().getName();
        gaps.add(reason + ": " + name);
        return Map.of("type",name,"observation","incomplete","reason",reason);
    }
    Map<String,Object> fields(String owner, Object receiver, String... names) throws ReflectiveOperationException {
        var row = new TreeMap<String,Object>();
        for (String name : names) row.put(name,encode(field(owner,receiver,name)));
        return row;
    }
    Object encode(Object value) throws ReflectiveOperationException {
        if(catalog!=null&&value!=null&&!(value instanceof String)&&!(value instanceof Number)
                &&!(value instanceof Boolean)&&!(value instanceof Character))return catalog.observe(value,this);
        return encodeInline(value);
    }
    private Object encodeInline(Object value) throws ReflectiveOperationException {
        if (value == null || value instanceof String || value instanceof Boolean) return value;
        Class<?> cls = value.getClass();String name = cls.getName();
        if (Set.of(Byte.class,Short.class,Integer.class,Long.class,java.math.BigInteger.class,java.math.BigDecimal.class).contains(cls))
            return Map.of("type",name,"value",value.toString());
        if (cls == Float.class) return Map.of("type",name,"bits",HexFormat.of().toHexDigits(Float.floatToRawIntBits((Float)value)));
        if (cls == Double.class) return Map.of("type",name,"bits",HexFormat.of().toHexDigits(Double.doubleToRawLongBits((Double)value)));
        if (cls == Character.class) return Map.of("type",name,"value",(int)(Character)value);
        if (cls == UUID.class) {
            var uuid=(UUID)value;
            return Map.of("type",name,"mostSignificantBits",Long.toString(uuid.getMostSignificantBits()),
                    "leastSignificantBits",Long.toString(uuid.getLeastSignificantBits()));
        }
        if (!active.add(value)) return unknown(value,"cyclic-native-value");
        try {
            if (name.equals(STACK)) return stack(value);
            if (STORED_INGREDIENT_TYPES.contains(name)) return storedFields(value,Object.class);
            if (name.equals("net.minecraft.item.crafting.ShapedRecipes")
                    || name.equals("net.minecraft.item.crafting.ShapelessRecipes"))
                return storedFields(value,type("net.minecraftforge.registries.IForgeRegistryEntry$Impl"));
            if (type("net.minecraft.item.Item").isInstance(value)) return registeredItem(value);
            if (type("net.minecraft.block.Block").isInstance(value)) return registeredBlock(value);
            if (value instanceof Optional<?> optional) {
                var row=new TreeMap<String,Object>();row.put("type",name);row.put("present",optional.isPresent());
                if(optional.isPresent())row.put("value",encode(optional.get()));return row;
            }
            if (Set.of("appeng.core.features.ItemDefinition", "appeng.core.features.DamagedItemDefinition",
                    "appeng.core.features.TileDefinition", "appeng.core.features.ItemStackSrc",
                    "appeng.core.features.MaterialStackSrc", "co.neeve.nae2.common.registration.definitions.Materials$MaterialStackSrc")
                    .contains(name)) return storedFields(value,Object.class);
            if(value instanceof Enum<?> constant&&Set.of("appeng.items.materials.MaterialType",
                    "co.neeve.nae2.common.registration.definitions.Materials$MaterialType").contains(name)) {
                // These are the fields used by the stored stack-source routes.
                // CLIENT models, drop entities and feature declaration metadata
                // do not define the stack returned by this recipe definition.
                var row=fields(name,value,"itemInstance","damageValue","isRegistered");
                row.put("type",name);row.put("value",constant.name());return row;
            }
            if (Set.of("scala.collection.immutable.Map$Map3", "scala.collection.mutable.WrappedArray$ofRef").contains(name))
                return storedFields(value,Object.class);
            if(name.equals("com.cleanroommc.groovyscript.helper.ingredient.OrIngredient")) {
                var row=fields(name,value,"ingredients","amount");row.put("type",name);
                row.putAll(fields("com.cleanroommc.groovyscript.helper.ingredient.IngredientBase",value,
                        "matchCondition","transformer","mark"));return row;
            }
            if(name.equals("com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient")) {
                // Stored amount is distinct from getAmount(), which consults
                // current ore membership. Do not resolve/copy matching stacks.
                var row=fields(name,value,"oreDict","amount");row.put("type",name);
                row.putAll(fields("com.cleanroommc.groovyscript.helper.ingredient.IngredientBase",value,
                        "matchCondition","transformer","mark"));return row;
            }
            if(name.startsWith("com.cleanroommc.groovyscript.api.IIngredient$"))
                return symbol(value,"com.cleanroommc.groovyscript.api.IIngredient","EMPTY","ANY");
            var function=NativeRecipeFunctionObservations.identity(value);
            if(function!=null)return function(function,null);
            var cache=NativeRecipeFunctionObservations.craftingCache(value);
            if(cache!=null) {
                var row=new TreeMap<String,Object>(cache);row.put("value",encode(cache.get("value")));return row;
            }
            var sourceFunction=NativeRecipeFunctionObservations.storedSourceIdentity(value);
            if(sourceFunction!=null)return function(sourceFunction,null);
            if (name.equals(FLUID_STACK)) return fluid(value);
            if (name.equals("net.minecraft.block.state.BlockStateContainer$StateImplementation")) return blockState(value);
            if (name.startsWith(NBT)) return nbt(value);
            if (name.equals("net.minecraft.util.ResourceLocation")) return Map.of("type",name,"value",value.toString());
            if (value instanceof Enum<?> constant && (Set.of("gregtech.api.recipes.ingredients.nbtmatch.NBTTagType",
                    "gregtech.api.capability.impl.CommonFluidFilters", "baubles.api.BaubleType",
                    "icbm.classic.content.missile.entity.itemstack.HeldActionMode").contains(constant.getDeclaringClass().getName())
                    ||BACKPACK_ENUMS.contains(constant.getDeclaringClass().getName())))
                return Map.of("type",constant.getDeclaringClass().getName(),"value",constant.name());
            if (name.equals("gregtech.api.capability.impl.SingleFluidFilter")) {
                var row=fields(name,value,"fluid","blacklist");row.put("type",name);return row;
            }
            if(name.equals("gregtech.common.items.armor.PowerlessJetpack$Behaviour$1"))
                return symbol(value,"gregtech.common.items.armor.PowerlessJetpack$Behaviour","JETPACK_FUEL_FILTER");
            if(name.equals("supersymmetry.common.item.behavior.HydrogenPoweredDroneBehavior$1"))
                return symbol(value,"supersymmetry.common.item.behavior.HydrogenPoweredDroneBehavior","HYDROGEN_FILTER");
            if(name.equals("supersymmetry.common.item.armor.JetWingpack$JetWingpackBehaviour$1"))
                return symbol(value,"supersymmetry.common.item.armor.JetWingpack$JetWingpackBehaviour","JET_WINGPACK_FUEL_FILTER");
            if (name.equals("gregtech.api.capability.impl.PropertyFluidFilter")
                    || name.equals("gregtech.api.unification.material.properties.FluidPipeProperties")) {
                var row=fields(name,value,"containmentPredicate","maxFluidTemperature","gasProof","cryoProof","plasmaProof");
                if(name.endsWith("FluidPipeProperties"))row.putAll(fields(name,value,"throughput","tanks"));
                row.put("type",name);return row;
            }
            if(name.equals("gregtech.api.fluids.attribute.FluidAttribute")) {
                // Native equality/hash identity is its resource location. Its
                // tooltip consumers do not participate in fluid containment.
                var row=fields(name,value,"resourceLocation");row.put("type",name);return row;
            }
            if(name.equals("gregtech.api.metatileentity.multiblock.CleanroomType")) {
                var row=fields(name,value,"name","translationKey");row.put("type",name);return row;
            }
            if(name.equals("supersymmetry.api.recipes.properties.BiomeProperty$BiomePropertyList")) {
                var row=new TreeMap<String,Object>();row.put("type",name);
                Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"BIOMES");
                var registered=new IdentityHashMap<Object,Object>();
                for(var entry:((Map<?,?>)field("net.minecraftforge.registries.ForgeRegistry",registry,"names")).entrySet())
                    registered.put(entry.getValue(),entry.getKey());
                for(String key:List.of("whiteListBiomes","blackListBiomes")) {
                    var biomes=new ArrayList<Object>();
                    for(Object biome:(List<?>)field(name,value,key)) {
                        if(biome==null)biomes.add(null);
                        else if(registered.containsKey(biome))biomes.add(Map.of("type",biome.getClass().getName(),
                                "registry","minecraft:biomes","key",encode(registered.get(biome))));
                        else biomes.add(unknown(biome,"recipe-biome-not-registered"));
                    }
                    row.put(key,biomes);
                }
                return row;
            }
            if(name.equals("supersymmetry.api.recipes.properties.PseudoMultiPropertyValues")) {
                var row=fields(name,value,"blockGroupName","validBlockStates");row.put("type",name);return row;
            }
            if(name.equals("icbm.classic.content.cluster.action.ActionDataCluster")) {
                var row=fields(name,value,"clusterSpawnEntries");row.put("type",name);return row;
            }
            if(name.equals("gregtech.api.recipes.recipeproperties.ResearchPropertyData")) {
                var row=fields(name,value,"entries");row.put("type",name);return row;
            }
            if(name.equals("gregtech.api.recipes.recipeproperties.ResearchPropertyData$ResearchEntry")) {
                var row=fields(name,value,"researchId","dataItem");row.put("type",name);return row;
            }
            if(name.equals("dev.tianmi.sussypatches.api.recipe.property.InfoProperty$TranslationData")) {
                var row=fields(name,value,"translationKey","args");row.put("type",name);return row;
            }
            if (name.equals("gregtech.api.recipes.ingredients.nbtmatch.NBTCondition")
                    || name.equals("gregtech.api.recipes.ingredients.nbtmatch.ListNBTCondition")) {
                var row=fields("gregtech.api.recipes.ingredients.nbtmatch.NBTCondition",value,"tagType","nbtKey","value");
                row.put("type",name);
                row.put("anyIdentity",value==field("gregtech.api.recipes.ingredients.nbtmatch.NBTCondition",null,"ANY"));
                if(name.endsWith("ListNBTCondition"))row.put("listTagType",encode(field(cls,value,"listTagType")));
                return row;
            }
            if (cls.isArray()) {
                var values = new ArrayList<Object>();
                for(int i=0;i<Array.getLength(value);i++)values.add(encode(Array.get(value,i)));
                return Map.of("type",name,"values",values);
            }
            if (value instanceof Collection<?> collection && nativeCollection(name)) {
                var values=new ArrayList<Object>();for(Object element:collection)values.add(encode(element));
                if(value instanceof Set<?>)values.sort(Comparator.comparing(RecipeStateCatalog::digest));
                return Map.of("type",value instanceof Set<?>?"set":"list","values",values);
            }
            if (value instanceof Map<?,?> map && nativeCollection(name)) {
                var values=new ArrayList<Object>();
                for(var entry:map.entrySet()) {
                    var pair=new ArrayList<Object>();pair.add(encode(entry.getKey()));pair.add(encode(entry.getValue()));values.add(pair);
                }
                values.sort(Comparator.comparing(RecipeStateCatalog::digest));return Map.of("type","map","entries",values);
            }
            return unknown(value,"unsupported-native-value");
        } finally {active.remove(value);}
    }
    private static boolean nativeCollection(String name) {
        return name.startsWith("java.util.")||name.startsWith("com.google.common.collect.")
                ||name.startsWith("it.unimi.dsi.fastutil.")||name.equals("net.minecraft.util.NonNullList");
    }
    /** Only called for source-inspected storage types. In particular, do not
     * invoke Ingredient getters: native implementations expand wildcards and
     * populate caches. Retain the existing cache fields as they stand. */
    private Object storedFields(Object value,Class<?> stop) throws ReflectiveOperationException {
        var fields=new TreeMap<String,Object>();
        for(Class<?> owner=value.getClass();owner!=stop;owner=owner.getSuperclass()) {
            for(var member:owner.getDeclaredFields()) {
                if(Modifier.isStatic(member.getModifiers()))continue;
                member.setAccessible(true);fields.put(owner.getName()+"#"+member.getName(),encode(member.get(value)));
            }
        }
        return Map.of("type",value.getClass().getName(),"storedFields",fields);
    }
    private Object stack(Object stack) throws ReflectiveOperationException {
        var row=new TreeMap<String,Object>();row.put("type",STACK);
        Object item=virtual(STACK,stack,"func_77973_b",type("net.minecraft.item.Item"),new Class<?>[0]);
        row.put("item",item==null?null:encode(itemName(item)));
        row.put("count",field(STACK,stack,"field_77994_a"));
        // Forge redirects damage/metadata getters through item callbacks.
        // Capture the stored field without evaluating those callbacks.
        row.put("damage",field(STACK,stack,"field_77991_e"));
        row.put("tag",encode(field(STACK,stack,"field_77990_d")));
        row.put("groovyIngredientState",fields(STACK,stack,"groovyScript$matchCondition",
                "groovyScript$transformer","groovyScript$nbtMatcher","groovyScript$mark"));
        row.put("capabilityTag",encode(field(STACK,stack,"capNBT")));
        Object capabilities=field(STACK,stack,"capabilities");
        row.put("capabilities",capabilities==null?null:capabilities(capabilities,stack));
        return row;
    }
    private Object itemName(Object item) throws ReflectiveOperationException {
        return virtual("net.minecraft.item.Item",item,"getRegistryName",type("net.minecraft.util.ResourceLocation"),new Class<?>[0]);
    }
    Object registeredItem(Object item) throws ReflectiveOperationException {
        return Map.of("type",item.getClass().getName(),"registry","minecraft:items","key",encode(itemName(item)));
    }
    private Object registeredBlock(Object block) throws ReflectiveOperationException {
        Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"BLOCKS");
        for(var entry:((Map<?,?>)field("net.minecraftforge.registries.ForgeRegistry",registry,"names")).entrySet())
            if(entry.getValue()==block)return Map.of("type",block.getClass().getName(),"registry","minecraft:blocks",
                    "key",encode(entry.getKey()));
        return unknown(block,"recipe-block-not-registered");
    }
    private Object blockState(Object state) throws ReflectiveOperationException {
        String owner="net.minecraft.block.state.BlockStateContainer$StateImplementation";
        String containerOwner="net.minecraft.block.state.BlockStateContainer";
        Object block=field(owner,state,"field_177239_a");
        Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"BLOCKS");
        Object key=null;
        for(var entry:((Map<?,?>)field("net.minecraftforge.registries.ForgeRegistry",registry,"names")).entrySet())
            if(entry.getValue()==block) {key=entry.getKey();break;}
        if(key==null)return unknown(state,"recipe-block-not-registered");
        Object container=field("net.minecraft.block.Block",block,"field_176227_L");
        if(container==null||container.getClass()!=type(containerOwner))
            return unknown(state,"unsupported-recipe-block-state-container");
        boolean present=false;
        for(Object registered:(List<?>)field(containerOwner,container,"field_177625_e"))
            if(registered==state) {present=true;break;}
        if(!present)return unknown(state,"recipe-block-state-outside-native-container");
        var definitions=(Map<?,?>)field(containerOwner,container,"field_177624_d");
        var properties=new TreeMap<String,Object>();
        for(var entry:((Map<?,?>)field(owner,state,"field_177237_b")).entrySet()) {
            Object property=entry.getKey(),value=entry.getValue();String propertyType=property.getClass().getName();
            if(!Set.of("net.minecraft.block.properties.PropertyEnum","net.minecraft.block.properties.PropertyBool",
                    "net.minecraft.block.properties.PropertyInteger").contains(propertyType))
                return unknown(property,"unsupported-recipe-block-property");
            String name=(String)field("net.minecraft.block.properties.PropertyHelper",property,"field_177703_b");
            Class<?> valueType=(Class<?>)field("net.minecraft.block.properties.PropertyHelper",property,"field_177704_a");
            if(definitions.get(name)!=property||!valueType.isInstance(value))
                return unknown(property,"recipe-block-property-definition-mismatch");
            Object encoded;
            if(value instanceof Boolean)encoded=value;
            else if(value.getClass()==Integer.class)encoded=encode(value);
            else if(value instanceof Enum<?> constant&&Set.of("net.minecraft.block.BlockLog$EnumAxis",
                    "net.minecraft.block.BlockPlanks$EnumType").contains(constant.getDeclaringClass().getName()))
                encoded=Map.of("type",constant.getDeclaringClass().getName(),"value",constant.name());
            else return unknown(value,"unsupported-recipe-block-property-value");
            properties.put(name,Map.of("type",propertyType,"valueClass",valueType.getName(),"value",encoded));
        }
        // Existing container membership and raw property fields establish the
        // state without invoking world queries, metadata conversion or matching.
        return Map.of("type",owner,"block",encode(key),"properties",properties);
    }
    private Object capabilities(Object dispatcher,Object stack) throws ReflectiveOperationException {
        String owner="net.minecraftforge.common.capabilities.CapabilityDispatcher";
        if(!dispatcher.getClass().getName().equals(owner))return unknown(dispatcher,"unsupported-capability-dispatcher");
        var providers=new ArrayList<Object>();var identities=new IdentityHashMap<Object,Integer>();
        for(Object cap:(Object[])field(owner,dispatcher,"caps")) {
            identities.put(cap,providers.size());providers.add(capability(cap,stack));
        }
        var writers=new ArrayList<Object>();
        for(Object writer:(Object[])field(owner,dispatcher,"writers")) {
            if(!identities.containsKey(writer))writers.add(unknown(writer,"capability-writer-outside-providers"));
            else writers.add(identities.get(writer));
        }
        return Map.of("type",owner,"providers",providers,"writers",writers,"names",encode(field(owner,dispatcher,"names")));
    }
    private Object capability(Object value,Object stack) throws ReflectiveOperationException {
        if(value==null)return null;
        String name=value.getClass().getName();var row=new TreeMap<String,Object>();row.put("type",name);
        if(!active.add(value))return unknown(value,"cyclic-native-capability");
        try {
            if(name.equals(BACKPACK+"capability.BackpackWrapper")) {
                row.putAll(fields(name,value,"uuid","isCached","sortType","mainColor","accentColor"));
                for(String member:List.of("backpackInventorySize","upgradeSlotsSize"))
                    row.put(member,backpackSize(field(name,value,member)));
                for(String member:List.of("backpackItemStackHandler","upgradeItemStackHandler"))
                    row.put(member,backpackInventory(field(name,value,member),value));
                return row;
            }
            if(name.startsWith(UPGRADE)&&name.endsWith("UpgradeWrapper")
                    &&BACKPACK_UPGRADES.contains(name.substring(UPGRADE.length(),name.length()-"UpgradeWrapper".length())))
                return backpackUpgrade(value);
            if(name.equals("gregtech.api.capability.impl.CombinedCapabilityProvider")) {
                var children=new ArrayList<Object>();
                for(Object child:(Object[])field(name,value,"providers"))children.add(capability(child,stack));
                row.put("providers",children);return row;
            }
            if(name.equals("com.direwolf20.buildinggadgets.common.items.capability.MultiCapabilityProvider")) {
                var children=new ArrayList<Object>();
                for(Object child:(List<?>)field(name,value,"childProviders"))children.add(capability(child,stack));
                row.put("childProviders",children);return row;
            }
            if(name.equals("com.direwolf20.buildinggadgets.common.items.capability.CapabilityProviderBlockProvider")
                    ||name.equals("com.direwolf20.buildinggadgets.common.items.capability.CapabilityProviderEnergy")) {
                row.put("container",stackReference(field(name,value,"stack"),stack));
                if(name.endsWith("Energy")) {
                    row.putAll(fields(name,value,"energyCapacity"));
                    row.put("poweredByFE",field("com.direwolf20.buildinggadgets.common.config.SyncedConfig",null,"poweredByFE"));
                }
                return row;
            }
            if(name.equals("mcjty.lib.varia.ItemCapabilityProvider")) {
                row.put("container",stackReference(field(name,value,"itemStack"),stack));
                row.put("item",registeredItem(field(name,value,"item")));
                Object energy=field(name,value,"energyStorage");String energyType=name+"$1";
                if(!energy.getClass().getName().equals(energyType)||field(energyType,energy,"this$0")!=value)
                    return unknown(energy,"mcjty-energy-owner-not-observed");
                row.put("energyStorage",Map.of("type",energyType,"owner",Map.of("reference","containing-energy-provider")));return row;
            }
            if(name.equals("appeng.items.tools.powered.powersink.PoweredItemCapabilities")) {
                row.put("container",stackReference(field(name,value,"is"),stack));
                Object item=field(name,value,"item");row.put("item",registeredItem(item));
                row.put("powerCapacity",encode(field("appeng.items.tools.powered.powersink.AEBasePoweredItem",item,"powerCapacity")));
                row.put("teslaAdapter",encode(field(name,value,"teslaAdapter")));return row;
            }
            if(name.equals("vazkii.quark.base.capability.EnchantColorWrapper")) {
                row.put("container",stackReference(field(name,value,"stack"),stack));
                row.put("item",registeredItem(field(name,value,"item")));return row;
            }
            if(name.equals("net.tardis.mod.cap.ITardisTracker$TrackerProvider")) {
                Object tracker=field(name,value,"tracker");
                if(!tracker.getClass().getName().equals("net.tardis.mod.cap.TardisTrackerCapability"))
                    return unknown(tracker,"unsupported-tardis-tracker");
                row.put("tracker",fields(tracker.getClass().getName(),tracker,"consolePos","console"));return row;
            }
            if(name.equals("gigaherz.toolbelt.belt.ItemToolBelt$1")) {
                row.put("container",stackReference(field(name,value,"val$stack"),stack));
                row.put("item",registeredItem(field(name,value,"this$0")));
                Object inventory=field(name,value,"itemHandler");
                if(!inventory.getClass().getName().equals("gigaherz.toolbelt.belt.ToolBeltInventory"))
                    return unknown(inventory,"unsupported-toolbelt-inventory");
                row.put("itemHandler",Map.of("type",inventory.getClass().getName(),"container",
                        stackReference(field(inventory.getClass(),inventory,"itemStack"),stack)));return row;
            }
            if(name.equals("gregtech.common.items.tool.PlungerBehavior$1")) {
                String handler="net.minecraftforge.fluids.capability.templates.FluidHandlerItemStack";
                row.put("container",stackReference(field(handler,value,"container"),stack));
                row.putAll(fields(handler,value,"capacity"));
                row.put("owner",symbol(field(name,value,"this$0"),"gregtech.common.items.tool.PlungerBehavior","INSTANCE"));return row;
            }
            if(name.equals("net.minecraftforge.fluids.capability.wrappers.FluidBucketWrapper")) {
                row.put("container",stackReference(field(name,value,"container"),stack));return row;
            }
            if(NativeRecipeFunctionObservations.STORAGE_TARGETS.contains(name)) {
                row.put("container",stackReference(field(name,value,"stack"),stack));
                boolean barrel=name.endsWith("Barrel");String member=barrel?"barrel":"crate";
                row.put(member,storageContents(field(name,value,member),value,stack,barrel));return row;
            }
            if(name.equals("com.bymarcin.openglasses.item.OpenGlassesItem$EnergyCapabilityProvider")) {
                Object storage=field(name,value,"storage");String owner=name+"$1";
                if(storage==null||storage.getClass()!=type(owner)||field(owner,storage,"this$0")!=value)
                    return unknown(storage,"openglasses-energy-owner-not-observed");
                var contents=fields("net.minecraftforge.energy.EnergyStorage",storage,
                        "energy","capacity","maxReceive","maxExtract");
                contents.put("type",owner);
                contents.put("owner",Map.of("reference","containing-energy-provider"));
                contents.put("container",stackReference(field(owner,storage,"val$stack"),stack));
                // The original overrides read energy/capacity from this stack's
                // NBT. Retain that reference and the raw superclass fields;
                // do not invoke energy queries, transfers or setters.
                row.put("storage",contents);return row;
            }
            if(name.equals("com.codetaylor.mc.pyrotech.modules.bucket.item.ItemBucketBase$BucketWrapper")) {
                row.put("container",stackReference(field("net.minecraftforge.fluids.capability.wrappers.FluidBucketWrapper",value,"container"),stack));
                row.put("item",encode(itemName(field(name,value,"this$0"))));return row;
            }
            if(name.equals("gregtech.api.capability.impl.ElectricItem")) {
                row.put("container",stackReference(field(name,value,"itemStack"),stack));
                row.putAll(fields(name,value,"maxCharge","tier","chargeable","canProvideEnergyExternally","listeners"));return row;
            }
            if(name.equals("li.cil.oc.common.item.traits.Chargeable$Provider")) {
                row.put("container",stackReference(field(name,value,"stack"),stack));
                Object item=field(name,value,"item");String owner=item.getClass().getName();
                if(!Set.of("li.cil.oc.common.item.UpgradeBattery","li.cil.oc.common.item.Tablet").contains(owner))
                    return unknown(item,"unobserved-oc-chargeable-delegate");
                // The original provider stores a stack and an item delegate.
                // Charge/max-charge callbacks read native NBT/configuration;
                // retain captured state without evaluating those callbacks.
                var delegate=owner.endsWith("Tablet")?fields(owner,item,"TimeToAnalyze","showInItemList","itemId")
                        :fields(owner,item,"tier","unlocalizedName","showInItemList","itemId");
                delegate.put("type",owner);delegate.put("parent",encode(itemName(field(owner,item,"parent"))));
                row.put("item",delegate);return row;
            }
            if(name.equals("icbm.classic.lib.capability.ex.CapabilityExplosiveStack")) {
                row.put("container",stackReference(field(name,value,"stack"),stack));
                row.putAll(fields(name,value,"customizationList"));return row;
            }
            if(name.equals("icbm.classic.lib.capability.missile.CapabilityMissileStack")) {
                row.put("container",stackReference(field(name,value,"stack"),stack));return row;
            }
            if(name.equals("icbm.classic.content.cluster.missile.CapabilityClusterMissileStack")) {
                row.put("container",stackReference(field(name,value,"stack"),stack));
                row.putAll(fields(name,value,"actionDataCluster"));return row;
            }
            if(name.equals("icbm.classic.content.missile.entity.anti.item.CapabilitySAMStack"))return row;
            if(name.equals("icbm.classic.content.missile.entity.itemstack.item.CapabilityHeldItemMissile")) {
                row.putAll(fields(name,value,"heldItem","actionMode"));return row;
            }
            if(name.equals("icbm.classic.lib.projectile.ProjectileStack")) {
                row.putAll(fields(name,value,"projectileData"));return row;
            }
            if(name.equals("icbm.classic.content.cluster.bomblet.BombletProjectileStack")) {
                Object supplier=field(name,value,"stack");var identity=NativeRecipeFunctionObservations.identity(supplier);
                row.put("stack",identity==null?unknown(supplier,"unobserved-bomblet-factory-result"):function(identity,stack));return row;
            }
            if(name.equals("icbm.classic.prefab.item.ItemStackCapProvider")) {
                row.put("container",stackReference(field(name,value,"host"),stack));
                String manager="net.minecraftforge.common.capabilities.CapabilityManager";
                var registered=new IdentityHashMap<Object,String>();
                for(var entry:((Map<?,?>)field(manager,field(manager,null,"INSTANCE"),"providers")).entrySet())
                    registered.put(entry.getValue(),(String)entry.getKey());
                var byType=new TreeMap<String,Object>();var byName=new TreeMap<String,Object>();
                for(var entry:((Map<?,?>)field(name,value,"capTypeToCap")).entrySet()) {
                    String key=registered.get(entry.getKey());
                    if(key==null)return unknown(value,"icbm-capability-type-not-registered");
                    byType.put(key,entry.getValue());
                }
                for(var entry:((Map<?,?>)field(name,value,"keyToCap")).entrySet()) {
                    if(!(entry.getKey() instanceof String key))return unknown(value,"icbm-capability-key-not-string");
                    byName.put(key,entry.getValue());
                }
                var identities=new IdentityHashMap<Object,Integer>();var providers=new ArrayList<Object>();
                for(var index:List.of(byType,byName))for(var entry:index.entrySet()) {
                    Object provider=entry.getValue();Integer ref=identities.get(provider);
                    if(ref==null) {ref=providers.size();identities.put(provider,ref);providers.add(capability(provider,stack));}
                    entry.setValue(Map.of("provider",ref));
                }
                row.put("providers",providers);row.put("capTypeToCap",byType);row.put("keyToCap",byName);return row;
            }
            if(name.equals("gregtech.integration.baubles.BaubleBehavior")
                    ||name.equals("supersymmetry.common.item.behavior.ArmorBaubleBehavior")) {
                row.putAll(fields("gregtech.integration.baubles.BaubleBehavior",value,"baubleType"));return row;
            }
            if(name.equals("baubles.common.event.EventHandlerItem$2")) {
                String owner="baubles.common.event.EventHandlerItem";
                Object capturedOwner=field(name,value,"this$0");
                if(capturedOwner==null||capturedOwner.getClass()!=type(owner))
                    return unknown(value,"bauble-capability-owner-not-observed");
                // The selected outer handler has no instance state. The
                // provider captures its stack and returns that stack's item;
                // do not query the capability or any IBauble callback.
                row.put("owner",Map.of("type",owner));
                row.put("container",stackReference(field(name,value,"val$stack"),stack));return row;
            }
            if(name.equals("vazkii.quark.management.capability.ShulkerBoxDropIn")) {
                // This exact provider and its AbstractDropIn base have no
                // instance fields. Container data is already in the stack NBT.
                return row;
            }
            if(name.equals("supersymmetry.common.item.armor.JetWingpack$ElytraFlyingProvider")) {
                // This exact original provider has no instance fields. Its
                // flight callback reads or mutates the supplied stack's NBT;
                // capture the existing provider without invoking that callback.
                return row;
            }
            if(name.equals("gregtech.api.terminal.hardware.HardwareProvider")) {
                row.put("container",stackReference(field(name,value,"itemStack"),stack));
                row.putAll(fields(name,value,"itemCache","isCreative","tag"));
                var hardware=new TreeMap<String,Object>();
                for(var entry:((Map<?,?>)field(name,value,"providers")).entrySet()) {
                    if(!(entry.getKey() instanceof String key))return unknown(value,"unsupported-hardware-key");
                    hardware.put(key,hardware(entry.getValue(),value));
                }
                row.put("providers",hardware);return row;
            }
            if(name.equals("gregtech.api.capability.impl.GTFluidHandlerItemStack")
                    ||name.equals("gregtech.api.capability.impl.GTSimpleFluidHandlerItemStack")) {
                String base="net.minecraftforge.fluids.capability.templates.FluidHandlerItemStack"
                        +(name.endsWith("GTSimpleFluidHandlerItemStack")?"Simple":"");
                row.put("container",stackReference(field(base,value,"container"),stack));
                row.put("capacity",field(base,value,"capacity"));row.putAll(fields(name,value,"canFill","canDrain","filter"));
                // properties is only a lazily created view of these fields.
                return row;
            }
            return unknown(value,"capability-provider-state-not-observed");
        } finally {active.remove(value);}
    }
    private Object hardware(Object value,Object provider) throws ReflectiveOperationException {
        if(value==null)return null;
        String name=value.getClass().getName();var row=new TreeMap<String,Object>();row.put("type",name);
        if(!Set.of("gregtech.common.terminal.hardware.BatteryHardware",
                "gregtech.common.terminal.hardware.DeviceHardware").contains(name))return unknown(value,"unsupported-terminal-hardware");
        if(field("gregtech.api.terminal.hardware.Hardware",value,"provider")!=provider)
            return unknown(value,"terminal-hardware-provider-mismatch");
        row.put("provider",Map.of("reference","containing-hardware-provider"));
        row.putAll(fields(name,value,name.endsWith("BatteryHardware")?"listeners":"slot"));return row;
    }
    private Object storageContents(Object value,Object provider,Object stack,boolean barrel) throws ReflectiveOperationException {
        String owner="funwayguy.bdsandm.inventory.capability.Capability"+(barrel?"Barrel":"Crate");
        if(value==null||value.getClass()!=type(owner))return unknown(value,"storage-content-owner-not-observed");
        var row=fields(owner,value,"cachedOres","maxStackCapacity","oreDict","lock","overflow","colors","stackCapacity","count");
        row.put("type",owner);
        for(String member:List.of("refStack","slotRef"))row.put(member,stackReference(field(owner,value,member),stack));
        Object callback=field(owner,value,"callback");
        if(callback==null)row.put("callback",null);
        else {
            var identity=NativeRecipeFunctionObservations.identity(callback);
            if(identity==null||!provider.getClass().getName().equals(identity.get("owner")))
                row.put("callback",unknown(callback,"storage-callback-factory-not-observed"));
            else {
                var captures=(List<?>)identity.get("capturedArguments");
                if(captures.size()!=3||captures.getFirst()!=provider)
                    row.put("callback",unknown(callback,"storage-callback-provider-mismatch"));
                else {
                    var function=new TreeMap<String,Object>(identity);
                    function.put("capturedArguments",Arrays.asList(Map.of("reference","containing-storage-provider"),
                            stackReference(captures.get(1),stack),stackReference(captures.get(2),stack)));
                    row.put("callback",function);
                }
            }
        }
        if(barrel) {
            row.put("refFluid",encode(field(owner,value,"refFluid")));
            row.put("containerItem",stackReference(field(owner,value,"containerItem"),stack));
            Object view=field(owner,value,"fluidTank");String viewOwner=owner+"$1";
            if(view==null||view.getClass()!=type(viewOwner)||field(viewOwner,view,"this$0")!=value)
                row.put("fluidTank",unknown(view,"storage-fluid-view-owner-not-observed"));
            else row.put("fluidTank",Map.of("type",viewOwner,"owner",Map.of("reference","containing-storage-content")));
            var views=new ArrayList<Object>();
            for(Object entry:(Object[])field(owner,value,"tankProps"))
                views.add(entry==view?Map.of("reference","fluidTank"):unknown(entry,"storage-fluid-view-not-observed"));
            row.put("tankProps",views);
        }
        return row;
    }
    private Object backpackSize(Object value) throws ReflectiveOperationException {
        if(value==null)return null;
        var identity=NativeRecipeFunctionObservations.identity(value);
        if(identity!=null)return function(identity,null);
        String name=value.getClass().getName();
        for(String tier:List.of("Leather","Iron","Gold","Diamond","Obsidian"))for(int index:List.of(1,2)) {
            if(!name.equals(BACKPACK+"item.Items$backpack"+tier+"$"+index))continue;
            String owner="kotlin.jvm.internal.CallableReference",config=BACKPACK+"config.Config$"+tier+"BackpackConfig";
            Object receiver=field(owner,value,"receiver");
            if(receiver==null||receiver.getClass()!=type(config)||field(owner,value,"owner")!=type(config))
                return unknown(value,"backpack-size-config-owner-not-observed");
            String member=index==1?"slots":"upgradeSlots";
            if(!member.equals(field(owner,value,"name")))return unknown(value,"backpack-size-property-not-observed");
            var row=fields(owner,value,"name","signature","isTopLevel","reflected");row.put("type",name);
            row.put("syntheticJavaProperty",field("kotlin.jvm.internal.PropertyReference",value,"syntheticJavaProperty"));
            row.put("owner",config);
            // The original property reference reads this exact stored field.
            // Never call Function0.invoke, get, reflection or a config getter.
            row.put("receiver",Map.of("type",config,"fields",fields(config,receiver,member)));
            return row;
        }
        return unknown(value,"backpack-size-function-not-observed");
    }
    private Object backpackInventory(Object value,Object wrapper) throws ReflectiveOperationException {
        if(value==null)return null;
        String name=value.getClass().getName(),base=BACKPACK+"inventory.";
        if(!Set.of(base+"ExposedItemStackHandler",base+"BackpackItemStackHandler",base+"UpgradeItemStackHandler",
                UPGRADE+"FeedingUpgradeWrapper$filterItems$1",UPGRADE+"AdvancedFeedingUpgradeWrapper$filterItems$1").contains(name))
            return unknown(value,"backpack-inventory-owner-not-observed");
        if(!active.add(value))return unknown(value,"cyclic-backpack-inventory");
        try {
            Object stacks=field("net.minecraftforge.items.ItemStackHandler",value,"stacks");
            var row=new TreeMap<String,Object>();row.put("type",name);row.put("stacks",encode(stacks));
            if(name.equals(base+"BackpackItemStackHandler")) {
                if(field(name,value,"wrapper")!=wrapper)return unknown(value,"backpack-inventory-wrapper-not-observed");
                row.put("wrapper",Map.of("reference","containing-backpack-provider"));
                row.putAll(fields(name,value,"memorizedSlotStack","memorizedSlotRespectNbtList","sortLockedSlots"));
            } else if(name.equals(base+"UpgradeItemStackHandler")) {
                Object inventory=field(name,value,"inventory");
                // Native deserialization may replace stacks but retain inventory.
                row.put("inventory",inventory==stacks?Map.of("reference","stacks"):encode(inventory));
            }
            return row;
        } finally {active.remove(value);}
    }
    private Object backpackUpgrade(Object value) throws ReflectiveOperationException {
        String name=value.getClass().getName();var row=fields(UPGRADE+"UpgradeWrapper",value,"isTabOpened");
        row.put("type",name);row.putAll(fields(name,value,"settingsLangKey"));
        if(name.equals(UPGRADE+"CraftingUpgradeWrapper")) {
            row.putAll(fields(name,value,"craftingDestination"));
            row.put("craftMatrix",backpackInventory(field(name,value,"craftMatrix"),null));return row;
        }
        boolean advanced=name.startsWith(UPGRADE+"Advanced");
        String base=UPGRADE+(advanced?"Advanced":"Basic")+"UpgradeWrapper";
        var inherited=fields(base,value,"enabled","filterType");
        Object filters=field(base,value,"filterItems");inherited.put("filterItems",backpackInventory(filters,null));
        if(advanced)inherited.putAll(fields(base,value,"matchType","oreDictEntries","ignoreDurability","ignoreNBT"));
        row.put("base",inherited);
        if(name.endsWith("FilterUpgradeWrapper"))row.putAll(fields(name,value,"filterWay"));
        if(name.endsWith("FeedingUpgradeWrapper")) {
            Object ownFilters=field(name,value,"filterItems");
            row.put("filterItems",ownFilters==filters?Map.of("reference","base.filterItems"):backpackInventory(ownFilters,null));
            if(advanced)row.putAll(fields(name,value,"hungerFeedingStrategy","healthFeedingStrategy"));
        }
        return row;
    }
    private Object stackReference(Object value,Object containingStack) throws ReflectiveOperationException {
        return value==containingStack?Map.of("reference","containing-item-stack"):encode(value);
    }
    private Object function(Map<String,Object> identity,Object containingStack) throws ReflectiveOperationException {
        var row=new TreeMap<String,Object>(identity);var captures=new ArrayList<Object>();
        for(Object capture:(List<?>)identity.get("capturedArguments"))
            captures.add(containingStack==null?encode(capture):stackReference(capture,containingStack));
        row.put("capturedArguments",captures);
        if(NativeRecipeFunctionObservations.CONDITION_TARGET.equals(identity.get("owner"))) {
            Object configuration=field("me.ichun.mods.ichunutil.common.iChunUtil",null,"config");
            row.put("nativeConfiguration",fields("me.ichun.mods.ichunutil.common.iChunUtil$Config",configuration,"enableCompactPorkchop"));
        }
        if(NativeRecipeFunctionObservations.GADGET_TARGET.equals(identity.get("owner")))
            row.put("nativeConfiguration",fields("com.direwolf20.buildinggadgets.common.config.SyncedConfig",null,"energyMax"));
        return row;
    }
    private Object fluid(Object stack) throws ReflectiveOperationException {
        Object fluid=virtual(FLUID_STACK,stack,"getFluid",type("net.minecraftforge.fluids.Fluid"),new Class<?>[0]);
        var row=new TreeMap<String,Object>();row.put("type",FLUID_STACK);
        row.put("fluid",fluid==null?null:virtual("net.minecraftforge.fluids.Fluid",fluid,"getName",String.class,new Class<?>[0]));
        row.put("amount",field(FLUID_STACK,stack,"amount"));row.put("tag",encode(field(FLUID_STACK,stack,"tag")));
        row.put("groovyIngredientState",fields(FLUID_STACK,stack,"matchCondition",
                "transformer","nbtMatcher","groovyScript$mark"));
        return row;
    }
    private Object nbt(Object value) throws ReflectiveOperationException {
        String name=value.getClass().getName();var row=new TreeMap<String,Object>();row.put("type",name);
        Object contents=switch(name.substring(NBT.length())) {
            case "NBTTagEnd" -> null;
            case "NBTTagByte" -> call(value,"func_150290_f");
            case "NBTTagShort" -> call(value,"func_150289_e");
            case "NBTTagInt" -> call(value,"func_150287_d");
            case "NBTTagLong" -> call(value,"func_150291_c");
            case "NBTTagFloat" -> call(value,"func_150288_h");
            case "NBTTagDouble" -> call(value,"func_150286_g");
            case "NBTTagString" -> call(value,"func_150285_a_");
            case "NBTTagByteArray" -> call(value,"func_150292_c");
            case "NBTTagIntArray" -> call(value,"func_150302_c");
            case "NBTTagList" -> {
                var entries=new ArrayList<Object>();int count=(Integer)call(value,"func_74745_c");
                row.put("elementTagType",call(value,"func_150303_d"));
                for(int i=0;i<count;i++)entries.add(encode(call(value,"func_179238_g",int.class,i)));
                row.put("values",entries);yield null;
            }
            case "NBTTagCompound" -> {
                var entries=new TreeMap<String,Object>();
                for(Object key:(Set<?>)call(value,"func_150296_c"))
                    entries.put((String)key,encode(call(value,"func_74781_a",String.class,key)));
                row.put("values",entries);yield null;
            }
            default -> unknown(value,"unsupported-nbt-value");
        };
        if(!row.containsKey("values"))row.put("value",encode(contents));
        return row;
    }
    Object symbol(Object value,String owner,String... names) throws ReflectiveOperationException {
        if(value==null)return null;
        // Only native nested/lambda implementations can establish this owner's
        // initialized constants. Do not initialize an unused symbol catalog for
        // a custom callback, nor invoke a callback to identify it.
        if(value.getClass().getNestHost().getName().equals(owner)
                || value.getClass().getEnclosingClass()!=null && value.getClass().getEnclosingClass().getName().equals(owner))
            for(String name:names)if(field(owner,null,name)==value)return Map.of("owner",owner,"constant",name);
        return unknown(value,"unrecognized-native-function");
    }
    Object ingredient(Object input) throws ReflectiveOperationException {
        String name=input.getClass().getName();
        Set<String> supported=Set.of("gregtech.api.recipes.ingredients.GTRecipeItemInput",
                "gregtech.api.recipes.ingredients.GTRecipeOreInput","gregtech.api.recipes.ingredients.GTRecipeFluidInput",
                "gregtech.api.recipes.ingredients.IntCircuitIngredient");
        if(!supported.contains(name))return unknown(input,"unsupported-recipe-ingredient");
        var row=fields(INPUT,input,"amount","isConsumable","nbtCondition");row.put("type",name);
        row.put("nbtMatcher",symbol(field(INPUT,input,"nbtMatcher"),"gregtech.api.recipes.ingredients.nbtmatch.NBTMatcher",
                "ANY","LESS_THAN","LESS_THAN_OR_EQUAL_TO","GREATER_THAN","GREATER_THAN_OR_EQUAL_TO",
                "EQUAL_TO","RECURSIVE_EQUAL_TO","NOT_PRESENT_OR_DEFAULT","NOT_PRESENT_OR_HAS_KEY"));
        if(name.endsWith("GTRecipeOreInput")) {
            int ore=(Integer)field(name,input,"ore");
            String oreName=(String)callStatic("net.minecraftforge.oredict.OreDictionary","getOreName",int.class,ore);
            // Native integer IDs depend on registration order. Preserve the
            // exact binding separately; content identity uses its native name.
            Integer previous=oreIds.putIfAbsent(oreName,ore);
            if("Unknown".equals(oreName)||previous!=null&&previous!=ore)gaps.add("ambiguous-native-ore-binding: "+oreName);
            row.put("oreName",oreName);
        } else if(name.endsWith("GTRecipeFluidInput"))row.put("stack",encode(field(name,input,"inputStack")));
        else if(name.endsWith("IntCircuitIngredient")) {
            // Its display-stack getter allocates lazily. Matching uses this
            // stored configuration directly; observation must not warm it.
            row.put("matchingConfigurations",field(name,input,"matchingConfigurations"));
        }
        else {
            String owner="gregtech.api.recipes.ingredients.GTRecipeItemInput";
            row.put("stacks",encode(field(owner,input,"inputStacks")));
            var items=new ArrayList<Object>();
            for(Object itemEntry:(List<?>)field(owner,input,"itemList")) {
                String itemOwner=INPUT+"$ItemToMetaList",metaOwner=INPUT+"$MetaToTAGList",tagOwner=INPUT+"$TagToStack";
                var itemRow=new TreeMap<String,Object>();
                Object item=field(itemOwner,itemEntry,"item");
                itemRow.put("item",encode(itemName(item)));
                var metas=new ArrayList<Object>();
                for(Object meta:(List<?>)field(itemOwner,itemEntry,"metaToTAGList")) {
                    var tags=new ArrayList<Object>();
                    for(Object tag:(List<?>)field(metaOwner,meta,"tagToStack"))tags.add(fields(tagOwner,tag,"tag","stack"));
                    metas.add(Map.of("metadata",field(metaOwner,meta,"meta"),"tags",tags));
                }
                itemRow.put("metadata",metas);items.add(itemRow);
            }
            row.put("matchingEntries",items);
        }
        return row;
    }
}
