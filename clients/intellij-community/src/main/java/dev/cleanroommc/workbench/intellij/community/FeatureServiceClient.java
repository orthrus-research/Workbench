package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.jetbrains.annotations.NotNull;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HexFormat;
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

/** Machine-JSON client for one exact installed Feature Studio Service V3 job. */
public final class FeatureServiceClient {
    private static final int MAX_OUTPUT = 4 * 1024 * 1024;
    private static final Pattern CONTENT_ID = Pattern.compile(
            "[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}"
    );
    private static final Pattern JOB_ID = Pattern.compile("job-v2:[0-9a-f]{32}");
    private static final Pattern SERVICE_INSTANCE_ID = Pattern.compile(
            "service-instance-v2:[0-9a-f]{32}"
    );
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    private static final List<String> ALLOWED_ENVIRONMENT = List.of(
            "PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR",
            "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
            "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP"
    );

    private FeatureServiceClient() {
    }

    public static @NotNull List<String> resultCommand(
            @NotNull String executable,
            @NotNull Connection connection,
            @NotNull Job job
    ) {
        return resultCommand(CoreLaunch.resolve(executable), connection, job);
    }

    static @NotNull List<String> resultCommand(
            @NotNull CoreLaunch launch,
            @NotNull Connection connection,
            @NotNull Job job
    ) {
        return launch.command(List.of(
                "feature-service", "result",
                "--endpoint", launch.commandPath(connection.endpoint(), "service endpoint"),
                "--credential", launch.commandPath(connection.credential(), "service credential"),
                "--context-ref-id", contentId(job.contextRefId(), "context ref ID", "context-ref"),
                "--input-binding-id", contentId(job.inputBindingId(), "input binding ID", "input-binding"),
                "--job-id", jobId(job.jobId(), "job ID"),
                "--json"
        ));
    }

    public static @NotNull Result readResult(
            @NotNull String executable,
            @NotNull Connection connection,
            @NotNull Job job
    ) throws IOException {
        CoreLaunch launch = CoreLaunch.resolve(executable);
        ProcessBuilder builder = new ProcessBuilder(resultCommand(launch, connection, job));
        Map<String, String> environment = builder.environment();
        Map<String, String> scrubbed = scrubbedEnvironment(environment);
        environment.clear();
        environment.putAll(scrubbed);
        Process process = builder.start();
        process.getOutputStream().close();
        ExecutorService readers = Executors.newFixedThreadPool(2, runnable -> {
            Thread thread = new Thread(runnable, "workbench-feature-service-client");
            thread.setDaemon(true);
            return thread;
        });
        Future<byte[]> stdout = readers.submit(() -> readBounded(process.getInputStream(), process));
        Future<byte[]> stderr = readers.submit(() -> readBounded(process.getErrorStream(), process));
        try {
            boolean finished;
            try {
                finished = process.waitFor(60, TimeUnit.SECONDS);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                terminate(process);
                throw new IOException("Feature Studio service call was interrupted", error);
            }
            if (!finished) {
                terminate(process);
                throw new IOException("Feature Studio service call timed out");
            }
            byte[] output = await(stdout, "stdout");
            String error = decode(await(stderr, "stderr"), "stderr").trim();
            if (process.exitValue() != 0) {
                throw new IOException(
                        "Feature Studio service call exited " + process.exitValue()
                                + (error.isEmpty() ? "" : ": " + error.substring(0, Math.min(2000, error.length())))
                );
            }
            return parseResult(decode(output, "stdout"), job.jobId());
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
            @NotNull String expectedJobId
    ) throws IOException {
        JsonObject root = parseObject(text, "Feature Studio service result");
        exact(root, Set.of(
                "format", "schema_version", "action", "registry_id",
                "service_instance_id", "registration", "result"
        ), "Feature Studio service result");
        require(root, "format", "workbench-feature-studio-service-cli-result-v1");
        integer(root, "schema_version", 1);
        require(root, "action", "result");
        if (!root.has("registration") || !root.get("registration").isJsonNull()) {
            throw new IOException("Feature Studio result unexpectedly registered context");
        }
        String registryId = contentId(string(root, "registry_id"), "registry ID", "component-capability-registry");
        String serviceInstanceId = serviceInstanceId(string(root, "service_instance_id"));
        JsonObject result = object(root, "result");
        JsonObject outcome = object(result, "outcome");
        require(outcome, "state", "succeeded");
        JsonObject wrapper = object(outcome, "value");
        String jobId = jobId(expectedJobId, "expected job ID");
        String serviceResultId = contentId(
                string(wrapper, "result_id"), "service result ID", "feature-studio-service-result"
        );
        String ownerRequestId = contentId(
                string(wrapper, "owner_request_id"), "owner request ID", "feature-studio-owner-request"
        );
        String operationPlanId = wrapper.has("operation_plan_id")
                && wrapper.get("operation_plan_id").isJsonNull()
                ? null
                : contentId(
                        string(wrapper, "operation_plan_id"),
                        "operation plan ID",
                        "operation-plan"
                );
        String ownerResultId = contentId(
                string(wrapper, "owner_result_id"), "owner result ID", "feature-studio-result"
        );
        String ownerText = string(wrapper, "owner_result_canonical_json");
        byte[] ownerBytes = ownerText.getBytes(StandardCharsets.UTF_8);
        int ownerSize = positiveInteger(wrapper, "owner_result_canonical_size", MAX_OUTPUT);
        String ownerSha256 = string(wrapper, "owner_result_canonical_sha256");
        if (!SHA256.matcher(ownerSha256).matches()
                || ownerSize != ownerBytes.length
                || !ownerSha256.equals(sha256(ownerBytes))) {
            throw new IOException("Feature Studio owner result bytes differ from their custody fields");
        }
        JsonObject owner = parseObject(ownerText, "Feature Studio canonical owner result");
        require(owner, "result_id", ownerResultId);
        return new Result(
                registryId, serviceInstanceId, jobId, serviceResultId,
                ownerRequestId, operationPlanId, ownerResultId,
                ownerSha256, ownerSize, owner.deepCopy()
        );
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
                throw new IOException("Feature Studio service output exceeds 4 MiB");
            }
            output.write(buffer, 0, read);
        }
    }

    private static byte[] await(Future<byte[]> future, String label) throws IOException {
        try {
            return future.get(5, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IOException("reading Feature Studio " + label + " was interrupted", error);
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof IOException io) {
                throw io;
            }
            throw new IOException("reading Feature Studio " + label + " failed", cause);
        } catch (TimeoutException error) {
            throw new IOException("Feature Studio " + label + " did not close", error);
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
            throw new IOException("Feature Studio " + label + " is not valid UTF-8", error);
        }
    }

    private static void terminate(Process process) {
        List<ProcessHandle> descendants = new ArrayList<>(process.toHandle().descendants().toList());
        Collections.reverse(descendants);
        descendants.forEach(ProcessHandle::destroyForcibly);
        process.destroyForcibly();
    }

    private static JsonObject parseObject(String text, String label) throws IOException {
        try {
            JsonElement parsed = JsonParser.parseString(text);
            if (!parsed.isJsonObject()) {
                throw new IOException(label + " is not an object");
            }
            return parsed.getAsJsonObject();
        } catch (RuntimeException error) {
            throw new IOException(label + " is invalid JSON", error);
        }
    }

    private static JsonObject object(JsonObject parent, String field) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonObject()) {
            throw new IOException("Feature Studio field " + field + " is not an object");
        }
        return parent.getAsJsonObject(field);
    }

    private static String string(JsonObject parent, String field) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonPrimitive()
                || !parent.get(field).getAsJsonPrimitive().isString()) {
            throw new IOException("Feature Studio field " + field + " is not a string");
        }
        String value = parent.get(field).getAsString();
        if (value.isEmpty() || value.indexOf('\0') >= 0
                || value.getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT) {
            throw new IOException("Feature Studio field " + field + " is outside its bound");
        }
        return value;
    }

    private static void exact(JsonObject value, Set<String> keys, String label) throws IOException {
        if (!value.keySet().equals(keys)) {
            throw new IOException(label + " fields changed");
        }
    }

    private static void require(JsonObject parent, String field, String expected) throws IOException {
        if (!string(parent, field).equals(expected)) {
            throw new IOException("Feature Studio field " + field + " changed");
        }
    }

    private static void integer(JsonObject parent, String field, int expected) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonPrimitive()) {
            throw new IOException("Feature Studio field " + field + " is not an integer");
        }
        try {
            if (parent.get(field).getAsInt() != expected) {
                throw new IOException("Feature Studio field " + field + " changed");
            }
        } catch (NumberFormatException error) {
            throw new IOException("Feature Studio field " + field + " is not an integer", error);
        }
    }

    private static int positiveInteger(JsonObject parent, String field, int maximum) throws IOException {
        if (!parent.has(field) || !parent.get(field).isJsonPrimitive()) {
            throw new IOException("Feature Studio field " + field + " is not an integer");
        }
        try {
            int value = parent.get(field).getAsInt();
            if (value < 1 || value > maximum) {
                throw new IOException("Feature Studio field " + field + " is outside its bound");
            }
            return value;
        } catch (NumberFormatException error) {
            throw new IOException("Feature Studio field " + field + " is not an integer", error);
        }
    }

    private static String bounded(String value, String label) {
        if (value.isBlank() || value.indexOf('\0') >= 0
                || value.getBytes(StandardCharsets.UTF_8).length > 32 * 1024) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return value;
    }

    private static String contentId(String value, String label, String kind) {
        if (!CONTENT_ID.matcher(value).matches() || !value.startsWith(kind + ":sha256:")) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return value;
    }

    private static String jobId(String value, String label) {
        if (!JOB_ID.matcher(value).matches()) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return value;
    }

    private static String serviceInstanceId(String value) {
        if (!SERVICE_INSTANCE_ID.matcher(value).matches()) {
            throw new IllegalArgumentException("service instance ID is invalid");
        }
        return value;
    }

    private static String sha256(byte[] value) throws IOException {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IOException("SHA-256 is unavailable", error);
        }
    }

    public record Connection(@NotNull String endpoint, @NotNull String credential) {
    }

    public record Job(
            @NotNull String contextRefId,
            @NotNull String inputBindingId,
            @NotNull String jobId,
            @NotNull String jobSubmissionId
    ) {
    }

    public record Result(
            @NotNull String registryId,
            @NotNull String serviceInstanceId,
            @NotNull String jobId,
            @NotNull String serviceResultId,
            @NotNull String ownerRequestId,
            String operationPlanId,
            @NotNull String ownerResultId,
            @NotNull String ownerResultCanonicalSha256,
            int ownerResultCanonicalSize,
            @NotNull JsonObject ownerResult
    ) {
    }
}
