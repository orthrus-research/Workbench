package research.orthrus.axiom;

import groovy.lang.GroovyClassLoader;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;

/** Host boundary tests with controlled executable fixtures, never a pack-validity oracle. */
class WorkerIsolationTest {
    @TempDir Path temporary;

    public static class Probe {
        public static class WorkerBase implements IMaterialProperty {
            @Override public void verifyProperty(MaterialProperties properties) {}
        }

        public static void main(String[] args) {
            try { run(args); }
            catch (Throwable failure) { failure.printStackTrace(); System.exit(2); }
        }
        private static void run(String[] args) throws Exception {
            var release = new CountDownLatch(1);
            ExecutorService existing = Executors.newSingleThreadExecutor();
            Future<Boolean> beforeInstallation = existing.submit(() -> {
                release.await();
                try (var socket = new java.net.Socket("127.0.0.1", 9)) { return false; }
                catch (IOException failure) { return denied(failure); }
            });
            NativeRuntime.require();
            WorkerIsolation.install();
            boolean ok;
            String detail = "";
            try (GroovyClassLoader loader = new GroovyClassLoader(Probe.class.getClassLoader())) {
                String mode = args[0];
                if (mode.equals("helpers")) {
                    if (System.getProperty("axiom.probe.state") != null) throw new AssertionError("Worker state leaked");
                    System.setProperty("axiom.probe.state", "used");
                    Class<?> helper = loader.parseClass("class Helper { static int calls = 0; static int value() { assert ++calls == 1; (1..4).collect { it * 2 }.sum() } }");
                    ok = helper.getMethod("value").invoke(null).equals(20);
                    // Thread creation must still work after the process-spawn restriction.
                    try (var after = Executors.newSingleThreadExecutor()) { ok &= after.submit(() -> 7).get() == 7; }
                } else if (mode.equals("material-state")) {
                    var properties = new MaterialState("worker", MaterialPhase.OPEN::canModifyMaterials).getProperties();
                    var key = new PropertyKey<>("worker_only_base", WorkerBase.class);
                    properties.setProperty(key, new WorkerBase());
                    ok = false;
                    try { properties.verify(); }
                    catch (IllegalArgumentException expected) {
                        ok = expected.getMessage().startsWith("Material must have at least one of:");
                    }
                    MaterialProperties.addBaseType(key);
                    properties.verify();
                } else if (mode.equals("native-events")) {
                    Path directory=Files.createTempDirectory("event-probe-");
                    Path source=directory.resolve("EventScenarios.java");
                    try(var input=Probe.class.getResourceAsStream("/events/EventScenarios.java")) {Files.write(source,input.readAllBytes());}
                    int compiled=javax.tools.ToolProvider.getSystemJavaCompiler().run(null,null,null,"--release","25",
                            "-cp",System.getProperty("java.class.path"),"-d",directory.toString(),source.toString());
                    if(compiled!=0)throw new AssertionError("Native event probe compilation failed");
                    Map<String,byte[]> classes=new LinkedHashMap<>();
                    try(var files=Files.walk(directory)) {
                        for(Path path:files.filter(p->p.toString().endsWith(".class")).toList())
                            classes.put(directory.relativize(path).toString().replace('/','.').replace(".class",""),Files.readAllBytes(path));
                    }
                    var space=new NativeEventSpace(classes);
                    Object result=space.loadClass("fixtureevents.EventScenarios").getMethod("run",String.class).invoke(null,"inheritance");
                    ok=result.equals("child-high:HIGH|root-high:HIGH|child-normal:NORMAL|root-normal:NORMAL");
                } else if (mode.equals("subprocess")) {
                    try { new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin/java").toString(), "-version").start(); ok = false; }
                    catch (IOException denied) { ok = true; }
                } else if (mode.equals("network")) {
                    // The already-created executor performs the one cold network
                    // attempt below. A second attempt would test cached JDK class
                    // initialization failure, not the TSYNC kernel boundary.
                    ok = true;
                } else if (mode.equals("private-socket-pair")) {
                    ok = privateSocketPair();
                } else if (mode.equals("write-input")) {
                    try { Files.writeString(Path.of(args[1]), "changed"); ok = false; }
                    catch (IOException denied) { ok = true; }
                } else if (mode.equals("read-host")) {
                    try { Files.readString(Path.of(args[1])); ok = false; }
                    catch (IOException denied) { ok = true; }
                } else if (mode.equals("compile-transform")) {
                    // ASTTest runs during compilation, before any Script.run invocation.
                    try {
                        loader.parseClass("@groovy.transform.ASTTest(value = {\n def process = new ProcessBuilder(['" +
                                System.getProperty("java.home") + "/bin/java', '-version'])\n process.start()\n})\nclass Attack {}");
                        ok = false;
                    } catch (Throwable denied) {
                        ok = denied(denied); detail = denied.toString();
                    }
                } else throw new AssertionError("Unknown controlled probe");
            } finally { release.countDown(); }
            ok &= beforeInstallation.get(2, TimeUnit.SECONDS);
            existing.shutdownNow();
            System.out.println(Json.write(Map.of("schema", "axiom.result.v1", "status", ok ? "accepted" : "rejected", "detail", detail)));
            if (!ok) System.exit(1);
        }

        private static boolean denied(Throwable failure) {
            // Compilation may wrap the original denial. Cold socket initialization
            // must now complete and expose its ordinary IOException to callers.
            for (Throwable cause = failure; cause != null; cause = cause.getCause())
                if (cause.getMessage() != null && cause.getMessage().contains("Operation not permitted")) return true;
            return false;
        }
        private static boolean privateSocketPair() throws Exception {
            var linker=java.lang.foreign.Linker.nativeLinker();var libc=linker.defaultLookup();
            var integer=java.lang.foreign.ValueLayout.JAVA_INT;
            var address=java.lang.foreign.ValueLayout.ADDRESS;
            var pair=linker.downcallHandle(libc.find("socketpair").orElseThrow(),
                    java.lang.foreign.FunctionDescriptor.of(integer,integer,integer,integer,address));
            var socket=linker.downcallHandle(libc.find("socket").orElseThrow(),
                    java.lang.foreign.FunctionDescriptor.of(integer,integer,integer,integer));
            var close=linker.downcallHandle(libc.find("close").orElseThrow(),
                    java.lang.foreign.FunctionDescriptor.of(integer,integer));
            var errno=linker.downcallHandle(libc.find("__errno_location").orElseThrow(),
                    java.lang.foreign.FunctionDescriptor.of(address));
            try(var arena=java.lang.foreign.Arena.ofConfined()) {
                var fds=arena.allocate(8,4);
                if((int)pair.invokeExact(1,1,0,fds)!=0)return false;
                if((int)close.invokeExact(fds.get(integer,0))!=0)return false;
                if((int)close.invokeExact(fds.get(integer,4))!=0)return false;
                for(int[] form:new int[][]{{2,1,0},{1,2,0},{1,1,1}}) {
                    if((int)pair.invokeExact(form[0],form[1],form[2],fds)!=-1)return false;
                    if(((java.lang.foreign.MemorySegment)errno.invokeExact()).reinterpret(4).get(integer,0)!=1)return false;
                }
                for(int domain:new int[]{1,2,10}) {
                    if((int)socket.invokeExact(domain,1,0)!=-1)return false;
                    if(((java.lang.foreign.MemorySegment)errno.invokeExact()).reinterpret(4).get(integer,0)!=1)return false;
                }
                return true;
            } catch(Throwable failure) {throw new Exception(failure);}
        }
    }

    private void probe(String mode, Path input, boolean mount) throws Exception {
        List<String> classpath = Arrays.asList(System.getProperty("axiom.test.runtimeClasspath").split(File.pathSeparator));
        List<String> args = new ArrayList<>(List.of(mode));
        if (input != null) args.add(input.toString());
        var builder = new ProcessBuilder(Main.sandboxCommand(mount ? List.of(input) : List.of(), classpath, Probe.class.getName(), args));
        builder.environment().clear();
        var result = Main.observe(builder.start(), new byte[0], 4096, 65536, 20_000);
        assertEquals("accepted", result.get("status"), result.toString());
    }

    @Test void nativeCompilationHelpersAndThreadsStillWorkWithoutStaticStateLeaking() throws Exception {
        probe("helpers", null, false); probe("helpers", null, false);
    }
    @Test void nativeMaterialBaseTypesDoNotLeakAcrossDisposableWorkers() throws Exception {
        probe("material-state", null, false); probe("material-state", null, false);
    }
    @Test void nativeEventCompilationTransformAndDispatchWorkAfterKernelIsolation() throws Exception {probe("native-events",null,false);}
    @Test void subprocessesAreDeniedAfterJvmStartup() throws Exception { probe("subprocess", null, false); }
    @Test void networkIsDeniedIncludingPreviouslyCreatedThreads() throws Exception { probe("network", null, false); }
    @Test void onlyPrivateUnixStreamPairsAreAdmittedAndColdNetworkingStillFailsNormally() throws Exception {
        probe("private-socket-pair",null,false);
    }
    @Test void compileTimeTransformCannotLaunchAProcess() throws Exception { probe("compile-transform", null, false); }
    @Test void hostFilesAreAbsentAndExplicitInputsAreReadOnly() throws Exception {
        Path file = temporary.resolve("host-only.txt"); Files.writeString(file, "sentinel");
        probe("read-host", file, false);
        probe("write-input", file, true);
        assertEquals("sentinel", Files.readString(file));
    }
}
