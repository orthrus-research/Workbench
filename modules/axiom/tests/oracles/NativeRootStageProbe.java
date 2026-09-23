package research.orthrus.axiom;

import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;

/** Trusted supervised initialization qualification; Groovy stage uses existing source admission. */
public final class NativeRootStageProbe {
    private static final String HOST = "research.orthrus.axiom.materialhost.NativeRootClassSpace";
    public static void main(String[] args) throws Exception {
        NativeRuntime.require();
        boolean worker = args.length == 2 && "--worker".equals(args[0]);
        if (!(worker || args.length == 1)) throw new IllegalArgumentException("[--worker] ROOT_PACKAGE_ROOT required");
        Path root = Path.of(args[worker ? 1 : 0]).toAbsolutePath();
        if (Files.isSymbolicLink(root) || !root.equals(root.toRealPath()) || !Files.isDirectory(root))
            throw new IllegalArgumentException("Native root package must be an ordinary absolute directory");
        Map<String,Object> result;
        if (worker) {
            WorkerIsolation.install(); result = execute(root);
        } else {
            String stage = "roots";
            try {
                try (var input = Files.newInputStream(root.resolve("program.json"))) {
                    var program = Json.object(Json.parse(Main.utf8(Main.read(input, 1 << 20))));
                    stage = Json.string(program.getOrDefault("executionStage", "roots"));
                    require(List.of("roots", "early", "selection", "construction", "groovy", "preinit", "recipes").contains(stage), "Native execution stage differs");
                }
                List<String> command = Main.sandboxCommand(List.of(root),
                        Arrays.asList(System.getProperty("java.class.path").split(File.pathSeparator)),
                        NativeRootStageProbe.class.getName(), List.of("--worker", root.toString()));
                var builder = new ProcessBuilder(command); builder.environment().clear();
                result = Main.observe(builder.start(), new byte[0], 0, 0, 0);
            } catch (Exception failure) {
                result = envelope(Map.of("rootStageReady", false, "earlyPipelineReady", false,
                        "executionStage", stage, "stage", "worker-startup", "failure", causes(failure)));
            }
        }
        System.out.print(Main.responseOutput(result));
    }

    private static Map<String,Object> execute(Path root) {
        var observed = new LinkedHashMap<String,Object>();
        observed.put("rootStageReady", false); observed.put("candidateCompilationStarted", false);
        PrintStream protocol = System.out;
        PrintStream originalError = System.err;
        ClassLoader previous = Thread.currentThread().getContextClassLoader();
        try {
            byte[] programRaw;
            try (var input = Files.newInputStream(root.resolve("program.json"))) { programRaw = Main.read(input, 1 << 20); }
            var program = Json.object(Json.parse(Main.utf8(programRaw)));
            require("axiom.native-root-stage-runtime.v1".equals(program.get("schema")), "Native root package schema differs");
            require("SERVER".equals(program.get("side")) && "raw-original-artifacts".equals(program.get("inputStage")),
                    "Native root package side or stage differs");
            String stage = Json.string(program.getOrDefault("executionStage", "roots"));
            require(List.of("roots", "early", "selection", "construction", "groovy", "preinit", "recipes").contains(stage), "Native execution stage differs");
            observed.put("executionStage", stage);
            var nativeContext = Json.object(program.getOrDefault("nativeContext", Map.of()));
            boolean pack = !nativeContext.isEmpty();
            require(!pack || (!stage.equals("roots") && "supersymmetry:required-early".equals(nativeContext.get("id"))),
                    "Native pack context requires an initialization stage");
            require(!(stage.equals("selection") || stage.equals("construction") || stage.equals("groovy") || stage.equals("preinit") || stage.equals("recipes")) || pack, "Native selection requires the explicit pack context");
            observed.put("nativeContext", pack ? nativeContext.get("id") : "root-only");
            var files = new LinkedHashMap<String,Path>();
            for (Object value : Json.array(program.get("files"))) {
                var row = Json.object(value); String name = Json.string(row.get("path"));
                require(!stage.equals("early") || pack || !name.startsWith("native-home/"), "Early root context requires an empty native home");
                Path relative = Path.of(name);
                require(!relative.isAbsolute() && relative.normalize().equals(relative) && !name.contains("\\")
                        && !name.equals(".") && !name.startsWith("../") && !files.containsKey(name), "Native root input path differs");
                Path path = root.resolve(relative);
                require(path.startsWith(root) && path.toRealPath().equals(path) && Files.isRegularFile(path)
                        && Files.size(path) == ((Number)row.get("size")).longValue(), "Native root input path/size differs: " + name);
                require(Json.bytesDigest(Files.readAllBytes(path)).equals(row.get("sha256")), "Native root input digest differs: " + name);
                files.put(name, path);
            }
            require(files.containsKey("root-class-space.json"), "Native root class-space descriptor is not retained");
            var urls = new ArrayList<URL>(); var classpath = new HashSet<String>();
            for (Object value : Json.array(program.get("classpath"))) {
                String name = Json.string(value);
                require(name.startsWith("lib/") && name.endsWith(".jar") && files.containsKey(name) && classpath.add(name),
                        "Native root classpath differs from verified files");
                urls.add(files.get(name).toUri().toURL());
            }
            require(!urls.isEmpty(), "Native root classpath is empty");
            observed.put("programSha256", Json.bytesDigest(programRaw)); observed.put("retainedFileCount", files.size());
            Path home = Files.createTempDirectory("axiom-native-root-home-");
            Map<String,Object> admission = Map.of();
            Map<String,String> sourceClasses = Map.of();
            Set<String> sourceTraits = Set.of();
            if (pack) {
                nativeContext = new LinkedHashMap<>(nativeContext);
                nativeContext.put("artifactHome", MaterialProgram.linkNativeArtifacts(root, home, files).toString());
                observed.put("nativeArtifactPlacement", "links-to-verified-read-only-inputs");
                var context = Json.object(program.get("programContext"));
                var roots = new HashSet<String>();
                for (Object entries : Json.object(context.get("loaders")).values())
                    for (Object entry : Json.array(entries)) roots.add(Json.string(entry));
                String archive = Json.string(program.get("programArchive"));
                require(files.containsKey(archive), "Complete saved program archive is missing");
                var sources = MaterialProgram.unpack(files.get(archive), home, context, roots);
                var acknowledgement = MaterialProgram.sourceAcknowledgement(sources);
                observed.put("sourceProgram", acknowledgement);
                require(Json.write(acknowledgement).equals(Json.write(program.get("sourceProgram"))),
                        "Native saved-source acknowledgement differs");
                if (stage.equals("groovy") || stage.equals("preinit") || stage.equals("recipes")) {
                    require(files.containsKey("admission-policy.json"), "Groovy initialization requires the bound admission policy");
                    admission = Json.object(Json.parse(Files.readString(files.get("admission-policy.json"))));
                    require(context.get("id").equals(admission.get("context")), "Groovy admission context differs");
                    var transforms = new HashSet<String>();
                    for (Object transform : Json.array(admission.get("sourceTransforms"))) transforms.add(Json.string(transform));
                    var structure = MaterialSourceAdmission.inspect(sources.sources(), roots, transforms);
                    observed.put("sourceAdmission", structure.json());
                    require(structure.structurallyAdmitted(), "Complete saved Groovy source was not structurally admitted");
                    sourceClasses = structure.classes(); sourceTraits = structure.traits();
                }
            } else for (var file : files.entrySet()) if (file.getKey().startsWith("native-home/")) {
                Path target = home.resolve(file.getKey().substring("native-home/".length()));
                Files.createDirectories(target.getParent()); Files.copy(file.getValue(), target);
            }
            String descriptor = Files.readString(files.get("root-class-space.json"));
            if (stage.equals("groovy") || stage.equals("preinit") || stage.equals("recipes")) {
                observed.putAll(OriginalNativeProgram.run(urls, home, descriptor, nativeContext, sourceClasses, sourceTraits, admission, stage));
                return envelope(observed);
            }
            System.setOut(System.err);
            try (var bridge = new URLClassLoader(urls.toArray(URL[]::new), ClassLoader.getPlatformClassLoader())) {
                var type = Class.forName("net.minecraft.launchwrapper.LaunchClassLoader", true, bridge);
                try (var loader = (URLClassLoader)type.getConstructor(URL[].class).newInstance((Object)urls.toArray(URL[]::new))) {
                    var inclusion = type.getSuperclass().getDeclaredMethod("addClassLoaderInclusion", String.class);
                    inclusion.setAccessible(true); inclusion.invoke(loader, "research.orthrus.axiom.materialhost.");
                    if (!stage.equals("roots")) {
                        // Extraction ASM belongs to the bridge. The original Foundation
                        // prefix must be the first code to load and patch native ASM.
                        var exclusion = type.getSuperclass().getDeclaredMethod("addClassLoaderExclusion0", String.class);
                        exclusion.setAccessible(true);
                        exclusion.invoke(loader, "research.orthrus.axiom.materialhost.NativeEarlyLaunchPrefix");
                        exclusion.invoke(loader, "research.orthrus.axiom.materialhost.NativeLoaderPrefix");
                        exclusion.invoke(loader, "research.orthrus.axiom.materialhost.NativeServerOwnerPrefix");
                    }
                    Thread.currentThread().setContextClassLoader(loader);
                    Class<?> launch = Class.forName("net.minecraft.launchwrapper.Launch", true, bridge);
                    var blackboard = new HashMap<String,Object>();
                    blackboard.put("TweakClasses", new ArrayList<String>()); blackboard.put("Tweaks", new ArrayList<Object>());
                    blackboard.put("ArgumentList", new ArrayList<String>());
                    launch.getField("blackboard").set(null, blackboard); launch.getField("minecraftHome").set(null, home.toFile());
                    Class<?> helper = null;
                    try {
                        helper = Class.forName(!stage.equals("roots") ? "research.orthrus.axiom.materialhost.NativeEarlyClassSpace" : HOST, true, loader);
                        Object result = !stage.equals("roots")
                                ? helper.getMethod(stage.equals("construction") ? "startConstruction" : stage.equals("selection") ? "startSelection" : "start", String.class, String.class)
                                        .invoke(null, descriptor, Json.write(nativeContext))
                                : helper.getMethod("start", String.class).invoke(null, descriptor);
                        observed.putAll(Json.object(result));
                    } catch (Exception | LinkageError failure) {
                        if (helper != null) {
                            try { observed.putAll(Json.object(helper.getMethod("observations").invoke(null))); }
                            catch (Exception | LinkageError observerFailure) { observed.put("observationFailure", causes(observerFailure)); }
                        }
                        observed.put("rootStageReady", false); observed.put("failure", causes(failure));
                        if (stage.equals("early")) observed.put("earlyPipelineReady", false);
                        if (stage.equals("selection")) observed.put("selectionReady", false);
                        if (stage.equals("construction")) observed.put("constructionReady", false);
                    }
                }
            }
        } catch (Exception | LinkageError failure) { observed.put("failure", causes(failure)); }
        finally {
            Thread.currentThread().setContextClassLoader(previous); System.setOut(protocol); System.setErr(originalError);
        }
        return envelope(observed);
    }

    private static Map<String,Object> envelope(Map<String,Object> observed) {
        var result = new LinkedHashMap<String,Object>(observed);
        result.put("materialInitializationComplete", false); result.put("fullLauncherCompositionQualified", false);
        result.put("minecraftLaunched", false); result.putIfAbsent("candidateCompilationStarted", false);
        String stage = Objects.toString(result.get("executionStage"), "roots");
        String name = stage.equals("roots") ? "root" : stage;
        var diagnostics = new ArrayList<>(Json.array(result.getOrDefault("nativeDiagnostics", List.of())));
        diagnostics.addAll(Json.array(result.getOrDefault("groovyDiagnostics", List.of())));
        Map<String,Object> body = stage.equals("preinit") ? NativeStageSnapshots.compact(result, diagnostics) : result;
        return Map.of("schema", "axiom.result.v1", "operation", "native-" + name + "-stage", "status", "incomplete",
                "result", body, "diagnostics", diagnostics,
                "scope", "original-server-" + name + "-stage-not-material-initialization");
    }
    private static List<Object> causes(Throwable failure) {
        var result = new ArrayList<Object>(); var seen = Collections.newSetFromMap(new IdentityHashMap<Throwable,Boolean>());
        for (Throwable current = failure; current != null && result.size() < 12 && seen.add(current); current = current.getCause()) {
            String message = Objects.toString(current.getMessage(), "");
            var frames = new ArrayList<String>();
            for (var frame : current.getStackTrace()) {
                if (frames.size() == 16) break;
                String text = frame.toString();
                frames.add(text.substring(0, Math.min(text.length(), 512)));
            }
            result.add(Map.of("class", current.getClass().getName(), "message", message.substring(0, Math.min(message.length(), 2048)),
                    "frames", frames));
        }
        return result;
    }
    private static void require(boolean value, String message) { if (!value) throw new IllegalArgumentException(message); }
}
