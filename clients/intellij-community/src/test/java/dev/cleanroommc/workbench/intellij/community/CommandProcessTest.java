package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.progress.ProcessCanceledException;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class CommandProcessTest {
    @Test
    void suspendedTargetsRetainCompleteLargeOutput() throws Exception {
        Path shell = Path.of("/bin/sh");
        org.junit.jupiter.api.Assumptions.assumeTrue(Files.isExecutable(shell));
        String result = CommandProcess.capture(CoreLaunch.resolve(shell.toString(), false, null),
                List.of("-c", "head -c 34603008 /dev/zero"), 0, 0, null);
        assertEquals("\0".repeat(33 * 1024 * 1024), result);
    }
    @Test
    void processEnvironmentDropsUnrelatedAndCredentialVariables() {
        Map<String, String> source = new LinkedHashMap<>();
        source.put("PATH", "/usr/bin");
        source.put("LANG", "C.UTF-8");
        source.put("WORKBENCH_CLEANROOM_FIXTURE_GRADLEW", "/opt/gradle/bin/gradle");
        source.put("WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME", "/opt/jdk");
        source.put("WORKBENCH_STATE_ROOT", "/var/lib/workbench-state");
        source.put("FIRECRAWL_API_KEY", "secret");
        source.put("JAVA_TOOL_OPTIONS", "-javaagent:untrusted.jar");
        assertEquals(
                Map.of(
                        "PATH", "/usr/bin",
                        "LANG", "C.UTF-8",
                        "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW", "/opt/gradle/bin/gradle",
                        "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME", "/opt/jdk",
                        "WORKBENCH_STATE_ROOT", "/var/lib/workbench-state"
                ),
                CommandProcess.scrubbedEnvironment(source)
        );
    }

    @Test
    void successfulProcessWithStderrFailsTheSingleChannelBoundary() {
        String java = Path.of(System.getProperty("java.home"), "bin", "java").toString();
        IOException error = assertThrows(IOException.class, () -> CommandProcess.capture(
                CoreLaunch.resolve(java, false, null),
                List.of("-version"),
                64 * 1024,
                30,
                null
        ));
        assertTrue(error.getMessage().contains("unexpected stderr"));
    }

    @Test
    void cancellationStopsAChildProcessPromptly() throws InterruptedException {
        Path shell = Path.of("/bin/sh");
        org.junit.jupiter.api.Assumptions.assumeTrue(Files.isExecutable(shell));
        AtomicBoolean cancelled = new AtomicBoolean();
        Thread trigger = new Thread(() -> {
            try {
                Thread.sleep(200);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
            cancelled.set(true);
        });
        trigger.start();
        long started = System.nanoTime();
        assertThrows(ProcessCanceledException.class, () -> CommandProcess.capture(
                CoreLaunch.resolve(shell.toString(), false, null),
                List.of("-c", "sleep 30"),
                0,
                0,
                null,
                cancelled::get
        ));
        trigger.join();
        long elapsedMillis = (System.nanoTime() - started) / 1_000_000;
        assertTrue(elapsedMillis < 5_000, "cancellation should not wait for the child timeout");
    }
}
