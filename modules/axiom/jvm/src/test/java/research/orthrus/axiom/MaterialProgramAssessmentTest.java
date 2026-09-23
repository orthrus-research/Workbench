package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Decision-policy fixtures, not native material qualification. */
class MaterialProgramAssessmentTest {
    @Test void scopedOutcomeDoesNotPromoteCleanButUnqualifiedCheckpoint() {
        var result=run(execution(),List.of());
        var initialization=Json.object(body(result).get("initialization"));
        assertEquals("incomplete",initialization.get("status"));
        assertEquals("completed",initialization.get("executionCheckpoint"));
        assertEquals(false,initialization.get("wholePackValidity"));
        assertTrue(Json.array(initialization.get("blockingEvidencePointers")).contains("/qualification"));
    }
    @Test void nativeFailureIsDistinctFromContextGapAndPreNativeStructuralError() {
        var execution=execution();execution.put("nativeErrors",List.of("Native rejection"));execution.put("cleanObservation",false);
        var failed=Json.object(body(run(execution,List.of())).get("initialization"));
        assertEquals("native-failed",failed.get("status"));assertEquals(true,failed.get("nativeErrorObserved"));
        execution.put("coverageGaps",List.of("missing required initialization"));
        var incomplete=Json.object(body(run(execution,List.of())).get("initialization"));
        assertEquals("incomplete",incomplete.get("status"));assertEquals(true,incomplete.get("nativeErrorObserved"));
        var structure=new MaterialSourceAdmission.Result(Map.of(),Set.of(),List.of(new MaterialSourceAdmission.Finding("groovy/Test.groovy","source.syntax","bad",1,1)));
        var notRun=MaterialProgramAssessment.finish(new LinkedHashMap<>(),structure,List.of());
        assertEquals("incomplete",Json.object(body(notRun).get("initialization")).get("status"));
    }
    @Test void producerSuccessFlagCannotWaiveSavedConfigurationOrDeferredLoaderGuards() {
        var execution=execution();execution.put("initializationQualified",true);
        for(var scope:List.of(Map.<String,Object>of("configuration",Map.of("fileCount",1)),Map.<String,Object>of("deferredLoaders",List.of("postInit")))) {
            var input=new LinkedHashMap<String,Object>();input.put("execution",execution);input.put("sourceScope",scope);
            input.put("initialization",Map.of("status","completed","qualified",true));
            var result=MaterialProgramAssessment.finish(input,ADMITTED,List.of());
            assertEquals("incomplete",Json.object(body(result).get("initialization")).get("status"));
            assertEquals("incomplete",result.get("status"));
        }
    }
    @Test void earlyNativePlatformCauseIsObservedWithoutPromotingOrBlamingSource() {
        var original=List.of(Map.<String,Object>of("type","java.lang.IllegalStateException","message","Original native early failure"));
        var input=new LinkedHashMap<String,Object>();input.put("bootstrap",Map.of("admitted",false,"compilerInvoked",false,
                "platformInitialization",Map.of("status","threw","causes",original)));
        var result=MaterialProgramAssessment.finish(input,ADMITTED,List.of());
        var initialization=Json.object(body(result).get("initialization"));
        assertEquals("incomplete",result.get("status"));assertEquals("incomplete",initialization.get("status"));
        assertEquals("incomplete",initialization.get("coverage"));assertEquals("not-run",initialization.get("executionCheckpoint"));
        assertEquals(true,initialization.get("nativeErrorObserved"));assertEquals(false,initialization.get("wholePackValidity"));
        assertTrue(codes(result).containsAll(List.of("native-bootstrap-incomplete","native-platform-error")));
        assertEquals(original,Json.object(Json.object(body(result).get("bootstrap")).get("platformInitialization")).get("causes"));
        assertFalse(body(result).containsKey("execution"));
    }
    private static final MaterialSourceAdmission.Result ADMITTED=new MaterialSourceAdmission.Result(Map.of(),Set.of(),List.of());
    private static final String NAME="supersymmetry:example";
    @Test void originalPreInitEffectsUseTheNormalExecutionReferenceWithoutMutatingNativeEvidence() {
        var nativeState=Map.<String,Object>of("registrationEffects",Map.of("customItems",
                Map.of("status","reference","sourcePointer","/result/customMetaItems")),
                "customMetaItems",Map.of("status","observed","items",List.of()));
        String retained=Json.write(nativeState);
        var execution=MaterialProgram.originalInitializationResult(nativeState);
        assertEquals("/execution/customMetaItems",Json.object(Json.object(execution.get("registrationEffects")).get("customItems")).get("sourcePointer"));
        assertEquals(execution,NativeStageSnapshots.expandExecution(NativeStageSnapshots.compactExecution(execution)));
        assertEquals(retained,Json.write(nativeState));
    }
    @Test void originalGroovyReturnCannotStandInForMaterialCompletionAndErrorsRemainVisible() {
        var nativeState=new LinkedHashMap<String,Object>();
        nativeState.put("groovyInitializationReady",true);nativeState.put("candidateCompilationStarted",true);
        nativeState.put("nativeGroovyErrors",List.of());
        var original=MaterialProgram.originalInitializationResult(nativeState);
        var pending=run(original,List.of(registration(true)));
        assertEquals("incomplete",pending.get("status"));
        assertEquals("stopped",assessment(pending).get("execution"));
        assertEquals("not-evaluated",Json.object(Json.array(Json.object(body(pending).get("expectations")).get("checks")).getFirst()).get("status"));
        var diagnostic=Map.of("severity","error","channel","groovy-log","message","Original file-only error",
                "locations",List.of(Map.of("path","groovy/preInit/Example.groovy","line",2)));
        nativeState.put("groovyDiagnostics",List.of(diagnostic));nativeState.put("nativeGroovyErrors",List.of("Original file-only error"));
        var failed=run(MaterialProgram.originalInitializationResult(nativeState),List.of());
        assertEquals("incomplete",failed.get("status"));
        assertEquals(true,Json.object(body(failed).get("initialization")).get("nativeErrorObserved"));
        assertTrue(Json.array(Json.object(body(failed).get("execution")).get("diagnostics")).contains(diagnostic));
    }
    private Map<String,Object> execution() {
        return new LinkedHashMap<>(Map.of("coverageGaps",List.of(),"candidateAdmissionViolations",List.of(),
                "scriptIndex",List.of(),"nativeErrors",List.of(),"diagnostics",List.of(),"cleanObservation",true,
                "executionCompleted",true,"materials",List.of(Map.of("name",NAME,"registryIdentity",true)),
                "lookups",List.of(),"vocabulary",Map.of("properties",List.of(),"flags",List.of())));
    }
    private Map<String,Object> registration(boolean expected) {
        return Map.of("id","registered","material",NAME,"kind","registration","equals",expected);
    }
    private Map<String,Object> run(Map<String,Object> execution,List<Map<String,Object>> checks) {
        var body=new LinkedHashMap<String,Object>();body.put("execution",execution);
        body.put("qualification","pending-native-program-acceptance");
        return MaterialProgramAssessment.finish(body,ADMITTED,MaterialExpectations.parse(checks));
    }
    private Map<String,Object> body(Map<String,Object> envelope) {return Json.object(envelope.get("result"));}
    private Map<String,Object> assessment(Map<String,Object> envelope) {return Json.object(body(envelope).get("assessment"));}
    private List<Object> codes(Map<String,Object> envelope) {
        return Json.array(assessment(envelope).get("reasons")).stream().map(Json::object).map(row->row.get("code")).toList();
    }
    @Test void matchedProgramExplainsPendingQualificationWithoutInventingCoverageLoss() {
        var result=run(execution(),List.of(registration(true)));
        assertEquals("incomplete",result.get("status"));
        assertEquals("completed-without-observed-error",body(result).get("nativeOutcome"));
        assertEquals("completed",assessment(result).get("execution"));
        assertEquals("no-observed-gaps",assessment(result).get("coverage"));
        assertEquals(false,assessment(result).get("qualifiedValidity"));
        assertEquals(List.of("qualification-pending"),codes(result));
        assertEquals(Map.of("matched",1L,"mismatch",0L,"unsupported",0L,"not-evaluated",0L),assessment(result).get("intentCounts"));
    }
    @Test void diagnosticsWithoutExpectationsNeverRequireInventedMaterialIntent() {
        var execution=execution();
        var clean=run(execution,List.of());
        assertEquals("not-requested",Json.object(body(clean).get("expectations")).get("status"));
        assertEquals(List.of("qualification-pending"),codes(clean));
        assertEquals("incomplete",clean.get("status"));
        execution.put("nativeErrors",List.of("Native logged failure"));
        execution.put("cleanObservation",false);
        var failed=run(execution,List.of());
        assertEquals("source-error",failed.get("status"));
        assertEquals("not-requested",Json.object(body(failed).get("expectations")).get("status"));
        assertEquals(List.of("Native logged failure"),Json.object(body(failed).get("execution")).get("nativeErrors"));
        assertEquals(false,assessment(failed).get("qualifiedValidity"));
    }
    @Test void deferredRecipesAreUnvalidatedEvenWhenSelectedMaterialPhaseIsClean() {
        var input=new LinkedHashMap<String,Object>();input.put("execution",execution());
        input.put("sourceScope",Map.of("selectedLoader","preInit","deferredLoaders",List.of("postInit")));
        var result=MaterialProgramAssessment.finish(input,ADMITTED,List.of());
        assertEquals("completed-without-observed-error",body(result).get("nativeOutcome"));
        assertEquals("incomplete",assessment(result).get("coverage"));
        assertTrue(codes(result).contains("deferred-loaders"));
        assertEquals("incomplete",result.get("status"));
        assertEquals(false,assessment(result).get("qualifiedValidity"));
    }
    @Test void knownMismatchAndUnresolvedIntentRemainSeparate() {
        var unknown=Map.<String,Object>of("id","addon","material",NAME,"kind","property","key","addon","equals",true);
        var result=run(execution(),List.of(registration(false),unknown));
        assertEquals("incomplete",result.get("status"));
        assertEquals("incomplete",Json.object(body(result).get("expectations")).get("status"));
        assertEquals(Map.of("matched",0L,"mismatch",1L,"unsupported",1L,"not-evaluated",0L),assessment(result).get("intentCounts"));
        assertEquals(List.of("expectation-mismatch","expectations-incomplete","qualification-pending"),codes(result));
        assertEquals("rejected",run(execution(),List.of(registration(false))).get("status"));
    }
    @Test void nativeErrorsAreNotErasedByContinuationOrContextGaps() {
        var execution=execution();execution.put("nativeErrors",List.of("Original log-and-continue failure"));
        execution.put("cleanObservation",false);
        var result=run(execution,List.of(registration(true)));
        assertEquals("source-error",result.get("status"));
        assertEquals("completed",assessment(result).get("execution"));
        assertTrue(codes(result).contains("native-error"));
        assertEquals(1L,Json.object(assessment(result).get("intentCounts")).get("not-evaluated"));
        execution.put("coverageGaps",List.of("unqualified addon"));
        result=run(execution,List.of(registration(true)));
        assertEquals("incomplete",result.get("status"));
        assertEquals("incomplete",body(result).get("nativeOutcome"));
        assertTrue(codes(result).containsAll(List.of("native-error","native-context-gap")));
    }
    @Test void capturedButUnqualifiedConfigurationCannotPassOrBlameDeveloperSource() {
        for(boolean nativeError:List.of(false,true)) {
            var execution=execution();
            if(nativeError)execution.put("nativeErrors",List.of("Original native error"));
            var input=new LinkedHashMap<String,Object>();input.put("execution",execution);
            input.put("sourceScope",Map.of("configuration",Map.of("application","not-qualified",
                    "fileCount",1)));
            String nativeBefore=Json.write(execution);
            var result=MaterialProgramAssessment.finish(input,ADMITTED,List.of(registration(false)));
            assertEquals("incomplete",result.get("status"));
            assertEquals("incomplete",body(result).get("nativeOutcome"));
            assertEquals("incomplete",assessment(result).get("coverage"));
            assertTrue(codes(result).contains("configuration-application-unqualified"));
            assertEquals(nativeBefore,Json.write(body(result).get("execution")));
            assertEquals(1L,Json.object(assessment(result).get("intentCounts")).get("not-evaluated"));
            assertEquals(nativeError,codes(result).contains("native-error"));
        }
    }
    @Test void emptyConfigurationDoesNotInventAnApplicationGap() {
        var input=new LinkedHashMap<String,Object>();input.put("execution",execution());
        input.put("sourceScope",Map.of("configuration",Map.of("application","not-qualified","fileCount",0)));
        var result=MaterialProgramAssessment.finish(input,ADMITTED,List.of());
        assertEquals("completed-without-observed-error",body(result).get("nativeOutcome"));
        assertFalse(codes(result).contains("configuration-application-unqualified"));
    }
    @Test void eachExistingCoverageCauseHasAnExplicitExplanationDespiteACleanFlag() {
        for(var field:Map.of("candidateResourceFailure","resource-interruption","candidateLinkageFailure","native-linkage",
                "nativeCompilationFailure","native-compilation","candidateAdmissionViolations","unqualified-operation",
                "scriptIndex","native-skipped-source").entrySet()) {
            var execution=execution();
            execution.put(field.getKey(),field.getKey().equals("scriptIndex")?List.of(Map.of("preprocessorCheckFailed",true))
                    :field.getKey().equals("candidateAdmissionViolations")?List.of("caught violation"):true);
            var result=run(execution,List.of(registration(true)));
            assertEquals("incomplete",result.get("status"));
            assertEquals("incomplete",assessment(result).get("coverage"));
            assertTrue(codes(result).contains(field.getValue()));
            assertEquals(1L,Json.object(assessment(result).get("intentCounts")).get("not-evaluated"));
        }
    }
    @Test void stoppedAndUnusableExecutionAreNotMistakenForQualificationOnly() {
        var execution=execution();execution.put("cleanObservation",false);execution.put("executionCompleted",false);
        var result=run(execution,List.of());
        assertEquals("stopped",assessment(result).get("execution"));
        assertTrue(codes(result).contains("execution-incomplete"));
        execution.put("executionCompleted",true);
        assertTrue(codes(run(execution,List.of())).contains("clean-observation-unavailable"));
    }
    @Test void structuralFailuresDoNotClaimNativeExecution() {
        for(String code:List.of("source.syntax","admission.source-path")) {
            var source=new MaterialSourceAdmission.Result(Map.of(),Set.of(),List.of(new MaterialSourceAdmission.Finding("groovy/material/Example.groovy",code,"finding",1,1)));
            var result=MaterialProgramAssessment.finish(new LinkedHashMap<>(),source,List.of(registration(true)));
            assertEquals(code.startsWith("source.")?"source-error":"incomplete",result.get("status"));
            assertEquals("not-run",body(result).get("nativeOutcome"));
            assertEquals("not-run",assessment(result).get("execution"));
            assertEquals("incomplete",assessment(result).get("coverage"));
            assertTrue(codes(result).contains("source-structure"));
        }
    }
    @Test void assessmentLeavesNativeObservationsAndExpectationOrderUnchanged() {
        var execution=execution();String before=Json.write(execution);
        var result=run(execution,List.of(registration(true)));
        assertEquals(before,Json.write(execution));
        assertSame(execution,body(result).get("execution"));
        for(Object row:Json.array(assessment(result).get("reasons"))) {
            assertTrue(Json.string(Json.object(row).get("evidencePointer")).startsWith("/"));
        }
    }
    @Test void missingCompilerLinkageRefusesBeforeNativeSourceExecution() {
        var body=new LinkedHashMap<String,Object>();
        body.put("bootstrap",Map.of("admitted",false,"compilerInvoked",false,
                "compilerLinkage",Map.of("status","incomplete","missing",List.of("required-hook"))));
        body.put("candidateCompilationStarted",false);
        var result=MaterialProgramAssessment.finish(body,ADMITTED,List.of(registration(true)));
        assertEquals("incomplete",result.get("status"));
        assertEquals("not-run",body(result).get("nativeOutcome"));
        assertEquals("not-run",assessment(result).get("execution"));
        assertEquals("incomplete",assessment(result).get("coverage"));
        assertTrue(codes(result).contains("native-bootstrap-incomplete"));
        assertEquals(false,Json.object(body(result).get("initialization")).get("nativeErrorObserved"));
        assertFalse(codes(result).contains("native-platform-error"));
        assertEquals(1L,Json.object(assessment(result).get("intentCounts")).get("not-evaluated"));
        assertFalse(body(result).containsKey("execution"));
    }
}
