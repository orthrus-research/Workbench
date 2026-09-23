package dev.workbench.crucible.forgerecipes;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Independent synthetic receivers exercise evidence equations, never initialize Minecraft. */
public final class ForgeOrdinaryItemMatchingTest {
    static class Stack {
        Map<String,Object> value;int writers,copies,initializations,initializedWriters;Boolean initialized=true;
        boolean drift,writerDrift,identityDrift,countDrift,deferCopies,initializationIdentityDrift,initializationCountDrift,initializationThrows,freshInitializationDrift;
        List<Stack> made=new ArrayList<Stack>();
        Stack(String name,int metadata,int damage,int count,Object tag,int writers) {
            value=row("registry_name",name,"metadata",metadata,"item_damage",damage,"count",count,"tag",tag);this.writers=writers;
        }
    }
    static final class Input {
        List<Map<String,Object>> targets;boolean eligible=true,custom,wrong,mutateNonmatch,mutateVisible;int calls;
        Input(Map<String,Object>... targets){this.targets=Arrays.asList(targets);}
    }
    static final class Access implements ForgeOrdinaryItemMatching.Access {
        public boolean eligibleClass(Object input){return ((Input)input).eligible;}
        public boolean customNbt(Object input){return ((Input)input).custom;}
        public List<Map<String,Object>> targets(Object input){return ((Input)input).targets;}
        public Object copy(Object original){
            Stack source=(Stack)original;source.copies++;
            Stack copy=new Stack("unused",0,0,1,null,source.writers);copy.value=new LinkedHashMap<String,Object>(source.value);
            copy.initialized=source.deferCopies?Boolean.FALSE:source.initialized;copy.initializedWriters=source.initializedWriters;
            copy.initializationIdentityDrift=source.initializationIdentityDrift || source.freshInitializationDrift && source.copies>1;copy.initializationCountDrift=source.initializationCountDrift;
            copy.initializationThrows=source.initializationThrows;source.made.add(copy);
            if(source.countDrift)copy.value.put("count",64);
            if(source.drift || source.identityDrift && source.copies>1)copy.value.put("metadata",999);
            if(source.writerDrift && source.copies>1)copy.writers++;
            return copy;
        }
        public Map<String,Object> describe(Object value){return new LinkedHashMap<String,Object>(((Stack)value).value);}
        public Boolean initialized(Object value){return ((Stack)value).initialized;}
        public void initialize(Object value){
            Stack stack=(Stack)value;stack.initializations++;stack.initialized=true;
            if(stack.initializationThrows)throw new IllegalStateException("native initializer callback failed");
            stack.writers=stack.initializedWriters;
            if(stack.initializationIdentityDrift)stack.value.put("tag",row("tag_id",10,"value",Collections.emptyMap()));
            if(stack.initializationCountDrift)stack.value.put("count",64);
        }
        public int writers(Object value){Stack stack=(Stack)value;if(!Boolean.TRUE.equals(stack.initialized))throw new AssertionError("Deferred writers read as zero");return stack.writers;}
        public boolean accepts(Object original,Object candidate){
            Input input=(Input)original;Stack stack=(Stack)candidate;input.calls++;
            if(input.wrong)return true;
            if(input.mutateVisible)stack.value.put("count",99);
            for(Map<String,Object> target:input.targets)if(target.get("registry_name").equals(stack.value.get("registry_name"))
                && target.get("metadata").equals(stack.value.get("metadata")) && stack.value.get("tag")==null) {
                    if(!Boolean.TRUE.equals(stack.initialized))initialize(stack);
                    return stack.writers==0;
                }
            if(input.mutateNonmatch && Boolean.FALSE.equals(stack.initialized))initialize(stack);
            return false;
        }
    }
    static Map<String,Object> target(String name,int metadata,Object tag,int writers){
        Map<String,Object> stack=row("registry_name",name,"metadata",metadata,"item_damage",metadata,"count",1,"tag",tag);
        return row("registry_name",name,"metadata",metadata,"tag",tag,"capability_writer_count",writers,
            "capability_initialization_state","initialized","stack_before_initialization",stack,"stack_after_initialization",stack);
    }
    static ForgeOrdinaryItemMatching.Recipe recipe(int index,List<Object> inputs,Stack... stacks){
        Map<String,Object> raw=row("recipe_map","fixture","semantic_sha256","fixture"+index,"duplicate_ordinal",0);
        List<ForgeOrdinaryItemMatching.Occurrence> values=new ArrayList<ForgeOrdinaryItemMatching.Occurrence>();
        for(int i=0;i<stacks.length;i++)values.add(new ForgeOrdinaryItemMatching.Occurrence(stacks[i],stacks[i].value,
            row("recipe_record_sha256",hash(raw),"pointer","/records/"+index+"/recipe/item_outputs/"+i+"/value")));
        return new ForgeOrdinaryItemMatching.Recipe(raw,inputs,values);
    }
    static List<Map<String,Object>> capture(ForgeOrdinaryItemMatching.Recipe... recipes){
        return ForgeOrdinaryItemMatching.capture(Arrays.asList(recipes),Collections.<String,Object>emptyMap(),Collections.<String,Object>emptyMap(),new Access());
    }
    static List<Map<String,Object>> selectors(List<Map<String,Object>> rows){
        List<Map<String,Object>> result=new ArrayList<Map<String,Object>>();
        for(Map<String,Object> row:rows)if(row.get("record_type").equals("gt-ordinary-item-matching-selector"))result.add(row);
        return result;
    }
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void refuses(Runnable work,String contains){try{work.run();throw new AssertionError("accepted "+contains);}catch(IllegalStateException refusal){check(refusal.getMessage().contains(contains),refusal.toString());}}
    public static void main(String[] args){
        Stack a=new Stack("fixture:a",7,1,2,null,0),b=new Stack("fixture:a",7,1,50,null,0);
        Stack damage=new Stack("fixture:a",7,99,1,null,0),foreign=new Stack("fixture:b",7,1,1,null,9);
        Stack tagged=new Stack("fixture:a",7,1,1,row("tag_id",10,"value",Collections.emptyMap()),9);
        Input same=new Input(target("fixture:a",7,null,0));
        ForgeOrdinaryItemMatching.Recipe first=recipe(0,Arrays.<Object>asList(same,same),a,b,damage,foreign,tagged);
        List<Map<String,Object>> rows=capture(first);List<Map<String,Object>> selected=selectors(rows);
        check(selected.size()==2 && same.calls==5,"All occurrences must execute once per live selector, including collapsed count variants");
        check(selected.get(0).get("accepted_domain_ordinals").equals(Arrays.asList(0,1)),"Metadata matches independently from item damage");
        check(selected.get(0).get("accepted_occurrence_ordinals").equals(Arrays.asList(0,1,2)),"Actual accepted occurrence vector");
        check(rows.get(0).get("domain_count").equals(4) && rows.get(0).get("occurrence_count").equals(5),"Separate domain and raw occurrence counts");
        List<Map<String,Object>> locators=new ArrayList<Map<String,Object>>();for(ForgeOrdinaryItemMatching.Occurrence occurrence:first.occurrences)locators.add(occurrence.locator);
        check(rows.get(0).get("occurrences_sha256").equals(hash(locators)),"Full ordered locator digest");
        check(rows.get(1).get("occurrence_count").equals(2) && rows.get(1).get("occurrences_sha256").equals(hash(locators.subList(0,2))),"Group digest retains every collapsed occurrence");
        check(((List<?>)rows.get(1).get("occurrences")).size()==2,"Copy records are not first occurrence only");

        Input contaminated=new Input(target("fixture:a",7,null,0));
        Stack bad=new Stack("fixture:a",7,1,2,null,1);
        check(selectors(capture(recipe(0,Arrays.<Object>asList(contaminated),a,bad))).isEmpty() && contaminated.calls==0,"Any matching occurrence writer makes this selector unsupported");
        for(Input unsupported:Arrays.asList(new Input(target("fixture:a",7,null,1)),new Input(target("fixture:a",7,row("tag_id",10),0))))
            check(selectors(capture(recipe(0,Arrays.<Object>asList(unsupported),a))).isEmpty(),"All stored target tags and writers matter");
        Input unknown=new Input(target("fixture:a",7,null,0));unknown.eligible=false;
        Input custom=new Input(target("fixture:a",7,null,0));custom.custom=true;
        check(selectors(capture(recipe(0,Arrays.<Object>asList(unknown,custom),a))).isEmpty(),"Unknown classes and custom NBT remain unsupported");

        Stack drift=new Stack("fixture:z",1,1,1,null,0);drift.drift=true;
        Input otherwise=new Input(target("fixture:a",7,null,0));
        check(selectors(capture(recipe(0,Arrays.<Object>asList(otherwise),a,drift))).isEmpty() && otherwise.calls==0,"Copy identity invariant is global, including nonmatching candidates");
        Stack countDrift=new Stack("fixture:a",7,1,2,null,0);countDrift.countDrift=true;
        check(selectors(capture(recipe(0,Arrays.<Object>asList(new Input(target("fixture:a",7,null,0))),countDrift))).isEmpty(),"Positive copied count2 to64 is not unchanged original occurrence");
        Stack changedWriters=new Stack("fixture:a",7,1,1,null,0);changedWriters.writerDrift=true;
        refuses(()->capture(recipe(0,Arrays.<Object>asList(new Input(target("fixture:a",7,null,0))),changedWriters)),"call copy differs");
        Stack changedIdentity=new Stack("fixture:a",7,1,1,null,0);changedIdentity.identityDrift=true;
        refuses(()->capture(recipe(0,Arrays.<Object>asList(new Input(target("fixture:a",7,null,0))),changedIdentity)),"call copy differs");
        Input disagreement=new Input(target("fixture:q",7,null,0));disagreement.wrong=true;
        refuses(()->capture(recipe(0,Arrays.<Object>asList(disagreement),a)),"disagrees");
        Input empty=new Input();check(selectors(capture(recipe(0,Arrays.<Object>asList(empty),a))).get(0).get("accepted_occurrence_ordinals").equals(Collections.emptyList()),"Complete empty target vector");
        check(ForgeOrdinaryItemMatching.writerArrayCount(new Object[0])==0 && ForgeOrdinaryItemMatching.writerArrayCount(new Object[2])==2,"Live serializable writer array cardinality");
        refuses(()->ForgeOrdinaryItemMatching.writerArrayCount(null),"not an array");
        StoredInput storedInput=new StoredInput();
        List<Map<String,Object>> internal=ForgeOrdinaryItemMatching.originalTargets(storedInput,new Access());
        check(internal.size()==2 && internal.get(0).get("metadata").equals(9) && internal.get(1).get("metadata").equals(2)
            && internal.get(0).get("registry_name").equals("fixture:internal")
            && internal.get(0).get("stack_before_initialization").equals(internal.get(0).get("stack_after_initialization"))
            && internal.get(0).get("capability_initialization_state").equals("initialized"),"Ordered stored fields plus independently observed target initialization");
        check(internal.get(1).get("capability_writer_count").equals(1),"Stored deferred target exposes its initialized writer rather than phantom zero");
        List<Map<String,Object>> beforeTargets=Arrays.asList(target("fixture:a",7,null,0));
        List<Map<String,Object>> afterTargets=Arrays.asList(target("fixture:a",7,null,1));
        ForgeOrdinaryItemMatching.requireStableTargets(first,0,same,beforeTargets,beforeTargets,3);
        try {ForgeOrdinaryItemMatching.requireStableTargets(first,1,same,beforeTargets,afterTargets,5001);throw new AssertionError("Target drift accepted");}
        catch(IllegalStateException failure) {
            String message=failure.getMessage();
            check(message.contains("\"recipe_map\":\"fixture\"") && message.contains("\"selector_ordinal\":1")
                && message.contains("\"completed_unique_selectors\":5001") && message.contains("\"targets_before\"")
                && message.contains("\"targets_after\"") && message.contains("\"changed_fields\":[\"capability_writer_count\"]")
                && message.contains("\"capability_writer_count\":0") && message.contains("\"capability_writer_count\":1"),"Exact before/after target drift context retained");
        }
        Stack deferred=new Stack("fixture:lazy",5,5,2,null,0);deferred.initialized=false;deferred.deferCopies=true;
        Stack unrelated=new Stack("fixture:other",1,1,3,null,0);unrelated.initialized=false;unrelated.deferCopies=true;
        Input lazy=new Input(target("fixture:lazy",5,null,0));
        ForgeOrdinaryItemMatching.Recipe lazyRecipe=recipe(1,Arrays.<Object>asList(lazy),deferred,unrelated);
        List<Map<String,Object>> lazyRows=capture(lazyRecipe);
        check(lazy.calls==2 && deferred.copies==2 && unrelated.copies==2,"Every original occurrence copied and predicate executed");
        check(deferred.initializations==1 && unrelated.initializations==1,"Every original occurrence resolves capabilities");
        check(deferred.made.get(0).initializations==1 && unrelated.made.get(0).initializations==1,"Every retained header copy resolves capabilities");
        check(deferred.made.get(1).initializations==1 && unrelated.made.get(1).initializations==0
            && Boolean.FALSE.equals(unrelated.made.get(1).initialized),"Only source-potential-matching fresh copies force initialization");
        check(lazyRows.get(0).get("native_copy_initialization_policy").equals(ForgeOrdinaryItemMatching.COPY_POLICY),"Copy initialization policy bound");
        check(lazyRows.equals(capture(lazyRecipe)),"Final initialized evidence is stable across two samples despite different initialization history");

        Stack unknownState=new Stack("fixture:unknown",0,0,1,null,0);unknownState.initialized=null;
        List<Map<String,Object>> unknownRows=capture(recipe(2,Arrays.<Object>asList(new Input(target("fixture:unknown",0,null,0))),unknownState));
        check(selectors(unknownRows).isEmpty(),"Unknown initialization cannot qualify");
        Map<?,?> unknownOccurrence=(Map<?,?>)((List<?>)unknownRows.get(1).get("occurrences")).get(0);
        check(unknownOccurrence.get("original_capability_writer_count")==null
            && unknownOccurrence.get("copy_capability_writer_count")==null,"Unknown does not assert zero writers");
        Stack materializedWriter=new Stack("fixture:provider",0,0,1,null,0);materializedWriter.initialized=false;materializedWriter.initializedWriters=1;
        check(selectors(capture(recipe(3,Arrays.<Object>asList(new Input(target("fixture:provider",0,null,0))),materializedWriter))).isEmpty(),"Deferred null becoming positive writers stays unsupported");
        Stack identityInit=new Stack("fixture:mutating",0,0,1,null,0);identityInit.initialized=false;identityInit.initializationIdentityDrift=true;
        refuses(()->capture(recipe(4,Collections.emptyList(),identityInit)),"Visible item identity or count changed");
        Stack countInit=new Stack("fixture:mutating",0,0,2,null,0);countInit.initialized=false;countInit.initializationCountDrift=true;
        refuses(()->capture(recipe(5,Collections.emptyList(),countInit)),"Visible item identity or count changed");
        Stack throwing=new Stack("fixture:throwing",0,0,1,null,0);throwing.initialized=false;throwing.initializationThrows=true;
        refuses(()->capture(recipe(6,Collections.emptyList(),throwing)),"native initializer callback failed");
        check(Boolean.TRUE.equals(throwing.initialized),"True flag after thrown callback does not produce completed evidence");
        Stack freshInit=new Stack("fixture:fresh",0,0,1,null,0);freshInit.deferCopies=true;freshInit.freshInitializationDrift=true;
        try {capture(recipe(9,Arrays.<Object>asList(new Input(target("fixture:fresh",0,null,0))),freshInit));throw new AssertionError("Fresh copy init mutation qualified");}
        catch(IllegalStateException failure) {
            check(failure.getMessage().contains("copy differs after capability initialization")
                && failure.getMessage().contains("candidate_locator") && failure.getMessage().contains("copied_stack")
                && failure.getMessage().contains("expected_stack"),"Fresh copied initialization mutation has exact occurrence context");
        }
        Stack badNonmatch=new Stack("fixture:unrelated",0,0,1,null,0);badNonmatch.deferCopies=true;
        Input mutator=new Input(target("fixture:absent",0,null,0));mutator.mutateNonmatch=true;
        refuses(()->capture(recipe(7,Arrays.<Object>asList(mutator),badNonmatch)),"Source-nonmatching native predicate changed");
        Input visibleMutator=new Input(target("fixture:absent",0,null,0));visibleMutator.mutateVisible=true;
        refuses(()->capture(recipe(8,Arrays.<Object>asList(visibleMutator),a)),"native predicate changed copied visible");
        System.out.println("Ordinary matching occurrence and refusal contracts passed; no native initialization performed");
    }
    public static final class RegisteredItem {public String getRegistryName(){return "fixture:internal";}}
    static final class StoredInput {final List<Object> itemList=Arrays.<Object>asList(new StoredItem());}
    static final class StoredItem {final Object item=new RegisteredItem();final List<Object> metaToTAGList=Arrays.<Object>asList(new StoredMeta(9),new StoredMeta(2));}
    static final class StoredMeta {final int meta;final List<Object> tagToStack;StoredMeta(int meta){this.meta=meta;tagToStack=Arrays.<Object>asList(new StoredTag(meta==2?1:0));}}
    static final class StoredTag {
        final Object tag=null;final Stack stack=new Stack("fixture:internal",0,0,1,null,0);
        StoredTag(int writers){stack.initialized=false;stack.initializedWriters=writers;}
    }
}
