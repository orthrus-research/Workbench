package dev.workbench.crucible.forgerecipes;

import java.lang.invoke.MethodHandle;
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Executed, occurrence-complete evidence for the original null-tag/zero-writer subset. */
final class ForgeOrdinaryItemMatching {
    static final String INPUT="gregtech.api.recipes.ingredients.GTRecipeItemInput";
    static final String DISPATCHER="net.minecraftforge.common.capabilities.CapabilityDispatcher";
    static final String DELAYER="zone.rong.loliasm.api.IItemStackCapabilityDelayer";
    static final String COPY_POLICY="initialize-source-potential-matches-otherwise-preserve-deferred-v1";
    private ForgeOrdinaryItemMatching() {}

    static final class Occurrence {
        final Object stack; final Map<String,Object> raw,locator;
        Occurrence(Object stack,Map<String,Object> raw,Map<String,Object> locator) {
            this.stack=stack;this.raw=raw;this.locator=locator;
        }
    }
    static final class Recipe {
        final Map<String,Object> raw; final List<Object> inputs; final List<Occurrence> occurrences;
        Recipe(Map<String,Object> raw,List<Object> inputs,List<Occurrence> occurrences) {
            this.raw=raw;this.inputs=inputs;this.occurrences=occurrences;
        }
    }
    /** Tests inject synthetic receivers; production uses only the original contracts below. */
    interface CopyCheck {boolean matches(Object copy);}
    interface Access {
        boolean eligibleClass(Object input);
        boolean customNbt(Object input);
        List<Map<String,Object>> targets(Object input);
        Object copy(Object stack);
        Map<String,Object> describe(Object stack);
        Boolean initialized(Object stack);
        void initialize(Object stack);
        int writers(Object stack);
        boolean accepts(Object input,Object copy);
        default CopyCheck compileCopyCheck(Map<String,Object> expected) {
            return copy->{Map<String,Object> current=describe(copy);return positive(current) && expected.equals(current);};
        }
    }
    static final class OriginalAccess implements Access {
        private final MethodHandle accepts=virtualContract(type(INPUT),"acceptsStack",boolean.class,type("net.minecraft.item.ItemStack"));
        public boolean eligibleClass(Object input) {return input.getClass().getName().equals(INPUT);}
        public boolean customNbt(Object input) {
            return Boolean.TRUE.equals(call(input,"hasNBTMatchingCondition")) || call(input,"getNBTMatcher")!=null || call(input,"getNBTMatchingCondition")!=null;
        }
        public List<Map<String,Object>> targets(Object input) {return originalTargets(input,this);}
        private final Class<?> stackType=type("net.minecraft.item.ItemStack"),delayerType=type(DELAYER);
        private final MethodHandle initialized=virtualContract(delayerType,"hasInitializedCapabilities",boolean.class);
        private final MethodHandle initialize=virtualContract(delayerType,"initializeCapabilities",void.class);
        private final MethodHandle copy=stackContract("copy","func_77946_l",stackType);
        private final MethodHandle empty=stackContract("isEmpty","func_190926_b",boolean.class);
        private final MethodHandle item=stackContract("getItem","func_77973_b",type("net.minecraft.item.Item"));
        private final MethodHandle count=stackContract("getCount","func_190916_E",int.class);
        private final MethodHandle damage=stackContract("getItemDamage","func_77952_i",int.class);
        private final MethodHandle metadata=stackContract("getMetadata","func_77960_j",int.class);
        private final MethodHandle tag=stackContract("getTagCompound","func_77978_p",type("net.minecraft.nbt.NBTTagCompound"));
        private final MethodHandle registryName=virtualContract(type("net.minecraftforge.registries.IForgeRegistryEntry"),"getRegistryName",type("net.minecraft.util.ResourceLocation"));
        private final Field capabilities=exactField(stackType,"capabilities"),writers=exactField(type(DISPATCHER),"writers");
        private MethodHandle stackContract(String mcp,String srg,Class<?> result) {
            try {return virtualContract(stackType,mcp,result);}
            catch(IllegalStateException absent) {
                if(!(absent.getCause() instanceof NoSuchMethodException))throw absent;
                return virtualContract(stackType,srg,result);
            }
        }
        private static Field exactField(Class<?> owner,String name) {
            try {Field result=owner.getDeclaredField(name);result.setAccessible(true);return result;}
            catch(ReflectiveOperationException failure){throw new IllegalStateException("Missing original field "+owner.getName()+"."+name,failure);}
        }
        public Object copy(Object stack) {
            try {return copy.invoke(stack);}
            catch(Throwable failure){throw new IllegalStateException("Original ItemStack.copy failed",failure);}
        }
        public Map<String,Object> describe(Object stack) {
            try {
                require(stack!=null && !(boolean)empty.invoke(stack),"Empty ordinary item occurrence");
                Object originalItem=item.invoke(stack),name=registryName.invoke(originalItem);
                require(name!=null,"Unregistered ordinary item occurrence");
                return row("count",(int)count.invoke(stack),"item_damage",(int)damage.invoke(stack),
                    "metadata",(int)metadata.invoke(stack),"registry_name",name.toString(),"tag",nbt(tag.invoke(stack)));
            } catch(Throwable failure){throw new IllegalStateException("Original ItemStack identity read failed",failure);}
        }
        public CopyCheck compileCopyCheck(Map<String,Object> expected) {
            CopyFields fields=new CopyFields(expected);
            return stack->{
                try {
                    if(stack==null || (boolean)empty.invoke(stack))return false;
                    Object name=registryName.invoke(item.invoke(stack));
                    return fields.matches((int)count.invoke(stack),(int)damage.invoke(stack),(int)metadata.invoke(stack),
                        name==null?null:name.toString(),tag.invoke(stack));
                } catch(Throwable failure){throw new IllegalStateException("Original copied item state verification failed",failure);}
            };
        }
        public Boolean initialized(Object stack) {
            if(!delayerType.isInstance(stack))return null;
            try {return (boolean)initialized.invoke(stack);}
            catch(Throwable failure){throw new IllegalStateException("Original lazy capability state read failed",failure);}
        }
        public void initialize(Object stack) {
            require(delayerType.isInstance(stack),"Original lazy capability interface is unavailable");
            try {initialize.invoke(stack);}
            catch(Throwable failure){throw new IllegalStateException("Original lazy capability initialization failed",failure);}
        }
        public int writers(Object stack) {
            require(Boolean.TRUE.equals(initialized(stack)),"Deferred or unknown capabilities have no observed writer count");
            try {Object dispatcher=capabilities.get(stack);return dispatcher==null?0:writerArrayCount(writers.get(dispatcher));}
            catch(IllegalAccessException failure){throw new IllegalStateException("Original live capability writer read failed",failure);}
        }
        public boolean accepts(Object input,Object copy) {
            try {return (boolean)accepts.invoke(input,copy);}
            catch(Throwable failure) {throw new IllegalStateException("Original GTRecipeItemInput.acceptsStack failed",failure);}
        }
    }
    /** Compile retained scalar fields once; every fresh copy still reads every matching field. */
    static final class CopyFields {
        final int count,damage,metadata;final String registryName;final ForgeNbtCheck.Check tag;
        CopyFields(Map<String,Object> expected) {
            count=((Number)expected.get("count")).intValue();damage=((Number)expected.get("item_damage")).intValue();
            metadata=((Number)expected.get("metadata")).intValue();registryName=(String)expected.get("registry_name");
            tag=ForgeNbtCheck.compile(expected.get("tag"));
        }
        boolean matches(int observedCount,int observedDamage,int observedMetadata,String observedName,Object observedTag) {
            return observedCount>0 && count==observedCount && damage==observedDamage && metadata==observedMetadata
                && registryName.equals(observedName) && tag.matches(observedTag);
        }
    }
    static int writerArrayCount(Object writers) {
        require(writers!=null && writers.getClass().isArray(),"Original capability writers are not an array");
        return Array.getLength(writers);
    }
    static final class StoredTarget {
        final Object stack;final Map<String,Object> fields;final String key;
        StoredTarget(Object stack,Map<String,Object> fields,String key){this.stack=stack;this.fields=fields;this.key=key;}
    }
    /** Observe internal ordered target references without initializing or reconstructing them. */
    static List<StoredTarget> originalStoredTargets(Object input) {
        List<StoredTarget> result=new ArrayList<StoredTarget>();int itemOrdinal=0;
        for(Object itemEntry:list(field(input,"itemList"))) {
            Object name=call(field(itemEntry,"item"),"getRegistryName");
            require(name!=null,"Unregistered stored ordinary item target");int metaOrdinal=0;
            for(Object metaEntry:list(field(itemEntry,"metaToTAGList"))) {
                Object metadata=field(metaEntry,"meta");require(metadata instanceof Integer,"Invalid stored ordinary target metadata");int tagOrdinal=0;
                for(Object tagEntry:list(field(metaEntry,"tagToStack"))) {
                    result.add(new StoredTarget(field(tagEntry,"stack"),row("registry_name",name.toString(),"metadata",metadata,
                        "tag",nbt(field(tagEntry,"tag")),"item_entry_ordinal",itemOrdinal,"metadata_entry_ordinal",metaOrdinal,
                        "tag_entry_ordinal",tagOrdinal),itemOrdinal+":"+metaOrdinal+":"+tagOrdinal));tagOrdinal++;
                }
                metaOrdinal++;
            }
            itemOrdinal++;
        }
        return result;
    }
    static List<Map<String,Object>> originalTargets(Object input,Access access) {
        List<Map<String,Object>> result=new ArrayList<Map<String,Object>>();
        for(StoredTarget target:originalStoredTargets(input)) {
            Initialized observed=observeInitialization(access,target.stack,"stored-target");
            result.add(row("registry_name",target.fields.get("registry_name"),"metadata",target.fields.get("metadata"),
                "tag",target.fields.get("tag"),"capability_writer_count",observed.writers,
                "stack_before_initialization",observed.before,"stack_after_initialization",observed.after,
                "capability_initialization_state",observed.state));
        }
        return result;
    }
    private static final class Initialized {
        final Map<String,Object> before,after;final String state;final Integer writers;
        Initialized(Map<String,Object> before,Map<String,Object> after,String state,Integer writers) {
            this.before=before;this.after=after;this.state=state;this.writers=writers;
        }
    }
    /** Invoke only the original initializer; a true flag after a thrown callback is never success. */
    private static Initialized observeInitialization(Access access,Object stack,String phase) {
        Map<String,Object> before=access.describe(stack);
        Boolean initialized=access.initialized(stack);
        if(Boolean.FALSE.equals(initialized)) {
            try {access.initialize(stack);}
            catch(RuntimeException failure) {
                throw new IllegalStateException(failure.getMessage()+"; initialization_context="+json(row("phase",phase,
                    "stack_before_initialization",before,"stack_after_failed_initialization",diagnosticStack(access,stack))),failure);
            }
            require(Boolean.TRUE.equals(access.initialized(stack)),"Original capability initialization returned without initialized state");
            initialized=Boolean.TRUE;
        }
        Map<String,Object> after=access.describe(stack);
        require(before.equals(after),"Visible item identity or count changed during capability initialization; context="
            +json(row("phase",phase,"stack_before_initialization",before,"stack_after_initialization",after)));
        Integer writers=Boolean.TRUE.equals(initialized)?access.writers(stack):null;
        require(writers==null || writers>=0,"Negative initialized capability writer count");
        return new Initialized(before,after,Boolean.TRUE.equals(initialized)?"initialized":"unknown",writers);
    }
    private static final class Candidate {
        final int ordinal; final List<Map<String,Object>> locators=new ArrayList<Map<String,Object>>();
        final List<Map<String,Object>> observations=new ArrayList<Map<String,Object>>();
        Candidate(int ordinal){this.ordinal=ordinal;}
    }
    private static final class Observed {
        final Occurrence source;final Candidate candidate;final Map<String,Object> copyIdentity;final Integer originalWriters,copyWriters;final CopyCheck check;final List<Object> key;final boolean untagged;
        Observed(Occurrence source,Candidate candidate,Map<String,Object> copyIdentity,Integer originalWriters,Integer copyWriters,CopyCheck check) {
            this.source=source;this.candidate=candidate;this.copyIdentity=copyIdentity;this.originalWriters=originalWriters;this.copyWriters=copyWriters;this.check=check;this.key=matchKey(copyIdentity);this.untagged=copyIdentity.get("tag")==null;
        }
    }
    private static final class Outcome {
        final List<Map<String,Object>> targets;
        final List<Integer> domains=new ArrayList<Integer>(),occurrences=new ArrayList<Integer>();
        Outcome(List<Map<String,Object>> targets){this.targets=targets;}
    }
    static List<Map<String,Object>> capture(List<Recipe> recipes,Map<String,Object> artifacts,Map<String,Object> classes) {
        return capture(recipes,artifacts,classes,new OriginalAccess());
    }
    static List<Map<String,Object>> capture(List<Recipe> recipes,Map<String,Object> artifacts,Map<String,Object> classes,Access access) {
        progress("candidate observation started; recipes="+recipes.size());
        Map<String,Candidate> candidates=new LinkedHashMap<String,Candidate>();
        List<Observed> observations=new ArrayList<Observed>();
        List<Map<String,Object>> domainLocators=new ArrayList<Map<String,Object>>(),allLocators=new ArrayList<Map<String,Object>>();
        Set<List<Object>> incompatibleKeys=new HashSet<List<Object>>();
        boolean invariant=true;
        for(Recipe recipe:recipes) for(Occurrence source:recipe.occurrences) {
            Map<String,Object> identity=identity(source.raw);
            if(!source.raw.equals(access.describe(source.stack)))throw contextualFailure(recipe,-1,null,source.locator,"candidate-observation",
                new IllegalStateException("Original item occurrence changed after recipe observation; expected="+json(source.raw)
                    +"; observed="+json(diagnosticStack(access,source.stack))));
            String digest=hash(identity);Candidate candidate=candidates.get(digest);
            if(candidate==null) {candidate=new Candidate(candidates.size());candidates.put(digest,candidate);domainLocators.add(source.locator);}
            Initialized original,copiedState;
            try {
                original=observeInitialization(access,source.stack,"original-occurrence");
                copiedState=observeInitialization(access,access.copy(source.stack),"retained-copy");
            } catch(RuntimeException failure){throw contextualFailure(recipe,-1,null,source.locator,"candidate-initialization",failure);}
            Map<String,Object> copied=copiedState.after;
            Integer originalWriters=original.writers,copyWriters=copiedState.writers;
            invariant &= original.state.equals("initialized") && copiedState.state.equals("initialized");
            Map<String,Object> copiedIdentity=identity(copied);
            invariant &= identity.equals(copiedIdentity) && positive(source.raw) && positive(copied) && source.raw.get("count").equals(copied.get("count"));
            if(identity.get("tag")==null && (!Integer.valueOf(0).equals(originalWriters) || !Integer.valueOf(0).equals(copyWriters)))incompatibleKeys.add(matchKey(identity));
            candidate.locators.add(source.locator);allLocators.add(source.locator);
            Map<String,Object> record=new LinkedHashMap<String,Object>(source.locator);
            record.put("copy_stack",copied);record.put("original_capability_writer_count",originalWriters);record.put("copy_capability_writer_count",copyWriters);
            record.put("original_initialized_stack",original.after);record.put("copy_before_initialization_stack",copiedState.before);
            record.put("original_capability_initialization_state",original.state);record.put("copy_capability_initialization_state",copiedState.state);
            candidate.observations.add(record);
            observations.add(new Observed(source,candidate,copiedIdentity,originalWriters,copyWriters,access.compileCopyCheck(source.raw)));
        }
        List<Map<String,Object>> result=new ArrayList<Map<String,Object>>();
        result.add(row("record_type","gt-ordinary-item-matching-domain",
            "model","gt-2.8.10-forge-1.12.2-null-tag-zero-writer-item-matching-v2",
            "native_copy_initialization_policy",COPY_POLICY,
            "domain_scope","captured-gt-input-output-item-variants","domain_sha256",hash(domainLocators),"domain_count",candidates.size(),
            "occurrence_count",allLocators.size(),"occurrences_sha256",hash(allLocators),"artifacts",artifacts,"class_sha256",classes));
        for(Candidate candidate:candidates.values()) result.add(row("record_type","gt-ordinary-item-matching-candidate",
            "domain_ordinal",candidate.ordinal,"occurrence_count",candidate.locators.size(),"occurrences_sha256",hash(candidate.locators),"occurrences",candidate.observations));
        progress("candidate observation complete; occurrences="+observations.size()+", candidates="+candidates.size()+", global_copy_identity_invariant="+invariant);
        if(!invariant)return result; // Retain observations; absent witnesses remain explicitly unknown.
        IdentityHashMap<Object,Outcome> memo=new IdentityHashMap<Object,Outcome>();
        IdentityHashMap<Object,Boolean> unsupported=new IdentityHashMap<Object,Boolean>();
        int selectors=0;
        progress("original ordinary matcher execution started");
        for(Recipe recipe:recipes) for(int ordinal=0;ordinal<recipe.inputs.size();ordinal++) {
            Object input=recipe.inputs.get(ordinal);
            if(!access.eligibleClass(input) || unsupported.containsKey(input) || access.customNbt(input))continue;
            Outcome outcome=memo.get(input);
            if(outcome==null) {
                List<Map<String,Object>> targets;
                try {targets=access.targets(input);}
                catch(RuntimeException failure){throw contextualFailure(recipe,ordinal,input,null,"target-initialization",failure);}
                Set<List<Object>> targetKeys=new HashSet<List<Object>>();
                boolean eligible=true;
                for(Map<String,Object> target:targets) {
                    List<Object> key=matchKey(target);
                    if(!"initialized".equals(target.get("capability_initialization_state")) || target.get("tag")!=null || !Integer.valueOf(0).equals(target.get("capability_writer_count")) || incompatibleKeys.contains(key))eligible=false;
                    targetKeys.add(key);
                }
                if(!eligible){unsupported.put(input,Boolean.TRUE);continue;}
                outcome=new Outcome(targets);Boolean[] acceptedGroups=new Boolean[candidates.size()];
                for(int index=0;index<observations.size();index++) {
                    Observed observed=observations.get(index);
                    boolean expected=observed.untagged && targetKeys.contains(observed.key),accepted;
                    Object copy=null;
                    try {
                        require(Boolean.TRUE.equals(access.initialized(observed.source.stack))
                            && access.writers(observed.source.stack)==observed.originalWriters,
                            "Original occurrence capability state changed before matcher execution");
                        copy=access.copy(observed.source.stack);
                        require(observed.check.matches(copy),"Ordinary matcher call copy differs from captured occurrence state");
                        Boolean before=access.initialized(copy);
                        Integer beforeWriters=null;
                        if(expected) {
                            if(Boolean.FALSE.equals(before))access.initialize(copy);
                            require(Boolean.TRUE.equals(access.initialized(copy)) && observed.check.matches(copy)
                                && access.writers(copy)==observed.copyWriters,
                                "Ordinary matcher call copy differs after capability initialization");
                        } else if(Boolean.TRUE.equals(before))beforeWriters=access.writers(copy);
                        // Every raw occurrence reaches the original predicate, including unrelated deferred copies.
                        accepted=access.accepts(input,copy);
                        require(observed.check.matches(copy),"Ordinary native predicate changed copied visible identity or count");
                        if(expected)require(Boolean.TRUE.equals(access.initialized(copy)) && access.writers(copy)==observed.copyWriters,
                            "Ordinary native predicate changed initialized copied writer state");
                        else require(java.util.Objects.equals(before,access.initialized(copy))
                            && (beforeWriters==null || access.writers(copy)==beforeWriters),
                            "Source-nonmatching native predicate changed copied capability state");
                        require(accepted==expected,"Original ordinary matcher disagrees with null-tag/zero-writer evidence");
                    } catch(RuntimeException failure) {
                        IllegalStateException detailed=new IllegalStateException(failure.getMessage()+"; candidate_context="+json(row(
                            "expected_stack",observed.source.raw,"copied_stack",copy==null?null:diagnosticStack(access,copy),
                            "targets_before",targets)),failure);
                        throw contextualFailure(recipe,ordinal,input,observed.source.locator,"all-occurrence-native-matching",detailed);
                    }
                    int group=observed.candidate.ordinal;
                    require(acceptedGroups[group]==null || acceptedGroups[group].booleanValue()==accepted,"Ordinary matching differs across collapsed item occurrences");
                    acceptedGroups[group]=accepted;
                    if(accepted)outcome.occurrences.add(index);
                }
                requireStableTargets(recipe,ordinal,input,targets,access.targets(input),memo.size());
                for(int group=0;group<acceptedGroups.length;group++)if(Boolean.TRUE.equals(acceptedGroups[group]))outcome.domains.add(group);
                memo.put(input,outcome);
                if(memo.size()%1000==0)progress("original ordinary matcher execution; unique_selectors="+memo.size()+", occurrences_per_selector="+observations.size());
            }
            result.add(row("record_type","gt-ordinary-item-matching-selector","recipe_map",recipe.raw.get("recipe_map"),
                "semantic_sha256",recipe.raw.get("semantic_sha256"),"duplicate_ordinal",recipe.raw.get("duplicate_ordinal"),
                "recipe_record_sha256",hash(recipe.raw),"selector_ordinal",ordinal,"targets",outcome.targets,
                "accepted_domain_ordinals",outcome.domains,"accepted_occurrence_ordinals",outcome.occurrences));selectors++;
        }
        progress("original ordinary matcher execution complete; unique_selectors="+memo.size()+", selector_records="+selectors+", unsupported_live_inputs="+unsupported.size());
        return result;
    }
    private static IllegalStateException contextualFailure(Recipe recipe,int selector,Object input,
            Map<String,Object> locator,String phase,RuntimeException failure) {
        return new IllegalStateException(failure.getMessage()+"; ordinary_context="+json(row(
            "recipe_map",recipe.raw.get("recipe_map"),"semantic_sha256",recipe.raw.get("semantic_sha256"),
            "duplicate_ordinal",recipe.raw.get("duplicate_ordinal"),"recipe_record_sha256",hash(recipe.raw),
            "selector_ordinal",selector,"input_runtime_class",input==null?null:input.getClass().getName(),
            "candidate_locator",locator,"phase",phase)),failure);
    }
    private static Object diagnosticStack(Access access,Object stack) {
        try {return access.describe(stack);}
        catch(RuntimeException failure){return row("observation_failed",failure.toString());}
    }
    private static String json(Object value){return new String(bytes(value),java.nio.charset.StandardCharsets.UTF_8);}
    static void requireStableTargets(Recipe recipe,int selector,Object input,List<Map<String,Object>> before,
            List<Map<String,Object>> after,int completedUniqueSelectors) {
        if(before.equals(after))return;
        List<Map<String,Object>> differences=new ArrayList<Map<String,Object>>();
        for(int index=0;index<Math.max(before.size(),after.size());index++) {
            Map<String,Object> left=index<before.size()?before.get(index):null,right=index<after.size()?after.get(index):null;
            if(java.util.Objects.equals(left,right))continue;
            java.util.Set<String> fields=new java.util.TreeSet<String>();
            if(left!=null)fields.addAll(left.keySet());if(right!=null)fields.addAll(right.keySet());
            List<String> changed=new ArrayList<String>();
            for(String key:fields)if(left==null || right==null || !java.util.Objects.equals(left.get(key),right.get(key)))changed.add(key);
            differences.add(row("target_ordinal",index,"before_present",left!=null,"after_present",right!=null,"changed_fields",changed));
        }
        Map<String,Object> context=row("recipe_map",recipe.raw.get("recipe_map"),"semantic_sha256",recipe.raw.get("semantic_sha256"),
            "duplicate_ordinal",recipe.raw.get("duplicate_ordinal"),"recipe_record_sha256",hash(recipe.raw),
            "selector_ordinal",selector,"input_runtime_class",input.getClass().getName(),
            "completed_unique_selectors",completedUniqueSelectors,"phase","after-all-occurrence-native-matching",
            "targets_before",before,"targets_after",after,"changed_targets",differences);
        throw new IllegalStateException("Original ordinary target fields changed during matcher execution; context="
            +new String(bytes(context),java.nio.charset.StandardCharsets.UTF_8));
    }
    private static Map<String,Object> identity(Map<String,Object> stack) {Map<String,Object> result=new LinkedHashMap<String,Object>(stack);result.remove("count");return result;}
    private static List<Object> matchKey(Map<String,Object> stack) {return java.util.Arrays.asList(stack.get("registry_name"),stack.get("metadata"));}
    private static boolean positive(Map<String,Object> stack) {return stack.get("count") instanceof Integer && ((Integer)stack.get("count"))>0;}
    private static void require(boolean condition,String message){if(!condition)throw new IllegalStateException(message);}
    private static void progress(String message){System.out.println("[Workbench Forge observer] "+message);}
}
