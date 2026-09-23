package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

public final class FeatureServiceClientTest {
    private static final FeatureServiceClient.Connection CONNECTION =
            new FeatureServiceClient.Connection("/tmp/service.sock", "/tmp/credential");
    private static final FeatureServiceClient.Job JOB = new FeatureServiceClient.Job(
            "context-ref:sha256:" + "1".repeat(64),
            "input-binding:sha256:" + "2".repeat(64),
            "job-v2:" + "3".repeat(32),
            "job-submission:sha256:" + "4".repeat(64)
    );

    @Test
    void constructsExactNoShellResultArgv() {
        assertEquals(List.of(
                "/opt/workbench", "feature-service", "result",
                "--endpoint", CONNECTION.endpoint(),
                "--credential", CONNECTION.credential(),
                "--context-ref-id", JOB.contextRefId(),
                "--input-binding-id", JOB.inputBindingId(),
                "--job-id", JOB.jobId(), "--json"
        ), FeatureServiceClient.resultCommand("/opt/workbench", CONNECTION, JOB));
    }

    @Test
    void mapsWslServiceCustodyPathsBeforeTransport() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\home\\dev\\core\\workbench",
                true,
                "C:\\Windows"
        );
        FeatureServiceClient.Connection connection = new FeatureServiceClient.Connection(
                "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state\\service.sock",
                "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state\\credential"
        );
        assertEquals(List.of(
                "C:\\Windows\\System32\\wsl.exe",
                "--distribution", "Ubuntu", "--cd", "/home/dev/core",
                "--exec", "/home/dev/core/workbench",
                "feature-service", "result",
                "--endpoint", "/home/dev/state/service.sock",
                "--credential", "/home/dev/state/credential",
                "--context-ref-id", JOB.contextRefId(),
                "--input-binding-id", JOB.inputBindingId(),
                "--job-id", JOB.jobId(), "--json"
        ), FeatureServiceClient.resultCommand(launch, connection, JOB));
    }

    @Test
    void preservesTerminalOwnerBytesAndIdentities() throws Exception {
        JsonObject owner = new JsonObject();
        owner.addProperty("result_id", "feature-studio-result:sha256:" + "5".repeat(64));
        owner.addProperty("state", "complete");
        String ownerText = owner.toString();
        byte[] bytes = ownerText.getBytes(StandardCharsets.UTF_8);
        String digest = HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(bytes)
        );
        JsonObject wrapper = new JsonObject();
        wrapper.addProperty("owner_result_id", owner.get("result_id").getAsString());
        wrapper.addProperty("result_id", "feature-studio-service-result:sha256:" + "8".repeat(64));
        wrapper.addProperty("owner_request_id", "feature-studio-owner-request:sha256:" + "9".repeat(64));
        wrapper.addProperty("operation_plan_id", "operation-plan:sha256:" + "a".repeat(64));
        wrapper.addProperty("owner_result_canonical_json", ownerText);
        wrapper.addProperty("owner_result_canonical_sha256", digest);
        wrapper.addProperty("owner_result_canonical_size", bytes.length);
        JsonObject outcome = new JsonObject();
        outcome.addProperty("state", "succeeded");
        outcome.add("value", wrapper);
        JsonObject result = new JsonObject();
        result.add("outcome", outcome);
        JsonObject root = new JsonObject();
        root.addProperty("format", "workbench-feature-studio-service-cli-result-v1");
        root.addProperty("schema_version", 1);
        root.addProperty("action", "result");
        root.addProperty("registry_id", "component-capability-registry:sha256:" + "6".repeat(64));
        root.addProperty("service_instance_id", "service-instance-v2:" + "7".repeat(32));
        root.add("registration", null);
        root.add("result", result);
        assertEquals(
                owner.get("result_id").getAsString(),
                FeatureServiceClient.parseResult(root.toString(), JOB.jobId()).ownerResultId()
        );

        wrapper.addProperty("owner_result_canonical_size", bytes.length + 1);
        assertThrows(
                java.io.IOException.class,
                () -> FeatureServiceClient.parseResult(root.toString(), JOB.jobId())
        );
    }
}
