package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.regex.Pattern;

/** Direct no-shell client for one reviewed material/fluid recipe run. */
public final class DeveloperFeatureClient {
    private static final int MAX_OUTPUT = 16 * 1024 * 1024;
    private static final Pattern PLAN_ID = Pattern.compile(
            "workbench-developer-material-fluid-recipe-plan:sha256:[0-9a-f]{64}"
    );
    private static final Pattern RUN_ID = Pattern.compile(
            "workbench-developer-material-fluid-recipe-run:sha256:[0-9a-f]{64}"
    );
    private static final Set<String> ASSERTIONS = Set.of(
            "fluid_registration", "fml_client_load", "groovy_compilation",
            "localization", "material_registration", "recipe_registration"
    );
    private static final Map<String, String> ASSERTION_MEANINGS = Map.of(
            "fml_client_load", "the disposable client reaches the exact FML loaded marker",
            "groovy_compilation", "the changed Groovy program compiles in the projected client",
            "material_registration", "the requested GregTech material identity is registered",
            "fluid_registration", "the material-backed Forge fluid identity is registered",
            "localization", "the requested client translation resolves to its intended label",
            "recipe_registration", "the exact reviewed machine recipe is registered once in its selected map"
    );
    private static final Set<String> ASSERTION_STATES = Set.of(
            "pending", "observed", "failed", "not-observed"
    );
    private static final Set<String> OUTCOMES = Set.of(
            "runtime-completed", "runtime-assertion-failed", "runtime-incomplete", "failed"
    );
    private static final List<String> ALLOWED_ENVIRONMENT = List.of(
            "PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR",
            "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
            "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP"
    );

    private DeveloperFeatureClient() {
    }

    public static @NotNull List<String> runCommand(
            @NotNull String executable,
            @NotNull String planId,
            @NotNull Options options
    ) {
        return runCommand(CoreLaunch.resolve(executable), planId, options);
    }

    static @NotNull List<String> runCommand(
            @NotNull CoreLaunch launch,
            @NotNull String planId,
            @NotNull Options options
    ) {
        String plan = planId(planId);
        if (!options.launcher().equals("prism") && !options.launcher().equals("multimc")) {
            throw new IllegalArgumentException("launcher must be prism or multimc");
        }
        List<String> arguments = new ArrayList<>(List.of(
                "feature", "run", "material-fluid-recipe", plan,
                "--consent", plan,
                "--launcher", options.launcher(),
                "--launcher-executable", path(launch, options.launcherExecutable(), "launcher executable"),
                "--launcher-root", path(launch, options.launcherRoot(), "launcher root")
        ));
        optionalPath(arguments, launch, "--launcher-java", options.launcherJava(), "launcher Java");
        optionalPath(
                arguments, launch, "--launcher-java-state",
                options.launcherJavaState(), "launcher Java state"
        );
        optionalPath(
                arguments, launch, "--packwiz-executable",
                options.packwizExecutable(), "Packwiz executable"
        );
        for (String seed : options.seedRoots()) {
            arguments.add("--seed");
            arguments.add(path(launch, seed, "seed root"));
        }
        optionalPath(arguments, launch, "--state-root", options.stateRoot(), "feature state root");
        arguments.addAll(List.of(
                "--timeout", positive(options.timeoutSeconds(), "launch timeout"),
                "--attach-timeout", positive(options.attachTimeoutSeconds(), "attach timeout"),
                "--session-timeout", positive(options.sessionTimeoutSeconds(), "session timeout"),
                "--json"
        ));
        return launch.command(arguments);
    }

    public static @NotNull Result run(
            @NotNull String executable,
            @NotNull String planId,
            @NotNull Options options,
            @Nullable String workingDirectory
    ) throws IOException {
        CoreLaunch launch = CoreLaunch.resolve(executable);
        ProcessBuilder builder = new ProcessBuilder(runCommand(launch, planId, options));
        if (launch.host().equals("native") && workingDirectory != null && !workingDirectory.isBlank()) {
            builder.directory(new File(workingDirectory));
        }
        Map<String, String> environment = builder.environment();
        Map<String, String> scrubbed = scrubbedEnvironment(environment);
        environment.clear();
        environment.putAll(scrubbed);
        Process process = builder.start();
        process.getOutputStream().close();
        ExecutorService readers = Executors.newFixedThreadPool(2, runnable -> {
            Thread thread = new Thread(runnable, "workbench-developer-feature-client");
            thread.setDaemon(true);
            return thread;
        });
        Future<byte[]> stdout = readers.submit(() -> readBounded(process.getInputStream(), process));
        Future<byte[]> stderr = readers.submit(() -> readBounded(process.getErrorStream(), process));
        try {
            boolean finished;
            long waitSeconds = Math.max(1L, (long) Math.ceil(options.sessionTimeoutSeconds() + 900));
            try {
                finished = process.waitFor(waitSeconds, TimeUnit.SECONDS);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                terminate(process);
                throw new IOException("developer feature run was interrupted", error);
            }
            if (!finished) {
                terminate(process);
                throw new IOException("developer feature run timed out");
            }
            String output = decode(await(stdout, "stdout"), "stdout");
            String errorText = decode(await(stderr, "stderr"), "stderr").trim();
            Result result;
            try {
                result = parseResult(output, planId);
            } catch (IOException error) {
                if (!errorText.isEmpty()) {
                    throw new IOException(errorText.substring(0, Math.min(4000, errorText.length())), error);
                }
                throw error;
            }
            validateExitStatus(process.exitValue(), result);
            return result;
        } finally {
            if (process.isAlive()) {
                terminate(process);
            }
            stdout.cancel(true);
            stderr.cancel(true);
            readers.shutdownNow();
        }
    }

    public static @NotNull Result parseResult(
            @NotNull String text,
            @NotNull String expectedPlanId
    ) throws IOException {
        if (text.getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT) {
            throw new IOException("developer feature output exceeds 16 MiB");
        }
        JsonObject root = parseObject(text);
        require(root, "format", "workbench-developer-material-fluid-recipe-run-v1");
        integer(root, "schema_version", 1);
        require(root, "kind", "workbench-developer-material-fluid-recipe-run");
        final String plan;
        final String expectedPlan;
        try {
            plan = planId(string(root, "plan_id"));
            expectedPlan = planId(expectedPlanId);
        } catch (IllegalArgumentException error) {
            throw new IOException("developer feature plan identity is invalid", error);
        }
        if (!plan.equals(expectedPlan)) {
            throw new IOException("developer feature receipt belongs to another plan");
        }
        String id = string(root, "id");
        if (!RUN_ID.matcher(id).matches()) {
            throw new IOException("developer feature run ID is invalid");
        }
        String state = string(root, "state");
        if (!state.equals("complete") && !state.equals("incomplete")) {
            throw new IOException("developer feature state is invalid");
        }
        String outcome = string(root, "outcome");
        if (!OUTCOMES.contains(outcome)) {
            throw new IOException("developer feature outcome is invalid");
        }
        JsonObject assertions = object(root, "assertions");
        if (!assertions.keySet().equals(ASSERTIONS)) {
            throw new IOException("developer feature assertion set changed");
        }
        Map<String, String> assertionStates = new LinkedHashMap<>();
        for (String name : ASSERTIONS.stream().sorted().toList()) {
            JsonObject assertion = object(assertions, name);
            require(assertion, "meaning", ASSERTION_MEANINGS.get(name));
            String assertionState = string(assertion, "state");
            if (!ASSERTION_STATES.contains(assertionState)) {
                throw new IOException("developer feature assertion " + name + " is invalid");
            }
            assertionStates.put(name, assertionState);
        }
        boolean allObserved = assertionStates.values().stream().allMatch("observed"::equals);
        boolean complete = outcome.equals("runtime-completed") && allObserved;
        if (state.equals("complete") != complete) {
            throw new IOException("developer feature completion contradicts its assertions");
        }
        return new Result(
                complete, id, plan, outcome,
                Collections.unmodifiableMap(assertionStates), root.deepCopy()
        );
    }

    static void validateExitStatus(int exitStatus, @NotNull Result result) throws IOException {
        int expected = result.complete() ? 0 : 1;
        if (exitStatus != expected) {
            throw new IOException(
                    "developer feature exit status " + exitStatus
                            + " conflicts with its "
                            + (result.complete() ? "complete" : "incomplete")
                            + " receipt"
            );
        }
    }

    private static void optionalPath(
            List<String> arguments,
            CoreLaunch launch,
            String flag,
            @Nullable String value,
            String label
    ) {
        if (value != null && !value.isBlank()) {
            arguments.add(flag);
            arguments.add(path(launch, value, label));
        }
    }

    private static String path(CoreLaunch launch, String value, String label) {
        return launch.commandPath(exact(value, label), label);
    }

    private static String exact(String value, String label) {
        if (value == null || value.isBlank() || value.indexOf('\0') >= 0
                || value.getBytes(StandardCharsets.UTF_8).length > 32 * 1024) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return value.trim();
    }

    private static String positive(double value, String label) {
        if (!Double.isFinite(value) || value <= 0) {
            throw new IllegalArgumentException(label + " must be positive");
        }
        return Double.toString(value);
    }

    private static String planId(String value) {
        String selected = exact(value, "reviewed plan ID");
        if (!PLAN_ID.matcher(selected).matches()) {
            throw new IllegalArgumentException("reviewed plan ID is invalid");
        }
        return selected;
    }

    private static Map<String, String> scrubbedEnvironment(Map<String, String> source) {
        Map<String, String> result = new LinkedHashMap<>();
        for (String key : ALLOWED_ENVIRONMENT) {
            String value = source.get(key);
            if (value != null) {
                result.put(key, value);
            }
        }
        return result;
    }

    private static byte[] readBounded(InputStream stream, Process process) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[16 * 1024];
        int total = 0;
        while (true) {
            int read = stream.read(buffer);
            if (read < 0) {
                return output.toByteArray();
            }
            total += read;
            if (total > MAX_OUTPUT) {
                terminate(process);
                throw new IOException("developer feature output exceeds 16 MiB");
            }
            output.write(buffer, 0, read);
        }
    }

    private static byte[] await(Future<byte[]> future, String label) throws IOException {
        try {
            return future.get(10, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IOException("reading developer feature " + label + " was interrupted", error);
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof IOException io) {
                throw io;
            }
            throw new IOException("reading developer feature " + label + " failed", cause);
        } catch (TimeoutException error) {
            throw new IOException("developer feature " + label + " did not close", error);
        }
    }

    private static String decode(byte[] bytes, String label) throws IOException {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(java.nio.ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException error) {
            throw new IOException("developer feature " + label + " is not valid UTF-8", error);
        }
    }

    private static void terminate(Process process) {
        List<ProcessHandle> descendants = new ArrayList<>(process.toHandle().descendants().toList());
        Collections.reverse(descendants);
        descendants.forEach(ProcessHandle::destroyForcibly);
        process.destroyForcibly();
    }

    private static JsonObject parseObject(String text) throws IOException {
        try {
            JsonElement parsed = JsonParser.parseString(text);
            if (!parsed.isJsonObject()) {
                throw new IOException("developer feature output is not an object");
            }
            return parsed.getAsJsonObject();
        } catch (RuntimeException error) {
            throw new IOException("developer feature output is invalid JSON", error);
        }
    }

    private static JsonObject object(JsonObject parent, String field) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonObject()) {
            throw new IOException("developer feature field " + field + " is not an object");
        }
        return parent.getAsJsonObject(field);
    }

    private static String string(JsonObject parent, String field) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonPrimitive()
                || !parent.get(field).getAsJsonPrimitive().isString()) {
            throw new IOException("developer feature field " + field + " is not a string");
        }
        String value = parent.get(field).getAsString();
        if (value.isEmpty() || value.indexOf('\0') >= 0
                || value.getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT) {
            throw new IOException("developer feature field " + field + " is outside its bound");
        }
        return value;
    }

    private static void require(JsonObject parent, String field, String expected) throws IOException {
        if (!string(parent, field).equals(expected)) {
            throw new IOException("developer feature field " + field + " changed");
        }
    }

    private static void integer(JsonObject parent, String field, int expected) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonPrimitive()
                || !parent.get(field).getAsJsonPrimitive().isNumber()) {
            throw new IOException("developer feature field " + field + " is not an integer");
        }
        try {
            if (parent.get(field).getAsInt() != expected
                    || parent.get(field).getAsDouble() != expected) {
                throw new IOException("developer feature field " + field + " changed");
            }
        } catch (RuntimeException error) {
            throw new IOException("developer feature field " + field + " is not an integer", error);
        }
    }

    public record Options(
            @NotNull String launcher,
            @NotNull String launcherExecutable,
            @NotNull String launcherRoot,
            @Nullable String launcherJava,
            @Nullable String launcherJavaState,
            @Nullable String packwizExecutable,
            @NotNull List<String> seedRoots,
            @Nullable String stateRoot,
            double timeoutSeconds,
            double attachTimeoutSeconds,
            double sessionTimeoutSeconds
    ) {
        public Options {
            seedRoots = List.copyOf(seedRoots);
        }
    }

    public record Result(
            boolean complete,
            @NotNull String id,
            @NotNull String planId,
            @NotNull String outcome,
            @NotNull Map<String, String> assertionStates,
            @NotNull JsonObject receipt
    ) {
    }
}
