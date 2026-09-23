package research.orthrus.axiom;

import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.time.Duration;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class MainTransportTest {
    @Test void completeSingleAndAggregateResponsesRoundTripBeyondFormerWireLimit() {
        var single=Map.<String,Object>of("result",Map.of("diagnostics",List.of("é".repeat(700000))));
        var pair=Map.<String,Object>of("result",Map.of("baseline",single,"candidate",single));
        for (var response : List.of(single,pair)) {
            String output=Main.responseOutput(response);
            assertTrue(output.getBytes(StandardCharsets.UTF_8).length>1_048_576);
            assertTrue(output.endsWith("\n"));
            assertEquals(response, Json.parse(output));
        }
    }
    @Test void defaultMvpSupervisorRetainsLargeStdoutAndStderr() throws Exception {
        var response = Main.observe(worker("large"), new byte[0], 0, 0, 0);
        assertEquals("x".repeat(1_100_000), response.get("detail"));
    }
    public static class Worker {
        public static void main(String[] args) throws Exception {
            if(args[0].equals("early-response")||args[0].equals("early-response-signal")) {
                System.out.print("{\"schema\":\"axiom.result.v1\",\"status\":\"accepted\"}");
                System.out.flush();System.exit(args[0].equals("early-response")?1:137);
            }
            if(args[0].equals("source-error")) {
                System.out.print("{\"schema\":\"axiom.result.v1\",\"status\":\"source-error\"}");System.exit(1);
            }
            if (args[0].equals("large")) {
                System.err.print("warning\n".repeat(200000));
                System.out.print("{\"schema\":\"axiom.result.v1\",\"status\":\"accepted\",\"detail\":\"" + "x".repeat(1_100_000) + "\"}"); return;
            }
            if (args[0].equals("valid")) { System.out.print("{\"schema\":\"axiom.result.v1\",\"status\":\"accepted\"}"); return; }
            if (args[0].equals("bad")) { System.out.print("{}"); return; }
            if (args[0].equals("empty")) return;
            if (args[0].equals("terminated")) System.exit(153);
            if (!args[0].equals("sleep")) {
                var output = args[0].equals("stdout") ? System.out : System.err;
                for (int i = 0; i < 4096; i++) output.print("x".repeat(1024));
                output.flush();
            }
            Thread.sleep(30_000);
        }
    }
    private Process worker(String mode) throws Exception {
        String classes = Path.of(Worker.class.getProtectionDomain().getCodeSource().getLocation().toURI()).toString();
        ProcessBuilder builder = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin/java").toString(),
                "-Xmx32m", "-XX:ActiveProcessorCount=1", "-cp", classes, Worker.class.getName(), mode);
        builder.environment().clear(); return builder.start();
    }
    private void overflow(String channel) {
        assertTimeoutPreemptively(Duration.ofSeconds(5), () -> {
            Process child = worker(channel);
            Failure failure = assertThrows(Failure.class, () -> Main.observe(child, new byte[0], 128, 128, 15_000));
            assertEquals("incomplete", failure.kind); assertEquals("transport.byte-bound", failure.rule);
            assertTrue(child.waitFor(1, TimeUnit.SECONDS));
        });
    }
    @Test void stdoutOverflowKillsWorkerImmediatelyWithOriginalBoundary() { overflow("stdout"); }
    @Test void stderrOverflowKillsWorkerImmediatelyWithOriginalBoundary() { overflow("stderr"); }
    @Test void validWorkerResponseIsPreserved() throws Exception {
        assertEquals("accepted", Main.observe(worker("valid"), new byte[0], 128, 128, 5000).get("status"));
    }
    @Test void deadlineKillsWorkerAndReportsIncomplete() throws Exception {
        Process child = worker("sleep");
        Failure failure = assertThrows(Failure.class, () -> Main.observe(child, new byte[0], 128, 128, 100));
        assertEquals("incomplete", failure.kind); assertEquals("sandbox.timeout", failure.rule);
        assertTrue(child.waitFor(1, TimeUnit.SECONDS));
    }
    @Test void emptyAndMalformedWorkerResponsesAreExecutionFailures() throws Exception {
        assertEquals("sandbox.worker", assertThrows(Failure.class, () -> Main.observe(worker("empty"), new byte[0], 128, 128, 5000)).rule);
        assertEquals("sandbox.protocol", assertThrows(Failure.class, () -> Main.observe(worker("bad"), new byte[0], 128, 128, 5000)).rule);
    }
    @Test void terminationWithoutAResultCannotBeRecipeRejection() throws Exception {
        Failure failure = assertThrows(Failure.class, () -> Main.observe(worker("terminated"), new byte[0], 128, 128, 5000));
        assertEquals("incomplete", failure.kind); assertEquals("sandbox.worker-terminated", failure.rule);
    }
    @Test void completeLookingOutputCannotHideAbnormalExit() throws Exception {
        for(String mode:new String[]{"early-response","early-response-signal"}) {
            Failure failure=assertThrows(Failure.class,()->Main.observe(worker(mode),new byte[0],128,128,5000));
            assertEquals("incomplete",failure.kind);
            assertEquals(mode.equals("early-response")?"sandbox.worker-exit":"sandbox.worker-terminated",failure.rule);
        }
    }
    @Test void canonicalNonzeroSourceErrorResponseIsRetained() throws Exception {
        assertEquals("source-error",Main.observe(worker("source-error"),new byte[0],128,128,5000).get("status"));
    }
    @Test void cancellationIsIncompletePreservesInterruptAndTerminatesWorker() throws Exception {
        Process child=worker("sleep");
        var entered=new java.util.concurrent.CountDownLatch(1);
        var outcome=new java.util.concurrent.atomic.AtomicReference<Throwable>();
        var interrupted=new java.util.concurrent.atomic.AtomicBoolean();
        Thread observer=new Thread(()->{
            entered.countDown();
            try {Main.observe(child,new byte[0],0,0,0);}
            catch(Throwable failure) {outcome.set(failure);interrupted.set(Thread.currentThread().isInterrupted());}
        });
        observer.start();assertTrue(entered.await(1,TimeUnit.SECONDS));observer.interrupt();observer.join(3000);
        assertFalse(observer.isAlive());assertTrue(child.waitFor(1,TimeUnit.SECONDS));
        Failure failure=assertInstanceOf(Failure.class,outcome.get());
        assertEquals("incomplete",failure.kind);assertEquals("sandbox.cancelled",failure.rule);assertTrue(interrupted.get());
    }
}
