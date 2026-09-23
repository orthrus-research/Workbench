package research.orthrus.axiom;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import javax.tools.ToolProvider;
import java.nio.file.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeFluidStacksTest {
    @TempDir Path temporary;

    @ParameterizedTest @ValueSource(strings = {"en", "tr", "source-edit"})
    void nativeFluidStackContractsInFreshRestrictedJvm(String mode) throws Exception {
        String registry = System.getProperty("axiom.test.registryRoot");
        Assumptions.assumeTrue(registry != null, "Use tools/build_axiom.py for pinned native inputs");
        var source = temporary.resolve("FluidStackConformance.java");
        try (var input = getClass().getResourceAsStream("/FluidStackConformance.java")) { Files.write(source, input.readAllBytes()); }
        String classpath = System.getProperty("axiom.test.runtimeClasspath");
        assertEquals(0, ToolProvider.getSystemJavaCompiler().run(null, null, null, "--release", "25", "-proc:none", "-cp",
                classpath, "-d", temporary.toString(), source.toString()));
        var output = temporary.resolve("output"); var error = temporary.resolve("error");
        var builder = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin/java").toString(), "-Xmx256m",
                "-Duser.language=" + (mode.equals("tr") ? "tr" : "en"), "-cp", temporary + java.io.File.pathSeparator + classpath,
                "research.orthrus.axiom.FluidStackConformance", registry, mode);
        builder.environment().clear();
        var process = builder.redirectOutput(output.toFile()).redirectError(error.toFile()).start();
        try {
            assertTrue(process.waitFor(45, java.util.concurrent.TimeUnit.SECONDS), "Fluid stack probe timeout");
            assertEquals(0, process.exitValue(), Files.readString(error));
            var result = Json.object(Json.parse(Files.readString(output)));
            if (mode.equals("source-edit")) assertEquals(7, ((Number) result.get("amount")).intValue());
            else {
                assertEquals(8192, ((Number) result.get("stackVectors")).intValue());
                assertEquals(6, ((Number) result.get("scenarios")).intValue());
                assertEquals(true, result.get("kernelIsolation"));
                assertEquals(false, result.get("minecraftLaunched"));
            }
            assertEquals(false, result.get("wholePackParity"));
        } finally { process.destroyForcibly(); }
    }
}
