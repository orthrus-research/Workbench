package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;
import static dev.cleanroommc.workbench.intellij.community.AtlasRecipeContract.*;

/** One no-shell Core command, closed with the browser; graph verification is reused. */
final class AtlasRecipeSession implements AutoCloseable {
    private final Process process;
    private final BufferedInputStream input;
    private final ExecutorService readers = Executors.newFixedThreadPool(2, runnable -> {
        Thread thread = new Thread(runnable, "workbench-atlas-session"); thread.setDaemon(true); return thread;
    });
    private final AtomicReference<String> diagnostic = new AtomicReference<>();
    private String graph;
    private int sequence;
    private boolean closed;

    AtlasRecipeSession(CoreLaunch launch, AtlasRecipeClient.GraphPath path, String cwd, BooleanSupplier cancelled) throws IOException {
        ProcessBuilder builder = new ProcessBuilder(launch.command(List.of("atlas", "recipes", "session", path.commandPath())));
        var environment = CommandProcess.scrubbedEnvironment(builder.environment());
        builder.environment().clear(); builder.environment().putAll(environment);
        if (launch.host().equals("native") && cwd != null) builder.directory(new File(cwd));
        process = builder.start(); input = new BufferedInputStream(process.getInputStream());
        readers.submit(() -> {
            try {
                byte[] buffer = new byte[4096]; int count = process.getErrorStream().read(buffer);
                if (count > 0) diagnostic.set(new String(buffer, 0, count, StandardCharsets.UTF_8));
            } catch (IOException ignored) { /* Closing the session closes stderr. */ }
        });
        try {
            JsonObject ready = parseRoot(read(cancelled), "recipe session ready");
            equal(string(ready, "format"), "workbench-atlas-recipe-session-ready-v1", "Unsupported Atlas session");
            require(integer(ready, "schema_version") == 1 && string(ready, "state").equals("ready"), "Atlas session did not open");
            AtlasRecipeContext context = AtlasRecipeContext.parse(ready.getAsJsonObject("context"));
            require(AtlasRecipeContext.sameRoot(context.root(), path.expectedContextRoot()), "Atlas session changed graph path");
            graph = string(ready, "graph_set_id"); equal(graph, context.graphSetId(), "Atlas session graph changed");
        } catch (IOException | RuntimeException error) { close(); throw error; }
    }

    String graph() { return graph; }

    String request(String operation, JsonObject arguments, BooleanSupplier cancelled) throws IOException {
        if (closed) throw new IOException("Atlas session is closed");
        String id = Integer.toString(++sequence);
        JsonObject request = new JsonObject();
        request.addProperty("format", "workbench-atlas-recipe-session-request-v1"); request.addProperty("schema_version", 1);
        request.addProperty("request_id", id); request.addProperty("graph_set_id", graph);
        request.addProperty("operation", operation); request.add("arguments", arguments);
        process.getOutputStream().write((request + "\n").getBytes(StandardCharsets.UTF_8)); process.getOutputStream().flush();
        JsonObject response = parseRoot(read(cancelled), "recipe session response");
        equal(string(response, "format"), "workbench-atlas-recipe-session-response-v1", "Unsupported Atlas response");
        require(integer(response, "schema_version") == 1, "Unsupported Atlas response schema");
        equal(string(response, "graph_set_id"), graph, "Atlas response graph changed");
        equal(string(response, "request_id"), id, "Atlas response request changed");
        if (!string(response, "state").equals("complete")) throw new IOException(string(response, "error"));
        return required(response, "result").toString();
    }

    private String read(BooleanSupplier cancelled) throws IOException {
        Future<String> frame = readers.submit(() -> {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            for (int next; (next = input.read()) != -1;) {
                if (next == '\n') return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes.toByteArray())).toString();
                bytes.write(next);
                if (bytes.size() > MAX_OUTPUT_BYTES) throw new IOException("Atlas page exceeds the client transport boundary");
            }
            throw new IOException("Atlas session ended; reopen and verify the graph");
        });
        try {
            for (;;) {
                if (cancelled.getAsBoolean()) throw new IOException("Atlas query cancelled");
                if (diagnostic.get() != null) throw new IOException(diagnostic.get());
                try { return frame.get(100, TimeUnit.MILLISECONDS); }
                catch (TimeoutException waiting) { /* Poll cancellation and diagnostic channel. */ }
            }
        } catch (ExecutionException error) {
            close(); throw new IOException(error.getCause().getMessage(), error.getCause());
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt(); close(); throw new IOException("Atlas session interrupted", error);
        } catch (IOException error) { close(); throw error; }
        finally { frame.cancel(true); }
    }

    @Override public void close() {
        if (closed) return; closed = true;
        try { process.getOutputStream().close(); } catch (IOException ignored) { }
        process.destroy(); readers.shutdownNow();
        process.onExit().thenRun(() -> { try { input.close(); } catch (IOException ignored) { } });
    }
}
