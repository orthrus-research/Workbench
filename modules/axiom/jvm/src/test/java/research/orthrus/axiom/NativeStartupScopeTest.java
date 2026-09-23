package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Decision fixtures only; original native execution is qualified separately. */
class NativeStartupScopeTest {
    private static final MaterialSourceAdmission.Result ADMITTED = new MaterialSourceAdmission.Result(Map.of(), Set.of(), List.of());

    @Test void completedStartupRetainsDeferredRecipesAndScopedConfigurationMeaning() {
        var result = assess(nativeState(), List.of()); var body = body(result); var initialization = object(body.get("initialization"));
        assertEquals("accepted", result.get("status")); assertEquals("completed", initialization.get("status"));
        assertEquals(true, initialization.get("nativeScopeQualified")); assertEquals(false, initialization.get("recipeEffectsChecked"));
        assertEquals(false, initialization.get("wholePackValidity"));
        assertEquals(List.of("postInit"), object(body.get("sourceScope")).get("deferredLoaders"));
        assertEquals("original-selected-annotation-bindings-observed", object(object(body.get("sourceScope")).get("configuration")).get("application"));
        assertEquals("no-observed-gaps", initialization.get("coverage"));
        assertFalse(Json.array(initialization.get("blockingEvidencePointers")).contains("/sourceScope"));
    }

    @Test void thrownSavedFailureDoesNotRequireLaterEffectsToClassifyNativeFailure() {
        var nativeState = nativeState(); var cause = Map.of("type", "java.lang.IllegalArgumentException", "message", "native property rejected", "locations", locations());
        nativeState.put("groovyDiagnostics", List.of(Map.of("severity", "error", "causality", Map.of("exceptions", List.of(cause)))));
        nativeState.put("failure", List.of(Map.of("class", cause.get("type"), "message", cause.get("message"))));
        nativeState.put("preInitializationReturned", false); nativeState.put("preInitializationReady", false);
        nativeState.remove("registrationEffects"); nativeState.remove("customMetaItems");
        var result = assess(nativeState, List.of()); var initialization = object(body(result).get("initialization"));
        assertEquals("source-error", result.get("status")); assertEquals("native-failed", initialization.get("status"));
        assertEquals("stopped", initialization.get("executionCheckpoint")); assertEquals(true, initialization.get("nativeErrorObserved"));
    }

    @Test void loggedFailureRemainsFailureAfterNativeCompletion() {
        var nativeState = nativeState(); loggedFailure(nativeState);
        String original = Json.write(nativeState);
        var result = assess(nativeState, List.of());
        assertEquals("source-error", result.get("status")); assertEquals("native-failed", object(body(result).get("initialization")).get("status"));
        assertEquals(true, object(body(result).get("execution")).get("executionCompleted"));
        assertEquals(false, object(body(result).get("execution")).get("cleanObservation"));
        assertEquals(original, Json.write(nativeState));
    }

    @Test void missingNativeContextAndCaughtUnavailableOperationsRemainIncomplete() {
        for (String field : List.of("selectionReady", "constructionReady", "nativeDiagnosticsComplete", "candidateLinkageFailure", "candidateResourceFailure", "candidateAdmissionViolations")) {
            var nativeState = nativeState(); loggedFailure(nativeState);
            nativeState.put(field, field.equals("candidateAdmissionViolations") ? List.of("caught unavailable operation")
                    : field.startsWith("candidate") ? true : false);
            var result = assess(nativeState, List.of());
            assertEquals("incomplete", result.get("status"), field);
            assertEquals("incomplete", object(body(result).get("initialization")).get("status"), field);
            assertEquals(true, object(body(result).get("initialization")).get("nativeErrorObserved"), field);
        }
    }

    @Test void nativeBootstrapGapCannotBeExplainedByAnUnrelatedSavedError() {
        var nativeState = nativeState(); loggedFailure(nativeState);
        nativeState.put("failure", List.of(Map.of("class", "java.lang.NoClassDefFoundError", "message", "unavailable.NativeOwner")));
        assertEquals("incomplete", assess(nativeState, List.of()).get("status"));
    }

    @Test void nativeCompilerLocationDistinguishesSourceFailureFromMissingCompilerEvidence() {
        var nativeState = nativeState(); loggedFailure(nativeState);
        nativeState.put("groovyCompilationFailure", true);
        nativeState.put("nativeScriptIndex", List.of(Map.of("classDefined", false, "preprocessorCheckFailed", false)));
        nativeState.put("groovyDiagnostics", List.of(Map.of("severity", "error", "compilerFindings", List.of(
                Map.of("message", "native compiler error", "location", locations().getFirst())))));
        var result = assess(nativeState, List.of());
        assertEquals("native-failed", object(body(result).get("initialization")).get("status"));
        assertEquals("native-failed", object(body(result).get("compilationCoverage")).get("status"));
        nativeState.put("groovyDiagnostics", List.of(Map.of("severity", "error", "message", "unlocated compiler failure")));
        assertEquals("incomplete", assess(nativeState, List.of()).get("status"));
    }

    @Test void configurationDirectoryBindingsAndMembershipAreRequired() {
        for (String missing : List.of("savedDirectoryIdentity", "classes", "declaredClasses", "observationsComplete")) {
            var nativeState = nativeState(); var configuration = new LinkedHashMap<>(object(nativeState.get("nativeConfiguration")));
            configuration.remove(missing); nativeState.put("nativeConfiguration", configuration);
            var result = assess(nativeState, List.of());
            assertEquals("incomplete", result.get("status"), missing);
            assertEquals(false, object(body(result).get("initialization")).get("nativeScopeQualified"));
        }
    }

    @Test void nativeCheckpointCannotReplaceMissingOrIncompatibleEffectCatalogs() {
        for (String field : List.of("materials", "fluids", "customItems", "prefixItems", "materialBlocks", "oreBlocks", "materialFluidBindings")) {
            var nativeState = nativeState(); var effects = new LinkedHashMap<>(object(nativeState.get("registrationEffects")));
            effects.remove(field); nativeState.put("registrationEffects", effects);
            assertEquals("incomplete", assess(nativeState, List.of()).get("status"), field);
        }
    }

    @Test void optionalUnresolvedExpectationsDoNotRewriteCompletedNativeStartup() {
        var checks = MaterialExpectations.parse(List.of(Map.of("id", "optional", "material", "gregtech:iron", "kind", "property", "key", "addon", "equals", true)));
        var result = assess(nativeState(), checks);
        assertEquals("incomplete", result.get("status"));
        assertEquals("completed", object(body(result).get("initialization")).get("status"));
        assertEquals("incomplete", object(body(result).get("expectations")).get("status"));
    }

    @Test void optionalRegistrationUsesCompleteNativeMembershipAndNeverInventsAbsence() {
        var checks = MaterialExpectations.parse(List.of(
                Map.of("id", "present", "material", "native:example", "kind", "registration", "equals", true),
                Map.of("id", "absent", "material", "native:absent", "kind", "registration", "equals", false)));
        var result = assess(nativeState(), checks);
        assertEquals("accepted", result.get("status"));
        assertEquals("matched", object(body(result).get("expectations")).get("status"));
        var missing = MaterialExpectations.evaluate(checks, Map.of("registrationEffects", Map.of("materials",
                Map.of("status", "unavailable", "inventoryComplete", false, "entries", Map.of()))), true);
        assertEquals("incomplete", missing.get("status"));
        assertTrue(Json.array(missing.get("checks")).stream().map(Json::object).allMatch(row -> "not-evaluated".equals(row.get("status"))));
    }

    private static Map<String,Object> assess(Map<String,Object> nativeState, List<Map<String,Object>> checks) {
        var result = new LinkedHashMap<String,Object>();
        result.put("context", Map.of("id", "supersymmetry:material-authoring-pack"));
        result.put("bootstrap", Map.of("route", "original-native-initialization"));
        result.put("sourceScope", Map.of("selectedLoader", "preInit", "deferredLoaders", List.of("postInit"),
                "configuration", Map.of("application", "not-qualified", "fileCount", 1)));
        result.put("execution", MaterialProgram.originalInitializationResult(nativeState));
        return MaterialProgramAssessment.finish(result, ADMITTED, checks);
    }
    private static void loggedFailure(Map<String,Object> state) {
        state.put("groovyDiagnostics", List.of(Map.of("severity", "error", "channel", "groovy-log", "message", "original logged rejection", "locations", locations())));
        state.put("preInitializationReady", false);
        state.put("failure", List.of(Map.of("class", "java.lang.IllegalStateException", "message", "Original Groovy initialization logged native errors")));
    }
    private static List<Map<String,Object>> locations() { return List.of(Map.of("path", "groovy/material/Example.groovy", "line", 7, "precision", "native-stack")); }
    private static Map<String,Object> body(Map<String,Object> result) { return object(result.get("result")); }
    private static Map<String,Object> object(Object value) { return Json.object(value); }

    static Map<String,Object> nativeState() {
        var state = new LinkedHashMap<String,Object>();
        state.put("schema", "axiom.native-preinit-stage.v1"); state.put("nativeContext", "supersymmetry:required-early");
        state.put("side", "SERVER"); state.put("mixinPhase", "DEFAULT"); state.put("executionStage", "preinit");
        state.put("minecraftLaunched", false);
        for (String field : List.of("earlyPipelineReady", "selectionReady", "constructionReady", "nativeHomeInitialized", "nativeDiagnosticsComplete",
                "nativeMapperAdmissionBound", "candidateCompilationStarted", "groovyInitializationReturned", "preInitializationReturned", "preInitializationDispatchStarted",
                "preInitializationDispatched", "preInitializationDispatchReturned", "nonRecipeRegistryEventsReturned", "preInitializationReady")) state.put(field, true);
        state.put("modPreInitDispatchCount", 1L); state.put("loaderState", "INITIALIZATION");
        state.put("nativeConsole", Map.of("complete", true));
        for (String field : List.of("nativeDiagnostics", "groovyDiagnostics", "nativeGroovyErrors", "candidateAdmissionViolations")) state.put(field, List.of());
        state.put("candidateLinkageFailure", false); state.put("candidateResourceFailure", false);
        state.put("nativeScriptIndex", List.of(Map.of("classDefined", true, "preprocessorCheckFailed", false)));
        state.put("nativeGroovyInitialization", Map.of("modSupportFrozen", true, "scriptOwnerIsOriginalContainer", true, "scriptOwner", "supersymmetry"));
        state.put("selectedMods", List.of(Map.of("id", "gregtech"))); state.put("preInitializedMods", List.of(Map.of("id", "gregtech", "state", "PREINITIALIZED")));
        state.put("nativeConfiguration", Map.of("schema", "axiom.native-configuration-bindings.v1", "scope", "original-active-annotation-config-bindings-before-preinit",
                "application", "original-config-manager-sync-during-construction", "status", "observed", "savedDirectoryIdentity", true, "observationsComplete", true,
                "affectingGaps", List.of(), "declaredClasses", Map.of("gregtech.common.ConfigHolder", "gregtech"), "classes", List.of(Map.of("class", "gregtech.common.ConfigHolder",
                        "owner", "gregtech", "path", "config/gregtech/gregtech.cfg", "nativeConfigurationIdentity", true, "nativeOwnerIdentity", true))));
        var effects = new LinkedHashMap<String,Object>(); effects.put("schema", "axiom.native-registration-effects.v1"); effects.put("phase", "FROZEN");
        var scopes = Map.of("materials", "native-material-observation-selected-property-values-v2", "fluids", "native-fluid-default-scalars-v1",
                "prefixItems", "original-prefix-item-variants-and-registry-identities-v1", "materialBlocks", "original-block-item-state-property-and-unifier-identities-v1",
                "oreBlocks", "original-ore-stone-variants-and-block-item-registry-identities-v1");
        var memberships = Map.of("materials", "native-material-registry", "fluids", "native-forge-fluid-registry", "prefixItems", "native-meta-prefix-item-collections",
                "materialBlocks", "native-material-block-collections", "oreBlocks", "native-ore-block-collection");
        for (var scope : scopes.entrySet()) effects.put(scope.getKey(), Map.of("status", "observed", "inventoryComplete", true, "membership", memberships.get(scope.getKey()),
                "stateScope", scope.getValue(), "entries", Map.of("native:example", "a".repeat(64)), "witnesses", List.of(Map.of("material", "gregtech:iron", "generated", List.of()))));
        state.put("nativeMaterialRegistries", Map.of("status", "observed", "phase", "FROZEN", "totalRegisteredMaterials", 1, "registries", List.of(Map.of("registeredMaterials", 1))));
        state.put("nativeMaterialWitnesses", List.of("gregtech:iron", "gregtech:diamond").stream().map(name -> Map.of("name", name, "registryIdentity", true,
                "nativePropertyState", Map.of("schema", "axiom.native-material-property-state.v1"))).toList());
        effects.put("materialFluidBindings", Map.of("status", "observed", "bindingsComplete", true, "affectingGaps", List.of(), "fingerprintedIn", "materials", "materialsWithFluidProperty", 1));
        effects.put("customItems", Map.of("status", "reference", "sourcePointer", "/execution/customMetaItems"));
        state.put("registrationEffects", effects);
        state.put("customMetaItems", Map.of("status", "observed", "items", List.of(Map.of("registryName", "gregtech:example", "nativeClassSpace", true,
                "forgeRegistered", true, "variants", List.of(Map.of("meta", 1, "name", "example", "ownerIdentity", true, "nameLookupIdentity", true))))));
        return state;
    }
}
