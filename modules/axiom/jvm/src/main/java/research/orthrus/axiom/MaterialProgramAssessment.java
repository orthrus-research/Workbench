package research.orthrus.axiom;

import java.util.*;

/** Explains the existing result policy; it does not evaluate material rules or qualify a context. */
final class MaterialProgramAssessment {
    private MaterialProgramAssessment() {}

    static Map<String,Object> finish(Map<String,Object> result, MaterialSourceAdmission.Result structure,
                                     List<Map<String,Object>> checks) {
        var reasons=new ArrayList<Map<String,Object>>();
        var scope=Json.object(result.getOrDefault("sourceScope",Map.of()));
        boolean deferred=!Json.array(scope.getOrDefault("deferredLoaders",List.of())).isEmpty();
        if(deferred)reason(reasons,"deferred-loaders",
                "Saved sources are available to native imports, but deferred loader bodies and their recipe effects were not checked. Native outcome and expectations describe only the selected material context.","/sourceScope");
        Map<String,Object> execution=result.containsKey("execution")?Json.object(result.get("execution")):Map.of();
        boolean recipes=NativeRecipeScope.selected(result);
        var nativeState=Json.object(execution.getOrDefault("nativeInitialization",Map.of()));
        var startup=recipes?NativeRecipeScope.evaluate(nativeState):NativeStartupScope.selected(result)
                ?NativeStartupScope.evaluate(nativeState):null;
        boolean scopeQualified=startup!=null&&startup.qualified();
        boolean ran=!execution.isEmpty(), gaps=false, errors=false;
        String status, outcome;
        if(!structure.structurallyAdmitted()) {
            status=structure.findings().stream().allMatch(f->f.code().startsWith("source."))?"source-error":"incomplete";
            outcome="not-run";
            reason(reasons,"source-structure","Native compilation did not start; saved source did not pass structural admission.","/sourceAdmission/findings");
            gaps=true;
        } else if(!ran) {
            status="incomplete";outcome="not-run";gaps=true;
            reason(reasons,"native-bootstrap-incomplete","Required native compiler linkage was not established; candidate compilation did not start.","/bootstrap");
            var platform=Json.object(Json.object(result.getOrDefault("bootstrap",Map.of())).getOrDefault("platformInitialization",Map.of()));
            if("threw".equals(platform.get("status"))&&!Json.array(platform.getOrDefault("causes",List.of())).isEmpty()) {
                errors=true;
                reason(reasons,"native-platform-error","The original native platform failure was retained before material execution. This incomplete context is not invalid developer source.",
                        "/bootstrap/platformInitialization/causes");
            }
        } else {
            gaps=gap(reasons,!Json.array(execution.get("coverageGaps")).isEmpty(),"native-context-gap",
                    "Native execution reached behavior outside the declared context.","/execution/coverageGaps");
            gaps|=gap(reasons,startup!=null&&!startup.gaps().isEmpty(),"startup-evidence-incomplete",
                    "Original startup evidence has an affecting context or observation gap.","/execution/nativeInitialization");
            gaps|=gap(reasons,!Json.array(execution.get("candidateAdmissionViolations")).isEmpty(),"unqualified-operation",
                    "An operation was not admitted; catching its failure does not restore coverage.","/execution/candidateAdmissionViolations");
            gaps|=gap(reasons,Boolean.TRUE.equals(execution.get("candidateResourceFailure")),"resource-interruption",
                    "Resource interruption prevents a complete result.","/execution/candidateResourceFailure");
            gaps|=gap(reasons,Boolean.TRUE.equals(execution.get("candidateLinkageFailure")),"native-linkage",
                    "Native linkage could not complete in this context.","/execution/candidateLinkageFailure");
            gaps|=gap(reasons,Boolean.TRUE.equals(execution.get("nativeCompilationFailure"))&&!(scopeQualified&&startup.sourceError()),"native-compilation",
                    "The native compiler could not complete; this is not proof of invalid source.","/execution/nativeCompilationFailure");
            gaps|=gap(reasons,!recipes&&Json.array(execution.get("scriptIndex")).stream().map(Json::object)
                    .anyMatch(row->Boolean.TRUE.equals(row.get("preprocessorCheckFailed"))),"native-skipped-source",
                    "Native preprocessing skipped saved source; the complete program was not covered.","/execution/scriptIndex");
            boolean nativeErrors=!Json.array(execution.get("nativeErrors")).isEmpty();
            errors=nativeErrors||Json.array(execution.get("diagnostics")).stream()
                    .anyMatch(value->"error".equals(Json.object(value).get("severity")));
            if(errors)reason(reasons,"native-error",scopeQualified
                    ?"Original native initialization reported errors in the established scope; native diagnostic origins, saved-source locations and causes are retained where observed."
                    :"Native error diagnostics were retained; incomplete coverage must be resolved before attributing them to invalid source.",
                    nativeErrors?"/execution/nativeErrors":"/execution/diagnostics");
            boolean nativePackError=recipes&&scopeQualified&&!startup.sourceError();
            outcome=gaps?"incomplete":errors?(nativePackError?"native-failed":"source-error"):Boolean.TRUE.equals(execution.get("cleanObservation"))
                    ?"completed-without-observed-error":"incomplete";
            status=!gaps&&errors?(nativePackError?"rejected":"source-error"):!gaps&&scopeQualified&&Boolean.TRUE.equals(execution.get("cleanObservation"))?"accepted":"incomplete";
            if(Boolean.TRUE.equals(execution.get("nativeCompilationFailure")))result.put("compilationCoverage",scopeQualified
                    ?Map.of("status","native-failed","reason","The original compiler rejected saved source in the established startup context; its diagnostics are retained")
                    :Map.of("status","incomplete","reason","Native compiler could not complete in this bounded context; this is not proof of invalid source"));
            if(!Boolean.TRUE.equals(execution.get("executionCompleted")))reason(reasons,"execution-incomplete",
                    "Native execution did not reach its completed checkpoint; retained state may be partial.","/execution/executionCompleted");
            else if(!gaps&&!errors&&!Boolean.TRUE.equals(execution.get("cleanObservation")))reason(reasons,"clean-observation-unavailable",
                    "Execution completed without a usable clean observation.","/execution/cleanObservation");
        }
        var configuration=Json.object(scope.getOrDefault("configuration",Map.of()));
        if(startup!=null&&startup.configurationApplied()) {
            var observedConfiguration=new LinkedHashMap<>(configuration);
            observedConfiguration.put("application","original-selected-annotation-bindings-observed");
            observedConfiguration.put("applicationEvidencePointer","/execution/nativeInitialization/nativeConfiguration");
            observedConfiguration.put("meaning","original-startup-config-bindings-not-all-captured-file-applicability");
            var observedScope=new LinkedHashMap<>(scope);observedScope.put("configuration",observedConfiguration);
            result.put("sourceScope",observedScope);
        }
        if(((Number)configuration.getOrDefault("fileCount",0)).intValue()>0&&!(startup!=null&&startup.configurationApplied())) {
            // Capturing actual config must not turn execution with still-partial
            // native config consumers into a successful saved-workspace check.
            gaps=true;status="incomplete";outcome=ran?"incomplete":"not-run";
            reason(reasons,"configuration-application-unqualified",
                    "Saved pack configuration was captured, but its native application is not yet qualified. This is an Axiom context limitation, not invalid developer source.",
                    "/sourceScope/configuration");
        }
        scopeQualified&=!gaps;
        var intent=MaterialExpectations.evaluate(checks,execution,ran&&!gaps&&!errors&&Boolean.TRUE.equals(execution.get("cleanObservation")));
        if("mismatch".equals(intent.get("status")))status="rejected";
        var counts=new LinkedHashMap<String,Object>();
        for(String category:List.of("matched","mismatch","unsupported","not-evaluated")) {
            long count=Json.array(intent.get("checks")).stream().map(Json::object).filter(row->category.equals(row.get("status"))).count();
            counts.put(category,count);
        }
        if(((Number)counts.get("mismatch")).longValue()>0)reason(reasons,"expectation-mismatch",
                "Usable native observations contradict one or more saved expectations; these mismatches remain visible even when other checks are unresolved.","/expectations/checks");
        if("incomplete".equals(intent.get("status"))) {
            if("accepted".equals(status))status="incomplete";
            reason(reasons,"expectations-incomplete",
                    "Some expectations are unsupported or not evaluated; no values were inferred for them.","/expectations/checks");
        }
        if(scopeQualified)result.put("qualification",recipes?"native-recipe-context-established":"native-startup-context-established");
        else reason(reasons,"qualification-pending","This native context has not completed qualification; clean execution and matched expectations do not establish material validity.","/qualification");
        result.put("nativeOutcome",outcome);result.put("expectations",intent);
        result.put("assessment",Map.of("schema","axiom.material-program-assessment.v1","status",status,
                "execution",!ran?"not-run":Boolean.TRUE.equals(execution.get("executionCompleted"))?"completed":"stopped",
                "coverage",gaps||deferred&&!scopeQualified?"incomplete":"no-observed-gaps","intentCounts",counts,
                "qualifiedValidity",false,"reasons",reasons,
                "meaning","decision-explanation-not-native-rule-evaluation"));
        // Optional expectations do not rewrite the original initialization outcome.
        String initializationStatus=scopeQualified?(errors?"native-failed":startup.clean()?"completed":"incomplete")
                :"source-error".equals(status)&&ran&&!gaps&&!deferred
                ?"native-failed":"accepted".equals(status)&&ran&&!gaps&&!deferred
                &&Boolean.TRUE.equals(execution.get("executionCompleted"))?"completed":"incomplete";
        var initialization=new LinkedHashMap<String,Object>();
        initialization.put("schema","axiom.scoped-initialization.v1");
        initialization.put("status",initializationStatus);
        initialization.put("scope",recipes?NativeRecipeScope.SCOPE:scope.getOrDefault("selectedLoader","not-established"));
        initialization.put("executionCheckpoint",!ran?"not-run":Boolean.TRUE.equals(execution.get("executionCompleted"))?"completed":"stopped");
        initialization.put("nativeErrorObserved",errors);
        initialization.put("coverage",gaps||deferred&&!scopeQualified?"incomplete":"no-observed-gaps");
        initialization.put("nativeScopeQualified",scopeQualified);
        initialization.put("recipeEffectsChecked",recipes&&scopeQualified&&startup.checkpointCompleted());
        initialization.put("assessmentPointer","/assessment");
        final boolean scoped=scopeQualified;
        initialization.put("blockingEvidencePointers",reasons.stream().filter(row->!scoped||!"deferred-loaders".equals(row.get("code")))
                .map(row->row.get("evidencePointer")).distinct().toList());
        initialization.put("wholePackValidity",false);
        initialization.put("meaning","supported-initialization-scope-not-whole-pack-or-gameplay-validity");
        result.put("initialization",initialization);
        return Engine.envelope("material-program",status,result);
    }

    private static boolean gap(List<Map<String,Object>> reasons,boolean present,String code,String message,String pointer) {
        if(present)reason(reasons,code,message,pointer);
        return present;
    }
    private static void reason(List<Map<String,Object>> reasons,String code,String message,String pointer) {
        reasons.add(Map.of("code",code,"message",message,"evidencePointer",pointer));
    }
}
