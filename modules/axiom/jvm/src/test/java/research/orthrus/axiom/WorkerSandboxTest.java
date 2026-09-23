package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class WorkerSandboxTest {
    @TempDir Path temporary;

    @Test void dockerAndGvisorUseTheSameConfinedWorkerManifest() throws Exception {
        Path input = temporary.resolve("source,\"quoted\".zip");
        Files.writeString(input, "source");
        Path socket = temporary.resolve("docker.sock");
        Files.writeString(socket, "test only");
        Map<String, String> selected = Map.of(
                "axiom.sandbox.docker", Path.of(System.getProperty("java.home"), "bin/java").toString(),
                "axiom.sandbox.dockerHost", "unix://" + socket,
                "axiom.sandbox.image", "docker.io/library/ubuntu@sha256:" + "a".repeat(64),
                "axiom.sandbox.session", "a0f9fbe6-5c19-438d-94fd-aea30ade36a4",
                "axiom.sandbox.user", "1001:1001", "axiom.sandbox.groups", "1001,1002");
        Map<String, String> previous = new java.util.HashMap<>();
        for (var entry : selected.entrySet()) {
            previous.put(entry.getKey(), System.getProperty(entry.getKey()));
            System.setProperty(entry.getKey(), entry.getValue());
        }
        try {
            for (String backend : List.of("docker", "gvisor")) {
                var plan = WorkerSandbox.dockerPlan(backend, List.of(input), List.of(input.toString()),
                        "research.orthrus.axiom.Main", List.of("--worker", "target", "--target", input.toString()));
                List<String> command = plan.command();
                assertTrue(command.contains("--runtime=" + (backend.equals("gvisor") ? "runsc" : "runc")));
                assertTrue(command.contains("--network=none"));
                assertTrue(command.contains("--read-only"));
                assertTrue(command.contains("--cap-drop=ALL"));
                assertTrue(command.contains("--security-opt=no-new-privileges"));
                assertTrue(command.contains("--user=1001:1001"));
                assertTrue(command.contains("--group-add=1002"));
                assertTrue(command.contains("--pull=never"));
                assertTrue(command.contains("--interactive"));
                assertFalse(command.contains("--privileged"));
                assertTrue(command.contains("type=bind,\"source="
                        + input.toRealPath().toString().replace("\"", "\"\"")
                        + "\",target=/axiom/inputs/0,readonly"));
                assertEquals("/axiom/inputs/0", command.get(command.size() - 1));
                assertEquals("--target", command.get(command.size() - 2));
                assertEquals("container", plan.cleanup().get(3));
                assertEquals("container", plan.inventory().get(3));
            }
        } finally {
            for (var entry : previous.entrySet()) {
                if (entry.getValue() == null) System.clearProperty(entry.getKey());
                else System.setProperty(entry.getKey(), entry.getValue());
            }
        }
    }
}
