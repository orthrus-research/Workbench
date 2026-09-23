package research.orthrus.axiom;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import research.orthrus.axiom.materialhost.NativeInitializationTrace;

/** Installed worker operation. Native implementation and context are separate trusted inputs. */
final class MaterialProgram {
    private static final Set<String> CONTEXTS=Set.of("supersymmetry:material-authoring-gt-base",
            "supersymmetry:material-authoring-pack");
    private static final String HOST="research.orthrus.axiom.materialhost.";
    private MaterialProgram() {}

    record Program(Map<String,String> sources,List<Map<String,Object>> inventory,String digest,Map<String,Object> scope) {}

    static final String INVENTORY_SCHEMA="axiom.native-source-inventory-ack.v1";
    static final String INVENTORY_ENCODING="sorted-path-recursively-key-sorted-json-utf8-v1";
    static final String INVENTORY_SCOPE="all-submitted-groovy-and-config-files";

    static Map<String,Object> sourceAcknowledgement(Program program) {
        return Map.of("schema",INVENTORY_SCHEMA,"sha256",program.digest(),"fileCount",program.inventory().size(),
                "inventoryEncoding",INVENTORY_ENCODING,"scope",INVENTORY_SCOPE);
    }

    static boolean validSourceAcknowledgement(Object value) {
        if(!(value instanceof Map<?,?> ack)||!ack.keySet().equals(Set.of("schema","sha256","fileCount","inventoryEncoding","scope")))return false;
        if(!INVENTORY_SCHEMA.equals(ack.get("schema"))||!INVENTORY_ENCODING.equals(ack.get("inventoryEncoding"))
                ||!INVENTORY_SCOPE.equals(ack.get("scope"))||!(ack.get("sha256") instanceof String digest)||!digest.matches("[0-9a-f]{64}"))return false;
        if(!(ack.get("fileCount") instanceof Number count))return false;
        try {int size=new java.math.BigDecimal(count.toString()).intValueExact();return size>=0&&size<=4096;}
        catch(ArithmeticException|NumberFormatException failure) {return false;}
    }

    /** Canonical UTF8 inventory: paths sort by Unicode code point, including supplementary characters. */
    static String inventoryDigest(List<Map<String,Object>> inventory) {
        var rows=new ArrayList<String>();
        for(var row:inventory) {
            // Portable archive paths cannot contain backslashes or control characters.
            // Keep supplementary code points as UTF8, matching Core's ensure_ascii=False.
            String path=Json.string(row.get("path")).replace("\"","\\\"");
            rows.add("{\"path\":\""+path+"\",\"sha256\":\""+row.get("sha256")+"\",\"size\":"+row.get("size")+"}");
        }
        return Json.bytesDigest(("["+String.join(",",rows)+"]").getBytes(StandardCharsets.UTF_8));
    }

    static Program unpack(Path archive,Path home,Map<String,Object> context,Set<String> roots) throws IOException {
        var sources=new LinkedHashMap<String,String>();
        var inventory=new ArrayList<Map<String,Object>>();
        var seen=new HashSet<String>();Map<String,Object> loaders=null;int total=0,groovyFiles=0,groovyBytes=0;
        try(var zip=new ZipFile(archive.toFile())) {
            var entries=zip.entries();
            while(entries.hasMoreElements()) {
                var entry=entries.nextElement();String name=entry.getName();
                if(entry.isDirectory()||!seen.add(name)||seen.size()>4096
                        ||!portable(name))
                    throw Failure.request("Program must contain ordinary, unique files beneath groovy/ or config/");
                boolean runConfig=name.equals(context.get("runConfig"));
                byte[] raw;
                try(var input=zip.getInputStream(entry)) {raw=Main.read(input,name.startsWith("config/")?4<<20:1<<20);}
                total+=raw.length;if(total>24<<20)throw Failure.request("Saved initialization inputs exceed twenty-four MiB");
                if(name.startsWith("groovy/")) {
                    groovyBytes+=raw.length;
                    if(++groovyFiles>512||groovyBytes>8<<20)throw Failure.request("Groovy program exceeds 512 files or eight MiB");
                }
                if(runConfig) loaders=validateRunConfig(Json.object(Json.parse(Main.utf8(raw))),context,roots);
                else if(name.startsWith("groovy/")&&name.endsWith(".groovy")) sources.put(name,Main.utf8(raw));
                Path target=home.resolve(name);Files.createDirectories(target.getParent());Files.write(target,raw);
                inventory.add(Map.of("path",name,"sha256",Json.bytesDigest(raw),"size",raw.length));
            }
        }
        if(loaders==null||sources.isEmpty())throw Failure.request("Complete material program requires runConfig.json and Groovy source");
        inventory.sort((left,right)->Arrays.compareUnsigned(((String)left.get("path")).getBytes(StandardCharsets.UTF_8),
                ((String)right.get("path")).getBytes(StandardCharsets.UTF_8)));
        String digest=inventoryDigest(inventory);
        var layout=loaders;
        String stage=initializationStage(context);
        var deferred=layout.keySet().stream().filter(name->!stage.equals("recipes")&&!name.equals(context.get("loader"))).sorted().toList();
        var deferredFiles=sources.keySet().stream().filter(path->deferred.stream().anyMatch(loader->
                Json.array(layout.get(loader)).stream().anyMatch(root->path.startsWith("groovy/"+root)))).sorted().toList();
        var configuration=inventory.stream().filter(row->((String)row.get("path")).startsWith("config/")).toList();
        var scope=new LinkedHashMap<String,Object>(Map.<String,Object>of("declaredLoaders",loaders,"selectedLoader",context.get("loader"),
                "executionBoundary",stage.equals("recipes")?NativeRecipeScope.SCOPE:"preInit-and-material-lifecycle","deferredLoaders",deferred,"deferredSourceFiles",deferredFiles,
                "sourceAvailability","complete-saved-groovy-and-config-directories","structuralAdmission","all-groovy-sources",
                "configuration",Map.of("fileCount",configuration.size(),"sha256",inventoryDigest(configuration),
                        "inventoryReference",Map.of("owner","core-retained-material-program","sourceProgramSha256",digest,"filesPointer","/files"),
                        "application","not-qualified","meaning","captured-configuration-is-not-proof-of-native-application"),
                "meaning","source-availability-is-not-phase-execution; see execution and assessment for observed progress"));
        scope.put("initializationStage",stage);
        return new Program(Collections.unmodifiableMap(sources),List.copyOf(inventory),digest,scope);
    }

    static String initializationStage(Map<String,Object> context) {
        String stage=Json.string(context.getOrDefault("initializationStage","preinit"));
        if(!Set.of("preinit","recipes").contains(stage)
                ||stage.equals("recipes")&&!"supersymmetry:material-authoring-pack".equals(context.get("id")))
            throw Failure.request("Native initialization stage is not covered by the selected profile context");
        return stage;
    }

    private static boolean portable(String name) {
        return (name.startsWith("groovy/")||name.startsWith("config/"))&&!name.endsWith("/")&&!name.contains("\\")&&!name.contains(":")
                &&name.chars().noneMatch(c->c<32||c==127)
                &&Arrays.stream(name.split("/",-1)).noneMatch(part->part.isEmpty()||Set.of(".","..",".git").contains(part));
    }

    private static Map<String,Object> validateRunConfig(Map<String,Object> value,Map<String,Object> context,Set<String> roots) {
        Json.keys(value,"packName","packId","version","debug","loaders");
        if(!Objects.equals(value.get("packId"),context.get("scriptOwner"))||!(value.get("debug") instanceof Boolean))
            throw Failure.request("Saved runConfig must declare the selected script owner and explicit debug setting");
        Json.string(value.get("packName"));Json.string(value.get("version"));
        var loaders=Json.object(value.get("loaders"));
        var allowed=Json.object(context.get("loaders"));
        var configuredRoots=new HashSet<String>();
        for(Object row:allowed.values())for(Object root:Json.array(row))configuredRoots.add(Json.string(root));
        if(!configuredRoots.equals(roots)||!loaders.containsKey(context.get("loader"))||!allowed.keySet().containsAll(loaders.keySet())
                ||loaders.entrySet().stream().anyMatch(entry->!entry.getValue().equals(allowed.get(entry.getKey()))))
            throw new Failure("requires-context","material-program.loaders","Saved loader order is not covered by the selected context");
        return Collections.unmodifiableMap(loaders);
    }

    static List<String> requestedMaterials(Map<String,Object> request,List<Map<String,Object>> expectations) {
        var requested=new ArrayList<String>();
        for(Object value:Json.array(request.getOrDefault("observeMaterials",List.of()))) {
            String name=MaterialExpectations.materialName(value);
            if(requested.contains(name))
                throw Failure.request("Observed materials require unique namespace:name identities");
            requested.add(name);
        }
        for(var check:expectations)if(!requested.contains(check.get("material")))requested.add(Json.string(check.get("material")));
        if(requested.size()>64)throw Failure.request("Select at most 64 material identities to observe");
        // Observation selectors do not control which source or lifecycle executes.
        // Native errors must be available without developer-authored assertions.
        return List.copyOf(requested);
    }

    /** Expose verified immutable mod inputs under original names in a fresh mutable home. */
    static Path linkNativeArtifacts(Path runtime, Path home, Map<String,Path> verifiedFiles) throws IOException {
        Path originalHome = runtime.resolve("native-home").toRealPath();
        for (var file : verifiedFiles.entrySet()) {
            if (!file.getKey().startsWith("native-home/")) continue;
            Path relative = Path.of(file.getKey().substring("native-home/".length()));
            if (relative.getNameCount() != 2 || !relative.startsWith("mods")
                    || !relative.toString().endsWith(".jar")
                    || !file.getValue().equals(originalHome.resolve(relative).toRealPath()))
                throw Failure.request("Native artifact layout differs from verified original mod inputs");
            Path target = home.resolve(relative);
            Files.createDirectories(target.getParent());
            Files.createSymbolicLink(target, file.getValue());
        }
        return originalHome;
    }

    static Map<String,Object> run(Path runtime,Path archive,Object input) throws Exception {
        var stages=new NativeInitializationTrace();
        for(String id:List.of("runtime-verification","source-intake-and-admission","native-bootstrap","material-context"))
            stages.declare(id,"stage","research.orthrus.axiom.MaterialProgram#run");
        stages.begin("runtime-verification");
        var request=Json.object(input);Json.keys(request,"context","contextPolicySha256","admissionPolicySha256","observeMaterials","expectations");
        var expectations=MaterialExpectations.parse(request.get("expectations"));
        var requested=requestedMaterials(request,expectations);
        byte[] manifestRaw=read(runtime.resolve("runtime.json"));
        var manifest=Json.object(Json.parse(Main.utf8(manifestRaw)));
        if(!"axiom.material-runtime.v1".equals(manifest.get("schema")))throw Failure.request("Unknown material runtime package");
        var context=Json.object(manifest.get("context"));
        if(!CONTEXTS.contains(context.get("id"))||!Objects.equals(context.get("id"),request.get("context")))
            throw new Failure("requires-context","material-program.context","Native implementation does not implement the requested context");
        requireRuntimeRoute(context,manifest);
        for(String key:List.of("contextPolicySha256","admissionPolicySha256"))
            if(!Objects.equals(request.get(key),manifest.get(key)))throw Failure.request("Installed profile and runtime policy differ: "+key);
        NativeRuntime.verifyFiles(runtime,Json.array(manifest.get("files")));
        byte[] contextRaw=read(runtime.resolve("context-policy.json")),admissionRaw=read(runtime.resolve("admission-policy.json"));
        if(!Json.bytesDigest(contextRaw).equals(manifest.get("contextPolicySha256"))
                ||!Json.bytesDigest(admissionRaw).equals(manifest.get("admissionPolicySha256")))throw Failure.request("Runtime policy digest differs");
        var contexts=Json.array(Json.object(Json.parse(Main.utf8(contextRaw))).get("contexts"));
        if(contexts.stream().filter(context::equals).count()!=1)throw Failure.request("Runtime context is not in its profile policy");
        var admission=Json.object(Json.parse(Main.utf8(admissionRaw)));
        if(!Objects.equals(context.get("id"),admission.get("context")))throw Failure.request("Runtime admission belongs to another context");
        var roots=new LinkedHashSet<String>();for(Object root:Json.array(admission.get("sourceRoots")))roots.add(Json.string(root));
        stages.returned("runtime-verification");stages.begin("source-intake-and-admission");
        Path home=Files.createTempDirectory("axiom-material-program-");
        var program=unpack(archive,home,context,roots);
        var transforms=new LinkedHashSet<String>();for(Object transform:Json.array(admission.get("sourceTransforms")))transforms.add(Json.string(transform));
        var structure=MaterialSourceAdmission.inspect(program.sources(),roots,transforms);
        stages.returned("source-intake-and-admission");
        var result=new LinkedHashMap<String,Object>();
        result.put("workerStages",stages.snapshot());
        result.put("context",context);result.put("runtimeManifestSha256",Json.bytesDigest(manifestRaw));
        result.put("contextPolicySha256",manifest.get("contextPolicySha256"));
        result.put("admissionPolicySha256",manifest.get("admissionPolicySha256"));
        result.put("sourceProgram",sourceAcknowledgement(program));
        result.put("sourceScope",program.scope());
        result.put("sourceAdmission",structure.json());result.put("runtime",NativeRuntime.require());
        result.put("groovyExecutionQualified",false);result.put("wholePackParity",false);result.put("minecraftLaunched",false);
        result.put("qualification","pending-native-program-acceptance");
        if(!structure.structurallyAdmitted()) {
            result.put("candidateCompilationStarted",false);
            return MaterialProgramAssessment.finish(result,structure,expectations);
        }
        var allowed=new HashSet<String>();for(Object row:Json.array(manifest.get("files")))allowed.add(Json.string(Json.object(row).get("path")));
        var urls=new ArrayList<URL>();var seen=new HashSet<String>();
        for(Object value:Json.array(manifest.get("classpath"))) {
            String path=Json.string(value);
            if(!path.startsWith("lib/")||!path.endsWith(".jar")||!allowed.contains(path)||!seen.add(path))throw Failure.request("Runtime classpath differs from verified inventory");
            urls.add(runtime.resolve(path).toUri().toURL());
        }
        if(manifest.containsKey("nativeInitialization")) {
            var selection=Json.object(manifest.get("nativeInitialization"));
            String stage=initializationStage(context);
            if(!"supersymmetry:material-authoring-pack".equals(context.get("id"))
                    ||!"axiom.original-native-initialization.v1".equals(selection.get("schema"))
                    ||!"raw-original-artifacts".equals(selection.get("inputStage"))
                    ||!(stage.equals("recipes")?NativeRecipeScope.SCOPE:"original-preinit-through-non-recipe-registry-events").equals(selection.get("scope"))
                    ||!allowed.contains("root-class-space.json"))
                throw Failure.request("Original native initialization context or stage differs");
            var nativeContext=new LinkedHashMap<>(Json.object(selection.get("nativeContext")));
            if(!"supersymmetry:required-early".equals(nativeContext.get("id")))
                throw Failure.request("Original native initialization requires its profile-owned pack context");
            var verified=new LinkedHashMap<String,Path>();
            for(String path:allowed)verified.put(path,runtime.resolve(path));
            nativeContext.put("artifactHome",linkNativeArtifacts(runtime,home,verified).toString());
            stages.begin("native-bootstrap");
            var nativeState=OriginalNativeProgram.run(urls,home,Main.utf8(read(runtime.resolve("root-class-space.json"))),
                    nativeContext,structure.classes(),structure.traits(),admission,stage,requested.isEmpty()?Map.of():Map.of(
                            "materials",requested,"fullMaterials",request.getOrDefault("observeMaterials",List.of()),"expectations",expectations));
            nativeState.put("sourceProgram",sourceAcknowledgement(program));
            result.put("bootstrap",Map.of("route","original-native-initialization",
                    "admitted",Boolean.TRUE.equals(nativeState.get(stage.equals("recipes")?"recipeInitializationReady":"preInitializationReady")),
                    "observationPointer","/execution/nativeInitialization"));
            result.put("candidateCompilationStarted",nativeState.get("candidateCompilationStarted"));
            result.put("execution",originalInitializationResult(nativeState));
            stages.returned("native-bootstrap");result.put("workerStages",stages.snapshot());
            var finished=MaterialProgramAssessment.finish(result,structure,expectations);
            Json.object(finished.get("result")).put("execution",NativeStageSnapshots.compactExecution(Json.object(result.get("execution"))));
            return finished;
        }
        Map<String,Object> execution;
        // Native stdout is diagnostic output, never the worker's protocol stream.
        PrintStream protocol=System.out;System.setOut(System.err);
        ClassLoader previous=Thread.currentThread().getContextClassLoader();
        stages.begin("native-bootstrap");
        try(var bridge=new URLClassLoader(urls.toArray(URL[]::new),ClassLoader.getPlatformClassLoader())) {
            var type=Class.forName("net.minecraft.launchwrapper.LaunchClassLoader",true,bridge);
            try(var loader=(URLClassLoader)type.getConstructor(URL[].class).newInstance((Object)urls.toArray(URL[]::new))) {
                var inclusion=type.getSuperclass().getDeclaredMethod("addClassLoaderInclusion",String.class);inclusion.setAccessible(true);
                for(String name:List.of("groovy.","org.codehaus.groovy.","org.apache.groovy.","groovyjarjarasm.","groovyjarjarantlr4.",
                        "gregtech.","codechicken.","supersymmetry.","supercritical.","gregicality.","gregtechfoodoption.","baubles.","dev.tianmi.sussypatches.","com.cleanroommc.configanytime.",HOST))
                    inclusion.invoke(loader,name);
                Thread.currentThread().setContextClassLoader(loader);
                Class<?> launch=Class.forName("net.minecraft.launchwrapper.Launch",true,bridge);
                var blackboard=new HashMap<String,Object>();
                blackboard.put("TweakClasses",new ArrayList<String>());blackboard.put("Tweaks",new ArrayList<Object>());blackboard.put("ArgumentList",new ArrayList<String>());
                launch.getField("blackboard").set(null,blackboard);launch.getField("minecraftHome").set(null,home.toFile());
                var bootstrap=Json.object(Class.forName(HOST+"GroovyNativeBootstrap",true,loader).getMethod("start",String.class).invoke(null,context.get("id")));
                result.put("bootstrap",bootstrap);
                stages.returned("native-bootstrap");result.put("workerStages",stages.snapshot());
                if(!Boolean.TRUE.equals(bootstrap.get("admitted"))) {
                    result.put("candidateCompilationStarted",false);
                    captureTransformations(result,loader);
                    return MaterialProgramAssessment.finish(result,structure,expectations);
                }
                stages.begin("material-context");
                execution=Json.object(Class.forName(HOST+"NativeMaterialProgram",true,loader)
                        .getMethod("run",String.class,List.class,Map.class,Set.class,Map.class)
                        .invoke(null,home.toString(),requested,structure.classes(),structure.traits(),admission));
                result.put("execution",execution);
                stages.returned("material-context");result.put("workerStages",stages.snapshot());
                captureTransformations(result,loader);
            }
        } finally {Thread.currentThread().setContextClassLoader(previous);System.setOut(protocol);}
        return MaterialProgramAssessment.finish(result,structure,expectations);
    }

    /** Preserve original evidence and derive the declared startup checkpoint without replay. */
    static Map<String,Object> originalInitializationResult(Map<String,Object> nativeState) {
        nativeState=new LinkedHashMap<>(nativeState);
        if(nativeState.get("registrationEffects") instanceof Map<?,?> observed) {
            var effects=new LinkedHashMap<>(Json.object(observed));
            if(effects.get("customItems") instanceof Map<?,?> reference
                    &&"reference".equals(reference.get("status"))&&"/result/customMetaItems".equals(reference.get("sourcePointer")))
                effects.put("customItems",Map.of("status","reference","sourcePointer","/execution/customMetaItems"));
            nativeState.put("registrationEffects",effects);
        }
        var execution=new LinkedHashMap<String,Object>();
        execution.put("nativeInitialization",nativeState);
        boolean recipes="recipes".equals(nativeState.get("executionStage"));
        execution.put("scope",recipes?NativeRecipeScope.SCOPE:"original-preinit-through-non-recipe-registry-events");
        var startup=recipes?NativeRecipeScope.evaluate(nativeState):NativeStartupScope.evaluate(nativeState);
        execution.put("executionCompleted",startup.checkpointCompleted());execution.put("cleanObservation",startup.clean());
        execution.put("coverageGaps",startup.gaps());
        for(String field:List.of("registrationEffects","customMetaItems","materials","lookups","missingMaterials","vocabulary",
                "prefixItems","materialBlocks","materialOres","deferredWork","selectedObservations"))
            if(nativeState.containsKey(field))execution.put(field,nativeState.get(field));
        for(String field:List.of("candidateAdmissionViolations","candidateResourceFailure","candidateLinkageFailure"))
            execution.put(field,nativeState.getOrDefault(field,field.equals("candidateAdmissionViolations")?List.of():false));
        execution.put("nativeCompilationFailure",nativeState.getOrDefault("groovyCompilationFailure",false));
        execution.put("scriptIndex",nativeState.getOrDefault("nativeScriptIndex",List.of()));
        execution.put("nativeErrors",nativeState.getOrDefault("nativeGroovyErrors",List.of()));
        var diagnostics=new ArrayList<>(Json.array(nativeState.getOrDefault("nativeDiagnostics",List.of())));
        diagnostics.addAll(Json.array(nativeState.getOrDefault("groovyDiagnostics",List.of())));
        if(nativeState.containsKey("failure"))diagnostics.add(Map.of("channel","native-bootstrap",
                "severity","error","message","Original native initialization did not return cleanly",
                "locationStatus","unlocated","locations",List.of(),"causes",nativeState.get("failure")));
        execution.put("diagnostics",diagnostics);
        return execution;
    }

    static void requireRuntimeRoute(Map<String,Object> context,Map<String,Object> manifest) {
        if("supersymmetry:material-authoring-pack".equals(context.get("id"))&&!manifest.containsKey("nativeInitialization"))
            throw new Failure("requires-context","material-program.original-runtime-required",
                    "The pack context requires its original native runtime; assemble the selected raw SERVER inputs before checking saved source");
    }
    static void captureTransformations(Map<String,Object> result,ClassLoader loader) {
        try {
            result.put("transformations",Class.forName(HOST+"NativeTransformAudit",true,loader).getMethod("observations").invoke(null));
        } catch(ReflectiveOperationException|LinkageError failure) {
            // A broken native transformer can prevent the observer itself from
            // loading. Never discard the original bootstrap failure, diagnostics
            // or saved-source custody while trying to collect secondary evidence.
            var causes=new ArrayList<Map<String,Object>>();
            var seen=Collections.newSetFromMap(new IdentityHashMap<Throwable,Boolean>());
            for(Throwable cause=failure;cause!=null&&causes.size()<12&&seen.add(cause);cause=cause.getCause()) {
                String message=Objects.toString(cause.getMessage(),"");
                causes.add(Map.of("type",cause.getClass().getName(),"message",message.substring(0,Math.min(2048,message.length()))));
            }
            result.put("transformationObservation",Map.of("status","unavailable","causes",causes,
                    "meaning","observer-unavailable-not-an-empty-transformer-audit"));
        }
    }
    private static byte[] read(Path path) throws IOException {try(var input=Files.newInputStream(path)){return Main.read(input,1<<20);}}
}
