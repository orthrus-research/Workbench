package research.orthrus.axiom;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

/** Trusted worker launch boundary. Candidate source never supplies a backend or mount. */
final class WorkerSandbox {
    private WorkerSandbox() {}

    record Worker(Process process, List<String> cleanup, List<String> inventory) implements AutoCloseable {
        @Override public void close() throws Exception {
            if (cleanup.isEmpty()) return;
            // Docker owns the container process, so killing its CLI is not enough.
            runControl(cleanup);
            Process remaining = control(inventory);
            if (!remaining.waitFor(10, TimeUnit.SECONDS)) {
                remaining.destroyForcibly();
                throw new Failure("incomplete", "sandbox.cleanup", "Container cleanup could not be verified");
            }
            if (remaining.exitValue() != 0 || remaining.getInputStream().readAllBytes().length != 0)
                throw new Failure("incomplete", "sandbox.cleanup", "An isolated worker container may still be running");
        }
    }

    static Worker launch(Collection<Path> inputs, List<String> classpath, String entryPoint,
                         List<String> arguments) throws Exception {
        String backend = System.getProperty("axiom.sandbox.backend", "bubblewrap");
        if (backend.equals("bubblewrap")) {
            Process child = control(Main.sandboxCommand(inputs, classpath, entryPoint, arguments));
            return new Worker(child, List.of(), List.of());
        }
        if (!backend.equals("docker") && !backend.equals("gvisor"))
            throw new Failure("execution-error", "sandbox.unavailable", "Unknown selected isolation backend");
        DockerPlan plan = dockerPlan(backend, inputs, classpath, entryPoint, arguments);
        Process child = control(plan.command());
        return new Worker(child, plan.cleanup(), plan.inventory());
    }

    static List<String> command(Collection<Path> inputs, List<String> classpath,
                                String entryPoint, List<String> arguments) throws IOException {
        String backend = System.getProperty("axiom.sandbox.backend", "bubblewrap");
        if (backend.equals("bubblewrap")) return Main.sandboxCommand(inputs, classpath, entryPoint, arguments);
        if (backend.equals("docker") || backend.equals("gvisor"))
            return dockerPlan(backend, inputs, classpath, entryPoint, arguments).command();
        throw new Failure("execution-error", "sandbox.unavailable", "Unknown selected isolation backend");
    }

    private static Process control(List<String> command) throws IOException {
        ProcessBuilder builder = new ProcessBuilder(command);
        builder.environment().clear();
        return builder.start();
    }

    private static void runControl(List<String> command) throws Exception {
        Process process = control(command);
        if (!process.waitFor(10, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new Failure("incomplete", "sandbox.cleanup", "Container cleanup timed out");
        }
        // A completed --rm container is already absent; verify through inventory.
    }

    record DockerPlan(List<String> command, List<String> cleanup, List<String> inventory) {}

    static DockerPlan dockerPlan(String backend, Collection<Path> inputs, List<String> classpathEntries,
                                 String entryPoint, List<String> arguments) throws IOException {
        if (!System.getProperty("os.name").equals("Linux") || !System.getProperty("os.arch").equals("amd64"))
            throw new Failure("execution-error", "sandbox.unavailable", "OCI evaluation requires Linux x86_64");
        String executable = System.getProperty("axiom.sandbox.docker", "");
        if (executable.isEmpty() || !Path.of(executable).isAbsolute() || !Files.isExecutable(Path.of(executable)))
            throw new Failure("execution-error", "sandbox.unavailable", "Core must select an executable Docker client");
        String image = System.getProperty("axiom.sandbox.image", "");
        if (!image.matches("[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}"))
            throw new Failure("execution-error", "sandbox.unavailable", "Core must select a digest-pinned Linux x64 image");
        String host = System.getProperty("axiom.sandbox.dockerHost", "");
        if (!host.startsWith("unix:///") || !Files.exists(Path.of(host.substring("unix://".length()))))
            throw new Failure("execution-error", "sandbox.unavailable", "Core must select a local Docker socket");
        String session = System.getProperty("axiom.sandbox.session", "");
        try { if (!UUID.fromString(session).toString().equals(session)) throw new IllegalArgumentException(); }
        catch (IllegalArgumentException failure) {
            throw new Failure("execution-error", "sandbox.unavailable", "Core must supply a sandbox cleanup session");
        }
        String user = System.getProperty("axiom.sandbox.user", "");
        if (!user.matches("[0-9]+:[0-9]+"))
            throw new Failure("execution-error", "sandbox.unavailable", "Core must supply the invoking user identity");
        String groups = System.getProperty("axiom.sandbox.groups", "");
        if (!groups.matches("[0-9]+(,[0-9]+)*"))
            throw new Failure("execution-error", "sandbox.unavailable", "Core must supply the invoking group identities");
        String name = "workbench-axiom-" + UUID.randomUUID();
        List<String> prefix = List.of(executable, "--host", host);
        List<String> command = new ArrayList<>(prefix);
        command.addAll(List.of("run", "--rm", "--interactive", "--pull=never", "--platform=linux/amd64", "--name", name,
                "--label", "org.orthrus.workbench.axiom.session=" + session,
                "--runtime=" + (backend.equals("gvisor") ? "runsc" : "runc"),
                "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--user=" + user, "--ipc=private",
                "--cgroupns=private", "--tmpfs=/tmp:rw,nosuid,nodev", "--workdir=/tmp",
                "--env=LANG=C.UTF-8", "--env=HOME=/tmp"));
        for (String group : groups.split(",")) command.add("--group-add=" + group);
        Path javaHome = Path.of(System.getProperty("java.home")).toRealPath();
        mount(command, javaHome, "/axiom/java");
        Map<String, String> translated = new LinkedHashMap<>();
        int index = 0;
        for (Path input : inputs) {
            Path source = input.toRealPath();
            String target = "/axiom/inputs/" + index++;
            mount(command, source, target);
            translated.put(source.toString(), target);
        }
        List<String> classpath = new ArrayList<>();
        index = 0;
        for (String part : classpathEntries) {
            Path source = Path.of(part).toRealPath();
            String target = "/axiom/classpath/" + index++
                    + (Files.isRegularFile(source) ? "/" + source.getFileName() : "");
            mount(command, source, target);
            classpath.add(target);
        }
        command.addAll(List.of("--entrypoint=/axiom/java/bin/java", image,
                "-XX:+UseStringDeduplication", "-XX:-UsePerfData", "-XX:+DisableAttachMechanism",
                "--enable-native-access=ALL-UNNAMED", "-cp", String.join(File.pathSeparator, classpath), entryPoint));
        for (String argument : arguments) command.add(translated.getOrDefault(argument, argument));
        List<String> cleanup = new ArrayList<>(prefix);
        cleanup.addAll(List.of("container", "rm", "-f", name));
        List<String> inventory = new ArrayList<>(prefix);
        inventory.addAll(List.of("container", "ls", "-aq", "--filter", "name=^/" + name + "$"));
        return new DockerPlan(List.copyOf(command), List.copyOf(cleanup), List.copyOf(inventory));
    }

    private static void mount(List<String> command, Path source, String target) {
        // Docker's --mount parser is CSV: quote the entire source field, not its value.
        String field = "source=" + source;
        command.addAll(List.of("--mount", "type=bind,\"" + field.replace("\"", "\"\"")
                + "\",target=" + target + ",readonly"));
    }
}
