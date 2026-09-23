package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.progress.ProcessCanceledException;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.BooleanSupplier;

/** Bounded direct process capture for catalog, review, preview, and execution calls. */
public final class CommandProcess {
    /** Exact no-shell child observation returned to installed qualification ports. */
    public record Observation(
            @NotNull List<String> argv,
            @Nullable String cwd,
            long pid,
            @Nullable Long processGroupId,
            @Nullable Long processStartTimeTicks,
            long parentPid,
            @NotNull String startedAt,
            @NotNull String finishedAt,
            int exitCode,
            @Nullable String signal,
            @NotNull String stdout,
            @NotNull String stderr,
            @NotNull List<Long> remainingDescendantPids
    ) {
        public Observation {
            argv = List.copyOf(argv);
            remainingDescendantPids = List.copyOf(remainingDescendantPids);
        }
    }

    private static final List<String> ALLOWED_ENVIRONMENT = List.of(
            "PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR",
            "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
            "WORKBENCH_STATE_ROOT",
            "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW",
            "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME",
            "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP"
    );

    private CommandProcess() {
    }

    public static @NotNull String capture(
            @NotNull CoreLaunch launch,
            @NotNull List<String> arguments,
            int maximumBytes,
            long timeoutSeconds,
            @Nullable String workingDirectory
    ) throws IOException {
        Observation observation = captureObserved(
                launch, arguments, maximumBytes, timeoutSeconds, workingDirectory
        );
        String error = observation.stderr().trim();
        if (observation.exitCode() != 0) {
            String detail = error.isEmpty() ? observation.stdout().trim() : error;
            throw new IOException(
                    "Workbench command exited " + observation.exitCode()
                            + (detail.isEmpty()
                            ? ""
                            : ":\n" + detail.substring(0, Math.min(16 * 1024, detail.length())))
            );
        }
        if (!error.isEmpty()) {
            throw new IOException(
                    "Workbench command returned unexpected stderr:\n"
                            + error.substring(0, Math.min(16 * 1024, error.length()))
            );
        }
        return observation.stdout();
    }

    public static @NotNull String capture(
            @NotNull CoreLaunch launch,
            @NotNull List<String> arguments,
            int maximumBytes,
            long timeoutSeconds,
            @Nullable String workingDirectory,
            @NotNull BooleanSupplier cancelled
    ) throws IOException {
        Observation observation = captureObserved(
                launch,
                arguments,
                maximumBytes,
                timeoutSeconds,
                workingDirectory,
                cancelled
        );
        String error = observation.stderr().trim();
        if (observation.exitCode() != 0) {
            String detail = error.isEmpty() ? observation.stdout().trim() : error;
            throw new IOException(
                    "Workbench command exited " + observation.exitCode()
                            + (detail.isEmpty()
                            ? ""
                            : ":\n" + detail.substring(0, Math.min(16 * 1024, detail.length())))
            );
        }
        if (!error.isEmpty()) {
            throw new IOException(
                    "Workbench command returned unexpected stderr:\n"
                            + error.substring(0, Math.min(16 * 1024, error.length()))
            );
        }
        return observation.stdout();
    }

    public static @NotNull Observation captureObserved(
            @NotNull CoreLaunch launch,
            @NotNull List<String> arguments,
            int maximumBytes,
            long timeoutSeconds,
            @Nullable String workingDirectory
    ) throws IOException {
        return captureObserved(
                launch,
                arguments,
                maximumBytes,
                timeoutSeconds,
                workingDirectory,
                () -> false
        );
    }

    public static @NotNull Observation captureObserved(
            @NotNull CoreLaunch launch,
            @NotNull List<String> arguments,
            int maximumBytes,
            long timeoutSeconds,
            @Nullable String workingDirectory,
            @NotNull BooleanSupplier cancelled
    ) throws IOException {
        // Zero suspends a resource target; cancellation remains independent.
        if (maximumBytes < 0 || timeoutSeconds < 0) {
            throw new IllegalArgumentException("process bounds must be nonnegative");
        }
        if (cancelled.getAsBoolean()) {
            throw new ProcessCanceledException();
        }
        List<String> argv = launch.command(arguments);
        ProcessBuilder builder = new ProcessBuilder(argv);
        String selectedWorkingDirectory = null;
        if (launch.host().equals("native")
                && workingDirectory != null
                && !workingDirectory.isBlank()) {
            builder.directory(new File(workingDirectory));
            selectedWorkingDirectory = workingDirectory;
        }
        Map<String, String> environment = builder.environment();
        Map<String, String> scrubbed = scrubbedEnvironment(environment);
        environment.clear();
        environment.putAll(scrubbed);
        String startedAt = Instant.now().toString();
        Process process = builder.start();
        long pid = process.pid();
        LinuxProcessIdentity processIdentity = linuxProcessIdentity(pid);
        Long processGroupId = processIdentity == null ? null : processIdentity.groupId();
        Long processStartTimeTicks = processIdentity == null
                ? null
                : processIdentity.startTimeTicks();
        process.getOutputStream().close();
        ExecutorService readers = Executors.newFixedThreadPool(2, runnable -> {
            Thread thread = new Thread(runnable, "workbench-catalog-command-client");
            thread.setDaemon(true);
            return thread;
        });
        Future<byte[]> stdout = readers.submit(
                () -> readBounded(process.getInputStream(), maximumBytes, process)
        );
        Future<byte[]> stderr = readers.submit(
                () -> readBounded(process.getErrorStream(), maximumBytes, process)
        );
        try {
            long startedNanos = System.nanoTime();
            long timeoutNanos = TimeUnit.SECONDS.toNanos(timeoutSeconds);
            while (process.isAlive()) {
                if (cancelled.getAsBoolean()) {
                    terminate(process);
                    throw new ProcessCanceledException();
                }
                try {
                    process.waitFor(100, TimeUnit.MILLISECONDS);
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    terminate(process);
                    throw new IOException("Workbench command was interrupted", error);
                }
                if (process.isAlive()
                        && timeoutSeconds > 0 && System.nanoTime() - startedNanos >= timeoutNanos) {
                    terminate(process);
                    throw new IOException("Workbench command timed out");
                }
            }
            String output = decode(await(stdout, "stdout", timeoutSeconds == 0), "stdout");
            String error = decode(await(stderr, "stderr", timeoutSeconds == 0), "stderr");
            List<Long> remainingDescendants = process.toHandle().descendants()
                    .filter(ProcessHandle::isAlive)
                    .map(ProcessHandle::pid)
                    .sorted()
                    .toList();
            return new Observation(
                    argv,
                    selectedWorkingDirectory,
                    pid,
                    processGroupId,
                    processStartTimeTicks,
                    ProcessHandle.current().pid(),
                    startedAt,
                    Instant.now().toString(),
                    process.exitValue(),
                    null,
                    output,
                    error,
                    remainingDescendants
            );
        } finally {
            if (process.isAlive()) {
                terminate(process);
            }
            stdout.cancel(true);
            stderr.cancel(true);
            readers.shutdownNow();
        }
    }

    private record LinuxProcessIdentity(long groupId, long startTimeTicks) {
    }

    private static @Nullable LinuxProcessIdentity linuxProcessIdentity(long pid) {
        if (!System.getProperty("os.name", "").toLowerCase().contains("linux")) {
            return null;
        }
        try {
            String value = Files.readString(Path.of("/proc", Long.toString(pid), "stat"));
            int close = value.lastIndexOf(')');
            if (close < 0) {
                return null;
            }
            String[] fields = value.substring(close + 2).trim().split("\\s+");
            if (fields.length < 20) {
                return null;
            }
            long group = Long.parseLong(fields[2]);
            long startTime = Long.parseLong(fields[19]);
            return group > 0 && startTime > 0
                    ? new LinuxProcessIdentity(group, startTime)
                    : null;
        } catch (IOException | NumberFormatException error) {
            return null;
        }
    }

    static @NotNull Map<String, String> scrubbedEnvironment(@NotNull Map<String, String> source) {
        Map<String, String> result = new LinkedHashMap<>();
        for (String key : ALLOWED_ENVIRONMENT) {
            String value = source.get(key);
            if (value != null) {
                result.put(key, value);
            }
        }
        return result;
    }

    private static byte[] readBounded(
            @NotNull InputStream stream,
            int maximumBytes,
            @NotNull Process process
    ) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(maximumBytes, 64 * 1024));
        byte[] buffer = new byte[64 * 1024];
        int total = 0;
        while (true) {
            int read = stream.read(buffer);
            if (read < 0) {
                return output.toByteArray();
            }
            total += read;
            if (maximumBytes > 0 && total > maximumBytes) {
                terminate(process);
                throw new IOException("Workbench command output exceeds the supported bound");
            }
            output.write(buffer, 0, read);
        }
    }

    private static byte[] await(@NotNull Future<byte[]> future, @NotNull String label, boolean noDeadline)
            throws IOException {
        try {
            return noDeadline ? future.get() : future.get(10, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IOException("reading Workbench " + label + " was interrupted", error);
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof IOException io) {
                throw io;
            }
            throw new IOException("reading Workbench " + label + " failed", cause);
        } catch (TimeoutException error) {
            throw new IOException("Workbench " + label + " did not close", error);
        }
    }

    private static @NotNull String decode(byte[] bytes, @NotNull String label) throws IOException {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(java.nio.ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException error) {
            throw new IOException("Workbench " + label + " is not valid UTF-8", error);
        }
    }

    private static void terminate(@NotNull Process process) {
        List<ProcessHandle> descendants = new ArrayList<>(process.toHandle().descendants().toList());
        Collections.reverse(descendants);
        descendants.forEach(ProcessHandle::destroyForcibly);
        process.destroyForcibly();
    }
}
