package research.orthrus.axiom.materialhost;

import com.cleanroommc.groovyscript.GroovyScript;
import com.cleanroommc.groovyscript.sandbox.*;
import java.lang.reflect.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Trusted qualification only: compile the complete program without running it,
 * use the native index writer/reader, then execute once. No synthetic class bytes,
 * registry replay, cache import API or cross-worker cache reuse is introduced.
 */
public final class NativeCompiledCacheProbe {
    private final Map<String,String> compiled = new TreeMap<>();
    private final Map<String,Class<?>> firstClasses = new TreeMap<>();
    private final List<Map<String,Object>> definitions = new ArrayList<>();
    private final Map<String,String> savedSources;
    private final boolean guarded;
    private Map<String,String> savedCache;
    private CustomGroovyScriptEngine executionEngine;

    private NativeCompiledCacheProbe(boolean guarded) throws Exception {
        this.guarded=guarded;
        savedSources=files(GroovyScript.getSandbox().getEngine().getScriptRoot().toPath());
    }
    private static void check(boolean condition,String detail) {
        if(!condition)throw new AssertionError("Native compiled cache: "+detail);
    }
    private static Object field(Class<?> type,Object instance,String name) throws Exception {
        Field field=type.getDeclaredField(name);field.setAccessible(true);return field.get(instance);
    }
    private static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    private static Map<String,String> files(Path root) throws Exception {
        var result=new TreeMap<String,String>();
        try(var paths=Files.walk(root)) {
            for(Path path:paths.filter(Files::isRegularFile).toList())
                result.put(root.relativize(path).toString(),digest(Files.readAllBytes(path)));
        }
        return result;
    }
    @SuppressWarnings("unchecked")
    private static Map<String,Object> records(CustomGroovyScriptEngine engine) throws Exception {
        return (Map<String,Object>)field(CustomGroovyScriptEngine.class,engine,"loadedClasses");
    }
    public static NativeCompiledCacheProbe prepare(boolean guarded,Map<String,String> sourceClasses,
                                                   Map<String,Object> policy) throws Exception {
        var probe=new NativeCompiledCacheProbe(guarded);
        var sandbox=GroovyScript.getSandbox();var compiler=sandbox.getEngine();
        check(records(compiler).isEmpty(),"compiler must start without cached classes");
        compiler.getConfig().setBytecodePostprocessor((name,bytes)-> {
            try {
                check(probe.compiled.putIfAbsent(name,digest(bytes))==null,"duplicate compilation: "+name);
                return bytes; // observational only, retain original compiler bytes
            } catch(Exception failure) {throw new IllegalStateException(failure);}
        });
        sandbox.checkSyntax(); // native run=false: no Script.run or class initializer
        check(GroovyLogImpl.LOG.collectErrors().isEmpty(),"compile-only source errors");
        Class<?> record=Class.forName("com.cleanroommc.groovyscript.sandbox.CompiledClass");
        check(records(compiler).keySet().equals(probe.compiled.keySet()),"compiler callbacks and native records differ");
        check(probe.compiled.keySet().containsAll(sourceClasses.keySet()),"not every complete-program class compiled");
        check(probe.compiled.keySet().stream().anyMatch(name->name.contains("$_")),"native closure bytes absent");
        for(var entry:records(compiler).entrySet()) {
            check(probe.compiled.get(entry.getKey()).equals(digest((byte[])field(record,entry.getValue(),"data"))),
                    "native cache did not retain original bytes");
            probe.firstClasses.put(entry.getKey(),(Class<?>)field(record,entry.getValue(),"clazz"));
            check(probe.firstClasses.get(entry.getKey())!=null,"fresh definition absent");
        }
        Method write=CustomGroovyScriptEngine.class.getDeclaredMethod("writeIndex");write.setAccessible(true);
        write.invoke(compiler); // explicit qualification setup, not a fabricated POST_INIT
        probe.savedCache=files(compiler.getCacheRoot().toPath());
        check(probe.savedCache.containsKey("_index.json"),"native disk index absent");
        var replacement=new GroovyScriptSandbox();
        Field singleton=GroovyScript.class.getDeclaredField("sandbox");singleton.setAccessible(true);singleton.set(null,replacement);
        probe.executionEngine=replacement.getEngine();
        check(records(probe.executionEngine).keySet().equals(probe.compiled.keySet()),"native index reader lost classes");
        for(Object cached:records(probe.executionEngine).values()) {
            check(field(record,cached,"clazz")==null,"cache reader reused compiler class identity");
            check(field(record,cached,"data")==null,"cache reader must read native disk bytes lazily");
        }
        if(guarded)MaterialCallGate.bind(sourceClasses.keySet(),policy);
        replacement.getEngine().getConfig().setBytecodePostprocessor((name,bytes)-> {
            try {
                check(guarded,"original cached route unexpectedly invokes compiler postprocessor");
                check(probe.compiled.get(name).equals(digest(bytes)),"cached bytes differ from actual compiler output");
                boolean ensure=Arrays.stream(Thread.currentThread().getStackTrace()).anyMatch(frame->
                    frame.getClassName().equals("com.cleanroommc.groovyscript.sandbox.CompiledClass")&&frame.getMethodName().equals("ensureLoaded"));
                check(ensure,"execution unexpectedly fell back to recompilation");
                byte[] processed=new MaterialBytecodeGate().processBytecode(name,bytes);
                probe.definitions.add(Map.of("name",name,"inputSha256",digest(bytes),"outputSha256",digest(processed),
                    "route","CompiledClass.ensureLoaded -> GroovyScriptClassLoader.defineClass(String,byte[])"));
                return processed;
            } catch(Exception failure) {throw new IllegalStateException(failure);}
        });
        return probe;
    }
    public Map<String,Object> observe() throws Exception {
        Class<?> record=Class.forName("com.cleanroommc.groovyscript.sandbox.CompiledClass");
        check(records(executionEngine).keySet().equals(compiled.keySet()),"execution class set changed");
        var loaded=new ArrayList<String>();
        for(var entry:records(executionEngine).entrySet()) {
            Class<?> clazz=(Class<?>)field(record,entry.getValue(),"clazz");
            check(clazz!=null&&clazz!=firstClasses.get(entry.getKey()),"cached class identity not independently defined");
            check(clazz.getClassLoader()==executionEngine.getClassLoader(),"not native cached loader identity");
            check(compiled.get(entry.getKey()).equals(digest((byte[])field(record,entry.getValue(),"data"))),"execution changed original cached bytes");
            loaded.add(entry.getKey());
        }
        if(guarded) {
            check(definitions.size()==compiled.size(),"not exactly one configured processor call per cached definition");
            check(definitions.stream().map(row->row.get("name")).collect(java.util.stream.Collectors.toSet()).equals(compiled.keySet()),
                    "cached definition set differs");
            check(MaterialCallGate.violations().isEmpty(),"ordinary source admission changed");
        } else check(definitions.isEmpty(),"original cache must have no definition processor callback");
        check(savedSources.equals(files(executionEngine.getScriptRoot().toPath())),"saved source changed");
        check(savedCache.equals(files(executionEngine.getCacheRoot().toPath())),"execution changed native cache files");
        var result=new LinkedHashMap<String,Object>();
        result.put("actualGroovyCompiledClasses",compiled);result.put("nativeCacheFiles",savedCache);
        result.put("cachedClassesLoaded",loaded.stream().sorted().toList());result.put("definitions",definitions);
        result.put("guarded",guarded);result.put("materialExecutions",1);result.put("registryReplay",false);
        result.put("compilerFallback",false);result.put("savedSourceUnchanged",true);result.put("cacheBytesUnchanged",true);
        result.put("setup","native checkSyntax, explicit native writeIndex, new sandbox/index read before one material execution");
        result.put("installedCacheReuseAvailable",false);return result;
    }
}
