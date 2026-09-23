package dev.workbench.crucible.forgerecipes;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.Hashing;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.invoke.MethodHandle;
import java.net.JarURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.LinkOption;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import java.util.zip.ZipFile;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Reads original finite GT registries and invokes original admitted input matchers. */
public final class ForgeRecipeSnapshot {
    private static final String GT = "gregtech.api.recipes.";
    private static final String ORE = GT + "ingredients.GTRecipeOreInput";
    private static final String CIRCUIT = GT + "ingredients.IntCircuitIngredient";
    private static final String MTE = "gregtech.api.metatileentity.MetaTileEntity";
    private static final String LOGIC = "gregtech.api.capability.impl.AbstractRecipeLogic";
    private final List<RecipeDraft> recipes = new ArrayList<RecipeDraft>();
    private final List<Map<String,Object>> mapRows = new ArrayList<Map<String,Object>>();
    private final List<Map<String,Object>> machineRows = new ArrayList<Map<String,Object>>();
    private final List<Map<String,Object>> bindings = new ArrayList<Map<String,Object>>();
    private final IdentityHashMap<Object,String> mapNames = new IdentityHashMap<Object,String>();

    private ForgeRecipeSnapshot() {}
    public static Map<String,Object> prepareCapabilities() {
        return ForgeCapabilityPreparation.prepare(ForgeRecipeSnapshot::preparationRecipes,new ForgeOrdinaryItemMatching.OriginalAccess());
    }
    private static List<ForgeCapabilityPreparation.Recipe> preparationRecipes() {
        ForgeRecipeSnapshot snapshot=new ForgeRecipeSnapshot();snapshot.readRecipes();
        List<ForgeCapabilityPreparation.Recipe> result=new ArrayList<ForgeCapabilityPreparation.Recipe>();
        for(int recipeOrdinal=0;recipeOrdinal<snapshot.recipes.size();recipeOrdinal++) {
            RecipeDraft draft=snapshot.recipes.get(recipeOrdinal);String digest=hash(draft.row);
            List<ForgeCapabilityPreparation.Reference> refs=new ArrayList<ForgeCapabilityPreparation.Reference>();
            for(ItemRef ref:draft.itemRefs) {
                String key=ref.pointer.contains("/item_stack_representatives/")?ref.pointer.substring(0,ref.pointer.lastIndexOf('/')):ref.pointer;
                refs.add(new ForgeCapabilityPreparation.Reference(ref.stack,"recipe-item-occurrence",key,
                    row("recipe_record_sha256",digest,"pointer","/records/"+recipeOrdinal+ref.pointer)));
            }
            for(int slot=0;slot<draft.inputs.size();slot++) {
                Object input=draft.inputs.get(slot);
                if(!input.getClass().getName().equals(ForgeOrdinaryItemMatching.INPUT))continue;
                for(ForgeOrdinaryItemMatching.StoredTarget target:ForgeOrdinaryItemMatching.originalStoredTargets(input)) {
                    Map<String,Object> fields=new LinkedHashMap<String,Object>(target.fields);
                    fields.put("recipe_record_sha256",digest);fields.put("selector_ordinal",slot);
                    refs.add(new ForgeCapabilityPreparation.Reference(target.stack,"ordinary-stored-target",slot+":"+target.key,fields));
                }
            }
            result.add(new ForgeCapabilityPreparation.Recipe(draft.recipe,draft.row,refs));
        }
        return result;
    }
    public static Map<String,List<Map<String,Object>>> capture() {
        ForgeRecipeSnapshot capture = new ForgeRecipeSnapshot();
        progress("finite recipe observation started");
        capture.readRecipes();
        progress("finite recipe observation complete; recipes="+capture.recipes.size()+", maps="+capture.mapRows.size());
        progress("machine observation started");
        capture.readMachines();
        progress("machine observation complete; machines="+capture.machineRows.size()+", bindings="+capture.bindings.size());
        List<Map<String,Object>> recipeRows = new ArrayList<Map<String,Object>>();
        for (RecipeDraft draft : capture.recipes) recipeRows.add(draft.row);
        Map<String,List<Map<String,Object>>> result = new LinkedHashMap<String,List<Map<String,Object>>>();
        result.put("gt-recipes", recipeRows);
        result.put("gt-recipe-maps", capture.mapRows);
        result.put("gt-meta-tile-entities", capture.machineRows);
        result.put("gt-machine-recipe-maps", capture.bindings);
        result.put("gt-item-matching", capture.matching());
        result.put("gt-item-names", capture.itemNames());
        result.put("gt-ordinary-item-matching", capture.ordinaryMatching());
        for (List<Map<String,Object>> rows : result.values()) sort(rows);
        return result;
    }

    private void readRecipes() {
        RecipeFailures failures=new RecipeFailures();
        Class<?> registry = type(GT + "RecipeMap");
        List<Object> maps = list(call(registry, "getRecipeMaps"));
        Collections.sort(maps, Comparator.comparing(m -> (String) mapCall(m, "getUnlocalizedName",String.class)));
        Set<String> names = new HashSet<String>();
        for (Object map : maps) {
            String name = (String) mapCall(map, "getUnlocalizedName",String.class);
            require(name != null && !name.isEmpty() && names.add(name) && call(registry, "getByName", name) == map, "Invalid GT map registry");
            mapNames.put(map, name);
            Membership membership = new Membership(map);
            List<RecipeDraft> drafts = new ArrayList<RecipeDraft>();
            int unionOrdinal=0;
            for (Object recipe : membership.union.keySet()) {
                try {drafts.add(new RecipeDraft(name, recipe, membership));}
                catch(RuntimeException | LinkageError failure) {failures.add(name,unionOrdinal,recipe,failure);}
                unionOrdinal++;
            }
            Collections.sort(drafts, (a,b) -> {
                int order = CanonicalJson.compareUnsigned(a.semantic, b.semantic);
                if (order != 0) return order;
                order = Boolean.compare(b.active, a.active);
                return order != 0 ? order : Boolean.compare(b.categoryPresent, a.categoryPresent);
            });
            Map<String,Integer> duplicates = new LinkedHashMap<String,Integer>();
            for (RecipeDraft draft : drafts) {
                String digest = Hashing.sha256(draft.semantic);
                int ordinal = duplicates.containsKey(digest) ? duplicates.get(digest) : 0;
                duplicates.put(digest, ordinal + 1);
                draft.row = row("record_type", "gt-recipe", "recipe_map", name, "semantic_sha256", digest,
                    "duplicate_ordinal", ordinal, "lookup_active", draft.active, "category_present", draft.categoryPresent, "recipe", draft.value);
                recipes.add(draft);
            }
            mapRows.add(mapRow(map, membership));
        }
        failures.throwIfAny();
        require(!maps.isEmpty() && !recipes.isEmpty(), "Empty GT finite registry");
        // Domain pointers and publication order share the exact canonical record order.
        Collections.sort(recipes, (a,b) -> CanonicalJson.compareUnsigned(bytes(a.row), bytes(b.row)));
    }

    /** Diagnostic-only continuation: any failed recipe refuses the entire capture. */
    static final class RecipeFailures {
        private final Map<String,FailureShape> shapes=new LinkedHashMap<String,FailureShape>();
        private int count;
        void add(String map,int ordinal,Object recipe,Throwable failure) {
            StringBuilder key=new StringBuilder();
            IdentityHashMap<Throwable,Boolean> causes=new IdentityHashMap<Throwable,Boolean>();
            for(Throwable cause=failure;cause!=null && causes.put(cause,Boolean.TRUE)==null;cause=cause.getCause())
                key.append(cause.getClass().getName()).append(':').append(cause.getMessage()).append('\n');
            FailureShape shape=shapes.get(key.toString());
            if(shape==null) {shape=new FailureShape(failure);shapes.put(key.toString(),shape);}
            shape.contexts.add(row("recipe_map",map,"union_iteration_ordinal",ordinal,"recipe_runtime_class",recipe.getClass().getName()));
            count++;
        }
        void throwIfAny() {
            if(count==0)return;
            IllegalStateException refused=new IllegalStateException("Recipe serialization refused: "+count+" observed recipes in "+shapes.size()+" failure shapes; no partial capture is publishable");
            for(FailureShape shape:shapes.values()) {
                sort(shape.contexts);
                refused.addSuppressed(new IllegalStateException("All affected recipe contexts (iteration ordinals are diagnostic, not stable identities): "
                    +new String(bytes(shape.contexts),StandardCharsets.UTF_8),shape.original));
            }
            throw refused;
        }
        private static final class FailureShape {
            final Throwable original;final List<Map<String,Object>> contexts=new ArrayList<Map<String,Object>>();
            FailureShape(Throwable original){this.original=original;}
        }
    }

    private Map<String,Object> mapRow(Object map, Membership membership) {
        int both = 0;
        for (Object recipe : membership.active.keySet()) if (membership.category.containsKey(recipe)) both++;
        List<Map<String,Object>> categories = new ArrayList<Map<String,Object>>();
        for (Map.Entry<?,?> entry : ((Map<?,?>) mapCall(map, "getRecipesByCategory",Map.class)).entrySet()) {
            Object category = entry.getKey();
            categories.add(row("unique_id", call(category,"getUniqueID"), "name", call(category,"getName"),
                "mod_id", call(category,"getModid"), "translation_key", call(category,"getTranslationKey"),
                "recipe_occurrence_count", list(entry.getValue()).size()));
        }
        sort(categories);
        String findOwner = virtualOwner(map,"findRecipe",type(GT+"Recipe"),long.class,List.class,List.class,boolean.class);
        return row("record_type","gt-recipe-map", "name",mapCall(map,"getUnlocalizedName",String.class),
            "translation_key",mapCall(map,"getTranslationKey",String.class), "runtime_class",map.getClass().getName(),
            "max_item_inputs",mapCall(map,"getMaxInputs",int.class), "max_item_outputs",mapCall(map,"getMaxOutputs",int.class),
            "max_fluid_inputs",mapCall(map,"getMaxFluidInputs",int.class), "max_fluid_outputs",mapCall(map,"getMaxFluidOutputs",int.class),
            "hidden",field(map,"isHidden"), "modify_item_inputs",field(map,"modifyItemInputs"),
            "modify_item_outputs",field(map,"modifyItemOutputs"), "modify_fluid_inputs",field(map,"modifyFluidInputs"),
            "modify_fluid_outputs",field(map,"modifyFluidOutputs"), "allow_empty_output",field(map,"allowEmptyOutput"),
            "has_ore_dictionary_inputs",field(map,"hasOreDictedInputs"), "has_nbt_matcher_inputs",field(map,"hasNBTMatcherInputs"),
            "lookup_identity_count",membership.active.size(), "category_occurrence_count",membership.categoryOccurrences,
            "union_identity_count",membership.union.size(), "lookup_and_category_count",both,
            "lookup_only_count",membership.active.size()-both, "category_only_count",membership.category.size()-both,
            "find_recipe_owner",findOwner, "dynamic",!findOwner.equals(GT+"RecipeMap"), "categories",categories,
            "capture_scope","finite storage and original lookup declaration; procedural findRecipe execution is not enumerated");
    }
    private static Object mapCall(Object map,String name,Class<?> result) {return callVirtual(map,type(GT+"RecipeMap"),name,result);}

    private static final class Membership {
        final IdentityHashMap<Object,Boolean> active = new IdentityHashMap<Object,Boolean>();
        final IdentityHashMap<Object,Boolean> category = new IdentityHashMap<Object,Boolean>();
        final IdentityHashMap<Object,Boolean> union = new IdentityHashMap<Object,Boolean>();
        int categoryOccurrences;
        Membership(Object map) {
            Object stream = call(field(map,"lookup"),"getRecipes",false);
            require(stream instanceof Stream<?>, "GT lookup is not its original finite stream");
            try (Stream<?> values = (Stream<?>) stream) { values.forEach(recipe -> {require(recipe != null,"Null lookup recipe");active.put(recipe,Boolean.TRUE);}); }
            for (Map.Entry<?,?> entry : ((Map<?,?>) mapCall(map,"getRecipesByCategory",Map.class)).entrySet()) {
                require(entry.getKey()!=null && call(entry.getKey(),"getRecipeMap")==map,"Foreign GT category");
                for (Object recipe : list(entry.getValue())) {
                    require(recipe!=null && call(recipe,"getRecipeCategory")==entry.getKey(),"GT category ownership mismatch");
                    require(category.put(recipe,Boolean.TRUE)==null,"Repeated recipe object in category index");
                    categoryOccurrences++;
                }
            }
            union.putAll(active);union.putAll(category);
            for (Object recipe : union.keySet()) require(call(call(recipe,"getRecipeCategory"),"getRecipeMap")==map,"Recipe points outside its map");
        }
    }

    private static final class ItemRef {
        final String pointer; final Object stack; final Map<String,Object> value;
        ItemRef(String pointer,Object stack,Map<String,Object> value) {this.pointer=pointer;this.stack=stack;this.value=value;}
    }
    private static final class RecipeDraft {
        final Object recipe; final boolean active,categoryPresent;
        final Map<String,Object> value; final byte[] semantic;
        final List<Object> inputs; final List<ItemRef> itemRefs=new ArrayList<ItemRef>();
        Map<String,Object> row;
        RecipeDraft(String name,Object recipe,Membership membership) {
            this.recipe=recipe;active=membership.active.containsKey(recipe);categoryPresent=membership.category.containsKey(recipe);
            inputs=list(call(recipe,"getInputs"));
            value=row("runtime_class",recipe.getClass().getName(), "category",call(call(recipe,"getRecipeCategory"),"getUniqueID"),
                "duration",call(recipe,"getDuration"), "eut",call(recipe,"getEUt"), "hidden",call(recipe,"isHidden"),
                "crafttweaker_recipe",call(recipe,"getIsCTRecipe"), "groovy_recipe",call(recipe,"isGroovyRecipe"),
                "item_inputs",inputRows(inputs,false), "fluid_inputs",inputRows(list(call(recipe,"getFluidInputs")),true),
                "item_outputs",outputs(list(call(recipe,"getOutputs")),false,"item_outputs"),
                "fluid_outputs",outputs(list(call(recipe,"getFluidOutputs")),true,"fluid_outputs"),
                "chanced_item_outputs",chances(call(recipe,"getChancedOutputs"),false,"chanced_item_outputs"),
                "chanced_fluid_outputs",chances(call(recipe,"getChancedFluidOutputs"),true,"chanced_fluid_outputs"),
                "properties",properties(recipe));
            semantic=bytes(value);
        }
        private List<Map<String,Object>> inputRows(List<Object> inputs,boolean fluid) {
            List<Map<String,Object>> rows=new ArrayList<Map<String,Object>>();
            for (int ordinal=0;ordinal<inputs.size();ordinal++) {
                Object input=inputs.get(ordinal);require(input!=null,"Null GT input");
                boolean ore=(Boolean)call(input,"isOreDict");
                Object stacks=call(input,"getInputStacks");
                List<ItemRef> references=new ArrayList<ItemRef>();
                if (stacks!=null) for (Object stack:list(stacks)) references.add(new ItemRef(null,stack,item(stack)));
                Collections.sort(references,(a,b)->CanonicalJson.compareUnsigned(bytes(a.value),bytes(b.value)));
                List<Map<String,Object>> representatives=new ArrayList<Map<String,Object>>();
                for (int i=0;i<references.size();i++) {
                    ItemRef ref=references.get(i);representatives.add(ref.value);
                    require(!fluid,"Fluid selector unexpectedly has item representatives");
                    itemRefs.add(new ItemRef("/recipe/item_inputs/"+ordinal+"/item_stack_representatives/"+i,ref.stack,ref.value));
                }
                rows.add(row("ordinal",ordinal,"runtime_class",input.getClass().getName(),"amount",call(input,"getAmount"),
                    "non_consumable",call(input,"isNonConsumable"),"ore_dictionary",ore,
                    "ore_dictionary_id",ore?call(input,"getOreDict"):null,
                    "ore_dictionary_name",ore?call(type("net.minecraftforge.oredict.OreDictionary"),"getOreName",call(input,"getOreDict")):null,
                    "has_nbt_matching_condition",call(input,"hasNBTMatchingCondition"),
                    "nbt_matcher",ForgeReflection.value(call(input,"getNBTMatcher")),
                    "nbt_condition",ForgeReflection.value(call(input,"getNBTMatchingCondition")),
                    "fluid_stack",fluid(call(input,"getInputFluidStack")),"item_stack_representatives",representatives));
            }
            return rows;
        }
        private List<Map<String,Object>> outputs(List<Object> outputs,boolean fluid,String family) {
            List<Map<String,Object>> rows=new ArrayList<Map<String,Object>>();
            for (int i=0;i<outputs.size();i++) {
                Map<String,Object> stack=fluid?fluid(outputs.get(i)):item(outputs.get(i));
                rows.add(row("ordinal",i,"value",stack));
                if (!fluid) itemRefs.add(new ItemRef("/recipe/"+family+"/"+i+"/value",outputs.get(i),stack));
            }
            return rows;
        }
        private Map<String,Object> chances(Object outputs,boolean fluid,String family) {
            List<Map<String,Object>> entries=new ArrayList<Map<String,Object>>();
            for (Object entry:list(call(outputs,"getChancedEntries"))) {
                Object ingredient=call(entry,"getIngredient");
                Map<String,Object> stack=fluid?fluid(ingredient):item(ingredient);
                int i=entries.size();
                entries.add(row("ordinal",i,"runtime_class",entry.getClass().getName(),"chance",call(entry,"getChance"),
                    "chance_boost",is(entry,GT+"chance.output.BoostableChanceOutput")?call(entry,"getChanceBoost"):null,"value",stack));
                if (!fluid) itemRefs.add(new ItemRef("/recipe/"+family+"/entries/"+i+"/value",ingredient,stack));
            }
            return row("logic_class",call(outputs,"getChancedOutputLogic").getClass().getName(),"entries",entries);
        }
        private static List<Map<String,Object>> properties(Object recipe) {
            List<Map<String,Object>> rows=new ArrayList<Map<String,Object>>();Set<String> keys=new HashSet<String>();
            for (Object raw:list(call(recipe,"getPropertyValues"))) {
                Map.Entry<?,?> entry=(Map.Entry<?,?>)raw;Object property=entry.getKey();
                Class<?> contract=type(GT+"recipeproperties.RecipeProperty");
                String key=(String)callVirtual(property,contract,"getKey",String.class);require(key!=null && keys.add(key),"Duplicate GT property key");
                rows.add(row("key",key,"property_class",property.getClass().getName(),"hidden",callVirtual(property,contract,"isHidden",boolean.class),"value",ForgeReflection.value(entry.getValue())));
            }
            Collections.sort(rows,Comparator.comparing(r->(String)r.get("key")));return rows;
        }
    }

    private void readMachines() {
        Object registry=readPublicField(null,type("gregtech.api.GregTechAPI"),"MTE_REGISTRY",type("gregtech.api.util.GTControlledRegistry"));
        require(Boolean.TRUE.equals(call(registry,"isFrozen")),"MTE registry is not frozen");
        List<Object> keys=list(callNames(registry,new String[]{"getKeys","func_148742_b"}));Collections.sort(keys,Comparator.comparing(Object::toString));
        require(!keys.isEmpty(),"MTE registry is empty");Set<Integer> ids=new HashSet<Integer>();
        for (Object key:keys) {
            Object machine=callNames(registry,new String[]{"getObject","func_82594_a"},key);int id=((Number)call(registry,"getIdByObjectName",key)).intValue();
            require(machine!=null && key.equals(readPublicField(machine,type(MTE),"metaTileEntityId",type("net.minecraft.util.ResourceLocation"))) && key.equals(callNames(registry,new String[]{"getNameForObject","func_177774_c"},machine))
                && id>=0 && ids.add(id) && callNames(registry,new String[]{"getObjectById","func_148754_a"},id)==machine,"Invalid MTE registry identity");
            Class<?> contract=type(MTE);
            Object stack=callVirtual(machine,contract,"getStackForm",type("net.minecraft.item.ItemStack"));Map<String,Object> stackRow=item(stack);
            require(Integer.valueOf(1).equals(stackRow.get("count")),"MTE stack count is not one");
            Object logic=callVirtual(machine,contract,"getRecipeLogic",type(LOGIC));
            String kind=is(machine,"gregtech.api.metatileentity.multiblock.MultiblockControllerBase")?"multiblock-controller":
                is(machine,"gregtech.api.metatileentity.WorkableTieredMetaTileEntity")?"singleblock-workable":"meta-tile-entity";
            machineRows.add(row("record_type","gt-meta-tile-entity","registry_name",key.toString(),"numeric_id",id,
                "runtime_class",machine.getClass().getName(),"meta_name",callVirtual(machine,contract,"getMetaName",String.class),"meta_full_name",callVirtual(machine,contract,"getMetaFullName",String.class),
                "controller_kind",kind,"stack_form",stackRow,
                "tier",is(machine,"gregtech.api.metatileentity.ITieredMetaTileEntity")?callVirtual(machine,type("gregtech.api.metatileentity.ITieredMetaTileEntity"),"getTier",int.class):null,
                "has_recipe_logic",logic!=null,"recipe_logic_class",logic==null?null:logic.getClass().getName(),
                "recipe_logic_consumes_energy",logic==null?null:callVirtual(logic,type(LOGIC),"consumesEnergy",boolean.class),
                "recipe_map_projection","gt-machine-recipe-maps category","capture_scope","registered prototype; formation and running machine viability are not observed"));
            if (is(machine,"gregtech.api.capability.IMultipleRecipeMaps")) {
                Class<?> multiple=type("gregtech.api.capability.IMultipleRecipeMaps");
                List<Object> available=list(callVirtual(machine,multiple,"getAvailableRecipeMaps",type("[Lgregtech.api.recipes.RecipeMap;")));require(!available.isEmpty(),"Multi-map machine has no available map");
                IdentityHashMap<Object,Boolean> seen=new IdentityHashMap<Object,Boolean>();
                for (int i=0;i<available.size();i++) {Object map=available.get(i);require(seen.put(map,Boolean.TRUE)==null,"Repeated available machine map");addBinding(key,map,"IMultipleRecipeMaps.getAvailableRecipeMaps",i,"runtime-selectable");}
                Object current=callVirtual(machine,multiple,"getCurrentRecipeMap",type(GT+"RecipeMap"));require(current==null || seen.containsKey(current),"Current machine map is unavailable");
                bindings.add(row("record_type","gt-machine-recipe-map-selection","machine",key.toString(),"authority","IMultipleRecipeMaps.getCurrentRecipeMap","current_recipe_map",current==null?null:mapName(current)));
            } else {
                Object map=logic==null?null:callVirtual(logic,type(LOGIC),"getRecipeMap",type(GT+"RecipeMap"));
                if (map==null) bindings.add(row("record_type","gt-machine-no-recipe-map-binding","machine",key.toString(),"reason",logic==null?"MetaTileEntity.getRecipeLogic returned null":"AbstractRecipeLogic.getRecipeMap returned null"));
                else {require(callVirtual(machine,contract,"getRecipeMap",type(GT+"RecipeMap"))==map,"MTE and recipe logic disagree on map");addBinding(key,map,"AbstractRecipeLogic.getRecipeMap",0,"direct");}
            }
        }
    }
    private String mapName(Object map) {String name=mapNames.get(map);require(name!=null,"Machine references an unregistered map");return name;}
    private void addBinding(Object machine,Object map,String authority,int ordinal,String selection) {
        bindings.add(row("record_type","gt-machine-recipe-map-binding","machine",machine.toString(),"recipe_map",mapName(map),"authority",authority,"ordinal",ordinal,"selection",selection));
    }

    private List<Map<String,Object>> matching() {
        List<Object> candidates=new ArrayList<Object>();List<Map<String,Object>> locators=new ArrayList<Map<String,Object>>();
        Set<String> identities=new HashSet<String>();
        for (int i=0;i<recipes.size();i++) {
            RecipeDraft recipe=recipes.get(i);String recordHash=hash(recipe.row);
            for (ItemRef ref:recipe.itemRefs) {
                Map<String,Object> key=new LinkedHashMap<String,Object>(ref.value);key.remove("count");
                if (identities.add(hash(key))) {candidates.add(ref.stack);locators.add(row("recipe_record_sha256",recordHash,"pointer","/records/"+i+ref.pointer));}
            }
        }
        progress("ore/circuit matcher execution started; recipes="+recipes.size()+", candidates="+candidates.size());
        Map<String,Object> artifacts=artifactBinding();
        Map<String,Object> classes=new LinkedHashMap<String,Object>();
        classes.put("GTRecipeOreInput",originalClassHash(type(ORE)));
        classes.put("IntCircuitIngredient",originalClassHash(type(CIRCUIT)));
        classes.put("MetaItem$MetaValueItem",originalClassHash(type("gregtech.api.items.metaitem.MetaItem$MetaValueItem")));
        classes.put("OreDictionary",originalClassHash(type("net.minecraftforge.oredict.OreDictionary")));
        List<Map<String,Object>> result=new ArrayList<Map<String,Object>>();
        result.add(row("record_type","gt-item-matching-domain","model","gt-2.8.10-forge-1.12.2-finite-item-matching-v1",
            "domain_scope","captured-gt-input-output-item-variants","domain_sha256",hash(locators),"domain_count",candidates.size(),
            "artifacts",artifacts,"class_sha256",classes));
        // Identity memoization only: same live input object, same immutable domain.
        IdentityHashMap<Object,List<Integer>> memo=new IdentityHashMap<Object,List<Integer>>();
        Object integrated=callVirtual(field(type("gregtech.common.items.MetaItems"),"INTEGRATED_CIRCUIT"),type("gregtech.api.items.metaitem.MetaItem$MetaValueItem"),"getStackForm",type("net.minecraft.item.ItemStack"));
        Map<String,Object> integratedStack=item(integrated);
        Class<?> stackContract=type("net.minecraft.item.ItemStack");
        MethodHandle copy;
        try {copy=virtualContract(stackContract,"copy",stackContract);}
        catch(IllegalStateException absent) {
            if(!(absent.getCause() instanceof NoSuchMethodException))throw absent;
            copy=virtualContract(stackContract,"func_77946_l",stackContract);
        }
        MethodHandle accepts=virtualContract(type(GT+"ingredients.GTRecipeInput"),"acceptsStack",boolean.class,stackContract);
        for (RecipeDraft recipe:recipes) for (int i=0;i<recipe.inputs.size();i++) {
            Object input=recipe.inputs.get(i);String name=input.getClass().getName();
            if ((!name.equals(ORE) && !name.equals(CIRCUIT)) || Boolean.TRUE.equals(call(input,"hasNBTMatchingCondition"))
                || call(input,"getNBTMatcher")!=null || call(input,"getNBTMatchingCondition")!=null) continue;
            List<Integer> accepted=memo.get(input);
            if (accepted==null) {
                accepted=new ArrayList<Integer>();
                for (int candidate=0;candidate<candidates.size();candidate++)
                    if (acceptsCopied(accepts,copy,input,candidates.get(candidate))) accepted.add(candidate);
                memo.put(input,accepted);
                if(memo.size()%1000==0)progress("ore/circuit matcher execution; unique_selectors="+memo.size()+", candidates="+candidates.size());
            }
            boolean circuit=name.equals(CIRCUIT);
            result.add(row("record_type","gt-item-matching-selector","recipe_map",recipe.row.get("recipe_map"),
                "semantic_sha256",recipe.row.get("semantic_sha256"),"duplicate_ordinal",recipe.row.get("duplicate_ordinal"),
                "recipe_record_sha256",hash(recipe.row),"selector_ordinal",i,"matcher",circuit?"integrated-circuit":"ore-dictionary",
                "matching_configurations",circuit?field(input,"matchingConfigurations"):null,
                "integrated_circuit",circuit?row("registry_name",integratedStack.get("registry_name"),"item_damage",integratedStack.get("item_damage")):null,
                "accepted_domain_ordinals",accepted));
        }
        progress("ore/circuit matcher execution complete; unique_selectors="+memo.size()+", records="+result.size());
        return result;
    }

    static boolean acceptsCopied(MethodHandle accepts,MethodHandle copy,Object input,Object candidate) {
        try {return (boolean)accepts.invoke(input,copy.invoke(candidate));}
        catch(Throwable failure){throw new IllegalStateException("Original copied candidate matcher invocation failed",failure);}
    }

    private List<Map<String,Object>> ordinaryMatching() {
        List<ForgeOrdinaryItemMatching.Recipe> source=new ArrayList<ForgeOrdinaryItemMatching.Recipe>();
        for(int index=0;index<recipes.size();index++) {
            RecipeDraft recipe=recipes.get(index);String digest=hash(recipe.row);
            List<ForgeOrdinaryItemMatching.Occurrence> occurrences=new ArrayList<ForgeOrdinaryItemMatching.Occurrence>();
            for(ItemRef ref:recipe.itemRefs) occurrences.add(new ForgeOrdinaryItemMatching.Occurrence(ref.stack,ref.value,
                row("recipe_record_sha256",digest,"pointer","/records/"+index+ref.pointer)));
            source.add(new ForgeOrdinaryItemMatching.Recipe(recipe.row,recipe.inputs,occurrences));
        }
        Map<String,Object> classes=row("GTRecipeItemInput",originalClassHash(type(ForgeOrdinaryItemMatching.INPUT)),
            "GTRecipeInput",originalClassHash(type(GT+"ingredients.GTRecipeInput")),
            "CapabilityDispatcher",originalClassHash(type(ForgeOrdinaryItemMatching.DISPATCHER)));
        Class<?> delayer=type(ForgeOrdinaryItemMatching.DELAYER);
        // Loading a Mixin implementation class is forbidden: hash its original member through the public interface archive.
        classes.put("IItemStackCapabilityDelayer",originalClassHash(delayer));
        classes.put("LoliItemStackMixin",originalMemberHash(artifact(delayer),"zone/rong/loliasm/common/capability/mixins/ItemStackMixin.class"));
        Map<String,Object> artifacts=artifactBinding();artifacts.put("loliasm_sha256",artifactHash(delayer));
        return ForgeOrdinaryItemMatching.capture(source,artifacts,classes);
    }

    private List<Map<String,Object>> itemNames() {
        Class<?> resolver=type("gregtech.integration.groovy.GroovyScriptModule");
        List<String> machineNames=new ArrayList<String>();
        for (Map<String,Object> machine:machineRows) machineNames.add((String)machine.get("registry_name"));
        return itemNameRows(resolver,machineNames,requestedItemNames(),artifactHash(resolver),originalClassHash(resolver));
    }

    /** Observe the existing resolver cache and invoke the original getter; never reload it. */
    static List<Map<String,Object>> itemNameRows(Object resolver,List<String> machineNames,
            List<String> requested,String artifactHash,String resolverHash) {
        Object rawCache=field(resolver,"metaItems");
        require(rawCache instanceof Map<?,?>,"Original meta-item resolver cache is not a map");
        Map<?,?> cache=(Map<?,?>)rawCache;
        Set<String> queries=new TreeSet<String>();
        for (Map.Entry<?,?> namespace:cache.entrySet()) {
            require(namespace.getKey() instanceof String && namespace.getValue() instanceof Map<?,?>,"Invalid meta-item resolver namespace");
            for (Object name:((Map<?,?>)namespace.getValue()).keySet()) {
                require(name instanceof String,"Invalid meta-item resolver name");
                addItemNameQueries(queries,(String)namespace.getKey(),(String)name);
            }
        }
        for (String machine:machineNames) {
            int colon=machine.indexOf(':');
            require(colon>0 && colon<machine.length()-1,"Invalid observed machine name");
            addItemNameQueries(queries,machine.substring(0,colon),machine.substring(colon+1));
        }
        for (String query:requested) {require(query!=null && !query.isEmpty(),"Requested item name is empty");queries.add(query);}
        List<Map<String,Object>> result=new ArrayList<Map<String,Object>>();
        for (String query:queries) {
            Object rawSplit=call(resolver,"splitObjectName",query);
            require(rawSplit instanceof String[] && ((String[])rawSplit).length==2,"Original item name split is invalid");
            String[] split=(String[])rawSplit;
            Object namespace=cache.get(split[0]);
            Object cached=namespace==null?null:((Map<?,?>)namespace).get(split[1]);
            Object expected=cached==null?call(resolver,"getMetaTileEntityItem",(Object)split):cached;
            Object resolved=call(resolver,"getMetaItem",query);
            Map<String,Object> stack=resolved==null?null:item(resolved);
            require(hash(stack).equals(hash(expected==null?null:item(expected))),"Original item resolver differs from its observed cache/fallback");
            result.add(row("record_type","gt-item-name-binding","query",query,"namespace",split[0],"name",split[1],
                "authority","GroovyScriptModule.getMetaItem","resolution",cached!=null?"cache":resolved!=null?"meta-tile-entity":"unresolved",
                "stack",stack,"gregtech_sha256",artifactHash,"resolver_class_sha256",resolverHash));
        }
        sort(result);return result;
    }
    private static void addItemNameQueries(Set<String> queries,String namespace,String name) {
        require(!namespace.isEmpty() && !name.isEmpty(),"Empty meta-item resolver key");
        queries.add(namespace+":"+name);
        if (namespace.equals("gregtech")) queries.add(name);
    }
    static List<String> requestedItemNames() {
        try {
            String selected=System.getProperty("workbench.runtimeGraph.input_manifest_path");
            String digest=System.getProperty("workbench.runtimeGraph.input_manifest_sha256");
            require(selected!=null && digest!=null,"Bound input manifest is required for item-name queries");
            Path path=Paths.get(selected);
            require(path.isAbsolute() && Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)
                && Files.size(path)<=32L*1024L*1024L,"Invalid input manifest for item-name queries");
            byte[] raw=Files.readAllBytes(path);
            require(Hashing.sha256(raw).equals(digest),"Item-name input manifest differs from launch binding");
            JsonObject input=new JsonParser().parse(new String(raw,StandardCharsets.UTF_8)).getAsJsonObject();
            List<String> result=new ArrayList<String>();
            if (!input.has("item_name_queries")) return result;
            JsonElement queries=input.get("item_name_queries");
            require(queries.isJsonArray(),"item_name_queries must be an array");
            for (JsonElement query:queries.getAsJsonArray()) {
                require(query.isJsonPrimitive() && query.getAsJsonPrimitive().isString() && !query.getAsString().isEmpty(),
                    "item_name_queries must contain nonempty strings");
                result.add(query.getAsString());
            }
            return result;
        } catch (java.io.IOException failure) {throw new IllegalStateException("Cannot read bound item-name queries",failure);}
    }
    private static Map<String,Object> artifactBinding() {
        // Forge's launch classpath contains one original Minecraft server artifact.
        String minecraft=System.getProperty("workbench.runtimeGraph.minecraft_path");
        require(minecraft!=null,"Exact Minecraft artifact path is required for matching evidence");
        return row("gregtech_sha256",artifactHash(type(ORE)),"forge_sha256",artifactHash(type("net.minecraftforge.oredict.OreDictionary")),
            "minecraft_sha256",fileHash(Paths.get(minecraft)));
    }
    private static String fileHash(Path path) {
        try {return Hashing.sha256(Files.readAllBytes(path));}
        catch (Exception failure) {throw new IllegalStateException("Cannot hash original artifact "+path,failure);}
    }
    private static Path artifact(Class<?> type) {
        try {return localArtifact(type.getProtectionDomain().getCodeSource().getLocation(),type.getName().replace('.','/')+".class");}
        catch (Exception failure) {throw new IllegalStateException("Cannot identify original artifact for "+type.getName(),failure);}
    }
    /** Resolve original local archive bytes; jar URLs do not authorize mounted ZIP filesystems or remote reads. */
    static Path localArtifact(URL origin,String member) {
        try {
            require(origin!=null && member!=null && member.endsWith(".class"),"Invalid original class origin");
            URL archive=origin;
            if(origin.getProtocol().equals("jar")) {
                JarURLConnection connection=(JarURLConnection)origin.openConnection();
                connection.setUseCaches(false);
                archive=connection.getJarFileURL();
                String entry=connection.getEntryName();
                require(entry==null || entry.isEmpty() || entry.equals(member),"Unexpected nested or foreign original archive origin");
            }
            require(archive.getProtocol().equals("file"),"Original archive origin must be a local file");
            URI uri=archive.toURI();
            require(uri.getAuthority()==null && uri.getQuery()==null && uri.getFragment()==null,"Original archive URL has remote or ambiguous components");
            Path path=Paths.get(uri);
            require(path.isAbsolute() && Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS),"Original class archive is not a regular local file");
            try(ZipFile zip=new ZipFile(path.toFile())) {
                require(zip.getEntry(member)!=null && !zip.getEntry(member).isDirectory(),"Original archive has no selected class member");
            }
            return path;
        } catch(Exception failure){throw new IllegalStateException("Cannot resolve exact local original class archive: "+origin,failure);}
    }
    private static String artifactHash(Class<?> type) {return fileHash(artifact(type));}
    private static String originalClassHash(Class<?> type) {
        return originalMemberHash(artifact(type),type.getName().replace('.','/')+".class");
    }
    static String originalMemberHash(Path artifact,String member) {
        try (ZipFile zip=new ZipFile(artifact.toFile())) {
            require(zip.getEntry(member)!=null && !zip.getEntry(member).isDirectory(),"Original archive has no selected class member");
            try(InputStream in=zip.getInputStream(zip.getEntry(member))) {
                ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] buffer=new byte[8192];int count;
                while ((count=in.read(buffer))!=-1) bytes.write(buffer,0,count);
                return Hashing.sha256(bytes.toByteArray());
            }
        } catch (Exception failure) {throw new IllegalStateException("Cannot hash original member "+member+" in "+artifact,failure);}
    }
    private static void progress(String message){System.out.println("[Workbench Forge observer] "+message);}
    private static void require(boolean condition,String message) {if(!condition)throw new IllegalStateException(message);}
}
