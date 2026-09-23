package research.orthrus.axiom.materialhost;

import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import java.lang.reflect.Modifier;
import java.util.*;

/** Passive storage observations after the original recipe lifecycle. Neither
 * crafting/matching nor furnace lookup, fuel conversion or serialization runs. */
public final class NativeVanillaRecipeObservations {
    private static final String REGISTRY="net.minecraftforge.registries.ForgeRegistry";
    private static final String FURNACE="net.minecraft.item.crafting.FurnaceRecipes";
    private static final String CUSTOM="com.cleanroommc.groovyscript.compat.vanilla.CustomFurnaceManager";
    private NativeVanillaRecipeObservations() {}

    public static Map<String,Object> crafting() {
        var entries=new TreeMap<String,Object>();var order=new ArrayList<String>();
        var lookupOrder=new ArrayList<Object>();
        var catalog=new NativeRecipeValues.Catalog();
        boolean frozen=false;
        var gaps=new TreeSet<String>();var types=new TreeMap<String,Object>();
        try {
            Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"RECIPES");
            frozen=Boolean.TRUE.equals(field(REGISTRY,registry,"isFrozen"));
            var names=(Map<?,?>)field(REGISTRY,registry,"names");
            var keys=new IdentityHashMap<Object,String>();
            for(var entry:names.entrySet())keys.put(entry.getValue(),entry.getKey().toString());
            // ForgeRegistry.iterator walks ascending occupied numeric IDs.
            var ids=new TreeMap<Integer,Object>();
            for(var entry:((Map<?,?>)field(REGISTRY,registry,"ids")).entrySet())ids.put((Integer)entry.getKey(),entry.getValue());
            for(var entry:ids.entrySet()) {
                String key=keys.get(entry.getValue());
                if(key==null)gaps.add("crafting-numeric-entry-without-name: "+entry.getKey());
                else lookupOrder.add(Map.of("id",entry.getKey(),"key",key));
            }
            if(ids.size()!=names.size())gaps.add("crafting-numeric-and-name-bindings-differ");
            for(var entry:names.entrySet()) {
                String key=entry.getKey().toString();Object recipe=entry.getValue();order.add(key);
                var values=new NativeRecipeValues(catalog);var row=new TreeMap<String,Object>();
                row.put("type",recipe.getClass().getName());
                try {
                    var fields=new TreeMap<String,Object>();var fieldTypes=new TreeMap<String,String>();
                    // The registry binding is observed above. Its delegate is a
                    // reference back into Forge's registry, not recipe contents.
                    for(Class<?> owner=recipe.getClass();owner!=Object.class
                            && !owner.getName().equals("net.minecraftforge.registries.IForgeRegistryEntry$Impl");
                            owner=owner.getSuperclass()) {
                        for(var member:owner.getDeclaredFields()) {
                            if(Modifier.isStatic(member.getModifiers()))continue;
                            member.setAccessible(true);String name=owner.getName()+"#"+member.getName();
                            fieldTypes.put(name,member.getType().getName());
                            fields.put(name,values.encode(member.get(recipe)));
                        }
                    }
                    types.put(recipe.getClass().getName(),fieldTypes);row.put("storedFields",fields);
                } catch(Exception|LinkageError failure) {
                    row.put("failure",NativeProgramObservations.trace(failure));
                    values.gaps.add("crafting-stored-fields-unavailable");
                }
                row.put("storedValuesComplete",values.gaps.isEmpty());row.put("affectingGaps",List.copyOf(values.gaps));
                entries.put(key,row);for(String gap:values.gaps)gaps.add(key+": "+gap);
            }
        } catch(Exception|LinkageError failure) {
            gaps.add("crafting-registry-unavailable: "+NativeProgramObservations.trace(failure));
        }
        var result=new TreeMap<String,Object>();
        result.put("schema","axiom.native-stored-crafting-recipes.v1");
        result.put("scope","current-forge-crafting-registry-storage");
        result.put("entries",entries);result.put("nativeNameIterationOrder",order);result.put("fieldTypes",types);
        result.put("nativeLookupOrder",lookupOrder);
        result.put("registryFrozen",frozen);
        result.put("nativeValues",catalog.values);gaps.addAll(catalog.gaps);
        result.put("status",gaps.isEmpty()?"observed":"incomplete");result.put("storedValuesComplete",gaps.isEmpty());
        result.put("affectingGaps",List.copyOf(gaps));result.put("callbackInvocationsByObserver",0);
        result.put("initializationAcceptance",false);return result;
    }

    public static Map<String,Object> furnace() {
        var result=new TreeMap<String,Object>();var values=new NativeRecipeValues();
        result.put("schema","axiom.native-stored-furnace-recipes.v1");
        result.put("scope","current-furnace-recipe-experience-time-and-fuel-conversion-storage");
        try {
            Object furnace=field(FURNACE,null,"field_77606_a");
            if(furnace==null)throw new IllegalStateException("Original furnace registry was not initialized");
            // Preserve native entry iteration order: wildcard matching can make
            // order meaningful. Do not call getSmeltingResult/getExperience.
            result.put("smelting",mapEntries((Map<?,?>)field(FURNACE,furnace,"field_77604_b"),values));
            result.put("experience",mapEntries((Map<?,?>)field(FURNACE,furnace,"field_77605_c"),values));
            Object times=field(CUSTOM,null,"TIME_MAP");
            String timeOwner="com.cleanroommc.groovyscript.helper.ingredient.itemstack.ItemStack2IntProxyMap";
            var wildcard=new ArrayList<Object>();
            for(var entry:((Map<?,?>)field(timeOwner,times,"wildcard")).entrySet())
                wildcard.add(Map.of("item",values.registeredItem(entry.getKey()),"time",values.encode(entry.getValue())));
            result.put("timeWildcard",wildcard);
            result.put("timeMetadata",mapEntries((Map<?,?>)field(timeOwner,times,"metadata"),values));
            // fastutil's raw default is part of the original getInt behavior.
            result.put("timeWildcardDefault",values.encode(field("it.unimi.dsi.fastutil.objects.AbstractObject2IntFunction",
                    field(timeOwner,times,"wildcard"),"defRetValue")));
            result.put("timeMetadataDefault",values.encode(field("it.unimi.dsi.fastutil.objects.AbstractObject2IntFunction",
                    field(timeOwner,times,"metadata"),"defRetValue")));
            var conversions=new ArrayList<Object>();
            for(Object conversion:(List<?>)field(CUSTOM,null,"FUEL_TRANSFORMERS"))
                conversions.add(values.fields(CUSTOM+"$FuelConversionRecipe",conversion,"smelted","fuel"));
            result.put("fuelConversions",conversions);
        } catch(Exception|LinkageError failure) {
            result.put("failure",NativeProgramObservations.trace(failure));values.gaps.add("furnace-storage-unavailable");
        }
        result.put("status",values.gaps.isEmpty()?"observed":"incomplete");
        result.put("storedValuesComplete",values.gaps.isEmpty());result.put("affectingGaps",List.copyOf(values.gaps));
        result.put("callbackInvocationsByObserver",0);result.put("initializationAcceptance",false);return result;
    }

    private static List<Object> mapEntries(Map<?,?> map,NativeRecipeValues values) throws ReflectiveOperationException {
        var entries=new ArrayList<Object>();
        for(var entry:map.entrySet()) {
            var row=new TreeMap<String,Object>();row.put("input",values.encode(entry.getKey()));
            row.put("value",values.encode(entry.getValue()));entries.add(row);
        }
        return entries;
    }
}
