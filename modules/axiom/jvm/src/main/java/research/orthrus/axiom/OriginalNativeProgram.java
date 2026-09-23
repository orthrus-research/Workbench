package research.orthrus.axiom;

import java.io.PrintStream;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.*;

/** Shared original initialization entry for the material command and native conformance. */
final class OriginalNativeProgram {
    private static final String HOST = "research.orthrus.axiom.materialhost.";
    private OriginalNativeProgram() {}

    static Map<String,Object> run(List<URL> urls, Path home, String descriptor, Map<String,Object> context,
            Map<String,String> sourceClasses, Set<String> sourceTraits, Map<String,Object> admission) {
        return run(urls, home, descriptor, context, sourceClasses, sourceTraits, admission, "groovy");
    }

    static Map<String,Object> run(List<URL> urls, Path home, String descriptor, Map<String,Object> context,
            Map<String,String> sourceClasses, Set<String> sourceTraits, Map<String,Object> admission, String stage) {
        return run(urls, home, descriptor, context, sourceClasses, sourceTraits, admission, stage, Map.of());
    }

    static Map<String,Object> run(List<URL> urls, Path home, String descriptor, Map<String,Object> context,
            Map<String,String> sourceClasses, Set<String> sourceTraits, Map<String,Object> admission, String stage,
            Map<String,Object> observations) {
        if (!Set.of("groovy", "preinit", "recipes").contains(stage)) throw new IllegalArgumentException("Native initialization stage differs");
        String readiness = stage.equals("recipes") ? "recipeInitializationReady"
                : stage.equals("preinit") ? "preInitializationReady" : "groovyInitializationReady";
        var observed = new LinkedHashMap<String,Object>();
        observed.put("candidateCompilationStarted", false);
        observed.put("executionStage", stage);
        observed.put("groovyInitializationReady", false);
        observed.put(readiness, false);
        PrintStream protocol = System.out, error = System.err;
        var console = new NativeConsoleCapture();
        ClassLoader previous = Thread.currentThread().getContextClassLoader();
        System.setErr(new PrintStream(console, true, StandardCharsets.UTF_8));
        System.setOut(System.err);
        try (var bridge = new URLClassLoader(urls.toArray(URL[]::new), ClassLoader.getPlatformClassLoader())) {
            observed.put("foundationBootstrap", Class.forName(HOST + "NativeFoundationBootstrap", true, bridge)
                    .getMethod("prepare", ClassLoader.class).invoke(null, bridge));
            var type = Class.forName("net.minecraft.launchwrapper.LaunchClassLoader", true, bridge);
            try (var loader = (URLClassLoader)type.getConstructor(URL[].class).newInstance((Object)urls.toArray(URL[]::new))) {
                var inclusion = type.getSuperclass().getDeclaredMethod("addClassLoaderInclusion", String.class);
                inclusion.setAccessible(true); inclusion.invoke(loader, HOST);
                var exclusion = type.getSuperclass().getDeclaredMethod("addClassLoaderExclusion0", String.class);
                exclusion.setAccessible(true);
                for (String name : List.of("NativeEarlyLaunchPrefix", "NativeLoaderPrefix", "NativeServerOwnerPrefix", "NativeGroovyInitializationPrefix"))
                    exclusion.invoke(loader, HOST + name);
                Thread.currentThread().setContextClassLoader(loader);
                Class<?> launch = Class.forName("net.minecraft.launchwrapper.Launch", true, bridge);
                var blackboard = new HashMap<String,Object>();
                blackboard.put("TweakClasses", new ArrayList<String>()); blackboard.put("Tweaks", new ArrayList<Object>());
                blackboard.put("ArgumentList", new ArrayList<String>());
                launch.getField("blackboard").set(null, blackboard); launch.getField("minecraftHome").set(null, home.toFile());
                Class<?> helper = null;
                try {
                    helper = Class.forName(HOST + "NativeEarlyClassSpace", true, loader);
                    if (observations.isEmpty()) {
                        observed.putAll(Json.object(helper.getMethod(stage.equals("recipes") ? "startRecipes"
                                        : stage.equals("preinit") ? "startPreInit" : "startGroovy",
                                String.class, String.class, Map.class, Set.class, Map.class)
                                .invoke(null, descriptor, Json.write(context), sourceClasses, sourceTraits, admission)));
                    } else {
                        if (!Set.of("preinit","recipes").contains(stage)) throw new IllegalArgumentException("Selected observations require native initialization");
                        observed.putAll(Json.object(helper.getMethod(stage.equals("recipes")?"startRecipes":"startPreInit", String.class, String.class,
                                Map.class, Set.class, Map.class, Map.class)
                                .invoke(null, descriptor, Json.write(context), sourceClasses, sourceTraits, admission, observations)));
                    }
                } catch (Exception | LinkageError failure) {
                    if (helper != null) try { observed.putAll(Json.object(helper.getMethod("observations").invoke(null))); }
                    catch (Exception | LinkageError observerFailure) { observed.put("observationFailure", causes(observerFailure)); }
                    observed.put(readiness, false); observed.put("failure", causes(failure));
                }
            }
        } catch (Exception | LinkageError failure) {
            observed.put(readiness, false); observed.put("failure", causes(failure));
        } finally {
            Thread.currentThread().setContextClassLoader(previous); System.setOut(protocol); System.setErr(error);
            var capture = console.snapshot(); observed.put("nativeConsole", capture);
            if (!Boolean.TRUE.equals(capture.get("complete"))) observed.put(readiness, false);
        }
        if(stage.equals("recipes")) {
            var evidence=NativeRecipeScope.evaluate(observed);
            observed.put("recipeInitializationReady",evidence.qualified()&&evidence.checkpointCompleted());
            observed.put("effectiveRecipeRegistryObserved",evidence.qualified()&&evidence.checkpointCompleted());
            observed.put("recipeScopeGaps",evidence.gaps());
            observed.put("nativeDiagnosticAttribution",NativeRecipeScope.diagnosticAttribution(observed));
            observed.put("recipeScopeStatus",evidence.qualified()?(evidence.nativeError()?"native-failed":"completed"):"incomplete");
        }
        return observed;
    }

    static List<Object> causes(Throwable failure) {
        var result = new ArrayList<Object>(); var seen = Collections.newSetFromMap(new IdentityHashMap<Throwable,Boolean>());
        for (Throwable current = failure; current != null && result.size() < 12 && seen.add(current); current = current.getCause()) {
            String message = Objects.toString(current.getMessage(), "");
            var frames = new ArrayList<String>();
            for (var frame : current.getStackTrace()) {
                if (frames.size() == 16) break;
                String text = frame.toString(); frames.add(text.substring(0, Math.min(text.length(), 512)));
            }
            result.add(Map.of("class", current.getClass().getName(), "message", message.substring(0, Math.min(message.length(), 2048)),
                    "frames", frames));
        }
        return result;
    }
}
