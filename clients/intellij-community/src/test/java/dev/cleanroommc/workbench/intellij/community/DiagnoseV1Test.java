package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class DiagnoseV1Test {
    private static final Path ROOT = Path.of("../..").toAbsolutePath().normalize();

    @Test
    void consumesLiveDiagnosisAndCapsuleReadRoutesWithoutASecondExecutor() throws Exception {
        Path fixtureRoot = Files.createTempDirectory(
                Path.of("/tmp"), "workbench-intellij-diagnose-"
        );
        try {
            JsonObject fixture = createLiveFixture(fixtureRoot);
            CoreLaunch launch = new CoreLaunch(
                    "python3",
                    "python3",
                    List.of("-B", ROOT.resolve("tools/workbench.py").toString()),
                    "native",
                    null
            );
            String sessionId = fixture.get("session_id").getAsString();
            DiagnoseV1.Diagnosis diagnosis = DiagnoseV1Client.diagnose(
                    launch,
                    sessionId,
                    fixture.get("state_root").getAsString(),
                    ROOT.toString()
            );
            assertEquals(fixture.get("diagnosis_id").getAsString(), diagnosis.diagnosisId());
            assertEquals(sessionId, diagnosis.workSessionId());
            assertEquals("observed", diagnosis.observedFailures().getFirst().claimState());
            assertEquals("unknown", diagnosis.unknowns().getFirst().claimState());
            assertEquals("read-only", diagnosis.nextActions().getFirst().mutation());
            assertTrue(diagnosis.timeline().getFirst().message().contains("café"));
            assertTrue(diagnosis.classifications().isEmpty());

            DiagnoseV1.Diagnosis classified = DiagnoseV1.parseDiagnosis(
                    fixture.getAsJsonObject("classified_diagnosis").toString()
            );
            DiagnoseV1.Classification classification = classified.classifications().getFirst();
            assertEquals(1, classified.classifications().size());
            assertEquals("cleanroom-dev-loop-stage", classification.classificationId());
            assertEquals("observed", classification.claimState());
            assertEquals("workbench-shell", classification.ownerId());
            assertEquals(
                    "workbench-cleanroom-dev-loop-receipt:sha256:" + "d".repeat(64),
                    classification.ownerRecordId()
            );
            assertEquals(
                    "workbench-cleanroom-dev-loop-receipt",
                    classification.ownerRecordKind()
            );
            assertTrue(classification.ownerRecordUri().startsWith("file:"));
            assertEquals("sha256:" + "d".repeat(64), classification.ownerRecordDigest());
            assertEquals("server", classification.stage());
            assertEquals("failed", classification.state());
            assertEquals(List.of(
                    "dedicated-server-ready", "common-registry-ready"
            ), classification.requiredMarkers());
            assertTrue(classification.observedMarkers().isEmpty());
            assertEquals(Integer.valueOf(0), classification.effectiveExitCode());
            assertEquals(Boolean.TRUE, classification.cleanupContained());
            assertEquals("sha256:" + "e".repeat(64), classification.artifactDigest());
            assertEquals(
                    "The required dedicated-server markers were not observed.",
                    classification.detail()
            );

            JsonObject extraClassification = fixture.getAsJsonObject(
                    "classified_diagnosis"
            ).deepCopy();
            extraClassification.getAsJsonArray("classifications")
                    .get(0).getAsJsonObject().addProperty("approval", true);
            IllegalArgumentException extraError = assertThrows(
                    IllegalArgumentException.class,
                    () -> DiagnoseV1.parseDiagnosis(extraClassification.toString())
            );
            assertTrue(extraError.getMessage().contains("fields changed"));

            JsonObject duplicateMarker = fixture.getAsJsonObject(
                    "classified_diagnosis"
            ).deepCopy();
            duplicateMarker.getAsJsonArray("classifications").get(0).getAsJsonObject()
                    .getAsJsonArray("required_markers").add("dedicated-server-ready");
            IllegalArgumentException markerError = assertThrows(
                    IllegalArgumentException.class,
                    () -> DiagnoseV1.parseDiagnosis(duplicateMarker.toString())
            );
            assertTrue(markerError.getMessage().contains("repeats a marker"));

            JsonObject invalidExit = fixture.getAsJsonObject(
                    "classified_diagnosis"
            ).deepCopy();
            invalidExit.getAsJsonArray("classifications").get(0).getAsJsonObject()
                    .addProperty("effective_exit_code", 256);
            IllegalArgumentException exitError = assertThrows(
                    IllegalArgumentException.class,
                    () -> DiagnoseV1.parseDiagnosis(invalidExit.toString())
            );
            assertTrue(exitError.getMessage().contains("bounded integer"));

            String capsule = fixture.get("capsule_path").getAsString();
            DiagnoseV1.CapsuleInspection inspected = DiagnoseV1Client.inspectCapsule(
                    launch, capsule, ROOT.toString()
            );
            DiagnoseV1.CapsuleInspection verified = DiagnoseV1Client.verifyCapsule(
                    launch, capsule, ROOT.toString()
            );
            assertEquals(fixture.get("capsule_id").getAsString(), inspected.capsuleId());
            assertEquals(diagnosis.diagnosisId(), inspected.diagnosisId());
            assertEquals(inspected, verified);

            JsonObject changedSession = JsonParser.parseString(diagnosis.rawJson()).getAsJsonObject();
            changedSession.addProperty(
                    "work_session_id", "work-session-v2-ffffffffffffffffffffffffffffffff"
            );
            assertThrows(IllegalArgumentException.class, () ->
                    DiagnoseV1.parseDiagnosis(changedSession.toString())
            );
            JsonObject changedFormat = JsonParser.parseString(diagnosis.rawJson()).getAsJsonObject();
            changedFormat.addProperty("format", "workbench-diagnosis-v2");
            assertThrows(IllegalArgumentException.class, () ->
                    DiagnoseV1.parseDiagnosis(changedFormat.toString())
            );
            JsonObject changedCapsule = JsonParser.parseString(inspected.rawJson()).getAsJsonObject();
            changedCapsule.addProperty("schema_version", 1);
            assertThrows(IllegalArgumentException.class, () ->
                    DiagnoseV1.parseCapsuleInspection(changedCapsule.toString())
            );
        } finally {
            deleteTree(fixtureRoot);
        }
    }

    @Test
    void mapsOnlyPathBearingInputsThroughExistingWindowsWslLaunch() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\Workbench\\workbench",
                true,
                "C:\\Windows"
        );
        assertEquals(List.of(
                "diagnose", "work-session-v2-11111111111111111111111111111111",
                "--state-root", "/home/developer/state", "--json"
        ), DiagnoseV1Client.diagnosisArguments(
                launch,
                "work-session-v2-11111111111111111111111111111111",
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\state"
        ));
        assertEquals(List.of(
                "diagnose", "reproduce", "verify",
                "/home/developer/failure.wb-repro", "--json"
        ), DiagnoseV1Client.capsuleInspectionArguments(
                launch,
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\failure.wb-repro",
                "verify"
        ));
        assertThrows(IllegalArgumentException.class, () ->
                DiagnoseV1Client.capsuleInspectionArguments(
                        launch,
                        "\\\\wsl.localhost\\Debian\\home\\developer\\failure.wb-repro",
                        "inspect"
                )
        );
        assertThrows(IllegalArgumentException.class, () ->
                DiagnoseV1Client.capsuleInspectionArguments(
                        launch,
                        "\\\\wsl.localhost\\Ubuntu\\home\\developer\\failure.wb-repro",
                        "create"
                )
        );
        assertFalse(DiagnoseV1Client.class.getDeclaredMethods().length == 0);
    }

    private static JsonObject createLiveFixture(Path root) throws IOException {
        CoreLaunch python = new CoreLaunch(
                "python3", "python3", List.of("-B"), "native", null
        );
        String output = CommandProcess.capture(
                python,
                List.of(
                        ROOT.resolve(
                                "clients/testing/diagnose_live_fixture.py"
                        ).toString(),
                        ROOT.toString(),
                        root.toString()
                ),
                8 * 1024 * 1024,
                120,
                ROOT.toString()
        );
        return ProductSpineJson.parse(output, "diagnosis live fixture", 8 * 1024 * 1024, 1000);
    }

    private static void deleteTree(Path root) throws IOException {
        if (!Files.exists(root)) return;
        try (var paths = Files.walk(root)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }
}
