package dev.workbench.crucible.forgerecipes;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** The phase records changed objects, never certifies before and after as one resource. */
public final class ForgeCapabilityPreparationTest {
    static final class Stack {
        Map<String,Object> value;Boolean initialized=false;int calls;Runnable callback;
        Stack(String name){value=row("registry_name",name,"count",1,"metadata",0,"item_damage",0,"tag",null);}
    }
    static final class Access implements ForgeOrdinaryItemMatching.Access {
        public boolean eligibleClass(Object input){return false;}
        public boolean customNbt(Object input){return false;}
        public List<Map<String,Object>> targets(Object input){throw new AssertionError("Matcher targets invoked during preparation");}
        public Object copy(Object stack){throw new AssertionError("Preparation copied an original");}
        public Map<String,Object> describe(Object stack){return new LinkedHashMap<String,Object>(((Stack)stack).value);}
        public Boolean initialized(Object stack){return ((Stack)stack).initialized;}
        public void initialize(Object value){Stack stack=(Stack)value;stack.calls++;stack.initialized=true;if(stack.callback!=null)stack.callback.run();}
        public int writers(Object stack){throw new AssertionError("Preparation asserted a writer count");}
        public boolean accepts(Object input,Object copy){throw new AssertionError("Preparation invoked matching");}
    }
    static class Source implements ForgeCapabilityPreparation.Source {
        final Object recipe=new Object();final List<Stack> stacks;int reads;
        boolean foreignIdentity,reorder,changeInventory,multiMap,changeStateOnSecondRead;
        Source(Stack... stacks){this.stacks=Arrays.asList(stacks);}
        public List<ForgeCapabilityPreparation.Recipe> read(){
            reads++;if(changeStateOnSecondRead && reads==2)stacks.get(0).initialized=false;
            List<ForgeCapabilityPreparation.Recipe> recipes=new ArrayList<ForgeCapabilityPreparation.Recipe>();
            for(String map:multiMap?Arrays.asList("a","b"):Arrays.asList("a")) {
                List<Map<String,Object>> output=new ArrayList<Map<String,Object>>();
                List<Stack> selected=new ArrayList<Stack>(stacks);if(reorder && reads==2)Collections.reverse(selected);
                if(changeInventory && reads==2)selected.remove(selected.size()-1);
                if(foreignIdentity && reads==2){Stack old=selected.get(0),other=new Stack("foreign");other.value=old.value;other.initialized=old.initialized;selected.set(0,other);}
                for(Stack stack:selected)output.add(new Access().describe(stack));
                Map<String,Object> raw=row("recipe_map",map,"recipe",row("representatives",output));
                List<ForgeCapabilityPreparation.Reference> refs=new ArrayList<ForgeCapabilityPreparation.Reference>();
                for(int index=0;index<selected.size();index++)refs.add(new ForgeCapabilityPreparation.Reference(selected.get(index),"recipe-item-occurrence","input:0",
                    row("recipe_record_sha256",hash(raw),"pointer","/records/"+recipes.size()+"/recipe/representatives/"+index)));
                recipes.add(new ForgeCapabilityPreparation.Recipe(recipe,raw,refs));
            }
            return recipes;
        }
    }
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void refuses(Runnable work,String text){try{work.run();throw new AssertionError("Accepted "+text);}catch(IllegalStateException failure){check(failure.getMessage().contains(text),failure.toString());}}
    static List<Map<String,Object>> objects(Map<String,Object> result){return (List<Map<String,Object>>)result.get("objects");}
    public static void main(String[] args) {
        Stack changed=new Stack("fixture:glasses"),untouched=new Stack("fixture:plain");
        changed.callback=()->changed.value.put("tag",row("tag_id",10,"value",row("UUIDMost",row("tag_id",4,"value",123L))));
        Source source=new Source(changed,untouched,changed);source.reorder=true;source.multiMap=true;
        Map<String,Object> result=ForgeCapabilityPreparation.prepare(source,new Access());
        Map<?,?> counts=(Map<?,?>)result.get("counts");
        check(source.reads==2 && changed.calls==1 && untouched.calls==1,"One initializer per live object and exactly one before/after inventory read");
        check(counts.get("recipe_count").equals(2) && counts.get("object_count").equals(2)
            && counts.get("reference_count").equals(6) && counts.get("changed_object_count").equals(1),"Shared live recipe across maps and repeated stack references retained");
        Map<String,Object> object=objects(result).get(0);
        check(((Map<?,?>)object.get("stack_before")).get("tag")==null && ((Map<?,?>)object.get("stack_after")).get("tag")!=null,"Changed identity preserved explicitly");
        check(((List<?>)object.get("references")).size()==4,"No shared-reference collapse");
        for(Map<String,Object> binding:(List<Map<String,Object>>)result.get("recipe_bindings"))
            check(!binding.get("before_record_sha256").equals(binding.get("after_record_sha256")),"Recipe identities recomputed after preparation");

        Stack first=new Stack("fixture:first"),indirect=new Stack("fixture:indirect");
        first.callback=()->{indirect.value.put("count",2);indirect.initialized=true;};
        Map<String,Object> indirectResult=ForgeCapabilityPreparation.prepare(new Source(first,indirect),new Access());
        Map<String,Object> indirectRow=objects(indirectResult).get(1);
        check(indirect.calls==0 && indirectRow.get("initialization_state_before").equals("deferred")
            && indirectRow.get("initialization_state_before_initializer").equals("initialized")
            && Boolean.FALSE.equals(indirectRow.get("initializer_invoked")),"Indirect earlier callbacks are not attributed to an unperformed initializer");
        check(((Map<?,?>)indirectRow.get("stack_before")).get("count").equals(1)
            && ((Map<?,?>)indirectRow.get("stack_before_initializer")).get("count").equals(2),"Global and local transitions remain distinct");
        Stack unknown=new Stack("fixture:unknown");unknown.initialized=null;
        Map<String,Object> unknownResult=ForgeCapabilityPreparation.prepare(new Source(unknown),new Access());
        check(unknown.calls==0 && ((Map<?,?>)unknownResult.get("counts")).get("unknown_object_count").equals(1),"Unknown preparation remains observed unknown");
        Stack throwing=new Stack("fixture:throws");throwing.callback=()->{throw new IllegalStateException("original callback");};
        refuses(()->ForgeCapabilityPreparation.prepare(new Source(throwing),new Access()),"One-shot original capability preparation failed");
        check(throwing.calls==1,"Failed initialization is never retried even if flag became true");
        Source replaced=new Source(new Stack("fixture:replaced"));replaced.foreignIdentity=true;
        refuses(()->ForgeCapabilityPreparation.prepare(replaced,new Access()),"Original stack or slot reference changed");
        Source removed=new Source(new Stack("fixture:removed"));removed.changeInventory=true;
        refuses(()->ForgeCapabilityPreparation.prepare(removed,new Access()),"reference inventory changed");
        Source stateChangingRead=new Source(new Stack("fixture:reader"));stateChangingRead.changeStateOnSecondRead=true;
        refuses(()->ForgeCapabilityPreparation.prepare(stateChangingRead,new Access()),"Source observation changed prepared item state");
        Stack lostState=new Stack("fixture:lost");lostState.callback=()->lostState.initialized=false;
        refuses(()->ForgeCapabilityPreparation.prepare(new Source(lostState),new Access()),"returned without initialized state");
        System.out.println("One-shot capability preparation transition, reference and refusal contracts passed; no native initialization performed");
    }
}
