package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.util.*;
import groovy.lang.GroovyClassLoader;
import static org.junit.jupiter.api.Assertions.*;

class NativeRuntimeTest {
    @TempDir Path root;

    @Test void selectedRuntimeExecutesOriginalJavaLibraryMethods() {
        Map<String, Object> runtime = NativeRuntime.require();
        assertEquals("native-jvm", runtime.get("execution"));
        assertEquals("25.0.4+7-LTS", ((Map<?, ?>)runtime.get("observed")).get("runtimeVersion"));
        assertTrue("recipe".equals(new String("recipe")));
        assertSame(String.class, new String[0].getClass().getComponentType());
        assertEquals(false, runtime.get("loadedMemoryAttested"));
    }

    @Test void realGroovyCompilerAndJvmCanExecuteHelpersWithoutMinecraft() throws Exception {
        NativeRuntime.require();
        // Trusted test fixture only. This is NOT a public arbitrary-source execution endpoint.
        try (GroovyClassLoader loader = new GroovyClassLoader(getClass().getClassLoader())) {
            Class<?> fixture = loader.parseClass("class NativeFixture { static int calculate() { def xs = (1..4).collect { it * 2 }; xs.sum() } }");
            assertEquals(20, fixture.getMethod("calculate").invoke(null));
            assertSame(loader, fixture.getClassLoader().getParent());
        }
    }

    @Test void runtimeFilesRejectTamperingAndSymlinks() throws Exception {
        Path file = root.resolve("runtime"); Files.writeString(file, "original");
        List<Object> entries = List.of(Map.of("path", "runtime", "size", 8,
                "sha256", Json.bytesDigest("original".getBytes(java.nio.charset.StandardCharsets.UTF_8))));
        NativeRuntime.verifyFiles(root, entries);
        Files.writeString(file, "tampered");
        Failure failure = assertThrows(Failure.class, () -> NativeRuntime.verifyFiles(root, entries));
        assertEquals("execution-error", failure.kind); assertEquals("runtime.mismatch", failure.rule);
        Files.delete(file); Files.createSymbolicLink(file, Path.of("missing"));
        assertThrows(Failure.class, () -> NativeRuntime.verifyFiles(root, entries));
    }

    @Test void runtimeInventoryRejectsTraversalAndDuplicates() throws Exception {
        for (String path : List.of("../outside", "/absolute", "nested/../runtime", "nested\\runtime"))
            assertThrows(Failure.class, () -> NativeRuntime.verifyFiles(root, List.of(Map.of("path", path))));
        Files.writeString(root.resolve("runtime"), "ok");
        Object entry = Map.of("path", "runtime", "size", 2, "sha256", Json.bytesDigest(new byte[]{111,107}));
        assertThrows(Failure.class, () -> NativeRuntime.verifyFiles(root, List.of(entry, entry)));
    }

    @Test void agentsAndAlternateBootstrapInputsAreNotAdmitted() {
        for (String argument : List.of("-javaagent:agent.jar", "-agentlib:jdwp", "--patch-module=java.base=evil.jar",
                "-Djava.home=/different", "-Xbootclasspath/a:extra.jar", "-Djava.security.properties=other"))
            assertThrows(Failure.class, () -> NativeRuntime.validateArguments(List.of(argument)));
        NativeRuntime.validateArguments(List.of("-Xmx192m", "-ea", "-XX:-UsePerfData"));
    }
}
