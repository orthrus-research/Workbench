package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class FeatureRecordCatalogTest {
    @TempDir
    Path temporary;

    @Test
    void parsesTheExactDiscoveryEnvelopeAndLinksItsOwnerPresentation() throws Exception {
        FeatureRecordCatalog catalog = FeatureRecordCatalog.parse(catalog().toString());
        FeaturePresentation presentation = FeaturePresentation.parse(
                FeaturePresentationTest.presentation().toString()
        );

        assertEquals(1, catalog.records().size());
        assertEquals("recipe-change", catalog.records().getFirst().family());
        RetainedFeatureClient.validateLink(catalog.records().getFirst(), presentation);
    }

    @Test
    void rejectsUnknownRowsAndMismatchedOwnerKinds() throws Exception {
        JsonObject unknown = catalog();
        unknown.getAsJsonArray("records").get(0).getAsJsonObject()
                .addProperty("guessed_label", "unsafe");
        assertThrows(IllegalArgumentException.class,
                () -> FeatureRecordCatalog.parse(unknown.toString()));

        JsonObject wrongKind = catalog();
        wrongKind.getAsJsonArray("records").get(0).getAsJsonObject().addProperty(
                "record_kind", "workbench-developer-material-fluid-recipe-plan"
        );
        assertThrows(IllegalArgumentException.class,
                () -> FeatureRecordCatalog.parse(wrongKind.toString()));
    }

    @Test
    void acceptsRecipeRuntimeComparisonDiscoveryRows() throws Exception {
        JsonObject value = catalog();
        value.remove("id");
        JsonObject presentation = FeaturePresentationTest.runtimePresentationV2();
        JsonObject owner = presentation.getAsJsonObject("owner_record");
        JsonObject record = value.getAsJsonArray("records").get(0).getAsJsonObject();
        record.addProperty("collection", "runs");
        record.addProperty("plan_id", presentation.get("plan_id").getAsString());
        record.addProperty("record_id", owner.get("id").getAsString());
        record.addProperty("record_kind", owner.get("kind").getAsString());
        record.addProperty("record_state", owner.get("state").getAsString());
        record.addProperty("reference", owner.get("id").getAsString());
        record.addProperty("uri", owner.get("uri").getAsString());
        value.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-record-catalog", value
        ));

        FeatureRecordCatalog parsed = FeatureRecordCatalog.parse(value.toString());
        assertEquals("workbench-developer-recipe-change-runtime-comparison",
                parsed.records().getFirst().recordKind());
        RetainedFeatureClient.validateLink(
                parsed.records().getFirst(),
                FeaturePresentation.parse(presentation.toString())
        );
    }

    @Test
    void composesDirectNativeAndWslArgumentsWithoutAShell() throws Exception {
        FeatureRecordCatalog.Record record = FeatureRecordCatalog.parse(catalog().toString())
                .records().getFirst();
        CoreLaunch nativeLaunch = CoreLaunch.resolve("/opt/workbench/bin/workbench", false, null);
        assertEquals(
                List.of("feature", "records", "--state-root", "/tmp/state", "--json"),
                RetainedFeatureClient.recordsArguments(nativeLaunch, "/tmp/state")
        );
        assertEquals(
                List.of(
                        "feature", "present", "recipe-change", "plans", record.recordId(),
                        "--json"
                ),
                RetainedFeatureClient.presentationArguments(nativeLaunch, record, "")
        );

        CoreLaunch wsl = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\bin\\workbench",
                true,
                "C:\\Windows"
        );
        assertEquals(
                List.of("feature", "records", "--state-root", "/home/dev/state", "--json"),
                RetainedFeatureClient.recordsArguments(
                        wsl, "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state"
                )
        );
        assertThrows(IllegalArgumentException.class, () ->
                RetainedFeatureClient.recordsArguments(wsl, "C:\\outside\\state")
        );
    }

    @Test
    void acceptsTheCurrentShellEmptyDiscoveryEnvelope() throws Exception {
        Path root = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath()
                .resolve("../..")
                .normalize();
        Process process = new ProcessBuilder(
                "python3",
                root.resolve("tools/workbench.py").toString(),
                "feature",
                "records",
                "--state-root",
                temporary.toString(),
                "--json"
        ).directory(root.toFile()).start();
        process.getOutputStream().close();
        byte[] stdout = process.getInputStream().readNBytes(FeatureRecordCatalog.MAX_BYTES + 1);
        byte[] stderr = process.getErrorStream().readNBytes(64 * 1024 + 1);
        if (!process.waitFor(30, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new AssertionError("live Workbench discovery timed out");
        }
        assertEquals(0, process.exitValue(), new String(stderr, StandardCharsets.UTF_8));
        FeatureRecordCatalog parsed = FeatureRecordCatalog.parse(
                new String(stdout, StandardCharsets.UTF_8)
        );
        assertEquals(List.of(), parsed.records());
    }

    static JsonObject catalog() throws Exception {
        JsonObject presentation = FeaturePresentationTest.presentation();
        JsonObject owner = presentation.getAsJsonObject("owner_record");
        JsonObject record = new JsonObject();
        record.addProperty("collection", "plans");
        record.add("diagnostic_code", JsonNull.INSTANCE);
        record.addProperty("family", "recipe-change");
        record.addProperty("operation_count", 1);
        record.addProperty("plan_id", presentation.get("plan_id").getAsString());
        record.addProperty("record_id", owner.get("id").getAsString());
        record.addProperty("record_kind", owner.get("kind").getAsString());
        record.addProperty("record_state", owner.get("state").getAsString());
        record.addProperty("reference", owner.get("id").getAsString());
        record.addProperty("uri", owner.get("uri").getAsString());
        record.addProperty("verification_state", "ready");
        record.addProperty("workspace_uri", presentation.get("workspace_uri").getAsString());

        JsonObject filters = new JsonObject();
        filters.add("collection", JsonNull.INSTANCE);
        filters.add("family", JsonNull.INSTANCE);
        JsonArray limitations = new JsonArray();
        limitations.add("Discovery covers only records retained in the selected state root.");
        JsonArray records = new JsonArray();
        records.add(record);
        JsonObject root = new JsonObject();
        root.add("filters", filters);
        root.addProperty("format", FeatureRecordCatalog.FORMAT);
        root.addProperty("kind", "workbench-developer-feature-record-catalog");
        root.add("limitations", limitations);
        root.add("records", records);
        root.addProperty("schema_version", 1);
        root.addProperty("state_root_uri", "file:///tmp/workbench-state");
        root.addProperty("id", CanonicalJson.contentId(
                "workbench-developer-feature-record-catalog", root
        ));
        return root;
    }
}
