package research.orthrus.axiom;

import java.nio.file.*;
import java.lang.reflect.*;
import java.util.*;

/** Original source classes and production extraction run in independent native
 * class spaces. Loader/owner/logging ports are declared, not a game harness. */
public final class EventConformance {
    static Map<String,byte[]> read(Path root) throws Exception {
        Map<String,byte[]> out=new LinkedHashMap<>();
        try (var files=Files.walk(root)) {
            for (Path file:files.filter(p->p.toString().endsWith(".class")).sorted().toList())
                out.put(root.relativize(file).toString().replace('/','.').replace(".class",""),Files.readAllBytes(file));
        }
        return out;
    }
    static final class OriginalSpace extends ClassLoader {
        final Map<String,byte[]> definitions;
        final Object first,second;
        final Method firstMethod,secondMethod;
        boolean ready;
        OriginalSpace(Map<String,byte[]> definitions) throws Exception {
            super(EventConformance.class.getClassLoader()); this.definitions=definitions;
            var a=loadClass("net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer");
            var b=loadClass("net.minecraftforge.fml.common.asm.transformers.EventSubscriberTransformer");
            first=a.getConstructor().newInstance(); second=b.getConstructor().newInstance();
            firstMethod=a.getMethod("transform",String.class,String.class,byte[].class);
            secondMethod=b.getMethod("transform",String.class,String.class,byte[].class);
            ready=true;
        }
        @Override protected synchronized Class<?> loadClass(String name,boolean resolve) throws ClassNotFoundException {
            var loaded=findLoadedClass(name);
            if (loaded==null) loaded=definitions.containsKey(name)?findClass(name):super.loadClass(name,false);
            if(resolve)resolveClass(loaded);return loaded;
        }
        @Override protected Class<?> findClass(String name) throws ClassNotFoundException {
            byte[] bytes=definitions.get(name);
            if(bytes==null)throw new ClassNotFoundException(name);
            if(ready && (name.startsWith("fixtureevents.")||name.equals("net.minecraftforge.fml.common.eventhandler.GenericEvent"))) {
                bytes=apply(firstMethod,first,name,bytes);bytes=apply(secondMethod,second,name,bytes);
            }
            return defineClass(name,bytes,0,bytes.length);
        }
        static byte[] apply(Method method,Object receiver,String name,byte[] input) {
            try {return (byte[])method.invoke(receiver,name,name,input);}
            catch(InvocationTargetException failure) {
                if(failure.getCause() instanceof RuntimeException e)throw e;
                if(failure.getCause() instanceof Error e)throw e;
                throw new AssertionError(failure.getCause());
            } catch(ReflectiveOperationException e){throw new AssertionError(e);}
        }
    }
    public static void main(String[] args) throws Exception {
        NativeRuntime.require();
        var original=read(Path.of(args[0]));var candidate=read(Path.of(args[1]));
        List<Map<String,Object>> results=new ArrayList<>();
        for(String scenario:List.of("inheritance","cancel","generic","mutation","context-failure","context-success",
                "registration-errors","subscriber-transform","static-and-shutdown","phase","listener-cache","randomized-cache")) {
            var upstream=new OriginalSpace(original);var engine=new NativeEventSpace(candidate);
            Object expected=upstream.loadClass("fixtureevents.EventScenarios").getMethod("run",String.class).invoke(null,scenario);
            Object actual=engine.loadClass("fixtureevents.EventScenarios").getMethod("run",String.class).invoke(null,scenario);
            if(!Objects.equals(expected,actual))throw new AssertionError(scenario+": "+expected+" != "+actual);
            results.add(Map.of("scenario",scenario,"transcript",actual));
        }
        System.out.println(Json.write(Map.of("schema","axiom.event-conformance.v1","status","accepted","scenarios",results,
                "execution","native-jvm-original-source","materialBootstrapExecuted",false,"wholePackParity",false)));
    }
}
