package research.orthrus.axiom;

import java.net.URI;
import java.nio.file.Path;
import java.util.*;

/** Assess the declared native recipe checkpoint and its passive observations.
 * A completed scope can contain native failures; those never become a clean pass. */
final class NativeRecipeScope {
    static final String SCOPE="original-preinit-through-available-recipe-registries";
    private NativeRecipeScope() {}

    static boolean selected(Map<String,Object> result) {
        return "supersymmetry:material-authoring-pack".equals(object(result.get("context")).get("id"))
                && "original-native-initialization".equals(object(result.get("bootstrap")).get("route"))
                && "recipes".equals(object(result.get("sourceScope")).get("initializationStage"));
    }

    static NativeStartupScope.Evidence evaluate(Map<String,Object> state) {
        if("axiom.native-preinit-stage.v1".equals(state.get("schema")))
            return NativeStartupScope.recipePrefixFailure(state);
        var gaps=new ArrayList<Map<String,Object>>();
        if(!"axiom.native-recipe-stage.v1".equals(state.get("schema"))
                ||!"supersymmetry:required-early".equals(state.get("nativeContext"))
                ||!"SERVER".equals(state.get("side"))||!"DEFAULT".equals(state.get("mixinPhase"))
                ||!"recipes".equals(state.get("executionStage"))||!Boolean.FALSE.equals(state.get("minecraftLaunched")))
            gap(gaps,"recipe-context","The declared original SERVER recipe context was not established","");
        for(String key:List.of("earlyPipelineReady","selectionReady","constructionMethodReturned","nativeHomeInitialized",
                "nativeDiagnosticsComplete","nativeMapperAdmissionBound","nativeRecipePropertyAdmissionBound",
                "candidateCompilationStarted","groovyInitializationReturned","preInitializationReturned",
                "preInitializationDispatchStarted","preInitializationDispatched","preInitializationDispatchReturned",
                "nonRecipeRegistryEventsReturned"))
            if(!Boolean.TRUE.equals(state.get(key)))gap(gaps,key,"Required original recipe prerequisite is absent: "+key,"/"+key);
        if(!(state.get("modPreInitDispatchCount") instanceof Number count)||count.intValue()!=1)
            gap(gaps,"preinit-dispatch","The original preInit dispatch was not observed exactly once","/modPreInitDispatchCount");
        for(String key:List.of("nativeDiagnostics","groovyDiagnostics","nativeGroovyErrors","nativeScriptIndex","candidateAdmissionViolations"))
            if(!(state.get(key) instanceof List<?>))gap(gaps,key,"Required native evidence is unavailable: "+key,"/"+key);
        if(!Boolean.TRUE.equals(object(state.get("nativeConsole")).get("complete")))
            gap(gaps,"native-console","Original native console evidence is incomplete","/nativeConsole");
        for(String key:List.of("observationFailure","constructionObservationFailure","constructionEffectsObservationFailure","groovyObservationFailure"))
            if(state.containsKey(key))gap(gaps,key,"Original observations could not be completed","/"+key);
        if(!list(state.get("candidateAdmissionViolations")).isEmpty()
                ||!Boolean.FALSE.equals(state.get("candidateResourceFailure"))||!Boolean.FALSE.equals(state.get("candidateLinkageFailure")))
            gap(gaps,"native-context-operation","Saved execution encountered an unavailable operation, linkage or resource","/candidateAdmissionViolations");
        var groovy=object(state.get("nativeGroovyInitialization"));
        if(!Boolean.TRUE.equals(groovy.get("modSupportFrozen"))||!Boolean.TRUE.equals(groovy.get("scriptOwnerIsOriginalContainer"))
                ||!"supersymmetry".equals(groovy.get("scriptOwner")))
            gap(gaps,"groovy-owner","Original Groovy compatibility and saved script ownership were not established","/nativeGroovyInitialization");
        boolean configuration=NativeStartupScope.configurationApplied(object(state.get("nativeConfiguration")));
        if(!configuration)gap(gaps,"native-configuration","Original selected configuration bindings are incomplete","/nativeConfiguration");
        if(!NativeStartupScope.effectsComplete(state))gap(gaps,"native-startup-effects",
                "Complete original material, generated item and fluid prerequisites are unavailable","/registrationEffects");
        var server=object(state.get("nativeServerOwner"));
        if(!"original-dedicated-server-construction".equals(server.get("mode"))
                ||!"net.minecraft.server.dedicated.DedicatedServer".equals(server.get("serverClass"))
                ||!"SERVER".equals(server.get("threadGroup"))
                ||!List.of("constructorReturned","nativeDefiningLoader","originalOwnershipPrefixReturned","gameThreadAbsent").stream()
                    .allMatch(key->Boolean.TRUE.equals(server.get(key)))
                ||!Boolean.FALSE.equals(server.get("snooperStarted"))
                ||!List.of("networkConnectionCount","networkEndpointCount","worldCount").stream()
                    .allMatch(key->server.get(key) instanceof Number count&&count.longValue()==0))
            gap(gaps,"native-server-owner","The original contained SERVER owner was not established","/nativeServerOwner");
        boolean bop=list(state.get("selectedMods")).stream().map(NativeRecipeScope::object).anyMatch(row->"biomesoplenty".equals(row.get("id")));
        for(String key:bop?List.of("nativeBiomes","nativeWorldgenBiomeBindings"):List.of("nativeWorldgenBiomeBindings")) {
            var value=object(state.get(key));
            if(!"observed".equals(value.get("status"))||!Boolean.TRUE.equals(value.get("observationsComplete"))
                    ||!(value.get("affectingGaps") instanceof List<?> observedGaps)||!observedGaps.isEmpty())
                gap(gaps,"native-biome-inputs","Original selected biome registrations or saved GT bindings are incomplete","/"+key);
        }

        var diagnostics=new ArrayList<>(list(state.get("nativeDiagnostics")));diagnostics.addAll(list(state.get("groovyDiagnostics")));
        boolean error=!list(state.get("nativeGroovyErrors")).isEmpty()||diagnostics.stream().map(NativeRecipeScope::object)
                .anyMatch(row->"error".equals(row.get("severity")));
        boolean sourceError=diagnostics.stream().map(NativeRecipeScope::object).anyMatch(NativeStartupScope::sourceError);
        boolean compilerError=Boolean.TRUE.equals(state.get("groovyCompilationFailure"))&&diagnostics.stream().map(NativeRecipeScope::object)
                .anyMatch(row->"error".equals(row.get("severity"))&&NativeStartupScope.sourceError(Map.of(
                        "severity","error","compilerFindings",list(row.get("compilerFindings")))));
        var index=list(state.get("nativeScriptIndex"));
        if(index.isEmpty()||index.stream().map(NativeRecipeScope::object).anyMatch(row->
                Boolean.TRUE.equals(row.get("preprocessorCheckFailed"))?list(row.get("preprocessors")).isEmpty()
                        :!Boolean.FALSE.equals(row.get("preprocessorCheckFailed"))||!Boolean.TRUE.equals(row.get("classDefined"))&&!compilerError))
            gap(gaps,"native-source-coverage","Original processing did not cover every applicable saved script","/nativeScriptIndex");
        if(Boolean.TRUE.equals(state.get("groovyCompilationFailure"))&&!compilerError)
            gap(gaps,"native-compiler","Native compilation failed without supported saved-source evidence","/groovyDiagnostics");
        var attribution=diagnosticAttribution(state);
        for(var row:attribution)if("unattributed".equals(row.get("origin")))
            gap(gaps,"unattributed-native-error","A native error lacks a saved-source location or an original artifact caller",(String)row.get("nativePointer"));
        if(error&&attribution.isEmpty())gap(gaps,"native-error-evidence","Native error storage has no corresponding diagnostic evidence","/nativeGroovyErrors");
        if(!list(state.get("failure")).isEmpty())gap(gaps,"native-bootstrap-failure",
                "Original initialization or its observer did not return; retained effects may be partial","/failure");

        boolean checkpoint=Boolean.TRUE.equals(state.get("recipeInitializationStarted"))
                &&Boolean.TRUE.equals(state.get("recipeInitializationReturned"))&&"AVAILABLE".equals(state.get("loaderState"));
        if(!checkpoint)gap(gaps,"native-recipe-checkpoint","Original recipe initialization did not reach AVAILABLE","/recipeInitializationReturned");
        for(var phase:Map.of("constructedMods","CONSTRUCTED","preInitializedMods","PREINITIALIZED","initializedMods","AVAILABLE").entrySet())
            if(!modCheckpoint(state,phase.getKey(),phase.getValue()))gap(gaps,"native-mod-checkpoint",
                    "Selected mods did not complete their original ordered checkpoint: "+phase.getValue(),"/"+phase.getKey());
        for(var entry:Map.of("nativeStoredRecipes","axiom.native-stored-recipes.v1",
                "nativeStoredCraftingRecipes","axiom.native-stored-crafting-recipes.v1",
                "nativeStoredFurnaceRecipes","axiom.native-stored-furnace-recipes.v1").entrySet())
            if(!completeStorage(object(state.get(entry.getKey())),entry.getValue()))gap(gaps,"native-recipe-storage",
                    "Effective native recipe storage is incomplete: "+entry.getKey(),"/"+entry.getKey());
        if(object(object(state.get("nativeStoredRecipes")).get("maps")).isEmpty())
            gap(gaps,"native-recipe-maps","Original GT map lookup observations are absent","/nativeStoredRecipes/maps");
        if(!craftingComplete(object(state.get("nativeStoredCraftingRecipes"))))gap(gaps,"native-crafting-bindings",
                "Crafting names, numeric lookup order or shared stored values are incomplete","/nativeStoredCraftingRecipes");
        var furnace=object(state.get("nativeStoredFurnaceRecipes"));
        for(String key:List.of("smelting","experience","timeWildcard","timeMetadata","fuelConversions"))
            if(!(furnace.get(key) instanceof List<?>))gap(gaps,"native-furnace-storage","Original furnace storage is missing: "+key,"/nativeStoredFurnaceRecipes/"+key);
        for(String key:List.of("timeWildcardDefault","timeMetadataDefault"))
            if(!furnace.containsKey(key))gap(gaps,"native-furnace-default","Original furnace map default is unavailable","/nativeStoredFurnaceRecipes/"+key);
        return new NativeStartupScope.Evidence(checkpoint,configuration,sourceError,error,List.copyOf(gaps));
    }

    static List<Map<String,Object>> diagnosticAttribution(Map<String,Object> state) {
        var paths=new HashSet<String>();
        for(String key:List.of("nativeArtifacts","nativeContainedArtifacts"))for(Object value:object(state.get(key)).values()) {
            var row=object(value);
            if(row.get("sha256") instanceof String hash&&hash.matches("[a-f0-9]{64}"))addPath(paths,row.get("codeSource"));
        }
        for(Object source:object(state.get("artifactCodeSources")).values())addPath(paths,source);
        var rows=new ArrayList<Map<String,Object>>();
        for(String channel:List.of("nativeDiagnostics","groovyDiagnostics")) {
            var diagnostics=list(state.get(channel));
            for(int index=0;index<diagnostics.size();index++) {
                var diagnostic=object(diagnostics.get(index));if(!"error".equals(diagnostic.get("severity")))continue;
                var row=new LinkedHashMap<String,Object>();row.put("nativePointer","/"+channel+"/"+index);
                String origin="unattributed";
                if(!diagnostic.containsKey("nativeOriginFailure")) {
                    if(NativeStartupScope.sourceError(diagnostic))origin="saved-source";
                    else for(Object value:list(diagnostic.get("nativeOrigins"))) {
                        var frame=object(value);String source=sourcePath(frame.get("codeSource"));
                        if(source!=null&&paths.contains(source)&&frame.get("class") instanceof String name&&!name.isBlank()) {
                            origin="original-native-artifact";row.put("nativeCaller",frame);break;
                        }
                    }
                }
                row.put("origin",origin);rows.add(Collections.unmodifiableMap(row));
            }
        }
        return List.copyOf(rows);
    }
    private static void addPath(Set<String> paths,Object value) {String path=sourcePath(value);if(path!=null)paths.add(path);}
    private static String sourcePath(Object value) {
        if(!(value instanceof String source))return null;
        try {
            if(source.startsWith("jar:"))source=source.substring(4,source.indexOf("!/"));
            if(source.startsWith("file:"))return Path.of(URI.create(source)).normalize().toString();
            return source.startsWith("/")?Path.of(source).normalize().toString():null;
        } catch(IllegalArgumentException|IndexOutOfBoundsException malformed) {return null;}
    }
    private static boolean modCheckpoint(Map<String,Object> state,String key,String expected) {
        var selected=list(state.get("selectedMods")).stream().map(NativeRecipeScope::object).map(row->row.get("id")).toList();
        var rows=list(state.get(key)).stream().map(NativeRecipeScope::object).toList();
        return !selected.isEmpty()&&new HashSet<>(selected).size()==selected.size()
                &&selected.equals(rows.stream().map(row->row.get("id")).toList())&&rows.stream().allMatch(row->expected.equals(row.get("state")));
    }
    private static boolean completeStorage(Map<String,Object> value,String schema) {
        return schema.equals(value.get("schema"))&&"observed".equals(value.get("status"))
                &&Boolean.TRUE.equals(value.get("storedValuesComplete"))&&value.get("affectingGaps") instanceof List<?> gaps&&gaps.isEmpty();
    }
    private static boolean craftingComplete(Map<String,Object> value) {
        var entries=object(value.get("entries"));var values=object(value.get("nativeValues"));var seen=new HashSet<String>();long previous=-1;
        if(entries.isEmpty()||values.isEmpty()||!Boolean.TRUE.equals(value.get("registryFrozen")))return false;
        for(Object item:list(value.get("nativeLookupOrder"))) {
            var row=object(item);if(!(row.get("id") instanceof Number id)||id.longValue()<=previous
                    ||!(row.get("key") instanceof String key)||!entries.containsKey(key)||!seen.add(key))return false;
            previous=id.longValue();
        }
        return seen.equals(entries.keySet())&&referencesPresent(entries,values)&&referencesPresent(values,values);
    }
    private static boolean referencesPresent(Object value,Map<String,Object> catalog) {
        if(value instanceof Map<?,?> map) {
            if(map.containsKey("nativeValueRef"))return map.size()==1&&map.get("nativeValueRef") instanceof String id&&catalog.containsKey(id);
            return map.values().stream().allMatch(item->referencesPresent(item,catalog));
        }
        return !(value instanceof List<?> list)||list.stream().allMatch(item->referencesPresent(item,catalog));
    }
    private static void gap(List<Map<String,Object>> gaps,String operation,String reason,String pointer) {
        gaps.add(Map.of("operation",operation,"reason",reason,"evidencePointer","/execution/nativeInitialization"+pointer));
    }
    private static Map<String,Object> object(Object value) {return value instanceof Map<?,?>?Json.object(value):Map.of();}
    private static List<Object> list(Object value) {return value instanceof List<?>?Json.array(value):List.of();}
}
