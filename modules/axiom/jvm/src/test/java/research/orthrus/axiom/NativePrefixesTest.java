package research.orthrus.axiom;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.Test;
import javax.tools.ToolProvider;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NativePrefixesTest {
    @TempDir Path temporary;
    @ParameterizedTest @ValueSource(booleans = {false, true})
    void completeNativeCatalogContractsInFreshRestrictedJvm(boolean uniqueStones) throws Exception {
        probe(uniqueStones, "normal");
    }
    @Test void unsetSourceFieldKeepsNativeInitializerFailure() throws Exception { probe(false, "null-stone"); }
    private void probe(boolean uniqueStones, String mode) throws Exception {
        String registry = System.getProperty("axiom.test.registryRoot");
        Assumptions.assumeTrue(registry != null, "Use tools/build_axiom.py for pinned native inputs");
        var lock = Json.object(Json.parse(Target.resource("/axiom/native-prefixes.lock.json")));
        var metadata = Json.object(lock.get("fixtureMetadata"));
        var source = temporary.resolve("PrefixConformance.java");
        try (var input = getClass().getResourceAsStream("/PrefixConformance.java")) { Files.write(source, input.readAllBytes()); }
        String classpath = System.getProperty("axiom.test.runtimeClasspath");
        assertEquals(0, ToolProvider.getSystemJavaCompiler().run(null, null, null, "--release", "25", "-proc:none", "-cp",
                classpath, "-d", temporary.toString(), source.toString()));
        var output = temporary.resolve("output"); var error = temporary.resolve("error");
        var builder = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin/java").toString(), "-Xmx256m", "-cp",
                temporary + java.io.File.pathSeparator + classpath, "research.orthrus.axiom.PrefixConformance", registry,
                String.join(",", Json.array(metadata.get("materialFields")).stream().map(Json::string).toList()),
                Boolean.toString(uniqueStones), metadata.get("prefixDeclarations").toString(), metadata.get("iconDeclarations").toString(), mode);
        builder.environment().clear();
        var process = builder.redirectOutput(output.toFile()).redirectError(error.toFile()).start();
        try {
            assertTrue(process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS), "prefix probe timeout");
            assertEquals(0, process.exitValue(), Files.readString(error));
            var result = Json.object(Json.parse(Files.readString(output)));
            if (mode.equals("normal")) assertEquals(4096, ((Number) result.get("vectors")).intValue());
            else assertEquals("rejected-null-stone", result.get("status"));
            assertEquals(false, result.get("wholePackParity"));
            assertEquals(true, result.get("kernelIsolation"));
        } finally { process.destroyForcibly(); }
    }
}
