package research.orthrus.axiom;

import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;

/** Isolated native Foundation/CleanMix linkage; no candidate compiler invocation. */
public final class GroovyTransformConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require();
        if (!args[0].equals("worker")) {
            List<String> next = new ArrayList<>(List.of(args)); next.set(0,"worker");
            var command = Main.sandboxCommand(List.of(Path.of(args[1]),Path.of(args[2]),Path.of(args[3]),Path.of(args[5])),
                    Arrays.asList(System.getProperty("java.class.path").split(File.pathSeparator)),
                    GroovyTransformConformance.class.getName(),next);
            System.out.println(Json.write(Main.observe(new ProcessBuilder(command).start(),new byte[0],8<<20,2<<20,30000)));
            return;
        }
        WorkerIsolation.install();
        PrintStream protocol = System.out; System.setOut(System.err);
        var policy = Json.object(Json.parse(Target.resource("/axiom/native-identity-runtime.json")));
        var urls = new ArrayList<URL>();
        for (String key : List.of("images","libraries")) {
            Path root = Path.of(args[key.equals("images")?1:2]);
            var rows = Json.array(policy.get(key)); NativeRuntime.verifyFiles(root,rows);
            for (Object row : rows) urls.add(root.resolve(Json.string(Json.object(row).get("path"))).toUri().toURL());
        }
        for (int index : List.of(3,5)) {
            Path input = Path.of(args[index]);
            if (!Json.bytesDigest(Files.readAllBytes(input)).equals(args[index+1]))
                throw new IllegalArgumentException("Groovy transform input digest differs");
            urls.add(input.toUri().toURL());
        }
        try (var bridge = new URLClassLoader(urls.toArray(URL[]::new),ClassLoader.getPlatformClassLoader())) {
            var type = Class.forName("net.minecraft.launchwrapper.LaunchClassLoader",true,bridge);
            try (var nativeLoader = (URLClassLoader)type.getConstructor(URL[].class).newInstance((Object)urls.toArray(URL[]::new))) {
                // Groovy and Axiom native program classes must never fall back to
                // the bridge's untransformed copy. Shared native library exclusions
                // remain those of the selected Foundation implementation.
                var inclusion = type.getSuperclass().getDeclaredMethod("addClassLoaderInclusion",String.class);
                inclusion.setAccessible(true);
                for (String name : List.of("groovy.","org.codehaus.groovy.","org.apache.groovy.",
                        "groovyjarjarasm.","groovyjarjarantlr4.","gregtech.","research.orthrus.axiom.materialtest."))
                    inclusion.invoke(nativeLoader,name);
                Thread.currentThread().setContextClassLoader(nativeLoader);
                Class<?> launch = Class.forName("net.minecraft.launchwrapper.Launch",true,bridge);
                var blackboard = new HashMap<String,Object>();
                blackboard.put("TweakClasses",new ArrayList<String>());
                blackboard.put("Tweaks",new ArrayList<Object>());
                blackboard.put("ArgumentList",new ArrayList<String>());
                launch.getField("blackboard").set(null,blackboard);
                launch.getField("minecraftHome").set(null,new File("/tmp"));
                Object result = Class.forName("research.orthrus.axiom.materialtest.GroovyTransformProbe",true,nativeLoader)
                        .getMethod("run",String.class).invoke(null,args[7]);
                protocol.println(Json.write(Map.of("schema","axiom.result.v1","result",result,
                        "kernelIsolation",true,"namespaceIsolation",true,"groovyExecutionQualified",false,
                        "minecraftLaunched",false)));
            }
        }
    }
}
