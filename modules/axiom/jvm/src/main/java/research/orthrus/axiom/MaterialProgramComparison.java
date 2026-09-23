package research.orthrus.axiom;

import java.util.*;

/** Pair fresh-worker observations; diagnostic transcripts remain unmodified. */
final class MaterialProgramComparison {
    private static final List<String> SEMANTIC_FIELDS=List.of("nativeOutcome","cleanObservation","nativeErrors","materials","lookups","prefixItems","materialBlocks","materialOres",
            "registeredMaterials","materialRegistries","customMetaItems","registrationEffects","recipeMaps","phase","lifecycle","contentProgress","deferredWork","scriptIndex","effectiveSide","physicalSide","executionCompleted","coverageGaps",
            "candidateAdmissionViolations","candidateResourceFailure","candidateLinkageFailure","nativeCompilationFailure");
    private MaterialProgramComparison() {}

    static Map<String,Object> compare(Map<String,Object> baseline,Map<String,Object> candidate) {
        var before=Json.object(baseline.get("result"));var after=Json.object(candidate.get("result"));
        var result=new LinkedHashMap<String,Object>();result.put("baseline",baseline);result.put("candidate",candidate);
        result.put("wholePackParity",false);result.put("groovyExecutionQualified",false);
        result.put("sourceComparison",sourceComparison(before,after));
        var reasons=new ArrayList<String>();
        for(String key:List.of("context","runtimeManifestSha256","contextPolicySha256","admissionPolicySha256")) {
            if(!before.containsKey(key)||!Objects.equals(before.get(key),after.get(key)))reasons.add("Different or unavailable "+key);
            else result.put(key,before.get(key));
        }
        if(!Objects.equals(baseline.get("engineId"),candidate.get("engineId")))reasons.add("Engine identities differ");
        if(!MaterialProgram.validSourceAcknowledgement(before.get("sourceProgram"))
                ||!MaterialProgram.validSourceAcknowledgement(after.get("sourceProgram")))
            reasons.add("Complete native source acknowledgement is unavailable or malformed");
        if(!before.containsKey("execution")||!after.containsKey("execution"))reasons.add("Native execution observation is unavailable");
        if(!reasons.isEmpty()) {
            result.put("comparison",Map.of("status","not-comparable","reasons",reasons));
            result.put("effectComparison",Map.of("schema","axiom.registration-effect-comparison.v1",
                    "status","not-comparable","reasons",List.copyOf(reasons)));
            return Engine.envelope("material-program","incomplete",result);
        }
        var left=projection(before);var right=projection(after);
        var changes=new ArrayList<Map<String,Object>>();
        for(String field:SEMANTIC_FIELDS)if(left.containsKey(field)!=right.containsKey(field)||!Objects.equals(left.get(field),right.get(field))) {
            String pointer=field.equals("nativeOutcome")?"/result/":"/result/execution/";
            changes.add(Map.of("field",field,"baselinePointer","/baseline"+pointer+field,"candidatePointer","/candidate"+pointer+field));
        }
        result.put("comparison",Map.of("status",changes.isEmpty()?"unchanged":"changed","changedSections",changes,
                "semanticFields",SEMANTIC_FIELDS,"baselineSemanticSha256",Json.digest(left),"candidateSemanticSha256",Json.digest(right),
                "baselineSourceSha256",Json.object(before.get("sourceProgram")).get("sha256"),
                "candidateSourceSha256",Json.object(after.get("sourceProgram")).get("sha256"),
                "meaning","observed-state-and-outcome-difference-not-source-causation","workers","separate-fresh-native-class-spaces"));
        var effects=new LinkedHashMap<>(MaterialRegistrationEffectComparison.compare(
                Json.object(before.get("execution")),Json.object(after.get("execution"))));
        effects.put("baselineSourceProgram",before.get("sourceProgram"));effects.put("candidateSourceProgram",after.get("sourceProgram"));
        result.put("effectComparison",effects);
        return Engine.envelope("material-program",Json.string(candidate.get("status")),result);
    }
    private static Map<String,Object> sourceComparison(Map<String,Object> before,Map<String,Object> after) {
        if(!MaterialProgram.validSourceAcknowledgement(before.get("sourceProgram"))
                ||!MaterialProgram.validSourceAcknowledgement(after.get("sourceProgram")))
            return Map.of("status","unavailable","meaning","Native source acknowledgement is unavailable; no source delta inferred");
        return Map.of("status","requires-retained-inventories","owner","core-verified-saved-inputs",
                "baselineSourceSha256",Json.object(before.get("sourceProgram")).get("sha256"),
                "candidateSourceSha256",Json.object(after.get("sourceProgram")).get("sha256"),
                "meaning","saved-file-difference-requires-complete-verified-retained-inventories");
    }
    private static Map<String,Object> projection(Map<String,Object> body) {
        var execution=NativeStageSnapshots.expandExecution(Json.object(body.get("execution")));
        var result=new LinkedHashMap<String,Object>();
        for(String field:SEMANTIC_FIELDS) {
            var source=field.equals("nativeOutcome")?body:execution;
            if(source.containsKey(field))result.put(field,source.get(field));
        }
        return result;
    }
}
