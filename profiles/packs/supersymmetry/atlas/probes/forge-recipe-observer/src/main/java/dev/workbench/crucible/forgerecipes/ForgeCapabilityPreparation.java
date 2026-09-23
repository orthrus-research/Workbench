package dev.workbench.crucible.forgerecipes;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Explicit one-shot owner initialization before independently observed effective snapshots. */
final class ForgeCapabilityPreparation {
    static final String POLICY="forge-loli-original-capability-materialization-v1";
    private ForgeCapabilityPreparation() {}
    interface Source {List<Recipe> read();}
    static final class Reference {
        final Object stack;final String role,key;final Map<String,Object> fields;
        Reference(Object stack,String role,String key,Map<String,Object> fields) {
            this.stack=stack;this.role=role;this.key=key;this.fields=fields;
        }
    }
    static final class Recipe {
        final Object identity;final Map<String,Object> row;final List<Reference> references;
        Recipe(Object identity,Map<String,Object> row,List<Reference> references) {
            this.identity=identity;this.row=row;this.references=references;
        }
    }
    private static final class ObjectState {
        final Object stack;final Map<String,Object> row;final List<Map<String,Object>> references=new ArrayList<Map<String,Object>>();
        ObjectState(Object stack,int ordinal,ForgeOrdinaryItemMatching.Access access) {
            this.stack=stack;row=row("object_ordinal",ordinal,"stack_before",access.describe(stack),
                "initialization_state_before",state(access.initialized(stack)),"references",references);
        }
    }
    private static String state(Boolean initialized){return initialized==null?"unknown":initialized?"initialized":"deferred";}
    static Map<String,Object> prepare(Source source,ForgeOrdinaryItemMatching.Access access) {
        List<Recipe> before=source.read();
        Map<String,IdentityHashMap<Object,Recipe>> beforeRecipes=new LinkedHashMap<String,IdentityHashMap<Object,Recipe>>();
        IdentityHashMap<Object,ObjectState> unique=new IdentityHashMap<Object,ObjectState>();
        List<ObjectState> objects=new ArrayList<ObjectState>();List<Map<String,Object>> recipesBefore=new ArrayList<Map<String,Object>>();
        IdentityHashMap<Reference,Map<String,Object>> referenceRows=new IdentityHashMap<Reference,Map<String,Object>>();
        int references=0,occurrences=0,targets=0;
        for(Recipe recipe:before) {
            addRecipe(beforeRecipes,recipe);recipesBefore.add(recipe.row);
            for(Reference reference:recipe.references) {
                ObjectState object=unique.get(reference.stack);
                if(object==null){object=new ObjectState(reference.stack,objects.size(),access);objects.add(object);unique.put(reference.stack,object);}
                require(reference.role.equals("recipe-item-occurrence") || reference.role.equals("ordinary-stored-target"),"Unknown preparation reference role");
                Map<String,Object> referenceRow=row("role",reference.role,"before",reference.fields,"after",null);
                object.references.add(referenceRow);referenceRows.put(reference,referenceRow);
                references++;if(reference.role.equals("recipe-item-occurrence"))occurrences++;else targets++;
            }
        }
        System.out.println("[Workbench Forge observer] capability preparation started; recipes="+before.size()+", objects="+objects.size()+", references="+references);
        int invocations=0;
        for(ObjectState object:objects) {
            Map<String,Object> row=object.row;row.put("stack_before_initializer",access.describe(object.stack));
            Boolean initial=access.initialized(object.stack);row.put("initialization_state_before_initializer",state(initial));
            boolean invoked=Boolean.FALSE.equals(initial);row.put("initializer_invoked",invoked);
            if(invoked) {
                try {access.initialize(object.stack);}
                catch(RuntimeException failure){throw new IllegalStateException("One-shot original capability preparation failed; context="
                    +new String(bytes(row),java.nio.charset.StandardCharsets.UTF_8),failure);}
                invocations++;
                require(Boolean.TRUE.equals(access.initialized(object.stack)),"Original preparation initializer returned without initialized state");
            }
            row.put("stack_after_initializer",access.describe(object.stack));
            row.put("initialization_state_after_initializer",state(access.initialized(object.stack)));
        }
        int changed=0,unknown=0;
        for(ObjectState object:objects) {
            Map<String,Object> after=access.describe(object.stack);String finalState=state(access.initialized(object.stack));
            require(!finalState.equals("deferred"),"Capabilities became deferred after one-shot preparation");
            object.row.put("stack_after",after);object.row.put("initialization_state_after",finalState);
            if(!object.row.get("stack_before").equals(after))changed++;
            if(finalState.equals("unknown"))unknown++;
        }
        List<Recipe> after=source.read();Map<String,IdentityHashMap<Object,Recipe>> afterRecipes=new LinkedHashMap<String,IdentityHashMap<Object,Recipe>>();
        for(Recipe recipe:after)addRecipe(afterRecipes,recipe);
        require(before.size()==after.size() && beforeRecipes.keySet().equals(afterRecipes.keySet()),"Recipe inventory changed during capability preparation");
        List<Map<String,Object>> bindings=new ArrayList<Map<String,Object>>();
        for(Recipe old:before) {
            Recipe current=afterRecipes.get((String)old.row.get("recipe_map")).get(old.identity);
            require(current!=null,"Recipe identity disappeared during capability preparation");
            require(old.row.get("recipe_map").equals(current.row.get("recipe_map")),"Recipe map changed during capability preparation");
            bindings.add(row("recipe_map",old.row.get("recipe_map"),"before_record_sha256",hash(old.row),"after_record_sha256",hash(current.row)));
            IdentityHashMap<Object,Map<String,List<Reference>>> remaining=new IdentityHashMap<Object,Map<String,List<Reference>>>();
            for(Reference ref:current.references) {
                Map<String,List<Reference>> keys=remaining.get(ref.stack);
                if(keys==null){keys=new LinkedHashMap<String,List<Reference>>();remaining.put(ref.stack,keys);}
                String key=ref.role+":"+ref.key;List<Reference> list=keys.get(key);
                if(list==null){list=new ArrayList<Reference>();keys.put(key,list);}list.add(ref);
            }
            require(old.references.size()==current.references.size(),"Recipe stack reference inventory changed during preparation");
            for(Reference ref:old.references) {
                Map<String,List<Reference>> keys=remaining.get(ref.stack);String key=ref.role+":"+ref.key;
                require(keys!=null && keys.containsKey(key) && !keys.get(key).isEmpty(),"Original stack or slot reference changed during preparation");
                Reference next=keys.get(key).remove(0);ObjectState object=unique.get(ref.stack);
                referenceRows.get(ref).put("after",next.fields);
                // An additional source read must not modify the settled stack behind the returned record.
                require(object.row.get("stack_after").equals(access.describe(ref.stack))
                    && object.row.get("initialization_state_after").equals(state(access.initialized(ref.stack))),
                    "Source observation changed prepared item state");
            }
        }
        List<Map<String,Object>> records=new ArrayList<Map<String,Object>>();for(ObjectState object:objects)records.add(object.row);
        System.out.println("[Workbench Forge observer] capability preparation complete; initializer_calls="+invocations+", changed_objects="+changed+", unknown_objects="+unknown);
        return row("policy",POLICY,"state","complete","recipes_before",recipesBefore,"recipe_bindings",bindings,"objects",records,
            "counts",row("recipe_count",before.size(),"object_count",objects.size(),"reference_count",references,
                "occurrence_reference_count",occurrences,"stored_target_reference_count",targets,
                "initializer_invocation_count",invocations,"changed_object_count",changed,"unknown_object_count",unknown));
    }
    private static void addRecipe(Map<String,IdentityHashMap<Object,Recipe>> inventory,Recipe recipe) {
        Object rawName=recipe.row.get("recipe_map");require(rawName instanceof String,"Invalid preparation recipe map");String name=(String)rawName;
        IdentityHashMap<Object,Recipe> entries=inventory.get(name);
        if(entries==null){entries=new IdentityHashMap<Object,Recipe>();inventory.put(name,entries);}
        require(entries.put(recipe.identity,recipe)==null,"Repeated live recipe identity within one preparation map");
    }
    private static void require(boolean value,String message){if(!value)throw new IllegalStateException(message);}
}
