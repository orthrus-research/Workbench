package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeRecipeScopeTest {
    @Test void supportedSourceFailureDuringStartupDoesNotClaimLaterRecipeEffects() {
        var state=NativeStartupScopeTest.nativeState();
        state.put("executionStage","recipes");state.put("constructionReady",false);state.put("constructionMethodReturned",true);
        state.put("groovyDiagnostics",List.of(Map.of("severity","error","locations",List.of(Map.of("path","groovy/material/Example.groovy","line",8)))));
        state.put("failure",List.of(Map.of("class","java.lang.IllegalStateException","message","Original Groovy initialization logged native errors")));
        var evidence=NativeRecipeScope.evaluate(state);
        assertTrue(evidence.qualified(),()->Json.write(evidence.gaps()));assertFalse(evidence.checkpointCompleted());
        var result=assess(state);var initialization=Json.object(Json.object(result.get("result")).get("initialization"));
        assertEquals("source-error",result.get("status"));assertEquals("native-failed",initialization.get("status"));
        assertEquals(false,initialization.get("recipeEffectsChecked"));assertEquals("stopped",initialization.get("executionCheckpoint"));
        state.put("failure",List.of(Map.of("class","java.lang.LinkageError","message","different native failure")));
        assertFalse(NativeRecipeScope.evaluate(state).qualified());
    }
    @Test void directProbeCatalogReferenceIsAdaptedWithoutChangingOriginalEvidence() {
        var state=state();var effects=new LinkedHashMap<>(Json.object(state.get("registrationEffects")));
        var reference=Map.of("status","reference","sourcePointer","/result/customMetaItems");
        effects.put("customItems",reference);state.put("registrationEffects",effects);
        assertTrue(NativeRecipeScope.evaluate(state).clean());
        assertEquals(reference,Json.object(state.get("registrationEffects")).get("customItems"));
        effects.put("customItems",Map.of("status","reference","sourcePointer","/unknown/customMetaItems"));
        assertGap(state,"native-startup-effects");
    }
    @Test void completedScopeWithNativeErrorsIsQualifiedButNeverCleanOrInvalidSource() {
        var state=state();
        assertTrue(NativeRecipeScope.evaluate(state).clean());
        state.put("nativeArtifacts",Map.of("example",Map.of("sha256","a".repeat(64),"codeSource","/native/example.jar")));
        state.put("nativeDiagnostics",List.of(nativeError("jar:file:/native/example.jar!/original/Example.class")));
        var evidence=NativeRecipeScope.evaluate(state);
        assertTrue(evidence.qualified(),()->Json.write(evidence.gaps()));
        assertTrue(evidence.checkpointCompleted());assertTrue(evidence.nativeError());
        assertFalse(evidence.sourceError());assertFalse(evidence.clean());
        var assessed=assess(state);var body=Json.object(assessed.get("result"));
        assertEquals("rejected",assessed.get("status"));assertEquals("native-failed",body.get("nativeOutcome"));
        var initialization=Json.object(body.get("initialization"));
        assertEquals("native-failed",initialization.get("status"));
        assertEquals(true,initialization.get("recipeEffectsChecked"));
        assertEquals("no-observed-gaps",initialization.get("coverage"));
    }

    @Test void loggedCallerMustBelongToAnObservedOriginalArtifact() {
        for(String source:List.of("file:/native/missing.jar","https://example.invalid/example.jar")) {
            var state=state();state.put("nativeDiagnostics",List.of(nativeError(source)));
            assertGap(state,"unattributed-native-error");
        }
        var state=state();state.put("artifactCodeSources",Map.of("original","file:/native/example.jar"));
        var row=new LinkedHashMap<>(nativeError("file:/native/example.jar"));row.put("nativeOriginFailure","capture failed");
        state.put("nativeDiagnostics",List.of(row));assertGap(state,"unattributed-native-error");
    }

    @Test void savedSourceErrorRemainsLocatedWithoutInventingArtifactAttribution() {
        var state=state();state.put("groovyDiagnostics",List.of(Map.of("severity","error","locations",
                List.of(Map.of("path","groovy/postInit/Recipe.groovy","line",12)))));
        var evidence=NativeRecipeScope.evaluate(state);
        assertTrue(evidence.qualified(),()->Json.write(evidence.gaps()));assertTrue(evidence.sourceError());
        assertEquals("saved-source",NativeRecipeScope.diagnosticAttribution(state).getFirst().get("origin"));
        assertEquals("source-error",assess(state).get("status"));
    }

    @Test void nativeCompilerRejectionNeedsItsOwnSavedSourceLocation() {
        var state=state();state.put("groovyCompilationFailure",true);
        state.put("nativeScriptIndex",List.of(Map.of("classDefined",false,"preprocessorCheckFailed",false)));
        state.put("groovyDiagnostics",List.of(Map.of("severity","error","compilerFindings",List.of(
                Map.of("message","original compiler rejection","location",Map.of("path","groovy/postInit/Example.groovy","line",3))))));
        assertTrue(NativeRecipeScope.evaluate(state).qualified());assertEquals("source-error",assess(state).get("status"));
        state.put("groovyDiagnostics",List.of(Map.of("severity","error","locations",List.of(Map.of("path","groovy/postInit/Other.groovy","line",3)),
                "compilerFindings",List.of(Map.of("message","unlocated compiler failure")))));
        assertGap(state,"native-compiler");
    }

    @Test void lifecycleReturnCannotStandInForMissingNativePrerequisites() {
        for(String field:List.of("nativeConfiguration","nativeServerOwner","registrationEffects","nativeWorldgenBiomeBindings",
                "nativeStoredRecipes","nativeStoredCraftingRecipes","nativeStoredFurnaceRecipes","initializedMods",
                "nativeConsole","candidateAdmissionViolations","nativeScriptIndex","nativeRecipePropertyAdmissionBound")) {
            var state=state();state.remove(field);
            assertFalse(NativeRecipeScope.evaluate(state).qualified(),field);
            var initialization=Json.object(Json.object(assess(state).get("result")).get("initialization"));
            assertEquals("incomplete",initialization.get("status"),field);
            assertEquals(false,initialization.get("recipeEffectsChecked"),field);
        }
    }

    @Test void originalServerPreprocessorSkipIsCoveredButUnexplainedSkipIsNot() {
        var state=state();state.put("nativeScriptIndex",List.of(Map.of("classDefined",false,"preprocessorCheckFailed",true,
                "preprocessors",List.of("side: client"))));
        assertTrue(NativeRecipeScope.evaluate(state).qualified());
        assertEquals("accepted",assess(state).get("status"));
        state.put("nativeScriptIndex",List.of(Map.of("classDefined",false,"preprocessorCheckFailed",true,"preprocessors",List.of())));
        assertGap(state,"native-source-coverage");
    }

    @Test void incompleteValuesDanglingReferencesAndLookupOrderRemainGaps() {
        for(String change:List.of("dangling","order","missing-entry","unfrozen","gap")) {
            var state=state();var crafting=new LinkedHashMap<>(Json.object(state.get("nativeStoredCraftingRecipes")));
            switch(change) {
                case "dangling" -> crafting.put("nativeValues",Map.of("1",Map.of("child",Map.of("nativeValueRef","missing"))));
                case "order" -> crafting.put("nativeLookupOrder",List.of(Map.of("id",2,"key","a"),Map.of("id",1,"key","b")));
                case "missing-entry" -> crafting.put("nativeLookupOrder",List.of());
                case "unfrozen" -> crafting.put("registryFrozen",false);
                case "gap" -> crafting.put("affectingGaps",List.of(Map.of("operation","unobserved-value")));
            }
            state.put("nativeStoredCraftingRecipes",crafting);
            assertFalse(NativeRecipeScope.evaluate(state).qualified(),change);
        }
    }

    @Test void genericBootstrapFailureCannotBeReclassifiedByAnUnrelatedSourceError() {
        var state=state();state.put("failure",List.of(Map.of("class","java.lang.LinkageError","message","missing required native code")));
        state.put("groovyDiagnostics",List.of(Map.of("severity","error","locations",List.of(Map.of("path","groovy/postInit/Recipe.groovy","line",12)))));
        assertGap(state,"native-bootstrap-failure");
    }

    @Test void recipeStageSelectionBelongsToTheProfile() {
        assertEquals("recipes",MaterialProgram.initializationStage(Map.of("id","supersymmetry:material-authoring-pack","initializationStage","recipes")));
        assertEquals("preinit",MaterialProgram.initializationStage(Map.of("id","supersymmetry:material-authoring-pack")));
        assertThrows(Failure.class,()->MaterialProgram.initializationStage(Map.of("id","supersymmetry:material-authoring-gt-base","initializationStage","recipes")));
        assertThrows(Failure.class,()->MaterialProgram.initializationStage(Map.of("id","supersymmetry:material-authoring-pack","initializationStage","world")));
    }

    private static Map<String,Object> nativeError(String source) {
        return Map.of("severity","error","message","Original native diagnostic","nativeOrigins",
                List.of(Map.of("class","original.Example","method","initialize","codeSource",source)));
    }
    private static void assertGap(Map<String,Object> state,String operation) {
        var evidence=NativeRecipeScope.evaluate(state);assertFalse(evidence.qualified());
        assertTrue(evidence.gaps().stream().anyMatch(row->operation.equals(row.get("operation"))),()->Json.write(evidence.gaps()));
    }
    private static Map<String,Object> assess(Map<String,Object> state) {
        var result=new LinkedHashMap<String,Object>();
        result.put("context",Map.of("id","supersymmetry:material-authoring-pack"));
        result.put("bootstrap",Map.of("route","original-native-initialization"));
        result.put("sourceScope",Map.of("selectedLoader","preInit","initializationStage","recipes","deferredLoaders",List.of()));
        result.put("execution",MaterialProgram.originalInitializationResult(state));
        return MaterialProgramAssessment.finish(result,new MaterialSourceAdmission.Result(Map.of(),Set.of(),List.of()),List.of());
    }
    static Map<String,Object> state() {
        var state=NativeStartupScopeTest.nativeState();
        state.put("schema","axiom.native-recipe-stage.v1");state.put("executionStage","recipes");state.put("loaderState","AVAILABLE");
        state.put("constructionMethodReturned",true);state.put("nativeRecipePropertyAdmissionBound",true);
        state.put("recipeInitializationStarted",true);state.put("recipeInitializationReturned",true);
        state.put("constructedMods",List.of(Map.of("id","gregtech","state","CONSTRUCTED")));
        state.put("initializedMods",List.of(Map.of("id","gregtech","state","AVAILABLE")));
        var server=new LinkedHashMap<String,Object>();
        server.put("mode","original-dedicated-server-construction");server.put("serverClass","net.minecraft.server.dedicated.DedicatedServer");
        server.put("threadGroup","SERVER");
        for(String field:List.of("constructorReturned","nativeDefiningLoader","originalOwnershipPrefixReturned","gameThreadAbsent"))server.put(field,true);
        server.put("snooperStarted",false);
        for(String field:List.of("networkConnectionCount","networkEndpointCount","worldCount"))server.put(field,0);
        state.put("nativeServerOwner",server);
        state.put("nativeWorldgenBiomeBindings",Map.of("status","observed","observationsComplete",true,"affectingGaps",List.of()));
        var gt=storage("axiom.native-stored-recipes.v1");gt.put("maps",Map.of("map",Map.of()));state.put("nativeStoredRecipes",gt);
        var crafting=storage("axiom.native-stored-crafting-recipes.v1");crafting.put("registryFrozen",true);
        crafting.put("entries",Map.of("a",Map.of("nativeValueRef","1"),"b",Map.of("nativeValueRef","1")));
        crafting.put("nativeValues",Map.of("1",Map.of("type","original.NativeValue","self",Map.of("nativeValueRef","1"))));
        crafting.put("nativeLookupOrder",List.of(Map.of("id",1,"key","a"),Map.of("id",5,"key","b")));
        state.put("nativeStoredCraftingRecipes",crafting);
        var furnace=storage("axiom.native-stored-furnace-recipes.v1");
        for(String key:List.of("smelting","experience","timeWildcard","timeMetadata","fuelConversions"))furnace.put(key,List.of());
        furnace.put("timeWildcardDefault",200);furnace.put("timeMetadataDefault",200);state.put("nativeStoredFurnaceRecipes",furnace);
        return state;
    }
    private static Map<String,Object> storage(String schema) {
        return new LinkedHashMap<>(Map.of("schema",schema,"status","observed","storedValuesComplete",true,"affectingGaps",List.of()));
    }
}
