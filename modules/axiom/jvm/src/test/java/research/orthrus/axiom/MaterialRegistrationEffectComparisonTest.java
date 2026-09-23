package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Synthetic observation-contract fixtures; these do not qualify native execution. */
class MaterialRegistrationEffectComparisonTest {
    private static final String A="a".repeat(64), B="b".repeat(64);
    private Map<String,Object> domain(Map<String,Object> entries,String membership) {
        return new LinkedHashMap<>(Map.of("status","observed","inventoryComplete",true,"membership",membership,
                "stateScope","selected-native-values-v1","entries",entries));
    }
    private Map<String,Object> execution() {
        var effects=new LinkedHashMap<String,Object>();effects.put("schema","axiom.native-registration-effects.v1");effects.put("phase","FROZEN");
        for(String name:List.of("materials","customItems","fluids"))effects.put(name,domain(new LinkedHashMap<>(Map.of("susy:example",A)),name));
        return new LinkedHashMap<>(Map.of("registrationEffects",effects));
    }
    private Map<String,Object> inventory(Map<String,Object> execution,String domain) {return Json.object(Json.object(execution.get("registrationEffects")).get(domain));}
    private Map<String,Object> delta(Map<String,Object> before,Map<String,Object> after,String domain) {
        return Json.object(Json.object(MaterialRegistrationEffectComparison.compare(before,after).get("domains")).get(domain));
    }
    @Test void generatedCollectionsCompareAcrossProductionEncodingAndRetainMissingInventory() {
        for(String family:List.of("prefixItems","materialBlocks","oreBlocks")) {
            var before=execution();var after=execution();
            Json.object(before.get("registrationEffects")).put(family,domain(Map.of("gregtech:form",A),family));
            Json.object(after.get("registrationEffects")).put(family,domain(Map.of("gregtech:form",B),family));
            for(var execution:List.of(before,after)) {
                execution.put("diagnostics",List.of());
                execution.put("nativeInitialization",Map.of("registrationEffects",execution.get("registrationEffects")));
            }
            var encodedBefore=NativeStageSnapshots.compactExecution(before);
            var encodedAfter=NativeStageSnapshots.compactExecution(after);
            String retained=Json.write(encodedAfter);
            var changed=delta(encodedBefore,encodedAfter,family);
            assertEquals("changed",changed.get("status"));
            var row=Json.object(Json.array(changed.get("modified")).getFirst());
            assertEquals(A,row.get("baselineStateSha256"));assertEquals(B,row.get("candidateStateSha256"));
            assertEquals("/candidate/result/execution/registrationEffects/"+family+"/entries/gregtech:form",row.get("candidatePointer"));
            assertEquals(retained,Json.write(encodedAfter));
            assertEquals("not-comparable",delta(encodedBefore,execution(),family).get("status"));
        }
    }
    @Test void sameCountReplacementReportsExactAdditionAndRemovalInEveryDomain() {
        for(String name:List.of("materials","customItems","fluids")) {
            var before=execution();var after=execution();inventory(after,name).put("entries",Map.of("susy:replacement",A));
            var delta=delta(before,after,name);assertEquals("changed",delta.get("status"));
            assertEquals("susy:example",Json.object(Json.array(delta.get("removed")).getFirst()).get("identity"));
            assertEquals("susy:replacement",Json.object(Json.array(delta.get("added")).getFirst()).get("identity"));
            assertEquals(List.of(),delta.get("modified"));
        }
    }
    @Test void sameIdentityStateChangesIncludeEscapedExactEvidencePointers() {
        var before=execution();var after=execution();
        inventory(before,"materials").put("entries",Map.of("susy:path/with~suffix",A));
        inventory(after,"materials").put("entries",Map.of("susy:path/with~suffix",B));
        var row=Json.object(Json.array(delta(before,after,"materials").get("modified")).getFirst());
        assertEquals(A,row.get("baselineStateSha256"));assertEquals(B,row.get("candidateStateSha256"));
        assertEquals("/candidate/result/execution/registrationEffects/materials/entries/susy:path~1with~0suffix",row.get("candidatePointer"));
    }
    @Test void absentPartialMalformedOrDifferentScopeInventoryCannotProveDeletion() {
        for(Object invalid:List.of(Map.of(),Map.of("status","unavailable"),
                Map.of("status","observed","inventoryComplete",false,"entries",Map.of()),
                domain(Map.of("susy:example","not-a-digest"),"materials"),
                domain(Map.of(),"different-membership"))) {
            var before=execution();var after=execution();Json.object(after.get("registrationEffects")).put("materials",invalid);
            var result=MaterialRegistrationEffectComparison.compare(before,after);
            assertEquals("incomplete",result.get("status"));
            var delta=delta(before,after,"materials");assertEquals("not-comparable",delta.get("status"));assertFalse(delta.containsKey("removed"));
        }
    }
    @Test void missingWholeInventoryOrDifferentPhaseIsNotAnEmptyRegistry() {
        var before=execution();var after=execution();Json.object(after.get("registrationEffects")).put("phase","CLOSED");
        assertEquals("not-comparable",delta(before,after,"materials").get("status"));
        assertEquals("incomplete",MaterialRegistrationEffectComparison.compare(before,Map.of()).get("status"));
    }
    @Test void currentMembershipCompletenessDoesNotClaimCompletedInitializationOrRecipes() {
        var before=execution();var after=execution();before.put("executionCompleted",false);after.put("executionCompleted",false);
        var result=MaterialRegistrationEffectComparison.compare(before,after);
        assertEquals("unchanged",result.get("status"));assertEquals(false,result.get("recipeEffectsChecked"));
        assertTrue(result.get("meaning").toString().contains("not-source-causation-or-initialization-completion"));
    }
    @Test void orderOfIdentityMapIsNotSemanticButNativeStateDigestIs() {
        var before=execution();var after=execution();
        var left=new LinkedHashMap<String,Object>();left.put("susy:first",A);left.put("susy:second",B);
        var right=new LinkedHashMap<String,Object>();right.put("susy:second",B);right.put("susy:first",A);
        inventory(before,"materials").put("entries",left);inventory(after,"materials").put("entries",right);
        String original=Json.write(before);
        assertEquals("unchanged",MaterialRegistrationEffectComparison.compare(before,after).get("status"));
        assertEquals(original,Json.write(before));
    }
    private Map<String,Object> variant(int meta,String name,int size) {
        return Map.of("meta",meta,"name",name,"maxStackSize",size,"ownerIdentity",true,"nameLookupIdentity",true);
    }
    private void custom(Map<String,Object> execution,List<Map<String,Object>> variants,boolean registered) {
        Json.object(execution.get("registrationEffects")).put("customItems",Map.of("status","reference","sourcePointer","/execution/customMetaItems"));
        execution.put("customMetaItems",Map.of("status","observed","scope","declared-custom-meta-items-not-generated-content",
                "items",List.of(Map.of("registryName","gregtech:meta_item_2","offset",2,"forgeRegistered",registered,"variants",variants))));
    }
    @Test void customDefinitionsUseOwnerAndMetaNotVariantCountOrARegisteredClaim() {
        var before=execution();var after=execution();custom(before,List.of(variant(1,"rock",64)),false);custom(after,List.of(variant(2,"dust",64)),false);
        var delta=delta(before,after,"customItems");assertEquals("changed",delta.get("status"));
        assertEquals("native-custom-meta-item-variant-definition",delta.get("membership"));
        var added=Json.object(Json.array(delta.get("added")).getFirst());assertEquals("gregtech:meta_item_2#2",added.get("identity"));
        assertEquals("/candidate/result/execution/customMetaItems/items/0/variants/0",added.get("candidatePointer"));
    }
    @Test void customNativePropertyNameAndOwnerRegistrationChangesAreVisibleAtSameIdentity() {
        for(int change=0;change<3;change++) {
            var before=execution();var after=execution();custom(before,List.of(variant(1,"rock",64)),false);
            custom(after,List.of(variant(1,change==0?"renamed":"rock",change==1?16:64)),change==2);
            assertEquals(1,Json.array(delta(before,after,"customItems").get("modified")).size());
        }
    }
    @Test void duplicateCustomNativeIdentityOrUnavailableObservationRefusesComparison() {
        var before=execution();var after=execution();custom(before,List.of(variant(1,"rock",64)),false);
        custom(after,List.of(variant(1,"rock",64),variant(1,"other",16)),false);
        assertEquals("not-comparable",delta(before,after,"customItems").get("status"));
        after.put("customMetaItems",Map.of("status","not-observed"));
        assertEquals("not-comparable",delta(before,after,"customItems").get("status"));
    }
    @Test void nativeDuplicateNameFalseLookupIdentityRemainsObservedState() {
        var before=execution();var after=execution();custom(before,List.of(variant(1,"probe",64)),false);
        var shadowed=new LinkedHashMap<>(variant(1,"probe",64));shadowed.put("nameLookupIdentity",false);
        custom(after,List.of(shadowed,variant(2,"probe",64)),false);
        var delta=delta(before,after,"customItems");assertEquals("changed",delta.get("status"));
        assertEquals("gregtech:meta_item_2#2",Json.object(Json.array(delta.get("added")).getFirst()).get("identity"));
        assertEquals("gregtech:meta_item_2#1",Json.object(Json.array(delta.get("modified")).getFirst()).get("identity"));
    }
    @Test void fluidBuilderQueueChangesDoNotBecomeRegisteredFluidEffects() {
        var before=execution();var after=execution();
        before.put("deferredWork",Map.of("fluids",List.of("old queued builder")));
        after.put("deferredWork",Map.of("fluids",List.of("new queued builder")));
        assertEquals("unchanged",delta(before,after,"fluids").get("status"));
    }
}
