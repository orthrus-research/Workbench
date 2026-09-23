package research.orthrus.axiom.materialhost;

import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import java.util.*;
import java.util.stream.Stream;

/** Observe stored GT lookup recipes, including hidden entries, after the original
 * recipe sequence. No matching, builder, cache refresh or chance callback runs. */
public final class NativeEffectiveRecipeObservations {
    private static final String MAP="gregtech.api.recipes.RecipeMap";
    private static final String RECIPE="gregtech.api.recipes.Recipe";
    private static final String CATEGORY="gregtech.api.recipes.category.GTRecipeCategory";
    private static final String OUTPUT="gregtech.api.recipes.chance.output.";
    private static final String PROPERTY="gregtech.api.recipes.recipeproperties.";
    private NativeEffectiveRecipeObservations() {}

    public static Map<String,Object> collect() {
        var maps=new TreeMap<String,Object>();var gaps=new TreeSet<String>();
        try {
            // Called only after native initialization returned. Read the existing
            // registry and lookup; getRecipeList filters hidden and uses equals.
            for(var entry:((Map<?,?>)field(MAP,null,"RECIPE_MAP_REGISTRY")).entrySet()) {
                String name=(String)entry.getKey();var values=new NativeRecipeValues();
                try {maps.put(name,map(entry.getValue(),values));}
                catch(Exception|LinkageError failure) {
                    maps.put(name,Map.of("status","unavailable","failure",NativeProgramObservations.trace(failure)));
                    values.gaps.add("native-map-observation-failed");
                }
                for(String gap:values.gaps)gaps.add(name+": "+gap);
            }
        } catch(Exception|LinkageError failure) {gaps.add("native-registry-observation-failed: "+failure);}
        return Map.of("schema","axiom.native-stored-recipes.v1","scope","current-gt-stored-lookup-recipes",
                "status",gaps.isEmpty()?"observed":"incomplete","storedValuesComplete",gaps.isEmpty(),
                "maps",maps,"affectingGaps",new ArrayList<>(gaps),"initializationAcceptance",false);
    }
    /** Bounded inventory of registered Groovy recipes with saved source callbacks.
     * Native-only functions and other recipe classes are outside this inventory. */
    public static Map<String,Object> collectStoredCraftingCallbacks() {
        String recipeOwner="com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipe";
        var entries=new TreeMap<String,Object>();var gaps=new TreeSet<String>();int examined=0;
        try {
            Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"RECIPES");
            for(var entry:((Map<?,?>)field("net.minecraftforge.registries.ForgeRegistry",registry,"names")).entrySet()) {
                Object recipe=entry.getValue();String name=recipe.getClass().getName();
                if(!Set.of("com.cleanroommc.groovyscript.compat.vanilla.ShapedCraftingRecipe",
                        "com.cleanroommc.groovyscript.compat.vanilla.ShapelessCraftingRecipe").contains(name))continue;
                examined++;
                Object function=field(recipeOwner,recipe,"recipeFunction"),action=field(recipeOwner,recipe,"recipeAction");
                var inputs=(List<?>)field(recipeOwner,recipe,"input");
                boolean selected=NativeRecipeFunctionObservations.storedSourceIdentity(function)!=null
                        ||NativeRecipeFunctionObservations.storedSourceIdentity(action)!=null;
                for(Object input:inputs)if(input!=null&&input.getClass().getName().equals("net.minecraft.item.ItemStack"))
                    selected|=NativeRecipeFunctionObservations.storedSourceIdentity(field(input.getClass(),input,"groovyScript$transformer"))!=null;
                if(!selected)continue;
                var values=new NativeRecipeValues();
                var row=values.fields(recipeOwner,recipe,"output","input","recipeFunction","recipeAction");
                row.put("type",name);row.put("callbackInvocationsByObserver",0);
                if(name.endsWith(".ShapedCraftingRecipe"))row.putAll(values.fields(name,recipe,"width","height","mirrored"));
                row.put("selectedStoredValuesEncoded",values.gaps.isEmpty());
                entries.put(entry.getKey().toString(),row);
                for(String gap:values.gaps)gaps.add(entry.getKey()+": "+gap);
            }
        } catch(Exception|LinkageError failure) {gaps.add("stored-crafting-callback-observation-failed: "+NativeProgramObservations.trace(failure));}
        return Map.of("schema","axiom.native-stored-crafting-callbacks.v1","scope","registered-groovy-recipes-with-source-callbacks",
                "status",gaps.isEmpty()?"observed":"incomplete","entries",entries,"groovyRecipesExamined",examined,
                "affectingGaps",List.copyOf(gaps),"callbackInvocationsByObserver",0,
                "deferredCraftingExecutionQualified",false,"initializationAcceptance",false);
    }
    /** Original reload records are distinct from current effective membership.
     * Read existing Forge tables directly: getOreID would create missing names. */
    public static Map<String,Object> collectOreMutations(Object oreDict) {
        var values=new NativeRecipeValues();var records=new TreeMap<String,Object>();
        var members=new TreeMap<String,Object>();var names=new TreeSet<String>();
        String entryType="com.cleanroommc.groovyscript.compat.vanilla.OreDictEntry";
        try {
            if(oreDict==null||!oreDict.getClass().getName().equals("com.cleanroommc.groovyscript.compat.vanilla.OreDict"))
                throw new IllegalStateException("Original ore registry root is unavailable");
            Object storage=field("com.cleanroommc.groovyscript.registry.VirtualizedRegistry",oreDict,"recipeStorage");
            for(String name:List.of("backup","scripted")) {
                var entries=new ArrayList<Object>();
                for(Object entry:(Collection<?>)field("com.cleanroommc.groovyscript.registry.AbstractReloadableStorage",storage,name)) {
                    if(entry==null||!entry.getClass().getName().equals(entryType)) {
                        entries.add(values.unknown(entry,"unsupported-ore-mutation-record"));continue;
                    }
                    String ore=(String)field(entryType,entry,"name");names.add(ore);
                    entries.add(values.fields(entryType,entry,"name","stack"));
                }
                records.put(name,entries);
            }
            var ids=(Map<?,?>)field("net.minecraftforge.oredict.OreDictionary",null,"nameToId");
            var stacks=(List<?>)field("net.minecraftforge.oredict.OreDictionary",null,"idToStack");
            for(String name:names) {
                Integer id=(Integer)ids.get(name);
                members.put(name,id==null?Map.of("registered",false):Map.of("registered",true,"nativeOreId",id,
                        "members",values.encode(stacks.get(id))));
            }
        } catch(Exception|LinkageError failure) {values.gaps.add("ore-mutation-observation-failed: "+NativeProgramObservations.trace(failure));}
        return Map.of("schema","axiom.native-ore-mutations.v1","scope","groovy-recorded-ore-names-current-membership",
                "status",values.gaps.isEmpty()?"observed":"incomplete","reloadRecords",records,"currentMembership",members,
                "affectingGaps",List.copyOf(values.gaps),"registryMutationsByObserver",0,"initializationAcceptance",false);
    }
    /** Stored fields only. No world-dependent hardness query or block callback. */
    public static Map<String,Object> collectBlockHardness() {
        var values=new NativeRecipeValues();var entries=new TreeMap<String,Object>();
        try {
            Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"BLOCKS");
            for(var entry:((Map<?,?>)field("net.minecraftforge.registries.ForgeRegistry",registry,"names")).entrySet()) {
                Object block=entry.getValue();entries.put(entry.getKey().toString(),Map.of("type",block.getClass().getName(),
                        "hardness",values.encode(field("net.minecraft.block.Block",block,"field_149782_v"))));
            }
        } catch(Exception|LinkageError failure) {values.gaps.add("stored-block-hardness-observation-failed: "+NativeProgramObservations.trace(failure));}
        return Map.of("schema","axiom.native-block-hardness.v1","scope","registered-block-stored-hardness-fields",
                "status",values.gaps.isEmpty()?"observed":"incomplete","entries",entries,"affectingGaps",List.copyOf(values.gaps),
                "blockCallbacksByObserver",0,"initializationAcceptance",false);
    }
    private static Map<String,Object> map(Object map,NativeRecipeValues values) throws ReflectiveOperationException {
        var row=new TreeMap<String,Object>();row.put("type",map.getClass().getName());
        row.put("name",field(MAP,map,"unlocalizedName"));
        row.put("limits",values.fields(MAP,map,"maxInputs","maxOutputs","maxFluidInputs","maxFluidOutputs",
                "modifyItemInputs","modifyItemOutputs","modifyFluidInputs","modifyFluidOutputs","allowEmptyOutput","isHidden"));
        row.put("chanceFunction",values.symbol(field(MAP,map,"chanceFunction"),
                "gregtech.api.recipes.chance.boost.ChanceBoostFunction","OVERCLOCK","NONE"));
        Object lookup=field(MAP,map,"lookup");
        var catalog=new RecipeStateCatalog();
        try(var stream=(Stream<?>)call(lookup,"getRecipes",boolean.class,false)) {
            var iterator=stream.iterator();while(iterator.hasNext())catalog.add(iterator.next(),recipe->recipe(recipe,map,values));
        }
        row.put("lookup",catalog.snapshot());
        // Categories can retain entries which were not inserted into the lookup.
        // Report that independently; they never establish effective membership.
        var categories=new ArrayList<Object>();
        for(var entry:((Map<?,?>)field(MAP,map,"recipeByCategory")).entrySet()) {
            var membership=new TreeMap<String,Integer>();var absent=new RecipeStateCatalog();
            for(Object recipe:(List<?>)entry.getValue()) {
                String identity=catalog.identity(recipe);
                if(identity==null)absent.add(recipe,value->recipe(value,map,values));
                else membership.merge(identity,1,Integer::sum);
            }
            categories.add(Map.of("category",category(entry.getKey(),map,values),"lookupMembers",membership,
                    "outsideLookup",absent.snapshot()));
        }
        categories.sort(Comparator.comparing(RecipeStateCatalog::digest));row.put("categories",categories);
        row.put("nativeOreIds",new TreeMap<>(values.oreIds));
        row.put("storedValuesComplete",values.gaps.isEmpty());row.put("affectingGaps",new ArrayList<>(values.gaps));
        return row;
    }
    private static Object recipe(Object recipe,Object map,NativeRecipeValues values) throws ReflectiveOperationException {
        if(!recipe.getClass().getName().equals(RECIPE))return values.unknown(recipe,"unsupported-native-recipe-class");
        var row=values.fields(RECIPE,recipe,"outputs","fluidOutputs","duration","EUt","hidden","isCTRecipe","groovyRecipe");
        row.put("type",RECIPE);
        for(String name:List.of("inputs","fluidInputs")) {
            var inputs=new ArrayList<Object>();for(Object input:(List<?>)field(RECIPE,recipe,name))inputs.add(values.ingredient(input));
            row.put(name,inputs);
        }
        for(String name:List.of("chancedOutputs","chancedFluidOutputs"))row.put(name,chance(field(RECIPE,recipe,name),values));
        row.put("category",category(field(RECIPE,recipe,"recipeCategory"),map,values));
        row.put("properties",properties(field(RECIPE,recipe,"recipePropertyStorage"),values));
        return row;
    }
    private static Object category(Object category,Object map,NativeRecipeValues values) throws ReflectiveOperationException {
        if(category==null)return Map.of("present",false);
        if(!category.getClass().getName().equals(CATEGORY))return values.unknown(category,"unsupported-recipe-category");
        var row=values.fields(CATEGORY,category,"modid","name","uniqueID","translationKey");
        row.put("owningMapMatches",field(CATEGORY,category,"recipeMap")==map);return row;
    }
    private static Object chance(Object chance,NativeRecipeValues values) throws ReflectiveOperationException {
        if(!chance.getClass().getName().equals(OUTPUT+"ChancedOutputList"))return values.unknown(chance,"unsupported-chance-list");
        var row=new TreeMap<String,Object>();
        row.put("logic",values.symbol(field(OUTPUT+"ChancedOutputList",chance,"chancedOutputLogic"),
                OUTPUT+"ChancedOutputLogic","OR","AND","XOR","NONE"));
        var entries=new ArrayList<Object>();
        for(Object entry:(List<?>)field(OUTPUT+"ChancedOutputList",chance,"chancedEntries")) {
            if(!Set.of(OUTPUT+"impl.ChancedItemOutput",OUTPUT+"impl.ChancedFluidOutput").contains(entry.getClass().getName())) {
                entries.add(values.unknown(entry,"unsupported-chance-entry"));continue;
            }
            var entryRow=values.fields("gregtech.api.recipes.chance.BaseChanceEntry",entry,"ingredient","chance");
            entryRow.put("chanceBoost",values.encode(field(OUTPUT+"BoostableChanceOutput",entry,"chanceBoost")));entries.add(entryRow);
        }
        row.put("entries",entries);return row;
    }
    private static Object properties(Object storage,NativeRecipeValues values) throws ReflectiveOperationException {
        if(storage.getClass().getName().equals(PROPERTY+"EmptyRecipePropertyStorage"))return Map.of("type",storage.getClass().getName(),"entries",List.of());
        if(!storage.getClass().getName().equals(PROPERTY+"RecipePropertyStorage"))return values.unknown(storage,"unsupported-recipe-property-storage");
        var entries=new ArrayList<Object>();
        for(var entry:((Map<?,?>)field(PROPERTY+"RecipePropertyStorage",storage,"recipeProperties")).entrySet()) {
            var row=new TreeMap<String,Object>();Object property=entry.getKey();
            row.put("propertyClass",property.getClass().getName());row.put("key",field(PROPERTY+"RecipeProperty",property,"key"));
            row.put("valueClass",((Class<?>)field(PROPERTY+"RecipeProperty",property,"type")).getName());
            row.put("value",values.encode(entry.getValue()));entries.add(row);
        }
        entries.sort(Comparator.comparing(RecipeStateCatalog::digest));
        return Map.of("type",storage.getClass().getName(),"frozen",field(PROPERTY+"RecipePropertyStorage",storage,"frozen"),"entries",entries);
    }
}
