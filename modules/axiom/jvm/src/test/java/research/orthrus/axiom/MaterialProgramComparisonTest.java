package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialProgramComparisonTest {
    @Test void sourceDeltaRequiresCompleteCoreInventoriesEvenWhenNativeExecutionIsUnavailable() {
        var before=response(605,"");var after=response(605,"");
        var old=Json.object(before.get("result"));var current=Json.object(after.get("result"));
        old.put("sourceProgram",ack("a"));current.put("sourceProgram",ack("b"));
        old.remove("execution");current.remove("execution");
        var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        assertEquals("not-comparable",Json.object(result.get("comparison")).get("status"));
        var diff=Json.object(result.get("sourceComparison"));
        assertEquals("requires-retained-inventories",diff.get("status"));
        assertEquals("a".repeat(64),diff.get("baselineSourceSha256"));
        assertEquals("b".repeat(64),diff.get("candidateSourceSha256"));
        assertFalse(diff.containsKey("removed"));assertFalse(diff.containsKey("added"));assertFalse(diff.containsKey("modified"));
        assertEquals("core-verified-saved-inputs",diff.get("owner"));
    }
    private Map<String,Object> ack(String digit) {
        return MaterialProgram.sourceAcknowledgement(new MaterialProgram.Program(Map.of(),List.of(),digit.repeat(64),Map.of()));
    }
    private Map<String,Object> response(int count,String diagnostic) {
        var result=new LinkedHashMap<String,Object>();
        for(String key:List.of("context","runtimeManifestSha256","contextPolicySha256","admissionPolicySha256"))result.put(key,"same");
        result.put("sourceProgram",ack("a"));
        result.put("execution",Map.of("registeredMaterials",count,"diagnostics",List.of(diagnostic),"scriptIndex",List.of("first","second")));
        return new LinkedHashMap<>(Map.of("result",result,"status","incomplete","engineId","engine"));
    }
    @Test void configurationChangesRemainInputDeltasNotClaimedNativeEffects() {
        var before=response(605,"");var after=response(605,"");
        Json.object(before.get("result")).put("sourceProgram",ack("a"));
        Json.object(after.get("result")).put("sourceProgram",ack("b"));
        var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        var delta=Json.object(result.get("sourceComparison"));
        assertEquals("requires-retained-inventories",delta.get("status"));
        assertEquals("a".repeat(64),delta.get("baselineSourceSha256"));
        assertEquals("b".repeat(64),delta.get("candidateSourceSha256"));
        assertEquals("unchanged",Json.object(result.get("comparison")).get("status"));
        assertEquals("saved-file-difference-requires-complete-verified-retained-inventories",delta.get("meaning"));
    }
    @Test void missingOrMalformedAcknowledgementCannotBecomeAnEmptyOrComparableInventory() {
        for(Object invalid:List.of(Map.of("sha256","a".repeat(64),"files",List.of()),
                Map.of("schema",MaterialProgram.INVENTORY_SCHEMA,"sha256","a".repeat(64),"fileCount",true,
                        "inventoryEncoding",MaterialProgram.INVENTORY_ENCODING,"scope",MaterialProgram.INVENTORY_SCOPE))) {
            var before=response(605,"");var after=response(605,"");Json.object(after.get("result")).put("sourceProgram",invalid);
            var body=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
            assertEquals("unavailable",Json.object(body.get("sourceComparison")).get("status"));
            assertEquals("not-comparable",Json.object(body.get("comparison")).get("status"));
        }
    }
    @Test void comparesNativeStateWithoutDiscardingDifferentDiagnosticTranscripts() {
        var before=response(605,"original timestamp");var after=response(605,"later timestamp");
        var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        var comparison=Json.object(result.get("comparison"));assertEquals("unchanged",comparison.get("status"));
        assertEquals(before,result.get("baseline"));assertEquals(after,result.get("candidate"));
        assertEquals(comparison.get("baselineSemanticSha256"),comparison.get("candidateSemanticSha256"));
        var changed=Json.object(MaterialProgramComparison.compare(before,response(606,"third timestamp")).get("result"));
        assertEquals("changed",Json.object(changed.get("comparison")).get("status"));
    }
    @Test void nativeOrderIsNotNormalizedAndDifferentRuntimeCannotBeCompared() {
        var before=response(605,"");var after=response(605,"");
        Json.object(after.get("result")).put("execution",Map.of("registeredMaterials",605,"scriptIndex",List.of("second","first")));
        var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        assertEquals("changed",Json.object(result.get("comparison")).get("status"));
        Json.object(after.get("result")).put("runtimeManifestSha256","changed");
        result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        assertEquals("not-comparable",Json.object(result.get("comparison")).get("status"));
    }
    @Test void lifecycleOrderAndPendingWorkAreMeaningfulDifferences() {
        for(String field:List.of("lifecycle","deferredWork","contentProgress","recipeMaps","materialRegistries","customMetaItems","registrationEffects")) {
            var before=response(605,"");var after=response(605,"");
            Json.object(before.get("result")).put("execution",Map.of(field,List.of("first","second")));
            Json.object(after.get("result")).put("execution",Map.of(field,List.of("second","first")));
            var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
            var comparison=Json.object(result.get("comparison"));assertEquals("changed",comparison.get("status"));
            assertEquals(field,Json.object(Json.array(comparison.get("changedSections")).getFirst()).get("field"));
        }
    }
    @Test void nativeErrorOutcomeChangesEvenWhenRegistryStateDoesNot() {
        var before=response(605,"earlier native log timestamp");var after=response(605,"later native log timestamp");
        Json.object(before.get("result")).put("nativeOutcome","completed-without-observed-error");
        Json.object(after.get("result")).put("nativeOutcome","source-error");
        var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        var comparison=Json.object(result.get("comparison"));
        assertEquals("changed",comparison.get("status"));
        var change=Json.object(Json.array(comparison.get("changedSections")).getFirst());
        assertEquals("nativeOutcome",change.get("field"));
        assertEquals("/baseline/result/nativeOutcome",change.get("baselinePointer"));
        assertEquals("/candidate/result/nativeOutcome",change.get("candidatePointer"));
        assertEquals(before,result.get("baseline"));assertEquals(after,result.get("candidate"));
    }
    @Test void cleanObservationAndOrderedNativeErrorsAreSemanticFacts() {
        for(String field:List.of("cleanObservation","nativeErrors")) {
            var before=response(605,"");var after=response(605,"");
            Json.object(before.get("result")).put("execution",Map.of(field,field.equals("cleanObservation")?true:List.of("first error","second error")));
            Json.object(after.get("result")).put("execution",Map.of(field,field.equals("cleanObservation")?false:List.of("second error","first error")));
            var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
            var comparison=Json.object(result.get("comparison"));assertEquals("changed",comparison.get("status"));
            var change=Json.object(Json.array(comparison.get("changedSections")).getFirst());
            assertEquals(field,change.get("field"));assertEquals("/candidate/result/execution/"+field,change.get("candidatePointer"));
        }
    }
    @Test void sameNativeErrorsDoNotRewriteOrCompareTimestampedTranscripts() {
        var before=response(605,"timestamp one");var after=response(605,"timestamp two");
        for(var value:List.of(before,after)) {
            var body=Json.object(value.get("result"));body.put("nativeOutcome","source-error");
            var execution=new LinkedHashMap<>(Json.object(body.get("execution")));
            execution.put("cleanObservation",false);execution.put("nativeErrors",List.of("original native error"));body.put("execution",execution);
        }
        var result=Json.object(MaterialProgramComparison.compare(before,after).get("result"));
        assertEquals("unchanged",Json.object(result.get("comparison")).get("status"));
        assertEquals(before,result.get("baseline"));assertEquals(after,result.get("candidate"));
    }
    @Test void nativeFloatBitChangesSurviveJsonAndNumericZeroEquality() {
        var before=response(605,"");var after=response(605,"");
        for(var entry:Map.of("00000000",before,"80000000",after).entrySet()) {
            var scalar=Map.of("type","float32","value",0,"rawBits",entry.getKey());
            Json.object(entry.getValue().get("result")).put("execution",Map.of("materials",List.of(
                    Map.of("propertyValues",Map.of("tool",Map.of("toolSpeed",scalar))))));
        }
        var result=Json.object(MaterialProgramComparison.compare(Json.object(Json.parse(Json.write(before))),
                Json.object(Json.parse(Json.write(after)))).get("result"));
        var comparison=Json.object(result.get("comparison"));assertEquals("changed",comparison.get("status"));
        assertEquals("materials",Json.object(Json.array(comparison.get("changedSections")).getFirst()).get("field"));
        assertNotEquals(comparison.get("baselineSemanticSha256"),comparison.get("candidateSemanticSha256"));
    }
}
