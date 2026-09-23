package research.orthrus.axiom;

import java.io.*;
import java.nio.charset.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

/** Linux supervisor. Scripts never receive the supervisor's filesystem or environment. */
public final class Main {
    private Main() {}
    public static void main(String[] args) {
        String operation = args.length == 0 ? "" : args[0].equals("--worker") && args.length > 1 ? args[1] : args[0];
        Map<String, Object> result;
        try {
            result = execute(args, operation);
        } catch (Failure failure) { result = Engine.failure(operation, failure); }
        catch (Exception exception) {
            result = Engine.failure(operation, new Failure("execution-error", "engine.failure", exception.getClass().getSimpleName() + ": " + exception.getMessage()));
        }
        String output;
        output=responseOutput(result);
        System.out.print(output);
        String status = (String)result.get("status");
        int code = switch(status) { case "accepted" -> 0; case "rejected", "source-error" -> 1; case "request-error" -> 2; case "unsupported", "requires-context" -> 3; default -> 4; };
        System.exit(code);
    }
    /** MVP resource targets are suspended; retain the complete single or paired result. */
    static String responseOutput(Map<String,Object> result) {
        return Json.write(result)+"\n";
    }
    private static Map<String, Object> execute(String[] args, String operation) throws Exception {
        NativeRuntime.require();
        boolean worker = args.length > 0 && args[0].equals("--worker");
        int offset = worker ? 1 : 0;
        Map<String, Path> inputs = new LinkedHashMap<>();
        if (operation.equals("material-program")) {
            for(int i=offset+1;i<args.length;i+=2) {
                if(i+1>=args.length||!Set.of("--runtime-home","--program","--baseline-program").contains(args[i])||inputs.containsKey(args[i]))
                    throw Failure.request("Material program requires --runtime-home directory and --program source.zip");
                Path path=Path.of(args[i+1]).toAbsolutePath();
                if(Files.isSymbolicLink(path)||!(args[i].equals("--runtime-home")?Files.isDirectory(path):Files.isRegularFile(path)))
                    throw Failure.request("Invalid material runtime directory or source archive");
                inputs.put(args[i],path.toRealPath());
            }
            if(!inputs.containsKey("--runtime-home")||!inputs.containsKey("--program"))throw Failure.request("Material program requires --runtime-home and --program");
            if(worker&&inputs.containsKey("--baseline-program"))throw Failure.request("Each native worker executes exactly one material program");
        } else if (Set.of("target", "platform").contains(operation)) {
            Set<String> allowed = operation.equals("target") ? Set.of("--target", "--artifacts", "--platform") : Set.of("--platform");
            for (int i = offset + 1; i < args.length; i += 2) {
                if (i + 1 >= args.length || !allowed.contains(args[i]) || inputs.containsKey(args[i]))
                    throw Failure.request("Unknown, duplicate or incomplete archive argument");
                Path supplied = Path.of(args[i + 1]).toAbsolutePath();
                if (Files.isSymbolicLink(supplied) || !Files.isRegularFile(supplied)) throw Failure.request("Input archive must be an ordinary file");
                inputs.put(args[i], supplied.toRealPath());
            }
            if (!inputs.containsKey("--" + operation)) throw Failure.request("Missing --" + operation + " input archive");
        } else if (args.length != offset + 1 || !Set.of("coverage", "check", "query").contains(operation))
            throw Failure.request("Usage: axiom coverage | check | query | target --target source.zip [--artifacts mods.zip] [--platform platform.zip] | platform --platform platform.zip");
        if (operation.equals("coverage")) return new Engine().run(operation, null);
        byte[] request = read(System.in, 0);
        if (worker) {
            WorkerIsolation.install();
            Object input = request.length == 0 && !inputs.isEmpty() ? null : Json.parse(utf8(request));
            if (operation.equals("material-program")) return MaterialProgram.run(inputs.get("--runtime-home"),inputs.get("--program"),input);
            if (operation.equals("platform")) return new Engine().inspectPlatform(inputs.get("--platform"), input);
            if (operation.equals("target")) return new Engine().inspectTarget(inputs.get("--target"), input, inputs.get("--artifacts"), inputs.get("--platform"));
            return new Engine().run(operation, input);
        }
        if(operation.equals("material-program")&&inputs.containsKey("--baseline-program")) {
            var baselineInputs=new LinkedHashMap<String,Path>();
            baselineInputs.put("--runtime-home",inputs.get("--runtime-home"));baselineInputs.put("--program",inputs.get("--baseline-program"));
            var baseline=supervise(operation,request,baselineInputs);
            var candidateInputs=new LinkedHashMap<String,Path>(inputs);candidateInputs.remove("--baseline-program");
            var candidate=supervise(operation,request,candidateInputs);
            return MaterialProgramComparison.compare(baseline,candidate);
        }
        return supervise(operation, request, inputs);
    }
    static byte[] read(InputStream stream, int bound) throws IOException {
        if (bound == 0) return stream.readAllBytes();
        byte[] result = stream.readNBytes(bound + 1);
        if (result.length > bound) throw new Failure("incomplete", "transport.byte-bound", "Input or output byte bound exceeded");
        return result;
    }
    static String utf8(byte[] value) {
        try { return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(value)).toString(); }
        catch (CharacterCodingException exception) { throw Failure.request("Invalid UTF-8"); }
    }
    private static Map<String, Object> supervise(String operation, byte[] request, Map<String, Path> inputs) throws Exception {
        List<String> arguments = new ArrayList<>(List.of("--worker", operation));
        inputs.forEach((flag, path) -> arguments.addAll(List.of(flag, path.toString())));
        List<String> command = sandboxCommand(inputs.values(), Arrays.asList(System.getProperty("java.class.path").split(File.pathSeparator)),
                Main.class.getName(), arguments);
        ProcessBuilder builder = new ProcessBuilder(command);
        builder.environment().clear();
        Process child = builder.start();
        return observe(child, request, 0, 0, 0);
    }
    /** Shared with trusted isolation probes; entry point and classpath are never source-request fields. */
    static List<String> sandboxCommand(Collection<Path> inputs, List<String> classpathEntries,
                                       String entryPoint, List<String> arguments) throws IOException {
        if (!System.getProperty("os.name").equals("Linux") || !Files.isExecutable(Path.of("/usr/bin/bwrap")))
            throw new Failure("execution-error", "sandbox.unavailable", "Linux bubblewrap is required; evaluation was not started");
        Path javaHome = Path.of(System.getProperty("java.home")).toRealPath();
        List<String> command = new ArrayList<>(List.of("/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
                "--clearenv", "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--chdir", "/tmp",
                "--setenv", "LANG", "C.UTF-8", "--setenv", "HOME", "/tmp"));
        Set<Path> mounts = new LinkedHashSet<>();
        for (String system : List.of("/lib", "/lib64", "/usr/lib")) if (Files.exists(Path.of(system))) {
            command.addAll(List.of("--ro-bind", system, system));
        }
        mounts.add(javaHome);
        mounts.addAll(inputs);
        List<String> classpath = new ArrayList<>();
        for (String part : classpathEntries) {
            Path path = Path.of(part).toRealPath();
            classpath.add(path.toString()); mounts.add(path);
        }
        for (Path mount : mounts) command.addAll(List.of("--ro-bind", mount.toString(), mount.toString()));
        // Use the selected JVM's ergonomics while MVP resource targets are suspended.
        command.addAll(List.of(javaHome.resolve("bin/java").toString(),
                "-XX:+UseStringDeduplication",
                "-XX:-UsePerfData", "-XX:+DisableAttachMechanism", "--enable-native-access=ALL-UNNAMED",
                "-cp", String.join(File.pathSeparator, classpath), entryPoint));
        command.addAll(arguments);
        return command;
    }
    /** Supervise protocol IO without allowing a full pipe to conceal a resource limit. */
    static Map<String, Object> observe(Process child, byte[] request, int outputLimit, int errorLimit, long timeoutMillis) throws Exception {
        Thread shutdown = new Thread(child::destroyForcibly);
        Runtime.getRuntime().addShutdownHook(shutdown);
        ExecutorService io = Executors.newFixedThreadPool(3);
        AtomicReference<Failure> limit = new AtomicReference<>();
        Future<byte[]> stdout = io.submit(() -> workerRead(child.getInputStream(), outputLimit, child, limit));
        Future<byte[]> stderr = io.submit(() -> workerRead(child.getErrorStream(), errorLimit, child, limit));
        Future<?> writer = io.submit(() -> { try (OutputStream stream = child.getOutputStream()) { stream.write(request); } return null; });
        try {
            if (timeoutMillis == 0) child.waitFor();
            else if (!child.waitFor(timeoutMillis, TimeUnit.MILLISECONDS)) throw new Failure("incomplete", "sandbox.timeout", "Evaluation exceeded " + timeoutMillis + " milliseconds");
            if (limit.get() != null) throw limit.get();
            // A worker may have printed a complete-looking response before a
            // signal/resource termination. That output cannot prove completion.
            if (child.exitValue() >= 128)
                throw new Failure("incomplete", "sandbox.worker-terminated", "Isolated worker terminated without completion (exit " + child.exitValue() + ")");
            byte[] output = completed(stdout);
            String error = utf8(completed(stderr));
            completed(writer);
            if (output.length == 0) {
                throw new Failure("execution-error", "sandbox.worker", "Isolated worker did not return a result (exit " + child.exitValue() + "): " + error);
            }
            Object result = Json.parse(utf8(output));
            Map<String, Object> envelope = Json.object(result);
            if (!"axiom.result.v1".equals(envelope.get("schema"))) throw new Failure("execution-error", "sandbox.protocol", "Worker returned an invalid result");
            if (child.exitValue() != 0) {
                Object status=envelope.get("status");
                int expected=status instanceof String value?switch(value) {
                    case "rejected", "source-error" -> 1;
                    case "request-error" -> 2;
                    case "unsupported", "requires-context" -> 3;
                    case "incomplete", "execution-error" -> 4;
                    default -> -1;
                }:-1;
                if(child.exitValue()!=expected)throw new Failure("incomplete","sandbox.worker-exit",
                        "Worker exit does not support its response (exit "+child.exitValue()+")");
            }
            return envelope;
        } catch (InterruptedException cancelled) {
            Thread.currentThread().interrupt();
            throw new Failure("incomplete","sandbox.cancelled","Evaluation was cancelled");
        } finally {
            child.destroyForcibly();
            for (Closeable stream : List.of(child.getInputStream(), child.getErrorStream(), child.getOutputStream())) {
                try { stream.close(); } catch (IOException ignored) { /* A killed pipe may already be closed. */ }
            }
            io.shutdownNow(); Runtime.getRuntime().removeShutdownHook(shutdown);
        }
    }
    private static byte[] workerRead(InputStream stream, int bound, Process child, AtomicReference<Failure> limit) throws IOException {
        try { return read(stream, bound); }
        catch (Failure failure) {
            limit.compareAndSet(null, failure);
            child.destroyForcibly();
            throw failure;
        }
    }
    private static <T> T completed(Future<T> future) throws Exception {
        try { return future.get(); }
        catch (ExecutionException failure) {
            if (failure.getCause() instanceof Failure boundary) throw boundary;
            if (failure.getCause() instanceof Exception cause) throw cause;
            throw failure;
        }
    }
}
