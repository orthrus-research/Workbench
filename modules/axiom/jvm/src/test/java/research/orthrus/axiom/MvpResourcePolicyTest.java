package research.orthrus.axiom;

import java.io.File;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class MvpResourcePolicyTest {
    public static class Probe {
        public static void main(String[] args) throws Exception {
            var before=Files.readAllLines(Path.of("/proc/self/limits"));
            WorkerIsolation.install();
            var after=Files.readAllLines(Path.of("/proc/self/limits"));
            for (String resource : List.of("Max cpu time", "Max file size", "Max open files", "Max address space")) {
                String inherited=before.stream().filter(row->row.startsWith(resource)).findFirst().orElseThrow();
                if (!after.contains(inherited)) throw new AssertionError("MVP changed inherited resource: "+resource);
            }
            System.out.print(Json.write(Map.of("schema","axiom.result.v1","status","accepted","limits",after)));
        }
    }
    @Test void workerInheritsHostResourcesAndUsesJvmErgonomics() throws Exception {
        var classpath=Arrays.asList(System.getProperty("axiom.test.runtimeClasspath").split(File.pathSeparator));
        var command=WorkerSandbox.command(List.of(),classpath,
                Probe.class.getName(),List.of());
        for (String argument : command) assertFalse(argument.matches("-(Xmx|Xms|Xss|XX:(MaxMetaspaceSize|MaxDirectMemorySize|ReservedCodeCacheSize|ActiveProcessorCount)=).*"),argument);
        try (var worker=WorkerSandbox.launch(List.of(),classpath,Probe.class.getName(),List.of())) {
            assertEquals("accepted",Main.observe(worker.process(),new byte[0],0,0,0).get("status"));
        }
    }
}
