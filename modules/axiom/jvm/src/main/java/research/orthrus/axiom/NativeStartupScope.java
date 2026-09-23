package research.orthrus.axiom;

import java.util.*;

/** Assess original startup evidence; this does not execute native rules or qualify recipes. */
final class NativeStartupScope {
    private NativeStartupScope() {}
    record Evidence(boolean checkpointCompleted, boolean configurationApplied, boolean sourceError,
                    boolean nativeError, List<Map<String,Object>> gaps) {
        boolean qualified() { return gaps.isEmpty() && (checkpointCompleted || sourceError); }
        boolean clean() { return qualified() && checkpointCompleted && !nativeError; }
    }

    static boolean selected(Map<String,Object> result) {
        return "supersymmetry:material-authoring-pack".equals(object(result.get("context")).get("id"))
                && "original-native-initialization".equals(object(result.get("bootstrap")).get("route"))
                && "preInit".equals(object(result.get("sourceScope")).get("selectedLoader"));
    }

    static Evidence evaluate(Map<String,Object> nativeState) {
        return evaluate(nativeState,false);
    }

    /** A saved-source failure can stop the requested recipe scope during preInit.
     * Retain that supported failure without claiming later recipes were observed. */
    static Evidence recipePrefixFailure(Map<String,Object> nativeState) {
        var prefix=evaluate(nativeState,true);
        return new Evidence(false,prefix.configurationApplied(),prefix.sourceError(),prefix.nativeError(),prefix.gaps());
    }

    private static Evidence evaluate(Map<String,Object> nativeState,boolean recipePrefix) {
        var gaps = new ArrayList<Map<String,Object>>();
        boolean identity = "axiom.native-preinit-stage.v1".equals(nativeState.get("schema"))
                && "supersymmetry:required-early".equals(nativeState.get("nativeContext"))
                && "SERVER".equals(nativeState.get("side")) && "DEFAULT".equals(nativeState.get("mixinPhase"))
                && (recipePrefix?"recipes":"preinit").equals(nativeState.get("executionStage")) && Boolean.FALSE.equals(nativeState.get("minecraftLaunched"));
        if (!identity) gap(gaps, "startup-context", "The original selected SERVER startup context was not established", "");
        for (String field : List.of("earlyPipelineReady", "selectionReady", recipePrefix?"constructionMethodReturned":"constructionReady", "nativeHomeInitialized",
                "nativeDiagnosticsComplete", "nativeMapperAdmissionBound", "candidateCompilationStarted", "groovyInitializationReturned"))
            if (!Boolean.TRUE.equals(nativeState.get(field))) gap(gaps, field, "Required original startup evidence is absent: " + field, "/" + field);
        for (String field : List.of("nativeDiagnostics", "groovyDiagnostics", "nativeGroovyErrors", "candidateAdmissionViolations", "nativeScriptIndex"))
            if (!(nativeState.get(field) instanceof List<?>)) gap(gaps, field, "Required native evidence is unavailable: " + field, "/" + field);
        if (!Boolean.TRUE.equals(object(nativeState.get("nativeConsole")).get("complete")))
            gap(gaps, "native-console", "Original native console evidence is incomplete", "/nativeConsole");
        for (String field : List.of("observationFailure", "constructionObservationFailure", "constructionEffectsObservationFailure", "groovyObservationFailure"))
            if (nativeState.containsKey(field)) gap(gaps, field, "Original startup observations could not be completed", "/" + field);
        if (!list(nativeState.get("candidateAdmissionViolations")).isEmpty()
                || !Boolean.FALSE.equals(nativeState.get("candidateResourceFailure"))
                || !Boolean.FALSE.equals(nativeState.get("candidateLinkageFailure")))
            gap(gaps, "native-context-operation", "Saved execution encountered an unavailable operation, linkage or resource", "/candidateAdmissionViolations");
        var groovy = object(nativeState.get("nativeGroovyInitialization"));
        if (!Boolean.TRUE.equals(groovy.get("modSupportFrozen")) || !Boolean.TRUE.equals(groovy.get("scriptOwnerIsOriginalContainer"))
                || !"supersymmetry".equals(groovy.get("scriptOwner")))
            gap(gaps, "groovy-owner", "Original Groovy compatibility and saved script ownership were not established", "/nativeGroovyInitialization");

        boolean configuration = configurationApplied(object(nativeState.get("nativeConfiguration")));
        if (!configuration) gap(gaps, "native-configuration", "Original selected configuration bindings or saved directory are incomplete", "/nativeConfiguration");
        var diagnostics = new ArrayList<>(list(nativeState.get("nativeDiagnostics")));
        diagnostics.addAll(list(nativeState.get("groovyDiagnostics")));
        boolean error = !list(nativeState.get("nativeGroovyErrors")).isEmpty()
                || diagnostics.stream().map(NativeStartupScope::object).anyMatch(row -> "error".equals(row.get("severity")));
        boolean sourceError = diagnostics.stream().map(NativeStartupScope::object).anyMatch(NativeStartupScope::sourceError);
        boolean compilerError = Boolean.TRUE.equals(nativeState.get("groovyCompilationFailure")) && diagnostics.stream()
                .map(NativeStartupScope::object).flatMap(row -> list(row.get("compilerFindings")).stream()).map(NativeStartupScope::object)
                .anyMatch(row -> sourceLocation(object(row.get("location"))));
        var scripts = list(nativeState.get("nativeScriptIndex"));
        if (scripts.isEmpty() || scripts.stream().map(NativeStartupScope::object).anyMatch(row ->
                !Boolean.FALSE.equals(row.get("preprocessorCheckFailed"))
                        || !Boolean.TRUE.equals(row.get("classDefined")) && !compilerError))
            gap(gaps, "native-source-coverage", "Original source coverage is incomplete beyond any retained native compiler failure", "/nativeScriptIndex");
        if (Boolean.TRUE.equals(nativeState.get("groovyCompilationFailure")) && !compilerError)
            gap(gaps, "native-compiler", "Native compilation failed without a supported saved-source diagnostic", "/groovyDiagnostics");

        var failures = list(nativeState.get("failure"));
        if (!failures.isEmpty() && !explainedFailure(failures, diagnostics, sourceError))
            gap(gaps, "native-bootstrap-failure", "The original startup failure is not explained by a retained saved-source error", "/failure");
        if(recipePrefix) {
            if(!sourceError)gap(gaps,"native-source-failure","No supported saved-source failure explains the stopped recipe prerequisite","/groovyDiagnostics");
            for(var row:NativeRecipeScope.diagnosticAttribution(nativeState))if("unattributed".equals(row.get("origin")))
                gap(gaps,"unattributed-native-error","A native error has no saved-source or original artifact caller",(String)row.get("nativePointer"));
        } else if (error && !sourceError) gap(gaps, "unattributed-native-error", "Native errors remain unattributed to the saved program in this context", "/nativeDiagnostics");
        boolean checkpoint = List.of("preInitializationReturned", "preInitializationDispatchStarted", "preInitializationDispatched",
                "preInitializationDispatchReturned", "nonRecipeRegistryEventsReturned").stream()
                .allMatch(field -> Boolean.TRUE.equals(nativeState.get(field)))
                && nativeState.get("modPreInitDispatchCount") instanceof Number count && count.intValue() == 1
                && "INITIALIZATION".equals(nativeState.get("loaderState"));
        if (!error) {
            if (!checkpoint || !Boolean.TRUE.equals(nativeState.get("preInitializationReady")))
                gap(gaps, "native-startup-checkpoint", "Original startup did not complete its declared checkpoint", "/preInitializationReturned");
            var selected = list(nativeState.get("selectedMods")); var initialized = list(nativeState.get("preInitializedMods"));
            if (selected.isEmpty() || !selected.stream().map(NativeStartupScope::object).map(row -> row.get("id")).toList()
                    .equals(initialized.stream().map(NativeStartupScope::object).map(row -> row.get("id")).toList())
                    || initialized.stream().map(NativeStartupScope::object).anyMatch(row -> !"PREINITIALIZED".equals(row.get("state"))))
                gap(gaps, "native-mod-checkpoint", "Not every originally selected mod completed preInit in its native order", "/preInitializedMods");
            if (!effectsComplete(nativeState)) gap(gaps, "native-startup-effects", "Complete current native material/item/fluid and generated effects are unavailable", "/registrationEffects");
        }
        return new Evidence(checkpoint, configuration, sourceError, error, List.copyOf(gaps));
    }

    static boolean configurationApplied(Map<String,Object> value) {
        if (!"axiom.native-configuration-bindings.v1".equals(value.get("schema")) || !"observed".equals(value.get("status"))
                || !"original-active-annotation-config-bindings-before-preinit".equals(value.get("scope"))
                || !"original-config-manager-sync-during-construction".equals(value.get("application"))
                || !Boolean.TRUE.equals(value.get("savedDirectoryIdentity")) || !Boolean.TRUE.equals(value.get("observationsComplete"))
                || !(value.get("affectingGaps") instanceof List<?> gaps) || !gaps.isEmpty()) return false;
        var expected = object(value.get("declaredClasses")); var observed = new HashMap<String,Object>();
        for (Object item : list(value.get("classes"))) {
            var row = object(item);
            if (!(row.get("class") instanceof String name) || !(row.get("path") instanceof String path) || !path.startsWith("config/")
                    || !Boolean.TRUE.equals(row.get("nativeConfigurationIdentity")) || !Boolean.TRUE.equals(row.get("nativeOwnerIdentity"))
                    || observed.put(name, row.get("owner")) != null) return false;
        }
        return !expected.isEmpty() && expected.entrySet().stream().allMatch(row -> Objects.equals(observed.get(row.getKey()), row.getValue()));
    }

    static boolean effectsComplete(Map<String,Object> nativeState) {
        var effects = object(nativeState.get("registrationEffects"));
        if (!"FROZEN".equals(effects.get("phase"))) return false;
        var scopes = Map.of("materials", "native-material-observation-selected-property-values-v2",
                "fluids", "native-fluid-default-scalars-v1", "prefixItems", "original-prefix-item-variants-and-registry-identities-v1",
                "materialBlocks", "original-block-item-state-property-and-unifier-identities-v1",
                "oreBlocks", "original-ore-stone-variants-and-block-item-registry-identities-v1");
        var memberships = Map.of("materials", "native-material-registry", "fluids", "native-forge-fluid-registry",
                "prefixItems", "native-meta-prefix-item-collections", "materialBlocks", "native-material-block-collections",
                "oreBlocks", "native-ore-block-collection");
        for (var scope : scopes.entrySet()) {
            var catalog = object(effects.get(scope.getKey()));
            if (!scope.getValue().equals(catalog.get("stateScope")) || !memberships.get(scope.getKey()).equals(catalog.get("membership"))
                    || object(catalog.get("entries")).isEmpty()) return false;
        }
        int materials = object(object(effects.get("materials")).get("entries")).size();
        var registries = object(nativeState.get("nativeMaterialRegistries"));
        if (!"observed".equals(registries.get("status")) || !"FROZEN".equals(registries.get("phase"))
                || !(registries.get("totalRegisteredMaterials") instanceof Number total) || total.intValue() != materials
                || list(registries.get("registries")).isEmpty()) return false;
        long registryCount = 0;
        for (Object row : list(registries.get("registries"))) {
            if (!(object(row).get("registeredMaterials") instanceof Number count) || count.longValue() < 0) return false;
            registryCount += count.longValue();
        }
        if (registryCount != materials) return false;
        var bindings = object(effects.get("materialFluidBindings"));
        if (!"observed".equals(bindings.get("status")) || !Boolean.TRUE.equals(bindings.get("bindingsComplete"))
                || !(bindings.get("affectingGaps") instanceof List<?> gaps) || !gaps.isEmpty()
                || !"materials".equals(bindings.get("fingerprintedIn"))
                || !(bindings.get("materialsWithFluidProperty") instanceof Number count) || count.intValue() <= 0 || count.intValue() > materials) return false;
        var custom = object(nativeState.get("customMetaItems"));
        if (list(custom.get("items")).isEmpty()) return false;
        for (Object item : list(custom.get("items"))) {
            var row = object(item);
            if (!Boolean.TRUE.equals(row.get("nativeClassSpace")) || !Boolean.TRUE.equals(row.get("forgeRegistered"))) return false;
            for (Object variant : list(row.get("variants"))) if (!Boolean.TRUE.equals(object(variant).get("ownerIdentity"))) return false;
        }
        var names = new HashSet<String>();
        for (Object value : list(nativeState.get("nativeMaterialWitnesses"))) {
            var row = object(value);
            if (!(row.get("name") instanceof String name) || !names.add(name) || !Boolean.TRUE.equals(row.get("registryIdentity"))
                    || !"axiom.native-material-property-state.v1".equals(object(row.get("nativePropertyState")).get("schema"))) return false;
        }
        if (!names.equals(Set.of("gregtech:iron", "gregtech:diamond"))) return false;
        for (String domain : List.of("prefixItems", "materialBlocks", "oreBlocks")) {
            var witnesses = list(object(effects.get(domain)).get("witnesses"));
            if (witnesses.isEmpty() || !witnessIdentities(witnesses)) return false;
            for (Object value : witnesses) {
                var row = object(value);
                if (!names.contains(row.get("material")) || !(row.get("generated") instanceof List<?>)) return false;
            }
        }
        // The direct native probe and normal saved-workspace envelope own the
        // same catalog at different paths. Adapt only this local comparison view.
        if("reference".equals(object(effects.get("customItems")).get("status"))
                &&"/result/customMetaItems".equals(object(effects.get("customItems")).get("sourcePointer"))) {
            effects=new LinkedHashMap<>(effects);
            effects.put("customItems",Map.of("status","reference","sourcePointer","/execution/customMetaItems"));
        }
        var execution = Map.<String,Object>of("registrationEffects", effects, "customMetaItems", custom);
        return "unchanged".equals(MaterialRegistrationEffectComparison.compare(execution, execution).get("status"));
    }

    private static boolean witnessIdentities(Object value) {
        if (value instanceof Map<?,?> row) {
            for (String field : List.of("registryIdentity", "blockItemIdentity", "stateRoundTripIdentity", "propertyRoundTripIdentity"))
                if (row.containsKey(field) && !Boolean.TRUE.equals(row.get(field))) return false;
            if (Boolean.FALSE.equals(row.get("empty")) && !Boolean.TRUE.equals(row.get("registryIdentity"))) return false;
            return row.values().stream().allMatch(NativeStartupScope::witnessIdentities);
        }
        return !(value instanceof List<?> rows) || rows.stream().allMatch(NativeStartupScope::witnessIdentities);
    }

    private static boolean explainedFailure(List<Object> failures, List<Object> diagnostics, boolean sourceError) {
        var root = object(failures.getLast());
        if (sourceError && "java.lang.IllegalStateException".equals(root.get("class"))
                && "Original Groovy initialization logged native errors".equals(root.get("message"))) return true;
        return diagnostics.stream().map(NativeStartupScope::object).flatMap(row -> list(object(row.get("causality")).get("exceptions")).stream())
                .map(NativeStartupScope::object).anyMatch(cause -> Objects.equals(root.get("class"), cause.get("type"))
                        && Objects.equals(root.get("message"), cause.get("message")) && locations(cause));
    }
    static boolean sourceError(Map<String,Object> row) {
        return "error".equals(row.get("severity")) && (locations(row)
                || list(row.get("compilerFindings")).stream().map(NativeStartupScope::object).anyMatch(f -> sourceLocation(object(f.get("location"))))
                || list(object(row.get("causality")).get("exceptions")).stream().map(NativeStartupScope::object).anyMatch(NativeStartupScope::locations));
    }
    private static boolean locations(Map<String,Object> row) {
        return list(row.get("locations")).stream().map(NativeStartupScope::object).anyMatch(NativeStartupScope::sourceLocation);
    }
    private static boolean sourceLocation(Map<String,Object> row) {
        return row.get("path") instanceof String path && path.startsWith("groovy/")
                && row.get("line") instanceof Number line && line.intValue() > 0;
    }
    private static void gap(List<Map<String,Object>> gaps, String operation, String reason, String pointer) {
        gaps.add(Map.of("operation", operation, "reason", reason, "evidencePointer", "/execution/nativeInitialization" + pointer));
    }
    private static Map<String,Object> object(Object value) { return value instanceof Map<?,?> ? Json.object(value) : Map.of(); }
    private static List<Object> list(Object value) { return value instanceof List<?> ? Json.array(value) : List.of(); }
}
