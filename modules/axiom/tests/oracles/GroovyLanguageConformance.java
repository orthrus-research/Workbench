package research.orthrus.axiom;

import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;

/** Fixed-corpus compiler discovery, entirely inside the requalified isolated worker. */
public final class GroovyLanguageConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require();
        if (!args[0].equals("worker")) {
            List<String> next=new ArrayList<>(List.of(args));next.set(0,"worker");
            var inputs=new ArrayList<Path>(List.of(Path.of(args[1]),Path.of(args[2])));
            for(int i=3;i<=11;i+=2) inputs.add(Path.of(args[i]));
            var command=Main.sandboxCommand(inputs,Arrays.asList(System.getProperty("java.class.path").split(File.pathSeparator)),
                    GroovyLanguageConformance.class.getName(),next);
            var builder=new ProcessBuilder(command);builder.environment().clear();
            System.out.println(Json.write(Main.observe(builder.start(),new byte[0],8<<20,4<<20,30000)));
            return;
        }
        WorkerIsolation.install();
        PrintStream protocol=System.out;System.setOut(System.err);
        var policy=Json.object(Json.parse(Target.resource("/axiom/native-identity-runtime.json")));
        var urls=new ArrayList<URL>();
        for(String key:List.of("images","libraries")) {
            Path root=Path.of(args[key.equals("images")?1:2]);var rows=Json.array(policy.get(key));
            NativeRuntime.verifyFiles(root,rows);
            for(Object row:rows) urls.add(root.resolve(Json.string(Json.object(row).get("path"))).toUri().toURL());
        }
        for(int i=3;i<=11;i+=2) {
            Path input=Path.of(args[i]);
            if(!Json.bytesDigest(Files.readAllBytes(input)).equals(args[i+1])) throw new IllegalArgumentException("Fixed language input digest differs");
            if(i<11) urls.add(input.toUri().toURL());
        }
        Path home=Files.createTempDirectory("axiom-material-program-");
        // The supervisor supplies only the frozen four-file program archive.
        Set<String> required=Set.of("groovy/runConfig.json","groovy/classes/MaterialEdits.groovy",
                "groovy/material/DeveloperMaterials.groovy","groovy/preInit/Materials.groovy");
        var allowed=new HashSet<>(required);
        // One additional unchanged pack file, only in the fixed trait corpus.
        if(args.length>14&&args[14].equals("trait-reference"))allowed.add("groovy/globals/Sintering.groovy");
        if(args.length>14&&args[14].equals("recipe-map-reference"))allowed.add("groovy/preInit/MetaClassExpansions.groovy");
        if(args.length>14&&args[14].equals("pack-helper-reference")) {
            required=new HashSet<>(required);
            required.addAll(Set.of("groovy/classes/Battery.groovy","groovy/classes/QuenchingFluid.groovy",
                    "groovy/globals/Carbons.groovy","groovy/globals/GroovyUtils.groovy","groovy/globals/Globals.groovy"));
            allowed.addAll(required);
        }
        var seen=new HashSet<String>();
        var sourceFiles=new LinkedHashMap<String,String>();
        try(var zip=new ZipFile(args[11])) {
            var entries=zip.entries();
            while(entries.hasMoreElements()) {
                var entry=entries.nextElement();String name=entry.getName();
                if(!allowed.contains(name)||!seen.add(name)||entry.isDirectory()) throw new IllegalArgumentException("Unqualified corpus entry");
                Path target=home.resolve(name);Files.createDirectories(target.getParent());
                try(var stream=zip.getInputStream(entry)) {
                    byte[] raw=Main.read(stream,1<<20);Files.write(target,raw);
                    if(name.endsWith(".groovy"))sourceFiles.put(name,Main.utf8(raw));
                }
            }
        }
        if(!seen.containsAll(required)) throw new IllegalArgumentException("Incomplete fixed corpus");
        Map<String,Object> result=new LinkedHashMap<>();result.put("stage","source-structure");
        byte[] admissionBytes;
        try(var input=GroovyLanguageConformance.class.getResourceAsStream("/axiom/material-admission.json")) {
            if(input==null)throw new IllegalArgumentException("Trusted profile admission policy absent");
            admissionBytes=Main.read(input,1<<20);
        }
        var admissionPolicy=Json.object(Json.parse(Main.utf8(admissionBytes)));
        var roots=new LinkedHashSet<String>();
        for(Object value:Json.array(admissionPolicy.get("sourceRoots")))roots.add(Json.string(value));
        result.put("admissionPolicySha256",Json.bytesDigest(admissionBytes));
        result.put("admissionContext",Json.string(admissionPolicy.get("context")));
        var transforms=new LinkedHashSet<String>();
        for(Object value:Json.array(admissionPolicy.get("sourceTransforms")))transforms.add(Json.string(value));
        var structure=MaterialSourceAdmission.inspect(sourceFiles,roots,transforms);
        result.put("sourceAdmission",structure.json());
        if(!structure.structurallyAdmitted()||args[13].equals("source-only")) {
            result.put("candidateCompilationStarted",false);result.put("nativeClassSpaceCreated",false);
            write(protocol,result,args[12]);return;
        }
        result.put("stage","native-loader");
        try(var bridge=new URLClassLoader(urls.toArray(URL[]::new),ClassLoader.getPlatformClassLoader())) {
            var type=Class.forName("net.minecraft.launchwrapper.LaunchClassLoader",true,bridge);
            try(var nativeLoader=(URLClassLoader)type.getConstructor(URL[].class).newInstance((Object)urls.toArray(URL[]::new))) {
                var inclusion=type.getSuperclass().getDeclaredMethod("addClassLoaderInclusion",String.class);inclusion.setAccessible(true);
                for(String name:List.of("groovy.","org.codehaus.groovy.","org.apache.groovy.","groovyjarjarasm.",
                        "groovyjarjarantlr4.","gregtech.","codechicken.","baubles.","research.orthrus.axiom.materialtest.","research.orthrus.axiom.materialhost."))
                    inclusion.invoke(nativeLoader,name);
                Thread.currentThread().setContextClassLoader(nativeLoader);
                Class<?> launch=Class.forName("net.minecraft.launchwrapper.Launch",true,bridge);
                var blackboard=new HashMap<String,Object>();
                blackboard.put("TweakClasses",new ArrayList<String>());blackboard.put("Tweaks",new ArrayList<Object>());
                blackboard.put("ArgumentList",new ArrayList<String>());
                launch.getField("blackboard").set(null,blackboard);launch.getField("minecraftHome").set(null,home.toFile());
                result.put("stage","native-transformations");
                Object bootstrap=Class.forName("research.orthrus.axiom.materialtest.GroovyNativeBootstrap",true,nativeLoader)
                        .getMethod("start",String.class).invoke(null,args[13]);
                result.put("bootstrap",bootstrap);
                if(!args[13].equals("missing")) {
                    result.put("stage","native-language-execution");
                    result.put("execution",Class.forName("research.orthrus.axiom.materialtest.GroovyLanguageProbe",true,nativeLoader)
                            .getMethod("run",String.class,String.class,Map.class,Map.class)
                            .invoke(null,home.toString(),args[13],structure.classes(),admissionPolicy));
                }
                result.put("transformations",Class.forName("research.orthrus.axiom.materialtest.NativeTransformAudit",true,nativeLoader)
                        .getMethod("observations").invoke(null));
                result.put("nativeClassSpaceClosed",true);
            }
        } catch(Throwable failure) {
            var writer=new StringWriter();failure.printStackTrace(new PrintWriter(writer));
            result.put("failure",writer.toString());result.put("completed",false);
        }
        write(protocol,result,args[12]);
    }
    private static void write(PrintStream protocol,Map<String,Object> result,String candidateDigest) {
        protocol.println(Json.write(Map.of("schema","axiom.result.v1","result",result,
                "kernelIsolation",true,"namespaceIsolation",true,"groovyExecutionQualified",false,
                "minecraftLaunched",false,"candidateArchiveSha256",candidateDigest)));
    }
}
